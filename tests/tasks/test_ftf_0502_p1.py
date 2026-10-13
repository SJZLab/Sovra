#!/usr/bin/env python3
"""FTF-0502-P1 -- dataset import common contract and verified-source reader.

Runnable without a database or network. `workers/data/import_common.py` is pure;
`apps/api/app/datasets/dataset_object_source.py` is exercised here over SQLite
with a fake object store (tenant scoping faked the same way test_ftf_0501_p1.py
does), and against PostgreSQL 16 + SeaweedFS by
tests/integration/test_ftf_0502_p1_dataset_source_postgres_s3.py.

Criteria (each test names the clause it pins):

  MAP   MappingSpec: exact types, exact allow-list per shape, no defaults
  PTR   JSON pointer grammar and exact-key resolution
  ROLE  role_map is an exact lookup (no casefold / strip / NFKC)
  LIM   limits are lower-only; max-1 / max / max+1 at every enforcement point
  JSON  strict_loads: duplicate keys, NaN, 1e999, depth, digits, surrogates
  READ  ChunkReader: size, sha, source failure = blocked, deadline
  CAND  candidates pass FTF-0508 validate_record_batch with 0 issues
  DET   determinism across runs and PYTHONHASHSEED
  LEAK  no source text, key or FTF-0506 message in any rejection or report
  REP   ImportReport invariants, closed catalogs, rejection budget
  PREV  preview shares the full-import path and stops at 20
  SRC   dataset_object_source: scoping, held-out, one GET, no LIST, no key

Run directly: python3 tests/tasks/test_ftf_0502_p1.py
"""

from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import inspect
import json
import math
import subprocess
import sys
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import import_test_support as support  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from workers.data import import_common as ic  # noqa: E402
from workers.data.canonical_conversation_normalizer import SCHEMA_VERSION  # noqa: E402
from workers.data.record_schema_validator import (  # noqa: E402
    MAX_CANONICAL_RECORD_BYTES,
    validate_record_batch,
)

from app.datasets import dataset_object_source as source_module  # noqa: E402
from app.datasets import dataset_upload_store as store  # noqa: E402
from app.datasets.direct_object_storage_upload import UploadStorageUnavailableError  # noqa: E402
from app.services.storage_semantics_client import S3Error  # noqa: E402

FAILURES: list[str] = []
SHA_X = "a" * 64


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def raises(exc_type: type[BaseException], call: Any, *args: Any, **kwargs: Any) -> Any:
    """The exception raised by `call`, or None if it returned (or raised another type)."""
    try:
        call(*args, **kwargs)
    except exc_type as exc:
        return exc
    except Exception:  # noqa: BLE001 - any other type is a different failure
        return None
    return None


# --- mapping fixtures --------------------------------------------------------------

ROLE_MAP = {"human": "user", "gpt": "assistant", "sys": "system", "fn": "tool"}
META_KEYS = ("language", "task_category", "consent", "quality_label", "source_record_id")


def _metadata(kind: str) -> dict[str, Any]:
    names = (*META_KEYS, "event_timestamp")
    if kind == "key":
        return {n: {"key": f"m_{n}"} for n in names}
    return {n: {"index": 10 + i} for i, n in enumerate(names)}


FORMATS: dict[str, Any] = {
    "csv": {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0},
    "xlsx": {"sheet_name": "Sheet1", "header_row": 0},
    "json": {"container": "/rows", "ignored_top_level_keys": ["_meta"]},
}


def messages_doc() -> dict[str, Any]:
    return {
        "shape": "messages",
        "messages_pointer": "/conversation/turns",
        "role_key": "from",
        "content_key": "value",
        "tool_calls_key": "calls",
        "tool_call_id_key": "call_id",
        "name_key": "tool",
        "role_map": dict(ROLE_MAP),
        "on_unmapped_message_keys": "reject",
        "group_key": {"key": "conv"},
        "metadata": _metadata("key"),
        **copy.deepcopy(FORMATS),
    }


def messages_json_doc() -> dict[str, Any]:
    return {
        "shape": "messages_json",
        "messages": {"header": "messages"},
        "role_key": "role",
        "content_key": "content",
        "tool_calls_key": "tool_calls",
        "tool_call_id_key": "tool_call_id",
        "name_key": "name",
        "role_map": {"user": "user", "assistant": "assistant", "tool": "tool", "system": "system"},
        "on_unmapped_message_keys": "ignore",
        "group_key": {"letters": "C"},
        "metadata": _metadata("index"),
        **copy.deepcopy(FORMATS),
    }


def prompt_completion_doc() -> dict[str, Any]:
    return {
        "shape": "prompt_completion",
        "prompt": {"key": "p"},
        "completion": {"key": "c"},
        "system": {"key": "s"},
        "group_key": {"index": 5},
        "metadata": _metadata("key"),
        **copy.deepcopy(FORMATS),
    }


BASES = {
    "messages": messages_doc,
    "messages_json": messages_json_doc,
    "prompt_completion": prompt_completion_doc,
}

# required keys by path ("" = top level), per shape
REQUIRED: dict[str, dict[str, set[str]]] = {
    "messages": {
        "": {
            "shape",
            "messages_pointer",
            "role_key",
            "content_key",
            "role_map",
            "on_unmapped_message_keys",
        },
        "csv": {"encoding", "delimiter", "has_header", "header_row"},
        "xlsx": {"sheet_name", "header_row"},
        "json": {"container", "ignored_top_level_keys"},
    },
    "messages_json": {
        "": {
            "shape",
            "messages",
            "role_key",
            "content_key",
            "role_map",
            "on_unmapped_message_keys",
        },
        "csv": {"encoding", "delimiter", "has_header", "header_row"},
        "xlsx": {"sheet_name", "header_row"},
        "json": {"container", "ignored_top_level_keys"},
    },
    "prompt_completion": {
        "": {"shape", "prompt", "completion"},
        "csv": {"encoding", "delimiter", "has_header", "header_row"},
        "xlsx": {"sheet_name", "header_row"},
        "json": {"container", "ignored_top_level_keys"},
    },
}


def mapping_of(doc: dict[str, Any]) -> ic.ImportMapping:
    return ic.mapping_from_object(copy.deepcopy(doc))


def is_invalid(doc: object) -> bool:
    exc = raises(ic.MappingInvalidError, ic.mapping_from_object, doc)
    return exc is not None and exc.code == "mapping_invalid"


