#!/usr/bin/env python3
"""FTF-0905 -- Canary-secret generator (no-DB unit/fault tests).

Proves, without any database or network:

1. ACC-1 ("generate unique harmless canary markers and record authorized
   placement/sinks"): `generate_canary` produces unique, unmistakably
   prefixed markers; `encode_canary`/`fragment_canary` cover Section 12's
   literal/encoded/fragmented cases; `CanaryPlacementLedger` records
   authorized placements.
2. NEG-1a ("never use real secrets for leak test"): a canary value that
   collides with a caller-disclosed real secret is refused, never handed
   out.
3. NEG-1b ("never equate one absent marker with proof of all content
   safety"): a leak test that checked some but not all of its planted
   placements cannot be asserted complete; a confirmed leak raises
   immediately.
4. Fail-closed input handling on every public entry point.
5. Fault injection (Law 1): regex mutations of the real module, run in a
   fresh subprocess, each must change behaviour; the file is restored
   byte-identical.

Run directly: python3 tests/tasks/test_ftf_0905.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.security.canary_secret_generator import (  # noqa: E402
    ENCODINGS,
    SINKS,
    CanaryLeakedError,
    CanaryMarker,
    CanaryPlacementLedger,
    CanarySecretGeneratorError,
    IncompleteLeakCoverageError,
    InvalidCanaryRequestError,
    PlacementRecord,
    RealSecretCollisionError,
    _reject_if_known_real_secret,
    encode_canary,
    fragment_canary,
    generate_canary,
)

MODULE = ROOT / "packages" / "security" / "canary_secret_generator.py"
FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _raises(exc_type: type[BaseException], fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
    except exc_type:
        return True
    except Exception:
        return False
    return False


def _no_raise(fn, *args: object, **kwargs: object) -> bool:
    fn(*args, **kwargs)
    return True


def test_sinks_and_encodings_are_pinned() -> None:
    check(
        "FTF-0905-A1",
        "sinks are exactly Section 12's seven, in order",
        SINKS
        == (
            "error_path",
            "tool_result",
            "metric",
            "trace",
            "artifact",
            "browser_report",
            "pipeline_history",
        ),
    )
    check(
        "FTF-0905-A1",
        "encodings cover literal/encoded/fragmented",
        ENCODINGS == ("literal", "base64", "hex", "url", "fragmented"),
    )


def test_generate_canary_produces_unique_prefixed_markers() -> None:
    markers = [generate_canary() for _ in range(200)]
    check(
        "FTF-0905-A1",
        "every generated marker carries the unmistakable canary prefix",
        all(m.value.startswith("CANARY-DO-NOT-USE-") for m in markers),
    )
    check(
        "FTF-0905-A1",
        "200 generated markers are all unique (by value)",
        len({m.value for m in markers}) == 200,
    )
    check(
        "FTF-0905-A1",
        "200 generated markers are all unique (by id)",
        len({m.canary_id for m in markers}) == 200,
    )


def test_canary_marker_rejects_a_value_without_the_prefix() -> None:
    check(
        "FTF-0905-A1",
        "directly constructing a marker with a non-canary value is refused",
        _raises(InvalidCanaryRequestError, CanaryMarker, "canary-x", "super-secret-api-key"),
    )
    check(
        "FTF-0905-A1",
        "a malformed canary id is refused",
        _raises(InvalidCanaryRequestError, CanaryMarker, "bad id", "CANARY-DO-NOT-USE-x"),
    )


def test_reject_if_known_real_secret_direct() -> None:
    check(
        "FTF-0905-N1",
        "a value matching a disclosed real secret is refused",
        _raises(
            RealSecretCollisionError,
            _reject_if_known_real_secret,
            "CANARY-DO-NOT-USE-abc",
            {"CANARY-DO-NOT-USE-abc"},
        ),
    )
    check(
        "FTF-0905-N1",
        "a value not matching any disclosed secret is accepted (no raise)",
        _no_raise(
            _reject_if_known_real_secret, "CANARY-DO-NOT-USE-abc", {"sk-live-something-else"}
        ),
    )
    check(
        "FTF-0905-N1",
        "an empty disclosure set is fine",
        _no_raise(_reject_if_known_real_secret, "CANARY-DO-NOT-USE-abc", ()),
    )
    check(
        "FTF-0905-N1",
        "a non-collection disclosure set raises our own error",
        _raises(InvalidCanaryRequestError, _reject_if_known_real_secret, "x", 12345),
    )
    check(
        "FTF-0905-N1",
        "a bare string disclosure set (iterable but not a set of secrets) raises our own error",
        _raises(InvalidCanaryRequestError, _reject_if_known_real_secret, "x", "a-real-secret"),
    )
    check(
        "FTF-0905-N1",
        "a non-string entry in the disclosure set raises our own error",
        _raises(InvalidCanaryRequestError, _reject_if_known_real_secret, "x", [123]),
    )


def test_generate_canary_refuses_an_engineered_collision() -> None:
    """Integration of the collision check into generate_canary itself,
    forcing the random token deterministically so the test does not rely
    on an astronomically unlikely real collision."""
    with mock.patch(
        "packages.security.canary_secret_generator.secrets.token_hex", return_value="deadbeef"
    ):
        forced_value = "CANARY-DO-NOT-USE-deadbeef"
        check(
            "FTF-0905-N1",
            "generate_canary succeeds when the forced token does not collide",
            generate_canary(known_real_secrets=("something-else",)).value == forced_value,
        )
        check(
            "FTF-0905-N1",
            "generate_canary refuses when the forced token collides with a disclosed secret",
            _raises(RealSecretCollisionError, generate_canary, known_real_secrets=(forced_value,)),
        )


def test_encode_canary_covers_literal_and_encoded_forms() -> None:
    import base64
    from urllib.parse import quote, unquote

    marker = generate_canary()
    literal = encode_canary(marker, "literal")
    b64 = encode_canary(marker, "base64")
    hexed = encode_canary(marker, "hex")
    urled = encode_canary(marker, "url")
    check("FTF-0905-A1", "literal encoding returns the raw value", literal == marker.value)
    check(
        "FTF-0905-A1",
        "base64 encoding round-trips to the raw value",
        base64.b64decode(b64).decode("utf-8") == marker.value,
    )
    check(
        "FTF-0905-A1",
        "hex encoding round-trips to the raw value",
        bytes.fromhex(hexed).decode("utf-8") == marker.value,
    )
    check(
        "FTF-0905-A1",
        "url encoding is percent-encoding (round-trips via unquote), matching quote()'s own output",
        urled == quote(marker.value, safe="") and unquote(urled) == marker.value,
    )
    check(
        "FTF-0905-A1",
        "an encoded form never equals a different marker's literal value",
        b64 != marker.value and hexed != marker.value,
    )


def test_encode_canary_fail_closed() -> None:
    marker = generate_canary()
    check(
        "FTF-0905-A2",
        "a non-CanaryMarker is refused",
        _raises(InvalidCanaryRequestError, encode_canary, "not-a-marker", "literal"),
    )
    check(
        "FTF-0905-A2",
        "an unknown encoding is refused",
        _raises(InvalidCanaryRequestError, encode_canary, marker, "rot13"),
    )
    try:
        encode_canary(marker, "fragmented")
        fragmented_message = None
    except InvalidCanaryRequestError as exc:
        fragmented_message = str(exc)
    check(
        "FTF-0905-A2",
        "'fragmented' is refused by encode_canary with its own specific message "
        "(not just the generic 'unsupported encoding' fallback)",
        fragmented_message is not None and "fragment_canary" in fragmented_message,
    )


def test_fragment_canary_reassembles_exactly() -> None:
    marker = generate_canary()
    for parts in (2, 3, 4, 8):
        fragments = fragment_canary(marker, parts)
        check(
            "FTF-0905-A1",
            f"fragment_canary({parts}) returns exactly {parts} non-empty parts",
            len(fragments) == parts and all(f for f in fragments),
        )
        check(
            "FTF-0905-A1",
            f"fragment_canary({parts}) reassembles to the exact original value",
            "".join(fragments) == marker.value,
        )
        check(
            "FTF-0905-A1",
            f"no single fragment of {parts} equals the whole marker (it is actually split)",
            all(f != marker.value for f in fragments),
        )


def test_fragment_canary_fail_closed() -> None:
    marker = generate_canary()
    check(
        "FTF-0905-A2",
        "a non-CanaryMarker is refused",
        _raises(InvalidCanaryRequestError, fragment_canary, "not-a-marker", 2),
    )
    check(
        "FTF-0905-A2",
        "parts below the minimum (1) is refused",
        _raises(InvalidCanaryRequestError, fragment_canary, marker, 1),
    )
    check(
        "FTF-0905-A2",
        "parts above the maximum is refused",
        _raises(InvalidCanaryRequestError, fragment_canary, marker, 9),
    )
    check(
        "FTF-0905-A2",
        "a bool is refused as parts (bool is not treated as a real int)",
        _raises(InvalidCanaryRequestError, fragment_canary, marker, True),
    )
    check(
        "FTF-0905-A2",
        "a float is refused as parts",
        _raises(InvalidCanaryRequestError, fragment_canary, marker, 2.0),
    )
    check(
        "FTF-0905-A2",
        "a very large parts count is still refused by the same maximum bound",
        _raises(InvalidCanaryRequestError, fragment_canary, marker, 10_000),
    )


_PLAN = (("trace", "literal"), ("metric", "base64"))


def _placed_ledger() -> tuple[CanaryPlacementLedger, CanaryMarker, CanaryMarker]:
    ledger = CanaryPlacementLedger(_PLAN)
    a = generate_canary()
    b = generate_canary()
    for marker in (a, b):
        for sink, encoding in _PLAN:
            ledger.record_placement(PlacementRecord(marker.canary_id, sink, encoding, "reviewer-1"))
    return ledger, a, b


def test_ledger_requires_every_placement_checked_before_complete() -> None:
    ledger, a, b = _placed_ledger()
    check(
        "FTF-0905-N2",
        "asserting complete before any check is refused",
        _raises(IncompleteLeakCoverageError, ledger.assert_leak_test_complete),
    )
    steps = [(a, "trace", "literal"), (a, "metric", "base64"), (b, "trace", "literal")]
    for marker, sink, encoding in steps:
        ledger.record_checked(marker.canary_id, sink, encoding, found=False)
        check(
            "FTF-0905-N2",
            f"asserting complete with an unchecked placement left ({sink}) is still refused",
            _raises(IncompleteLeakCoverageError, ledger.assert_leak_test_complete),
        )
    ledger.record_checked(b.canary_id, "metric", "base64", found=False)
    check(
        "FTF-0905-N2",
        "asserting complete after every placement was checked, all clean, succeeds",
        _no_raise(ledger.assert_leak_test_complete),
    )


def test_ledger_raises_immediately_on_a_confirmed_leak() -> None:
    ledger, a, b = _placed_ledger()
    ledger.record_checked(a.canary_id, "trace", "literal", found=False)
    check(
        "FTF-0905-N2",
        "found=True at one sink raises CanaryLeakedError immediately, not deferred",
        _raises(
            CanaryLeakedError,
            ledger.record_checked,
            a.canary_id,
            "metric",
            "base64",
            found=True,
        ),
    )


def test_ledger_rejects_duplicate_placement_and_unplaced_check() -> None:
    ledger = CanaryPlacementLedger()
    marker = generate_canary()
    record = PlacementRecord(marker.canary_id, "trace", "literal", "reviewer-1")
    check(
        "FTF-0905-A2",
        "recording a non-PlacementRecord placement is refused",
        _raises(InvalidCanaryRequestError, ledger.record_placement, "not-a-record"),
    )
    ledger.record_placement(record)
    check(
        "FTF-0905-A1",
        "re-placing the identical (canary, sink, encoding) combination is refused",
        _raises(InvalidCanaryRequestError, ledger.record_placement, record),
    )
    check(
        "FTF-0905-A2",
        "checking a placement that was never recorded is refused",
        _raises(
            InvalidCanaryRequestError,
            ledger.record_checked,
            marker.canary_id,
            "metric",
            "literal",
            found=False,
        ),
    )
    check(
        "FTF-0905-A2",
        "a non-bool 'found' is refused",
        _raises(
            InvalidCanaryRequestError,
            ledger.record_checked,
            marker.canary_id,
            "trace",
            "literal",
            found="no",
        ),
    )


def test_placement_record_fail_closed() -> None:
    marker = generate_canary()
    check(
        "FTF-0905-A2",
        "an unknown sink is refused",
        _raises(
            InvalidCanaryRequestError, PlacementRecord, marker.canary_id, "stdout", "literal", "r"
        ),
    )
    check(
        "FTF-0905-A2",
        "an unknown encoding is refused",
        _raises(
            InvalidCanaryRequestError, PlacementRecord, marker.canary_id, "trace", "rot13", "r"
        ),
    )
    check(
        "FTF-0905-A2",
        "an empty authorized_by is refused",
        _raises(
            InvalidCanaryRequestError, PlacementRecord, marker.canary_id, "trace", "literal", ""
        ),
    )
    check(
        "FTF-0905-A2",
        "authorized_by of exactly the max length (200) is the boundary and is accepted",
        _no_raise(PlacementRecord, marker.canary_id, "trace", "literal", "r" * 200),
    )
    check(
        "FTF-0905-A2",
        "authorized_by one char past the max length is refused",
        _raises(
            InvalidCanaryRequestError,
            PlacementRecord,
            marker.canary_id,
            "trace",
            "literal",
            "r" * 201,
        ),
    )
    check(
        "FTF-0905-A2",
        "an unhashable sink value raises our own error, never a bare TypeError",
        _raises(InvalidCanaryRequestError, PlacementRecord, marker.canary_id, [], "literal", "r"),
    )
    check(
        "FTF-0905-A2",
        "every subclass error is a CanarySecretGeneratorError",
        issubclass(RealSecretCollisionError, CanarySecretGeneratorError)
        and issubclass(CanaryLeakedError, CanarySecretGeneratorError)
        and issubclass(IncompleteLeakCoverageError, CanarySecretGeneratorError)
        and issubclass(InvalidCanaryRequestError, CanarySecretGeneratorError),
    )


def test_confirmed_leak_stays_recorded_after_the_error_is_caught() -> None:
    ledger, a, b = _placed_ledger()
    try:
        ledger.record_checked(a.canary_id, "trace", "literal", found=True)
    except CanaryLeakedError:
        pass
    for marker, sink, encoding in (
        (a, "metric", "base64"),
        (b, "trace", "literal"),
        (b, "metric", "base64"),
    ):
        ledger.record_checked(marker.canary_id, sink, encoding, found=False)
    check(
        "FTF-0905-R2-1",
        "every other sink clean after a caught leak: complete still raises CanaryLeakedError",
        _raises(CanaryLeakedError, ledger.assert_leak_test_complete),
    )


def test_complete_requires_something_checked_and_the_declared_plan() -> None:
    check(
        "FTF-0905-R2-2",
        "an empty ledger is never complete (nothing checked proves nothing)",
        _raises(IncompleteLeakCoverageError, CanaryPlacementLedger().assert_leak_test_complete),
    )
    ledger = CanaryPlacementLedger()
    marker = generate_canary()
    ledger.record_placement(PlacementRecord(marker.canary_id, "trace", "literal", "reviewer-1"))
    ledger.record_checked(marker.canary_id, "trace", "literal", found=False)
    check(
        "FTF-0905-R2-2",
        "one placement + one clean check does not satisfy the default full plan",
        _raises(IncompleteLeakCoverageError, ledger.assert_leak_test_complete),
    )
    full = CanaryPlacementLedger()
    for sink in SINKS:
        for encoding in ENCODINGS:
            full.record_placement(PlacementRecord(marker.canary_id, sink, encoding, "reviewer-1"))
            full.record_checked(marker.canary_id, sink, encoding, found=False)
    check(
        "FTF-0905-R2-2",
        "the full default plan, every pair placed and checked clean, completes",
        _no_raise(full.assert_leak_test_complete),
    )
    other = generate_canary()
    full.record_placement(PlacementRecord(other.canary_id, "trace", "literal", "reviewer-1"))
    full.record_checked(other.canary_id, "trace", "literal", found=False)
    check(
        "FTF-0905-R2-2",
        "a second canary placed at only one pair is short of the plan",
        _raises(IncompleteLeakCoverageError, full.assert_leak_test_complete),
    )
    bad: object
    for bad in ([], (), "trace", [("trace",)], [("nope", "literal")], [["trace", "literal"]], [[]]):
        check(
            "FTF-0905-R2-2",
            f"an invalid declared plan {bad!r} is refused",
            _raises(InvalidCanaryRequestError, CanaryPlacementLedger, bad),
        )


def test_declared_plan_must_be_a_collection_of_pairs() -> None:
    for bad_plan in (5, 1.5, object(), "trace", b"xy"):
        try:
            CanaryPlacementLedger(bad_plan)  # type: ignore[arg-type]
            outcome = "allowed"
        except InvalidCanaryRequestError as exc:
            outcome = str(exc)
        except Exception as exc:
            outcome = "ERROR:" + type(exc).__name__
        check(
            "FTF-0905-R2-2",
            f"a non-collection / text plan {bad_plan!r} is refused with the typed collection error",
            "collection of pairs" in outcome,
        )


def test_canary_id_is_independent_of_the_token_and_entropy_is_pinned() -> None:
    prefix = "CANARY-DO-NOT-USE-"
    markers = [generate_canary() for _ in range(50)]
    for m in markers:
        token = m.value[len(prefix) :]
        check(
            "FTF-0905-R2-3",
            "the canary id contains no 16+ char run of the token",
            all(token[i : i + 16] not in m.canary_id for i in range(len(token) - 15)),
        )
        check(
            "FTF-0905-R2-4",
            "the token carries at least 128 bits (>= 32 hex chars)",
            len(token) >= 32,
        )
    check(
        "FTF-0905-R2-3",
        "ids are distinct from token-derived ids and unique across 50 markers",
        len({m.canary_id for m in markers}) == 50,
    )


def test_error_text_never_carries_the_token_or_a_real_secret() -> None:
    marker = generate_canary()
    token = marker.value[len("CANARY-DO-NOT-USE-") :]
    ledger = CanaryPlacementLedger(_PLAN)
    ledger.record_placement(PlacementRecord(marker.canary_id, "trace", "literal", "reviewer-1"))
    try:
        ledger.record_checked(marker.canary_id, "trace", "literal", found=True)
        text = ""
    except CanaryLeakedError as exc:
        text = str(exc) + repr(exc.args)
    check(
        "FTF-0905-R2-3",
        "CanaryLeakedError is raised and its text carries neither the token nor the value",
        text != "" and token not in text and marker.value not in text,
    )
    try:
        generate_canary_collision = "CANARY-DO-NOT-USE-REALSECRET99"
        _reject_if_known_real_secret(generate_canary_collision, {generate_canary_collision})
        text = ""
    except RealSecretCollisionError as exc:
        text = str(exc) + repr(exc.args)
    check(
        "FTF-0905-R2-3",
        "the collision error is raised and does not echo the secret",
        text != "" and "REALSECRET99" not in text,
    )


class _LyingStr(str):
    def __eq__(self, other: object) -> bool:
        return True

    def __hash__(self) -> int:
        return hash("trace")


def test_record_checked_input_types_are_exact_and_typed() -> None:
    marker = generate_canary()
    ledger = CanaryPlacementLedger(_PLAN)
    ledger.record_placement(PlacementRecord(marker.canary_id, "trace", "literal", "reviewer-1"))
    bad_id: object
    for bad_id in ([], {}, 7, None):
        check(
            "FTF-0905-R2-5",
            f"an unhashable/non-str canary id {bad_id!r} raises the module's own error",
            _raises(
                InvalidCanaryRequestError,
                ledger.record_checked,
                bad_id,
                "trace",
                "literal",
                found=False,
            ),
        )
    check(
        "FTF-0905-R2-5",
        "a str subclass whose __eq__ is always True is not accepted as a sink",
        _raises(
            InvalidCanaryRequestError,
            ledger.record_checked,
            marker.canary_id,
            _LyingStr("nope"),
            "literal",
            found=False,
        ),
    )


# --- Fault injection (Law 1): mutate the real module, prove each guard is load-bearing. ---

_PROBE = """
import sys
sys.path.insert(0, {root!r})
from packages.security.canary_secret_generator import (
    PlacementRecord, CanaryMarker, CanaryPlacementLedger, CanarySecretGeneratorError,
    _reject_if_known_real_secret, fragment_canary, generate_canary,
)

