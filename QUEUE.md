# QUEUE — Sovra (Model Factory)
Updated: 2026-09-20 (same relay shift, follow-up) — PR #120 (FTF-0105)
  was merged directly by the repo owner (`merged_by: samerzakaria`,
  confirmed via `pull_request_read`) 6 minutes after opening, on green CI
  (8/8), before any independent Control-plane L2 review ran. This is an
  owner action, not a session action, and does not itself certify the
  work: `docs/spec-matrix.yaml` on `main` still reads
  `certification_state: PROVISIONAL` / `proof_state: L1-PROVEN` for
  FTF-0105 (confirmed directly against `origin/main` after the merge, not
  assumed). Matches CLAUDE.md's own "Do without asking" allowance ("merge
  on green + L1 doc (PROVISIONAL)") and Law 4 ("Merge = PROVISIONAL
  only") — merged code is not certified code. FTF-0106 still lists
  FTF-0105 as a direct dependency and is not claimed this shift; per this
  session's own prior "Next shift" note, a dependent task on FTF-0105
  waits for it to be independently CERTIFIED, not merely merged, matching
  every prior task's own established precedent in this queue's history.

Earlier the same date (scheduled relay shift) — picked up the next
  unblocked item per the prior shift's own dependency graph (FTF-0105,
  Tenant-isolation query guards — FTF-0102/FTF-0103 both CERTIFIED,
  unblocking FTF-0105/0106's own dependency chain). Full submission
  report: docs/certifications/queue.md; see "## Provisional" below for
  the summary.

