# BLOCKER.md

_No open blockers. The entry below is resolved history, kept for the record per this repo's
"reported, not silently worked around" norm — not a live issue._

## RESOLVED 2026-10-01T01:14Z — Repo-wide GitHub Actions runner-provisioning outage, past its own
## "clears within hours" precedent window

**Resolution:** confirmed genuinely cleared via PR #342's own run (`36681155484`, head
`8066b666`), independently of any prior shift's claim: `get_workflow_job` on the
`network-policy-enforcement-integration` job returned `runner_id: 1000012646`, a real runner
name, and real per-step timestamps spanning 3+ minutes of actual `kind`/`kubectl`/Calico
execution — structurally different from every `runner_id: 0`/zero-log-content failure during the
outage. 38/39 jobs on that run completed green, `gate` included; the one failure is the
already-diagnosed, pre-existing baseline-cluster ARP timing defect this repo's own PR #342 thread
has tracked since 2026-09-27 (ticket-level detail in that PR's comments), not a new or
outage-related failure. Cross-checked against PR #390's own independently re-run workflow
(`36681644901`) to confirm the clearing is repo-wide, not specific to one PR. This meets the
same resolution bar the 2026-09-21 occurrence used ("a session confirms CI is provisioning
runners again on a fresh PR event").

**Original entry, preserved for context:**

**What failed:** every check run on every open PR in this repository — including PRs whose base
sha is the current `main` tip (`bb2ea6c`) and whose own code has nothing wrong with it — has
failed within 2-40 seconds of starting, with `runner_id: 0`/`runner_name: ""` (no runner ever
assigned), since approximately 2026-09-30T05:30Z. Confirmed directly via `actions_get`
(`get_workflow_job`) on jobs spanning five separate open PRs (#337, #342, #359, #364, #380) and
the newest workflow run in the whole repository (run #600, PR #393, `09:08-09:10Z`) — still
failing at the moment of this entry. This is the identical signature this repo already documented
twice before (2026-09-21, 2026-09-27; see "RESOLVED 2026-09-21" note below and
`docs/certifications/ledger.md`): every job in the `gate` workflow fails uniformly and
near-instantly, never reaching a real test, which rules out any individual task's own code as the
cause (39/39 checks fail identically regardless of what the PR touches).

**What was tried:** a prior shift (2026-09-30T07:04Z, PR #390) independently diagnosed this same
outage, re-ran one affected job to confirm it wasn't a one-off blip, and deliberately did **not**
file a fresh owner ask — reasoning that this exact outage class had self-cleared "within hours"
both prior times. This shift (2026-09-30, ~2h+ after that diagnosis) re-confirmed the outage is
still fully live on the most recent run in the entire repository, with zero jobs recovering in the
interim across ten further workflow runs from multiple independent sessions. Job log downloads via
`get_job_logs` 404 on every attempt (no log content was ever produced, consistent with the runner
never attaching). No repo file (`.github/workflows/ci.yml`, secrets, runner labels) changed
between the last known-green run (#589, `05:23Z`, real `runner_id`) and the first failing run
(#590, `05:33Z`) other than ordinary doc/append-only content — ruled out as the cause by diff.

**Why this is now an ask rather than a further wait:** the precedent this repo itself set (both
2026-09-21 and 2026-09-27) was "self-clears within hours." This occurrence has now run
uninterrupted for 3h37m+ across every job on every PR with no recovery, exceeding that bar, and at
least four separate automated sessions today (PRs #390, #392, #391/#393 Control-plane certifiers,
and this one) have each independently hit it without being able to do anything about it from
inside the sandbox — this is a GitHub-Actions-account-level condition (runner supply), not
something a further push, retry, or code change from this repo can fix. Per Law 17 ("provider
failure/outage/unknown state stays visible as blocked ... never converted to success") and this
repo's own 2026-09-21 precedent (escalated the same day once confirmed on two independent PRs),
this is escalated now rather than left implicit in scattered PR comments.

**Three route options for the owner:**
1. **Check GitHub Actions account/org-level limits** — concurrency limits or a spending/usage cap
   on the account (Settings → Billing and plans → Actions, or Settings → Actions → General →
   concurrent job limits) could plausibly explain "queues but never assigns a runner" if today's
   unusually high volume of parallel automated PR pushes (11 open PRs, many pushed within the same
   few minutes) hit a cap — this is the most actionable, fastest-to-check option.
2. **Check the GitHub Actions status/incident page directly** (this sandbox's egress is blocked
   from reaching it) for a live incident matching "runner provisioning" — if confirmed
   platform-wide, no repo-side action is possible; just wait it out, same as the two prior
   occurrences, but now with an actual upstream confirmation rather than an assumption.
3. **Do nothing and let it clear on its own**, as both prior occurrences did, and treat this entry
   as a timestamped record rather than a request for immediate action — acceptable if the owner has
   no reason to think this occurrence is different, but disclosed as option 3 rather than assumed
   as the only path, since 3h37m already exceeds the two prior occurrences' own resolution window.

No relay task work is possible while this holds: `merge on green` cannot be exercised for any PR
(none can go green), including the three already-open docs-only tracking PRs (#389, #390, #392)
that document the frontier being fully claimed. The dependency-ready frontier itself was
independently recomputed fresh this shift (parsing `docs/spec-matrix.yaml` + `docs/TASK_INDEX.md`
directly, not trusted from any prior shift's summary) and confirms the same conclusion three
independent shifts already reached today: of 13 dependency-ready, not-yet-certified tasks, 6 are
already `PROVISIONAL` (awaiting Control-plane certification, not relay's to touch), 6 more
(FTF-0214/FTF-0306/FTF-0500B/FTF-0500C/FTF-0601/FTF-0902) already have an open relay PR each, and
the remaining one (FTF-1703) stays correctly declined — its own acceptance clause needs a real
Argilla-backed reviewer workflow that does not exist in this repository, and its would-be
unblocker (FTF-R006) is not dependency-ready either (FTF-0516 still `certification_state: NONE`).
There is no genuinely unclaimed, undeclined task to pick even once CI recovers.

_No further BLOCKER.md entry is needed on the next scheduled fire if this is still unresolved —
re-check one fresh run first; if still `runner_id: 0`, this entry already covers it and the shift
should stop immediately rather than re-diagnose a fourth time._

## RESOLVED (history) — entries below are old, cleared occurrences of the same outage class and
prior unrelated blockers, kept for the record per this repo's "reported, not silently worked
around" norm — not live issues on their own.

## RESOLVED 2026-09-16 — Relay fully blocked past FTF-0001 (Section 85 gap)

Original entry: no queued item past FTF-0001 had a real acceptance/negative-acceptance/
verification contract; `docs/architecture/CRITERION_MAPPING.md` "Known gap" and
`docs/TASK_INDEX.md` had been unchanged since the initial scaffold commit (`f9c2ccd`) with no
Section 85 content ever supplied. Wrote this file and a QUEUE.md owner ask rather than improvise
a DoD, per CLAUDE.md "Relay is execution-only."

**Resolution:** the owner supplied `FINE_TUNING_FACTORY_RELEASE_1.md` (the single-file Release 1
master specification — root of the repo) and `tools/r1_handoff.py` (extracted from the master
doc's own Section 86). Verified before trusting either:
- Read `r1_handoff.py` in full: stdlib-only, no network/subprocess/eval/exec, path-traversal-
  and symlink-safe writes, refuses to overwrite existing content with different bytes.
- Copied the uploaded spec file byte-for-byte (`cp`, not regenerated) and confirmed
  `sha256sum` on disk matches the tool's own reported `source_sha256`
  (`5d3c183ff11b85718d5a3bbb08188868c455464202bb4898f6ddde66013eb664`) — no transfer corruption.
- `validate --self-test`: `status: PASS`, 300/300 tasks, 43 scenarios, 21 capabilities,
  dependency graph acyclic, 16/16 deliberately-broken structural fixtures correctly rejected.
- `bootstrap`: inspected every file it wrote (`spec/RELEASE_1.md`, `spec/tasks.json`,
  `spec/runtime-architecture.json`, `spec/qualification-inventory.json`,
  `spec/release-1-handoff.json`, `docs/implementation/TRACKER.json`) — all plain JSON/Markdown,
  every task `NOT_STARTED`/`ready=false`/no evidence, nothing executable, no claim of completion.
- `packet FTF-0002`: produced a genuine per-task DoD (real acceptance, negative_acceptance,
  verification_commands, files, dependencies, plus binding section text) — not a placeholder.

Updated `docs/architecture/CRITERION_MAPPING.md` ("Known gap — RESOLVED"), `CLAUDE.md` (Read-
first line now points to the master spec + tool ahead of the four excerpt docs it supersedes;
`CLAUDE.md.sha256` re-pinned via `sha256sum`, verified with `sha256sum -c`), and `QUEUE.md`
(relay now runs `tools/r1_handoff.py packet <task-id>` for a real DoD before claiming a task;
BLOCKER.md only if that command itself fails).

Per the sharing session's explicit instruction: this shift does not claim or work FTF-0002 or
any other task — this is a doc/tooling PR only. The relay picks up the now-unblocked queue on
its normal next fire.

## RESOLVED 2026-09-15 — CLAUDE.md hash mismatch

Original entry: a scheduled-relay prompt baked in a pinned CLAUDE.md hash that went stale after
an authorized edit re-pinned the file's own internal self-reference. Root-caused as expected
fallout from a legitimate change, not tampering; fixed at the source in commit `a010433` by
replacing the pinned-hash convention with a self-verifying one (compare `sha256sum CLAUDE.md`
against the committed `CLAUDE.md.sha256` directly, which can't go stale). Re-verified clean on
the next two shifts since (2026-09-15 and 2026-09-16, the latter after CLAUDE.md's own Read-first
edit above and hash re-pin).