def outcome(fn):
    try:
        fn()
        return "allowed"
    except CanarySecretGeneratorError as exc:
        return exc.code
    except Exception as exc:
        return "ERROR:" + type(exc).__name__

marker = generate_canary()

def bad_sink_placement():
    PlacementRecord(marker.canary_id, "not_a_sink", "literal", "reviewer-1")

def bad_prefix_marker():
    CanaryMarker("canary-x", "not-a-canary-value")

def collision():
    _reject_if_known_real_secret("CANARY-DO-NOT-USE-abc", {{"CANARY-DO-NOT-USE-abc"}})

def dup_placement():
    ledger = CanaryPlacementLedger()
    record = PlacementRecord(marker.canary_id, "trace", "literal", "reviewer-1")
    ledger.record_placement(record)
    ledger.record_placement(record)

def found_leak():
    ledger = CanaryPlacementLedger()
    record = PlacementRecord(marker.canary_id, "trace", "literal", "reviewer-1")
    ledger.record_placement(record)
    ledger.record_checked(marker.canary_id, "trace", "literal", found=True)

def incomplete_coverage():
    ledger = CanaryPlacementLedger([("trace", "literal")])
    record = PlacementRecord(marker.canary_id, "trace", "literal", "reviewer-1")
    ledger.record_placement(record)
    other = PlacementRecord(marker.canary_id, "metric", "literal", "reviewer-1")
    ledger.record_placement(other)
    ledger.record_checked(marker.canary_id, "trace", "literal", found=False)
    ledger.assert_leak_test_complete()

