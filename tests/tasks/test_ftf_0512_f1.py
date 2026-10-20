#!/usr/bin/env python3
"""FTF-0512-F1 -- Normalized-text hash look-alike gaps.

Runnable without any database or network. Child slice of FTF-0512 (Law 15:
FTF-0512 is requalified; the FTF-0518/0513 suites are re-run by CI).

Found by the FTF-0518 verifier: fullwidth letters, zero-width characters and
a timestamp present on one side only got past all eight leakage methods
because FTF-0512's normalization kept them distinct. Covered here:

  - F1-LETTERS: generated table (letters x look-alike classes x positions);
    every look-alike spelling hashes like the plain one.
  - F1-FORMAT-CHARS: Cf characters, also INSIDE a UUID / timestamp, are inert.
  - F1-ONE-SIDED: a generated timestamp / UUID present on one side only.
  - F1-NO-COLLAPSE (negative, real default code): legitimately different texts,
    tool arguments, tool-call ids and JSON values stay distinct; a text that
    literally says "generated timestamp" does not collide with a timestamp.
  - F1-DIFFERENTIAL: a frozen copy of the pre-F1 normalization is compared
    over a corpus; the only pairs whose verdict changes are the intended ones.
  - F1-FAULTS: each class has a fault (a behavioural override of one seam on a
    fresh copy of the real module, no text anchors) that must turn a check RED.

FTF-0512-F2 (symbols and name fields) is covered by `run_f2_checks`:
  - F2-SYMBOL-PAIR / F2-STILL-EQUAL: math/currency/modifier/other symbols are
    distinct tokens ("x <= 5" != "x >= 5", "$100" != "EUR100"); P* is still stripped.
  - F2-SYMBOL-TABLE: every Sm/Sc/Sk/So code point in one fixed sentence is kept
    and distinct symbols never merge (all pairs, against an independent oracle).
  - F2-NAME: tool_calls[].name and a message `name` stay byte-for-byte.
  - F2-DIFFERENTIAL: over the repo's own prose only symbol-bearing texts change verdict.

FTF-0512-F3 (digit-adjacent punctuation) is covered by `run_f3_checks`:
  - F3-PAIR / F3-STILL-EQUAL: "-5", "5%", "#1", "5\u2032" stay distinct from "5"; "hello, world" still merges.
  - F3-PUNCT-TABLE: every Unicode P* code point x before/after a digit x a fixed sentence.
  - F3-NAME-NFC: an NFC/NFD pair in tool_calls[].name / message `name` stays distinct.
  - F3-DIFFERENTIAL: over the repo corpus only digit-adjacent-punctuation pairs un-merge.

Run directly: python3 tests/tasks/test_ftf_0512_f1.py
"""

# ruff: noqa: E501
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import types
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import workers.data.normalized_duplicate_detector as production  # noqa: E402

MODULE_PATH = ROOT / "workers" / "data" / "normalized_duplicate_detector.py"
FAILURES: list[str] = []

# Frozen digest of _conv("Hello") under detector_version 5 (golden pin).
FROZEN_SAMPLE_DIGEST = "7022b46370cd40ade19ff0c4faa3f62614a9308241247bffb6e34198e6b374e8"


def check(
    criterion_id: str, description: str, condition: bool, sink: list[str] | None = None
) -> None:
    if not condition:
        (FAILURES if sink is None else sink).append(f"FAIL [{criterion_id}] {description}")


def _conv(*texts: str) -> dict[str, object]:
    roles = ["user", "assistant"]
    return {
        "messages": [
            {"role": roles[i % 2], "content": [{"type": "text", "text": t}]}
            for i, t in enumerate(texts)
        ],
        "metadata": {},
    }


# --- look-alike classes -----------------------------------------------------

LETTERS = "aeksz"


def _fullwidth(ch: str) -> str:
    return chr(ord(ch) - 0x21 + 0xFF01)


def _math_bold(ch: str) -> str:
    return chr(ord(ch) - ord("a") + 0x1D41A)


def _circled(ch: str) -> str:
    return chr(ord(ch) - ord("a") + 0x24D0)


SUBSTITUTION_CLASSES: dict[str, Callable[[str], str]] = {
    "fullwidth": _fullwidth,
    "math_bold": _math_bold,
    "circled": _circled,
    "fullwidth_upper": lambda ch: _fullwidth(ch.upper()),
}
INSERTED_FORMAT_CHARS = {
    "zwsp": "\u200b",
    "zwnj": "\u200c",
    "zwj": "\u200d",
    "word_joiner": "\u2060",
    "bom": "\ufeff",
    "soft_hyphen": "\u00ad",
    "rlo": "\u202e",
    "lrm": "\u200e",
}


def _no_nl_code_points() -> list[int]:
    """Every Unicode code point whose category is No (superscript/subscript
    digits, vulgar fractions, circled numbers, ...) or Nl (Roman numerals,
    ...) -- the classes F1-NFKC-DIGIT-MERGE must keep distinct from any
    ASCII digit sequence. Computed from `unicodedata` directly, never a
    hand-picked subset (the same FB2 gap item 3 closes for Cf)."""
    return [cp for cp in range(0x110000) if unicodedata.category(chr(cp)) in ("No", "Nl")]


def _cf_code_points() -> list[int]:
    """Every Unicode code point whose category is Cf, computed from
    `unicodedata` directly rather than the 8-character
    `INSERTED_FORMAT_CHARS` sample those cases use for position coverage."""
    return [cp for cp in range(0x110000) if unicodedata.category(chr(cp)) == "Cf"]


