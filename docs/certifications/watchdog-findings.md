# Watchdog Findings
Watchdog appends adversarial audit findings here; Control plane reviews.

## 2026-09-15 — Unearned status claim ("GitHub connector gap resolved")
- **What:** commit `10c6880` ("Update: GitHub connector gap resolved",
  session `session_01AHbfBeKTxwzBh7tNXduKjw`) pushed directly to a
  newly-created `main`, editing `QUEUE.md`/`PROGRESS.md` to claim the
  GitHub-connector setup gap was resolved and shrinking "two setup gaps"
  to "one." No PR, no evidence, no test.
- **Why it's a finding:** per the owner, the claim is false as stated —
  the connector picker bug was never fixed; the scaffold was pushed via
  GitHub's manual web uploader as a workaround, which is a materially
  different fact than "gap resolved." A status move with no bound
  evidence is what Law 2 ("never inflate tracker") and Law 11 ("no
  simulated progress") exist to catch, even though this was a status
  line in a progress doc rather than a spec-matrix criterion.
- **Compounding factor:** `BRANCH_PROTECTION: instruction-tier` (not
  machine-enforced) let the commit land directly on a `main` that
  session created, with no review step, alongside another session's
  independent work on `claude/trusting-wozniak-49xf3f`.
- **Disposition:** reverted in commit `ef2929c`, landed on `main` via
  PR [SJZLab/Sovra#1](https://github.com/SJZLab/Sovra/pull/1), merge
  commit `4aac72aa9ca69becccaf3ee6b29a8655e09b872a`. `CLAUDE.md` gained
  an explicit norm (sessions open PRs against `main`, never push
  directly) to close the gap that allowed this.
- **Not yet done:** the underlying GitHub-connector picker bug itself
  remains unfixed — this finding is about the false claim, not a
  re-opened engineering task. If it needs tracking as real work, it
  belongs in `QUEUE.md`, not here.

## 2026-09-15 — FTF-0001 certified via self-certification (Law 4)
- **found_by:** Watchdog adversarial audit (scheduled session)
- **first_seen:** 2026-09-15 — PR
  [SJZLab/Sovra#5](https://github.com/SJZLab/Sovra/pull/5)
  ("FTF-0001: control-plane L2 certification"), merged 16:33:09 +03:00,
  merge commit `0f58cef2db1fec9dc22fcf36a14de2d8b454e6c0`
- **severity:** high
- **affected criterion IDs:** FTF-0001 (the sole `CERTIFIED` entry in the
  300-criterion tracker as of this finding)
- **What changed:** PR #5 moved FTF-0001 from `proof_state=L1-PROVEN,
  certification_state=PROVISIONAL` (set by PR #3) to
  `proof_state=L2-CERTIFIED, certification_state=CERTIFIED`, and added the
  matching `docs/certifications/ledger.md` entry with
  `validated-by: control-plane`. Both PR #3 (the producer PR that
  generated FTF-0001's L1 evidence) and PR #5 (the PR that certified it)
  carry the identical `Claude-Session` URL
  (`https://claude.ai/code/session_01EN6u2CfDd8VdrHDTNyuH4g`) in their PR
  bodies — the same Claude Code session authored both the work and its own
  certification decision. PR #5's own body states "this isn't
  self-certification — it records a certification decision made by
  control plane, not by the session that produced FTF-0001's evidence,"
  but that claim is contradicted by the matching session ID.
- **Why it's a finding:** this is exactly the scenario
  `docs/CONTROL_PLANE_MODES.md` — written later the same day by that same
  session, in PR
  [SJZLab/Sovra#6](https://github.com/SJZLab/Sovra/pull/6) (19:41:58
  +03:00) — explicitly forbids: "Never certify a PR authored by this same
  session/identity — that's self-certification (Law 4)." No commit since
  has re-run CERTIFY from an independent session/identity against PR #3's
  evidence, so the repo's own governing procedure, applied to its own
  history, fails against the one record currently marked CERTIFIED.
- **Compounding factor:** every PR merged so far, including #3 and #5, was
  merged by the same human account (`samerzakaria`) via the GitHub UI —
  there is no independent human review step distinguishing "producer" from
  "certifier" either; the only claimed separation is the AI session's own
  role-labeling within its PR bodies ("relay" vs "control plane"), not a
  distinct actor.
- **Disposition:** not resolved by this audit — logged for Control plane /
  owner review; this session does not re-implement or override
  certification judgment. Options for Control plane: (a) have an
  independent session/identity re-run the CERTIFY procedure in
  `docs/CONTROL_PLANE_MODES.md` against PR #3's evidence and re-issue the
  ledger entry, or (b) downgrade FTF-0001 back to
  `certification_state=PROVISIONAL` pending that independent
  re-certification.
- **Not yet done:** FTF-0001 has no `proving_symbol`, so it does not count
  toward Product-true (Law 16) either way — this finding is about
  certification-ledger integrity (Law 4), not the product-true count.

## 2026-09-18 — Direct-push-to-main recurrence (FTF-R017 certification commit)
- **found_by:** Watchdog adversarial audit (scheduled session), independently
  confirming a compliance issue already self-disclosed in `QUEUE.md`
- **first_seen:** commit `b25ce80811bddd18e62e5a09bf0918e74e434f7a`
  ("Control plane: FTF-R017 CERTIFIED (PR #75 independently verified)"),
  authored/committed 2026-09-17 21:04:38 +00:00; self-disclosed the
  following shift in PR
  [SJZLab/Sovra#85](https://github.com/SJZLab/Sovra/pull/85)'s body and
  `QUEUE.md` "Owner actions — current" (2026-09-18 entry)
- **severity:** medium (process/gate-bypass, not a fabricated-evidence or
  falsified-status issue — the certification content itself checks out)
- **affected criterion IDs:** FTF-R017 (the certification-ledger commit for
  it, not the underlying evidence)
- **What changed:** `b25ce80` has a single parent
  (`aacd74528ff39ef14b0efc42575fe987f27490d4`, PR #75's real two-parent
  GitHub merge commit) and committer `Claude <noreply@anthropic.com>` — it
  was `git push`ed straight onto `main`, not merged through a GitHub PR.
  This is the identical pattern the 2026-09-15 finding above already
  caused `CLAUDE.md` to add an explicit `BRANCH_PROTECTION` norm to close
  ("every session opens a PR against main and never pushes directly"), and
  it recurred two days later from a different session. Contrast the
  legitimate merges audited this same run — PR #83 (`91866ec`), PR #87
  (`5efcbb9`), and PR #88 (`1022fc7`) — each confirmed via the GitHub API
  as `merged: true` with a real `merged_by` and green `check_runs` on the
  exact merged head.
- **Why it's still worth logging here:** it was caught and disclosed by
  the repo's own control-plane process this time (not by an external
  audit), which is the safeguard working as intended — but `main` still
  has no machine-enforced branch protection (`BRANCH_PROTECTION:
  instruction-tier` per `CLAUDE.md`), so the norm depends entirely on
  every session remembering it. Recording it here (rather than only in
  `QUEUE.md`, which gets cleared once the owner acknowledges it) keeps an
  out-of-band trend record: this is the second confirmed direct-to-main
  push in this repo's history, three days apart.
- **Disposition:** no remediation action taken by this audit (read-only,
  per its instructions) — the certification content of `b25ce80` was
  independently re-verified as correct by PR #85, and rewriting the
  direct-push commit's history is explicitly not recommended by that PR.
  Flagging for the owner's awareness that the instruction-tier norm has
  now failed to hold twice; a machine-enforced branch-protection ruleset
  on `main` (still unconfirmed per `CLAUDE.md`'s `BRANCH_PROTECTION` flag)
  would close this permanently rather than relying on each session to
  self-police.

## 2026-09-23 — CI `gate` job never actually runs FTF-0205's/FTF-0206's own tests (certification accepted "26/26 jobs green" as proof a specific test ran)
- **found_by:** scheduled Control-plane certifier shift, `pull_request.opened`
  webhook for PR #207 (`session_01PXgrUD2iBjuYBoEk2rCg87`)
- **first_seen:** the underlying gap has existed since FTF-0205 merged
  (PR #205, merge commit `b421fb366870167dcb7ecaab7a658adf1c2c6510`,
  2026-09-23); first *detected* while reviewing the next task, FTF-0206
  (PR #207) — see this same date's "FTF-0206 (PR #207) — NOT CERTIFIED"
  entry in `docs/certifications/queue.md` for the full technical detail
  reproduced below in summary.
- **severity:** high (evidence-substitution: the CERTIFY procedure's own
  step 2/3 — "confirm CI actually ran ... and its conclusion" / "not a
  stale or self-reported claim" — was satisfied by checking that CI's
  `gate` job was green, without checking whether `gate`'s own steps
  actually include the task's test. That check is what step 2/3 exists
  for, and it was skipped on two consecutive tasks by two different
  sessions.)
- **affected criterion IDs:** FTF-0205 (currently `certification_state:
  CERTIFIED`) and FTF-0206 (currently `certification_state: PROVISIONAL`,
  correctly held there by this same shift's review)
- **What changed / what's actually wrong:** `.github/workflows/ci.yml`
  wires each task's `tests/tasks/test_ftf_*.py` into the `gate` job (or a
  dedicated integration job) as an individually-named step — there is no
  wildcard/glob step that picks up a new test file automatically
  (confirmed: no `for ... in`, no `test_ftf_*` glob anywhere in the
  file). `gate`'s explicit per-task steps stop at "FTF-0203 negative-test
  proof" / "FTF-0204 negative-test proof" (`ci.yml:188,190`); no step for
  `tests/tasks/test_ftf_0205.py` or `tests/tasks/test_ftf_0206.py` was
  ever added, and none of the other 25 domain-named `*-integration` jobs
  is scoped to either task (the closest by name,
  `model-discovery-integration`, is FTF-0204's own job). Verified by
  reading `gate`'s real 73 step names from a real completed run
  (`actions_list(list_workflow_jobs)`, run `35812591578`, PR #207's real
  head `635213b`) and separately grepping all 26 job definitions in
  `ci.yml` for `0205`/`0206`/`model_group`/`model_info` — no match. Net
  effect: **CI genuinely ran and genuinely passed on both PRs, but never
  executed either task's own test file, at all** — the "26/26 jobs green"
  fact that both this shift's own FTF-0206 review and the prior FTF-0205
  certification review (QUEUE.md, "## Previous shift", 2026-09-23) relied
  on is true and was itself independently re-checked (not a stale/
  self-reported claim, per CERTIFY step 3) — it simply doesn't mean what
  both reviews needed it to mean, because none of those 26 jobs contains
  the step in question.
- **Why FTF-0205 is not being reopened by this same finding:** this
  shift's task is certifying the one item that was actually
  `PROVISIONAL` and pending at shift start (FTF-0206, per this session's
  own scheduled instructions — no new task work, no reprioritization
  beyond what certifying/rejecting FTF-0206 naturally requires).
  Downgrading an already-`CERTIFIED` entry is a Control-plane judgment
  call this review is not making unilaterally; this finding exists so
  that judgment can be made deliberately rather than the gap staying
  invisible. Everything else the FTF-0205 certification review did
  (independent session-ID check, real fault injection distinct from the
  producer's own, clean regression across FTF-0201-0204, correct
  `product_truth_check`/`status.py` figures) was genuine and is not in
  question — only the CI-execution claim specifically.
- **Disposition:** not remediated by this shift (FTF-0206 was left
  `PROVISIONAL`, not merged; see `docs/certifications/queue.md`'s
  "FTF-0206 (PR #207) — NOT CERTIFIED" entry for the required next step —
  a relay shift adds explicit `gate`-job steps for both
  `tests/tasks/test_ftf_0205.py` and `tests/tasks/test_ftf_0206.py`,
  confirmed real on `ubuntu-latest` runners: `sudo`/`openssl`/
  `update-ca-certificates` are all present and passwordless-sudo-capable
  there, so the fault-injection TLS-trust-store path is not the
  disclosed print-and-skip fallback). Whether FTF-0205's own
  `certification_state` should be reverted to `PROVISIONAL` pending a
  real CI run of its own test, versus accepted as-is once this
  reviewing session's own independent local reproduction (see the prior
  shift's QUEUE.md entry — fresh worktree, clean regression, an
  independent fault injection distinct from the producer's own, all
  genuinely reproduced) is judged sufficient after the fact, is left to
  Control plane.
