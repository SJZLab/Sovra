#!/usr/bin/env python3
"""FTF-0504 -- CSV importer.

Runnable without any database or network: `workers.data.csv_importer` is pure
and synchronous and sits on FTF-0502-P1's `ChunkReader` / `import_common`.

Acceptance ("Use explicit header/encoding/delimiter/column mapping; preserve
strings, IDs and Arabic text"): test_basic_import_*, test_encoding_round_trip_*,
test_delimiter_quote_line_ending_table, test_header_*, test_preservation_*,
test_column_positions_*, test_mapping_*.

Negative acceptance ("CSV formula text is not evaluated; leading-zero
identifiers and quoted multiline fields remain intact"): test_formula_*,
test_preservation_*, test_multiline_*.

Fatal / rejected / blocked: test_wrong_encoding_*, test_bom_*, test_ragged_*,
test_nul_*, test_size_*, test_structure_*, test_too_many_columns, test_source_*.

Run directly: python3 tests/tasks/test_ftf_0504.py
"""

from __future__ import annotations

import csv
import hashlib
import io
import random
import re
import sys
import threading
import tracemalloc
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import import_test_support as support  # noqa: E402
from workers.data import csv_importer as ci  # noqa: E402
from workers.data import import_common as ic  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0504"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# --- helpers -------------------------------------------------------------------------


def mapping_text(
    *,
    encoding: str = "utf-8",
    delimiter: str = ",",
    has_header: bool = True,
    header_row: int = 0,
    prompt: object = None,
    completion: object = None,
    extra: str = "",
) -> str:
    prompt = {"header": "q"} if prompt is None else prompt
    completion = {"header": "a"} if completion is None else completion
    delimiter_json = '"\\t"' if delimiter == "\t" else f'"{delimiter}"'
    return (
        '{"shape":"prompt_completion",'
        f'"prompt":{_slot_json(prompt)},"completion":{_slot_json(completion)}{extra},'
        f'"csv":{{"encoding":"{encoding}","delimiter":{delimiter_json},'
        f'"has_header":{"true" if has_header else "false"},"header_row":{header_row}}}}}'
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
    **mapping_kwargs: Any,
) -> ci.CsvImportOutcome:
    mapping = build_mapping(**mapping_kwargs)
    reader = make_reader(data, chunk, limits)
    return ci.run_csv_import(
        reader,
        mapping,
        verified_content_type="text/csv",
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        limits=limits,
    )


def collect(
    data: bytes,
    *,
    chunk: int | None = None,
    limits: ic.ImportLimits = ic.DEFAULT_LIMITS,
    **mapping_kwargs: Any,
) -> tuple[list[ic.ImportEvent], ci.CsvImportOutcome]:
    """The emitted accepted candidates and rejections, via `run_csv_import`."""
    events: list[ic.ImportEvent] = []
    mapping = build_mapping(**mapping_kwargs)
    reader = make_reader(data, chunk, limits)
    outcome = ci.run_csv_import(
        reader,
        mapping,
        verified_content_type="text/csv",
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        limits=limits,
        on_accepted=lambda candidate: events.append(ic.AcceptedRecord(candidate)),
    )
    events.extend(outcome.report.rejections)
    return events, outcome


def csv_bytes(
    rows: list[list[str]], *, encoding: str = "utf-8", delimiter: str = ",", eol: str = "\r\n"
) -> bytes:
    buffer = io.StringIO(newline="")
    csv.writer(buffer, delimiter=delimiter, lineterminator=eol).writerows(rows)
    return buffer.getvalue().encode(encoding)


def texts(candidate: object) -> list[str]:
    """The text of every message part of an accepted candidate."""
    assert isinstance(candidate, dict)
    out: list[str] = []
    for message in candidate["content"]["messages"]:
        for part in message["content"]:
            out.append(part["text"])
    return out


def prompt_of(candidate: object) -> str:
    return texts(candidate)[0]


def completion_of(candidate: object) -> str:
    return texts(candidate)[-1]


def accepted(events: list[ic.ImportEvent]) -> list[dict[str, object]]:
    return [dict(e.candidate) for e in events if isinstance(e, ic.AcceptedRecord)]


def rejected(outcome: ci.CsvImportOutcome) -> list[ic.RowRejection]:
    return list(outcome.report.rejections)


ARABIC = "مرحبا بالعالم"
CORPUS = {
    "ascii": "hello world",
    "arabic": ARABIC,
    "arabic_digits": "٠١٢٣٤٥٦٧٨٩",
    "arabic_mixed": "مرحبا ١٢٣ abc",
    "rtl_marks": "abc‏مرحبا‎abc",
    "combining": "é ä",
    "emoji": "ok \U0001f600 \U0001f1f8\U0001f1e6",
}
CP1256_CORPUS = {k: v for k, v in CORPUS.items() if k in ("ascii", "arabic")}


# --- tests ---------------------------------------------------------------------------


def test_basic_import_candidates_and_report() -> None:
    data = csv_bytes([["q", "a"], ["hi", "there"], ["two", "rows"]])
    events, outcome = collect(data)
    report = outcome.report
    cands = accepted(events)
    check(CID, "completed", report.state == "completed" and report.failure_code is None)
    check(CID, "two records accepted", report.records_accepted == 2 and len(cands) == 2)
    check(CID, "no rejections", report.records_rejected == 0 and report.records_seen == 2)
    check(CID, "prompt/completion preserved", texts(cands[0]) == ["hi", "there"])
    check(CID, "record_index is the 0-based source ordinal", cands[1]["record_index"] == 1)
    check(CID, "no fatal locator", outcome.fatal_locator is None)
    check(
        CID, "importer name recorded", report.importer == "csv" and report.importer_version == "1"
    )
    check(CID, "report sha binds the report", len(report.report_sha256) == 64)
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


def test_encoding_round_trip_is_byte_exact_for_every_encoding() -> None:
    for encoding in ci.CSV_ENCODINGS:
        corpus = CP1256_CORPUS if encoding == "windows-1256" else CORPUS
        rows = [["q", "a"]] + [[value, value[::-1]] for value in corpus.values()]
        py_encoding = "utf-8-sig" if encoding == "utf-8-sig" else encoding
        data = csv_bytes(rows, encoding=py_encoding)
        for chunk in (None, 1, 2, 3, 7):
            events, outcome = collect(data, chunk=chunk, encoding=encoding)
            cands = accepted(events)
            ok = outcome.report.state == "completed" and len(cands) == len(corpus)
            check(CID, f"{encoding} chunk={chunk}: completed with all records", ok)
            if ok:
                for cand, value in zip(cands, corpus.values(), strict=True):
                    check(
                        CID,
                        f"{encoding} chunk={chunk}: {value!r} round-trips exactly",
                        texts(cand) == [value, value[::-1]],
                    )


