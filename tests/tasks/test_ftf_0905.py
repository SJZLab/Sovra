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


def _placed_ledger() -> tuple[CanaryPlacementLedger, CanaryMarker, CanaryMarker]:
    ledger = CanaryPlacementLedger()
    a = generate_canary()
    b = generate_canary()
    ledger.record_placement(PlacementRecord(a.canary_id, "trace", "literal", "reviewer-1"))
    ledger.record_placement(PlacementRecord(a.canary_id, "metric", "base64", "reviewer-1"))
    ledger.record_placement(PlacementRecord(b.canary_id, "artifact", "hex", "reviewer-2"))
    return ledger, a, b


def test_ledger_requires_every_placement_checked_before_complete() -> None:
    ledger, a, b = _placed_ledger()
    check(
        "FTF-0905-N2",
        "asserting complete before any check is refused",
        _raises(IncompleteLeakCoverageError, ledger.assert_leak_test_complete),
    )
    ledger.record_checked(a.canary_id, "trace", "literal", found=False)
    check(
        "FTF-0905-N2",
        "asserting complete after checking only 1 of 3 placements is still refused "
        "(the absence at 'trace' is not proof the other two sinks are clean)",
        _raises(IncompleteLeakCoverageError, ledger.assert_leak_test_complete),
    )
    ledger.record_checked(a.canary_id, "metric", "base64", found=False)
    check(
        "FTF-0905-N2",
        "asserting complete after 2 of 3 is still refused",
        _raises(IncompleteLeakCoverageError, ledger.assert_leak_test_complete),
    )
    ledger.record_checked(b.canary_id, "artifact", "hex", found=False)
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


def test_empty_ledger_is_vacuously_complete() -> None:
    check(
        "FTF-0905-A1",
        "a ledger with nothing placed is vacuously complete (nothing to assert unsafe)",
        _no_raise(CanaryPlacementLedger().assert_leak_test_complete),
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
    ledger = CanaryPlacementLedger()
    record = PlacementRecord(marker.canary_id, "trace", "literal", "reviewer-1")
    ledger.record_placement(record)
    ledger.assert_leak_test_complete()

def bad_fragment_parts():
    fragment_canary(marker, 1)

def bad_authorized_by():
    PlacementRecord(marker.canary_id, "trace", "literal", "")

print(outcome(bad_sink_placement))
print(outcome(bad_prefix_marker))
print(outcome(collision))
print(outcome(dup_placement))
print(outcome(found_leak))
print(outcome(incomplete_coverage))
print(outcome(bad_fragment_parts))
print(outcome(bad_authorized_by))
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
    "",
]

# (description, regex, replacement, output line index that must change)
MUTATIONS = [
    (
        "membership check removed (_member always passes)",
        r"if not isinstance\(value, str\) or value not in allowed:",
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
        r"        if found:\n            raise CanaryLeakedError\(",
        "        if False:\n            raise CanaryLeakedError(",
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
