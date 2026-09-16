# PROGRESS — Sovra
Updated: 2026-09-16T00:00:00Z · Shift: 2 (catch-up + stop; shifts covering
PRs #5–#14 ran without updating this file per STEP 3 — reconstructed from
git history below rather than left stale)

## Last shift (reconstructed from git history — PRs #5 through #14)
Work landed on `main` since Shift 1's PROGRESS.md entry (PR #3/#4, 1/300
certified), none of it recorded here at the time:
- PR #5 (0f58cef): FTF-0001 moved to `certification_state=CERTIFIED`,
  `proof_state=L2-CERTIFIED` — later found to be **self-certified** (same
  session authored PR #3 and PR #5, a Law 4 violation) by watchdog audit,
  logged in `docs/certifications/watchdog-findings.md`.
- PR #6 (c6e0251): added `docs/CONTROL_PLANE_MODES.md` (OPERATE/CERTIFY/
  UNBLOCK procedure).
- PR #7 (ed1bac0): resolved owner action — GitHub connector setup gap
  (push/PR/merge access confirmed working via Code CLI session).
- PR #8 (c1a3870): wired `docs/CONTROL_PLANE_MODES.md` into CLAUDE.md
  Read-first, re-pinned CLAUDE.md's (then still embedded) self-hash.
- PR #10 (dd60cef): re-derived `scripts/product_truth_check.py`
  `SOURCE_ROOTS` from FTF-0001's real layout (`apps/web`, `apps/api`,
  `workers/{training,evaluation,data,deploy}`, `packages/shared`).
- PR #9 (b8af8ac): a relay shift hit a CLAUDE.md hash mismatch (fallout
  from #8's re-pin not being reflected in the scheduled trigger's baked-in
  hash) and correctly stopped per the hard-stop rule, writing BLOCKER.md
  rather than proceeding.
- PR #11 (a010433): root-caused the #9 incident — the embedded
  self-hash convention could never equal a plain `sha256sum`. Replaced it
  with a committed `CLAUDE.md.sha256` file + CI enforcement (this is the
  fix architecturally recorded as `docs/adr/0002` by *this* shift, since
  #11 itself didn't add the ADR entry `docs/CONTROL_PLANE_MODES.md`'s
  UNBLOCK step 4 calls for).
- PR #12 (f9af49e): `scripts/status.py` now requires a non-empty
  `proving_symbol` for the product-true count (tightens Law 16 accounting).