Earlier the same date (2026-09-19, scheduled Control-plane certifier shift, triggered by
  the `pull_request.opened` webhook for PR #118) — independent CERTIFY
  review of FTF-0103 (RBAC middleware): **CERTIFIED**. Session-ID clean
  against all 5 commits on PR #118, CI reconfirmed green (7/7) on the real
  final head after one doc-only mid-review head-move, two independent
  fault injections (body-tenant-mismatch flip; unknown-membership
  auto-downgrade) each caught for real, `product_truth_check.py`'s `1/1`
  confirmed legitimate (empty `proving_symbol`, no live-wiring claim), and
  the real task contract (`tools/r1_handoff.py packet FTF-0103`) confirms
  both acceptance clauses and the negative_acceptance clause are met for
  this task's own scope. Merged PR #118 (merge commit `b4b47c0`);
  `docs/spec-matrix.yaml` set to `CERTIFIED`/`L2-CERTIFIED`; full entry in
  `docs/certifications/ledger.md`. Full review history:
  `docs/certifications/queue.md`.

Earlier the same date (scheduled relay shift) — picked up the next
  unblocked item per the prior shift's own "Next shift" note (FTF-0103,
  RBAC middleware — FTF-0101/0102/0104 all CERTIFIED, unblocking
  FTF-0103/0105/0106's dependency graph). Full submission report:
  docs/certifications/queue.md.

Earlier the same date (scheduled Control-plane certifier shift, triggered by
  the `pull_request.opened` webhook for PR #114) — independent CERTIFY
  review of FTF-0101 (OIDC login, real server-side refresh follow-up):
  **CERTIFIED**. Both gaps the two prior 2026-09-19 reviews found (PR #103:
  real-provider evidence missing entirely; PR #112: refresh unimplemented,
  untested and undisclosed) are closed for real in PR #114 — session-ID
  clean, CI reconfirmed green (7/7) on the real final head after two
  mid-review head-moves, own independent fault injection (disabling the
  refresh identity-mismatch check, re-run the way CI actually invokes this
  suite — `python3 tests/tasks/test_ftf_0101.py`, not pytest — since a bare
  `pytest -k` run of this file misleadingly reports PASSED regardless of
  its soft `check()`/`FAILURES` results) caught the break for real,
  `product_truth_check.py`'s `1/1` confirmed a genuine live-caller wiring,
  and the real task contract (`tools/r1_handoff.py packet FTF-0101`)
  confirms acceptance clause 1 (login+refresh+logout against a real OIDC
  provider) and the negative_acceptance clause are both met. Merged PR
  #114 (merge commit `5de1c58`); `docs/spec-matrix.yaml` set to
  `CERTIFIED`/`L2-CERTIFIED`; full entry in `docs/certifications/ledger.md`.
  Full review history for PR #103/#112/#114: `docs/certifications/queue.md`.

  Earlier the same date: the owner directly confirmed, live in
  conversation, **option 1** for FTF-0101's 2026-09-18
  new-runtime-dependency ask: FastAPI + uvicorn + authlib + itsdangerous
  (Python) and Next.js + React + react-dom (TypeScript) — the implied
  default stack already assumed by the architecture docs for
  `apps/api`/`apps/web`. This resolved the core web-framework/auth-library
  piece of the ask; FTF-0101 became dependency-ready that same shift
  (moved out of "## Blocked").
Relay: 2h · Report: 08:30 & 19:30 Asia/Riyadh · Certifier: Control plane (autonomous)

## Status
Certified: 21/300 (main; FTF-0103 CERTIFIED 2026-09-19, PR #118 merged as
`b4b47c0` — see docs/certifications/ledger.md) · Provisional: 1
(FTF-0105, Tenant-isolation query guards — submitted this shift; PR #120
merged directly by the repo owner on green CI before independent review,
still awaiting Control-plane L2 certification — merged is not certified,
see banner above) · Blocked: 0
(FTF-0101's web-framework/auth-stack approval and its own
local-OIDC-test-provider package choice were both resolved 2026-09-19 by
live owner instruction/confirmation — see "## Blocked" history below; no
open owner ask remains)
Product-true (Law 16): 1 — must match certified (FTF-0101 is the one
live-wired product-runtime path: `build_oidc_login_router` is imported and
called unconditionally from `apps/api/app/main.py`'s `create_app()`,
confirmed via `product_truth_check.py --fail-on-unwired` -> `1/1` and a
direct `grep` on `main.py`, not merely asserted. FTF-0001, FTF-0002,
FTF-0003, FTF-0004, FTF-0005, FTF-0006, FTF-H001, FTF-R001, FTF-H002,
FTF-H028, FTF-H034, FTF-1810, FTF-R014, FTF-R015, FTF-R017, FTF-0102,
FTF-0104 and FTF-1601 all have no proving_symbol; FTF-0001 is structural
layout, FTF-0002 is CI tooling, FTF-0003 is test-harness tooling, FTF-0004
is local-stack bootstrap/health tooling, FTF-0005 is a configuration
module with no live caller yet, FTF-0006 is an observability seam with no
live FastAPI app or Temporal workflow to instrument yet, FTF-H001 is the
spec/gate verifier tooling itself, FTF-R001 is an architecture/version-lock
schema freeze with no live product-runtime caller yet, FTF-H002 is a
preflight/release-scope-freeze layer with no live product-runtime caller
yet, FTF-H028 is the release-evidence-gate/CI-aggregator tooling itself,
`workflow_dispatch`-only and never run by the relay, FTF-H034 is the
task-claim scheduler/tracker-sync tooling itself, with no live multi-agent
dispatch harness wiring it into a running caller yet, FTF-1810 is the
release-qualification aggregator tooling itself — run against the real
repo it honestly reports NOT READY (every one of the 43 required scenarios
missing evidence, image/profile bindings BLOCKED_EXTERNAL),
`workflow_dispatch`-only and never run by the relay, FTF-R014 is
build/implementation tooling binding the selected coding executor to
Section 84's controls, with no Factory production service importing it,
and FTF-R015 is the safe-parallel-execution/orchestration tooling itself
(workspace manager, executor adapter, task orchestrator), with no live
multi-agent dispatch harness wiring it into a running caller yet, FTF-R011
is a release-matrix governance/validator layer with no Factory production
service importing it, FTF-R017 is a storage/OSS-dependency-lifecycle
qualification layer (SigV4 client, component-maintenance inventory,
storage-profile qualification) with no live Factory production service
importing `storage_semantics_client.py` yet, FTF-0102 is the
Organization/Workspace ORM/migration/RLS layer with no live FastAPI app
importing `organization_workspace_schema.py` yet, FTF-0104 is the audit
event writer with no live FastAPI app importing `audit_event_writer.py`
yet, FTF-1601 is the Postgres backup/restore tooling with no live
FastAPI app or Temporal workflow importing `postgres_backup.py` yet, and
FTF-0103 is the RBAC middleware enforcement primitive with no live
`/api/v1` route anywhere in this repository yet to wire `require_role`
into — none a live-wired product-runtime path, so all twenty are
CERTIFIED without counting toward product-true).

## Owner actions — current
None currently. The one open ask (FTF-0101's local-OIDC-test-provider
package choice) was resolved 2026-09-19 by live owner instruction — see
the banner above and "## Blocked" history below.

## Active (awaiting Control plane — no relay action left)
None currently. FTF-1601 (Postgres backup) was the only item here; it was
independently reviewed and CERTIFIED this shift via PR #100 (merge commit
`f4e9b39`) — see "## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the
full history and evidence.

## Provisional (awaiting Control plane L2 certification)
- FTF-0105 — Tenant-isolation query guards. Submitted this shift (relay,
  scheduled, non-interactive): `apps/api/app/auth/tenant_isolation_query_guards.py`
  (`tenant_scoped_session` — the one blessed way to open a tenant-scoped
  unit of work, request handler or background job alike, fail-closed
  before a session is even created; `execute_tenant_scoped` — the guard a
  raw-SQL/background-job caller must use instead of `session.execute(...)`
  directly, refusing to run a statement at all on a tenant-context
  mismatch; `install_tenant_pool_reset_guard` — a real SQLAlchemy pool
  `checkin` listener force-clearing the tenant GUID on every connection
  returned to the pool) + `tests/tasks/test_ftf_0105.py` (30 no-DB checks)
  + `tests/integration/test_tenant_isolation_query_guards.py` (real
  live-Postgres proof: connection-pool reuse, a naive background-job-style
  session, and a raw-SQL context mismatch all genuinely cannot leak a
  previous tenant's rows; Law 1 shown able to fail twice for the
  pool-reset guard itself — a bare session-level `SET` misuse genuinely
  leaks without the guard installed and genuinely does not leak with it
  installed, on the same real pool). Disclosed, not hidden: no live
  product caller (no FastAPI route or background-worker service) exists
  anywhere in this repository yet that imports this module, so it does
  not count toward product-true (`product_truth_check.py` stays `1/1`).
  Full submission report: docs/certifications/queue.md.
  `certification_state: PROVISIONAL` / `proof_state: L1-PROVEN` in
  docs/spec-matrix.yaml. PR: https://github.com/SJZLab/Sovra/pull/120
  (`claude/wizardly-fermat-daazqp` → `main`).

Prior item: FTF-0103 (RBAC middleware) was independently reviewed and
CERTIFIED a prior shift via PR #118 (merge commit `b4b47c0`) — see
"## Done" for the full narrative and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the complete review history.

## Done
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
- FTF-H028 — Trusted CI and exact-revision release evidence gate.
  CERTIFIED 2026-09-17, PR #55 (merge commit
  `1de330d7cbf71ba36ad8a65565eae1645fbd1929`). Independently re-verified:
  real toolchain installed (`pip install pyyaml`, `uv sync`, `pnpm install
  --frozen-lockfile`) and both of the task's own `verification_commands`
  genuinely re-run (`validate_spec.py --strict`, `verify_task.py --task
  FTF-H028 --stage task`), full 10-task regression set clean,
  `product_truth_check.py` confirmed legitimately 0/0 (no proving_symbol —
  CI/gate-tooling layer, `workflow_dispatch`-only), `verify_release.py`
  confirmed non-inflated (`9/300`/`FAILED (1): ['FTF-H028']`/`NOT READY`
  before this certification, `10/300` after), CI reconfirmed green on the
  actual PR head (`2040620`, run 35176632687, both `gate` and
  `docker-local-stack-integration`), the live branch-protection 403 finding
  reproduced independently with this session's own token, and an own fault
  injection (made `check_branch_protection`'s 403/404 branch return `PASS`
  instead of `BLOCKED_EXTERNAL` — distinct from the producer's own two
  fault injections) caught for real by the delivered test suite, restored
  byte-identical, re-ran clean. Also confirmed the two open items left by
  the prior FTF-H002 review cycle's own note (stale-SHA discrimination
  distinct from "missing", and no check treating `BLOCKED_EXTERNAL` as
  equivalent to `PASS` in the composition) directly against the delivered
  code and tests. Delivered work checked against FTF-H028's real
  acceptance/negative_acceptance clauses via `tools/r1_handoff.py packet
  FTF-H028`. Full entry: docs/certifications/ledger.md.
