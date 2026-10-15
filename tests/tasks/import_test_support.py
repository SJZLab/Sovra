"""Shared generators and the META-LEAK checker for the dataset importers' tests
(FTF-0502-P1; reused by FTF-0502, FTF-0503, FTF-0504 and FTF-0505).

Not a test file: no `test_` functions, nothing runs on import apart from loading
the FTF-0616 round-8 Unicode variant generator, which is taken from that test
file's own source (parsed, not imported: importing it starts its servers).
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import os
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


class StrSub(str):
    """A `str` subclass: exact-type checks must refuse it."""


class IntSub(int):
    pass


NAN = float("nan")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wrong_type_values(original: object) -> list[object]:
    """Values that are not of `original`'s exact type, covering the strict-parse
    table: int-for-str, bool-for-int, float, null, list, nested object, NaN and
    a str subclass."""
    candidates: list[object] = [
        1,
        True,
        False,
        1.5,
        None,
        [],
        ["x"],
        {},
        {"a": {"b": 1}},
        StrSub("x"),
        IntSub(1),
        NAN,
    ]
    return [c for c in candidates if type(c) is not type(original)]


# --- the FTF-0616 round-8 generator ------------------------------------------------


def _load_r8_generator() -> Callable[[str], list[tuple[str, str, bool]]]:
    path = HERE / "test_ftf_0616.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assigned = {"_R8_REQUIRED_EDGE_CODEPOINTS", "_R8_FULL_SWEEP", "_R8_STRIDE", "_R8_POOLS"}
    functions = {"_r8_pools", "_generated_echo_variants"}
    body: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in assigned for t in node.targets
        ):
            body.append(node)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in assigned
        ):
            body.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in functions:
            body.append(node)
    names = {
        n.name if isinstance(n, ast.FunctionDef) else getattr(getattr(n, "target", None), "id", "")
        for n in body
    }
    if not functions <= names:
        raise RuntimeError("the FTF-0616 round-8 generator moved; update import_test_support")
    namespace: dict[str, Any] = {"unicodedata": unicodedata, "os": os}
    exec(compile(ast.Module(body, []), str(path), "exec"), namespace)  # noqa: S102
    generator: Callable[[str], list[tuple[str, str, bool]]] = namespace["_generated_echo_variants"]
    return generator


_GENERATOR: Callable[[str], list[tuple[str, str, bool]]] | None = None


def unicode_variants(base: str) -> list[tuple[str, str]]:
    """(label, variant) for every variant of `base` an exact comparison must
    refuse: the FTF-0616 round-8 generated set plus a fixed hand list."""
    global _GENERATOR  # noqa: PLW0603
    if _GENERATOR is None:
        _GENERATOR = _load_r8_generator()
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    hand = [
        ("upper", base.upper()),
        ("title", base.title()),
        ("leading space", " " + base),
        ("trailing space", base + " "),
        ("trailing newline", base + "\n"),
        ("trailing NUL", base + "\x00"),
        ("NFKC fullwidth", base.replace(base[0], chr(ord(base[0]) + 0xFEE0), 1)),
        ("zero-width inside", base[:1] + "​" + base[1:]),
        ("BOM prefix", "﻿" + base),
    ]
    for label, variant, _always in [(lab, var, a) for lab, var, a in _GENERATOR(base)]:
        if variant != base and variant not in seen:
            seen.add(variant)
            out.append((label, variant))
    for label, variant in hand:
        if variant != base and variant not in seen:
            seen.add(variant)
            out.append((label, variant))
    return out


# --- META-LEAK ---------------------------------------------------------------------


def make_marker(label: str) -> str:
    return f"META-LEAK-{label}-7f3a9c1d"


def _texts(value: object, seen: set[int], depth: int = 0) -> Iterator[str]:
    if depth > 12 or id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, str):
        yield value
    elif isinstance(value, bytes | bytearray):
        yield bytes(value).decode("utf-8", "replace")
    elif isinstance(value, BaseException):
        yield str(value)
        yield repr(value)
        for arg in value.args:
            yield from _texts(arg, seen, depth + 1)
        # What a traceback prints: the explicit cause, and the implicit context
        # unless `raise ... from None` suppressed it.
        chained_exceptions = [value.__cause__]
        if not value.__suppress_context__:
            chained_exceptions.append(value.__context__)
        for chained in chained_exceptions:
            if chained is not None:
                yield from _texts(chained, seen, depth + 1)
        for note in getattr(value, "__notes__", ()):
            yield from _texts(note, seen, depth + 1)
        for attribute in vars(value).values():
            yield from _texts(attribute, seen, depth + 1)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        yield repr(value)
        for item in dataclasses.fields(value):
            yield from _texts(getattr(value, item.name), seen, depth + 1)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _texts(key, seen, depth + 1)
            yield from _texts(item, seen, depth + 1)
    elif isinstance(value, list | tuple | set | frozenset):
        for item in value:
            yield from _texts(item, seen, depth + 1)
    else:
        yield repr(value)


def leaked_markers(value: object, markers: list[str]) -> list[str]:
    """The markers that appear anywhere in `value`: its text, repr, fields,
    exception args and the exception chain."""
    haystack = "\n".join(_texts(value, set()))
    try:
        haystack += "\n" + json.dumps(value, default=repr, ensure_ascii=False)
    except (TypeError, ValueError):
        pass
    return [m for m in markers if m in haystack]


# --- sources ------------------------------------------------------------------------


class ExplodingSource:
    """Yields `chunks`, then raises `error` instead of ending (or raises at
    position `at`, counted in chunks, when given)."""

    def __init__(self, chunks: list[bytes], error: BaseException, at: int | None = None) -> None:
        self._chunks = chunks
        self._error = error
        self._at = len(chunks) if at is None else at
        self._position = 0
        self.closed = False

    def __iter__(self) -> ExplodingSource:
        return self

    def __next__(self) -> bytes:
        if self._position >= self._at:
            raise self._error
        if self._position >= len(self._chunks):
            raise StopIteration
        chunk = self._chunks[self._position]
        self._position += 1
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def split_chunks(data: bytes, size: int) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)] or [b""]
