#!/usr/bin/env python3
"""FTF-1505 -- Secret scanning, round 5 (unit/fault tests).

Round 4 (Control-plane return 2026-10-16, QUEUE.md "0h") replaces every
source-text check with a behavioural one and adds:

  - F-FAULTS-BEHAVIOURAL: `main()` is run for real on disposable repositories
    (`--root`/`--baseline`) to prove each lane's result and each completeness
    check decides the run -- a history-only, artifact-only or source-only
    secret, an uncovered upload path, an untracked production directory, a
    tracked file that was never scanned, and a blocking finding whose own
    scenario passed each make `main()` raise; a clean repository passes.
  - F-LIVE-POSITIVE-CONTROL: the live lane is run for real and must see
    uvicorn's stderr startup line, one access-log line per probe and a
    path-placed canary, and reaches the 422/400 error paths.
  - F-ARCHIVE-ZLIB / CORRUPT / OVERSIZE: a corrupt deflated member, a
    truncated zip/tar.gz/gz and an oversized member are blocking
    `scan_incomplete`, never silent.
  - F13-SEAM: no line windows; long secrets are found at every offset of a
    long line, and the linear JWT matcher is proven equal to its regex.
  - F-SEEDS-PER-LANE: `scan_seeded_recall` (called by `main()`) plants every
    family in every file lane.

Round 3 notes follow.

Runnable without network egress. Several checks use a disposable temporary
git repository (real `git init`/`commit`, no network) to prove the real
negative_acceptance clauses this round's return items named. Round 3
(Control-plane return 2026-10-15T14:14Z, QUEUE.md "0h") adds:

  - F-FAULTS: a fault-matrix section below, one RED row per named fault
    (F2b/F4/F6/F10/F13/F14/F15/F16/F17/F18/F19/F20/F21-F23/F27), each
    proven by deliberately reintroducing the fault (via monkeypatching the
    real, already-imported module in place -- `secret_scanning.py` computes
    several paths relative to its own `__file__`, which makes a literal
    file-copy-and-reimport unreliable; monkeypatch-with-restore proves the
    identical behavioural claim without that fragility) and showing the
    matching check goes RED, then restoring and showing it is GREEN again.
  - F-SEEDS: every seed value anywhere in this task's own two files is
    generated at runtime (`secret_rules.generate_seed_for_rule`) -- nothing
    here is a fixed literal needing a baseline entry.
  - F-LOGS: `scan_log_samples`/`scan_live_app_log_samples` now cover every
    rule family (round 2 covered 5), plus a direct `StructuredEvent`/
    `emit_to_stream` cross-check and a proof that a base64/hex-ENCODED
    canary is now actually detectable (round 2's own decode layer never
    re-checked the canary shape on decoded content).
  - F-DECODE: the quick gate is DERIVED from the rule table
    (`secret_rules.quick_gate_hints_for_rule`), proven complete per rule,
    and a base64/hex decode-cap overrun is itself a blocking
    `scan_incomplete` finding, never a silent skip.
  - F-SCOPE/F-BINARY: a non-ASCII tracked filename, oversized content and a
    corrupt archive member (with later members still read) are all fail-
    closed, never silently omitted; `assert_scanned_set_matches_tracked_
    files` proves the scanned set equals `git ls-files` exactly.
  - F-ARTIFACTS: `parse_upload_artifact_paths` is a structural YAML parse
    (covers `actions/upload-artifact/merge@...` and refuses a `..`
    traversal), not a fixed-window regex.
  - F-HISTORY: `scope="pr"` (HEAD only) vs `scope="all-refs"` (every ref,
    with branch attribution) -- the 2026-10-15T08:43Z ruling.
  - Redaction: a NEW (blocking) finding's `to_dict()` omits `value_sha256`
    entirely (an unsalted hash of a low-entropy secret is dictionary-
    reversible); a baselined (non-blocking) finding keeps it.

Run directly: python3 tests/tasks/test_ftf_1505.py
"""

from __future__ import annotations

import atexit
import base64
import contextlib
import io
import logging
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "security"))

import secret_rules as sr  # noqa: E402
import secret_scanning as ss  # noqa: E402
from secret_rules import (  # noqa: E402
    RULES,
    SecretFinding,
    _could_match_any_rule,
    baseline_fingerprint,
    generate_seed_for_rule,
    quick_gate_hints_for_rule,
)
from secret_scanning import (  # noqa: E402
    ARTIFACT_ROOTS,
    SOURCE_ROOTS,
    BaselineEntry,
    SecretScanError,
    assert_artifact_uploads_covered,
    assert_baseline_fresh,
    assert_dirs_covered,
    assert_not_shallow,
    assert_scanned_set_matches_tracked_files,
    evaluate,
    generate_seed_values,
    is_baselined,
    iter_history_blob_ids,
    iter_tracked_files,
    iter_tracked_files_under,
    load_baseline,
    parse_upload_artifact_paths,
    scan_history,
    scan_line,
    scan_log_samples,
    scan_source,
    scan_structured_event_cross_check,
)
from tenant_isolation_suite import ScenarioResult  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _run_git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "HOME": str(cwd),
            "PATH": "/usr/bin:/bin",
        },
    ).stdout


_TEMP_DIRS: list[Path] = []


@atexit.register
def _remove_temp_dirs() -> None:
    for path in _TEMP_DIRS:
        shutil.rmtree(path, ignore_errors=True)


def _mkdtemp(prefix: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix=prefix))
    _TEMP_DIRS.append(path)
    return path


def _build_temp_repo() -> Path:
    tmp = _mkdtemp("ftf1505_")
    _run_git("init", "-q", "-b", "main", cwd=tmp)
    _run_git("config", "user.email", "t@example.com", cwd=tmp)
    _run_git("config", "user.name", "t", cwd=tmp)
    return tmp


@contextlib.contextmanager
def _monkeypatch(obj: object, name: str, value: object):
    sentinel = object()
    original = getattr(obj, name, sentinel)
    setattr(obj, name, value)
    try:
        yield
    finally:
        if original is sentinel:
            delattr(obj, name)
        else:
            setattr(obj, name, original)


# ---------------------------------------------------------------------------
# F-RULES: every rule fires on its own runtime-generated shape (F-SEEDS: no
# committed literal fixtures anywhere in this file either).
# ---------------------------------------------------------------------------


def test_every_rule_fires_on_its_own_generated_seed() -> None:
    for rule in RULES:
        seed = generate_seed_for_rule(rule)
        hits = scan_line(seed)
        check(
            "FTF-1505",
            f"rule {rule!r} fires on its own runtime-generated seed",
            any(r == rule for r, _ in hits),
        )


def test_pem_private_key_variants() -> None:
    # F-SEEDS: each header is built from separate literals (never a single
    # contiguous match in THIS file's own source text -- the same split
    # discipline generate_seed_for_rule uses), so no baseline entry is
    # needed for this task's own test fixture.
    begin = "-----BEGIN "
    tail = "PRIVATE KEY-----"
    for header in (
        begin + "RSA " + tail,
        begin + "ENCRYPTED " + tail,
        begin + tail,
        begin + "OPENSSH " + tail,
    ):
        hits = scan_line(header)
        check(
            "FTF-1505",
            f"pem_private_key fires on {header!r}",
            any(r == "pem_private_key" for r, _ in hits),
        )


def test_canary_shape_is_gated_to_include_canary() -> None:
    marker = generate_seed_for_rule("canary_leaked")
    check(
        "FTF-1505",
        "the FTF-0905 canary shape is NOT flagged by default (source/history lanes)",
        scan_line(marker, include_canary=False) == [],
    )
    hits = scan_line(marker, include_canary=True)
    check(
        "FTF-1505",
        "the FTF-0905 canary shape IS flagged when include_canary=True (log/artifact lanes)",
        any(r == "canary_leaked" for r, _ in hits),
    )


_NEGATIVE_LINES: tuple[str, ...] = (
    "    tokenizer: LineageFact",
    "        tokenizer=tokenizer,",
    "    credentials: S3Credentials",
    '    password = os.environ.get("SOVRA_APP_DB_PASSWORD")',
    "class TokenBucket:",
    "secret_reference: str | None = None",
    'api_key_header = "X-Api-Key"',
    'password = "changeme"',
    'secret = "<PLACEHOLDER>"',
    'password = "test"',
)


def test_ordinary_identifiers_and_type_annotations_never_fire() -> None:
    for line in _NEGATIVE_LINES:
        hits = scan_line(line)
        check(
            "FTF-1505",
            f"ordinary code line does not trip any rule: {line!r}",
            hits == [],
        )


def test_too_short_shapes_do_not_fire() -> None:
    check("FTF-1505", "AKIA with 15 chars does not fire", scan_line("AKIA" + "A" * 15) == [])
    check("FTF-1505", "ghp_ with 35 chars does not fire", scan_line("ghp_" + "a" * 35) == [])
    check(
        "FTF-1505",
        "quoted_secret_assignment with 19-char value does not fire",
        scan_line('password = "' + "a" * 19 + '"') == [],
    )


def test_low_entropy_quoted_value_does_not_fire() -> None:
    check(
        "FTF-1505",
        "a long but low-entropy quoted value does not fire (entropy gate)",
        scan_line('password = "' + "a" * 25 + '"') == [],
    )


def test_your_placeholder_bypass_is_removed() -> None:
    value = "your_" + "RealLookingHighEntropyValue9Q7z"
    hits = scan_line(f'api_key = "{value}"')
    rule_hits = [v for r, v in hits if r == "quoted_secret_assignment"]
    check(
        "FTF-1505",
        "a high-entropy value prefixed 'your_' is no longer waved through as a placeholder",
        value in rule_hits,
    )


def test_url_userinfo_password_placeholder_is_not_flagged() -> None:
    for value in (
        "postgresql://user:${DB_PASSWORD}@db/x",
        "postgresql://user:<password>@db/x",
        "postgresql://user:{secret}@db/x",
        "postgresql://user:***@db/x",
        "postgresql://user:[REDACTED]@db/x",
    ):
        check(
            "FTF-1505",
            f"a templated/placeholder URL password is not flagged: {value!r}",
            scan_line(value) == [],
        )


# ---------------------------------------------------------------------------
# F-DECODE: base64, hex and percent-decoding layers; a derived, provably
# complete quick gate; a decode-cap overrun is itself a blocking finding.
# ---------------------------------------------------------------------------


def test_base64_wrapped_secret_is_found() -> None:
    seed = generate_seed_for_rule("aws_access_key_id")
    wrapped = base64.b64encode(f"aws_key={seed}".encode()).decode()
    hits = scan_line(f"blob: {wrapped}")
    check(
        "FTF-1505",
        "a secret shape hidden behind one layer of base64 is still found",
        any(rule == "aws_access_key_id" and value == seed for rule, value in hits),
    )