def _positions(word: str) -> list[tuple[str, int]]:
    return [("start", 0), ("middle", len(word) // 2), ("end", len(word) - 1)]


def look_alike_cases() -> list[tuple[str, str, str]]:
    """(label, plain, variant) over letters x classes x positions."""
    cases: list[tuple[str, str, str]] = []
    for letter in LETTERS:
        word = f"{letter}ccount{letter}"
        plain = f"Please close the {word} today"
        for cls, sub in SUBSTITUTION_CLASSES.items():
            for pos_name, idx in _positions(word):
                if word[idx] != letter:
                    continue
                swapped = word[:idx] + sub(word[idx]) + word[idx + 1 :]
                cases.append((f"{cls}/{letter}/{pos_name}", plain, plain.replace(word, swapped)))
        for cls, ch in INSERTED_FORMAT_CHARS.items():
            for pos_name, idx in _positions(word):
                inserted = word[:idx] + ch + word[idx:]
                cases.append((f"{cls}/{letter}/{pos_name}", plain, plain.replace(word, inserted)))
    # a Cf character splitting a composition sequence (e + ZWSP + U+0301 == e-acute)
    cases.append(("composition_split", "Cafe\u0301 open", "Cafe\u200b\u0301 open"))
    cases.append(("composition_split_precomposed", "Caf\u00e9 open", "Cafe\u200b\u0301 open"))
    return cases


def generated_cases() -> list[tuple[str, str, str]]:
    """(label, with-generated, without-generated) for one-sided presence."""
    stamps = [
        "2026-01-01T12:00:00Z",
        "2026-01-01 12:00:00",
        "2026-01-01T12:00:00.123456+03:00",
        "2026-01-01t12:00:00z",
    ]
    uuid = "3f2e1a10-9b4c-4d3e-8f1a-2b3c4d5e6f70"
    cases: list[tuple[str, str, str]] = []
    for gen in [*stamps, uuid]:
        kind = "uuid" if gen == uuid else "timestamp"
        cases.append((f"{kind}/{gen}/end", f"Event recorded {gen}", "Event recorded"))
        cases.append((f"{kind}/{gen}/middle", f"Event {gen} recorded", "Event recorded"))
        cases.append((f"{kind}/{gen}/start", f"{gen} Event recorded", "Event recorded"))
        cases.append((f"{kind}/{gen}/punct", f"Event recorded ({gen}).", "Event recorded"))
    return cases


def format_char_inside_generated_cases() -> list[tuple[str, str, str]]:
    uuid = "3f2e1a10-9b4c-4d3e-8f1a-2b3c4d5e6f70"
    stamp = "2026-01-01T12:00:00Z"
    cases = []
    for cls, ch in INSERTED_FORMAT_CHARS.items():
        cases.append(
            (f"uuid_zw/{cls}", f"Ticket {uuid[:10]}{ch}{uuid[10:]} resolved", "Ticket resolved")
        )
        cases.append((f"ts_zw/{cls}", f"Seen {stamp[:12]}{ch}{stamp[12:]} ok", "Seen ok"))
    # fullwidth digits/separators in a timestamp fold to the ASCII shape first
    fw_stamp = "".join(_fullwidth(c) if c.isdigit() or c in "-:T" else c for c in stamp)
    cases.append(("ts_fullwidth", f"Seen {fw_stamp} ok", "Seen ok"))
    return cases


# --- distinctness corpus (negative) -------------------------------------------

DISTINCT_CORPUS = [
    "refunds are forbidden",
    "refunds are allowed",
    "Order 1001 has shipped",
    "Order 1002 has shipped",
    "Hello, how can I help you today?",
    "Hello, how can I help you tomorrow?",
    "generated timestamp",
    "generated id",
    "The account is locked",
    "The account is unlocked",
    "Approve the transfer",
    "Do not approve the transfer",
    "Reset password for admin",
    "Reset password for guest",
    "Ticket resolved",
    "Ticket reopened",
    "Event recorded",
    "Event deleted",
    "ключ доступа",
    "ключ доступен",
    "日本語のテキスト",
    "日本語のテキスト二",
    "naïve approach",
    "naive approach",
]

# Pairs whose verdict is INTENDED to change in F1 (old: distinct, new: same).
INTENDED_COLLAPSES: list[tuple[str, str]] = [
    ("Event recorded 2026-01-01T12:00:00Z", "Event recorded"),
    ("\uff2f\uff52\uff44\uff45\uff52 1001 has shipped", "Order 1001 has shipped"),
    ("Tick\u200bet resolved", "Ticket resolved"),
]

_OLD_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_OLD_TS = re.compile(r"\b\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:z|[+-]\d{2}:\d{2})?\b")


def frozen_pre_f1_normalize(text: str) -> str:
    lowered = text.lower()
    redacted = _OLD_TS.sub("<generated_timestamp>", lowered)
    redacted = _OLD_UUID.sub("<generated_id>", redacted)
    no_punct = re.sub(r"[^\w\s]", " ", redacted)
    return re.sub(r"\s+", " ", no_punct).strip()


def _nfkd_non_starter_code_points() -> list[int]:
    return [
        cp
        for cp in range(0x110000)
        if any(unicodedata.combining(part) for part in unicodedata.normalize("NFKD", chr(cp)))
    ]


def _trailing_non_starters(char: str) -> int:
    count = 0
    for part in reversed(unicodedata.normalize("NFKD", char)):
        if not unicodedata.combining(part):
            break
        count += 1
    return count


def _accepts(mod: types.ModuleType, text: str) -> bool:
    try:
        mod._normalize_text(text)
    except mod.NormalizedDuplicateDetectionError:
        return False
    return True


# The test's own statement of which NFKC compatibility mappings are a mere
# glyph variant of the same character (look-alikes that must keep folding).
GLYPH_VARIANT_TAGS = frozenset(
    {
        "<wide>",
        "<narrow>",
        "<font>",
        "<circle>",
        "<initial>",
        "<medial>",
        "<final>",
        "<isolated>",
        "<vertical>",
        "<small>",
    }
)


def _compat_fold_code_points() -> list[tuple[int, str, str, str | None, str]]:
    """(cp, char, NFKC(char), decomposition tag, category) for every code point
    whose NFKC mapping changes it and yields an alphanumeric, excluding the
    No/Nl categories (kept verbatim, checked by F1-NFKC-DIGIT-MERGE)."""
    rows = []
    for cp in range(0x110000):
        char = chr(cp)
        mapped = unicodedata.normalize("NFKC", char)
        category = unicodedata.category(char)
        if mapped == char or category in ("No", "Nl"):
            continue
        if not any(part.isalnum() for part in mapped):
            continue
        decomposition = unicodedata.decomposition(char)
        tag = decomposition.split(" ", 1)[0] if decomposition.startswith("<") else None
        rows.append((cp, char, mapped, tag, category))
    return rows


def _is_glyph_variant(tag: str | None, category: str) -> bool:
    if tag is None or tag in GLYPH_VARIANT_TAGS:
        return True
    return tag == "<compat>" and category.startswith("L")


def _fusing_fold_code_points() -> list[tuple[int, str, str]]:
    return [
        (cp, char, mapped)
        for cp, char, mapped, tag, category in _compat_fold_code_points()
        if not _is_glyph_variant(tag, category)
    ]


def _glyph_variant_code_points() -> list[tuple[int, str, str]]:
    return [
        (cp, char, mapped)
        for cp, char, mapped, tag, category in _compat_fold_code_points()
        if _is_glyph_variant(tag, category)
    ]


# --- the checks, parameterised by the module under test -------------------------


# --- FTF-0512-F2: meaning-bearing symbols and name-field pins -----------------


def _symbol_code_points() -> list[tuple[int, str]]:
    """Every Sm/Sc/Sk/So code point, computed from `unicodedata` directly."""
    return [
        (cp, unicodedata.category(chr(cp)))
        for cp in range(0x110000)
        if unicodedata.category(chr(cp)).startswith("S")
    ]


def _meaning_key(char: str) -> str:
    """Independent oracle: what a symbol MEANS once only case, whitespace and
    Unicode P* are ignored (the only differences the detector may erase)."""
    image = unicodedata.normalize("NFKC", char).lower()
    return "".join(
        c for c in image if not unicodedata.category(c).startswith("P") and not c.isspace()
    )


F2_SENTENCE = "pay 5{} now"
F2_SYMBOL_PAIRS: list[tuple[str, str, str]] = [
    ("math: less-or-equal vs greater-or-equal", "x \u2264 5", "x \u2265 5"),
    ("currency: dollar vs euro", "$100", "\u20ac100"),
    ("currency: dollar vs pound", "$100", "\u00a3100"),
    ("currency: yen vs yuan sign", "\u00a5100", "\u5713100"),
    ("math: plus vs minus-sign", "a + b", "a \u2212 b"),
    ("math: plus vs nothing", "a+b", "ab"),
    ("math: equals vs not-equals", "a = b", "a \u2260 b"),
    ("math: less vs greater", "a < b", "a > b"),
    ("modifier: caret", "a^b", "ab"),
    ("other: degree sign", "20\u00b0", "20"),
    ("other: copyright", "\u00a9 2020", "2020"),
    ("spacing diacritic (Sk) is not erased", "a\u00a8b", "ab"),
]
F2_STILL_EQUAL: list[tuple[str, str, str]] = [
    ("P*: comma and exclamation are still stripped", "Hello, world!", "Hello world"),
    ("P*: hyphen / quotes / brackets still stripped", '("well-known")', "well known"),
    ("symbol spacing is irrelevant", "x\u2264 5", "x \u2264  5"),
    ("symbol fullwidth fold still applies", "\uff04100", "$100"),
    ("case still folds next to a symbol", "COST: $100", "cost: $100"),
]


def run_f2_checks(mod: types.ModuleType, sink: list[str]) -> None:
    h = mod.compute_normalized_text_hash
    n = mod._normalize_text

    for label, left, right in F2_SYMBOL_PAIRS:
        check(
            "FTF-0512-F2",
            f"F2-SYMBOL-PAIR {label}: {left!r} != {right!r} (text and hash)",
            n(left) != n(right) and h(_conv(left)) != h(_conv(right)),
            sink,
        )
    for label, left, right in F2_STILL_EQUAL:
        check(
            "FTF-0512-F2",
            f"F2-STILL-EQUAL {label}: {left!r} == {right!r}",
            n(left) == n(right) and h(_conv(left)) == h(_conv(right)),
            sink,
        )

    # F2-SYMBOL-TABLE: every Sm/Sc/Sk/So code point in one fixed sentence. A
    # symbol must never normalize like the sentence without it, and two
    # symbols may share a normalized form only when their NFKC meanings differ
    # solely by case / whitespace / P* (checked against an independent oracle).
    # Comparing the normalized forms covers every symbol PAIR at once.
    baseline = n(F2_SENTENCE.format(""))
    by_form: dict[str, set[str]] = {}
    vanished: list[int] = []
    for cp, _category in _symbol_code_points():
        char = chr(cp)
        form = n(F2_SENTENCE.format(char))
        if form == baseline:
            vanished.append(cp)
        by_form.setdefault(form, set()).add(char)
    check(
        "FTF-0512-F2",
        f"F2-SYMBOL-TABLE: no symbol is erased (first: {[hex(c) for c in vanished[:5]]}, {len(vanished)} total)",
        not vanished,
        sink,
    )
    merged = [
        sorted(hex(ord(c)) for c in chars)[:4]
        for chars in by_form.values()
        if len({_meaning_key(c) for c in chars}) > 1
    ]
    check(
        "FTF-0512-F2",
        f"F2-SYMBOL-TABLE: distinct symbols never merge (first: {merged[:3]}, {len(merged)} groups)",
        not merged,
        sink,
    )
    # the Sm x Sc cross product in the fixed sentence, pair by pair
    sm = [chr(cp) for cp, cat in _symbol_code_points() if cat == "Sm"][:60]
    sc = [chr(cp) for cp, cat in _symbol_code_points() if cat == "Sc"]
    forms = {(a, b): n(f"{a}5{b}") for a in sm for b in sc}
    check(
        "FTF-0512-F2",
        "F2-SYMBOL-TABLE: Sm x Sc pairs in one sentence are all distinct from their single-symbol forms",
        all(
            form != n(f"{a}5") and form != n(f"5{b}") and form != n("5")
            for (a, b), form in forms.items()
        ),
        sink,
    )

    # F2-NAME-FIELDS: tool_calls[].name and a message `name` are byte-for-byte
    # identifiers; no case / width / Cf / whitespace variant may merge.
    def tool_call_name(name: str) -> dict[str, object]:
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": [{"id": "call_1", "name": name, "arguments": {"k": "v"}}],
                },
            ],
            "metadata": {},
        }

    def message_name(name: str) -> dict[str, object]:
        return {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hi"}], "name": name},
            ],
            "metadata": {},
        }

    for label, builder in [("tool_calls[].name", tool_call_name), ("message name", message_name)]:
        check(
            "FTF-0512-F2",
            f"F2-NAME {label}: identical names hash identically",
            h(builder("lookup_order")) == h(builder("lookup_order")),
            sink,
        )
        for variant_label, base, variant in [
            ("case", "lookup_order", "Lookup_Order"),
            ("fullwidth letters", "lookup_order", "\uff4cookup_order"),
            ("fullwidth underscore", "lookup_order", "lookup\uff3forder"),
            ("zero-width space", "lookup_order", "lookup\u200b_order"),
            ("soft hyphen", "lookup_order", "look\u00adup_order"),
            ("BOM", "lookup_order", "\ufefflookup_order"),
            ("trailing space", "lookup_order", "lookup_order "),
            ("leading space", "lookup_order", " lookup_order"),
            ("inner space", "lookup_order", "lookup _order"),
            ("compat ligature", "file_order", "\ufb01le_order"),
            ("superscript digit", "step2", "step\u00b2"),
        ]:
            check(
                "FTF-0512-F2",
                f"F2-NAME {label} {variant_label}: {variant!r} stays distinct from {base!r}",
                h(builder(base)) != h(builder(variant)),
                sink,
            )

    # F2-DIFFERENTIAL: against a frozen copy of the pre-F2 punctuation rule,
    # over the repo corpus. New equality must imply old equality (F2 only
    # un-merges), and every un-merged pair must differ in nothing but S*
    # characters (the intended class).
    differential_violations = _f2_differential(mod)
    check(
        "FTF-0512-F2",
        f"F2-DIFFERENTIAL: only symbol-bearing pairs un-merge (first: {differential_violations[:2]})",
        not differential_violations,
        sink,
    )
    check(
        "FTF-0512-F2",
        "F2-DIFFERENTIAL: the corpus really contains symbol-only un-merges (the gap was real)",
        _f2_unmerged_symbol_pairs(mod) > 0,
        sink,
    )


