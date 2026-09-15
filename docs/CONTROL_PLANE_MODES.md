# Control Plane Operating Modes

This file lets any Claude Code session (not just one with the Groundwork skill
installed) act as control plane for Sovra. Read this + CLAUDE.md before doing
control-plane work (certify, unblock, or routine check-in).

Prime directive: a producer may generate L1 self-validation evidence but may
never independently certify, audit, or promote its own work.

## MODE — declare before acting: OPERATE · CERTIFY · UNBLOCK

### OPERATE (routine check-in, run this first each session)
1. Read PROGRESS.md — what did the last relay shift do?
2. Read QUEUE.md — any owner actions pending? Any new blockers?
3. Read docs/certifications/queue.md — anything awaiting certification?
4. Read docs/certifications/watchdog-findings.md — any unreviewed findings?
Then: if BLOCKER.md has content → UNBLOCK mode. If certifications/queue.md has
pending items → CERTIFY mode. Check CLAUDE.md integrity by comparing
`sha256sum CLAUDE.md` against the committed CLAUDE.md.sha256 (CI enforces
this on every PR too — .github/workflows/ci.yml). If they match, no action.
If they don't (or CLAUDE.md.sha256 is missing), CLAUDE.md changed without
its hash file being regenerated in the same commit — that commit's author
should have run `sha256sum CLAUDE.md | awk '{print $1}' > CLAUDE.md.sha256`
and committed it alongside their change; fix it the same way now, or write
BLOCKER.md if the cause isn't obviously a benign edit. Never hardcode a
CLAUDE.md hash value in a relay/watchdog prompt — always compare against
the committed file, which is the only value that can't go stale.

### CERTIFY (independent review of relay's L1 work — this is the core loop)
1. Read the PR diff.
2. Confirm CI actually ran (a real job, not 0 jobs / parse failure) and its
   conclusion.
3. Confirm the test genuinely fails when the feature is broken (read the
   before/after log, or break it locally and re-run).
4. Confirm product_truth_check.py passed for real criteria (0/0 is only valid
   when the criterion claims no proving_symbol — don't accept 0/0 as a pass
   once proving_symbol is filled in).
5. If all of the above hold: merge the PR, update docs/spec-matrix.yaml
   (proof_state: L2-CERTIFIED, certification_state: CERTIFIED, proving_test,
   evidence_hash = merge commit SHA), append a signed entry to
   docs/certifications/ledger.md (criterion ID · PR · commit · validated-by:
   control-plane · date · one-line evidence summary), update QUEUE.md (move
   item to Done, bump Certified count) and PROGRESS.md.
6. If anything above fails: do NOT merge. Write the gap back into
   docs/certifications/queue.md or QUEUE.md "Owner actions" as appropriate.

Never certify a PR authored by this same session/identity — that's
self-certification (Law 4). Never accept the producer's own local test run as
the certifying evidence; CI must have actually run it independently.

Before certifying any PR, compare your own session ID against every commit's
session ID on that PR. If any match, you are not an independent certifier for
that PR — say so and stop. State the session-ID comparison explicitly in
every ledger.md entry, not just a role label.

### UNBLOCK (relay wrote BLOCKER.md)
1. Read BLOCKER.md: what failed, what was tried, the relay's route options.
2. Code/logic problem → pick or extend a route option; if it needs an owner
   decision, write QUEUE.md "Owner actions — current" (one item, dated) and
   stop; otherwise write the resolution as the next Active QUEUE.md item with
   enough detail the relay can execute without guessing.
3. Configuration problem (wrong CLAUDE.md hash, broken CI YAML, wrong
   SOURCE_ROOTS) → fix it directly; this is a system change, not something the
   relay can self-correct.
4. Clear BLOCKER.md. If the fix is architectural, record it in docs/adr/.

## Denominator discipline
Certified/300 only moves via this CERTIFY procedure with a ledger entry.
Never hand-edit spec-matrix.yaml's certification_state without the matching
ledger.md line — status.py fails the build if they disagree.
