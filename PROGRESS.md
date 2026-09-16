# PROGRESS — Sovra
Updated: 2026-09-16T00:00:00Z

## 2026-09-16 — FTF-0002 implemented (Provisional, awaiting L2 certification)
Done at the user's direct live instruction ("Go then FTF-0002 it's"), not the autonomous
relay's normal cadence — explicitly not self-certified (Law 4). Full evidence in
docs/certifications/queue.md; commit e3f0c1a981db04bd5d16078f5fd72fcebfc866c7. Summary:
- Real Python (ruff + mypy, `uv sync`) and TypeScript (eslint + prettier + tsc, `pnpm install`)
  checks, wired into `.github/workflows/ci.yml`, replacing the FTF-0001/2/3 combined placeholder
  (left a correctly-scoped FTF-0003 placeholder for the unit-test step).
- Law 1 evidence: tests/tasks/test_ftf_0002.py proves each tool catches a real injected defect
  and passes clean equivalents; separately, manually injected `import os` into the real
  scripts/status.py, watched `ruff check .` fail for real (E402+F401), reverted, watched it pass.
- Fixed a pre-existing FTF-0001 defect blocking this task: `uv sync` failed outright (hatchling
  wheel build declared with no source dir in any workspace member) — added `[tool.uv] package =
  false` everywhere needed.
- Kept `tools/r1_handoff.py` byte-identical to the master spec's own Section 86 (excluded from
  ruff/mypy via top-level `extend-exclude`, not a per-file lint-only ignore — an earlier pass
  with `ruff format .` briefly reformatted it before that exclusion was in place; reverted via
  `git checkout --`, confirmed zero diff before committing).
- Scoped `prettier --check` to actual TS/JS/config globs after `prettier --check .` matched 178
  unrelated files (CLAUDE.md, spec-matrix.yaml, every doc) — not this task's scope.
- Added minimal real entry files (`apps/web/src/index.ts`, `packages/shared/src/index.ts`) since
  `tsc --build` needs actual inputs and FTF-0001 left those dirs as README+manifest only.
Updated docs/spec-matrix.yaml (FTF-0002: IMPLEMENTED/L1-PROVEN/PROVISIONAL,
evidence_hash=e3f0c1a9...), docs/certifications/queue.md (full entry), QUEUE.md (moved to
Provisional, cleared Active). Certified count unchanged at 1/300 — PROVISIONAL is not
certified. PR opened against main; not merging without confirming CI green first.

## 2026-09-16 — Section 85 gap resolved
Owner supplied `FINE_TUNING_FACTORY_RELEASE_1.md` (Release 1 master spec, root of repo) and
`tools/r1_handoff.py` (its own Section 86 helper) via PR #15's branch
(`claude/blissful-goodall-wzvd2d`). Verified before trusting: read the helper in full (stdlib-
only, no network/subprocess/eval/exec, safe writes); confirmed the committed spec file's
`sha256sum` matches the tool's own reported `source_sha256`
(`5d3c183ff11b85718d5a3bbb08188868c455464202bb4898f6ddde66013eb664`); ran `validate --self-test`
(PASS, 300/300 tasks, 43 scenarios, 21 capabilities, acyclic, 16/16 negative fixtures rejected);
ran `bootstrap` and inspected every file it wrote (plain JSON/Markdown only, nothing executable,
all NOT_STARTED); ran `packet FTF-0002` and confirmed it's a genuine per-task DoD, not a
placeholder. Updated `docs/architecture/CRITERION_MAPPING.md`, `CLAUDE.md` (Read-first + re-
pinned hash), `QUEUE.md` (relay now resolves DoDs via the tool) and `BLOCKER.md` (resolved,
history kept). Did not claim or work FTF-0002 itself — doc/tooling only, per instruction; the
relay picks this queue up on its normal next fire. Certified: 1/300 · Product-true: 0
(unchanged this shift).

## Prior entries

Updated: 2026-09-15T00:00:00Z · Shift: this entry supersedes stale "Shift 1"
text below — several shifts ran between the last time this file was
written and now without updating it (see git log for the real record;
`PROGRESS.md`'s own history stopped at commit `ed1bac0` while `main`
continued through `1b50304`). Reconstructed from git log/file state this
shift so the doc matches reality, not just this shift's own diff.

