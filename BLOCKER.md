# BLOCKER.md

## Prior blocker resolved — CLAUDE.md hash mismatch (closed 2026-09-15)
The hash-mismatch blocker logged by PR #9 was root-caused (owner-authorized
edit in `c1a3870` moved the true hash without the then-current scheduled
trigger being updated) and fixed architecturally in PR #11
(`a010433` — "Self-verifying CLAUDE.md hash: replace embedded value with
CLAUDE.md.sha256"): the trigger prompt no longer bakes in a hash value at
all, it compares `sha256sum CLAUDE.md` against the committed
`CLAUDE.md.sha256`, so there is nothing left to go stale on a future
CLAUDE.md edit as long as the editing commit regenerates
`CLAUDE.md.sha256` alongside it (CI enforces this — `.github/workflows/ci.yml`).
Re-verified independently this shift: `sha256sum CLAUDE.md` matches
`CLAUDE.md.sha256` exactly (`6ac215c16e9adac14ed2f7b88c93e615a3c5f10bc9bab3dfd6b5893174e6dd04`).
Per `docs/CONTROL_PLANE_MODES.md` UNBLOCK step 4, clearing this entry.

## New blocker — no remaining Active item has a full DoD contract (2026-09-15)

**What failed:** STEP 1 requires picking the highest-priority unblocked item
from QUEUE.md "Active." That section currently reads "Nothing active" and
names FTF-0002 as the next candidate once SOURCE_ROOTS is re-derived.
SOURCE_ROOTS *has* been re-derived (PR #10, `dd60cef`, confirmed present in
`scripts/product_truth_check.py` — `apps/web`, `apps/api`,
`workers/{training,evaluation,data,deploy}`, `packages/shared`), so that
precondition is met. But `docs/architecture/CRITERION_MAPPING.md`'s "Known
gap" section — unchanged since the initial scaffold commit `f9c2ccd`, per
`git log -- docs/architecture/CRITERION_MAPPING.md docs/TASK_INDEX.md` —
still states explicitly: *"FTF-0001 (Initialize monorepo) is the only item
with enough detail ... to be picked up as Active without a BLOCKER.md stop —
every other item needs its full contract read in before the relay can
execute it without improvising the DoD."* `docs/TASK_INDEX.md` line 327
confirms each task's real acceptance/negative-acceptance/verification-command
contract lives in "Section 85" of a source specification that was never
supplied to this repo. FTF-0001 is now done and certified, so this is no
longer a narrow gap around one item — it is now a full stop on the entire
remaining 299-item backlog: every next candidate in QUEUE.md's dependency
read order (FTF-0002, FTF-0003, FTF-0005, ...) hits the identical gap.

**What was tried / root-cause check (read-only, no state changed):**
- Re-read QUEUE.md, PROGRESS.md, docs/certifications/queue.md,
  docs/certifications/ledger.md, docs/certifications/watchdog-findings.md,
  docs/architecture/CRITERION_MAPPING.md, docs/TASK_INDEX.md, and
  docs/spec-matrix.yaml's FTF-0001/FTF-0002 rows.
- Confirmed via `git log` that no commit since scaffold (`f9c2ccd`) has
  touched CRITERION_MAPPING.md or TASK_INDEX.md — the Section 85 content
  gap this doc flags has not been filled by any later shift.
- Confirmed FTF-0002's `docs/TASK_INDEX.md` row carries only
  `deps=FTF-0001, requirements=AF-31/AF-35, scenarios=Q01/Q26` — dependency
  and tag metadata, not acceptance/negative-acceptance/verification detail.
- Ran `python3 scripts/product_truth_check.py --fail-on-unwired`: clean
  (0/0), confirming no unrelated wiring regression is hiding behind this.
- This is not a code defect to fix; it is a missing-input condition the
  relay's own charter forbids working around ("Relay is execution-only ...
  if an item's DoD is unclear — write BLOCKER.md and stop. Do not improvise
  the DoD," CLAUDE.md).

**Three route options for control plane / owner:**
1. Supply the actual Section 85 content (acceptance clauses, negative-
   acceptance clauses, verification commands, API contract, shared locks,
   database-change rules) for FTF-0002 at minimum, ideally for the next
   several items in QUEUE.md's dependency read order, so relay shifts have
   a real DoD to execute against instead of stopping every 2h cycle.
2. If Section 85 content genuinely doesn't exist yet in usable form, have
   control plane write a scoped, explicit DoD for one specific next item
   (mirroring how FTF-0001's DoD was inferred from README quick-start +
   ARCHITECTURE.md §2 without inventing acceptance criteria) and record
   that authorization in CRITERION_MAPPING.md's "Known gap" section by name,
   the same way FTF-0001 was named — so the relay isn't the one deciding
   which items count as "detailed enough."
3. Accept that relay execution is paused on new FTF work until (1) or (2)
   happens. Non-FTF hygiene (tracker accuracy, blocker/queue upkeep,
   certification review of already-produced evidence) can continue in the
   meantime; this shift did some of that (see PROGRESS.md).

Logged to QUEUE.md "Owner actions — current" per the "Owner actions live in
ONE place" rule. No spec-matrix.yaml or certification state was touched
this shift — Certified stays 1/300, Product-true stays 0. Stopping per
STEP 4(b)/(e).