def test_wrong_declared_encoding_is_fatal_not_garbled() -> None:
    arabic = csv_bytes([["q", "a"], [ARABIC, ARABIC]], encoding="windows-1256")
    utf16 = csv_bytes([["q", "a"], ["x", "y"]], encoding="utf-16-le")
    utf8 = csv_bytes([["q", "a"], [ARABIC, ARABIC]])
    cases = [
        ("windows-1256 bytes read as utf-8", arabic, "utf-8", "invalid_encoding"),
        ("utf-16 bytes read as utf-8", utf16, "utf-8", None),
        ("utf-8 bytes read as utf-16-le", utf8, "utf-16-le", None),
        ("utf-8 bytes read as utf-16-be", utf8, "utf-16-be", None),
        (
            "odd-length utf-16",
            csv_bytes([["q", "a"]], encoding="utf-16-le") + b"x",
            "utf-16-le",
            "invalid_encoding",
        ),
    ]
    for label, data, encoding, code in cases:
        events, outcome = collect(data, encoding=encoding)
        report = outcome.report
        check(
            CID,
            f"{label}: never completed with accepted records",
            not (report.state == "completed" and accepted(events)),
        )
        check(CID, f"{label}: failed", report.state == "failed" and report.failure_code is not None)
        if code is not None:
            check(CID, f"{label}: code {code}", report.failure_code == code)
        check(
            CID,
            f"{label}: a physical line is reported",
            (outcome.fatal_locator or "").startswith("line:"),
        )


def test_invalid_encoding_reports_the_physical_line() -> None:
    good = b"q,a\nok,fine\nbad,\xff\xfe\n"
    _events, outcome = collect(good, chunk=1)
    check(
        CID,
        "invalid utf-8 on line 3",
        outcome.report.failure_code == "invalid_encoding" and outcome.fatal_locator == "line:3",
    )
    truncated = b"q,a\nok,fine\nlast,\xd8\xa7"[:-1]
    _events, outcome = collect(truncated)
    check(
        CID,
        "truncated multibyte tail is invalid_encoding",
        outcome.report.failure_code == "invalid_encoding",
    )


def test_bom_handling() -> None:
    body = csv_bytes([["q", "a"], ["x", "y"]])
    _events, outcome = collect(b"\xef\xbb\xbf" + body, encoding="utf-8")
    check(
        CID,
        "BOM under utf-8 is fatal unexpected_bom",
        outcome.report.failure_code == "unexpected_bom" and outcome.fatal_locator == "line:1",
    )
    events, outcome = collect(b"\xef\xbb\xbf" + body, encoding="utf-8-sig")
    check(
        CID,
        "BOM under utf-8-sig is consumed",
        outcome.report.state == "completed" and len(accepted(events)) == 1,
    )
    events, outcome = collect(body, encoding="utf-8-sig")
    check(
        CID,
        "no BOM under utf-8-sig is fine",
        outcome.report.state == "completed" and len(accepted(events)) == 1,
    )
    for enc in ("utf-16-le", "utf-16-be"):
        bom = "﻿".encode(enc)
        _events, outcome = collect(
            bom + csv_bytes([["q", "a"], ["x", "y"]], encoding=enc), encoding=enc
        )
        check(
            CID, f"BOM char under {enc} is fatal", outcome.report.failure_code == "unexpected_bom"
        )
    # a BOM only counts at the start: U+FEFF inside a value is kept
    events, outcome = collect(csv_bytes([["q", "a"], ["a﻿b", "y"]]))
    check(CID, "inner U+FEFF is data", prompt_of(accepted(events)[0]) == "a﻿b")
    # split BOM across 1-byte chunks
    _events, outcome = collect(b"\xef\xbb\xbf" + body, chunk=1, encoding="utf-8")
    check(
        CID,
        "BOM split over chunks is still unexpected_bom",
        outcome.report.failure_code == "unexpected_bom",
    )


def test_delimiter_quote_line_ending_table() -> None:
    values = [
        "plain",
        "has,comma",
        "has;semi",
        "has\ttab",
        "has|pipe",
        'has"quote',
        "has\nlf",
        "has\r\ncrlf",
        "has\rcr",
        "",
    ]
    for delimiter in ci.CSV_DELIMITERS:
        for eol in ("\n", "\r\n", "\r"):
            for value in values:
                rows = (
                    [["q", "a"], [value or "z", "tail"]]
                    if value == ""
                    else [["q", "a"], [value, "tail"]]
                )
                data = csv_bytes(rows, delimiter=delimiter, eol=eol)
                for chunk in (None, 1):
                    events, outcome = collect(data, delimiter=delimiter, chunk=chunk)
                    cands = accepted(events)
                    expected = rows[1][0]
                    ok = outcome.report.state == "completed" and len(cands) == 1
                    check(
                        CID,
                        f"delim={delimiter!r} eol={eol!r} {value!r} chunk={chunk}: imported",
                        ok,
                    )
                    if ok:
                        check(
                            CID,
                            f"delim={delimiter!r} eol={eol!r} {value!r} chunk={chunk}: exact field",
                            texts(cands[0]) == [expected, "tail"],
                        )


def test_wrong_delimiter_is_not_guessed() -> None:
    data = csv_bytes([["q", "a"], ["x", "y"]], delimiter=";")
    events, outcome = collect(data, delimiter=",")
    check(
        CID,
        "a ; file read as , has no q/a headers: fatal mapping_column_missing",
        outcome.report.failure_code == "mapping_column_missing" and not accepted(events),
    )


def test_header_exactness() -> None:
    nfc, nfd = unicodedata.normalize("NFC", "é"), unicodedata.normalize("NFD", "é")
    # distinct variants are distinct columns, not duplicates
    rows = [["q", "Q", " q", "q ", nfc, nfd, "a"], ["1", "2", "3", "4", "5", "6", "7"]]
    data = csv_bytes(rows)
    for header, expected in (
        ("q", "1"),
        ("Q", "2"),
        (" q", "3"),
        ("q ", "4"),
        (nfc, "5"),
        (nfd, "6"),
    ):
        events, outcome = collect(data, prompt={"header": header})
        cands = accepted(events)
        check(
            CID,
            f"header {header!r} matches exactly one column",
            outcome.report.state == "completed"
            and len(cands) == 1
            and prompt_of(cands[0]) == expected,
        )
    for header in ("q\t", " q", "ｑ", "é́"):
        _events, outcome = collect(data, prompt={"header": header})
        check(
            CID,
            f"near-miss header {header!r} is not matched",
            outcome.report.failure_code == "mapping_column_missing",
        )
    _events, outcome = collect(data, prompt={"header": "missing"})
    check(
        CID,
        "a mapped column that is absent is fatal",
        outcome.report.failure_code == "mapping_column_missing"
        and outcome.report.state == "failed",
    )


def test_duplicate_header_is_fatal_even_when_unmapped() -> None:
    data = csv_bytes([["q", "a", "extra", "extra"], ["1", "2", "3", "4"]])
    events, outcome = collect(data)
    check(
        CID,
        "duplicate header fatal",
        outcome.report.failure_code == "duplicate_header"
        and outcome.fatal_locator == "line:1"
        and not accepted(events),
    )
    data = csv_bytes([["q", "a", "q"], ["1", "2", "3"]])
    _events, outcome = collect(data)
    check(CID, "duplicate mapped header fatal", outcome.report.failure_code == "duplicate_header")


def test_empty_source_and_header_only() -> None:
    _events, outcome = collect(b"")
    check(
        CID,
        "an empty file has no header: fatal mapping_column_missing",
        outcome.report.failure_code == "mapping_column_missing",
    )
    events, outcome = collect(csv_bytes([["q", "a"]]))
    check(
        CID,
        "header only completes with 0 records",
        outcome.report.state == "completed"
        and outcome.report.records_seen == 0
        and not accepted(events),
    )