def bad_fragment_parts():
    fragment_canary(marker, 1)

def bad_authorized_by():
    PlacementRecord(marker.canary_id, "trace", "literal", "")

class LyingStr(str):
    def __eq__(self, other):
        return True
    def __hash__(self):
        return hash("trace")

PLAN = [("trace", "literal")]

def swallowed_leak():
    ledger = CanaryPlacementLedger(PLAN)
    ledger.record_placement(PlacementRecord(marker.canary_id, "trace", "literal", "reviewer-1"))
    try:
        ledger.record_checked(marker.canary_id, "trace", "literal", found=True)
    except CanarySecretGeneratorError:
        pass
    ledger.assert_leak_test_complete()

def empty_ledger():
    CanaryPlacementLedger().assert_leak_test_complete()

def short_of_plan():
    ledger = CanaryPlacementLedger()
    ledger.record_placement(PlacementRecord(marker.canary_id, "trace", "literal", "reviewer-1"))
    ledger.record_checked(marker.canary_id, "trace", "literal", found=False)
    ledger.assert_leak_test_complete()

def unhashable_id():
    ledger = CanaryPlacementLedger(PLAN)
    ledger.record_checked([], "trace", "literal", found=False)

def lying_sink():
    ledger = CanaryPlacementLedger(PLAN)
    ledger.record_placement(PlacementRecord(marker.canary_id, "trace", "literal", "reviewer-1"))
    ledger.record_checked(marker.canary_id, LyingStr("nope"), "literal", found=False)

