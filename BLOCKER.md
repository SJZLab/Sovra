# BLOCKER.md

## LIVE 2026-10-08T23:41Z — GitHub Actions runner-provisioning outage recurrence (same class as
## the 2026-09-30/10-01 occurrences below)

**What failed:** on PR #663 (`relay/FTF-0903-r6`), the `gate`, `classify`, `mutation-gate` and
`relay-policy` jobs failed identically on three separate attempts (the original run and two
re-runs: `created_at`→`completed_at` deltas of 2-3 seconds each time, `runner_id: 0`,
`runner_name: ""` on every attempt — never scheduled, before any test body ran). Confirmed this is
not specific to this PR or this one workflow run: at the exact same moment (23:41:18Z-23:41:20Z),
an unrelated, independent PR (#662, `claude/zealous-brown-verta8`, a `[CERT]` run, re-run attempt
2) showed the identical signature on its own `gate`/`classify` jobs — 2 seconds, `runner_id: 0`.
Two different runs, two different branches, two different sessions' work, failing identically at
the same instant rules out anything specific to PR #663's own diff (a tests-only change to
`tests/tasks/test_ftf_0903.py`) or to one cursed workflow-run id.

**What was tried:** re-ran the failed jobs once (the sanctioned single retry for "died before any
test body ran") — identical failure. Checked whether this was narrower than repo-wide (e.g.
GitHub Actions concurrency contention from PR #658's own ~61-job run in the same window) by
re-running again after that PR's load should have cleared — identical failure a third time, and
by then PR #658's own batch had already mostly completed, weakening the concurrency-contention
theory. The cross-PR correlation at the same timestamp is the strongest evidence: this is an
account/platform-level condition, not something a further push or retry from inside this
sandbox can fix.

**Why this is escalated now rather than retried again:** per `docs/operations/routines.md` CI-red
rules, the one-re-run cap for this failure class is already spent, and "a second failure is real"
— a third identical occurrence, now cross-PR-confirmed, is not something to keep probing from
inside the sandbox. Per Law 17 ("provider failure/outage/unknown state stays visible as blocked
... never converted to success") and this repo's own precedent for this exact failure class
(2026-09-21, 2026-09-27, 2026-09-30/10-01 below), this is recorded here rather than left implicit
in PR comments, and PR #663 is not merged (merge requires a green `ci-verdict`, which cannot be
exercised while this holds).

**Scope — this does not block all relay work:** per the 2026-09-30 precedent's own resolution,
code-level work (implementing, committing, pushing, opening draft PRs) does not require CI and
can continue; only *merging* (which needs a green `ci-verdict` on the final head) is blocked for
any PR caught by this outage. This session continued other relay-owned work (FTF-1703 round 4)
on a separate branch while this held.

**Three route options for whoever picks this up next (same as the 2026-09-30 precedent):**
1. Check GitHub Actions account/org-level limits (Settings → Billing and plans → Actions, or
   Settings → Actions → General → concurrent job limits) for a spending cap or concurrency ceiling
   hit by today's volume of parallel automated PR pushes.
2. Check the GitHub Actions status/incident page directly (this sandbox's egress cannot reach it)
   for a live platform-wide incident.
3. Do nothing and let it clear on its own, as all three prior occurrences did (2026-09-21,
   2026-09-27, 2026-09-30/10-01) — re-check one fresh run before re-diagnosing from scratch; if
   still `runner_id: 0`, this entry already covers it.

_No further re-run on PR #663 from this session until this is confirmed cleared on a fresh run._

## RESOLVED 2026-10-04 by owner decision (option 1): FTF-1206 entry below
The owner approved adding FTF-1206A (Organization gateway policy persistence) as a Release 1
addendum. Its contract is `spec/addenda/FTF-1206A.json` (ADR `docs/adr/0002-task-addendum-ftf-1206a.md`),
and the denominator is now 301. FTF-1206's persistence half now depends on FTF-1206A, which is first
in QUEUE.md "## Backlog". FTF-1206's route-change-against-grant half stays independently
re-reviewable.


## RESOLVED (was LIVE 2026-10-02) — FTF-1206: no task in the fixed 300-task index owns the `OrganizationGatewayPolicy`
## persistence migration the 2026-10-01 L2 review's own DoD requires — Control-plane scope decision

**What this is not:** not an owner ask (no secret, destructive op, repo-setting change, new spend,
or reused-component swap is involved), and not a stuck implementation attempt (STEP 4(c) does not
apply — no fix was attempted and failed; this is a scope question raised before attempting one, per
CLAUDE.md "Relay is execution-only ... write BLOCKER.md and stop ... do not improvise the DoD").

**The gap:** the 2026-10-01 Control-plane L2 review (`docs/certifications/queue.md`) returned
FTF-1206 NOT CERTIFIED with a two-part DoD: (1) "a persisted, tenant-scoped org-policy record (its
own migration task, per the packet's `database_changes` rule; until that exists, mark FTF-1206
BLOCKED_DEPENDENCY rather than done)"; (2) "a route-change check against the granted deployment
namespaces." This shift completed (2) — see this file's matching `docs/certifications/queue.md`
2026-10-02 entry and the fix PR — but found (1) has no existing task to claim:

- FTF-1206's own task packet (`docs/implementation/task-packets/FTF-1206.md`, pulled fresh via
  `tools/r1_handoff.py packet FTF-1206`) closes its own `files` clause to exactly
  `packages/litellm-client/organization_policy_control.py` and `tests/tasks/test_ftf_1206.py`, and
  its own `database_changes` clause reads "No independent schema change. Consume frozen contract;
  any required change is a separately claimed migration prerequisite" — i.e. this task is
  contractually barred from adding its own table, and some *other* task is supposed to own it.