def test_hex_wrapped_secret_is_found() -> None:
    seed = generate_seed_for_rule("aws_access_key_id")
    wrapped = f"aws_key={seed}".encode().hex()
    hits = scan_line(f"blob: {wrapped}")
    check(
        "FTF-1505",
        "a secret shape hidden behind one layer of hex-encoding is still found",
        any(rule == "aws_access_key_id" and value == seed for rule, value in hits),
    )


def test_percent_decoded_secret_is_found() -> None:
    hits = scan_line("token=AKIA%41BCDEFGHIJKLMNOP")
    check(
        "FTF-1505",
        "a secret shape hidden behind percent-encoding is still found",
        any(
            rule == "aws_access_key_id" and value == "AKIAABCDEFGHIJKLMNOP" for rule, value in hits
        ),
    )


def test_canary_is_detected_through_base64_and_hex_decode_layers() -> None:
    """F-LOGS fault (F14/F15): round 2's decode layer never re-checked the
    canary shape on DECODED content -- only on the raw text -- so a
    base64/hex-encoded canary could never fail this suite's own positive
    control. Reproduced directly: the OLD check (canary tested against raw
    text only) misses both; the real, fixed `scan_line` catches both."""
    canary = generate_seed_for_rule("canary_leaked")
    b64 = base64.b64encode(canary.encode()).decode()
    hexed = canary.encode().hex()

    def _old_canary_check_raw_text_only(text: str) -> bool:
        return bool(sr._CANARY_SHAPE.search(text))

    check(
        "FTF-1505",
        "RED (fault reproduced): checking the canary shape against raw text only misses a "
        "base64-encoded canary",
        not _old_canary_check_raw_text_only(b64),
    )
    check(
        "FTF-1505",
        "RED (fault reproduced): checking the canary shape against raw text only misses a "
        "hex-encoded canary",
        not _old_canary_check_raw_text_only(hexed),
    )
    check(
        "FTF-1505",
        "GREEN (fixed): scan_line catches the base64-encoded canary",
        any(r == "canary_leaked" for r, _ in scan_line(b64, include_canary=True)),
    )
    check(
        "FTF-1505",
        "GREEN (fixed): scan_line catches the hex-encoded canary",
        any(r == "canary_leaked" for r, _ in scan_line(hexed, include_canary=True)),
    )


def test_quick_gate_literals_cover_every_rule_in_the_table() -> None:
    """F-DECODE: the quick gate is DERIVED from the same rule table every
    rule is matched with (`quick_gate_hints_for_rule`) -- every rule in
    `RULES` (plus the canary shape) must have a non-empty hint set, and a
    generated sample for every rule must actually pass the gate."""
    for rule in (*RULES, "canary_leaked"):
        hints = quick_gate_hints_for_rule(rule)
        check("FTF-1505", f"rule {rule!r} has at least one quick-gate hint registered", bool(hints))
        seed = generate_seed_for_rule(rule)
        check(
            "FTF-1505",
            f"rule {rule!r}'s own generated seed passes the quick gate",
            _could_match_any_rule(seed.lower()),
        )


def test_quick_gate_previously_missed_shapes_now_covered() -> None:
    """The concrete gaps the round-3 verifier found: `sk_live_` (only
    `rk_live_` was listed), `private_key=`/`session_key=` (no invariant
    substring at all) and a TAB after `Bearer` (only a literal space was
    checked)."""
    check(
        "FTF-1505", "'sk_live_...' passes the quick gate", _could_match_any_rule("sk_live_abcdef")
    )
    check(
        "FTF-1505",
        "'private_key=...' passes the quick gate",
        _could_match_any_rule("private_key=x"),
    )
    check(
        "FTF-1505",
        "'session_key=...' passes the quick gate",
        _could_match_any_rule("session_key=x"),
    )
    check("FTF-1505", "'bearer<TAB>...' passes the quick gate", _could_match_any_rule("bearer\tx"))
    hits = scan_line("Bearer\t" + generate_seed_for_rule("bearer_or_basic_header").split(" ", 1)[1])
    check(
        "FTF-1505",
        "a Bearer token separated by a TAB (not a space) is still matched end to end",
        any(r == "bearer_or_basic_header" for r, _ in hits),
    )


def test_quick_gate_is_a_performance_filter_only_never_a_detection_gap() -> None:
    check(
        "FTF-1505", "AKIA literal passes the quick gate", _could_match_any_rule("akia" + "x" * 20)
    )
    check(
        "FTF-1505",
        "unrelated text fails the quick gate",
        not _could_match_any_rule("just some prose"),
    )


def test_f13_long_line_window_finds_a_secret_past_the_200_char_boundary() -> None:
    """F13 fault: a line longer than the quick-scan window must not be
    silently truncated to its first 200 chars. Reproduced directly: a
    fault that disables windowing (truncate-then-scan-once, the shape a
    naive 200-char cap would take) misses a secret placed well past
    offset 200; the real, fixed `scan_line` still finds it."""
    seed = generate_seed_for_rule("aws_access_key_id")
    line = ("x" * 500) + f" aws_key={seed} " + ("y" * 500)
    hits = scan_line(line)
    check(
        "FTF-1505",
        "a secret ~500 chars into a long line is found (no silent truncation)",
        any(rule == "aws_access_key_id" and value == seed for rule, value in hits),
    )

    def _fault_truncate_then_scan_once(text: str) -> list[tuple[str, str]]:
        return list(sr._matches(text[:200], include_canary=False))

    check(
        "FTF-1505",
        "RED (fault reproduced): a hard 200-char truncation misses the same secret",
        not any(rule == "aws_access_key_id" for rule, _ in _fault_truncate_then_scan_once(line)),
    )


def test_no_decode_cap_every_candidate_is_decoded() -> None:
    """Round 4: the per-unit base64/hex candidate caps are gone (runs found by
    `finditer` are disjoint, so decoding all of them is linear). A secret
    wrapped in base64 after thousands of other candidates on the same line, in
    the same binary unit and through the CI-report CLI is still found; the
    round-3 shape (stop after 2,000) is reproduced as a fault and misses it."""
    import secrets as _secrets

    seed = generate_seed_for_rule("github_token")
    wrapped = base64.b64encode(f"note {seed}".encode()).decode()
    noise = " ".join(_secrets.token_urlsafe(24) for _ in range(3000))
    line = f"{noise} {wrapped}"
    check(
        "FTF-1505-NO-CAP",
        "a base64-wrapped secret after 3,000 other candidates on one line is found",
        any(rule == "github_token" and value == seed for rule, value in scan_line(line)),
    )

    def _fault_capped(text: str, cap: int = 2000) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for i, match in enumerate(sr._BASE64_RUN.finditer(text)):
            if i >= cap:
                break
            decoded = sr._try_base64_decode(match.group(0))
            if decoded and sr._could_match_any_rule(decoded.lower()):
                out.extend(sr._iter_shape_matches(decoded))
        return out

    check(
        "FTF-1505-NO-CAP",
        "RED (fault reproduced): a 2,000-candidate cap silently drops the same secret",
        not any(rule == "github_token" for rule, _ in _fault_capped(line)),
    )
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.bin"
        report.write_bytes(b"\xff\xfe" + line.encode())
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(ROOT / "tests" / "security" / "secret_rules.py"),
                str(report),
            ],
            capture_output=True,
            text=True,
        )
        check(
            "FTF-1505-NO-CAP",
            "the CI-report CLI finds it in a non-UTF-8 file with >2,000 base64 runs",
            result.returncode == 1 and "github_token" in result.stderr,
        )


def _long_secret_cases() -> dict[str, tuple[str, str]]:
    import json as _json
    import secrets as _secrets
    import string as _string
    from urllib.parse import quote as _quote

    alnum = _string.ascii_letters + _string.digits

    def rnd(alphabet: str, n: int) -> str:
        return "".join(_secrets.choice(alphabet) for _ in range(n))

    jwt = generate_seed_for_rule("jwt_shaped")
    jwt_long = (
        jwt.split(".")[0]
        + "."
        + base64.urlsafe_b64encode(_json.dumps({"sub": rnd(alnum, 40)}).encode())
        .decode()
        .rstrip("=")
        + "."
        + rnd(alnum, 43)
    )
    return {
        "jwt_shaped": (jwt_long, "jwt_shaped"),
        "jwt_percent_encoded": (_quote(jwt_long, safe=""), "jwt_shaped"),
        "client_secret_64": (
            'client_secret = "' + rnd(alnum, 64) + '"',
            "quoted_secret_assignment",
        ),
        "aws_secret_access_key": (
            "aws_secret_access_key = " + rnd(alnum + "/+", 40),
            "aws_secret_access_key",
        ),
        "slack_webhook": (generate_seed_for_rule("slack_webhook"), "slack_webhook"),
        "base64_github_token": (
            base64.b64encode(generate_seed_for_rule("github_token").encode()).decode(),
            "github_token",
        ),
        "url_userinfo_40": (
            "postgresql://svc:" + rnd(alnum, 40) + "@db.internal:5432/x",
            "url_userinfo_password",
        ),
    }


# The cases the round-3 verifier reproduced as missed at a window seam.
_ROUND3_SEAM_MISSES = frozenset(
    {"jwt_shaped", "client_secret_64", "aws_secret_access_key", "slack_webhook"}
)