def walk(node: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    yield path, node
    if isinstance(node, dict):
        for key, child in node.items():
            yield from walk(child, (*path, key))
    elif isinstance(node, list):
        for i, child in enumerate(node):
            yield from walk(child, (*path, str(i)))


def get_at(doc: Any, path: tuple[str, ...]) -> Any:
    for token in path:
        doc = doc[int(token)] if isinstance(doc, list) else doc[token]
    return doc


def set_at(doc: Any, path: tuple[str, ...], value: Any) -> None:
    parent = get_at(doc, path[:-1])
    if isinstance(parent, list):
        parent[int(path[-1])] = value
    else:
        parent[path[-1]] = value


def dumps_with_duplicate(node: Any, target: tuple[str, ...], path: tuple[str, ...] = ()) -> str:
    if isinstance(node, dict):
        parts = []
        for key, child in node.items():
            rendered = dumps_with_duplicate(child, target, (*path, key))
            parts.append(f"{json.dumps(key)}:{rendered}")
            if path == target and len(parts) == 1:
                parts.append(f"{json.dumps(key)}:{rendered}")
        return "{" + ",".join(parts) + "}"
    if isinstance(node, list):
        items = [dumps_with_duplicate(c, target, (*path, str(i))) for i, c in enumerate(node)]
        return "[" + ",".join(items) + "]"
    return json.dumps(node)


# --- MAP ----------------------------------------------------------------------------


def test_map_valid_bases_parse_and_hash_canonically() -> None:
    for shape, make in BASES.items():
        doc = make()
        parsed = ic.parse_mapping(json.dumps(doc))
        check("MAP-1", f"the full {shape} mapping parses", parsed.shape == shape)
        check(
            "MAP-1",
            f"{shape}: mapping_sha256 is the sha256 of canonical JSON",
            parsed.mapping_sha256
            == hashlib.sha256(
                json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        )
        shuffled = json.dumps(dict(reversed(list(doc.items()))), indent=3)
        check(
            "MAP-1",
            f"{shape}: key order and whitespace do not change mapping_sha256",
            ic.parse_mapping(shuffled).mapping_sha256 == parsed.mapping_sha256,
        )
        changed = copy.deepcopy(doc)
        changed["group_key"] = {"key": "other"}
        check(
            "MAP-1",
            f"{shape}: a changed value changes mapping_sha256",
            mapping_of(changed).mapping_sha256 != parsed.mapping_sha256,
        )
        before = copy.deepcopy(doc)
        ic.mapping_from_object(doc)
        check("MAP-1", f"{shape}: parsing does not mutate its input", doc == before)


def test_map_optional_keys_are_optional() -> None:
    for shape, make in BASES.items():
        required = REQUIRED[shape]
        for path_key, names in required.items():
            del path_key, names
        doc = make()
        optional_top = set(doc) - REQUIRED[shape][""]
        for key in sorted(optional_top):
            trimmed = make()
            del trimmed[key]
            check("MAP-2", f"{shape}: optional key {key!r} may be omitted", not is_invalid(trimmed))
        bare = {k: v for k, v in make().items() if k in REQUIRED[shape][""]}
        check("MAP-2", f"{shape}: the minimal mapping parses", not is_invalid(bare))
        if shape != "prompt_completion":
            for key in ("tool_calls_key", "tool_call_id_key", "name_key"):
                check("MAP-2", f"{shape}: {key} is optional", key not in REQUIRED[shape][""])
        for name in (*META_KEYS, "event_timestamp"):
            trimmed = make()
            del trimmed["metadata"][name]
            check("MAP-2", f"{shape}: metadata.{name} may be omitted", not is_invalid(trimmed))


def test_map_strict_table_every_key_every_wrong_type() -> None:
    attempted = 0
    for shape, make in BASES.items():
        base = make()
        for path, node in walk(base):
            if not path:
                continue
            original = node
            for bad in support.wrong_type_values(original):
                doc = make()
                set_at(doc, path, bad)
                attempted += 1
                check(
                    "MAP-3",
                    f"{shape}: {'/'.join(path)} = {type(bad).__name__}:{bad!r} is mapping_invalid",
                    is_invalid(doc),
                )
    check("MAP-3", f"the strict table ran ({attempted} cases)", attempted > 1000)


def test_map_missing_and_extra_keys_at_every_object() -> None:
    for shape, make in BASES.items():
        base = make()
        for path, node in walk(base):
            if not isinstance(node, dict):
                continue
            doc = make()
            get_at(doc, path)["zz_extra"] = 1
            check("MAP-4", f"{shape}: extra key at {'/'.join(path) or 'top'}", is_invalid(doc))
            if path in (("role_map",),):
                continue  # arbitrary keys are the point of a role_map
        for dotted, names in REQUIRED[shape].items():
            path = tuple(p for p in dotted.split("/") if p)
            for name in names:
                doc = make()
                del get_at(doc, path)[name]
                check("MAP-4", f"{shape}: missing {dotted or 'top'}.{name}", is_invalid(doc))
        for path, node in walk(base):
            if len(path) >= 2 and path[0] == "metadata" and isinstance(node, dict):
                doc = make()
                get_at(doc, path).clear()
                check("MAP-4", f"{shape}: empty slot {'/'.join(path)}", is_invalid(doc))
        two = make()
        two["group_key"] = {"key": "a", "header": "b"}
        check("MAP-4", f"{shape}: a slot with two addresses", is_invalid(two))


def test_map_duplicate_keys_and_nan_in_document_text() -> None:
    for shape, make in BASES.items():
        base = make()
        for path, node in walk(base):
            if isinstance(node, dict) and node:
                text = dumps_with_duplicate(base, path)
                exc = raises(ic.MappingInvalidError, ic.parse_mapping, text)
                check(
                    "MAP-5",
                    f"{shape}: duplicate key under {'/'.join(path) or 'top'} is mapping_invalid",
                    exc is not None,
                )
        for path, node in walk(base):
            if type(node) is int:
                doc = copy.deepcopy(base)
                set_at(doc, path, "__NAN__")
                for literal in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999"):
                    text = json.dumps(doc).replace('"__NAN__"', literal)
                    check(
                        "MAP-5",
                        f"{shape}: {literal} at {'/'.join(path)} is mapping_invalid",
                        raises(ic.MappingInvalidError, ic.parse_mapping, text) is not None,
                    )


def test_map_document_level_errors() -> None:
    text = json.dumps(prompt_completion_doc())
    for bad in (None, 5, b"{}", text.encode(), ["x"], support.StrSub(text)):
        check(
            "MAP-6",
            f"parse_mapping({type(bad).__name__}) is mapping_invalid",
            raises(ic.MappingInvalidError, ic.parse_mapping, bad) is not None,
        )
    for bad_text in ("", " ", "[]", '"x"', "1", "null", "{", "{}", "[" * 9 + "]" * 9, text + "x"):
        check(
            "MAP-6",
            f"parse_mapping({bad_text[:12]!r}) is mapping_invalid",
            raises(ic.MappingInvalidError, ic.parse_mapping, bad_text) is not None,
        )
    huge = json.dumps({**prompt_completion_doc(), "pad": "x" * (ic.MAX_MAPPING_BYTES + 1)})
    exc = raises(ic.MappingInvalidError, ic.parse_mapping, huge)
    check("MAP-6", "an oversize mapping document is refused", exc is not None)
    for value in (b"x", {1: 2}, {"shape": "messages", 1: 2}):
        check("MAP-6", f"{value!r} is not a mapping", is_invalid(value))
    errors = [raises(ic.MappingInvalidError, ic.mapping_from_object, d) for d in (None, [], {})]
    check(
        "MAP-6",
        "every refusal carries code mapping_invalid and a catalogued reason",
        all(e is not None and e.code == "mapping_invalid" for e in errors)
        and all(e.reason in ic.MAPPING_INVALID_REASONS for e in errors if e is not None),
    )


def test_map_unsupported_and_unknown_shapes() -> None:
    for shape in ("grouped_rows", "grouped_multi_row"):
        exc = raises(ic.MappingUnsupportedError, ic.mapping_from_object, {"shape": shape})
        check(
            "MAP-7",
            f"{shape} is mapping_unsupported",
            exc is not None and exc.code == "mapping_unsupported",
        )
        check(
            "MAP-7",
            f"{shape} is not mapping_invalid",
            raises(ic.MappingInvalidError, ic.mapping_from_object, {"shape": shape}) is None,
        )
    for shape in ("", "Messages", "messages ", "unknown", "prompt-completion"):
        check("MAP-7", f"shape {shape!r} is mapping_invalid", is_invalid({"shape": shape}))
    doc = prompt_completion_doc()
    doc["role_map"] = {"a": "user"}
    check("MAP-7", "prompt_completion refuses message keys", is_invalid(doc))
    doc = messages_doc()
    doc["prompt"] = {"key": "p"}
    check("MAP-7", "messages refuses prompt/completion keys", is_invalid(doc))


def test_map_on_unmapped_message_keys_has_no_default() -> None:
    doc = messages_doc()
    del doc["on_unmapped_message_keys"]
    check("MAP-8", "on_unmapped_message_keys is required (no default)", is_invalid(doc))
    for value in ("Reject", "keep", "", "reject ", None, True):
        doc = messages_doc()
        doc["on_unmapped_message_keys"] = value
        check("MAP-8", f"on_unmapped_message_keys={value!r} is refused", is_invalid(doc))
    for value in ("reject", "ignore"):
        doc = messages_doc()
        doc["on_unmapped_message_keys"] = value
        check("MAP-8", f"on_unmapped_message_keys={value!r} is accepted", not is_invalid(doc))


def test_map_metadata_keys_are_closed() -> None:
    for name in ("purpose", "split", "dataset_purpose", "Language", "quality_status", "", "x"):
        doc = prompt_completion_doc()
        doc["metadata"][name] = {"key": "k"}
        check("MAP-9", f"metadata key {name!r} is not allowed", is_invalid(doc))
    parsed = mapping_of(prompt_completion_doc())
    check(
        "MAP-9",
        "the allowed metadata keys are exactly the six named ones",
        {n for n, _ in parsed.metadata_slots}
        == {
            "language",
            "task_category",
            "consent",
            "quality_label",
            "source_record_id",
            "event_timestamp",
        },
    )


def test_map_slot_addresses() -> None:
    cases_ok = [
        {"key": "p"},
        {"key": "a b é"},
        {"header": "Prompt Text"},
        {"index": 0},
        {"index": 16383},
        {"letters": "A"},
        {"letters": "ZZ"},
        {"letters": "XFD"},
    ]
    cases_bad = [
        {"index": -1},
        {"index": 16384},
        {"index": "0"},
        {"letters": "a"},
        {"letters": "A1"},
        {"letters": "ABCD"},
        {"letters": ""},
        {"key": ""},
        {"key": "x" * 257},
        {"key": "\ud800"},
        {"column": "A"},
        {},
        "A",
        3,
    ]
    for slot in cases_ok:
        doc = prompt_completion_doc()
        doc["prompt"] = slot
        check("MAP-10", f"slot {slot!r} is accepted", not is_invalid(doc))
    for slot in cases_bad:
        doc = prompt_completion_doc()
        doc["prompt"] = slot
        check("MAP-10", f"slot {slot!r} is refused", is_invalid(doc))


def test_map_format_blocks() -> None:
    for delimiter in ("", ",,", None):
        doc = messages_doc()
        doc["csv"]["delimiter"] = delimiter
        check("MAP-11", f"csv delimiter {delimiter!r} refused", is_invalid(doc))
    for field_name, value in (("header_row", -1), ("header_row", 1_000_001)):
        doc = messages_doc()
        doc["csv"][field_name] = value
        doc["xlsx"][field_name] = value
        check("MAP-11", f"header_row {value} refused", is_invalid(doc))
    doc = messages_doc()
    doc["json"]["ignored_top_level_keys"] = ["a", 1]
    check("MAP-11", "ignored_top_level_keys entries must be strings", is_invalid(doc))
    doc = messages_doc()
    doc["json"]["ignored_top_level_keys"] = [f"k{i}" for i in range(ic.MAX_ROLE_MAP_ENTRIES + 1)]
    check("MAP-11", "ignored_top_level_keys is bounded", is_invalid(doc))
    doc = messages_doc()
    doc["json"]["container"] = "rows"
    check("MAP-11", "json.container must be a JSON pointer", is_invalid(doc))


# --- PTR ----------------------------------------------------------------------------


def _pointer_doc(pointer: Any) -> dict[str, Any]:
    doc = messages_doc()
    doc["messages_pointer"] = pointer
    return doc


def test_ptr_grammar_table() -> None:
    ok = ["", "/a", "/a/0", "/a/b/c", "/a~0b", "/a~1b", "/é", "/é", "/0", "/a/10", "/ключ"]
    bad = [
        "a",
        "/",
        "/a//b",
        "/a/",
        "/-",
        "/a/-",
        "/01",
        "/a/007",
        "/a~",
        "/a~2",
        "/a~x",
        "/~",
        "/1234567890",
        "/" + "/".join("a" * 1 for _ in range(ic.MAX_POINTER_TOKENS + 1)),
        "/" + "a" * 300,
        "/\ud800",
        5,
        None,
        ["a"],
    ]
    for pointer in ok:
        check("PTR-1", f"pointer {pointer!r} is accepted", not is_invalid(_pointer_doc(pointer)))
    for pointer in bad:
        check(
            "PTR-1", f"pointer {str(pointer)[:20]!r} is refused", is_invalid(_pointer_doc(pointer))
        )
    check(
        "PTR-1",
        "exactly MAX_POINTER_TOKENS tokens are accepted",
        not is_invalid(_pointer_doc("/" + "/".join("a" for _ in range(ic.MAX_POINTER_TOKENS)))),
    )
    check(
        "PTR-1",
        "a nine-digit index is accepted",
        not is_invalid(_pointer_doc("/a/" + "9" * ic.MAX_POINTER_INDEX_DIGITS)),
    )


def test_ptr_tokens_and_resolution() -> None:
    parsed = mapping_of(_pointer_doc("/a~0b/c~1d/0/é"))
    check(
        "PTR-2", "~0 and ~1 unescape in order", parsed.messages_pointer == ("a~b", "c/d", "0", "é")
    )
    check(
        "PTR-2",
        "the empty pointer is the root",
        mapping_of(_pointer_doc("")).messages_pointer == (),
    )
    doc = {"a~b": {"c/d": [{"é": [1, 2]}]}}
    check(
        "PTR-2",
        "escapes resolve against the exact keys",
        ic.resolve_pointer(doc, ("a~b", "c/d", "0", "é")) == (True, [1, 2]),
    )
    nfc, nfd = "é", "é"
    check("PTR-2", "NFC key is found by NFC", ic.resolve_pointer({nfc: 1}, (nfc,)) == (True, 1))
    check(
        "PTR-2",
        "NFD token does not find an NFC key",
        ic.resolve_pointer({nfc: 1}, (nfd,)) == (False, None),
    )
    check(
        "PTR-2",
        "NFC token does not find an NFD key",
        ic.resolve_pointer({nfd: 1}, (nfc,)) == (False, None),
    )
    check(
        "PTR-2",
        "casefolded token does not find the key",
        ic.resolve_pointer({"A": 1}, ("a",)) == (False, None),
    )
    check(
        "PTR-2",
        "a numeric token is a key on an object",
        ic.resolve_pointer({"0": "k"}, ("0",)) == (True, "k"),
    )
    check(
        "PTR-2",
        "a numeric token indexes a list",
        ic.resolve_pointer(["x", "y"], ("1",)) == (True, "y"),
    )
    check(
        "PTR-2",
        "an index past the end is not found",
        ic.resolve_pointer(["x"], ("1",)) == (False, None),
    )
    check(
        "PTR-2",
        "a non-numeric token on a list is not found",
        ic.resolve_pointer(["x"], ("a",)) == (False, None),
    )
    check(
        "PTR-2",
        "a token into a scalar is not found",
        ic.resolve_pointer({"a": 5}, ("a", "b")) == (False, None),
    )
    check("PTR-2", "a missing key is not found", ic.resolve_pointer({}, ("a",)) == (False, None))
    check(
        "PTR-2",
        "unicode digits are not list indices",
        ic.resolve_pointer(["x", "y"], ("١",)) == (False, None),
    )
    check("PTR-2", "the root resolves to the document", ic.resolve_pointer([1], ()) == (True, [1]))
    check(
        "PTR-2",
        "a negative-looking token is not an index",
        ic.resolve_pointer(["x"], ("-1",)) == (False, None),
    )


# --- ROLE ---------------------------------------------------------------------------


def _role_mapping() -> ic.ImportMapping:
    doc = messages_doc()
    doc["role_map"] = {"user": "user", "assistant": "assistant"}
    return mapping_of(doc)


def _conv(role: str, content: str = "hi") -> dict[str, Any]:
    return {"conversation": {"turns": [{"from": role, "value": content}]}}


def _role_outcome(mapping: ic.ImportMapping, role: Any) -> str | None:
    try:
        row = ic.apply_mapping(mapping, {"conversation": {"turns": [{"from": role, "value": "x"}]}})
    except ic.RowMappingError as exc:
        return exc.code
    return str(row.raw_record["messages"][0]["role"])  # type: ignore[index]


def test_role_exact_lookup_accepts_only_the_exact_key() -> None:
    mapping = _role_mapping()
    check("ROLE-1", "'user' maps to user", _role_outcome(mapping, "user") == "user")
    check(
        "ROLE-1",
        "'assistant' maps to assistant",
        _role_outcome(mapping, "assistant") == "assistant",
    )
    refused = 0
    for base in ("user", "assistant"):
        for label, variant in support.unicode_variants(base):
            refused += 1
            outcome = _role_outcome(mapping, variant)
            check(
                "ROLE-1",
                f"{base}: variant {label} is unmapped_role (got {outcome})",
                outcome == "unmapped_role",
            )
    check("ROLE-1", f"the generated variant set ran ({refused} variants)", refused > 100)
    for role in (None, 1, True, ["user"], {"user": 1}, support.StrSub("user"), b"user"):
        outcome = _role_outcome(mapping, role)
        expected = "mapping_type_mismatch" if type(role) is not support.StrSub else None
        if type(role) is support.StrSub:
            # a str subclass is not an exact str: it is refused, never mapped
            check(
                "ROLE-1",
                "a str-subclass role is not mapped",
                outcome in ("mapping_type_mismatch", "unmapped_role"),
            )
        else:
            check("ROLE-1", f"role {role!r} is mapping_type_mismatch", outcome == expected)


def _assign_role(mapping: ic.ImportMapping) -> None:
    mapping.role_map["x"] = "user"  # type: ignore[index]


def test_role_map_values_are_exactly_the_ftf_0506_roles() -> None:
    from workers.data.canonical_conversation_normalizer import ALLOWED_ROLES

    for role in sorted(ALLOWED_ROLES):
        doc = messages_doc()
        doc["role_map"] = {"x": role}
        check("ROLE-2", f"role_map value {role!r} is accepted", not is_invalid(doc))
    for _label, variant in support.unicode_variants("assistant")[:60]:
        doc = messages_doc()
        doc["role_map"] = {"x": variant}
        check("ROLE-2", f"role_map value {variant!r} is refused", is_invalid(doc))
    for value in ("function", "", "System", 1, None, ["user"]):
        doc = messages_doc()
        doc["role_map"] = {"x": value}
        check("ROLE-2", f"role_map value {value!r} is refused", is_invalid(doc))
    for role_map in (
        {},
        {"": "user"},
        {"a" * 257: "user"},
        {f"r{i}": "user" for i in range(65)},
        [],
        "user",
    ):
        doc = messages_doc()
        doc["role_map"] = role_map
        check("ROLE-2", f"role_map {str(role_map)[:20]!r} is refused", is_invalid(doc))
    check(
        "ROLE-2",
        "the role_map parsed from a document cannot be mutated",
        raises(TypeError, _assign_role, mapping_of(messages_doc())) is not None,
    )


# --- LIM ----------------------------------------------------------------------------


def test_lim_defaults_are_the_section_67_3_values() -> None:
    d = ic.DEFAULT_LIMITS
    check("LIM-1", "max_source_bytes is 10 GiB", d.max_source_bytes == 10 * 1024**3)
    check(
        "LIM-1",
        "max_record_bytes is FTF-0508's constant",
        d.max_record_bytes == MAX_CANONICAL_RECORD_BYTES,
    )
    check("LIM-1", "max_depth is 64", d.max_depth == 64)
    check("LIM-1", "max_rejections is 10,000", d.max_rejections == 10_000)
    check("LIM-1", "preview_max_records is 20", d.preview_max_records == 20)
    check("LIM-1", "deadline_seconds default is positive", d.deadline_seconds > 0)
    text = Path(ic.__file__).read_text(encoding="utf-8")
    check(
        "LIM-1",
        "the 2 MiB record limit is imported, not re-declared",
        "2097152" not in text
        and "2 * 1024 * 1024" not in text
        and "MAX_CANONICAL_RECORD_BYTES" in text,
    )


def test_lim_lower_only_table() -> None:
    d = ic.DEFAULT_LIMITS
    for name in (
        "max_source_bytes",
        "max_record_bytes",
        "max_depth",
        "max_rejections",
        "preview_max_records",
    ):
        default = getattr(d, name)
        check(
            "LIM-2",
            f"{name}: default accepted",
            getattr(replace(d, **{name: default}), name) == default,
        )
        check(
            "LIM-2",
            f"{name}: default-1 accepted",
            getattr(replace(d, **{name: default - 1}), name) == default - 1,
        )
        check("LIM-2", f"{name}: 1 accepted", getattr(replace(d, **{name: 1}), name) == 1)
        for bad in (
            default + 1,
            default * 2,
            0,
            -1,
            True,
            1.0,
            None,
            "1",
            float(default),
            support.IntSub(1),
        ):
            check(
                "LIM-2",
                f"{name}={bad!r} is refused",
                raises(ValueError, replace, d, **{name: bad}) is not None,
            )
    check(
        "LIM-2",
        "deadline: default accepted",
        replace(d, deadline_seconds=d.deadline_seconds).deadline_seconds == d.deadline_seconds,
    )
    check(
        "LIM-2",
        "deadline: 0.001 accepted",
        replace(d, deadline_seconds=0.001).deadline_seconds == 0.001,
    )
    check(
        "LIM-2",
        "deadline: an int is accepted",
        replace(d, deadline_seconds=5).deadline_seconds == 5,
    )
    for bad in (d.deadline_seconds + 1, 0, -1, math.nan, math.inf, True, None, "5"):
        check(
            "LIM-2",
            f"deadline_seconds={bad!r} is refused",
            raises(ValueError, replace, d, deadline_seconds=bad) is not None,
        )


def _reader(data: bytes, **kwargs: Any) -> ic.ChunkReader:
    chunks = kwargs.pop("chunks", None) or support.split_chunks(data, 4)
    return ic.ChunkReader(
        chunks,
        expected_size=kwargs.pop("expected_size", len(data)),
        expected_sha256=kwargs.pop("expected_sha256", support.sha256_hex(data)),
        **kwargs,
    )


def _drain(reader: ic.ChunkReader) -> ic.ImportAborted | None:
    try:
        for _ in reader:
            pass
    except ic.ImportAborted as exc:
        return exc
    return None


def test_lim_source_bytes_boundary() -> None:
    data = b"abcdefghij"
    for limit, expect in ((9, "source_too_large"), (10, None), (11, None)):
        reader = _reader(data, limits=replace(ic.DEFAULT_LIMITS, max_source_bytes=limit))
        outcome = _drain(reader)
        check(
            "LIM-3",
            f"max_source_bytes={limit} for a {len(data)}-byte source -> {expect}",
            (outcome.failure_code if outcome else None) == expect,
        )
        if expect:
            check(
                "LIM-3",
                "an oversize source is failed, not blocked",
                outcome is not None and outcome.state == "failed",
            )
    declared = _reader(
        data, limits=replace(ic.DEFAULT_LIMITS, max_source_bytes=9), expected_size=100
    )
    outcome = _drain(declared)
    check(
        "LIM-3",
        "a declared size over the limit fails before reading",
        outcome is not None
        and outcome.failure_code == "source_too_large"
        and declared.bytes_read == 0,
    )


def test_lim_rejection_budget_boundary() -> None:
    mapping = mapping_of(prompt_completion_doc())
    for budget in (3, 1):
        limits = replace(ic.DEFAULT_LIMITS, max_rejections=budget)
        for count, expect in (
            (budget - 1, "completed"),
            (budget, "completed"),
            (budget + 1, "failed"),
        ):
            data = b"x" * 8
            events = [
                ic.RowRejection(f"line:{i}", "unmapped_role", "messages[0]") for i in range(count)
            ]
            report = ic.run_import(
                iter(events),
                _consume(_reader(data)),
                importer="jsonl",
                importer_version="1",
                mapping=mapping,
                dataset_purpose="training",
                expected_sha256=SHA_X,
                limits=limits,
            )
            check(
                "LIM-4",
                f"budget {budget}, {count} rejections -> {expect} (got {report.state})",
                report.state == expect,
            )
            if expect == "failed":
                check(
                    "LIM-4",
                    "too many rejections is failed/too_many_rejections",
                    report.failure_code == "too_many_rejections",
                )
                check(
                    "LIM-4",
                    "the report lists exactly the budget",
                    len(report.rejections) == budget and report.records_rejected == budget,
                )
            else:
                check(
                    "LIM-4",
                    "within budget every rejection is listed",
                    len(report.rejections) == count,
                )


def _consume(reader: ic.ChunkReader) -> ic.ChunkReader:
    """A reader whose bytes were already consumed by the 'importer' (events are synthetic)."""
    for _ in reader:
        pass
    return reader


# --- JSON ---------------------------------------------------------------------------


def _code(data: Any, **kwargs: Any) -> str | None:
    try:
        ic.strict_loads(data, **kwargs)
    except ic.StrictJsonError as exc:
        return exc.code
    return None


def _nest(depth: int, kind: str) -> str:
    if kind == "array":
        return "[" * depth + "1" + "]" * depth
    if kind == "object":
        return '{"a":' * depth + "1" + "}" * depth
    return _mixed(depth)


def _mixed(depth: int) -> str:
    opens, closes = [], []
    for i in range(depth):
        if i % 2 == 0:
            opens.append("[")
            closes.append("]")
        else:
            opens.append('{"a":')
            closes.append("}")
    return "".join(opens) + "1" + "".join(reversed(closes))


def test_json_depth_boundary_and_pre_scan() -> None:
    for kind in ("array", "object", "mixed"):
        for depth, expect in ((63, None), (64, None), (65, "depth_exceeded")):
            check(
                "JSON-1", f"{kind} depth {depth} -> {expect}", _code(_nest(depth, kind)) == expect
            )
    for depth in (100_000, 2_000_000):
        for kind in ("array", "object"):
            check(
                "JSON-1",
                f"{kind} depth {depth} is depth_exceeded (no RecursionError)",
                _code(_nest(depth, kind)) == "depth_exceeded",
            )
        check(
            "JSON-1",
            f"unclosed depth {depth} is depth_exceeded",
            _code("[" * depth) == "depth_exceeded",
        )
    check(
        "JSON-1",
        "bytes input is scanned the same way",
        _code(("[" * 65 + "]" * 65).encode()) == "depth_exceeded",
    )
    check("JSON-1", "brackets inside strings do not count", _code('["' + "[" * 5000 + '"]') is None)
    check(
        "JSON-1",
        "escaped quotes inside strings do not end them",
        _code('["\\"' + "[" * 5000 + '"]') is None,
    )
    check(
        "JSON-1", "a backslash before the closing quote is handled", _code('["\\\\"]' + "") is None
    )
    check(
        "JSON-1",
        "a lower max_depth applies",
        _code("[[1]]", max_depth=1) == "depth_exceeded" and _code("[[1]]", max_depth=2) is None,
    )
    check("JSON-1", "depth 0 containers: a scalar is fine", _code("1", max_depth=1) is None)
    for bad in (0, -1, True, 1.5, None, "64"):
        check(
            "JSON-1",
            f"max_depth={bad!r} is refused",
            raises(ValueError, ic.strict_loads, "1", max_depth=bad) is not None,
        )
    check(
        "JSON-1",
        "the default depth is 64",
        _code(_nest(64, "array")) is None and _code(_nest(65, "array")) == "depth_exceeded",
    )


def test_json_duplicate_keys_at_every_level() -> None:
    cases = [
        '{"a":1,"a":2}',
        '{"x":{"a":1,"a":2}}',
        '[{"a":1,"a":2}]',
        '{"x":[{"y":{"a":1,"a":1}}]}',
        '{"a":1,"b":2,"a":3}',
        '{"\\u0061":1,"a":2}',
    ]
    for text in cases:
        check("JSON-2", f"duplicate key in {text}", _code(text) == "duplicate_key")
    check(
        "JSON-2", "distinct keys that differ only by case are fine", _code('{"a":1,"A":2}') is None
    )
    check("JSON-2", "NFC and NFD keys are distinct keys", _code('{"é":1,"é":2}') is None)
    check("JSON-2", "the empty key twice is a duplicate", _code('{"":1,"":2}') == "duplicate_key")
    parsed = ic.strict_loads('{"a":{"b":[1,2,{"c":null}]},"d":true}')
    check(
        "JSON-2",
        "a valid document round-trips",
        parsed == {"a": {"b": [1, 2, {"c": None}]}, "d": True},
    )


def test_json_non_finite_numbers() -> None:
    for text in (
        "NaN",
        "Infinity",
        "-Infinity",
        "[NaN]",
        '{"a":Infinity}',
        "1e999",
        "-1e999",
        "[1E400]",
        '{"a":{"b":-1e309}}',
        "1e99999999",
    ):
        check("JSON-3", f"{text} is non_finite_number", _code(text) == "non_finite_number")
    for text in ("1e-999", "0.0", "-0.0", "1.5", "1e308", "123456789012345678901234567890.5"):
        check("JSON-3", f"{text} is accepted", _code(text) is None)
    check("JSON-3", "1e-999 is zero, not an error", ic.strict_loads("1e-999") == 0.0)


def test_json_integer_digit_limit_is_independent_of_the_host() -> None:
    limit = ic.MAX_JSON_INT_DIGITS
    saved = sys.get_int_max_str_digits()
    try:
        for host in (saved, 0):
            sys.set_int_max_str_digits(host)
            for digits, expect in (
                (limit - 1, None),
                (limit, None),
                (limit + 1, "number_out_of_range"),
            ):
                text = "9" * digits
                check(
                    "JSON-4",
                    f"host limit {host}: {digits}-digit int -> {expect}",
                    _code(text) == expect,
                )
                check(
                    "JSON-4",
                    f"host limit {host}: negative {digits}-digit int -> {expect}",
                    _code("-" + text) == expect,
                )
                check(
                    "JSON-4",
                    f"host limit {host}: nested {digits}-digit int -> {expect}",
                    _code(f'{{"a":[{text}]}}') == expect,
                )
        sys.set_int_max_str_digits(640)
        check(
            "JSON-4",
            "a stricter host limit still fails closed",
            _code("9" * 1000) == "number_out_of_range",
        )
    finally:
        sys.set_int_max_str_digits(saved)
    check("JSON-4", "the limit constant is CPython's default", limit == 4300)
    check("JSON-4", "ordinary integers parse as int", ic.strict_loads("[0,-1,12]") == [0, -1, 12])


def test_json_surrogates_and_encoding() -> None:
    for text in (
        '"\\ud800"',
        '"\\udc00"',
        '"\\ud800x"',
        '["\\udbff\\u0041"]',
        '{"\\ud800":1}',
        '{"k":["\\udfff"]}',
        '"\\ude00\\ud83d"',
    ):
        check("JSON-5", f"{text} is invalid_unicode", _code(text) == "invalid_unicode")
    paired = ic.strict_loads('"\\ud83d\\ude00"')
    check("JSON-5", "a paired surrogate escape is the real character", paired == "\U0001f600")
    check("JSON-5", "raw astral characters are accepted", _code('"\U0001f600"') is None)
    check(
        "JSON-5", "invalid UTF-8 bytes are invalid_unicode", _code(b'"\xff"') == "invalid_unicode"
    )
    check(
        "JSON-5",
        "a truncated UTF-8 sequence is invalid_unicode",
        _code(b'"\xe2\x82"') == "invalid_unicode",
    )
    check("JSON-5", "valid UTF-8 bytes parse", ic.strict_loads('"é"'.encode()) == "é")
    check(
        "JSON-5",
        "a lone surrogate typed into a str is invalid_unicode",
        _code('"\ud800"') == "invalid_unicode",
    )
    check("JSON-5", "NUL escapes are allowed", ic.strict_loads('"\\u0000"') == "\x00")
    for bad in (5, None, ["x"], bytearray(b"1")):
        check(
            "JSON-5",
            f"strict_loads({type(bad).__name__}) is a TypeError",
            raises(TypeError, ic.strict_loads, bad) is not None,
        )


def test_json_syntax_errors_are_invalid_json() -> None:
    for text in (
        "",
        " ",
        "{",
        "[1,]",
        "{'a':1}",
        "01",
        "1 2",
        "[1]x",
        "tru",
        "﻿[1]",
        '{"a"}',
        "--1",
        ".5",
        "1.",
        "+1",
    ):
        check("JSON-6", f"{text!r} is invalid_json", _code(text) == "invalid_json")
    for exc in (
        raises(ic.StrictJsonError, ic.strict_loads, "{"),
        raises(ic.StrictJsonError, ic.strict_loads, "NaN"),
    ):
        check(
            "JSON-6",
            "a refusal carries only its code",
            exc is not None and exc.args == (exc.code,) and exc.__cause__ is None,
        )
    check(
        "JSON-6",
        "an unknown code cannot be constructed",
        raises(ValueError, ic.StrictJsonError, "oops") is not None,
    )


# --- READ ---------------------------------------------------------------------------

PAYLOAD = b'{"p":"hello","c":"world"}\n' * 3


def test_read_exact_stream_passes() -> None:
    reader = _reader(PAYLOAD, chunks=support.split_chunks(PAYLOAD, 7))
    got = b"".join(reader)
    check("READ-1", "an exact stream is yielded intact", got == PAYLOAD)
    check("READ-1", "bytes_read counts every byte", reader.bytes_read == len(PAYLOAD))
    check(
        "READ-1",
        "sha256 is the digest of what was read",
        reader.sha256 == support.sha256_hex(PAYLOAD),
    )
    check("READ-1", "iterating again after the end stays at the end", list(reader) == [])
    empty = _reader(b"", chunks=[b"", b""])
    check(
        "READ-1",
        "an empty source with the empty digest passes",
        _drain(empty) is None and empty.bytes_read == 0,
    )
    skipped = _reader(PAYLOAD, chunks=[b"", PAYLOAD[:5], b"", PAYLOAD[5:], b""])
    check("READ-1", "empty chunks are skipped", b"".join(skipped) == PAYLOAD)


def test_read_size_and_hash_mismatch_are_source_changed() -> None:
    cases: list[tuple[str, ic.ChunkReader]] = [
        (
            "size+1 (extra byte)",
            _reader(
                PAYLOAD + b"x",
                expected_size=len(PAYLOAD),
                expected_sha256=support.sha256_hex(PAYLOAD + b"x"),
            ),
        ),
        (
            "size-1 (short)",
            _reader(
                PAYLOAD[:-1],
                expected_size=len(PAYLOAD),
                expected_sha256=support.sha256_hex(PAYLOAD[:-1]),
            ),
        ),
        (
            "sha differs at the last byte",
            _reader(
                PAYLOAD[:-1] + b"X",
                expected_size=len(PAYLOAD),
                expected_sha256=support.sha256_hex(PAYLOAD),
            ),
        ),
        (
            "sha differs at the first byte",
            _reader(
                b"X" + PAYLOAD[1:],
                expected_size=len(PAYLOAD),
                expected_sha256=support.sha256_hex(PAYLOAD),
            ),
        ),
        (
            "empty but declared non-empty",
            _reader(b"", expected_size=len(PAYLOAD), expected_sha256=support.sha256_hex(PAYLOAD)),
        ),
    ]
    for label, reader in cases:
        outcome = _drain(reader)
        check(
            "READ-2",
            f"{label} -> failed/source_changed",
            outcome is not None
            and (outcome.state, outcome.failure_code) == ("failed", "source_changed"),
        )
    extra = _reader(
        PAYLOAD + b"x",
        expected_size=len(PAYLOAD),
        expected_sha256=support.sha256_hex(PAYLOAD + b"x"),
        chunks=[PAYLOAD, b"x"],
    )
    first = next(extra)
    check("READ-2", "the chunk that reaches the declared size is delivered", first == PAYLOAD)
    check(
        "READ-2",
        "the extra byte aborts on arrival",
        raises(ic.ImportAborted, next, extra) is not None,
    )
    outcome = _drain(_reader(PAYLOAD, expected_sha256="0" * 64))
    check(
        "READ-2",
        "a wrong expected digest is source_changed",
        outcome is not None and outcome.failure_code == "source_changed",
    )


def test_read_source_failure_is_blocked_never_completed_or_failed() -> None:
    marker = support.make_marker("source-error")
    chunks = [b"abc", b"def", b"ghi"]
    data = b"".join(chunks)
    errors: list[BaseException] = [
        RuntimeError(marker),
        OSError(marker),
        TimeoutError(marker),
        S3Error(503, "SlowDown", marker, ""),
        ValueError(marker),
        KeyError(marker),
        UploadStorageUnavailableError(marker),
    ]
    for error in errors:
        for at in (0, 1, 2, 3):
            source = support.ExplodingSource(chunks, error, at=at)
            reader = ic.ChunkReader(
                source, expected_size=len(data), expected_sha256=support.sha256_hex(data)
            )
            outcome = _drain(reader)
            label = f"{type(error).__name__} at chunk {at}"
            check(
                "READ-3",
                f"{label}: blocked/source_unavailable",
                outcome is not None
                and (outcome.state, outcome.failure_code) == ("blocked", "source_unavailable"),
            )
            if outcome is not None:
                check(
                    "READ-3",
                    f"{label}: the source error text is not carried",
                    support.leaked_markers(outcome, [marker]) == [],
                )
                check(
                    "READ-3",
                    f"{label}: raised from None (no chain)",
                    outcome.__cause__ is None and outcome.__suppress_context__,
                )
            check("READ-3", f"{label}: the source iterator was closed", source.closed)
            again = raises(ic.ImportAborted, next, reader)
            check("READ-3", f"{label}: the abort is sticky", again is outcome)

    class Boom(BaseException):
        pass

    source = support.ExplodingSource(chunks, Boom())
    check(
        "READ-3",
        "a BaseException that is not an Exception is not swallowed",
        raises(Boom, lambda: list(ic.ChunkReader(source, expected_size=9, expected_sha256=SHA_X)))
        is not None,
    )
    for chunk in ("abc", bytearray(b"abc"), memoryview(b"abc"), None, 3):
        reader = ic.ChunkReader(
            [chunk], expected_size=3, expected_sha256=support.sha256_hex(b"abc")
        )  # type: ignore[list-item]
        outcome = _drain(reader)
        check(
            "READ-3",
            f"a {type(chunk).__name__} chunk is blocked, not accepted",
            outcome is not None and outcome.state == "blocked",
        )

    class Gen:
        def __iter__(self) -> Iterator[bytes]:
            yield b"abc"
            raise RuntimeError("late")

    outcome = _drain(
        ic.ChunkReader(Gen(), expected_size=3, expected_sha256=support.sha256_hex(b"abc"))
    )
    check(
        "READ-3",
        "a generator that fails after its last chunk is blocked",
        outcome is not None and outcome.state == "blocked",
    )


def test_read_deadline() -> None:
    clock = support.FakeClock()
    limits = replace(ic.DEFAULT_LIMITS, deadline_seconds=10.0)
    chunks = [b"ab", b"cd", b"ef"]
    reader = ic.ChunkReader(
        chunks,
        expected_size=6,
        expected_sha256=support.sha256_hex(b"abcdef"),
        limits=limits,
        clock=clock,
    )
    check("READ-4", "the first chunk arrives in time", next(reader) == b"ab")
    clock.advance(10.0)
    check("READ-4", "exactly at the deadline is still in time", next(reader) == b"cd")
    clock.advance(0.001)
    outcome = raises(ic.ImportAborted, next, reader)
    check(
        "READ-4",
        "past the deadline is failed/deadline_exceeded",
        outcome is not None
        and (outcome.state, outcome.failure_code) == ("failed", "deadline_exceeded"),
    )
    check(
        "READ-4", "the deadline abort is sticky", raises(ic.ImportAborted, next, reader) is outcome
    )
    clock2 = support.FakeClock()
    slow = support.ExplodingSource([b"ab"], RuntimeError("x"), at=1)
    reader2 = ic.ChunkReader(
        slow, expected_size=4, expected_sha256=SHA_X, limits=limits, clock=clock2
    )
    next(reader2)
    clock2.advance(11.0)
    outcome = raises(ic.ImportAborted, next, reader2)
    check(
        "READ-4",
        "a stale deadline wins before the source is asked again",
        outcome is not None and outcome.failure_code == "deadline_exceeded",
    )
    deadline = ic.Deadline(5.0, clock2)
    deadline.check()
    clock2.advance(5.0)
    deadline.check()
    clock2.advance(0.5)
    check(
        "READ-4",
        "Deadline.check raises past the limit",
        raises(ic.ImportAborted, deadline.check) is not None,
    )


def test_read_constructor_validation() -> None:
    for kwargs in (
        {"expected_size": -1, "expected_sha256": SHA_X},
        {"expected_size": True, "expected_sha256": SHA_X},
        {"expected_size": 1.0, "expected_sha256": SHA_X},
        {"expected_size": 1, "expected_sha256": "A" * 64},
        {"expected_size": 1, "expected_sha256": "a" * 63},
        {"expected_size": 1, "expected_sha256": None},
    ):
        check(
            "READ-5",
            f"ChunkReader({kwargs}) is refused",
            raises(ValueError, ic.ChunkReader, [b"x"], **kwargs) is not None,
        )
    check(
        "READ-5",
        "ImportAborted refuses unknown verdicts",
        raises(ValueError, ic.ImportAborted, "completed", "source_changed") is not None
        and raises(ValueError, ic.ImportAborted, "failed", "nope") is not None,
    )


# --- run_import ---------------------------------------------------------------------


def _import(events: Any, reader: ic.ChunkReader, **kwargs: Any) -> ic.ImportReport:
    return ic.run_import(
        events,
        reader,
        importer=kwargs.pop("importer", "jsonl"),
        importer_version=kwargs.pop("importer_version", "1"),
        mapping=kwargs.pop("mapping", mapping_of(prompt_completion_doc())),
        dataset_purpose=kwargs.pop("dataset_purpose", "training"),
        expected_sha256=kwargs.pop("expected_sha256", support.sha256_hex(PAYLOAD)),
        **kwargs,
    )


def _accepted(index: int = 0) -> ic.AcceptedRecord:
    mapping = mapping_of(prompt_completion_doc())
    row = ic.apply_mapping(mapping, {"p": f"hello {index}", "c": "world"})
    record = ic.normalize_mapped_record(
        mapping, row, source_record_index=index, dataset_sha256=SHA_X, source_type="upload"
    )
    return ic.AcceptedRecord(
        ic.to_dataset_record_candidate(
            record, record_index=index, tool_schema_status="not_applicable"
        )
    )


def _pulling(reader: ic.ChunkReader, events: list[ic.ImportEvent]) -> Iterator[ic.ImportEvent]:
    """An importer stand-in: reads the whole stream, then yields `events`."""
    for _ in reader:
        pass
    yield from events


def test_read_run_import_states() -> None:
    reader = _reader(PAYLOAD)
    report = _import(
        _pulling(
            reader,
            [
                _accepted(0),
                ic.BlankLine(),
                ic.RowRejection("line:2", "unmapped_role", "messages[1]"),
            ],
        ),
        reader,
    )
    check(
        "READ-6",
        "a clean stream is completed",
        report.state == "completed" and report.failure_code is None,
    )
    check(
        "READ-6",
        "counts add up",
        (report.records_seen, report.records_accepted, report.records_rejected, report.blank_lines)
        == (2, 1, 1, 1),
    )
    check(
        "READ-6",
        "the report records the source digest and size",
        report.source_sha256 == support.sha256_hex(PAYLOAD) and report.source_bytes == len(PAYLOAD),
    )

    bad = _reader(
        PAYLOAD[:-1] + b"X", expected_size=len(PAYLOAD), expected_sha256=support.sha256_hex(PAYLOAD)
    )
    report = _import(_pulling_safely(bad, [_accepted(0)]), bad)
    check(
        "READ-6",
        "a sha mismatch is never completed",
        report.state == "failed" and report.failure_code == "source_changed",
    )

    source = support.ExplodingSource([PAYLOAD[:10], PAYLOAD[10:20]], RuntimeError("down"), at=2)
    reader2 = ic.ChunkReader(
        source, expected_size=len(PAYLOAD), expected_sha256=support.sha256_hex(PAYLOAD)
    )
    report = _import(_pulling_safely(reader2, [_accepted(0)]), reader2)
    check(
        "READ-6",
        "a source exception is blocked/source_unavailable, not completed or failed",
        (report.state, report.failure_code) == ("blocked", "source_unavailable"),
    )
    check(
        "READ-6",
        "accepted-before-the-outage records are still counted honestly",
        report.records_accepted in (0, 1),
    )

    reader3 = _reader(PAYLOAD)
    report = _import(iter([_accepted(0)]), reader3)
    check(
        "READ-6",
        "an importer that stops before the end of the stream is not completed",
        (report.state, report.failure_code) == ("failed", "stream_not_consumed"),
    )

    clock = support.FakeClock()
    reader4 = ic.ChunkReader(
        support.split_chunks(PAYLOAD, 4),
        expected_size=len(PAYLOAD),
        expected_sha256=support.sha256_hex(PAYLOAD),
        limits=replace(ic.DEFAULT_LIMITS, deadline_seconds=1.0),
        clock=clock,
    )

    def slow_events() -> Iterator[ic.ImportEvent]:
        yield _accepted(0)
        clock.advance(5.0)
        yield _accepted(1)

    report = _import(slow_events(), reader4)
    check(
        "READ-6",
        "a deadline that passes between records is failed/deadline_exceeded",
        (report.state, report.failure_code) == ("failed", "deadline_exceeded"),
    )

    seen: list[object] = []
    reader5 = _reader(PAYLOAD)
    report = _import(
        _pulling(reader5, [_accepted(0), _accepted(1)]), reader5, on_accepted=seen.append
    )
    check(
        "READ-6",
        "on_accepted receives each accepted candidate",
        len(seen) == 2 and report.records_accepted == 2,
    )
    reader6 = _reader(PAYLOAD)
    report = _import(_pulling(reader6, [_accepted(0)]), reader6, source_schema_sha256="b" * 64)
    check("READ-6", "source_schema_sha256 is carried", report.source_schema_sha256 == "b" * 64)


def _pulling_safely(
    reader: ic.ChunkReader, events: list[ic.ImportEvent]
) -> Iterator[ic.ImportEvent]:
    """Like `_pulling`, but lets the reader's ImportAborted surface mid-iteration."""
    yield from events
    for _ in reader:
        pass


# --- REP ----------------------------------------------------------------------------


def _report_fields(report: ic.ImportReport) -> dict[str, Any]:
    return {name: getattr(report, name) for name in ic.ImportReport.__dataclass_fields__}


def _sample_report() -> ic.ImportReport:
    reader = _reader(PAYLOAD)
    return _import(
        _pulling(
            reader,
            [
                _accepted(0),
                ic.RowRejection("line:2", "unmapped_role", "messages[0]"),
                ic.RowRejection("line:3", "record_too_large"),
                ic.BlankLine(),
            ],
        ),
        reader,
    )


def test_rep_invariants_and_seal() -> None:
    report = _sample_report()
    check(
        "REP-1",
        "records_seen == accepted + rejected",
        report.records_seen == report.records_accepted + report.records_rejected == 3,
    )
    fields = _report_fields(report)
    check("REP-1", "the report rebuilds from its own fields", ic.ImportReport(**fields) == report)
    inconsistent = {**fields, "records_seen": fields["records_seen"] + 1}
    check(
        "REP-1",
        "records_seen != accepted + rejected is refused",
        raises(ValueError, ic.ImportReport, **inconsistent) is not None,
    )
    for name, change in (
        ("records_accepted", 5),
        ("records_rejected", 0),
        ("blank_lines", 9),
        ("source_bytes", 1),
        ("source_sha256", "c" * 64),
        ("expected_sha256", "c" * 64),
        ("mapping_sha256", "c" * 64),
        ("dataset_purpose", "validation"),
        ("importer", "csv"),
        ("importer_version", "2"),
        ("source_schema_sha256", "d" * 64),
        ("rejection_counts", (("unmapped_role", 9),)),
        ("rejections", ()),
    ):
        tampered = {**fields, name: change}
        if name in ("records_accepted", "records_rejected"):
            tampered["records_seen"] = tampered["records_accepted"] + tampered["records_rejected"]
        check(
            "REP-1",
            f"changing {name} without re-sealing is refused",
            raises(ValueError, ic.ImportReport, **tampered) is not None,
        )
    body = report.body()
    check(
        "REP-1",
        "report_sha256 covers every other field",
        set(body) == set(fields) - {"report_sha256"},
    )
    check(
        "REP-1",
        "report_sha256 is the sha256 of the canonical body",
        report.report_sha256
        == hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )
    check(
        "REP-1",
        "rejection_counts are exact",
        dict(report.rejection_counts) == {"unmapped_role": 1, "record_too_large": 1},
    )
    check(
        "REP-1",
        "rejections keep their order",
        [r.locator for r in report.rejections] == ["line:2", "line:3"],
    )
    for state, code in (
        ("completed", "source_changed"),
        ("failed", None),
        ("blocked", None),
        ("weird", None),
        ("failed", "nope"),
    ):
        bad = {**fields, "state": state, "failure_code": code}
        check(
            "REP-1",
            f"state={state}, failure_code={code} is refused",
            raises(ValueError, ic.ImportReport, **bad) is not None,
        )


def test_rep_catalogs_are_closed_and_complete() -> None:
    check("REP-2", "REJECTION_CODES is a frozenset", isinstance(ic.REJECTION_CODES, frozenset))
    check("REP-2", "FAILURE_CODES is a frozenset", isinstance(ic.FAILURE_CODES, frozenset))
    check(
        "REP-2",
        "every strict-JSON code is also a rejection code",
        ic.STRICT_JSON_CODES <= ic.REJECTION_CODES,
    )
    check("REP-2", "no code is in both catalogs", not (ic.REJECTION_CODES & ic.FAILURE_CODES))
    emitted: set[str] = set()
    constructors = {
        "RowMappingError": 0,
        "RowRejection": 1,
        "StrictJsonError": 0,
        "ImportAborted": 1,
        "MappingInvalidError": 0,
        "_bad": 0,
    }
    for path in (Path(ic.__file__), Path(source_module.__file__)):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else getattr(node.func, "attr", "")
                )
                if name in constructors and len(node.args) > constructors[name]:
                    arg = node.args[constructors[name]]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        emitted.add((name, arg.value))  # type: ignore[arg-type]
    by_kind = {
        "RowMappingError": ic.REJECTION_CODES,
        "RowRejection": ic.REJECTION_CODES,
        "StrictJsonError": ic.STRICT_JSON_CODES,
        "ImportAborted": ic.FAILURE_CODES,
        "MappingInvalidError": ic.MAPPING_INVALID_REASONS,
        "_bad": ic.MAPPING_INVALID_REASONS,
    }
    unknown = [(kind, code) for kind, code in emitted if code not in by_kind[kind]]  # type: ignore[misc]
    check("REP-2", f"every literal code the modules emit is in its catalog: {unknown}", not unknown)
    check("REP-2", f"the scan saw the emitters ({len(emitted)} literals)", len(emitted) > 25)
    for kind, factory in (
        ("RowMappingError", lambda c: ic.RowMappingError(c)),
        ("RowRejection", lambda c: ic.RowRejection("line:1", c)),
    ):
        check(
            "REP-2",
            f"{kind} refuses an uncatalogued code",
            raises(ValueError, factory, "not_a_code") is not None,
        )
    for code in sorted(ic.REJECTION_CODES):
        check("REP-2", f"RowRejection accepts {code}", ic.RowRejection("line:1", code).code == code)
    for locator in ("line:0", "row:12", "record:999999999999999999", "object:3"):
        check(
            "REP-2",
            f"locator {locator} accepted",
            ic.RowRejection(locator, "unmapped_role").locator == locator,
        )
    for locator in (
        "",
        "line:",
        "line:-1",
        "line:01x",
        "LINE:1",
        "line:1\n",
        "line:1 ",
        "line: 1",
        "x:1",
        "line:1234567890123456789",
        support.make_marker("loc"),
        5,
        None,
    ):
        check(
            "REP-2",
            f"locator {str(locator)[:25]!r} refused",
            raises(ValueError, ic.RowRejection, locator, "unmapped_role") is not None,
        )
    for slot in (
        "messages[1]",
        "prompt",
        "metadata.language",
        "group_key",
        "metadata.event_timestamp",
        "messages",
    ):
        check(
            "REP-2",
            f"slot {slot} accepted",
            ic.RowRejection("line:1", "unmapped_role", slot).slot == slot,
        )
    for slot in (
        "",
        "Messages[1]",
        "messages[x]",
        "messages[1",
        "a" * 40,
        "prompt\n",
        "has space",
        support.make_marker("slot"),
        "a.b.c",
        "messages[1234567890]",
    ):
        check(
            "REP-2",
            f"slot {slot[:25]!r} refused",
            raises(ValueError, ic.RowRejection, "line:1", "unmapped_role", slot) is not None,
        )


def test_rep_accumulator_validation_and_counts() -> None:
    base = {
        "importer": "jsonl",
        "importer_version": "1",
        "mapping_sha256": SHA_X,
        "dataset_purpose": "training",
        "expected_sha256": SHA_X,
    }
    ic.ImportAccumulator(**base)
    for name, bad in (
        ("importer", "has space"),
        ("importer", ""),
        ("importer", "x\n"),
        ("importer_version", "v 1"),
        ("mapping_sha256", "A" * 64),
        ("expected_sha256", "z" * 64),
        ("dataset_purpose", "Training"),
        ("dataset_purpose", "a b"),
        ("dataset_purpose", ""),
        ("importer", 3),
    ):
        check(
            "REP-3",
            f"{name}={bad!r} is refused",
            raises(ValueError, ic.ImportAccumulator, **{**base, name: bad}) is not None,
        )
    acc = ic.ImportAccumulator(**base)
    check(
        "REP-3", "an unknown event is a TypeError", raises(TypeError, acc.add, "nope") is not None
    )  # type: ignore[arg-type]
    acc.add(_accepted(0))
    acc.add(ic.BlankLine())
    acc.add(ic.BlankLine())
    acc.add(ic.RowRejection("line:3", "unmapped_role"))
    acc.add(ic.RowRejection("line:4", "unmapped_role"))
    acc.add(ic.RowRejection("line:5", "canonical_schema_invalid", "messages[2]"))
    report = acc.report(source_sha256=SHA_X, source_bytes=5)
    check(
        "REP-3", "blank lines are not records", report.blank_lines == 2 and report.records_seen == 4
    )
    check(
        "REP-3",
        "rejection_counts are sorted and exact",
        report.rejection_counts == (("canonical_schema_invalid", 1), ("unmapped_role", 2)),
    )
    check(
        "REP-3",
        "a report with no abort is completed",
        report.state == "completed" and report.source_schema_sha256 is None,
    )
    aborted = acc.report(
        source_sha256=SHA_X,
        source_bytes=5,
        aborted=ic.ImportAborted("blocked", "source_unavailable"),
    )
    check(
        "REP-3",
        "an abort sets state and failure_code",
        (aborted.state, aborted.failure_code) == ("blocked", "source_unavailable"),
    )
    for bad_stop in (0, 21, -1, True, 1.0, "3"):
        check(
            "REP-3",
            f"stop_after_accepted={bad_stop!r} refused",
            raises(ValueError, ic.ImportAccumulator, **base, stop_after_accepted=bad_stop)
            is not None,
        )


# --- CAND ---------------------------------------------------------------------------


def _tool_rows() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    messages_map = messages_doc()
    messages_map["group_key"] = {"key": "conv"}
    tool_turns = [
        {"from": "sys", "value": "be brief"},
        {"from": "human", "value": "weather?"},
        {
            "from": "gpt",
            "value": "",
            "calls": [{"id": "c1", "name": "get_weather", "arguments": {"city": "Riyadh"}}],
        },
        {"from": "fn", "value": "41C", "call_id": "c1", "tool": "get_weather"},
        {"from": "gpt", "value": "It is 41C."},
    ]
    return [
        (
            "messages",
            messages_map,
            {
                "conversation": {"turns": tool_turns},
                "conv": "g1",
                "m_language": "en",
                "m_event_timestamp": "2026-10-13T10:00:00Z",
            },
        ),
        (
            "messages-plain",
            messages_map,
            {
                "conversation": {
                    "turns": [{"from": "human", "value": "hi"}, {"from": "gpt", "value": "yo"}]
                }
            },
        ),
        (
            "prompt_completion",
            prompt_completion_doc(),
            {"p": "q", "c": "a", "s": "sys", "m_consent": "granted"},
        ),
        ("prompt_completion-no-system", prompt_completion_doc(), {"p": "q", "c": "a"}),
    ]


def test_cand_every_accepted_candidate_passes_ftf_0508() -> None:
    candidates = []
    for index, (label, doc, row) in enumerate(_tool_rows()):
        mapping = mapping_of(doc)
        validator_calls: list[str] = []

        def validator(
            schema: dict[str, object],
            instance: object,
            *,
            field_name: str = "x",
            sink: list[str] = validator_calls,
        ) -> None:
            sink.append(field_name)

        mapped = ic.apply_mapping(mapping, row)
        record = ic.normalize_mapped_record(
            mapping,
            mapped,
            source_record_index=index,
            dataset_sha256=SHA_X,
            source_type="upload",
            tool_schemas={"get_weather": {"type": "object"}},
            tool_argument_validator=validator,
        )
        status = "valid" if any(m.tool_calls for m in record.messages) else "not_applicable"
        check(
            "CAND-1",
            f"{label}: the injected tool-argument validator ran exactly when there were tool calls",
            bool(validator_calls) == (status == "valid"),
        )
        candidate = ic.to_dataset_record_candidate(
            record, record_index=index, tool_schema_status=status
        )
        candidates.append(candidate)
        check(
            "CAND-1",
            f"{label}: the candidate is the Section 6.11 shape",
            set(candidate)
            == {
                "record_index",
                "schema_version",
                "source_type",
                "content",
                "record_sha256",
                "normalized_sha256",
                "quality_status",
                "pii_status",
                "tool_schema_status",
            },
        )
        check(
            "CAND-1",
            f"{label}: source_type is the lineage source type",
            candidate["source_type"] == "upload",
        )
        check(
            "CAND-1",
            f"{label}: record_sha256 is the lineage raw hash",
            candidate["record_sha256"] == record.lineage.raw_sha256,
        )
        check(
            "CAND-1",
            f"{label}: normalized_sha256 is the canonical hash",
            candidate["normalized_sha256"] == record.canonical_hash,
        )
        check(
            "CAND-1",
            f"{label}: starts pending/unscanned",
            (candidate["quality_status"], candidate["pii_status"]) == ("pending", "unscanned"),
        )
        check(
            "CAND-1",
            f"{label}: schema_version is FTF-0506's",
            candidate["schema_version"] == SCHEMA_VERSION,
        )
        check(
            "CAND-1",
            f"{label}: content is exactly messages + metadata",
            set(candidate["content"]) == {"messages", "metadata"},
        )  # type: ignore[arg-type]
    report = validate_record_batch(candidates)
    check(
        "CAND-1",
        f"validate_record_batch reports 0 issues: {report.issues}",
        report.issues == () and len(report.accepted_batch_positions) == len(candidates),
    )
    tool_candidate = candidates[0]
    check(
        "CAND-1",
        "the tool call survived into the candidate",
        "tool_calls" in json.dumps(tool_candidate["content"]),
    )
    check(
        "CAND-1",
        "the metadata carried through",
        tool_candidate["content"]["metadata"]
        == {"language": "en", "event_timestamp": "2026-10-13T10:00:00Z"},
    )  # type: ignore[index]
    mapping = mapping_of(prompt_completion_doc())
    mapped = ic.apply_mapping(mapping, {"p": "q", "c": "a"})
    record = ic.normalize_mapped_record(
        mapping, mapped, source_record_index=0, dataset_sha256=SHA_X, source_type="upload"
    )
    for bad_index in (-1, True, 1.0, None, "1"):
        check(
            "CAND-1",
            f"record_index={bad_index!r} refused",
            raises(
                ValueError,
                ic.to_dataset_record_candidate,
                record,
                record_index=bad_index,
                tool_schema_status="valid",
            )
            is not None,
        )
    for bad_status in ("invalid", "", None, "VALID"):
        check(
            "CAND-1",
            f"tool_schema_status={bad_status!r} refused",
            raises(
                ValueError,
                ic.to_dataset_record_candidate,
                record,
                record_index=0,
                tool_schema_status=bad_status,
            )
            is not None,
        )
    for status in ("valid", "not_applicable"):
        check(
            "CAND-1",
            f"tool_schema_status={status} accepted",
            ic.to_dataset_record_candidate(record, record_index=0, tool_schema_status=status)[
                "tool_schema_status"
            ]
            == status,
        )


def test_cand_record_index_is_the_caller_ordinal() -> None:
    mapping = mapping_of(prompt_completion_doc())
    candidates = []
    for ordinal in (0, 2, 3, 40):
        mapped = ic.apply_mapping(mapping, {"p": f"q{ordinal}", "c": "a"})
        record = ic.normalize_mapped_record(
            mapping, mapped, source_record_index=ordinal, dataset_sha256=SHA_X, source_type="upload"
        )
        candidates.append(
            ic.to_dataset_record_candidate(
                record, record_index=ordinal, tool_schema_status="not_applicable"
            )
        )
        check(
            "CAND-2",
            f"lineage index {ordinal} equals the ordinal",
            record.lineage.source_record_index == ordinal,
        )
    check(
        "CAND-2",
        "gaps in the ordinal (rejected rows counted) validate",
        validate_record_batch(candidates).issues == (),
    )


# --- ROW: applying a mapping ----------------------------------------------------------


def _pc(doc_over: dict[str, Any] | None = None) -> ic.ImportMapping:
    doc = prompt_completion_doc()
    doc.update(doc_over or {})
    return mapping_of(doc)


def _row_error(
    mapping: ic.ImportMapping, row: Any, headers: Any = None
) -> tuple[str, str | None] | None:
    try:
        ic.apply_mapping(mapping, row, headers=headers)
    except ic.RowMappingError as exc:
        return exc.code, exc.slot
    return None


def test_row_prompt_completion_codes() -> None:
    m = _pc()
    ok = ic.apply_mapping(m, {"p": "q", "c": "a"})
    check(
        "ROW-1",
        "prompt+completion maps to [user, assistant]",
        ok.raw_record["messages"]
        == [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
    )
    check(
        "ROW-1",
        "an absent optional system is omitted",
        ok.raw_record["metadata"] == {} and ok.group_key is None,
    )
    with_system = ic.apply_mapping(m, {"p": "q", "c": "a", "s": "be kind"})
    check(
        "ROW-1",
        "system comes first",
        [x["role"] for x in with_system.raw_record["messages"]] == ["system", "user", "assistant"],
    )  # type: ignore[union-attr]
    check(
        "ROW-1",
        "a missing required slot is required_field_missing",
        _row_error(m, {"c": "a"}) == ("required_field_missing", "prompt")
        and _row_error(m, {"p": "q"}) == ("required_field_missing", "completion"),
    )
    check(
        "ROW-1",
        "a null required slot is required_field_missing",
        _row_error(m, {"p": None, "c": "a"}) == ("required_field_missing", "prompt"),
    )
    for bad in (1, True, 1.5, [], {}, b"x"):
        check(
            "ROW-1",
            f"prompt={bad!r} is mapping_type_mismatch",
            _row_error(m, {"p": bad, "c": "a"}) == ("mapping_type_mismatch", "prompt"),
        )
    check(
        "ROW-1",
        "a str-subclass prompt is not an exact str",
        _row_error(m, {"p": support.StrSub("q"), "c": "a"}) == ("mapping_type_mismatch", "prompt"),
    )
    check(
        "ROW-1",
        "a wrong-typed optional system is mapping_type_mismatch, not omitted",
        _row_error(m, {"p": "q", "c": "a", "s": 5}) == ("mapping_type_mismatch", "system"),
    )
    check(
        "ROW-1",
        "a null optional system is omitted",
        len(ic.apply_mapping(m, {"p": "q", "c": "a", "s": None}).raw_record["messages"]) == 2,
    )  # type: ignore[arg-type]
    check(
        "ROW-1",
        "an empty string in a JSON record is passed on (FTF-0506 decides)",
        ic.apply_mapping(m, {"p": "", "c": "a"}).raw_record["messages"][0]["content"] == "",
    )  # type: ignore[index]
    row = {"p": "q", "c": "a"}
    snapshot = copy.deepcopy(row)
    ic.apply_mapping(m, row)
    check("ROW-1", "the source row is not mutated", row == snapshot)
    check(
        "ROW-1",
        "a non-row is refused cleanly",
        _row_error(m, "just a string") is not None and _row_error(m, 5) is not None,
    )  # type: ignore[arg-type]


def test_row_tabular_slots_and_empty_cells() -> None:
    m = mapping_of(
        {
            **prompt_completion_doc(),
            "prompt": {"header": "Question"},
            "completion": {"letters": "C"},
            "system": {"index": 4},
            "group_key": {"letters": "AA"},
            "metadata": {},
        }
    )
    headers = ["id", "Question", "Answer", "x", "sys"] + [""] * 30
    row: list[Any] = ["1", "q?", "a!", "ignored", "be nice"] + [""] * 21 + ["grp"] + [""] * 8
    mapped = ic.apply_mapping(m, row, headers=headers)
    check(
        "ROW-2",
        "header / letters / index slots address the right cells",
        [x["content"] for x in mapped.raw_record["messages"]] == ["be nice", "q?", "a!"],
    )  # type: ignore[union-attr]
    check("ROW-2", "letters AA is column 26", mapped.group_key == "grp")
    for letters, position in (
        ("A", 0),
        ("B", 1),
        ("Z", 25),
        ("AA", 26),
        ("AB", 27),
        ("AZ", 51),
        ("BA", 52),
        ("ZZ", 701),
    ):
        probe = mapping_of(
            {
                **prompt_completion_doc(),
                "prompt": {"letters": letters},
                "completion": {"index": 0},
                "system": {"index": 1},
                "metadata": {},
                "group_key": {"index": 2},
            }
        )
        cells: list[Any] = ["c"] * (max(position, 2) + 1)
        cells[position] = "HIT"
        got = ic.apply_mapping(probe, cells)
        check(
            "ROW-2",
            f"letters {letters} is column {position}",
            got.raw_record["messages"][-2]["content"] == "HIT"
            or got.raw_record["messages"][0]["content"] == "HIT",
        )  # type: ignore[index]
    short = ["a", "b"]
    check(
        "ROW-2",
        "a row shorter than the slot is required_field_missing",
        _row_error(m, short, headers=headers) == ("required_field_missing", "completion"),
    )
    blank_prompt = list(row)
    blank_prompt[1] = ""
    check(
        "ROW-2",
        "an empty required tabular cell is required_field_empty",
        _row_error(m, blank_prompt, headers=headers) == ("required_field_empty", "prompt"),
    )
    blank_system = list(row)
    blank_system[4] = ""
    got = ic.apply_mapping(m, blank_system, headers=headers)
    check(
        "ROW-2",
        "an empty optional tabular cell is omitted, never defaulted",
        [x["role"] for x in got.raw_record["messages"]] == ["user", "assistant"],
    )  # type: ignore[union-attr]
    check(
        "ROW-2",
        "a whitespace-only cell is a value, not empty",
        _row_error(m, [*row[:1], " ", *row[2:]], headers=headers) is None,
    )
    dup_headers = list(headers)
    dup_headers[2] = "Question"
    check(
        "ROW-2",
        "a duplicated header is ambiguous: required_field_missing, not a guess",
        _row_error(m, row, headers=dup_headers) == ("required_field_missing", "prompt"),
    )
    check(
        "ROW-2",
        "a header slot with no headers given is missing",
        _row_error(m, row) == ("required_field_missing", "prompt"),
    )
    check(
        "ROW-2",
        "a non-str tabular cell is mapping_type_mismatch",
        _row_error(m, [*row[:1], 5, *row[2:]], headers=headers)
        == ("mapping_type_mismatch", "prompt"),
    )
    obj_row = {"Question": "q", "x": "y"}
    check(
        "ROW-2",
        "an object row with a letters slot has nothing to address",
        _row_error(m, obj_row) == ("required_field_missing", "prompt")
        or _row_error(m, obj_row) == ("required_field_missing", "completion"),
    )
    m_key = _pc()
    check(
        "ROW-2",
        "a tabular row with only key slots has nothing to address",
        _row_error(m_key, ["q", "a"]) == ("required_field_missing", "prompt"),
    )
    check(
        "ROW-2",
        "an object row honours a header slot as an exact key",
        _row_error(
            mapping_of({**prompt_completion_doc(), "prompt": {"header": "p"}}), {"p": "q", "c": "a"}
        )
        is None,
    )
    m_idx = mapping_of(
        {
            **prompt_completion_doc(),
            "prompt": {"index": 0},
            "completion": {"index": 1},
            "system": {"index": 2},
            "metadata": {},
            "group_key": {"index": 3},
        }
    )
    check(
        "ROW-2",
        "an index slot on an object row is missing",
        _row_error(m_idx, {"p": "q", "c": "a"}) == ("required_field_missing", "prompt"),
    )
    check("ROW-2", "a tuple row is a row", _row_error(m_idx, ("q", "a")) is None)
    check("ROW-2", "a bytes row is not a row", _row_error(m_idx, b"qa") is not None)


def test_row_messages_shape_and_roles() -> None:
    m = mapping_of(messages_doc())
    turns = lambda *items: {"conversation": {"turns": list(items)}}  # noqa: E731
    good = turns({"from": "human", "value": "hi"}, {"from": "gpt", "value": "yo"})
    mapped = ic.apply_mapping(m, good)
    check(
        "ROW-3",
        "roles are mapped through role_map",
        [x["role"] for x in mapped.raw_record["messages"]] == ["user", "assistant"],
    )  # type: ignore[union-attr]
    check(
        "ROW-3",
        "a missing messages container is required_field_missing",
        _row_error(m, {"other": 1}) == ("required_field_missing", "messages"),
    )
    for container in (None, "x", {}, 5, []):
        check(
            "ROW-3",
            f"messages container {container!r} is mapping_type_mismatch",
            _row_error(m, {"conversation": {"turns": container}})
            in (("mapping_type_mismatch", "messages"), ("required_field_missing", "messages")),
        )
    check(
        "ROW-3",
        "an empty message array is mapping_type_mismatch",
        _row_error(m, turns()) == ("mapping_type_mismatch", "messages"),
    )
    check(
        "ROW-3",
        "a non-object message is mapping_type_mismatch with its index",
        _row_error(m, turns({"from": "human", "value": "a"}, "text"))
        == ("mapping_type_mismatch", "messages[1]"),
    )
    check(
        "ROW-3",
        "an unmapped role names the message index",
        _row_error(m, turns({"from": "human", "value": "a"}, {"from": "robot", "value": "b"}))
        == ("unmapped_role", "messages[1]"),
    )
    check(
        "ROW-3",
        "a missing role key is required_field_missing",
        _row_error(m, turns({"value": "a"})) == ("required_field_missing", "messages[0]"),
    )
    check(
        "ROW-3",
        "a missing content key is required_field_missing",
        _row_error(m, turns({"from": "human"})) == ("required_field_missing", "messages[0]"),
    )
    check(
        "ROW-3",
        "a non-str role is mapping_type_mismatch",
        _row_error(m, turns({"from": 5, "value": "a"})) == ("mapping_type_mismatch", "messages[0]"),
    )
    check(
        "ROW-3",
        "an extra message key is rejected under reject",
        _row_error(m, turns({"from": "human", "value": "a", "extra": 1}))
        == ("unmapped_message_key", "messages[0]"),
    )
    ignoring = mapping_of({**messages_doc(), "on_unmapped_message_keys": "ignore"})
    got = ic.apply_mapping(
        ignoring, turns({"from": "human", "value": "a", "extra": 1}, {"from": "gpt", "value": "b"})
    )
    check(
        "ROW-3",
        "an extra message key is dropped under ignore",
        got.raw_record["messages"][0] == {"role": "user", "content": "a"},
    )  # type: ignore[index]
    full = ic.apply_mapping(
        m,
        turns({"from": "gpt", "value": "", "calls": [{"id": "c1"}], "call_id": "c1", "tool": "t"}),
    )
    check(
        "ROW-3",
        "mapped keys carry tool_calls / tool_call_id / name",
        full.raw_record["messages"][0]
        == {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1"}],
            "tool_call_id": "c1",
            "name": "t",
        },
    )  # type: ignore[index]
    root = mapping_of({**messages_doc(), "messages_pointer": ""})
    check(
        "ROW-3",
        "a root-pointer messages shape needs an object row, and the array is not found in it",
        _row_error(root, {"a": 1}) is not None,
    )
    check(
        "ROW-3",
        "a messages shape refuses a tabular row",
        _row_error(m, ["a", "b"]) == ("mapping_type_mismatch", "messages"),
    )
    indexed = mapping_of({**messages_doc(), "messages_pointer": "/c/0/m"})
    check(
        "ROW-3",
        "a pointer through an array index resolves",
        _row_error(indexed, {"c": [{"m": [{"from": "human", "value": "a"}]}]}) is None,
    )


def test_row_messages_json_cells() -> None:
    m = mapping_of(messages_json_doc())
    headers = ["messages", "x", "grp"]
    cell = json.dumps(
        [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "name": "t", "arguments": {}}],
            },
            {"role": "tool", "content": "r", "tool_call_id": "c1", "name": "t"},
            {"role": "assistant", "content": "done"},
        ]
    )
    mapped = ic.apply_mapping(m, [cell, "x", "grp"], headers=headers)
    check(
        "ROW-4",
        "tool calls ride in a JSON cell",
        [x["role"] for x in mapped.raw_record["messages"]]
        == ["user", "assistant", "tool", "assistant"],
    )  # type: ignore[union-attr]
    check("ROW-4", "group_key by letters", mapped.group_key == "grp")
    for text, expect in (
        ("", "required_field_empty"),
        ("[", "invalid_json"),
        ('[{"role":"user","role":"x","content":"a"}]', "duplicate_key"),
        ('[{"role":"user","content":NaN}]', "non_finite_number"),
        ('[{"role":"user","content":1e999}]', "non_finite_number"),
        ("[" * 65 + "]" * 65, "depth_exceeded"),
        ('[{"role":"user","content":"\\ud800"}]', "invalid_unicode"),
        ("9" * 4301, "number_out_of_range"),
        ("{}", "mapping_type_mismatch"),
        ("[]", "mapping_type_mismatch"),
        ('["x"]', "mapping_type_mismatch"),
        ('[{"role":"alien","content":"a"}]', "unmapped_role"),
    ):
        outcome = _row_error(m, [text, "x", "g"], headers=headers)
        check(
            "ROW-4",
            f"cell {text[:25]!r} -> {expect}",
            outcome is not None
            and outcome[0] == expect
            and outcome[1] in ("messages", "messages[0]"),
        )
    check(
        "ROW-4",
        "a missing cell is required_field_missing",
        _row_error(m, [], headers=headers) == ("required_field_missing", "messages"),
    )
    check(
        "ROW-4",
        "a null cell is required_field_missing",
        _row_error(m, [None, "x", "g"], headers=headers) == ("required_field_missing", "messages"),
    )
    check(
        "ROW-4",
        "an already-decoded list is used as-is on an object row",
        _row_error(
            mapping_of({**messages_json_doc(), "messages": {"key": "m"}}),
            {"m": [{"role": "user", "content": "a"}]},
        )
        is None,
    )
    check(
        "ROW-4",
        "a non-str non-list cell is mapping_type_mismatch",
        _row_error(m, [5, "x", "g"], headers=headers) == ("mapping_type_mismatch", "messages"),
    )


