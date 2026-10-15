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
  - F1-FAULTS: each class has a fault (the production source rewritten in
    memory) that must turn at least one check RED.

Run directly: python3 tests/tasks/test_ftf_0512_f1.py
"""

# ruff: noqa: E501
from __future__ import annotations

import re
import sys
import types
import unicodedata
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import workers.data.normalized_duplicate_detector as production  # noqa: E402

MODULE_PATH = ROOT / "workers" / "data" / "normalized_duplicate_detector.py"
FAILURES: list[str] = []


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


# --- the checks, parameterised by the module under test -------------------------


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

    # F1-CF-HANDPICKED: every Cf code point in Unicode (not only the 8 in
    # INSERTED_FORMAT_CHARS) is removed -- idempotent, never survives.
    for cp in _cf_code_points():
        char = chr(cp)
        once = mod._normalize_text(f"a{char}b")
        check(
            "FTF-0512-F1",
            f"F1-CF-HANDPICKED U+{cp:04X} is stripped",
            char not in once,
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


# --- fault injection: the production source rewritten in memory -----------------


def _load_variant(name: str, replacements: list[tuple[str, str]]) -> types.ModuleType:
    source = MODULE_PATH.read_text(encoding="utf-8")
    for old, new in replacements:
        if source.count(old) != 1:
            raise AssertionError(f"fault {name}: anchor not unique/present: {old!r}")
        source = source.replace(old, new)
    module = types.ModuleType(f"ftf_0512_f1_fault_{name}")
    module.__dict__["__file__"] = str(MODULE_PATH)
    sys.modules[module.__name__] = module
    exec(compile(source, str(MODULE_PATH), "exec"), module.__dict__)
    return module


FAULTS: dict[str, list[tuple[str, str]]] = {
    "F1-NFKC-REMOVED": [("folded = _fold_look_alikes(stripped)", "folded = stripped")],
    "F1-CF-REMOVED": [('if unicodedata.category(char) != "Cf"', "if True")],
    "F1-CF-STRIPPED-AFTER-NFKC": [
        (
            "stripped = _strip_format_characters(text)\n    _assert_safe_combining_run(stripped)\n    folded = _fold_look_alikes(stripped)",
            "folded = _fold_look_alikes(text)\n    _assert_safe_combining_run(folded)\n    folded = _strip_format_characters(folded)",
        )
    ],
    "F1-TS-PLACEHOLDER": [
        (
            '_ISO_TIMESTAMP_PATTERN.sub(" ", lowered)',
            '_ISO_TIMESTAMP_PATTERN.sub("<generated_timestamp>", lowered)',
        )
    ],
    "F1-UUID-PLACEHOLDER": [
        ('_UUID_PATTERN.sub(" ", redacted)', '_UUID_PATTERN.sub("<generated_id>", redacted)')
    ],
    "F1-TS-NOT-REDACTED": [
        ('redacted = _ISO_TIMESTAMP_PATTERN.sub(" ", lowered)', "redacted = lowered")
    ],
    "F1-UUID-NOT-REDACTED": [
        ('redacted = _UUID_PATTERN.sub(" ", redacted)', "redacted = redacted")
    ],
    "F1-LOWER-REMOVED": [("lowered = folded.lower()", "lowered = folded")],
    "F1-TOOL-ARGS-NORMALIZED": [
        (
            'return {"id": call_id, "name": name, "arguments": dict(arguments)}',
            'return {"id": call_id, "name": name, "arguments": json.loads(json.dumps(dict(arguments)).lower())}',
        ),
    ],
    "F1-TOOL-ARGS-NFKC": [
        (
            'return {"id": call_id, "name": name, "arguments": dict(arguments)}',
            'return {"id": call_id, "name": unicodedata.normalize("NFKC", name), "arguments": json.loads(unicodedata.normalize("NFKC", json.dumps(dict(arguments), ensure_ascii=False)))}',
        ),
    ],
    "F1-JSON-PART-NFKC": [
        (
            'return {"type": "json", "value": part["value"]}',
            'return {"type": "json", "value": json.loads(unicodedata.normalize("NFKC", json.dumps(part["value"], ensure_ascii=False)))}',
        ),
    ],
    "F1-DIGIT-LIKE-UNPROTECTED": [
        (
            "if unicodedata.category(char) in _DIGIT_LIKE_CATEGORIES:",
            "if False:",
        )
    ],
    "F1-COMBINING-RUN-UNCHECKED": [("_assert_safe_combining_run(stripped)", "None")],
    "FG2-TOOL-CALL-ID-LOWERCASED": [
        (
            'return {"id": call_id, "name": name, "arguments": dict(arguments)}',
            'return {"id": call_id.lower(), "name": name, "arguments": dict(arguments)}',
        )
    ],
    "FI-TOOL-CALL-NAME-LOWERCASED": [
        (
            'return {"id": call_id, "name": name, "arguments": dict(arguments)}',
            'return {"id": call_id, "name": name.lower(), "arguments": dict(arguments)}',
        )
    ],
    "FJ-MESSAGE-NAME-LOWERCASED": [
        ('normalized["name"] = name', 'normalized["name"] = name.lower()')
    ],
    "FK-TOOL-CALL-ID-FIELD-LOWERCASED": [
        (
            'normalized["tool_call_id"] = tool_call_id',
            'normalized["tool_call_id"] = tool_call_id.lower()',
        )
    ],
}


def test_each_fault_turns_a_check_red() -> None:
    for name, replacements in FAULTS.items():
        sink: list[str] = []
        try:
            variant = _load_variant(name, replacements)
            run_checks(variant, sink)
            _differential_variant_check(variant, sink)
        except AssertionError as exc:
            sink.append(f"anchor-error {exc}")
            check("FTF-0512-F1", f"fault {name} could not be built: {exc}", False)
            continue
        check(
            "FTF-0512-F1", f"F1-FAULTS {name}: fault must turn at least one check RED", bool(sink)
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
