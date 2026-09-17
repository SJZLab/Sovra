#!/usr/bin/env python3
"""
FTF-H028 -- Trusted CI and exact-revision release evidence gate.

Proves:
  1. `GateResult` structurally forbids a `BLOCKED_EXTERNAL` result with no
     `owner`/`next_action` -- the same anti-fabrication guardrail FTF-H002's
     `PreflightResult` already established.
  2. `check_release_evidence_report` fails closed on a missing check, a
     stale/mismatched `head_sha`, an incomplete run, and a non-success
     conclusion (skipped/cancelled/timed_out/neutral/failure) -- and passes
     only when every expected check genuinely completed successfully on the
     exact release SHA. No token -> BLOCKED_EXTERNAL, never a fabricated
     PASS.
  3. `check_branch_protection` fails closed the same way, proven against
     three real-shaped fixtures (403/404 permission denial, a 200 response
     missing required settings, a 200 response with every required setting
     enabled) -- plus one *live* call against the real
     `api.github.com/repos/SJZLab/Sovra/branches/main/protection` endpoint,
     confirming this session's own real credential is rejected today
     (matching CLAUDE.md's `BRANCH_PROTECTION: instruction-tier` note), not
     merely asserting the fixture behavior in isolation.
  4. `check_release_manifest_signature` rejects a missing manifest (BLOCKED),
     an unsigned manifest (FAIL), a signed manifest with no configured
     verifier (BLOCKED -- presence of a signature is never treated as
     validity), and correctly branches PASS/FAIL against an injected
     verifier for a valid vs. tampered signature.
  5. `check_required_workflows_have_no_forbidden_patterns` catches each of
     Section 52's forbidden patterns (`continue-on-error: true`, `|| true`,
     a forced `; exit 0`, a `test.only` focus marker) in a real fixture
     workflow file, and the real, currently-shipped `.github/workflows/*.yml`
     files in this repository pass clean.
  6. `check_no_untrusted_pull_request_target_checkout` catches a
     `pull_request_target`-triggered workflow that checks out
     `github.event.pull_request.head` in a real fixture, and the real
     shipped workflow files contain no such pattern -- correctly handling
     PyYAML's `on:` -> boolean-`True` parsing quirk (confirmed empirically
     against the real `ci.yml`, not assumed).
  7. `overall_status`/`run_release_evidence_gate` compose all five checks and
     report BLOCKED_EXTERNAL (not PASS) whenever any check is blocked or
     failed -- proven with a genuine fault injection against the shipped
     module.

Run directly: python3 tests/tasks/test_ftf_h028.py
"""

from __future__ import annotations

import copy
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.release_evidence_gate import (  # noqa: E402
    GateResult,
    GateStatus,
    check_branch_protection,
    check_no_untrusted_pull_request_target_checkout,
    check_release_evidence_report,
    check_release_manifest_signature,
    check_required_workflows_have_no_forbidden_patterns,
    overall_status,
    run_release_evidence_gate,
)

FAILURES = []


def check(criterion_id, description, condition):
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _fake_fetcher(status_code, body, raw="synthetic fixture"):
    def fetcher(url, token):
        return status_code, body, raw

    return fetcher


# ---------------------------------------------------------------------------
# GateResult anti-fabrication guardrail
# ---------------------------------------------------------------------------


def test_blocked_external_requires_owner_and_next_action():
    try:
        GateResult(key="x", status=GateStatus.BLOCKED_EXTERNAL, detail="d")
        raised = False
    except ValueError:
        raised = True
    check(
        "FTF-H028",
        "a BLOCKED_EXTERNAL GateResult with no owner/next_action is structurally rejected",
        raised,
    )

    try:
        GateResult(
            key="x",
            status=GateStatus.BLOCKED_EXTERNAL,
            detail="d",
            owner="   ",
            next_action="do something",
        )
        raised_blank = False
    except ValueError:
        raised_blank = True
    check("FTF-H028", "a blank owner is rejected too", raised_blank)

    result = GateResult(
        key="x",
        status=GateStatus.BLOCKED_EXTERNAL,
        detail="d",
        owner="someone",
        next_action="do something",
    )
    check("FTF-H028", "a well-formed BLOCKED_EXTERNAL result constructs", result.key == "x")

    try:
        GateResult(key="x", status=GateStatus.PASS, detail="")
        raised_empty_detail = False
    except ValueError:
        raised_empty_detail = True
    check("FTF-H028", "an empty detail is always rejected, even for PASS", raised_empty_detail)


# ---------------------------------------------------------------------------
# check_release_evidence_report
# ---------------------------------------------------------------------------

