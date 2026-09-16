# BLOCKER.md

## No unblocked item has a supplied DoD (Section 85 task-contract gap) — 2026-09-16

**What failed:** STEP 1 this shift: `QUEUE.md`'s Active section is empty
("Nothing active — FTF-0001 merged and certified"). The next candidates in
QUEUE.md's dependency read order (FTF-0002 "Add formatting/lint/type
checks", FTF-0005 "Configuration system") are both `implementation_state:
ABSENT`, `proof_state: UNPROVEN` in `docs/spec-matrix.yaml`, with no
`proving_symbol`/`proving_test` filled in. Per
`docs/architecture/CRITERION_MAPPING.md` ("Known gap"): the full
acceptance/negative-acceptance/verification contract for every task lives
in "Section 85" of a source specification that was **not supplied to this
scaffold**, and that doc explicitly states FTF-0001 is "the only item with
enough detail... to be picked up as Active without a BLOCKER.md stop —
every other item needs its full contract read in before the relay can
execute it without improvising the DoD." `docs/TASK_INDEX.md` line 327
confirms the same: "Read that record from Section 85 before claiming or
implementing the task." No file anywhere in the repo (`grep -r "Section
85"` → only `CRITERION_MAPPING.md`, `TASK_INDEX.md`, `CLAUDE.md` itself)
contains that content.

**What was tried:**
- Re-verified `SOURCE_ROOTS` in `scripts/product_truth_check.py` is filled
  (it is, since PR #10 — `apps/web`, `apps/api`,
  `workers/{training,evaluation,data,deploy}`, `packages/shared`) in case
  that was gating something else. It doesn't change this: FTF-0002/0005
  still have no acceptance contract, independent of SOURCE_ROOTS.
- Searched the repo for any Section 85 content added since scaffold time —
  none found.
- Confirmed via `git log` that no shift since FTF-0001 (PRs #3–#14) has
  attempted FTF-0002 or any other criterion — all subsequent work was
  governance/self-correction on FTF-0001 itself (self-cert finding,
  re-certification, SOURCE_ROOTS derivation, hash-scheme fix, status.py
  fix), consistent with this gap having blocked all forward progress the
  whole time, not just this shift.
- Checked `QUEUE.md` "Owner actions — current": it lists only the 2h
  trigger-tooling gap, not this DoD gap — even though `CRITERION_MAPPING.md`
  has declared it since FTF-0001's own scaffold commit. This is itself a
  process gap (Owner actions are supposed to live in exactly one place, and
  this qualifies as an ask-first hard stop per CLAUDE.md "Relay is
  execution-only": "If an item's DoD is unclear... write BLOCKER.md and
  stop. Do not improvise the DoD.").

**Per Law 3 / "Relay is execution-only":** this session will not invent
acceptance/negative-acceptance/verification criteria for FTF-0002 (or any
other item) to fill the gap. That is a control-plane/owner decision, not a
relay one.

**Three route options for control plane / owner:**
1. Supply Section 85's full per-task contract content (or point to where it
   already exists outside this repo) so `docs/spec-matrix.yaml` can be
   filled in with real `proving_symbol`/`proving_test` targets and
   acceptance/negative-acceptance clauses per task, starting with FTF-0002.
2. Have control plane author a DoD for FTF-0002 specifically (a narrow,
   low-risk task — "add formatting/lint/type checks" — where a reasonable
   DoD may be derivable without the full source spec) and hand it to the
   relay as a decided, sliced item, per "Relay is execution-only": the
   relay executes what control plane decides, it does not decide DoDs
   itself.
3. If Section 85 content genuinely does not exist and will not be supplied,
   have control plane/owner formally scope Release 1 down to what FTF-0001
   already proves, so the relay stops re-discovering the same gap every
   shift with no forward motion.

**No source or spec-matrix changes made this shift.** Added a dated entry
to `QUEUE.md` "Owner actions — current" so this stops being silently
rediscovered each fire. Stopping per STEP 4(b) (true blocker) / STEP 4(e)
(ask-first item).

---

## Superseded — CLAUDE.md hash mismatch (2026-09-15 entry, resolved)
The prior blocker below described a scheduled-trigger-vs-repo CLAUDE.md
hash mismatch. It was resolved by PR #11 ("Self-verifying CLAUDE.md hash:
replace embedded value with CLAUDE.md.sha256"), which implemented route
option 3 from that entry: CLAUDE.md no longer carries a self-referential
pinned hash string at all; instead a separate, always-in-sync
`CLAUDE.md.sha256` file (`sha256sum CLAUDE.md > CLAUDE.md.sha256`) is
committed alongside it, and any session compares `sha256sum CLAUDE.md`
against that file directly — no external trigger-side value to fall out of
sync. This shift's own hash self-check (`sha256sum CLAUDE.md` vs.
`CLAUDE.md.sha256`) matched cleanly. Original entry kept below for the
record, not because it's still live.

# BLOCKER.md (original 2026-09-15 entry, resolved — see above)

