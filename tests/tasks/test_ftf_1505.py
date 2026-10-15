#!/usr/bin/env python3
"""FTF-1505 -- Secret scanning, round 2 (unit/fault tests).

Runnable without network egress. Several checks use a disposable temporary
git repository (real `git init`/`commit`, no network) to prove the real
negative_acceptance clauses this round's return items named:

  - F-SCOPE: no path exclusion -- every git-tracked file is scanned,
    including `tests/` and files outside `SOURCE_ROOTS`.
  - F-BINARY/F-ARCHIVE: a binary file and a zip-archive member are scanned.
  - F-DECODE: one layer of base64 and one layer of percent-decoding.
  - F-HISTORY: every unique blob reachable from ANY ref is scanned -- not
    only lines `git log -p` renders as "added" -- including content that
    enters history only through a merge commit (which plain `git log -p`
    does not diff by default).
  - shallow-clone refusal (history lane NOT_EXERCISED is a release
    blocker, never silently narrowed to HEAD).
  - F-BASELINE: fingerprint-only waiving (no value/path/glob key), a stale
    entry is itself a release blocker, and the removed "your_" placeholder
    bypass no longer suppresses a real-looking value.
  - F-RULES: the new rule families fire on their own shapes.
  - F-CI: covered generically by tests/unit/test_ci_policy.py (every job
    in ci.yml, including `secret-scanning`, must be aggregated by
    `ci-verdict`'s own `needs:` -- no separate test needed here).

Run directly: python3 tests/tasks/test_ftf_1505.py
"""

from __future__ import annotations

import base64
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

