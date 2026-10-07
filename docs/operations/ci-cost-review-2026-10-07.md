# CI cost and repeat-run review (2026-10-07)

Scope: every CI workflow run in this repository (859 runs, 2026-09-15 → 2026-10-07T07:12Z,
24,937 jobs), all 542 PRs, and the merged-PR file sets. The data was pulled from the GitHub
Actions API, not estimated. Re-measure at any time with `python3 tools/ci_metrics.py --since <ISO>`.

## Baseline (measured)

| Measure | Value |
|---|---|
| CI runs / PRs with CI | 859 / 526 (1.63 runs per PR; current regime since 2026-10-06T13:10Z: 2.9) |
| Job-minutes actually executed | 27,503 |
| Billed job-minutes (each job rounded up to a whole minute) | 40,416 |
| Runs per job | 46 integration jobs + `gate` + `mutation-gate` on every PR |
| Median job: `gate` / integration job | 3.6 min / 0.5 min (so rounding is about 1/3 of the bill) |
| Queue wait, job created → started | median 0.43 min, p90 1.57 min |
| Run wall time | median 7.3 min |
| Re-run attempts | 26 runs |
| Runs blocked before start by the account spending limit | 18 (2026-09-21, 09-22, 09-30, 10-05, 10-07) |
| Billed minutes per certified criterion (117) | about 345 |

## What actually caused each pattern

