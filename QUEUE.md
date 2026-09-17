# QUEUE — Sovra (Model Factory)
Updated: 2026-09-17 by Control plane (FTF-H028 independently certified)
Relay: 2h · Report: 08:30 & 19:30 Asia/Riyadh · Certifier: Control plane (autonomous)

## Status
Certified: 10/300 · Provisional: 0 · Blocked: 1 (FTF-0102,
new-runtime-dep owner ask, see below)
Product-true (Law 16): 0 — must match certified (FTF-0001, FTF-0002,
FTF-0003, FTF-0004, FTF-0005, FTF-0006, FTF-H001, FTF-R001, FTF-H002 and
FTF-H028 all have no proving_symbol; FTF-0001 is structural layout,
FTF-0002 is CI tooling, FTF-0003 is test-harness tooling, FTF-0004 is
local-stack bootstrap/health tooling, FTF-0005 is a configuration module
with no live caller yet, FTF-0006 is an observability seam with no live
FastAPI app or Temporal workflow to instrument yet, FTF-H001 is the
spec/gate verifier tooling itself, FTF-R001 is an architecture/
version-lock schema freeze with no live product-runtime caller yet,
FTF-H002 is a preflight/release-scope-freeze layer with no live
product-runtime caller yet, and FTF-H028 is a CI/gate-evidence tooling
layer with no live product-runtime caller yet — none
a live-wired product-runtime path, so all ten are CERTIFIED without
counting toward product-true).

## Owner actions — current
- 2026-09-15: One setup gap remains — no *durable* trigger tooling
  available to create the 2h relay / watchdog triggers. See CLAUDE.md
  "Setup gaps". Owner must create them (or grant a control-plane session
  trigger tooling and re-run Groundwork S1.7/S1.8). Note: this shift's
  own session was itself invoked by some external schedule (its prompt
  says "started automatically by a schedule"), so a trigger mechanism
  exists for the relay cadence at minimum — not confirming this gap
  resolved without evidence of the watchdog trigger and the 08:30/19:30
  report cadence too, per the prior watchdog finding on unverified
  "gap resolved" claims. Owner: please confirm what's actually configured.
- 2026-09-16: FTF-0102 (Organization/workspace schema) is dependency-ready
  (deps FTF-0004/FTF-0005/FTF-H001 all CERTIFIED; FTF-0101 is blocked
  behind FTF-0102 itself, not ready) but blocked on this ask, not on
  readiness. This shift instead picked up FTF-R001, a separate
  dependency-ready item (deps FTF-0001/FTF-H001, unrelated to this ask) —
  see "Provisional" below; this ask still stands unchanged. Its own resolved
  task contract (`tools/r1_handoff.py packet FTF-0102`) requires "Own
  forward Alembic migration for this resource; tenant/composite
  constraints/RLS" and a negative-acceptance clause proven against real
  DB constraints/RLS (cross-tenant parent IDs and missing tenant context
  must fail). This repo currently has **zero** Postgres client/ORM/
  migration runtime dependencies anywhere (`apps/api/pyproject.toml`
  dependencies list is empty; root `pyproject.toml` dev-group is
  ruff/mypy/pytest only) — satisfying this contract requires adding
  SQLAlchemy + Alembic + a Postgres driver (psycopg or asyncpg) to
  `apps/api`. The master architecture table
  (`FINE_TUNING_FACTORY_RELEASE_1.md`, "binding architecture", API/policy
  row) does name "FastAPI + SQLAlchemy/Alembic + PostgreSQL" for this
  layer — but this repo's own consistent precedent (FTF-0004/0005/0006
  each deliberately declined to add FastAPI/pydantic even though that
  same table row names them, explicitly citing CLAUDE.md's "new runtime
  deps" ask-first hard stop as governing regardless of prior architecture
  naming) treats that table as declared intent, not a standing
  pre-approval to install. Improvising a stdlib-only substitute (e.g.
  shelling out to a `psql` CLI instead of Alembic) would silently deviate
  from the contract's explicit, specific "Alembic" naming — that's
  improvising the DoD, not executing it, so this shift did not do that
  either. Options for the owner: (1) approve adding SQLAlchemy + Alembic
  + a Postgres driver as apps/api's first runtime dependencies, matching
  the master architecture table as written; (2) approve a narrower
  stdlib/CLI-only alternative (raw SQL migration files run via `psql`
  subprocess against FTF-0004's docker-local-stack Postgres, no new
  Python package) with the "Alembic" contract-language deviation
  explicitly disclosed rather than hidden; (3) some other explicit stack
  decision. FTF-0102 stays in `## Blocked` below until this is answered —
  not claimed, not started, no dependency installed.
