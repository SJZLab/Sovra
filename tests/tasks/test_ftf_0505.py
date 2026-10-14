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

# ruff: noqa: E501
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


def store_text(text: str) -> str:
    """Text as a spreadsheet writer stores it in a string element: markup escaped, a literal
    `_xHHHH_` sequence protected as `_x005F_xHHHH_`, CR (which XML would normalise to LF)
    and the other control characters as `_xHHHH_` escapes."""
    text = re.sub(r"_(x[0-9A-Fa-f]{4}_)", r"_x005F_\1", text)
    text = escape(text)
    return "".join(
        f"_x{ord(ch):04X}_" if ord(ch) < 0x20 and ch not in "\t\n" else ch for ch in text
    )


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
    return Cell(
        lambda ref, _s: f'<c r="{ref}" t="inlineStr"><is><t>{store_text(text)}</t></is></c>'
    )


def RIS(raw: str) -> Cell:
    """An inline-string cell whose stored text is exactly `raw` (escapes included)."""
    return Cell(lambda ref, _s: f'<c r="{ref}" t="inlineStr"><is><t>{raw}</t></is></c>')


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
    items = "".join(f"<si><t>{store_text(s)}</t></si>" for s in strings)
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
    xlsx_limits: xi.XlsxLimits = xi.DEFAULT_XLSX_LIMITS,
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
    check(CID, "no fatal locator", outcome.report.fatal_locator is None)
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
    for bad_type in (None, 5, XLSX_TYPE.encode(), support.StrSub(XLSX_TYPE)):
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
            # V-CT-GATE-STR-SUBCLASS: a `str` subclass with the exact right
            # value (`isinstance` would wrongly accept it) is refused too --
            # the check is `type(x) is not str`, not `isinstance`.
            f"{bad_type!r} ({type(bad_type).__name__}): refused by exact type",
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
            and outcome.report.fatal_locator == "row:2"
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
        events, outcome = run(book([[IS("q"), IS("a")], [RIS(text), IS("x")]]))
        check(
            CID,
            f"{label}: row rejected {code}",
            not accepted(events)
            and outcome.report.rejections == (ic.RowRejection("row:2", code, "prompt"),),
        )
    # the same text in an UNMAPPED column does not matter
    events, outcome = run(book([[IS("q"), IS("a"), IS("n")], [IS("p"), IS("c"), RIS("a_x0000_b")]]))
    check(CID, "NUL in an unmapped column is ignored", len(accepted(events)) == 1)


# --- headers ---------------------------------------------------------------------------