1. **Tracker-only PRs ran the whole integration suite.** 320 of 859 runs (37%) changed only
   QUEUE.md, PROGRESS.md, docs/certifications/**, docs/progress/** or the spec-matrix/tracker
   mirrors. No integration test reads those files (checked: only comments mention them), yet
   each run spent about 46 billed minutes on integration jobs, 9,079 job-minutes in total. 130 of
   the 282 merge-commit PRs are tracker-only. Cause: workflow design. This is not a product or
   test failure.
2. **Superseded runs ran to completion.** `ci.yml` had no `concurrency` group. 192 runs were
   still running when a newer run started on the same PR, and they spent 3,476 job-minutes after
   that point, all on inputs that were already stale. Cause: workflow design.
3. **The `da57b10` pair (runs 36840503380 and 36840503871) was a true duplicate.** One push to PR #402
   at 09:05:39Z produced two `pull_request` runs 1 second apart. They had the same full head SHA
   (`da57b102b27b4d08fec50820270e4354e38df999`), the same branch and base, the same workflow and
   the same result (41 success, 1 failure). It is the only same-head duplicate in the history
   (49 job-minutes). The concurrency group now cancels the older of such a pair.
4. **Debugging one integration job by pushing the whole suite (PR #402, 2026-10-01).** There were
   12 runs in 54 minutes, and `network-policy-enforcement-integration` failed in all 12. Each push
   tested a new hypothesis (the commits record falsified theories), so it was diagnosis, not
   blind retry. But 538 of the 579 job-minutes went to the 47 unrelated jobs. Cause: the test's
   real failure (product/environment: kind CNI behaviour) plus a process gap, since there was no
   way to run only the job under diagnosis.
5. **PRs #437–#442 (and #427–#481, 20 PRs in total): "tracking-only" relay PRs.** Each recorded that
   the previous tracking PR had merged, a self-feeding loop every 2 h, with full CI each time.
   Cause: process. It stopped after the 2026-10-06T13:10Z "no docs-only record PRs" rule, which
   until now was written but not enforced.
6. **#410/#413 and 35 more overlapping pairs: two open PRs for the same item.** The last one before
   the claim rule (2026-10-06T17:40Z) was #467/#468. None since then, but the rule was
   instruction-only. Cause: no atomic claim, so two lanes took the same item.
7. **#471/#472 conflict on the queue file.** Two relay PRs both edited Control-plane tracker
   files. Cause: process (shared-file edits). The no-shared-file rule addressed it, but did not
   enforce it.
8. **"gate passed but another job failed" happened in 65 runs.** No branch protection or required
   check aggregated the 48 jobs, so "gate is green" was routinely read as "CI is green". Cause:
   no single verdict.
9. **Failures by class (106 failed runs).**
   - **Budget:** 18 runs blocked before start by the spending limit. This includes the 2026-10-07
     04:20Z failure on PR #536, which an earlier queue.md note wrongly called transient.
   - **Gate checks:** 42. Of these, 11 were lint/format/type, and 31 were a task proof or the spec
     validator (test or product).
   - **Integration:** 53 job failures. 22 were network-policy (environment/test), 13 were
     storage-semantics, 8 were the OSV dependency scan (an external service) and 10 were others.
   - **Cancelled:** 1.
10. **Silent skips.** `mutation-gate` reported success ("SKIPPED") on a `[relay]` PR that changed
    production Python but had no task test to run mutants against.

## What changed (this PR)

- **`concurrency` per PR** (keyed by PR number, never SHA) with `cancel-in-progress`. A newer
  event supersedes an older in-flight run. Two PRs with the same head but different bases keep
  separate runs. Release, smoke and deploy workflows are not touched.
- **`classify` job** (scripts/ci_policy.py, run from the base branch copy). A tracker-only change
  set skips the 46 integration jobs. `gate` (lint, type checks, every unit and task proof, and the
  spec/product-truth/data-exposure gates) always runs. Any other file, any doubt or any error
  means the full run. A shared-infra change (workflow, lockfiles, packages) is never tracker-only.
- **`ci-focus: <job>`** in the PR body narrows a run to `gate` plus the named integration jobs.
  This is for a #402-style diagnosis loop. A focused run always fails `ci-verdict`, so it can
  never be merged as green.
- **`ci-verdict` job: the one result to read.** It fails on any failed, cancelled or timed-out
  job, on any skip without a documented exemption, and on any job missing from its `needs`. A
  unit test also fails the gate if a job is added without being aggregated. It writes
  `ci-verdict.json`, which binds the result to the merge-candidate SHA, the head and base SHAs, the
  workflow file hash, the policy script hash, the change-set hash, the runner image and every job
  result. It uploads that file as an artifact, so an old or cached result cannot stand in for the
  current one. A run superseded by the concurrency group gets no verdict at all.
- **`relay-policy` job** (`[relay]` PRs, base branch copy) enforces:
  - one open PR per item, where the oldest open PR owns it;
  - no edits to Control-plane or governance files;
  - no CERTIFIED flips;
  - no log-only PRs.
- **`mutation-gate`** now fails, instead of skipping, when production Python changed with no task
  test.
- **Procedure.** CLAUDE.md, docs/operations/routines.md ("CI contract") and QUEUE.md now define
  green as "the latest `ci-verdict` on the current head is success". They require each re-push for
  a failure to state a new hypothesis, and they class the spending-limit stop as an owner (billing)
  action, not a flake.

## Expected effect (replayed on the real history; to be re-measured on live runs)

Replaying the same 859 runs under the new rules:

| | Before | After (replay) |
|---|---|---|
| Job-minutes executed | 27,503 | 16,370 (−40.5%) |
| Billed job-minutes | 40,416 | 25,977 (−35.7%) |
| Saved by tracker-only scope | — | 9,079 job-min |
| Saved by superseded-run cancellation | — | 2,743 job-min (after overlap with the above) |
| Added by classify/verdict/relay-policy | — | about 2–3 billed min per run (included above) |

**What the replay does not include:**
- savings from `ci-focus` loops, because the replay cannot know where it would have been used;
- duplicate PRs and tracking-only PRs that the new relay-policy check rejects, which it would
  stop before their full runs;
- fewer spending-limit stops.

Test strength, the set of checks run on every code change, and the release evidence gate (which
still fails any skipped required check) are unchanged.

## Not changed, needs the owner (repository settings / billing)

- **Make `ci-verdict` a required status check on `main`** (repository ruleset or branch protection),
  together with CODEOWNERS review for `.github/workflows/`. Until then, the verdict is enforced by
  procedure, and a PR could still edit the workflow that judges it. That edit would show in its
  diff, and relay-policy rejects it on `[relay]` PRs.
- **Spending limit.** 18 runs were stopped by "recent account payments have failed or your spending
  limit needs to be increased". This is a billing setting. The changes above reduce the minutes
  needed, but do not change the limit.
