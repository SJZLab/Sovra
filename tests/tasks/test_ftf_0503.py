#!/usr/bin/env python3
"""FTF-0503 -- JSON importer (round 2, rebuilt on FTF-0502-P1).

Runnable without any database or network: `workers.data.json_importer` is pure and
synchronous and sits on FTF-0502-P1's `ChunkReader` / `import_common`.

Acceptance ("Parse approved JSON conversation/container structures into canonical records
without loading unbounded file"): test_basic_*, test_container_*, test_ignored_*,
test_chunking_*, test_memory_*, test_time_*, test_differential_*, test_report_*.

Negative acceptance ("Deep nesting, malformed objects and unsupported structures fail with
actionable mapping errors"): test_structure_*, test_element_*, test_size_*, test_depth_*,
test_encoding_*, test_mapping_*, test_content_type_*, test_leak_*.

Run directly: python3 tests/tasks/test_ftf_0503.py
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
import time
import tracemalloc
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import import_test_support as support  # noqa: E402
from workers.data import import_common as ic  # noqa: E402
from workers.data import json_importer as jm  # noqa: E402
from workers.data.record_schema_validator import validate_record_batch  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0503"
JSON = "application/json"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# --- helpers -------------------------------------------------------------------------------

PC = '{"shape":"prompt_completion","prompt":{"key":"q"},"completion":{"key":"a"}}'
MESSAGES = (
    '{"shape":"messages","messages_pointer":"/messages","role_key":"role","content_key":"content",'
    '"role_map":{"user":"user","assistant":"assistant","system":"system","tool":"tool"},'
    '"on_unmapped_message_keys":"ignore"}'
)


def mapping_text(
    container: str = "/rows", ignored: tuple[str, ...] = ("_meta",), base: str = PC
) -> str:
    spec = json.loads(base)
    spec["json"] = {"container": container, "ignored_top_level_keys": list(ignored)}
    return json.dumps(spec)


def build_mapping(container: str = "/rows", ignored: tuple[str, ...] = ("_meta",)) -> Any:
    return ic.parse_mapping(mapping_text(container, ignored))


def rec(q: str = "hello", a: str = "world", **extra: object) -> dict[str, object]:
    return {"q": q, "a": a, **extra}


def dump(value: object, **kwargs: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, **kwargs).encode("utf-8")


def rows_doc(records: Sequence[object], **siblings: object) -> bytes:
    return dump({"rows": records, **siblings})


def array_doc(records: Sequence[object]) -> bytes:
    return dump(records)


def make_reader(
    data: bytes, chunk: int | None = None, limits: ic.ImportLimits = ic.DEFAULT_LIMITS
) -> ic.ChunkReader:
    chunks = [data] if chunk is None else support.split_chunks(data, chunk)
    return ic.ChunkReader(
        chunks,
        expected_size=len(data),
        expected_sha256=hashlib.sha256(data).hexdigest(),
        limits=limits,
    )


def run(
    data: bytes,
    *,
    chunk: int | None = None,
    limits: ic.ImportLimits = ic.DEFAULT_LIMITS,
    container: str = "/rows",
    ignored: tuple[str, ...] = ("_meta",),
    content_type: str = JSON,
    **kwargs: Any,
) -> jm.JsonImportOutcome:
    return jm.run_json_import(
        make_reader(data, chunk, limits),
        build_mapping(container, ignored),
        verified_content_type=content_type,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        limits=limits,
        **kwargs,
    )


def collect(data: bytes, **kwargs: Any) -> tuple[list[dict[str, object]], jm.JsonImportOutcome]:
    got: list[dict[str, object]] = []
    outcome = run(data, on_accepted=lambda candidate: got.append(dict(candidate)), **kwargs)
    return got, outcome


def codes(outcome: jm.JsonImportOutcome) -> list[tuple[str, str]]:
    return [(r.locator, r.code) for r in outcome.report.rejections]


def fatal(outcome: jm.JsonImportOutcome) -> tuple[str, str | None, str | None]:
    return outcome.report.state, outcome.report.failure_code, outcome.fatal_locator


def texts(candidate: object) -> list[str]:
    assert isinstance(candidate, dict)
    return [part["text"] for m in candidate["content"]["messages"] for part in m["content"]]


def scan(
    data: bytes,
    *,
    container: str = "/rows",
    ignored: tuple[str, ...] = ("_meta",),
    chunk: int | None = None,
    max_depth: int = 64,
    max_record_bytes: int = ic.MAX_CANONICAL_RECORD_BYTES,
) -> tuple[list[jm.ScannedElement], jm.JsonAborted | None]:
    """The raw scanner: its elements and the fatal that ended it (if any)."""
    chunks = [data] if chunk is None else support.split_chunks(data, chunk)
    scanner = jm.JsonContainerScanner(
        chunks,
        container=container,
        ignored_top_level_keys=frozenset(ignored),
        max_depth=max_depth,
        max_record_bytes=max_record_bytes,
    )
    out: list[jm.ScannedElement] = []
    try:
        for element in scanner.elements():
            out.append(element)
    except jm.JsonAborted as exc:
        return out, exc
    return out, None


def padded_record(size: int, q: str = "q") -> bytes:
    """A valid record object of exactly `size` bytes (the filler is the completion text)."""
    base = len(dump(rec(q, "")))
    assert base <= size
    return dump(rec(q, "x" * (size - base)))


# --- basics --------------------------------------------------------------------------------


def test_basic_import_rows_container() -> None:
    data = rows_doc([rec("q1", "a1"), rec("q2", "a2"), rec("q3", "a3")])
    got, outcome = collect(data)
    report = outcome.report
    check(CID, "completed", report.state == "completed" and report.failure_code is None)
    check(CID, "three accepted none rejected", len(got) == 3 and report.records_rejected == 0)
    check(
        CID,
        "texts in order",
        [texts(c) for c in got] == [["q1", "a1"], ["q2", "a2"], ["q3", "a3"]],
    )
    check(CID, "record_index is the element ordinal", [c["record_index"] for c in got] == [0, 1, 2])
    check(CID, "importer name", report.importer == "json" and report.importer_version == "1")
    check(CID, "source hash bound", report.source_sha256 == hashlib.sha256(data).hexdigest())
    check(CID, "no header, no schema hash", report.source_schema_sha256 is None)
    check(
        CID, "no fatal locator", outcome.fatal_locator is None and outcome.fatal_container is None
    )
    check(CID, "accepted candidates pass FTF-0508", validate_record_batch(got).issues == ())
    check(
        CID,
        "a quality label is never defaulted",
        all(c["quality_status"] == "pending" and c["pii_status"] == "unscanned" for c in got),
    )


def test_basic_root_array_container() -> None:
    data = array_doc([rec("one", "1"), rec("two", "2")])
    got, outcome = collect(data, container="$", ignored=())
    check(
        CID,
        "a root array imports each element",
        outcome.report.state == "completed"
        and [texts(c) for c in got] == [["one", "1"], ["two", "2"]],
    )


def test_basic_record_container() -> None:
    data = dump(rec("solo", "record"))
    got, outcome = collect(data, container="$record", ignored=())
    check(
        CID,
        "a root object is one record",
        outcome.report.state == "completed"
        and [texts(c) for c in got] == [["solo", "record"]]
        and outcome.report.records_seen == 1,
    )
    got, outcome = collect(
        dump(rec("big", "x" * 600)),
        container="$record",
        ignored=(),
        limits=ic.ImportLimits(max_record_bytes=500),
    )
    check(
        CID,
        "an over-size $record is record_too_large, not a failure",
        outcome.report.state == "completed"
        and codes(outcome) == [("record:1", "record_too_large")]
        and not got,
    )
    got, outcome = collect(b'[{"q":"a","a":"b"}]', container="$record", ignored=())
    check(
        CID,
        "$record over an array root is container_not_found",
        fatal(outcome) == ("failed", "container_not_found", "offset:0"),
    )


def test_basic_messages_mapping_and_content_preservation() -> None:
    values = {
        "arabic": "مرحبا بالعالم",
        "digits": "٠١٢٣٤٥٦٧٨٩",
        "leading_zero": "00123",
        "formula": "=SUM(A1:A9)",
        "quote": 'he said "hi"\\n',
        "ls_ps": "a b c",
        "zwj": "\U0001f468‍\U0001f469",
    }
    for label, value in values.items():
        for ensure_ascii in (False, True):
            data = json.dumps(
                {"rows": [rec(value, value + "!")]}, ensure_ascii=ensure_ascii
            ).encode()
            got, outcome = collect(data)
            check(
                CID,
                f"{label} (ensure_ascii={ensure_ascii}) is preserved exactly",
                outcome.report.records_accepted == 1 and texts(got[0]) == [value, value + "!"],
            )
    msgs = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
        ]
    }
    spec = mapping_text("/rows", ("_meta",), MESSAGES)
    data = dump({"rows": [msgs]})
    got2: list[dict[str, object]] = []
    outcome = jm.run_json_import(
        make_reader(data),
        ic.parse_mapping(spec),
        verified_content_type=JSON,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        on_accepted=lambda c: got2.append(dict(c)),
    )
    check(
        CID,
        "the messages shape maps through the container",
        outcome.report.state == "completed" and [texts(c) for c in got2] == [["hi", "yo"]],
    )


def test_basic_empty_container_is_completed_with_nothing() -> None:
    for label, data, container, ignored in (
        ("rows []", b'{"rows":[]}', "/rows", ("_meta",)),
        ("rows [ ws ]", b'{ "rows" : [ \n\t\r ] }', "/rows", ("_meta",)),
        ("root []", b"[]", "$", ()),
        ("root [ ws ]", b" [ \r\n ] ", "$", ()),
        ("rows empty + ignored", b'{"_meta":{"n":0},"rows":[]}', "/rows", ("_meta",)),
    ):
        for chunk in (None, 1, 3):
            got, outcome = collect(data, container=container, ignored=ignored, chunk=chunk)
            report = outcome.report
            check(
                CID,
                f"{label} / chunk {chunk}: completed with 0 records, no invented record",
                report.state == "completed"
                and report.failure_code is None
                and report.records_seen == 0
                and not got
                and report.rejections == (),
            )


def test_basic_candidates_are_deterministic() -> None:
    data = rows_doc([rec("a", "1"), rec("b", "2")])
    first, _ = collect(data)
    second, _ = collect(data, chunk=3)
    check(
        CID,
        "the same bytes give the same candidates whatever the chunking",
        first == second and len({c["normalized_sha256"] for c in first}) == 2,
    )


# --- container x shape table ---------------------------------------------------------------


def test_container_shape_table() -> None:
    r1 = dump(rec("x", "y"))
    cases: list[tuple[str, bytes, str, tuple[str, ...], tuple[str, str | None, str | None]]] = [
        (
            "mapped key + ignored sibling",
            b'{"_meta":{"a":[1,2]},"rows":[' + r1 + b"]}",
            "/rows",
            ("_meta",),
            ("completed", None, None),
        ),
        (
            "unmapped sibling before",
            b'{"extra":1,"rows":[' + r1 + b"]}",
            "/rows",
            ("_meta",),
            ("failed", "unmapped_top_level_key", "offset:1"),
        ),
        (
            "unmapped sibling after the container",
            b'{"rows":[' + r1 + b'],"extra":1}',
            "/rows",
            ("_meta",),
            ("failed", "unmapped_top_level_key", f"offset:{11 + len(r1)}"),
        ),
        (
            "duplicate ignored root key",
            b'{"_meta":1,"_meta":2,"rows":[]}',
            "/rows",
            ("_meta",),
            ("failed", "duplicate_root_key", "offset:11"),
        ),
        (
            "duplicate container root key",
            b'{"rows":[],"rows":[]}',
            "/rows",
            ("_meta",),
            ("failed", "duplicate_root_key", "offset:11"),
        ),
        (
            "duplicate written with an escape",
            b'{"rows":[],"r\\u006fws":[]}',
            "/rows",
            ("_meta",),
            ("failed", "duplicate_root_key", "offset:11"),
        ),
        (
            "container missing",
            b'{"_meta":1}',
            "/rows",
            ("_meta",),
            ("failed", "container_not_found", "offset:10"),
        ),
        (
            "empty root object",
            b"{}",
            "/rows",
            ("_meta",),
            ("failed", "container_not_found", "offset:1"),
        ),
        (
            "container is an object",
            b'{"rows":{"a":1}}',
            "/rows",
            ("_meta",),
            ("failed", "container_not_array", "offset:8"),
        ),
        (
            "container is a string",
            b'{"rows":"x"}',
            "/rows",
            ("_meta",),
            ("failed", "container_not_array", "offset:8"),
        ),
        (
            "container is null",
            b'{"rows":null}',
            "/rows",
            ("_meta",),
            ("failed", "container_not_array", "offset:8"),
        ),
        (
            "$ over an object root",
            b'{"rows":[]}',
            "$",
            (),
            ("failed", "container_not_array", "offset:0"),
        ),
        (
            "/rows over an array root",
            b"[]",
            "/rows",
            ("_meta",),
            ("failed", "container_not_found", "offset:0"),
        ),
        (
            "a container key in a different case is not the container",
            b'{"Rows":[]}',
            "/rows",
            ("_meta",),
            ("failed", "unmapped_top_level_key", "offset:1"),
        ),
        (
            "an ignored key in a different case is not ignored",
            b'{"_META":1,"rows":[]}',
            "/rows",
            ("_meta",),
            ("failed", "unmapped_top_level_key", "offset:1"),
        ),
        (
            "a scalar root",
            b"42",
            "/rows",
            ("_meta",),
            ("failed", "json_structure_invalid", "offset:0"),
        ),
        (
            "a string root",
            b'"rows"',
            "$",
            (),
            ("failed", "json_structure_invalid", "offset:0"),
        ),
        (
            "an empty file",
            b"",
            "$",
            (),
            ("failed", "json_structure_invalid", "offset:0"),
        ),
        (
            "a whitespace-only file",
            b" \n\t\r ",
            "$",
            (),
            ("failed", "json_structure_invalid", "offset:5"),
        ),
        (
            "$record over a number",
            b"42",
            "$record",
            (),
            ("failed", "json_structure_invalid", "offset:0"),
        ),
        (
            "$record over a string",
            b'"rows"',
            "$record",
            (),
            ("failed", "json_structure_invalid", "offset:0"),
        ),
        (
            "$record over null",
            b" null",
            "$record",
            (),
            ("failed", "json_structure_invalid", "offset:1"),
        ),
        (
            "$record over trailing data",
            b'{"q":"a","a":"b"} 1',
            "$record",
            (),
            ("failed", "json_structure_invalid", "offset:18"),
        ),
        (
            "/rows over null",
            b"null",
            "/rows",
            ("_meta",),
            ("failed", "json_structure_invalid", "offset:0"),
        ),
        (
            "an escaped container key",
            b'{"r\\u006fws":[' + r1 + b"]}",
            "/rows",
            ("_meta",),
            ("completed", None, None),
        ),
    ]
    for label, data, container, ignored, expected in cases:
        for chunk in (None, 1, 4):
            outcome = run(data, container=container, ignored=ignored, chunk=chunk)
            check(
                CID,
                f"{label} / chunk {chunk}: {expected} (got {fatal(outcome)})",
                fatal(outcome) == expected,
            )
    outcome = run(b'{"rows":[]}', container="/rows")
    check(CID, "the fatal names the mapping's own pointer", outcome.fatal_container is None)
    outcome = run(b"{}", container="/rows")
    check(
        CID,
        "a fatal carries the mapping's container pointer, not document content",
        outcome.fatal_container == "/rows",
    )
    outcome = run(b"[1", container="$", ignored=())
    check(CID, "and the $ pointer for a root array", outcome.fatal_container == "$")


def test_container_key_matching_is_exact_and_unescaped() -> None:
    data = b'{"a/b":[' + dump(rec()) + b"]}"
    got, outcome = collect(data, container="/a~1b", ignored=())
    check(
        CID, "~1 in the pointer is a slash in the key", len(got) == 1 and not outcome.fatal_locator
    )
    data = b'{"a~b":[' + dump(rec()) + b"]}"
    got, outcome = collect(data, container="/a~0b", ignored=())
    check(
        CID, "~0 in the pointer is a tilde in the key", len(got) == 1 and not outcome.fatal_locator
    )
    got, outcome = collect(b'{"\xc3\xa9":[' + dump(rec()) + b"]}", container="/é", ignored=())
    check(CID, "a non-ASCII key matches exactly", len(got) == 1)
    nfd = b'{"e\xcc\x81":[' + dump(rec()) + b"]}"
    outcome = run(nfd, container="/é", ignored=())
    check(
        CID,
        "no Unicode normalisation of the key",
        fatal(outcome)[:2] == ("failed", "unmapped_top_level_key"),
    )


def test_container_numeric_and_odd_keys() -> None:
    got, outcome = collect(b'{"0":[' + dump(rec()) + b"]}", container="/0", ignored=())
    check(CID, "a numeric key names an object key, not an index", len(got) == 1)
    got, outcome = collect(b'{"":[' + dump(rec()) + b"]}", container="/ ", ignored=())
    check(
        CID,
        "a key that differs only by a space is a different key",
        fatal(outcome)[:2] == ("failed", "unmapped_top_level_key"),
    )


# --- ignored values ------------------------------------------------------------------------


def test_ignored_values_are_scanned_and_not_retained() -> None:
    nasty = {"a": ['}]"', "\\", "\\\\", '"\\"', [[[[]]]], {"b": {"c": None}}], "k": "}{]["}
    for placement in ("before", "after", "both"):
        parts = []
        if placement in ("before", "both"):
            parts.append(b'"_meta":' + dump(nasty))
        parts.append(b'"rows":[' + dump(rec("kept", "yes")) + b"]")
        if placement == "both":
            parts.append(b'"_meta2":' + dump(nasty))
        elif placement == "after":
            parts.append(b'"_meta":' + dump(nasty))
        data = b"{" + b",".join(parts) + b"}"
        ignored = ("_meta", "_meta2")
        for chunk in (None, 1, 5):
            got, outcome = collect(data, ignored=ignored, chunk=chunk)
            check(
                CID,
                f"ignored sibling {placement} / chunk {chunk}: only the container imports",
                outcome.report.state == "completed"
                and [texts(c) for c in got] == [["kept", "yes"]],
            )
    elements, aborted = scan(
        b'{"_meta":' + dump(nasty) + b',"rows":[{"q":"z","a":"w"}]}', ignored=("_meta",)
    )
    check(
        CID,
        "an ignored value yields no element at all",
        aborted is None and [e.ordinal for e in elements] == [1],
    )


def test_ignored_values_are_not_held_in_memory() -> None:
    mib = 1024 * 1024
    body = b'{"_meta":"' + b"m" * (3 * mib // 2) + b'","rows":[' + dump(rec("after", "ok")) + b"]}"
    reader = make_reader(body, 4096)
    mapping = build_mapping()
    digest = hashlib.sha256(body).hexdigest()
    got: list[object] = []
    tracemalloc.start()
    outcome = jm.run_json_import(
        reader,
        mapping,
        verified_content_type=JSON,
        dataset_purpose="training",
        expected_sha256=digest,
        on_accepted=got.append,
    )
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    check(
        CID,
        f"a 1.5 MiB ignored value is scanned and the record after it imports ({fatal(outcome)})",
        outcome.report.state == "completed" and len(got) == 1,
    )
    check(CID, f"and it is not retained (peak {peak} bytes)", peak < 256 * 1024)


def test_ignored_value_over_the_record_limit_is_a_limit_failure() -> None:
    limits = ic.ImportLimits(max_record_bytes=200)
    big = b'{"_meta":"' + b"x" * 300 + b'","rows":[]}'
    outcome = run(big, limits=limits)
    check(
        CID,
        f"an ignored value over max_record_bytes ends the import ({fatal(outcome)})",
        fatal(outcome) == ("failed", "limit_exceeded", "offset:9"),
    )
    exact = b'{"_meta":"' + b"x" * 190 + b'","rows":[]}'
    check(
        CID,
        "an ignored value that fits is fine",
        run(exact, limits=limits).report.state == "completed",
    )
    at_limit = b'{"_meta":' + b'"' + b"x" * 198 + b'"' + b',"rows":[]}'
    over_by_one = b'{"_meta":' + b'"' + b"x" * 199 + b'"' + b',"rows":[]}'
    check(
        CID,
        "exactly max_record_bytes is allowed",
        run(at_limit, limits=limits).report.state == "completed",
    )
    check(
        CID,
        "one more byte is not",
        fatal(run(over_by_one, limits=limits))[1] == "limit_exceeded",
    )
    deep = b'{"_meta":' + b"[" * 65 + b"]" * 65 + b',"rows":[]}'
    outcome = run(deep)
    check(
        CID,
        "an ignored value obeys the depth limit (root + 64 = 65 > 64)",
        fatal(outcome) == ("failed", "limit_exceeded", "offset:" + str(9 + 63)),
    )
    ok = b'{"_meta":' + b"[" * 63 + b"]" * 63 + b',"rows":[]}'
    check(
        CID, "63 levels under the root is the deepest allowed", run(ok).report.state == "completed"
    )
    bad_close = b'{"_meta":[}],"rows":[]}'
    check(
        CID,
        "mismatched brackets inside an ignored value are a structure error",
        fatal(run(bad_close)) == ("failed", "json_structure_invalid", "offset:10"),
    )


# --- structure errors are fatal, with the byte offset --------------------------------------


def test_structure_errors_table() -> None:
    r = dump(rec())
    n = len(r)
    cases: list[tuple[str, bytes, str, str]] = [
        ("trailing comma", b"[" + r + b",]", "$", f"offset:{n + 2}"),
        ("leading comma", b"[," + r + b"]", "$", "offset:1"),
        ("double comma", b"[" + r + b",," + r + b"]", "$", f"offset:{n + 2}"),
        ("missing comma", b"[" + r + b" " + r + b"]", "$", f"offset:{n + 2}"),
        ("bad token between elements", b"[" + r + b" x " + r + b"]", "$", f"offset:{n + 2}"),
        ("colon where a value should be", b"[:]", "$", "offset:1"),
        ("close brace in an array", b"[}", "$", "offset:1"),
        ("close bracket in an element", b"[{]", "$", "offset:2"),
        ("unclosed element", b"[" + r[:-1], "$", f"offset:{n}"),
        ("unclosed array", b"[" + r, "$", f"offset:{n + 1}"),
        ("unclosed string", b'["abc', "$", "offset:5"),
        ("trailing data", b"[" + r + b"] x", "$", f"offset:{n + 3}"),
        ("two root values", b"[" + r + b"][]", "$", f"offset:{n + 2}"),
        ("NUL after the root", b"[1,2]\x00", "$", "offset:5"),
        ("vertical tab is not JSON whitespace", b"[" + r + b"\x0b]", "$", f"offset:{n + 1}"),
        ("form feed is not JSON whitespace", b"[" + r + b"\x0c]", "$", f"offset:{n + 1}"),
        ("NBSP is not JSON whitespace", b"[" + r + b"\xc2\xa0]", "$", f"offset:{n + 1}"),
        ("EM SPACE is not JSON whitespace", b"[" + r + b"\xe2\x80\x83]", "$", f"offset:{n + 1}"),
        ("key is not a string", b"{rows:[]}", "/rows", "offset:1"),
        ("single-quoted key", b"{'rows':[]}", "/rows", "offset:1"),
        ("missing colon", b'{"rows" []}', "/rows", "offset:8"),
        ("trailing comma in the root object", b'{"rows":[],}', "/rows", "offset:11"),
        ("leading comma in the root object", b'{,"rows":[]}', "/rows", "offset:1"),
        ("missing comma between root keys", b'{"rows":[] "_meta":1}', "/rows", "offset:11"),
        ("unclosed root object", b'{"rows":[]', "/rows", "offset:10"),
        ("root object closed with a bracket", b'{"rows":[]]', "/rows", "offset:10"),
        ("array closed with a brace", b'{"rows":[}', "/rows", "offset:9"),
        ("truncated after the colon", b'{"rows":', "/rows", "offset:8"),
        ("truncated inside a key", b'{"ro', "/rows", "offset:4"),
        ("a value-less key", b'{"_meta":}', "/rows", "offset:9"),
    ]
    for label, data, container, locator in cases:
        ignored = ("_meta",) if container != "$" else ()
        for chunk in (None, 1, 3):
            outcome = run(data, container=container, ignored=ignored, chunk=chunk)
            state, code, where = fatal(outcome)
            check(
                CID,
                f"{label} / chunk {chunk}: failed/json_structure_invalid at {locator} "
                f"(got {state}/{code}/{where})",
                (state, code, where) == ("failed", "json_structure_invalid", locator),
            )
            check(
                CID,
                f"{label} / chunk {chunk}: names the mapping's pointer",
                outcome.fatal_container == container,
            )


def test_structure_error_keeps_the_rows_before_it() -> None:
    data = b"[" + dump(rec("one", "1")) + b"," + dump(rec("two", "2")) + b",]"
    got, outcome = collect(data, container="$", ignored=())
    check(
        CID,
        "rows read before the fault are reported, and the import is failed (never completed)",
        outcome.report.state == "failed" and outcome.report.records_accepted == 2 and len(got) == 2,
    )


def test_structure_mismatched_nesting_is_caught_by_kind() -> None:
    for label, data in (
        ("array closed by brace", b'[{"q":[}]}]'),
        ("object closed by bracket", b'[{"q":{]}]'),
        ("extra closer", b"[{}]]"),
        ("closer first", b"]"),
    ):
        outcome = run(data, container="$", ignored=())
        check(
            CID,
            f"{label} is failed/json_structure_invalid ({fatal(outcome)})",
            fatal(outcome)[:2] == ("failed", "json_structure_invalid"),
        )


def test_structure_whitespace_kinds_between_tokens() -> None:
    r = dump(rec("w", "s"))
    for ws in (b" ", b"\t", b"\n", b"\r", b" \r\n\t "):
        data = (
            ws
            + b"{"
            + ws
            + b'"rows"'
            + ws
            + b":"
            + ws
            + b"["
            + ws
            + r
            + ws
            + b","
            + ws
            + r
            + ws
            + b"]"
            + ws
            + b"}"
            + ws
        )
        for chunk in (None, 1, 2):
            got, outcome = collect(data, ignored=(), chunk=chunk)
            check(
                CID,
                f"whitespace {ws!r} / chunk {chunk} between every token",
                outcome.report.state == "completed" and len(got) == 2,
            )


# --- elements: row rejections with the ordinal ---------------------------------------------


def test_element_rejections_are_rows_and_the_import_continues() -> None:
    good = dump(rec("ok", "fine"))
    elements = [
        (b'{"q":"a","q":"b","a":"c"}', "duplicate_key"),
        (b'{"q":"a","a":NaN}', "non_finite_number"),
        (b'{"q":"a","a":1e999}', "non_finite_number"),
        (b'{"q":"a","a":"b","x":' + b"1" * 4301 + b"}", "number_out_of_range"),
        (b'{"q":"\\ud800","a":"b"}', "invalid_unicode"),
        (b'{"q":"a","a":"b\xff"}', "invalid_utf8"),
        (b'{"q":"a","a":}', "invalid_json"),
        (b"tru", "invalid_json"),
        (b"[1,2]", "record_not_object"),
        (b'"text"', "record_not_object"),
        (b"42", "record_not_object"),
        (b"null", "record_not_object"),
        (b'{"a":"b"}', "required_field_missing"),
        (b'{"q":5,"a":"b"}', "mapping_type_mismatch"),
    ]
    docs = [good]
    for element, _code in elements:
        docs.append(element)
        docs.append(good)
    data = b'{"rows":[' + b",".join(docs) + b"]}"
    for chunk in (None, 1, 7):
        got, outcome = collect(data, chunk=chunk)
        report = outcome.report
        want = [(f"record:{2 + 2 * i}", code) for i, (_e, code) in enumerate(elements)]
        check(
            CID,
            f"every bad element is its own coded row / chunk {chunk} (got {codes(outcome)})",
            codes(outcome) == want,
        )
        check(
            CID,
            f"the good elements still import / chunk {chunk}",
            report.state == "completed"
            and len(got) == len(elements) + 1
            and report.records_seen == report.records_accepted + report.records_rejected
            and report.records_seen == 2 * len(elements) + 1,
        )
    check(
        CID,
        "record_index counts rejected elements too",
        [c["record_index"] for c in collect(data)[0]]
        == [0] + [2 + 2 * i for i in range(len(elements))],
    )


def test_element_slot_is_named_when_a_field_is_wrong() -> None:
    data = rows_doc([{"q": 5, "a": "b"}, {"q": "a"}])
    outcome = run(data)
    check(
        CID,
        "a wrong slot is named from the slot grammar, never from the data",
        [(r.locator, r.code, r.slot) for r in outcome.report.rejections]
        == [
            ("record:1", "mapping_type_mismatch", "prompt"),
            ("record:2", "required_field_missing", "completion"),
        ],
    )


# --- element size --------------------------------------------------------------------------


def test_size_element_limit_boundaries() -> None:
    limit = 4096
    limits = ic.ImportLimits(max_record_bytes=limit)
    for size, accepted in ((limit - 1, True), (limit, True), (limit + 1, False)):
        element = padded_record(size)
        for chunk in (1, 3, 100, None):
            data = (
                b'{"rows":['
                + dump(rec("a", "1"))
                + b","
                + element
                + b","
                + dump(rec("z", "9"))
                + b"]}"
            )
            got, outcome = collect(data, limits=limits, chunk=chunk)
            report = outcome.report
            if accepted:
                ok = report.state == "completed" and len(got) == 3 and not report.rejections
            else:
                ok = (
                    report.state == "completed"
                    and codes(outcome) == [("record:2", "record_too_large")]
                    and [texts(c) for c in got] == [["a", "1"], ["z", "9"]]
                )
            check(CID, f"element of {size} bytes (limit {limit}) / chunk {chunk}", ok)


def test_size_element_size_excludes_surrounding_whitespace() -> None:
    limits = ic.ImportLimits(max_record_bytes=64)
    body = dump(rec("a", "b"))
    check(CID, "fixture is under the limit", len(body) <= 64)
    data = b'{"rows":[' + b" " * 500 + body + b" " * 500 + b"," + b"\n" * 500 + body + b"]}"
    got, outcome = collect(data, limits=limits)
    check(
        CID,
        "whitespace around an element is not part of it",
        outcome.report.state == "completed" and len(got) == 2,
    )


def test_size_oversize_element_is_skipped_by_scanning() -> None:
    limits = ic.ImportLimits(max_record_bytes=1000)
    nasty = b'{"q":"' + b'}]\\"\\\\' * 400 + b'","a":"x"}'
    data = b'{"rows":[' + nasty + b"," + dump(rec("after", "ok")) + b"]}"
    got, outcome = collect(data, limits=limits, chunk=11)
    check(
        CID,
        "brackets and escaped quotes inside an oversize element do not end it early",
        codes(outcome) == [("record:1", "record_too_large")]
        and [texts(c) for c in got] == [["after", "ok"]],
    )
    elements, aborted = scan(data, max_record_bytes=1000)
    check(
        CID,
        "the scanner hands over no bytes for an oversize element",
        aborted is None and elements[0].data is None and elements[1].data is not None,
    )


def test_size_oversize_element_does_not_end_the_import() -> None:
    limits = ic.ImportLimits(max_record_bytes=300)
    data = array_doc([rec("a", "x" * 500), rec("b", "y"), rec("c", "z" * 500), rec("d", "w")])
    got, outcome = collect(data, container="$", ignored=(), limits=limits)
    check(
        CID,
        "two oversize elements are two rows; the others import",
        outcome.report.state == "completed"
        and codes(outcome) == [("record:1", "record_too_large"), ("record:3", "record_too_large")]
        and [texts(c)[0] for c in got] == ["b", "d"],
    )


def test_size_canonical_size_over_the_validator_limit() -> None:
    # Fits max_record_bytes as written, but its canonical (ASCII-escaped) form does not.
    count = ic.MAX_CANONICAL_RECORD_BYTES // 6 + 50_000
    element = dump(rec("é" * count, "a"))
    check(
        CID,
        "the written element is under the 2 MiB limit",
        len(element) <= ic.MAX_CANONICAL_RECORD_BYTES,
    )
    data = b'{"rows":[' + element + b"," + dump(rec("next", "ok")) + b"]}"
    got, outcome = collect(data, chunk=65536)
    check(
        CID,
        f"canonical size over the limit is record_too_large ({codes(outcome)})",
        codes(outcome) == [("record:1", "record_too_large")] and len(got) == 1,
    )


# --- depth ---------------------------------------------------------------------------------


def nested(levels: int, leaf: bytes = b"1") -> bytes:
    return b"[" * levels + leaf + b"]" * levels


def test_depth_inside_the_container() -> None:
    # `rows` object (1) + `rows` array (2) + element: element levels + 2 must be <= 64.
    for levels, ok in ((61, True), (62, True), (63, False), (64, False)):
        element = b'{"q":"a","a":"b","x":' + nested(levels - 1) + b"}"
        data = b'{"rows":[' + element + b"]}"
        outcome = run(data)
        want_ok = levels <= 62
        check(CID, f"fixture claim for {levels} levels", ok == want_ok)
        if want_ok:
            check(
                CID,
                f"element nested {levels} levels (document depth {levels + 2}) imports",
                outcome.report.state == "completed" and outcome.report.records_accepted == 1,
            )
        else:
            check(
                CID,
                f"element nested {levels} levels (depth {levels + 2}) is failed/limit_exceeded "
                f"({fatal(outcome)})",
                fatal(outcome)[:2] == ("failed", "limit_exceeded"),
            )
    for container, prefix, suffix, base in (
        ("$", b"[", b"]", 1),
        ("/rows", b'{"rows":[', b"]}", 2),
        ("$record", b"", b"", 0),
    ):
        for total in (63, 64, 65, 66):
            count = total - base - 1  # brackets inside the element object
            data = prefix + b'{"q":"a","a":"b","x":' + nested(count) + b"}" + suffix
            outcome = run(data, container=container, ignored=())
            if total <= 64:
                check(
                    CID,
                    f"{container}: document depth {total} imports",
                    outcome.report.state == "completed" and outcome.report.records_accepted == 1,
                )
            else:
                first_bracket = data.index(b'"x":') + 4
                offending = first_bracket + (64 - base - 1)
                check(
                    CID,
                    f"{container}: document depth {total} is failed/limit_exceeded at the 65th "
                    f"bracket ({fatal(outcome)})",
                    fatal(outcome) == ("failed", "limit_exceeded", f"offset:{offending}"),
                )


def test_depth_off_by_one_with_lowered_limit() -> None:
    limits = ic.ImportLimits(max_depth=5)
    ok = b"[" + b'{"q":"a","a":"b","x":[[[]]]}' + b"]"  # [ { [ [ [  => depth 5
    bad = b"[" + b'{"q":"a","a":"b","x":[[[[]]]]}' + b"]"  # depth 6
    check(
        CID,
        "depth equal to the lowered limit is fine",
        run(ok, container="$", ignored=(), limits=limits).report.state == "completed",
    )
    outcome = run(bad, container="$", ignored=(), limits=limits)
    check(
        CID,
        f"one level more is failed/limit_exceeded ({fatal(outcome)})",
        fatal(outcome)[:2] == ("failed", "limit_exceeded"),
    )


def test_depth_hostile_documents_do_not_crash() -> None:
    for label, data, container in (
        ("100,000-deep array element", b"[" + b"[" * 100_000 + b"]" * 100_000 + b"]", "$"),
        (
            "100,000-deep object element",
            b'{"rows":[' + b'{"a":' * 100_000 + b"1" + b"}" * 100_000 + b"]}",
            "/rows",
        ),
        ("100,000 open brackets, never closed", b"[" * 100_000, "$"),
        ("100,000-deep ignored value", b'{"_meta":' + b"[" * 100_000 + b"]}", "/rows"),
    ):
        for chunk in (None, 997):
            outcome = run(data, container=container, chunk=chunk)
            check(
                CID,
                f"{label} / chunk {chunk}: failed/limit_exceeded, no crash ({fatal(outcome)})",
                outcome.report.state == "failed"
                and outcome.report.failure_code == "limit_exceeded",
            )


# --- encoding ------------------------------------------------------------------------------


def test_encoding_bom_and_wide_encodings() -> None:
    body = rows_doc([rec("bom", "ok")])
    for chunk in (None, 1, 2, 3, 4):
        got, outcome = collect(b"\xef\xbb\xbf" + body, chunk=chunk)
        check(
            CID,
            f"a BOM at byte 0 is accepted / chunk {chunk}",
            outcome.report.state == "completed" and [texts(c) for c in got] == [["bom", "ok"]],
        )
    outcome = run(b"\xef\xbb\xbf" + b'{"rows":[,]}')
    check(
        CID,
        f"offsets count the BOM bytes ({fatal(outcome)})",
        fatal(outcome) == ("failed", "json_structure_invalid", "offset:12"),
    )
    outcome = run(b" \xef\xbb\xbf" + body)
    check(
        CID,
        "a BOM anywhere but byte 0 is a structure error",
        fatal(outcome) == ("failed", "json_structure_invalid", "offset:1"),
    )
    for label, data in (
        ("UTF-16 LE", b"\xff\xfe" + body.decode().encode("utf-16-le")),
        ("UTF-16 BE", b"\xfe\xff" + body.decode().encode("utf-16-be")),
        ("UTF-16 LE no BOM", body.decode().encode("utf-16-le")),
        ("UTF-16 BE no BOM", body.decode().encode("utf-16-be")),
        ("UTF-32 LE", body.decode().encode("utf-32")),
        ("UTF-32 BE no BOM", body.decode().encode("utf-32-be")),
    ):
        for chunk in (None, 1, 3):
            outcome = run(data, chunk=chunk)
            check(
                CID,
                f"{label} / chunk {chunk}: failed/unsupported_encoding at offset:0",
                fatal(outcome) == ("failed", "unsupported_encoding", "offset:0")
                and outcome.report.records_seen == 0,
            )
    for short in (b"[]", b"{}", b"[1]"):
        outcome = run(short, container="$", ignored=())
        check(
            CID,
            f"a source under four bytes ({short!r}) is read, not mistaken for a wide encoding",
            outcome.report.failure_code != "unsupported_encoding",
        )
    outcome = run(b"\xff\xfe")
    check(
        CID,
        "a lone UTF-16 BOM is unsupported_encoding",
        fatal(outcome) == ("failed", "unsupported_encoding", "offset:0"),
    )


def test_encoding_invalid_utf8_is_per_element_or_structural() -> None:
    data = b'{"rows":[{"q":"a\xc3","a":"b"},' + dump(rec("next", "ok")) + b"]}"
    got, outcome = collect(data)
    check(
        CID,
        "a bad byte inside an element's string is that element's invalid_utf8",
        codes(outcome) == [("record:1", "invalid_utf8")] and len(got) == 1,
    )
    outcome = run(b'{"rows":[]\xff}')
    check(
        CID,
        "a bad byte between tokens is a structure error",
        fatal(outcome) == ("failed", "json_structure_invalid", "offset:10"),
    )


# --- content type, mapping -----------------------------------------------------------------


def test_content_type_only_application_json() -> None:
    data = rows_doc([rec()])
    for ct in (
        "application/jsonl",
        "application/x-ndjson",
        "text/csv",
        "application/json; charset=utf-8",
        "Application/JSON",
        "application/json ",
        "text/json",
        "",
        None,
        b"application/json",
    ):
        calls: list[int] = []

        def source(calls: list[int] = calls) -> Iterator[bytes]:
            calls.append(1)
            yield data

        reader = ic.ChunkReader(
            source(), expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest()
        )
        outcome = jm.run_json_import(
            reader,
            build_mapping(),
            verified_content_type=ct,  # type: ignore[arg-type]
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )
        check(
            CID,
            f"{ct!r}: failed/content_type_not_supported_by_importer before a byte is read",
            outcome.report.state == "failed"
            and outcome.report.failure_code == "content_type_not_supported_by_importer"
            and reader.bytes_read == 0
            and not calls,
        )
    check(CID, "application/json is the accepted type", jm.JSON_CONTENT_TYPES == frozenset({JSON}))


def test_mapping_must_describe_a_json_source() -> None:
    def refused(text: str, reason: str | None = None) -> bool:
        data = b"[]"
        try:
            jm.json_events(
                make_reader(data),
                ic.parse_mapping(text),
                verified_content_type=JSON,
                dataset_sha256="0" * 64,
            )
        except ic.MappingInvalidError as exc:
            return reason is None or exc.reason == reason
        return False

    def unsupported(container: str) -> bool:
        try:
            jm.json_events(
                make_reader(b"[]"),
                ic.parse_mapping(mapping_text(container, ())),
                verified_content_type=JSON,
                dataset_sha256="0" * 64,
            )
        except ic.MappingUnsupportedError:
            return True
        return False

    check(
        CID, "a mapping with no json block is mapping_invalid/bad_shape", refused(PC, "bad_shape")
    )
    spec = json.loads(mapping_text())
    spec["csv"] = {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0}
    check(CID, "a csv block is refused", refused(json.dumps(spec), "bad_shape"))
    spec = json.loads(mapping_text())
    spec["xlsx"] = {"sheet_name": "s", "header_row": 0}
    check(CID, "an xlsx block is refused", refused(json.dumps(spec), "bad_shape"))
    spec = json.loads(mapping_text())
    spec["prompt"] = {"header": "q"}
    check(CID, "a header slot is refused", refused(json.dumps(spec), "bad_slot"))
    spec = json.loads(mapping_text())
    spec["prompt"] = {"index": 0}
    check(CID, "an index slot is refused", refused(json.dumps(spec), "bad_slot"))
    spec = json.loads(mapping_text())
    spec["group_key"] = {"letters": "A"}
    check(CID, "a letters group_key slot is refused", refused(json.dumps(spec), "bad_slot"))
    spec = json.loads(mapping_text())
    spec["metadata"] = {"language": {"index": 3}}
    check(CID, "a metadata slot of another kind is refused", refused(json.dumps(spec), "bad_slot"))
    for container in ("/a/b", "", "/rows/0", "/a~1b/c"):
        check(CID, f"container {container!r} is mapping_unsupported", unsupported(container))
    for container in ("$", "$record", "/rows", "/a~1b"):
        check(CID, f"container {container!r} is supported", not unsupported(container))
    try:
        ic.parse_mapping(mapping_text("$x", ()))
        accepted_odd = True
    except ic.MappingInvalidError:
        accepted_odd = False
    check(CID, "$x is not a container spelling", not accepted_odd)
    for container in ("$", "$record"):
        check(
            CID,
            f"P1 accepts {container!r} as a json.container",
            dict(ic.parse_mapping(mapping_text(container, ())).document["json"]).get("container")  # type: ignore[call-overload]
            == container,
        )


def test_mapping_source_type_and_construction_errors() -> None:
    try:
        jm.json_events(
            make_reader(b"[]"),
            build_mapping(),
            verified_content_type=JSON,
            dataset_sha256="0" * 64,
            source_type="nope",
        )
        raised = False
    except ValueError:
        raised = True
    check(CID, "an unsupported source_type is refused up front", raised)


# --- chunking ------------------------------------------------------------------------------


def test_chunking_is_irrelevant() -> None:
    rng = random.Random(503)
    records: list[object] = []
    for i in range(40):
        kind = rng.randrange(7)
        if kind == 0:
            records.append(rec(f"q{i}", "a" * rng.randrange(1, 40)))
        elif kind == 1:
            records.append([i])
        elif kind == 2:
            records.append("text" + "\\" * rng.randrange(3))
        elif kind == 3:
            records.append(rec("é" * rng.randrange(1, 5), "ا" * 3))
        elif kind == 4:
            records.append({"a": "no q"})
        elif kind == 5:
            records.append(rec("q", "a", extra={"deep": [[{"x": '}]"'}]]}))
        else:
            records.append(None)
    data = json.dumps(
        {"_meta": {"k": "}]"}, "rows": records}, ensure_ascii=rng.random() < 0.5
    ).encode()
    base, base_out = collect(data)
    check(CID, "reference run completes", base_out.report.state == "completed")
    for size in (1, 2, 3, 5, 7, 13, 64, 1000, len(data)):
        got, outcome = collect(data, chunk=size)
        check(
            CID,
            f"chunk size {size}: same records, same rejections, same report",
            got == base and codes(outcome) == codes(base_out) and outcome.report == base_out.report,
        )
    for blank in range(3):
        pieces = [data[: len(data) // 2], b"", data[len(data) // 2 :]]
        pieces.insert(blank, b"")
        reader = ic.ChunkReader(
            pieces, expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest()
        )
        outcome = jm.run_json_import(
            reader,
            build_mapping(),
            verified_content_type=JSON,
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )
        check(CID, "empty chunks in the stream are harmless", outcome.report == base_out.report)


# --- the scanner against json.loads --------------------------------------------------------


def _ws(rng: random.Random) -> str:
    return rng.choice(["", "", "", " ", "\n", "\t", "\r\n", "  \n\t "])


def _string(rng: random.Random) -> str:
    pieces = ['"', "\\", "[", "]", "{", "}", ",", ":", "é", "ا", "\U0001f600", " ", " ", "a", "0"]
    text = "".join(rng.choice(pieces) for _ in range(rng.randrange(0, 8)))
    return json.dumps(text, ensure_ascii=rng.random() < 0.5)


def _serialize(value: object, rng: random.Random, depth: int = 0) -> str:
    if isinstance(value, dict):
        items = [
            f"{_ws(rng)}{json.dumps(k)}{_ws(rng)}:{_ws(rng)}"
            f"{_serialize(v, rng, depth + 1)}{_ws(rng)}"
            for k, v in value.items()
        ]
        return "{" + ",".join(items) + "}"
    if isinstance(value, list):
        items = [f"{_ws(rng)}{_serialize(v, rng, depth + 1)}{_ws(rng)}" for v in value]
        return "[" + ",".join(items) + "]"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=rng.random() < 0.5)
    return json.dumps(value)


def _random_value(rng: random.Random, depth: int) -> object:
    kinds = ["str", "int", "float", "bool", "null"]
    if depth < 5:
        kinds += ["list", "dict", "dict"]
    kind = rng.choice(kinds)
    if kind == "str":
        return json.loads(_string(rng))
    if kind == "int":
        return rng.choice([0, -1, 7, 10**18, -(10**30)])
    if kind == "float":
        return rng.choice([0.5, -2.25, 1e5, 3.14159, 1e-7])
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "null":
        return None
    if kind == "list":
        return [_random_value(rng, depth + 1) for _ in range(rng.randrange(0, 4))]
    return {
        json.loads(_string(rng)) + str(i): _random_value(rng, depth + 1)
        for i in range(rng.randrange(0, 4))
    }


def _document(rng: random.Random, container: str) -> tuple[bytes, list[object]]:
    elements = [_random_value(rng, 1) for _ in range(rng.randrange(0, 6))]
    body = "[" + ",".join(f"{_ws(rng)}{_serialize(e, rng)}{_ws(rng)}" for e in elements) + "]"
    if container == "$":
        text = f"{_ws(rng)}{body}{_ws(rng)}"
    else:
        text = f'{_ws(rng)}{{{_ws(rng)}"rows"{_ws(rng)}:{_ws(rng)}{body}{_ws(rng)}}}{_ws(rng)}'
    return text.encode("utf-8"), elements


def _oracle(data: bytes, container: str) -> list[object] | None:
    """The whole-document reference: strict_loads of everything. None when it refuses."""
    try:
        value = ic.strict_loads(data, max_depth=64)
    except ic.StrictJsonError:
        return None
    if container == "$":
        return value if isinstance(value, list) else None
    if isinstance(value, dict) and set(value) == {"rows"} and isinstance(value["rows"], list):
        return value["rows"]
    return None


def _scanner_view(data: bytes, container: str, chunk: int | None) -> list[object] | None:
    """The scanner's elements parsed by strict_loads; None when anything is refused."""
    elements, aborted = scan(data, container=container, ignored=(), chunk=chunk)
    if aborted is not None:
        return None
    out: list[object] = []
    for element in elements:
        if element.data is None:
            return None
        try:
            out.append(ic.strict_loads(element.data, max_depth=64))
        except ic.StrictJsonError:
            return None
    return out


