# PROGRESS — Sovra
Updated: 2026-09-15T00:00:00Z · Shift: this is a relay OPERATE/blocker shift
(no new FTF work — see "This shift"). Denominator: 1/300 certified,
unchanged.

## Last shift(s) — condensed (full detail in git history / ledger / watchdog-findings)
- FTF-0001 (Initialize monorepo) implemented, PR #3 merged
  (`7ab3f6ff1ac7bcc240e546b764af8d67ae0c8561`), L1-PROVEN.
- PR #4: unrelated pre-existing YAML-quoting bug fixed across all three
  GitHub Actions workflow files (real CI was returning 0 jobs before this).
- PR #5: FTF-0001 certified L2 — later found self-certified (same session
  authored PR #3 and PR #5, Law 4 violation) by watchdog PR #13
  (`77b3b2f`), reverted to PROVISIONAL (`3804dfa`), then independently
  re-certified by a session with no session-ID overlap with either
  (`795cc44`, merged via PR #14). Both ledger entries are in
  docs/certifications/ledger.md; the Certified count was never inflated —
  it stayed 1/300 across the correction.
- PR #6: added docs/CONTROL_PLANE_MODES.md (OPERATE/CERTIFY/UNBLOCK
  procedure for any session).
- PR #7: resolved owner action — GitHub connector setup gap (relay/control
  plane sessions do have working push/PR/merge access via GitHub MCP; the
  claude.ai chat connector picker bug is separately tracked as a known
  product bug, not a Sovra blocker).
- PR #8: wired docs/CONTROL_PLANE_MODES.md into CLAUDE.md's Read-first
  line, re-pinned CLAUDE.md's internal self-reference hash.
- PR #9: a relay shift correctly stopped on a CLAUDE.md hash mismatch
  caused by PR #8's re-pin outpacing the then-current scheduled trigger's
  baked-in hash value (BLOCKER.md written, no work touched).
- PR #10: re-derived SOURCE_ROOTS in scripts/product_truth_check.py from
  FTF-0001's real layout (apps/web, apps/api,
  workers/{training,evaluation,data,deploy}, packages/shared).
- PR #11: fixed the PR #9 root cause architecturally — CLAUDE.md hash is
  no longer baked into trigger prompts; a session now compares
  `sha256sum CLAUDE.md` against the committed CLAUDE.md.sha256 directly,
  which can't go stale independent of a CLAUDE.md edit.
- PR #12: status.py now requires a non-empty proving_symbol to count
  toward product-true (closes a loophole where a blank symbol could look
  like a pass).
- Watchdog PR #2/#13: two findings logged and resolved (a false "gap
  resolved" status claim, and the FTF-0001 self-certification above).
Certified: 1/300 · Product-true: 0 (unchanged all of the above — only
FTF-0001 is certified and it claims no proving_symbol, so it doesn't count
toward product-true by design).

## This shift (2026-09-15)
STEP 0.1: read PROGRESS.md (this file, pre-rewrite), QUEUE.md,
docs/certifications/queue.md, docs/certifications/ledger.md,
docs/certifications/watchdog-findings.md — all already reflected the state
summarized above; nothing pending certification, no unreviewed watchdog
findings.
STEP 0.2: `cognee-cli` not on PATH — text-search fallback active again
this shift (same as every prior shift).
Hash check: ran `sha256sum CLAUDE.md`, compared against the committed
CLAUDE.md.sha256 (this scheduled trigger no longer bakes in a value, per
PR #11) — match
(`6ac215c16e9adac14ed2f7b88c93e615a3c5f10bc9bab3dfd6b5893174e6dd04`), no
drift.
Found BLOCKER.md still contained PR #9's stale hash-mismatch entry even
though its root cause was fixed by PR #11. Verified the fix holds (hash
matches cleanly now) and cleared that entry per
docs/CONTROL_PLANE_MODES.md UNBLOCK step 4.
STEP 1: QUEUE.md "Active" names FTF-0002 as the next candidate once
SOURCE_ROOTS is re-derived. Confirmed SOURCE_ROOTS was re-derived (PR
#10). But cross-checked docs/architecture/CRITERION_MAPPING.md's "Known
gap" section (confirmed via `git log` unchanged since the initial scaffold
commit) and docs/TASK_INDEX.md line 327: every task's real DoD
(acceptance/negative-acceptance/verification contract, "Section 85") is
still unsupplied, and only FTF-0001 was ever named as detailed enough to
pick up without it. With FTF-0001 done, this gap now blocks the entire
remaining backlog, not just one item. Per CLAUDE.md's relay charter ("if
an item's DoD is unclear — write BLOCKER.md and stop. Do not improvise the
DoD"), did not pick up FTF-0002 or improvise its DoD.
STEP 2: N/A — no code/test/certification work this shift; this was an
OPERATE-style check-in plus a genuine STEP-4(b) blocker.
Ran `python3 scripts/product_truth_check.py --fail-on-unwired` as a sanity
check (not tied to any new criterion): clean, 0/0, no regression.
Wrote a fresh BLOCKER.md: closed the stale PR #9 entry, opened a new entry
detailing the Section-85 DoD gap with three route options for control
plane/owner. Added a dated entry to QUEUE.md "Owner actions — current"
(did not touch the existing trigger-tooling ask, which is still open and
unresolved). Did not touch docs/spec-matrix.yaml or
docs/certifications/ledger.md — no certification state changed.
No new commits to source/tests; PROGRESS.md, QUEUE.md, BLOCKER.md are the
only files this shift touched. Will open a PR against `main` per CLAUDE.md's
no-direct-push norm even though this shift is tracker/ops content, not
product code.

## Next shift
Top item: if the owner/control plane has supplied Section 85 content (or
an explicit narrower DoD authorization) for FTF-0002 or another queue item
by then, pick that item up per STEP 1/2. Otherwise, re-run the same
OPERATE check-in (hash, queue, certifications, watchdog) and expect the
same BLOCKER.md outcome — this is not a "stuck, retry 3x" loop condition,
it's a correctly-reported waiting state each time until the input changes.
Blocked: entire FTF-0002+ backlog, on missing Section 85 DoD content (see
BLOCKER.md, QUEUE.md "Owner actions — current").

## Metrics
Certified / denominator: 1/300
Product-true (Law 16): 0 — must match certified; FTF-0001 has no
proving_symbol so it is CERTIFIED without counting toward product-true
L4 gate: not run (Tier 3, release-only — relay never dispatches it)
Knowledge graph: not set up (cognee-cli not found this shift either; still
text-search fallback)

## Open owner asks
See QUEUE.md "Owner actions — current" (single source of truth per
CLAUDE.md — not duplicated here). As of this shift: (1) 2h relay/watchdog
trigger tooling still not created, (2) Section 85 DoD content needed for
FTF-0002 onward (new this shift).
