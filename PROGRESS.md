# PROGRESS — Sovra
Updated: 2026-09-15T21:00:00Z · Shift: relay, 2026-09-15 (blocked, no code
change — see BLOCKER.md)

## This shift (relay)
STEP 0.1: read PROGRESS.md, QUEUE.md, docs/certifications/queue.md,
docs/certifications/watchdog-findings.md. State as of HEAD `1b50304`:
FTF-0001 CERTIFIED (independently re-certified after a self-certification
finding — see ledger.md and watchdog-findings.md, both already
dispositioned, nothing new to review there). Certified: 1/300,
product-true: 0.
Hash check: `sha256sum CLAUDE.md` == committed `CLAUDE.md.sha256`
(`6ac215c1...`) — clean, no drift.
Resolved a stale BLOCKER.md left over from an earlier same-day shift
(CLAUDE.md-hash-mismatch incident, PR #9): its root-cause fix (PR #11,
self-verifying `CLAUDE.md.sha256` convention) is now confirmed working —
this shift's hash check passed cleanly under the new convention. Cleared
that entry.
STEP 1: QUEUE.md Active was empty; SOURCE_ROOTS (the stated blocker on
FTF-0002) had already been re-derived (PR #10), but FTF-0002 still has no
Section 85 acceptance/negative-acceptance/verification contract per
`docs/architecture/CRITERION_MAPPING.md` "Known gap" (true of every
remaining task, not just FTF-0002 — confirmed no Section 85 content exists
anywhere in the tree). Declining to improvise a DoD per CLAUDE.md "Relay is
execution-only". Wrote a fresh BLOCKER.md with three route options and
added the gap to QUEUE.md "Owner actions — current" (it had never been
surfaced there before, only documented passively in CRITERION_MAPPING.md
since scaffold creation).
STEP 3: this file, before stopping.
STEP 4: stop condition (b) — true blocker (DoD unclear for every
dependency-unblocked item). No code changed. No PR opened this shift.

## Last shift (pre-existing, kept for history)
Completed: FTF-0001 — Initialize monorepo — CERTIFIED (L2, control-plane,
2026-09-15). Merged as PR #3, merge commit
7ab3f6ff1ac7bcc240e546b764af8d67ae0c8561; ledger entry in
docs/certifications/ledger.md. Not yet PROMOTED — L4 gate is release-only
and hasn't run.
PRs: https://github.com/SJZLab/Sovra/pull/3 (merged),
https://github.com/SJZLab/Sovra/pull/4 (merged — unrelated pre-existing
workflow-YAML fix needed before PR #3 could get real CI)
Certified: 1/300 · Product-true: 0

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
Top item: re-derive SOURCE_ROOTS in scripts/product_truth_check.py from
FTF-0001's real layout (apps/web, apps/api,
workers/{training,evaluation,data,deploy}, packages/shared), then FTF-0002
(lint/type checks) and FTF-0005 (config) become the next candidates per
QUEUE.md's dependency read order.
Blocked: none.

## Metrics
Certified / denominator: 1/300
Product-true (Law 16): 0 — must match certified; FTF-0001 has no
proving_symbol so it is CERTIFIED without counting toward product-true
L4 gate: not run (Tier 3, release-only — relay never dispatches it)
Knowledge graph: not set up (cognee-cli not found this shift; still
text-search fallback per S1.9)

## Open owner asks
2026-09-15: Owner resolved setup gap #1 (GitHub connector) — repo
push/PR/merge access is solved via a Claude Code terminal session (6 PRs
merged, real CI verified this shift); no longer a blocker for
relay/certification work. Removed from QUEUE.md "Owner actions — current"
per the "remove the moment the owner responds" rule. Separately and
explicitly *not* tracked here: the owner also noted the GitHub connector
inside claude.ai chat itself still errors on repo access — downgraded by
the owner to a known product bug worth periodic re-checking, not a Sovra
blocker, so it stays out of QUEUE.md.
Gap #2 (2h relay / watchdog trigger tooling) is still open — see QUEUE.md
"Owner actions — current".
Not yet done, flagged for the owner but not acted on unilaterally:
CLAUDE.md's OPERATING MODE FLAGS (`GIT_CONNECTOR: none`, `GIT_WRITE: none`)
and "Setup gaps" section describe the pre-resolution state and are now
stale. Editing CLAUDE.md changes its hash-pinned content (drift tripwire),
so this wasn't done without being asked.
