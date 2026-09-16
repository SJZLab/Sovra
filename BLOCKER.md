# BLOCKER.md

## Resolved (2026-09-16): CLAUDE.md hash-mismatch incident (2026-09-15)
The 2026-09-15 stop (embedded self-hash convention couldn't match a plain
`sha256sum`) is closed. Verified this shift:
- `sha256sum CLAUDE.md` matches the committed `CLAUDE.md.sha256` exactly
  (`6ac215c16e9adac14ed2f7b88c93e615a3c5f10bc9bab3dfd6b5893174e6dd04`) — no
  drift.
- This shift's own scheduled trigger prompt no longer embeds a hash value;
  it instructs comparing against the committed `CLAUDE.md.sha256` file
  directly. Route options 2 and 3 from the 2026-09-15 entry are both
  satisfied by the a010433 fix (PR #11).
- Recorded the architectural fix as `docs/adr/0002-self-verifying-claude-md-hash.md`
  (was implemented in a010433 but never logged to docs/adr/ as
  `docs/CONTROL_PLANE_MODES.md`'s UNBLOCK step 4 requires — added this
  shift).

Full prior history of this incident (what failed, root cause, control-plane
review) is preserved in git history for this file (commit b8af8ac and its
Control-plane review addendum) rather than repeated here.

---

## New stop (2026-09-16): no unblocked item has a full DoD contract

**What happened:** STEP 1 — read QUEUE.md "Active": empty ("Nothing active
— FTF-0001 merged and certified"). The text names FTF-0002 (lint/type
checks) as the next candidate once SOURCE_ROOTS is re-derived.
SOURCE_ROOTS *has* been re-derived (`scripts/product_truth_check.py`
already lists `apps/web, apps/api, workers/{training,evaluation,data,deploy},
packages/shared`, landed in dd60cef/PR #10) — but that only clears one
precondition, not the DoD-contract requirement.

**Root cause:** `docs/architecture/CRITERION_MAPPING.md` ("Known gap"
section, present since the initial scaffold commit f9c2ccd — not new
information) states explicitly: "FTF-0001 (Initialize monorepo) is the only
item with enough detail (README quick-start + architecture doc) to be
picked up as Active without a BLOCKER.md stop — every other item needs its
full contract read in before the relay can execute it without improvising
the DoD." `docs/TASK_INDEX.md` reiterates: task contracts require the full
acceptance/negative-acceptance/verification detail from "Section 85" of the
source specification, which was never supplied to this scaffold. FTF-0002's
row in `docs/TASK_INDEX.md` has only title/deps/requirements/scenarios —
no acceptance or verification detail.

CLAUDE.md's own "Relay is execution-only" law is unconditional here: "If an
item's DoD is unclear — write BLOCKER.md and stop. Do not improvise the
DoD." Lint/type-check tooling reads as low-ambiguity, but the repo's own
governing doc has already, deliberately, drawn the line at FTF-0001 only —
overriding that line on my own judgment would be exactly the kind of
relay-improvises-the-DoD action Law 3/CLAUDE.md's "Relay is execution-only"
section forbids.

**What was tried:** confirmed no other QUEUE.md section (Blocked, Done)
hides a ready item; confirmed `docs/spec-matrix.yaml` has no other row past
FTF-0001 with a non-empty `proving_test`; confirmed this is not new
information this shift discovered by surprise — it's the standing,
documented state of the project once FTF-0001 was the only task completed.

**Three route options for control plane / owner:**
1. Supply "Section 85" (the full per-task acceptance/negative-
   acceptance/verification contract) for FTF-0002 at minimum, ideally for
   the whole FTF-0001→FTF-0006 bootstrap chain QUEUE.md names as the early
   read order, so relay shifts can proceed without repeated BLOCKER.md
   stops on each subsequent item.
2. Control plane writes and appends a fully-specified DoD directly into
   QUEUE.md "Active" for FTF-0002 (exact scope/files, positive/negative
   tests, verification commands) — this is design work, not something the
   relay may generate for itself per "Relay is execution-only."
3. If Section 85 content genuinely doesn't exist anywhere and never will,
   revisit whether `docs/TASK_INDEX.md`'s "Queue rules" ("A task is READY
   only when its contract... is known") should instead allow a lighter-
   weight DoD for `retained_feature` bootstrap tasks like FTF-0002 — an
   explicit governance decision, not a relay self-exception.

**Also checked, not a fix:** `CronCreate`/`CronList` tooling is available
to this session, which could look like it closes the standing "no
create_trigger/fire_trigger tooling" setup gap (CLAUDE.md "Setup gaps" #2,
QUEUE.md "Owner actions"). It does not: `CronCreate` jobs are session-only
(in-memory, deleted when this session ends) and auto-expire after 7 days —
they cannot provide the persistent, cross-session 2h relay / watchdog
cadence CLAUDE.md describes. Declaring this gap resolved on the strength of
a tool that can't actually deliver durable scheduling would repeat the
exact false "gap resolved" pattern already logged in
`docs/certifications/watchdog-findings.md` (2026-09-15, commit 10c6880) —
so it is reported here as checked-and-insufficient, not acted on.

No QUEUE.md item was picked or worked this shift beyond the documentation
fixes above (ADR 0002, this file, PROGRESS.md catch-up, QUEUE.md owner-ask
entry). Stopping per relay STEP 4(b)/(e) — DoD unclear for the only
candidate item; owner/control-plane decision needed.