def collision_text():
    try:
        secret = "CANARY-DO-NOT-USE-REALSECRET99"
        _reject_if_known_real_secret(secret, {{secret}})
    except CanarySecretGeneratorError as exc:
        return "LEAK" if "REALSECRET99" in str(exc) + repr(exc.args) else "clean"
    return "allowed"

PREFIX = "CANARY-DO-NOT-USE-"
token = marker.value[len(PREFIX):]
derived = any(token[i:i + 16] in marker.canary_id for i in range(len(token) - 15))

print(outcome(bad_sink_placement))
print(outcome(bad_prefix_marker))
print(outcome(collision))
print(outcome(dup_placement))
print(outcome(found_leak))
print(outcome(incomplete_coverage))
print(outcome(bad_fragment_parts))
print(outcome(bad_authorized_by))
print(outcome(swallowed_leak))
print(outcome(empty_ledger))
print(outcome(short_of_plan))
print(outcome(unhashable_id))
print(outcome(lying_sink))
print(collision_text())
print("derived" if derived else "independent")
print("weak" if len(token) < 32 else "strong")
"""


def _probe(source: str | None) -> list[str]:
    original = MODULE.read_bytes()
    try:
        if source is not None:
            MODULE.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-c", _PROBE.format(root=str(ROOT))],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        MODULE.write_bytes(original)
    if MODULE.read_bytes() != original:
        raise RuntimeError("module not restored")
    return (
        result.stdout.split("\n") if result.returncode == 0 else ["ERROR:" + result.stderr[-300:]]
    )


BASELINE = [
    "canary_invalid_request",  # bad sink
    "canary_invalid_request",  # bad prefix marker
    "canary_real_secret_collision",  # collision
    "canary_invalid_request",  # duplicate placement
    "canary_leaked",  # found leak
    "canary_incomplete_leak_coverage",  # incomplete coverage
    "canary_invalid_request",  # bad fragment parts
    "canary_invalid_request",  # bad authorized_by
    "canary_leaked",  # a caught leak stays recorded
    "canary_incomplete_leak_coverage",  # empty ledger
    "canary_incomplete_leak_coverage",  # short of the declared plan
    "canary_invalid_request",  # unhashable canary id
    "canary_invalid_request",  # lying str subclass as sink
    "clean",  # collision text does not echo the secret
    "independent",  # canary id not derived from the token
    "strong",  # >= 128-bit token
    "",
]

# (description, regex, replacement, output line index that must change)
MUTATIONS = [
    (
        "membership check removed (_member always passes)",
        r"if type\(value\) is not str or value not in allowed:",
        "if False:",
        0,
    ),
    (
        "canary-prefix check removed on direct construction",
        r"if not isinstance\(self\.value, str\) or not self\.value\.startswith\(_CANARY_PREFIX\):",
        "if False:",
        1,
    ),
    (
        "real-secret collision check removed",
        r"if secret == value:",
        "if False:",
        2,
    ),
    (
        "duplicate-placement check removed",
        r"if key in self\._placements:",
        "if False:",
        3,
    ),
    (
        "found-leak raise removed (a real leak is silently swallowed)",
        r"        if found:\n",
        "        if False:\n",
        4,
    ),
    (
        "incomplete-coverage check removed",
        r"        if missing:\n            raise IncompleteLeakCoverageError\(",
        "        if False:\n            raise IncompleteLeakCoverageError(",
        5,
    ),
    (
        "fragment parts bound check removed",
        r"if parts < _MIN_FRAGMENTS or parts > _MAX_FRAGMENTS:",
        "if False:",
        6,
    ),
    (
        "authorized_by non-empty check dropped",
        r'_text\(self\.authorized_by, "authorized_by"\)',
        "None",
        7,
    ),
]


def test_fault_injection_each_guard_is_load_bearing() -> None:
    check("FTF-0905-FAULT", "unmutated probe matches baseline", _probe(None) == BASELINE)
    text = MODULE.read_text(encoding="utf-8")
    for description, pattern, replacement, index in MUTATIONS:
        mutated, count = re.subn(pattern, replacement, text, count=1)
        check("FTF-0905-FAULT", f"mutation pattern matches once: {description}", count == 1)
        if count != 1:
            continue
        out = _probe(mutated)
        check(
            "FTF-0905-FAULT",
            f"mutation turns the probe RED: {description}",
            len(out) == len(BASELINE) and out[index] != BASELINE[index],
        )


# --- Round 3 (FTF-0905-R3-*) ------------------------------------------------
# The probe below is loaded from a *file path* (never the shared package), so a
# mutant is a private temp copy: the real module is never rewritten and a
# stale __pycache__ can never leak a mutant into another import.
_R3_PROBE = r"""
import importlib.util, sys
from unittest import mock

