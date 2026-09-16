# PROGRESS — Sovra
Updated: 2026-09-16T00:00:00Z · Shift: 3 (this shift stopped at STEP 1 —
see "This shift" below; shift 2 covers commits landed between the last
PROGRESS.md update and now, back-filled here since PROGRESS.md wasn't
updated at the time)

## Shift 2 (back-filled, 2026-09-15, commits dd60cef..1b50304)
Not previously logged in PROGRESS.md. Reconstructed from git history and
docs/certifications/{queue,ledger,watchdog-findings}.md so this file stops
understating repo state:
- Re-derived `scripts/product_truth_check.py` SOURCE_ROOTS from FTF-0001's
  real layout (`dd60cef`, PR #10) — apps/web, apps/api,
  workers/{training,evaluation,data,deploy}, packages/shared, replacing the
  stale guessed paths.
- A relay shift stopped on a CLAUDE.md hash mismatch and wrote BLOCKER.md
  (`b8af8ac`, PR #9) — root cause: a prior commit (`c1a3870`) had re-pinned
  CLAUDE.md's internal hash line but the scheduled trigger's own baked-in
  hash copy went stale. Control plane reviewed and confirmed the diagnosis.
- Fix landed same day: replaced the embedded/baked-in hash convention with
  a self-verifying `CLAUDE.md.sha256` file compared via plain `sha256sum`
  (`a010433`, PR #11) — closes the recurring drift class the BLOCKER.md
  found, not just that one instance.
- `status.py` tightened to require a non-empty `proving_symbol` before
  counting a criterion toward product-true (`f9af49e`, PR #12), so a
  criterion can't count as product-true on a technicality.
- Watchdog found FTF-0001's original L2 certification was self-certified
  (PR #3 and PR #5 shared session identity — Law 4 violation): logged
  (`77b3b2f`), FTF-0001 reverted to PROVISIONAL (`3804dfa`), then
  independently re-certified by a session with no session-ID overlap with
  either the original producer or the watchdog (`795cc44`), merged via PR
  #13 (watchdog finding) and PR #14 (recertification). Full trail in
  docs/certifications/{ledger,watchdog-findings}.md. Certified count was
  never inflated by this — it stayed 1/300 throughout, since the correction
  and re-certification landed together.
Net state after shift 2: FTF-0001 CERTIFIED (legitimately, independently
re-verified), SOURCE_ROOTS filled, CLAUDE.md hash self-verifying, no open
blocker.

## This shift (2026-09-16)
STEP 0.1: read PROGRESS.md (this file, found stale — see back-fill above),
QUEUE.md, docs/certifications/queue.md (only the FTF-0001 entry, already
certified — nothing new pending) fresh, not assumed from prior context.
STEP 0.2: `cognee-cli` not found on PATH — text-search fallback still
active, same as every prior shift.
Hash check: `sha256sum CLAUDE.md` = committed `CLAUDE.md.sha256` exactly
(`6ac215c1...4e6dd04`) — no drift. This shift's scheduled prompt carries no
separate baked-in hash to compare against (consistent with the self-
verifying convention landed in shift 2), so there's nothing to reconcile.
STEP 1: QUEUE.md Active is empty. Checked the next item in QUEUE.md's own
dependency read order, FTF-0002 (lint/type checks) — dependency-unblocked
(FTF-0001 CERTIFIED, SOURCE_ROOTS filled), but
`docs/architecture/CRITERION_MAPPING.md` "Known gap" states FTF-0001 was
the *only* item supplied with enough detail to pick up without a
BLOCKER.md stop; every other item still lacks the source spec's "Section
85" acceptance/negative-acceptance/verification content. This shift's own
prompt repeats the same fact. No item this shift is authorized to pick up
without improvising a DoD.
Verified before stopping (not assumed): `python3 scripts/status.py` → `1/300
certified · 0 product-true`; `python3 scripts/product_truth_check.py
--fail-on-unwired` → `0/0 product-true`, exit 0; `docs/spec-matrix.yaml`
FTF-0001 row shows `certification_state: CERTIFIED`,
`proof_state: L2-CERTIFIED` — all consistent with the ledger, no drift to
report.
Wrote `BLOCKER.md` ("No Active item has a full DoD contract" entry) with
what was checked and three route options for control plane/owner. Added a
dated entry to QUEUE.md "Owner actions — current" pointing at it (removed
the moment it's resolved, per QUEUE.md's own rule). Did not touch
`docs/spec-matrix.yaml` or `docs/certifications/queue.md` — nothing was
produced or certified this shift.
Stopping per STEP 4(b): true blocker, DoD unclear, do not improvise.

## Next shift
Top item: whatever control plane/owner resolves via BLOCKER.md's three
route options (supply Section 85 content for FTF-0002+, hand-write a
scoped DoD for FTF-0002 directly in QUEUE.md Active, or point to where
Section 85 actually lives). Until QUEUE.md Active has an item with a full
contract, the relay has nothing new to execute.
Blocked: yes — see BLOCKER.md and QUEUE.md "Owner actions — current".

## Metrics
Certified / denominator: 1/300 (unchanged this shift)
Product-true (Law 16): 0 — must match certified; FTF-0001 has no
proving_symbol so it doesn't count toward product-true either way
L4 gate: not run (Tier 3, release-only — relay never dispatches it)
Knowledge graph: not set up (cognee-cli still not found; text-search
fallback per S1.9)

## Open owner asks
See QUEUE.md "Owner actions — current" — the single source of truth for
open asks (per CLAUDE.md "Owner actions live in ONE place"); not duplicated
here.
