"""scripts/release_evidence_gate.py — FTF-H028 trusted CI and exact-revision
release evidence gate.

Section 52 requires a protected final aggregator that fails on missing,
cancelled, timed-out, skipped, xfailed or stale results; zero collected
tests; missing matrix legs; untrusted reports; invalid signatures; or
mismatched commit/image/config digests, and forbids `continue-on-error`,
`|| true`, skip/xfail/focus-only markers and forced exit-zero wrappers in
required checks. Section 73.2/73.3 additionally require verifying branch
protection and required-check configuration as real repository settings,
and signing release manifests with an approved workload identity/signing
service. Every check below is a real, fail-closed probe -- never a canned
verdict -- following the same `BLOCKED_EXTERNAL`-never-fabricated-PASS
pattern `apps/api/app/services/environment_preflight.py` (FTF-H002) already
established for this repository.

This module was factored out of `scripts/verify_release.py` (which imports
and calls it) as a path refinement of this task's initial `files` list --
same rationale `scripts/spec_verification.py` already backs
`scripts/verify_release.py`/`scripts/verify_task.py`, disclosed in
docs/spec-matrix.yaml's notes rather than silently deviating from the
task packet.

Confirmed empirically before writing the branch-protection check (not
assumed): a live GET against
`https://api.github.com/repos/SJZLab/Sovra/branches/main/protection` from
this session returned a real HTTP 403 `"Resource not accessible by
integration"` -- reachable, but this session's credential lacks the
`Administration:read` permission branch protection requires. `GITHUB_TOKEN`
inside a GitHub Actions workflow has the same default limitation unless a
repository owner explicitly grants a scoped fine-grained PAT or GitHub App
installation permission. `check_branch_protection` below reports this
real, reproducible condition as `BLOCKED_EXTERNAL` with the actual next
action -- never a fabricated PASS -- matching CLAUDE.md's own
`BRANCH_PROTECTION: instruction-tier` note.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
GITHUB_API_BASE = "https://api.github.com"

# The real job names this repository's `gate` workflow currently runs
# (.github/workflows/ci.yml). Grown only as real jobs are added -- never
# padded with an aspirational job name that doesn't exist yet.
REQUIRED_RELEASE_CHECKS: tuple[str, ...] = ("gate", "docker-local-stack-integration")

# The job name the protected aggregator itself publishes
# (.github/workflows/10-release-qualification.yml) -- this is the context a
# real branch-protection ruleset must require, once one exists.
AGGREGATOR_CHECK_NAME = "release-qualification"

_NON_PASSING_CONCLUSIONS = frozenset(
    {"skipped", "cancelled", "timed_out", "action_required", "stale", "neutral"}
)

FORBIDDEN_WORKFLOW_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("continue_on_error", re.compile(r"continue-on-error\s*:\s*true", re.IGNORECASE)),
    ("or_true_fallback", re.compile(r"\|\|\s*true\b")),
    ("forced_exit_zero", re.compile(r"[;&]\s*exit\s+0\b")),
    (
        "test_focus_marker",
        re.compile(r"\b(test\.only|it\.only|describe\.only|fit\(|fdescribe\()"),
    ),
)


class GateStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED_EXTERNAL = "BLOCKED_EXTERNAL"


@dataclass(frozen=True)
class GateResult:
    key: str
    status: GateStatus
    detail: str
    owner: str | None = None
    next_action: str | None = None

    def __post_init__(self) -> None:
        if self.status is GateStatus.BLOCKED_EXTERNAL:
            if not self.owner or not self.owner.strip():
                raise ValueError(f"{self.key}: BLOCKED_EXTERNAL requires a non-empty owner")
            if not self.next_action or not self.next_action.strip():
                raise ValueError(f"{self.key}: BLOCKED_EXTERNAL requires a non-empty next_action")
        if not self.detail or not self.detail.strip():
            raise ValueError(f"{self.key}: detail must not be empty")


Fetcher = Callable[[str, str], tuple[int, dict[str, Any] | None, str]]


def _github_request(url: str, token: str) -> tuple[int, dict[str, Any] | None, str]:
    """Real GET against api.github.com. Returns (status_code, json_body, raw_text)."""
    request = urllib.request.Request(  # noqa: S310
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "sovra-release-evidence-gate",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
            return response.status, (json.loads(raw) if raw else None), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = None
        return exc.code, body, raw
    except urllib.error.URLError as exc:
        return 0, None, str(exc.reason)


def check_release_evidence_report(
    *,
    owner: str,
    repo: str,
    sha: str,
    token: str | None,
    expected_checks: tuple[str, ...] = REQUIRED_RELEASE_CHECKS,
    fetcher: Fetcher = _github_request,
) -> GateResult:
    """Section 52/73.2: every required check must have a real, non-stale,
    successfully-completed report on the exact release SHA -- missing,
    stale-commit, incomplete or non-success conclusions all fail closed."""
    if not token:
        return GateResult(
            key="release_evidence_report",
            status=GateStatus.BLOCKED_EXTERNAL,
            detail="no GitHub token available to query the real check-runs API for this commit",
            owner="platform operator",
            next_action=(
                "supply a token with repository Checks:read access (e.g. the workflow's own "
                "GITHUB_TOKEN) so the aggregator can fetch real check-run conclusions for the "
                "exact release SHA"
            ),
        )
    status_code, body, raw = fetcher(
        f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{sha}/check-runs", token
    )
    if status_code != 200 or body is None:
        return GateResult(
            key="release_evidence_report",
            status=GateStatus.BLOCKED_EXTERNAL,
            detail=f"GitHub check-runs API returned status {status_code}: {raw[:300]}",
            owner="platform operator",
            next_action=(
                "grant this token Checks:read access on this repository, then re-run against "
                "the exact release SHA"
            ),
        )

    by_name: dict[str, list[dict[str, Any]]] = {}
    for run in body.get("check_runs", []):
        by_name.setdefault(str(run.get("name", "")), []).append(run)

    problems: list[str] = []
    for expected in expected_checks:
        runs = by_name.get(expected, [])
        if not runs:
            problems.append(f"missing: {expected!r} has no check-run report for {sha}")
            continue
        run = max(runs, key=lambda r: str(r.get("started_at") or ""))
        head_sha = run.get("head_sha")
        if head_sha != sha:
            problems.append(
                f"stale: {expected!r} report head_sha {head_sha!r} does not match "
                f"release sha {sha!r}"
            )
            continue
        if run.get("status") != "completed":
            problems.append(f"incomplete: {expected!r} status={run.get('status')!r}")
            continue
        conclusion = run.get("conclusion")
        if conclusion in _NON_PASSING_CONCLUSIONS:
            problems.append(f"non_passing: {expected!r} conclusion={conclusion!r}")
            continue
        if conclusion != "success":
            problems.append(f"failed: {expected!r} conclusion={conclusion!r}")

    if problems:
        return GateResult(
            key="release_evidence_report", status=GateStatus.FAIL, detail="; ".join(problems)
        )
    return GateResult(
        key="release_evidence_report",
        status=GateStatus.PASS,
        detail=f"all {len(expected_checks)} required check(s) completed successfully on {sha}",
    )


def check_branch_protection(
    *,
    owner: str,
    repo: str,
    branch: str,
    token: str | None,
    required_context: str = AGGREGATOR_CHECK_NAME,
    fetcher: Fetcher = _github_request,
) -> GateResult:
    """Section 52/84.6: 'Branch/merge protection... are actual repository
    settings verified at qualification, not merely a workflow file in
    source.' A real GET against the branches/.../protection endpoint --
    never inferred from a workflow file's mere existence."""
    if not token:
        return GateResult(
            key="branch_protection",
            status=GateStatus.BLOCKED_EXTERNAL,
            detail="no GitHub token available to query real branch protection settings",
            owner="owner (repository administrator)",
            next_action=(
                f"grant a token Administration:read on {owner}/{repo} so branch protection can "
                "be verified for real"
            ),
        )
    status_code, body, raw = fetcher(
        f"{GITHUB_API_BASE}/repos/{owner}/{repo}/branches/{branch}/protection", token
    )
    if status_code in (403, 404):
        return GateResult(
            key="branch_protection",
            status=GateStatus.BLOCKED_EXTERNAL,
            detail=(
                f"GitHub rejected the real branch-protection read for {owner}/{repo}@{branch} "
                f"with status {status_code}: {raw[:300]}"
            ),
            owner="owner (repository administrator)",
            next_action=(
                "grant this session's credential the Administration:read permission (a "
                "fine-grained PAT scope or GitHub App installation permission), or run this "
                "check from a workflow with that permission, then re-run"
            ),
        )
    if status_code != 200 or body is None:
        return GateResult(
            key="branch_protection",
            status=GateStatus.BLOCKED_EXTERNAL,
            detail=f"unexpected status {status_code} reading branch protection: {raw[:300]}",
            owner="owner (repository administrator)",
            next_action="investigate the real API response, then re-run once readable",
        )

    problems: list[str] = []
    if not body.get("required_pull_request_reviews"):
        problems.append("required_pull_request_reviews is not enabled")
    contexts = set((body.get("required_status_checks") or {}).get("contexts") or [])
    if required_context not in contexts:
        problems.append(f"required_status_checks does not include {required_context!r}")
    if bool((body.get("allow_force_pushes") or {}).get("enabled")):
        problems.append("force pushes are allowed on this branch")
    if bool((body.get("allow_deletions") or {}).get("enabled")):
        problems.append("branch deletion is allowed")

    if problems:
        return GateResult(
            key="branch_protection", status=GateStatus.FAIL, detail="; ".join(problems)
        )
    return GateResult(
        key="branch_protection",
        status=GateStatus.PASS,
        detail=(
            f"real branch protection on {branch} enforces review, requires "
            f"{required_context!r}, forbids force-push and deletion"
        ),
    )