def test_f13_seam_long_secrets_found_at_every_offset() -> None:
    """F13-SEAM (round-3 regression): 200-char windows with a 50-char overlap
    missed any secret longer than 50 chars straddling a seam. Every case is
    placed at every offset 0..450 of a >1,000-char line; all must be found.
    The round-3 window shape is reproduced as a fault and misses some."""

    def _fault_round3_windows(line: str) -> list[tuple[str, str]]:
        if len(line) <= 200:
            return list(sr._matches(line))
        out: list[tuple[str, str]] = []
        start = 0
        while start < len(line):
            out.extend(sr._matches(line[start : start + 200]))
            if start + 200 >= len(line):
                break
            start += 150
        return out

    for name, (seed, rule) in _long_secret_cases().items():
        missed = []
        fault_missed = 0
        for offset in range(451):
            line = ("ab" * 300)[:offset] + " " + seed + " " + ("cd " * 200)
            if not any(r == rule for r, _ in scan_line(line)):
                missed.append(offset)
            if not any(r == rule for r, _ in _fault_round3_windows(line)):
                fault_missed += 1
        check(
            "FTF-1505-SEAM",
            f"{name} (len {len(seed)}) is found at every one of 451 offsets in a long line "
            f"(missed at {missed[:5]})",
            not missed,
        )
        if name in _ROUND3_SEAM_MISSES:
            check(
                "FTF-1505-SEAM",
                f"RED (fault reproduced): round-3 windows miss {name} at some offset",
                fault_missed > 0,
            )

    jwt = _long_secret_cases()["jwt_shaped"][0]
    one_line_json = (
        '{"pad": "' + "z" * 113 + '", "auth": "' + jwt + '", "more": "' + "q" * 400 + '"}'
    )
    tmp = _build_temp_repo()
    (tmp / "fixture.json").write_text(one_line_json + "\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "one-line json", cwd=tmp)
    source_findings, _ = scan_source(tmp, {})
    history_findings, _ = scan_history(tmp, {})
    check(
        "FTF-1505-SEAM",
        "end to end: a JWT inside a one-line JSON file is found by the source lane",
        any(f.rule == "jwt_shaped" for f in source_findings),
    )
    check(
        "FTF-1505-SEAM",
        "end to end: the same blob is found by the history lane",
        any(f.rule == "jwt_shaped" for f in history_findings),
    )


def test_jwt_linear_matcher_equals_the_regex() -> None:
    """`_jwt_tokens` replaces `_JWT_CANDIDATE.finditer` (quadratic on a long
    dotless `a-a-a-...` run). Differential over 60,000 random strings built
    from the characters that matter (word chars, `-`, `.`, separators): the
    same tokens, in the same order. Plus a scaling check on the regex's own
    worst case."""
    import random
    import time

    rng = random.Random(1505)
    mismatches = 0
    compared = 0
    for _ in range(60_000):
        parts = []
        for _ in range(rng.randint(1, 8)):
            parts.append("".join(rng.choice("aB9_--") for _ in range(rng.randint(0, 25))))
            parts.append(rng.choice([".", " ", ".", "/", "", ".."]))
        text = "".join(parts)
        expected = [m.group(1) for m in sr._JWT_CANDIDATE.finditer(text)]
        compared += len(expected)
        if list(sr._jwt_tokens(text)) != expected:
            mismatches += 1
    check("FTF-1505-JWT", f"0 mismatches over 60,000 strings (got {mismatches})", mismatches == 0)
    check("FTF-1505-JWT", f"the differential compared real matches ({compared})", compared > 1000)

    timings = []
    for k in (20_000, 80_000):
        text = "x " + "a-" * k + "." + "b" * 12 + "." + "c" * 12
        start = time.perf_counter()
        scan_line(text)
        timings.append(time.perf_counter() - start)
    check(
        "FTF-1505-JWT",
        f"scan_line is linear on the JWT regex's worst case (x4 input: {timings})",
        timings[1] < 2.0 and timings[1] < 12 * max(timings[0], 0.005),
    )


def test_url_userinfo_linear_matcher_equals_the_regex() -> None:
    """`_url_userinfo_passwords` replaces `_URL_USERINFO.finditer` (quadratic
    on `http://a:http://a:...` with no `@`). Differential over 60,000 random
    strings built from the characters the pattern branches on: the same
    passwords, in the same order."""
    import random

    rng = random.Random(15051)
    atoms = [
        "://",
        ":",
        "/",
        "@",
        "a",
        "Z",
        "9",
        "+",
        ".",
        "-",
        "_",
        " ",
        "\n",
        "\t",
        '"',
        "'",
        "http",
        "s3",
        "x+y",
        "\u00e9",
        "\x1c",
        "\v",
        "://a:b@",
        "\nhttp",
    ]
    mismatches = 0
    compared = 0
    for _ in range(60_000):
        text = "".join(rng.choice(atoms) for _ in range(rng.randint(0, 40)))
        expected = [m.group(2) for m in sr._URL_USERINFO.finditer(text)]
        compared += len(expected)
        if list(sr._url_userinfo_passwords(text)) != expected:
            mismatches += 1
    check("FTF-1505-URL", f"0 mismatches over 60,000 strings (got {mismatches})", mismatches == 0)
    check("FTF-1505-URL", f"the differential compared real matches ({compared})", compared > 1000)


def test_whole_line_scanning_stays_linear_on_pathological_lines() -> None:
    """No window means each rule sees the whole line; every rule must stay
    linear on the shapes that make naive patterns backtrack."""
    import time

    shapes = {
        "dash_words": lambda k: "a-" * k,
        "long_base64_run": lambda k: "Q" * (2 * k),
        "many_urls": lambda k: "https://host:8080/p " * (k // 10),
        "many_assignments": lambda k: 'api_key = "' * (k // 6),
        "hex_then_word": lambda k: "ab" * k + "g",
        "userinfo_without_at": lambda k: "http://a:" * (k // 9),
        "userinfo_password_runs": lambda k: "a://b:c/d" * (k // 9),
    }
    for name, build in shapes.items():
        small, big = build(25_000), build(100_000)
        t0 = time.perf_counter()
        scan_line(small)
        t1 = time.perf_counter()
        scan_line(big)
        t2 = time.perf_counter()
        small_t, big_t = t1 - t0, t2 - t1
        check(
            "FTF-1505-LINEAR",
            f"{name}: 4x input stays roughly linear ({small_t:.3f}s -> {big_t:.3f}s)",
            big_t < 5.0 and big_t < 12 * max(small_t, 0.01),
        )


# ---------------------------------------------------------------------------
# F-SCOPE / F-BINARY / F-ARCHIVE
# ---------------------------------------------------------------------------


def test_source_scan_has_no_path_exclusion() -> None:
    tmp = _build_temp_repo()
    for rel in ("apps/api/main.py", "tests/test_x.py", "scripts/tool.py", "README.md"):
        path = tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
    secret_path = tmp / "tests" / "fixtures" / "leaked.py"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text(
        f'aws_key = "{generate_seed_for_rule("aws_access_key_id")}"\n', encoding="utf-8"
    )
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "init", cwd=tmp)

    tracked = iter_tracked_files(tmp)
    check(
        "FTF-1505",
        "iter_tracked_files covers tests/ (not restricted to SOURCE_ROOTS)",
        "tests/fixtures/leaked.py" in tracked,
    )
    scanned: set[str] = set()
    findings, results = scan_source(tmp, {}, scanned_paths=scanned)
    check(
        "FTF-1505",
        "a secret under tests/ (outside every SOURCE_ROOTS entry) is found by scan_source",
        any(
            f.rule == "aws_access_key_id" and "tests/fixtures/leaked.py" in f.location
            for f in findings
        ),
    )
    check(
        "FTF-1505",
        "scan_source produced one ScenarioResult per tracked file",
        len(results) == len(tracked),
    )
    check("FTF-1505", "scanned_paths captured every tracked file", scanned == set(tracked))

    # F4: no directory is excluded -- docs/ and vendor/ included.
    for rel in ("docs/notes.md", "vendor/lib/x.py"):
        path = tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'k = "{generate_seed_for_rule("github_token")}"\n', encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "docs and vendor", cwd=tmp)
    findings_dv, _ = scan_source(tmp, {})
    for rel in ("docs/notes.md", "vendor/lib/x.py"):
        check(
            "FTF-1505-F4",
            f"a secret under {rel.split('/')[0]}/ is found by scan_source",
            any(f.rule == "github_token" and f.location.startswith(rel) for f in findings_dv),
        )


def test_non_ascii_filename_is_scanned_not_silently_skipped() -> None:
    """F-SCOPE fault, fixed: plain `git ls-files` (no `-z`) applies
    `core.quotePath` by default and octal-escapes a non-ASCII filename
    (`"caf\\303\\251.txt"`), which then never resolves back to a real path
    on disk -- the file is silently skipped with zero signal. `-z` avoids
    the quoting entirely."""
    tmp = _build_temp_repo()
    nonascii = tmp / "café.txt"
    nonascii.write_text(
        f'aws_key = "{generate_seed_for_rule("aws_access_key_id")}"\n', encoding="utf-8"
    )
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "init", cwd=tmp)

    tracked = iter_tracked_files(tmp)
    check("FTF-1505", "the non-ASCII filename comes back unquoted", "café.txt" in tracked)

    def _fault_quoted_ls_files(root: Path) -> list[str]:
        out = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
        ).stdout
        return out.splitlines()

    quoted = _fault_quoted_ls_files(tmp)
    check(
        "FTF-1505",
        "RED (fault reproduced): plain `git ls-files` (no -z) returns the quoted/escaped form, "
        "which does not match the real filename",
        "café.txt" not in quoted,
    )

    findings, _ = scan_source(tmp, {})
    check(
        "FTF-1505",
        "GREEN (fixed): the secret in the non-ASCII-named file is actually found",
        any(f.rule == "aws_access_key_id" for f in findings),
    )


def test_binary_file_is_scanned() -> None:
    tmp = _build_temp_repo()
    seed = generate_seed_for_rule("aws_access_key_id")
    binary_path = tmp / "asset.bin"
    binary_path.write_bytes(bytes([0, 1, 2, 3]) + f'aws_key="{seed}"'.encode() + bytes([255, 254]))
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "init", cwd=tmp)
    findings, _ = scan_source(tmp, {})
    check(
        "FTF-1505",
        "a secret-shaped value inside a non-UTF-8 binary file is found",
        any(f.rule == "aws_access_key_id" for f in findings),
    )


def test_zip_archive_member_is_scanned() -> None:
    tmp = _build_temp_repo()
    seed = generate_seed_for_rule("aws_access_key_id")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("config.ini", f'aws_key = "{seed}"\n')
    (tmp / "bundle.zip").write_bytes(buf.getvalue())
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "init", cwd=tmp)
    findings, _ = scan_source(tmp, {})
    check(
        "FTF-1505",
        "a secret inside a zip archive member is found",
        any(
            f.rule == "aws_access_key_id" and "bundle.zip!config.ini" in f.location
            for f in findings
        ),
    )