def test_ragged_rows_are_rejected_never_padded() -> None:
    data = csv_bytes(
        [
            ["q", "a", "n"],
            ["1", "2", "3"],
            ["short", "row"],
            ["long", "row", "x", "y"],
            ["ok", "again", "z"],
            ["only"],
        ]
    )
    events, outcome = collect(data)
    report = outcome.report
    check(CID, "completed", report.state == "completed")
    check(CID, "two good rows", len(accepted(events)) == 2)
    check(
        CID,
        "three ragged rows rejected",
        report.records_rejected == 3
        and dict(report.rejection_counts) == {"column_count_mismatch": 3},
    )
    check(
        CID,
        "locators are the physical lines",
        [r.locator for r in report.rejections] == ["line:3", "line:4", "line:6"],
    )
    check(
        CID,
        "record_index counts rejected rows",
        [c["record_index"] for c in accepted(events)] == [0, 3],
    )
    check(CID, "seen = accepted + rejected", report.records_seen == 5)
    check(CID, "no padded record", all("short" not in " ".join(texts(c)) for c in accepted(events)))


def test_blank_lines_and_preamble() -> None:
    data = b"q,a\n\n1,2\n\n\n3,4\n"
    events, outcome = collect(data)
    check(
        CID,
        "blank lines counted, not records",
        outcome.report.blank_lines == 3 and len(accepted(events)) == 2,
    )
    check(
        CID,
        "record_index ignores blank lines",
        [c["record_index"] for c in accepted(events)] == [0, 1],
    )
    pre = b"title row,x\n\nq,a\n1,2\n"
    events, outcome = collect(pre, header_row=1)
    check(
        CID,
        "header_row=1 skips one declared preamble record",
        outcome.report.state == "completed"
        and len(accepted(events)) == 1
        and texts(accepted(events)[0]) == ["1", "2"],
    )
    check(CID, "preamble is neither accepted nor rejected", outcome.report.records_seen == 1)
    _events, outcome = collect(pre, header_row=0)
    check(
        CID,
        "header_row=0 takes the first non-blank record as the header",
        outcome.report.failure_code == "mapping_column_missing",
    )
    _events, outcome = collect(b"x,y\n", header_row=3)
    check(
        CID,
        "header_row past the end: no header, fatal",
        outcome.report.failure_code == "mapping_column_missing",
    )


def test_no_header_uses_index_slots_and_first_record_width() -> None:
    data = b"1,2\n3,4\n5\n"
    events, outcome = collect(data, has_header=False, prompt={"index": 0}, completion={"index": 1})
    check(
        CID,
        "records accepted without a header",
        [texts(c) for c in accepted(events)] == [["1", "2"], ["3", "4"]],
    )
    check(
        CID,
        "the first record fixes the width",
        [r.code for r in outcome.report.rejections] == ["column_count_mismatch"],
    )
    check(CID, "no schema digest without a header", outcome.report.source_schema_sha256 is None)
    _events, outcome = collect(data, has_header=False, prompt={"index": 0}, completion={"index": 5})
    check(
        CID,
        "an index beyond the width is fatal mapping_column_missing",
        outcome.report.failure_code == "mapping_column_missing",
    )
    events, outcome = collect(b"x,y\n1,2\n", prompt={"index": 1}, completion={"index": 0})
    check(CID, "index slots also work with a header", texts(accepted(events)[0]) == ["2", "1"])


def test_preservation_table() -> None:
    values = [
        "007",
        "0012345",
        "-0",
        "+1",
        "1e5",
        "0x1F",
        "٠١٢",
        "TRUE",
        "false",
        "NaN",
        "null",
        " 12 ",
        "1.0",
        "00:15",
        "1,000",
        "٣٫١٤",
        " 12",
        "12 ",
    ]
    for value in values:
        for position in ("prompt", "completion"):
            row = [value, "x"] if position == "prompt" else ["x", value]
            data = csv_bytes([["q", "a"], row])
            for chunk in (None, 1):
                events, outcome = collect(data, chunk=chunk)
                cands = accepted(events)
                ok = len(cands) == 1 and outcome.report.state == "completed"
                check(CID, f"{value!r} in {position} chunk={chunk}: accepted", ok)
                if ok:
                    got = prompt_of(cands[0]) if position == "prompt" else completion_of(cands[0])
                    check(
                        CID,
                        f"{value!r} in {position} chunk={chunk}: exact str, no type inference",
                        got == value and type(got) is str,
                    )


def test_column_positions_do_not_matter() -> None:
    for position in range(4):
        header = [f"junk{i}" for i in range(3)]
        row = ["j"] * 3
        header.insert(position, "q")
        row.insert(position, "007")
        header.append("a")
        row.append("x")
        events, outcome = collect(csv_bytes([header, row]))
        cands = accepted(events)
        check(
            CID,
            f"q in column {position}",
            outcome.report.state == "completed"
            and len(cands) == 1
            and texts(cands[0]) == ["007", "x"],
        )


def test_multiline_quoted_fields_keep_their_exact_line_breaks() -> None:
    for eol in ("\r\n", "\n", "\r"):
        field = "line1\r\nline2\nline3\rline4"
        data = csv_bytes([["q", "a"], [field, "end"]], eol=eol)
        for chunk in (None, 1, 2):
            events, outcome = collect(data, chunk=chunk)
            cands = accepted(events)
            ok = len(cands) == 1 and outcome.report.state == "completed"
            check(CID, f"eol={eol!r} chunk={chunk}: one record", ok)
            if ok:
                check(
                    CID,
                    f"eol={eol!r} chunk={chunk}: embedded CRLF/LF/CR exact",
                    prompt_of(cands[0]) == field,
                )


def test_physical_line_numbers_across_multiline_records() -> None:
    # header = line 1, 3-line record = 2-4, short = 5, 2-line bad = 6-7, ok = 8, bad = 9
    rows = [
        ["q", "a", "n"],
        ["a\nb\nc", "x", "1"],
        ["short"],
        ["d\ne", "y"],
        ["ok", "z", "2"],
        ["bad", "row"],
    ]
    for eol in ("\n", "\r\n", "\r"):
        data = csv_bytes(rows, eol=eol)
        for chunk in (None, 1):
            events, outcome = collect(data, chunk=chunk)
            locators = [r.locator for r in outcome.report.rejections]
            check(
                CID,
                f"eol={eol!r} chunk={chunk}: rejection locators are start lines ({locators})",
                locators == ["line:5", "line:6", "line:9"],
            )
            check(
                CID, f"eol={eol!r} chunk={chunk}: two records accepted", len(accepted(events)) == 2
            )