spec = importlib.util.spec_from_file_location("canary_under_test", sys.argv[1])
gen = importlib.util.module_from_spec(spec)
sys.modules["canary_under_test"] = gen
spec.loader.exec_module(gen)

PREFIX = "CANARY-DO-NOT-USE-"
OPERATOR = "reviewer-1"

def default_minus_one():
    marker = gen.generate_canary()
    pairs = [(s, e) for s in gen.SINKS for e in gen.ENCODINGS]
    bad = []
    for skipped in pairs:
        ledger = gen.CanaryPlacementLedger()
        for sink, encoding in pairs:
            if (sink, encoding) == skipped:
                continue
            ledger.record_placement(gen.PlacementRecord(marker.canary_id, sink, encoding, OPERATOR))
            ledger.record_checked(marker.canary_id, sink, encoding, found=False)
        try:
            ledger.assert_leak_test_complete()
            bad.append(skipped)
        except gen.IncompleteLeakCoverageError:
            pass
    if not bad and len(pairs) == 35:
        return "refused-all-35"
    return f"completed-with-{len(bad)}-missing"

def literal_only():
    marker = gen.generate_canary()
    ledger = gen.CanaryPlacementLedger()
    for sink in gen.SINKS:
        ledger.record_placement(gen.PlacementRecord(marker.canary_id, sink, "literal", OPERATOR))
        ledger.record_checked(marker.canary_id, sink, "literal", found=False)
    try:
        ledger.assert_leak_test_complete()
    except gen.IncompleteLeakCoverageError:
        return "refused"
    return "completed"

