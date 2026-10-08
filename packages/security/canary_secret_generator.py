"""FTF-0905 -- Canary-secret generator (Section 11-12, 65.4, 70, 77, 81).

Section 12 requires leak qualification to "test literal, encoded and
fragmented canaries in error paths, tool results, metrics, traces,
artifacts, browser reports and pipeline histories" -- planting harmless
markers and later checking every sink they were placed in for recovery.
This module owns generating those markers and recording their authorized
placements; a later, separate task (FTF-0906, egress capture/verification)
owns actually scanning the named sinks and feeding the result back here.

Two rules make the negative_acceptance executable, not advisory:

* **Never use a real secret for the leak test.** `generate_canary` never
  derives its value from caller-supplied material: it is a fixed,
  unmistakable `CANARY-DO-NOT-USE-` prefix in front of a cryptographically
  random token from `secrets.token_hex` (real randomness, never a
  seedable/deterministic generator). As defense in depth, the generated
  value is still checked against every real secret the caller discloses
  before it is returned, so even an astronomically unlikely collision with
  real material is refused rather than silently handed out as a "canary".
* **One absent marker is never proof of all content safety.** A leak test
  that plants a canary in several sinks but only checks one of them learns
  nothing about the others. `CanaryPlacementLedger.assert_leak_test_complete`
  fails closed unless *every* placement that was recorded has also been
  explicitly marked checked; a missing check is never silently treated as
  "not leaked", and a single confirmed leak raises immediately rather than
  being averaged away by sinks that came back clean.

This module is a pure library with no live caller yet: the producers that
will actually place canaries in a real error path/trace/artifact (training
and evaluation workers) are separate, dependency-gated tasks. That gap is
disclosed in docs/spec-matrix.yaml, the same blank-`proving_symbol`
precedent FTF-0901/FTF-0902/FTF-0903 already carry.
"""

from __future__ import annotations

import base64
import re
import secrets
from collections.abc import Collection
from dataclasses import dataclass
from urllib.parse import quote

__all__ = [
    "SINKS",
    "ENCODINGS",
    "CanarySecretGeneratorError",
    "InvalidCanaryRequestError",
    "RealSecretCollisionError",
    "CanaryLeakedError",
    "IncompleteLeakCoverageError",
    "CanaryMarker",
    "PlacementRecord",
    "CanaryPlacementLedger",
    "generate_canary",
    "encode_canary",
    "fragment_canary",
]

# Section 12's own sink list, verbatim.
SINKS: tuple[str, ...] = (
    "error_path",
    "tool_result",
    "metric",
    "trace",
    "artifact",
    "browser_report",
    "pipeline_history",
)
ENCODINGS: tuple[str, ...] = ("literal", "base64", "hex", "url", "fragmented")

_CANARY_PREFIX = "CANARY-DO-NOT-USE-"
_MARKER_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,98}")
_TOKEN_NBYTES = 16
_STRING_MAX_LENGTH = 200
_MIN_FRAGMENTS = 2
_MAX_FRAGMENTS = 8
assert _MAX_FRAGMENTS < len(_CANARY_PREFIX), "fragment_canary relies on this to never overrun"


class CanarySecretGeneratorError(Exception):
    """Base; every subclass has a stable `code` for the API error body."""

    code = "canary_secret_generator_error"


class InvalidCanaryRequestError(CanarySecretGeneratorError):
    code = "canary_invalid_request"


class RealSecretCollisionError(CanarySecretGeneratorError):
    """negative_acceptance: never use a real secret for the leak test."""

    code = "canary_real_secret_collision"


class CanaryLeakedError(CanarySecretGeneratorError):
    """A placed canary was actually recovered from a sink: a real leak."""

    code = "canary_leaked"


class IncompleteLeakCoverageError(CanarySecretGeneratorError):
    """negative_acceptance: one absent marker is never proof of total
    content safety -- every planted (canary, sink, encoding) combination
    must be explicitly checked before the ledger can call the test
    complete."""

    code = "canary_incomplete_leak_coverage"


def _present(value: object) -> bool:
    return isinstance(value, str) and bool(value) and not value.isspace()


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not _present(value) or len(value) > _STRING_MAX_LENGTH:
        raise InvalidCanaryRequestError(f"{what} must be a non-empty string")
    return value


def _member(value: object, allowed: tuple[str, ...], what: str) -> str:
    # Type check before membership: an unhashable value must raise our own
    # error rather than a bare TypeError from `in`.
    if not isinstance(value, str) or value not in allowed:
        raise InvalidCanaryRequestError(f"{what} must be one of {allowed}")
    return value


def _marker_id(value: object, what: str) -> str:
    if not isinstance(value, str) or not _MARKER_ID_RE.fullmatch(value):
        raise InvalidCanaryRequestError(f"{what} is not a bounded id")
    return value


def _reject_if_known_real_secret(value: str, known_real_secrets: Collection[object]) -> None:
    if not isinstance(known_real_secrets, Collection) or isinstance(
        known_real_secrets, str | bytes
    ):
        raise InvalidCanaryRequestError("known_real_secrets must be a collection of strings")
    for secret in known_real_secrets:
        if not isinstance(secret, str) or not secret:
            raise InvalidCanaryRequestError(
                "every real secret to exclude must be a non-empty string"
            )
        if secret == value:
            raise RealSecretCollisionError("canary value collided with a known real secret")


@dataclass(frozen=True)
class CanaryMarker:
    """A unique, harmless marker. `value` always carries the unmistakable
    canary prefix -- it is never mistakable for, or equal to, a real
    secret."""

    canary_id: str
    value: str

    def __post_init__(self) -> None:
        _marker_id(self.canary_id, "canary id")
        if not isinstance(self.value, str) or not self.value.startswith(_CANARY_PREFIX):
            raise InvalidCanaryRequestError(
                "canary value must carry the unmistakable canary prefix"
            )