def test_formula_text_is_data_and_never_evaluated() -> None:
    import socket
    import subprocess

    formulas = [
        "=1+1",
        "+1+1",
        "-1+1",
        "@SUM(A1)",
        "\t=1+1",
        "\r=1+1",
        "＝1+1",
        "=cmd|' /C calc'!A0",
        '=HYPERLINK("http://x")',
        " =1+1",
    ]
    events_log: list[str] = []

    def hook(event: str, args: tuple[object, ...]) -> None:
        if event in (
            "subprocess.Popen",
            "os.system",
            "os.exec",
            "os.posix_spawn",
            "socket.connect",
            "socket.getaddrinfo",
        ):
            events_log.append(event)

    cases = [(f, quoted) for f in formulas for quoted in (False, True)]
    sys.addaudithook(hook)
    try:
        for formula, quoted in cases:
            if quoted:
                data = ('q,a\r\n"' + formula.replace('"', '""') + '",ok\r\n').encode("utf-8")
            else:
                if any(ch in formula for ch in ',\r\n"'):
                    data = csv_bytes([["q", "a"], [formula, "ok"]])
                else:
                    data = f"q,a\r\n{formula},ok\r\n".encode()
            events, outcome = collect(data)
            cands = accepted(events)
            ok = len(cands) == 1 and outcome.report.state == "completed"
            check(CID, f"formula {formula!r} quoted={quoted}: accepted", ok)
            if ok:
                check(
                    CID,
                    f"formula {formula!r} quoted={quoted}: byte-exact in the canonical content",
                    prompt_of(cands[0]) == formula,
                )
    finally:
        pass
    check(
        CID,
        f"no subprocess or socket activity while importing formulas ({events_log})",
        not events_log,
    )
    _ = (socket, subprocess)


def test_nul_character_in_mapped_field() -> None:
    data = b"q,a,u\nok,fine,1\nbad\x00,fine,2\nok,fi\x00ne,3\nok,fine,\x004\n"
    events, outcome = collect(data)
    check(
        CID,
        "NUL in a mapped field is rejected",
        [(r.code, r.slot) for r in outcome.report.rejections]
        == [("nul_character", "prompt"), ("nul_character", "completion")],
    )
    check(
        CID, "NUL in an unmapped field is not content and is accepted", len(accepted(events)) == 2
    )
    check(CID, "locators", [r.locator for r in outcome.report.rejections] == ["line:3", "line:4"])
    events, outcome = collect(b"q,a\n\x00,x\n")
    check(
        CID,
        "a lone NUL is not an empty field",
        outcome.report.rejections[0].code == "nul_character",
    )


def test_size_limits() -> None:
    limits = ic.ImportLimits(max_record_bytes=64)
    for size, fatal in ((63, False), (64, False), (65, True)):
        data = csv_bytes([["q", "a"], ["x" * size, "y"]])
        for chunk in (None, 1):
            events, outcome = collect(data, limits=limits, chunk=chunk)
            if fatal:
                check(
                    CID,
                    f"field {size} chunk={chunk}: fatal field_too_large",
                    outcome.report.failure_code == "field_too_large"
                    and outcome.report.state == "failed"
                    and outcome.fatal_locator == "line:2",
                )
            else:
                ok = (
                    outcome.report.state == "completed"
                    and len(accepted(events)) + outcome.report.records_rejected == 1
                )
                check(
                    CID,
                    f"field {size} chunk={chunk}: not fatal ({outcome.report.failure_code})",
                    ok,
                )
    # many fields that together exceed the record size are one rejected record
    data = csv_bytes([["q", "a", "n"], ["x" * 40, "y" * 40, "z"], ["ok", "fine", "1"]])
    events, outcome = collect(data, limits=limits)
    check(
        CID,
        "record over max_record_bytes in mapped fields is rejected, the stream goes on",
        [r.code for r in outcome.report.rejections] == ["record_too_large"]
        and len(accepted(events)) == 1,
    )
    # an unmapped field is not content; only the line cap applies
    data = csv_bytes([["q", "a", "n"], ["ok", "fine", "z" * 60]])
    events, outcome = collect(data, limits=limits)
    check(CID, "a large unmapped field within the line cap is fine", len(accepted(events)) == 1)
    # a physical line beyond the line cap is fatal and bounded in memory
    data = b"q,a\n" + b"x" * 5000 + b"\n"
    _events, outcome = collect(data, limits=limits, chunk=7)
    check(
        CID,
        "a line over the cap is fatal field_too_large",
        outcome.report.failure_code == "field_too_large",
    )
    default = csv.field_size_limit()
    collect(csv_bytes([["q", "a"], ["x" * 70, "y"]]), limits=limits)
    check(
        CID,
        "the csv module's global field size limit is restored",
        csv.field_size_limit() == default,
    )


def test_line_cap_is_exact() -> None:
    limits = ic.ImportLimits(max_record_bytes=64)  # a physical line may hold 130 characters
    for chars in (130, 131):
        body = ("x," * 70)[: chars - 1] + "\n"
        for tail in (body, body[:-1]):
            for chunk in (None, 1, 5):
                data = b"q,a\n" + tail.encode()
                _events, outcome = collect(data, limits=limits, chunk=chunk)
                size = len(tail)
                got = outcome.report.failure_code == "field_too_large"
                check(CID, f"line of {size} chars chunk={chunk}: fatal={got}", got == (size > 130))


def test_record_size_cap_is_exact() -> None:
    limits = ic.ImportLimits(max_record_bytes=64)
    ok = csv_bytes([["q", "a"], ["x" * 32, "y" * 32]])
    over = csv_bytes([["q", "a"], ["x" * 32, "y" * 33]])
    events, outcome = collect(ok, limits=limits)
    check(CID, "mapped fields adding to exactly max are accepted", len(accepted(events)) == 1)
    _events, outcome = collect(over, limits=limits)
    check(
        CID,
        "one byte more is record_too_large",
        [r.code for r in outcome.report.rejections] == ["record_too_large"],
    )
    arabic = csv_bytes([["q", "a"], ["م" * 16, "y" * 32]])
    _events, outcome = collect(arabic, limits=limits)
    check(CID, "the cap counts UTF-8 bytes, not characters", outcome.report.records_rejected == 0)
    arabic = csv_bytes([["q", "a"], ["م" * 17, "y" * 32]])
    _events, outcome = collect(arabic, limits=limits)
    check(CID, "34 bytes of Arabic + 32 is over", outcome.report.records_rejected == 1)


def test_index_slot_equal_to_width_is_missing() -> None:
    _events, outcome = collect(
        b"1,2\n3,4\n", has_header=False, prompt={"index": 0}, completion={"index": 2}
    )
    check(CID, "index == width is fatal", outcome.report.failure_code == "mapping_column_missing")
    _events, outcome = collect(
        b"1,2\n3,4\n", has_header=False, prompt={"index": 0}, completion={"index": 1}
    )
    check(CID, "index == width-1 is fine", outcome.report.state == "completed")


def test_foreign_slot_kinds_are_refused_even_if_hand_built() -> None:
    import dataclasses

    base = build_mapping()
    for kind in ("HEADER", " header", "header ", "Index", "key", "letters"):
        value: str | int = 0 if kind.strip().lower() == "index" else "q"
        hand = dataclasses.replace(base, prompt=ic.Slot(kind, value))
        try:
            ci.csv_events(
                make_reader(b"q,a\n"),
                hand,
                verified_content_type="text/csv",
                dataset_sha256="0" * 64,
            )
        except ic.MappingInvalidError as exc:
            check(CID, f"slot kind {kind!r} refused as bad_slot", exc.reason == "bad_slot")
        else:
            check(CID, f"slot kind {kind!r} refused", False)


def test_fatal_codes_are_matched_exactly() -> None:
    for bad in (" invalid_encoding", "INVALID_ENCODING", "invalid_encoding ", "Invalid_Encoding"):
        try:
            ci.CsvAborted(bad, 1)
        except ValueError:
            continue
        check(CID, f"CsvAborted refuses {bad!r}", False)


