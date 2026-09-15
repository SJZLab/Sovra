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
