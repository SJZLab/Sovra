# BLOCKER.md

_No open blockers. Both entries below are resolved history, kept for the record per this repo's
"reported, not silently worked around" norm — not live issues._

## RESOLVED 2026-09-16 — Relay fully blocked past FTF-0001 (Section 85 gap)

Original entry: no queued item past FTF-0001 had a real acceptance/negative-acceptance/
verification contract; `docs/architecture/CRITERION_MAPPING.md` "Known gap" and
`docs/TASK_INDEX.md` had been unchanged since the initial scaffold commit (`f9c2ccd`) with no
Section 85 content ever supplied. Wrote this file and a QUEUE.md owner ask rather than improvise
a DoD, per CLAUDE.md "Relay is execution-only."

**Resolution:** the owner supplied `FINE_TUNING_FACTORY_RELEASE_1.md` (the single-file Release 1
master specification — root of the repo) and `tools/r1_handoff.py` (extracted from the master
doc's own Section 86). Verified before trusting either:
- Read `r1_handoff.py` in full: stdlib-only, no network/subprocess/eval/exec, path-traversal-
  and symlink-safe writes, refuses to overwrite existing content with different bytes.
- Copied the uploaded spec file byte-for-byte (`cp`, not regenerated) and confirmed
  `sha256sum` on disk matches the tool's own reported `source_sha256`
  (`5d3c183ff11b85718d5a3bbb08188868c455464202bb4898f6ddde66013eb664`) — no transfer corruption.
- `validate --self-test`: `status: PASS`, 300/300 tasks, 43 scenarios, 21 capabilities,
  dependency graph acyclic, 16/16 deliberately-broken structural fixtures correctly rejected.
- `bootstrap`: inspected every file it wrote (`spec/RELEASE_1.md`, `spec/tasks.json`,
  `spec/runtime-architecture.json`, `spec/qualification-inventory.json`,
  `spec/release-1-handoff.json`, `docs/implementation/TRACKER.json`) — all plain JSON/Markdown,
  every task `NOT_STARTED`/`ready=false`/no evidence, nothing executable, no claim of completion.
- `packet FTF-0002`: produced a genuine per-task DoD (real acceptance, negative_acceptance,
  verification_commands, files, dependencies, plus binding section text) — not a placeholder.

Updated `docs/architecture/CRITERION_MAPPING.md` ("Known gap — RESOLVED"), `CLAUDE.md` (Read-
first line now points to the master spec + tool ahead of the four excerpt docs it supersedes;
`CLAUDE.md.sha256` re-pinned via `sha256sum`, verified with `sha256sum -c`), and `QUEUE.md`
(relay now runs `tools/r1_handoff.py packet <task-id>` for a real DoD before claiming a task;
BLOCKER.md only if that command itself fails).

Per the sharing session's explicit instruction: this shift does not claim or work FTF-0002 or
any other task — this is a doc/tooling PR only. The relay picks up the now-unblocked queue on
its normal next fire.

## RESOLVED 2026-09-15 — CLAUDE.md hash mismatch

Original entry: a scheduled-relay prompt baked in a pinned CLAUDE.md hash that went stale after
an authorized edit re-pinned the file's own internal self-reference. Root-caused as expected
fallout from a legitimate change, not tampering; fixed at the source in commit `a010433` by
replacing the pinned-hash convention with a self-verifying one (compare `sha256sum CLAUDE.md`
against the committed `CLAUDE.md.sha256` directly, which can't go stale). Re-verified clean on
the next two shifts since (2026-09-15 and 2026-09-16, the latter after CLAUDE.md's own Read-first
edit above and hash re-pin).