def test_tool_schemas_and_validator_are_forwarded() -> None:
    import json as _json

    from app.services.json_schema_tool_validation import validate_instance

    schema = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
        "additionalProperties": False,
    }

    def conversation(arguments: dict[str, object]) -> str:
        return _json.dumps(
            [
                {"role": "user", "content": "where is my order"},
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": [{"id": "c1", "name": "lookup_order", "arguments": arguments}],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "name": "lookup_order",
                    "content": [{"type": "json", "value": {"status": "shipped"}}],
                },
            ]
        )

    mapping = ic.parse_mapping(
        '{"shape":"messages_json","messages":{"header":"m"},"role_key":"role","content_key":"content",'
        '"role_map":{"user":"user","assistant":"assistant","tool":"tool"},'
        '"tool_calls_key":"tool_calls","tool_call_id_key":"tool_call_id","name_key":"name",'
        '"on_unmapped_message_keys":"reject",'
        '"csv":{"encoding":"utf-8","delimiter":",","has_header":true,"header_row":0}}'
    )
    data = csv_bytes([["m"], [conversation({"order_id": "abc"})], [conversation({"x": 1})]])
    sha = hashlib.sha256(data).hexdigest()
    events: list[ic.AcceptedRecord] = []
    outcome = ci.run_csv_import(
        make_reader(data),
        mapping,
        verified_content_type="text/csv",
        dataset_purpose="training",
        expected_sha256=sha,
        tool_schemas={"lookup_order": schema},
        tool_argument_validator=validate_instance,
        on_accepted=lambda c: events.append(ic.AcceptedRecord(c)),
    )
    check(CID, "the conforming tool call is accepted", len(events) == 1)
    check(
        CID, "its tool schema status is valid", events[0].candidate["tool_schema_status"] == "valid"
    )
    check(
        CID,
        "the non-conforming call is rejected by the forwarded validator",
        [r.code for r in outcome.report.rejections] == ["canonical_schema_invalid"],
    )
    stream = ci.csv_events(
        make_reader(data),
        mapping,
        verified_content_type="text/csv",
        dataset_sha256=sha,
        tool_schemas={"lookup_order": schema},
        tool_argument_validator=validate_instance,
    )
    result = ic.preview(stream, 5)
    check(
        CID,
        "csv_events forwards them too",
        len(result.accepted) == 1 and len(result.rejections) == 1,
    )


def test_deadline_is_checked_between_events() -> None:
    data = csv_bytes([["q", "a"], ["1", "x"], ["2", "y"], ["3", "z"]])
    clock = support.FakeClock()
    reader = ic.ChunkReader(
        [data],
        expected_size=len(data),
        expected_sha256=hashlib.sha256(data).hexdigest(),
        clock=clock,
    )
    seen: list[object] = []

    def advance(candidate: object) -> None:
        seen.append(candidate)
        clock.advance(10_000)

    outcome = ci.run_csv_import(
        reader,
        build_mapping(),
        verified_content_type="text/csv",
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        on_accepted=advance,
    )
    check(
        CID,
        "a single chunk cannot hide a passed deadline",
        outcome.report.failure_code == "parse_timeout" and len(seen) == 1,
    )


def test_field_size_limit_is_restored_after_every_run() -> None:
    before = csv.field_size_limit()
    collect(csv_bytes([["q", "a"], ["x", "y"]]))
    check(CID, "restored after a clean run", csv.field_size_limit() == before)
    collect(b'q,a\n"unterminated,x')
    check(CID, "restored after a structure error", csv.field_size_limit() == before)
    small = ic.ImportLimits(max_record_bytes=8)
    collect(csv_bytes([["q", "a"], ["x" * 20, "y"]]), limits=small)
    check(CID, "restored after a size error", csv.field_size_limit() == before)


def test_structure_errors_are_fatal() -> None:
    cases = [
        ("unterminated quote at EOF", b'q,a\n"open,x\n', "csv_structure_invalid"),
        ("text after a closing quote", b'q,a\n"ab"cd,x\n', "csv_structure_invalid"),
        ("unterminated quote, 1-byte chunks", b'q,a\n1,"x\ny', "csv_structure_invalid"),
    ]
    for label, data, code in cases:
        for chunk in (None, 1):
            events, outcome = collect(data, chunk=chunk)
            check(
                CID,
                f"{label} chunk={chunk}",
                outcome.report.failure_code == code and outcome.report.state == "failed",
            )
            check(
                CID,
                f"{label} chunk={chunk}: a line is reported",
                (outcome.fatal_locator or "").startswith("line:"),
            )


def test_too_many_columns() -> None:
    wide = [f"c{i}" for i in range(ci.CSV_MAX_COLUMNS + 1)]
    data = csv_bytes([wide, ["1"] * len(wide)])
    _events, outcome = collect(data, prompt={"header": "c0"}, completion={"header": "c1"})
    check(
        CID,
        "16,385 columns is fatal too_many_columns",
        outcome.report.failure_code == "too_many_columns" and outcome.fatal_locator == "line:1",
    )
    ok = [f"c{i}" for i in range(ci.CSV_MAX_COLUMNS)]
    events, outcome = collect(
        csv_bytes([ok, ["1"] * len(ok)]), prompt={"header": "c0"}, completion={"header": "c1"}
    )
    check(
        CID,
        "16,384 columns is accepted",
        outcome.report.state == "completed" and len(accepted(events)) == 1,
    )


def test_row_level_mapping_errors_are_rejections() -> None:
    data = b"q,a\n,b\nok,\nfine,good\n"
    events, outcome = collect(data)
    got = [(r.code, r.slot, r.locator) for r in outcome.report.rejections]
    check(
        CID,
        "empty required cells are required_field_empty",
        got
        == [
            ("required_field_empty", "prompt", "line:2"),
            ("required_field_empty", "completion", "line:3"),
        ],
    )
    check(CID, "the good row is still accepted", len(accepted(events)) == 1)


def test_mapping_is_validated_before_a_byte_is_read() -> None:
    def refused(label: str, text: str, reason: str) -> None:
        reader = make_reader(b"q,a\n1,2\n")
        try:
            ci.csv_events(
                reader,
                ic.parse_mapping(text),
                verified_content_type="text/csv",
                dataset_sha256="0" * 64,
            )
        except ic.MappingInvalidError as exc:
            check(CID, f"{label}: reason {reason}", exc.reason == reason)
        except Exception as exc:  # noqa: BLE001
            check(CID, f"{label}: wrong exception {type(exc).__name__}", False)
        else:
            check(CID, f"{label}: refused", False)
        check(CID, f"{label}: no byte was read", reader.bytes_read == 0)

    refused("encoding not allowed", mapping_text(encoding="latin-1"), "bad_value")
    refused("encoding guessed (empty-ish)", mapping_text(encoding="UTF-8"), "bad_value")
    refused("delimiter not allowed", mapping_text(delimiter=":"), "bad_value")
    refused(
        "header_row without a header",
        mapping_text(has_header=False, header_row=1, prompt={"index": 0}, completion={"index": 1}),
        "bad_value",
    )
    refused("header slot without a header", mapping_text(has_header=False), "bad_slot")
    refused("key slot", mapping_text(prompt={"key": "q"}), "bad_slot")
    refused("letters slot", mapping_text(prompt={"letters": "A"}), "bad_slot")
    no_csv = mapping_text().replace(
        ',"csv":{"encoding":"utf-8","delimiter":",","has_header":true,"header_row":0}', ""
    )
    refused("no csv block", no_csv, "missing_key")
    pointer = (
        '{"shape":"messages","messages_pointer":"/messages","role_key":"role","content_key":"content",'
        '"role_map":{"user":"user"},"on_unmapped_message_keys":"reject",'
        '"csv":{"encoding":"utf-8","delimiter":",","has_header":true,"header_row":0}}'
    )
    refused("pointer shape", pointer, "bad_shape")


