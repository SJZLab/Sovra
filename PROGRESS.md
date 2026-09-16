# PROGRESS — Sovra
Updated: 2026-09-16T00:00:00Z · Shift: 2 (blocked — no executable DoD available)

## This shift (2026-09-16)
STEP 0: `sha256sum CLAUDE.md` = `6ac215c16e9adac14ed2f7b88c93e615a3c5f10bc9bab3dfd6b5893174e6dd04`,
matches committed `CLAUDE.md.sha256` exactly — clean pass. The
self-verifying scheme from PR #11 (`a010433`) works as designed: this
shift's scheduled prompt carries no pinned hash to drift against, so the
2026-09-15 hash-mismatch blocker class cannot recur. Closed out the prior
BLOCKER.md entry for that (resolved, evidence above) per
docs/CONTROL_PLANE_MODES.md UNBLOCK step 4.

STEP 0.1: read PROGRESS.md, QUEUE.md, docs/certifications/queue.md — found
all three describe repo state as of the first relay shift (2026-09-15),
stale relative to `origin/main` HEAD `1b50304`. Reconciled against real
state (git log + current file contents) rather than trusting the stale
text, per STEP 0.1's "never assume state from prior context":
- FTF-0001 is `CERTIFIED`/`L2-CERTIFIED` for real — the original
  2026-09-15 certification was found self-certified (Law 4 violation,
  watchdog PR #13), reverted to PROVISIONAL, then independently
  re-certified same day by a session with no session-ID overlap with
  either the producer or the watchdog (see
  docs/certifications/ledger.md's second FTF-0001 entry). Certified count
  stayed 1/300 throughout — never inflated.
- SOURCE_ROOTS is filled in scripts/product_truth_check.py (PR #10):
  apps/web, apps/api, workers/{training,evaluation,data,deploy},
  packages/shared.
- status.py now requires non-empty proving_symbol for the product-true
  count (PR #12) — Law 16 discipline tightened, not loosened.
STEP 0.2: cognee-cli not on PATH — text-search fallback active (same as
prior shift; declaring per S1.9).

STEP 1: picked FTF-0002 (Add formatting/lint/type checks) — the only
item QUEUE.md's Active/Queue guidance names as next-unblocked now that
SOURCE_ROOTS is filled.

STEP 2 (P only — stopped before E): read docs/TASK_INDEX.md's FTF-0002 row
(title + deps + AF/Q tags only, no acceptance detail),
docs/architecture/CRITERION_MAPPING.md's "Known gap" section (explicitly:
FTF-0001 is the *only* item with enough detail to execute without a
BLOCKER.md stop — confirmed via `git log` that this file hasn't been
touched since the initial scaffold commit, so it's still current policy,
not stale), docs/architecture/ for any concrete lint/type tooling
decision (none found), and .github/workflows/ci.yml's own placeholder
step (`echo "TODO: fill once FTF-0001/0002/0003 land"`, confirming the
stack choice genuinely hasn't been made anywhere in the repo). Checked
the rest of QUEUE.md's dependency-ordered queue (FTF-0003, FTF-0005,
FTF-0101…) for any item with more detail than a title+deps+tags row: none
found — same gap applies across the board.

Did not write any test or touch source files — per CLAUDE.md's "Relay is
execution-only" law and the scheduled task's own RELAY ROLE instruction,
inventing FTF-0002's tooling choice and acceptance criteria myself would
be improvising the DoD, which this session does not have standing to do.
Wrote BLOCKER.md instead (three route options for control plane/owner)
and stopped per STEP 4(b).

## Next shift
Top item: whichever of BLOCKER.md's three route options control
plane/owner picks (most likely: control plane pins an explicit tooling
decision for FTF-0002 into QUEUE.md Active, mirroring how FTF-0001 became
executable without the full Section 85 contract). Until then, no
QUEUE.md item is safely pickable by the relay without improvising.
Blocked: yes — see BLOCKER.md.

## Metrics
Certified / denominator: 1/300 (unchanged this shift)
Product-true (Law 16): 0 — unchanged, must match certified
L4 gate: not run (Tier 3, release-only — relay never dispatches it)
Knowledge graph: not set up (cognee-cli not found this shift; still
text-search fallback per S1.9)

## Open owner asks
Gap #2 (2h relay / watchdog trigger tooling) is still open — see
QUEUE.md "Owner actions — current". Unchanged this shift.
New (via BLOCKER.md, not owner-only — control plane can resolve route 1
without the owner): FTF-0002 (and by extension FTF-0003, FTF-0005, and
most of the queue) has no executable DoD until Section 85 detail is
supplied or a documented lighter-weight bootstrap-task convention is
adopted. See BLOCKER.md for full detail and three route options.

---

## Shift 1 (2026-09-15) — historical
Completed: FTF-0001 — Initialize monorepo — CERTIFIED (L2, control-plane,
2026-09-15, independently re-certified after a self-cert finding was
corrected same day). Merged as PR #3, merge commit
7ab3f6ff1ac7bcc240e546b764af8d67ae0c8561; ledger entries in
docs/certifications/ledger.md (both the original and the corrected
re-certification). Not yet PROMOTED — L4 gate is release-only and hasn't
run.
PRs: #3 (FTF-0001 producer), #4 (unrelated CI YAML fix), #5 (self-cert,
later reverted), #6 (CONTROL_PLANE_MODES.md), #8 (Read-first wiring +
hash re-pin), #9 (hash-mismatch BLOCKER.md stop), #10 (SOURCE_ROOTS
re-derivation), #11 (self-verifying CLAUDE.md hash), #12 (status.py
non-empty-proving_symbol fix), #13 (watchdog: self-cert finding logged),
#14 (independent re-certification of FTF-0001).
Full narrative preserved in git history; not re-copied here to avoid
drift between this file and the commits it describes.