- FTF-H034 — Safe parallel scheduler and task/evidence ownership.
  CERTIFIED 2026-09-17, PR #59 (merge commit
  `adba85013f58712415e871ab258f4262bf1ae567`). Independently re-verified:
  real toolchain installed (system `python3` for the task scripts, `uv
  sync`, `pnpm install --frozen-lockfile`) and both of the task's own
  `verification_commands` genuinely re-run (`validate_spec.py --strict`,
  `verify_task.py --task FTF-H034 --stage task`), full 11-task regression
  set clean, `product_truth_check.py` confirmed legitimately 0/0 (no
  proving_symbol — scheduler/tracking tooling, no live multi-agent
  dispatch caller yet), `verify_release.py` confirmed non-inflated
  (`10/300`/`FAILED (1): ['FTF-H034']`/`NOT READY` before this
  certification, `11/300` after), CI reconfirmed green on the actual PR
  head (`37d7bcd`, run 35184262911, both `gate` and
  `docker-local-stack-integration`), and an own fault injection (removed
  the `DEPENDENCY_NOT_READY` guard from `claim()` — distinct from the
  producer's own committed fault injection, which targeted the
  self-certification guard in `complete()`) caught for real by the
  delivered test suite, restored byte-identical, re-ran clean. Also
  confirmed the PR's own `sync_certified_status()` diff to
  `spec/tasks.json`/`TRACKER.json` touched only the `status`/`evidence`
  fields of the 10 already-certified task IDs and nothing else; this
  certification's own `certification_state: CERTIFIED` edit then made
  `test_real_committed_tracker_and_tasks_reflect_actual_certified_set`
  fail (correctly — FTF-H034 itself wasn't yet reflected), so
  `sync_certified_status()` was re-run once more against the real repo
  files post-certification (diff limited to FTF-H034's own two fields,
  confirmed via `git diff`), restoring 17/17. Delivered work checked
  against FTF-H034's real acceptance/negative_acceptance clauses via
  `tools/r1_handoff.py packet FTF-H034`; the real-8-OS-thread
  parallel-wave proof and the disclosed no-live-subagent-harness
  limitation both match the task's own negative_acceptance rather than
  quietly narrowing its scope. Full entry: docs/certifications/ledger.md.
- FTF-1810 — ReleaseQualificationWorkflow. CERTIFIED 2026-09-17, PR #63
  (merge commit `84b71cfc254ea1ff90441a11692f6d77260d7eb7`). Independently
  re-verified: real toolchain installed (`uv sync`, `pnpm install
  --frozen-lockfile`), all 16 of FTF-1810's own checks plus the full
  12-task existing regression set clean (`ruff`/`format`/`mypy`/
  `pytest`/`eslint`/`tsc`/`prettier`/`vitest` all clean with the pinned
  toolchain), `product_truth_check.py` confirmed legitimately 0/0 (no
  proving_symbol in `docs/spec-matrix.yaml`), `validate_spec.py --strict`
  clean, `verify_task.py --task FTF-1810 --stage task` -> `PASS`,
  `verify_release.py` confirmed non-inflated (`11/300`/`FAILED (1):
  ['FTF-1810']`/`NOT READY` before this certification, `12/300`/`NOT
  READY` after), CI reconfirmed green on the actual PR head (`e4ac93b`,
  run 35192517673, both `gate` and `docker-local-stack-integration`), and
  an own fault injection (removed the `incomplete`-evidence field check —
  distinct from the producer's committed `skipped`-check injection and
  its own ad hoc `stale`-check injection) caught for real by the
  delivered test suite (exactly the 3 expected checks failed), restored
  byte-identical, re-ran clean. Ran the real aggregator against the
  actual current repo directly (not just re-reading the producer's
  claim): honestly reports `overall_status: FAIL`, all 43 scenarios
  `missing`, both bindings `BLOCKED_EXTERNAL` — matches the disclosed
  claim exactly. Ran `sync_certified_status()` after the
  `certification_state: CERTIFIED` edit — diff limited to exactly
  FTF-1810's own `status`/`evidence` fields in `spec/tasks.json`/
  `TRACKER.json`, confirmed via `git diff`. Delivered work checked
  against FTF-1810's real acceptance/negative_acceptance clauses via
  `tools/r1_handoff.py packet FTF-1810 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`. Full entry:
  docs/certifications/ledger.md.
- FTF-R014 — Bind selected coding executor to the embedded Release 1
  execution controls. CERTIFIED 2026-09-17, PR #68 (merge commit
  `222c665843da206929df50a3857f659a10ec5dd6`). Independently re-verified
  in a fresh, unshallowed git worktree at the PR's actual final head: CI
  reconfirmed green on the real head (`cc9b7e2`, run 35203455799, both
  `gate` and `docker-local-stack-integration`), all 24 of
  `tests/implementation/test_r1_execution_binding.py`'s checks plus the
  full 12-task existing regression set clean (`ruff`/`format`/`mypy`/
  `eslint`/`tsc`/`prettier`/`vitest`/`pytest` all clean with the pinned
  toolchain), `product_truth_check.py` confirmed legitimately 0/0 (no
  proving_symbol — no Factory production service imports this module,
  confirmed via `grep`), `validate_spec.py --strict` clean,
  `verify_task.py --task FTF-R014 --stage task` -> `PASS`,
  `verify_release.py` confirmed non-inflated (`12/300`/`FAILED (1):
  ['FTF-R014']`/`NOT READY` before this certification, `13/300`/`NOT
  READY`, no FAILED, after). Own fault injection against the tracked
  module on disk (removed the `SELF_CERTIFICATION_DENIED` guard block,
  reproducing the producer's own claim independently rather than trusting
  it) confirmed a self-certified receipt wrongly `PASS`es with the guard
  removed and is correctly rejected once restored byte-identical. Ran
  `sync_certified_status()` after the `certification_state: CERTIFIED`
  edit — diff limited to exactly FTF-R014's own `status`/`evidence`
  fields in `spec/tasks.json`/`TRACKER.json`. Delivered work checked
  against FTF-R014's real acceptance/negative_acceptance clauses via
  `tools/r1_handoff.py packet FTF-R014 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`; `.github/CODEOWNERS` confirmed to
  protect the two new gate files under the same release authority
  (`owner:samerzakaria`) the code itself checks against. Full entry:
  docs/certifications/ledger.md.