def _one_pair_ledger():
    marker = gen.generate_canary()
    ledger = gen.CanaryPlacementLedger([("trace", "literal")])
    ledger.record_placement(gen.PlacementRecord(marker.canary_id, "trace", "literal", OPERATOR))
    return ledger, marker

def sticky(first_found, second_found):
    ledger, marker = _one_pair_ledger()
    for found in (first_found, second_found):
        try:
            ledger.record_checked(marker.canary_id, "trace", "literal", found=found)
        except gen.CanaryLeakedError:
            pass
    try:
        ledger.assert_leak_test_complete()
    except gen.CanaryLeakedError:
        return "leak-stays"
    except gen.CanarySecretGeneratorError as exc:
        return exc.code
    return "completed"

def draws(sequence):
    calls = []
    def fake(nbytes):
        calls.append(nbytes)
        return sequence[len(calls) - 1]
    with mock.patch.object(gen.secrets, "token_hex", side_effect=fake):
        marker = gen.generate_canary()
    return marker, len(calls)

A = "0123456789abcdef0123456789abcdef"
B = "fedcba98765432100123456789abcdef"
C = "13579bdf02468ace13579bdf02468ace"
D = "a1b2c3d4e5f60718293a4b5c6d7e8f90"

def id_only_draw():
    base, n = draws([A, B])
    moved, _ = draws([A, C])
    ok = (
        n == 2
        and base.value == PREFIX + A
        and base.canary_id == "canary-" + B
        and moved.value == base.value
        and moved.canary_id == "canary-" + C
    )
    return "id-follows-its-own-draw" if ok else "id-not-independent"