def _pre_f2_normalize(mod: types.ModuleType, text: str) -> str:
    """The pre-F2 pipeline: everything the current module does, except that
    every non-word, non-space character (symbols included) is stripped."""
    stripped = mod._strip_format_characters(text)
    mod._assert_safe_combining_run(stripped)
    lowered = mod._lower_case(mod._fold_look_alikes(stripped))
    redacted = mod._UUID_PATTERN.sub(" ", mod._ISO_TIMESTAMP_PATTERN.sub(" ", lowered))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", redacted)).strip()


_F2_CORPUS_CACHE: list[str] = []


def _f2_corpus() -> list[str]:
    """Deterministic sample of the repo's own prose (docs/, spec/) -- every
    line that carries a symbol (capped), a stride sample of the rest, and for
    each symbol-bearing line its symbol-stripped twin (an old-equal pair by
    construction) -- plus DISTINCT_CORPUS and the hand-written symbol pairs."""
    if _F2_CORPUS_CACHE:
        return _F2_CORPUS_CACHE
    prose: set[str] = set()
    for folder, patterns in (("docs", ("*.md",)), ("spec", ("*.json", "*.yaml"))):
        for pattern in patterns:
            for path in sorted((ROOT / folder).rglob(pattern)):
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for line in text.splitlines():
                    line = line.strip()
                    if 4 <= len(line) <= 300:
                        prose.add(line)
    ordered = sorted(prose)
    with_symbols = [t for t in ordered if _strip_symbols(t) != t][:4000]
    without = [t for t in ordered if _strip_symbols(t) == t]
    lines: set[str] = (
        set(DISTINCT_CORPUS) | set(with_symbols) | set(without[:: max(1, len(without) // 3000)])
    )
    lines.update(_strip_symbols(t) for t in with_symbols)
    for _label, left, right in F2_SYMBOL_PAIRS + F2_STILL_EQUAL:
        lines.update((left, right))
    _F2_CORPUS_CACHE.extend(sorted(lines))
    return _F2_CORPUS_CACHE


def _f2_pairs(mod: types.ModuleType) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    old_to_texts: dict[str, set[str]] = {}
    new_to_old: dict[str, set[str]] = {}
    for text in _f2_corpus():
        try:
            new = mod._normalize_text(text)
        except mod.NormalizedDuplicateDetectionError:
            continue
        old = _pre_f2_normalize(mod, text)
        old_to_texts.setdefault(old, set()).add(text)
        new_to_old.setdefault(new, set()).add(old)
    return old_to_texts, new_to_old


def _strip_symbols(text: str) -> str:
    """Drop the characters F2/F3 deliberately keep distinct: every S* symbol and
    every non-word P* character directly adjacent to a decimal digit."""
    return "".join(
        c
        for i, c in enumerate(text)
        if not unicodedata.category(c).startswith("S") and not _digit_adjacent_punct(text, i)
    )


def _is_plain(text: str) -> bool:
    """No S* symbol and no digit-adjacent P* once Cf is stripped and NFKC folded."""
    prepared = unicodedata.normalize(
        "NFKC", "".join(c for c in text if unicodedata.category(c) != "Cf")
    )
    return _strip_symbols(prepared) == prepared


def _digit_adjacent_punct(text: str, index: int) -> bool:
    char = text[index]
    if not unicodedata.category(char).startswith("P") or re.match(r"\w", char):
        return False
    return (index > 0 and unicodedata.category(text[index - 1]) == "Nd") or (
        index + 1 < len(text) and unicodedata.category(text[index + 1]) == "Nd"
    )


def _f2_differential(mod: types.ModuleType) -> list[str]:
    """Two invariants over the corpus: (1) F2 only un-merges -- new-equal
    implies old-equal; (2) symbol-free texts are untouched -- among them
    new-equal and old-equal agree exactly, so every verdict change involves a
    symbol."""
    old_to_texts, new_to_old = _f2_pairs(mod)
    violations = [
        f"new-equal pair is old-distinct: {sorted(olds)[:2]}"
        for olds in new_to_old.values()
        if len(olds) > 1
    ]
    for texts in old_to_texts.values():
        symbol_free = [t for t in texts if _is_plain(t)]
        if len({mod._normalize_text(t) for t in symbol_free}) > 1:
            violations.append(f"symbol-free texts un-merged: {sorted(symbol_free)[:2]}")
    return violations


def _f2_unmerged_symbol_pairs(mod: types.ModuleType) -> int:
    old_to_texts, _ = _f2_pairs(mod)
    return sum(
        1 for texts in old_to_texts.values() if len({mod._normalize_text(t) for t in texts}) > 1
    )


F3_PAIRS: list[tuple[str, str, str]] = [
    ("hyphen-minus: negative five", "-5", "5"),
    ("percent", "5%", "5"),
    ("per mille", "5\u2030", "5"),
    ("number sign", "#1", "1"),
    ("prime", "5\u2032", "5"),
    ("decimal point vs none", "5.0", "50"),
    ("thousands comma vs none", "1,000", "1000"),
    ("minus between digits", "5-3", "53"),
    ("colon between digits", "10:30", "1030"),
    ("slash between digits", "1/2", "12"),
    ("fullwidth digit keeps its percent", "\uff15%", "5"),
    ("fullwidth percent next to a digit", "5\uff05", "5"),
    ("Cf between digit and punctuation is inert", "5\u200b%", "5"),
    ("sentence-final period after a number", "total 5.", "total 5"),
]
F3_STILL_EQUAL: list[tuple[str, str, str]] = [
    ("comma between words", "hello, world", "hello world"),
    ("period and quotes between words", 'say "hi." now', "say hi now"),
    ("punctuation separated from a digit by a space", "5 , 6", "5 6"),
    ("punctuation after a word that ends in a letter", "item5 (a)", "item5 a"),
    ("fullwidth punctuation away from digits", "hello\uff0c world", "hello world"),
    ("same number, same punctuation", "-5", "\uff0d5"),
    ("a removed timestamp leaves no digit neighbour", "at 2026-10-20T10:00:00Z, ok", "at , ok"),
]
F3_SENTENCE_AFTER = "pay 5{} now"
F3_SENTENCE_BEFORE = "pay {}5 now"
F3_SENTENCE_FAR = "pay a{}b now"


def _punctuation_code_points() -> list[str]:
    """Every non-word P* code point, computed from `unicodedata` directly."""
    return [
        chr(cp)
        for cp in range(0x110000)
        if unicodedata.category(chr(cp)).startswith("P")
        and not re.search(r"\w", unicodedata.normalize("NFKC", chr(cp)))
    ]


def run_f3_checks(mod: types.ModuleType, sink: list[str]) -> None:
    h = mod.compute_normalized_text_hash
    n = mod._normalize_text

    for label, left, right in F3_PAIRS:
        check(
            "FTF-0512-F3",
            f"F3-PAIR {label}: {left!r} != {right!r} (text and hash)",
            n(left) != n(right) and h(_conv(left)) != h(_conv(right)),
            sink,
        )
    for label, left, right in F3_STILL_EQUAL:
        check(
            "FTF-0512-F3",
            f"F3-STILL-EQUAL {label}: {left!r} == {right!r}",
            n(left) == n(right) and h(_conv(left)) == h(_conv(right)),
            sink,
        )

    # F3-PUNCT-TABLE: every P* code point, on each side of a digit, in a fixed
    # sentence. Next to a digit it must survive (never normalize like the
    # sentence without it); away from a digit it must still be stripped; two
    # different marks may share a form only when their NFKC images are equal.
    points = _punctuation_code_points()
    baseline = {
        "after": n(F3_SENTENCE_AFTER.format("")),
        "before": n(F3_SENTENCE_BEFORE.format("")),
        "far": n(F3_SENTENCE_FAR.format(" ")),
    }
    erased: list[str] = []
    leaked: list[str] = []
    by_form: dict[tuple[str, str], set[str]] = {}
    for char in points:
        image = unicodedata.normalize("NFKC", char)
        for side, template, edge in (
            ("after", F3_SENTENCE_AFTER, image[0]),
            ("before", F3_SENTENCE_BEFORE, image[-1]),
        ):
            form = n(template.format(char))
            if form == baseline[side]:
                erased.append(f"{side}:{ord(char):#x}")
            by_form.setdefault((form, edge), set()).add(char)
        if n(F3_SENTENCE_FAR.format(char)) != baseline["far"]:
            leaked.append(f"far:{ord(char):#x}")
    check(
        "FTF-0512-F3",
        f"F3-PUNCT-TABLE: no digit-adjacent mark is erased (first: {erased[:5]}, {len(erased)} total of {len(points)})",
        not erased and len(points) > 100,
        sink,
    )
    check(
        "FTF-0512-F3",
        f"F3-PUNCT-TABLE: a mark away from a digit is still stripped (first: {leaked[:5]}, {len(leaked)} total)",
        not leaked,
        sink,
    )
    # marks that fold to the same character next to the digit may share a form;
    # two marks whose digit-side characters differ must not (an oracle that is
    # independent of the detector: the NFKC image's edge character)
    forms_by_edge: dict[str, set[str]] = {}
    for (form, edge), _chars in by_form.items():
        forms_by_edge.setdefault(form, set()).add(edge)
    merged = [form for form, edges in forms_by_edge.items() if len(edges) > 1]
    check(
        "FTF-0512-F3",
        f"F3-PUNCT-TABLE: distinct marks next to a digit never merge (first: {merged[:3]}, {len(merged)} groups)",
        not merged,
        sink,
    )
    # a mark between two digits and a mark on both sides: still kept
    between = [c for c in points if n(f"5{c}5") == n("5 5")]
    check(
        "FTF-0512-F3",
        f"F3-PUNCT-TABLE: a mark between two digits is kept (first: {[hex(ord(c)) for c in between[:5]]})",
        not between,
        sink,
    )

    # F3-NAME-NFC: tool_calls[].name and a message `name` are byte-for-byte; an
    # NFC / NFD pair of the same name must stay distinct.
    nfc, nfd = "caf\u00e9_lookup", "cafe\u0301_lookup"
    assert unicodedata.normalize("NFC", nfd) == nfc and nfc != nfd
    for label, build in (
        (
            "tool_calls[].name",
            lambda name: {
                "messages": [
                    {
                        "role": "assistant",
                        "content": [],
                        "tool_calls": [{"id": "call_1", "name": name, "arguments": {}}],
                    }
                ],
                "metadata": {},
            },
        ),
        (
            "message name",
            lambda name: {
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "hi"}], "name": name}
                ],
                "metadata": {},
            },
        ),
    ):
        check(
            "FTF-0512-F3",
            f"F3-NAME-NFC {label}: the NFC and NFD spellings stay distinct",
            h(build(nfc)) != h(build(nfd)) and h(build(nfc)) == h(build(nfc)),
            sink,
        )

    differential_violations = _f3_differential(mod)
    check(
        "FTF-0512-F3",
        f"F3-DIFFERENTIAL: only digit-adjacent-punctuation pairs un-merge (first: {differential_violations[:2]})",
        not differential_violations,
        sink,
    )
    check(
        "FTF-0512-F3",
        "F3-DIFFERENTIAL: the corpus really contains digit-adjacent un-merges (the gap was real)",
        _f3_unmerged_pairs(mod) > 0,
        sink,
    )


