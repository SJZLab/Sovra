# BLOCKER.md

## Prior entry resolved (2026-09-15, this shift)

The previous entry below ("CLAUDE.md hash mismatch") described a mismatch
between a *pinned* hash baked into an earlier scheduled-relay prompt and
the actual `CLAUDE.md` on `main`. This shift's scheduled prompt no longer
bakes in a pinned hash — it instructs `sha256sum CLAUDE.md` compared
directly against the committed `CLAUDE.md.sha256` (the self-verifying
convention added in `a010433`, route option 3 from the prior entry).
Re-run this shift: `sha256sum CLAUDE.md` = `6ac215c16e9adac14ed2f7b88c93e615a3c5f10bc9bab3dfd6b5893174e6dd04`,
matches `CLAUDE.md.sha256` exactly. No drift. That specific blocker is
closed — superseded text kept below for the record, not as a live issue.

---

## Relay fully blocked past FTF-0001 — no queued item has a real DoD (2026-09-15)

**What failed:** STEP 1 (pick highest-priority unblocked item from QUEUE.md
Active). QUEUE.md's Active section is empty — FTF-0001 is the only
`CERTIFIED` row in `docs/spec-matrix.yaml` (confirmed:
`certification_state: CERTIFIED`, `proof_state: L2-CERTIFIED`,
independently re-certified per `docs/certifications/ledger.md`'s second
entry, watchdog self-cert finding closed). The next item in QUEUE.md's
documented dependency read order is FTF-0002 ("Add formatting/lint/type
checks", deps: FTF-0001 only — satisfied).

Picking up FTF-0002 anyway would mean inventing its acceptance/negative-
acceptance/verification contract, which `CLAUDE.md`'s "Relay is
execution-only" law and this shift's own instructions ("if an item's DoD
is unclear... write BLOCKER.md and stop rather than improvise") forbid.

**What was tried / root-cause check (read-only, no source changed):**
- Re-read `docs/architecture/CRITERION_MAPPING.md` "Known gap": states
  the full per-task acceptance/negative-acceptance/verification-command
  contract ("Section 85" of the source spec, referenced from
  `docs/TASK_INDEX.md` "Execution interpretation") was never supplied to
  this repo, and that "FTF-0001... is the only item with enough detail...
  to be picked up as Active without a BLOCKER.md stop — every other item
  needs its full contract read in."
- `git log --oneline -- docs/architecture/CRITERION_MAPPING.md` and
  `git log --oneline -- docs/TASK_INDEX.md`: both show only the single
  initial scaffold commit (`f9c2ccd`). No shift since scaffold has added
  Section 85 content for any task, including FTF-0002.
- `git log --all --oneline | grep -i "0002\|section 85\|contract"`: no
  hits — no prior shift has attempted or blocked on FTF-0002 specifically;
  this is the first time a shift has reached this wall (earlier shifts
  spent their turns on FTF-0001 itself, a CLAUDE.md hash-mismatch stop,
  and a Law-4 self-certification finding/correction).
- Confirmed `docs/spec-matrix.yaml`'s FTF-0002 row is exactly what an
  unstarted item should look like (`certification_state: NONE`,
  `implementation_state: ABSENT`, `proof_state: UNPROVEN`, empty
  `proving_symbol`/`proving_test`) — not evidence of anything improper,
  just confirmation nothing has been improvised here yet either.
- Confirmed via `git fetch origin main` that this branch
  (`claude/blissful-goodall-wzvd2d`) is exactly even with `origin/main`
  (`1b50304`) — not working from stale state.

**Three route options for control plane / owner:**
1. Owner or control plane supplies the real Section 85 record (or a
   pointer to where it lives) for FTF-0002 — or whichever task should be
   prioritized next — so the relay has genuine acceptance/negative-
   acceptance/verification detail to execute against instead of guessing.
2. Control plane (which proposes and designs — the relay does not) writes
   a decided, sliced DoD directly into QUEUE.md's "Active" section for one
   specific task, with enough acceptance/negative-acceptance/verification
   detail that the relay can execute it without inventing anything. This
   is explicitly the contract CLAUDE.md's "Relay is execution-only"
   section describes ("The relay receives decided, sliced items with
   DoDs").
3. If Section 85 content genuinely doesn't exist anywhere retrievable, the
   owner/control plane makes an explicit scope decision to derive DoDs for
   the remaining 299 tasks from what documentation does exist (SPEC.md,
   docs/architecture/, docs/adr/) — a scope call, not something the relay
   makes unilaterally by improvising acceptance criteria.

**State:** no source files touched this shift beyond BLOCKER.md,
QUEUE.md (owner-ask block), and PROGRESS.md. No PR opened for code — this
is a coordination-only push. Stopping per STEP 4(b)/(e) (true blocker +
ask-first item, same underlying cause).