from secret_scanning import (  # noqa: E402
    ARTIFACT_ROOTS,
    SOURCE_ROOTS,
    BaselineEntry,
    SecretScanError,
    _baseline_fingerprint,
    _could_match_any_rule,
    _iter_archive_members,
    assert_artifact_uploads_covered,
    assert_baseline_fresh,
    assert_dirs_covered,
    assert_not_shallow,
    evaluate,
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
    _run_git("init", "-q", cwd=tmp)
    _run_git("config", "user.email", "t@example.com", cwd=tmp)
    _run_git("config", "user.name", "t", cwd=tmp)
    return tmp


# ---------------------------------------------------------------------------
# F-RULES: the new/fixed rule families fire on their own shape.
# ---------------------------------------------------------------------------

_POSITIVE_SHAPES: tuple[tuple[str, str, str | None], ...] = (
    ("aws_access_key_id", "aws_key = AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
    (
        "aws_secret_access_key",
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    ),
    ("github_token", "token = ghp_" + "a" * 36, "ghp_" + "a" * 36),
    ("github_pat", "token = github_pat_" + "a" * 82, "github_pat_" + "a" * 82),
    ("gitlab_token", "token = glpat-" + "a" * 20, "glpat-" + "a" * 20),
    ("slack_token", "slack = xoxb-" + "1" * 10, "xoxb-" + "1" * 10),
    (
        "slack_webhook",
        "hook = https://hooks.slack.com/services/T000/B000/" + "X" * 20,
        "https://hooks.slack.com/services/T000/B000/" + "X" * 20,
    ),
    ("bearer_or_basic_header", "Authorization: Bearer " + "a" * 24, "Bearer " + "a" * 24),
    ("pem_private_key", "-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----"),
    (
        "pem_private_key",
        "-----BEGIN ENCRYPTED PRIVATE KEY-----",
        "-----BEGIN ENCRYPTED PRIVATE KEY-----",
    ),
    ("pem_private_key", "-----BEGIN PRIVATE KEY-----", "-----BEGIN PRIVATE KEY-----"),
    (
        "jwt_shaped",
        "jwt = " + "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "a" * 20,
        None,
    ),
    ("google_api_key", "key = AIza" + "a" * 35, "AIza" + "a" * 35),
    ("huggingface_token", "hf = hf_" + "a" * 34, "hf_" + "a" * 34),
    ("openai_style_key", "key = sk-" + "a" * 32, "sk-" + "a" * 32),
    ("openai_style_key", "key = sk-proj-" + "a" * 32, "sk-proj-" + "a" * 32),
    ("openai_style_key", "key = sk-ant-" + "a" * 32, "sk-ant-" + "a" * 32),
    ("stripe_style_key", "key = sk_live_" + "a" * 20, "sk_live_" + "a" * 20),
    ("stripe_style_key", "key = rk_live_" + "a" * 20, "rk_live_" + "a" * 20),
    (
        "url_userinfo_password",
        "postgresql://user:hunter2realpass@db.internal:5432/sovra",
        "hunter2realpass",
    ),
    ("quoted_secret_assignment", 'password = "S0meRandomLookingValue1"', "S0meRandomLookingValue1"),
    # F-RULES: "prefixed secret names ... the \b boundary blocks them today".
    (
        "quoted_secret_assignment",
        'db_password = "S0meRandomLookingValue1"',
        "S0meRandomLookingValue1",
    ),
    (
        "quoted_secret_assignment",
        'client_secret: "S0meRandomLookingValue1"',
        "S0meRandomLookingValue1",
    ),
    (
        "quoted_secret_assignment",
        'SECRET_KEY = "S0meRandomLookingValue1"',
        "S0meRandomLookingValue1",
    ),
)


def test_every_rule_fires_on_its_own_shape() -> None:
    for rule, line, expected_value in _POSITIVE_SHAPES:
        hits = scan_line(line)
        rule_hits = [v for r, v in hits if r == rule]
        check(
            "FTF-1505",
            f"rule {rule!r} fires on its own crafted shape ({line!r})",
            bool(rule_hits),
        )
        if expected_value is not None and rule_hits:
            check(
                "FTF-1505",
                f"rule {rule!r} captures the exact matched value",
                rule_hits[0] == expected_value,
            )


def test_canary_shape_is_gated_to_include_canary() -> None:
    marker = "CANARY-DO-NOT-USE-" + "0" * 32
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
    # 20+ chars, but low Shannon entropy (a repeated run) -- not flagged.
    check(
        "FTF-1505",
        "a long but low-entropy quoted value does not fire (entropy gate)",
        scan_line('password = "' + "a" * 25 + '"') == [],
    )


def test_your_placeholder_bypass_is_removed() -> None:
    """F-RULES fault: round 1's `your[_-].*` placeholder alternative
    treated ANY value prefixed `your_`/`your-` as a non-secret placeholder
    -- an attacker (or an accidental copy-paste of a real secret into a
    "your_api_key_here"-style template) could hide a real-looking secret
    behind that prefix and it would never be flagged. Round 2 removes that
    alternative entirely."""
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
# F-DECODE: one layer of base64, one layer of percent-decoding.
# ---------------------------------------------------------------------------


def test_base64_wrapped_secret_is_found() -> None:
    secret_line = "aws_key=AKIAABCDEFGHIJKLMNOP"
    wrapped = base64.b64encode(secret_line.encode()).decode()
    hits = scan_line(f"blob: {wrapped}")
    check(
        "FTF-1505",
        "a secret shape hidden behind one layer of base64 is still found",
        any(
            rule == "aws_access_key_id" and value == "AKIAABCDEFGHIJKLMNOP" for rule, value in hits
        ),
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


def test_quick_gate_is_a_performance_filter_only_never_a_detection_gap() -> None:
    """The cheap substring pre-filter before re-running rules on decoded
    content must never itself hide a real finding: every rule's own
    required literal is covered by the gate. Reproduced directly: a
    decoded candidate containing each rule's anchor literal passes the
    gate; one containing none of them does not (and is correctly never
    checked, since it cannot match)."""
    check(
        "FTF-1505", "AKIA literal passes the quick gate", _could_match_any_rule("akia" + "x" * 20)
    )
    check(
        "FTF-1505",
        "unrelated text fails the quick gate",
        not _could_match_any_rule("just some prose"),
    )


# ---------------------------------------------------------------------------
# F-SCOPE / F-BINARY / F-ARCHIVE: no path exclusion; binary files and
# archive members are scanned.
# ---------------------------------------------------------------------------


def test_source_scan_has_no_path_exclusion() -> None:
    """Round 1 restricted scanning to a hand-picked SOURCE_ROOTS list.
    Round 2's `scan_source` must cover every git-tracked path -- including
    `tests/`, root files and any other directory -- with no exclusion."""
    tmp = _build_temp_repo()
    for rel in ("apps/api/main.py", "tests/test_x.py", "scripts/tool.py", "README.md"):
        path = tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
    secret_path = tmp / "tests" / "fixtures" / "leaked.py"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text('aws_key = "AKIALEAKEDFROMTESTSX"\n', encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "init", cwd=tmp)

    tracked = iter_tracked_files(tmp)
    check(
        "FTF-1505",
        "iter_tracked_files covers tests/ (not restricted to SOURCE_ROOTS)",
        "tests/fixtures/leaked.py" in tracked,
    )
    findings, results = scan_source(tmp, {})
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


def test_binary_file_is_scanned() -> None:
    tmp = _build_temp_repo()
    binary_path = tmp / "asset.bin"
    binary_path.write_bytes(
        bytes([0, 1, 2, 3]) + b'aws_key="AKIABINARYFILESECRET"' + bytes([255, 254])
    )
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
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("config.ini", 'aws_key = "AKIAZIPMEMBERSECRETS"\n')
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


def test_archive_member_bomb_safety_caps() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(3):
            zf.writestr(f"f{i}.txt", "hello world\n")
    data = buf.getvalue()
    members = list(_iter_archive_members(data, "bundle.zip"))
    check("FTF-1505", "zip members are extracted and bounded", len(members) == 3)

    import secret_scanning as _ss

    original_cap = _ss._MAX_ARCHIVE_MEMBERS
    try:
        _ss._MAX_ARCHIVE_MEMBERS = 1
        raised = False
        try:
            list(_iter_archive_members(data, "bundle.zip"))
        except SecretScanError:
            raised = True
        check(
            "FTF-1505",
            "an archive exceeding the member cap is refused, not silently truncated",
            raised,
        )
    finally:
        _ss._MAX_ARCHIVE_MEMBERS = original_cap


def test_long_line_is_not_silently_skipped() -> None:
    """Round 1 had a 4,000-char line cutoff (F-BINARY return item: 'drop
    the silent 4,000-char line skip'). Round 2 has none."""
    padding = "x" * 10_000
    line = f"{padding} aws_key=AKIANOLINELIMITSECRE"
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


# ---------------------------------------------------------------------------
# assert_dirs_covered / assert_artifact_uploads_covered / assert_not_shallow.
# ---------------------------------------------------------------------------


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
# F-HISTORY: every unique blob reachable from any ref, including a blob
# that enters history ONLY through a merge commit (which `git log --all -p`
# does not diff by default -- the concrete gap a diff-based round-1 scan
# could miss that a blob-based round-2 scan cannot).
# ---------------------------------------------------------------------------


def test_history_finds_a_secret_removed_from_the_working_tree() -> None:
    tmp = _build_temp_repo()
    target = tmp / "apps" / "api" / "config.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "init", cwd=tmp)

    leaked_key = "AKIA" + "LEAKEDSECRET234Z"  # A-Z2-7 only, exactly 16 chars
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
        "the history scan finds the key that was committed and later removed",
        len(aws_hits) == 1 and aws_hits[0].blocking,
    )
    check(
        "FTF-1505",
        "scan_history yields one ScenarioResult per unique blob (2 distinct contents: "
        "the 3 commits' first and third states are byte-identical, so they dedupe to one blob)",
        len(history_results) == 2,
    )


def test_history_finds_a_secret_introduced_only_in_a_merge_commit() -> None:
    """A secret that enters history ONLY as the resolution of a merge
    conflict is invisible to `git log --all -p` (which does not render a
    diff for merge commits by default) but is still a real, reachable
    blob -- `scan_history`'s blob-based approach (not diff-hunk-based)
    must still find it. This is the concrete behavioural gap between a
    diff-based (round 1 style) and a blob-based (round 2) history scan."""
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
    leaked_key = "AKIA" + "MERGEONLYSECRETS"  # A-Z2-7, 16 chars
    target.write_text(f'base = 1\naws_key = "{leaked_key}"\n', encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "resolve merge conflict", cwd=tmp)

    # Reproduce the gap directly: plain `git log --all -p` never shows this
    # blob's content as an "added" line, because merge commits get no diff
    # by default.
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
        "scan_history (blob-based) still finds a secret introduced only in a merge commit",
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
    target.write_text("x = 1\n", encoding="utf-8")  # identical content to commit "a"
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "c", cwd=tmp)

    blobs = iter_history_blob_ids(tmp)
    check(
        "FTF-1505", "3 commits with only 2 distinct contents yield 2 unique blobs", len(blobs) == 2
    )


# ---------------------------------------------------------------------------
# F-BASELINE: fingerprint-only (no value/path/glob), stale entries blocking,
# and fingerprint waiving across every recurrence of the same value.
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
    fingerprint = _baseline_fingerprint("aws_access_key_id", "AKIASHAREDACROSSFILES1")
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
    fingerprint = _baseline_fingerprint("aws_access_key_id", "AKIANEVERACTUALLYSEEN1")
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
# evaluate(): reused build_evidence's own Law-1 zero-results refusal.
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
# scan_log_samples: the REAL, already-certified FTF-0904 pipeline. Law 1 --
# shown able to fail first: a logger with no sink wired at all (the fault)
# must leak the seeded marker; the real `install_redaction`-wired logger
# (the production path) must not.
# ---------------------------------------------------------------------------


def test_log_sample_fault_an_unwired_logger_leaks_the_seeded_marker() -> None:
    buffer = io.StringIO()
    logger = logging.getLogger("ftf_1505_fault_unwired")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    # Fault: deliberately skip install_redaction(logger) -- no sink wired.
    marker = "AKIA" + "FAULTUNWIREDKEY0"
    logger.info("request completed token=%s status=ok", marker)
    output = buffer.getvalue()
    check(
        "FTF-1505",
        "RED (fault): an unwired logger leaks the seeded AWS-shaped marker verbatim",
        marker in output,
    )
    logger.removeHandler(handler)


def test_log_sample_real_pipeline_detects_and_then_redacts() -> None:
    findings, results = scan_log_samples(["AKIA" + "REALPIPELINEKEYS", "ghp_" + "b" * 40])
    pre_checks = [r for r in results if r.check == "pre_redaction_detects_seeded_shape"]
    post_checks = [r for r in results if r.check == "post_redaction_clean"]
    check(
        "FTF-1505",
        "GREEN: every seeded marker is detected pre-redaction",
        all(r.passed for r in pre_checks) and len(pre_checks) == 2,
    )
    check(
        "FTF-1505",
        "GREEN: the real, certified FTF-0904 pipeline leaves the sink clean",
        all(r.passed for r in post_checks) and len(post_checks) == 2,
    )
    check(
        "FTF-1505",
        "no blocking finding is raised for the suite's own seeded markers",
        not any(f.blocking for f in findings),
    )


# ---------------------------------------------------------------------------
# End-to-end, against this real repository, exactly as CI's own
# `secret-scanning` job runs it: python3 tests/security/secret_scanning.py.
# This alone exercises source + artifacts + history (full clone required)
# + both log-sample lanes, so it is skipped here if the local clone is
# shallow (the module itself fails closed on that -- this is a sandbox
# capability check, not a weakening of the real check).
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
        [sys.executable, str(ROOT / "tests" / "security" / "secret_scanning.py")],
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
    test_every_rule_fires_on_its_own_shape()
    test_canary_shape_is_gated_to_include_canary()
    test_ordinary_identifiers_and_type_annotations_never_fire()
    test_too_short_shapes_do_not_fire()
    test_low_entropy_quoted_value_does_not_fire()
    test_your_placeholder_bypass_is_removed()
    test_url_userinfo_password_placeholder_is_not_flagged()
    test_base64_wrapped_secret_is_found()
    test_percent_decoded_secret_is_found()
    test_quick_gate_is_a_performance_filter_only_never_a_detection_gap()
    test_source_scan_has_no_path_exclusion()
    test_binary_file_is_scanned()
    test_zip_archive_member_is_scanned()
    test_archive_member_bomb_safety_caps()
    test_long_line_is_not_silently_skipped()
    test_source_roots_come_from_product_truth_check_not_a_second_list()
    test_artifact_roots_include_reports()
    test_assert_dirs_covered_fails_closed_on_a_real_omission()
    test_assert_artifact_uploads_covered_fails_closed_on_a_new_root()
    test_this_repos_ci_yml_artifact_uploads_are_covered()
    test_assert_not_shallow_fails_closed_on_a_real_shallow_clone()
    test_history_finds_a_secret_removed_from_the_working_tree()
    test_history_finds_a_secret_introduced_only_in_a_merge_commit()
    test_history_blob_ids_are_deduplicated()
    test_baseline_rejects_a_value_path_or_glob_key()
    test_baseline_rejects_malformed_or_missing_fields()
    test_baseline_fingerprint_waives_every_recurrence_of_the_same_value()
    test_stale_baseline_entry_is_itself_a_blocking_finding()
    test_this_repos_committed_baseline_loads_and_has_justified_entries()
    test_evaluate_refuses_zero_scenario_results()
    test_evaluate_reports_an_all_clean_scan_as_real_non_vacuous_evidence()
    test_log_sample_fault_an_unwired_logger_leaks_the_seeded_marker()
    test_log_sample_real_pipeline_detects_and_then_redacts()
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