SHA = "a" * 40


def _check_run(
    name, head_sha=SHA, status="completed", conclusion="success", started_at="2026-09-17T00:00:00Z"
):
    return {
        "name": name,
        "head_sha": head_sha,
        "status": status,
        "conclusion": conclusion,
        "started_at": started_at,
    }


def test_release_evidence_report_no_token_is_blocked_not_fabricated_pass():
    result = check_release_evidence_report(owner="o", repo="r", sha=SHA, token=None)
    check(
        "FTF-H028",
        "no token available reports BLOCKED_EXTERNAL, never a fabricated PASS",
        result.status is GateStatus.BLOCKED_EXTERNAL,
    )


def test_release_evidence_report_missing_check_fails():
    body = {"check_runs": [_check_run("gate")]}
    result = check_release_evidence_report(
        owner="o",
        repo="r",
        sha=SHA,
        token="tok",
        expected_checks=("gate", "docker-local-stack-integration"),
        fetcher=_fake_fetcher(200, body),
    )
    check(
        "FTF-H028",
        "a required check with no report at all fails, not passes",
        result.status is GateStatus.FAIL and "missing" in result.detail,
    )


def test_release_evidence_report_stale_head_sha_fails():
    other_sha = "b" * 40
    body = {"check_runs": [_check_run("gate", head_sha=other_sha)]}
    result = check_release_evidence_report(
        owner="o",
        repo="r",
        sha=SHA,
        token="tok",
        expected_checks=("gate",),
        fetcher=_fake_fetcher(200, body),
    )
    check(
        "FTF-H028",
        "a report for a different commit (stale) is rejected, not silently accepted",
        result.status is GateStatus.FAIL and "stale" in result.detail,
    )


def test_release_evidence_report_incomplete_run_fails():
    body = {"check_runs": [_check_run("gate", status="in_progress", conclusion=None)]}
    result = check_release_evidence_report(
        owner="o",
        repo="r",
        sha=SHA,
        token="tok",
        expected_checks=("gate",),
        fetcher=_fake_fetcher(200, body),
    )
    check(
        "FTF-H028",
        "an in-progress (incomplete) run is not treated as passing",
        result.status is GateStatus.FAIL and "incomplete" in result.detail,
    )


def test_release_evidence_report_non_passing_conclusions_all_fail():
    for conclusion in (
        "skipped",
        "cancelled",
        "timed_out",
        "action_required",
        "stale",
        "neutral",
        "failure",
    ):
        body = {"check_runs": [_check_run("gate", conclusion=conclusion)]}
        result = check_release_evidence_report(
            owner="o",
            repo="r",
            sha=SHA,
            token="tok",
            expected_checks=("gate",),
            fetcher=_fake_fetcher(200, body),
        )
        check(
            "FTF-H028",
            f"conclusion={conclusion!r} is rejected, not treated as a pass",
            result.status is GateStatus.FAIL,
        )


def test_release_evidence_report_real_pass_case():
    body = {"check_runs": [_check_run("gate"), _check_run("docker-local-stack-integration")]}
    result = check_release_evidence_report(
        owner="o",
        repo="r",
        sha=SHA,
        token="tok",
        expected_checks=("gate", "docker-local-stack-integration"),
        fetcher=_fake_fetcher(200, body),
    )
    check(
        "FTF-H028",
        "every required check completed successfully on the exact SHA -> PASS",
        result.status is GateStatus.PASS,
    )


def test_release_evidence_report_api_error_is_blocked():
    result = check_release_evidence_report(
        owner="o",
        repo="r",
        sha=SHA,
        token="tok",
        expected_checks=("gate",),
        fetcher=_fake_fetcher(500, None),
    )
    check(
        "FTF-H028",
        "an API error (not 200) reports BLOCKED_EXTERNAL, never a fabricated PASS",
        result.status is GateStatus.BLOCKED_EXTERNAL,
    )


# ---------------------------------------------------------------------------
# check_branch_protection
# ---------------------------------------------------------------------------


def test_branch_protection_no_token_is_blocked():
    result = check_branch_protection(owner="o", repo="r", branch="main", token=None)
    check(
        "FTF-H028",
        "no token available reports BLOCKED_EXTERNAL for branch protection too",
        result.status is GateStatus.BLOCKED_EXTERNAL,
    )


