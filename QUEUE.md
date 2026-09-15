# QUEUE — Sovra (Model Factory)
Updated: 2026-09-15 by control-plane
Relay: 2h · Report: 08:30 & 19:30 Asia/Riyadh · Certifier: Control plane (autonomous)

## Status
Certified: 1/300 · Provisional: 0 · Blocked: 0
Product-true (Law 16): 0 — must match certified (FTF-0001 has no
proving_symbol; it is structural layout, not a live-wired path, so it is
CERTIFIED without counting toward product-true)

## Owner actions — current
- 2026-09-15: One setup gap remains — no trigger tooling available to
  create the 2h relay / watchdog triggers. See CLAUDE.md "Setup gaps".
  Owner must create them (or grant a control-plane session trigger
  tooling and re-run Groundwork S1.7/S1.8).

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
