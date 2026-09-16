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

## Known gap — RESOLVED 2026-09-16

The gap described below (Section 85 content never supplied) is closed. The owner supplied
`FINE_TUNING_FACTORY_RELEASE_1.md` — the single-file Release 1 master specification, root of
the repo — plus `tools/r1_handoff.py`, a stdlib-only helper extracted from the master doc's own
Section 86. Together they resolve this structurally for all 300 tasks, not just FTF-0001:

- `FINE_TUNING_FACTORY_RELEASE_1.md` §85 is the canonical embedded task ledger: every FTF-####
  row's real `acceptance`, `negative_acceptance`, `verification_commands`, `files`,
  `dependencies` and binding section text — the exact content `docs/TASK_INDEX.md` pointed at
  and that was missing from this repo since scaffold.
- `tools/r1_handoff.py packet <task-id> --plan FINE_TUNING_FACTORY_RELEASE_1.md` resolves one
  task's full contract on demand into `docs/implementation/task-packets/<task-id>.md` — verified
  end to end for FTF-0002 (2026-09-16): real acceptance/negative-acceptance/verification detail,
  not a placeholder. `validate --self-test` passes on the committed file (300/300 tasks, 43
  scenarios, 21 capabilities, dependency graph acyclic, 16/16 negative structural cases
  correctly rejected); `bootstrap` writes only plain JSON/Markdown spec/control files, verified
  by direct inspection, nothing executable and no claim of completion.
- Per this master doc's own Authority/Section 84.1, it supersedes `SPEC.md`,
  `docs/architecture/ARCHITECTURE.md`, `docs/architecture/REFERENCE_REPOSITORIES.md` and
  `docs/TASK_INDEX.md` as far as any conflict goes — those four were excerpts of this single
  source. This mapping doc (task ID scheme, CAP-##/FTF-#### relationship) remains accurate and
  is not itself superseded; it should be read alongside the master spec, not replaced by it.

The relay's instruction is updated accordingly (see QUEUE.md): run
`python tools/r1_handoff.py packet <task-id> --plan FINE_TUNING_FACTORY_RELEASE_1.md` to get a
task's real DoD before claiming it; write BLOCKER.md only if that command itself fails (unknown
task ID, hash-mismatch on a tampered plan file, or similar) — not merely because the task looked
title-only in `docs/TASK_INDEX.md`.

### Original gap (superseded, kept for history)

The task index gives dependency graph, requirements, and scenario tags per task — not the
acceptance/negative-acceptance/verification-command detail Groundwork's DoD needs per item
(`docs/TASK_INDEX.md` points to "Section 85" of the source specification for that). Until that
content is supplied, `FTF-0001` (Initialize monorepo) is the only item with enough detail
(README quick-start + architecture doc) to be picked up as Active without a BLOCKER.md stop —
every other item needs its full contract read in before the relay can execute it without
improvising the DoD (CLAUDE.md "Relay is execution-only" law).
