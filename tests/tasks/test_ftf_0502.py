#!/usr/bin/env python3
"""FTF-0502 -- JSONL importer (round 2, rebuilt on FTF-0502-P1).

Runnable without any database or network: `workers.data.jsonl_importer` is pure
and synchronous and sits on FTF-0502-P1's `ChunkReader` / `import_common`.

Acceptance ("Stream JSONL per record and report exact row/schema failures with
bounded memory"): test_basic_*, test_content_preservation_*, test_chunking_*,
test_memory_*, test_time_*, test_report_*, test_events_generator_*.

Negative acceptance ("Malformed UTF-8/line, huge record and mixed critical
schemas produce quarantine/errors, not silent loss"): test_utf8_*, test_size_*,
test_mixed_schemas_*, test_depth_*, test_duplicate_*, test_numbers_*,
test_non_object_*, test_trailing_garbage_*, test_line_endings_*, test_bom_*,
test_utf16_*.

Run directly: python3 tests/tasks/test_ftf_0502.py
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import import_test_support as support  # noqa: E402
from workers.data import import_common as ic  # noqa: E402
from workers.data import jsonl_importer as ji  # noqa: E402
from workers.data.record_schema_validator import validate_record_batch  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0502"
JSONL = "application/jsonl"
NDJSON = "application/x-ndjson"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# --- helpers -------------------------------------------------------------------------

PC_MAPPING = '{"shape":"prompt_completion","prompt":{"key":"q"},"completion":{"key":"a"}}'
MESSAGES_MAPPING = (
    '{"shape":"messages","messages_pointer":"/messages","role_key":"role","content_key":"content",'
    '"role_map":{"user":"user","assistant":"assistant","system":"system","tool":"tool"},'
    '"on_unmapped_message_keys":"ignore","tool_calls_key":"tool_calls",'
    '"tool_call_id_key":"tool_call_id","name_key":"name"}'
)


def build_mapping(text: str = PC_MAPPING) -> ic.ImportMapping:
    return ic.parse_mapping(text)


def line(q: str = "hello", a: str = "world", **extra: object) -> bytes:
    return json.dumps({"q": q, "a": a, **extra}, ensure_ascii=False).encode("utf-8")


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
    mapping: str = PC_MAPPING,
    content_type: str = JSONL,
    **kwargs: Any,
) -> ji.JsonlImportOutcome:
    return ji.run_jsonl_import(
        make_reader(data, chunk, limits),
        build_mapping(mapping),
        verified_content_type=content_type,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        limits=limits,
        **kwargs,
    )


def collect(
    data: bytes,
    *,
    chunk: int | None = None,
    limits: ic.ImportLimits = ic.DEFAULT_LIMITS,
    mapping: str = PC_MAPPING,
    **kwargs: Any,
) -> tuple[list[dict[str, object]], ji.JsonlImportOutcome]:
    """The accepted candidates, in order, and the outcome."""
    got: list[dict[str, object]] = []
    outcome = run(
        data,
        chunk=chunk,
        limits=limits,
        mapping=mapping,
        on_accepted=lambda candidate: got.append(dict(candidate)),
        **kwargs,
    )
    return got, outcome


def codes(outcome: ji.JsonlImportOutcome) -> list[tuple[str, str]]:
    return [(r.locator, r.code) for r in outcome.report.rejections]


def texts(candidate: object) -> list[str]:
    assert isinstance(candidate, dict)
    return [part["text"] for m in candidate["content"]["messages"] for part in m["content"]]


def padded(q: str, a: str, size: int) -> bytes:
    """A valid record line of exactly `size` bytes (JSON whitespace padding)."""
    base = json.dumps({"q": q, "a": a}, ensure_ascii=False).encode("utf-8")
    assert len(base) <= size
    return base + b" " * (size - len(base))


# --- basics --------------------------------------------------------------------------


def test_basic_import() -> None:
    data = b"\n".join([line("q1", "a1"), line("q2", "a2"), line("q3", "a3")]) + b"\n"
    got, outcome = collect(data)
    report = outcome.report
    check(CID, "completed", report.state == "completed" and report.failure_code is None)
    check(CID, "three accepted, none rejected", len(got) == 3 and report.records_rejected == 0)
    check(
        CID, "texts in order", [texts(c) for c in got] == [["q1", "a1"], ["q2", "a2"], ["q3", "a3"]]
    )
    check(CID, "record_index is the ordinal", [c["record_index"] for c in got] == [0, 1, 2])
    check(CID, "importer name", report.importer == "jsonl" and report.importer_version == "1")
    check(
        CID, "source hash and size bound", report.source_sha256 == hashlib.sha256(data).hexdigest()
    )
    check(CID, "no header, no schema hash", report.source_schema_sha256 is None)
    check(CID, "no fatal locator", outcome.report.fatal_locator is None)
    check(
        CID,
        "every accepted candidate passes FTF-0508 with 0 issues",
        validate_record_batch(got).issues == (),
    )
    check(
        CID,
        "a quality label is never defaulted",
        all(c["quality_status"] == "pending" and c["pii_status"] == "unscanned" for c in got),
    )
    check(CID, "empty source completes with nothing", run(b"").report.records_seen == 0)


def test_basic_messages_shape_and_tool_calls() -> None:
    schemas: dict[str, dict[str, object]] = {"lookup": {"type": "object"}}
    validated: list[object] = []

    def validator(schema: dict[str, object], instance: object, *, field_name: str = "x") -> None:
        validated.append(instance)
        if instance != {"id": "7"}:
            raise ValueError("bad arguments")

    def tool_record(arguments: dict[str, object]) -> bytes:
        return json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "find 7"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "c1", "name": "lookup", "arguments": arguments}],
                    },
                    {"role": "tool", "content": "found", "tool_call_id": "c1", "name": "lookup"},
                    {"role": "assistant", "content": "done"},
                ]
            }
        ).encode()

    plain = json.dumps(
        {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]}
    ).encode()
    data = b"\n".join([plain, tool_record({"id": "7"}), tool_record({"id": 7})]) + b"\n"
    got, outcome = collect(
        data, mapping=MESSAGES_MAPPING, tool_schemas=schemas, tool_argument_validator=validator
    )
    check(
        CID,
        "plain record is not_applicable, a validated tool call is valid",
        [c["tool_schema_status"] for c in got] == ["not_applicable", "valid"],
    )
    check(
        CID,
        "arguments the validator refuses are canonical_schema_invalid (no silent acceptance)",
        codes(outcome) == [("line:3", "canonical_schema_invalid")],
    )
    check(CID, "the validator saw both tool calls", validated == [{"id": "7"}, {"id": 7}])
    check(CID, "tool-call candidates pass FTF-0508", validate_record_batch(got).issues == ())
    got, outcome = collect(data, mapping=MESSAGES_MAPPING, tool_schemas=schemas)
    check(
        CID,
        "no validator: a tool call is refused, never accepted on trust",
        [c for _, c in codes(outcome)] == ["canonical_schema_invalid"] * 2 and len(got) == 1,
    )
    got, outcome = collect(data, mapping=MESSAGES_MAPPING, tool_argument_validator=validator)
    check(
        CID,
        "an unmapped tool is refused",
        [c for _, c in codes(outcome)] == ["canonical_schema_invalid"] * 2 and len(got) == 1,
    )


# --- content preservation -----------------------------------------------------------

CONTENT = {
    "arabic": "مرحبا بالعالم",
    "arabic_digits": "٠١٢٣٤٥٦٧٨٩",
    "rtl_marks": "abc‏مرحبا‎abc",
    "emoji_zwj": "\U0001f468‍\U0001f469‍\U0001f467",
    "flag": "\U0001f1f8\U0001f1e6",
    "combining": "é ä",
    "leading_zero": "00123",
    "formula_eq": "=SUM(A1:A9)",
    "formula_plus": "+1+1",
    "formula_minus": "-2+3",
    "formula_at": "@cmd",
    "formula_tab": "\t=1",
    "ls_ps": "a b c",
    "quote": 'he said "hi"\\n',
    "nul_escape_free": "a b",
}


def test_content_preservation_is_byte_exact() -> None:
    for label, value in CONTENT.items():
        for ensure_ascii in (False, True):
            data = json.dumps({"q": value, "a": value + "!"}, ensure_ascii=ensure_ascii).encode()
            got, outcome = collect(data + b"\n")
            check(
                CID,
                f"{label} (ensure_ascii={ensure_ascii}) is preserved exactly",
                outcome.report.records_accepted == 1 and texts(got[0]) == [value, value + "!"],
            )


# --- line endings, chunking ------------------------------------------------------------


def test_line_endings_table() -> None:
    a, b_ = line("one", "1"), line("two", "2")
    cases = [
        ("LF", a + b"\n" + b_ + b"\n", 2, 0, 0),
        ("CRLF", a + b"\r\n" + b_ + b"\r\n", 2, 0, 0),
        ("no final newline", a + b"\n" + b_, 2, 0, 0),
        ("no final newline CRLF-ish", a + b"\r\n" + b_ + b"\r", 2, 0, 0),
        ("mixed LF/CRLF", a + b"\r\n" + b_ + b"\n", 2, 0, 0),
        ("blank line between", a + b"\n\n" + b_ + b"\n", 2, 0, 1),
        ("whitespace-only line", a + b"\n \t \n" + b_ + b"\n", 2, 0, 1),
        ("CRLF blank line", a + b"\r\n\r\n" + b_ + b"\r\n", 2, 0, 1),
        ("only blanks", b"\n\n \n\r\n", 0, 0, 4),
        ("blank at end", a + b"\n\n", 1, 0, 1),
        ("CR-only is one line", a + b"\r" + b_ + b"\r", 0, 1, 0),
        ("two CRs before LF", a + b"\r\r\n", 1, 0, 0),
    ]
    for label, data, want_accepted, want_rejected, want_blank in cases:
        for chunk in (None, 1, 2, 3, 7):
            got, outcome = collect(data, chunk=chunk)
            report = outcome.report
            check(
                CID,
                f"{label} / chunk {chunk}: {want_accepted} accepted {want_rejected} rejected "
                f"{want_blank} blank",
                report.state == "completed"
                and len(got) == want_accepted
                and report.records_rejected == want_rejected
                and report.blank_lines == want_blank,
            )
    got, outcome = collect(line("x", "y") + b"\r\r\n")
    check(CID, "exactly one CR is stripped (the second is JSON whitespace)", len(got) == 1)
    # a CR must never be left inside the record
    check(
        CID,
        "no CR in accepted text",
        all("\r" not in t for c in collect(a + b"\r\n" + b_ + b"\r\n")[0] for t in texts(c)),
    )


def test_line_terminators_inside_strings_are_data() -> None:
    value = "line one two three\x85 four\x0b five\x0c six"
    data = json.dumps({"q": value, "a": "z"}, ensure_ascii=False).encode() + b"\n"
    got, outcome = collect(data)
    check(CID, "U+2028/2029/NEL/VT/FF inside a string do not split", len(got) == 1)
    check(CID, "and are preserved", texts(got[0])[0] == value if got else False)
    check(CID, "one line, one record", outcome.report.records_seen == 1)


def test_chunking_is_irrelevant() -> None:
    rng = random.Random(502)
    lines_: list[bytes] = []
    for i in range(60):
        kind = rng.randrange(8)
        if kind == 0:
            lines_.append(b"")
        elif kind == 1:
            lines_.append(b"{bad json")
        elif kind == 2:
            lines_.append(b"[1,2]")
        elif kind == 3:
            lines_.append(b"\xff\xfe" + b"x" * rng.randrange(1, 20))
        elif kind == 4:
            lines_.append(line(f"q{i}", "ab" * rng.randrange(1, 40)) + b"\r")
        else:
            lines_.append(line(f"q{i}", "مرحبا" * rng.randrange(1, 10)))
    data = b"\n".join(lines_) + b"\n"
    base_got, base = collect(data)
    check(CID, "baseline imports something", base.report.records_accepted > 5)
    for chunk in (1, 2, 3, 5, 8, 13, 64, 1000):
        got, outcome = collect(data, chunk=chunk)
        check(
            CID,
            f"chunk {chunk}: same candidates, same report",
            got == base_got and outcome.report == base.report,
        )
    rng2 = random.Random(7)
    for _ in range(25):
        cuts = sorted({rng2.randrange(1, len(data)) for _ in range(rng2.randrange(1, 12))})
        pieces = [data[i:j] for i, j in zip([0, *cuts], [*cuts, len(data)], strict=True)]
        reader = ic.ChunkReader(
            pieces, expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest()
        )
        out = ji.run_jsonl_import(
            reader,
            build_mapping(),
            verified_content_type=JSONL,
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )
        check(CID, f"random cuts {cuts[:4]}...: same report", out.report == base.report)


# --- invalid UTF-8 ---------------------------------------------------------------------

BAD_UTF8 = {
    "overlong": b"\xc0\x80",
    "encoded surrogate": b"\xed\xa0\x80",
    "truncated multibyte": b"\xe2\x82",
    "0xFF": b"\xff",
    "0xFE": b"\xfe",
    "CESU-8 pair": b"\xed\xa0\xbd\xed\xb8\x80",
    "stray continuation": b"\x80",
    "overlong slash": b"\xc0\xaf",
    "above U+10FFFF": b"\xf4\x90\x80\x80",
}


def test_utf8_bad_lines_are_rejected_alone() -> None:
    good1, good2 = line("first", "1"), line("last", "2")
    for label, bad in BAD_UTF8.items():
        positions = {
            "start": bad + b'{"q":"x","a":"y"}',
            "middle": b'{"q":"x' + bad + b'x","a":"y"}',
            "end": b'{"q":"x","a":"y"}' + bad,
            "alone": bad,
        }
        for position, content in positions.items():
            data = good1 + b"\n" + content + b"\n" + good2 + b"\n"
            for chunk in (None, 1, 2, 5):
                got, outcome = collect(data, chunk=chunk)
                report = outcome.report
                check(
                    CID,
                    f"{label} at {position} / chunk {chunk}: only line 2 is lost, as invalid_utf8",
                    report.state == "completed"
                    and codes(outcome) == [("line:2", "invalid_utf8")]
                    and [texts(c)[0] for c in got] == ["first", "last"]
                    and report.records_seen == 3,
                )


def test_utf8_never_replaces() -> None:
    # `errors="replace"` would turn this into a record containing U+FFFD
    data = b'{"q":"a\xffb","a":"y"}\n'
    got, outcome = collect(data)
    check(
        CID,
        "no replacement-character record",
        got == [] and codes(outcome) == [("line:1", "invalid_utf8")],
    )
    data = '{"q":"a�b","a":"y"}\n'.encode()
    got, _ = collect(data)
    check(CID, "a real U+FFFD in the source is data", len(got) == 1 and "�" in texts(got[0])[0])


def test_utf8_last_line_truncated_mid_character() -> None:
    data = line("a", "b") + b"\n" + '{"q":"é'.encode()[:-1]
    for chunk in (None, 1, 3):
        got, outcome = collect(data, chunk=chunk)
        check(
            CID,
            f"truncated final line / chunk {chunk}",
            len(got) == 1 and codes(outcome) == [("line:2", "invalid_utf8")],
        )


# --- BOM and encodings -----------------------------------------------------------------


def test_bom_only_at_byte_zero() -> None:
    bom = b"\xef\xbb\xbf"
    good = line("a", "b")
    for chunk in (None, 1, 2, 3):
        got, outcome = collect(bom + good + b"\n", chunk=chunk)
        check(
            CID, f"BOM at byte 0 is accepted / chunk {chunk}", len(got) == 1 and not codes(outcome)
        )
        got, outcome = collect(good + b"\n" + bom + good + b"\n" + good + b"\n", chunk=chunk)
        check(
            CID,
            f"BOM at line 2 is invalid_json / chunk {chunk}",
            len(got) == 2 and codes(outcome) == [("line:2", "invalid_json")],
        )
        got, outcome = collect(bom + bom + good + b"\n", chunk=chunk)
        check(
            CID,
            f"a second BOM is not stripped / chunk {chunk}",
            got == [] and codes(outcome) == [("line:1", "invalid_json")],
        )
        got, outcome = collect(bom, chunk=chunk)
        check(CID, f"a BOM-only source is empty / chunk {chunk}", outcome.report.records_seen == 0)
        got, outcome = collect(bom + b"\n" + good + b"\n", chunk=chunk)
        check(
            CID,
            f"BOM then a blank first line / chunk {chunk}",
            len(got) == 1 and outcome.report.blank_lines == 1,
        )
    got, outcome = collect(b"x" + bom + good + b"\n")
    check(
        CID,
        "a BOM after other bytes on line 1 is invalid_json",
        got == [] and codes(outcome) == [("line:1", "invalid_json")],
    )
    got, outcome = collect(good + bom + b"\n")
    check(
        CID,
        "a BOM at the end of a line is invalid_json",
        got == [] and codes(outcome) == [("line:1", "invalid_json")],
    )


def test_utf16_utf32_are_fatal() -> None:
    cases = {
        "UTF-16 LE BOM": b"\xff\xfe" + "{}\n".encode("utf-16-le"),
        "UTF-16 BE BOM": b"\xfe\xff" + "{}\n".encode("utf-16-be"),
        "UTF-32 LE BOM": b"\xff\xfe\x00\x00" + "{}\n".encode("utf-32-le"),
        "UTF-32 BE BOM": b"\x00\x00\xfe\xff" + "{}\n".encode("utf-32-be"),
        "UTF-16 LE no BOM": json.dumps({"q": "a", "a": "b"}).encode("utf-16-le"),
        "UTF-16 BE no BOM": json.dumps({"q": "a", "a": "b"}).encode("utf-16-be"),
        "UTF-32 LE no BOM": json.dumps({"q": "a", "a": "b"}).encode("utf-32-le"),
        "NUL first": b"\x00" + line("a", "b"),
        "NUL fourth": b"abc\x00",
        "two bytes UTF-16 BOM only": b"\xff\xfe",
    }
    for label, data in cases.items():
        for chunk in (None, 1, 3):
            got, outcome = collect(data, chunk=chunk)
            report = outcome.report
            check(
                CID,
                f"{label} / chunk {chunk}: failed/unsupported_encoding at line:1, no records",
                report.state == "failed"
                and report.failure_code == "unsupported_encoding"
                and outcome.report.fatal_locator == "line:1"
                and not got
                and report.records_seen == 0,
            )
    got, outcome = collect(b'{"q":"a","a":"b"}\n{"q":"\x00","a":"b"}\n')
    check(
        CID,
        "a NUL later in the file is just an invalid line",
        len(got) == 1 and codes(outcome) == [("line:2", "invalid_json")],
    )


# --- size ------------------------------------------------------------------------------


def test_size_boundary_table() -> None:
    limit = 256
    limits = ic.ImportLimits(max_record_bytes=limit)
    follower = line("after", "ok")
    for eol in (b"\n", b"\r\n"):
        for delta, accepted_ in ((-1, True), (0, True), (1, False), (50, False)):
            body = padded("q", "a", limit + delta)
            data = body + eol + follower + eol
            for chunk in (None, 1, 5, 64):
                got, outcome = collect(data, chunk=chunk, limits=limits)
                report = outcome.report
                want = [("line:1", "record_too_large")] if not accepted_ else []
                check(
                    CID,
                    f"limit{delta:+d} eol={eol!r} chunk={chunk}: "
                    f"{'accepted' if accepted_ else 'rejected'} and the next line imports",
                    report.state == "completed"
                    and codes(outcome) == want
                    and [texts(c)[0] for c in got] == (["q", "after"] if accepted_ else ["after"])
                    and report.records_seen == 2,
                )
    # the CR of a CRLF is not part of the line: max bytes + CR is still max bytes
    body = padded("q", "a", limit)
    got, outcome = collect(body + b"\r\n", limits=limits)
    check(CID, "max bytes plus CR is accepted", len(got) == 1)
    got, outcome = collect(body + b"x\r\n", limits=limits)
    check(
        CID,
        "max+1 bytes plus CR is rejected",
        not got and codes(outcome) == [("line:1", "record_too_large")],
    )
    got, outcome = collect(body + b"x", limits=limits)
    check(
        CID,
        "oversize final line without LF is rejected",
        not got and codes(outcome) == [("line:1", "record_too_large")],
    )


def test_size_oversize_line_does_not_swallow_followers() -> None:
    limits = ic.ImportLimits(max_record_bytes=100)
    parts: list[bytes] = []
    for i in range(30):
        parts.append(b"x" * 500 if i % 3 == 0 else line(f"q{i}", "a"))
    data = b"\n".join(parts) + b"\n"
    for chunk in (None, 1, 7, 100):
        got, outcome = collect(data, chunk=chunk, limits=limits)
        expected_bad = [f"line:{i + 1}" for i in range(30) if i % 3 == 0]
        check(
            CID,
            f"chunk {chunk}: only the oversize lines are rejected",
            [loc for loc, _ in codes(outcome)] == expected_bad
            and all(c == "record_too_large" for _, c in codes(outcome))
            and len(got) == 20,
        )


def test_size_canonical_content_that_outgrows_the_cap_is_rejected() -> None:
    # 700,000 Arabic letters: 1.4 MB as UTF-8 (under the 2 MiB line cap) but 4.2 MB once the
    # canonical content is ASCII-escaped, which is what FTF-0508 measures.
    big = "ا" * 700_000
    data = line(big, "a") + b"\n" + line("small", "b") + b"\n"
    got, outcome = collect(data)
    check(
        CID,
        "a line under the cap whose canonical content is over it is record_too_large",
        codes(outcome) == [("line:1", "record_too_large")] and len(got) == 1,
    )
    check(
        CID,
        "and nothing accepted fails FTF-0508",
        not got or validate_record_batch(got).issues == (),
    )


def test_memory_huge_record_is_discarded_not_buffered() -> None:
    # A 64 MiB line is refused without being held: peak memory stays far below its size.
    piece = b"x" * (64 * 1024)
    total = 64 * 1024 * 1024
    follower = line("after", "ok") + b"\n"
    body = [b'{"q":"' + piece] + [piece] * 1023 + [b'","a":"y"}\n', follower]
    data_size = sum(len(p) for p in body)
    digest = hashlib.sha256()
    for p in body:
        digest.update(p)
    tracemalloc.start()
    reader = ic.ChunkReader(body, expected_size=data_size, expected_sha256=digest.hexdigest())
    got: list[object] = []
    outcome = ji.run_jsonl_import(
        reader,
        build_mapping(),
        verified_content_type=JSONL,
        dataset_purpose="training",
        expected_sha256=digest.hexdigest(),
        on_accepted=got.append,
    )
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    check(
        CID, "the 64 MiB line is one rejection", codes(outcome) == [("line:1", "record_too_large")]
    )
    check(CID, "the next line imports", len(got) == 1 and outcome.report.state == "completed")
    check(
        CID,
        f"peak memory {peak} bytes is well under the line size",
        peak < 8 * 1024 * 1024 and data_size > total,
    )


def test_memory_does_not_grow_with_the_record_count() -> None:
    def peak_for(count: int) -> int:
        data = b"".join(line(f"q{i}", "a" * 40) + b"\n" for i in range(count))
        digest = hashlib.sha256(data).hexdigest()
        reader = ic.ChunkReader(
            support.split_chunks(data, 4096), expected_size=len(data), expected_sha256=digest
        )
        counter = [0]

        def count_accepted(_candidate: object) -> None:
            counter[0] += 1

        tracemalloc.start()
        ji.run_jsonl_import(
            reader,
            build_mapping(),
            verified_content_type=JSONL,
            dataset_purpose="training",
            expected_sha256=digest,
            on_accepted=count_accepted,
        )
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert counter[0] == count
        return peak - len(data)

    small = peak_for(500)
    large = peak_for(10_000)
    check(
        CID,
        f"peak with 20x the records ({large}) stays within 3x ({small})",
        large < 3 * max(small, 200_000),
    )


def test_time_one_byte_chunks_are_linear() -> None:
    # One byte at a time, a line 4x as long must take about 4x as long. A rescan or a copy of the
    # buffer per chunk makes it ~16x (and minutes at 1 MiB), whatever the machine's speed.
    def seconds(size: int) -> float:
        data = padded("q", "a", size) + b"\n" + line("after", "ok") + b"\n"
        best = float("inf")
        for _ in range(2):
            started = time.monotonic()
            got, outcome = collect(data, chunk=1)
            best = min(best, time.monotonic() - started)
            check(
                CID,
                f"{size}-byte record imports",
                len(got) == 2 and outcome.report.state == "completed",
            )
        return best

    small, large = seconds(128 * 1024), seconds(512 * 1024)
    check(
        CID,
        f"4x the bytes took {large / max(small, 1e-3):.1f}x the time ({small:.2f}s, {large:.2f}s)",
        large < 9 * max(small, 0.05) and large < 60,
    )
    limits = ic.ImportLimits(max_record_bytes=1000)

    def discard_seconds(size: int) -> float:
        started = time.monotonic()
        got, _outcome = collect(
            b"x" * size + b"\n" + line("a", "b") + b"\n", chunk=1, limits=limits
        )
        check(CID, "the follower of a discarded line imports", len(got) == 1)
        return time.monotonic() - started

    small, large = discard_seconds(128 * 1024), discard_seconds(512 * 1024)
    check(
        CID,
        f"a discarded line is linear too ({small:.2f}s, {large:.2f}s)",
        large < 9 * max(small, 0.05),
    )


# --- strict JSON axes ------------------------------------------------------------------


def nested(depth: int, inner: str = "1") -> str:
    return "[" * (depth - 1) + inner + "]" * (depth - 1)


def test_depth_axis() -> None:
    # the record object is depth 1; a value nested `d` deep under key "x" reaches depth d + 1
    for depth_total, ok in ((63, True), (64, True), (65, False)):
        value = nested(depth_total, "1")
        text = '{"q":"a","a":"b","x":' + value + "}"
        got, outcome = collect(text.encode() + b"\n" + line("n", "n") + b"\n")
        want = [] if ok else [("line:1", "nesting_too_deep")]
        check(
            CID,
            f"depth {depth_total}: {'accepted' if ok else 'nesting_too_deep'}, follower imports",
            codes(outcome) == want and len(got) == (2 if ok else 1),
        )
    deep = b'{"q":"a","a":"b","x":' + b"[" * 100_000 + b"]" * 100_000 + b"}"
    for chunk in (None, 1000):
        got, outcome = collect(deep + b"\n" + line("n", "n") + b"\n", chunk=chunk)
        check(
            CID,
            f"100,000 deep / chunk {chunk}: nesting_too_deep, no crash, follower imports",
            codes(outcome) == [("line:1", "nesting_too_deep")] and len(got) == 1,
        )
    deep_obj = b'{"q":"a","a":"b","x":' + b'{"k":' * 70_000 + b"1" + b"}" * 70_000 + b"}"
    got, outcome = collect(deep_obj + b"\n")
    check(CID, "deeply nested objects too", codes(outcome) == [("line:1", "nesting_too_deep")])
    got, outcome = collect(b'{"q":"a","a":"b","x":"' + b"[" * 100 + b'"}\n')
    check(CID, "brackets inside a string are not depth", len(got) == 1)
    lowered = ic.ImportLimits(max_depth=4)
    got, outcome = collect(
        b'{"q":"a","a":"b","x":[[[[1]]]]}\n{"q":"a","a":"b","x":[[[1]]]}\n', limits=lowered
    )
    check(
        CID,
        "a lowered max_depth is honoured",
        codes(outcome) == [("line:1", "nesting_too_deep")] and len(got) == 1,
    )


def test_duplicate_keys_at_every_level() -> None:
    cases = {
        "top level, mapped key": '{"q":"a","q":"b","a":"c"}',
        "top level, unmapped key": '{"q":"a","a":"c","z":1,"z":2}',
        "nested object": '{"q":"a","a":"c","z":{"k":1,"k":2}}',
        "inside an array": '{"q":"a","a":"c","z":[{"k":1},{"k":1,"k":1}]}',
        "same value twice": '{"q":"a","q":"a","a":"c"}',
        "escaped spelling": '{"q":"a","\\u0071":"b","a":"c"}',
        "deep": '{"q":"a","a":"c","z":{"y":{"x":{"w":1,"w":2}}}}',
    }
    for label, text in cases.items():
        got, outcome = collect(text.encode() + b"\n" + line("n", "n") + b"\n")
        check(
            CID,
            f"{label}: duplicate_key and the follower imports",
            codes(outcome) == [("line:1", "duplicate_key")] and len(got) == 1,
        )
    got, outcome = collect(b'{"q":"a","a":"c","z":{"k":1},"y":{"k":2}}\n')
    check(CID, "the same key in sibling objects is fine", len(got) == 1)


def test_numbers_axis() -> None:
    bad = {
        "NaN": ("NaN", "non_finite_number"),
        "Infinity": ("Infinity", "non_finite_number"),
        "-Infinity": ("-Infinity", "non_finite_number"),
        "1e999": ("1e999", "non_finite_number"),
        "-1e999": ("-1e999", "non_finite_number"),
        "4301 digits": ("1" * 4301, "number_out_of_range"),
        "-4301 digits": ("-" + "1" * 4301, "number_out_of_range"),
    }
    for label, (literal, code) in bad.items():
        text = '{"q":"a","a":"b","x":' + literal + "}"
        got, outcome = collect(text.encode() + b"\n" + line("n", "n") + b"\n")
        check(
            CID,
            f"{label}: {code} and the follower imports",
            codes(outcome) == [("line:1", code)] and len(got) == 1,
        )
    for label, literal in (
        ("4300 digits", "1" * 4300),
        ("small float", "1.5e-3"),
        ("big but finite float", "1e308"),
        ("negative zero", "-0"),
    ):
        got, outcome = collect(('{"q":"a","a":"b","x":' + literal + "}\n").encode())
        check(CID, f"{label} is accepted", len(got) == 1 and not codes(outcome))


def test_unicode_escapes_axis() -> None:
    for label, text, ok in (
        ("lone high surrogate", '{"q":"\\ud800","a":"b"}', False),
        ("lone low surrogate", '{"q":"\\udc00","a":"b"}', False),
        ("reversed pair", '{"q":"\\udc00\\ud800","a":"b"}', False),
        ("lone surrogate in a key", '{"q":"a","a":"b","\\ud800":1}', False),
        ("paired escape", '{"q":"\\ud83d\\ude00","a":"b"}', True),
        ("BMP escape", '{"q":"\\u0627","a":"b"}', True),
    ):
        got, outcome = collect(text.encode() + b"\n")
        want = [] if ok else [("line:1", "invalid_unicode")]
        check(
            CID,
            f"{label}: {'accepted' if ok else 'invalid_unicode'}",
            codes(outcome) == want and len(got) == (1 if ok else 0),
        )
    got, _ = collect(b'{"q":"\\ud83d\\ude00","a":"b"}\n')
    check(
        CID,
        "the paired escape decodes to one emoji",
        bool(got) and texts(got[0])[0] == "\U0001f600",
    )


def test_non_object_axis() -> None:
    for text in ("[]", '["q","a"]', '"string"', "1", "1.5", "null", "true", "false", "[{}]", '""'):
        got, outcome = collect(text.encode() + b"\n" + line("n", "n") + b"\n")
        check(
            CID,
            f"{text}: record_not_object and the follower imports",
            codes(outcome) == [("line:1", "record_not_object")] and len(got) == 1,
        )


def test_trailing_garbage_axis() -> None:
    one = line("a", "b").decode()
    for label, text in (
        ("two objects", one + one),
        ("trailing text", one + " x"),
        ("trailing NUL", one + "\x00"),
        ("trailing comma", one + ","),
        ("trailing brace", one + "}"),
        ("leading text", "x " + one),
        ("trailing line separator char", one + "\x0b"),
        ("empty key colon", '{"q":"a","a":}'),
        ("unterminated", '{"q":"a","a":"b"'),
        ("single quotes", "{'q':'a','a':'b'}"),
        ("comments", one + " // c"),
    ):
        got, outcome = collect(text.encode() + b"\n" + line("n", "n") + b"\n")
        check(
            CID,
            f"{label}: invalid_json and the follower imports",
            codes(outcome) == [("line:1", "invalid_json")] and len(got) == 1,
        )
    got, outcome = collect((one + " \t ").encode() + b"\n")
    check(CID, "trailing JSON whitespace is fine", len(got) == 1)


# --- mixed critical schemas ------------------------------------------------------------


def test_mixed_schemas_each_line_gets_its_code() -> None:
    def conv(user: str) -> bytes:
        return json.dumps(
            {"messages": [{"role": "user", "content": user}, {"role": "assistant", "content": "r"}]}
        ).encode()

    lines_ = [
        (conv("ok1"), None),
        (b'{"q":"a prompt/completion row in a messages source","a":"b"}', "required_field_missing"),
        (b'{"messages":"not a list"}', "mapping_type_mismatch"),
        (b'{"messages":[{"role":"robot","content":"hi"}]}', "unmapped_role"),
        (b'{"messages":[{"role":"user"}]}', "required_field_missing"),
        (b'{"messages":[{"role":"tool","content":"x"}]}', "canonical_schema_invalid"),
        (b"[1]", "record_not_object"),
        (b"{broken", "invalid_json"),
        (b"\xff\xfe", "invalid_utf8"),
        (b'{"messages":[],"messages":[]}', "duplicate_key"),
        (b'{"messages":[],"x":NaN}', "non_finite_number"),
        (b"", "BLANK"),
        (conv("ok2"), None),
        (b'{"messages":[{"role":5,"content":"x"}]}', "mapping_type_mismatch"),
        (conv("ok3"), None),
        (b"null", "record_not_object"),
    ]
    data = b"\n".join(raw for raw, _ in lines_) + b"\n"
    expected: list[tuple[str, str]] = []
    accepted_count = blank = 0
    for number, (_raw, code) in enumerate(lines_, start=1):
        if code is None:
            accepted_count += 1
        elif code == "BLANK":
            blank += 1
        else:
            expected.append((f"line:{number}", code))
    for chunk in (None, 1, 4, 33):
        got, outcome = collect(data, chunk=chunk, mapping=MESSAGES_MAPPING)
        report = outcome.report
        check(CID, f"chunk {chunk}: completed", report.state == "completed")
        check(
            CID,
            f"chunk {chunk}: every rejection has its code and locator",
            codes(outcome) == expected,
        )
        check(
            CID,
            f"chunk {chunk}: invariant and counts are exact",
            report.records_accepted == accepted_count == len(got)
            and report.records_rejected == len(expected)
            and report.records_seen == accepted_count + len(expected)
            and report.blank_lines == blank,
        )
        counts = dict(report.rejection_counts)
        check(
            CID,
            f"chunk {chunk}: per-code counts are exact",
            counts == {c: sum(1 for _, x in expected if x == c) for c in {c for _, c in expected}},
        )
        check(
            CID,
            f"chunk {chunk}: record_index counts rejected rows and skips blank lines",
            [c["record_index"] for c in got] == [0, 11, 13],
        )
    check(
        CID,
        "line count equals seen + blank for every source",
        collect(data, mapping=MESSAGES_MAPPING)[1].report.records_seen
        + collect(data, mapping=MESSAGES_MAPPING)[1].report.blank_lines
        == len(lines_),
    )


def test_mapping_roles_and_unmapped_keys() -> None:
    rows = [
        {"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]},
        {"messages": [{"role": "User", "content": "a"}, {"role": "assistant", "content": "b"}]},
        {"messages": [{"role": "user ", "content": "a"}, {"role": "assistant", "content": "b"}]},
        {
            "messages": [
                {"role": "user", "content": "a", "extra": 1},
                {"role": "assistant", "content": "b"},
            ]
        },
        {"messages": "nope"},
        {"other": []},
        {"messages": []},
        {"messages": [5]},
    ]
    data = b"\n".join(json.dumps(r).encode() for r in rows) + b"\n"
    got, outcome = collect(data, mapping=MESSAGES_MAPPING)
    check(
        CID,
        "role_map is exact; unmapped keys ignored by policy",
        [(loc, c) for loc, c in codes(outcome)]
        == [
            ("line:2", "unmapped_role"),
            ("line:3", "unmapped_role"),
            ("line:5", "mapping_type_mismatch"),
            ("line:6", "required_field_missing"),
            ("line:7", "mapping_type_mismatch"),
            ("line:8", "mapping_type_mismatch"),
        ]
        and len(got) == 2,
    )
    reject = MESSAGES_MAPPING.replace('"ignore"', '"reject"')
    got, outcome = collect(data, mapping=reject)
    check(
        CID,
        "on_unmapped_message_keys=reject rejects the extra key",
        ("line:4", "unmapped_message_key") in codes(outcome),
    )


def test_data_cannot_choose_its_own_purpose() -> None:
    data = line("q", "a", purpose="evaluation", split="held_out", quality_label="gold") + b"\n"
    got, outcome = collect(data)
    flat = json.dumps(got, sort_keys=True)
    check(
        CID,
        "a purpose/split/quality field in the data is not read",
        len(got) == 1
        and "held_out" not in flat
        and "evaluation" not in flat
        and "gold" not in flat
        and outcome.report.dataset_purpose == "training",
    )
    quality = (
        '{"shape":"prompt_completion","prompt":{"key":"q"},"completion":{"key":"a"},'
        '"metadata":{"quality_label":{"key":"ql"}}}'
    )
    got, _ = collect(line("q", "a") + b"\n", mapping=quality)
    check(
        CID,
        "an absent quality_label slot is omitted, not defaulted",
        bool(got) and "quality_label" not in json.dumps(got[0]["content"]),
    )


# --- mapping validation -----------------------------------------------------------------


def test_mapping_must_describe_a_jsonl_source() -> None:
    bad = {
        "csv block": PC_MAPPING[:-1]
        + ',"csv":{"encoding":"utf-8","delimiter":",","has_header":true,"header_row":0}}',
        "xlsx block": PC_MAPPING[:-1] + ',"xlsx":{"sheet_name":"s","header_row":0}}',
        "json block": PC_MAPPING[:-1] + ',"json":{"container":"","ignored_top_level_keys":[]}}',
        "header slot": PC_MAPPING.replace('{"key":"q"}', '{"header":"q"}'),
        "index slot": PC_MAPPING.replace('{"key":"q"}', '{"index":0}'),
        "letters slot": PC_MAPPING.replace('{"key":"q"}', '{"letters":"A"}'),
        "header group key": PC_MAPPING[:-1] + ',"group_key":{"header":"g"}}',
    }
    for label, text in bad.items():
        mapping = ic.parse_mapping(text)
        data = line() + b"\n"
        reader = make_reader(data)
        try:
            ji.jsonl_events(reader, mapping, verified_content_type=JSONL, dataset_sha256="0" * 64)
        except ic.MappingInvalidError:
            check(CID, f"{label}: refused before reading a byte", reader.bytes_read == 0)
        else:
            check(CID, f"{label}: refused", False)
        try:
            run(data, mapping=text)
        except ic.MappingInvalidError:
            pass
        else:
            check(CID, f"{label}: run refuses it too", False)


def test_canonical_size_boundary_matches_ftf_0508() -> None:
    import json as _json

    from workers.data.record_schema_validator import MAX_CANONICAL_RECORD_BYTES

    def candidate_for(pad: int) -> dict[str, object]:
        got, _ = collect(line("x" * pad, "a") + b"\n")
        return got[0] if got else {}

    def size(c: dict[str, object]) -> int:
        return len(_json.dumps(c["content"], sort_keys=True, separators=(",", ":")).encode())

    probe = size(candidate_for(10))
    exact = MAX_CANONICAL_RECORD_BYTES - (probe - 10)  # q length giving the exact limit
    at_limit = candidate_for(exact)
    check(
        CID,
        "a record at exactly the canonical limit is accepted",
        size(at_limit) == MAX_CANONICAL_RECORD_BYTES,
    )
    check(CID, "and FTF-0508 agrees (0 issues)", validate_record_batch([at_limit]).issues == ())
    got, outcome = collect(line("x" * (exact + 1), "a") + b"\n")
    check(
        CID,
        "one byte over the canonical limit is record_too_large",
        got == [] and codes(outcome) == [("line:1", "record_too_large")],
    )
    over = dict(at_limit)
    over["content"] = {**at_limit["content"], "metadata": {"pad": "x"}}  # type: ignore[dict-item]
    check(CID, "FTF-0508 refuses what we refuse", validate_record_batch([over]).issues != ())


def test_first_four_bytes_are_judged_before_the_source_can_fail() -> None:
    data = b"\xff\xfe\x00\x00"
    for chunks in ([data], [data[:1], data[1:]], [data[:3], data[3:]]):
        source = support.ExplodingSource(chunks, ConnectionResetError("boom"))
        reader = ic.ChunkReader(
            source, expected_size=10, expected_sha256=hashlib.sha256(b"x").hexdigest()
        )
        outcome = ji.run_jsonl_import(
            reader,
            build_mapping(),
            verified_content_type=JSONL,
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(b"x").hexdigest(),
        )
        check(
            CID,
            f"{len(chunks)} chunk(s): a UTF-32 head is unsupported_encoding, not an outage",
            outcome.report.failure_code == "unsupported_encoding",
        )


def test_mapping_slot_kinds_are_matched_exactly() -> None:
    import dataclasses

    mapping = build_mapping()
    for kind in ("Key", "key ", " key", "KEY", "header"):
        odd = dataclasses.replace(mapping, prompt=ic.Slot(kind, "q"))
        try:
            ji.jsonl_events(
                make_reader(b""), odd, verified_content_type=JSONL, dataset_sha256="0" * 64
            )
        except ic.MappingInvalidError:
            continue
        check(CID, f"slot kind {kind!r} is refused", False)
    for block in ("CSV", "csv ", "Json"):
        odd = dataclasses.replace(mapping, document={**mapping.document, block: {}})
        check(CID, f"unknown block {block!r} is not mistaken for a tabular block", _accepts(odd))
    for block in ("csv", "xlsx", "json"):
        odd = dataclasses.replace(mapping, document={**mapping.document, block: {}})
        check(CID, f"block {block!r} is refused", not _accepts(odd))


def _accepts(mapping: ic.ImportMapping) -> bool:
    try:
        ji.jsonl_events(
            make_reader(b""), mapping, verified_content_type=JSONL, dataset_sha256="0" * 64
        )
    except ic.MappingInvalidError:
        return False
    return True


def test_source_type_is_validated() -> None:
    for odd in ("", "UPLOAD", "nope", None, 5):
        try:
            run(line() + b"\n", source_type=odd)
        except ValueError:
            pass
        else:
            check(CID, f"source_type {odd!r} is refused", False)
    got, outcome = collect(line() + b"\n", source_type="upload")
    check(CID, "a supported source_type is accepted", len(got) == 1)


# --- content type gate ------------------------------------------------------------------


def test_content_type_gate() -> None:
    data = line() + b"\n"
    for good in (JSONL, NDJSON):
        outcome = run(data, content_type=good)
        check(
            CID,
            f"{good} is imported",
            outcome.report.state == "completed" and outcome.report.records_accepted == 1,
        )

    class Shouting(str):
        pass

    for odd in (
        "text/csv",
        "application/json",
        "text/plain",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/JSONL",
        " application/jsonl",
        "application/jsonl ",
        "application/jsonl; charset=utf-8",
        "application/x-ndjson\n",
        "",
        None,
        b"application/jsonl",
        5,
        Shouting(JSONL),
    ):
        reader = make_reader(data)
        outcome = ji.run_jsonl_import(
            reader,
            build_mapping(),
            verified_content_type=odd,  # type: ignore[arg-type]
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )
        check(
            CID,
            f"{odd!r} is refused before a byte is read",
            outcome.report.state == "failed"
            and outcome.report.failure_code == "content_type_not_supported_by_importer"
            and reader.bytes_read == 0
            and outcome.report.records_seen == 0,
        )
    stream = ji.jsonl_events(
        make_reader(data),
        build_mapping(),
        verified_content_type="text/csv",
        dataset_sha256="0" * 64,
    )
    result = ic.preview(stream, 5)
    check(CID, "preview takes the same gate", result.accepted == () and not result.complete)


# --- source failures --------------------------------------------------------------------


def test_source_failures_are_never_completed() -> None:
    data = b"".join(line(f"r{i}", "x") + b"\n" for i in range(20))
    chunks = support.split_chunks(data, 16)
    for at in (0, 1, len(chunks) // 2, len(chunks)):
        source = support.ExplodingSource(chunks, ConnectionResetError("boom"), at=at)
        reader = ic.ChunkReader(
            source, expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest()
        )
        outcome = ji.run_jsonl_import(
            reader,
            build_mapping(),
            verified_content_type=JSONL,
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )
        check(
            CID,
            f"source error at chunk {at}: blocked/source_unavailable",
            outcome.report.state == "blocked"
            and outcome.report.failure_code == "source_unavailable",
        )
    for label, changed in (
        ("last byte", data[:-1] + b"Z"),
        ("shorter", data[:-5]),
        ("longer", data + b"{}\n"),
    ):
        reader = ic.ChunkReader(
            [changed], expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest()
        )
        outcome = ji.run_jsonl_import(
            reader,
            build_mapping(),
            verified_content_type=JSONL,
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )
        check(
            CID,
            f"{label} changed: failed/source_changed",
            outcome.report.state == "failed" and outcome.report.failure_code == "source_changed",
        )
    # a final line with no LF whose source then fails the hash is not completed either
    tail = line("tail", "x")
    wrong = tail[:-1] + b" "
    reader = ic.ChunkReader(
        [wrong], expected_size=len(tail), expected_sha256=hashlib.sha256(tail).hexdigest()
    )
    outcome = ji.run_jsonl_import(
        reader,
        build_mapping(),
        verified_content_type=JSONL,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(tail).hexdigest(),
    )
    check(
        CID,
        "a changed unterminated tail is source_changed",
        outcome.report.failure_code == "source_changed",
    )


def test_deadline_and_rejection_budget() -> None:
    data = b"".join(line(f"r{i}", "x") + b"\n" for i in range(6))
    clock = support.FakeClock()
    reader = ic.ChunkReader(
        [data],  # one chunk: only the per-event check can notice the deadline
        expected_size=len(data),
        expected_sha256=hashlib.sha256(data).hexdigest(),
        clock=clock,
    )
    seen: list[object] = []

    def advance(candidate: object) -> None:
        seen.append(candidate)
        clock.advance(10_000)

    outcome = ji.run_jsonl_import(
        reader,
        build_mapping(),
        verified_content_type=JSONL,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        on_accepted=advance,
    )
    check(
        CID,
        "a passed deadline is failed/parse_timeout",
        outcome.report.failure_code == "parse_timeout" and len(seen) == 1,
    )
    limits = ic.ImportLimits(max_rejections=3)
    bad = b"[1]\n" * 10 + line() + b"\n"
    got, outcome = collect(bad, limits=limits)
    check(
        CID,
        "too many rejections is failed, never completed",
        outcome.report.state == "failed"
        and outcome.report.failure_code == "too_many_rejections"
        and outcome.report.records_rejected == 3
        and not got,
    )
    exact = b"[1]\n" * 3 + line() + b"\n"
    got, outcome = collect(exact, limits=limits)
    check(
        CID, "exactly the budget completes", outcome.report.state == "completed" and len(got) == 1
    )
    small = ic.ImportLimits(max_source_bytes=10)
    outcome = run(line() + b"\n", limits=small)
    check(
        CID,
        "a source over max_source_bytes is source_too_large",
        outcome.report.failure_code == "source_too_large",
    )


# --- report, determinism, generator, preview ---------------------------------------------


def test_report_is_deterministic() -> None:
    data = b"\n".join([line("a", "b"), b"[1]", b"", line("c", "d"), b"\xff"]) + b"\n"
    first = collect(data)
    second = collect(data, chunk=3)
    check(CID, "same bytes, same report hash for any chunking", first[1].report == second[1].report)
    check(CID, "same candidates", first[0] == second[0])
    ids = [texts(c)[0] for c in first[0]]
    check(CID, "record content ok", ids == ["a", "c"])
    other = run(data + b"\n")
    check(
        CID,
        "different bytes, different report hash",
        other.report.report_sha256 != first[1].report.report_sha256,
    )
    code = (
        "import sys,hashlib\n"
        f"sys.path.insert(0,{str(ROOT)!r})\n"
        "from workers.data import import_common as ic, jsonl_importer as ji\n"
        f"data={data!r}\n"
        f"m=ic.parse_mapping({PC_MAPPING!r})\n"
        "r=ic.ChunkReader([data],expected_size=len(data),expected_sha256=hashlib.sha256(data).hexdigest())\n"
        "o=ji.run_jsonl_import(r,m,verified_content_type='application/jsonl',dataset_purpose='training',"
        "expected_sha256=hashlib.sha256(data).hexdigest())\n"
        "print(o.report.report_sha256)\n"
    )
    outputs = set()
    for seed in ("0", "1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        outputs.add(
            subprocess.run(
                [sys.executable, "-I", "-c", code],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            ).stdout.strip()
        )
    check(
        CID,
        "report hash is stable across PYTHONHASHSEED",
        outputs == {first[1].report.report_sha256},
    )
    ids_a = {texts(c)[0] for c in first[0]}
    check(CID, "record ids are not random", len(ids_a) == 2)
    candidates_again = collect(data)[0]
    check(CID, "candidates (incl. normalized_sha256) repeat exactly", candidates_again == first[0])


def test_events_generator_ends_with_one_report() -> None:
    data = b"\n".join([line("a", "b"), b"[1]", b"", line("c", "d")]) + b"\n"
    items = list(
        ji.import_jsonl(
            support.split_chunks(data, 5),
            mapping=build_mapping(),
            expected_sha256=hashlib.sha256(data).hexdigest(),
            expected_size=len(data),
            verified_content_type=NDJSON,
            dataset_purpose="training",
        )
    )
    reports = [i for i in items if isinstance(i, ic.ImportReport)]
    check(CID, "exactly one report, and it is last", len(reports) == 1 and items[-1] is reports[0])
    kinds = [type(i).__name__ for i in items[:-1]]
    check(
        CID,
        "events stream in source order",
        kinds == ["AcceptedRecord", "RowRejection", "BlankLine", "AcceptedRecord"],
    )
    check(
        CID, "report counts agree", reports[0].records_accepted == 2 and reports[0].blank_lines == 1
    )
    exploding = support.ExplodingSource(support.split_chunks(data, 5), OSError("down"), at=2)
    items = list(
        ji.import_jsonl(
            exploding,
            mapping=build_mapping(),
            expected_sha256=hashlib.sha256(data).hexdigest(),
            expected_size=len(data),
            verified_content_type=JSONL,
            dataset_purpose="training",
        )
    )
    last = items[-1]
    check(
        CID,
        "a failing source ends the stream with a blocked report, not an exception",
        isinstance(last, ic.ImportReport)
        and last.state == "blocked"
        and last.failure_code == "source_unavailable",
    )
    items = list(
        ji.import_jsonl(
            [data],
            mapping=build_mapping(),
            expected_sha256=hashlib.sha256(data).hexdigest(),
            expected_size=len(data),
            verified_content_type="text/csv",
            dataset_purpose="training",
        )
    )
    check(
        CID,
        "a wrong content type ends with a failed report and no events",
        len(items) == 1
        and isinstance(items[0], ic.ImportReport)
        and items[0].failure_code == "content_type_not_supported_by_importer",
    )


def test_preview_uses_the_same_path() -> None:
    data = b"".join((line(f"q{i}", "a") if i % 4 else b"[1]") + b"\n" for i in range(60))
    stream = ji.jsonl_events(
        make_reader(data, 7),
        build_mapping(),
        verified_content_type=JSONL,
        dataset_sha256=hashlib.sha256(data).hexdigest(),
    )
    result = ic.preview(stream, 5)
    check(
        CID,
        "five accepted, rejections seen on the way",
        len(result.accepted) == 5 and len(result.rejections) == 2,
    )
    check(CID, "incomplete preview says so", not result.complete)
    full = [c for c in collect(data)[0]][:5]
    check(
        CID,
        "preview candidates equal the first import candidates",
        list(map(dict, result.accepted)) == full,
    )
    try:
        ic.preview(iter(()), 21)
    except ValueError:
        pass
    else:
        check(CID, "preview of 21 raises", False)


# --- differential and META-LEAK ----------------------------------------------------------


def _random_value(rng: random.Random, depth: int = 0) -> object:
    kind = rng.randrange(7 if depth < 3 else 5)
    if kind == 0:
        return rng.choice([None, True, False])
    if kind == 1:
        return rng.randrange(-(10**12), 10**12)
    if kind == 2:
        return rng.random() * 1000
    if kind == 3:
        return "".join(rng.choice('abcمرحبا "\\\n\t é\U0001f600') for _ in range(rng.randrange(8)))
    if kind == 4:
        return rng.choice(["", "x", "00123"])
    if kind == 5:
        return [_random_value(rng, depth + 1) for _ in range(rng.randrange(4))]
    return {f"k{i}": _random_value(rng, depth + 1) for i in range(rng.randrange(4))}


def test_differential_against_json_loads() -> None:
    rng = random.Random(20260502)
    for round_ in range(200):
        q = "".join(rng.choice('abcمرحبا "\\\n\t é\U0001f600') for _ in range(rng.randrange(1, 30)))
        a = "".join(rng.choice("xyz١٢٣ ") for _ in range(rng.randrange(1, 30)))
        extras = {f"x{i}": _random_value(rng) for i in range(rng.randrange(4))}
        record = {"q": q, "a": a, **extras}
        text = json.dumps(record, ensure_ascii=bool(round_ % 2), indent=None if round_ % 3 else 1)
        text = text.replace("\n", " ") if round_ % 3 == 0 else text
        data = text.encode() + (b"\r\n" if round_ % 5 == 0 else b"\n")
        got, outcome = collect(data, chunk=rng.choice([None, 1, 3, 11]))
        reference = json.loads(text)
        check(
            CID,
            f"differential {round_}: accepted with the reference's text",
            len(got) == 1 and texts(got[0]) == [reference["q"], reference["a"]],
        )


def test_meta_leak() -> None:
    m_val = support.make_marker("VALUE")
    m_key = support.make_marker("KEY")
    m_bad = support.make_marker("BADBYTES")
    m_num = support.make_marker("NUMBER")
    lines_ = [
        line(m_val, m_val),
        json.dumps({"q": m_val, "a": m_val, m_key: 1, "q2": {m_key: 1, m_key + "": 2}})
        .encode()
        .replace(b'"q2"', b'"q2"'),
        ('{"q":"' + m_val + '","a":"b","' + m_key + '":1,"' + m_key + '":2}').encode(),
        b'{"q":"' + m_bad.encode() + b'\xff","a":"b"}',
        ('{"q":"' + m_val + '","a":' + m_num + "}").encode(),
        ('["' + m_val + '"]').encode(),
        ('{"messages":[{"role":"' + m_val + '","content":"' + m_val + '"}]}').encode(),
        ('{"q":"' + m_val + '","a":"b","x":' + "[" * 100_000).encode(),
    ]
    data = b"\n".join(lines_) + b"\n"
    markers = [m_val, m_key, m_bad, m_num]
    got, outcome = collect(data)
    leaks = support.leaked_markers(outcome.report, markers)
    check(CID, f"no marker in the report or any rejection ({leaks})", not leaks)
    check(
        CID,
        "no marker in the locators",
        not support.leaked_markers(outcome.report.fatal_locator, markers),
    )
    check(CID, "rejections exist to be leak-checked", outcome.report.records_rejected >= 5)
    got2, outcome2 = collect(data, mapping=MESSAGES_MAPPING)
    check(
        CID, "nor under the messages mapping", not support.leaked_markers(outcome2.report, markers)
    )
    # fatal paths
    for label, fatal in (
        ("utf-16", (m_val.encode("utf-16-le"))),
        ("bad content type", None),
    ):
        if fatal is None:
            out = run(data, content_type=m_val)
        else:
            out = run(b"\xff\xfe" + fatal)
        check(
            CID,
            f"{label}: no marker in the fatal report",
            not support.leaked_markers((out.report, out.report.fatal_locator), markers),
        )
    # exceptions raised by the importer name nothing from the data
    try:
        run(data, source_type=m_val)
    except ValueError as exc:
        check(CID, "the source_type error names no value", not support.leaked_markers(exc, markers))
    try:
        run(
            data,
            mapping='{"shape":"prompt_completion","prompt":{"header":"'
            + m_key
            + '"},"completion":{"key":"a"}}',
        )
    except ic.MappingInvalidError as exc:
        check(CID, "mapping errors name no value", not support.leaked_markers(exc, markers))
    # the source's own exception text never reaches the report
    source = support.ExplodingSource([data[:20]], ConnectionResetError(m_val), at=1)
    reader = ic.ChunkReader(
        source, expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest()
    )
    out = ji.run_jsonl_import(
        reader,
        build_mapping(),
        verified_content_type=JSONL,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
    )
    check(
        CID,
        "a source error's text is not in the report",
        not support.leaked_markers(out.report, [m_val]),
    )


def test_catalog_covers_every_emitted_code() -> None:
    data = (
        b"\n".join(
            [
                b"[1]",
                b"{bad",
                b"\xff",
                b'{"q":1,"a":"b"}',
                b'{"a":"b"}',
                b'{"q":"a","q":"b","a":"c"}',
                b'{"q":"a","a":NaN}',
                b'{"q":"a","a":"b","x":' + b"1" * 4301 + b"}",
                b'{"q":"\\ud800","a":"b"}',
                b'{"q":"a","a":"b","x":' + b"[" * 200 + b"]" * 200 + b"}",
            ]
        )
        + b"\n"
    )
    outcome = run(data)
    emitted = {r.code for r in outcome.report.rejections}
    check(
        CID,
        f"every emitted code is in REJECTION_CODES ({emitted - ic.REJECTION_CODES})",
        emitted <= ic.REJECTION_CODES,
    )
    check(CID, "the import saw all those kinds", len(emitted) >= 8)


# --- round 3 ---------------------------------------------------------------------------

OTHER_SHA = hashlib.sha256(b"a different source").hexdigest()


def test_expected_sha_mismatch_fails_before_any_record() -> None:
    data = line("q1", "a1") + b"\n" + line("q2", "a2") + b"\n"
    got: list[object] = []
    outcome = ji.run_jsonl_import(
        make_reader(data),  # declared (and verified) for hash Y ...
        build_mapping(),
        verified_content_type=JSONL,
        dataset_purpose="training",
        expected_sha256=OTHER_SHA,  # ... but the caller expects X
        on_accepted=got.append,
    )
    report = outcome.report
    check(
        CID,
        "a reader declared for another hash ends failed / source_changed",
        report.state == "failed" and report.failure_code == "source_changed",
    )
    check(
        CID,
        "and no record was accepted, rejected or tagged with the wrong hash",
        not got and report.records_seen == 0 and report.source_bytes == 0,
    )
    stream = ji.jsonl_events(
        make_reader(data),
        build_mapping(),
        verified_content_type=JSONL,
        dataset_sha256=OTHER_SHA,
    )
    try:
        next(stream)
        raised = None
    except ic.ImportAborted as exc:
        raised = exc.failure_code
    check(CID, "jsonl_events refuses on its first next()", raised == "source_changed")
    ok = run(data)
    check(CID, "the matching hash still imports", ok.report.state == "completed")
    # negative: a one-character difference in the expected hash is enough
    near = data and hashlib.sha256(data).hexdigest()
    near = near[:-1] + ("0" if near[-1] != "0" else "1")
    out = ji.run_jsonl_import(
        make_reader(data),
        build_mapping(),
        verified_content_type=JSONL,
        dataset_purpose="training",
        expected_sha256=near,
    )
    check(CID, "a near-miss hash is refused too", out.report.failure_code == "source_changed")


def test_fatal_locator_is_inside_the_report() -> None:
    data = b"\xff\xfe" + "{}".encode("utf-16-le")
    outcome = run(data)
    report = outcome.report
    check(
        CID,
        "the fatal locator is the report's own field",
        report.fatal_locator == "line:1" and True,
    )
    check(CID, "on a LocatedImportReport", isinstance(report, ic.LocatedImportReport))
    events = list(
        ji.import_jsonl(
            [data],
            mapping=build_mapping(),
            expected_sha256=hashlib.sha256(data).hexdigest(),
            expected_size=len(data),
            verified_content_type=JSONL,
            dataset_purpose="training",
        )
    )
    last = events[-1]
    check(
        CID,
        "import_jsonl's final report carries it",
        isinstance(last, ic.ImportReport) and last.fatal_locator == "line:1",
    )
    try:
        ic.LocatedImportReport(
            **{f: getattr(report, f) for f in report.__dataclass_fields__ if f != "fatal_locator"},
            fatal_locator="line:2",
        )
        moved = True
    except ValueError:
        moved = False
    check(CID, "the digest covers the locator: another line fails the seal", not moved)
    done = run(line("q", "a") + b"\n")
    check(
        CID,
        "a completed import has no locator",
        done.report.fatal_locator is None,
    )
    changed = line("q", "a") + b"\n" + b"x"
    out = ji.run_jsonl_import(
        make_reader(changed[:-1], expected_sha=hashlib.sha256(changed).hexdigest()),
        build_mapping(),
        verified_content_type=JSONL,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(changed).hexdigest(),
    )
    check(
        CID,
        "a source problem that is not a line problem has no locator",
        out.report.state == "failed" and out.report.fatal_locator is None,
    )


def test_size_boundary_at_the_production_constant() -> None:
    limit = ic.DEFAULT_LIMITS.max_record_bytes
    check(CID, "the production line cap is the FTF-0508 cap", limit == 2 * 1024 * 1024)
    follower = line("after", "ok")
    for eol in (b"\n", b"\r\n"):
        for delta, accepted_ in ((-1, True), (0, True), (1, False)):
            data = padded("q", "a", limit + delta) + eol + follower + eol
            for chunk in (None, 4096, 1 << 20):
                got, outcome = collect(data, chunk=chunk)
                want = [] if accepted_ else [("line:1", "record_too_large")]
                check(
                    CID,
                    f"production limit{delta:+d} eol={eol!r} chunk={chunk}",
                    outcome.report.state == "completed"
                    and codes(outcome) == want
                    and [texts(c)[0] for c in got] == (["q", "after"] if accepted_ else ["after"])
                    and outcome.report.records_seen == 2,
                )


def test_time_max_record_in_one_byte_chunks_has_a_budget() -> None:
    limit = ic.DEFAULT_LIMITS.max_record_bytes
    data = padded("q", "a", limit) + b"\n" + line("after", "ok") + b"\n"
    started = time.monotonic()
    got, outcome = collect(data, chunk=1)
    elapsed = time.monotonic() - started
    check(
        CID,
        "a 2 MiB record in 1-byte chunks imports with its follower",
        len(got) == 2 and outcome.report.state == "completed",
    )
    check(CID, f"and inside a fixed budget ({elapsed:.1f}s of 120s)", elapsed < 120)


def test_chunking_every_offset_of_small_inputs() -> None:
    sources = [
        line("q1", "a1") + b"\n" + b"\n" + b"{bad\n" + line("q2", "مرحبا") + b"\r\n",
        b"\xef\xbb\xbf" + line("q", "a") + b"\r\n" + b"\xff\n" + line("z", "y"),
        b"[1]\n" + padded("q", "a", 200) + b"\r\n\r\n",
        b"\n\n\n",
        b"",
    ]
    for data in sources:
        base_got, base = collect(data)
        seven_got, seven = collect(data, chunk=7)
        check(
            CID,
            f"7-byte chunks give the same result ({len(data)} bytes)",
            seven_got == base_got and seven.report == base.report,
        )
        for cut in range(1, len(data)):
            reader = ic.ChunkReader(
                [data[:cut], data[cut:]],
                expected_size=len(data),
                expected_sha256=hashlib.sha256(data).hexdigest(),
            )
            got: list[dict[str, object]] = []
            out = ji.run_jsonl_import(
                reader,
                build_mapping(),
                verified_content_type=JSONL,
                dataset_purpose="training",
                expected_sha256=hashlib.sha256(data).hexdigest(),
                on_accepted=lambda c, got=got: got.append(dict(c)),  # type: ignore[misc]
            )
            if got != base_got or out.report != base.report:
                check(CID, f"split at {cut} of {len(data)} bytes differs", False)
                break
        else:
            check(CID, f"every split point of a {len(data)}-byte input agrees", True)


def test_memory_ratio_between_sizes_is_within_one_and_a_half() -> None:
    def peak_for(mib: int) -> int:
        record = padded("q", "a", 64 * 1024 - 1) + b"\n"  # few, large lines: tracemalloc stays fast
        pieces = [record] * (mib * 16)
        size = sum(len(piece) for piece in pieces)
        digest = hashlib.sha256()
        for piece in pieces:
            digest.update(piece)
        reader = ic.ChunkReader(pieces, expected_size=size, expected_sha256=digest.hexdigest())
        tracemalloc.start()
        ji.run_jsonl_import(
            reader,
            build_mapping(),
            verified_content_type=JSONL,
            dataset_purpose="training",
            expected_sha256=digest.hexdigest(),
            on_accepted=lambda _c: None,
        )
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return peak

    small, large = peak_for(1), peak_for(64)
    check(
        CID,
        f"peak for 64 MiB ({large}) is within 1.5x of the peak for 1 MiB ({small})",
        large <= 1.5 * small,
    )


def test_report_fields_slot_and_non_training_purpose() -> None:
    mapping = (
        '{"shape":"prompt_completion","prompt":{"key":"q"},"completion":{"key":"a"},'
        '"metadata":{"quality_label":{"key":"ql"}}}'
    )
    data = line("q", "a", ql=[1]) + b"\n" + line("q2", "a2") + b"\n"
    outcome = run(data, mapping=mapping)
    slots = [r.slot for r in outcome.report.rejections]
    check(
        CID,
        f"a rejected mapped value names its slot ({slots})",
        outcome.report.records_rejected == 1 and slots and all(slots),
    )
    for purpose in ("evaluation", "held_out"):
        out = ji.run_jsonl_import(
            make_reader(line("q", "a") + b"\n"),
            build_mapping(),
            verified_content_type=JSONL,
            dataset_purpose=purpose,
            expected_sha256=hashlib.sha256(line("q", "a") + b"\n").hexdigest(),
        )
        check(
            CID,
            f"dataset_purpose {purpose!r} is carried into the report, not hardcoded",
            out.report.dataset_purpose == purpose,
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
