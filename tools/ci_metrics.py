#!/usr/bin/env python3
"""ci_metrics.py -- measure what CI actually costs, from the real GitHub Actions history.

Read-only. Uses `gh api` (the session's own credential); never estimates from PR counts or
total wall time. For every CI run in the window it fetches every job and reports:

  - job-minutes actually executed, and billed minutes (GitHub rounds each job up to a minute)
  - runs, re-run attempts, and runs per PR
  - minutes spent in runs that a newer run on the same PR had already superseded
  - same-head duplicate runs (identical inputs delivered twice)
  - cancelled runs, and runs blocked before start (spending limit / no runner)
  - queue wait (job created -> started) and run wall time, median and p90
  - integration jobs skipped by scope (tracker-only / focused), and ci-verdict outcomes
  - billed minutes per criterion certified in the window (pass --certified N from the ledger)

Usage:
  python3 tools/ci_metrics.py --since 2026-10-07T08:00:00Z [--until ...] [--json out.json]
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = "SJZLab/Sovra"


def api(path: str) -> dict | list:
    for attempt in range(4):
        r = subprocess.run(["gh", "api", path], capture_output=True, text=True)
        if r.returncode == 0:
            return json.loads(r.stdout)
        time.sleep(2**attempt)
    raise RuntimeError(f"gh api {path} failed: {r.stderr[:300]}")


def ts(s: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def minutes(a: str | None, b: str | None) -> float:
    s, e = ts(a), ts(b)
    return max(0.0, (e - s).total_seconds() / 60) if s and e else 0.0


def billed(m: float) -> int:
    return int(m) + (1 if m % 1 else 0) if m > 0 else 0


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))] if xs else 0.0


def collect(since: str, until: str | None) -> list[dict]:
    runs: list[dict] = []
    created = f">={since}" if not until else f"{since}..{until}"
    page = 1
    while True:
        d = api(
            f"repos/{REPO}/actions/workflows/ci.yml/runs?per_page=100&page={page}&created={created}"
        )
        assert isinstance(d, dict)
        runs += d["workflow_runs"]
        if len(d["workflow_runs"]) < 100:
            break
        page += 1

    def jobs_for(run: dict, flt: str) -> list[dict]:
        out: list[dict] = []
        p = 1
        while True:
            path = f"repos/{REPO}/actions/runs/{run['id']}/jobs?per_page=100&filter={flt}&page={p}"
            d = api(path)
            assert isinstance(d, dict)
            out += d["jobs"]
            if len(d["jobs"]) < 100:
                return out
            p += 1

    def jobs(run: dict) -> list[dict]:
        # GitHub sometimes 502s on filter=all for a re-run run. Fall back to the latest attempt
        # and say so (partial_job_history_runs) instead of failing or silently dropping the run.
        try:
            return jobs_for(run, "all")
        except RuntimeError:
            run["_partial_jobs"] = True
            return jobs_for(run, "latest")

    with cf.ThreadPoolExecutor(8) as ex:
        for run, js in zip(runs, ex.map(jobs, runs), strict=True):
            run["_jobs"] = js
    return runs


def certified_count() -> int:
    text = Path("docs/spec-matrix.yaml").read_text()
    return len(re.findall(r"^  certification_state: CERTIFIED$", text, re.M))


def report(runs: list[dict], certified_in_window: int | None = None) -> dict:
    act = bil = 0.0
    queue: list[float] = []
    wall: list[float] = []
    blocked = 0
    skipped_scope = 0
    verdicts: Counter[str] = Counter()
    by_head: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_branch: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_branch[r["head_branch"]].append(r)
        by_head[(r["head_branch"], r["head_sha"])].append(r)
        wall.append(minutes(r.get("run_started_at") or r["created_at"], r["updated_at"]))
        not_started = [j for j in r["_jobs"] if j["conclusion"] == "failure" and not j.get("steps")]
        if not_started and len(not_started) == len(
            [j for j in r["_jobs"] if j["conclusion"] == "failure"]
        ):
            blocked += 1
        for j in r["_jobs"]:
            m = (
                minutes(j.get("started_at"), j.get("completed_at"))
                if j["conclusion"] != "skipped"
                else 0.0
            )
            act += m
            bil += billed(m)
            if m > 0:
                queue.append(minutes(j.get("created_at"), j.get("started_at")))
            if j["conclusion"] == "skipped" and j["name"].endswith("-integration"):
                skipped_scope += 1
            if j["name"] == "ci-verdict":
                verdicts[j["conclusion"] or "none"] += 1
    superseded = 0.0
    for rs in by_branch.values():
        rs.sort(key=lambda r: r["created_at"])
        for a, b in zip(rs, rs[1:], strict=False):
            cut, end = ts(b["created_at"]), ts(a["updated_at"])
            if cut and end and cut < end:
                for j in a["_jobs"]:
                    s, e = ts(j.get("started_at")), ts(j.get("completed_at"))
                    if s and e and e > cut:
                        superseded += (e - max(s, cut)).total_seconds() / 60
    dups = sum(len(v) - 1 for v in by_head.values() if len(v) > 1)
    prs = {r["head_branch"] for r in runs}
    return {
        "runs": len(runs),
        "prs_with_runs": len(prs),
        "runs_per_pr": round(len(runs) / len(prs), 2) if prs else 0,
        "rerun_attempts": sum(1 for r in runs if (r.get("run_attempt") or 1) > 1),
        "partial_job_history_runs": sum(1 for r in runs if r.get("_partial_jobs")),
        "conclusions": dict(Counter(r["conclusion"] or "none" for r in runs)),
        "blocked_before_start_runs": blocked,
        "job_minutes_actual": round(act),
        "job_minutes_billed": round(bil),
        "minutes_after_supersession": round(superseded),
        "same_head_repeat_runs": dups,
        "integration_jobs_skipped_by_scope": skipped_scope,
        "ci_verdict": dict(verdicts),
        "queue_wait_min": {"median": round(pct(queue, 0.5), 2), "p90": round(pct(queue, 0.9), 2)},
        "run_wall_min": {"median": round(pct(wall, 0.5), 1), "p90": round(pct(wall, 0.9), 1)},
        "certified_criteria_now": certified_count(),
        "certified_in_window": certified_in_window,
        "billed_min_per_certified_in_window": (
            round(bil / certified_in_window, 1) if certified_in_window else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--since", required=True, help="ISO time, e.g. 2026-10-07T08:00:00Z")
    ap.add_argument("--until")
    ap.add_argument("--json", help="also write the raw runs+jobs here")
    ap.add_argument(
        "--certified",
        type=int,
        help="criteria certified in the window (from the ledger), for cost per item",
    )
    args = ap.parse_args(argv)
    runs = collect(args.since, args.until)
    if args.json:
        Path(args.json).write_text(json.dumps(runs))
    print(json.dumps(report(runs, args.certified), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
