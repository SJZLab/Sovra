# QUEUE — Sovra (Model Factory)
Updated: 2026-09-15 by control-plane
Relay: 2h · Report: 08:30 & 19:30 Asia/Riyadh · Certifier: Control plane (autonomous)

## Status
Certified: 0/300 · Provisional: 1 (FTF-0001) · Blocked: 0
Product-true (Law 16): 0 — must match certified (FTF-0001 has no
proving_symbol; it is structural layout, not a live-wired path)

## Owner actions — current
- 2026-09-15: Two setup gaps block relay start — (1) no GitHub connector
  available to push this scaffold to SJZLab/Sovra, (2) no trigger tooling
  available to create the 2h relay / watchdog triggers. See CLAUDE.md
  "Setup gaps". Push this scaffold, then create the triggers (or grant a
  control-plane session both a git-host connector and trigger tooling and
  re-run Groundwork S1.7/S1.8).

## Active (relay works this)
- [~] FTF-0001 — Initialize monorepo (no deps; requirements AF-31, AF-35;
      scenarios Q01, Q26). L1-PROVEN this shift (2026-09-15), PR opened as
      PROVISIONAL: https://github.com/SJZLab/Sovra/pull/3, appended
      to docs/certifications/queue.md. Awaiting Control plane L2
      certification — not self-certified (Law 4). Nothing else unblocks
      until this merges and SOURCE_ROOTS is re-derived from the real layout.

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
