# QUEUE — Sovra (Model Factory)
Updated: 2026-09-16 by control-plane
Relay: 2h · Report: 08:30 & 19:30 Asia/Riyadh · Certifier: Control plane (autonomous)

## Status
Certified: 1/300 · Provisional: 0 · Blocked: 0
Product-true (Law 16): 0 — must match certified (FTF-0001 has no
proving_symbol; it is structural layout, not a live-wired path, so it is
CERTIFIED without counting toward product-true)

## Owner actions — current
- 2026-09-15: One setup gap remains — no *durable* trigger tooling
  available to create the 2h relay / watchdog triggers. See CLAUDE.md
  "Setup gaps". Owner must create them (or grant a control-plane session
  trigger tooling and re-run Groundwork S1.7/S1.8). Note: this shift's
  own session was itself invoked by some external schedule (its prompt
  says "started automatically by a schedule"), so a trigger mechanism
  exists for the relay cadence at minimum — not confirming this gap
  resolved without evidence of the watchdog trigger and the 08:30/19:30
  report cadence too, per the prior watchdog finding on unverified
  "gap resolved" claims. Owner: please confirm what's actually configured.
## Active (relay works this)
FTF-0002 (Add formatting/lint/type checks) — dependency FTF-0001 satisfied
and certified. Real DoD resolved via
`python tools/r1_handoff.py packet FTF-0002 --plan FINE_TUNING_FACTORY_RELEASE_1.md`
→ `docs/implementation/task-packets/FTF-0002.md`. Not claimed/worked this
shift per the sharing session's explicit instruction (doc/tooling PR only,
let the relay pick it up on its normal next fire).

## Queue (ordered by dependency)
Full 300-record dependency graph: `docs/TASK_INDEX.md` (canonical) and
`docs/spec-matrix.yaml` (proof-state per record). Do not re-copy the table
here — two copies drift. Read order for early work:
1. FTF-0001 → FTF-0002 → FTF-0003 (bootstrap, lint/type, test harness)
2. FTF-0005 (config) → FTF-0004 (docker local stack) → FTF-0006 (observability)
3. FTF-0101…0106 (identity/tenancy/RBAC/audit — CAP-01)
4. FTF-R001/R002 (freeze seven-component architecture + authenticated bindings —
   do this early; every reuse_architecture and most audit_hardening tasks depend
   on it transitively)
5. FTF-H001/H002 (release-scope freeze) before anything claims DONE

Every task's real acceptance/negative-acceptance/verification-command contract
now lives in `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolvable on demand. Relay:
before claiming any task, run
`python tools/r1_handoff.py packet <task-id> --plan FINE_TUNING_FACTORY_RELEASE_1.md`
to get its real DoD — read the generated
`docs/implementation/task-packets/<task-id>.md`, not just the title in
`docs/TASK_INDEX.md`. Write BLOCKER.md only if that command itself fails
(unknown task ID, hash-mismatch on a tampered/edited plan file, or similar) —
not merely because the task looked title-only elsewhere.

## Blocked

## Done (certified + promoted via L4)
- [x] FTF-0001 — Initialize monorepo. CERTIFIED 2026-09-15 (control-plane
      L2). PR: https://github.com/SJZLab/Sovra/pull/3, merge commit
      7ab3f6ff1ac7bcc240e546b764af8d67ae0c8561. Ledger:
      docs/certifications/ledger.md. Not yet PROMOTED — L4 gate is
      release-only and hasn't run.
      Note: the original 2026-09-15 L2 certification was found
      self-certified (watchdog PR #13 — PR #3 and PR #5 share session
      identity, Law 4 violation), briefly reverted to PROVISIONAL, then
      independently re-verified and re-certified same day by a session
      with no session-ID overlap with either PR #3/#5 or PR #13. See
      docs/certifications/ledger.md for both entries. Count below was
      never inflated by this — it stayed 1/300 before and after, since
      the correction and re-certification landed together.
