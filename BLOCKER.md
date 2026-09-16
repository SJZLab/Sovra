# BLOCKER.md

## Previous blocker — RESOLVED, closing out (2026-09-16)

The prior entry in this file (CLAUDE.md hash mismatch, 2026-09-15) is
resolved. This shift's STEP 0 check ran `sha256sum CLAUDE.md` against the
committed `CLAUDE.md.sha256` (the self-verifying scheme landed in
`a010433` "Self-verifying CLAUDE.md hash: replace embedded value with
CLAUDE.md.sha256 (#11)", implementing that blocker's route option 3) and
got a clean match:
`6ac215c16e9adac14ed2f7b88c93e615a3c5f10bc9bab3dfd6b5893174e6dd04`. No
externally-pinned hash is embedded in this shift's scheduled prompt
anymore — it instructs comparing against the committed file directly, so
the drift class that caused the prior stop can't recur. Clearing per
`docs/CONTROL_PLANE_MODES.md` UNBLOCK step 4. Full history preserved in
git (`b8af8ac`, `a010433`) and `docs/certifications/watchdog-findings.md`
is unaffected (separate finding, already disposed).

## New blocker — no unblocked QUEUE.md item has an executable DoD (2026-09-16)

**What failed:** STEP 0.1 read `PROGRESS.md`, `QUEUE.md`,
`docs/certifications/queue.md` — all three are stale relative to actual
repo state at `origin/main` HEAD `1b50304` (they describe the state as of
the first relay shift, 2026-09-15, before PRs #4–#14 landed: SOURCE_ROOTS
re-derivation, the self-cert finding + independent re-certification of
FTF-0001, the self-verifying hash scheme, and `status.py`'s
non-empty-`proving_symbol` fix). Reconciled against real state directly
(git log, current file contents) rather than trusting the stale doc text,
per STEP 0.1 "never assume state from prior context."

Real current state: FTF-0001 is `certification_state=CERTIFIED`,
`proof_state=L2-CERTIFIED` (independently re-certified after the
self-cert finding was corrected — see `docs/certifications/ledger.md`'s
second entry). `QUEUE.md`'s Active section is empty and names FTF-0002
("Add formatting/lint/type checks") as the next candidate now that
SOURCE_ROOTS is filled — SOURCE_ROOTS is indeed filled
(`scripts/product_truth_check.py:21-27`: `apps/web`, `apps/api`,
`workers/{training,evaluation,data,deploy}`, `packages/shared`).

STEP 1 picked FTF-0002 as the highest-priority unblocked item per
`QUEUE.md`. It has no executable DoD:

- `docs/TASK_INDEX.md` row 25 gives only a title ("Add formatting/lint/type
  checks"), deps (FTF-0001), requirements (AF-31, AF-35), and scenarios
  (Q01, Q26) — no acceptance/negative-acceptance/verification detail.
- `docs/architecture/CRITERION_MAPPING.md`'s own "Known gap" section
  states explicitly: "FTF-0001 (Initialize monorepo) is the only item
  with enough detail (README quick-start + architecture doc) to be picked
  up as Active without a BLOCKER.md stop — every other item needs its
  full contract read in before the relay can execute it without
  improvising the DoD." This file has not been touched since the initial
  scaffold commit (`f9c2ccd`) — confirmed via `git log --
  docs/architecture/CRITERION_MAPPING.md` — so this is still current,
  undisputed policy, not stale text.
- Searched `docs/architecture/` for any concrete tooling decision
  (eslint/prettier/ruff/mypy/etc.) that would make FTF-0002 executable
  without inventing one: none found.
- `.github/workflows/ci.yml`'s own placeholder step confirms the gap from
  the CI side: `echo "TODO: fill once FTF-0001/0002/0003 land (monorepo +
  lint/type + test harness)"` — the stack choice for FTF-0002 has not
  been decided anywhere in the repo.

Per CLAUDE.md's "Relay is execution-only" law and the scheduled task's own
RELAY ROLE line ("if an item's DoD is unclear... write BLOCKER.md and
stop rather than improvise — FTF-0001 is the one item with enough detail
to start"), this is a hard stop, not a judgment call for this session to
resolve by picking tooling itself.

**What was tried (read-only, no state changed):** read `QUEUE.md`'s full
dependency-ordered queue section for an alternative unblocked item with
sufficient detail (FTF-0003, FTF-0005, FTF-0101…) — all cite the same
`AF-##`/`Q##`-only contract shape as FTF-0002, none carry more detail than
a title + deps + tags, so none is more executable than FTF-0002. No
candidate found.

**Three route options for control plane / owner:**
1. Control plane makes an explicit, documented tooling decision for
   FTF-0002 (e.g., "ruff + mypy for the Python side, eslint + prettier +
   tsc for the TS side, wired into `ci.yml`'s placeholder step; DoD = the
   CI step reddens on an injected lint/type violation and passes clean")
   and writes it into `QUEUE.md` Active as a fully-specified item — the
   same way FTF-0001 became executable from README/architecture-doc-level
   detail without the full Section 85 contract. Unblocks relay
   immediately; scope stays this-item-only, no improvising by the
   producer session.
2. Owner locates and supplies the actual "Section 85" source document
   `docs/TASK_INDEX.md` references, so `CRITERION_MAPPING.md`'s gap closes
   for all 300 items at once rather than one at a time.
3. If the Groundwork scaffold's granularity is heavier than needed for
   early bootstrap tasks (FTF-0002/0003/0005 are all tooling-setup, not
   product-facing features), control plane could define a lighter DoD
   convention specific to `kind=retained_feature` bootstrap tasks with no
   product-facing acceptance criteria (mirroring how FTF-0001 itself was
   treated as structural, no `proving_symbol`, product-truth-exempt) —
   documented once in `CRITERION_MAPPING.md` rather than decided ad hoc
   per item.

No QUEUE.md item was worked, no source files changed. Only this file and
`PROGRESS.md` were touched this shift. Stopping per STEP 4(b) (true
blocker).