## Active (relay works this)
(empty — FTF-H028 implemented, merged and now independently certified,
see "Done" below. FTF-0101/FTF-0102 remain blocked, see "Owner actions"
and "Blocked" below.)

## Provisional (awaiting Control plane L2 certification)
(empty — FTF-H028 independently certified this shift, see "Done" below.)

## Done
- FTF-H028 — Trusted CI and exact-revision release evidence gate.
  CERTIFIED 2026-09-17, PR #55 (merge commit
  `1de330d7cbf71ba36ad8a65565eae1645fbd1929`), evidence commit `d502c89`.
  Independently re-verified: session-ID check (producer
  `session_01D4UBNWAuJwWHKpfHwSuV7U`, this reviewer
  `session_01MqgdZaVXK61u6s6scVuNkr` — no overlap), real diff read
  end-to-end, CI reconfirmed green on the PR's actual head via
  `pull_request_read get_check_runs` (`gate` + `docker-local-stack-
  integration`, run 35176632687) with the merge commit confirmed
  tree-identical to that head, full regression clean, `product_truth_check.py`
  0/0 confirmed legitimate (no proving_symbol — CI/gate tooling layer, no
  live product-runtime caller yet), a fresh fault injection (removing
  `check_branch_protection`'s force-push rejection) caught by
  `test_branch_protection_missing_settings_fails` and distinct from either
  of the producer's own two fault injections, an independently constructed
  fixture closing the "matrix-omitted" acceptance sub-clause the delivered
  test suite didn't itself exercise (no repo workflow uses a matrix today),
  and the real task-packet contract's acceptance/negative_acceptance
  clauses checked item by item. Full entry:
  `docs/certifications/ledger.md`.
- FTF-0001 — Initialize monorepo. CERTIFIED (docs/certifications/ledger.md).
- FTF-0002 — Add formatting/lint/type checks. CERTIFIED (docs/certifications/ledger.md).
- FTF-0003 — Add test harnesses. CERTIFIED (docs/certifications/ledger.md).
- FTF-0004 — Docker local stack. CERTIFIED 2026-09-16, PR #35 (merge
  commit `a477e678b785d3248bdc985c91586b148411b4fe`). Independently
  re-verified: own fault injection (4/9 checks fail on a canned-healthy
  stub), the actual `docker-local-stack-integration` CI job log read
  directly (real boot → marker row written → postgres stopped → readiness
  genuinely flips unhealthy → restart → recovery → marker row survives),
  `product_truth_check.py` 0/0 confirmed legitimate (no proving_symbol
  yet), and the delivered work checked against FTF-0004's real
  acceptance/negative_acceptance clauses via `tools/r1_handoff.py packet
  FTF-0004`. `web` is disclosed as deliberately excluded from the compose
  stack (apps/web has no server-side runtime yet; adding one now would be
  a new-runtime-dep ask-first item out of this task's scope) — accepted
  as a transparent, reasoned scope limitation, not a hidden gap; tracked
  as a follow-up for whichever task first builds the real web app. Full
  entry: docs/certifications/ledger.md.
- FTF-0005 — Configuration system. CERTIFIED (docs/certifications/ledger.md).
- FTF-0006 — Observability foundation. CERTIFIED 2026-09-16, PR #39
  (merge commit `268186bd5e70032a6c563796910059ab2684b684`). Independently
  re-verified in a fresh git worktree at the PR's actual final head: own
  fault injection (broke the `identity` safe-identifier regex — the
  content-exclusion check failed for real, distinct from the producer's
  own bounded-label fault injection), the real `gate` job log read
  directly (not inferred from the green check), `product_truth_check.py`
  confirmed legitimately 0/0 (no proving_symbol yet), and the delivered
  work checked against FTF-0006's real acceptance/negative_acceptance
  clauses via `tools/r1_handoff.py packet FTF-0006`. One residual gap
  disclosed, not hidden: the `identity` format boundary cannot
  distinguish a legitimate slug-style ID from an opaque token of the
  same shape — inert today (no live caller exists), tracked in
  `docs/spec-matrix.yaml`'s notes as a required follow-up for whichever
  task first wires a real caller into this seam. Full entry:
  docs/certifications/ledger.md.
- FTF-H001 — Executable specification and gate bootstrap. CERTIFIED
  2026-09-16, PR #43 (merge commit
  `be6494c3ec97615417f9f1e9fabad9dfa83feaf4`). Independently re-verified
  in a fresh git worktree at the PR's actual final head: real toolchain
  installed (`uv sync`, `pnpm install`) and both of the task's own
  `verification_commands` genuinely re-run (`validate_spec.py --strict`,
  `verify_task.py --task FTF-H001 --stage task`), full 7-task regression
  set clean, `product_truth_check.py` confirmed legitimately 0/0 (no
  proving_symbol — CI/gate tooling, not a live product-runtime path),
  `verify_release.py` confirmed non-inflated (`6/300`/`NOT READY` before
  this certification), and an own fault injection (disabled the
  cycle-detection result in `check_structural`, distinct from the
  producer's own two fault injections against `commit`/
  `expected_output_contains`) — the cyclic-dependency check failed for
  real, restored byte-identical. Delivered work checked against
  FTF-H001's real acceptance/negative_acceptance clauses via
  `tools/r1_handoff.py packet FTF-H001`. Full entry:
  docs/certifications/ledger.md.
