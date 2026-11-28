#!/usr/bin/env python3
# ruff: noqa: E501
# mypy: disable-error-code="arg-type,attr-defined,index,union-attr,call-overload,assignment"
"""FTF-H011-P5 -- grouped rows and label maps (pure; no database or network).

FINE_TUNING_FACTORY_RELEASE_1.md Section 67.2: "Multiple rows forming a conversation require
deterministic ordering and group identity; incomplete groups are quarantined", "supported
transforms are a finite typed allowlist (... enum map, join/group by stable ID)", and "invented
'approved' labels are forbidden". Criteria:

  GRAM   `grouped_multi_row` parses with `group_rows {group_key, order_key, order_type: int |
         rfc3339}`, `role`, `content`, `role_map`; `grouped_rows` stays `mapping_unsupported`;
         `label_enum_map` only for `language` / `task_category`, only for a mapped metadata slot
  GROUP  the CSV importer assembles contiguous, strictly ordered rows into one record per group
  QUAR   a gap, a duplicate or earlier order, a non-contiguous group, a group that does not end
         with an assistant turn, an over-large group and a group with an unreadable row are each
         ONE `incomplete_group` (or `record_too_large`) rejection at the group's first row
  ORDER  an order value outside its type is `invalid_order_key`
  MEM    an over-large group is no longer buffered
  OTHER  JSONL / JSON / XLSX refuse a grouped mapping as `failed` / `mapping_unsupported`
  LABEL  an exact enum map for language / task_category, `unmapped_label` otherwise; consent and
         quality_label stay verbatim
  SAME   a mapping without the new keys builds the same record as before (no field changed)

Run directly: python3 tests/tasks/test_ftf_h011_p5.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))

from workers.data import csv_importer, json_importer, jsonl_importer  # noqa: E402
from workers.data import import_common as ic  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


CSV_BLOCK = {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0}


def grouped(order_type: str = "int", **extra: Any) -> dict[str, Any]:
    return {
        "shape": "grouped_multi_row",
        "group_rows": {
            "group_key": {"header": "conv"},
            "order_key": {"header": "turn"},
            "order_type": order_type,
        },
        "role": {"header": "who"},
        "content": {"header": "text"},
        "role_map": {"u": "user", "a": "assistant", "s": "system"},
        "csv": CSV_BLOCK,
        **extra,
    }


def csv_bytes(
    rows: list[list[str]], header: tuple[str, ...] = ("conv", "turn", "who", "text")
) -> bytes:
    lines = [",".join(header)] + [",".join(r) for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def run_csv(data: bytes, document: dict[str, Any], **limits: Any) -> tuple[Any, list[Any]]:
    mapping = ic.mapping_from_object(document)
    lim = ic.ImportLimits(**limits) if limits else ic.DEFAULT_LIMITS
    reader = ic.ChunkReader([data], expected_size=len(data), expected_sha256=sha(data), limits=lim)
    accepted: list[Any] = []
    outcome = csv_importer.run_csv_import(
        reader,
        mapping,
        verified_content_type="text/csv",
        dataset_purpose="training",
        expected_sha256=sha(data),
        limits=lim,
        on_accepted=accepted.append,
    )
    return outcome.report, accepted


def texts(candidate: Any) -> list[tuple[str, str]]:
    return [
        (m["role"], "".join(part["text"] for part in m["content"]))
        for m in candidate["content"]["messages"]
    ]


def rejections(report: Any) -> list[tuple[str, str]]:
    return [(r.locator, r.code) for r in report.rejections]


def refusal(document: object) -> str | None:
    try:
        ic.mapping_from_object(document)
    except ic.MappingUnsupportedError:
        return "mapping_unsupported"
    except ic.MappingInvalidError as exc:
        return f"mapping_invalid:{exc.reason}"
    return None


# --- GRAM ------------------------------------------------------------------------------------


def test_grammar() -> None:
    check("GRAM-1", "grouped_multi_row parses", refusal(grouped()) is None)
    check("GRAM-2", "rfc3339 order parses", refusal(grouped("rfc3339")) is None)
    check(
        "GRAM-3",
        "grouped_rows is still recognised-unsupported",
        refusal({"shape": "grouped_rows"}) == "mapping_unsupported",
    )
    for label, document, reason in (
        ("an unknown order type", grouped("float"), "bad_value"),
        ("a non-str order type", grouped(7), "wrong_type"),
        ("a top-level group_key", {**grouped(), "group_key": {"header": "conv"}}, "unknown_key"),
        ("no role", {k: v for k, v in grouped().items() if k != "role"}, "missing_key"),
        ("no group_rows", {k: v for k, v in grouped().items() if k != "group_rows"}, "missing_key"),
        (
            "an extra group_rows key",
            {**grouped(), "group_rows": {**grouped()["group_rows"], "x": 1}},
            "unknown_key",
        ),
        ("a role map to an unknown role", {**grouped(), "role_map": {"u": "bot"}}, "bad_role_map"),
    ):
        got = refusal(document)
        check(
            "GRAM-4",
            f"{label} is mapping_invalid:{reason} (got {got})",
            got == f"mapping_invalid:{reason}",
        )
    meta = {"language": {"header": "lang"}, "consent": {"header": "ok"}}
    base = {**grouped(), "metadata": meta}
    for label, labels, reason in (
        ("a consent map (no invented approved)", {"consent": {"y": "approved"}}, "unknown_key"),
        ("a quality_label map", {"quality_label": {"g": "good"}}, "unknown_key"),
        (
            "a map for a slot the mapping does not read",
            {"task_category": {"a": "b"}},
            "missing_key",
        ),
        ("an empty block", {}, "bad_value"),
        ("an empty table", {"language": {}}, "bad_value"),
        ("a non-str label", {"language": {"ar": 1}}, "wrong_type"),
        ("an empty label", {"language": {"ar": ""}}, "bad_value"),
        ("a table that is a list", {"language": ["ar"]}, "wrong_type"),
    ):
        got = refusal({**base, "label_enum_map": labels})
        check(
            "GRAM-5",
            f"{label} is mapping_invalid:{reason} (got {got})",
            got == f"mapping_invalid:{reason}",
        )
    check(
        "GRAM-6",
        "a language map on a prompt_completion mapping parses",
        refusal(
            {
                "shape": "prompt_completion",
                "prompt": {"header": "q"},
                "completion": {"header": "a"},
                "metadata": {"language": {"header": "lang"}},
                "label_enum_map": {"language": {"Arabic": "ar"}},
            }
        )
        is None,
    )
    a = ic.mapping_from_object(grouped())
    b = ic.mapping_from_object(json.loads(json.dumps(grouped())))
    check(
        "GRAM-7",
        "the grouped mapping digest is deterministic",
        a.mapping_sha256 == b.mapping_sha256,
    )


# --- GROUP / QUAR / ORDER / MEM ----------------------------------------------------------------


def test_groups_assemble_in_order() -> None:
    data = csv_bytes(
        [
            ["c1", "1", "s", "be brief"],
            ["c1", "2", "u", "مرحبا"],
            ["c1", "3", "a", "أهلا"],
            ["c2", "7", "u", "hi"],
            ["c2", "8", "a", "hello"],
        ]
    )
    report, accepted = run_csv(data, grouped())
    check(
        "GROUP-1",
        f"two groups, two records ({report.state}, {rejections(report)})",
        report.state == "completed" and len(accepted) == 2,
    )
    if len(accepted) == 2:
        check(
            "GROUP-2",
            "the rows of a group are its turns, in order",
            texts(accepted[0]) == [("system", "be brief"), ("user", "مرحبا"), ("assistant", "أهلا")]
            and texts(accepted[1]) == [("user", "hi"), ("assistant", "hello")],
        )
        check(
            "GROUP-3",
            "record indexes count groups, not rows",
            [c["record_index"] for c in accepted] == [0, 1],
        )
    check(
        "GROUP-4",
        "records_seen counts groups and balances",
        report.records_seen == 2 and report.records_accepted == 2 and report.records_rejected == 0,
    )
    stamps = csv_bytes(
        [
            ["c1", "2026-01-01T10:00:00Z", "u", "q"],
            ["c1", "2026-01-01T10:00:01+00:00", "a", "r"],
            ["c2", "2026-01-01T09:00:00+03:00", "u", "q"],
            ["c2", "2026-01-01T09:30:00.5+03:00", "a", "r"],
        ]
    )
    report, accepted = run_csv(stamps, grouped("rfc3339"))
    check(
        "GROUP-5",
        f"rfc3339 orders assemble ({rejections(report)})",
        len(accepted) == 2 and report.records_rejected == 0,
    )


def test_incomplete_groups_are_quarantined() -> None:
    cases = [
        (
            "a gap",
            [["c1", "1", "u", "q"], ["c1", "3", "a", "r"]],
            [("line:2", "incomplete_group")],
            0,
        ),
        (
            "a duplicate order",
            [["c1", "1", "u", "q"], ["c1", "1", "a", "r"]],
            [("line:2", "incomplete_group")],
            0,
        ),
        (
            "an earlier order",
            [["c1", "2", "u", "q"], ["c1", "1", "a", "r"]],
            [("line:2", "incomplete_group")],
            0,
        ),
        (
            "no assistant at the end",
            [["c1", "1", "u", "q"], ["c1", "2", "a", "r"], ["c1", "3", "u", "more?"]],
            [("line:2", "incomplete_group")],
            0,
        ),
        ("a single user row", [["c1", "1", "u", "q"]], [("line:2", "incomplete_group")], 0),
        (
            "an unreadable row inside the group",
            [["c1", "1", "u", "q"], ["c1", "2", "x", "?"], ["c1", "3", "a", "r"]],
            [("line:3", "unmapped_role"), ("line:2", "incomplete_group")],
            0,
        ),
        (
            "a short row inside the group",
            [["c1", "1", "u", "q"], ["c1", "2"], ["c1", "3", "a", "r"]],
            [("line:3", "column_count_mismatch"), ("line:2", "incomplete_group")],
            0,
        ),
        (
            "a good group after a bad one",
            [
                ["c1", "1", "u", "q"],
                ["c1", "3", "a", "r"],
                ["c2", "1", "u", "q"],
                ["c2", "2", "a", "r"],
            ],
            [("line:2", "incomplete_group")],
            1,
        ),
    ]
    for label, rows, expected, accepted_count in cases:
        report, accepted = run_csv(csv_bytes(rows), grouped())
        check(
            "QUAR-1",
            f"{label}: {expected} and {accepted_count} accepted (got {rejections(report)}, {len(accepted)})",
            sorted(rejections(report)) == sorted(expected) and len(accepted) == accepted_count,
        )
        check(
            "QUAR-2",
            f"{label}: the report balances",
            report.records_seen == report.records_accepted + report.records_rejected,
        )
    # a gap is legal for rfc3339 (only strictly later is required)
    report, accepted = run_csv(
        csv_bytes(
            [["c1", "2026-01-01T00:00:00Z", "u", "q"], ["c1", "2026-01-05T00:00:00Z", "a", "r"]]
        ),
        grouped("rfc3339"),
    )
    check("QUAR-3", "rfc3339: a later timestamp after any gap is ordered", len(accepted) == 1)
    report, accepted = run_csv(
        csv_bytes(
            [
                ["c1", "2026-01-01T00:00:00Z", "u", "q"],
                ["c1", "2026-01-01T03:00:00+03:00", "a", "r"],
            ]
        ),
        grouped("rfc3339"),
    )
    check(
        "QUAR-4",
        "rfc3339: the same instant in another offset is a duplicate order",
        rejections(report) == [("line:2", "incomplete_group")] and not accepted,
    )


def test_order_values() -> None:
    for label, order_type, value in (
        ("a decimal", "int", "1.5"),
        ("a negative", "int", "-1"),
        ("a padded int", "int", " 1"),
        ("an Arabic-Indic digit", "int", "١"),
        ("a timestamp without an offset", "rfc3339", "2026-01-01T00:00:00"),
        ("a date only", "rfc3339", "2026-01-01"),
        ("a space for T", "rfc3339", "2026-01-01 00:00:00Z"),
        ("an impossible date", "rfc3339", "2026-02-30T00:00:00Z"),
    ):
        report, _ = run_csv(csv_bytes([["c1", value, "u", "q"]]), grouped(order_type))
        got = rejections(report)
        check(
            "ORDER-1",
            f"{label} ({value!r}) is invalid_order_key (got {got})",
            got == [("line:2", "invalid_order_key")],
        )
    report, _ = run_csv(csv_bytes([["", "1", "u", "q"]]), grouped())
    check(
        "ORDER-2",
        "an empty group key is required_field_empty",
        rejections(report) == [("line:2", "required_field_empty")],
    )


def test_memory_is_bounded_by_one_group() -> None:
    big = "x" * 400
    rows = [["c1", str(i), "u" if i % 2 else "a", big] for i in range(1, 9)]
    rows.append(["c2", "1", "u", "q"])
    rows.append(["c2", "2", "a", "r"])
    report, accepted = run_csv(csv_bytes(rows), grouped(), max_record_bytes=2048)
    check(
        "MEM-1",
        f"a group over max_record_bytes is one record_too_large; the next group is fine ({rejections(report)})",
        rejections(report) == [("line:2", "record_too_large")] and len(accepted) == 1,
    )
    mapping = ic.mapping_from_object(grouped())
    assembler = ic.GroupAssembler(mapping, max_record_bytes=100)
    for i in range(1, 50):
        mapped = ic.MappedRow(
            {"messages": [{"role": "user", "content": "y" * 60}], "metadata": {}}, "k", i
        )
        assembler.add(f"line:{i + 1}", mapped, 60)
    held = assembler._open.messages if assembler._open is not None else None
    check("MEM-2", f"an over-large group buffers nothing ({len(held or [])} held)", held == [])


# --- OTHER -------------------------------------------------------------------------------------


def test_other_importers_refuse_a_grouped_mapping() -> None:
    document = {k: v for k, v in grouped().items() if k != "csv"}
    document = {
        **document,
        "group_rows": {
            **document["group_rows"],
            "group_key": {"key": "c"},
            "order_key": {"key": "t"},
        },
        "role": {"key": "r"},
        "content": {"key": "x"},
    }
    mapping = ic.mapping_from_object(document)
    line = b'{"c":"1","t":"1","r":"u","x":"q"}\n'
    json_mapping = ic.mapping_from_object(
        {**document, "json": {"container": "", "ignored_top_level_keys": []}}
    )
    for name, run, data, ct, m in (
        ("jsonl", jsonl_importer.run_jsonl_import, line, "application/jsonl", mapping),
        (
            "json",
            json_importer.run_json_import,
            b"[" + line.strip() + b"]",
            "application/json",
            json_mapping,
        ),
    ):
        reader = ic.ChunkReader([data], expected_size=len(data), expected_sha256=sha(data))
        try:
            outcome = run(
                reader,
                m,
                verified_content_type=ct,
                dataset_purpose="training",
                expected_sha256=sha(data),
            )
            got = (
                outcome.report.state,
                outcome.report.failure_code,
                outcome.report.records_accepted,
            )
        except Exception as exc:  # noqa: BLE001 -- the verdict must be a report, never a crash
            got = (type(exc).__name__, str(exc), -1)
        check(
            "OTHER-1",
            f"{name}: a grouped mapping is failed/mapping_unsupported (got {got})",
            got == ("failed", "mapping_unsupported", 0),
        )
    import test_ftf_0505 as t505

    data = t505.book(
        [
            [t505.S("c"), t505.S("t"), t505.S("r"), t505.S("x")],
            [t505.S("1"), t505.S("1"), t505.S("u"), t505.S("q")],
        ]
    )
    xlsx_doc = {**grouped(), "xlsx": {"sheet_name": "Data", "header_row": 0}}
    del xlsx_doc["csv"]
    xlsx_doc["group_rows"] = {
        "group_key": {"header": "c"},
        "order_key": {"header": "t"},
        "order_type": "int",
    }
    xlsx_doc["role"], xlsx_doc["content"] = {"header": "r"}, {"header": "x"}
    from workers.data import xlsx_importer as xi

    reader = ic.ChunkReader([data], expected_size=len(data), expected_sha256=sha(data))
    outcome = xi.run_xlsx_import(
        reader,
        ic.mapping_from_object(xlsx_doc),
        verified_content_type=xi.XLSX_CONTENT_TYPE,
        dataset_purpose="training",
        expected_sha256=sha(data),
    )
    got = (outcome.report.state, outcome.report.failure_code)
    check(
        "OTHER-2",
        f"xlsx: a grouped mapping is failed/mapping_unsupported (got {got})",
        got == ("failed", "mapping_unsupported"),
    )
    got2: tuple[str, str] | None
    try:
        ic.apply_mapping(ic.mapping_from_object(grouped()), ["c", "1", "u", "q"])
    except ic.ImportAborted as exc:
        got2 = (exc.state, exc.failure_code)
    else:
        got2 = None
    check(
        "OTHER-3",
        "apply_mapping without grouped=True refuses a grouped mapping",
        got2 == ("failed", "mapping_unsupported"),
    )
    plain = ic.mapping_from_object(
        {"shape": "prompt_completion", "prompt": {"index": 0}, "completion": {"index": 1}}
    )
    got3: str | None
    try:
        ic.apply_mapping(plain, ["q", "a"], grouped=True)
    except ic.ImportAborted as exc:
        got3 = exc.failure_code
    else:
        got3 = None
    check(
        "OTHER-4", "grouped=True on a plain mapping is refused too", got3 == "mapping_unsupported"
    )


# --- LABEL ---------------------------------------------------------------------------------------


def test_label_maps_are_exact() -> None:
    document = {
        "shape": "prompt_completion",
        "prompt": {"header": "q"},
        "completion": {"header": "a"},
        "metadata": {
            "language": {"header": "lang"},
            "task_category": {"header": "cat"},
            "consent": {"header": "ok"},
            "quality_label": {"header": "qual"},
        },
        "label_enum_map": {
            "language": {"Arabic": "ar", "العربية": "ar", "English": "en"},
            "task_category": {"billing q": "billing"},
        },
        "csv": CSV_BLOCK,
    }
    header = ("q", "a", "lang", "cat", "ok", "qual")
    rows = [
        ["hi", "hello", "العربية", "billing q", "yes", "gold"],
        ["hi", "hello", "arabic", "billing q", "yes", "gold"],
        ["hi", "hello", "Arabic ", "billing q", "yes", "gold"],
        ["hi", "hello", "English", "other", "yes", "gold"],
        ["hi", "hello", "", "", "y", ""],
    ]
    report, accepted = run_csv(csv_bytes(rows, header), document)
    check(
        "LABEL-1",
        f"only exact raw values map; case and padding are unmapped_label ({rejections(report)})",
        rejections(report)
        == [
            ("line:3", "unmapped_label"),
            ("line:4", "unmapped_label"),
            ("line:5", "unmapped_label"),
        ],
    )
    slots = [r.slot for r in report.rejections]
    check(
        "LABEL-2",
        "the rejection names the slot",
        slots == ["metadata.language", "metadata.language", "metadata.task_category"],
    )
    if len(accepted) == 2:
        first, empty = accepted[0]["content"]["metadata"], accepted[1]["content"]["metadata"]
        check(
            "LABEL-3",
            f"mapped labels are the targets; consent and quality_label stay verbatim ({first})",
            first
            == {
                "language": "ar",
                "task_category": "billing",
                "consent": "yes",
                "quality_label": "gold",
            },
        )
        check(
            "LABEL-4",
            f"an empty optional cell is omitted, never mapped ({empty})",
            empty == {"consent": "y"},
        )
    else:
        check("LABEL-3", f"two rows accepted (got {len(accepted)})", False)
    grouped_doc = {
        **grouped(),
        "metadata": {"language": {"header": "lang"}},
        "label_enum_map": {"language": {"Arabic": "ar"}},
    }
    report, accepted = run_csv(
        csv_bytes(
            [["c1", "1", "u", "q", "Arabic"], ["c1", "2", "a", "r", "Arabic"]],
            ("conv", "turn", "who", "text", "lang"),
        ),
        grouped_doc,
    )
    check(
        "LABEL-5",
        "a grouped record takes its first row's mapped metadata",
        len(accepted) == 1 and accepted[0]["content"]["metadata"] == {"language": "ar"},
    )


# --- SAME ------------------------------------------------------------------------------------------


def test_plain_mappings_are_unchanged() -> None:
    document = {
        "shape": "prompt_completion",
        "prompt": {"header": "q"},
        "completion": {"header": "a"},
        "csv": CSV_BLOCK,
    }
    mapping = ic.mapping_from_object(document)
    check(
        "SAME-1",
        "a plain mapping carries none of the new fields",
        mapping.group_rows is None
        and mapping.role_slot is None
        and mapping.content_slot is None
        and mapping.label_maps == (),
    )
    mapped = ic.apply_mapping(mapping, ["q1", "a1"], headers=["q", "a"])
    check(
        "SAME-2",
        "apply_mapping builds the same raw record and no order",
        mapped
        == ic.MappedRow(
            {
                "messages": [
                    {"role": "user", "content": "q1"},
                    {"role": "assistant", "content": "a1"},
                ],
                "metadata": {},
            },
            None,
        ),
    )
    report, accepted = run_csv(b"q,a\nq1,a1\nq2,a2\n", document)
    check(
        "SAME-3",
        "a plain CSV import is one record per row",
        [c["record_index"] for c in accepted] == [0, 1] and report.records_seen == 2,
    )


# --- STORE: the review/approval path does not accept the new grammar yet ------------------------


def test_store_keeps_the_new_grammar_unsupported() -> None:
    sys.path.insert(0, str(ROOT / "apps" / "api"))
    import app.datasets.dataset_mapping_store as store

    def code(document: object) -> str | None:
        try:
            store._parse_document(document)
        except store.MappingStoreError as exc:
            return exc.code
        return None

    labelled = {
        "shape": "prompt_completion",
        "prompt": {"index": 0},
        "completion": {"index": 1},
        "metadata": {"language": {"index": 2}},
        "label_enum_map": {"language": {"Arabic": "ar"}},
    }
    for label, document, expected in (
        ("a complete grouped_multi_row mapping", grouped(), "mapping_unsupported"),
        ("a bare grouped_multi_row shape", {"shape": "grouped_multi_row"}, "mapping_unsupported"),
        ("grouped_rows", {"shape": "grouped_rows"}, "mapping_unsupported"),
        ("a label_enum_map", labelled, "mapping_unsupported"),
        ("a list shape", {"shape": ["grouped_multi_row"]}, "mapping_invalid"),
        ("a dict shape", {"shape": {}}, "mapping_invalid"),
        ("a padded grouped shape", {"shape": " grouped_multi_row"}, "mapping_invalid"),
        ("a plain mapping", {k: v for k, v in labelled.items() if k != "label_enum_map"}, None),
    ):
        got = code(document)
        check("STORE-1", f"{label}: {expected} (got {got})", got == expected)


# --- Mutation-gate pins (each a behaviour, not an implementation detail) ----------------------


def test_edges_are_pinned() -> None:
    # group keys are exact: case and padding make another group
    rows = [
        ["c1", "1", "u", "q"],
        ["c1", "2", "a", "r"],
        ["C1", "1", "u", "q"],
        ["C1", "2", "a", "r"],
        [" c1", "1", "u", "q"],
        [" c1", "2", "a", "r"],
    ]
    report, accepted = run_csv(csv_bytes(rows), grouped())
    check(
        "EDGE-1",
        f"c1 / C1 / ' c1' are three groups ({rejections(report)})",
        len(accepted) == 3 and report.records_rejected == 0,
    )
    # order types are exact
    for value in (" int", "INT", "Int", "rfc3339 ", "RFC3339"):
        got = refusal(grouped(value))
        check(
            "EDGE-2",
            f"order_type {value!r} is mapping_invalid:bad_value (got {got})",
            got == "mapping_invalid:bad_value",
        )
    # a NUL in a grouped row is its own rejection and poisons the group
    report, accepted = run_csv(
        csv_bytes(
            [
                ["c1", "2026-01-01T00:00:00Z", "u", "q"],
                ["c1", "2026-01-01T00:00:01Z", "a", "r\x00"],
                ["c1", "2026-01-01T00:00:02Z", "a", "r"],
            ]
        ),
        grouped("rfc3339"),
    )
    check(
        "EDGE-3",
        f"a NUL cell is nul_character and the group is incomplete ({rejections(report)})",
        sorted(rejections(report)) == [("line:2", "incomplete_group"), ("line:3", "nul_character")]
        and not accepted,
    )
    # poison matters even where no ordering rule would catch the missing turn (rfc3339 has no gaps)
    report, accepted = run_csv(
        csv_bytes(
            [
                ["c1", "2026-01-01T00:00:00Z", "u", "q"],
                ["c1", "2026-01-01T00:00:01Z", "x", "?"],
                ["c1", "2026-01-01T00:00:02Z", "a", "r"],
            ]
        ),
        grouped("rfc3339"),
    )
    check(
        "EDGE-4",
        f"an unreadable row poisons an rfc3339 group ({rejections(report)})",
        sorted(rejections(report)) == [("line:2", "incomplete_group"), ("line:3", "unmapped_role")]
        and not accepted,
    )
    # the size bound is inclusive: exactly max_record_bytes is allowed
    one = "z" * 500
    rows = [["c1", "1", "u", one], ["c1", "2", "a", one]]
    per_row = len("c1") + len("1") + len("u") + len(one)
    report, accepted = run_csv(csv_bytes(rows), grouped(), max_record_bytes=2 * per_row)
    check(
        "EDGE-5",
        f"a group of exactly max_record_bytes is accepted ({rejections(report)})",
        len(accepted) == 1,
    )
    report, accepted = run_csv(csv_bytes(rows), grouped(), max_record_bytes=2 * per_row - 1)
    check(
        "EDGE-6",
        "one byte over is record_too_large",
        rejections(report) == [("line:2", "record_too_large")],
    )
    # a label table of exactly 64 entries is allowed, 65 is not
    meta = {**grouped(), "metadata": {"language": {"header": "lang"}}}
    table = {f"v{i}": "x" for i in range(64)}
    check(
        "EDGE-7",
        "64 label entries parse",
        refusal({**meta, "label_enum_map": {"language": table}}) is None,
    )
    check(
        "EDGE-8",
        "65 label entries are bad_value",
        refusal({**meta, "label_enum_map": {"language": {**table, "v64": "x"}}})
        == "mapping_invalid:bad_value",
    )
    # the assembler refuses to be misused
    plain = ic.mapping_from_object(
        {"shape": "prompt_completion", "prompt": {"index": 0}, "completion": {"index": 1}}
    )
    try:
        ic.GroupAssembler(plain, max_record_bytes=10)
        ok = False
    except ValueError:
        ok = True
    check("EDGE-9", "a GroupAssembler needs a grouped mapping", ok)
    assembler = ic.GroupAssembler(ic.mapping_from_object(grouped()), max_record_bytes=1000)
    for label, mapped in (
        ("no group key", ic.MappedRow({"messages": [], "metadata": {}}, None, 1)),
        ("no order", ic.MappedRow({"messages": [], "metadata": {}}, "k", None)),
    ):
        try:
            assembler.add("line:2", mapped, 1)
            ok = False
        except ValueError:
            ok = True
        check("EDGE-10", f"a grouped row with {label} is a programming error", ok)
    # the observer still gets the exact headers (None for an object row, a tuple for a table)
    seen: list[object] = []
    import dataclasses

    observed = dataclasses.replace(plain, row_observer=lambda row, headers: seen.append(headers))
    ic.apply_mapping(observed, ["q", "a"], headers=["x", "y"])
    ic.apply_mapping(observed, ["q", "a"])
    check(
        "EDGE-11", f"the observer receives ('x','y') then None ({seen})", seen == [("x", "y"), None]
    )


def test_csv_importer_requalifies() -> None:
    """Law 15: the certified FTF-0504 CSV suite still passes on the edited importer."""
    import subprocess

    ran = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "tasks" / "test_ftf_0504.py")],
        capture_output=True,
        text=True,
        timeout=600,
    )
    check(
        "LAW15-0504",
        f"tests/tasks/test_ftf_0504.py passes ({ran.stdout[-300:]})",
        ran.returncode == 0,
    )


def test_review_round_1_pins() -> None:
    # (1) round 2: a failed row whose group key is readable belongs to ITS group; one whose key
    # cannot be read (empty key) poisons the open group and the next one it may have opened.
    for order_type, a, b, c in (
        ("int", "1", "2", "3"),
        ("rfc3339", "2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z"),
    ):
        rows = [
            ["c1", a, "u", "q"],
            ["c1", b, "a", "r"],
            ["c2", a, "s", "sys\x00"],
            ["c2", b, "u", "q"],
            ["c2", c, "a", "r"],
        ]
        report, accepted = run_csv(csv_bytes(rows), grouped(order_type))
        got = sorted(rejections(report))
        check(
            "BOUND-1",
            f"{order_type}: a NUL first row of c2 quarantines c2 only; c1 is complete ({got}, {len(accepted)})",
            got == [("line:4", "incomplete_group"), ("line:4", "nul_character")]
            and [texts(x) for x in accepted] == [[("user", "q"), ("assistant", "r")]],
        )
        rows = [["c1", a, "x", "?"], ["c1", b, "u", "q"], ["c1", c, "a", "r"]]
        report, accepted = run_csv(csv_bytes(rows), grouped(order_type))
        got = sorted(rejections(report))
        check(
            "BOUND-2",
            f"{order_type}: a failed first data row quarantines its group ({got})",
            got == [("line:2", "incomplete_group"), ("line:2", "unmapped_role")] and not accepted,
        )
        rows = [
            ["c1", a, "u", "q"],
            ["c1", b, "a", "r"],
            ["", a, "u", "?"],
            ["c2", a, "u", "q"],
            ["c2", b, "a", "r"],
        ]
        report, accepted = run_csv(csv_bytes(rows), grouped(order_type))
        got = sorted(rejections(report))
        check(
            "BOUND-3",
            f"{order_type}: a row whose key cannot be read poisons both neighbouring groups ({got})",
            got
            == [
                ("line:2", "incomplete_group"),
                ("line:4", "required_field_empty"),
                ("line:5", "incomplete_group"),
            ]
            and not accepted,
        )
        # the L2 round-2 finding: C's failed first row, then B, then the rest of C
        rows = [
            ["A", a, "u", "q"],
            ["A", b, "a", "r"],
            ["C", a, "x", "?"],
            ["B", a, "u", "q"],
            ["B", b, "a", "r"],
            ["C", b, "u", "q2"],
            ["C", c, "a", "r2"],
        ]
        report, _accepted = run_csv(csv_bytes(rows), grouped(order_type))
        check(
            "BOUND-4",
            f"{order_type}: a group whose failed first row is not adjacent fails the import ({report.state}/{report.failure_code})",
            (report.state, report.failure_code) == ("failed", "non_contiguous_group"),
        )
        rows = [
            ["A", a, "u", "q"],
            ["A", b, "a", "r"],
            ["C", a, "x", "?"],
            ["C", b, "u", "q2"],
            ["C", c, "a", "r2"],
        ]
        report, accepted = run_csv(csv_bytes(rows), grouped(order_type))
        got = sorted(rejections(report))
        check(
            "BOUND-5",
            f"{order_type}: an adjacent failed first row quarantines C and keeps A ({got})",
            got == [("line:4", "incomplete_group"), ("line:4", "unmapped_role")]
            and len(accepted) == 1,
        )
    # advisory: Unicode-distinct keys are distinct groups (NFC vs NFD are different strings)
    rows = [
        ["\u00e9", "1", "u", "q"],
        ["\u00e9", "2", "a", "r"],
        ["e\u0301", "1", "u", "q"],
        ["e\u0301", "2", "a", "r"],
    ]
    report, accepted = run_csv(csv_bytes(rows), grouped())
    check(
        "BOUND-6",
        f"NFC and NFD keys are two groups, never merged ({rejections(report)})",
        len(accepted) == 2 and report.records_rejected == 0,
    )
    # (2) a key that reappears stops the whole import fail-closed
    rows = [
        ["c1", "1", "u", "q"],
        ["c1", "2", "a", "r"],
        ["c2", "1", "u", "q"],
        ["c2", "2", "a", "r"],
        ["c1", "3", "u", "late"],
        ["c1", "4", "a", "late"],
    ]
    report, _accepted = run_csv(csv_bytes(rows), grouped())
    check(
        "NONCONT-1",
        f"a non-contiguous group fails the import (got {report.state}/{report.failure_code})",
        (report.state, report.failure_code) == ("failed", "non_contiguous_group"),
    )
    # (3) every row of a group carries the same metadata: no label is chosen from one row
    header = ("conv", "turn", "who", "text", "qual", "ok")
    doc = {
        **grouped(),
        "metadata": {"quality_label": {"header": "qual"}, "consent": {"header": "ok"}},
    }
    for label, r1, r2 in (
        ("approved then rejected", ["approved", "y"], ["rejected", "y"]),
        ("empty then rejected", ["", "y"], ["rejected", "y"]),
        ("consent granted then revoked", ["gold", "granted"], ["gold", "revoked"]),
    ):
        rows = [["c1", "1", "u", "q", *r1], ["c1", "2", "a", "r", *r2]]
        report, accepted = run_csv(csv_bytes(rows, header), doc)
        check(
            "META-1",
            f"{label}: group_metadata_conflict and nothing accepted ({rejections(report)})",
            rejections(report) == [("line:2", "group_metadata_conflict")] and not accepted,
        )
    rows = [["c1", "1", "u", "q", "gold", "y"], ["c1", "2", "a", "r", "gold", "y"]]
    report, accepted = run_csv(csv_bytes(rows, header), doc)
    check(
        "META-2",
        "identical metadata on every row is kept verbatim",
        len(accepted) == 1
        and accepted[0]["content"]["metadata"] == {"quality_label": "gold", "consent": "y"},
    )
    # (4) a short row poisons an rfc3339 group (no gap rule to catch it)
    rows = [
        ["c1", "2026-01-01T00:00:00Z", "u", "q"],
        ["c1", "2026-01-01T00:00:01Z"],
        ["c1", "2026-01-01T00:00:02Z", "a", "r"],
    ]
    report, accepted = run_csv(csv_bytes(rows), grouped("rfc3339"))
    check(
        "EDGE-12",
        f"a short row inside an rfc3339 group quarantines it ({rejections(report)})",
        sorted(rejections(report))
        == [("line:2", "incomplete_group"), ("line:3", "column_count_mismatch")]
        and not accepted,
    )


def test_round_2_mutation_pins() -> None:
    # a failed row's key is exact: C1 is not c1
    rows = [
        ["c1", "1", "u", "q"],
        ["c1", "2", "a", "r"],
        ["C1", "1", "x", "?"],
        ["C1", "2", "u", "q"],
        ["C1", "3", "a", "r"],
    ]
    report, accepted = run_csv(csv_bytes(rows), grouped())
    got = sorted(rejections(report))
    check(
        "R2-1",
        f"a failed C1 row never joins c1 ({got})",
        got == [("line:4", "incomplete_group"), ("line:4", "unmapped_role")] and len(accepted) == 1,
    )
    # a failed row of a group that already closed fails the import
    rows = [
        ["A", "1", "u", "q"],
        ["A", "2", "a", "r"],
        ["B", "1", "u", "q"],
        ["B", "2", "a", "r"],
        ["A", "3", "x", "?"],
    ]
    report, _ = run_csv(csv_bytes(rows), grouped())
    check(
        "R2-2",
        f"a failed row of a closed group is non_contiguous_group ({report.state}/{report.failure_code})",
        (report.state, report.failure_code) == ("failed", "non_contiguous_group"),
    )
    # grouped_row_key reads the key, and only for a grouped mapping
    mapping = ic.mapping_from_object(grouped())
    headers = ["conv", "turn", "who", "text"]
    check(
        "R2-3",
        "the key of a readable row",
        ic.grouped_row_key(mapping, ["c9", "1", "x", "?"], headers) == "c9",
    )
    check(
        "R2-4",
        "an empty key is unreadable",
        ic.grouped_row_key(mapping, ["", "1", "x", "?"], headers) is None,
    )
    check(
        "R2-5",
        "a non-text key is unreadable",
        ic.grouped_row_key(
            ic.mapping_from_object(
                {
                    **grouped(),
                    "group_rows": {**grouped()["group_rows"], "group_key": {"key": "conv"}},
                }
            ),
            {"conv": 5},
        )
        is None,
    )
    check(
        "R2-7",
        "a whitespace key is a key, read verbatim",
        ic.grouped_row_key(mapping, [" ", "1", "x", "?"], headers) == " ",
    )
    rows = [
        ["C1", "1", "u", "q"],
        ["C1", "2", "x", "?"],
        ["D1", "1", "u", "q"],
        ["D1", "2", "a", "r"],
    ]
    report, accepted = run_csv(csv_bytes(rows), grouped())
    got = sorted(rejections(report))
    check(
        "R2-8",
        f"a failed row of the open group C1 quarantines C1, the import goes on ({report.state}/{got})",
        report.state != "failed" and ("line:2", "incomplete_group") in got and len(accepted) == 1,
    )
    plain = ic.mapping_from_object(
        {"shape": "prompt_completion", "prompt": {"index": 0}, "completion": {"index": 1}}
    )
    check("R2-6", "a plain mapping has no group key", ic.grouped_row_key(plain, ["q", "a"]) is None)


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        try:
            test()
        except Exception as exc:  # noqa: BLE001 -- a crash is a failing check, never a pass
            FAILURES.append(
                f"FAIL [FTF-H011-P5] {test.__name__} raised {type(exc).__name__}: {exc}"
            )
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed")
        for failure in FAILURES:
            print(f"  {failure}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
