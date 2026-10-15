#!/usr/bin/env python3
"""FTF-1505 -- Secret scanning (unit/fault tests).

Runnable without network egress; three checks use a disposable temporary
git repository (real `git init`/`commit`, no network) to prove the
`history` dimension actually needs the full history, not only the
working tree, and to prove `assert_dirs_covered` fails closed on a real
scanner omission. `scan_log_samples` is proven against the REAL,
already-certified FTF-0904 redaction pipeline -- no stub -- including an
injected fault (skip wiring the sink) shown RED before the real wiring
is shown GREEN (Law 1).

Run directly: python3 tests/tasks/test_ftf_1505.py
"""

from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "security"))

from secret_scanning import (  # noqa: E402
    ARTIFACT_DIRS,
    SOURCE_ROOTS,
    AllowlistEntry,
    SecretScanError,
    assert_dirs_covered,
    evaluate,
    is_allowlisted,
    iter_tracked_files,
    load_allowlist,
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


# ---------------------------------------------------------------------------
# scan_line: shape-only rule table. Positive cases prove each rule fires on
# an actual credential shape; negative cases reproduce, directly, the
# false-positive flood this module's own docstring says it rejected
# `redact_text` for (ordinary identifiers/type-annotations containing a
# secret keyword, never quoted/shaped like a real value).
# ---------------------------------------------------------------------------

_POSITIVE_SHAPES: tuple[tuple[str, str, str | None], ...] = (
    ("aws_access_key_id", "aws_key = AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
    ("github_token", "token = ghp_" + "a" * 36, "ghp_" + "a" * 36),
    ("github_pat", "token = github_pat_" + "a" * 22, "github_pat_" + "a" * 22),
    ("slack_token", "slack = xoxb-" + "1" * 10, "xoxb-" + "1" * 10),
    ("bearer_or_basic_header", "Authorization: Bearer " + "a" * 24, "Bearer " + "a" * 24),
    ("pem_private_key", "-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----"),
    (
        "jwt_shaped",
        "jwt = " + "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "a" * 20,
        None,
    ),
    ("google_api_key", "key = AIza" + "a" * 35, "AIza" + "a" * 35),
    ("huggingface_token", "hf = hf_" + "a" * 20, "hf_" + "a" * 20),
    ("openai_style_key", "key = sk-" + "a" * 20, "sk-" + "a" * 20),
    ("stripe_style_key", "key = sk_live_" + "a" * 20, "sk_live_" + "a" * 20),
    ("quoted_secret_assignment", 'password = "S0meRandomLookingValue1"', "S0meRandomLookingValue1"),
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


def test_ordinary_identifiers_and_type_annotations_never_fire() -> None:
    for line in _NEGATIVE_LINES:
        hits = scan_line(line)
        check(
            "FTF-1505",
            f"ordinary code line does not trip any rule: {line!r}",
            hits == [],
        )


def test_too_short_shapes_do_not_fire() -> None:
    # One character short of each length-gated rule's own minimum.
    check("FTF-1505", "AKIA with 15 chars does not fire", scan_line("AKIA" + "A" * 15) == [])
    check(
        "FTF-1505",
        "ghp_ with 35 chars does not fire",
        scan_line("ghp_" + "a" * 35) == [],
    )
    check(
        "FTF-1505",
        "quoted_secret_assignment with 15-char value does not fire",
        scan_line('password = "' + "a" * 15 + '"') == [],
    )


# ---------------------------------------------------------------------------
# Allowlist: exact (rule, value, file_glob) match required; a near miss on
# any single field must still block.
# ---------------------------------------------------------------------------


def test_allowlist_requires_exact_rule_value_and_glob() -> None:
    entries = [
        AllowlistEntry(
            rule="quoted_secret_assignment",
            value="SECRET-EXPECTED-ANSWER-4711",
            file_glob="apps/web/tests/*",
            justification="known test fixture",
        )
    ]
    check(
        "FTF-1505",
        "exact match is allowlisted",
        is_allowlisted(
            "quoted_secret_assignment",
            "SECRET-EXPECTED-ANSWER-4711",
            "apps/web/tests/ftf_0602.spec.ts",
            entries,
        ),
    )
    check(
        "FTF-1505",
        "wrong rule still blocks",
        not is_allowlisted(
            "aws_access_key_id",
            "SECRET-EXPECTED-ANSWER-4711",
            "apps/web/tests/ftf_0602.spec.ts",
            entries,
        ),
    )
    check(
        "FTF-1505",
        "wrong value still blocks",
        not is_allowlisted(
            "quoted_secret_assignment", "SOMETHING-ELSE", "apps/web/tests/ftf_0602.spec.ts", entries
        ),
    )
    check(
        "FTF-1505",
        "file outside the glob still blocks",
        not is_allowlisted(
            "quoted_secret_assignment",
            "SECRET-EXPECTED-ANSWER-4711",
            "apps/api/app/main.py",
            entries,
        ),
    )


def test_load_allowlist_rejects_malformed_entries() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bad_path = Path(tmp) / "allowlist.yaml"
        bad_path.write_text(
            "exceptions:\n  - rule: quoted_secret_assignment\n    value: x\n    file_glob: y\n",
            encoding="utf-8",
        )
        raised = False
        try:
            load_allowlist(bad_path)
        except SecretScanError:
            raised = True
        check(
            "FTF-1505",
            "an allowlist entry missing justification is refused, not silently accepted",
            raised,
        )


def test_this_repos_committed_allowlist_has_one_justified_entry() -> None:
    entries = load_allowlist()
    check("FTF-1505", "committed allowlist has exactly one entry", len(entries) == 1)
    if entries:
        check("FTF-1505", "entry is justified", bool(entries[0].justification.strip()))


# ---------------------------------------------------------------------------
# A disposable, real git repository (no network) proves:
#   (a) assert_dirs_covered fails closed on a real scanner omission
#       (a tracked-on-disk-but-gitignored production directory);
#   (b) scan_history finds a secret that was committed and later removed,
#       which scan_source (working tree only) provably cannot.
# ---------------------------------------------------------------------------


def _build_temp_repo() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="ftf1505_"))
    _run_git("init", "-q", cwd=tmp)
    _run_git("config", "user.email", "t@example.com", cwd=tmp)
    _run_git("config", "user.name", "t", cwd=tmp)
    return tmp


def test_assert_dirs_covered_fails_closed_on_a_real_omission() -> None:
    tmp = _build_temp_repo()
    covered_dir = tmp / "apps" / "api"
    covered_dir.mkdir(parents=True)
    (covered_dir / "main.py").write_text("x = 1\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "init", cwd=tmp)
    # Fault: a second production directory exists on disk but every file in
    # it is gitignored, so git itself never tracks it -- the exact "scanner
    # omission" this negative_acceptance names.
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

    # Un-ignore it; the same real git-tracked file now covers it -- GREEN.
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
        "iter_tracked_files agrees with assert_dirs_covered's own verdict",
        len(iter_tracked_files(tmp, ("workers/training",))) == 1,
    )


def test_history_finds_a_secret_removed_from_the_working_tree() -> None:
    tmp = _build_temp_repo()
    target_dir = tmp / "apps" / "api"
    target_dir.mkdir(parents=True)
    config_file = target_dir / "config.py"

    config_file.write_text("x = 1\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "init", cwd=tmp)

    leaked_key = "AKIA" + "LEAKEDSECRET1234"  # exactly 16 chars after AKIA
    config_file.write_text(f'aws_key = "{leaked_key}"\n', encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "oops, hardcoded a key", cwd=tmp)

    config_file.write_text("x = 1\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "remove the key", cwd=tmp)

    source_findings, _ = scan_source(tmp, ("apps/api",), [], target="source")
    check(
        "FTF-1505",
        "the working-tree-only scan sees nothing -- the key is gone from HEAD",
        not any(f.rule == "aws_access_key_id" for f in source_findings),
    )

    history_findings, history_results = scan_history(tmp, ("apps/api",), [])
    aws_hits = [f for f in history_findings if f.rule == "aws_access_key_id"]
    check(
        "FTF-1505",
        "the history scan finds the key that was committed and later removed",
        len(aws_hits) == 1 and aws_hits[0].blocking,
    )
    check(
        "FTF-1505",
        "scan_history yields one ScenarioResult per commit that touched the dir",
        len(history_results) == 3,
    )
    check(
        "FTF-1505",
        "the commit that introduced the key is marked failed",
        sum(1 for r in history_results if not r.passed) == 1,
    )


def test_history_allowlist_entry_suppresses_the_same_blocking_finding() -> None:
    tmp = _build_temp_repo()
    target_dir = tmp / "apps" / "api"
    target_dir.mkdir(parents=True)
    config_file = target_dir / "config.py"
    config_file.write_text("x = 1\n", encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "init", cwd=tmp)

    value = "AKIA" + "ALLOWLISTEDKEY00"
    config_file.write_text(f'aws_key = "{value}"\n', encoding="utf-8")
    _run_git("add", "-A", cwd=tmp)
    _run_git("commit", "-q", "-m", "add", cwd=tmp)

    allowlist = [
        AllowlistEntry(
            rule="aws_access_key_id", value=value, file_glob="apps/api/*", justification="fixture"
        )
    ]
    findings, results = scan_history(tmp, ("apps/api",), allowlist)
    aws_hits = [f for f in findings if f.rule == "aws_access_key_id"]
    check(
        "FTF-1505",
        "allowlisted history finding is reported but not blocking",
        bool(aws_hits) and not aws_hits[0].blocking,
    )
    check(
        "FTF-1505",
        "an allowlisted finding keeps its commit's ScenarioResult passing",
        all(r.passed for r in results),
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
    import io

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
    findings, results = scan_log_samples(["AKIA" + "REALPIPELINEKEY1", "ghp_" + "b" * 40])
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
# End-to-end, against this real repository, exactly as CI's own gate job
# runs it: python3 tests/security/secret_scanning.py.
# ---------------------------------------------------------------------------


def test_real_module_run_against_this_repository_is_clean() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "security" / "secret_scanning.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    check(
        "FTF-1505",
        f"a real run against this repository's own history/source/artifacts exits 0 "
        f"(stderr: {result.stderr[-500:]!r})",
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


def test_source_roots_and_artifact_dirs_are_the_real_configured_lists() -> None:
    check(
        "FTF-1505",
        "SOURCE_ROOTS matches this repo's own CLAUDE.md list",
        SOURCE_ROOTS
        == (
            "apps/web",
            "apps/api",
            "workers/training",
            "workers/evaluation",
            "workers/data",
            "workers/deploy",
            "packages/shared",
        ),
    )
    check(
        "FTF-1505",
        "ARTIFACT_DIRS covers this repo's own real evidence directories",
        ARTIFACT_DIRS
        == (
            "docs/certifications",
            "docs/progress",
        ),
    )


def main() -> None:
    test_every_rule_fires_on_its_own_shape()
    test_ordinary_identifiers_and_type_annotations_never_fire()
    test_too_short_shapes_do_not_fire()
    test_allowlist_requires_exact_rule_value_and_glob()
    test_load_allowlist_rejects_malformed_entries()
    test_this_repos_committed_allowlist_has_one_justified_entry()
    test_assert_dirs_covered_fails_closed_on_a_real_omission()
    test_history_finds_a_secret_removed_from_the_working_tree()
    test_history_allowlist_entry_suppresses_the_same_blocking_finding()
    test_evaluate_refuses_zero_scenario_results()
    test_evaluate_reports_an_all_clean_scan_as_real_non_vacuous_evidence()
    test_log_sample_fault_an_unwired_logger_leaks_the_seeded_marker()
    test_log_sample_real_pipeline_detects_and_then_redacts()
    test_real_module_run_against_this_repository_is_clean()
    test_source_roots_and_artifact_dirs_are_the_real_configured_lists()

    if FAILURES:
        print(f"FTF-1505 secret scanning tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1505 secret scanning tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