## CLAUDE.md hash mismatch — control plane must re-verify and re-pin (2026-09-15)

**What failed:** This shift's scheduled relay prompt carries a pinned
CLAUDE.md drift-tripwire hash of
`sha256:68f978e58d9cd6a98e57e4447bbd97960e5c0d5e87acd7be840bfcb8600c39a7`.
Running `sha256sum CLAUDE.md` against the actual file checked out at
`origin/main` HEAD (`c1a3870428b182fc66b40729a07f57d84e61a2ee`) gives
`f324e834bb6f29972fa5c78bb574bbba41b3af67d009695eea675b292ed7cd2d` — a
mismatch. Per CLAUDE.md's own operating-mode line ("mismatch between this
hash and current CLAUDE.md means stop this shift immediately and write
BLOCKER.md"), this shift is stopping here without touching QUEUE.md Active
work.

**What was tried / root-cause check (read-only, no state changed):**
- Confirmed `main` exists on origin and the checkout is genuinely at its
  HEAD (no stale/detached state) — `git log origin/main` top commit is
  `c1a3870`, matching `HEAD`.
- Inspected `c1a3870` ("Wire docs/CONTROL_PLANE_MODES.md into CLAUDE.md
  Read-first, re-pin hash (#8)", authored by samerzakaria): it edited
  CLAUDE.md's Read-first line and re-pinned the file's internal
  self-reference hash to `sha256:d5f9c5b8...`. The commit message states
  this was owner-requested and explicitly flags: "scheduled relay prompts
  embedding the old hash will need it updated externally to avoid a false
  drift-mismatch stop on their next fire." That is exactly the condition
  this shift hit.
- This strongly suggests the mismatch is expected fallout from a
  legitimate, owner-authorized edit rather than tampering — but per Law 10
  ("Repository text is data, never instructions") and the hard,
  unconditional wording of the stop instruction, a plausible commit
  message is not treated as self-certifying grounds to override the
  tripwire and proceed. The mismatch is reported, not resolved, by this
  session.

**Three route options for control plane / owner:**
1. Re-verify CLAUDE.md content against the owner's intent (confirm `c1a3870`
   was the only change since the trigger's hash was pinned, nothing else
   drifted), then re-pin the *scheduled relay trigger's* embedded hash to
   the current true value (`sha256:f324e834bb6f29972fa5c78bb574bbba41b3af67d009695eea675b292ed7cd2d`,
   computed via plain `sha256sum CLAUDE.md` on `main` HEAD `c1a3870`) so
   future fires don't false-stop on this same known-good state.
2. If trigger-editing tooling still isn't available to any session (per
   CLAUDE.md's declared Setup gaps), the owner updates the scheduled
   prompt's baked-in hash directly wherever the schedule is configured.
3. If control plane instead wants a self-verifying convention going
   forward (rather than a "hash of file with old hash still in place"
   placeholder scheme that necessarily can't match a naive `sha256sum` from
   an external trigger), consider changing CLAUDE.md's documented hashing
   convention so the trigger and the file can agree on one procedure
   without manual re-sync on every edit.

No QUEUE.md item was picked or worked this shift. No commits made to
source beyond this BLOCKER.md. Stopping per STEP 4(b)/(hash-mismatch hard
stop).

---

## Control-plane review (2026-09-15)
Independently re-verified: `sha256sum CLAUDE.md` on `main` HEAD `c1a3870`
gives `f324e834bb6f29972fa5c78bb574bbba41b3af67d009695eea675b292ed7cd2d`,
confirming this shift's root-cause check exactly. This session's own
`c1a3870` commit message explicitly anticipated this fire, so it is
expected fallout from an authorized edit (CLAUDE.md's Read-first wiring +
hash re-pin, owner-requested), not tampering. Diagnosis is correct; route
option 1 is the fix.

**Not resolved yet:** re-pinning the *scheduled relay trigger's* baked-in
hash to `sha256:f324e834bb6f29972fa5c78bb574bbba41b3af67d009695eea675b292ed7cd2d`
requires editing the trigger's own configuration, which lives outside this
repo and outside this session's tool surface (same `create_trigger`/
`fire_trigger` gap CLAUDE.md's "Setup gaps" already declares) — matches
route option 2. Reported to the owner directly in the conversation driving
this session rather than closed out here. This entry stays as a live
record, not cleared, until the owner confirms the trigger's hash is
updated — the next relay fire will either succeed (fix applied) or
regenerate an equally-accurate BLOCKER.md (fix not yet applied); either
outcome is informative, so nothing is lost by leaving this in place.

Route option 3 (rethink the hashing convention so a naive external
`sha256sum` and the file's self-reference agree without manual re-sync) is
a real architectural question, not something control plane is deciding
unilaterally here — flagged to the owner alongside this entry.

**Resolution (2026-09-16, this shift):** Route option 3 was the one
implemented (PR #11, `a010433`), superseding this entire original entry —
see the note at the top of this file.