SignatureVerifier = Callable[[str, str], bool]


def check_release_manifest_signature(
    manifest: Mapping[str, Any] | None,
    *,
    verifier: SignatureVerifier | None = None,
) -> GateResult:
    """Section 73.3/84.7: 'Sign release manifests with an approved workload
    identity/signing service; verify trust policy, not just presence of a
    signature.' No such signing service is provisioned in this repository
    yet (Section 73.1's `evidence/release/manifest.json` is a later task's
    deliverable) -- this check is real and testable via an injectable
    verifier, but its default/production path never treats a present
    signature as proof of validity: with no configured verifier it reports
    BLOCKED_EXTERNAL, and with no manifest/signature at all it fails closed
    rather than silently passing (Q26: 'unsigned... reports cannot pass')."""
    if manifest is None:
        return GateResult(
            key="release_manifest_signature",
            status=GateStatus.BLOCKED_EXTERNAL,
            detail="no release manifest exists yet to sign or verify",
            owner="owner (release-signing authority)",
            next_action=(
                "build the Section 73.1 evidence/release/manifest.json deliverable and provision "
                "an approved workload identity/signing service before any signed release-evidence "
                "claim can be made"
            ),
        )
    signature = str(manifest.get("signature") or "").strip()
    payload = str(manifest.get("payload") or "")
    if not signature:
        return GateResult(
            key="release_manifest_signature",
            status=GateStatus.FAIL,
            detail="release manifest carries no signature -- unsigned reports cannot pass",
        )
    if verifier is None:
        return GateResult(
            key="release_manifest_signature",
            status=GateStatus.BLOCKED_EXTERNAL,
            detail=(
                "a signature is present but no trusted verification service/key is configured "
                "in this repository -- presence of a signature is never treated as validity"
            ),
            owner="owner (release-signing authority)",
            next_action=(
                "provision an approved workload identity/signing service and its trusted "
                "verification key (Section 73.3) before any signed release-evidence claim can "
                "pass"
            ),
        )
    if not verifier(payload, signature):
        return GateResult(
            key="release_manifest_signature",
            status=GateStatus.FAIL,
            detail="signature verification failed against the configured trusted key",
        )
    return GateResult(
        key="release_manifest_signature",
        status=GateStatus.PASS,
        detail="signature verified against the configured trusted key",
    )