- FTF-R015 — Enforce isolated concurrent coding execution and independent
  integration. CERTIFIED 2026-09-17, PR #71 (merge commit
  `8f4a6fd25df3439dac166e324ecf9b4e0e7d8034`; fix/re-review of PR #70,
  merged earlier to `main` as PROVISIONAL). Background: a prior
  independent Control-plane session found FTF-R015's own resolved
  acceptance clause 1 ("record actual overlap, leases, **commits**, tests
  and an independent integrated merge gate") had no corresponding
  commit-evidence code/test anywhere in PR #70 and did not certify (Law
  1/Law 5); the same session then implemented the fix (`execute_task` now
  captures a task's real `git rev-parse HEAD` as `ExecutionResult.
  commit_sha`, `None` when no commit was made — never fabricated) and
  opened PR #71 for a fresh certifying session. This session's
  independent re-verification (session-ID clean against both PR #70's and
  PR #71's commits): read the PR #71 diff directly; CI reconfirmed green
  on the real head (`6a47f08`, both `gate` and
  `docker-local-stack-integration`); own re-run in a fresh, unshallowed
  worktree — all 36 `tests/implementation/test_safe_parallel_executor.py`
  checks plus the full 15-script existing regression set clean
  (`ruff`/`format`/`mypy`/`eslint`/`tsc`/`prettier`/`vitest`/`pytest`, all
  with `pnpm install --frozen-lockfile` run first); an own fault injection
  distinct from both prior sessions' (mutated `_read_commit_sha` to return
  a fabricated SHA instead of `None` on failure) caught a real regression,
  restored byte-identical; `tools/r1_handoff.py packet FTF-R015` confirmed
  the fix closes exactly the identified gap, with every acceptance/
  negative_acceptance clause mapped to a specific passing test;
  `validate_spec.py --strict` clean, `verify_task.py --task FTF-R015
  --stage task` -> `PASS`, `product_truth_check.py` legitimately `0/0`,
  `verify_release.py` confirmed non-inflated (`13/300`/`FAILED (1):
  ['FTF-R015']` before, `14/300`/`FAILED (1): ['FTF-R011']` after — the
  remaining FAILED is the separate, still-PROVISIONAL FTF-R011, not this
  task). Ran `sync_certified_status()` — diff limited to exactly
  FTF-R015's own `status`/`evidence` fields. Full entry:
  docs/certifications/ledger.md.
- FTF-R011 — Define immutable connected and air-gap assertion-level release
  matrix. CERTIFIED 2026-09-17, PR #72 (merge commit
  `71501cb3692ac34ee71e52ba80c424a7e021c40e`). PR #72's `mergeable_state`
  was `dirty` at review time (this session's own FTF-R015 certification
  bookkeeping had just advanced `main` past PR #72's base) — fixed by
  merging current `main` into the PR branch (only real conflict was
  `PROGRESS.md`, resolved chronologically, nothing dropped) and re-pushing;
  CI re-ran and passed on the new head (`6be8eab`, both `gate` and
  `docker-local-stack-integration`) before merging. Independent
  re-verification (session-ID clean against
  `session_018Whdzxf76ttaAQnag7YY2E`): read
  `scripts/validate_profile_scope.py`/`spec/deployment-profiles.yaml`/
  `tests/security/test_profile_scope.py` directly; own re-run in a fresh,
  unshallowed worktree — full 16-script existing regression set clean
  (`ruff`/`format`/`mypy`/`eslint`/`tsc`/`prettier`/`vitest`/`pytest`, all
  with `pnpm install --frozen-lockfile`); an own fault injection distinct
  from the producer's own (widened `ALLOWED_STATUSES` to admit `"N/A"`)
  caught exactly the 2 checks that should fail, restored byte-identical;
  the Law 15 concern (editing `spec/deployment-profiles.yaml`, created by
  certified FTF-R001) independently re-verified, not just trusted — `grep`
  confirms FTF-R001's entire certified evidence surface never reads that
  file, and `yaml.safe_load`-parsed `profiles:` mappings before/after are
  equal; `tools/r1_handoff.py packet FTF-R011` confirmed both acceptance
  clauses and all four negative_acceptance cases map to specific,
  independently re-run tests; `validate_spec.py --strict` clean,
  `verify_task.py --task FTF-R011 --stage task` -> `PASS`,
  `product_truth_check.py` legitimately `0/0`, `verify_release.py`
  confirmed non-inflated (`14/300` before, `15/300`/zero FAILED after).
  Ran `sync_certified_status()` — diff limited to exactly FTF-R011's own
  `status`/`evidence` fields. Full entry: docs/certifications/ledger.md.
- FTF-R017 — Qualify maintained internal storage and OSS dependency
  lifecycle. CERTIFIED 2026-09-17, PR #75 (merge commit
  `aacd74528ff39ef14b0efc42575fe987f27490d4`). PR #75's `mergeable_state`
  was `dirty` at review time (a same-shift docs-only PR, #77, had just
  advanced `main` past PR #75's base) — fixed the same way as the
  FTF-0004/FTF-R011 precedent: merged current `main` into the PR branch
  (only real conflicts were `QUEUE.md`/`PROGRESS.md`, resolved by keeping
  `main`'s fuller, already-reconciled history; no code conflict) and
  re-pushed; a fresh, genuine CI run on the new head (all 3 jobs) passed
  before merging. Independent re-verification (session-ID clean against
  producer sessions `session_016bZ7g5DPgSU8eYMTo8H5NY`/
  `session_01Nzct85anuqYiaChHWrojc7`/`session_01CuWKZ8HB6vaFGqxfYg8aVL`/
  `session_01YVhNTy4brYqAztqcSzWLrs`): read the full delivered diff
  directly; read the real `storage-semantics-integration` job log on two
  separate genuine CI runs (both ending `FTF-R017 storage semantics: all
  checks passed` against a live SeaweedFS 3.99 container, real S3
  responses including 400/403/404 negative cases, not just 200s); an own
  fault injection distinct from the producer's own (broke `_signing_key`'s
  AWS4 prefix) caught the exact SigV4 signature-mismatch failure, restored
  byte-identical; `tools/r1_handoff.py packet FTF-R017` confirmed both
  acceptance clauses and the negative_acceptance clause map to specific,
  independently re-run tests; `validate_spec.py --strict` clean (a
  transient `stale_commit` defect traced to this review's own shallow
  clone, not real), `verify_task.py --task FTF-R017 --stage task` ->
  `PASS`, `product_truth_check.py` legitimately `0/0`, full regression
  (`ruff`/`mypy`/`pnpm lint`/`typecheck`/`format:check`) clean. Ran
  `sync_certified_status()` — diff limited to exactly FTF-R017's own
  `status`/`evidence` fields. Full entry: docs/certifications/ledger.md.
- FTF-0102 — Organization/workspace schema. CERTIFIED 2026-09-18, PR #83
  (merge commit `18749323d091a6f6a23be2e9510692035911ac25`). Independent
  re-verification (session-ID clean against producer session
  `session_01FLEHnA2RjFZB9RftzV4Lso`): read the full delivered diff
  directly; re-ran both delivered test suites against a real,
  directly-installed local PostgreSQL 16 cluster (no container-registry
  egress in this sandbox, same FTF-0004/FTF-R017 finding); an own fault
  injection matching the producer's own claim exactly (granting
  `sovra_app` `BYPASSRLS` caught the same 6 real failures, restored
  byte-identical); a second, independent fault injection (breaking the
  `workspaces` policy's `WITH CHECK` to `true`) surfaced a non-blocking
  observation about Postgres's own SELECT-policy/RETURNING interaction
  masking that specific fault for the ORM-only code path that actually
  exists — recorded in docs/certifications/ledger.md, not a live defect;
  read the real `organization-workspace-schema-integration` CI job log
  directly on the actual merged head (genuine Postgres 16.15 container,
  real RLS/unique-constraint errors); `tools/r1_handoff.py packet
  FTF-0102` confirmed both acceptance clauses and the negative_acceptance
  clause map to specific, independently re-run tests; `validate_spec.py
  --strict` clean (a transient `stale_commit` defect traced to this
  review's own shallow clone, not real), `verify_task.py --task FTF-0102
  --stage task` -> `PASS`, `product_truth_check.py` legitimately `0/0`,
  full regression (`ruff`/`mypy`/`pnpm lint`/`typecheck`/`format:check`/
  every existing task test/`pytest`/`vitest`) clean. Ran
  `sync_certified_status()` — diff limited to exactly FTF-0102's own
  `status`/`evidence` fields. `verify_release.py` non-inflated (`16/300`
  before, `17/300`/zero FAILED after). Full entry:
  docs/certifications/ledger.md.
- FTF-0104 — Audit event writer. CERTIFIED 2026-09-18, PR #87 (merge
  commit `c682687af99230efcbbdd1942cefcf933844b826`). Independent
  re-verification (session-ID clean against producer session
  `session_01FbdsNrj2DRZh92AB3edHUJ`, all 5 PR commits): read the full
  delivered diff directly; re-ran the no-DB suite in a fresh
  `uv sync --all-packages` environment; own fault injection against the
  no-DB suite (removed the `actor_type` allow-list check — caught for
  real, restored byte-identical); real, independent live-Postgres
  reproduction (directly-installed local PostgreSQL 16, no
  container-registry egress in this sandbox, same FTF-0004/FTF-R017/
  FTF-0102 finding) — ran the real migration, inspected the real GRANT
  surface via `psql \dp` (`sovra_app=ar/sovra_migrate`, confirmed
  SELECT+INSERT only), ran the integration suite clean; own independent
  fault injection directly against the live database (`GRANT UPDATE,
  DELETE ... TO sovra_app` via `psql`, not through the producer's own
  script) — exactly the 3 append-only negative_acceptance checks failed
  for real, matching the producer's own claim; waited out CI on the
  actual final PR head (the producer pushed one more tracking-only
  commit mid-review; confirmed via `actions_get get_workflow_run` that
  the workflow run's `head_sha` matched) — all 5 jobs `success`, read the
  `audit-event-writer-integration` job's log directly (genuine Postgres
  16.15 container, real `permission denied for table audit_events`
  errors on both UPDATE and DELETE); `tools/r1_handoff.py packet
  FTF-0104` confirmed both the acceptance and negative_acceptance clauses
  map to specific, independently re-run/reproduced evidence; double-checked
  the PR body's governance claim that PR #82/#84 were closed as
  stale/superseded — confirmed via direct `pull_request_read`;
  `validate_spec.py --strict` clean, `verify_task.py --task FTF-0104
  --stage task` -> `PASS`, `product_truth_check.py` legitimately `0/0`,
  full regression (`ruff`/`mypy`/`pnpm lint`/`typecheck`/`format:check`/
  every existing task test including FTF-0102's own integration test/
  `pytest`/`vitest`) clean. Ran `sync_certified_status()` — diff limited
  to exactly FTF-0104's own `status`/`evidence` fields. `verify_release.py`
  non-inflated (`17/300` before, `18/300`/zero FAILED after). Full entry:
  docs/certifications/ledger.md.
- FTF-1601 — Postgres backup. CERTIFIED 2026-09-19, PR #100 (merge commit
  `f4e9b39b5cb9e8cc1222dde8e03f9e4d6cf9dbf4`) — third and final PR in the
  #90→#96→#100 supersession chain, same underlying implementation commit
  `f5d3aee` throughout. Independent re-verification (session-ID clean
  against every commit unique to this PR: session_01B9eYtKy6gPX3Ym8Cvx3X9c,
  session_01RDefDg12JGVTtU732EEARs, session_01TkFfAaPq6htuVNNLXU9G2b,
  session_016ryLQ1vSJCNYTMHw9DrcSd, session_015rZiy6EjN6jvYYWzkGE2gh — none
  match this reviewing session's own ID): `git log origin/main..PR-100`
  and `git diff` against PR #96's own already-reviewed-sound head confirmed
  this PR's only delta is a tracking-doc merge-conflict resolution
  (`PROGRESS.md`/`QUEUE.md`/`docs/certifications/queue.md`) — zero
  production code, test, or contract change since the implementation
  (`f5d3aee`, independently live-Postgres-reproduced and fault-injected on
  the encrypt/self-verification path by the 2026-09-18/PR #90 review) and
  its fix (`353b82b`, independently fault-injection-reproduced on each of
  `restoration_validated`'s three subset terms by the 2026-09-19/PR #96
  review). CI reconfirmed green (6/6, including `postgres-backup-integration`
  on a real Postgres 16 container) directly on the PR's actual final head
  (`4a2f3d3`, not a stale earlier commit — the PR advanced mid-review with
  one further doc-only commit), `mergeable_state: "clean"`, zero open
  review threads. Own regression in a fresh worktree at the real merged
  head: `ruff`/`mypy`/`pnpm lint`/`typecheck`/`format:check` clean, the
  no-DB suite (18/18) and full existing test suite clean, `validate_spec.py
  --strict` clean, `product_truth_check.py` legitimately `0/0`.
  `tools/r1_handoff.py packet FTF-1601` confirmed both acceptance clauses
  and the negative_acceptance clause map to the already-reproduced evidence
  above. Did not repeat the live-Postgres integration test or the two
  fault injections locally — no code changed since they were independently
  reproduced by two prior, distinct Control-plane sessions, and CI's own
  job re-confirmed the same real backup/restore path green on this exact
  head. Full entry: docs/certifications/ledger.md.
- FTF-0101 — OIDC login. CERTIFIED 2026-09-19, PR #114 (merge commit
  `5de1c58832ea7e34d0c91f3083654b1fa46e909a`) — third and final PR in the
  #103→#112→#114 supersession chain. PR #103 (2026-09-19 review): NOT
  CERTIFIED — acceptance clause 1's "Real OIDC provider" evidence rested
  only on a fake stdlib stand-in. PR #112 (2026-09-19 review): NOT
  CERTIFIED — login/logout newly proven for real against a real `idpyoidc`
  OpenID Provider, but "refresh" (a named third of the same compound
  acceptance clause) was neither implemented nor tested anywhere, and
  claimed MET without disclosure. PR #114 closes that exact gap: real
  `exchange_refresh_token()` / `POST /auth/refresh` against the real OP's
  own token endpoint, real session rotation, real CSRF enforcement — proven
  end to end by two new tests in
  `tests/integration/test_oidc_login_real_provider.py` and six new
  unit/fault tests in `tests/tasks/test_ftf_0101.py`. Independent
  re-verification this shift (session-ID clean against all 15 commits
  unique to PR #114 — session_01BaakM658Kz9nuZXKF53kc1,
  session_01Un2BpAFyPcvY7zUpk7ZHbe, session_015u9WhWDyzkFgkk74HzHJ9d — none
  match this reviewing session's own ID): CI reconfirmed green (7/7) on the
  real final head after two mid-review head-moves (a missing
  `docs/certifications/queue.md` PROVISIONAL entry added, then a
  `PROGRESS.md`-only commit); own independent fault injection distinct
  from the producer's own (disabled `refresh()`'s subject/issuer-continuity
  check) — a `pytest -k` run of the affected test misleadingly reported
  PASSED (this file's own soft `check()`/`FAILURES` pattern only becomes a
  real assertion via its `main()`, exactly how CI actually invokes it:
  `python3 tests/tasks/test_ftf_0101.py`, not pytest) — re-run that way,
  the break was caught for real, reverted clean. `product_truth_check.py`
  `--fail-on-unwired` confirmed legitimately `1/1` (`grep` on
  `apps/api/app/main.py` shows `build_oidc_login_router` called
  unconditionally from `create_app()`). `tools/r1_handoff.py packet
  FTF-0101` confirmed acceptance clause 1 (login+refresh+logout, all three
  against a real provider) and the negative_acceptance clause both met; the
  remaining disclosed gaps (revoked-membership fail-closed default,
  in-memory refresh-token/session stores, no RFC 7009 provider-revocation
  on logout, no Next.js app shell, no secret-manager integration) are
  unchanged from the PR #112 review and accurately recorded in
  `docs/spec-matrix.yaml`. Full entry: docs/certifications/ledger.md.
- FTF-0103 — RBAC middleware. CERTIFIED 2026-09-19, PR #118 (merge commit
  `b4b47c0678422dace8e5994af5929586e9301166`) — certified on first
  submission, no resubmission needed. Independent re-verification this
  shift (session-ID clean against all 5 commits on PR #118 —
  session_012jXC2eTsD2Vx47hmtLLiBu — none match this reviewing session's
  own ID): CI reconfirmed green (7/7) on the real final head after one
  doc-only mid-review head-move (`PROGRESS.md`-only commit, `git diff
  --stat` confirmed no code delta); two own independent fault injections
  distinct from the producer's own vectors (flipped the body-tenant
  `!=` to `==`; auto-downgraded an unknown membership to `Role.VIEWER`
  instead of denying) — both caught for real by `tests/tasks/
  test_ftf_0103.py`, reverted clean. `product_truth_check.py`
  `--fail-on-unwired` confirmed legitimately `1/1` (`docs/spec-matrix.yaml`
  FTF-0103's `proving_symbol` is empty — no live-wiring claim, since no
  `/api/v1` route exists anywhere in this repository yet). `tools/
  r1_handoff.py packet FTF-0103` confirmed both acceptance clauses (the
  module's own enforcement mechanism, and independent-review/negative-test
  evidence) and the negative_acceptance clause are met for this task's own
  scope; disclosed gaps (no live `Membership` table, no live `/api/v1`
  caller yet, organization-level-only scoping, `principal_resolver`
  decoupling from FTF-0101) are unchanged from the submission and
  accurately recorded in `docs/spec-matrix.yaml`. Full entry:
  docs/certifications/ledger.md.

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
(no task currently blocked — all three entries below are resolved history,
kept for the record.)
- (FTF-0101's 2026-09-18 new-runtime-dependency ask — no live FastAPI app,
  no Next.js/React runtime, no OIDC/JWT client or session library — was
  resolved 2026-09-19 by live owner confirmation (option 1: FastAPI +
  uvicorn + authlib + itsdangerous, and Next.js + React + react-dom,
  matching the architecture docs' implied default) — see PROGRESS.md's
  matching entry for the full record. FTF-0101 was then implemented and
  submitted as PR #103.)
- (FTF-0101's own narrower local-OIDC-test-provider-package ask — Section
  65.3's real-integration-evidence clause needs a real local OIDC
  provider, and neither the Node `oidc-provider` package nor the Python
  `idpy-oidc` package was installed or approved — was resolved 2026-09-19
  by a live owner instruction: apply a new standing default-resolution
  rule, now recorded in CLAUDE.md "Ask owner first," rather than treat
  every architecture-adjacent choice as a fresh hard stop. Applied that
  rule here: resolved as `idpy-oidc` (Python, dev/test-only dependency),
  matching the already-owner-confirmed `apps/api` Python/FastAPI stack
  (the entry above) rather than adding a new Node dev-tooling dependency
  into a Python-native backend test suite. See CLAUDE.md and PROGRESS.md's
  matching entry for the full record. That follow-up implementation work
  is now done: real integration evidence against a real local `idpyoidc`
  instance was added and resubmitted as PR #112, superseding PR #103, and
  (after a further real-server-side-refresh follow-up, PR #114,
  superseding PR #112) CERTIFIED — see "## Done" above.)
- (FTF-0102's own 2026-09-16 new-runtime-dependency ask was resolved
  2026-09-18 by live owner confirmation (option 1: SQLAlchemy + Alembic +
  a Postgres driver, matching the master architecture table as written) —
  see PROGRESS.md's matching entry for the full record. FTF-0102 has
  since been implemented, submitted and CERTIFIED — see "## Done" above.)

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