def test_messages_json_column_carries_a_conversation() -> None:
    mapping_json = (
        '{"shape":"messages_json","messages":{"header":"m"},"role_key":"role","content_key":"content",'
        '"role_map":{"user":"user","assistant":"assistant"},"on_unmapped_message_keys":"reject",'
        '"csv":{"encoding":"utf-8","delimiter":",","has_header":true,"header_row":0}}'
    )
    cell = '[{"role":"user","content":"hi"},{"role":"assistant","content":"hello"}]'
    data = csv_bytes([["m"], [cell], ["not json"], ['[{"role":"user","content":"x","extra":1}]']])
    mapping = ic.parse_mapping(mapping_json)
    reader = make_reader(data)
    outcome = ci.run_csv_import(
        reader,
        mapping,
        verified_content_type="text/csv",
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
    )
    check(CID, "one conversation accepted", outcome.report.records_accepted == 1)
    check(
        CID,
        "bad JSON and unmapped key are row rejections",
        [(r.code) for r in outcome.report.rejections] == ["invalid_json", "unmapped_message_key"],
    )


def test_schema_digest_is_over_the_exact_header_cells() -> None:
    a = csv_bytes([["q", "a"], ["1", "2"]])
    b = csv_bytes([["q", "a"], ["3", "4"], ["5", "6"]])
    c = csv_bytes([["q", "a "], ["1", "2"]])
    _e, oa = collect(a)
    _e, ob = collect(b)
    _e, oc = collect(c, completion={"header": "a "})
    expected = hashlib.sha256(b'["q","a"]').hexdigest()
    check(
        CID,
        "digest is sha256 of the canonical JSON array of the header cells",
        oa.report.source_schema_sha256 == expected,
    )
    check(
        CID,
        "data rows do not change it",
        oa.report.source_schema_sha256 == ob.report.source_schema_sha256,
    )
    check(
        CID,
        "a whitespace difference does",
        oc.report.source_schema_sha256 != oa.report.source_schema_sha256,
    )
    d = csv_bytes([["a", "q"], ["2", "1"]])
    _e, od = collect(d)
    check(
        CID,
        "column order matters",
        od.report.source_schema_sha256 != oa.report.source_schema_sha256,
    )


def test_source_failures_are_never_completed() -> None:
    data = csv_bytes([["q", "a"]] + [[f"r{i}", "x"] for i in range(20)])
    chunks = support.split_chunks(data, 16)
    for at in (0, 1, len(chunks) // 2, len(chunks)):
        source = support.ExplodingSource(chunks, ConnectionResetError("boom"), at=at)
        reader = ic.ChunkReader(
            source, expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest()
        )
        outcome = ci.run_csv_import(
            reader,
            build_mapping(),
            verified_content_type="text/csv",
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )
        check(
            CID,
            f"source error at chunk {at}: blocked/source_unavailable",
            outcome.report.state == "blocked"
            and outcome.report.failure_code == "source_unavailable",
        )
    changed = data[:-1] + b"Z"
    reader = ic.ChunkReader(
        [changed], expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest()
    )
    outcome = ci.run_csv_import(
        reader,
        build_mapping(),
        verified_content_type="text/csv",
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
    )
    check(
        CID,
        "a changed last byte is failed/source_changed",
        outcome.report.state == "failed" and outcome.report.failure_code == "source_changed",
    )
    clock = support.FakeClock()
    reader = ic.ChunkReader(
        support.split_chunks(data, 8),
        expected_size=len(data),
        expected_sha256=hashlib.sha256(data).hexdigest(),
        clock=clock,
    )
    stream_events = []
    outcome_holder: list[ci.CsvImportOutcome] = []

    def advancing(candidate: object) -> None:
        stream_events.append(candidate)
        clock.advance(10_000)

    outcome_holder.append(
        ci.run_csv_import(
            reader,
            build_mapping(),
            verified_content_type="text/csv",
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(data).hexdigest(),
            on_accepted=advancing,
        )
    )
    check(
        CID,
        "a deadline passing mid-import is failed/parse_timeout",
        outcome_holder[0].report.failure_code == "parse_timeout",
    )


def test_rejection_budget_is_enforced() -> None:
    limits = ic.ImportLimits(max_rejections=3)
    data = csv_bytes([["q", "a"]] + [["only"]] * 10)
    _events, outcome = collect(data, limits=limits)
    check(
        CID,
        "too many rejections is failed/too_many_rejections",
        outcome.report.failure_code == "too_many_rejections" and outcome.report.state == "failed",
    )


def test_preview_uses_the_same_events() -> None:
    data = csv_bytes([["q", "a"]] + [[f"r{i}", "x"] for i in range(30)])
    stream = ci.csv_events(
        make_reader(data),
        build_mapping(),
        verified_content_type="text/csv",
        dataset_sha256=hashlib.sha256(data).hexdigest(),
    )
    result = ic.preview(stream, 5)
    check(
        CID,
        "preview returns the first 5 accepted records",
        len(result.accepted) == 5 and not result.complete,
    )
    check(
        CID,
        "preview candidates are the real candidates",
        prompt_of(dict(result.accepted[0])) == "r0",
    )


def test_meta_leak_markers_never_reach_the_report() -> None:
    m_val, m_head, m_row = (support.make_marker(x) for x in ("value", "header", "row"))
    data = csv_bytes(
        [["q", "a", m_head], [m_val, "x", "1"], [m_row], ["bad\x00" + m_val, "x", "1"]]
    )
    events, outcome = collect(data)
    leaks = support.leaked_markers(outcome.report, [m_val, m_head, m_row])
    check(CID, f"no content in the report ({leaks})", not leaks)
    leaks = support.leaked_markers(outcome.fatal_locator, [m_val, m_head, m_row])
    check(CID, "no content in the fatal locator", not leaks)
    bad = csv_bytes([["q", "a", m_head, m_head]])
    _events, outcome = collect(bad)
    check(
        CID,
        "a duplicate-header failure names no header text",
        not support.leaked_markers((outcome.report, outcome.fatal_locator), [m_head]),
    )
    broken = f'q,a\n"{m_val}'.encode()
    _events, outcome = collect(broken)
    check(
        CID,
        "a structure failure names no content",
        not support.leaked_markers((outcome.report, outcome.fatal_locator), [m_val]),
    )
    try:
        ci.csv_events(
            make_reader(data),
            ic.parse_mapping(mapping_text(encoding=m_val)),
            verified_content_type="text/csv",
            dataset_sha256="0" * 64,
        )
    except Exception as exc:  # noqa: BLE001
        check(CID, "mapping errors name no value", not support.leaked_markers(exc, [m_val]))


def test_catalogs_cover_every_emitted_code() -> None:
    check(CID, "fatal codes are all in FAILURE_CODES", ci.CSV_FATAL_CODES <= ic.FAILURE_CODES)
    check(
        CID,
        "the new row codes are in REJECTION_CODES",
        {"column_count_mismatch", "nul_character"} <= ic.REJECTION_CODES,
    )
    check(CID, "catalogs stay disjoint", not (ic.REJECTION_CODES & ic.FAILURE_CODES))
    for code in sorted(ci.CSV_FATAL_CODES):
        err = ci.CsvAborted(code, 3)
        check(
            CID,
            f"CsvAborted({code}) is a failed ImportAborted at line:3",
            err.state == "failed" and err.failure_code == code and err.locator == "line:3",
        )
    for bad in ("nope",):
        try:
            ci.CsvAborted(bad, 1)
        except ValueError:
            continue
        check(CID, f"CsvAborted refuses unknown code {bad}", False)
    check(
        CID,
        "line numbers below 1 are clamped",
        ci.CsvAborted("invalid_encoding", 0).locator == "line:1",
    )


def test_report_is_deterministic() -> None:
    data = csv_bytes([["q", "a"], ["x", "y"], ["only"]])
    _e, first = collect(data)
    _e, second = collect(data, chunk=3)
    check(
        CID,
        "same bytes -> same report sha regardless of chunking",
        first.report.report_sha256 == second.report.report_sha256,
    )
    ids_a = [c["normalized_sha256"] for c in accepted(collect(data)[0])]
    ids_b = [c["normalized_sha256"] for c in accepted(collect(data, chunk=1)[0])]
    check(CID, "same canonical hashes", ids_a == ids_b)


def test_memory_is_bounded_by_the_record_not_the_source() -> None:
    def source(rows: int) -> tuple[Iterator[bytes], int, str]:
        row = b"q,a\n" if False else None
        del row
        field = b"x" * 16_000
        header = b"q,a\n"
        line = field + b"," + b"y\n"
        digest = hashlib.sha256(header)
        for _ in range(rows):
            digest.update(line)
        size = len(header) + rows * len(line)

        def gen() -> Iterator[bytes]:
            yield header
            for _ in range(rows):
                yield line

        return gen(), size, digest.hexdigest()

    peaks = []
    for rows in (64, 4096):
        chunks, size, sha = source(rows)
        reader = ic.ChunkReader(chunks, expected_size=size, expected_sha256=sha)
        tracemalloc.start()
        outcome = ci.run_csv_import(
            reader,
            build_mapping(),
            verified_content_type="text/csv",
            dataset_purpose="training",
            expected_sha256=sha,
            on_accepted=lambda _c: None,
        )
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak)
        check(
            CID,
            f"{rows} rows completed",
            outcome.report.state == "completed" and outcome.report.records_accepted == rows,
        )
    check(
        CID, f"peak for a 64x larger source stays within 1.5x ({peaks})", peaks[1] <= peaks[0] * 1.5
    )