def _pre_f3_normalize(mod: types.ModuleType, text: str) -> str:
    """The pre-F3 pipeline: everything the current module does, except that
    every P* character is stripped wherever it sits (S* kept as a token)."""
    stripped = mod._strip_format_characters(text)
    mod._assert_safe_combining_run(stripped)
    lowered = mod._lower_case(mod._fold_look_alikes(stripped))
    redacted = mod._UUID_PATTERN.sub(" ", mod._ISO_TIMESTAMP_PATTERN.sub(" ", lowered))
    spaced = re.sub(
        r"[^\w\s]",
        lambda m: f" {m.group()} " if unicodedata.category(m.group()).startswith("S") else " ",
        redacted,
    )
    return re.sub(r"\s+", " ", spaced).strip()


def _f3_pairs(mod: types.ModuleType) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    corpus = set(_f2_corpus())
    for _label, left, right in F3_PAIRS + F3_STILL_EQUAL:
        corpus.update((left, right))
    old_to_texts: dict[str, set[str]] = {}
    new_to_old: dict[str, set[str]] = {}
    for text in sorted(corpus):
        try:
            new = mod._normalize_text(text)
        except mod.NormalizedDuplicateDetectionError:
            continue
        old = _pre_f3_normalize(mod, text)
        old_to_texts.setdefault(old, set()).add(text)
        new_to_old.setdefault(new, set()).add(old)
    return old_to_texts, new_to_old


def _f3_differential(mod: types.ModuleType) -> list[str]:
    """(1) F3 only un-merges -- new-equal implies old-equal; (2) texts with no
    digit-adjacent P* are untouched -- among them new-equal and old-equal agree."""
    old_to_texts, new_to_old = _f3_pairs(mod)
    violations = [
        f"new-equal pair is old-distinct: {sorted(olds)[:2]}"
        for olds in new_to_old.values()
        if len(olds) > 1
    ]
    for texts in old_to_texts.values():
        plain = [t for t in texts if _is_plain(t)]
        if len({mod._normalize_text(t) for t in plain}) > 1:
            violations.append(
                f"texts without digit-adjacent punctuation un-merged: {sorted(plain)[:2]}"
            )
    return violations


def _f3_unmerged_pairs(mod: types.ModuleType) -> int:
    old_to_texts, _ = _f3_pairs(mod)
    return sum(
        1 for texts in old_to_texts.values() if len({mod._normalize_text(t) for t in texts}) > 1
    )