def test_header_exactness() -> None:
    for label, header in (
        ("case differs", "Q"),
        ("trailing space", "q "),
        ("leading space", " q"),
        ("NFD vs NFC", "q\u0301"),
    ):
        _events, outcome = run(book([[IS(header), IS("a")], [IS("p"), IS("c")]]))
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
        failed_with(outcome, "duplicate_header") and outcome.report.fatal_locator == "row:1",
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
    # V-NONBLANK-SEEN-ALL-ROWS: a skipped row with no text at all -- only a
    # formula cell, or only an error cell -- is still non-blank (kind != VALUE)
    # and must still count toward header_row's own skip count.
    for label, odd_cell in (("a formula cell", F()), ("an error cell", E())):
        skip_only_formula = [[odd_cell], [IS("q"), IS("a")], [IS("p"), IS("c")]]
        events, outcome = run(book(skip_only_formula), header_row=1)
        check(
            CID,
            f"a skipped row of only {label} still counts as the one non-blank row to skip",
            outcome.report.state == "completed" and len(accepted(events)) == 1,
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
            failed_with(outcome, "xlsx_malformed") and outcome.report.fatal_locator == "row:1",
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


def test_tool_schema_status_is_valid_only_when_the_record_has_a_tool_call() -> None:
    """V-TOOL-STATUS-ALWAYS-NA: `tool_schema_status` must track whether the
    accepted record actually has a tool call, not be a constant -- a mutant
    hardcoding "not_applicable" would otherwise pass every other check in
    this file (none of them exercises a record that has a tool call)."""
    import json

    from app.services.json_schema_tool_validation import validate_instance

    schema = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
        "additionalProperties": False,
    }
    conversation = json.dumps(
        [
            {"role": "user", "content": "where is my order"},
            {
                "role": "assistant",
                "content": [],
                "tool_calls": [
                    {"id": "c1", "name": "lookup_order", "arguments": {"order_id": "abc"}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "name": "lookup_order",
                "content": [{"type": "json", "value": {"status": "shipped"}}],
            },
        ]
    )
    text = (
        '{"shape":"messages_json","messages":{"header":"conv"},"role_key":"role",'
        '"content_key":"content","role_map":{"user":"user","assistant":"assistant","tool":"tool"},'
        '"tool_calls_key":"tool_calls","tool_call_id_key":"tool_call_id","name_key":"name",'
        '"on_unmapped_message_keys":"reject","xlsx":{"sheet_name":"Data","header_row":0}}'
    )
    plain = json.dumps([{"role": "user", "content": "hi"}])
    data = book([[IS("conv")], [IS(conversation)], [IS(plain)]])
    events: list[dict[str, object]] = []
    outcome = xi.run_xlsx_import(
        make_reader(data),
        ic.parse_mapping(text),
        verified_content_type=XLSX_TYPE,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        tool_schemas={"lookup_order": schema},
        tool_argument_validator=validate_instance,
        on_accepted=lambda c: events.append(dict(c)),
    )
    check(
        CID,
        "both rows accepted",
        outcome.report.state == "completed" and len(events) == 2,
    )
    check(
        CID,
        "the row with a conforming tool call is tool_schema_status valid",
        events[0]["tool_schema_status"] == "valid",
    )
    check(
        CID,
        "the plain row with no tool call is not_applicable",
        events[1]["tool_schema_status"] == "not_applicable",
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
        limits = ic.ImportLimits(max_xlsx_cells=cap)
        at_cap = sized_sheet(cap)
        _e, outcome = run(at_cap, limits=limits)
        check(
            CID,
            f"max_xlsx_cells={cap}: exactly {cap} cells completes",
            outcome.report.state == "completed",
        )
        over = sized_sheet(cap + 2)
        _e, outcome = run(over, limits=limits)
        check(
            CID,
            f"max_xlsx_cells={cap}: {cap + 2} cells is fatal too_many_cells",
            failed_with(outcome, "too_many_cells")
            and (outcome.report.fatal_locator or "").startswith("row:"),
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
        "cap-1 is fatal for max_xlsx_cells=2 (3 cells)",
        failed_with(run(one_over, limits=ic.ImportLimits(max_xlsx_cells=2))[1], "too_many_cells"),
    )
    check(
        CID,
        "cap exactly met passes for max_xlsx_cells=3",
        run(one_over, limits=ic.ImportLimits(max_xlsx_cells=3))[1].report.state == "completed",
    )
    # unmapped and empty cells count too
    cells = "".join(f'<c r="{col_letters(i)}1"/>' for i in range(20))
    raw = f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData><row r="1">{cells}</row></sheetData></worksheet>'
    check(
        CID,
        "empty cells count toward the cap",
        failed_with(
            run(book(raw_sheet=raw), limits=ic.ImportLimits(max_xlsx_cells=19))[1],
            "too_many_cells",
        ),
    )


def test_limits_can_be_lowered_never_raised() -> None:
    check(
        CID,
        "the total-uncompressed-bytes default is the FTF-0501 10 GiB",
        xi.XlsxLimits().max_total_uncompressed_bytes == 10 * 1024**3,
    )
    for bad in (
        xi.XlsxLimits().max_total_uncompressed_bytes + 1,
        0,
        -1,
        True,
        1.5,
        "5",
    ):
        try:
            xi.XlsxLimits(max_total_uncompressed_bytes=bad)  # type: ignore[arg-type]
        except ValueError:
            continue
        check(CID, f"XlsxLimits(max_total_uncompressed_bytes={bad!r}) is refused", False)
    xi.XlsxLimits(max_total_uncompressed_bytes=1)
    # max_cells and the shared-strings byte cap moved to the shared ImportLimits
    # (FTF-0502-P1's own LIM-1/LIM-2 pin their defaults and lower-only table).
    check(CID, "the cell default is 50,000,000", ic.DEFAULT_LIMITS.max_xlsx_cells == 50_000_000)
    check(
        CID,
        "the shared-strings default is 256 MiB",
        ic.DEFAULT_LIMITS.max_xlsx_shared_strings_bytes == 256 * 1024**2,
    )


def test_shared_strings_cap_is_exact_and_fatal() -> None:
    body = "<si><t>" + "x" * 200 + "</t></si>"
    data = sst_book(body, ["0", "0"])
    real = len(zipfile.ZipFile(io.BytesIO(data)).read("xl/sharedStrings.xml"))
    ok = ic.ImportLimits(max_xlsx_shared_strings_bytes=real)
    check(
        CID,
        "a shared-strings part exactly at the cap loads",
        run(data, limits=ok)[1].report.state == "completed",
    )
    low = ic.ImportLimits(max_xlsx_shared_strings_bytes=real - 1)
    _e, outcome = run(data, limits=low)
    check(
        CID,
        "one byte over the cap is fatal limit_exceeded",
        failed_with(outcome, "limit_exceeded") and outcome.report.fatal_locator is None,
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
        run(with_extra(GOOD, ("../e", b"x")))[1].report.fatal_locator is None,
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
        (
            # V-ROW-NUMBER-NONCANONICAL: the <row> element's own `r`, not just
            # a cell reference's row digits (already covered above).
            "leading-zero row number on the row element itself",
            head + '<row r="01"><c r="A1"/></row></sheetData></worksheet>',
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
        failed_with(outcome, "xlsx_malformed") and outcome.report.fatal_locator == "row:2",
    )
    _e, outcome = run(book(raw_sheet=head + ok_row + "<row"))
    check(
        CID,
        "a truncated tail is located at the last row read",
        failed_with(outcome, "xlsx_malformed") and outcome.report.fatal_locator == "row:1",
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
        run(book(extra=[("xl/vbaProject.bin", b"M")]))[1].report.fatal_locator is None,
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
    preview_book = book([[S("q"), S("a")]] + [[S(f"r{i}"), S("x")] for i in range(30)])
    stream = xi.xlsx_events(
        make_reader(preview_book),
        build_mapping(),
        verified_content_type=XLSX_TYPE,
        dataset_sha256=hashlib.sha256(preview_book).hexdigest(),
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
        bool({fd for fd, _t in open_spool_descriptors()} - before),
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
    tempfile.TemporaryFile = lambda *a, **k: FullDisk()  # type: ignore[assignment]
    try:
        _e, outcome = run(GOOD)
    finally:
        tempfile.TemporaryFile = original
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
    )
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
    leaks = support.leaked_markers(
        (outcome.report, outcome.report.fatal_locator), [m_val, m_head, m_row]
    )
    check(CID, f"no content in the report ({leaks})", not leaks)
    _e, outcome = run(book([[IS("q"), IS("a"), IS(m_head), IS(m_head)]]))
    check(
        CID,
        "a duplicate-header failure names no header text",
        not support.leaked_markers((outcome.report, outcome.report.fatal_locator), [m_head]),
    )
    _e, outcome = run(book(), sheet=m_sheet[:30])
    check(
        CID,
        "a missing-sheet failure names no sheet text",
        not support.leaked_markers((outcome.report, outcome.report.fatal_locator), [m_sheet[:30]]),
    )
    _e, outcome = run(with_extra(GOOD, (f"../{m_val}.xml", b"x")))
    check(
        CID,
        "an archive failure names no member name",
        not support.leaked_markers((outcome.report, outcome.report.fatal_locator), [m_val]),
    )
    _e, outcome = run(book(extra=[("xl/vbaProject.bin", m_val.encode())]))
    check(
        CID,
        "a macro failure names no content",
        not support.leaked_markers((outcome.report, outcome.report.fatal_locator), [m_val]),
    )
    _e, outcome = run(
        book(raw_sheet=f"{XML_HEAD}<worksheet xmlns='{MAIN}'><sheetData><row>{m_val}")
    )
    check(
        CID,
        "an XML failure names no content",
        not support.leaked_markers((outcome.report, outcome.report.fatal_locator), [m_val]),
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
    for bad in (
        "nope",
        "source_changed",
        "csv_structure_invalid",
        "XLSX_MALFORMED",
        " xlsx_malformed",
        "xlsx_malformed\n",
    ):
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
    for bad in ("nope", "parse_timeout_x", "UNSAFE_ARCHIVE", " unsafe_archive", "unsafe_archive "):
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


# --- generated tables and properties -----------------------------------------------------

CELL_KINDS = ("s", "inlineStr", "str", "n", "b", "e", "d", "none")
FORMULAS = ("none", "normal", "shared-empty", "array")


def generated_cell(kind: str, formula: str, ref: str, sst: Shared, text: str) -> str:
    attr = "" if kind == "none" else f' t="{kind}"'
    f = {
        "none": "",
        "normal": "<f>A1+1</f>",
        "shared-empty": '<f t="shared" si="3"/>',
        "array": f'<f t="array" ref="{ref}">1</f>',
    }[formula]
    if kind == "s":
        return f'<c r="{ref}"{attr}>{f}<v>{sst.add(text)}</v></c>'
    if kind == "inlineStr":
        return f'<c r="{ref}"{attr}>{f}<is><t>{escape(text)}</t></is></c>'
    return f'<c r="{ref}"{attr}>{f}<v>{escape(text)}</v></c>'


def test_generated_cell_type_by_formula_by_column_table() -> None:
    for kind in CELL_KINDS:
        for formula in FORMULAS:
            for mapped in (True, False):
                sst = Shared()
                header = (
                    '<row r="1"><c r="A1" t="inlineStr"><is><t>q</t></is></c>'
                    '<c r="B1" t="inlineStr"><is><t>a</t></is></c>'
                    '<c r="C1" t="inlineStr"><is><t>n</t></is></c></row>'
                )
                target = "B2" if mapped else "C2"
                cells = {
                    "A2": '<c r="A2" t="inlineStr"><is><t>P</t></is></c>',
                    "B2": '<c r="B2" t="inlineStr"><is><t>C</t></is></c>',
                    "C2": '<c r="C2" t="inlineStr"><is><t>N</t></is></c>',
                }
                cells[target] = generated_cell(kind, formula, target, sst, "VALUE")
                raw = (
                    f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>{header}<row r="2">'
                    f"{cells['A2']}{cells['B2']}{cells['C2']}</row></sheetData></worksheet>"
                )
                data = book(raw_sheet=raw, shared_override=shared_xml(sst.strings).encode())
                events, outcome = run(data)
                label = f"type={kind} formula={formula} mapped={mapped}"
                is_formula = formula != "none" or kind == "str"
                if not mapped:
                    check(
                        CID,
                        f"{label}: an unmapped cell never rejects the row",
                        len(accepted(events)) == 1 and not outcome.report.rejections,
                    )
                elif is_formula:
                    check(
                        CID,
                        f"{label}: formula_cell",
                        outcome.report.rejections
                        == (ic.RowRejection("row:2", "formula_cell", "completion"),),
                    )
                elif kind == "e":
                    check(
                        CID,
                        f"{label}: cell_error_value",
                        outcome.report.rejections
                        == (ic.RowRejection("row:2", "cell_error_value", "completion"),),
                    )
                else:
                    got = accepted(events)
                    check(
                        CID,
                        f"{label}: imported as the literal text",
                        len(got) == 1 and texts(got[0]) == ["P", "VALUE"],
                    )


def random_text(rng: Any) -> str:
    alphabet = [
        "a", "b", " ", "1", "0", "٠", "م", "ر", "é", "́", "😀", "&", "<", ">", '"', "'",
        "=", "+", "-", "@", "\t", "‏", "‮", "_", "x", " ", "ß", "İ",
    ]  # fmt: skip
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12)))


def xml_safe(text: str) -> str:
    """The way a spreadsheet writer stores `text`: markup escaped, tab / CR / LF kept
    literal where XML allows (CR as an _x000D_ escape, which XML would normalise away)."""
    return (
        escape(text).replace("\r", "_x000D_").replace("_x", "_x005F_x")
        if "_x" in text
        else escape(text)
    )


def test_random_workbooks_round_trip_exactly() -> None:
    import random

    rng = random.Random(505)
    for case in range(120):
        count = rng.randint(1, 12)
        pairs = [(random_text(rng), random_text(rng)) for _ in range(count)]
        rows = [[S("q"), S("a")]]
        for prompt, completion in pairs:
            first = S(prompt) if rng.random() < 0.5 else IS(prompt)
            second = S(completion) if rng.random() < 0.5 else IS(completion)
            rows.append([first, second])
        data = book(rows)
        chunk = rng.choice([None, 1, 7, 100, 4096])
        events, outcome = run(data, chunk=chunk)
        got = [texts(c) for c in accepted(events)]
        # whitespace-only / empty strings are empty cells to the mapping
        expected = [[p, c] for p, c in pairs if p != "" and c != ""]
        check(
            CID,
            f"random workbook #{case} chunk={chunk}: every text survives exactly",
            outcome.report.state == "completed" and got == expected,
        )


def test_encodings_of_the_xml_parts_are_read_by_expat() -> None:
    sheet = sheet_xml([[IS("q"), IS("a")], [IS("مرحبا"), IS("ok")]], Shared())
    for label, payload in (
        ("utf-8", sheet.encode("utf-8")),
        ("utf-8 with a BOM", b"\xef\xbb\xbf" + sheet.encode("utf-8")),
        ("utf-16", utf16(sheet)),
        ("utf-16-be", sheet.replace('encoding="UTF-8"', 'encoding="UTF-16BE"').encode("utf-16-be")),
    ):
        events, outcome = run(book(raw_sheet=payload))
        got = [texts(c) for c in accepted(events)]
        check(
            CID,
            f"a {label} sheet part imports",
            outcome.report.state == "completed" and got == [["مرحبا", "ok"]],
        )
    shared = shared_xml(["q", "a", "مرحبا"])
    cells = [
        [RAWC('<c r="{ref}" t="s"><v>0</v></c>'), RAWC('<c r="{ref}" t="s"><v>1</v></c>')],
        [RAWC('<c r="{ref}" t="s"><v>2</v></c>'), IS("ok")],
    ]
    events, outcome = run(book(cells, shared_override=utf16(shared)))
    check(
        CID,
        "a UTF-16 sharedStrings part imports",
        [texts(c) for c in accepted(events)] == [["مرحبا", "ok"]],
    )


def test_rejected_rows_consume_a_record_index() -> None:
    rows = [
        [S("q"), S("a")],
        [S("bad"), F()],
        [S("good"), S("x")],
        [S("bad2"), E()],
        [S("good2"), S("y")],
    ]
    events, outcome = run(book(rows))
    check(
        CID,
        "record_index counts every data row, rejected or not",
        [c["record_index"] for c in accepted(events)] == [1, 3]
        and outcome.report.records_seen == 4,
    )


def test_sheet_names_with_markup_characters_match_exactly() -> None:
    for name in ("A&B", "A<B>", 'say "hi"', "it's", "مرحبا", "a b", "Sheet.1", "x" * 31):
        events, outcome = run(book(sheet=name), sheet=name)
        check(
            CID,
            f"sheet {name!r} is found by its exact name",
            outcome.report.state == "completed" and len(accepted(events)) == 1,
        )


def test_memory_is_bounded_by_the_row_not_the_sheet() -> None:
    import tracemalloc

    def peak_for(rows: int) -> tuple[int, int, int]:
        body = "".join(
            f'<row r="{r}"><c r="A{r}" t="inlineStr"><is><t>prompt {r} {"x" * 60}</t></is></c>'
            f'<c r="B{r}" t="inlineStr"><is><t>answer {r}</t></is></c></row>'
            for r in range(2, rows + 2)
        )
        head = (
            '<row r="1"><c r="A1" t="inlineStr"><is><t>q</t></is></c>'
            '<c r="B1" t="inlineStr"><is><t>a</t></is></c></row>'
        )
        raw = f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>{head}{body}</sheetData></worksheet>'
        data = book(raw_sheet=raw)
        count = [0]
        tracemalloc.start()
        try:
            outcome = xi.run_xlsx_import(
                make_reader(data, 64 * 1024),
                build_mapping(),
                verified_content_type=XLSX_TYPE,
                dataset_purpose="training",
                expected_sha256=hashlib.sha256(data).hexdigest(),
                on_accepted=lambda _c: count.__setitem__(0, count[0] + 1),
            )
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        ok = outcome.report.state == "completed" and count[0] == rows
        check(CID, f"the {rows}-row sheet imported", ok)
        return peak, len(raw), count[0]

    small_peak, small_size, _ = peak_for(2_000)
    large_peak, large_size, _ = peak_for(8_000)
    check(CID, "the large sheet really is 4x the small one", large_size > 3.5 * small_size)
    check(
        CID,
        f"4x the rows needs under 1.5x the peak memory ({small_peak} -> {large_peak})",
        large_peak < 1.5 * small_peak,
    )
    check(
        CID,
        f"and the peak {large_peak} is far below the {large_size} byte sheet",
        large_peak < large_size // 2,
    )


def _wide_cell_row(row_number: int, cols: int, pad: int) -> str:
    cells = "".join(
        f'<c r="{_col_letters(c)}{row_number}" t="inlineStr"><is><t>{"z" * pad}</t></is></c>'
        for c in range(cols)
    )
    return f'<row r="{row_number}">{cells}</row>'


def _col_letters(zero_based: int) -> str:
    n = zero_based + 1
    letters = ""
    while n:
        n, remainder = divmod(n - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def test_header_row_skip_does_not_retain_the_skipped_rows_cells() -> None:
    """HEADER-ROW-UNBOUNDED-MEMORY: `header_row` tells the importer to skip some
    number of non-blank rows before taking the header. Before this fix, every
    cell of every row was kept in full (`wanted` is still None: the header has
    not been read yet) even for a row the caller is about to discard unread --
    so one wide, deeply padded row the caller will never use could turn a
    roughly one-megabyte upload into a transient gigabyte-scale peak, 'ZIP/XML
    bombs fail safely' notwithstanding. `tracemalloc`, started only after the
    attack payload itself is built (so building it is never counted against
    the import), isolates the import's own peak exactly as the sibling
    `test_memory_is_bounded_by_the_row_not_the_sheet` above does."""
    import tracemalloc

    cols, pad = 200, 50_000  # ~10 MB in one skipped row if ever retained
    skip_row = _wide_cell_row(1, cols, pad)
    header_row_xml = (
        '<row r="2"><c r="A2" t="inlineStr"><is><t>q</t></is></c>'
        '<c r="B2" t="inlineStr"><is><t>a</t></is></c></row>'
    )
    data_row_xml = (
        '<row r="3"><c r="A3" t="inlineStr"><is><t>hi</t></is></c>'
        '<c r="B3" t="inlineStr"><is><t>there</t></is></c></row>'
    )
    raw = (
        f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>'
        f"{skip_row}{header_row_xml}{data_row_xml}</sheetData></worksheet>"
    )
    data = book(raw_sheet=raw)
    reader = make_reader(data, 64 * 1024)
    tracemalloc.start()
    try:
        outcome = xi.run_xlsx_import(
            reader,
            build_mapping(header_row=1),
            verified_content_type=XLSX_TYPE,
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(data).hexdigest(),
            on_accepted=lambda _c: None,
        )
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    check(
        CID,
        "the sheet with a wide skipped row before the header still imports",
        outcome.report.state == "completed",
    )
    check(
        CID,
        f"a {cols * pad} byte skipped row costs well under 2 MB of real peak memory (saw {peak})",
        peak < 2 * 1024 * 1024,
    )


def test_shared_strings_do_not_cost_more_than_their_byte_cap_regardless_of_script() -> None:
    """A non-Latin-1 character anywhere in a Python `str` widens that whole
    string's own storage to 2 or 4 bytes per character -- not only the wide
    character's own, every character in it -- so storing the `sst` part's
    strings as `str` cost up to ~3.4x the declared `max_shared_strings_bytes`
    quota for an ordinary multilingual workbook (one emoji or non-Latin-1
    letter per otherwise-ASCII string). Stored as `bytes` (decoded back to
    `str` only for a string a cell actually uses), the real peak tracks the
    byte cap directly, whatever script the text is in."""
    import tracemalloc

    cap = 1_000_000
    n_strings = 1100
    strings = [("a" * 900 + "\U0001f600") for _ in range(n_strings)]  # ~901 bytes each, one emoji
    # S() cells number shared strings by the order cells appear in `rows` (header
    # "q"/"a" first); the override must carry those same indices, with the bulk
    # payload strings appended after, purely to fill the aggregate budget.
    sst = shared_xml(["q", "a", strings[0], "x", *strings[1:]]).encode("utf-8")
    rows = [[S("q"), S("a")], [S(strings[0]), S("x")]]
    data = book(rows, shared_override=sst)
    limits = ic.ImportLimits(max_xlsx_shared_strings_bytes=cap)
    reader = make_reader(data)
    tracemalloc.start()
    try:
        outcome = xi.run_xlsx_import(
            reader,
            build_mapping(),
            verified_content_type=XLSX_TYPE,
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(data).hexdigest(),
            limits=limits,
            on_accepted=lambda _c: None,
        )
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    check(
        CID,
        "the aggregate cap still stops a multilingual shared-strings part",
        failed_with(outcome, "limit_exceeded"),
    )
    check(
        CID,
        f"peak memory ({peak}) stays within 2x the {cap} byte cap, not the ~3.4x a wide "
        "Python str would cost",
        peak < 2 * cap,
    )
    # A small, correctness-only round trip: a non-Latin-1 shared string still
    # decodes back to exactly itself once looked up by a real cell.
    word = "café \U0001f600 مرحبا"
    small_sst = shared_xml(["q", "a", word, "x"]).encode("utf-8")
    small_data = book(
        [[S("q"), S("a")], [S(word), S("x")]],
        shared_override=small_sst,
    )
    events2, outcome2 = run(small_data)
    check(
        CID,
        "a non-Latin-1 shared string round-trips exactly through the bytes-backed store",
        outcome2.report.state == "completed" and texts(accepted(events2)[0]) == [word, "x"],
    )


def test_shared_strings_rss_at_the_real_production_cap() -> None:
    """The round-2 test above only ever ran at a 1 MiB toy cap -- real bytes
    retained (`tracemalloc`, Python-level) track the cap almost exactly, but
    process RSS (real pages touched, including the C-level zlib/expat/malloc
    overhead `tracemalloc` cannot see) is a separate question, and only shows
    up at a realistic scale. Measured in a fresh subprocess, never this
    process (which has already built the attack payload itself, and that
    construction cost -- unrelated to the import -- would otherwise dominate
    any same-process RSS reading). 32 MiB (32x the round-2 test's own cap, not
    the production 256 MiB default): the measured ratio is the same at both
    scales (confirmed separately, not committed as a second slow test) and
    this one keeps the mutation gate's per-mutant cost -- the whole file,
    including this test, runs once per mutant -- in proportion."""
    import subprocess

    script = f"""
import ctypes, hashlib, sys
sys.path.insert(0, {str(ROOT)!r})
sys.path.insert(0, {str(ROOT / "tests" / "tasks")!r})
import import_test_support as support
from workers.data import import_common as ic
from workers.data import xlsx_importer as xi
import test_ftf_0505 as t

cap = 32 * 1024 ** 2
n_strings = 40000  # comfortably past the cap (~904 bytes each, ~34.5 MiB), not just under it
strings = [("a" * 900 + "\\U0001f600") for _ in range(n_strings)]
sst = t.shared_xml(["q", "a", *strings]).encode("utf-8")
data = t.book([[t.S("q"), t.S("a")], [t.S("hi"), t.S("there")]], shared_override=sst)

ctypes.CDLL("libc.so.6").malloc_trim(0)
def vmrss_kb():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS"):
                return int(line.split()[1])

baseline = vmrss_kb()
reader = t.make_reader(data, 64 * 1024)
outcome = xi.run_xlsx_import(
    reader,
    t.build_mapping(),
    verified_content_type=t.XLSX_TYPE,
    dataset_purpose="training",
    expected_sha256=hashlib.sha256(data).hexdigest(),
    limits=ic.ImportLimits(max_xlsx_shared_strings_bytes=cap),
    on_accepted=lambda c: None,
)
after = vmrss_kb()
print(outcome.report.state, outcome.report.failure_code, baseline, after)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )
    check(
        CID,
        f"the RSS subprocess ran cleanly: {result.stderr[-2000:]}",
        result.returncode == 0,
    )
    if result.returncode != 0:
        return
    state, code, baseline_kb, after_kb = result.stdout.strip().split()
    cap = 32 * 1024**2
    delta_bytes = (int(after_kb) - int(baseline_kb)) * 1024
    check(
        CID,
        "the aggregate cap still stops a multilingual shared-strings part at real scale",
        state == "failed" and code == "limit_exceeded",
    )
    check(
        CID,
        f"real process RSS for the import itself ({delta_bytes} bytes, "
        f"{delta_bytes / cap:.2f}x the {cap}-byte cap) stays within 2x it",
        delta_bytes < 2 * cap,
    )


def test_concurrent_imports_with_different_limits_do_not_interfere() -> None:
    import threading

    data = sized_sheet(40)
    results: dict[str, str] = {}

    def worker(name: str, limits: ic.ImportLimits) -> None:
        for _ in range(5):
            _e, outcome = run(data, limits=limits)
            results[name] = f"{outcome.report.state}:{outcome.report.failure_code}"

    threads = [
        threading.Thread(target=worker, args=("low", ic.ImportLimits(max_xlsx_cells=10))),
        threading.Thread(target=worker, args=("default", ic.DEFAULT_LIMITS)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check(
        CID,
        "the low-limit import fails on its own limit",
        results["low"] == "failed:too_many_cells",
    )
    check(CID, "the default import is untouched by it", results["default"] == "completed:None")


def test_directory_padding_counts_against_the_total_cap() -> None:
    pad = b"\x00" * (3 * 1024 * 1024)
    data = with_extra(GOOD, ("docProps/pad.bin", pad))
    cap = xi.XlsxLimits(max_total_uncompressed_bytes=1024 * 1024)
    _e, outcome = run(data, xlsx_limits=cap)
    check(
        CID,
        "a never-read member still counts against the declared total",
        failed_with(outcome, "unsafe_archive"),
    )
    check(
        CID,
        "and the same archive imports under the default cap",
        run(data)[1].report.state == "completed",
    )


def test_part_names_that_differ_only_by_whitespace_or_folding_rules() -> None:
    near = t501._zip([*members_of(GOOD), ("docProps/a.txt", b"1"), ("docProps/a.txt ", b"2")])
    check(
        CID,
        "names differing by a trailing space are distinct parts",
        run(near)[1].report.state == "completed",
    )
    sharp = t501._zip([*members_of(GOOD), ("docProps/ß.txt", b"1"), ("docProps/SS.txt", b"2")])
    check(
        CID,
        "names equal only after Unicode casefold are duplicates",
        failed_with(run(sharp)[1], "unsafe_archive"),
    )
    dotted = t501._zip([*members_of(GOOD), ("docProps/İ.txt", b"1"), ("docProps/i̇.txt", b"2")])
    check(
        CID,
        "casefold-equal names (dotted capital I) are duplicates",
        failed_with(run(dotted)[1], "unsafe_archive"),
    )


# --- the defensive layers, each killed on its own -------------------------------------------


class _LyingArchive:
    """A zip reader whose members inflate to more than their directory claims: what a
    `zipfile` that stopped enforcing the declared size would hand over."""

    def __init__(self, produced: int) -> None:
        self._produced = produced

    def open(self, _info: object) -> Any:
        outer = self

        class Handle:
            def __init__(self) -> None:
                self.left = outer._produced

            def __enter__(self) -> Handle:
                return self

            def __exit__(self, *_a: object) -> None:
                return None

            def read(self, size: int) -> bytes:
                take = min(size, self.left)
                self.left -= take
                return b"\x00" * take

        return Handle()


def drain(archive: Any, *, part_cap: int, remaining: int, code: str = "unsafe_archive") -> str:
    try:
        total = sum(
            len(block)
            for block in ox.read_part_blocks(
                archive,
                zipfile.ZipInfo("x"),
                part_cap=part_cap,
                budget=ox.PartBudget(remaining),
                over_cap_code=code,
            )
        )
    except ox.OoxmlRefused as exc:
        return exc.code
    return f"read {total}"


def test_bytes_are_counted_as_they_are_read_not_as_the_directory_claims() -> None:
    block = ox.READ_BLOCK_BYTES
    lying = _LyingArchive(3 * block)
    big = 10 * block
    check(
        CID,
        "within both caps the part is read whole",
        drain(lying, part_cap=big, remaining=big) == f"read {3 * block}",
    )
    check(
        CID,
        "exactly at the part cap is allowed",
        drain(lying, part_cap=3 * block, remaining=big) == f"read {3 * block}",
    )
    check(
        CID,
        "one byte over the part cap raises the part's own code",
        drain(lying, part_cap=3 * block - 1, remaining=big, code="limit_exceeded")
        == "limit_exceeded",
    )
    check(
        CID,
        "exactly at the archive-wide budget is allowed",
        drain(lying, part_cap=big, remaining=3 * block) == f"read {3 * block}",
    )
    check(
        CID,
        "one byte over the archive-wide budget is unsafe_archive",
        drain(lying, part_cap=big, remaining=3 * block - 1) == "unsafe_archive",
    )
    check(
        CID,
        "the part cap is checked even when the budget is huge",
        drain(lying, part_cap=block, remaining=10**12, code="limit_exceeded") == "limit_exceeded",
    )
    ticks: list[int] = []
    list(
        ox.read_part_blocks(
            lying,  # type: ignore[arg-type]
            zipfile.ZipInfo("x"),
            part_cap=big,
            budget=ox.PartBudget(big),
            tick=lambda: ticks.append(1),
        )
    )
    check(
        CID, "the caller's tick runs before every block (and once more at the end)", len(ticks) == 4
    )
    budget = ox.PartBudget(100)
    budget.spend(100)
    check(CID, "spending exactly the budget leaves zero", budget.remaining == 0)
    try:
        budget.spend(1)
    except ox.OoxmlRefused as exc:
        check(CID, "spending past the budget is unsafe_archive", exc.code == "unsafe_archive")
    else:
        check(CID, "spending past the budget is refused", False)

    # a part that raises while it is read is a damaged entry, never a crash
    class Broken:
        def open(self, _i: object) -> Any:
            raise zipfile.BadZipFile("Bad CRC-32")

    check(
        CID,
        "a damaged entry is xlsx_malformed",
        drain(Broken(), part_cap=big, remaining=big) == "xlsx_malformed",
    )

    # ZLIB-ERROR-ESCAPES: a corrupt deflate stream raises zlib.error, not one of
    # the exception types read_part_blocks mapped before this fix.
    class BrokenZlib:
        def open(self, _i: object) -> Any:
            return self

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

        def read(self, _size: int) -> bytes:
            import zlib

            raise zlib.error("Error -3 while decompressing data: invalid code lengths set")

    check(
        CID,
        "a corrupt deflate stream (zlib.error) is xlsx_malformed, not an unhandled crash",
        drain(BrokenZlib(), part_cap=big, remaining=big) == "xlsx_malformed",
    )


def _corrupt_deflate_byte(data: bytes, member: str, byte_offset: int, xor: int = 0xFF) -> bytes:
    """`data`, a valid zip, with one byte of `member`'s own compressed stream
    flipped — a real, on-the-wire corrupt deflate stream, not a mock."""
    buf = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = archive.getinfo(member)
    header = info.header_offset
    name_len, extra_len = struct.unpack("<HH", bytes(buf[header + 26 : header + 30]))
    start = header + 30 + name_len + extra_len
    buf[start + byte_offset] ^= xor
    return bytes(buf)


def test_a_real_corrupt_deflate_stream_is_xlsx_malformed_not_a_crash() -> None:
    """ZLIB-ERROR-ESCAPES, end to end: `run_xlsx_import` over an archive whose
    sheet part's own compressed bytes (not its CRC, not its header) are
    corrupted. Before this fix, `zlib.error` propagated out of `run_xlsx_import`
    uncaught; it must come back as an ordinary `failed`/`xlsx_malformed` report,
    the same as any other damaged entry."""
    import zlib

    padding = "pad " * 400  # compresses well; several blocks before the corrupt byte
    rows = [[S("q"), S("a")], [S(padding), S("x")], [S("hi"), S("there")]]
    data = book(rows)
    seen_malformed = 0
    for offset in range(4, 40):
        corrupted = _corrupt_deflate_byte(data, "xl/worksheets/sheet1.xml", offset)
        try:
            with (
                zipfile.ZipFile(io.BytesIO(corrupted)) as archive,
                archive.open("xl/worksheets/sheet1.xml") as handle,
            ):
                handle.read()
        except zlib.error:
            pass
        else:
            continue  # this particular flip happened not to break decompression
        reader = make_reader(corrupted, expected_sha=hashlib.sha256(corrupted).hexdigest())
        outcome = xi.run_xlsx_import(
            reader,
            build_mapping(),
            verified_content_type=XLSX_TYPE,
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(corrupted).hexdigest(),
            on_accepted=lambda _c: None,
        )
        check(
            CID,
            f"byte offset {offset}: a real zlib.error completes as failed/xlsx_malformed",
            failed_with(outcome, "xlsx_malformed"),
        )
        seen_malformed += 1
    check(CID, "at least one flipped byte actually produced a real zlib.error", seen_malformed > 0)


def test_a_malformed_part_that_mentions_a_declaration_is_an_attempt() -> None:
    def verdict(part: str, payload: bytes) -> str:
        outcome = run(with_member(GOOD, part, payload))[1]
        return str(outcome.report.failure_code)

    wb = "xl/workbook.xml"
    ns = f' xmlns="{MAIN}"'.encode()
    mentions = [
        ("lower-case doctype", b"<workbook" + ns + b"><!doctype x></workbook>"),
        ("upper-case doctype", b"<workbook" + ns + b"><!DOCTYPE x></workbook>"),
        ("entity declaration", b"<workbook" + ns + b'><!ENTITY a "b"></workbook>'),
        ("mixed-case entity", b"<workbook" + ns + b'><!Entity a "b"></workbook>'),
        ("utf-16 doctype", f"<workbook{ns.decode()}><!DOCTYPE x></workbook>".encode("utf-16")),
        (
            "utf-16-le doctype",
            b"\xff\xfe" + f"<workbook{ns.decode()}><!DOCTYPE x>".encode("utf-16-le"),
        ),
        (
            "utf-16-be doctype",
            b"\xfe\xff" + f"<workbook{ns.decode()}><!DOCTYPE x>".encode("utf-16-be"),
        ),
    ]
    for label, payload in mentions:
        check(
            CID,
            f"a malformed workbook mentioning a {label} is unsafe_archive",
            verdict(wb, payload) == "unsafe_archive",
        )
    for label, payload in (
        ("plain garbage", b"<workbook" + ns + b"><sheets></workbook>"),
        (
            "a comment that says doctype-ish",
            b"<workbook" + ns + b"><!-- doc type --><sheets></workbook>",
        ),
        ("an unrelated declaration word", b"<workbook" + ns + b"><ENTITLEMENT></workbook>"),
    ):
        check(
            CID,
            f"a malformed workbook with {label} is xlsx_malformed",
            verdict(wb, payload) == "xlsx_malformed",
        )
    check(
        CID,
        "a malformed [Content_Types].xml that mentions an entity is unsafe_archive",
        verdict("[Content_Types].xml", b'<Types><!ENTITY a "b"></Types>') == "unsafe_archive",
    )
    check(
        CID,
        "a malformed rels part that mentions a doctype is unsafe_archive",
        verdict("xl/_rels/workbook.xml.rels", b"<Relationships><!DOCTYPE x></Relationships>")
        == "unsafe_archive",
    )
    check(
        CID,
        "a malformed rels part otherwise is xlsx_malformed",
        verdict("xl/_rels/workbook.xml.rels", b"<Relationships>") == "xlsx_malformed",
    )


def test_namespaces_and_element_names_are_matched_exactly() -> None:
    sheet = sheet_xml([[IS("q"), IS("a")], [IS("p"), IS("c")]], Shared())
    upper_ns = MAIN.upper()
    cases = [
        (
            "an upper-cased worksheet namespace",
            book(raw_sheet=sheet.replace(MAIN, upper_ns)),
            "xlsx_malformed",
        ),
        (
            "an upper-cased workbook namespace",
            book(replace={"xl/workbook.xml": workbook_xml(ns=upper_ns).encode()}),
            "xlsx_malformed",
        ),
        (
            "an upper-cased sharedStrings namespace",
            book(shared_override=shared_xml(["x"]).replace(MAIN, upper_ns).encode()),
            "xlsx_malformed",
        ),
        (
            "an upper-cased content-types namespace (no Override is seen)",
            book(
                replace={
                    "[Content_Types].xml": content_types().replace(CT_NS, CT_NS.upper()).encode()
                }
            ),
            "unsupported_workbook",
        ),
        (
            "an upper-cased relationships namespace (no relationship is seen)",
            book(
                replace={
                    "xl/_rels/workbook.xml.rels": workbook_rels().replace(PKG, PKG.upper()).encode()
                }
            ),
            "xlsx_malformed",
        ),
    ]
    for label, data, code in cases:
        check(CID, f"{label}: fatal {code}", failed_with(run(data)[1], code))
    # an upper-cased element name is a different, unknown element and is not read as data
    events, outcome = run(book(raw_sheet=sheet.replace("<row", "<Row").replace("</row", "</Row")))
    check(
        CID,
        "<Row> is not a row",
        failed_with(outcome, "mapping_column_missing") and not accepted(events),
    )
    events, _o = run(
        book(
            raw_sheet=sheet.replace("<is>", "<IS>")
            .replace("</is>", "</IS>")
            .replace("<t>", "<T>")
            .replace("</t>", "</T>")
        )
    )
    check(CID, "<IS>/<T> are not inline text", not accepted(events))
    si_upper = f'{XML_HEAD}<sst xmlns="{MAIN}"><si><T>seen?</T></si></sst>'
    rows = [[IS("q"), IS("a")], [RAWC('<c r="{ref}" t="s"><v>0</v></c>'), IS("x")]]
    events, outcome = run(book(rows, shared_override=si_upper.encode()))
    check(
        CID,
        "<T> in sharedStrings is not text: the string is empty",
        codes(outcome) == ["required_field_empty"],
    )


def test_shared_strings_structure_faults() -> None:
    rows = [[IS("q"), IS("a")], [RAWC('<c r="{ref}" t="s"><v>0</v></c>'), IS("x")]]
    bad = f'{XML_HEAD}<sst xmlns="{MAIN}"><si><t>a<b/>c</t></si></sst>'
    check(
        CID,
        "an element inside a shared-string <t> is fatal",
        failed_with(run(book(rows, shared_override=bad.encode()))[1], "xlsx_malformed"),
    )
    spaced = (
        f'{XML_HEAD}<sst xmlns="{MAIN}">'
        + " " * 5000
        + "<si><t>ok</t></si>"
        + " \n " * 5000
        + "</sst>"
    )
    events, outcome = run(
        book(rows, shared_override=spaced.encode()), limits=ic.ImportLimits(max_record_bytes=50)
    )
    check(CID, "whitespace between <si> elements is not string content", len(accepted(events)) == 1)
    runs = (
        f'{XML_HEAD}<sst xmlns="{MAIN}"><si>'
        + "<r><t>"
        + "x" * 30
        + "</t></r>"
        + "<r><t>"
        + "y" * 30
        + "</t></r></si></sst>"
    )
    _e, outcome = run(
        book(rows, shared_override=runs.encode()), limits=ic.ImportLimits(max_record_bytes=50)
    )
    check(
        CID,
        "runs of one string add up against the string bound",
        failed_with(outcome, "field_too_large"),
    )
    truncated = f'{XML_HEAD}<sst xmlns="{MAIN}"><si><t>never closed'
    check(
        CID,
        "a truncated sharedStrings part is fatal xlsx_malformed",
        failed_with(run(book(rows, shared_override=truncated.encode()))[1], "xlsx_malformed"),
    )
    gone = book(
        rows,
        rel_extra="",
        replace={
            "xl/_rels/workbook.xml.rels": workbook_rels(shared=False)
            .replace(
                "</Relationships>",
                f'<Relationship Id="rIdS" Type="{REL}/sharedStrings" Target="missing.xml"/></Relationships>',
            )
            .encode()
        },
    )
    check(
        CID,
        "a sharedStrings relationship to a missing part is xlsx_malformed",
        failed_with(run(gone)[1], "xlsx_malformed"),
    )
    no_shared = book(
        [[IS("q"), IS("a")], [IS("p"), IS("c")]],
        replace={"xl/_rels/workbook.xml.rels": workbook_rels(shared=False).encode()},
        drop=("xl/sharedStrings.xml",),
    )
    check(
        CID,
        "a workbook with no sharedStrings part at all imports inline strings",
        run(no_shared)[1].report.state == "completed",
    )
    s_without = book(
        rows,
        replace={"xl/_rels/workbook.xml.rels": workbook_rels(shared=False).encode()},
        drop=("xl/sharedStrings.xml",),
    )
    check(
        CID,
        "t=s with no shared-strings table is fatal, not an empty string",
        failed_with(run(s_without)[1], "xlsx_malformed"),
    )
    for target, label in (("../x.xml", "traversal"), ("/xl/sharedStrings.xml", "absolute")):
        data = book(
            rows,
            replace={
                "xl/_rels/workbook.xml.rels": workbook_rels(shared=False)
                .replace(
                    "</Relationships>",
                    f'<Relationship Id="rIdS" Type="{REL}/sharedStrings" Target="{target}"/></Relationships>',
                )
                .encode()
            },
        )
        check(
            CID,
            f"a sharedStrings target with {label} is unsafe_archive",
            failed_with(run(data)[1], "unsafe_archive"),
        )


def test_content_types_entries_must_be_complete() -> None:
    for label, extra in (
        ("an Override with no PartName", '<Override ContentType="application/xml"/>'),
        ("an Override with no ContentType", '<Override PartName="/xl/x.xml"/>'),
        ("a Default with no ContentType", '<Default Extension="bin"/>'),
    ):
        data = book(ct_extra=extra)
        check(CID, f"{label}: xlsx_malformed", failed_with(run(data)[1], "xlsx_malformed"))
    data = book(ct_extra='<Default Extension="bin" ContentType="application/octet-stream"/>')
    check(CID, "a complete extra Default is fine", run(data)[1].report.state == "completed")


def test_mapped_headers_match_in_both_directions() -> None:
    sheet = book([[IS("q"), IS("Aa")], [IS("p"), IS("c")]])
    attempts: list[tuple[str, dict[str, Any]]] = [
        ("an upper-case mapping against a lower-case header", {"prompt": {"header": "Q"}}),
        ("a mapping with a trailing space", {"prompt": {"header": "q "}}),
        ("a lower-case mapping against a mixed-case header", {"completion": {"header": "aa"}}),
        ("a mapping with a leading space", {"completion": {"header": " Aa"}}),
        ("an upper-cased mapping of a mixed-case header", {"completion": {"header": "AA"}}),
    ]
    for label, kwargs in attempts:
        kwargs.setdefault("completion", {"header": "Aa"})
        _e, outcome = run(sheet, **kwargs)
        check(CID, f"{label}: no column", failed_with(outcome, "mapping_column_missing"))
    events, _o = run(sheet, completion={"header": "Aa"})
    check(CID, "the exact header maps", len(accepted(events)) == 1)


def test_unmapped_columns_are_dropped_after_the_header_across_blocks() -> None:
    # more than one 64 KiB block of rows with several unmapped columns each
    unmapped = "".join(
        f'<c r="{col_letters(i)}{{r}}" t="inlineStr"><is><t>{"z" * 20}</t></is></c>'
        for i in range(2, 8)
    )
    header = '<row r="1"><c r="A1" t="inlineStr"><is><t>q</t></is></c><c r="B1" t="inlineStr"><is><t>a</t></is></c></row>'
    body = "".join(
        f'<row r="{r}"><c r="A{r}" t="inlineStr"><is><t>p{r}</t></is></c><c r="B{r}" t="inlineStr"><is><t>c{r}</t></is></c>{unmapped.replace("{r}", str(r))}</row>'
        for r in range(2, 1200)
    )
    raw = f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>{header}{body}</sheetData></worksheet>'
    check(CID, "the sheet spans several inflate blocks", len(raw) > 3 * ox.READ_BLOCK_BYTES)
    events, outcome = run(book(raw_sheet=raw))
    got = [texts(c) for c in accepted(events)]
    check(
        CID,
        "every row maps the right two columns",
        got == [[f"p{r}", f"c{r}"] for r in range(2, 1200)],
    )
    # the same sheet, one row of which has an unmapped formula after the header was read
    bad = raw.replace(
        f'<c r="C1100" t="inlineStr"><is><t>{"z" * 20}</t></is></c>',
        '<c r="C1100"><f>1</f><v>1</v></c>',
    )
    events, outcome = run(book(raw_sheet=bad))
    check(
        CID,
        "an unmapped formula cell never rejects, however late",
        outcome.report.state == "completed" and len(accepted(events)) == 1198,
    )


def test_many_wide_unmapped_shared_string_cells_cost_little_memory() -> None:
    """V-WANTED-NOT-APPLIED: once `wanted` is set (well after the header, past
    several inflate blocks), a row's unmapped cells must really be skipped --
    not decoded, not retained -- or thousands of large unmapped references to
    one shared string would cost real memory despite being thrown away. A
    mutant that stops applying `wanted` would still pass every output-only
    test (`_map_row` only ever reads the mapped positions), so only a memory
    measurement like this one catches it."""
    import tracemalloc

    big_index = 0
    last = 202
    filler_rows = "".join(
        f'<row r="{r}"><c r="A{r}" t="inlineStr"><is><t>p{r} {"x" * 1400}</t></is></c>'
        f'<c r="B{r}" t="inlineStr"><is><t>c{r}</t></is></c></row>'
        for r in range(2, last)  # several 64 KiB inflate blocks of filler, few rows
    )
    unmapped = "".join(
        f'<c r="{col_letters(i)}{last}" t="s"><v>{big_index}</v></c>' for i in range(2, 2002)
    )
    attack_row = (
        f'<row r="{last}"><c r="A{last}" t="inlineStr"><is><t>plast</t></is></c>'
        f'<c r="B{last}" t="inlineStr"><is><t>clast</t></is></c>{unmapped}</row>'
    )
    header = '<row r="1"><c r="A1" t="inlineStr"><is><t>q</t></is></c><c r="B1" t="inlineStr"><is><t>a</t></is></c></row>'
    raw = f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>{header}{filler_rows}{attack_row}</sheetData></worksheet>'
    check(CID, "the attack row is past several inflate blocks", len(raw) > 4 * ox.READ_BLOCK_BYTES)
    big_string = "Y" * (1024 * 1024)  # 1 MiB, referenced 2,000 times by unmapped cells
    sst = shared_xml([big_string]).encode("utf-8")
    data = book(raw_sheet=raw, shared_override=sst)
    tracemalloc.start()
    try:
        events, outcome = run(data)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    check(
        CID,
        "the sheet with 2,000 unmapped references to a 1 MiB string still completes",
        outcome.report.state == "completed" and len(accepted(events)) == last - 1,
    )
    check(
        CID,
        f"peak memory ({peak}) stays within a few times the 1 MiB string's own "
        "size (the shared-strings table itself), not 2,000 decoded copies of it",
        peak < 4 * len(big_string),
    )


def test_a_malformed_row_number_is_located_at_the_last_good_row() -> None:
    head = f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>'
    ok = '<row r="1"><c r="A1" t="inlineStr"><is><t>q</t></is></c><c r="B1" t="inlineStr"><is><t>a</t></is></c></row>'
    _e, outcome = run(book(raw_sheet=head + ok + '<row r="x"/></sheetData></worksheet>'))
    check(
        CID,
        "a bad row number after row 1 is located at row:1",
        failed_with(outcome, "xlsx_malformed") and outcome.report.fatal_locator == "row:1",
    )
    _e, outcome = run(book(raw_sheet=head + '<row r="x"/></sheetData></worksheet>'))
    check(
        CID,
        "a bad first row has no row to name",
        failed_with(outcome, "xlsx_malformed") and outcome.report.fatal_locator is None,
    )


def test_row_and_column_bounds_are_exact_at_the_digit_boundary() -> None:
    def cell_book(ref: str, row_attr: str) -> bytes:
        raw = f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData><row r="{row_attr}"><c r="{ref}" t="inlineStr"><is><t>x</t></is></c></row></sheetData></worksheet>'
        return book(raw_sheet=raw)

    for row, ok in (
        ("999999", True),
        ("1000000", True),
        ("1048576", True),
        ("1048577", False),
        ("9999999", False),
        ("10000000", False),
        ("99999999", False),
    ):
        _e, outcome = run(cell_book(f"A{row}", row))
        out = outcome.report.failure_code == "sheet_out_of_bounds"
        check(CID, f"row {row} is {'inside' if ok else 'outside'} the sheet", out is (not ok))
        _e, outcome = run(cell_book("A1", row))
        out = outcome.report.failure_code == "sheet_out_of_bounds"
        check(
            CID,
            f"<row r={row}> alone is {'inside' if ok else 'outside'} the sheet",
            out is (not ok),
        )
    for ref, ok in (
        ("XFC1", True),
        ("XFD1", True),
        ("XFE1", False),
        ("XFZ1", False),
        ("YAA1", False),
    ):
        _e, outcome = run(cell_book(ref, "1"))
        out = outcome.report.failure_code == "sheet_out_of_bounds"
        check(CID, f"column of {ref} is {'inside' if ok else 'outside'} the sheet", out is (not ok))
    _e, outcome = run(cell_book("A2", "1"))
    check(
        CID,
        "a cell reference naming another row is malformed, not out of bounds",
        failed_with(outcome, "xlsx_malformed"),
    )
    _e, outcome = run(cell_book("XFE2000000", "1"))
    check(
        CID,
        "an out-of-range reference beats a row mismatch",
        failed_with(outcome, "sheet_out_of_bounds"),
    )


def test_names_are_folded_the_unicode_way() -> None:
    long_s = "ſ"  # LATIN SMALL LETTER LONG S: casefolds to "s", lower() keeps it
    cases = [
        (
            "an externalLinks part spelled with a long s",
            book(extra=[(f"xl/externalLink{long_s}/e.xml", b"<e/>")]),
            "external_link_present",
        ),
        (
            "a queryTables part spelled with a long s",
            book(extra=[(f"xl/queryTable{long_s}/q.xml", b"<q/>")]),
            "external_link_present",
        ),
        (
            "a macrosheets part spelled with a long s",
            book(extra=[(f"xl/macro{long_s}heets/m.xml", b"<m/>")]),
            "unsafe_workbook",
        ),
    ]
    for label, data, code in cases:
        check(CID, f"{label}: fatal {code}", failed_with(run(data)[1], str(code)))
    # a sheet / sharedStrings target and the member it names differ only by Unicode folding
    sheet = sheet_xml([[IS("q"), IS("a")], [IS("p"), IS("c")]], Shared()).encode()
    data = t501._zip(
        [(n, d) for n, d in members_of(book()) if n != "xl/worksheets/sheet1.xml"]
        + [(f"xl/worksheets/shee{long_s}.xml", sheet)]
    )
    rels = workbook_rels([("rId1", "worksheets/shees.xml")]).encode()
    data = with_member(data, "xl/_rels/workbook.xml.rels", rels)
    check(
        CID,
        "a sheet target equal to its member only after folding resolves",
        run(data)[1].report.state == "completed",
    )
    shared = shared_xml(["x"]).encode()
    data = t501._zip(
        [
            (n, d)
            for n, d in members_of(book([[IS("q"), IS("a")], [IS("p"), IS("c")]]))
            if n != "xl/sharedStrings.xml"
        ]
        + [(f"xl/{long_s}haredStrings.xml", shared)]
    )
    rels = workbook_rels().replace("sharedStrings.xml", "sharedStrings.xml").encode()
    data = with_member(data, "xl/_rels/workbook.xml.rels", rels)
    check(
        CID,
        "a sharedStrings member spelled with a long s resolves",
        run(data)[1].report.state == "completed",
    )
    # [Content_Types].xml and the workbook relationships are found whatever their case
    renamed = t501._zip(
        [
            (
                "[CONTENT_TYPES].XML"
                if n == "[Content_Types].xml"
                else "XL/_RELS/WORKBOOK.XML.RELS"
                if n == "xl/_rels/workbook.xml.rels"
                else n,
                d,
            )
            for n, d in members_of(book())
        ]
    )
    check(
        CID,
        "OPC part names are case-insensitive for the package's own parts",
        run(renamed)[1].report.state == "completed",
    )


def test_end_record_reader_matches_ftf_0501_on_generated_tails() -> None:
    import random

    from app.datasets import direct_object_storage_upload as dos

    rng = random.Random(0x0505)
    eocd = b"PK\x05\x06"
    for case in range(4000):
        parts: list[bytes] = [bytes(rng.randrange(256) for _ in range(rng.randint(0, 40)))]
        for _ in range(rng.randint(0, 3)):
            comment = bytes(rng.randrange(256) for _ in range(rng.choice([0, 0, 1, 5, 18, 30])))
            if rng.random() < 0.5:
                comment = eocd + comment
            parts.append(
                t501._end_record(
                    rng.randrange(70_000) & 0xFFFF,
                    rng.choice([rng.randrange(2**32), 0x06054B50]),
                    rng.choice([rng.randrange(2**32), 0x06054B50]),
                    comment,
                )
            )
            if rng.random() < 0.3:
                parts.append(
                    t501._zip64_tail(
                        rng.randrange(80_000),
                        rng.randrange(2**40),
                        rng.randrange(2**40),
                        locator=rng.random() < 0.8,
                    )
                )
        tail = b"".join(parts)
        if rng.random() < 0.3:
            tail = tail[: max(0, len(tail) - rng.randint(0, 25))]
        if rng.random() < 0.2:
            tail += bytes(rng.randrange(256) for _ in range(rng.randint(1, 3)))
        got, want = ox.end_record_totals(tail), dos._end_record_totals(tail)
        if got != want:
            check(CID, f"generated tail #{case} read differently: {got} vs {want}", False)
            break
    else:
        check(CID, "4000 generated tails read exactly as FTF-0501 reads them", True)
    # hand-built cases that pin each branch of the reader
    only = t501._end_record(7, 1234, 99)
    check(
        CID,
        "a bare record with no comment is read in place",
        ox.end_record_totals(only) == (7, 1234, 99),
    )
    fake_inside = t501._end_record(1, 2, 3, eocd + b"\x00" * 14 + b"\x05\x00") + b"\x00" * 3
    check(
        CID,
        "a signature inside a comment does not hide the real record",
        ox.end_record_totals(fake_inside) == dos._end_record_totals(fake_inside),
    )
    commented = t501._end_record(9, 8, 7, b"x" * 50)
    check(
        CID,
        "a record with a comment is found by searching",
        ox.end_record_totals(commented) == (9, 8, 7),
    )
    nonzero = b"junk" + t501._end_record(4, 5, 6)[:-2] + b"\x02\x00" + b"ab"
    check(
        CID,
        "a record whose comment length is non-zero is searched for",
        ox.end_record_totals(nonzero) == dos._end_record_totals(nonzero),
    )


def test_any_doctype_is_refused_not_only_those_with_entities() -> None:
    sheet_ok = f'<worksheet xmlns="{MAIN}"><sheetData/></worksheet>'
    sst_ok = f'<sst xmlns="{MAIN}"/>'
    wb_ok = workbook_xml().split("?>\n", 1)[1]
    rels_ok = workbook_rels().split("?>\n", 1)[1]
    ct_ok = content_types().split("?>\n", 1)[1]
    declarations = [
        "<!DOCTYPE {r}>",
        '<!DOCTYPE {r} SYSTEM "http://127.0.0.1:9/evil.dtd">',
        '<!DOCTYPE {r} PUBLIC "-//X//EN" "http://127.0.0.1:9/evil.dtd">',
        "<!DOCTYPE {r} [<!ELEMENT {r} ANY>]>",
        "<!DOCTYPE {r} [<!NOTATION n SYSTEM 'x'>]>",
        "<!DOCTYPE {r} [<!ATTLIST {r} a CDATA 'd'>]>",
    ]
    targets = [
        ("xl/worksheets/sheet1.xml", sheet_ok, "worksheet"),
        ("xl/sharedStrings.xml", sst_ok, "sst"),
        ("xl/workbook.xml", wb_ok, "workbook"),
        ("xl/_rels/workbook.xml.rels", rels_ok, "Relationships"),
        ("[Content_Types].xml", ct_ok, "Types"),
    ]
    for part, body, root in targets:
        for declaration in declarations:
            doc = XML_HEAD + declaration.format(r=root) + body
            for label, payload in (("utf-8", doc.encode()), ("utf-16", utf16(doc))):
                _e, outcome = run(with_member(GOOD, part, payload))
                check(
                    CID,
                    f"{part}: {declaration[:40]!r} ({label}) is unsafe_archive",
                    failed_with(outcome, "unsafe_archive"),
                )


def test_entry_count_is_checked_again_after_the_directory_is_read() -> None:
    members = [
        *members_of(GOOD),
        *[(f"docProps/f{i}.txt", b"x") for i in range(10_001 - len(members_of(GOOD)))],
    ]
    real = bytearray(t501._zip(members))
    end = real.rfind(b"PK\x05\x06")
    check(
        CID,
        "the lie target is the end-of-central-directory record",
        end >= 0 and struct.unpack_from("<H", real, end + 10)[0] == 10_001,
    )
    struct.pack_into("<HH", real, end + 8, 5, 5)  # this-disk and total entries now claim 5
    check(CID, "the record now claims 5 entries", ox.end_record_totals(bytes(real))[0] == 5)  # type: ignore[index]
    _e, outcome = run(bytes(real))
    check(
        CID,
        "a directory holding 10,001 entries is refused though its end record claims 5",
        failed_with(outcome, "unsafe_archive"),
    )


def test_foreign_slot_kinds_are_refused_even_if_hand_built() -> None:
    base = build_mapping()
    for kind in (
        "key",
        "Key",
        "KEY",
        "HEADER",
        " header",
        "header ",
        "Index",
        "LETTERS",
        "",
        "pointer",
        "col",
    ):
        hand = dataclasses.replace(base, prompt=ic.Slot(kind, "q"))
        reader = make_reader(GOOD)
        try:
            xi.xlsx_events(
                hand_reader := reader,
                hand,
                verified_content_type=XLSX_TYPE,
                dataset_sha256="0" * 64,
            )
        except ic.MappingInvalidError as exc:
            check(
                CID,
                f"slot kind {kind!r}: bad_slot before a byte is read",
                str(exc).endswith("bad_slot") and hand_reader.bytes_read == 0,
            )
        else:
            check(CID, f"slot kind {kind!r} is refused", False)
    for kind, value in (("header", "q"), ("index", 0), ("letters", "A")):
        hand = dataclasses.replace(base, prompt=ic.Slot(kind, value))
        xi.xlsx_events(
            make_reader(GOOD), hand, verified_content_type=XLSX_TYPE, dataset_sha256="0" * 64
        )
    check(CID, "the three tabular slot kinds are accepted", True)


def test_many_small_comments_and_instructions_are_progress_not_an_attack() -> None:
    head = f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>'
    ok_row = '<row r="1"><c r="A1" t="inlineStr"><is><t>q</t></is></c><c r="B1" t="inlineStr"><is><t>a</t></is></c></row>'
    for label, filler in (
        ("comments", "<!--x-->" * 200_000),
        ("processing instructions", "<?p?>" * 300_000),
    ):
        _e, outcome = run(book(raw_sheet=head + ok_row + filler + "</sheetData></worksheet>"))
        check(
            CID,
            f"1.6 MB of {label} between events is not a token attack",
            outcome.report.state == "completed",
        )
        _e, outcome = run(book(raw_sheet=head + filler + ok_row + "</sheetData></worksheet>"))
        check(
            CID,
            f"{label} before the first row are progress too",
            outcome.report.state == "completed",
        )
    rows = [[RAWC('<c r="{ref}" t="s"><v>0</v></c>'), IS("x")]]
    sst = f'{XML_HEAD}<sst xmlns="{MAIN}">' + "<!--x-->" * 200_000 + "<si><t>seen</t></si></sst>"
    events, outcome = run(book([[IS("q"), IS("a")], *rows], shared_override=sst.encode()))
    check(CID, "comments in sharedStrings are progress too", len(accepted(events)) == 1)


def test_whitespace_is_content_not_blank() -> None:
    rows = [[IS("q"), IS("a")], [IS(" "), IS("\t")], [IS(""), IS("")]]
    events, outcome = run(book(rows))
    check(
        CID,
        "a row of whitespace-only cells is a record, not a blank line",
        outcome.report.blank_lines == 1 and outcome.report.records_seen == 1,
    )
    rows = [[IS(" "), IS(" ")], [IS("q"), IS("a")], [IS("p"), IS("c")]]
    _e, outcome = run(book(rows), header_row=1)
    check(
        CID,
        "and a whitespace row counts toward header_row",
        outcome.report.state == "completed" and outcome.report.records_accepted == 1,
    )


def test_dot_segments_are_removed_only_when_exactly_a_dot() -> None:
    sheet = sheet_xml([[IS("q"), IS("a")], [IS("p"), IS("c")]], Shared()).encode()
    base = [(n, d) for n, d in members_of(book()) if n != "xl/worksheets/sheet1.xml"]
    odd = t501._zip([*base, ("xl/worksheets/ . /s.xml", sheet)])
    odd = with_member(
        odd,
        "xl/_rels/workbook.xml.rels",
        workbook_rels([("rId1", "worksheets/ . /s.xml")]).encode(),
    )
    check(
        CID,
        "a segment that merely contains a dot and spaces is a real name",
        run(odd)[1].report.state == "completed",
    )
    plain = t501._zip([*base, ("xl/worksheets/s.xml", sheet)])
    plain = with_member(
        plain,
        "xl/_rels/workbook.xml.rels",
        workbook_rels([("rId1", "./worksheets/./s.xml")]).encode(),
    )
    check(CID, "and exact './' segments are removed", run(plain)[1].report.state == "completed")


def test_part_names_with_surrounding_whitespace_are_other_parts() -> None:
    for name in (
        " xl/vbaProject.bin",
        " xl/connections.xml",
        "xl/connections.xml\t",
        " xl/externalLinks/e.xml",
        " xl/macrosheets/m.xml",
    ):
        events, outcome = run(book(extra=[(name, b"<x/>")]))
        check(
            CID,
            f"a member named {name!r} is not the part it resembles",
            outcome.report.state == "completed" and len(accepted(events)) == 1,
        )
    padded = t501._zip(
        [
            (" [Content_Types].xml" if n == "[Content_Types].xml" else n, d)
            for n, d in members_of(book())
        ]
    )
    check(
        CID,
        "a content-types part with a space in its name is not the content-types part",
        failed_with(run(padded)[1], "unsupported_workbook"),
    )
    padded = t501._zip(
        [(" xl/workbook.xml" if n == "xl/workbook.xml" else n, d) for n, d in members_of(book())]
    )
    check(
        CID,
        "nor is a workbook part with a space in its name",
        failed_with(run(padded)[1], "unsupported_workbook"),
    )


def test_targets_are_folded_the_unicode_way_on_the_target_side_too() -> None:
    long_s = "ſ"
    sheet = sheet_xml([[IS("q"), IS("a")], [IS("p"), IS("c")]], Shared()).encode()
    base = [(n, d) for n, d in members_of(book()) if n != "xl/worksheets/sheet1.xml"]
    data = t501._zip([*base, ("xl/worksheets/shees.xml", sheet)])
    data = with_member(
        data,
        "xl/_rels/workbook.xml.rels",
        workbook_rels([("rId1", f"worksheets/shee{long_s}.xml")]).encode(),
    )
    check(
        CID,
        "a sheet target spelled with a long s finds the member spelled with an s",
        run(data)[1].report.state == "completed",
    )
    rows = [[IS("q"), IS("a")], [RAWC('<c r="{ref}" t="s"><v>0</v></c>'), IS("x")]]
    shared = shared_xml(["seen"]).encode()
    ok = book(rows, drop=("xl/sharedStrings.xml",))
    data = t501._zip([*members_of(ok), ("xl/sharedstrings.xml", shared)])
    data = with_member(
        data,
        "xl/_rels/workbook.xml.rels",
        workbook_rels().replace("sharedStrings.xml", f"{long_s}haredStrings.xml").encode(),
    )
    check(
        CID,
        "a sharedStrings target spelled with a long s finds the member spelled with an s",
        len(accepted(run(data)[0])) == 1,
    )
    for first, second in (("Straße", "STRASSE"), ("Ǆ", "ǆ"), ("ΣΑΣ", "σας")):
        rels = workbook_rels(
            [("rId1", "worksheets/sheet1.xml"), ("rId2", "worksheets/sheet1.xml")]
        ).encode()
        data = book(
            [[IS("q"), IS("a")], [IS("p"), IS("c")]],
            sheet=first,
            extra_sheets=[(second, "rId2", None)],
            replace={"xl/_rels/workbook.xml.rels": rels},
        )
        check(
            CID,
            f"sheets {first!r} and {second!r} are equal after Unicode folding: duplicate_sheet_name",
            failed_with(run(data, sheet=first)[1], "duplicate_sheet_name"),
        )
    rels = workbook_rels(
        [("rId1", "worksheets/sheet1.xml"), ("rId2", "worksheets/sheet1.xml")]
    ).encode()
    data = book(
        [[IS("q"), IS("a")], [IS("p"), IS("c")]],
        sheet="Straße",
        extra_sheets=[("Strasse2", "rId2", None)],
        replace={"xl/_rels/workbook.xml.rels": rels},
    )
    check(
        CID,
        "distinct names stay distinct",
        run(data, sheet="Straße")[1].report.state == "completed",
    )
    ext = f'<Relationship Id="rIdX" Type="{REL}/externalLink" Target="http://127.0.0.1:9/x" TargetMode="EXTERNAL"/>'
    check(
        CID,
        "TargetMode is matched case-insensitively",
        failed_with(run(book(rel_extra=ext))[1], "external_link_present"),
    )


def test_target_mode_padded_with_whitespace_is_still_external() -> None:
    """Ruling (c), 2026-10-14T13:02Z: a bare `.lower()` comparison, with no
    `.strip()`, let `TargetMode=" External "` (or a trailing/leading space,
    tab or newline of any kind) through as neither matching `"external"` nor
    being treated as absent — the fault this test pins."""
    for label, padded in (
        ("leading space", " External"),
        ("trailing space", "External "),
        ("both sides", " External "),
        ("tab", "External\t"),
        ("newline", "External\n"),
    ):
        ext = (
            f'<Relationship Id="rIdX" Type="{REL}/externalLink" '
            f'Target="http://127.0.0.1:9/x" TargetMode="{padded}"/>'
        )
        check(
            CID,
            f"TargetMode={padded!r} ({label}) is still external_link_present",
            failed_with(run(book(rel_extra=ext))[1], "external_link_present"),
        )


def test_vba_project_reached_only_through_a_relationship_type_is_refused() -> None:
    """Ruling (c): the macro-carrier check so far only looks at the PART NAME
    (`xl/vbaProject.bin`, `MACRO_PART_PREFIXES`) and the declared content type.
    A relationship can point at a vbaProject part under any other name, with no
    macro-enabled content type declared for it at all; this defence-in-depth
    check catches that by relationship `Type` alone."""
    vba_type = f"{REL}/vbaProject"
    rel_extra = f'<Relationship Id="rIdVba" Type="{vba_type}" Target="notVbaAtAll.bin"/>'
    data = with_member(
        book(rel_extra=rel_extra), "xl/notVbaAtAll.bin", b"\xccMacro bytes, no declared type\xcc"
    )
    check(
        CID,
        "a vbaProject relationship Type alone is unsafe_workbook, whatever the target is named",
        failed_with(run(data)[1], "unsafe_workbook"),
    )
    plain = f'<Relationship Id="rIdPlain" Type="{REL}/styles" Target="styles.xml"/>'
    check(
        CID,
        "and an ordinary relationship Type is untouched by this check",
        run(book(rel_extra=plain))[1].report.state == "completed",
    )
    # Advisory: Excel's own, non-transitional vbaProject relationship type
    # (not the OOXML transitional one above) is defence in depth too.
    ms_vba_type = "http://schemas.microsoft.com/office/2006/relationships/vbaProject"
    ms_rel_extra = f'<Relationship Id="rIdVbaMs" Type="{ms_vba_type}" Target="alsoNotVba.bin"/>'
    ms_data = with_member(
        book(rel_extra=ms_rel_extra), "xl/alsoNotVba.bin", b"\xccMacro bytes, no declared type\xcc"
    )
    check(
        CID,
        "Excel's own vbaProject relationship type is unsafe_workbook too",
        failed_with(run(ms_data)[1], "unsafe_workbook"),
    )


def test_a_header_made_of_a_space_is_a_real_header() -> None:
    data = book([[IS(" "), IS("a")], [IS("p"), IS("c")]])
    events, outcome = run(data, prompt={"header": " "})
    check(
        CID,
        "a header cell holding a single space is matched exactly",
        len(accepted(events)) == 1 and texts(accepted(events)[0]) == ["p", "c"],
    )
    _e, outcome = run(data, prompt={"header": "  "})
    check(CID, "two spaces are not one", failed_with(outcome, "mapping_column_missing"))


def test_relationship_targets_with_stray_whitespace_name_nothing() -> None:
    for target in (
        "worksheets/sheet1.xml ",
        " worksheets/sheet1.xml",
        "worksheets/sheet1.xml\n",
        "worksheets/ sheet1.xml",
    ):
        _e, outcome = run(book(sheet_target=target))
        check(
            CID,
            f"sheet target {target!r} names no part: xlsx_malformed",
            failed_with(outcome, "xlsx_malformed"),
        )
    rows = [[IS("q"), IS("a")], [RAWC('<c r="{ref}" t="s"><v>0</v></c>'), IS("x")]]
    for target in ("sharedStrings.xml ", " sharedStrings.xml", "sharedStrings.xml\n"):
        bad = workbook_rels(shared=False).replace(
            "</Relationships>",
            f'<Relationship Id="rIdS" Type="{REL}/sharedStrings" Target="{target}"/></Relationships>',
        )
        _e, outcome = run(book(rows, replace={"xl/_rels/workbook.xml.rels": bad.encode()}))
        check(
            CID,
            f"sharedStrings target {target!r} names no part: xlsx_malformed",
            failed_with(outcome, "xlsx_malformed"),
        )


def test_a_truncated_shared_strings_part_never_loads_what_came_before_the_damage() -> None:
    rows = [[IS("q"), IS("a")], [RAWC('<c r="{ref}" t="s"><v>0</v></c>'), IS("x")]]
    half = f'{XML_HEAD}<sst xmlns="{MAIN}"><si><t>seen</t></si><si><t>never closed'
    _e, outcome = run(book(rows, shared_override=half.encode()))
    check(
        CID,
        "a string table cut off after a complete entry is fatal, not partly loaded",
        failed_with(outcome, "xlsx_malformed"),
    )
    broken = f'{XML_HEAD}<sst xmlns="{MAIN}"><si><t>seen</t></si></oops></sst>'
    _e, outcome = run(book(rows, shared_override=broken.encode()))
    check(
        CID,
        "so is one with a mismatched tag after a complete entry",
        failed_with(outcome, "xlsx_malformed"),
    )


def test_token_guard_boundary_is_exact() -> None:
    limit = ox.MAX_TOKEN_BYTES
    guard = xi._check_progress

    def refused(silent: int, before: int, after: int, size: int) -> bool:
        try:
            guard(silent, before, after, size)
        except ox.OoxmlRefused as exc:
            return exc.code == "unsafe_archive"
        return False

    check(CID, "a block with no event adds its size to the silent run", guard(100, 7, 7, 50) == 150)
    check(
        CID,
        "a block with an event resets the run, whatever its size",
        guard(limit, 7, 8, limit) == 0,
    )
    check(CID, "a run of exactly the limit is allowed", guard(0, 1, 1, limit) == limit)
    check(CID, "and so is one that reaches it across blocks", guard(limit - 1, 1, 1, 1) == limit)
    check(CID, "one byte past the limit is refused", refused(limit, 1, 1, 1))
    check(CID, "in one block", refused(0, 1, 1, limit + 1))
    check(
        CID,
        "a single event in the block that crosses the limit saves it",
        not refused(limit, 1, 2, 1),
    )


def test_the_shared_strings_root_must_be_an_sst_in_the_main_namespace() -> None:
    inline = [[IS("q"), IS("a")], [IS("p"), IS("c")]]  # no cell reads the table
    cases = [
        ("a wrong root element", f'{XML_HEAD}<foo xmlns="{MAIN}"/>', "xlsx_malformed"),
        ("a root in no namespace", f"{XML_HEAD}<sst/>", "xlsx_malformed"),
        ("a strict-namespace sst", f'{XML_HEAD}<sst xmlns="{STRICT}"/>', "unsupported_workbook"),
    ]
    for label, payload, code in cases:
        _e, outcome = run(book(inline, shared_override=payload.encode()))
        check(
            CID,
            f"{label}: fatal {code} even when no cell uses the table",
            failed_with(outcome, code),
        )
    _e, outcome = run(book(inline, shared_override=f'{XML_HEAD}<sst xmlns="{MAIN}"/>'.encode()))
    check(CID, "an empty sst is fine", outcome.report.state == "completed")


def test_a_hash_the_caller_expects_that_the_reader_did_not_verify_fails() -> None:
    good_sha = hashlib.sha256(GOOD).hexdigest()
    other = hashlib.sha256(GOOD + b"x").hexdigest()
    seen: list[object] = []
    # the reader is verified for the real bytes; the caller expects another dataset's hash --
    # PREVIEW-SHA-TYPEERROR class: caught against the reader's own pinned hash before a byte
    # of the source is read, same as the sibling importers (FTF-0502/0503/0504).
    reader = make_reader(GOOD)
    outcome = xi.run_xlsx_import(
        reader,
        build_mapping(),
        verified_content_type=XLSX_TYPE,
        dataset_purpose="training",
        expected_sha256=other,
        on_accepted=seen.append,
    )
    check(
        CID,
        "a mismatch between the expected and the verified hash is failed/source_changed",
        failed_with(outcome, "source_changed"),
    )
    check(
        CID,
        "before one record is accepted and before a byte is read",
        not seen and outcome.report.records_accepted == 0 and reader.bytes_read == 0,
    )
    check(
        CID,
        "the report names the hash the caller expected",
        outcome.report.expected_sha256 == other,
    )
    stream = xi.xlsx_events(
        make_reader(GOOD), build_mapping(), verified_content_type=XLSX_TYPE, dataset_sha256=other
    )
    result = ic.preview(stream, 3)
    check(
        CID,
        "a preview fails the same way: nothing accepted, not complete",
        not result.accepted and not result.complete,
    )
    stream.close()
    ok = xi.run_xlsx_import(
        make_reader(GOOD),
        build_mapping(),
        verified_content_type=XLSX_TYPE,
        dataset_purpose="training",
        expected_sha256=good_sha,
    )
    check(
        CID,
        "the matching hash imports",
        ok.report.state == "completed" and ok.report.records_accepted == 1,
    )


def test_a_single_hex_character_anywhere_in_the_hash_is_a_mismatch() -> None:
    """V-SHA-FIRST-LAST: `hmac.compare_digest` must see the whole 64-character
    hash, not a prefix or suffix -- pins the comparison at the first character,
    the last, and the middle (character 32), so a regression to a truncated
    comparison (e.g. the first or last few characters only) goes red."""
    good_sha = hashlib.sha256(GOOD).hexdigest()
    for position in (0, 32, 63):
        flipped_digit = "0" if good_sha[position] != "0" else "1"
        other = good_sha[:position] + flipped_digit + good_sha[position + 1 :]
        reader = make_reader(GOOD, expected_sha=good_sha)
        outcome = xi.run_xlsx_import(
            reader,
            build_mapping(),
            verified_content_type=XLSX_TYPE,
            dataset_purpose="training",
            expected_sha256=other,
        )
        check(
            CID,
            f"one hex character differing at position {position}: source_changed, 0 bytes read",
            failed_with(outcome, "source_changed") and reader.bytes_read == 0,
        )


def test_the_report_carries_the_purpose_it_was_given() -> None:
    for purpose in ("training", "held_out_evaluation", "validation"):
        outcome = xi.run_xlsx_import(
            make_reader(GOOD),
            build_mapping(),
            verified_content_type=XLSX_TYPE,
            dataset_purpose=purpose,
            expected_sha256=hashlib.sha256(GOOD).hexdigest(),
        )
        check(
            CID,
            f"dataset_purpose {purpose!r} is taken from the caller, not the data",
            outcome.report.dataset_purpose == purpose,
        )
    try:
        xi.run_xlsx_import(
            make_reader(GOOD),
            build_mapping(),
            verified_content_type=XLSX_TYPE,
            dataset_purpose="Not A Purpose",
            expected_sha256=hashlib.sha256(GOOD).hexdigest(),
        )
    except ValueError:
        check(CID, "a purpose that is not a plain name is refused", True)
    else:
        check(CID, "a purpose that is not a plain name is refused", False)


def test_every_split_point_of_a_small_workbook_gives_the_same_report() -> None:
    small = book([[IS("q"), IS("a")], [IS("p"), IS("c")]], method=zipfile.ZIP_STORED)
    check(CID, "the workbook is small enough for an every-split-point table", len(small) <= 4096)
    base = run(small)[1].report.report_sha256
    bad = 0
    for cut in range(1, len(small)):
        reader = ic.ChunkReader(
            [small[:cut], small[cut:]],
            expected_size=len(small),
            expected_sha256=hashlib.sha256(small).hexdigest(),
        )
        outcome = xi.run_xlsx_import(
            reader,
            build_mapping(),
            verified_content_type=XLSX_TYPE,
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(small).hexdigest(),
        )
        bad += outcome.report.report_sha256 != base
    check(CID, f"all {len(small) - 1} two-piece splits give one report ({bad} differ)", bad == 0)


def test_size_boundaries_at_the_production_constants() -> None:
    cap = ic.DEFAULT_LIMITS.max_record_bytes
    check(CID, "the production record cap is 2 MiB", cap == 2 * 1024 * 1024)
    unmapped = [[IS("q"), IS("a"), IS("n")], [IS("p"), IS("c"), IS("x" * cap)]]
    events, outcome = run(book(unmapped))
    check(
        CID,
        "an unmapped cell of exactly the cap imports",
        outcome.report.state == "completed" and len(accepted(events)) == 1,
    )
    over = [[IS("q"), IS("a"), IS("n")], [IS("p"), IS("c"), IS("x" * (cap + 1))]]
    _e, outcome = run(book(over))
    check(
        CID, "one character more is fatal field_too_large", failed_with(outcome, "field_too_large")
    )
    wide = [[IS("q"), IS("a")], [IS("x" * (cap - 1)), IS("y")]]
    events, outcome = run(book(wide))
    check(
        CID,
        "mapped fields adding up to exactly the cap are accepted by the size rule",
        outcome.report.failure_code is None and "record_too_large" not in codes(outcome),
    )
    wide = [[IS("q"), IS("a")], [IS("x" * cap), IS("y")]]
    _e, outcome = run(book(wide))
    check(CID, "one byte more is record_too_large", codes(outcome) == ["record_too_large"])
    # a maximum-size cell read in one-byte chunks stays linear (the compressed bytes are few)
    import time

    started = time.monotonic()
    _e, outcome = run(book(unmapped), chunk=1)
    check(
        CID,
        "a cap-size cell in 1-byte chunks completes quickly",
        outcome.report.state == "completed" and time.monotonic() - started < 30,
    )
    # a shared string of exactly the cap and one more
    shared = shared_xml(["x" * cap]).encode()
    rows = [
        [IS("q"), IS("a"), IS("n")],
        [IS("p"), IS("c"), RAWC('<c r="{ref}" t="s"><v>0</v></c>')],
    ]
    _e, outcome = run(book(rows, shared_override=shared))
    check(CID, "a shared string of exactly the cap loads", outcome.report.state == "completed")
    shared = shared_xml(["x" * (cap + 1)]).encode()
    _e, outcome = run(book(rows, shared_override=shared))
    check(
        CID, "one character more is fatal field_too_large", failed_with(outcome, "field_too_large")
    )


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
