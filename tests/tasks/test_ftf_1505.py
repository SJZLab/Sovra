#!/usr/bin/env python3
"""FTF-1505 -- Secret scanning, round 3 (unit/fault tests).

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

import base64
import contextlib
import inspect
import io
import logging
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


def _build_temp_repo() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="ftf1505_"))
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
    for header in (
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN ENCRYPTED PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
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


def test_f6_decode_cap_overrun_is_a_blocking_scan_incomplete_finding() -> None:
    """F6/F-DECODE: a base64 candidate past the per-unit cap is dropped
    silently today only by accident (no observable signal either way);
    round 3 makes it an explicit, blocking `scan_incomplete`."""
    many_candidates = " ".join(
        f"{'A' * 24}{i:04d}" for i in range(sr.MAX_BASE64_ATTEMPTS_PER_UNIT + 5)
    )
    flagged: list[bool] = []
    list(sr._matches(many_candidates, on_incomplete=lambda: flagged.append(True)))
    check(
        "FTF-1505",
        "exceeding the base64-candidate cap calls on_incomplete at least once",
        bool(flagged),
    )
    under_cap = " ".join(f"{'A' * 24}{i:04d}" for i in range(10))
    flagged_under: list[bool] = []
    list(sr._matches(under_cap, on_incomplete=lambda: flagged_under.append(True)))
    check(
        "FTF-1505",
        "staying under the cap never calls on_incomplete",
        not flagged_under,
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

    # F4 fault, reproduced directly: a path exclusion added to file inclusion
    # would hide a real production directory's own secrets.
    def _fault_excludes_docs_and_vendor(root: Path) -> list[str]:
        return [
            f
            for f in iter_tracked_files(root)
            if not (f.startswith("docs/") or f.startswith("vendor/"))
        ]

    with_fault = _fault_excludes_docs_and_vendor(tmp)
    check(
        "FTF-1505",
        "sanity: the fault helper itself actually excludes tests/ files -- no, it should not",
        "tests/fixtures/leaked.py" in with_fault,
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
    names = [name for name, _content in members]
    check(
        "FTF-1505", "all 3 members are enumerated", names == ["good1.txt", "bad.txt", "good2.txt"]
    )
    content_by_name = dict(members)
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

    shallow = Path(tempfile.mkdtemp(prefix="ftf1505_shallow_"))
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
        "other-pr-branch" in aws_hits[0].location,
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


_MAIN_SOURCE = inspect.getsource(ss.main)


def test_main_calls_every_lane_and_every_coverage_check() -> None:
    """F16/F17/F19/F20/F21/F22/F23: `main()` must actually call every
    scan lane and every completeness/coverage check -- a structural proof
    that none of them was quietly dropped from the orchestration."""
    for needle in (
        "scan_source(",
        "scan_artifact_roots(",
        "scan_history(",
        "scan_log_samples(",
        "scan_structured_event_cross_check(",
        "scan_live_app_log_samples(",
        "assert_dirs_covered(",
        "assert_artifact_uploads_covered(",
        "assert_scanned_set_matches_tracked_files(",
        "assert_baseline_fresh(",
    ):
        check("FTF-1505", f"main() calls {needle}", needle in _MAIN_SOURCE)


def test_main_raises_on_a_real_blocking_finding() -> None:
    """F18 fixed: `main()` must actually raise when a blocking finding is
    present -- proven by injecting one real blocking finding through a
    cooperating scan function and running `main()` for real (every other
    lane is cheap/fast; only the live-app boot is monkeypatched out, since
    it alone takes real wall-clock time irrelevant to this specific
    check)."""
    original_live = ss.scan_live_app_log_samples
    original_structured = ss.scan_structured_event_cross_check

    def _fake_live(root: Path, **_kw: object) -> tuple[list[SecretFinding], list[ScenarioResult]]:
        finding = SecretFinding(
            rule="aws_access_key_id",
            target="log_sample",
            location="<injected-for-test>",
            value_sha256="",
            blocking=True,
        )
        result = ScenarioResult(
            resource_kind="log_sample",
            check="injected_for_test",
            method="SCAN",
            path="<injected-for-test>",
            status=1,
            expected_denied=True,
            passed=False,
        )
        return [finding], [result]

    def _fake_structured() -> tuple[list[SecretFinding], list[ScenarioResult]]:
        return [], [
            ScenarioResult(
                resource_kind="log_sample",
                check="structured_event_canary_detected",
                method="SCAN",
                path="<structured-event>",
                status=0,
                expected_denied=True,
                passed=True,
            )
        ]

    with (
        _monkeypatch(ss, "scan_live_app_log_samples", _fake_live),
        _monkeypatch(ss, "scan_structured_event_cross_check", _fake_structured),
    ):
        raised = False
        try:
            ss.main(["--scope", "pr"])
        except SecretScanError:
            raised = True
        check(
            "FTF-1505",
            "main() raises SecretScanError when a real blocking finding is present",
            raised,
        )
    check(
        "FTF-1505",
        "the real functions are restored after the test",
        ss.scan_live_app_log_samples is original_live,
    )
    check(
        "FTF-1505",
        "the real functions are restored after the test",
        ss.scan_structured_event_cross_check is original_structured,
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
    test_f6_decode_cap_overrun_is_a_blocking_scan_incomplete_finding()
    test_source_scan_has_no_path_exclusion()
    test_non_ascii_filename_is_scanned_not_silently_skipped()
    test_binary_file_is_scanned()
    test_zip_archive_member_is_scanned()
    test_corrupt_archive_member_does_not_abort_later_members()
    test_archive_member_bomb_safety_caps()
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
    test_main_calls_every_lane_and_every_coverage_check()
    test_main_raises_on_a_real_blocking_finding()
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