def generate_canary(known_real_secrets: Collection[object] = ()) -> CanaryMarker:
    """Generate a unique, harmless canary marker.

    `known_real_secrets` is an optional disclosure set the caller may pass
    (e.g. configured provider keys) so the generator can refuse outright if
    its random output ever collided with one -- never the other way
    around: this function never *accepts* a caller-supplied value as the
    canary itself.
    """
    token = secrets.token_hex(_TOKEN_NBYTES)
    value = f"{_CANARY_PREFIX}{token}"
    _reject_if_known_real_secret(value, known_real_secrets)
    canary_id = f"canary-{token[:32]}"
    return CanaryMarker(canary_id, value)


def encode_canary(marker: CanaryMarker, encoding: str) -> str:
    """Return the marker's value under one declared, non-fragmented
    encoding. Section 12 requires testing encoded canaries too, since a
    scanner that only substring-matches the literal value would miss a
    base64/hex/url-encoded leak."""
    if not isinstance(marker, CanaryMarker):
        raise InvalidCanaryRequestError("marker must be a CanaryMarker")
    kind = _member(encoding, ENCODINGS, "encoding")
    if kind == "fragmented":
        raise InvalidCanaryRequestError(
            "fragmented is produced by fragment_canary, not encode_canary"
        )
    raw = marker.value.encode("utf-8")
    if kind == "literal":
        return marker.value
    if kind == "base64":
        return base64.b64encode(raw).decode("ascii")
    if kind == "hex":
        return raw.hex()
    if kind == "url":
        return quote(marker.value, safe="")
    raise InvalidCanaryRequestError(f"unsupported encoding {kind!r}")  # pragma: no cover


def fragment_canary(marker: CanaryMarker, parts: int) -> tuple[str, ...]:
    """Split the marker's value into `parts` contiguous, non-empty chunks
    that reassemble to the exact original value -- Section 12's
    "fragmented" canary case, testing whether content split across
    multiple fields/sinks is still caught."""
    if not isinstance(marker, CanaryMarker):
        raise InvalidCanaryRequestError("marker must be a CanaryMarker")
    if isinstance(parts, bool) or not isinstance(parts, int):
        raise InvalidCanaryRequestError("parts must be an int")
    if parts < _MIN_FRAGMENTS or parts > _MAX_FRAGMENTS:
        raise InvalidCanaryRequestError(
            f"parts must be between {_MIN_FRAGMENTS} and {_MAX_FRAGMENTS}"
        )
    # No "parts > len(value)" guard: every CanaryMarker's value starts with
    # _CANARY_PREFIX, which alone is already longer than _MAX_FRAGMENTS, so
    # this can never be true -- a defensive check for an impossible input
    # is not added (and the slice arithmetic below always reassembles
    # exactly, so no post-hoc reassembly check is added either).
    value = marker.value
    size = len(value) // parts
    fragments = [value[i * size : (i + 1) * size] for i in range(parts - 1)]
    fragments.append(value[(parts - 1) * size :])
    return tuple(fragments)


@dataclass(frozen=True)
class PlacementRecord:
    """One authorized placement of a canary at a named sink, under one
    encoding."""

    canary_id: str
    sink: str
    encoding: str
    authorized_by: str

    def __post_init__(self) -> None:
        _marker_id(self.canary_id, "canary id")
        _member(self.sink, SINKS, "sink")
        _member(self.encoding, ENCODINGS, "encoding")
        _text(self.authorized_by, "authorized_by")


class CanaryPlacementLedger:
    """Tracks every authorized placement of every canary, and whether each
    one has actually been checked for recovery in its sink.

    See the module docstring for why `assert_leak_test_complete` fails
    closed on incomplete coverage rather than inferring safety from a
    partial check.
    """

    def __init__(self) -> None:
        self._placements: dict[tuple[str, str, str], PlacementRecord] = {}
        self._checked: set[tuple[str, str, str]] = set()

    def record_placement(self, record: PlacementRecord) -> None:
        if not isinstance(record, PlacementRecord):
            raise InvalidCanaryRequestError("record must be a PlacementRecord")
        key = (record.canary_id, record.sink, record.encoding)
        if key in self._placements:
            raise InvalidCanaryRequestError(
                "this canary/sink/encoding combination was already placed"
            )
        self._placements[key] = record

    def record_checked(
        self, canary_id: object, sink: object, encoding: object, *, found: bool
    ) -> None:
        """Mark one placement checked. `found=True` -- the canary was
        actually recovered from that sink -- raises `CanaryLeakedError`
        immediately; it is never deferred or averaged against sinks that
        came back clean."""
        key = (
            _marker_id(canary_id, "canary id"),
            _member(sink, SINKS, "sink"),
            _member(encoding, ENCODINGS, "encoding"),
        )
        if key not in self._placements:
            raise InvalidCanaryRequestError("cannot check a placement that was never recorded")
        if not isinstance(found, bool):
            raise InvalidCanaryRequestError("found must be a bool")
        self._checked.add(key)
        if found:
            raise CanaryLeakedError(
                f"canary {canary_id!r} was recovered at sink {sink!r} ({encoding})"
            )

    def assert_leak_test_complete(self) -> None:
        """Raise unless every recorded placement has also been checked.
        An empty ledger (nothing was ever placed) is vacuously complete --
        there is nothing it could be asserting safety about."""
        missing = set(self._placements) - self._checked
        if missing:
            raise IncompleteLeakCoverageError(
                f"{len(missing)} of {len(self._placements)} placements were never checked; "
                "absence at one sink is not evidence of safety at the rest"
            )