def test_row_event_timestamp_and_metadata() -> None:
    m = _pc(
        {
            "metadata": {
                "event_timestamp": {"key": "ts"},
                "language": {"key": "lang"},
                "quality_label": {"key": "ql"},
                "consent": {"key": "consent"},
            }
        }
    )
    for value in (
        "2026-10-13T10:00:00Z",
        "2026-10-13T10:00:00+03:00",
        "2026-10-13T10:00:00.123456-05:30",
        "2026-10-13 10:00:00+00:00",
        "2026-10-13T10:00:00Z "[:-1],
    ):
        got = ic.apply_mapping(m, {"p": "q", "c": "a", "ts": value})
        check(
            "ROW-5",
            f"{value!r} is accepted and kept as written",
            got.raw_record["metadata"] == {"event_timestamp": value},
        )
    for value in (
        "2026-10-13T10:00:00",
        "2026-10-13",
        "10:00:00Z",
        "yesterday",
        "2026-13-01T00:00:00Z",
        "2026-10-13T25:00:00Z",
        "2026-10-13T10:00:00+99:00",
        "",
        " ",
    ):
        outcome = _row_error(m, {"p": "q", "c": "a", "ts": value})
        check(
            "ROW-5",
            f"{value!r} is invalid_event_timestamp",
            outcome == ("invalid_event_timestamp", "metadata.event_timestamp"),
        )
    check(
        "ROW-5",
        "a non-str timestamp is mapping_type_mismatch",
        _row_error(m, {"p": "q", "c": "a", "ts": 1760349600})
        == ("mapping_type_mismatch", "metadata.event_timestamp"),
    )
    got = ic.apply_mapping(m, {"p": "q", "c": "a"})
    check(
        "ROW-5",
        "absent metadata is omitted, never defaulted (quality_label included)",
        got.raw_record["metadata"] == {},
    )
    got = ic.apply_mapping(
        m, {"p": "q", "c": "a", "ql": "passed", "lang": "ar", "consent": "granted"}
    )
    check(
        "ROW-5",
        "present metadata is carried verbatim",
        got.raw_record["metadata"]
        == {"quality_label": "passed", "language": "ar", "consent": "granted"},
    )
    record = ic.normalize_mapped_record(
        m, got, source_record_index=0, dataset_sha256=SHA_X, source_type="upload"
    )
    candidate = ic.to_dataset_record_candidate(
        record, record_index=0, tool_schema_status="not_applicable"
    )
    check(
        "ROW-5",
        "a source quality_label never becomes quality_status",
        candidate["quality_status"] == "pending",
    )
    check(
        "ROW-5",
        "a source consent value never becomes pii_status",
        candidate["pii_status"] == "unscanned",
    )
    tab = mapping_of(
        {
            **prompt_completion_doc(),
            "prompt": {"index": 0},
            "completion": {"index": 1},
            "system": {"index": 2},
            "group_key": {"index": 3},
            "metadata": {"quality_label": {"index": 4}, "language": {"index": 5}},
        }
    )
    got = ic.apply_mapping(tab, ["q", "a", "", "", "", "en"])
    check(
        "ROW-5",
        "an empty tabular metadata cell is omitted",
        got.raw_record["metadata"] == {"language": "en"},
    )
    check("ROW-5", "an empty optional group_key cell is omitted", got.group_key is None)