def token_only_draw():
    base, _ = draws([A, B])
    moved, _ = draws([D, B])
    ok = moved.canary_id == base.canary_id and moved.value == PREFIX + D
    return "value-follows-its-own-draw" if ok else "token-leaks-into-id"

print(default_minus_one())
print(literal_only())
print(sticky(False, True))
print(sticky(True, False))
print(id_only_draw())
print(token_only_draw())
"""

R3_BASELINE = [
    "refused-all-35",
    "refused",
    "leak-stays",
    "leak-stays",
    "id-follows-its-own-draw",
    "value-follows-its-own-draw",
    "",
]


def _r3_probe(module_path: Path) -> list[str]:
    import os
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ftf-0905-r3-pyc-") as pyc_dir:
        env = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPYCACHEPREFIX": pyc_dir,
        }
        result = subprocess.run(
            [sys.executable, "-B", "-c", _R3_PROBE, str(module_path)],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            check=False,
        )
    if result.returncode != 0:
        return ["ERROR:" + result.stderr[-300:]]
    return result.stdout.split("\n")


def test_r3_default_plan_sticky_leak_and_id_independence() -> None:
    out = _r3_probe(MODULE)
    check(
        "FTF-0905-R3-1",
        f"default plan refuses every 34-of-35 subset and literal-only ({out[:2]})",
        out[:2] == R3_BASELINE[:2],
    )
    check(
        "FTF-0905-R3-2",
        f"a leak on one key stays sticky across a re-check ({out[2:4]})",
        out[2:4] == R3_BASELINE[2:4],
    )
    check(
        "FTF-0905-R3-3",
        f"canary_id and token come from independent draws ({out[4:6]})",
        out[4:6] == R3_BASELINE[4:6],
    )
    check("FTF-0905-R3-ALL", "whole R3 probe matches baseline", out == R3_BASELINE)


_PLAN_LINE = r"plan = \{\(sink, encoding\) for sink in SINKS for encoding in ENCODINGS\}"
_CHECKED_ADD = "        self._checked.add(key)\n        if found:\n"
_ID_LINE = r'canary_id = f"canary-\{secrets\.token_hex\(_TOKEN_NBYTES\)\}"'

# (description, regex, replacement, probe line index that must change)
R3_MUTATIONS = [
    (
        "F6 default plan = every sink x literal only",
        _PLAN_LINE,
        'plan = {(sink, "literal") for sink in SINKS}',
        1,
    ),
    (
        "F7 default plan = two pairs",
        _PLAN_LINE,
        'plan = {("trace", "literal"), ("metric", "literal")}',
        0,
    ),
    (
        "F7b default plan minus one pair",
        _PLAN_LINE,
        "plan = {(sink, encoding) for sink in SINKS for encoding in ENCODINGS}"
        ' - {("pipeline_history", "fragmented")}',
        0,
    ),
    (
        "F8 a re-check of an already-checked key is ignored",
        re.escape(_CHECKED_ADD),
        "        _seen = key in self._checked\n        self._checked.add(key)\n"
        "        if found and not _seen:\n",
        2,
    ),
    (
        "F9 a clean re-check clears a recorded leak",
        re.escape(_CHECKED_ADD),
        "        self._checked.add(key)\n        if not found:\n"
        "            self._leaked.discard(key)\n        if found:\n",
        3,
    ),
    (
        "F11 canary_id = reversed token",
        _ID_LINE,
        'canary_id = f"canary-{token[::-1]}"',
        4,
    ),
    (
        "F12 canary_id = hex-complemented token",
        _ID_LINE,
        'canary_id = "canary-" + "".join(format(15 - int(c, 16), "x") for c in token)',
        4,
    ),
]


def test_r3_each_fault_turns_the_probe_red() -> None:
    import tempfile

    text = MODULE.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="ftf-0905-r3-mutant-") as tmp:
        for n, (description, pattern, replacement, index) in enumerate(R3_MUTATIONS):
            mutated, count = re.subn(pattern, replacement.replace("\\", "\\\\"), text, count=1)
            check("FTF-0905-R3-FAULT", f"mutation pattern matches once: {description}", count == 1)
            if count != 1:
                continue
            path = Path(tmp) / f"mutant_{n}.py"
            path.write_text(mutated, encoding="utf-8")
            out = _r3_probe(path)
            check(
                "FTF-0905-R3-FAULT",
                f"mutation turns the probe RED at line {index}: {description} ({out})",
                len(out) == len(R3_BASELINE) and out[index] != R3_BASELINE[index],
            )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FTF-0905 Canary-secret generator tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0905 Canary-secret generator tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
