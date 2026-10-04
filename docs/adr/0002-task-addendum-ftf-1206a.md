# ADR 0002 — Release 1 task addendum: FTF-1206A (denominator 300 → 301)

- Status: Accepted, 2026-10-04
- Decided by: owner (live instruction, option "a"), recorded by Control plane session
  `session_01QVznorVMjnmcttUhyDnoGR`

## Context
FTF-1206's acceptance requires persisting organization gateway policy. Its contract's
`database_changes` clause forbids an independent schema change and says any needed schema is
"a separately claimed migration prerequisite". No task in the frozen 300-task index owns that
migration, and no existing table can hold the policy. The relay correctly stopped on this
(BLOCKER.md, 2026-10-02).

## Decision
Add one task, **FTF-1206A — Organization gateway policy persistence**, with its own
forward-migration contract (`spec/addenda/FTF-1206A.json`). FTF-1206 depends on it. The
Release 1 denominator becomes 301. `docs/spec-matrix.yaml` is the denominator's source of truth,
and `scripts/status.py` reads it.

## Consequences
- `spec/tasks.json` and `tools/r1_handoff.py` stay frozen at 300, because the tool's integrity
  check requires exactly 300. Addendum contracts live in `spec/addenda/<ID>.json`. The relay reads
  the contract there instead of from `r1_handoff.py packet`.
- Any further addendum needs the same owner decision and an ADR.