def test_differential_valid_documents_match_json_loads() -> None:
    rng = random.Random(5030)
    checked = 0
    for index in range(400):
        container = "$" if index % 2 else "/rows"
        data, elements = _document(rng, container)
        oracle = _oracle(data, container)
        check(CID, f"generator makes a valid document ({index})", oracle == elements)
        for chunk in (None, 1, 3, 17):
            view = _scanner_view(data, container, chunk)
            ok = view == oracle
            if not ok:
                check(CID, f"doc {index} chunk {chunk}: scanner elements == json.loads", False)
            checked += 1
    check(CID, "the differential corpus was exercised", checked == 1600)


def _mutate(data: bytes, rng: random.Random) -> bytes:
    kind = rng.randrange(6)
    if not data:
        return data
    pos = rng.randrange(len(data))
    if kind == 0:
        return data[:pos] + data[pos + 1 :]
    if kind == 1:
        return (
            data[:pos]
            + rng.choice([b",", b"]", b"}", b"[", b"{", b'"', b"\\", b" ", b":", b"\x00", b"\xff"])
            + data[pos:]
        )
    if kind == 2:
        return (
            data[:pos]
            + rng.choice([b",", b"]", b"}", b"[", b"{", b'"', b"1", b"x"])
            + data[pos + 1 :]
        )
    if kind == 3 and pos + 1 < len(data):
        return data[:pos] + data[pos + 1 : pos + 2] + data[pos : pos + 1] + data[pos + 2 :]
    if kind == 4:
        return data[:pos]
    return data[:pos] + data[pos:] + rng.choice([b",", b"]", b"x", b" ", b"{}", b"[]"])