def run_checks(mod: types.ModuleType, sink: list[str]) -> None:
    h = mod.compute_normalized_text_hash

    for label, plain, variant in look_alike_cases():
        check(
            "FTF-0512-F1",
            f"F1-LETTERS {label}: look-alike hashes like the plain text",
            h(_conv(plain)) == h(_conv(variant)),
            sink,
        )
    # same look-alike in a LATER message of a longer conversation
    for label, plain, variant in look_alike_cases()[:12]:
        check(
            "FTF-0512-F1",
            f"F1-LETTERS second-message {label}",
            h(_conv("hi", plain)) == h(_conv("hi", variant)),
            sink,
        )

    for label, with_gen, without in generated_cases():
        check(
            "FTF-0512-F1",
            f"F1-ONE-SIDED {label}: generated value on one side only",
            h(_conv(with_gen)) == h(_conv(without)),
            sink,
        )
        check(
            "FTF-0512-F1",
            f"F1-ONE-SIDED {label}: symmetric (other direction)",
            h(_conv(without)) == h(_conv(with_gen)),
            sink,
        )

    for label, with_gen, without in format_char_inside_generated_cases():
        check(
            "FTF-0512-F1", f"F1-FORMAT-CHARS {label}", h(_conv(with_gen)) == h(_conv(without)), sink
        )

    # NEGATIVE, real default code: legitimately different texts stay distinct.
    digests = {text: h(_conv(text)) for text in DISTINCT_CORPUS}
    check(
        "FTF-0512-F1",
        "F1-NO-COLLAPSE: corpus of different texts has pairwise distinct hashes",
        len(set(digests.values())) == len(DISTINCT_CORPUS),
        sink,
    )
    check(
        "FTF-0512-F1",
        "F1-NO-COLLAPSE: literal 'generated timestamp' text != a text with a timestamp",
        h(_conv("generated timestamp")) != h(_conv("2026-01-01T12:00:00Z")),
        sink,
    )
    check(
        "FTF-0512-F1",
        "F1-NO-COLLAPSE: attack input present in a different-outcome pair still differs",
        h(_conv("refunds\u200b are forbidden")) != h(_conv("ｒｅｆｕｎｄｓ are allowed")),
        sink,
    )

    # tool semantics stay byte-for-byte
    def with_call(
        call_id: str, arg: str, value: object = "x", *, name: str = "lookup"
    ) -> dict[str, object]:
        return {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "go"}]},
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": [{"id": call_id, "name": name, "arguments": {"k": arg}}],
                },
                {
                    "role": "tool",
                    "content": [{"type": "json", "value": value}],
                    "tool_call_id": call_id,
                },
            ],
            "metadata": {},
        }

    def with_tool_message_name(name: str) -> dict[str, object]:
        return {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "go"}]},
                {
                    "role": "tool",
                    "content": [{"type": "json", "value": "ok"}],
                    "tool_call_id": "call_1",
                    "name": name,
                },
            ],
            "metadata": {},
        }

    def with_tool_call_entry_id(call_id: str) -> dict[str, object]:
        # isolates tool_calls[].id alone -- with_call's own tool-role message
        # also carries a `tool_call_id` field copied from the same call_id,
        # which would mask a mutation limited to just the tool_calls entry.
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": [{"id": call_id, "name": "lookup", "arguments": {"k": "v"}}],
                },
            ],
            "metadata": {},
        }

    def with_tool_call_id_field(tool_call_id: str) -> dict[str, object]:
        return {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "go"}]},
                {
                    "role": "tool",
                    "content": [{"type": "json", "value": "ok"}],
                    "tool_call_id": tool_call_id,
                    "name": "lookup_order",
                },
            ],
            "metadata": {},
        }

    check(
        "FTF-0512-F1",
        "F1-NO-COLLAPSE: fullwidth vs ASCII tool argument stays distinct",
        h(with_call("c1", "abc")) != h(with_call("c1", "ａｂｃ")),
        sink,
    )
    check(
        "FTF-0512-F1",
        "F1-NO-COLLAPSE: zero-width in a tool argument stays distinct",
        h(with_call("c1", "abc")) != h(with_call("c1", "a\u200bbc")),
        sink,
    )
    check(
        "FTF-0512-F1",
        "F1-NO-COLLAPSE: tool call id with zero-width stays distinct",
        h(with_call("c1", "abc")) != h(with_call("c\u200b1", "abc")),
        sink,
    )
    check(
        "FTF-0512-F1",
        "F1-NO-COLLAPSE: json tool result with fullwidth stays distinct",
        h(with_call("c1", "abc", "ok")) != h(with_call("c1", "abc", "ｏｋ")),
        sink,
    )
    check(
        "FTF-0512-F1",
        "F1-NO-COLLAPSE: tool argument case difference stays distinct",
        h(with_call("c1", "ABC")) != h(with_call("c1", "abc")),
        sink,
    )
    check(
        "FTF-0512-F1",
        "F1-NO-COLLAPSE: identical tool conversations still match",
        h(with_call("c1", "abc")) == h(with_call("c1", "abc")),
        sink,
    )

    # FTF-0512-TOOL-ID (Law 15 requalification gap): the tool call id, the tool
    # call name, the message-level `name` and the message-level `tool_call_id`
    # are "meaningful tool/ID semantics" the acceptance clause requires
    # preserved -- a case-only difference in any of them is a different real
    # identifier, never a formatting variant, and must never collapse.
    check(
        "FTF-0512-F1",
        "FG2 tool-call id case difference stays distinct",
        h(with_tool_call_entry_id("call_1")) != h(with_tool_call_entry_id("CALL_1")),
        sink,
    )
    check(
        "FTF-0512-F1",
        "FI tool-call name case difference stays distinct",
        h(with_call("c1", "abc", name="lookup_order"))
        != h(with_call("c1", "abc", name="LOOKUP_ORDER")),
        sink,
    )
    check(
        "FTF-0512-F1",
        "FJ message name field case difference stays distinct",
        h(with_tool_message_name("lookup_order")) != h(with_tool_message_name("LOOKUP_ORDER")),
        sink,
    )
    check(
        "FTF-0512-F1",
        "FK tool_call_id field case difference stays distinct",
        h(with_tool_call_id_field("call_1")) != h(with_tool_call_id_field("CALL_1")),
        sink,
    )

    # F1-NFKC-DIGIT-MERGE: a superscript/subscript digit, a vulgar fraction or
    # a circled/Roman numeral (category No/Nl) must never be rewritten into a
    # plain ASCII digit that can then fuse with a neighbouring real digit run.
    # Named regressions from the round-2 verifier finding, reproduced first
    # (each pair must NOT collapse):
    for label, with_symbol, plain in [
        ("superscript-power", "10⁵ requests", "105 requests"),
        ("vulgar-fraction", "1¼", "11/4"),
        ("superscript-cubed", "2³", "23"),
        ("superscript-squared-suffix", "x²", "x2"),
        ("superscript-negative-exponent", "10⁻³", "10-3"),
    ]:
        check(
            "FTF-0512-F1",
            f"F1-NFKC-DIGIT-MERGE {label}: {with_symbol!r} does not collapse onto {plain!r}",
            h(_conv(with_symbol)) != h(_conv(plain)),
            sink,
        )
    # generated table: every No/Nl code point in Unicode, adjacent to a digit
    # run on both sides, must survive `_normalize_text` byte-for-byte -- the
    # only way it can be guaranteed to never fuse with any neighbour's digits
    # regardless of that neighbour's own shape.
    for cp in _no_nl_code_points():
        char = chr(cp)
        for text in (f"7{char}", f"{char}7", f"a{char}9b"):
            # Roman numerals (Nl) are case-paired (U+2160 Ⅰ <-> U+2170 ⅰ) and
            # the existing `.lower()` step (unrelated to F1, applied to every
            # character uniformly) legitimately recases them -- so the
            # byte-for-byte invariant is "still its own distinct No/Nl symbol,
            # never folded away into a plain ASCII digit that could fuse with
            # its neighbours".
            normalized = mod._normalize_text(text)
            check(
                "FTF-0512-F1",
                f"F1-NFKC-DIGIT-MERGE U+{cp:04X} stays a No/Nl symbol, never an ASCII digit",
                any(unicodedata.category(c) in ("No", "Nl") for c in normalized),
                sink,
            )

    # F1-NFKC-QUADRATIC: a maximal run of combining (non-starter) code points
    # longer than the UAX #15 Stream-Safe bound is refused, not normalized at
    # unbounded cost.
    arabic_base = "ر"
    safe_run = arabic_base + ("ِّ" * 15)  # 30 combining marks
    unsafe_run = arabic_base + ("ِّ" * 16)  # 32 combining marks
    try:
        mod._normalize_text(safe_run)
        safe_ok = True
    except mod.NormalizedDuplicateDetectionError:
        safe_ok = False
    check("FTF-0512-F1", "F1-NFKC-QUADRATIC: a 30-long combining run is accepted", safe_ok, sink)
    try:
        mod._normalize_text(unsafe_run)
        unsafe_refused = False
    except mod.NormalizedDuplicateDetectionError:
        unsafe_refused = True
    check(
        "FTF-0512-F1",
        "F1-NFKC-QUADRATIC: a 32-long combining run is refused, not processed",
        unsafe_refused,
        sink,
    )
    # the refusal surfaces as a per-row issue, never an uncaught crash or a
    # silently dropped record.
    report = mod.detect_normalized_duplicates(
        [{"record_index": 0, "purpose": "training", "content": _conv(unsafe_run)}]
    )
    check(
        "FTF-0512-F1",
        "F1-NFKC-QUADRATIC: an over-long combining run is a reported row issue",
        any(issue.code == "unknown_content" for issue in report.issues),
        sink,
    )

    # F1-CF-HANDPICKED (FB2): every Cf code point in Unicode (not only the 8 in
    # INSERTED_FORMAT_CHARS) is removed EXACTLY. `char not in once` is vacuous
    # because the punctuation step turns a surviving Cf into a space, so the
    # check is equality with the plain text.
    for cp in _cf_code_points():
        check(
            "FTF-0512-F1",
            f"F1-CF-HANDPICKED U+{cp:04X} is stripped exactly",
            mod._normalize_text(f"a{chr(cp)}b") == "ab",
            sink,
        )

    # F1-NFKD-STREAM-SAFE (round 3): the bound counts NFKD non-starters, driven
    # by every code point whose NFKD contains one. "a" + c + enough U+0301 marks
    # to make the run exactly 30 is accepted; exactly 31 is refused.
    for cp in _nfkd_non_starter_code_points():
        char = chr(cp)
        trailing = _trailing_non_starters(char)
        for run, expect_refused in ((30, False), (31, True)):
            text = "a" + char + "\u0301" * (run - trailing)
            try:
                mod._normalize_text(text)
                refused = False
            except mod.NormalizedDuplicateDetectionError:
                refused = True
            check(
                "FTF-0512-F1",
                f"F1-NFKD-STREAM-SAFE U+{cp:04X}: NFKD run of {run} is "
                f"{'refused' if expect_refused else 'accepted'}",
                refused == expect_refused,
                sink,
            )
    # timing-independent: a U+0F73 run (ccc 0, NFKD = two non-starters) and a
    # Cf-interleaved run are refused by behaviour, not by elapsed time.
    for label, text, expect_refused in [
        ("U+0F73 x15 (30 NFKD non-starters)", "a" + "\u0f73" * 15, False),
        ("U+0F73 x16 (32 NFKD non-starters)", "a" + "\u0f73" * 16, True),
        ("U+0F73 x20000 (long run)", "a" + "\u0f73" * 20_000, True),
        ("U+0F75 / U+0F81 / U+FF9E mix", "a" + ("\u0f75\u0f81\uff9e" * 11), True),
        ("ZWSP every 29 marks", "a" + ("\u0301" * 29 + "\u200b") * 5, True),
        ("ZWSP between every mark", "a" + "\u0301\u200b" * 40, True),
        ("ZWSP every 15 marks, total 30", "a" + ("\u0301" * 15 + "\u200b") * 2, False),
    ]:
        try:
            mod._normalize_text(text)
            refused = False
        except mod.NormalizedDuplicateDetectionError:
            refused = True
        check(
            "FTF-0512-F1",
            f"F1-NFKD-STREAM-SAFE {label}: {'refused' if expect_refused else 'accepted'}",
            refused == expect_refused,
            sink,
        )
    # a starter between marks resets the run (real text is not over-refused)
    check(
        "FTF-0512-F1",
        "F1-NFKD-STREAM-SAFE: runs of 30 separated by starters are accepted",
        _accepts(mod, ("a" + "\u0301" * 30) * 5),
        sink,
    )

    # F1-DETECTOR-VERSION (round 3): the version is mixed into the digest and
    # pinned. Rebuilding the canonical document by hand with the literal
    # version fails if the field is dropped or the number is reverted.
    sample = _conv("Hello")
    expected_document = (
        '{"detector_version":5,"messages":[{"content":[{"text":"hello","type":"text"}],'
        '"role":"user"}]}'
    )
    check(
        "FTF-0512-F1",
        "F1-DETECTOR-VERSION: digest == sha256 of the canonical document with detector_version 5",
        h(sample) == hashlib.sha256(expected_document.encode("utf-8")).hexdigest(),
        sink,
    )
    check(
        "FTF-0512-F1",
        "F1-DETECTOR-VERSION: frozen digest for the sample conversation",
        h(sample) == FROZEN_SAMPLE_DIGEST,
        sink,
    )

    # Requalification gaps (round 3 item 4): role, JSON value and tool ids are
    # meaningful and survive byte-for-byte.
    def two_messages(first_role: str, second_role: str) -> dict[str, object]:
        return {
            "messages": [
                {"role": first_role, "content": [{"type": "text", "text": "hi"}]},
                {"role": second_role, "content": [{"type": "text", "text": "ok"}]},
            ],
            "metadata": {},
        }

    for first, second in [
        ("user", "assistant"),
        ("assistant", "user"),
        ("system", "user"),
        ("user", "tool"),
        ("assistant", "assistant"),
    ]:
        for other_first, other_second in [("user", "user"), ("user", "assistant")]:
            if (first, second) == (other_first, other_second):
                continue
            check(
                "FTF-0512-F1",
                f"REQUAL-ROLE {first}/{second} vs {other_first}/{other_second} stay distinct",
                h(two_messages(first, second)) != h(two_messages(other_first, other_second)),
                sink,
            )

    def json_result(value: object) -> dict[str, object]:
        return {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "go"}]},
                {"role": "tool", "content": [{"type": "json", "value": value}]},
            ],
            "metadata": {},
        }

    for left, right in [
        ("Approved", "approved"),
        ("APPROVED", "approved"),
        ("approved", "ａpproved"),
        ("approved", "approved\u200b"),
        ({"Status": "OK"}, {"status": "ok"}),
        ({"status": "Approved"}, {"status": "approved"}),
        ({"status": "ok"}, {"status": "ｏｋ"}),
        (["A", "b"], ["a", "b"]),
    ]:
        check(
            "FTF-0512-F1",
            f"REQUAL-JSON-VALUE {left!r} vs {right!r} stay distinct",
            h(json_result(left)) != h(json_result(right)),
            sink,
        )

    def entry_id(call_id: str) -> dict[str, object]:
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": [{"id": call_id, "name": "lookup", "arguments": {"k": "v"}}],
                },
            ],
            "metadata": {},
        }

    def result_call_id(call_id: str) -> dict[str, object]:
        return {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "go"}]},
                {
                    "role": "tool",
                    "content": [{"type": "json", "value": "ok"}],
                    "tool_call_id": call_id,
                },
            ],
            "metadata": {},
        }

    for label, builder in [("tool_calls[].id", entry_id), ("tool_call_id", result_call_id)]:
        for variant_label, variant in [
            ("fullwidth", "ｃａｌｌ_1"),
            ("fullwidth digit", "call_１"),
            ("Cf-only", "call\u200b_1"),
            ("soft hyphen", "cal\u00adl_1"),
            ("compat ligature", "ﬁle_1"),
        ]:
            base = "file_1" if variant_label == "compat ligature" else "call_1"
            check(
                "FTF-0512-F1",
                f"REQUAL-ID {label} {variant_label} stays distinct from {base!r}",
                h(builder(base)) != h(builder(variant)),
                sink,
            )

    # F1-FUSING-FOLD (round 3 item 5): every NFKC compatibility mapping that
    # yields alphanumerics and is NOT a same-character glyph variant must never
    # fuse into its neighbours. Driven by every code point.
    for label, fused_text, plain_text in [
        ("telegraph day", "2㏠", "21日"),
        ("superscript n", "O(2ⁿ)", "O(2n)"),
        ("squared unit", "5㎏", "5kg"),
        ("trade mark", "Foo℠", "FooSM"),
    ]:
        check(
            "FTF-0512-F1",
            f"F1-FUSING-FOLD {label}: {fused_text!r} != {plain_text!r}",
            mod._normalize_text(fused_text) != mod._normalize_text(plain_text),
            sink,
        )
    for cp, char, mapped in _fusing_fold_code_points():
        for left, right in (("7", "7"), ("a", "b"), ("7", "b")):
            tokens = mod._normalize_text(f"{left}{char}{right}").split()
            check(
                "FTF-0512-F1",
                f"F1-FUSING-FOLD U+{cp:04X}: neighbours {left!r}/{right!r} stay separate tokens",
                bool(tokens) and tokens[0] == left and tokens[-1] == right,
                sink,
            )
            if mapped[0].isalnum() and mapped[-1].isalnum():
                check(
                    "FTF-0512-F1",
                    f"F1-FUSING-FOLD U+{cp:04X}: not equal to its fused spelling",
                    mod._normalize_text(f"{left}{char}{right}")
                    != mod._normalize_text(f"{left}{mapped}{right}"),
                    sink,
                )
    # ...while same-character glyph variants (fullwidth, math styles, circled
    # letters, positional forms, ligatures) still fold in place.
    for cp, char, mapped in _glyph_variant_code_points():
        check(
            "FTF-0512-F1",
            f"F1-FUSING-FOLD U+{cp:04X}: glyph variant still folds to its plain spelling",
            mod._normalize_text(f"a{char}b") == mod._normalize_text(f"a{mapped}b"),
            sink,
        )

    # full detector path: look-alike pair is reported as a blocking group
    report = mod.detect_normalized_duplicates(
        [
            {"record_index": 0, "purpose": "training", "content": _conv("Close the account")},
            {
                "record_index": 1,
                "purpose": "held_out_evaluation",
                "content": _conv("Ｃｌｏｓｅ the acc\u200bount"),
            },
        ]
    )
    check(
        "FTF-0512-F1",
        "F1-LETTERS: look-alike cross-purpose pair is one blocking group",
        len(report.blocking_groups) == 1,
        sink,
    )

    run_f2_checks(mod, sink)
    run_f3_checks(mod, sink)