def _iter_workflow_files(workflows_dir: Path = WORKFLOWS_DIR) -> list[Path]:
    return sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))


def scan_workflow_text_for_forbidden_patterns(text: str) -> list[str]:
    return [label for label, pattern in FORBIDDEN_WORKFLOW_PATTERNS if pattern.search(text)]


def check_required_workflows_have_no_forbidden_patterns(
    *, workflow_paths: Iterable[Path] | None = None
) -> GateResult:
    """Section 52: 'Required checks forbid continue-on-error, || true,
    skip/xfail/focus-only markers and forced exit-zero wrappers.'"""
    paths = list(workflow_paths) if workflow_paths is not None else _iter_workflow_files()
    if not paths:
        return GateResult(
            key="workflow_forbidden_patterns",
            status=GateStatus.FAIL,
            detail="no workflow files found to scan -- an empty report fails closed",
        )
    problems: list[str] = []
    for path in paths:
        findings = scan_workflow_text_for_forbidden_patterns(path.read_text(encoding="utf-8"))
        if findings:
            problems.append(f"{path.name}: {', '.join(findings)}")
    if problems:
        return GateResult(
            key="workflow_forbidden_patterns", status=GateStatus.FAIL, detail="; ".join(problems)
        )
    return GateResult(
        key="workflow_forbidden_patterns",
        status=GateStatus.PASS,
        detail=(
            f"{len(paths)} workflow file(s) contain no forbidden continue-on-error/||true/"
            "forced-exit-zero/focus-only pattern"
        ),
    )