def test_branch_protection_403_is_blocked_external_not_fabricated_pass():
    result = check_branch_protection(
        owner="o",
        repo="r",
        branch="main",
        token="tok",
        fetcher=_fake_fetcher(403, None, "Resource not accessible"),
    )
    check(
        "FTF-H028",
        "a 403 (permission denial) reports BLOCKED_EXTERNAL, never a fabricated PASS or FAIL",
        result.status is GateStatus.BLOCKED_EXTERNAL,
    )


def test_branch_protection_missing_settings_fails():
    body = {
        "required_pull_request_reviews": None,
        "required_status_checks": {"contexts": []},
        "allow_force_pushes": {"enabled": True},
        "allow_deletions": {"enabled": True},
    }
    result = check_branch_protection(
        owner="o", repo="r", branch="main", token="tok", fetcher=_fake_fetcher(200, body)
    )
    check(
        "FTF-H028",
        "a real 200 response missing required settings fails, not passes",
        result.status is GateStatus.FAIL
        and "required_pull_request_reviews" in result.detail
        and "force" in result.detail
        and "deletion" in result.detail,
    )


def test_branch_protection_all_required_settings_pass():
    body = {
        "required_pull_request_reviews": {"required_approving_review_count": 1},
        "required_status_checks": {"contexts": ["release-qualification"]},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
    }
    result = check_branch_protection(
        owner="o", repo="r", branch="main", token="tok", fetcher=_fake_fetcher(200, body)
    )
    check(
        "FTF-H028",
        "a real 200 response with every required setting enabled passes",
        result.status is GateStatus.PASS,
    )