def test_look_alike_and_generated_tables_pass_on_production() -> None:
    run_checks(production, FAILURES)


def test_table_sizes_are_real() -> None:
    check(
        "FTF-0512-F1",
        "generated look-alike table is non-trivial (>=150 cases)",
        len(look_alike_cases()) >= 150,
    )
    check(
        "FTF-0512-F1",
        "generated one-sided table covers 5 values x 4 positions",
        len(generated_cases()) == 20,
    )


def test_differential_against_frozen_pre_f1_normalization() -> None:
    """Over the corpus plus every intended pair: the verdicts differ from the
    pre-F1 normalization ONLY for the intended collapses."""
    texts = list(DISTINCT_CORPUS)
    for a, b in INTENDED_COLLAPSES:
        texts.extend([a, b])
    unique = sorted(set(texts))
    changed: set[frozenset[str]] = set()
    for i, a in enumerate(unique):
        for b in unique[i + 1 :]:
            old_same = frozen_pre_f1_normalize(a) == frozen_pre_f1_normalize(b)
            new_same = production._normalize_text(a) == production._normalize_text(b)
            if old_same != new_same:
                changed.add(frozenset({a, b}))
    intended = {frozenset(pair) for pair in INTENDED_COLLAPSES}
    check(
        "FTF-0512-F1",
        f"F1-DIFFERENTIAL: only intended pairs changed verdict (extra: {sorted(map(sorted, changed - intended))})",
        changed == intended,
    )
    for a, b in INTENDED_COLLAPSES:
        check(
            "FTF-0512-F1",
            f"F1-DIFFERENTIAL: pre-F1 kept {a!r} and {b!r} distinct (the gap was real)",
            frozen_pre_f1_normalize(a) != frozen_pre_f1_normalize(b),
        )
    # normalization is idempotent and produces no Cf
    for text in unique:
        once = production._normalize_text(text)
        check("FTF-0512-F1", f"idempotent {text!r}", production._normalize_text(once) == once)
        check(
            "FTF-0512-F1",
            f"no Cf survives {text!r}",
            all(unicodedata.category(c) != "Cf" for c in once),
        )