- Watchdog audit (77b3b2f) found FTF-0001's PR #5 certification was
  self-certified; PR #14 (3804dfa, 795cc44) reverted it to PROVISIONAL and
  then had an independently-verified session (confirmed distinct session
  ID from both PR #3/#5 and the watchdog's PR #13) redo the CERTIFY
  procedure from scratch — re-read the PR diff, re-fetched the CI run
  directly from the Actions API, re-ran the test locally including a
  fault-injection redenning check, re-confirmed the 0/0 product-truth
  result was legitimate for a no-`proving_symbol` criterion — and
  re-certified L2-CERTIFIED. Both the original (superseded) and corrected
  ledger entries are preserved in `docs/certifications/ledger.md`.
- PR #13 (77b3b2f, merged 1b50304): logged the above self-certification
  finding to `docs/certifications/watchdog-findings.md`.

Net effect on tracked state: FTF-0001 is CERTIFIED (1/300), legitimately
this time (independent re-certification, session-ID-verified). Certified
count was never inflated across this sequence — it stayed 1/300 throughout
the self-cert → revert → re-cert cycle.

## This shift (2026-09-16)
STEP 0.1: read PROGRESS.md (this file — found stale, last genuinely
updated at PR #7, six PRs behind `main` HEAD), QUEUE.md (current — last
updated at the 795cc44 re-certification commit), `docs/certifications/queue.md`
(current — only entry is FTF-0001/PR #3, already certified, nothing
pending).
STEP 0.2: `cognee-cli` not found on PATH — text-search fallback active
(same as Shift 1; no change).
Hash check: `sha256sum CLAUDE.md` == committed `CLAUDE.md.sha256`
(`6ac215c16e9adac14ed2f7b88c93e615a3c5f10bc9bab3dfd6b5893174e6dd04`) — match,
no drift. This shift's scheduled prompt does not embed a hash value at all
(compares against the committed file directly), confirming the PR #11 fix
is working as intended.
Ran both gate scripts locally to confirm no drift before touching anything:
`python3 scripts/status.py` → `Status: 1/300 certified · 0 product-true`,
exit 0. `python3 scripts/product_truth_check.py --fail-on-unwired` →
`Product-truth: 0/0 product-true`, exit 0.
STEP 1: QUEUE.md "Active" is empty. The only named next candidate,
FTF-0002, lacks a full DoD contract per `docs/architecture/CRITERION_MAPPING.md`
"Known gap" (present since the initial scaffold, not new) — FTF-0001 is
explicitly documented as the *only* item with enough detail to execute
without a BLOCKER.md stop. Did not pick FTF-0002 or improvise its DoD.
STEP 2: not entered — no item to run PEV against this shift.
Housekeeping performed instead (all within "Do without asking" — read/edit
non-secret files):
- Cleared the resolved 2026-09-15 hash-mismatch entry in BLOCKER.md,
  recorded verification that both its route options (2 and 3) are now
  satisfied.
- Added `docs/adr/0002-self-verifying-claude-md-hash.md` — the ADR
  `docs/CONTROL_PLANE_MODES.md`'s UNBLOCK step 4 required for the PR #11
  fix but that PR never added.
- Checked whether this session's `CronCreate` tool closes the standing
  "no trigger tooling" setup gap — it does not (session-scoped, 7-day
  auto-expiry, no durable cross-session cadence) — documented as
  checked-and-insufficient in BLOCKER.md and QUEUE.md rather than declared
  resolved, to avoid repeating the exact false "gap resolved" pattern
  already logged in `docs/certifications/watchdog-findings.md`.
- Added a new BLOCKER.md entry and a new QUEUE.md "Owner actions" entry:
  every item past FTF-0001 needs its full Section 85 contract (or a
  control-plane-authored DoD in QUEUE.md Active) before relay can proceed.
- Wrote this PROGRESS.md catch-up so the next shift isn't reading
  six-PRs-stale state again.
No code, tests, or spec-matrix.yaml changes this shift — nothing was
certified, promoted, or claimed proven. Committed housekeeping/doc changes
on `claude/blissful-goodall-i02ft7`, opened as a PR against `main` per
CLAUDE.md's no-direct-push-to-main norm.

## Next shift
Top item: cannot proceed on FTF-0002 (or anything past FTF-0001) without
either (a) Section 85 acceptance/negative-acceptance/verification content
for it, or (b) a control-plane-authored DoD written directly into
QUEUE.md "Active". See BLOCKER.md "New stop (2026-09-16)" for full detail
and three route options. Until one of those lands, every relay shift
should expect to hit this same stop — that is expected, not a new failure,
and should not be re-diagnosed from scratch each time.
Blocked: yes — see above. Not a code/CI blocker; a missing-input blocker.

## Metrics
Certified / denominator: 1/300 (unchanged this shift)
Product-true (Law 16): 0 — must match certified; FTF-0001 has no
proving_symbol so it is CERTIFIED without counting toward product-true
L4 gate: not run (Tier 3, release-only — relay never dispatches it)
Knowledge graph: not set up (cognee-cli not found this shift; still
text-search fallback per S1.9)

## Open owner asks
See QUEUE.md "Owner actions — current" for the live, authoritative list
(two items as of this shift: durable trigger tooling; Section 85 task
contracts). Per CLAUDE.md, owner asks live only there — not duplicated in
item history here once resolved.