# --- round 2 (FTF-0504 RETURN items 1-4) -----------------------------------------------


def _drive(parser: ci._Parser, text: str, size: int) -> list[tuple[int, list[str]]]:
    rows: list[tuple[int, list[str]]] = []
    for at in range(0, len(text), size):
        rows.extend(parser.feed(text[at : at + size]))
    rows.extend(parser.finish())
    return rows


def _reference(text: str, delimiter: str) -> tuple[list[list[str]] | None, str | None]:
    """The csv module (strict) over the same text split at CR, LF or CRLF."""
    lines = [m.group() for m in re.finditer(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", text)]
    saved = csv.field_size_limit(10**9)
    try:
        return list(csv.reader(lines, delimiter=delimiter, strict=True)), None
    except csv.Error:
        return None, "error"
    finally:
        csv.field_size_limit(saved)


def test_parser_matches_the_csv_module_on_generated_inputs() -> None:
    """Differential: the state machine agrees with CPython's strict reader on
    thousands of generated texts at every chunking, rows and errors alike."""
    rng = random.Random(504)
    alphabet = ["a", "b", ",", ";", '"', '"', "\n", "\r", "\r\n", " ", "\x00", "\u0645", "\t", "|"]
    mismatches = 0
    total = 0
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
        delimiter = rng.choice([",", ";", "\t", "|"])
        expected, expected_error = _reference(text, delimiter)
        for size in (1, 2, 3, 7, 1000):
            total += 1
            parser = ci._Parser(delimiter, 10**6, 10**6)
            try:
                got = [row for _line, row in _drive(parser, text, size)]
                got_error = None
            except ci.CsvAborted:
                got, got_error = None, "error"
            if got != expected or got_error != expected_error:
                mismatches += 1
    check(
        CID,
        f"parser == csv.reader on {total} generated cases ({mismatches} differ)",
        mismatches == 0,
    )


def test_parser_start_lines_count_physical_lines_for_every_chunking() -> None:
    text = 'a,b\r\n"x\r\ny\nz",2\n\n\r\nlast,"q""q"'
    for size in (1, 2, 3, 5, 100):
        rows = _drive(ci._Parser(",", 10**6, 10**6), text, size)
        check(
            CID,
            f"start lines chunk={size}: {[line for line, _ in rows]}",
            [line for line, _ in rows] == [1, 2, 5, 6, 7],
        )


def test_invalid_encoding_locator_is_chunk_independent() -> None:
    cases = {
        "utf-8": (b"q,a\nok,fine\nok2,fine\nbad,\xff\xfe\nafter,x\n", 4),
        "windows-1256": None,
        "utf-16-le": (
            "q,a\nok,fine\nok2,fine\n".encode("utf-16-le")
            + b"\x00\xd8"
            + "x\n".encode("utf-16-le"),
            4,
        ),
        "utf-16-be": (
            "q,a\nok,fine\nok2,fine\n".encode("utf-16-be")
            + b"\xd8\x00"
            + "x\n".encode("utf-16-be"),
            4,
        ),
        "utf-8-sig": (b"\xef\xbb\xbfq,a\nok,fine\nok2,fine\nbad,\xff\n", 4),
    }
    for encoding, case in cases.items():
        if case is None:
            continue
        data, line = case
        seen = set()
        for size in list(range(1, 14)) + [64, 10**6]:
            _events, outcome = collect(data, chunk=size, encoding=encoding)
            seen.add((outcome.report.failure_code, outcome.fatal_locator))
        check(
            CID,
            f"{encoding}: one outcome for every chunking: {seen}",
            seen == {("invalid_encoding", f"line:{line}")},
        )
    quoted = b'q,a\n"multi\nline\r\nfield",x\n"bad \xff",y\n'
    seen = set()
    for size in list(range(1, 12)) + [10**6]:
        _events, outcome = collect(quoted, chunk=size)
        seen.add((outcome.report.failure_code, outcome.fatal_locator))
    check(
        CID, f"bad bytes after a multiline record: {seen}", seen == {("invalid_encoding", "line:5")}
    )
    # bytes ending mid-sequence: located at the line the sequence starts on
    truncated = b"q,a\nok,fine\nlast,\xd8"
    seen = set()
    for size in (1, 2, 5, 10**6):
        _events, outcome = collect(truncated, chunk=size)
        seen.add((outcome.report.failure_code, outcome.fatal_locator))
    check(CID, f"truncated tail: {seen}", seen == {("invalid_encoding", "line:3")})


def test_bom_is_refused_even_when_bad_bytes_follow_in_the_same_chunk() -> None:
    utf8 = b"\xef\xbb\xbfq,a\nbad,\xff\n"
    utf16 = "\ufeffq,a\n".encode("utf-16-le") + b"\x00\xd8" + "x\n".encode("utf-16-le")
    for encoding, data in (("utf-8", utf8), ("utf-16-le", utf16)):
        for size in (1, 3, 10**6):
            _events, outcome = collect(data, chunk=size, encoding=encoding)
            check(
                CID,
                f"{encoding} chunk={size}: the BOM wins over later bad bytes",
                outcome.report.failure_code == "unexpected_bom"
                and outcome.fatal_locator == "line:1",
            )


def test_an_earlier_structure_error_beats_a_later_bad_byte_for_every_chunking() -> None:
    data = b'q,a\nok,"bad"x\nlater,\xff\n'
    seen = set()
    for size in list(range(1, 10)) + [10**6]:
        _events, outcome = collect(data, chunk=size)
        seen.add((outcome.report.failure_code, outcome.fatal_locator))
    check(CID, f"structure error first: {seen}", seen == {("csv_structure_invalid", "line:2")})


def test_concurrent_imports_with_different_limits_do_not_interfere() -> None:
    small = ic.ImportLimits(max_record_bytes=200)
    row = b"x" * 150 + b",y\n"
    data = b"q,a\n" + row * 20
    before = csv.field_size_limit()
    results: list[tuple[str, int]] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker(use_small: bool) -> None:
        try:
            barrier.wait()
            for _ in range(15):
                lim = small if use_small else ic.DEFAULT_LIMITS
                big = b"q,a\n" + (b"y" * 5000) + b",z\n"
                payload = data if use_small else big
                _e, outcome = collect(payload, limits=lim, chunk=997)
                results.append((outcome.report.state, outcome.report.records_accepted))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i % 2 == 0,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check(CID, f"no worker crashed: {errors[:1]}", not errors)
    check(
        CID,
        "every mixed-limit import completed",
        all(state == "completed" for state, _ in results) and len(results) == 8 * 15,
    )
    check(CID, "the csv module's global limit was never touched", csv.field_size_limit() == before)


def test_one_logical_record_is_capped_across_physical_lines() -> None:
    limits = ic.ImportLimits(max_record_bytes=64)  # a record may hold 130 characters
    header = b"q,a\n"
    for chars in (130, 131):
        # one logical record of `chars` characters in all (terminator included),
        # made of quoted multiline fields that are each under the 64-character field cap
        quoted = b'"' + b"x\n" * 20 + b'"'
        record = b"ok," + quoted + b"," + quoted + b","
        record += b"p" * (chars - 1 - len(record)) + b"\n"
        total = len(record)
        for chunk in (None, 1, 5):
            _e, outcome = collect(header + record, limits=limits, chunk=chunk)
            fatal = outcome.report.failure_code == "field_too_large"
            check(
                CID,
                f"record of {total} chars chunk={chunk}: fatal={fatal}",
                total == chars and fatal == (total > 130),
            )
    # one quoted field of 60 short lines is over the field cap although no line is
    lines = b'ok,"' + b"ab\n" * 60 + b'"\n'
    for chunk in (None, 1, 9):
        _e, outcome = collect(header + lines, limits=limits, chunk=chunk)
        check(
            CID,
            f"many short lines in one record is fatal chunk={chunk}",
            outcome.report.failure_code == "field_too_large",
        )
    _e, outcome = collect(header + lines, limits=limits)
    check(
        CID,
        "located on the physical line where the field cap was crossed",
        outcome.fatal_locator == "line:23",
    )


def test_wide_record_memory_is_bounded() -> None:
    """A 64 MiB single logical record of multiline unmapped fields is refused
    after the cap, not accepted whole."""
    target = 64 * 1024**2
    piece = (b"x" * 99 + b"\n") * 10_000  # 1 MB of short lines

    def source() -> Iterator[bytes]:
        yield b'q,a,extra\nq1,a1,"'
        for _ in range(target // len(piece)):
            yield piece
        yield b'"\n'

    size = len(b'q,a,extra\nq1,a1,"') + (target // len(piece)) * len(piece) + 2
    digest = hashlib.sha256()
    for part in source():
        digest.update(part)
    reader = ic.ChunkReader(source(), expected_size=size, expected_sha256=digest.hexdigest())
    tracemalloc.start()
    outcome = ci.run_csv_import(
        reader,
        build_mapping(),
        verified_content_type="text/csv",
        dataset_purpose="training",
        expected_sha256=digest.hexdigest(),
    )
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    check(
        CID,
        f"a 64 MiB record is fatal field_too_large ({outcome.report.failure_code})",
        outcome.report.failure_code == "field_too_large",
    )
    check(
        CID, f"peak {peak / 2**20:.1f} MiB stays far below the record's size", peak < 24 * 1024**2
    )
    check(CID, "nothing was read past the cap", reader.bytes_read < 8 * 1024**2)


def test_content_type_gate_refuses_everything_but_csv_before_a_byte() -> None:
    supported = [
        "application/json",
        "application/jsonl",
        "application/x-ndjson",
        "text/csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ]
    data = b"q,a\nx,y\n"
    for content_type in supported:
        chunks_read: list[int] = []

        def chunks(log: list[int] = chunks_read) -> Iterator[bytes]:
            log.append(1)
            yield data

        reader = ic.ChunkReader(
            chunks(),
            expected_size=len(data),
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )
        outcome = ci.run_csv_import(
            reader,
            build_mapping(),
            verified_content_type=content_type,
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )
        if content_type == "text/csv":
            check(
                CID,
                "text/csv is imported",
                outcome.report.state == "completed" and outcome.report.records_accepted == 1,
            )
            continue
        check(
            CID,
            f"{content_type} is refused",
            outcome.report.state == "failed"
            and outcome.report.failure_code == "content_type_not_supported_by_importer"
            and outcome.report.records_seen == 0,
        )
        check(
            CID,
            f"{content_type}: not one byte was read",
            reader.bytes_read == 0 and not chunks_read,
        )
    for odd in (
        None,
        "",
        "TEXT/CSV",
        "text/csv ",
        "text/csv; charset=utf-8",
        b"text/csv",
        5,
        "text/plain",
    ):
        reader = make_reader(data)
        outcome = ci.run_csv_import(
            reader,
            build_mapping(),
            verified_content_type=odd,  # type: ignore[arg-type]
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )
        check(
            CID,
            f"{odd!r} is refused with the closed code",
            outcome.report.failure_code == "content_type_not_supported_by_importer"
            and reader.bytes_read == 0,
        )

    class Shouting(str):
        pass

    reader = make_reader(data)
    outcome = ci.run_csv_import(
        reader,
        build_mapping(),
        verified_content_type=Shouting("text/csv"),
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
    )
    check(
        CID,
        "a str subclass is refused",
        outcome.report.failure_code == "content_type_not_supported_by_importer",
    )
    stream = ci.csv_events(
        make_reader(data),
        build_mapping(),
        verified_content_type="application/json",
        dataset_sha256="0" * 64,
    )
    result = ic.preview(stream, 5)
    check(CID, "preview takes the same gate", result.accepted == () and not result.complete)


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