def test_corrupt_archive_member_does_not_abort_later_members() -> None:
    """F-SCOPE/F-BINARY fault, fixed: round 2 wrapped the ENTIRE member
    loop in one try/except, so a corrupt member (bad CRC, truncated entry)
    raised mid-iteration and silently abandoned every member after it.
    Reproduced directly: a 3-member zip with the middle member's stored
    bytes corrupted (bad CRC) -- the real, fixed extractor still yields
    both good members (one marked unreadable), while the OLD
    whole-loop-in-one-try/except shape would have yielded only the first."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("good1.txt", "A" * 50)
        zf.writestr("bad.txt", "B" * 50)
        zf.writestr("good2.txt", "C" * 50)
    mutable = bytearray(buf.getvalue())
    idx = mutable.find(b"B" * 50)
    assert idx != -1
    mutable[idx + 5] = (mutable[idx + 5] + 1) % 256  # flip a byte -> CRC mismatch on read
    data = bytes(mutable)

    members = list(ss._iter_archive_members(data, "bundle.zip"))
    names = [name for name, _content, _truncated in members]
    check(
        "FTF-1505", "all 3 members are enumerated", names == ["good1.txt", "bad.txt", "good2.txt"]
    )
    content_by_name = {name: content for name, content, _truncated in members}
    check(
        "FTF-1505",
        "the member before the corrupt one is still read",
        content_by_name["good1.txt"] is not None,
    )
    check(
        "FTF-1505",
        "the corrupt member itself yields None (fail closed)",
        content_by_name["bad.txt"] is None,
    )
    check(
        "FTF-1505",
        "GREEN (fixed): the member AFTER the corrupt one is still read, not abandoned",
        content_by_name["good2.txt"] is not None,
    )

    def _fault_whole_loop_one_try(data: bytes, name: str) -> list[tuple[str, bytes]]:
        out = []
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    with zf.open(info) as handle:
                        out.append((info.filename, handle.read()))
        except Exception:
            pass
        return out

    with_fault = _fault_whole_loop_one_try(data, "bundle.zip")
    check(
        "FTF-1505",
        "RED (fault reproduced): the old whole-loop-in-one-try/except shape abandons good2.txt",
        "good2.txt" not in [n for n, _ in with_fault],
    )


def test_archive_member_bomb_safety_caps() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(3):
            zf.writestr(f"f{i}.txt", "hello world\n")
    data = buf.getvalue()
    members = list(ss._iter_archive_members(data, "bundle.zip"))
    check("FTF-1505", "zip members are extracted and bounded", len(members) == 3)

    with _monkeypatch(ss, "_MAX_ARCHIVE_MEMBERS", 1):
        raised = False
        try:
            list(ss._iter_archive_members(data, "bundle.zip"))
        except SecretScanError:
            raised = True
        check(
            "FTF-1505",
            "an archive exceeding the member cap is refused, not silently truncated",
            raised,
        )


def _scan_incomplete_labels(name: str, data: bytes, archive_name: str | None = None) -> list[str]:
    return [
        location
        for rule, _value, location in ss._scan_content_and_members(
            name, data, include_canary=False, content_truncated=False, archive_name=archive_name
        )
        if rule == "scan_incomplete"
    ]


def test_corrupt_deflated_member_is_blocking_and_later_members_still_scanned() -> None:
    """F-ARCHIVE-ZLIB (round-2 P9, unchanged in round 3): a corrupt DEFLATED
    member raises `zlib.error`, which escaped the per-member except tuple and
    silently dropped every later member. Now any per-member failure yields a
    blocking scan_incomplete and the later member's secret is still found."""
    import secrets as _secrets

    seed = generate_seed_for_rule("huggingface_token")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.txt", "".join(_secrets.choice("abcdefgh") for _ in range(4000)))
        zf.writestr("b.txt", seed + "\n")
    data = bytearray(buf.getvalue())
    idx = data.find(b"a.txt") + len("a.txt") + 10
    data[idx] ^= 0xFF
    results = ss._scan_content_and_members(
        "x.zip", bytes(data), include_canary=False, content_truncated=False
    )
    rules = [rule for rule, _value, _location in results]
    check(
        "FTF-1505-ARCHIVE",
        "a corrupt deflated member is a blocking scan_incomplete (unreadable member)",
        any(
            "x.zip!a.txt (unreadable member)" in loc
            for loc in _scan_incomplete_labels("x.zip", bytes(data))
        ),
    )
    check(
        "FTF-1505-ARCHIVE",
        "the member after the corrupt deflated one is still scanned (its secret is found)",
        "huggingface_token" in rules,
    )

    def _fault_round3_except_tuple(blob: bytes) -> list[str]:
        out = []
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                for info in zf.infolist():
                    try:
                        with zf.open(info) as handle:
                            out.append(handle.read().decode("latin-1"))
                    except (zipfile.BadZipFile, OSError, RuntimeError, EOFError):
                        out.append("")
        except Exception:
            return out
        return out

    check(
        "FTF-1505-ARCHIVE",
        "RED (fault reproduced): the round-3 except tuple loses every member after the zlib error",
        not any(seed in text for text in _fault_round3_except_tuple(bytes(data))),
    )


