# Certification Queue
Relay appends PROVISIONAL items here for the Control plane to certify.

## 2026-09-15 — FTF-0001 — Initialize monorepo
- **PR:** relay/ftf-0001-init-monorepo → main (opened this shift; see PR link
  in PROGRESS.md once assigned)
- **Commit:** 00bacb5c992938e47ad4b294c77bf3e48c5c203e
- **Test:** tests/test_ftf_0001_monorepo_layout.py — 26 checks
- **Evidence:** run against pre-scaffold tree → 26/26 FAIL (root config +
  all seven ARCHITECTURE.md §2 component dirs missing); run again after this
  commit's files only → 0 failures, exit 0. product_truth_check.py run
  clean (0/0 — no proving_symbol claimed; this criterion is structural
  layout, not a live-wired code path).
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.
- **Note for Control plane:** once this merges, scripts/product_truth_check.py
  SOURCE_ROOTS should be filled from this real layout (apps/web, apps/api,
  workers/{training,evaluation,data,deploy}, packages/shared) — currently
  left commented per the script's own instruction to fill it "the moment
  FTF-0001 merges," not before.
- **Note for Control plane (owner-ask review):** this session had working
  GitHub MCP write access (branch/commit/push/PR) end-to-end — QUEUE.md's
  "Owner actions — current" setup-gap item #1 (no GitHub connector to push
  the scaffold) may be stale. Not marking it resolved myself — flagging for
  Control plane/owner to verify and update QUEUE.md, per the prior watchdog
  finding on unverified "gap resolved" claims. Setup gap #2 (2h relay /
  watchdog trigger tooling) is unaffected — this session has no
  create_trigger/fire_trigger tooling.
