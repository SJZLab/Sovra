"""PII/secrets detector (FTF-0514, Section 8.1 step 14/67.4/77/81).

Section 8.1's import pipeline step 14 ("Run PII/secrets policy") needs one
real, load-bearing place where a batch of already-normalized candidate
records (FTF-0506's own canonical `content` body shape,
`{"messages": [...], "metadata": {...}}`, the same shape FTF-0508's own
`validate_record_batch` already validates on every Section 6.11
`DatasetRecord` candidate) is scanned for personally identifiable
information and secret-shaped values before any row is allowed into split
generation -- this module is that place.

This task's own `database_changes` clause ("No independent schema change.
Consume frozen contract.") fixes this module's shape, the same as
FTF-0506/FTF-0507/FTF-0508/FTF-0511/FTF-0512: a pure, synchronous, no-I/O,
no-DB function.

This task's own acceptance clause ("Run secrets/PII detectors inside
private boundary and quarantine required findings with human resolution")
is met in two parts:

  - **run secrets/PII detectors inside private boundary**: `detect_pii_and_secrets`
    is a pure, synchronous, no-I/O, no-network function -- there is no
    outbound call of any kind, so scanning never leaves the private
    processing boundary (Section 77.6's own "no browser-held ... arbitrary
    outbound proxy" discipline, applied here to a batch worker instead of a
    route).
  - **quarantine required findings with human resolution**: this module
    reports every finding with `requires_quarantine=True` and a `category`,
    but never itself flips a record's `pii_status` to FTF-0508's own
    `"quarantined"` value -- only `"clean"` (`pii_status_for` returns
    `"clean"` for a batch position with no findings) or `"detected"`
    (`pii_status_for` returns `"detected"` for a batch position with at
    least one finding). `"quarantined"` is a later, human-resolved state a
    reviewer moves a `"detected"` record into (Section 67.4's own
    "suspicious data is quarantined for reviewer resolution") -- this
    module cannot manufacture that human action, the same disclosed "no
    live caller yet" scope every sibling detector already carries (see
    below). `PII_STATUS_VALUES`, imported directly rather than
    re-declared, is FTF-0508's own already-CERTIFIED vocabulary
    (`unscanned`/`clean`/`detected`/`quarantined`) -- reuse-first, Section
    77.1 -- and this module only ever returns the two values a pure
    detector is actually entitled to assert.

This task's own negative_acceptance ("Detection absence is not proof of no
PII; false negatives/residual risks recorded and real secrets not logged")
is met by two real, independently testable design choices:

  - **detection absence is not proof of no PII; false negatives/residual
    risks recorded**: `RESIDUAL_RISK_DISCLOSURE` is a real, non-empty,
    machine-readable field on every `PiiSecretsReport` (not just a comment
    a caller could ignore), stating plainly that a clean report is a
    negative detection result, not a certification of absence. This is a
    genuine limitation, not decorative text: the detectors below are a
    closed, named pattern/entropy set (email, phone number, credit card
    with Luhn check, US SSN, AWS access-key ID, bearer token, PEM private
    key block, generic high-entropy secret) -- a PII-shaped value that
    matches none of them (for example a short, low-entropy password, or a
    personal name with no other structure) is never detected, proven by
    `test_low_entropy_secret_like_string_is_a_genuine_false_negative` and
    `test_bare_personal_name_is_a_genuine_false_negative` rather than
    merely asserted.
  - **real secrets not logged**: `PiiSecretsFinding` never carries the
    matched substring, a hash of it, or any partial/prefix/suffix
    characters from it -- only `category`, `match_length` (an integer, safe
    evidence for a reviewer to gauge match size) and a structural location
    (`batch_position`/`record_index`/`message_index`/`content_path`). This
    mirrors this repository's own house redaction convention
    (`apps/api/app/config/configuration_system.py`'s `_redact`:
    `"<redacted:{len} chars>"`, length-only, never partial content) rather
    than a masked preview that could still leak characters of a short
    secret. `test_finding_never_carries_raw_or_partial_secret_value`
    proves no matched raw text from any planted secret appears anywhere in
    a produced report's own field values.

Every `text` content part, every `json` content part's own string leaves
(recursively -- Section 67.4's "secrets detected under zero-egress policy"
is read to cover a tool result's structured payload, not only prose), and
every tool call's `arguments` string leaves are scanned uniformly; no
role or content type is exempt (Section 8.1 step 14 names no carve-out).
This is a deliberately broader scan surface than FTF-0512's own
`_normalize_content_part`, which treats `json` content and tool-call
`arguments` as opaque business data for *hashing* -- a different task with
a different purpose. A tool argument or a JSON tool result can carry a
real customer email or an API key exactly as easily as prose can.

Never raises on a per-row violation -- `detect_pii_and_secrets` reports a
malformed `content`, a non-object candidate, or an unscannable content
part as a `PiiSecretsIssue` with row evidence, the same "never raise on a
per-row violation, always report" discipline FTF-0508's/FTF-0511's/
FTF-0512's own reports already established. Only a batch that is not a
real sequence of records at all (not a sequence, or empty) raises
`PiiSecretsDetectionError` outright -- there is no row to attach that
failure's evidence to.

Scope decision, same disclosed precedent as FTF-0506/FTF-0507/FTF-0508/
FTF-0511/FTF-0512 ("no live caller yet"): this task's own packet `files`
list names only this module and its test, not an importer/route. No
dataset-import pipeline route exists yet in this repository to call
`detect_pii_and_secrets` (Section 67.1's actual import pipeline routes are
separate, later, dependency-gated tasks). `proving_symbol` stays blank;
`product_truth_check.py`'s product-true count is deliberately unchanged.

Path refinement recorded before READY (Section 84.3), same precedent as
FTF-0506/FTF-0507/FTF-0508/FTF-0511/FTF-0512: the packet names
`services/training-worker/data/pii_secrets_detector.py`, but this repo's
real `SOURCE_ROOTS` (FTF-0001, PR #10) name `workers/data` as the real
data/review worker root -- there is no `services/` tree in this
repository. Placed alongside FTF-0506's/FTF-0507's/FTF-0508's/FTF-0511's/
FTF-0512's own modules instead of creating a second, non-existent tree.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from workers.data.record_schema_validator import PII_STATUS_VALUES

__all__ = [
    "PII_SECRET_CATEGORIES",
    "RESIDUAL_RISK_DISCLOSURE",
    "PiiSecretsDetectionError",
    "PiiSecretsFinding",
    "PiiSecretsIssue",
    "PiiSecretsReport",
    "detect_pii_and_secrets",
]

RESIDUAL_RISK_DISCLOSURE = (
    "This scan checks a closed set of named patterns and a generic high-entropy "
    "heuristic. A record with zero findings is a negative detection result, not "
    "a certification that it carries no personal or secret data -- unstructured "
    "PII (a bare name, a low-entropy password, a description of a person) is a "
    "known, disclosed false-negative class this module does not claim to catch."
)

PII_SECRET_CATEGORIES = frozenset(
    {
        "email",
        "phone_number",
        "credit_card",
        "us_ssn",
        "aws_access_key_id",
        "bearer_token",
        "private_key_block",
        "high_entropy_secret",
    }
)

_ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})
_ALLOWED_CONTENT_TYPES = frozenset({"text", "json"})
_REQUIRED_CONTENT_KEYS = frozenset({"messages", "metadata"})

_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_PATTERN = re.compile(r"(?<!\d)(\+?\d[\d\-. ()]{8,}\d)(?!\d)")
_CREDIT_CARD_CANDIDATE_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_US_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_AWS_ACCESS_KEY_PATTERN = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_BEARER_TOKEN_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9\-_.=]{20,}\b")
_PRIVATE_KEY_BLOCK_PATTERN = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----")
# A high-entropy run of 20+ token characters that none of the named
# patterns above already claimed -- Section 10's own "schema-aware rules"
# discipline (normalized_duplicate_detector.py's UUID/timestamp redaction)
# applied here to unnamed secret-shaped tokens (a generic API key) instead.
_HIGH_ENTROPY_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_\-/+=]{20,}")
_MIN_HIGH_ENTROPY_BITS_PER_CHAR = 3.5


class PiiSecretsDetectionError(ValueError):
    """The candidate record batch itself is not a real sequence of
    records -- not a sequence at all, or empty. There is no row to attach
    this failure's evidence to, so it is raised, not reported as a row
    issue."""


@dataclass(frozen=True)
class PiiSecretsIssue:
    batch_position: int
    record_index: int | None
    code: str
    message: str


@dataclass(frozen=True)
class PiiSecretsFinding:
    batch_position: int
    record_index: int | None
    message_index: int
    content_path: str
    category: str
    match_length: int
    requires_quarantine: bool


@dataclass(frozen=True)
class PiiSecretsReport:
    findings: tuple[PiiSecretsFinding, ...]
    issues: tuple[PiiSecretsIssue, ...]
    residual_risk_disclosure: str = RESIDUAL_RISK_DISCLOSURE

    def pii_status_for(self, batch_position: int) -> str:
        """Returns FTF-0508's own `PII_STATUS_VALUES` vocabulary value this
        pure detector is entitled to assert for one batch position:
        `"detected"` if any finding names that position, else `"clean"`.
        Never returns `"unscanned"` (a batch position this call inspected
        was, by definition, scanned) or `"quarantined"` (a human-resolved
        state this module cannot manufacture -- see this module's own
        acceptance-clause discussion above)."""
        for finding in self.findings:
            if finding.batch_position == batch_position:
                return "detected"
        return "clean"

    @property
    def is_clean(self) -> bool:
        return not self.issues and not self.findings


def _shannon_entropy_bits_per_char(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    length = len(text)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _luhn_checksum_valid(digits: str) -> bool:
    total = 0
    reversed_digits = digits[::-1]
    for index, char in enumerate(reversed_digits):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _find_credit_cards(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _CREDIT_CARD_CANDIDATE_PATTERN.finditer(text):
        digits = re.sub(r"[ -]", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_checksum_valid(digits):
            spans.append(match.span())
    return spans


def _find_high_entropy_secrets(
    text: str, *, already_claimed: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _HIGH_ENTROPY_TOKEN_PATTERN.finditer(text):
        span = match.span()
        if any(start <= span[0] < end or start < span[1] <= end for start, end in already_claimed):
            continue
        if _shannon_entropy_bits_per_char(match.group(0)) >= _MIN_HIGH_ENTROPY_BITS_PER_CHAR:
            spans.append(span)
    return spans


def _scan_text(text: str) -> list[tuple[str, int]]:
    """Scans one text leaf; returns `(category, match_length)` pairs. Never
    returns the matched substring itself -- see this module's own
    negative_acceptance discussion above."""
    found: list[tuple[str, int]] = []
    claimed_spans: list[tuple[int, int]] = []

    for pattern, category in (
        (_PRIVATE_KEY_BLOCK_PATTERN, "private_key_block"),
        (_AWS_ACCESS_KEY_PATTERN, "aws_access_key_id"),
        (_BEARER_TOKEN_PATTERN, "bearer_token"),
        (_EMAIL_PATTERN, "email"),
        (_US_SSN_PATTERN, "us_ssn"),
    ):
        for match in pattern.finditer(text):
            found.append((category, len(match.group(0))))
            claimed_spans.append(match.span())

    for start, end in _find_credit_cards(text):
        found.append(("credit_card", end - start))
        claimed_spans.append((start, end))

    for match in _PHONE_PATTERN.finditer(text):
        span = match.span()
        if any(start <= span[0] < end or start < span[1] <= end for start, end in claimed_spans):
            continue
        digits_only = re.sub(r"\D", "", match.group(0))
        if 9 <= len(digits_only) <= 15:
            found.append(("phone_number", len(match.group(0))))
            claimed_spans.append(span)

    for start, end in _find_high_entropy_secrets(text, already_claimed=claimed_spans):
        found.append(("high_entropy_secret", end - start))

    return found


def _iter_string_leaves(value: object, *, path: str) -> list[tuple[str, str]]:
    """Recursively walks a JSON-shaped value, returning `(path, string)`
    leaves. Used both for a `json` content part's own `value` and for a
    tool call's `arguments` -- Section 67.4's "secrets detected" is read to
    cover structured payloads, not only prose (see this module's own
    docstring discussion above)."""
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, Mapping):
        leaves: list[tuple[str, str]] = []
        for key in sorted(value, key=str):
            leaves.extend(_iter_string_leaves(value[key], path=f"{path}.{key}"))
        return leaves
    if isinstance(value, list):
        leaves = []
        for index, item in enumerate(value):
            leaves.extend(_iter_string_leaves(item, path=f"{path}[{index}]"))
        return leaves
    return []


def _scan_message(
    message: object,
    *,
    batch_position: int,
    record_index: int | None,
    message_index: int,
) -> tuple[list[PiiSecretsFinding], list[PiiSecretsIssue]]:
    findings: list[PiiSecretsFinding] = []
    issues: list[PiiSecretsIssue] = []

    if not isinstance(message, Mapping):
        issues.append(
            PiiSecretsIssue(
                batch_position=batch_position,
                record_index=record_index,
                code="invalid_message",
                message=f"messages[{message_index}]: message must be an object",
            )
        )
        return findings, issues

    role = message.get("role")
    if role not in _ALLOWED_ROLES:
        issues.append(
            PiiSecretsIssue(
                batch_position=batch_position,
                record_index=record_index,
                code="invalid_message",
                message=f"messages[{message_index}]: unmapped role {role!r}",
            )
        )
        return findings, issues

    leaves: list[tuple[str, str]] = []

    raw_content = message.get("content")
    if not isinstance(raw_content, list):
        issues.append(
            PiiSecretsIssue(
                batch_position=batch_position,
                record_index=record_index,
                code="invalid_message",
                message=f"messages[{message_index}]: content must be a list",
            )
        )
        return findings, issues

    for part_index, part in enumerate(raw_content):
        part_path = f"content[{part_index}]"
        if not isinstance(part, Mapping):
            issues.append(
                PiiSecretsIssue(
                    batch_position=batch_position,
                    record_index=record_index,
                    code="invalid_content_part",
                    message=(
                        f"messages[{message_index}].{part_path}: content part must be an object"
                    ),
                )
            )
            continue
        part_type = part.get("type")
        if part_type not in _ALLOWED_CONTENT_TYPES:
            issues.append(
                PiiSecretsIssue(
                    batch_position=batch_position,
                    record_index=record_index,
                    code="invalid_content_part",
                    message=(
                        f"messages[{message_index}].{part_path}: unsupported content "
                        f"type {part_type!r}"
                    ),
                )
            )
            continue
        if part_type == "text":
            text = part.get("text")
            if isinstance(text, str):
                leaves.append((f"{part_path}.text", text))
        else:
            leaves.extend(_iter_string_leaves(part.get("value"), path=f"{part_path}.value"))

    raw_tool_calls = message.get("tool_calls")
    if isinstance(raw_tool_calls, list):
        for call_index, call in enumerate(raw_tool_calls):
            if isinstance(call, Mapping):
                leaves.extend(
                    _iter_string_leaves(
                        call.get("arguments"), path=f"tool_calls[{call_index}].arguments"
                    )
                )

    for content_path, text in leaves:
        for category, match_length in _scan_text(text):
            findings.append(
                PiiSecretsFinding(
                    batch_position=batch_position,
                    record_index=record_index,
                    message_index=message_index,
                    content_path=content_path,
                    category=category,
                    match_length=match_length,
                    requires_quarantine=True,
                )
            )

    return findings, issues


def detect_pii_and_secrets(records: Sequence[Mapping[str, object]]) -> PiiSecretsReport:
    """Scans an already-normalized candidate batch for PII/secret-shaped
    values (Section 8.1 step 14). Every candidate must carry `content`
    (FTF-0506's own canonical body shape). Never raises on a per-row
    violation -- returns a `PiiSecretsReport` whose `issues` carry row
    evidence. Raises `PiiSecretsDetectionError` only when the batch itself
    is not a real sequence of records (not a sequence, or empty)."""
    if not isinstance(records, Sequence) or isinstance(records, str | bytes):
        raise PiiSecretsDetectionError("records must be a sequence of candidate records")
    if not records:
        raise PiiSecretsDetectionError("records must be a non-empty sequence")

    findings: list[PiiSecretsFinding] = []
    issues: list[PiiSecretsIssue] = []

    for batch_position, candidate in enumerate(records):
        if not isinstance(candidate, Mapping):
            issues.append(
                PiiSecretsIssue(
                    batch_position=batch_position,
                    record_index=None,
                    code="invalid_candidate",
                    message="candidate record must be an object",
                )
            )
            continue

        raw_record_index = candidate.get("record_index")
        record_index: int | None
        if isinstance(raw_record_index, int) and not isinstance(raw_record_index, bool):
            record_index = raw_record_index
        else:
            record_index = None

        content = candidate.get("content")
        if not isinstance(content, Mapping) or set(content) != _REQUIRED_CONTENT_KEYS:
            issues.append(
                PiiSecretsIssue(
                    batch_position=batch_position,
                    record_index=record_index,
                    code="unknown_content",
                    message="content must be an object with exactly the canonical "
                    "'messages'/'metadata' keys",
                )
            )
            continue

        messages = content.get("messages")
        if not isinstance(messages, list) or not messages:
            issues.append(
                PiiSecretsIssue(
                    batch_position=batch_position,
                    record_index=record_index,
                    code="unknown_content",
                    message="content.messages must be a non-empty list",
                )
            )
            continue

        for message_index, message in enumerate(messages):
            message_findings, message_issues = _scan_message(
                message,
                batch_position=batch_position,
                record_index=record_index,
                message_index=message_index,
            )
            findings.extend(message_findings)
            issues.extend(message_issues)

    return PiiSecretsReport(findings=tuple(findings), issues=tuple(issues))


assert PII_STATUS_VALUES >= frozenset({"clean", "detected"}), (
    "this module's own pii_status_for() return values must remain a subset of "
    "FTF-0508's own PII_STATUS_VALUES vocabulary"
)