- Checked `docs/TASK_INDEX.md` directly for that other task: it does not exist. FTF-1206's own
  dependency FTF-1201 (`litellm_registration_capability_detection.py`) explicitly disclaims the
  identical persistence in its own certified notes ("No independent schema change... capability is
  computed fresh per call, never persisted"). No other FTF-12xx entry, and no FTF-R-series
  architecture task, names an `OrganizationGatewayPolicy`/org-gateway-policy migration as its own
  scope. The review's own phrase "claim a separate migration prerequisite first" names an action
  with no task ID behind it anywhere in the real, fixed 300-task index.

**Why this can't be resolved by relay improvisation:** the task's own `files`/`database_changes`
clauses are a closed contract, the same kind of boundary every other FTF-xxxx submission in this
repository has been held to (e.g. FTF-0500B/FTF-0500C/FTF-1807's own fix-and-resubmit PRs each
stayed inside their task's own declared file scope). Adding a migration/table under this task's own
module would exceed that closed scope on relay's own say-so; inventing a brand-new task ID outside
the 300-task index is a bigger decision than a single relay shift should make unilaterally.

**Three route options for whoever picks this up next:**
1. **Add a new task to the 300-task index** (e.g. an `FTF-12xx`/`FTF-R`-numbered "Organization
   gateway policy persistence" migration task) with its own acceptance/negative_acceptance/
   `database_changes` contract, FTF-1206 depending on it. Most faithful to the architecture's own
   "separately claimed migration prerequisite" framing, but changes the fixed task count (currently
   300) — a Control-plane/owner-level decision, not relay's to make alone.
2. **Fold the persistence into FTF-1206's own scope directly** (widen this task's own `files` list
   to include a migration + a real DB-backed read/write path, consuming the existing `apps/api`
   Alembic chain the same way FTF-0601's own fix-and-resubmit added migration `0018` within its own
   task) — faster, but overrides this task's own explicit `database_changes` clause rather than
   following it.
3. **Accept the disclosed-gap posture as sufficient for this task specifically** (same class FTF-
   0103/FTF-0104/FTF-0105/FTF-H003/FTF-0402 already established and were certified under) and treat
   the 2026-10-01 review's DoD item 1 as not actually required for *this* task's own certification,
   overriding the review rather than the task contract — a position only Control plane can take,
   since the review itself already rejected that posture once for this exact item.

Until one of these is decided, FTF-1206's own `certification_state` stays `PROVISIONAL` on the
persistence half (the route-change-against-grant half is independently re-review-eligible now, per
`docs/certifications/queue.md`'s 2026-10-02 entry). No further relay attempt at DoD item 1 should be
made without this decision — that would be exactly the improvisation CLAUDE.md's "Relay is
execution-only" rule exists to prevent.

## RESOLVED 2026-10-03 — FTF-0902: `network-policy-enforcement-integration` baseline cluster

## RESOLVED 2026-10-01T13:50Z by Control plane (UNBLOCK): FTF-0902 entry below
The three "falsified" theories did not rule out the root cause. The harness pins
`NODE_IMAGE = "kindest/node:v1.31.2"` for BOTH clusters, and kind ships kindnet inside the node
image, so pinning the `kind` binary to v0.23.0 never changed the baseline's CNI. The entry's own
evidence confirms enforcement: the pre-policy probe to `denied` succeeded, every post-policy probe
timed out (dropped packets), and the in-policy `allowed` and the hostNetwork listener stayed
reachable. Corrected route: give the baseline `kindest/node:v1.30.0` and a pre/post-policy self-check
(DoD on PR #402, comment 5930089145, and QUEUE.md Active item 1). This was a single-item problem, so
under the v0.6.10 rules it belongs in "## Parked" or "## Active", not as a whole-relay BLOCKER.


## RESOLVED (was LIVE 2026-10-01T10:30Z) — FTF-0902: `network-policy-enforcement-integration` baseline cluster
## fails deterministically; three distinct, real-evidence-backed root-cause theories each
## implemented for real and each falsified by a real CI run

**What failed:** on PR #402 (branch `claude/wizardly-fermat-c06dbx`), the baseline (`npt-baseline`,
meant to be a non-enforcing contrast cluster) side of `network-policy-enforcement-integration`
deterministically returns `{allowed: True, denied: False}` — the policy appears to block nothing,
on every retry, across every fix attempted. This is FTF-0902's own real acceptance-clause evidence
(Section 11-12/65.4/77.5); without it the task cannot be certified.

**What was tried (all on PR #402, all real CI runs, none a guess left unverified):**
1. **kindnet-version-based enforcement** (the Control-plane-specified DoD: pin `kind` to v0.23.0,
   since v0.24.0's bundled kindnet enforces NetworkPolicy by default) — job log confirms
   `kind v0.23.0` was genuinely installed and used (run `36839582390`, job `110295202473`), yet the
   baseline cluster returned the identical failure signature. Falsified: v0.23.0's kindnet
   genuinely does not enforce, so this isn't why `npt-baseline` fails.
2. **ARP/route warm-up timing, before policy application** — a discarded single-attempt probe to
   both `allowed_ip`/`denied_ip`, inserted before the `NetworkPolicy` object exists on either
   cluster (symmetric, cannot mask real enforcement). Job log shows the warm-up itself completed
   quickly (~0.1-0.2s, a real connection or fast refusal, not a timeout) — the mechanism ran as
   designed — yet the real assertion probe still failed identically (run `36840503380`, job
   `110298220036`). Falsified.
3. **Route/ARP disruption from `fake-metadata-service`'s own `hostNetwork` setup, warm-up moved to
   after policy application** — anchored to a concrete new fact from deepened diagnostics: the
   verbose probe reported "Connection timed out" (a silently dropped packet), never "Connection
   refused" (nothing listening), and the same destination was reachable early (fast, not a
   timeout) and became unreachable later in the same cluster's lifetime. Moved the warm-up to run
   immediately before the real probes, after `fake-metadata-service`'s `hostNetwork` setup already
   ran. Job log: `{allowed: True, denied: False, metadata: True}` unchanged (run `36842540660`, job
   `110304795503`). Falsified.

Diagnostics ruled out a crash/restart (`denied` pod: `Running`, `Ready: True`, `Restart Count: 0`,
no previous terminated container) and confirmed `nc -l -p 8080` is genuinely running inside the
pod (not a silent bind failure). `allowed` and the separately-created `fake-metadata-service`
(also `hostNetwork`) are both reliably reachable; only `denied` — the second of the two
near-simultaneous same-batch listener pods — is consistently affected, on every run, independent
of `kind` version and of warm-up placement. Full job IDs, log excerpts and reasoning for each
round: PR #402's own comments (five dated comments, 09:05Z-09:30Z).

**Why this is now formalized here rather than left in PR comments:** per STEP 4(c) ("stuck — same
action, same failure, 3x — never retry a 4th time") three distinct, real-evidence-backed fix
attempts is the stop condition. The PR #402 session itself recognized this and stopped attempting
further fixes, but moved directly to other RELAY-OWNED work on the same branch (a single-branch
push constraint, disclosed in its own PR comment) without writing this file's own stop — this
entry closes that gap rather than let the condition stay implicit in PR comments only, per Law 1/5
("reported, not silently worked around").

**Three route options for whoever picks this up next (Control plane design question, not an
owner ask — nothing here is a secret, a destructive op, a repo-setting change, new spend, or a
reused-component swap):**
1. **A session with live cluster/tcpdump/conntrack access** — directly observe the dropped
   packet's path (`iptables` counters, `conntrack -L`, `tcpdump` on `cni0`/`veth*` on the node)
   during the failure window, inside the GitHub Actions runner itself while the job is live. Most
   likely to find the real cause; needs tooling/access this relay sandbox does not have.
2. **Replace the `busybox nc -l` single-shot-then-respawn listener with a real persistent server**
   (e.g. a small Python TCP echo server in a `python:3-alpine` image) that actually logs each
   connection attempt — removes the entire "is it the listener or the network" ambiguity the
   current diagnostics could not fully resolve, at the cost of a slightly heavier test fixture.
3. **Test whether the two listener pods' near-simultaneous creation/IP-allocation is itself
   implicated** — not yet tried: create `denied` and `allowed` several seconds apart instead of
   back-to-back, see if the deterministic failure follows the pod or the timing.

**No relay-side further iteration on this specific job until one of these is decided or new
evidence changes the picture.** `docs/spec-matrix.yaml`'s FTF-0902 entry stays `PROVISIONAL`/
`L1-PROVEN`; not self-certified, not claimed fixed. PR #402 stays open and watched, unchanged by
this session (this session pushed no code to PR #402's own branch, consistent with "never push to
a different branch without explicit permission" — the clean, unrelated FTF-H007/FTF-04A06/
FTF-0601 portion of that PR's own work was instead ported to this session's own branch and
resubmitted separately; see `QUEUE.md`'s matching top dated entry).

**RESOLUTION (2026-10-03):** this entry went stale the moment it was written — Control plane posted a
corrected root-cause diagnosis and a new DoD directly on PR #402 (comment, 2026-10-01T11:08:10Z, ~38
minutes after this entry's own timestamp above), explicitly stating "this is a new route, so the
3-attempt count starts fresh." That correction sat unactioned for 2 days: every relay shift since
(2026-10-01T12:58Z through 2026-10-03T04:51Z, several "tracking-only, frontier saturated" entries in
`PROGRESS.md`) read only this file's own summary and never re-checked PR #402's live comment thread for
a Control-plane update, so each one's own "FTF-0902 stuck, not retried" conclusion was correct about
*this entry* but stale about the real, current state of the task. A 2026-10-03 shift found the
correction by reading PR #402's comments directly via the GitHub API, confirmed no commit or shift had
acted on it since, and executed Control plane's own posted DoD on a fresh branch off current `main`
(not a push onto PR #402's own branch, consistent with "never push to a different branch without
explicit permission") — see `docs/certifications/queue.md`'s matching 2026-10-03 entry and
`docs/spec-matrix.yaml`'s FTF-0902 entry for the full fix (a per-cluster node-image split plus a
self-checking pre/post-policy probe on the baseline cluster, replacing the three falsified theories
above). Not self-certified (Law 4) — the fix is submitted PROVISIONAL, awaiting a real CI run on the
newly opened PR and independent Control-plane L2 review before certification. **Lesson for future
shifts, not yet enforced by tooling:** a `BLOCKER.md` entry can go stale the moment Control plane
replies on the PR thread it names; re-reading that PR's own live comments before trusting this file's
summary is cheap and should be habitual, not just done once a gap is already suspected.

---

_No other open blockers. The entry below is resolved history, kept for the record per this repo's
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
