# QUEUE — Sovra (Model Factory)
Updated: 2026-09-16 by relay
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
  trigger tooling and re-run Groundwork S1.7/S1.8). Checked 2026-09-16:
  this session has session-scoped `CronCreate` tooling, but those jobs are
  in-memory and auto-expire after 7 days — they cannot provide the
  persistent cross-session cadence this needs, so the gap stays open
  rather than being declared resolved on tooling that can't actually
  deliver it (see BLOCKER.md).
- 2026-09-16: Every remaining QUEUE.md item past FTF-0001 (starting with
  FTF-0002) lacks the full acceptance/negative-acceptance/verification
  contract ("Section 85" per docs/TASK_INDEX.md and
  docs/architecture/CRITERION_MAPPING.md "Known gap") a relay shift needs
  to execute it without improvising the DoD. This is not a new problem —
  CRITERION_MAPPING.md has said so since the initial scaffold — but it is
  now the actual blocker on forward progress since FTF-0001 is the only
  task completed. Owner/control-plane must either supply Section 85
  content (at minimum for FTF-0002) or write a fully-specified DoD
  directly into this file's "Active" section. See BLOCKER.md for full
  detail and route options. Relay will not improvise a DoD in the
  meantime.

## Active (relay works this)
Nothing active — FTF-0001 merged and certified (see Done below). Next
unblocked item is FTF-0002 (lint/type checks) once SOURCE_ROOTS is
re-derived from the real layout landed by FTF-0001.

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

Most task rows cite a `requirements` (CAP-##/AF-##) and `scenarios` (Q##) pair
whose full acceptance contract lives outside this scaffold (see
docs/architecture/CRITERION_MAPPING.md "Known gap"). Relay: if a picked item's
full contract isn't available to you, write BLOCKER.md — do not improvise.

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