def test_row_data_never_decides_purpose_or_split() -> None:
    m = _pc()
    row = {
        "p": "q",
        "c": "a",
        "purpose": "held_out_evaluation",
        "split": "test",
        "dataset_purpose": "validation",
        "metadata": {"purpose": "x", "split": "y"},
    }
    mapped = ic.apply_mapping(m, row)
    record = ic.normalize_mapped_record(
        m, mapped, source_record_index=0, dataset_sha256=SHA_X, source_type="upload"
    )
    candidate = ic.to_dataset_record_candidate(
        record, record_index=0, tool_schema_status="not_applicable"
    )
    dumped = json.dumps(candidate)
    check(
        "ROW-6",
        "no purpose/split value from the data reaches the candidate",
        "held_out_evaluation" not in dumped
        and "validation" not in dumped
        and '"test"' not in dumped,
    )
    check(
        "ROW-6", "the metadata is exactly the mapped slots", candidate["content"]["metadata"] == {}
    )  # type: ignore[index]
    acc = ic.ImportAccumulator(
        importer="jsonl",
        importer_version="1",
        mapping_sha256=m.mapping_sha256,
        dataset_purpose="training",
        expected_sha256=SHA_X,
    )
    acc.add(ic.AcceptedRecord(candidate))
    report = acc.report(source_sha256=SHA_X, source_bytes=1)
    check(
        "ROW-6",
        "the report's purpose is the dataset's, as passed",
        report.dataset_purpose == "training",
    )
    leaky = _pc({"metadata": {"language": {"key": "purpose"}}})
    got = ic.apply_mapping(leaky, row)
    acc2 = ic.ImportAccumulator(
        importer="jsonl",
        importer_version="1",
        mapping_sha256=leaky.mapping_sha256,
        dataset_purpose="training",
        expected_sha256=SHA_X,
    )
    acc2.add(ic.AcceptedRecord({"content": {"metadata": got.raw_record["metadata"]}}))
    check(
        "ROW-6",
        "even a mapped 'purpose' value cannot change the report's purpose",
        acc2.report(source_sha256=SHA_X, source_bytes=1).dataset_purpose == "training",
    )