- FTF-R001 — Freeze seven-component architecture, ownership and
  version-lock schemas. CERTIFIED 2026-09-16, PR #47 (merge commit
  `2e3e1af0b81789da4a21e68bd536c3baae9f2ac2`). Independently re-verified
  in a fresh git worktree at the real merged tip: real toolchain
  installed (`uv sync`, `pnpm install`) and both of the task's own
  `verification_commands` genuinely re-run (`validate_spec.py --strict`,
  `verify_task.py --task FTF-R001 --stage task`), full 8-task regression
  set clean, `product_truth_check.py` confirmed legitimately 0/0 (no
  proving_symbol — architecture/schema freeze, no live product-runtime
  caller yet), `verify_release.py` confirmed non-inflated (`7/300`/
  `NOT READY` before this certification), and two own fault injections
  (widened the mutable-tag pattern; loosened the release-authority
  maxItems/type constraint — both distinct from the producer's own two,
  which targeted the whole-set key check and the worker-package `allOf`
  rule) each let the corresponding negative_acceptance case through with
  zero errors, restored byte-identical, re-ran clean. Delivered work
  checked against FTF-R001's real acceptance/negative_acceptance clauses
  via `tools/r1_handoff.py packet FTF-R001`; the undelivered
  `ExternalRunBinding` schema is a disclosed scope deferral matching
  existing certified precedent (FTF-0004's `web` exclusion, FTF-H001's
  contract-stub exclusion), not a hidden gap. Full entry:
  docs/certifications/ledger.md.
- FTF-H002 — Freeze release scope, compatibility and measured execution
  inputs. CERTIFIED 2026-09-17, PR #51 (merge commit
  `b0f818498660fa0f376fdf1d2a0db42baac25e43`, superseding PR #49 which was
  closed after a real `gate` CI failure — an unisolated `kubectl`-presence
  assertion — was fixed in `419f9cc`). Independently re-verified: real
  toolchain installed (`uv sync` + `pip install pyyaml`, `pnpm install
  --frozen-lockfile`) and both of the task's own `verification_commands`
  genuinely re-run (`validate_spec.py --strict`, `verify_task.py --task
  FTF-H002 --stage task`), full 9-task regression set clean,
  `product_truth_check.py` confirmed legitimately 0/0 (no proving_symbol —
  preflight/release-scope-freeze layer, no live product-runtime caller
  yet), `verify_release.py` confirmed non-inflated (`8/300`/`FAILED (1):
  ['FTF-H002']`/`NOT READY` before this certification), CI reconfirmed
  green on the actual PR head (`c6985e0`, run 35168539940, both `gate` and
  `docker-local-stack-integration`), and an own fault injection (removed
  `check_managed_provider_bedrock`'s "unvalidated access" rejection so
  credential presence alone reports `AVAILABLE` — distinct from the
  producer's own fault injection against `PreflightResult`'s owner/
  next_action guard) caught for real by the delivered test suite, restored
  byte-identical, re-ran clean. Delivered work checked against FTF-H002's
  real acceptance/negative_acceptance clauses via `tools/r1_handoff.py
  packet FTF-H002` — no fabricated model ID/rate anywhere in
  `budget-forecast.md`, preflight reads `SOVRA_LITELLM_GATEWAY_URL`
  directly rather than through FTF-0005's `Settings.from_env()`. Full
  entry: docs/certifications/ledger.md.