def _workflow_triggers(data: Mapping[Any, Any]) -> set[str]:
    # PyYAML's default resolver treats a bare `on:` key as the boolean True
    # (YAML 1.1 on/off/yes/no resolution) -- confirmed empirically against
    # this repo's own ci.yml before relying on it: yaml.safe_load(open(
    # ".github/workflows/ci.yml")).keys() == ['name', True, 'jobs'].
    raw = data.get("on", data.get(True))
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {str(item) for item in raw}
    if isinstance(raw, dict):
        return {str(key) for key in raw}
    return set()


def check_no_untrusted_pull_request_target_checkout(
    *, workflow_paths: Iterable[Path] | None = None
) -> GateResult:
    """Section 52: 'no pull_request_target checkout of attacker code.'
    A pull_request_target-triggered workflow that checks out the PR head
    ref/sha runs untrusted fork code with base-branch secrets -- a real,
    well-known GitHub Actions supply-chain hole."""
    paths = list(workflow_paths) if workflow_paths is not None else _iter_workflow_files()
    problems: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            problems.append(f"{path.name}: could not parse YAML ({exc})")
            continue
        if "pull_request_target" not in _workflow_triggers(data):
            continue
        if "pull_request.head" in text or "github.event.pull_request.head" in text:
            problems.append(
                f"{path.name}: pull_request_target trigger checks out untrusted PR head content"
            )
    if problems:
        return GateResult(
            key="untrusted_pull_request_target", status=GateStatus.FAIL, detail="; ".join(problems)
        )
    return GateResult(
        key="untrusted_pull_request_target",
        status=GateStatus.PASS,
        detail=f"{len(paths)} workflow file(s) contain no untrusted pull_request_target checkout",
    )


def run_release_evidence_gate(
    *,
    owner: str,
    repo: str,
    sha: str,
    branch: str,
    token: str | None,
    manifest: Mapping[str, Any] | None = None,
    signature_verifier: SignatureVerifier | None = None,
    fetcher: Fetcher = _github_request,
) -> tuple[GateResult, ...]:
    return (
        check_release_evidence_report(
            owner=owner, repo=repo, sha=sha, token=token, fetcher=fetcher
        ),
        check_branch_protection(
            owner=owner, repo=repo, branch=branch, token=token, fetcher=fetcher
        ),
        check_release_manifest_signature(manifest, verifier=signature_verifier),
        check_required_workflows_have_no_forbidden_patterns(),
        check_no_untrusted_pull_request_target_checkout(),
    )


def overall_status(results: tuple[GateResult, ...]) -> GateStatus:
    if all(result.status is GateStatus.PASS for result in results):
        return GateStatus.PASS
    if any(result.status is GateStatus.FAIL for result in results):
        return GateStatus.FAIL
    return GateStatus.BLOCKED_EXTERNAL


def to_report(results: tuple[GateResult, ...]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "overall_status": overall_status(results).value,
        "checks": [
            {
                "key": r.key,
                "status": r.status.value,
                "detail": r.detail,
                "owner": r.owner,
                "next_action": r.next_action,
            }
            for r in results
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="path to a release evidence manifest JSON file (optional)",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    manifest: dict[str, Any] | None = None
    if args.manifest is not None and args.manifest.exists():
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))

    results = run_release_evidence_gate(
        owner=args.owner,
        repo=args.repo,
        sha=args.sha,
        branch=args.branch,
        token=token,
        manifest=manifest,
    )
    report = to_report(results)
    print(json.dumps(report, indent=2))
    # BLOCKED_EXTERNAL is not success -- Section 52: "Missing GitHub/GPU/
    # provider access is BLOCKED_EXTERNAL and blocks full release." Only a
    # unanimous PASS exits 0.
    return 0 if report["overall_status"] == GateStatus.PASS.value else 1


if __name__ == "__main__":
    sys.exit(main())