def test_row_normalization_failures_are_canonical_schema_invalid() -> None:
    marker = support.make_marker("norm")
    m = mapping_of(
        {
            **messages_doc(),
            "on_unmapped_message_keys": "ignore",
            "role_map": {"human": "user", "gpt": "assistant", "fn": "tool", "sys": "system"},
        }
    )

    def run(turns: list[dict[str, Any]]) -> ic.RowMappingError | None:
        mapped = ic.apply_mapping(m, {"conversation": {"turns": turns}})
        return raises(
            ic.RowMappingError,
            ic.normalize_mapped_record,
            m,
            mapped,
            source_record_index=3,
            dataset_sha256=SHA_X,
            source_type="upload",
        )

    cases = {
        "user after user": (
            [{"from": "human", "value": "a"}, {"from": "human", "value": "b"}],
            "messages[1]",
        ),
        "tool without a call": (
            [
                {"from": "human", "value": "a"},
                {"from": "fn", "value": "r", "call_id": "z", "tool": marker},
            ],
            "messages[1]",
        ),
        "system after body": (
            [{"from": "human", "value": "a"}, {"from": "sys", "value": "s"}],
            "messages[1]",
        ),
        "empty content list": ([{"from": "human", "value": []}], "messages[0]"),
    }
    for label, (turns, slot) in cases.items():
        error = run(turns)
        check(
            "ROW-7",
            f"{label}: canonical_schema_invalid at {slot}",
            error is not None and (error.code, error.slot) == ("canonical_schema_invalid", slot),
        )
        if error is not None:
            check(
                "ROW-7",
                f"{label}: the FTF-0506 message is not carried",
                support.leaked_markers(
                    error, [marker, "cannot follow", "unmapped", "pending tool call"]
                )
                == [],
            )
            check(
                "ROW-7",
                f"{label}: raised from None",
                error.__cause__ is None and error.__suppress_context__,
            )
    bad_user = mapping_of({**prompt_completion_doc(), "system": {"key": "s"}})
    mapped = ic.apply_mapping(bad_user, {"p": "q", "c": "a"})
    for index in (-1, True, 1.5, None):
        check(
            "ROW-7",
            f"source_record_index={index!r} refused",
            raises(
                ValueError,
                ic.normalize_mapped_record,
                bad_user,
                mapped,
                source_record_index=index,
                dataset_sha256=SHA_X,
                source_type="upload",
            )
            is not None,
        )
    for digest in ("", "A" * 64, "a" * 63, None, 5):
        check(
            "ROW-7",
            f"dataset_sha256={digest!r} refused",
            raises(
                ValueError,
                ic.normalize_mapped_record,
                bad_user,
                mapped,
                source_record_index=0,
                dataset_sha256=digest,
                source_type="upload",
            )
            is not None,
        )
    check(
        "ROW-7",
        "RowMappingError refuses an uncatalogued slot",
        raises(ValueError, ic.RowMappingError, "unmapped_role", "Has Space") is not None,
    )
    check(
        "ROW-7",
        "the empty tool_schemas default does not accumulate",
        ic.normalize_mapped_record(
            bad_user, mapped, source_record_index=0, dataset_sha256=SHA_X, source_type="upload"
        )
        .messages[0]
        .role
        == "user",
    )
    grouped_a = ic.normalize_mapped_record(
        m,
        ic.apply_mapping(
            m, {"conversation": {"turns": [{"from": "human", "value": "a"}]}, "conv": "G"}
        ),
        source_record_index=0,
        dataset_sha256=SHA_X,
        source_type="upload",
    )
    grouped_b = ic.normalize_mapped_record(
        m,
        ic.apply_mapping(
            m, {"conversation": {"turns": [{"from": "human", "value": "b"}]}, "conv": "G"}
        ),
        source_record_index=1,
        dataset_sha256=SHA_X,
        source_type="upload",
    )
    solo = ic.normalize_mapped_record(
        m,
        ic.apply_mapping(m, {"conversation": {"turns": [{"from": "human", "value": "b"}]}}),
        source_record_index=2,
        dataset_sha256=SHA_X,
        source_type="upload",
    )
    check(
        "ROW-7",
        "records sharing a group_key slot share a group id",
        grouped_a.group_id == grouped_b.group_id,
    )
    check(
        "ROW-7",
        "a record with no group_key is its own group",
        solo.group_id not in (grouped_a.group_id, grouped_b.group_id),
    )