# --- fault injection: behavioural overrides on a fresh copy of the real module ----
#
# Each fault loads a FRESH copy of the real, current production source and then
# overrides one named seam (a helper, a constant or a wrapped function) with the
# faulty behaviour. There is no text anchor into the production source, so a
# production edit can never make a fault "fail to build" and be miscounted as a
# kill: a fault is RED only if a named behavioural check notices the behaviour.


def _load_fresh(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(f"ftf_0512_f1_fault_{name}", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _PatternShim:
    """Stands in for a compiled regex; `sub` is replaced by `fn(real, repl, text)`."""

    def __init__(self, real: re.Pattern[str], fn: Callable[[re.Pattern[str], str, str], str]):
        self._real = real
        self._fn = fn

    def sub(self, repl: str, text: str) -> str:
        return self._fn(self._real, repl, text)


def _wrap(module: types.ModuleType, name: str, post: Callable[[dict[str, object]], None]) -> None:
    original = getattr(module, name)

    def wrapped(*args: object, **kwargs: object) -> dict[str, object]:
        result = original(*args, **kwargs)
        post(result)
        return result

    setattr(module, name, wrapped)


def _nfkc(value: str) -> str:
    return unicodedata.normalize("NFKC", value)


def _strip_cf(value: str) -> str:
    return "".join(c for c in value if unicodedata.category(c) != "Cf")


def _fault_cf_handpicked(v: Any) -> None:
    hand_picked = set(INSERTED_FORMAT_CHARS.values())
    v._strip_format_characters = lambda text: "".join(c for c in text if c not in hand_picked)


def _fault_cf_after_nfkc(v: Any) -> None:
    def normalize(text: str) -> str:
        folded = v._fold_look_alikes(text)
        v._assert_safe_combining_run(folded)
        return v._finish_text(v._strip_format_characters(folded))

    v._normalize_text = normalize


def _fault_guard_before_cf_strip(v: Any) -> None:
    def normalize(text: str) -> str:
        v._assert_safe_combining_run(text)
        return v._finish_text(v._fold_look_alikes(v._strip_format_characters(text)))

    v._normalize_text = normalize


def _fault_guard_on_input_ccc(v: Any) -> None:
    def guard(text: str) -> None:
        run = 0
        for char in text:
            run = run + 1 if unicodedata.combining(char) else 0
            if run > v._MAX_COMBINING_RUN:
                raise v.NormalizedDuplicateDetectionError("run")

    v._assert_safe_combining_run = guard


def _fault_timestamp(replacement: str | None) -> Callable[[Any], None]:
    def fault(v: Any) -> None:
        def sub(real: re.Pattern[str], repl: str, text: str) -> str:
            return text if replacement is None else real.sub(replacement, text)

        v._ISO_TIMESTAMP_PATTERN = _PatternShim(v._ISO_TIMESTAMP_PATTERN, sub)

    return fault


def _fault_uuid(replacement: str | None) -> Callable[[Any], None]:
    def fault(v: Any) -> None:
        def sub(real: re.Pattern[str], repl: str, text: str) -> str:
            return text if replacement is None else real.sub(replacement, text)

        v._UUID_PATTERN = _PatternShim(v._UUID_PATTERN, sub)

    return fault


def _json_lower(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=False).lower())


def _json_nfkc(value: object) -> object:
    return json.loads(_nfkc(json.dumps(value, ensure_ascii=False)))


def _post_content_part(fn: Callable[[object], object]) -> Callable[[Any], None]:
    def fault(v: Any) -> None:
        def post(result: dict[str, object]) -> None:
            if result.get("type") == "json":
                result["value"] = fn(result["value"])

        _wrap(v, "_normalize_content_part", post)

    return fault


def _post_tool_call(key: str, fn: Callable[[object], object]) -> Callable[[Any], None]:
    def fault(v: Any) -> None:
        def post(result: dict[str, object]) -> None:
            result[key] = fn(result[key])

        _wrap(v, "_normalize_tool_call", post)

    return fault


def _post_message(key: str, fn: Callable[[object], object]) -> Callable[[Any], None]:
    def fault(v: Any) -> None:
        def post(result: dict[str, object]) -> None:
            if key in result:
                result[key] = fn(result[key])

        _wrap(v, "_normalize_message", post)

    return fault


def _fault_drop_version(v: Any) -> None:
    v._digest_document = lambda messages: {"messages": messages}


def _fault_bound(value: int) -> Callable[[Any], None]:
    def fault(v: Any) -> None:
        v._MAX_COMBINING_RUN = value

    return fault


def _fault_version(value: int) -> Callable[[Any], None]:
    def fault(v: Any) -> None:
        v._DETECTOR_VERSION = value

    return fault


def _fault_no_isolation(v: Any) -> None:
    v._fold_action = lambda char: v._FOLD_KEEP


def _fault_no_separation(v: Any) -> None:
    original = v._fold_action
    v._fold_action = lambda char: (
        v._FOLD_KEEP if original(char) == v._FOLD_SEPARATE else original(char)
    )


def _fault_separation_hand_picked(v: Any) -> None:
    # only the two classes the round-2 reviewer named are separated
    original = v._fold_action
    hand_picked = {"\u33e0", "\u207f"}
    v._fold_action = lambda char: (
        v._FOLD_KEEP
        if original(char) == v._FOLD_SEPARATE and char not in hand_picked
        else original(char)
    )


def _fault_separate_glyph_variants(v: Any) -> None:
    original = v._fold_action
    v._fold_action = lambda char: (
        v._FOLD_SEPARATE
        if original(char) == v._FOLD_KEEP and unicodedata.normalize("NFKC", char) != char
        else original(char)
    )


def _fault_punctuation(keep: Callable[[str], bool], *, separate: bool) -> Callable[[Any], None]:
    """Replace the punctuation rule: every non-word, non-space character for
    which `keep` is true survives (spaced out when `separate`), the rest is
    stripped to a space."""

    def fault(v: Any) -> None:
        def strip(text: str) -> str:
            return re.sub(
                r"[^\w\s]",
                lambda m: (f" {m.group()} " if separate else m.group()) if keep(m.group()) else " ",
                text,
            )

        v._strip_punctuation = strip

    return fault


def _category(char: str) -> str:
    return unicodedata.category(char)


def _fault_sk_compat_stripped(v: Any) -> None:
    original = v._fold_action
    v._fold_action = lambda char: (
        v._FOLD_SEPARATE
        if _category(char).startswith("S") and original(char) == v._FOLD_ISOLATE_VERBATIM
        else original(char)
    )


def _fault_digit_adjacent(side: str) -> Callable[[Any], None]:
    """Replace the digit-adjacency rule: which side(s) of a P* mark are checked."""

    def fault(v: Any) -> None:
        def strip(text: str) -> str:
            def replace(m: re.Match[str]) -> str:
                char, start, end = m.group(), m.start(), m.end()
                if _category(char).startswith("S"):
                    return f" {char} "
                left = start > 0 and _category(text[start - 1]) == "Nd"
                right = end < len(text) and _category(text[end]) == "Nd"
                keep = {"left": left, "right": right, "none": False, "both": left and right}[side]
                return char if keep else " "

            return re.sub(r"[^\w\s]", replace, text)

        v._strip_punctuation = strip

    return fault


def _fault_digit_adjacent_ascii(v: Any) -> None:
    v._strip_punctuation = _ascii_only_strip


def _ascii_only_strip(text: str) -> str:
    def replace(m: re.Match[str]) -> str:
        char, start, end = m.group(), m.start(), m.end()
        if _category(char).startswith("S"):
            return f" {char} "
        near = (start > 0 and text[start - 1] in "0123456789") or (
            end < len(text) and text[end] in "0123456789"
        )
        return char if near else " "

    return re.sub(r"[^\w\s]", replace, text)


def _fault_every_punctuation_kept(v: Any) -> None:
    v._strip_punctuation = lambda text: re.sub(
        r"[^\w\s]",
        lambda m: f" {m.group()} " if _category(m.group()).startswith("S") else m.group(),
        text,
    )


FAULTS: dict[str, Callable[[Any], None]] = {
    "F3-DETECTOR-VERSION-REVERTED-4": _fault_version(4),
    "F3-DIGIT-ADJACENT-IGNORED": _fault_digit_adjacent("none"),
    "F3-DIGIT-ADJACENT-LEFT-ONLY": _fault_digit_adjacent("left"),
    "F3-DIGIT-ADJACENT-RIGHT-ONLY": _fault_digit_adjacent("right"),
    "F3-DIGIT-ADJACENT-BOTH-SIDES-REQUIRED": _fault_digit_adjacent("both"),
    "F3-DIGIT-ADJACENT-ASCII-DIGITS-ONLY": _fault_digit_adjacent_ascii,
    "F3-EVERY-PUNCTUATION-KEPT": _fault_every_punctuation_kept,
    "F3-TOOL-CALL-NAME-NFC": _post_tool_call(
        "name", lambda value: unicodedata.normalize("NFC", str(value))
    ),
    "F3-MESSAGE-NAME-NFC": _post_message(
        "name", lambda value: unicodedata.normalize("NFC", str(value))
    ),
    "F1-NFKC-REMOVED": lambda v: setattr(v, "_fold_look_alikes", lambda text: text),
    "F1-CF-REMOVED": lambda v: setattr(v, "_strip_format_characters", lambda text: text),
    "FB2-CF-HANDPICKED": _fault_cf_handpicked,
    "F1-CF-STRIPPED-AFTER-NFKC": _fault_cf_after_nfkc,
    "F1-GUARD-BEFORE-CF-STRIP": _fault_guard_before_cf_strip,
    "F1-TS-PLACEHOLDER": _fault_timestamp("<generated_timestamp>"),
    "F1-UUID-PLACEHOLDER": _fault_uuid("<generated_id>"),
    "F1-TS-NOT-REDACTED": _fault_timestamp(None),
    "F1-UUID-NOT-REDACTED": _fault_uuid(None),
    "F1-LOWER-REMOVED": lambda v: setattr(v, "_lower_case", lambda text: text),
    "F1-TOOL-ARGS-NORMALIZED": _post_tool_call("arguments", _json_lower),
    "F1-TOOL-ARGS-NFKC": _post_tool_call("arguments", _json_nfkc),
    "F1-JSON-PART-NFKC": _post_content_part(_json_nfkc),
    "F1-DIGIT-LIKE-UNPROTECTED": _fault_no_isolation,
    "F1-COMBINING-RUN-UNCHECKED": lambda v: setattr(
        v, "_assert_safe_combining_run", lambda text: None
    ),
    "F1-NFKD-GUARD-ON-INPUT-CCC": _fault_guard_on_input_ccc,
    "F1-NFKD-BOUND-31": _fault_bound(31),
    "F1-NFKD-BOUND-29": _fault_bound(29),
    "F1-DETECTOR-VERSION-DROPPED": _fault_drop_version,
    "F1-DETECTOR-VERSION-REVERTED-1": _fault_version(1),
    "F1-DETECTOR-VERSION-REVERTED-2": _fault_version(2),
    "F2-DETECTOR-VERSION-REVERTED-3": _fault_version(3),
    "F2-SYMBOLS-STRIPPED": _fault_punctuation(lambda c: False, separate=True),
    "F2-SYMBOLS-UNSEPARATED": _fault_punctuation(
        lambda c: _category(c).startswith("S"), separate=False
    ),
    "F2-SYMBOLS-SM-ONLY": _fault_punctuation(lambda c: _category(c) == "Sm", separate=True),
    "F2-SYMBOLS-SM-SC-ONLY": _fault_punctuation(
        lambda c: _category(c) in ("Sm", "Sc"), separate=True
    ),
    "F2-SYMBOLS-ASCII-ONLY": _fault_punctuation(
        lambda c: _category(c).startswith("S") and ord(c) < 128, separate=True
    ),
    "F2-PUNCTUATION-NOT-STRIPPED": _fault_punctuation(lambda c: True, separate=True),
    "F2-SK-COMPAT-SYMBOL-ERASED": _fault_sk_compat_stripped,
    "F2-TOOL-CALL-NAME-NFKC": _post_tool_call("name", lambda value: _nfkc(str(value))),
    "F2-TOOL-CALL-NAME-CF-STRIPPED": _post_tool_call("name", lambda value: _strip_cf(str(value))),
    "F2-TOOL-CALL-NAME-WHITESPACE-STRIPPED": _post_tool_call(
        "name", lambda value: str(value).strip()
    ),
    "F2-MESSAGE-NAME-NFKC": _post_message("name", lambda value: _nfkc(str(value))),
    "F2-MESSAGE-NAME-CF-STRIPPED": _post_message("name", lambda value: _strip_cf(str(value))),
    "F2-MESSAGE-NAME-WHITESPACE-STRIPPED": _post_message("name", lambda value: str(value).strip()),
    "F1-FUSING-FOLD-UNSEPARATED": _fault_no_separation,
    "F1-FUSING-FOLD-HAND-PICKED": _fault_separation_hand_picked,
    "F1-FUSING-FOLD-OVER-SEPARATES-GLYPHS": _fault_separate_glyph_variants,
    "FG2-TOOL-CALL-ID-LOWERCASED": _post_tool_call("id", lambda value: str(value).lower()),
    "FG3-TOOL-CALL-ID-NFKC": _post_tool_call("id", lambda value: _nfkc(str(value))),
    "FG4-TOOL-CALL-ID-CF-STRIPPED": _post_tool_call("id", lambda value: _strip_cf(str(value))),
    "FI-TOOL-CALL-NAME-LOWERCASED": _post_tool_call("name", lambda value: str(value).lower()),
    "FJ-MESSAGE-NAME-LOWERCASED": _post_message("name", lambda value: str(value).lower()),
    "FK-TOOL-CALL-ID-FIELD-LOWERCASED": _post_message(
        "tool_call_id", lambda value: str(value).lower()
    ),
    "FK2-TOOL-CALL-ID-FIELD-NFKC": _post_message("tool_call_id", lambda value: _nfkc(str(value))),
    "FK3-TOOL-CALL-ID-FIELD-CF-STRIPPED": _post_message(
        "tool_call_id", lambda value: _strip_cf(str(value))
    ),
    "FL-ROLE-COERCED-TO-USER": _post_message("role", lambda value: "user"),
    "FM-JSON-VALUE-LOWERCASED": _post_content_part(_json_lower),
}


def test_each_fault_turns_a_check_red() -> None:
    for name, apply_fault in FAULTS.items():
        sink: list[str] = []
        variant = _load_fresh(name)
        apply_fault(variant)
        run_checks(variant, sink)
        _differential_variant_check(variant, sink)
        check(
            "FTF-0512-F1", f"F1-FAULTS {name}: fault must turn at least one check RED", bool(sink)
        )
        if sink:
            # the RED must come from a behavioural check, never from the fault
            # failing to install: no check text mentions anchors any more.
            check(
                "FTF-0512-F1",
                f"F1-FAULTS {name}: RED reason is a named check ({sink[0][:60]!r})",
                sink[0].startswith("FAIL ["),
            )


def _differential_variant_check(variant: types.ModuleType, sink: list[str]) -> None:
    unique = sorted({t for pair in INTENDED_COLLAPSES for t in pair} | set(DISTINCT_CORPUS))
    changed: set[frozenset[str]] = set()
    for i, a in enumerate(unique):
        for b in unique[i + 1 :]:
            old_same = frozen_pre_f1_normalize(a) == frozen_pre_f1_normalize(b)
            new_same = variant._normalize_text(a) == variant._normalize_text(b)
            if old_same != new_same:
                changed.add(frozenset({a, b}))
    check(
        "FTF-0512-F1",
        "differential changed set != intended",
        changed == {frozenset(p) for p in INTENDED_COLLAPSES},
        sink,
    )


def test_normalization_cost_stays_roughly_linear_up_to_the_record_cap() -> None:
    """F1-NFKC-QUADRATIC scale check. A single wall-clock number on a shared
    CI runner is not reliable evidence on its own (this module's own round-2
    finding was itself only reproduced at 99.7s on a specific run), so the
    primary proof that the quadratic class is closed is the behavioural
    refusal in `run_checks` above (an over-long combining run is refused
    outright, bounding worst-case cost to a small constant per run,
    independent of timing). This check is a sanity backstop over safe
    (bounded-run) content at 8K/64K/256K/2MiB: elapsed time must grow close
    to linearly, never anywhere near the quadratic blowup the finding
    reproduced (0.047s pre-F1 vs. 99.7s with the regression, for the same
    2.04 MB size)."""
    import time

    sizes = [8_000, 64_000, 256_000, 2_040_000]
    # realistic mixed content with short, legal (<=30) combining runs, not one
    # giant pathological run -- this is what the bound above must let through.
    unit = "the quick brówn fox رِّ jumps over the lazy dog 123 "
    elapsed: list[float] = []
    for size in sizes:
        text = (unit * (size // len(unit) + 1))[:size]
        production._normalize_text(text)  # warm up (regex/module caches)
        start = time.perf_counter()
        production._normalize_text(text)
        elapsed.append(time.perf_counter() - start)
    check(
        "FTF-0512-F1",
        f"F1-NFKC-QUADRATIC: 2.04 MB of safe content normalizes in well under 10s (took {elapsed[-1]:.3f}s)",
        elapsed[-1] < 10.0,
    )
    # quadratic growth would make the last/first ratio explode far past the
    # ~255x a linear algorithm predicts (2,040,000 / 8,000); a generous 1000x
    # ceiling catches real quadratic blowup (which would be ~255x squared,
    # tens of thousands) without being timing-flaky on a shared CI runner.
    if elapsed[0] > 0:
        ratio = elapsed[-1] / elapsed[0]
        check(
            "FTF-0512-F1",
            f"F1-NFKC-QUADRATIC: time scales near-linearly with size (ratio {ratio:.1f}, sizes 8K->2.04MB)",
            ratio < 1000,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0512-F1 look-alike gaps: {len(FAILURES)} failed")
        for failure in FAILURES[:40]:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0512-F1 look-alike gaps: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