def test_truncated_archives_are_blocking_not_silent() -> None:
    """F-ARCHIVE-CORRUPT: a truncated .zip, .tar.gz and .gz used to hit an
    outer `except Exception: return` (and EOFError escaped the .gz path) and
    passed clean. Each is now a blocking scan_incomplete."""
    import gzip
    import tarfile

    seed = generate_seed_for_rule("huggingface_token")
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("m.txt", seed + "\n")
    tbuf = io.BytesIO()
    payload = ("x" * 3000 + "\n" + seed + "\n").encode()
    with tarfile.open(fileobj=tbuf, mode="w:gz") as tf:
        info = tarfile.TarInfo("c.ini")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    gz = gzip.compress(("x" * 3000 + seed).encode())
    for name, blob in (
        ("t.zip", zbuf.getvalue()[:-30]),
        ("t.tar.gz", tbuf.getvalue()[: len(tbuf.getvalue()) // 2]),
        ("t.txt.gz", gz[: len(gz) // 2]),
    ):
        labels = _scan_incomplete_labels(name, blob)
        check(
            "FTF-1505-ARCHIVE",
            f"a truncated {name} yields a blocking scan_incomplete (got {labels})",
            bool(labels),
        )
    check(
        "FTF-1505-ARCHIVE",
        "an intact archive yields no scan_incomplete",
        not _scan_incomplete_labels("ok.zip", zbuf.getvalue()),
    )


def test_oversized_archive_member_is_blocking_and_history_names_resolve() -> None:
    """F-ARCHIVE-OVERSIZE: a member longer than MAX_SCAN_BYTES was read to the
    bound and the rest dropped silently. Now it is a blocking scan_incomplete,
    for zip, tar.gz and .gz alike -- also for a history blob, whose location
    label (`path@sha`) no longer hides the archive's extension."""
    import gzip
    import tarfile

    with _monkeypatch(ss, "MAX_SCAN_BYTES", 100):
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w") as zf:
            zf.writestr("big.txt", "x" * 500)
        tbuf = io.BytesIO()
        with tarfile.open(fileobj=tbuf, mode="w:gz") as tf:
            info = tarfile.TarInfo("big.txt")
            info.size = 500
            tf.addfile(info, io.BytesIO(b"y" * 500))
        cases = (
            ("a.zip", zbuf.getvalue(), None, "a.zip!big.txt (member exceeds scan bound)"),
            ("a.tar.gz", tbuf.getvalue(), None, "a.tar.gz!big.txt (member exceeds scan bound)"),
            (
                "a.txt.gz",
                gzip.compress(b"z" * 500),
                None,
                "a.txt.gz!a.txt (member exceeds scan bound)",
            ),
            (
                "a.tar.gz@0123456789ab",
                tbuf.getvalue(),
                "a.tar.gz",
                "a.tar.gz@0123456789ab!big.txt (member exceeds scan bound)",
            ),
        )
        for name, blob, archive_name, expected in cases:
            labels = _scan_incomplete_labels(name, blob, archive_name)
            check(
                "FTF-1505-ARCHIVE",
                f"oversized member in {name} is a blocking scan_incomplete (got {labels})",
                expected in labels,
            )


def test_history_blob_over_the_bound_is_blocking() -> None:
    """F6h: the history lane's own truncation flag must reach the result -- a
    history-only blob larger than MAX_SCAN_BYTES is a blocking scan_incomplete."""
    tmp = _build_temp_repo()
    (tmp / "big.txt").write_text("x" * 500, encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "big", cwd=tmp)
    (tmp / "big.txt").unlink()
    (tmp / "small.txt").write_text("ok\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "drop big", cwd=tmp)
    with _monkeypatch(ss, "MAX_SCAN_BYTES", 100):
        findings, _ = scan_history(tmp, {})
    check(
        "FTF-1505-F6h",
        "a history-only blob over the scan bound is a blocking scan_incomplete",
        any(
            f.rule == "scan_incomplete" and f.blocking and "big.txt@" in f.location
            for f in findings
        ),
    )
    findings_ok, _ = scan_history(tmp, {})
    check(
        "FTF-1505-F6h",
        "the same blob under the real bound is not flagged",
        not any(f.rule == "scan_incomplete" for f in findings_ok),
    )


def test_oversized_content_is_a_blocking_scan_incomplete_finding() -> None:
    """F6 fixed: content past `MAX_SCAN_BYTES` is truncated for scanning
    (an unbounded read is itself a resource-exhaustion risk) but the
    truncation is now a blocking `scan_incomplete` finding, never silent."""
    tmp = _build_temp_repo()
    with _monkeypatch(ss, "MAX_SCAN_BYTES", 100):
        big = tmp / "big.txt"
        big.write_text("x" * 500, encoding="utf-8")
        _run_git("add", "-A", cwd=tmp)
        _run_git("commit", "-q", "-m", "init", cwd=tmp)
        findings, _ = scan_source(tmp, {})
        check(
            "FTF-1505",
            "oversized content yields a blocking scan_incomplete finding",
            any(f.rule == "scan_incomplete" and f.blocking for f in findings),
        )

    with _monkeypatch(ss, "MAX_SCAN_BYTES", 10_000):
        findings2, _ = scan_source(tmp, {})
        check(
            "FTF-1505",
            "the same content well under the (restored) bound produces no scan_incomplete",
            not any(f.rule == "scan_incomplete" for f in findings2),
        )


def test_long_line_is_not_silently_skipped() -> None:
    padding = "x" * 10_000
    seed = generate_seed_for_rule("aws_access_key_id")
    line = f"{padding} aws_key={seed}"
    hits = scan_line(line)
    check(
        "FTF-1505",
        "a secret on a line far longer than the old 4,000-char cap is still found",
        any(rule == "aws_access_key_id" for rule, _ in hits),
    )


def test_source_roots_come_from_product_truth_check_not_a_second_list() -> None:
    import scripts.product_truth_check as ptc

    check(
        "FTF-1505",
        "SOURCE_ROOTS is reused directly from scripts/product_truth_check.py, not hand-listed",
        SOURCE_ROOTS == tuple(ptc.SOURCE_ROOTS),
    )


def test_artifact_roots_include_reports() -> None:
    check(
        "FTF-1505",
        "ARTIFACT_ROOTS covers docs/certifications, docs/progress and reports",
        ARTIFACT_ROOTS == ("docs/certifications", "docs/progress", "reports"),
    )


def test_assert_dirs_covered_fails_closed_on_a_real_omission() -> None:
    tmp = _build_temp_repo()
    covered_dir = tmp / "apps" / "api"
    covered_dir.mkdir(parents=True)
    (covered_dir / "main.py").write_text("x = 1\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "init", cwd=tmp)
    omitted_dir = tmp / "workers" / "training"
    omitted_dir.mkdir(parents=True)
    (tmp / ".gitignore").write_text("workers/training/\n", encoding="utf-8")
    (omitted_dir / "train.py").write_text("x = 1\n", encoding="utf-8")
    _run_git("add", ".gitignore", cwd=tmp)
    _run_git("commit", "-q", "-m", "gitignore workers/training", cwd=tmp)

    raised = False
    try:
        assert_dirs_covered(tmp, ("apps/api", "workers/training"))
    except SecretScanError:
        raised = True
    check(
        "FTF-1505",
        "assert_dirs_covered RED: a production dir with zero tracked files is a release blocker",
        raised,
    )

    (tmp / ".gitignore").unlink()
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "stop ignoring", cwd=tmp)
    ok = True
    try:
        assert_dirs_covered(tmp, ("apps/api", "workers/training"))
    except SecretScanError:
        ok = False
    check("FTF-1505", "assert_dirs_covered GREEN once the directory is actually tracked", ok)
    check(
        "FTF-1505",
        "iter_tracked_files_under agrees with assert_dirs_covered's own verdict",
        len(iter_tracked_files_under(tmp, ("workers/training",))) == 1,
    )


def test_assert_scanned_set_matches_tracked_files() -> None:
    tmp = _build_temp_repo()
    for rel in ("a.py", "b.py", "c.py"):
        (tmp / rel).write_text("x = 1\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "init", cwd=tmp)

    ok = True
    try:
        assert_scanned_set_matches_tracked_files(tmp, {"a.py", "b.py", "c.py"})
    except SecretScanError:
        ok = False
    check("FTF-1505", "GREEN: the scanned set matches git ls-files exactly", ok)

    raised = False
    try:
        assert_scanned_set_matches_tracked_files(tmp, {"a.py", "b.py"})
    except SecretScanError:
        raised = True
    check(
        "FTF-1505",
        "RED: a tracked file missing from the scanned set is a release blocker",
        raised,
    )


def test_assert_artifact_uploads_covered_fails_closed_on_a_new_root() -> None:
    covered = """
jobs:
  x:
    steps:
      - uses: actions/upload-artifact@abc
        with:
          path: reports/thing.json
"""
    assert_artifact_uploads_covered(covered, ARTIFACT_ROOTS)  # does not raise
    uncovered = """
jobs:
  x:
    steps:
      - uses: actions/upload-artifact@abc
        with:
          path: build/models/thing.bin
"""
    raised = False
    try:
        assert_artifact_uploads_covered(uncovered, ARTIFACT_ROOTS)
    except SecretScanError:
        raised = True
    check(
        "FTF-1505",
        "an upload-artifact path outside every declared root fails closed",
        raised,
    )
    check(
        "FTF-1505",
        "parse_upload_artifact_paths finds the real ci.yml's own upload paths",
        set(parse_upload_artifact_paths(covered)) == {"reports/thing.json"},
    )


def test_upload_artifact_merge_variant_is_covered() -> None:
    merge_ok = """
jobs:
  x:
    steps:
      - uses: actions/upload-artifact/merge@v4
        with:
          path: reports/merged.json
"""
    assert_artifact_uploads_covered(merge_ok, ARTIFACT_ROOTS)  # does not raise
    merge_bad = """
jobs:
  x:
    steps:
      - uses: actions/upload-artifact/merge@v4
        with:
          path: elsewhere/merged.json
"""
    raised = False
    try:
        assert_artifact_uploads_covered(merge_bad, ARTIFACT_ROOTS)
    except SecretScanError:
        raised = True
    check(
        "FTF-1505",
        "the actions/upload-artifact/merge@... variant is parsed, not only the bare action",
        raised,
    )


def test_upload_path_traversal_is_refused() -> None:
    traversal = """
jobs:
  x:
    steps:
      - uses: actions/upload-artifact@abc
        with:
          path: reports/../build
"""
    raised = False
    try:
        assert_artifact_uploads_covered(traversal, ARTIFACT_ROOTS)
    except SecretScanError:
        raised = True
    check("FTF-1505", "a '..' path-traversal segment in an upload path is refused outright", raised)


def test_upload_path_far_from_uses_line_is_still_found() -> None:
    """Round 2 used a fixed 9-line lookahead window from `uses:`; round 3
    parses the YAML structurally, so a `path:` many lines later (another
    `with:` key first) is still found."""
    far = """
jobs:
  x:
    steps:
      - uses: actions/upload-artifact@abc
        with:
          name: some-name
          retention-days: 5
          if-no-files-found: error
          compression-level: 9
          overwrite: true
          include-hidden-files: false
          path: reports/far.json
"""
    paths = parse_upload_artifact_paths(far)
    check(
        "FTF-1505",
        "a path: far from the uses: line (structural parse, not a fixed window) is still found",
        "reports/far.json" in paths,
    )


def test_this_repos_ci_yml_artifact_uploads_are_covered() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    raised = False
    try:
        assert_artifact_uploads_covered(text, ARTIFACT_ROOTS)
    except SecretScanError:
        raised = True
    check("FTF-1505", "this repo's real ci.yml upload-artifact paths are all covered", not raised)


def test_assert_not_shallow_fails_closed_on_a_real_shallow_clone() -> None:
    tmp = _build_temp_repo()
    (tmp / "f.py").write_text("x = 1\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "init", cwd=tmp)
    (tmp / "f.py").write_text("x = 2\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "second", cwd=tmp)

    shallow = _mkdtemp("ftf1505_shallow_")
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{tmp}", str(shallow)],
        check=True,
        capture_output=True,
    )
    raised = False
    try:
        assert_not_shallow(shallow)
    except SecretScanError:
        raised = True
    check(
        "FTF-1505",
        "assert_not_shallow RED: a shallow clone is NOT_EXERCISED, never silently narrowed to HEAD",
        raised,
    )
    ok = True
    try:
        assert_not_shallow(tmp)
    except SecretScanError:
        ok = False
    check("FTF-1505", "assert_not_shallow GREEN on a real full clone", ok)


# ---------------------------------------------------------------------------
# F-HISTORY: HEAD-reachable blobs ("pr" scope, the CI default) vs every ref
# ("all-refs", with branch attribution) -- the 2026-10-15T08:43Z ruling
# (F2b: a dropped --all/ref restriction).
# ---------------------------------------------------------------------------


def test_history_finds_a_secret_removed_from_the_working_tree() -> None:
    tmp = _build_temp_repo()
    target = tmp / "apps" / "api" / "config.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "init", cwd=tmp)

    leaked_key = generate_seed_for_rule("aws_access_key_id")
    target.write_text(f'aws_key = "{leaked_key}"\n', encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "oops, hardcoded a key", cwd=tmp)

    target.write_text("x = 1\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "remove the key", cwd=tmp)

    source_findings, _ = scan_source(tmp, {})
    check(
        "FTF-1505",
        "the working-tree-only scan sees nothing -- the key is gone from HEAD",
        not any(f.rule == "aws_access_key_id" for f in source_findings),
    )
    history_findings, history_results = scan_history(tmp, {})
    aws_hits = [f for f in history_findings if f.rule == "aws_access_key_id"]
    check(
        "FTF-1505",
        "the history scan (default scope='pr', HEAD-reachable) finds the key that was "
        "committed and later removed",
        len(aws_hits) == 1 and aws_hits[0].blocking,
    )
    check(
        "FTF-1505",
        "scan_history yields one ScenarioResult per unique blob (2 distinct contents: "
        "the 3 commits' first and third states are byte-identical, so they dedupe to one blob)",
        len(history_results) == 2,
    )


def test_history_finds_a_secret_introduced_only_in_a_merge_commit() -> None:
    tmp = _build_temp_repo()
    target = tmp / "apps" / "api" / "config.py"
    target.parent.mkdir(parents=True)
    target.write_text("base = 1\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "init", cwd=tmp)
    base_branch = _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=tmp).strip()

    _run_git("checkout", "-q", "-b", "branch-a", cwd=tmp)
    target.write_text("base = 1\nside_a = 2\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "side a", cwd=tmp)

    _run_git("checkout", "-q", "-b", "branch-b", base_branch, cwd=tmp)
    target.write_text("base = 1\nside_b = 3\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "side b", cwd=tmp)

    _run_git("checkout", "-q", "branch-a", cwd=tmp)
    merge_rc = subprocess.run(
        ["git", "merge", "-q", "--no-ff", "branch-b"],
        cwd=tmp,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "HOME": str(tmp),
            "PATH": "/usr/bin:/bin",
        },
    )
    check("FTF-1505", "the merge produced a real conflict to resolve", merge_rc.returncode != 0)
    leaked_key = generate_seed_for_rule("aws_access_key_id")
    target.write_text(f'base = 1\naws_key = "{leaked_key}"\n', encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "resolve merge conflict", cwd=tmp)

    log_p = subprocess.run(
        ["git", "log", "--all", "-p"], cwd=tmp, capture_output=True, text=True, check=True
    ).stdout
    check(
        "FTF-1505",
        "reproduced: plain `git log --all -p` never shows the merge-only secret as an added line",
        leaked_key not in log_p,
    )

    history_findings, _ = scan_history(tmp, {})
    aws_hits = [f for f in history_findings if f.rule == "aws_access_key_id"]
    check(
        "FTF-1505",
        "scan_history (blob-based, HEAD-reachable) still finds a secret introduced only in a "
        "merge commit on the current branch",
        len(aws_hits) == 1 and aws_hits[0].blocking,
    )


def test_history_blob_ids_are_deduplicated() -> None:
    tmp = _build_temp_repo()
    target = tmp / "f.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "a", cwd=tmp)
    target.write_text("x = 2\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "b", cwd=tmp)
    target.write_text("x = 1\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "c", cwd=tmp)

    blobs = iter_history_blob_ids(tmp, refs=("HEAD",))
    check(
        "FTF-1505", "3 commits with only 2 distinct contents yield 2 unique blobs", len(blobs) == 2
    )


def test_archive_member_in_a_history_only_blob_is_scanned() -> None:
    """F-SCOPE fault, fixed: round 2's `scan_history` never extracted
    archive members from a blob that is itself an archive (only
    `scan_source`/`scan_artifact_roots` did) -- a secret inside a zip that
    was committed and later deleted was invisible to the history lane."""
    tmp = _build_temp_repo()
    seed = generate_seed_for_rule("aws_access_key_id")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("secret.txt", f'aws_key = "{seed}"')
    (tmp / "bundle.zip").write_bytes(buf.getvalue())
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "add zip", cwd=tmp)
    (tmp / "bundle.zip").unlink()
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "remove zip", cwd=tmp)

    findings, _ = scan_history(tmp, {})
    check(
        "FTF-1505",
        "a secret inside a history-only blob that is itself a zip archive is found",
        any(
            f.rule == "aws_access_key_id"
            and "bundle.zip@" in f.location
            and "!secret.txt" in f.location
            for f in findings
        ),
    )


def test_scan_history_pr_scope_excludes_other_refs_all_refs_scope_attributes_them() -> None:
    """The 2026-10-15T08:43Z ruling, F2b: in PR CI the history lane must
    scan only the checked-out HEAD (so an unrelated open PR's own fixture
    on another fetched ref can never redden THIS PR); a separate all-refs
    sweep still covers every ref and names which branch carries a finding
    HEAD cannot reach."""
    tmp = _build_temp_repo()
    (tmp / "a.txt").write_text("hello world\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "init", cwd=tmp)

    _run_git("checkout", "-q", "-b", "other-pr-branch", cwd=tmp)
    seed = generate_seed_for_rule("aws_access_key_id")
    (tmp / "secret.txt").write_text(f'aws_key = "{seed}"\n', encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "secret on another PR's branch", cwd=tmp)
    _run_git("checkout", "-q", "main", cwd=tmp)

    pr_findings, _ = scan_history(tmp, {}, scope="pr")
    check(
        "FTF-1505",
        "scope='pr' (HEAD only) never sees a secret that exists only on another ref",
        not any(f.rule == "aws_access_key_id" for f in pr_findings),
    )

    all_refs_findings, _ = scan_history(tmp, {}, scope="all-refs")
    aws_hits = [f for f in all_refs_findings if f.rule == "aws_access_key_id"]
    check(
        "FTF-1505",
        "scope='all-refs' DOES find the secret on the other ref",
        len(aws_hits) == 1 and aws_hits[0].blocking,
    )
    check(
        "FTF-1505",
        "the all-refs finding's location names the branch that actually carries it",
        bool(aws_hits) and "other-pr-branch" in aws_hits[0].location,
    )

    raised = False
    try:
        scan_history(tmp, {}, scope="not-a-real-scope")
    except ValueError:
        raised = True
    check(
        "FTF-1505",
        "an unknown scope is refused outright, not silently treated as a default",
        raised,
    )


# ---------------------------------------------------------------------------
# F-BASELINE
# ---------------------------------------------------------------------------


def test_baseline_rejects_a_value_path_or_glob_key() -> None:
    for bad_key, bad_value in (("value", "x"), ("path", "y"), ("file_glob", "z"), ("glob", "w")):
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = Path(tmp) / "baseline.yaml"
            bad_path.write_text(
                "entries:\n"
                f'  - fingerprint: "{"a" * 64}"\n'
                f'    {bad_key}: "{bad_value}"\n'
                "    reason: x\n"
                "    reviewed_by: y\n"
                "    reviewed_on: z\n",
                encoding="utf-8",
            )
            raised = False
            try:
                load_baseline(bad_path)
            except SecretScanError:
                raised = True
            check(
                "FTF-1505",
                f"a baseline entry carrying a {bad_key!r} key is refused, not silently accepted",
                raised,
            )


def test_baseline_schema_is_strict() -> None:
    """Advisory from round 3: only fingerprint/reason/reviewed_by/reviewed_on
    are accepted on an entry, and only `entries` at the top level."""
    for body in (
        f'entries:\n  - fingerprint: "{"a" * 64}"\n    reason: x\n    reviewed_by: y\n'
        "    reviewed_on: z\n    note: extra\n",
        f'entries:\n  - fingerprint: "{"a" * 64}"\n    reason: x\n    reviewed_by: y\n'
        "    reviewed_on: z\nwaive_all: true\n",
    ):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.yaml"
            path.write_text(body, encoding="utf-8")
            raised = False
            try:
                load_baseline(path)
            except SecretScanError:
                raised = True
            check(
                "FTF-1505-BASELINE",
                f"a baseline with an unknown key is refused: {body[-20:]!r}",
                raised,
            )


def test_baseline_rejects_malformed_or_missing_fields() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bad_path = Path(tmp) / "baseline.yaml"
        bad_path.write_text(
            f'entries:\n  - fingerprint: "{"a" * 64}"\n    reason: x\n    reviewed_by: ""\n'
            "    reviewed_on: z\n",
            encoding="utf-8",
        )
        raised = False
        try:
            load_baseline(bad_path)
        except SecretScanError:
            raised = True
        check("FTF-1505", "a baseline entry with an empty reviewed_by is refused", raised)


def test_baseline_fingerprint_waives_every_recurrence_of_the_same_value() -> None:
    fingerprint = baseline_fingerprint("aws_access_key_id", "AKIASHAREDACROSSFILES1")
    baseline = {
        fingerprint: BaselineEntry(fingerprint, "shared test fixture", "tester", "2026-10-15")
    }
    check(
        "FTF-1505",
        "the same (rule, value) pair is waived in file A",
        is_baselined("aws_access_key_id", "AKIASHAREDACROSSFILES1", baseline),
    )
    check(
        "FTF-1505",
        "a DIFFERENT value under the same rule is still blocking",
        not is_baselined("aws_access_key_id", "AKIADIFFERENTVALUE0001", baseline),
    )


def test_stale_baseline_entry_is_itself_a_blocking_finding() -> None:
    fingerprint = baseline_fingerprint("aws_access_key_id", "AKIANEVERACTUALLYSEEN1")
    baseline = {fingerprint: BaselineEntry(fingerprint, "x", "y", "z")}
    raised = False
    try:
        assert_baseline_fresh(baseline, seen_fingerprints=set())
    except SecretScanError:
        raised = True
    check(
        "FTF-1505",
        "RED: a baseline entry that matched nothing this run is a release blocker, not a free pass",
        raised,
    )
    ok = True
    try:
        assert_baseline_fresh(baseline, seen_fingerprints={fingerprint})
    except SecretScanError:
        ok = False
    check("FTF-1505", "GREEN: a baseline entry that matched something this run is not stale", ok)


def test_this_repos_committed_baseline_loads_and_has_justified_entries() -> None:
    entries = load_baseline()
    check("FTF-1505", "committed baseline has entries", len(entries) > 0)
    check(
        "FTF-1505",
        "every entry has a non-empty reason/reviewer/date",
        all(
            e.reason.strip() and e.reviewed_by.strip() and e.reviewed_on.strip()
            for e in entries.values()
        ),
    )


# ---------------------------------------------------------------------------
# Redaction: a NEW (blocking) finding's value_sha256 is never printed.
# ---------------------------------------------------------------------------


def test_blocking_finding_omits_value_sha256_baselined_finding_keeps_it() -> None:
    blocking = SecretFinding(
        rule="aws_access_key_id",
        target="source",
        location="a.py:1",
        value_sha256="deadbeef" * 8,
        blocking=True,
    )
    non_blocking = SecretFinding(
        rule="aws_access_key_id",
        target="source",
        location="a.py:1",
        value_sha256="deadbeef" * 8,
        blocking=False,
    )
    check(
        "FTF-1505",
        "a NEW (blocking) finding's to_dict() omits value_sha256 -- an unsalted sha256 of a "
        "low-entropy secret is dictionary-reversible, so printing it for an unreviewed finding "
        "would itself leak",
        blocking.to_dict()["value_sha256"] is None,
    )
    check(
        "FTF-1505",
        "a baselined (already-reviewed, non-blocking) finding keeps its hash",
        non_blocking.to_dict()["value_sha256"] == "deadbeef" * 8,
    )

    def _fault_always_includes_hash(finding: SecretFinding) -> dict[str, object]:
        import dataclasses as _dc

        return _dc.asdict(finding)

    check(
        "FTF-1505",
        "RED (fault reproduced): the old to_dict() shape (plain dataclasses.asdict) would have "
        "printed the hash for a blocking finding too",
        _fault_always_includes_hash(blocking)["value_sha256"] is not None,
    )


# ---------------------------------------------------------------------------
# evaluate()
# ---------------------------------------------------------------------------


def test_evaluate_refuses_zero_scenario_results() -> None:
    raised = False
    try:
        evaluate([])
    except SecretScanError:
        raised = True
    check("FTF-1505", "evaluate refuses to build evidence from zero scanned units", raised)


def test_evaluate_reports_an_all_clean_scan_as_real_non_vacuous_evidence() -> None:
    results = [
        ScenarioResult(
            resource_kind="source",
            check="clean",
            method="SCAN",
            path="a.py",
            status=0,
            expected_denied=True,
            passed=True,
        )
    ]
    evidence = evaluate(results)
    check(
        "FTF-1505",
        "an all-clean scan still produces real evidence (not an error)",
        evidence.scenarios_total == 1,
    )
    check(
        "FTF-1505",
        "zero findings means zero failures, not zero evidence",
        evidence.scenarios_failed == 0,
    )


# ---------------------------------------------------------------------------
# F-SEEDS / F-LOGS: runtime-generated seeds across every rule family; the
# real, already-certified FTF-0904 pipeline; a direct StructuredEvent cross-
# check.
# ---------------------------------------------------------------------------


def test_generate_seed_values_covers_every_rule_fresh_each_call() -> None:
    first = generate_seed_values()
    second = generate_seed_values()
    check("FTF-1505", "generate_seed_values covers every rule in RULES", set(first) == set(RULES))
    check(
        "FTF-1505",
        "every generated seed value actually fires its own rule",
        all(any(r == rule for r, _ in scan_line(value)) for rule, value in first.items()),
    )
    check(
        "FTF-1505",
        "two calls generate DIFFERENT values (runtime-random, never a fixed literal)",
        first != second,
    )


def test_log_sample_fault_an_unwired_logger_leaks_the_seeded_marker() -> None:
    buffer = io.StringIO()
    logger = logging.getLogger("ftf_1505_fault_unwired")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    # Fault: deliberately skip install_redaction(logger) -- no sink wired.
    marker = generate_seed_for_rule("aws_access_key_id")
    logger.info("request completed token=%s status=ok", marker)
    output = buffer.getvalue()
    check(
        "FTF-1505",
        "RED (fault): an unwired logger leaks the seeded AWS-shaped marker verbatim",
        marker in output,
    )
    logger.removeHandler(handler)


def test_log_sample_real_pipeline_detects_and_then_redacts_every_family() -> None:
    seed_pairs = list(generate_seed_values().items())
    findings, results = scan_log_samples(seed_pairs)
    pre_checks = [r for r in results if r.check.startswith("pre_redaction_detects_seeded_shape")]
    post_checks = [r for r in results if r.check.startswith("post_redaction_clean")]
    check(
        "FTF-1505",
        "GREEN: every one of this suite's rule families is detected pre-redaction",
        all(r.passed for r in pre_checks) and len(pre_checks) == len(RULES),
    )
    check(
        "FTF-1505",
        "GREEN: the real, certified FTF-0904 pipeline leaves the sink clean for every family",
        all(r.passed for r in post_checks) and len(post_checks) == len(RULES),
    )
    check(
        "FTF-1505",
        "no blocking finding is raised for the suite's own seeded markers",
        not any(f.blocking for f in findings),
    )


def test_structured_event_cross_check_detects_a_misused_canary() -> None:
    findings, results = scan_structured_event_cross_check()
    check(
        "FTF-1505",
        "GREEN: a canary placed in StructuredEvent.identity and emitted raw is detected by "
        "this suite's own canary check (StructuredEvent has no redaction of its own)",
        all(r.passed for r in results) and not findings,
    )


def test_log_sample_recall_and_control_sink_can_fail() -> None:
    """N14 / F23: a seed that does NOT fire its own rule must fail both the
    pre-redaction recall check and the unredacted control-sink check -- so
    neither check can pass vacuously."""
    _findings, results = scan_log_samples([("aws_access_key_id", "not-a-key-shape")])
    by_check = {r.check: r.passed for r in results}
    check(
        "FTF-1505-LOG",
        "pre-redaction recall fails for a non-firing seed",
        by_check.get("pre_redaction_detects_seeded_shape[aws_access_key_id]") is False,
    )
    check(
        "FTF-1505-LOG",
        "the control-sink positive control fails for a non-firing seed",
        by_check.get("control_sink_detects_seed[aws_access_key_id]") is False,
    )
    _f2, real_results = scan_log_samples()
    control = [r for r in real_results if r.check.startswith("control_sink_detects_seed")]
    check(
        "FTF-1505-LOG",
        "with real seeds the unredacted control sink is detected for every family",
        len(control) == len(RULES) and all(r.passed for r in control),
    )


def test_live_lane_positive_controls_and_error_paths() -> None:
    """F14/F15/F22 and F-LIVE-POSITIVE-CONTROL, run for real: the live lane
    must see uvicorn's stderr startup line, one access-log line per probe, and
    detect the path-placed positive-control canary through the same scan that
    finds nothing else; the OIDC callback's unauthenticated 422 and 400 paths
    are probed and answer as expected."""
    findings, results = ss.scan_live_app_log_samples(ROOT)
    by_check = {r.check: r for r in results}
    check("FTF-1505-LIVE", "the real app booted", by_check["live_app_boot"].passed)
    for name in (
        "live_app_stderr_captured",
        "live_app_scan_detects_positive_control",
        "live_app_output_clean",
        "live_app_status_callback_validation_422",
        "live_app_status_callback_rejected_400",
        "live_app_access_line[callback_validation_422]",
        "live_app_access_line[positive_control]",
    ):
        check(
            "FTF-1505-LIVE",
            f"{name} is present and passed",
            name in by_check and by_check[name].passed,
        )
    access = [r for r in results if r.check.startswith("live_app_access_line[")]
    check(
        "FTF-1505-LIVE",
        f"every probe produced its own access-log line ({len(access)} probes)",
        len(access) > len(RULES) and all(r.passed for r in access),
    )
    check("FTF-1505-LIVE", "no live-output leak", not findings)


def test_seeded_recall_covers_every_lane_and_can_fail() -> None:
    results = ss.scan_seeded_recall()
    lanes = {r.check.split("]")[0].split("[")[1] for r in results}
    check(
        "FTF-1505-RECALL",
        "seeded recall covers source, base64, zip_member, history and artifact lanes",
        lanes == {"source", "base64", "zip_member", "history", "artifact"},
    )
    check(
        "FTF-1505-RECALL",
        "one result per lane per rule family, all found",
        len(results) == 5 * len(RULES) and all(r.passed for r in results),
    )
    with _monkeypatch(ss, "_iter_archive_members", lambda data, name: iter(())):
        blind = ss.scan_seeded_recall()
    check(
        "FTF-1505-RECALL",
        "RED (fault reproduced): a scanner that stops opening archives fails zip_member recall",
        any(not r.passed and "[zip_member]" in r.check for r in blind),
    )


def test_live_lane_helpers_cannot_pass_vacuously() -> None:
    """The live lane's own decisions, pinned on inputs the real app never
    produces: a probe whose access line is missing (only a longer tag that
    contains its name is present) is NOT seen, and a canary other than the
    positive control's exact value IS a leak."""
    lines = ['127.0.0.1:1 - "GET /auth/callback?code=[REDACTED]&probe=error_family_x HTTP/1.1" 400']
    check(
        "FTF-1505-LIVE", "an exact probe tag is seen", ss._access_line_seen("error_family_x", lines)
    )
    check(
        "FTF-1505-LIVE",
        "a probe whose own line is missing is not satisfied by a longer tag containing it",
        not ss._access_line_seen("family_x", lines),
    )
    control = generate_seed_for_rule("canary_leaked")
    other = generate_seed_for_rule("canary_leaked")
    leaks = ss._live_output_leaks([("canary_leaked", control), ("canary_leaked", other)], control)
    check(
        "FTF-1505-LIVE",
        "only the positive control's exact value is exempt; any other canary is a leak",
        leaks == [("canary_leaked", other)],
    )


def test_history_only_tar_gz_member_is_scanned() -> None:
    """A tar.gz is recognised by its name only (no magic check), so the
    history lane must pass the blob's real path, not its `path@sha` label."""
    import tarfile

    tmp = _build_temp_repo()
    seed = generate_seed_for_rule("github_token")
    payload = f"token = {seed}\n".encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("conf/app.ini")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    (tmp / "bundle.tar.gz").write_bytes(buf.getvalue())
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "add tarball", cwd=tmp)
    (tmp / "bundle.tar.gz").unlink()
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "remove tarball", cwd=tmp)
    findings, _ = scan_history(tmp, {})
    check(
        "FTF-1505-HISTORY-ARCHIVE",
        "a secret inside a history-only tar.gz member is found",
        any(
            f.rule == "github_token"
            and "bundle.tar.gz@" in f.location
            and "!conf/app.ini" in f.location
            for f in findings
        ),
    )


def _fake_live_lane(blocking: bool = False):
    def _lane(root: Path, **_kw: object) -> tuple[list[SecretFinding], list[ScenarioResult]]:
        findings = []
        if blocking:
            findings.append(
                SecretFinding(
                    rule="aws_access_key_id",
                    target="log_sample",
                    location="<injected-for-test>",
                    value_sha256="",
                    blocking=True,
                )
            )
        return findings, [
            ScenarioResult(
                resource_kind="log_sample",
                check="injected_for_test",
                method="SCAN",
                path="<injected-for-test>",
                status=0,
                expected_denied=True,
                passed=True,
            )
        ]

    return _lane


_MINIMAL_CI_YML = """jobs:
  x:
    steps:
      - uses: actions/upload-artifact@abc
        with:
          path: reports/thing.json
"""


def _main_repo() -> Path:
    tmp = _build_temp_repo()
    ci = tmp / ".github" / "workflows" / "ci.yml"
    ci.parent.mkdir(parents=True)
    ci.write_text(_MINIMAL_CI_YML, encoding="utf-8")
    (tmp / "README.md").write_text("clean\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "init", cwd=tmp)
    return tmp


def _failing_recall() -> list[ScenarioResult]:
    return [
        ScenarioResult(
            resource_kind="seeded_recall",
            check="seeded_recall[source][aws_access_key_id]",
            method="SCAN",
            path="source/aws_access_key_id.txt:",
            status=1,
            expected_denied=True,
            passed=False,
        )
    ]


def _run_main(
    root: Path, *, live_blocking: bool = False, failing_recall: bool = False
) -> tuple[bool, str]:
    """`main()` for real on `root` with an empty baseline; only the live app
    boot is replaced (it needs this repository's app, not `root`'s)."""
    stderr = io.StringIO()
    stdout = io.StringIO()
    raised = False
    recall = _failing_recall if failing_recall else ss.scan_seeded_recall
    with (
        _monkeypatch(ss, "scan_live_app_log_samples", _fake_live_lane(live_blocking)),
        _monkeypatch(ss, "scan_seeded_recall", recall),
        contextlib.redirect_stderr(stderr),
        contextlib.redirect_stdout(stdout),
    ):
        try:
            ss.main(["--root", str(root), "--baseline", str(root / "no-baseline.yaml")])
        except SecretScanError as exc:
            raised = True
            stderr.write(f"\nraised: {exc}\n")
    return raised, stderr.getvalue() + stdout.getvalue()[-300:]


def test_main_decides_on_every_lane_and_every_completeness_check() -> None:
    """F16/F17/F18/F19/F20/F20b, behavioural: `main()` runs for real on
    disposable repositories. A clean repository passes; each single defect
    below makes it raise, naming the lane or check that caught it."""
    clean_raised, clean_out = _run_main(_main_repo())
    check(
        "FTF-1505-MAIN",
        f"a clean repository passes main() (output tail: {clean_out[-200:]!r})",
        not clean_raised and "0 blocking -- clean" in clean_out,
    )

    seed = generate_seed_for_rule("aws_access_key_id")

    history_repo = _main_repo()
    (history_repo / "old.py").write_text(f'k = "{seed}"\n', encoding="utf-8")
    _run_git("add", "-A", cwd=history_repo)
    _run_git("commit", "-q", "-m", "leak", cwd=history_repo)
    (history_repo / "old.py").unlink()
    _run_git("add", "-A", cwd=history_repo)
    _run_git("commit", "-q", "-m", "remove", cwd=history_repo)
    raised, out = _run_main(history_repo)
    check("FTF-1505-F16", "a history-only secret makes main() raise", raised and "[history]" in out)

    artifact_repo = _main_repo()
    (artifact_repo / "reports").mkdir()
    (artifact_repo / "reports" / "run.json").write_text(f'{{"k": "{seed}"}}\n', encoding="utf-8")
    raised, out = _run_main(artifact_repo)
    check(
        "FTF-1505-F17",
        "an artifact-only (untracked reports/) secret makes main() raise",
        raised and "[artifact]" in out and "[source]" not in out,
    )

    source_repo = _main_repo()
    (source_repo / "docs").mkdir()
    (source_repo / "docs" / "cfg.md").write_text(f'k = "{seed}"\n', encoding="utf-8")
    _run_git("add", "-A", cwd=source_repo)
    _run_git("commit", "-q", "-m", "leak", cwd=source_repo)
    raised, out = _run_main(source_repo)
    check("FTF-1505-MAIN", "a tracked secret makes main() raise", raised and "[source]" in out)

    raised, out = _run_main(_main_repo(), live_blocking=True)
    check(
        "FTF-1505-F18",
        "a blocking finding whose own scenario PASSED still makes main() raise",
        raised and "secret-shaped finding" in out,
    )

    upload_repo = _main_repo()
    (upload_repo / ".github" / "workflows" / "ci.yml").write_text(
        _MINIMAL_CI_YML.replace("reports/thing.json", "build/out.bin"), encoding="utf-8"
    )
    raised, out = _run_main(upload_repo)
    check(
        "FTF-1505-F19",
        "an upload-artifact path outside every scanned root makes main() raise",
        raised and "lies under none of the declared artifact roots" in out,
    )

    dirs_repo = _main_repo()
    hidden = dirs_repo / SOURCE_ROOTS[0]
    hidden.mkdir(parents=True)
    (hidden / "x.py").write_text("x = 1\n", encoding="utf-8")
    (dirs_repo / ".gitignore").write_text(f"/{SOURCE_ROOTS[0]}/\n", encoding="utf-8")
    _run_git("add", "-A", cwd=dirs_repo)
    _run_git("commit", "-q", "-m", "ignore a production dir", cwd=dirs_repo)
    raised, out = _run_main(dirs_repo)
    check(
        "FTF-1505-F20",
        "a production directory with no tracked file makes main() raise",
        raised and "covers zero tracked files" in out,
    )

    raised, out = _run_main(_main_repo(), failing_recall=True)
    check(
        "FTF-1505-RECALL",
        "a failed seeded-recall check makes main() raise",
        raised and "seeded_recall[source][aws_access_key_id]" in out,
    )

    unscanned_repo = _main_repo()
    (unscanned_repo / "README.md").unlink()
    raised, out = _run_main(unscanned_repo)
    check(
        "FTF-1505-F20b",
        "a tracked file the source lane never scanned makes main() raise",
        raised and "were never scanned" in out,
    )


def test_artifact_lane_detects_a_canary() -> None:
    """F21: the artifact lane must apply the FTF-0905 canary rule."""
    tmp = _build_temp_repo()
    (tmp / "reports").mkdir()
    (tmp / "reports" / "out.log").write_text(
        f"line {generate_seed_for_rule('canary_leaked')}\n", encoding="utf-8"
    )
    findings, _ = ss.scan_artifact_roots(tmp, {})
    check(
        "FTF-1505-F21",
        "a canary in an artifact-root file is a blocking canary_leaked finding",
        any(f.rule == "canary_leaked" and f.blocking for f in findings),
    )


def test_f27_aws_access_key_id_matches_both_akia_and_asia() -> None:
    akia = generate_seed_for_rule("aws_access_key_id")
    while not akia.startswith("AKIA"):
        akia = generate_seed_for_rule("aws_access_key_id")
    asia = "ASIA" + akia[4:]
    check(
        "FTF-1505",
        "AKIA-prefixed key matches",
        any(r == "aws_access_key_id" for r, _ in scan_line(akia)),
    )
    check(
        "FTF-1505",
        "ASIA-prefixed (STS) key matches too",
        any(r == "aws_access_key_id" for r, _ in scan_line(asia)),
    )

    import re as _re

    original_rule = sr._SHAPE_RULES["aws_access_key_id"]
    fault_rule = sr._ShapeRule(
        _re.compile(r"(?a)\bAKIA[A-Z2-7]{16}\b"), original_rule.quick_gate_hints
    )
    sr._SHAPE_RULES["aws_access_key_id"] = fault_rule
    try:
        check(
            "FTF-1505",
            "RED (fault reproduced): an AKIA-only pattern misses the ASIA (STS) shape",
            not any(r == "aws_access_key_id" for r, _ in scan_line(asia)),
        )
    finally:
        sr._SHAPE_RULES["aws_access_key_id"] = original_rule
    check(
        "FTF-1505",
        "GREEN (restored): ASIA matches again after the fault is reverted",
        any(r == "aws_access_key_id" for r, _ in scan_line(asia)),
    )


# ---------------------------------------------------------------------------
# F-CI / dependency-light core: secret_rules.py is stdlib-only (no pyyaml,
# no uvicorn chain) so the mutation-gate/ci-verdict jobs can scan their own
# report with no extra setup.
# ---------------------------------------------------------------------------

_THIRD_PARTY_MODULES = {"yaml", "uvicorn", "fastapi", "starlette", "pydantic"}


def test_secret_rules_module_is_stdlib_only() -> None:
    import ast

    tree = ast.parse((ROOT / "tests" / "security" / "secret_rules.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    check(
        "FTF-1505",
        f"secret_rules.py imports no third-party package (found: {sorted(imported)})",
        not (imported & _THIRD_PARTY_MODULES),
    )


def test_secret_rules_cli_scans_a_single_file_with_no_baseline() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp) / "clean.txt"
        clean.write_text("nothing secret-shaped here\n", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(ROOT / "tests" / "security" / "secret_rules.py"),
                str(clean),
            ],
            capture_output=True,
            text=True,
        )
        check(
            "FTF-1505",
            "secret_rules.py's own CLI runs clean with plain -I python3, no site-packages",
            result.returncode == 0,
        )

    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        report.write_text(
            f'{{"note": "{generate_seed_for_rule("aws_access_key_id")}"}}', encoding="utf-8"
        )
        result2 = subprocess.run(
            [
                sys.executable,
                "-I",
                str(ROOT / "tests" / "security" / "secret_rules.py"),
                str(report),
            ],
            capture_output=True,
            text=True,
        )
        check(
            "FTF-1505",
            "secret_rules.py's CLI exits non-zero on a report containing a secret shape, with "
            "no baseline (a freshly generated CI artifact has no reviewed-fixture history)",
            result2.returncode == 1,
        )
        check(
            "FTF-1505",
            "the CLI's own failure output never prints the raw matched value",
            generate_seed_for_rule("aws_access_key_id")[:10] not in result2.stderr
            and "aws_access_key_id" in result2.stderr,
        )


# ---------------------------------------------------------------------------
# End-to-end, against this real repository, exactly as CI's own
# `secret-scanning` job runs it.
# ---------------------------------------------------------------------------


def test_real_module_run_against_this_repository() -> None:
    is_shallow = (
        subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "true"
    )
    if is_shallow:
        print("  (skipped: this sandbox clone is shallow -- CI's own job runs with fetch-depth: 0)")
        return
    result = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "security" / "secret_scanning.py"), "--scope", "pr"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    check(
        "FTF-1505",
        f"a real run against this repository's own history/source/artifacts exits 0 "
        f"(stderr tail: {result.stderr[-1000:]!r})",
        result.returncode == 0,
    )
    check(
        "FTF-1505",
        "the real run reports 0 blocking findings",
        "0 blocking -- clean" in result.stdout,
    )
    check(
        "FTF-1505",
        "the real run covers more than a trivial number of scanned units",
        '"scenarios_total"' in result.stdout,
    )


def main() -> None:
    test_every_rule_fires_on_its_own_generated_seed()
    test_pem_private_key_variants()
    test_canary_shape_is_gated_to_include_canary()
    test_ordinary_identifiers_and_type_annotations_never_fire()
    test_too_short_shapes_do_not_fire()
    test_low_entropy_quoted_value_does_not_fire()
    test_your_placeholder_bypass_is_removed()
    test_url_userinfo_password_placeholder_is_not_flagged()
    test_base64_wrapped_secret_is_found()
    test_hex_wrapped_secret_is_found()
    test_percent_decoded_secret_is_found()
    test_canary_is_detected_through_base64_and_hex_decode_layers()
    test_quick_gate_literals_cover_every_rule_in_the_table()
    test_quick_gate_previously_missed_shapes_now_covered()
    test_quick_gate_is_a_performance_filter_only_never_a_detection_gap()
    test_f13_long_line_window_finds_a_secret_past_the_200_char_boundary()
    test_no_decode_cap_every_candidate_is_decoded()
    test_f13_seam_long_secrets_found_at_every_offset()
    test_jwt_linear_matcher_equals_the_regex()
    test_url_userinfo_linear_matcher_equals_the_regex()
    test_whole_line_scanning_stays_linear_on_pathological_lines()
    test_source_scan_has_no_path_exclusion()
    test_non_ascii_filename_is_scanned_not_silently_skipped()
    test_binary_file_is_scanned()
    test_zip_archive_member_is_scanned()
    test_corrupt_archive_member_does_not_abort_later_members()
    test_archive_member_bomb_safety_caps()
    test_corrupt_deflated_member_is_blocking_and_later_members_still_scanned()
    test_truncated_archives_are_blocking_not_silent()
    test_oversized_archive_member_is_blocking_and_history_names_resolve()
    test_history_blob_over_the_bound_is_blocking()
    test_oversized_content_is_a_blocking_scan_incomplete_finding()
    test_long_line_is_not_silently_skipped()
    test_source_roots_come_from_product_truth_check_not_a_second_list()
    test_artifact_roots_include_reports()
    test_assert_dirs_covered_fails_closed_on_a_real_omission()
    test_assert_scanned_set_matches_tracked_files()
    test_assert_artifact_uploads_covered_fails_closed_on_a_new_root()
    test_upload_artifact_merge_variant_is_covered()
    test_upload_path_traversal_is_refused()
    test_upload_path_far_from_uses_line_is_still_found()
    test_this_repos_ci_yml_artifact_uploads_are_covered()
    test_assert_not_shallow_fails_closed_on_a_real_shallow_clone()
    test_history_finds_a_secret_removed_from_the_working_tree()
    test_history_finds_a_secret_introduced_only_in_a_merge_commit()
    test_history_blob_ids_are_deduplicated()
    test_archive_member_in_a_history_only_blob_is_scanned()
    test_scan_history_pr_scope_excludes_other_refs_all_refs_scope_attributes_them()
    test_baseline_rejects_a_value_path_or_glob_key()
    test_baseline_schema_is_strict()
    test_baseline_rejects_malformed_or_missing_fields()
    test_baseline_fingerprint_waives_every_recurrence_of_the_same_value()
    test_stale_baseline_entry_is_itself_a_blocking_finding()
    test_this_repos_committed_baseline_loads_and_has_justified_entries()
    test_blocking_finding_omits_value_sha256_baselined_finding_keeps_it()
    test_evaluate_refuses_zero_scenario_results()
    test_evaluate_reports_an_all_clean_scan_as_real_non_vacuous_evidence()
    test_generate_seed_values_covers_every_rule_fresh_each_call()
    test_log_sample_fault_an_unwired_logger_leaks_the_seeded_marker()
    test_log_sample_real_pipeline_detects_and_then_redacts_every_family()
    test_structured_event_cross_check_detects_a_misused_canary()
    test_log_sample_recall_and_control_sink_can_fail()
    test_live_lane_positive_controls_and_error_paths()
    test_seeded_recall_covers_every_lane_and_can_fail()
    test_live_lane_helpers_cannot_pass_vacuously()
    test_history_only_tar_gz_member_is_scanned()
    test_main_decides_on_every_lane_and_every_completeness_check()
    test_artifact_lane_detects_a_canary()
    test_f27_aws_access_key_id_matches_both_akia_and_asia()
    test_secret_rules_module_is_stdlib_only()
    test_secret_rules_cli_scans_a_single_file_with_no_baseline()
    test_real_module_run_against_this_repository()

    if FAILURES:
        print(f"FTF-1505 secret scanning tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1505 secret scanning tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