# --- DET ----------------------------------------------------------------------------

_DET_SCRIPT = r"""
import hashlib, json, sys
sys.path.insert(0, sys.argv[1])
from workers.data import import_common as ic
doc = {"shape": "messages", "messages_pointer": "/m", "role_key": "r", "content_key": "c",
       "role_map": {"u": "user", "a": "assistant"}, "on_unmapped_message_keys": "ignore",
       "group_key": {"key": "g"}, "metadata": {"language": {"key": "l"}, "consent": {"key": "k"}}}
mapping = ic.mapping_from_object(doc)
payload = b"line1\nline2\n"
digest = hashlib.sha256(payload).hexdigest()
events, hashes = [], []
for i, text in enumerate(["alpha", "beta", "gamma"]):
    row = {"m": [{"r": "u", "c": text}, {"r": "a", "c": text[::-1]}],
           "g": "G" + str(i % 2), "l": "en", "k": "yes"}
    mapped = ic.apply_mapping(mapping, row)
    rec = ic.normalize_mapped_record(mapping, mapped, source_record_index=i,
                                     dataset_sha256=digest, source_type="upload")
    cand = ic.to_dataset_record_candidate(rec, record_index=i, tool_schema_status="not_applicable")
    hashes.append([rec.record_id, rec.group_id, rec.canonical_hash, cand["record_sha256"]])
    events.append(ic.AcceptedRecord(cand))
events.append(ic.RowRejection("line:4", "unmapped_role", "messages[1]"))
events.append(ic.RowRejection("line:5", "canonical_schema_invalid", "messages[0]"))
events.append(ic.BlankLine())
reader = ic.ChunkReader([payload[:3], payload[3:]], expected_size=len(payload),
                        expected_sha256=digest)
list(reader)
report = ic.run_import(iter(events), reader, importer="jsonl", importer_version="1",
                       mapping=mapping, dataset_purpose="training", expected_sha256=digest)
print(json.dumps([mapping.mapping_sha256, hashes, report.report_sha256]))
"""


