# PROGRESS — Sovra
Updated: 2026-09-16T00:00:00Z · Shift: relay, 2026-09-16 (blocked — see below)

## Last shift (recap, corrected — PROGRESS.md had gone stale)
This file previously still read "Shift: 1" (2026-09-15) even though seven
more PRs (#7–#14) merged after it was last narrated. For the true current
state, QUEUE.md's "Done" section and docs/certifications/ledger.md are
authoritative, not this file's old prose. Summary of what actually
happened since Shift 1, from `git log`/ledger/watchdog-findings:
- PR #5: FTF-0001 given an L2 certification — later found **self-certified**
  (PR #3 and PR #5 shared session identity, Law 4 violation) by a watchdog
  audit (PR #13, `docs/certifications/watchdog-findings.md`).
- PR #14 (`795cc44`): independent re-certification from a session with no
  ID overlap with either the original producer/self-certifier or the
  watchdog — re-verified PR #3's evidence from scratch (fault-injection
  re-run, real CI run fetched via API, file diff re-read) and re-issued
  FTF-0001 as L2-CERTIFIED. Both the original and corrected ledger entries
  are kept in `docs/certifications/ledger.md` (superseded, not deleted).
- PR #10 (`dd60cef`): re-derived `SOURCE_ROOTS` in
  `scripts/product_truth_check.py` from FTF-0001's real layout.
- PR #11 (`a010433`): replaced CLAUDE.md's embedded self-reference hash
  with a separate `CLAUDE.md.sha256` file, so any session's plain
  `sha256sum CLAUDE.md` check can never drift from the file's own committed
  pin. This is what resolved the 2026-09-15 BLOCKER.md hash-mismatch entry
  (see BLOCKER.md's "Superseded" section for detail).
- PR #12 (`f9b1c...`/`f9c...`): `status.py` fix requiring a non-empty
  `proving_symbol` for the product-true count.
Certified: 1/300 (FTF-0001, independently re-certified) · Product-true: 0
(FTF-0001 has no `proving_symbol` — structural layout, not a live-wired
code path, so it's excluded from that count by design, not a gap).

## This shift (2026-09-16)
STEP 0.1: read PROGRESS.md (found stale — see recap above),
QUEUE.md, docs/certifications/queue.md.
STEP 0.2: `cognee-cli` not found on PATH — text-search fallback active
(same as every prior shift; no Cognee MCP recall used).
Hash check: `sha256sum CLAUDE.md` vs. committed `CLAUDE.md.sha256` —
match (`6ac215c16e9adac14ed2f7b88c93e615a3c5f10bc9bab3dfd6b5893174e6dd04`).
No mismatch; not a stop condition this shift.
STEP 1: QUEUE.md Active is empty. Checked the next items in dependency
order (FTF-0002 lint/type checks, FTF-0005 config): both
`implementation_state: ABSENT`, no `proving_symbol`/`proving_test` in
docs/spec-matrix.yaml, and `docs/architecture/CRITERION_MAPPING.md`
"Known gap" explicitly documents that no item past FTF-0001 has a
supplied acceptance/negative-acceptance/verification contract (the
"Section 85" content referenced by `docs/TASK_INDEX.md` line 327 was never
supplied to this scaffold). Confirmed via `git log` that no shift since
FTF-0001 merged has actually attempted FTF-0002+ — all of PRs #5–#14 were
governance/self-correction on FTF-0001 itself.
STEP 2: not reached — no item to execute (Relay is execution-only; does
not improvise a DoD).
STEP 4(b)/(e): true blocker + ask-first item. Wrote BLOCKER.md documenting
the DoD gap and three route options. Added a dated entry to QUEUE.md
"Owner actions — current" for this gap — it had been silently blocking
every shift since FTF-0001 without ever being logged there. Stopping shift
here; no source or spec-matrix changes made.

## Next shift
Top item: same STEP 1 pick (FTF-0002) remains blocked until control plane
or the owner responds to one of BLOCKER.md's three route options (supply
Section 85 content; control plane authors FTF-0002's DoD directly and
hands it to the relay as a decided item; or formally scope Release 1 down
to FTF-0001). Next relay shift: re-check QUEUE.md "Owner actions — current"
first — if this entry is gone, the owner responded; proceed from whatever
route they picked. If still present, this is expected to block again;
don't rediscover it from scratch, reference this entry and BLOCKER.md.
Blocked: yes — see BLOCKER.md ("No unblocked item has a supplied DoD").
Also still open: 2h relay/watchdog trigger-tooling gap (unrelated,
pre-existing, see QUEUE.md).

## Metrics
Certified / denominator: 1/300
Product-true (Law 16): 0 — matches certified (FTF-0001 excluded by design,
no proving_symbol claimed)
L4 gate: not run (Tier 3, release-only — relay never dispatches it)
Knowledge graph: not set up (cognee-cli not found this shift; still
text-search fallback per S1.9)

## Open owner asks
See QUEUE.md "Owner actions — current" for the authoritative, single-source
list (per CLAUDE.md "Owner actions live in ONE place"). As of this shift:
1. 2h relay/watchdog trigger-tooling gap (open since 2026-09-15).
2. DoD/Section-85 contract gap blocking all items past FTF-0001 (opened
   this shift, 2026-09-16 — see BLOCKER.md for full detail).
