# PROGRESS — Sovra
Updated: 2026-09-15T00:00:00Z · Shift: 1 (first relay shift to actually execute an item)

## Last shift
Completed: FTF-0001 — Initialize monorepo (L1-PROVEN, PROVISIONAL — not
self-certified)
PRs: https://github.com/SJZLab/Sovra/pull/3 (relay/ftf-0001-init-monorepo → main)
Certified: 0/300 · Product-true: 0

## This shift
STEP 0.1: read PROGRESS.md, QUEUE.md, docs/certifications/queue.md — all as
of prior scaffold-only state (0/300, no relay shift had run).
STEP 0.2: cognee-cli not found on PATH — text-search fallback active
(product_truth_check.py's deterministic text search only; no Cognee MCP
recall used this shift).
Hash check: scheduled-task-asserted CLAUDE.md hash
(sha256:68f978e58d9cd6a98e57e4447bbd97960e5c0d5e87acd7be840bfcb8600c39a7)
verified against `sha256sum CLAUDE.md` on the actual checked-out file —
match, no drift. (CLAUDE.md's own internal self-reference line reads a
different, stale placeholder value — the file explicitly documents that
line as non-self-verifying, so it is not the check that matters here.)
STEP 1: picked FTF-0001 (only Active item; only item with a full-enough
contract per docs/architecture/CRITERION_MAPPING.md "Known gap").
STEP 2 (PEV):
  P — read SPEC.md, docs/architecture/ARCHITECTURE.md (seven components),
      docs/architecture/REFERENCE_REPOSITORIES.md, docs/adr/0001, and
      docs/spec-matrix.yaml's FTF-0001 row. No stack was pinned anywhere in
      the repo, so I chose conventional, low-risk tooling: pnpm workspace
      for the JS/TS side (apps/web, packages/shared) and a uv workspace for
      the Python side (apps/api, workers/{training,evaluation,data,deploy})
      — Python because Axolotl/Inspect AI/Argilla, which the workers wrap,
      are Python packages. This is scaffolding (manifests + READMEs), not
      an implementation, so it doesn't lock in a framework choice.
  E — wrote tests/test_ftf_0001_monorepo_layout.py FIRST, ran it against
      the pre-scaffold tree: 26/26 checks failed (output bound to
      criterion ID FTF-0001, see commit message). Then created the root
      workspace config (README.md, package.json, pnpm-workspace.yaml,
      pyproject.toml, .gitignore) and the seven component directories from
      ARCHITECTURE.md §2, each with a README + manifest. Re-ran the same
      test: 0 failures. Committed as 00bacb5c992938e47ad4b294c77bf3e48c5c203e.
  V — ran `python3 scripts/product_truth_check.py --fail-on-unwired`: clean
      (0/0 — FTF-0001 claims no proving_symbol; it's structural layout, not
      a live-wired code path, so there's no live-caller assertion to fault-
      inject against). Opened PR against main from
      relay/ftf-0001-init-monorepo (never pushed directly to main, per
      CLAUDE.md's hard instruction-tier norm). Marked PROVISIONAL. Appended
      to docs/certifications/queue.md for Control plane.
Updated docs/spec-matrix.yaml FTF-0001: implementation_state=IMPLEMENTED,
proof_state=L1-PROVEN, certification_state=PROVISIONAL,
proving_test=tests/test_ftf_0001_monorepo_layout.py, evidence_hash=commit
sha above. Did NOT set certification_state=CERTIFIED (Law 4 — producer
never certifies own work) and did NOT fill
scripts/product_truth_check.py's SOURCE_ROOTS yet (its own comment says to
fill it "the moment FTF-0001 merges" — this PR hasn't merged).
Did NOT edit QUEUE.md's "Owner actions — current" block myself. This
session did have working GitHub MCP write access end-to-end (branch,
commit, push, PR) — flagged that as a note for Control plane in
docs/certifications/queue.md rather than declaring the connector setup gap
resolved unilaterally, given the prior watchdog finding
(docs/certifications/watchdog-findings.md, 2026-09-15) about exactly this
kind of unverified "gap resolved" claim.

## Next shift
Top item: FTF-0001 awaits Control plane L2 certification + merge. Once
merged: re-derive SOURCE_ROOTS in scripts/product_truth_check.py from the
real layout, then FTF-0002 (lint/type checks) and FTF-0005 (config) become
the next candidates per QUEUE.md's dependency read order.
Blocked: none. Stopping this shift per stop condition (a) — this item is
now N/N L1-PROVEN with nothing else unblocked; awaiting Control plane
certification and (eventually) the L4 gate before PROMOTE.

## Metrics
Certified / denominator: 0/300 (1 PROVISIONAL, pending L2)
Product-true (Law 16): 0 — must match certified; FTF-0001 has no
proving_symbol so it is not counted as product-true either way
L4 gate: not run (Tier 3, release-only — relay never dispatches it)
Knowledge graph: not set up (cognee-cli not found this shift; still
text-search fallback per S1.9)

## Open owner asks
See QUEUE.md "Owner actions — current" (unchanged this shift — two setup
gaps, dated 2026-09-15, still there pending owner response; a note about
gap #1 possibly being stale was added to docs/certifications/queue.md for
Control plane to verify, not asserted as resolved).
