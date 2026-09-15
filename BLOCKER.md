# BLOCKER.md

## No workable Active item — Section 85 task contracts still not supplied (2026-09-15, relay shift)

**STEP 0 / hash check:** `sha256sum CLAUDE.md` matches the committed
`CLAUDE.md.sha256` exactly
(`6ac215c16e9adac14ed2f7b88c93e615a3c5f10bc9bab3dfd6b5893174e6dd04`). No
drift this shift.

**Prior incident, now confirmed resolved (clearing it, not re-reporting):**
this file previously recorded a CLAUDE.md hash mismatch (commit `b8af8ac`,
PR #9) caused by a scheduled-trigger prompt carrying a stale baked-in hash.
Commit `a010433` (PR #11) fixed the root cause by replacing the embedded
self-reference with the committed `CLAUDE.md.sha256` file, so the check is
now self-verifying instead of depending on a hardcoded value that goes
stale on every edit. This shift's own scheduled prompt no longer carries a
baked-in hash at all (it says "compare against the committed
`CLAUDE.md.sha256` file"), and the check above passed cleanly — the fix is
confirmed working under real conditions, not just in theory. Per the prior
entry's own note ("the next relay fire will either succeed... or regenerate
an equally-accurate BLOCKER.md... either outcome is informative"), this is
that next fire, and the outcome is: fixed. Old entry cleared below.

**What failed this shift:** QUEUE.md's Active section says the next
unblocked item is FTF-0002 once SOURCE_ROOTS is re-derived. SOURCE_ROOTS
*was* re-derived (commit `dd60cef`, PR #10) — but dependency-unblocked is
not the same as DoD-complete. `docs/architecture/CRITERION_MAPPING.md`
"Known gap" (present since the very first scaffold commit `f9c2ccd`,
unchanged since) states explicitly:

> Until that content is supplied, `FTF-0001` (Initialize monorepo) is the
> only item with enough detail... to be picked up as Active without a
> BLOCKER.md stop — every other item needs its full contract read in
> before the relay can execute it without improvising the DoD.

FTF-0001 is already IMPLEMENTED/L2-CERTIFIED. Every other row in
`docs/spec-matrix.yaml` (299 remaining, including FTF-0002 "Add
formatting/lint/type checks", the next in dependency order) has
`requirements`/`scenarios` tags only — the actual acceptance/
negative-acceptance/verification-command detail ("Section 85" of the
source specification, per `docs/TASK_INDEX.md:327`) was never supplied to
this repo. I confirmed this is still true: `grep -rn "Section 85"
docs/` only turns up the three places that *reference* Section 85 by
name, and `docs/validation/` is an empty `.gitkeep` directory — no
per-task contract content exists anywhere in the tree.

**What was tried:** Considered picking up FTF-0002 anyway on the reasoning
that "add lint/format/type checks" is generic engineering setup, low-risk,
and unlikely to need product-specific acceptance criteria. Rejected this:
CLAUDE.md's "Relay is execution-only" section is unconditional ("If an
item's DoD is unclear... write BLOCKER.md and stop. Do not improvise the
DoD") and does not carve out an exception for tasks that merely look
simple — that judgment call is itself the kind of design/proposal work the
relay is barred from making. Checked whether a prior shift had already
surfaced this as an Owner action (it hadn't — `git log -p --all -- QUEUE.md
| grep "Section 85"` returns nothing); the gap has been sitting undeclared
as a live ask since scaffold creation, documented only passively inside
`CRITERION_MAPPING.md`.

**Three route options for control plane / owner:**
1. Owner/control plane supplies the real Section 85 content (acceptance,
   negative-acceptance, verification-command detail) for at least the next
   few queued items (FTF-0002, FTF-0003, FTF-0005) from the source
   specification, so the relay has a real contract to execute against
   instead of improvising one.
2. Control plane — not the relay, per "Relay is execution-only" — writes
   an explicit DoD itself for the narrow set of pure-tooling bootstrap
   tasks (FTF-0002 lint/type, FTF-0003 test harness, FTF-0005 config) that
   have no product-facing behavior, and lands it as the next Active
   QUEUE.md item with enough detail the relay can execute without
   guessing. This is squarely inside CONTROL_PLANE_MODES.md's UNBLOCK
   step 2 ("otherwise write the resolution as the next Active QUEUE.md
   item with enough detail the relay can execute without guessing").
3. Owner explicitly descopes the Section-85 requirement for this class of
   task (tooling/CI bootstrap with no CAP-## product behavior), recording
   that decision in `docs/architecture/CRITERION_MAPPING.md` so it isn't
   re-litigated every shift — while leaving the requirement intact for
   product-capability tasks (FTF-01xx onward) where an invented acceptance
   contract would be a real Law-2/Law-11 risk (inflated tracker / simulated
   progress dressed up as "done").

No code changed this shift beyond BLOCKER.md/QUEUE.md/PROGRESS.md
housekeeping. No QUEUE.md item was picked or worked. Stopping per STEP
4(b) (true blocker — DoD unclear, must not improvise).
