#!/usr/bin/env python3
"""ci_policy.py -- CI change classification, relay policy and the single CI verdict.

Three subcommands, each run by .github/workflows/ci.yml from the BASE branch's copy of this
file (never the PR's own copy), so the code under review cannot rewrite the rules it is judged by:

  classify      Decide the verification scope of a pull request from the files it changes.
                "tracker" = every changed file is a tracker/record file that no integration test
                reads (QUEUE.md, PROGRESS.md, docs/certifications/** except the mutation
                allowlist, docs/progress/**, the spec-matrix/tracker mirrors). Anything else,
                any unknown file, an empty base, or any error = "full". The gate job (lint, type
                check, every unit and tests/tasks proof, the spec/product-truth/data-exposure
                gates) runs in BOTH scopes; "tracker" only skips the service-backed
                integration jobs. "focused" = a `ci-focus: <job>` line in the PR body narrows a
                full run to the gate plus the named integration jobs, for iterating on one
                failing job; ci-verdict always FAILS a focused run, so it is never a merge pass.

  relay-policy  Machine checks for "[relay]" PRs: one open PR per item (the oldest open PR owns
                the item), no edits to Control-plane or governance files, no CERTIFIED flips in
                the spec-matrix, and no log-only PRs (shift logs / submissions with no work).

  verdict       The one result to read. Fails on any failed or cancelled job, on any skipped job
                without a documented exemption, and on any workflow job missing from the
                aggregation. Writes an evidence record bound to the merge candidate, head, base,
                workflow file hash, change set hash, runner image and every job's result.

Stdlib only: the classify/verdict jobs install nothing.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

# Top-level job ids in a workflow file: two-space indented keys under `jobs:`.
JOB_HEADER = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")

# --- classify -------------------------------------------------------------------------------

# Files that are records about the work, not inputs to any integration test. Verified on
# 2026-10-07: no file under tests/integration, apps/, workers/ or packages/ opens any of them
# (the only mentions are comments/docstrings). The gate job still validates all of them.
TRACKER_EXACT = {
    "QUEUE.md",
    "PROGRESS.md",
    "docs/spec-matrix.yaml",
    "docs/implementation/TRACKER.json",
    "spec/tasks.json",
}
TRACKER_PREFIXES = ("docs/certifications/", "docs/progress/")
# Inside a tracker prefix but an input to a gate: always full scope.
NOT_TRACKER = {"docs/certifications/mutation-allowlist.yaml"}


def is_tracker_file(path: str) -> bool:
    if path in NOT_TRACKER:
        return False
    return path in TRACKER_EXACT or path.startswith(TRACKER_PREFIXES)


def classify_files(files: list[str]) -> tuple[str, str]:
    """Return (scope, reason). Fails toward "full"."""
    if not files:
        return "tracker", "empty change set (claim commit): nothing for integration jobs to test"
    other = [f for f in files if not is_tracker_file(f)]
    if other:
        shown = ", ".join(other[:5]) + (" ..." if len(other) > 5 else "")
        return "full", f"{len(other)} non-tracker file(s) changed: {shown}"
    return "tracker", f"all {len(files)} changed file(s) are tracker/record files"


FOCUS_LINE = re.compile(r"^[ \t]*ci-focus:[ \t]*(\S.*)$", re.M | re.I)


def parse_focus(body: str, integration_jobs: list[str]) -> tuple[list[str], list[str]]:
    """`ci-focus: job-a, job-b` lines in the PR body -> (known integration jobs, unknown names).

    A focused run executes the gate plus only the named integration jobs, so one failing
    integration job can be iterated on without re-running the other ~45 each push. It is
    never a merge verdict: ci-verdict always fails a focused run (see evaluate)."""
    names: list[str] = []
    for m in FOCUS_LINE.finditer(body or ""):
        names += [n for n in re.split(r"[\s,]+", m.group(1).strip()) if n]
    known = sorted({n for n in names if n in integration_jobs})
    unknown = sorted({n for n in names if n not in integration_jobs})
    return known, unknown


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def changed_files(base: str, head: str = "HEAD") -> list[str]:
    out = git("diff", "--name-only", "--no-renames", f"{base}...{head}")
    return sorted(line for line in out.splitlines() if line.strip())


def write_output(**kv: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for k, v in kv.items():
            fh.write(f"{k}={v}\n")


def cmd_classify(args: argparse.Namespace) -> int:
    focus: list[str] = []
    try:
        if args.event != "pull_request":
            scope, reason, files = "full", f"event {args.event!r} is not pull_request", []
        else:
            files = changed_files(args.base)
            scope, reason = classify_files(files)
            if scope == "full" and args.workflow:
                integration = [
                    j
                    for j in workflow_jobs(Path(args.workflow).read_text())
                    if j.endswith("-integration")
                ]
                focus, unknown = parse_focus(os.environ.get("PR_BODY", ""), integration)
                for name in unknown:
                    print(f"::warning::ci-focus names no integration job {name!r}; ignored")
                if focus:
                    scope = "focused"
                    reason = (
                        f"ci-focus in the PR body: only {', '.join(focus)} of the integration jobs "
                        "run; this run can never pass ci-verdict"
                    )
    except Exception as exc:  # any doubt -> widest scope, never a silent narrow run
        scope, reason, files, focus = (
            "full",
            f"classification error, running everything: {exc}",
            [],
            [],
        )
    digest = hashlib.sha256("\n".join(files).encode()).hexdigest()
    print(
        json.dumps(
            {
                "scope": scope,
                "reason": reason,
                "focus": focus,
                "files": files,
                "files_sha256": digest,
            },
            indent=2,
        )
    )
    write_output(
        scope=scope,
        reason=reason.replace("\n", " "),
        files_sha256=digest,
        files_count=str(len(files)),
        # Space-padded so the workflow's contains() matches whole job names only.
        focus=" " + " ".join(focus) + " ",
    )
    return 0


# --- relay-policy ---------------------------------------------------------------------------

ITEM_ID = re.compile(r"FTF-[0-9A-Z]+(?:-F\d+)?", re.I)

# Control-plane / governance files a [relay] PR must never change (CLAUDE.md "Relay is
# execution-only", "Governance PRs", Law 4). QUEUE.md stays allowed: a relay parks an item there.
RELAY_FORBIDDEN_EXACT = {
    "CLAUDE.md",
    "CLAUDE.md.sha256",
    "PROGRESS.md",
    "docs/certifications/queue.md",
    "docs/certifications/ledger.md",
    "docs/certifications/mutation-allowlist.yaml",
    "docs/CONTROL_PLANE_MODES.md",
    "docs/operations/routines.md",
    "scripts/ci_policy.py",
    "scripts/mutation_gate.py",
    "scripts/product_truth_check.py",
    "scripts/data_exposure_check.py",
    "scripts/validate_spec.py",
    "scripts/check_test_harness.py",
}
RELAY_FORBIDDEN_PREFIXES = (".github/workflows/",)
# The relay is told to add its new test as a CI job (Relay STEP 5), so ci.yml alone is allowed,
# but only additively: see ci_yml_violations.
CI_WORKFLOW = ".github/workflows/ci.yml"
LOG_ONLY_PREFIXES = ("docs/progress/", "docs/certifications/submissions/")
CERT_FLIP = re.compile(
    r"^\+\s*(certification_state:\s*CERTIFIED|proof_state:\s*L2-CERTIFIED)\b", re.M
)


def item_ids(title: str) -> set[str]:
    return {m.upper() for m in ITEM_ID.findall(title or "")}


def is_relay_title(title: str) -> bool:
    return (title or "").strip().lower().startswith("[relay]")


def _job_blocks(text: str) -> tuple[str, dict[str, str]]:
    """(everything outside `jobs:` entries, {job id: its full text})."""
    lines = text.splitlines(keepends=True)
    head: list[str] = []
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    in_jobs = False
    for line in lines:
        if line.startswith("jobs:"):
            in_jobs = True
            head.append(line)
            continue
        if in_jobs and line.strip() and not line.startswith((" ", "#")):
            in_jobs, current = False, None
        m = JOB_HEADER.match(line) if in_jobs else None
        if m:
            current = m.group(1)
            blocks[current] = []
        if current is not None:
            blocks[current].append(line)
        else:
            head.append(line)
    return "".join(head), {k: "".join(v).rstrip() for k, v in blocks.items()}


def _additive_only(before: str, after: str) -> str | None:
    """None if `after` is `before` plus whole new list items (steps, or `needs` entries) only;
    otherwise a description of the first non-additive change."""
    a, b = before.splitlines(), after.splitlines()
    for op, i1, _i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if op != "insert":
            return (
                f"line {i1 + 1} changed or removed: {a[i1].strip()[:80] if i1 < len(a) else ''!r}"
            )
        added = b[j1:j2]
        if not added[0].startswith("      - "):
            return f"inserted text does not start a new step/list item: {added[0].strip()[:80]!r}"
        nxt = a[i1] if i1 < len(a) else ""
        if nxt and not (nxt.startswith("      - ") or re.match(r"^    \S", nxt)):
            return f"inserted inside an existing step, before: {nxt.strip()[:80]!r}"
    return None


def ci_yml_violations(base_text: str, head_text: str) -> list[str]:
    """A [relay] PR may only ADD to ci.yml: new jobs, new steps, new `needs` entries. The
    workflow header (triggers, concurrency) and every existing line stay as they are, so an
    added step can never edit, weaken or skip an existing check."""
    problems: list[str] = []
    base_head, base_jobs = _job_blocks(base_text)
    head_head, head_jobs = _job_blocks(head_text)
    if base_head.strip() != head_head.strip():
        problems.append("ci.yml: the workflow header (on/concurrency/top-level keys) changed")
    for job, body in base_jobs.items():
        if job not in head_jobs:
            problems.append(f"ci.yml: existing job {job!r} was removed")
            continue
        why = _additive_only(body, head_jobs[job])
        if why:
            problems.append(f"ci.yml: existing job {job!r} was modified, not only extended: {why}")
    return problems


def relay_violations(
    title: str,
    number: int,
    files: list[str],
    spec_matrix_diff: str,
    open_prs: list[dict],
    ci_yml: tuple[str, str] | None = None,
) -> list[str]:
    """Pure policy: every violation, as text. Empty list = compliant.

    ci_yml = (base text, head text) of .github/workflows/ci.yml when the PR changes it."""
    problems: list[str] = []
    forbidden = [
        f
        for f in files
        if f != CI_WORKFLOW
        and (f in RELAY_FORBIDDEN_EXACT or f.startswith(RELAY_FORBIDDEN_PREFIXES))
    ]
    if forbidden:
        problems.append(
            "changes Control-plane/governance files a [relay] PR may not touch: "
            + ", ".join(forbidden)
        )
    if CI_WORKFLOW in files:
        if ci_yml is None:
            problems.append("ci.yml changed but its base/head text was not provided")
        else:
            problems.extend(ci_yml_violations(*ci_yml))
    if CERT_FLIP.search(spec_matrix_diff or ""):
        problems.append(
            "sets certification_state CERTIFIED / proof_state L2-CERTIFIED "
            "(Law 4: only the Control plane certifies)"
        )
    if files and all(f.startswith(LOG_ONLY_PREFIXES) for f in files):
        problems.append(
            "log-only PR: every change is a shift log or submission note, with no work; "
            "record the shift in the PR that does the work"
        )
    mine = item_ids(title)
    if not mine:
        problems.append("title names no FTF item ID, so the item cannot be owned")
    for pr in open_prs:
        other = int(pr["number"])
        if other == number or not is_relay_title(pr.get("title", "")):
            continue
        shared = mine & item_ids(pr.get("title", ""))
        if shared and other < number:
            problems.append(
                f"item(s) {', '.join(sorted(shared))} already owned by open PR #{other}; "
                "continue on that PR (or close it with a reason) instead of opening a second one"
            )
    return problems


def fetch_open_prs(repo: str, token: str) -> list[dict]:
    prs: list[dict] = []
    page = 1
    while True:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/pulls?state=open&per_page=100&page={page}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            batch = json.load(resp)
        prs.extend({"number": p["number"], "title": p["title"]} for p in batch)
        if len(batch) < 100:
            return prs
        page += 1


def cmd_relay_policy(args: argparse.Namespace) -> int:
    if not is_relay_title(args.title):
        print("relay-policy: not a [relay] PR; nothing to check.")
        return 0
    files = changed_files(args.base)
    spec_diff = git("diff", f"{args.base}...HEAD", "--", "docs/spec-matrix.yaml")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("::error::relay-policy needs GITHUB_TOKEN to check item ownership")
        return 1
    ci_yml = None
    if CI_WORKFLOW in files:
        ci_yml = (git("show", f"{args.base}:{CI_WORKFLOW}"), Path(CI_WORKFLOW).read_text())
    problems = relay_violations(
        args.title, args.number, files, spec_diff, fetch_open_prs(args.repo, token), ci_yml
    )
    for p in problems:
        print(f"::error::relay-policy: {p}")
    if not problems:
        print(
            f"relay-policy: compliant ({len(files)} file(s), items {sorted(item_ids(args.title))})."
        )
    return 1 if problems else 0


# --- verdict --------------------------------------------------------------------------------


def workflow_jobs(workflow_text: str) -> list[str]:
    """Top-level job ids of a workflow file (two-space indented keys under `jobs:`)."""
    jobs: list[str] = []
    in_jobs = False
    for line in workflow_text.splitlines():
        if line.startswith("jobs:"):
            in_jobs = True
            continue
        if in_jobs and line and not line.startswith((" ", "#")):
            in_jobs = False
        if in_jobs:
            m = JOB_HEADER.match(line)
            if m:
                jobs.append(m.group(1))
    return jobs


def skip_exemption(job: str, scope: str, is_relay: bool) -> str | None:
    """The documented reason a skipped job is acceptable, or None (= skipped is a failure)."""
    if job.endswith("-integration") and scope == "tracker":
        return "tracker-only change set: integration jobs test code that did not change"
    if job.endswith("-integration") and scope == "focused":
        return "not in ci-focus (the focused run itself fails the verdict)"
    if job in {"mutation-gate", "relay-policy"} and not is_relay:
        return "runs only on [relay] PRs"
    return None


def evaluate(
    results: dict[str, str], expected: list[str], scope: str, is_relay: bool
) -> tuple[list[str], dict[str, str]]:
    problems: list[str] = []
    exempt: dict[str, str] = {}
    for job in expected:
        if job not in results:
            problems.append(
                f"job {job!r} is not aggregated by the verdict (add it to ci-verdict needs)"
            )
    for job, result in sorted(results.items()):
        if result == "success":
            continue
        if result == "skipped":
            why = skip_exemption(job, scope, is_relay)
            if why:
                exempt[job] = why
                continue
            problems.append(f"job {job!r} was skipped with no documented exemption")
        elif result == "cancelled":
            problems.append(f"job {job!r} was cancelled; a cancelled check is not a pass")
        else:
            problems.append(f"job {job!r} result is {result!r}")
    if scope == "focused":
        problems.append(
            "focused run (ci-focus in the PR body) verified only part of the change; "
            "remove the ci-focus line and push to get a merge verdict"
        )
    elif scope not in {"tracker", "full"}:
        problems.append(f"scope {scope!r} is unknown; classify must decide tracker or full")
    return problems, exempt


def cmd_verdict(args: argparse.Namespace) -> int:
    needs = json.loads(Path(args.needs_json).read_text())
    results = {job: (data or {}).get("result", "missing") for job, data in needs.items()}
    workflow = Path(args.workflow).read_text()
    expected = [j for j in workflow_jobs(workflow) if j != args.self_job]
    is_relay = is_relay_title(args.title)
    problems, exempt = evaluate(results, expected, args.scope, is_relay)
    env = os.environ
    record = {
        "verdict": "fail" if problems else "pass",
        "problems": problems,
        "skipped_with_exemption": exempt,
        "jobs": results,
        "scope": args.scope,
        "scope_reason": args.scope_reason,
        "changed_files_sha256": args.files_sha256,
        "pr_number": args.number,
        "pr_is_relay": is_relay,
        "head_sha": args.head_sha,
        "base_sha": args.base_sha,
        "merge_candidate_sha": env.get("GITHUB_SHA", ""),
        "workflow_file": args.workflow,
        "workflow_sha256": hashlib.sha256(workflow.encode()).hexdigest(),
        "policy_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "run_id": env.get("GITHUB_RUN_ID", ""),
        "run_attempt": env.get("GITHUB_RUN_ATTEMPT", ""),
        "runner_image": f"{env.get('ImageOS', '')} {env.get('ImageVersion', '')}".strip(),
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
    }
    Path(args.evidence).parent.mkdir(parents=True, exist_ok=True)
    Path(args.evidence).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(record, indent=2, sort_keys=True))
    for p in problems:
        print(f"::error::ci-verdict: {p}")
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(
                f"## CI verdict: {record['verdict'].upper()}\n\n"
                f"scope `{args.scope}`: {args.scope_reason}\n\n"
            )
            for p in problems:
                fh.write(f"- FAIL: {p}\n")
            for job, why in sorted(exempt.items()):
                fh.write(f"- skipped `{job}`: {why}\n")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("classify")
    c.add_argument("--base", required=True)
    c.add_argument("--event", default="pull_request")
    c.add_argument("--workflow", default="", help="workflow file, for ci-focus job names")
    c.set_defaults(fn=cmd_classify)

    r = sub.add_parser("relay-policy")
    r.add_argument("--base", required=True)
    r.add_argument("--title", required=True)
    r.add_argument("--number", type=int, required=True)
    r.add_argument("--repo", required=True)
    r.set_defaults(fn=cmd_relay_policy)

    v = sub.add_parser("verdict")
    v.add_argument("--needs-json", required=True)
    v.add_argument("--workflow", required=True)
    v.add_argument("--self-job", default="ci-verdict")
    v.add_argument("--scope", required=True)
    v.add_argument("--scope-reason", default="")
    v.add_argument("--files-sha256", default="")
    v.add_argument("--title", default="")
    v.add_argument("--number", default="")
    v.add_argument("--head-sha", default="")
    v.add_argument("--base-sha", default="")
    v.add_argument("--evidence", required=True)
    v.set_defaults(fn=cmd_verdict)

    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