## Queue (ordered by dependency)
Full 300-record dependency graph: `docs/TASK_INDEX.md` (canonical) and
`docs/spec-matrix.yaml` (proof-state per record). Do not re-copy the table
here — two copies drift. Read order for early work:
1. FTF-0001 → FTF-0002 → FTF-0003 (bootstrap, lint/type, test harness)
2. FTF-0005 (config) → FTF-0004 (docker local stack) → FTF-0006 (observability)
3. FTF-0101…0106 (identity/tenancy/RBAC/audit — CAP-01)
4. FTF-R001/R002 (freeze seven-component architecture + authenticated bindings —
   do this early; every reuse_architecture and most audit_hardening tasks depend
   on it transitively)
5. FTF-H001/H002 (release-scope freeze) before anything claims DONE

Every task's real acceptance/negative-acceptance/verification-command contract
now lives in `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolvable on demand. Relay:
before claiming any task, run
`python tools/r1_handoff.py packet <task-id> --plan FINE_TUNING_FACTORY_RELEASE_1.md`
to get its real DoD — read the generated
`docs/implementation/task-packets/<task-id>.md`, not just the title in
`docs/TASK_INDEX.md`. Write BLOCKER.md only if that command itself fails
(unknown task ID, hash-mismatch on a tampered/edited plan file, or similar) —
not merely because the task looked title-only elsewhere.

## Blocked
- FTF-0102 — Organization/workspace schema. Blocked on owner decision re:
  new runtime dependencies (SQLAlchemy/Alembic/Postgres driver) required
  by its own task contract. See "Owner actions — current" above, dated
  2026-09-16. Not claimed, no code written, no dependency installed.

## Done (certified + promoted via L4)
- [x] FTF-0001 — Initialize monorepo. CERTIFIED 2026-09-15 (control-plane
      L2). PR: https://github.com/SJZLab/Sovra/pull/3, merge commit
      7ab3f6ff1ac7bcc240e546b764af8d67ae0c8561. Ledger:
      docs/certifications/ledger.md. Not yet PROMOTED — L4 gate is
      release-only and hasn't run.
      Note: the original 2026-09-15 L2 certification was found
      self-certified (watchdog PR #13 — PR #3 and PR #5 share session
      identity, Law 4 violation), briefly reverted to PROVISIONAL, then
      independently re-verified and re-certified same day by a session
      with no session-ID overlap with either PR #3/#5 or PR #13. See
      docs/certifications/ledger.md for both entries. Count below was
      never inflated by this — it stayed 1/300 before and after, since
      the correction and re-certification landed together.
- [x] FTF-0002 — Add formatting/lint/type checks. CERTIFIED 2026-09-16
      (control-plane L2, session_01DY5FK4nK2VLrUMKHwNQQU2 — session-ID
      compared against both PR #23 commits' session_01NuAjwmK5rwuxefyo5L7zgy,
      no overlap). PR: https://github.com/SJZLab/Sovra/pull/23, merge
      commit 41f1493bca4f70aa74ab61516d1a22ee197daf79. Ledger:
      docs/certifications/ledger.md. Independently re-verified: fresh
      `uv sync`/`pnpm install`, fresh run of `tests/tasks/test_ftf_0002.py`
      (6/6 pass), a real fault injection (not just re-reading the
      producer's claim), the real GitHub Actions run on the actual merged
      PR head, and the delivered work checked against FTF-0002's real
      acceptance/negative_acceptance contract pulled via
      `tools/r1_handoff.py packet FTF-0002`. Not yet PROMOTED — L4 gate is
      release-only and hasn't run.
- [x] FTF-0003 — Add test harnesses. CERTIFIED 2026-09-16 (control-plane
      L2, session_018gZRsspGdmcTmtxkqRDE59 — session-ID compared against
      both PR #26 commits' session_01DY5FK4nK2VLrUMKHwNQQU2, no overlap).
      PR: https://github.com/SJZLab/Sovra/pull/26, merge commit
      e2c2dae96142c95a78005fd7528df6ac7e3e5dfc. Ledger:
      docs/certifications/ledger.md. Independently re-verified in a fresh
      git worktree at the actual merge commit: fresh `uv sync`/`pnpm
      install`, fresh runs of the real CI test commands (pytest + Vitest +
      `check_test_harness.py` + `tests/tasks/test_ftf_0003.py`), three
      independent fault injections (empty pytest dir, a `pytest.mark.skip`
      fixture, a Vitest `test.only` fixture — each shown to fail the gate
      for real), the real GitHub Actions run on the actual merged PR head,
      and the delivered work checked against FTF-0003's real
      acceptance/negative_acceptance contract pulled via
      `tools/r1_handoff.py packet FTF-0003`. Confirmed no regression to
      FTF-0001/0002's certified evidence (both files untouched by this
      PR's diff). Not yet PROMOTED — L4 gate is release-only and hasn't
      run.
- [x] FTF-0005 — Configuration system. CERTIFIED 2026-09-16 (control-plane
      L2, session_01WqPEZmJdJyFH7tk2cbakWP — session-ID compared against
      PR #30's commits' session_012mAtoqKYzA7PiXbkGkD7rY, no overlap).
      PR #28 (initial delivery) was reviewed 2026-09-16 and NOT certified —
      a real repr secret-redaction defect on `litellm_gateway_url` (see
      docs/certifications/queue.md). PR:
      https://github.com/SJZLab/Sovra/pull/30 (scoped fix), merge commit
      5f5a34d803a76e3261c8cc860bd5f21cd8290424. Ledger:
      docs/certifications/ledger.md. Independently re-verified in a fresh
      git worktree at the actual PR head commit: fresh `uv sync`, fresh run
      of `tests/tasks/test_ftf_0005.py` (10/10 pass), an independent fault
      injection reverting the fix (exactly 1 check failed for real,
      reproducing the original finding; restored clean), the real GitHub
      Actions run on the actual PR head SHA, `product_truth_check.py`
      confirmed legitimately 0/0 (`proving_symbol: ''`, no live caller
      yet), and the delivered fix checked against FTF-0005's real
      negative_acceptance contract pulled via `tools/r1_handoff.py packet
      FTF-0005`. Confirmed no regression to FTF-0001/0002/0003's certified
      evidence or to the other 8 originally-proven FTF-0005 checks. Not yet
      PROMOTED — L4 gate is release-only and hasn't run.
- [x] FTF-0006 — Observability foundation. CERTIFIED 2026-09-16
      (control-plane L2, session_01LPY7Wy6ASA6BiQnvu9eyvF — session-ID
      compared against PR #39's commits' session_01GvzemvrJeMmGiZsAAPK5m1,
      no overlap). PR: https://github.com/SJZLab/Sovra/pull/39, merge
      commit 268186bd5e70032a6c563796910059ab2684b684. Ledger:
      docs/certifications/ledger.md. Independently re-verified in a fresh
      git worktree at the PR's actual final head (`0970fa8`, confirmed
      live via `pull_request_read`): fresh `uv sync`/`pnpm install`, fresh
      run of `tests/tasks/test_ftf_0006.py` (10/10 pass), an independent
      fault injection distinct from the producer's own (broke the
      `identity` safe-identifier regex instead of the bounded-label
      check — exactly the content-exclusion check failed for real;
      restored byte-identical, re-ran clean), the real GitHub Actions
      `gate` job log read directly on the actual final head SHA,
      `product_truth_check.py` confirmed legitimately 0/0
      (`proving_symbol: ''`, no live caller yet), and the delivered work
      checked against FTF-0006's real acceptance/negative_acceptance
      contract pulled via `tools/r1_handoff.py packet FTF-0006`.
      Confirmed no regression to FTF-0001/0002/0003/0004/0005's certified
      evidence. One residual gap disclosed rather than hidden: the
      `identity` field's format boundary cannot distinguish a legitimate
      slug-style ID from an opaque token of the same shape — inert today
      since no live caller exists, tracked as a required follow-up in
      `docs/spec-matrix.yaml`'s notes. Not yet PROMOTED — L4 gate is
      release-only and hasn't run.
