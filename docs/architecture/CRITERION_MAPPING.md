# Criterion ID Mapping

Groundwork's spec-matrix uses one row per granular unit of proof. For Sovra/Model Factory,
that granular unit is the **task ID** (`FTF-####`, `FTF-04A##`, `FTF-H###`, `FTF-R###`) from
`docs/TASK_INDEX.md` (the 300-record Release 1 queue), not the top-level `CAP-##` capability
IDs from `SPEC.md`.

- **CAP-01 … CAP-21** (`SPEC.md` §3) are product capabilities — durable, coarse-grained,
  referenced by many tasks. They never move state directly; they are satisfied once every task
  whose `requirements` column cites them is `L2-CERTIFIED` / `CERTIFIED`.
- **FTF-####** (`docs/spec-matrix.yaml`) are the 300 proof-bearing criteria. Each row's
  `notes` field carries its originating `kind`, `requirements` (CAP-##/AF-##), `scenarios`
  (Q##), and `deps` from the task index, so the CAP ↔ task relationship is never lost even
  though CAP-## itself is not a spec-matrix key.
- **AF-##** codes (also in `requirements`) and **Q##** scenario codes reference the full task
  contracts described in `docs/TASK_INDEX.md` §"Execution interpretation" (Section 85 in the
  source specification) — acceptance/negative-acceptance/verification detail per task. That
  full per-task contract content was not supplied to Groundwork at scaffold time; the relay
  and control plane must treat `proving_symbol`/`proving_test` as blank (`UNPROVEN`) until a
  task's real contract is read from wherever Section 85 lives, and must not invent acceptance
  criteria to fill the gap.

## Coverage check

All 300 rows from `docs/TASK_INDEX.md` are present in `docs/spec-matrix.yaml`:
retained_feature=246, audit_hardening=36, reuse_architecture=18 (verified by parse at scaffold
time). CAP-19/20/21 (the seven-component reuse architecture and runtime-profile capabilities)
map onto the 18 `FTF-R0##` rows; every other CAP maps onto the `FTF-0###` / `FTF-04A##` /
`FTF-H0##` rows that cite it.

## Known gap

The task index gives dependency graph, requirements, and scenario tags per task — not the
acceptance/negative-acceptance/verification-command detail Groundwork's DoD needs per item
(`docs/TASK_INDEX.md` points to "Section 85" of the source specification for that). Until that
content is supplied, `FTF-0001` (Initialize monorepo) is the only item with enough detail
(README quick-start + architecture doc) to be picked up as Active without a BLOCKER.md stop —
every other item needs its full contract read in before the relay can execute it without
improvising the DoD (CLAUDE.md "Relay is execution-only" law).