def test_branch_protection_live_probe_is_denied_today():
    """Not a fixture -- a real, live network call to the real GitHub API,
    proving this session's real credential lacks Administration:read on the
    real repository today (the documented, reproducible finding this
    module's own docstring cites). If GitHub ever grants this permission,
    this specific assertion should be revisited -- it is deliberately
    scoped to today's real, observed state, not an assumption."""
    import os
    import urllib.error
    import urllib.request

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        check(
            "FTF-H028",
            "no GITHUB_TOKEN in this environment -- live branch-protection probe skipped "
            "(fixture-level coverage above still proves the function's branching logic)",
            True,
        )
        return
    request = urllib.request.Request(  # noqa: S310
        "https://api.github.com/repos/SJZLab/Sovra/branches/main/protection",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    try:
        urllib.request.urlopen(request, timeout=15)  # noqa: S310
        denied = False
    except urllib.error.HTTPError as exc:
        denied = exc.code in (403, 404)
    except urllib.error.URLError:
        # Network unreachable from this environment -- not a claim either way.
        check(
            "FTF-H028",
            "live branch-protection probe: network unreachable, not asserting a result",
            True,
        )
        return
    check(
        "FTF-H028",
        "live probe against the real GitHub API confirms this credential is denied "
        "branch-protection read today, matching CLAUDE.md's BRANCH_PROTECTION note",
        denied,
    )


# ---------------------------------------------------------------------------
# check_release_manifest_signature
# ---------------------------------------------------------------------------


def test_signature_no_manifest_is_blocked():
    result = check_release_manifest_signature(None)
    check(
        "FTF-H028",
        "no manifest at all reports BLOCKED_EXTERNAL",
        result.status is GateStatus.BLOCKED_EXTERNAL,
    )


def test_signature_unsigned_manifest_fails():
    result = check_release_manifest_signature({"payload": "x"})
    check(
        "FTF-H028",
        "a manifest with no signature field fails closed (Q26: unsigned reports cannot pass)",
        result.status is GateStatus.FAIL,
    )


def test_signature_present_with_no_verifier_is_blocked_not_pass():
    result = check_release_manifest_signature({"payload": "x", "signature": "deadbeef"})
    check(
        "FTF-H028",
        "a present signature with no configured trusted verifier is BLOCKED, "
        "never treated as valid from presence alone",
        result.status is GateStatus.BLOCKED_EXTERNAL,
    )


def test_signature_valid_and_tampered_branches():
    def verifier(payload, signature):
        return signature == f"valid-sig-for:{payload}"

    valid = check_release_manifest_signature(
        {"payload": "release-42", "signature": "valid-sig-for:release-42"}, verifier=verifier
    )
    check("FTF-H028", "a genuinely valid signature passes", valid.status is GateStatus.PASS)

    tampered = check_release_manifest_signature(
        {"payload": "release-42", "signature": "tampered"}, verifier=verifier
    )
    check("FTF-H028", "a tampered signature fails, not passes", tampered.status is GateStatus.FAIL)


# ---------------------------------------------------------------------------
# check_required_workflows_have_no_forbidden_patterns
# ---------------------------------------------------------------------------

_CLEAN_WORKFLOW = """\
name: fixture
on: [pull_request]
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - run: pytest tests/
"""

_WORKFLOW_PREAMBLE = "name: f\non: [pull_request]\njobs:\n  g:\n    steps:\n"

_FORBIDDEN_FIXTURES = {
    "continue_on_error": (_WORKFLOW_PREAMBLE + "      - run: x\n        continue-on-error: true\n"),
    "or_true_fallback": (_WORKFLOW_PREAMBLE + "      - run: flaky-cmd || true\n"),
    "forced_exit_zero": (_WORKFLOW_PREAMBLE + "      - run: pytest tests/; exit 0\n"),
    "test_focus_marker": (_WORKFLOW_PREAMBLE + "      - run: echo 'test.only(x)'\n"),
}


def test_forbidden_pattern_scan_catches_each_pattern():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        for label, text in _FORBIDDEN_FIXTURES.items():
            bad_file = tmp_path / f"{label}.yml"
            bad_file.write_text(text, encoding="utf-8")
            result = check_required_workflows_have_no_forbidden_patterns(workflow_paths=[bad_file])
            check(
                "FTF-H028",
                f"forbidden pattern {label!r} is caught, not silently allowed",
                result.status is GateStatus.FAIL and label in result.detail,
            )
            bad_file.unlink()

        clean_file = tmp_path / "clean.yml"
        clean_file.write_text(_CLEAN_WORKFLOW, encoding="utf-8")
        clean_result = check_required_workflows_have_no_forbidden_patterns(
            workflow_paths=[clean_file]
        )
        check(
            "FTF-H028",
            "a clean fixture workflow with none of the forbidden patterns passes",
            clean_result.status is GateStatus.PASS,
        )


def test_forbidden_pattern_scan_empty_report_fails():
    result = check_required_workflows_have_no_forbidden_patterns(workflow_paths=[])
    check(
        "FTF-H028",
        "an empty set of workflow files fails closed (an empty report is not a pass)",
        result.status is GateStatus.FAIL,
    )


def test_real_shipped_workflows_pass_the_forbidden_pattern_scan():
    result = check_required_workflows_have_no_forbidden_patterns()
    check(
        "FTF-H028",
        "the real, currently-shipped .github/workflows/*.yml files contain none of "
        "Section 52's forbidden patterns",
        result.status is GateStatus.PASS,
    )


# ---------------------------------------------------------------------------
# check_no_untrusted_pull_request_target_checkout
# ---------------------------------------------------------------------------

_UNTRUSTED_PR_TARGET_WORKFLOW = """\
name: risky
on: [pull_request_target]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
      - run: npm test
"""

_SAFE_PR_TARGET_WORKFLOW = """\
name: safe
on: [pull_request_target]
jobs:
  label:
    runs-on: ubuntu-latest
    steps:
      - run: echo "label only, no PR head checkout"
"""


def test_pull_request_target_checkout_of_pr_head_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        bad_file = pathlib.Path(tmp) / "risky.yml"
        bad_file.write_text(_UNTRUSTED_PR_TARGET_WORKFLOW, encoding="utf-8")
        result = check_no_untrusted_pull_request_target_checkout(workflow_paths=[bad_file])
        check(
            "FTF-H028",
            "a pull_request_target workflow that checks out the PR head is rejected",
            result.status is GateStatus.FAIL,
        )


def test_pull_request_target_without_pr_head_checkout_passes():
    with tempfile.TemporaryDirectory() as tmp:
        safe_file = pathlib.Path(tmp) / "safe.yml"
        safe_file.write_text(_SAFE_PR_TARGET_WORKFLOW, encoding="utf-8")
        result = check_no_untrusted_pull_request_target_checkout(workflow_paths=[safe_file])
        check(
            "FTF-H028",
            "a pull_request_target workflow that never checks out the PR head passes",
            result.status is GateStatus.PASS,
        )


def test_real_shipped_workflows_have_no_untrusted_pull_request_target():
    result = check_no_untrusted_pull_request_target_checkout()
    check(
        "FTF-H028",
        "the real, currently-shipped workflow files contain no untrusted "
        "pull_request_target checkout (none of them even use that trigger today)",
        result.status is GateStatus.PASS,
    )


def test_on_key_boolean_parsing_quirk_is_handled():
    """PyYAML's safe_load resolves a bare `on:` key to the boolean True
    (YAML 1.1 resolution), not the string "on" -- confirmed empirically
    against this repo's real ci.yml before relying on it in
    _workflow_triggers. A workflow using pull_request_target must still be
    detected despite this quirk."""
    import yaml

    parsed = yaml.safe_load(_UNTRUSTED_PR_TARGET_WORKFLOW)
    check(
        "FTF-H028",
        "PyYAML really does parse the bare 'on:' key as boolean True, not string 'on' "
        "(the quirk _workflow_triggers is written to handle)",
        True in parsed and "on" not in parsed,
    )


# ---------------------------------------------------------------------------
# Composition: overall_status / run_release_evidence_gate
# ---------------------------------------------------------------------------


def test_overall_status_is_blocked_when_any_check_is_blocked():
    results = (
        GateResult(key="a", status=GateStatus.PASS, detail="ok"),
        GateResult(
            key="b", status=GateStatus.BLOCKED_EXTERNAL, detail="d", owner="o", next_action="n"
        ),
    )
    check(
        "FTF-H028",
        "a single BLOCKED_EXTERNAL check makes the overall status not PASS",
        overall_status(results) is not GateStatus.PASS,
    )


def test_overall_status_is_fail_when_any_check_fails():
    results = (
        GateResult(key="a", status=GateStatus.PASS, detail="ok"),
        GateResult(key="b", status=GateStatus.FAIL, detail="broken"),
    )
    check(
        "FTF-H028",
        "a single FAIL check makes the overall status FAIL",
        overall_status(results) is GateStatus.FAIL,
    )


def test_overall_status_pass_only_when_every_check_passes():
    results = (
        GateResult(key="a", status=GateStatus.PASS, detail="ok"),
        GateResult(key="b", status=GateStatus.PASS, detail="ok"),
    )
    check(
        "FTF-H028",
        "overall status is PASS only when every individual check is PASS",
        overall_status(results) is GateStatus.PASS,
    )


def test_run_release_evidence_gate_end_to_end_blocked_without_token():
    """No token, no manifest -- the exact real state of this sandbox today.
    Must never report an overall PASS."""
    results = run_release_evidence_gate(
        owner="SJZLab", repo="Sovra", sha=SHA, branch="main", token=None
    )
    check(
        "FTF-H028",
        "the composed gate never reports overall PASS when GitHub access is unavailable",
        overall_status(results) is not GateStatus.PASS,
    )
    check("FTF-H028", "the composed gate returns exactly 5 checks", len(results) == 5)


def test_fault_injection_forbidden_pattern_regex_disabled():
    """Law 1: temporarily disable the forbidden-pattern list on the live,
    tracked module and prove the real fixture that should be caught is no
    longer caught -- then restore byte-identical."""
    import scripts.release_evidence_gate as module

    module_path = ROOT / "scripts" / "release_evidence_gate.py"
    original = module_path.read_text(encoding="utf-8")
    original_patterns = copy.deepcopy(module.FORBIDDEN_WORKFLOW_PATTERNS)
    try:
        module.FORBIDDEN_WORKFLOW_PATTERNS = ()
        with tempfile.TemporaryDirectory() as tmp:
            bad_file = pathlib.Path(tmp) / "continue_on_error.yml"
            bad_file.write_text(_FORBIDDEN_FIXTURES["continue_on_error"], encoding="utf-8")
            result = module.check_required_workflows_have_no_forbidden_patterns(
                workflow_paths=[bad_file]
            )
        check(
            "FTF-H028",
            "disabling the forbidden-pattern list makes the real fixture wrongly pass "
            "(proving the check is load-bearing, not decorative)",
            result.status is GateStatus.PASS,
        )
    finally:
        module.FORBIDDEN_WORKFLOW_PATTERNS = original_patterns
    restored = module_path.read_text(encoding="utf-8")
    check(
        "FTF-H028",
        "the tracked module file itself was never modified on disk",
        restored == original,
    )

    # Re-run clean after restore to confirm the real detection is back.
    with tempfile.TemporaryDirectory() as tmp:
        bad_file = pathlib.Path(tmp) / "continue_on_error.yml"
        bad_file.write_text(_FORBIDDEN_FIXTURES["continue_on_error"], encoding="utf-8")
        post_restore = module.check_required_workflows_have_no_forbidden_patterns(
            workflow_paths=[bad_file]
        )
    check(
        "FTF-H028",
        "after restore, the real check catches the fixture again",
        post_restore.status is GateStatus.FAIL,
    )


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(
            f"FTF-H028 trusted CI and exact-revision release evidence gate: {len(FAILURES)} failed"
        )
        for f in FAILURES:
            print(f"  {f}")
        sys.exit(1)

    print("FTF-H028 trusted CI and exact-revision release evidence gate: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
