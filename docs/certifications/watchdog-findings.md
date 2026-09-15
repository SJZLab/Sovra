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