def test_differential_mutated_documents_fail_in_both() -> None:
    rng = random.Random(5031)
    both_ok = both_bad = 0
    for index in range(1500):
        container = "$" if index % 2 else "/rows"
        data, _elements = _document(rng, container)
        mutated = _mutate(data, rng)
        oracle = _oracle(mutated, container)
        for chunk in (None, 1, 5):
            view = _scanner_view(mutated, container, chunk)
            if (oracle is None) != (view is None) or (oracle is not None and view != oracle):
                check(
                    CID,
                    f"mutated doc {index} chunk {chunk}: scanner {view!r:.60} vs {oracle!r:.60} "
                    f"for {mutated!r:.120}",
                    False,
                )
        if oracle is None:
            both_bad += 1
        else:
            both_ok += 1
    check(
        CID,
        f"the mutation corpus has both outcomes ({both_ok} ok, {both_bad} bad)",
        both_ok > 20 and both_bad > 500,
    )


# --- memory and time -----------------------------------------------------------------------


def _big_rows_chunks(total_bytes: int, element_bytes: int = 4000) -> tuple[list[bytes], str, int]:
    """A `{"rows": [...]}` document of about `total_bytes`, as a list of chunk objects that
    share their storage: built from three byte strings repeated."""
    element = dump(rec("q" * 10, "a" * (element_bytes - 40)))
    head, tail, sep = b'{"rows":[', b"]}", b","
    count = max(1, (total_bytes - len(head) - len(tail)) // (len(element) + 1))
    unit = sep + element
    digest = hashlib.sha256()
    size = 0
    pieces = [head + element] + [unit] * (count - 1) + [tail]
    for piece in pieces:
        digest.update(piece)
        size += len(piece)
    return pieces, digest.hexdigest(), size


def _scan_peak(total_bytes: int) -> tuple[int, int]:
    pieces, _digest, size = _big_rows_chunks(total_bytes)
    scanner = jm.JsonContainerScanner(
        pieces,
        container="/rows",
        ignored_top_level_keys=frozenset(),
        max_depth=64,
        max_record_bytes=ic.MAX_CANONICAL_RECORD_BYTES,
    )
    tracemalloc.start()
    count = 0
    for _element in scanner.elements():
        count += 1
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak, count


def test_memory_peak_does_not_grow_with_the_container() -> None:
    small_peak, small_count = _scan_peak(1024 * 1024)
    large_peak, large_count = _scan_peak(64 * 1024 * 1024)
    check(CID, "both scans saw their elements", small_count > 100 and large_count > 10_000)
    check(
        CID,
        f"peak for a 64 MiB container ({large_peak}) is within 1.5x of a 1 MiB one ({small_peak})",
        large_peak <= 1.5 * max(small_peak, 1),
    )


def test_memory_oversize_element_is_not_buffered() -> None:
    piece = b"x" * (64 * 1024)
    body = (
        [b'{"rows":[{"q":"' + piece]
        + [piece] * 1023
        + [b'","a":"y"},', dump(rec("after", "ok")), b"]}"]
    )
    size = sum(len(p) for p in body)
    digest = hashlib.sha256()
    for p in body:
        digest.update(p)
    tracemalloc.start()
    reader = ic.ChunkReader(body, expected_size=size, expected_sha256=digest.hexdigest())
    got: list[object] = []
    outcome = jm.run_json_import(
        reader,
        build_mapping(),
        verified_content_type=JSON,
        dataset_purpose="training",
        expected_sha256=digest.hexdigest(),
        on_accepted=got.append,
    )
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    check(
        CID,
        f"the 64 MiB element is one rejection and the next imports ({codes(outcome)})",
        codes(outcome) == [("record:1", "record_too_large")] and len(got) == 1,
    )
    check(
        CID,
        f"peak {peak} is far below the element size",
        peak < 8 * 1024 * 1024 and size > 64 * 1024 * 1024,
    )


def test_memory_json_loads_is_never_given_the_document() -> None:
    sizes: list[int] = []
    real = json.loads

    def spy(s: Any, *args: Any, **kwargs: Any) -> Any:
        sizes.append(len(s))
        return real(s, *args, **kwargs)

    records = [rec(f"q{i}", "a" * 200) for i in range(400)]
    data = rows_doc(records, _meta={"pad": "x" * 5000})
    json.loads = spy
    try:
        got, outcome = collect(data, chunk=1000)
    finally:
        json.loads = real
    check(CID, "the import ran", outcome.report.state == "completed" and len(got) == 400)
    check(
        CID,
        f"json.loads saw one element at a time (largest {max(sizes, default=0)} of {len(data)})",
        bool(sizes) and max(sizes) < 400,
    )


def test_time_is_linear_in_the_source_size() -> None:
    def seconds(size: int) -> float:
        best = float("inf")
        for _ in range(2):
            data = b'{"rows":[' + padded_record(size) + b"," + dump(rec("after", "ok")) + b"]}"
            started = time.monotonic()
            got, outcome = collect(data, chunk=1, limits=ic.ImportLimits())
            best = min(best, time.monotonic() - started)
            check(
                CID,
                f"{size}-byte element imports",
                len(got) == 2 and outcome.report.state == "completed",
            )
        return best

    small, large = seconds(64 * 1024), seconds(256 * 1024)
    check(
        CID,
        f"4x the bytes took {large / max(small, 1e-3):.1f}x the time ({small:.2f}s, {large:.2f}s)",
        large < 9 * max(small, 0.05) and large < 60,
    )
    limits = ic.ImportLimits(max_record_bytes=1000)

    def discard_seconds(size: int) -> float:
        started = time.monotonic()
        data = b'{"rows":[{"q":"' + b"x" * size + b'","a":"y"},' + dump(rec("a", "b")) + b"]}"
        got, _outcome = collect(data, chunk=1, limits=limits)
        check(CID, "the follower of a discarded element imports", len(got) == 1)
        return time.monotonic() - started

    small, large = discard_seconds(64 * 1024), discard_seconds(256 * 1024)
    check(
        CID,
        f"a discarded element is linear too ({small:.2f}s, {large:.2f}s)",
        large < 9 * max(small, 0.05),
    )

    def keys_seconds(count: int) -> float:
        parts = b",".join(b'"_k%d":1' % i for i in range(count))
        started = time.monotonic()
        outcome = run(b"{" + parts + b',"rows":[]}', ignored=tuple(f"_k{i}" for i in range(3)))
        elapsed = time.monotonic() - started
        check(
            CID,
            "many unmapped keys end the import at the first",
            outcome.report.failure_code == "unmapped_top_level_key",
        )
        return elapsed

    check(CID, "an unmapped key stops the scan at once", keys_seconds(100_000) < 5.0)


# --- reports: Law 17 -----------------------------------------------------------------------


def test_report_source_failures_are_visible() -> None:
    data = rows_doc([rec("a", "1"), rec("b", "2"), rec("c", "3")])
    digest = hashlib.sha256(data).hexdigest()

    def drive(source: Any, size: int = len(data)) -> jm.JsonImportOutcome:
        reader = ic.ChunkReader(source, expected_size=size, expected_sha256=digest)
        return jm.run_json_import(
            reader,
            build_mapping(),
            verified_content_type=JSON,
            dataset_purpose="training",
            expected_sha256=digest,
        )

    out = drive(support.ExplodingSource([data[:30]], ConnectionResetError("boom"), at=1))
    check(
        CID,
        "a source error mid-stream is blocked/source_unavailable",
        fatal(out)[:2] == ("blocked", "source_unavailable"),
    )
    out = drive([data[:-10]])
    check(
        CID,
        f"a truncated source is never completed ({fatal(out)})",
        out.report.state == "failed"
        and out.report.failure_code in ("source_changed", "json_structure_invalid"),
    )
    out = drive([data[:-1] + b" "])
    check(
        CID,
        "same size, different bytes is failed/source_changed",
        fatal(out)[:2] == ("failed", "source_changed"),
    )
    out = drive([data + b" "], size=len(data))
    check(
        CID,
        "a longer source is failed/source_changed",
        fatal(out)[:2] == ("failed", "source_changed"),
    )
    clock = support.FakeClock()
    reader = ic.ChunkReader(
        [data],
        expected_size=len(data),
        expected_sha256=digest,
        limits=ic.ImportLimits(deadline_seconds=1.0),
        clock=clock,
    )
    clock.advance(5.0)
    out = jm.run_json_import(
        reader,
        build_mapping(),
        verified_content_type=JSON,
        dataset_purpose="training",
        expected_sha256=digest,
    )
    check(
        CID,
        "a passed deadline is failed/parse_timeout",
        fatal(out)[:2] == ("failed", "parse_timeout"),
    )
    limits = ic.ImportLimits(max_rejections=3)
    bad = rows_doc([[1]] * 10)
    out = run(bad, limits=limits)
    check(
        CID,
        f"a spent rejection budget is failed/too_many_rejections ({fatal(out)})",
        fatal(out)[:2] == ("failed", "too_many_rejections"),
    )
    big = ic.ImportLimits(max_source_bytes=10)
    out = run(data, limits=big)
    check(
        CID,
        "a source over the byte limit is failed/source_too_large",
        fatal(out)[:2] == ("failed", "source_too_large"),
    )


def test_report_trailing_bytes_are_hash_checked() -> None:
    data = rows_doc([rec()]) + b"   \n"
    out = run(data)
    check(
        CID,
        "trailing whitespace is part of the verified source",
        out.report.state == "completed" and out.report.source_bytes == len(data),
    )
    short = b'{"rows":[]}'
    reader = ic.ChunkReader(
        [short],
        expected_size=len(short) + 5,
        expected_sha256=hashlib.sha256(short + b"     ").hexdigest(),
    )
    out = jm.run_json_import(
        reader,
        build_mapping(),
        verified_content_type=JSON,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(short + b"     ").hexdigest(),
    )
    check(
        CID,
        "a source shorter than declared is failed/source_changed, not completed",
        fatal(out)[:2] == ("failed", "source_changed"),
    )


def test_report_stream_api_and_preview() -> None:
    data = rows_doc([rec(f"q{i}", "a") for i in range(30)])
    digest = hashlib.sha256(data).hexdigest()
    items = list(
        jm.import_json(
            support.split_chunks(data, 100),
            mapping=build_mapping(),
            expected_sha256=digest,
            expected_size=len(data),
            verified_content_type=JSON,
            dataset_purpose="training",
        )
    )
    check(
        CID,
        "import_json yields every event then one final report",
        isinstance(items[-1], ic.ImportReport)
        and sum(isinstance(i, ic.AcceptedRecord) for i in items[:-1]) == 30,
    )
    stream = jm.json_events(
        make_reader(data), build_mapping(), verified_content_type=JSON, dataset_sha256=digest
    )
    result = ic.preview(stream, 5)
    check(
        CID,
        "preview takes the first five and says it is not complete",
        len(result.accepted) == 5 and not result.complete,
    )


# --- leak, catalogs, wiring ----------------------------------------------------------------


def test_leak_nothing_from_the_document_reaches_a_report() -> None:
    m_val, m_key, m_bad, m_role = (support.make_marker(x) for x in ("val", "key", "bad", "role"))
    data = (
        f'{{"{m_key}":1,"rows":[{{"q":"{m_val}","a":1}},{{"q":"{m_val}","a":"b","{m_key}":1}},'
        f'[{m_val}],"{m_val}",{{"q":"a","a":"b\\ud800{m_bad}"}},'
        f'{{"messages":[{{"role":"{m_role}","content":"x"}}]}}]}}'
    ).encode()
    markers = [m_val, m_key, m_bad, m_role]
    out = run(data)
    check(
        CID, "an unmapped key ends the import", out.report.failure_code == "unmapped_top_level_key"
    )
    check(
        CID,
        "no marker in the fatal report or locators",
        not support.leaked_markers((out.report, out.fatal_locator, out.fatal_container), markers),
    )
    good_keys = data.replace(m_key.encode(), b"_meta")
    out = run(good_keys)
    check(CID, "rejections exist to leak-check", out.report.records_rejected >= 4)
    check(
        CID,
        "no marker in any rejection or locator",
        not support.leaked_markers((out.report, out.fatal_locator), markers),
    )
    try:
        run(data, container="/" + m_key.replace("/", ""), ignored=())
    except Exception as exc:  # noqa: BLE001
        check(CID, "a mapping error names no value", not support.leaked_markers(exc, markers))
    out = run(b"\xff\xfe" + m_val.encode("utf-16-le"))
    check(
        CID,
        "no marker in the encoding failure",
        not support.leaked_markers((out.report, out.fatal_locator), [m_val]),
    )
    source = support.ExplodingSource([data[:20]], ConnectionResetError(m_val), at=1)
    reader = ic.ChunkReader(
        source, expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest()
    )
    out = jm.run_json_import(
        reader,
        build_mapping(),
        verified_content_type=JSON,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
    )
    check(
        CID,
        "a source error's text is not in the report",
        not support.leaked_markers(out.report, [m_val]),
    )


def test_report_every_emitted_code_is_in_a_catalog() -> None:
    emitted_rows: set[str] = set()
    emitted_fatal: set[str] = set()
    docs: list[tuple[bytes, str, tuple[str, ...]]] = [
        (b'{"rows":[[1],{bad,"x"]}', "/rows", ()),
        (b'{"rows":[{"q":"a","q":"b","a":"c"},{"q":"a","a":NaN},"s",{"a":"b"}]}', "/rows", ()),
        (b"[]x", "$", ()),
        (b"{}", "/rows", ()),
        (b'{"rows":1}', "/rows", ()),
        (b'{"x":1}', "/rows", ()),
        (b'{"rows":[],"rows":[]}', "/rows", ()),
        (b"[" * 70, "$", ()),
        (b"\xff\xfe[]", "$", ()),
        (b'{"_meta":1,"rows":[]}', "/rows", ("_meta",)),
    ]
    for data, container, ignored in docs:
        out = run(data, container=container, ignored=ignored)
        emitted_rows |= {r.code for r in out.report.rejections}
        if out.report.failure_code:
            emitted_fatal.add(out.report.failure_code)
    check(
        CID,
        f"every row code is in REJECTION_CODES ({emitted_rows - ic.REJECTION_CODES})",
        emitted_rows <= ic.REJECTION_CODES,
    )
    check(
        CID,
        f"every failure code is in FAILURE_CODES ({emitted_fatal - ic.FAILURE_CODES})",
        emitted_fatal <= ic.FAILURE_CODES,
    )
    new = {
        "json_structure_invalid",
        "container_not_found",
        "container_not_array",
        "duplicate_root_key",
    }
    check(
        CID,
        "the four new failure codes are registered",
        new <= ic.FAILURE_CODES and not (new & ic.REJECTION_CODES),
    )
    check(CID, "and each is emitted by this importer", new <= emitted_fatal)


def test_container_key_spellings() -> None:
    def unsupported(value: object) -> bool:
        try:
            jm.container_key(value)
        except ic.MappingUnsupportedError:
            return True
        return False

    modes = {jm.container_key(c)[0] for c in ("$", "$record", "/rows")}
    check(CID, "the three spellings are three modes", len(modes) == 3)
    check(
        CID,
        "$ and $record carry no key",
        jm.container_key("$")[1] == jm.container_key("$record")[1] == "",
    )
    check(CID, "/a~1b decodes to a/b", jm.container_key("/a~1b")[1] == "a/b")
    check(CID, "/a~0b decodes to a~b", jm.container_key("/a~0b")[1] == "a~b")
    check(CID, "/~01 decodes to ~1", jm.container_key("/~01")[1] == "~1")
    for value in (
        5,
        None,
        b"$",
        ["$"],
        support.StrSub("$"),
        support.StrSub("/rows"),
        " $",
        "$ ",
        "$RECORD",
        "$Record",
        "$records",
        "",
        "rows",
        "/a/b",
        "/",
    ):
        if value == "/":
            check(CID, "/ alone is the empty key, a valid spelling", not unsupported(value))
            continue
        check(CID, f"{value!r} is mapping_unsupported", unsupported(value))


def test_encoding_is_decided_from_the_first_four_bytes() -> None:
    for first, label in (
        (b"\xff\xfe\x00\x00", "UTF-32 LE BOM"),
        (b"\x00\x00\xfe\xff", "UTF-32 BE BOM"),
    ):
        source = support.ExplodingSource([first], ConnectionResetError("later"), at=1)
        reader = ic.ChunkReader(source, expected_size=64, expected_sha256="0" * 64)
        outcome = jm.run_json_import(
            reader,
            build_mapping(),
            verified_content_type=JSON,
            dataset_purpose="training",
            expected_sha256="0" * 64,
        )
        check(
            CID,
            f"{label}: unsupported_encoding is decided before another byte is requested "
            f"({fatal(outcome)})",
            fatal(outcome) == ("failed", "unsupported_encoding", "offset:0"),
        )


def test_ignored_keys_are_compared_exactly() -> None:
    for names, doc_keys in ((("a", " A "), (b"a", b" A ")), (("a", "A"), (b"a", b"A"))):
        data = b"{" + b",".join(b'"' + k + b'":1' for k in doc_keys) + b',"rows":[]}'
        outcome = run(data, ignored=names)
        check(
            CID,
            f"keys {names} that differ only by case / padding are two keys, not a duplicate "
            f"({fatal(outcome)})",
            outcome.report.state == "completed",
        )


def test_container_keys_with_escapes_across_chunk_boundaries() -> None:
    one = dump(rec("k", "v"))
    cases = [
        ("an escaped quote", b'{"a\\"b":[' + one + b"]}", "/" + 'a"b', ()),
        ("an escaped backslash at the end", b'{"a\\\\":[' + one + b"]}", "/a\\", ()),
        (
            "an escaped backslash at the end of an ignored key",
            b'{"x\\\\":1,"rows":[' + one + b"]}",
            "/rows",
            ("x\\",),
        ),
        ("a unicode escape", b'{"\\u00e9":[' + one + b"]}", "/\u00e9", ()),
    ]
    for label, data, container, ignored in cases:
        for chunk in (None, 1, 2, 3, 5):
            got, outcome = collect(data, container=container, ignored=ignored, chunk=chunk)
            check(
                CID,
                f"{label} / chunk {chunk} ({fatal(outcome)})",
                outcome.report.state == "completed" and len(got) == 1,
            )


def test_container_key_length_boundary() -> None:
    name = "\U0001f600" * 256
    raw = json.dumps(name).encode()
    check(
        CID, "fixture: a 256-character name written fully escaped is 3,072 bytes", len(raw) == 3074
    )
    data = b"{" + raw + b':1,"rows":[]}'
    for chunk in (None, 7, 1000):
        outcome = run(data, ignored=(name,), chunk=chunk)
        check(
            CID,
            f"a key of 3,072 escaped bytes is still a mappable name / chunk {chunk} "
            f"({fatal(outcome)})",
            outcome.report.state == "completed",
        )
    for chunk in (None, 7, 1000):
        too_long = b'{"' + b"a" * 3073 + b'":1,"rows":[]}'
        outcome = run(too_long, ignored=(name,), chunk=chunk)
        check(
            CID,
            f"a key over 3,072 bytes is unmapped_top_level_key at its quote / chunk {chunk} "
            f"({fatal(outcome)})",
            fatal(outcome) == ("failed", "unmapped_top_level_key", "offset:1"),
        )
    huge = b'{"' + b"a" * (8 * 1024 * 1024) + b'":1}'
    reader = make_reader(huge, 65536)
    mapping = build_mapping()
    digest = hashlib.sha256(huge).hexdigest()
    tracemalloc.start()
    outcome = jm.run_json_import(
        reader,
        mapping,
        verified_content_type=JSON,
        dataset_purpose="training",
        expected_sha256=digest,
    )
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    check(
        CID,
        f"an 8 MiB key ends the import at once without being held (peak {peak})",
        outcome.report.failure_code == "unmapped_top_level_key" and peak < 2 * 1024 * 1024,
    )
    bad_escape = b'{"\\x":1,"rows":[]}'
    check(
        CID,
        "a key that is not a valid JSON string is a structure error at its quote",
        fatal(run(bad_escape)) == ("failed", "json_structure_invalid", "offset:1"),
    )
    lone = b'{"\\ud800":1,"rows":[]}'
    check(
        CID,
        "a key with a lone surrogate is a structure error at its quote",
        fatal(run(lone)) == ("failed", "json_structure_invalid", "offset:1"),
    )
    check(
        CID,
        "a key holding a raw control character is a structure error at its quote",
        fatal(run(b'{"a\x01":1,"rows":[]}')) == ("failed", "json_structure_invalid", "offset:1"),
    )


def test_depth_counts_the_first_byte_of_every_value() -> None:
    rows = b'{"q":"a","a":"b"}'
    nested_x = b'{"q":"a","a":"b","x":[]}'
    cases = [
        (
            "$ elements at max_depth 1",
            b"[" + rows + b"]",
            "$",
            (),
            1,
            ("failed", "limit_exceeded", "offset:1"),
        ),
        ("$ elements at max_depth 2", b"[" + rows + b"]", "$", (), 2, ("completed", None, None)),
        (
            "$ nested value at max_depth 2",
            b"[" + nested_x + b"]",
            "$",
            (),
            2,
            ("failed", "limit_exceeded", "offset:" + str(1 + nested_x.index(b"[]"))),
        ),
        (
            "$ nested value at max_depth 3",
            b"[" + nested_x + b"]",
            "$",
            (),
            3,
            ("completed", None, None),
        ),
        (
            "/rows elements at max_depth 2",
            b'{"rows":[' + rows + b"]}",
            "/rows",
            (),
            2,
            ("failed", "limit_exceeded", "offset:9"),
        ),
        (
            "/rows elements at max_depth 3",
            b'{"rows":[' + rows + b"]}",
            "/rows",
            (),
            3,
            ("completed", None, None),
        ),
        (
            "an ignored array at max_depth 1",
            b'{"_meta":[],"rows":[]}',
            "/rows",
            ("_meta",),
            1,
            ("failed", "limit_exceeded", "offset:9"),
        ),
        (
            "an ignored array at max_depth 2",
            b'{"_meta":[],"rows":[]}',
            "/rows",
            ("_meta",),
            2,
            ("completed", None, None),
        ),
        (
            "an ignored object at max_depth 2",
            b'{"_meta":{"a":1},"rows":[]}',
            "/rows",
            ("_meta",),
            2,
            ("completed", None, None),
        ),
        (
            "an ignored scalar at max_depth 1",
            b'{"_meta":1,"rows":[]}',
            "/rows",
            ("_meta",),
            1,
            ("completed", None, None),
        ),
        (
            "an ignored string at max_depth 1",
            b'{"_meta":"[[","rows":[]}',
            "/rows",
            ("_meta",),
            1,
            ("completed", None, None),
        ),
        ("$record at max_depth 1", rows, "$record", (), 1, ("completed", None, None)),
        (
            "$record nested at max_depth 1",
            nested_x,
            "$record",
            (),
            1,
            ("failed", "limit_exceeded", "offset:" + str(nested_x.index(b"[]"))),
        ),
        ("$record nested at max_depth 2", nested_x, "$record", (), 2, ("completed", None, None)),
    ]
    for label, data, container, ignored, depth, expected in cases:
        for chunk in (None, 1):
            outcome = run(
                data,
                container=container,
                ignored=ignored,
                chunk=chunk,
                limits=ic.ImportLimits(max_depth=depth),
            )
            check(
                CID,
                f"{label} / chunk {chunk}: {expected} (got {fatal(outcome)})",
                fatal(outcome) == expected,
            )


def test_size_canonical_boundary() -> None:
    limit = ic.MAX_CANONICAL_RECORD_BYTES

    def canonical(count: int) -> tuple[int, int]:
        element = dump(rec("q" * count, "a"))
        got, outcome = collect(b'{"rows":[' + element + b"]}")
        if not got:
            return limit + 1, len(element)
        return len(json.dumps(got[0]["content"], sort_keys=True, separators=(",", ":"))), len(
            element
        )

    low = 1000
    base, _ = canonical(low)
    count = low + (limit - base)
    size, written = canonical(count)
    check(CID, f"fixture reaches the canonical limit exactly ({size} vs {limit})", size == limit)
    check(CID, "and the written element is under max_record_bytes", written < limit)
    got, outcome = collect(b'{"rows":[' + dump(rec("q" * count, "a")) + b"]}")
    check(CID, "a canonical size of exactly MAX_CANONICAL_RECORD_BYTES is accepted", len(got) == 1)
    got, outcome = collect(b'{"rows":[' + dump(rec("q" * (count + 1), "a")) + b"]}")
    check(
        CID,
        "one byte more is record_too_large",
        codes(outcome) == [("record:1", "record_too_large")] and not got,
    )


def test_basic_tool_calls_reach_the_normalizer() -> None:
    tools_mapping = MESSAGES.replace(
        '"on_unmapped_message_keys":"ignore"',
        '"on_unmapped_message_keys":"ignore","tool_calls_key":"tool_calls",'
        '"tool_call_id_key":"tool_call_id","name_key":"name"',
    )
    schemas: dict[str, dict[str, object]] = {"lookup": {"type": "object"}}
    validated: list[object] = []

    def validator(schema: dict[str, object], instance: object, *, field_name: str = "x") -> None:
        validated.append(instance)
        if instance != {"id": "7"}:
            raise ValueError("bad arguments")

    def tool_record(arguments: dict[str, object]) -> dict[str, object]:
        return {
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

    plain = {
        "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    }
    data = rows_doc([plain, tool_record({"id": "7"}), tool_record({"id": 7})])
    digest = hashlib.sha256(data).hexdigest()

    def go(**kwargs: Any) -> tuple[list[dict[str, object]], jm.JsonImportOutcome]:
        got: list[dict[str, object]] = []
        outcome = jm.run_json_import(
            make_reader(data),
            ic.parse_mapping(mapping_text("/rows", ("_meta",), tools_mapping)),
            verified_content_type=JSON,
            dataset_purpose="training",
            expected_sha256=digest,
            on_accepted=lambda candidate: got.append(dict(candidate)),
            **kwargs,
        )
        return got, outcome

    got, outcome = go(tool_schemas=schemas, tool_argument_validator=validator)
    check(
        CID,
        "a validated tool call is valid, a plain record not_applicable",
        [c["tool_schema_status"] for c in got] == ["not_applicable", "valid"],
    )
    check(
        CID,
        "arguments the validator refuses are canonical_schema_invalid",
        codes(outcome) == [("record:3", "canonical_schema_invalid")],
    )
    check(CID, "the validator saw both tool calls", validated == [{"id": "7"}, {"id": 7}])
    check(CID, "tool-call candidates pass FTF-0508", validate_record_batch(got).issues == ())
    got, outcome = go(tool_schemas=schemas)
    check(
        CID,
        "no validator: a tool call is refused, never accepted on trust",
        [c for _, c in codes(outcome)] == ["canonical_schema_invalid"] * 2 and len(got) == 1,
    )
    got, outcome = go(tool_argument_validator=validator)
    check(
        CID,
        "no schemas: an unmapped tool is refused",
        [c for _, c in codes(outcome)] == ["canonical_schema_invalid"] * 2 and len(got) == 1,
    )


def test_report_deadline_is_checked_between_events() -> None:
    data = rows_doc([rec(f"r{i}", "x") for i in range(6)])
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

    outcome = jm.run_json_import(
        reader,
        build_mapping(),
        verified_content_type=JSON,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        on_accepted=advance,
    )
    check(
        CID,
        f"a passed deadline is failed/parse_timeout after the first record ({fatal(outcome)})",
        outcome.report.failure_code == "parse_timeout" and len(seen) == 1,
    )


def test_mapping_source_type_is_exact() -> None:
    for value in (" upload", "upload ", "UPLOAD", "Upload", "", "uploads", None, b"upload"):
        try:
            jm.json_events(
                make_reader(b"[]"),
                build_mapping(),
                verified_content_type=JSON,
                dataset_sha256="0" * 64,
                source_type=value,  # type: ignore[arg-type]
            )
            raised = False
        except ValueError:
            raised = True
        check(CID, f"source_type {value!r} is refused", raised)
    stream = jm.json_events(
        make_reader(b"[]"),
        build_mapping(),
        verified_content_type=JSON,
        dataset_sha256="0" * 64,
        source_type="upload",
    )
    check(CID, "source_type 'upload' is accepted", stream.container == "/rows")


def test_wiring_ci_runs_this_test_and_the_integration_job() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    check(CID, "ci.yml runs tests/tasks/test_ftf_0503.py", "tests/tasks/test_ftf_0503.py" in ci)
    check(CID, "ci.yml has the json-import-integration job", "\n  json-import-integration:" in ci)
    verdict = ci[ci.index("\n  ci-verdict:") :]
    check(
        CID,
        "ci-verdict needs json-import-integration",
        "      - json-import-integration" in verdict,
    )
    check(
        CID,
        "the job runs the integration test",
        "tests/integration/test_ftf_0503_json_import_postgres_s3.py" in ci,
    )
    check(
        CID,
        "the integration test exists",
        (ROOT / "tests" / "integration" / "test_ftf_0503_json_import_postgres_s3.py").is_file(),
    )


def main() -> int:
    tests: list[tuple[str, Callable[[], None]]] = [
        (n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)
    ]
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