def test_det_runs_and_hash_seeds_agree() -> None:
    outputs = []
    for seed in ("0", "1", "2", "12345", "random"):
        done = subprocess.run(
            [sys.executable, "-I", "-c", _DET_SCRIPT, str(ROOT)],
            capture_output=True,
            text=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
            check=False,
        )
        check(
            "DET-1",
            f"PYTHONHASHSEED={seed} run succeeds: {done.stderr[-300:]}",
            done.returncode == 0,
        )
        outputs.append(done.stdout)
    check(
        "DET-1",
        "every PYTHONHASHSEED produces byte-identical ids and hashes",
        len(set(outputs)) == 1 and outputs[0] != "",
    )
    again = subprocess.run(
        [sys.executable, "-I", "-c", _DET_SCRIPT, str(ROOT)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )
    check("DET-1", "a second run in a fresh process agrees", again.stdout == outputs[0])
    parsed = json.loads(outputs[0])
    ids = [h[0] for h in parsed[1]]
    check(
        "DET-1",
        "record ids are uuid5 values, one per record",
        len(set(ids)) == 3 and all(uuid.UUID(i).version == 5 for i in ids),
    )


def test_det_record_id_depends_on_exactly_dataset_mapping_and_index() -> None:
    m1, m2 = _pc(), _pc({"completion": {"key": "other"}})

    def rid(mapping: ic.ImportMapping, digest: str, index: int) -> str:
        row = {"p": "q", "c": "a", "other": "a"}
        return ic.normalize_mapped_record(
            mapping,
            ic.apply_mapping(mapping, row),
            source_record_index=index,
            dataset_sha256=digest,
            source_type="upload",
        ).record_id

    base = rid(m1, SHA_X, 0)
    check(
        "DET-2",
        "the same inputs give the same id",
        rid(m1, SHA_X, 0) == base == rid(_pc(), SHA_X, 0),
    )
    check("DET-2", "a different dataset digest changes the id", rid(m1, "b" * 64, 0) != base)
    check("DET-2", "a different mapping changes the id", rid(m2, SHA_X, 0) != base)
    check("DET-2", "a different index changes the id", rid(m1, SHA_X, 1) != base)
    expected = str(uuid.uuid5(ic.IMPORTER_RECORD_ID_NAMESPACE, f"{SHA_X}:{m1.mapping_sha256}:0"))
    check("DET-2", "the id is uuid5(namespace, dataset:mapping:index)", base == expected)
    check(
        "DET-2",
        "the id is not content-derived (two rows, one index, one id)",
        rid(m1, SHA_X, 0)
        == ic.normalize_mapped_record(
            m1,
            ic.apply_mapping(m1, {"p": "zzz", "c": "yyy"}),
            source_record_index=0,
            dataset_sha256=SHA_X,
            source_type="upload",
        ).record_id,
    )
    report_a, report_b = _sample_report(), _sample_report()
    check(
        "DET-2",
        "an identical import gives an identical report_sha256",
        report_a.report_sha256 == report_b.report_sha256 and report_a == report_b,
    )


# --- LEAK ---------------------------------------------------------------------------


def test_leak_no_source_text_in_any_output() -> None:
    value_m, key_m, tool_m, role_m, norm_m, map_m, src_m = (
        support.make_marker(x) for x in ("value", "key", "tool", "role", "norm", "mapkey", "srcerr")
    )
    markers = [value_m, key_m, tool_m, role_m, norm_m, map_m, src_m]
    mapping = mapping_of({**messages_doc(), "on_unmapped_message_keys": "reject"})
    rows: list[dict[str, Any]] = [
        {"conversation": {"turns": [{"from": role_m, "value": value_m}]}},
        {"conversation": {"turns": [{"from": "human", "value": value_m, key_m: value_m}]}},
        {
            "conversation": {
                "turns": [
                    {"from": "human", "value": value_m},
                    {
                        "from": "gpt",
                        "value": "",
                        "calls": [{"id": "c1", "name": tool_m, "arguments": {key_m: value_m}}],
                    },
                    {"from": "fn", "value": value_m, "call_id": "c1", "tool": norm_m},
                ]
            }
        },
        {
            "conversation": {
                "turns": [{"from": "human", "value": value_m}, {"from": "human", "value": value_m}]
            }
        },
        {"conversation": {"turns": [{"from": "human", "value": 5, key_m: value_m}]}},
        {"conversation": {"turns": [value_m]}},
        {value_m: value_m},
        {
            "conversation": {
                "turns": [
                    {"from": "sys", "value": value_m},
                    {"from": "human", "value": value_m},
                    {"from": "sys", "value": value_m},
                ]
            }
        },
    ]
    produced: list[object] = []
    rejections: list[ic.RowRejection] = []
    for i, row in enumerate(rows):
        try:
            mapped = ic.apply_mapping(mapping, row)
            ic.normalize_mapped_record(
                mapping, mapped, source_record_index=i, dataset_sha256=SHA_X, source_type="upload"
            )
        except ic.RowMappingError as exc:
            produced.append(exc)
            rejections.append(ic.RowRejection(f"line:{i}", exc.code, exc.slot))
    check(
        "LEAK-1",
        f"the leak rows were all refused ({len(rejections)} of {len(rows)})",
        len(rejections) >= 7,
    )
    for exc in produced:
        check(
            "LEAK-1",
            f"RowMappingError {exc} carries no marker",
            support.leaked_markers(exc, markers) == [],
        )
        check(
            "LEAK-1",
            "RowMappingError has no cause and no visible context",
            exc.__cause__ is None and (exc.__context__ is None or exc.__suppress_context__),
        )
    reader = _reader(PAYLOAD)
    report = _import(_pulling(reader, list(rejections)), reader)
    check(
        "LEAK-1",
        "the report carries no marker (fields, repr or JSON)",
        support.leaked_markers(report, markers) == []
        and support.leaked_markers(report.body(), markers) == [],
    )
    check(
        "LEAK-1",
        "the rejections carry no marker",
        support.leaked_markers(rejections, markers) == [],
    )
    check(
        "LEAK-1",
        "the checker can see a marker (non-vacuous)",
        support.leaked_markers({"x": [value_m]}, markers) == [value_m]
        and support.leaked_markers(RuntimeError(key_m), markers) == [key_m],
    )

    source = support.ExplodingSource([b"ab"], OSError(src_m), at=1)
    aborted = _drain(ic.ChunkReader(source, expected_size=4, expected_sha256=SHA_X))
    check(
        "LEAK-1",
        "a source error's text is not carried by ImportAborted",
        aborted is not None and support.leaked_markers(aborted, markers) == [],
    )
    for text in (
        f'{{"{key_m}":1,"{key_m}":2}}',
        f'{{"{key_m}": NaN}}',
        f'["{value_m}", ',
        f'"{value_m}\\ud800"',
        f"{value_m}",
    ):
        exc = raises(ic.StrictJsonError, ic.strict_loads, text)
        check(
            "LEAK-1",
            f"StrictJsonError for {text[:24]!r} carries no marker",
            exc is not None and support.leaked_markers(exc, markers) == [],
        )
    for doc in (
        {**prompt_completion_doc(), map_m: 1},
        {**prompt_completion_doc(), "prompt": {map_m: "x"}},
        {**prompt_completion_doc(), "metadata": {map_m: {"key": "x"}}},
        {**messages_doc(), "role_map": {map_m: map_m}},
        {**messages_doc(), "messages_pointer": "/" + map_m + "~2"},
    ):
        exc = raises(ic.MappingInvalidError, ic.mapping_from_object, doc)
        check(
            "LEAK-1",
            f"MappingInvalidError for a bad mapping carries no marker ({exc})",
            exc is not None and support.leaked_markers(exc, [map_m]) == [],
        )
    text = json.dumps({**prompt_completion_doc(), map_m: 1})
    exc = raises(ic.MappingInvalidError, ic.parse_mapping, text)
    check(
        "LEAK-1",
        "parse_mapping's refusal carries no marker",
        exc is not None and support.leaked_markers(exc, [map_m]) == [],
    )
    bad_text = '{"shape":"prompt_completion","prompt":{"key":"' + map_m + '"},"prompt":1}'
    exc = raises(ic.MappingInvalidError, ic.parse_mapping, bad_text)
    check(
        "LEAK-1",
        "a duplicate-key refusal does not echo the key",
        exc is not None and support.leaked_markers(exc, [map_m]) == [] and exc.__cause__ is None,
    )
    check(
        "LEAK-1",
        "a mapping_invalid refusal is raised from None",
        exc is not None and exc.__suppress_context__,
    )


# --- PREV ---------------------------------------------------------------------------


def test_prev_limit_and_shared_path() -> None:
    events = [_accepted(i) for i in range(25)]
    for limit in (1, 5, 20):
        result = ic.preview(iter(events), limit)
        check(
            "PREV-1",
            f"preview({limit}) returns exactly {limit} accepted records",
            len(result.accepted) == limit,
        )
        check("PREV-1", f"preview({limit}) of 25 is not complete", result.complete is False)
    for bad in (21, 0, -1, True, 1.0, None, "5", 100):
        check(
            "PREV-1",
            f"preview({bad!r}) is refused",
            raises(ValueError, ic.preview, iter(events), bad) is not None,
        )
    lowered = replace(ic.DEFAULT_LIMITS, preview_max_records=3)
    check(
        "PREV-1",
        "a lowered preview maximum applies",
        len(ic.preview(iter(events), 3, limits=lowered).accepted) == 3
        and raises(ValueError, ic.preview, iter(events), 4, limits=lowered) is not None,
    )
    pulled: list[int] = []

    def stream() -> Iterator[ic.ImportEvent]:
        for i in range(25):
            pulled.append(i)
            yield _accepted(i)

    ic.preview(stream(), 5)
    check("PREV-1", "preview stops pulling events once it has what it asked for", len(pulled) == 5)
    short = ic.preview(iter(events[:3]), 20)
    check(
        "PREV-1", "a short stream is complete", short.complete is True and len(short.accepted) == 3
    )
    mixed = [
        ic.RowRejection("line:1", "unmapped_role"),
        _accepted(0),
        ic.BlankLine(),
        ic.RowRejection("line:3", "record_too_large"),
        _accepted(1),
    ]
    result = ic.preview(iter(mixed), 20)
    check(
        "PREV-1",
        "rejections seen on the way are returned",
        [r.locator for r in result.rejections] == ["line:1", "line:3"],
    )
    full_events = [_accepted(i) for i in range(4)]
    full: list[object] = []
    reader = _reader(PAYLOAD)
    ic.run_import(
        _pulling(reader, list(full_events)),
        reader,
        importer="jsonl",
        importer_version="1",
        mapping=mapping_of(prompt_completion_doc()),
        dataset_purpose="training",
        expected_sha256=SHA_X,
        on_accepted=full.append,
    )
    check(
        "PREV-1",
        "preview and a full import accept the same records",
        list(ic.preview(iter(full_events), 4).accepted) == full,
    )
    budget = replace(ic.DEFAULT_LIMITS, max_rejections=1)
    result = ic.preview(
        iter(
            [
                ic.RowRejection("line:1", "unmapped_role"),
                ic.RowRejection("line:2", "unmapped_role"),
                _accepted(0),
            ]
        ),
        5,
        limits=budget,
    )
    check(
        "PREV-1",
        "a preview honours the rejection budget (same code path)",
        result.complete is False and len(result.rejections) == 1 and len(result.accepted) == 0,
    )
    calls: list[int] = []
    original = ic.ImportAccumulator.add

    def counting(self: ic.ImportAccumulator, event: ic.ImportEvent) -> bool:
        calls.append(1)
        return original(self, event)

    ic.ImportAccumulator.add = counting  # type: ignore[method-assign]
    try:
        ic.preview(iter(events[:4]), 20)
    finally:
        ic.ImportAccumulator.add = original  # type: ignore[method-assign]
    check("PREV-1", "preview goes through ImportAccumulator.add for every event", len(calls) == 4)


# --- SRC ----------------------------------------------------------------------------

ORG, OTHER_ORG = uuid.uuid4(), uuid.uuid4()
PROJECT, OTHER_PROJECT = uuid.uuid4(), uuid.uuid4()
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
SRC_BYTES = b'{"p":"hello","c":"world"}\n' * 40
BUCKET = "sovra-datasets"


class FakeS3:
    """Records every call; any method other than stream_object is a failure."""

    def __init__(self, data: bytes = SRC_BYTES) -> None:
        self.data = data
        self.gets: list[tuple[str, str, int]] = []
        self.fail_at: int | None = None
        self.error: BaseException = S3Error(503, "SlowDown", "slow", "")

    def stream_object(
        self, bucket: str, key: str, *, chunk_size: int = 1024 * 1024
    ) -> Iterator[bytes]:
        self.gets.append((bucket, key, chunk_size))
        pieces = support.split_chunks(self.data, 64)
        for i, piece in enumerate(pieces):
            if self.fail_at is not None and i == self.fail_at:
                raise self.error
            yield piece

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"dataset_object_source called S3Client.{name}")


def _tenant_execute(session: Session, organization_id: uuid.UUID, statement: Any) -> Any:
    rows = [
        r
        for r in session.execute(statement).scalars().all()
        if r.organization_id == organization_id
    ]
    return SimpleNamespace(
        scalars=lambda: SimpleNamespace(
            one_or_none=lambda: rows[0] if rows else None, all=lambda: rows
        )
    )


