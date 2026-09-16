# ADR 0002 — Self-verifying CLAUDE.md hash (drop embedded self-reference)

Status: Accepted (implemented in commit a010433, PR #11, 2026-09-15)

## Context
CLAUDE.md originally carried its own sha256 as an embedded self-reference
line. By its documented convention, that value could never equal a plain
`sha256sum CLAUDE.md` on the final file — it was always "hash of the file
with the placeholder still in place," a value only reproducible by knowing
the convention, not by running the obvious command. Two incidents
(commit 3f60527 and PR #9 / BLOCKER.md 2026-09-15) were false drift-mismatch
stops caused entirely by this scheme: a scheduled relay prompt carrying a
baked-in expected hash could never be kept in sync with CLAUDE.md edits
without a manual re-pin of *both* the file and the external trigger config
on every change, and any session without trigger-editing tooling could not
close the loop.

## Decision
Drop the embedded self-reference line from CLAUDE.md. Add a committed
`CLAUDE.md.sha256` file (one line, plain `sha256sum` output, no prefix) as
the single source of truth. `.github/workflows/ci.yml` fails the build if
`CLAUDE.md.sha256` doesn't match a fresh `sha256sum CLAUDE.md`. Any
relay/watchdog session — and the scheduled trigger prompt itself — compares
`sha256sum(CLAUDE.md)` against the *committed file*, never a hardcoded value
carried in a prompt.

## Consequences
- Every CLAUDE.md edit must regenerate `CLAUDE.md.sha256` in the same
  commit (`sha256sum CLAUDE.md | awk '{print $1}' > CLAUDE.md.sha256`); CI
  enforces this.
- Scheduled relay/watchdog prompts no longer embed a hash value at all —
  confirmed on the 2026-09-16 shift, whose scheduled prompt already reads
  "compare against the committed CLAUDE.md.sha256 file" with no baked-in
  hash, closing out BLOCKER.md route option 2/3 from the 2026-09-15
  incident.
- Documented here per `docs/CONTROL_PLANE_MODES.md`'s UNBLOCK step 4
  ("if the fix is architectural, record it in docs/adr/") — this record was
  missing until this shift; the fix itself already landed in a010433.
