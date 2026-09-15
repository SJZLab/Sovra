# Certification Queue
Relay appends PROVISIONAL items here for the Control plane to certify.

## 2026-09-15 — FTF-0001 — Initialize monorepo
- **PR:** https://github.com/SJZLab/Sovra/pull/3 (relay/ftf-0001-init-monorepo → main)
- **Commit:** 00bacb5c992938e47ad4b294c77bf3e48c5c203e
- **Test:** tests/test_ftf_0001_monorepo_layout.py — 26 checks
- **Evidence:** run against pre-scaffold tree → 26/26 FAIL (root config +
  all seven ARCHITECTURE.md §2 component dirs missing); run again after this
  commit's files only → 0 failures, exit 0. product_truth_check.py run
  clean (0/0 — no proving_symbol claimed; this criterion is structural
  layout, not a live-wired code path).
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.
- **CI (real, not local-only):** PR head merged with `main` at commit
  `2f63b7f` (brings in the `.github/workflows` YAML-quoting fix from #4 —
  see below); GitHub Actions run
  https://github.com/SJZLab/Sovra/actions/runs/34995170054, job `gate`,
  conclusion `success`, all 7 real steps ran (not the earlier 0-job parse
  failures). Actual log lines: `Product-truth: 0/0 product-true` and
  `Status: 0/300 certified · 0 product-true`, matching the local run
  above exactly. Note: the `Typecheck + lint + unit` step is still the
  FTF-0002/0003 placeholder (`echo "TODO: ..."`) — it succeeds trivially
  because it doesn't check anything yet, not because lint/type/tests pass.
- **Unrelated fix landed first:** all three `.github/workflows/*.yml` files
  on `main` had a YAML syntax bug (unquoted `run: echo "TODO: ..."` with
  an embedded `: `) that made every workflow fail to parse — 0 jobs, not a
  code failure. Fixed separately in PR #4 (merged as `6cf3d27`), verified
  with a real green `pull_request`-triggered run before merging, then
  brought into this branch via a merge commit so PR #3's own CI could run
  for real. Full diagnosis and fix are in PR #4, not repeated here.
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