@contextlib.contextmanager
def _src_env() -> Iterator[tuple[Session, dict[str, Any]]]:
    engine = create_engine("sqlite://")
    store.DatasetUploadSession.__table__.create(engine)  # type: ignore[attr-defined]
    store.Dataset.__table__.create(engine)  # type: ignore[attr-defined]
    saved = store.execute_tenant_scoped
    store.execute_tenant_scoped = _tenant_execute  # type: ignore[assignment]
    try:
        with Session(engine) as session:
            yield session, _seed(session)
    finally:
        store.execute_tenant_scoped = saved
        engine.dispose()


def _seed(session: Session) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, purpose, org, project in (
        ("train", "training", ORG, PROJECT),
        ("held", "held_out_evaluation", ORG, PROJECT),
        ("foreign", "training", OTHER_ORG, OTHER_PROJECT),
    ):
        sid, did = uuid.uuid4(), uuid.uuid4()
        key = f"tenants/{org}/projects/{project}/uploads/{sid}/source"
        session.add(
            store.DatasetUploadSession(
                id=sid,
                organization_id=org,
                project_id=project,
                dataset_source_connection_id=uuid.uuid4(),
                purpose=purpose,
                object_key=key,
                declared_size_bytes=len(SRC_BYTES),
                max_bytes=len(SRC_BYTES),
                declared_sha256=support.sha256_hex(SRC_BYTES),
                declared_content_type="application/jsonl",
                status="verified",
                verified_size_bytes=len(SRC_BYTES),
                verified_sha256=support.sha256_hex(SRC_BYTES),
                verified_content_type="application/jsonl",
                created_by="u",
                created_at=NOW,
                expires_at=NOW,
                finalized_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            store.Dataset(
                id=did,
                organization_id=org,
                project_id=project,
                name=label,
                version=1,
                purpose=purpose,
                upload_session_id=sid,
                upload_status="verified",
                verified_sha256=support.sha256_hex(SRC_BYTES),
                verified_size_bytes=len(SRC_BYTES),
                created_by="u",
                created_at=NOW,
            )
        )
        out[label] = SimpleNamespace(
            dataset_id=did, session_id=sid, key=key, org=org, project=project
        )
    session.commit()
    return out


def _open(
    session: Session,
    seed: Any,
    fake: FakeS3,
    *,
    org: uuid.UUID = ORG,
    include_held_out: bool = False,
    project: uuid.UUID | None = None,
    dataset_id: uuid.UUID | None = None,
) -> source_module.DatasetObjectSource:
    return source_module.open_dataset_source(
        session,
        SimpleNamespace(organization_id=org),  # type: ignore[arg-type]
        project_id=project or seed.project,
        dataset_id=dataset_id or seed.dataset_id,
        include_held_out=include_held_out,
        storage=source_module.DatasetObjectStorage(fake, BUCKET),
    )


def test_src_happy_path_is_lazy_one_get_and_exact() -> None:
    with _src_env() as (session, seeds):
        fake = FakeS3()
        seed = seeds["train"]
        src = _open(session, seed, fake)
        check("SRC-1", "opening makes no storage call", fake.gets == [])
        check(
            "SRC-1",
            "the source describes the dataset",
            (
                src.dataset_id,
                src.purpose,
                src.source_type,
                src.verified_content_type,
                src.expected_size,
                src.expected_sha256,
            )
            == (
                seed.dataset_id,
                "training",
                "upload",
                "application/jsonl",
                len(SRC_BYTES),
                support.sha256_hex(SRC_BYTES),
            ),
        )
        reader = ic.ChunkReader(
            src.chunks, expected_size=src.expected_size, expected_sha256=src.expected_sha256
        )
        got = b"".join(reader)
        check(
            "SRC-1",
            "the streamed bytes are the stored bytes and verify",
            got == SRC_BYTES and reader.sha256 == src.expected_sha256,
        )
        check(
            "SRC-1",
            "exactly one GET, of the session's own key, in the configured bucket",
            fake.gets == [(BUCKET, seed.key, 1024 * 1024)],
        )
        check(
            "SRC-1",
            "the object key is not in the returned value",
            seed.key not in repr(src) and str(seed.session_id) not in repr(src),
        )
        check(
            "SRC-1",
            "the source is immutable",
            raises(Exception, setattr, src, "purpose", "x") is not None,
        )
        check(
            "SRC-1",
            "SOURCE_TYPE is a valid FTF-0508 source type",
            source_module.SOURCE_TYPE == "upload",
        )
    params = inspect.signature(source_module.open_dataset_source).parameters
    check(
        "SRC-1",
        "include_held_out is keyword-only with no default",
        params["include_held_out"].kind is inspect.Parameter.KEYWORD_ONLY
        and params["include_held_out"].default is inspect.Parameter.empty,
    )
    check(
        "SRC-1",
        "every parameter after the context is keyword-only",
        all(
            p.kind is inspect.Parameter.KEYWORD_ONLY
            for n, p in params.items()
            if n not in ("session", "context")
        ),
    )
    check(
        "SRC-1",
        "no parameter can carry a caller-chosen object key",
        not any(n in params for n in ("key", "object_key", "bucket", "prefix", "path", "uri")),
    )


def test_src_scoping_is_indistinguishable_from_missing() -> None:
    with _src_env() as (session, seeds):
        fake = FakeS3()
        train, held, foreign = seeds["train"], seeds["held"], seeds["foreign"]
        wanted = uuid.uuid4()

        def failure(**kw: Any) -> str | None:
            exc = raises(
                store.DatasetNotFoundError, _open, session, kw.pop("seed", train), fake, **kw
            )
            return (
                None
                if exc is None
                else str(exc).replace(str(kw.get("dataset_id") or train.dataset_id), "<id>")
            )

        missing = raises(store.DatasetNotFoundError, _open, session, train, fake, dataset_id=wanted)
        foreign_org = raises(store.DatasetNotFoundError, _open, session, foreign, fake, org=ORG)
        foreign_project = raises(
            store.DatasetNotFoundError, _open, session, train, fake, project=OTHER_PROJECT
        )
        wrong_org_context = raises(
            store.DatasetNotFoundError, _open, session, train, fake, org=OTHER_ORG
        )
        hidden = raises(
            store.DatasetNotFoundError, _open, session, held, fake, include_held_out=False
        )
        check("SRC-2", "a missing dataset raises DatasetNotFoundError", missing is not None)
        check(
            "SRC-2",
            "another organization's dataset raises DatasetNotFoundError",
            foreign_org is not None,
        )
        check(
            "SRC-2",
            "another project's dataset raises DatasetNotFoundError",
            foreign_project is not None,
        )
        check(
            "SRC-2",
            "the right dataset under the wrong organization raises DatasetNotFoundError",
            wrong_org_context is not None,
        )
        check("SRC-2", "a hidden held-out dataset raises DatasetNotFoundError", hidden is not None)
        normalised = {
            "missing": str(missing).replace(str(wanted), "<id>"),
            "foreign org": str(foreign_org).replace(str(foreign.dataset_id), "<id>"),
            "foreign project": str(foreign_project).replace(str(train.dataset_id), "<id>"),
            "wrong org": str(wrong_org_context).replace(str(train.dataset_id), "<id>"),
            "hidden held-out": str(hidden).replace(str(held.dataset_id), "<id>"),
        }
        check(
            "SRC-2",
            f"all five failures read exactly alike: {normalised}",
            len(set(normalised.values())) == 1,
        )
        check("SRC-2", "none of them touched storage", fake.gets == [])
        got = _open(session, held, fake, include_held_out=True)
        check(
            "SRC-2",
            "a held-out dataset is readable with include_held_out=True",
            got.purpose == "held_out_evaluation",
        )
        check(
            "SRC-2",
            "an ordinary dataset is readable either way",
            _open(session, train, fake, include_held_out=True).purpose == "training",
        )
        for bad in (1, 0, "yes", None, "", [], 1.0):
            check(
                "SRC-2",
                f"include_held_out={bad!r} is a TypeError",
                raises(TypeError, _open, session, train, fake, include_held_out=bad) is not None,
            )
        check(
            "SRC-2",
            "ids that are not UUIDs are not-found",
            raises(store.DatasetNotFoundError, _open, session, train, fake, dataset_id="x")
            is not None,
        )  # type: ignore[arg-type]
        check(
            "SRC-2",
            "calling positionally is a TypeError",
            raises(
                TypeError,
                source_module.open_dataset_source,
                session,
                SimpleNamespace(organization_id=ORG),
                PROJECT,
                train.dataset_id,
                False,
            )
            is not None,
        )  # type: ignore[arg-type]
        check(
            "SRC-2",
            "omitting include_held_out is a TypeError",
            raises(
                TypeError,
                source_module.open_dataset_source,
                session,
                SimpleNamespace(organization_id=ORG),
                project_id=PROJECT,
                dataset_id=train.dataset_id,
                storage=source_module.DatasetObjectStorage(fake, BUCKET),
            )
            is not None,
        )  # type: ignore[call-arg]


def test_src_no_list_no_write_and_inconsistent_rows() -> None:
    with _src_env() as (session, seeds):
        fake = FakeS3()
        train = seeds["train"]
        for name in (
            "list_objects",
            "list_object_versions",
            "put_object",
            "delete_object",
            "get_object",
            "head_object",
        ):
            check(
                "SRC-3",
                f"the fake refuses {name} (non-vacuous guard)",
                raises(AssertionError, getattr, fake, name) is not None,
            )
        b"".join(_open(session, train, fake).chunks)
        check(
            "SRC-3",
            "reading a dataset used no client method but stream_object",
            len(fake.gets) == 1,
        )
        row = session.get(store.DatasetUploadSession, train.session_id)
        assert row is not None
        for field, value in (
            ("status", "open"),
            ("status", "rejected"),
            ("verified_sha256", "f" * 64),
            ("verified_size_bytes", 1),
            ("verified_content_type", None),
        ):
            original = getattr(row, field)
            setattr(row, field, value)
            session.flush()
            err = raises(source_module.DatasetSourceInconsistentError, _open, session, train, fake)
            check(
                "SRC-3",
                f"upload session {field}={value!r} is DatasetSourceInconsistentError",
                err is not None and err.__cause__ is None,
            )
            setattr(row, field, original)
            session.flush()
        session.delete(row)
        session.flush()
        gone = raises(store.DatasetNotFoundError, _open, session, train, fake)
        check(
            "SRC-3",
            "a dataset whose session row is gone is DatasetNotFoundError",
            gone is not None and gone.__cause__ is None and gone.__suppress_context__,
        )


def test_src_storage_failures_are_content_free_and_block_the_import() -> None:
    marker = support.make_marker("s3key")
    errors: list[BaseException] = [
        S3Error(503, "SlowDown", marker, marker),
        S3Error(404, "NoSuchKey", marker, ""),
        OSError(marker),
        TimeoutError(marker),
        ConnectionResetError(marker),
        ValueError(marker),
    ]
    import urllib.error

    errors.append(urllib.error.URLError(marker))
    with _src_env() as (session, seeds):
        train = seeds["train"]
        for error in errors:
            for at in (0, 2):
                fake = FakeS3()
                fake.fail_at, fake.error = at, error
                src = _open(session, train, fake)
                caught = raises(UploadStorageUnavailableError, _join, src.chunks)
                label = f"{type(error).__name__} at chunk {at}"
                check(
                    "SRC-4", f"{label}: becomes UploadStorageUnavailableError", caught is not None
                )
                if caught is not None:
                    check(
                        "SRC-4",
                        f"{label}: no key, marker or session id in the error",
                        support.leaked_markers(caught, [marker, train.key, str(train.session_id)])
                        == [],
                    )
                    check(
                        "SRC-4",
                        f"{label}: raised from None (the original, with the URL, is not chained)",
                        caught.__cause__ is None and caught.__suppress_context__,
                    )
                chain = ic.ChunkReader(
                    _open(session, train, _failing(error, at)).chunks,
                    expected_size=len(SRC_BYTES),
                    expected_sha256=support.sha256_hex(SRC_BYTES),
                )
                outcome = _drain(chain)
                check(
                    "SRC-4",
                    f"{label}: the import is blocked/source_unavailable end to end",
                    outcome is not None
                    and (outcome.state, outcome.failure_code) == ("blocked", "source_unavailable"),
                )
        short = FakeS3(SRC_BYTES[:-1])
        src = _open(session, train, short)
        caught = raises(UploadStorageUnavailableError, lambda: b"".join(src.chunks))
        check(
            "SRC-4",
            "a stream that ends one byte early is an outage, not a clean end",
            caught is not None,
        )
        outcome = _drain(
            ic.ChunkReader(
                _open(session, train, FakeS3(SRC_BYTES[:-1])).chunks,
                expected_size=len(SRC_BYTES),
                expected_sha256=support.sha256_hex(SRC_BYTES),
            )
        )
        check(
            "SRC-4",
            "...and the import is blocked, not failed/source_changed",
            outcome is not None
            and (outcome.state, outcome.failure_code) == ("blocked", "source_unavailable"),
        )
        empty = FakeS3(b"")
        check(
            "SRC-4",
            "an empty stream for a non-empty dataset is an outage",
            raises(
                UploadStorageUnavailableError, lambda: b"".join(_open(session, train, empty).chunks)
            )
            is not None,
        )
        longer = FakeS3(SRC_BYTES + b"x")
        outcome = _drain(
            ic.ChunkReader(
                _open(session, train, longer).chunks,
                expected_size=len(SRC_BYTES),
                expected_sha256=support.sha256_hex(SRC_BYTES),
            )
        )
        check(
            "SRC-4",
            "a longer object is a changed source, not an outage",
            outcome is not None
            and (outcome.state, outcome.failure_code) == ("failed", "source_changed"),
        )
        fake = FakeS3()
        fake.fail_at, fake.error = 1, RuntimeError(marker)
        src = _open(session, train, fake)
        err = raises(RuntimeError, lambda: b"".join(src.chunks))
        check(
            "SRC-4",
            "an error that is not a storage error is not relabelled as one",
            err is not None and not isinstance(err, UploadStorageUnavailableError),
        )


def _join(chunks: Iterator[bytes]) -> bytes:
    return b"".join(chunks)


def _failing(error: BaseException, at: int) -> FakeS3:
    fake = FakeS3()
    fake.fail_at, fake.error = at, error
    return fake


# --- wiring -------------------------------------------------------------------------


def test_wiring_ci_runs_this_suite_and_the_integration_job() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    check("CI-1", "ci.yml runs the pure suite", "python3 tests/tasks/test_ftf_0502_p1.py" in ci)
    check(
        "CI-1",
        "ci.yml defines dataset-import-source-integration",
        "\n  dataset-import-source-integration:\n" in ci,
    )
    check(
        "CI-1",
        "ci-verdict waits for dataset-import-source-integration",
        "      - dataset-import-source-integration\n" in ci,
    )
    check(
        "CI-1",
        "the job runs the PG16+S3 integration test",
        "tests/integration/test_ftf_0502_p1_dataset_source_postgres_s3.py" in ci,
    )
    check(
        "CI-1",
        "the integration test file exists",
        (
            ROOT / "tests" / "integration" / "test_ftf_0502_p1_dataset_source_postgres_s3.py"
        ).is_file(),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a crash is a failed test, reported by name
                FAILURES.append(f"FAIL [{name}] raised {type(exc).__name__}: {exc}")

    if FAILURES:
        print(f"FTF-0502-P1 dataset import common contract: {len(FAILURES)} failed")
        for failure in FAILURES[:200]:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0502-P1 dataset import common contract: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