## Reconstructed history since last PROGRESS.md write (ed1bac0 → 1b50304)
- `c1a3870` (#8): wired docs/CONTROL_PLANE_MODES.md into CLAUDE.md
  Read-first, re-pinned CLAUDE.md's internal self-reference hash.
- `dd60cef` (#10): re-derived `scripts/product_truth_check.py`'s
  SOURCE_ROOTS from FTF-0001's real layout (apps/web, apps/api,
  workers/{training,evaluation,data,deploy}, packages/shared) — now
  filled, confirmed on disk this shift.
- `b8af8ac` (#9): a relay shift stopped on a CLAUDE.md hash mismatch
  (BLOCKER.md) caused by #8's re-pin; a control-plane review confirmed
  the diagnosis but couldn't fix the scheduled trigger's baked-in hash
  from inside any session (no trigger-config tool surface).
- `a010433` (#11): fixed the root cause — replaced the scheduled prompt's
  practice of baking in a pinned hash with the self-verifying convention
  (compare `sha256sum CLAUDE.md` against committed `CLAUDE.md.sha256`
  directly). This is route option 3 from the BLOCKER.md entry.
- `f9af49e` (#12): status.py now requires a non-empty proving_symbol to
  count toward product-true.
- `77b3b2f` / `3804dfa` / `795cc44` / `8837378` / `1b50304` (#13/#14):
  watchdog found FTF-0001's original L2 certification was self-certified
  (PR #3 and PR #5 shared session identity — Law 4 violation); reverted to
  PROVISIONAL, then independently re-certified by a session with no
  session-ID overlap with either the original producer/certifier or the
  watchdog. Full detail: docs/certifications/ledger.md (both entries),
  docs/certifications/watchdog-findings.md.

Net effect: FTF-0001 is genuinely CERTIFIED (L2, independently verified,
watchdog finding closed), SOURCE_ROOTS is filled, the CLAUDE.md hash
tripwire is now self-verifying. Certified: 1/300 · Product-true: 0
(unchanged — FTF-0001 has no proving_symbol, doesn't count either way).

## This shift (2026-09-15)
STEP 0.1: read PROGRESS.md (stale, see above), QUEUE.md, and
docs/certifications/queue.md (all as of last write, `ed1bac0`) — then
cross-checked against actual git log / file state on disk since those
files hadn't been kept current.
STEP 0.2: `cognee-cli` not found on PATH — text-search fallback active
(product_truth_check.py's deterministic text search only).
Hash check (this shift's prompt uses the self-verifying convention, no
baked-in value): `sha256sum CLAUDE.md` =
`6ac215c16e9adac14ed2f7b88c93e615a3c5f10bc9bab3dfd6b5893174e6dd04`,
matches committed `CLAUDE.md.sha256` exactly — no drift. Confirms `a010433`'s
fix holds; closed out the still-open BLOCKER.md entry from `b8af8ac`.
STEP 1: QUEUE.md Active is empty. Next item in dependency order is
FTF-0002 (deps: FTF-0001, satisfied). Checked whether it has a real DoD
before picking it up: `docs/architecture/CRITERION_MAPPING.md` "Known
gap" states only FTF-0001 has enough detail to execute without a
BLOCKER.md stop; `git log` on that file and on `docs/TASK_INDEX.md` shows
neither has changed since the initial scaffold commit (`f9c2ccd`) — no
Section 85 content has ever been supplied for FTF-0002 or any other task.
Did not improvise a DoD. Wrote BLOCKER.md and stopped per STEP 4(b)/(e).
Also checked (read-only, via GitHub MCP): no open PRs, CI green on
main's current tip — nothing else pending review.
Updated QUEUE.md "Owner actions — current" with a dated ask: relay is
blocked past FTF-0001 pending either real Section 85 content or a
control-plane-authored, decided/sliced DoD written directly into QUEUE.md
Active. Also noted (without declaring resolved) that this shift's own
invocation came from some working scheduled-trigger mechanism, which is
evidence bearing on setup gap #2 but not confirmation it's fully resolved
(cadence/watchdog counterpart unverified) — flagged for owner to confirm,
per the standing norm against unverified "gap resolved" claims.
No source code changed this shift. No new PR opened for code.

## Next shift
Top item: still FTF-0002, but only once one of BLOCKER.md's three route
options is actioned by owner/control-plane (real Section 85 content, or a
control-plane-authored DoD written into QUEUE.md Active, or an explicit
scope decision to derive DoDs from existing docs). Do not re-attempt
FTF-0002 without one of those — re-hitting the same wall a second time
without new information would be STEP 4(c) territory.
Blocked: yes — see BLOCKER.md.

## Metrics
Certified / denominator: 1/300
Product-true (Law 16): 0 — must match certified; FTF-0001 has no
proving_symbol so it is CERTIFIED without counting toward product-true
L4 gate: not run (Tier 3, release-only — relay never dispatches it)
Knowledge graph: not set up (cognee-cli not found this shift; still
text-search fallback per S1.9)

## Open owner asks
See QUEUE.md "Owner actions — current" for the authoritative, current
list (two items as of this shift: trigger-tooling durability
confirmation, and the Section 85 / DoD-supply blocker). Not duplicated
here in full per "Owner actions live in ONE place."
