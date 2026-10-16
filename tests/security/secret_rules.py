#!/usr/bin/env python3
"""FTF-1505 round 4 -- the dependency-light detection core (Section 84.3 reuse).

Extracted out of `secret_scanning.py` so a single generated CI artifact
(`reports/mutation-gate.json`, `reports/ci-verdict.json`) can be scanned by
the very job that just wrote it, with ZERO third-party dependencies (no
`pyyaml`, no `uvicorn`/`fastapi` transitive chain) -- stdlib only, same
"no setup step" philosophy `.github/workflows/ci.yml`'s own `ci-verdict`
job already uses for `scripts/ci_policy.py`. `secret_scanning.py` imports
every name below and adds history/source/artifact/live-app scanning,
baseline matching and orchestration on top; nothing here is duplicated,
only re-exported (Section 84.3 "reuse ... instead of creating a
duplicate").

Quick-gate completeness (F-DECODE): `_QUICK_GATE_LITERALS` is DERIVED from
the same rule table every rule is matched with, not a second hand-
maintained list -- each shape rule and the quoted-secret keyword pairs
carry their own `quick_gate_hints`/invariant substrings right next to
their pattern, so a new rule with no hint fails loudly (`test_ftf_1505.py`
asserts every rule name in `RULES` has a non-empty hint set) instead of
silently becoming a detection gap. Rule prefixes needing no following
random run (the PEM header) are written as two separate string literals
split at a point the pattern itself requires to be contiguous, so this
module's own source text is never itself a match (same discipline
FTF-0905's `CANARY-DO-NOT-USE` markers use) -- the generated seed helper
in this module applies the same split for every family that needs it.

Round 4 (Control-plane return 2026-10-16, QUEUE.md "0h"), F13-SEAM: round 3
scanned any line longer than 200 chars in overlapping 200-char windows (step
150), so a secret longer than the 50-char overlap that straddled a seam was
never inside any one window and was silently missed (a JWT in a one-line JSON
file passed every lane clean). No finite overlap can cover patterns with an
unbounded `{n,}` tail, so windowing is gone: every line is matched whole. The
one rule whose regex was not linear on a long line -- the JWT candidate, which
restarted at every `-`-separated word boundary of a dotless run -- is now
matched by an exact, linear emulation of the same regex (`_jwt_tokens`;
`test_ftf_1505.py` proves it differentially against the original pattern),
and the per-unit base64/hex candidate caps are gone too: runs found by
`finditer` are disjoint, so decoding every one of them is already linear in
the input, and a cap could only ever be a detection gap.

Run directly against one or more files (e.g. a freshly written CI report):
    python3 tests/security/secret_rules.py reports/mutation-gate.json
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import re
import secrets as _secrets_module
import string
import sys
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote

__all__ = [
    "RULES",
    "SecretScanError",
    "SecretFinding",
    "scan_line",
    "value_sha256",
    "baseline_fingerprint",
    "BaselineEntry",
    "FORBIDDEN_BASELINE_KEYS",
    "generate_seed_for_rule",
    "matches_in_unit",
    "MAX_SCAN_BYTES",
]


class SecretScanError(Exception):
    """Refuses to produce trustworthy evidence, or reports a blocking
    (non-baselined) finding -- never silently converted to a pass
    (Law 17)."""


MAX_SCAN_BYTES = 50 * 1024 * 1024  # per file/blob/member scan bound
_MIN_ENTROPY_BITS_PER_CHAR = 3.5  # FTF-0514's own threshold, reused


@dataclasses.dataclass(frozen=True)
class _ShapeRule:
    pattern: re.Pattern[str]
    quick_gate_hints: tuple[str, ...]  # lowercase substrings; >=1 in any real match


_SHAPE_RULES: dict[str, _ShapeRule] = {
    "aws_access_key_id": _ShapeRule(
        re.compile(r"(?a)\b(?:AKIA|ASIA)[A-Z2-7]{16}\b"), ("akia", "asia")
    ),
    "github_token": _ShapeRule(
        re.compile(r"(?a)\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
        ("ghp_", "gho_", "ghu_", "ghs_", "ghr_"),
    ),
    "github_pat": _ShapeRule(re.compile(r"(?a)\bgithub_pat_[A-Za-z0-9_]{22,}\b"), ("github_pat_",)),
    "gitlab_token": _ShapeRule(re.compile(r"(?a)\bglpat-[A-Za-z0-9_-]{20}\b"), ("glpat-",)),
    "slack_token": _ShapeRule(re.compile(r"(?a)\bxox[abposr]-[A-Za-z0-9-]{10,}\b"), ("xox",)),
    "slack_webhook": _ShapeRule(
        re.compile(r"(?a)\bhttps://hooks\.slack\.com/services/[A-Za-z0-9/+]{20,}\b"),
        ("hooks.slack.com",),
    ),
    "bearer_or_basic_header": _ShapeRule(
        re.compile(r"(?ia)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{20,}\b"),
        ("bearer", "basic"),
    ),
    "pem_private_key": _ShapeRule(
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----"),
        ("-----begin",),
    ),
    "google_api_key": _ShapeRule(re.compile(r"(?a)\bAIza[0-9A-Za-z_-]{35}\b"), ("aiza",)),
    "huggingface_token": _ShapeRule(re.compile(r"(?a)\bhf_[A-Za-z0-9]{34}\b"), ("hf_",)),
    "openai_style_key": _ShapeRule(
        re.compile(r"(?a)\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}\b"), ("sk-",)
    ),
    "stripe_style_key": _ShapeRule(
        re.compile(r"(?a)\b(?:sk|rk)_live_[A-Za-z0-9]{20,}\b"), ("sk_live_", "rk_live_")
    ),
}

_AWS_SECRET_KEY_RULE = "aws_secret_access_key"
_AWS_SECRET_KEY = re.compile(
    r"(?ia)\baws_secret_access_key\b\s*[:=]\s*[\"']?([A-Za-z0-9/+=]{40})[\"']?"
)
_AWS_SECRET_KEY_HINTS = ("aws_secret_access_key",)

# The JWT candidate is defined by this regex; `_jwt_tokens` matches exactly what
# `_JWT_CANDIDATE.finditer` would, but in linear time (see its docstring).
_JWT_CANDIDATE = re.compile(r"(?a)\b([A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b")
# A maximal run of candidate characters (possessive, and only ever started at the
# run's first character, so every position is visited once).
_JWT_DOTTED_RUN = re.compile(r"(?a)(?<![A-Za-z0-9_.\-])[A-Za-z0-9_.\-]++")
_JWT_SEGMENT_MIN = 10
_JWT_RULE = "jwt_shaped"
_JWT_HINTS = ("eyj",)

_URL_USERINFO_RULE = "url_userinfo_password"
_URL_USERINFO = re.compile(r"(?a)\b[a-zA-Z][a-zA-Z0-9+.-]{1,20}://([^\s:/@\"']+):([^\s@\"']+)@")
_URL_PASSWORD_PLACEHOLDER = re.compile(r"^(?:\$\{.*\}|<.*>|\{.*\}|\*+|\[REDACTED\])$")
_URL_USERINFO_HINTS = ("://",)

# F-RULES keyword pairs for quoted_secret_assignment: (first_word, second_word) joined by an
# optional `[_-]?` separator, plus a few bare keywords with no pairing. The quick-gate hint for
# a pair is its FIRST word (shorter, always present regardless of separator style); for a bare
# keyword it is the keyword itself. Co-located with the pattern build so adding a keyword can
# never forget its own quick-gate coverage (F-DECODE).
_QUOTED_SECRET_ASSIGNMENT_RULE = "quoted_secret_assignment"
_SECRET_KEYWORD_PAIRS: tuple[tuple[str, str], ...] = (
    ("db", "password"),
    ("client", "secret"),
    ("secret", "key"),
    ("session", "key"),
    ("private", "key"),
    ("access", "token"),
    ("refresh", "token"),
    ("auth", "token"),
    ("api", "key"),
)
_SECRET_KEYWORD_BARE: tuple[str, ...] = ("passwd", "password", "secret", "token")


def _build_quoted_secret_assignment_pattern() -> re.Pattern[str]:
    alternatives = [f"{a}[_-]?{b}" for a, b in _SECRET_KEYWORD_PAIRS] + list(_SECRET_KEYWORD_BARE)
    return re.compile(
        r"(?ia)(?<![A-Za-z])(?:"
        + "|".join(alternatives)
        + r')\s*[:=]\s*["\']([A-Za-z0-9+/_=.\-]{20,})["\']'
    )


_QUOTED_SECRET_ASSIGNMENT = _build_quoted_secret_assignment_pattern()
_QUOTED_SECRET_ASSIGNMENT_HINTS: tuple[str, ...] = tuple(
    sorted({a for a, _ in _SECRET_KEYWORD_PAIRS} | set(_SECRET_KEYWORD_BARE))
)
_PLACEHOLDER_VALUE = re.compile(
    r"(?i)^(?:changeme|placeholder|example|dummy|test|fake|xxx+|none|null|<.*>|\$\{.*\})$"
)

_CANARY_SHAPE_RULE = "canary_leaked"
_CANARY_SHAPE = re.compile(r"(?a)\bCANARY-DO-NOT-USE-[0-9a-f]{32}\b")
_CANARY_SHAPE_HINTS = ("canary-do-not-use-",)

_BASE64_RUN = re.compile(r"[A-Za-z0-9+/_-]{24,}={0,2}")
_HEX_RUN = re.compile(r"(?a)\b[0-9A-Fa-f]{16,}\b")

RULES: tuple[str, ...] = (
    *sorted(_SHAPE_RULES),
    _AWS_SECRET_KEY_RULE,
    _JWT_RULE,
    _URL_USERINFO_RULE,
    _QUOTED_SECRET_ASSIGNMENT_RULE,
)

# F-DECODE: derived, not hand-maintained -- the union of every rule's own quick_gate_hints.
# `test_quick_gate_literals_cover_every_rule_in_the_table` proves no rule in RULES is missing.
_QUICK_GATE_LITERALS: tuple[str, ...] = tuple(
    sorted(
        {h for rule in _SHAPE_RULES.values() for h in rule.quick_gate_hints}
        | set(_AWS_SECRET_KEY_HINTS)
        | set(_JWT_HINTS)
        | set(_URL_USERINFO_HINTS)
        | set(_QUOTED_SECRET_ASSIGNMENT_HINTS)
        | set(_CANARY_SHAPE_HINTS)
    )
)


def quick_gate_hints_for_rule(rule: str) -> tuple[str, ...]:
    """Every rule's own invariant substrings -- used both to build
    `_QUICK_GATE_LITERALS` and to test completeness per rule."""
    if rule in _SHAPE_RULES:
        return _SHAPE_RULES[rule].quick_gate_hints
    if rule == _AWS_SECRET_KEY_RULE:
        return _AWS_SECRET_KEY_HINTS
    if rule == _JWT_RULE:
        return _JWT_HINTS
    if rule == _URL_USERINFO_RULE:
        return _URL_USERINFO_HINTS
    if rule == _QUOTED_SECRET_ASSIGNMENT_RULE:
        return _QUOTED_SECRET_ASSIGNMENT_HINTS
    if rule == _CANARY_SHAPE_RULE:
        return _CANARY_SHAPE_HINTS
    raise ValueError(f"no quick-gate hints registered for rule {rule!r}")


def _could_match_any_rule(lowered_text: str) -> bool:
    return any(literal in lowered_text for literal in _QUICK_GATE_LITERALS)


def _is_url_password_placeholder(value: str) -> bool:
    return bool(_URL_PASSWORD_PLACEHOLDER.match(value))


def _is_word_char(ch: str) -> bool:
    # Inside a candidate run every character is [A-Za-z0-9_.-]; ASCII `\b` word chars are
    # everything but `.` and `-`.
    return ch not in ".-"


def _jwt_third_segment_end(run: str, start: int, end: int) -> int | None:
    """The end the greedy `[A-Za-z0-9_-]{10,}\\b` takes inside the segment
    run[start:end] (followed by `.` or the end of the run, both non-word): the
    largest e in [start+10, end] where the word/non-word class changes."""
    e = end
    while e >= start + _JWT_SEGMENT_MIN:
        before = _is_word_char(run[e - 1])
        after = _is_word_char(run[e]) if e < len(run) else False
        if before != after:
            return e
        e -= 1
    return None


def _jwt_tokens_in_run(run: str) -> Iterator[str]:
    """`_JWT_CANDIDATE.finditer(run)`, emulated segment by segment. A match
    starts at a word boundary (a word char after `-`, `.` or the run start),
    takes the rest of its own segment and the whole next segment (the
    character class excludes `.`, so neither can backtrack), then greedily
    the third segment back to its last word boundary. Every start inside one
    segment shares the same next two segments, so a segment either yields its
    first start with >=10 chars left or none at all -- no per-start rescans."""
    segments = run.split(".")
    offsets: list[int] = []
    offset = 0
    for segment in segments:
        offsets.append(offset)
        offset += len(segment) + 1
    pos = 0
    i = 0
    while i + 2 < len(segments):
        if len(segments[i + 1]) < _JWT_SEGMENT_MIN or len(segments[i + 2]) < _JWT_SEGMENT_MIN:
            i += 1
            continue
        third_start = offsets[i + 2]
        third_end = _jwt_third_segment_end(run, third_start, third_start + len(segments[i + 2]))
        if third_end is None:
            i += 1
            continue
        seg_start = offsets[i]
        seg_end = seg_start + len(segments[i])
        start = None
        p = max(pos, seg_start)
        while p <= seg_end - _JWT_SEGMENT_MIN:
            if _is_word_char(run[p]) and (p == seg_start or run[p - 1] == "-"):
                start = p
                break
            p += 1
        if start is None:
            i += 1
            continue
        yield run[start:third_end]
        pos = third_end
        i += 2


def _jwt_tokens(text: str) -> Iterator[str]:
    """Every token `_JWT_CANDIDATE.finditer(text)` yields, in linear time.
    The regex itself is quadratic on a long dotless `a-a-a-...` run (it
    restarts at every word boundary and rescans to the run's end), which is
    why round 3 had to window long lines; candidates can never cross a run of
    `[A-Za-z0-9_.-]`, so each run is matched on its own."""
    for match in _JWT_DOTTED_RUN.finditer(text):
        run = match.group(0)
        if run.count(".") >= 2:
            yield from _jwt_tokens_in_run(run)


def _jwt_matches(text: str) -> Iterator[tuple[str, str]]:
    for token in _jwt_tokens(text):
        header_b64 = token.split(".", 1)[0]
        padded = header_b64 + "=" * ((-len(header_b64)) % 4)
        try:
            decoded = base64.urlsafe_b64decode(padded)
            obj = json.loads(decoded)
        except Exception:
            continue
        if isinstance(obj, dict) and "alg" in obj:
            yield _JWT_RULE, token


def _iter_shape_matches(text: str) -> Iterator[tuple[str, str]]:
    """Every rule except the FTF-0905 canary shape (log/artifact lanes
    only -- see `_matches`)."""
    for name, rule in _SHAPE_RULES.items():
        for match in rule.pattern.finditer(text):
            yield name, match.group(0)
    for match in _AWS_SECRET_KEY.finditer(text):
        yield _AWS_SECRET_KEY_RULE, match.group(1)
    for match in _URL_USERINFO.finditer(text):
        password = match.group(2)
        if not _is_url_password_placeholder(password):
            yield _URL_USERINFO_RULE, password
    for match in _QUOTED_SECRET_ASSIGNMENT.finditer(text):
        value = match.group(1)
        if _PLACEHOLDER_VALUE.match(value):
            continue
        if _shannon_entropy_bits_per_char(value) >= _MIN_ENTROPY_BITS_PER_CHAR:
            yield _QUOTED_SECRET_ASSIGNMENT_RULE, value
    yield from _jwt_matches(text)


def _shannon_entropy_bits_per_char(value: str) -> float:
    """FTF-0514's own threshold (`workers/data/pii_secrets_detector.
    _shannon_entropy_bits_per_char`). Reimplemented identically here (not
    imported) so this module stays stdlib-only and importable with no
    workspace package installed -- the one deliberate duplication in this
    file, proven byte-for-byte identical by
    `test_entropy_matches_pii_secrets_detector_reference` in
    `tests/tasks/test_ftf_1505.py`, which imports the real FTF-0514
    function and compares outputs directly over a generated corpus."""
    if not value:
        return 0.0
    import collections
    import math

    counts = collections.Counter(value)
    length = len(value)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def _try_base64_decode(candidate: str) -> str | None:
    body = candidate.rstrip("=")
    padded = body + "=" * ((-len(body)) % 4)
    raw: bytes | None = None
    try:
        raw = base64.b64decode(padded, validate=False)
    except Exception:
        try:
            raw = base64.urlsafe_b64decode(padded)
        except Exception:
            raw = None
    if raw is None or len(raw) < 8:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _try_hex_decode(candidate: str) -> str | None:
    if len(candidate) % 2:
        candidate = candidate[:-1]
    try:
        raw = bytes.fromhex(candidate)
    except ValueError:
        return None
    if len(raw) < 8:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _canary_matches(text: str) -> Iterator[tuple[str, str]]:
    for match in _CANARY_SHAPE.finditer(text):
        yield _CANARY_SHAPE_RULE, match.group(0)


def _matches(text: str, *, include_canary: bool = False) -> Iterator[tuple[str, str]]:
    """Yields (rule_name, matched_value) for every rule firing on `text`,
    including one layer of base64 decoding (runs >=24 chars), one layer
    of hex decoding (runs >=16 hex digits) and one layer of percent-
    decoding -- each layer is also checked for the FTF-0905 canary shape
    when `include_canary` (a canary encoded one layer deep must be just
    as detectable as a raw one). Every candidate run is decoded: the runs
    are disjoint, so this is linear in `text` and needs no cap."""
    yield from _iter_shape_matches(text)
    if include_canary:
        yield from _canary_matches(text)
    for match in _BASE64_RUN.finditer(text):
        decoded = _try_base64_decode(match.group(0))
        if decoded is None:
            continue
        if include_canary:
            yield from _canary_matches(decoded)
        if _could_match_any_rule(decoded.lower()):
            yield from _iter_shape_matches(decoded)
    for match in _HEX_RUN.finditer(text):
        decoded = _try_hex_decode(match.group(0))
        if decoded is None:
            continue
        if include_canary:
            yield from _canary_matches(decoded)
        if _could_match_any_rule(decoded.lower()):
            yield from _iter_shape_matches(decoded)
    percent_decoded = unquote(text)
    if percent_decoded != text:
        if include_canary:
            yield from _canary_matches(percent_decoded)
        if _could_match_any_rule(percent_decoded.lower()):
            yield from _iter_shape_matches(percent_decoded)


def matches_in_unit(text: str, *, include_canary: bool) -> Iterator[tuple[str, str]]:
    """Every (rule, value) in one line (or one whole non-UTF-8 unit), each
    distinct pair once. The whole text is matched in one pass, however long:
    no window, so no seam a long secret can straddle (F13-SEAM)."""
    seen: set[tuple[str, str]] = set()
    for item in _matches(text, include_canary=include_canary):
        if item not in seen:
            seen.add(item)
            yield item


def scan_line(line: str, *, include_canary: bool = False) -> list[tuple[str, str]]:
    """Pure, public entry point so tests can exercise the exact rule set
    with no filesystem/git access. Handles arbitrarily long single lines
    with no truncation and no window seams."""
    return list(matches_in_unit(line, include_canary=include_canary))


def value_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="surrogateescape")).hexdigest()


def baseline_fingerprint(rule: str, value: str) -> str:
    return hashlib.sha256(
        (rule + "\0" + value).encode("utf-8", errors="surrogateescape")
    ).hexdigest()


@dataclasses.dataclass(frozen=True)
class SecretFinding:
    """One matched secret shape. Content-free by construction (Section
    47 "no secrets ... in task reports"): callers get the matched value
    only through `value_sha256`, and even that is omitted entirely for a
    NEW (unbaselined/blocking) finding -- an unsalted sha256 of a weak,
    low-entropy secret (a short DSN password, say) is reversible by
    dictionary in seconds, so printing it for a finding nobody has
    reviewed yet would itself leak. A baselined (already-reviewed,
    non-blocking) finding keeps its hash: the value behind it is already
    known to Control plane."""

    rule: str
    target: str  # "source" | "history" | "artifact" | "log_sample"
    location: str
    value_sha256: str
    blocking: bool

    def to_dict(self) -> dict[str, object]:
        data = dataclasses.asdict(self)
        if self.blocking:
            data["value_sha256"] = None
        return data


@dataclasses.dataclass(frozen=True)
class BaselineEntry:
    fingerprint: str
    reason: str
    reviewed_by: str
    reviewed_on: str


FORBIDDEN_BASELINE_KEYS = ("value", "path", "file_glob", "glob")


# ---------------------------------------------------------------------------
# Runtime seed generation (F-SEEDS): one obviously-fake value per rule
# family, built fresh every call -- nothing here is a fixed literal that
# could itself appear, contiguously, in this module's own source text (the
# PEM header is the one rule whose full match needs no trailing random
# run, so it alone is written as two separate string literals, split at
# the exact point the pattern requires contiguity -- same discipline
# FTF-0905's `CANARY-DO-NOT-USE` markers already use).
# ---------------------------------------------------------------------------

_AWS_ID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"  # real AKIA/ASIA id charset


def _rand(alphabet: str, length: int) -> str:
    return "".join(_secrets_module.choice(alphabet) for _ in range(length))


def _alnum(length: int) -> str:
    return _rand(string.ascii_letters + string.digits, length)


def generate_seed_for_rule(rule: str) -> str:
    """A runtime-generated, harmless marker whose SHAPE fires this rule's
    own pattern -- never a real secret, never a fixed literal."""
    if rule == "aws_access_key_id":
        return "AKIA" + _rand(_AWS_ID_ALPHABET, 16)
    if rule == "github_token":
        return "ghp" + "_" + _alnum(40)
    if rule == "github_pat":
        return "github_pat" + "_" + _alnum(30)
    if rule == "gitlab_token":
        return "glpat" + "-" + _alnum(20)
    if rule == "slack_token":
        return "xoxb" + "-" + _alnum(20)
    if rule == "slack_webhook":
        return (
            "https://hooks.slack.com/services/" + _alnum(11) + "/" + _alnum(11) + "/" + _alnum(24)
        )
    if rule == "bearer_or_basic_header":
        return "Bearer" + " " + _alnum(24)
    if rule == "pem_private_key":
        return "-----BEGIN " + "PRIVATE KEY-----"
    if rule == "google_api_key":
        return "AIza" + _alnum(35)
    if rule == "huggingface_token":
        return "hf" + "_" + _alnum(34)
    if rule == "openai_style_key":
        return "sk" + "-" + _alnum(32)
    if rule == "stripe_style_key":
        return "sk" + "_live_" + _alnum(20)
    if rule == _AWS_SECRET_KEY_RULE:
        return "aws_secret_access_key" + ": " + '"' + _rand(string.ascii_letters + "/+", 40) + '"'
    if rule == _JWT_RULE:
        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
            .rstrip(b"=")
            .decode()
        )
        payload = (
            base64.urlsafe_b64encode(json.dumps({"sub": _secrets_module.token_hex(8)}).encode())
            .rstrip(b"=")
            .decode()
        )
        return f"{header}.{payload}.{_alnum(20)}"
    if rule == _URL_USERINFO_RULE:
        return "https" + "://" + "user" + ":" + _alnum(16) + "@" + "example.internal/db"
    if rule == _QUOTED_SECRET_ASSIGNMENT_RULE:
        return "api" + "_key" + ": " + '"' + _alnum(24) + '"'
    if rule == _CANARY_SHAPE_RULE:
        return "CANARY-DO-NOT-USE-" + _secrets_module.token_hex(16)
    raise ValueError(f"no seed template for rule {rule!r}")


# ---------------------------------------------------------------------------
# Minimal, dependency-free CLI: scan one or more files for any shape-rule
# hit, no baseline (a freshly generated CI report file has no reviewed
# fixture history -- any hit in one is unconditionally blocking). F-
# ARTIFACTS: used by the `mutation-gate` and `ci-verdict` jobs to scan
# their own `reports/*.json` the moment each is written, before upload.
# ---------------------------------------------------------------------------


def _scan_file_no_baseline(path: Path) -> list[tuple[str, str, str]]:
    with path.open("rb") as handle:
        data = handle.read(MAX_SCAN_BYTES + 1)
    truncated = len(data) > MAX_SCAN_BYTES
    data = data[:MAX_SCAN_BYTES]
    findings: list[tuple[str, str, str]] = []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
        for rule, value in matches_in_unit(text, include_canary=True):
            findings.append((rule, value, ""))
        if truncated:
            findings.append(("scan_incomplete", "", f"{path}: content exceeds scan bound"))
        return findings
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule, value in matches_in_unit(line, include_canary=True):
            findings.append((rule, value, f":{lineno}"))
    if truncated:
        findings.append(("scan_incomplete", "", f"{path}: content exceeds scan bound"))
    return findings


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: secret_rules.py <file> [<file> ...]", file=sys.stderr)
        return 2
    total_findings = 0
    for arg in argv:
        path = Path(arg)
        if not path.is_file():
            print(f"secret_rules: {path} is not a file -- fail closed", file=sys.stderr)
            return 1
        findings = _scan_file_no_baseline(path)
        for rule, _value, loc_suffix in findings:
            total_findings += 1
            print(f"  [{path}{loc_suffix}] {rule}", file=sys.stderr)
    if total_findings:
        print(f"secret_rules: {total_findings} finding(s), cannot pass", file=sys.stderr)
        return 1
    print(f"secret_rules: {len(argv)} file(s) scanned, 0 findings -- clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
