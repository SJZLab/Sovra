# QUEUE — Sovra (Model Factory)
Updated: 2026-09-18 (Control plane, scheduled CERTIFY-mode session
  triggered by PR #96's own `pull_request.opened` webhook) — PR #90's
  structural blocker (recurring `PROGRESS.md`/`QUEUE.md` tracking-doc
  merge conflict) is resolved for real: a relay shift merged FTF-1601's
  actual code into its own designated branch and opened PR #96
  (`claude/wizardly-fermat-vmg0oc` → `main`), which closed PR #90 and
  reached `mergeable_state: "clean"` with CI green (6/6) on its real
  final head (`902139b`), independently reconfirmed here via
  `get_workflow_run` matching `head_sha`, not a stale claim. **Not
  certified anyway:** independent review found a real Law 1 gap — the
  `restoration_validated` boolean's schema/RLS-survival subset terms
  (the code's own mechanism for Section 71.2's "a backup that has never
  been restored does not count as validated") have never been shown able
  to fail by any test in this PR. Proved this concretely against a real
  live Postgres cluster (a real restore missing one RLS policy silently
  reports `restoration_validated: True` once that one term is removed
  from the conjunction; neither existing test suite catches it) — full
  writeup: `docs/certifications/queue.md`, "Control plane review —
  2026-09-18 — NOT CERTIFIED (real Law 1 gap: restoration completeness
  never proven able to fail)". The shipped code itself is correct
  (unpatched, it already returns `False` for this exact real scenario) —
  only a negative test is missing. `certification_state` for FTF-1601 is
  not yet `PROVISIONAL` on `main` (PR #96 is unmerged); nothing to
  downgrade. **Action for the next relay shift:** push one no-DB negative
  test to PR #96's own branch (`claude/wizardly-fermat-vmg0oc`) proving
  `restoration_validated` goes `False` when a `metadata_fn` reports a
  restored state missing a table / `FORCE ROW LEVEL SECURITY` flag /
  RLS policy the original backup had, confirm CI stays green, then
  resubmit for certification — no other rework needed. Recomputed the
  dependency frontier: still only FTF-1601 (this PR, now blocked on the
  above) and FTF-0101 (blocked on a new-runtime-dependency ask); no other
  unblocked, unclaimed work exists. See "## Owner actions — current" and
  "## Blocked" below.

Updated: 2026-09-19 (relay shift) — pushed the exact fix the review above
  asked for: four new no-DB checks in `tests/tasks/test_ftf_1601.py`
  (three independent `restoration_validated is False` negatives, one for
  each subset term — missing table / missing FORCE ROW LEVEL SECURITY
  flag / missing RLS policy — plus one baseline `True` case proving the
  negatives fail for the right reason). No code change. Reproduced the
  reviewer's exact finding three times over (removed each conjunction
  term from `scripts/postgres_backup.py` in turn) — each time exactly the
  matching new check failed, restored byte-identical between injections.
  Full regression clean (`ruff`/`mypy`/`eslint`/`tsc`/`prettier`,
  `pytest tests/unit tests/tasks` 213/213 including the new checks,
  `vitest` 2/2, `validate_spec.py --strict`, `status.py` 18/300 unchanged,
  `product_truth_check.py` 0/0 unchanged, `verify_release.py` 18/300 zero
  FAILED unchanged). Merged current `main` into this branch first (pure
  tracking-doc conflict against the review itself; resolved
  chronologically, no code touched). See "## Active" below and
  `docs/certifications/queue.md`'s matching new entry for the pushed
  commit SHA and CI confirmation.

Relay: 2h · Report: 08:30 & 19:30 Asia/Riyadh · Certifier: Control plane (autonomous)

## Status
Certified: 18/300 (main; FTF-0104 CERTIFIED 2026-09-18, PR #87 merged as
`c682687` — see docs/certifications/ledger.md) · Provisional: 0 (PR #96,
FTF-1601, is open, CI green, but NOT CERTIFIED — real Law 1 test-coverage
gap found, see banner above and "## Active" below) ·Blocked: 1 (FTF-0101 — new-runtime-dependency ask, see "## Blocked" and
"## Owner actions — current")
Product-true (Law 16): 0 — must match certified (FTF-0001, FTF-0002,
FTF-0003, FTF-0004, FTF-0005, FTF-0006, FTF-H001, FTF-R001, FTF-H002,
FTF-H028, FTF-H034, FTF-1810, FTF-R014, FTF-R015, FTF-R017 and FTF-0102
all have no proving_symbol; FTF-0001 is structural layout, FTF-0002 is CI
tooling, FTF-0003 is test-harness tooling, FTF-0004 is local-stack
bootstrap/health tooling, FTF-0005 is a configuration module with no live
caller yet, FTF-0006 is an observability seam with no live FastAPI app or
Temporal workflow to instrument yet, FTF-H001 is the spec/gate verifier
tooling itself, FTF-R001 is an architecture/version-lock schema freeze
with no live product-runtime caller yet, FTF-H002 is a preflight/release-
scope-freeze layer with no live product-runtime caller yet, FTF-H028 is
the release-evidence-gate/CI-aggregator tooling itself, `workflow_dispatch`-
only and never run by the relay, FTF-H034 is the task-claim scheduler/
tracker-sync tooling itself, with no live multi-agent dispatch harness
wiring it into a running caller yet, FTF-1810 is the release-qualification
aggregator tooling itself — run against the real repo it honestly reports
NOT READY (every one of the 43 required scenarios missing evidence,
image/profile bindings BLOCKED_EXTERNAL), `workflow_dispatch`-only and
never run by the relay, FTF-R014 is build/implementation tooling binding
the selected coding executor to Section 84's controls, with no Factory
production service importing it, and FTF-R015 is the safe-parallel-
execution/orchestration tooling itself (workspace manager, executor
adapter, task orchestrator), with no live multi-agent dispatch harness
wiring it into a running caller yet, FTF-R011 is a release-matrix
governance/validator layer with no Factory production service importing
it, FTF-R017 is a storage/OSS-dependency-lifecycle qualification layer
(SigV4 client, component-maintenance inventory, storage-profile
qualification) with no live Factory production service importing
`storage_semantics_client.py` yet, FTF-0102 is the Organization/
Workspace ORM/migration/RLS layer with no live FastAPI app importing
`organization_workspace_schema.py` yet, and FTF-0104 is the audit event
writer with no live FastAPI app importing `audit_event_writer.py` yet —
none a live-wired product-runtime path, so all eighteen are CERTIFIED
without counting toward product-true).

## Owner actions — current
- 2026-09-18 (this shift, relay): FTF-0101 (OIDC login) is the only other
  dependency-ready item (deps FTF-0004/FTF-0005/FTF-0102, all CERTIFIED)
  besides FTF-1601 (already claimed elsewhere this same day — PR #90,
  open, not yet merged). FTF-0101's real DoD (`tools/r1_handoff.py packet
  FTF-0101`) requires `apps/web/components/identity-and-tenancy/
  oidc_login.tsx` and a real OIDC callback/session API enforcing scoped
  roles on `/api/v1` — this repo has *no* live FastAPI app anywhere yet
  (`apps/api/app` has no `main.py`) and *no* Next.js/React runtime in
  `apps/web` (its `package.json` has zero dependencies). Building this
  task's acceptance criteria for real (not a fake/placeholder path, per
  Law 11) requires installing, at minimum: `fastapi` + an ASGI server
  (`uvicorn`) + an OIDC/OAuth2 client and JWT/JWKS-validation library
  (e.g. `authlib`) + a session-signing library (e.g. `itsdangerous`) on
  the Python side, and `next`/`react`/`react-dom` (+ types) on the
  TypeScript side — every one of these is a genuinely new runtime
  dependency, not yet approved for this repo. The prior 2026-09-16
  FTF-0102 new-runtime-dependency approval (see "## Blocked" below) was
  confirmed scoped only to SQLAlchemy/Alembic/a Postgres driver for that
  task's ORM/migration work — it does not cover FastAPI, an OIDC client
  library, a session library, or any JS frontend framework, none of
  which FTF-0102 needed or installed (it still has no live FastAPI app
  either). Also relevant: FTF-0101's own acceptance clause expects a real
  local OIDC provider for integration evidence (Section 65.3 allows "a
  real local OIDC provider" for read-only local setup, never a
  bypass/test-token login) — that is itself another new dependency
  decision (e.g. the Node `oidc-provider` package, or Python `idpy-oidc`)
  this relay should not pick unilaterally either. Filed as a dated ask
  below rather than improvised or silently substituted, per CLAUDE.md
  "Relay is execution-only" and the "new runtime deps" ask-first hard
  stop. Not claimed. No other dependency-ready, unclaimed task exists
  this shift (recomputed the full 300-task frontier against all 18
  CERTIFIED entries directly via `scripts.task_claim.load_tasks()` /
  `load_certification_states()` — FTF-0101 and FTF-1601 are the only two
  ready items, and FTF-1601 is already in flight). Per STEP 4(e),
  stopping this shift here.
## Active (relay works this)
FTF-1601 (Postgres backup, deps FTF-0004/FTF-0102, both CERTIFIED) — open
PR https://github.com/SJZLab/Sovra/pull/96 (supersedes closed PR #90),
head `902139b` on branch `claude/wizardly-fermat-vmg0oc`.

History: PR #90's recurring `PROGRESS.md`/`QUEUE.md` tracking-doc merge
conflict against a moving `main` stalled for three consecutive relay
shifts (each correctly declined to push a fix to a branch it wasn't
assigned). A later shift broke the stall by merging FTF-1601's actual
delivered code from `claude/wizardly-fermat-e9tyvg` into its own
designated branch (`claude/wizardly-fermat-vmg0oc`) instead, resolved the
tracking-doc conflicts there, and opened PR #96 to supersede #90 (closed
with an explanatory comment).

**This shift (Control plane, scheduled CERTIFY-mode, triggered by PR
#96's own `pull_request.opened` webhook):** confirmed CI green (6/6) on
the real final head `902139b` via `get_workflow_run` matching `head_sha`
directly (the PR advanced through two more tracking-doc-only commits
mid-review; re-checked each time rather than trusting a stale claim), and
`mergeable_state: "clean"` — the structural blocker is genuinely resolved.
**Did not certify anyway:** independent fault injection against a real
live Postgres cluster found that `restore_backup()`'s
`restoration_validated` result — the code's own mechanism for Section
71.2's "a backup that has never been restored does not count as
validated" — has never been shown able to fail by any test in this PR
(Law 1). Proved it concretely: took a real encrypted backup, restored it
into a scratch database, manually dropped one RLS policy from that
restored copy (a real, physically incomplete restore), and confirmed the
shipped code correctly reports `restoration_validated: False` for it —
but removing just that one term from the boolean conjunction makes the
identical real scenario silently report `True`, and neither
`tests/tasks/test_ftf_1601.py` nor `tests/integration/test_postgres_backup.py`
catches the difference. Full writeup: `docs/certifications/queue.md`,
"Control plane review — 2026-09-18 — NOT CERTIFIED (real Law 1 gap:
restoration completeness never proven able to fail)".

**Fix needed (relay, same PR #96 branch, no code change expected):** add
a no-DB test — the existing `dump_fn`/`metadata_fn`-injection pattern
already used elsewhere in `tests/tasks/test_ftf_1601.py` is sufficient —
that calls `restore_backup()` with a `metadata_fn` reporting a restored
state missing a table, a `FORCE ROW LEVEL SECURITY` flag, or an RLS
policy the original backup recorded, and asserts
`restoration_validated is False` for each of the three subset terms
independently. Push to `claude/wizardly-fermat-vmg0oc`, confirm CI stays
green, resubmit for certification.

**This shift (relay):** added exactly that fix, no code change. Four new
`tests/tasks/test_ftf_1601.py` checks: three independent no-DB negative
cases (a `metadata_fn` reporting the restored state missing a table / a
FORCE ROW LEVEL SECURITY flag / an RLS policy the original backup
recorded, each asserting `restoration_validated is False`) plus one
baseline positive case (a genuinely complete restore still reports
`True`, proving the three negatives fail for the right reason and not
because the scaffold can never report `True`). `restore_backup()`'s own
row-count step still queries a live DB directly for any table both the
original and restored metadata agree exists, unrelated to
`restoration_validated` itself — worked around the same way
`test_create_backup_self_verification_catches_a_broken_encrypt_path`
already does, by monkeypatching `_psql_rows` for the scaffold's own scope
only. Reproduced the reviewer's exact finding three times over, once per
subset term (removed the `tables` / `rls_forced_tables` / `rls_policies`
conjunction term from `scripts/postgres_backup.py` in turn) — each time
exactly the matching new check failed and no other check did, restored
byte-identical from a pre-edit copy (`diff` confirmed empty) after each,
re-ran clean between injections. Merged current `main` into this branch
first (pure tracking-doc conflict against the review itself landing on
`main`; resolved chronologically, no code touched — see this file's
"Updated" banner). Full regression on the resulting tree: `ruff check .`/
`ruff format --check .`/`mypy .` clean (63 files); `pnpm lint`/
`typecheck`/`format:check` clean; `uv run pytest tests/unit tests/tasks` —
213 passed (was 209; +4 new checks, all in `test_ftf_1601.py`, now 18);
`pnpm exec vitest run` — 2 passed; `validate_spec.py --strict` clean;
`status.py` — `18/300 certified · 0 product-true` (unchanged);
`product_truth_check.py --fail-on-unwired` — `0/0` (unchanged);
`verify_release.py` — `18/300`, zero `FAILED`, `NOT READY` (unchanged).
Pushed to `claude/wizardly-fermat-vmg0oc`; CI to be reconfirmed on the new
head before resubmitting for certification — see this file's "Updated"
banner and `docs/certifications/queue.md`'s matching new entry for the
real commit SHA and CI run.
## Provisional (awaiting Control plane L2 certification)
FTF-1601 (Postgres backup, deps FTF-0004/FTF-0102) — third
structural-blocker recurrence resolved via a new PR rather than a fourth
stall. History: claimed by a relay shift 2026-09-18 via PR #90
(`claude/wizardly-fermat-e9tyvg`); first structural blocker (PR #91's
merge into `main` conflicting on tracking docs) reviewed NOT CERTIFIED by
Control plane, fixed by that branch merging `main` back in (`8f8484a`, CI
green, run `35371593078`); blocker recurred a second time (`main` moved
again via `71914f2`/`02b1ed2`/`d2780c1`/`4f6c031`) and was left unfixed for
one shift under the branch-permission constraint (this session's
designated branch differs from PR #90's own branch, and CLAUDE.md forbids
pushing to a branch this session wasn't assigned without explicit
permission — no live user exists in a scheduled shift to grant it).

**This shift:** rather than record the same finding a third time, merged
FTF-1601's actual delivered code from `claude/wizardly-fermat-e9tyvg`
(a read of that branch, never a push to it) into this session's own
designated branch `claude/wizardly-fermat-vmg0oc`, resolved the
tracking-doc conflicts there, ran the full regression suite (clean — see
PROGRESS.md's matching entry), pushed, and opened
https://github.com/SJZLab/Sovra/pull/96 (`claude/wizardly-fermat-vmg0oc`
→ `main`) to supersede #90. CI reconfirmed green on the real final head
`2a98c9d` (all 6 jobs, run `35403943528`; also green on the intermediate
head `20aa4e5`, run `35403729104`), `mergeable_state: "clean"`.
PR #90 closed with an explanatory comment linking to #96, avoiding two
open PRs for the same task. Not re-claiming FTF-1601 as a new task — this
is a continuation of the same item, just retargeted to a branch this
session can actually push to, and it is now ready for a fresh,
independent Control-plane L2 review — see `docs/certifications/queue.md`'s
matching continuation entry. `proof_state=L1-PROVEN`,
`certification_state=PROVISIONAL`.

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
- FTF-0101 — OIDC login. Blocked 2026-09-18 on a genuine new-runtime-
  dependency ask-first item: this repo has no live FastAPI app and no
  Next.js/React runtime anywhere yet, and this task's real acceptance
  criteria cannot be met without both, plus an OIDC/JWT client library,
  a session library, and (for real integration evidence) a local OIDC
  test provider. See "## Owner actions — current" (dated 2026-09-18) for
  the concrete options recorded for the owner. Distinct from, and not
  covered by, the FTF-0102 approval below (that one was scoped only to
  SQLAlchemy/Alembic/a Postgres driver).
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
