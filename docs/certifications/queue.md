# Certification Queue
Relay appends PROVISIONAL items here for the Control plane to certify.

## 2026-09-15 — FTF-0001 — Initialize monorepo
- **PR:** https://github.com/SJZLab/Sovra/pull/3 (relay/ftf-0001-init-monorepo → main)
- **Commit:** 00bacb5c992938e47ad4b294c77bf3e48c5c203e
- **Test:** tests/test_ftf_0001_monorepo_layout.py — 26 checks
- **Evidence:** run against pre-scaffold tree → 26/26 FAIL (root config +
  all seven ARCHITECTURE.md §2 component dirs missing); run again after this
  commit's files only → 0 failures, exit 0. product_truth_check.py run
  clean (0/0 — no proving_symbol claimed; this criterion is structural
  layout, not a live-wired code path).
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.
- **CI (real, not local-only):** PR head merged with `main` at commit
  `2f63b7f` (brings in the `.github/workflows` YAML-quoting fix from #4 —
  see below); GitHub Actions run
  https://github.com/SJZLab/Sovra/actions/runs/34995170054, job `gate`,
  conclusion `success`, all 7 real steps ran (not the earlier 0-job parse
  failures). Actual log lines: `Product-truth: 0/0 product-true` and
  `Status: 0/300 certified · 0 product-true`, matching the local run
  above exactly. Note: the `Typecheck + lint + unit` step is still the
  FTF-0002/0003 placeholder (`echo "TODO: ..."`) — it succeeds trivially
  because it doesn't check anything yet, not because lint/type/tests pass.
- **Unrelated fix landed first:** all three `.github/workflows/*.yml` files
  on `main` had a YAML syntax bug (unquoted `run: echo "TODO: ..."` with
  an embedded `: `) that made every workflow fail to parse — 0 jobs, not a
  code failure. Fixed separately in PR #4 (merged as `6cf3d27`), verified
  with a real green `pull_request`-triggered run before merging, then
  brought into this branch via a merge commit so PR #3's own CI could run
  for real. Full diagnosis and fix are in PR #4, not repeated here.
- **Note for Control plane:** once this merges, scripts/product_truth_check.py
  SOURCE_ROOTS should be filled from this real layout (apps/web, apps/api,
  workers/{training,evaluation,data,deploy}, packages/shared) — currently
  left commented per the script's own instruction to fill it "the moment
  FTF-0001 merges," not before.
- **Note for Control plane (owner-ask review):** this session had working
  GitHub MCP write access (branch/commit/push/PR) end-to-end — QUEUE.md's
  "Owner actions — current" setup-gap item #1 (no GitHub connector to push
  the scaffold) may be stale. Not marking it resolved myself — flagging for
  Control plane/owner to verify and update QUEUE.md, per the prior watchdog
  finding on unverified "gap resolved" claims. Setup gap #2 (2h relay /
  watchdog trigger tooling) is unaffected — this session has no
  create_trigger/fire_trigger tooling.

## 2026-09-16 — FTF-0002 — Add formatting/lint/type checks
- **PR:** (see QUEUE.md — opened against `main` from `claude/blissful-goodall-wzvd2d`)
- **Commit:** e3f0c1a981db04bd5d16078f5fd72fcebfc866c7
- **Real DoD source:** `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolved via
  `python tools/r1_handoff.py packet FTF-0002` (see
  docs/architecture/CRITERION_MAPPING.md "Known gap — RESOLVED" and this
  session's earlier PRs #15/#22).
- **Delivered:** real Python formatter/linter/type-checker (ruff + mypy,
  installed via `uv sync`, config in root `pyproject.toml`) and real
  TypeScript formatter/linter/type-checker (prettier + eslint + tsc,
  installed via `pnpm install`, config in `eslint.config.mjs` /
  `.prettierrc.json` / `tsconfig*.json`), wired into
  `.github/workflows/ci.yml`.
- **Test:** tests/tasks/test_ftf_0002.py — 6 checks (ruff check, ruff
  format, mypy, eslint, tsc each proven against a deliberately broken
  fixture + its clean equivalent, plus a repo-wide "commands CI actually
  runs" check).
- **Evidence — shown able to fail (Law 1):**
  1. Fixture-level: each of ruff/mypy/eslint/tsc run against an in-repo
     temp fixture with a real injected defect (unused import, `int =
     "str"`, unused var, `number = "str"`) — nonzero exit + expected
     diagnostic code asserted (F401, `assignment`, `no-unused-vars`,
     `TS2322`), then re-run clean — exit 0. Fixtures are written inside
     the repo tree, not system `/tmp` — confirmed empirically that
     ESLint's flat-config base path silently ignores files under `/tmp`,
     which would have made the eslint check pass for the wrong reason.
  2. Repo-wide: appended `import os` to the real, tracked
     `scripts/status.py`, ran the unmodified `ruff check .` — real
     failure (`E402` + `F401` at `scripts/status.py:68`). Reverted,
     re-ran — clean. Both outputs observed directly, not simulated.
- **Pre-existing defect fixed to unblock this task:** `uv sync` failed
  outright — root and all 5 workspace-member `pyproject.toml` files
  declared a hatchling wheel build with no source directory to build,
  meaning FTF-0001's "fresh clone install succeeds" acceptance was
  apparently never verified for the Python side. Added `[tool.uv]
  package = false` to mark these workspace-only until real source lands.
- **Byte-fidelity preserved:** `tools/r1_handoff.py` is excluded from
  both ruff (lint + format, via top-level `extend-exclude`) and mypy —
  confirmed via `git diff` showing zero delta, after an intermediate
  `ruff format .` pass briefly reformatted it and was reverted before
  committing. It stays byte-for-byte identical to the master spec's own
  Section 86.
- **Scope discipline:** `format:check` targets explicit TS/JS/config
  globs, not `prettier --check .` — the latter matched 178 files
  including `CLAUDE.md` and every doc/spec file in the repo, which is
  not this task's scope. `apps/web/src/index.ts` and
  `packages/shared/src/index.ts` are the minimal real entry files
  `tsc --build` needs to have inputs (trivial name-constant exports, not
  feature code) — FTF-0001 left these dirs as README+manifest only.
- **CI:** YAML-validated (`yaml.safe_load`); real run status to be
  confirmed on the opened PR before merge.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.

## 2026-09-16 — FTF-0003 — Add test harnesses
- **PR:** (see QUEUE.md — opened against `main` from `claude/blissful-goodall-ugtklt`)
- **Commit:** d4482de2cbcdda17000f059886c47b894b680830
- **Real DoD source:** `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolved via
  `python tools/r1_handoff.py packet FTF-0003 --plan FINE_TUNING_FACTORY_RELEASE_1.md`.
- **Delivered:** real pytest unit-test harness (Python, scoped to
  `tests/unit`) and real Vitest unit-test harness (TypeScript, scoped to
  `apps/web/src` and `packages/shared/src`), each collecting genuine
  assertions and publishing machine-readable JUnit XML counts; a gate
  script (`scripts/check_test_harness.py`) enforcing the negative_acceptance
  clause ("Zero collected tests, focused .only or skipped required test
  must fail validation"), wired into `.github/workflows/ci.yml` in place of
  the FTF-0003 TODO placeholder.
- **Test:** `tests/tasks/test_ftf_0003.py` — 4 fixture-based checks plus a
  repo-wide clean-run check.
- **Evidence — shown able to fail (Law 1):**
  1. Empirically verified (not assumed) pytest's and Vitest's own behavior
     first: an empty pytest target exits 5 with `tests="0"` in its JUnit
     XML; an empty Vitest root exits 1 the same way; a `pytest.mark.skip`
     fixture and a Vitest `.only` fixture both exit 0 on their own (neither
     runner treats a skip/focus as a failure by itself) but both report the
     skipped/deselected test's count in JUnit's `skipped` attribute —
     confirmed by reading the actual generated XML for each case in a
     scratch directory before writing the gate script.
  2. `tests/tasks/test_ftf_0003.py` reproduces all four cases as real
     subprocess calls against in-repo fixtures (not `/tmp`, same rationale
     as FTF-0002): zero-collected pytest, skipped pytest test, zero-collected
     Vitest, focused-`.only` Vitest — each shown to make
     `scripts/check_test_harness.py` exit nonzero with the right message,
     each with a clean equivalent shown to exit 0.
  3. Repo-wide: the real `pytest tests/unit`, `vitest run`, and
     `check_test_harness.py` commands CI invokes are re-run against the
     actual delivered tests, confirmed clean (3 pytest tests, 2 Vitest
     tests, 0 skipped either side).
- **Bugs caught before reaching CI (not assumed away):**
  1. `pnpm run test -- --reporter=junit --outputFile=...` silently
     mis-forwards a stray `--` through pnpm's script wrapper and never
     writes the JUnit file at all — caught by actually running it and
     checking for the output file, not by trusting the command would work.
     Fixed by having CI call `pnpm exec vitest run ...` directly.
  2. `mypy .` failed for real ("Source file found twice under different
     module names: `product_truth_check` and `scripts.product_truth_check`")
     once `tests/unit/test_product_truth_check_helpers.py` imported
     `scripts.product_truth_check` — fixed with `scripts/__init__.py`,
     re-ran `mypy .` clean afterward.
- **Certified evidence left untouched:** `tests/test_ftf_0001_monorepo_layout.py`
  and `tests/tasks/test_ftf_0002.py` (both already independently
  L2-certified) are deliberately excluded from pytest's `testpaths`
  (scoped to `tests/unit` only) rather than modified to fit pytest's
  assertion model — avoids any Law 15 requalification question for their
  existing certified evidence. Both were re-run directly after this
  change and still pass exactly as before.
- **Negative_acceptance — "remove template sample tests":** no scaffolding
  boilerplate test files were introduced; `pnpm add -Dw vitest` added no
  default sample test, and the two new `.test.ts` files each test a real
  (if currently trivial) exported constant, not a template placeholder.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.

## 2026-09-16 — FTF-0005 — Configuration system
- **PR:** (see QUEUE.md — opened against `main` from `relay/ftf-0005-configuration-system`)
- **Commit:** b782a2c (implementation)
- **Real DoD source:** `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolved via
  `python tools/r1_handoff.py packet FTF-0005 --plan FINE_TUNING_FACTORY_RELEASE_1.md`.
- **Delivered:** a stdlib-only, fail-fast typed settings loader
  (`apps/api/app/config/configuration_system.py`, `Settings.from_env()`).
  No third-party runtime dependency was added — CLAUDE.md's "new runtime
  deps" item is an ask-first hard stop, so validation uses only
  `dataclasses`/`enum`/`decimal`/`urllib.parse`/`re`:
  - `database_url` / `litellm_gateway_url`: parsed and scheme/host-checked
    (`postgresql[+driver]://` for the DB; `https://` for the LiteLLM
    gateway in production, `http://` permitted only outside production).
  - `environment` / `privacy_policy`: real `Environment`/`PrivacyPolicy`
    enums, rejecting any value outside their members.
  - `max_training_budget_usd`: a positive `Decimal`, rejecting
    zero/negative/non-numeric input.
  - `litellm_api_key_secret_ref` / `database_password_secret_ref`: must be
    a secret-reference URI (`vault://`, `openbao://`, `secret://`),
    rejecting a raw inline credential pasted in by mistake.
- **Test:** `tests/tasks/test_ftf_0005.py` — 9 check functions covering
  both the acceptance and negative_acceptance criteria, run against an
  isolated env dict (never this process's real environment variables).
- **Evidence — shown able to fail (Law 1):**
  1. Every acceptance case (missing var, malformed/wrong-scheme URL,
     unknown enum value, non-positive/non-numeric budget, inline
     non-reference secret) is asserted to raise `ConfigurationError`, with
     a parallel clean case asserted to construct `Settings` successfully.
  2. Real fault injection against the tracked module (not just fixtures):
     temporarily removed the production/`tls_verify=false` rejection from
     `_validate_tls_verify` in `apps/api/app/config/configuration_system.py`
     and re-ran `tests/tasks/test_ftf_0005.py` — 2 checks failed for real
     (`SOVRA_TLS_VERIFY=false in production raises`,
     `the production/tls_verify rejection names the actual rule`);
     reverted via a byte-identical restore from a pre-edit copy, confirmed
     `diff` empty, re-ran clean (all 9 checks pass again).
- **Negative_acceptance — proven, not assumed:**
  - No development fallback credential: omitting either secret-ref env var
    raises `ConfigurationError`; there is no default value anywhere in the
    module for a credential-shaped field.
  - No production `tls_verify=false`: accepted outside production, raises
    when `SOVRA_ENVIRONMENT=production` — proven by the fault injection
    above.
  - Sensitive values absent from errors: a database URL's embedded
    password, a URL password in a rejected gateway URL, and inline
    secret-ref values never appear in a raised message or in `Settings`'
    own `__repr__` (each redacted to scheme/host or a length-only
    fingerprint) — asserted directly in
    `test_sensitive_values_absent_from_errors_and_repr` and
    `test_invalid_secret_ref_rejected`.
- **CI:** wired `python3 tests/tasks/test_ftf_0005.py` into
  `.github/workflows/ci.yml` alongside the existing FTF-0002/0003 evidence
  steps; YAML-validated (`yaml.safe_load`). Real run status to be confirmed
  on the opened PR before merge.
- **Regression check:** `ruff check .` / `ruff format --check .` / `mypy .`
  clean; `pytest tests/unit` (3 passed, unchanged);
  `tests/tasks/test_ftf_0002.py`, `tests/tasks/test_ftf_0003.py`, and
  `tests/test_ftf_0001_monorepo_layout.py` all still pass unmodified (ran
  `pnpm install` first — this fresh container had no `node_modules`,
  unrelated to this change). `product_truth_check.py --fail-on-unwired`
  stays `0/0 product-true`, legitimate since no live caller wires this
  module into a running service yet (no `apps/api/app/main.py` exists —
  same no-proving-symbol rationale as FTF-0002/0003).
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.

### Control plane review — 2026-09-16 — NOT CERTIFIED (defect found)
- **PR:** https://github.com/SJZLab/Sovra/pull/28 (`relay/ftf-0005-configuration-system`
  → `main`), head `ea80d36dfb9ba25933825df7c914e090b63ea534`.
- **Session-ID check:** both PR commits (`b782a2c`, `ea80d36`) carry
  `Claude-Session: .../session_012mAtoqKYzA7PiXbkGkD7rY`; this reviewing
  session's ID does not match — independent, not self-certification.
- **CI verified for real:** GitHub Actions run
  https://github.com/SJZLab/Sovra/actions/runs/35085894198, job `gate`,
  tied to the actual head SHA `ea80d36` (confirmed via `actions_get`, not a
  stale/older commit), conclusion `success`.
- **Own re-run:** `python3 tests/tasks/test_ftf_0005.py` — all 9 checks
  pass, matching the PR's claim. `product_truth_check.py --fail-on-unwired`
  — `0/0 product-true`, legitimate (proving_symbol is `''` in
  spec-matrix.yaml, no live caller yet).
- **Defect — real, reproduced independently, not in the producer's own
  fault-injection set:** `Settings.__repr__`
  (`apps/api/app/config/configuration_system.py:204-216`) redacts
  `database_url` via `_redact_url(...)` (line 209) but does **not** redact
  `litellm_gateway_url` — line 210 interpolates the raw field
  (`f"litellm_gateway_url={self.litellm_gateway_url!r}, "`) with no
  redaction call at all. `_validate_url` (line 88) never rejects userinfo
  in a URL, so a *valid, accepted* gateway URL carrying embedded
  credentials (e.g. `https://svc:hunter2@litellm.internal.example`, which
  passes every acceptance check for any environment including production)
  has its password echoed verbatim by `repr(settings)`. Reproduced live
  this session:
  ```
  >>> repr(Settings.from_env({..., "SOVRA_LITELLM_GATEWAY_URL":
        "https://svc:hunter2@litellm.internal.example", ...}))
  "Settings(..., litellm_gateway_url='https://svc:hunter2@litellm.internal.example', ...)"
  >>> "hunter2" in repr(settings)
  True
  ```
  This directly contradicts this same queue entry's own negative_acceptance
  claim above ("a URL password in a rejected gateway URL... never appear...
  in Settings' own `__repr__`") — the claim only exercised a *rejected*
  gateway URL (which never reaches a constructed `Settings`, so testing its
  repr proves nothing), never an *accepted* one, which is the actual risk
  surface and is unredacted. `tests/tasks/test_ftf_0005.py`'s
  `test_sensitive_values_absent_from_errors_and_repr` (lines 186-211) has
  no case for an accepted gateway URL with embedded credentials against
  `repr(settings)` — this is a real gap in the delivered evidence, not a
  flaky or environmental failure.
  This module's own docstring (lines 7-10) states "no message this module
  raises embeds a raw secret or a URL's embedded password," and its
  `__repr__` comment (line 205) states "Secret-shaped fields and URL
  userinfo never appear in logs/tracebacks" — the shipped code does not
  meet its own stated contract for this field, and this is exactly the
  secret-redaction property CLAUDE.md and
  `docs/implementation/task-packets/FTF-0005.md`'s security line call for.
- **Conclusion — per CERTIFY step 3 ("confirm the test genuinely fails when
  the feature is broken"):** the feature (secret redaction in `__repr__`)
  is itself broken for one of its two URL fields, and the delivered test
  suite does not catch it. Not merging. `certification_state` stays
  `PROVISIONAL` (not downgraded further — the acceptance criteria and the
  other eight checks are sound; this is a scoped fix, not a redo).
  **Fix needed:** redact `litellm_gateway_url` in `__repr__` the same way
  `database_url` already is (`_redact_url(self.litellm_gateway_url)`), and
  add a `test_sensitive_values_absent_from_errors_and_repr` case that
  constructs a valid `Settings` with an *accepted* gateway URL carrying
  embedded userinfo and asserts the password is absent from
  `repr(settings)` — mirroring the existing database_url case so this
  exact regression cannot reappear silently. Re-submit for certification
  once fixed; no other part of this PR needs rework.
- **Validated-by:** session https://claude.ai/code/session_01WjQKEb87DwDx51YLNRxT6o
  (independent — see session-ID check above), 2026-09-16.

## 2026-09-16 — FTF-0005 — Fix for repr secret-redaction defect (re-submitted)
- **Commit:** 9f2dcbc, on branch `relay/ftf-0005-fix-repr-redaction`
  (produced by `session_012mAtoqKYzA7PiXbkGkD7rY` — the same producer
  session as the original PR #28; this is the fix, not a certification).
- **Fixes exactly what the review above found:** `_redact_url()` is now
  applied to `litellm_gateway_url` in `Settings.__repr__`
  (`apps/api/app/config/configuration_system.py`), matching how
  `database_url` was already handled. No other line changed.
- **Test:** `tests/tasks/test_ftf_0005.py`'s
  `test_sensitive_values_absent_from_errors_and_repr` gained a case that
  constructs a valid `Settings` with an *accepted* gateway URL carrying
  embedded userinfo (`https://svc:gw-s3cr3t@litellm.internal.example`)
  and asserts the password is absent from `repr(settings)`.
- **Evidence — shown able to fail (Law 1), reproducing the certifier's
  exact finding:** reverted the fix locally (raw
  `f"litellm_gateway_url={self.litellm_gateway_url!r}, "` restored),
  re-ran `tests/tasks/test_ftf_0005.py` — the new check failed for real
  (`an accepted gateway URL's embedded password never appears in
  Settings' repr`), the other 9 checks stayed green; restored the fix
  from a pre-edit copy, `diff` confirmed empty, re-ran clean (10/10
  pass).
- **Regression check:** `ruff check .` / `ruff format --check .` /
  `mypy .` clean; `pytest tests/unit` (3 passed, unchanged). No other
  part of FTF-0005 was touched, per the review's own scoping.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL
  (unchanged). Not self-certified — this is the same producer session as
  PR #28, so it cannot certify its own fix; awaiting a fresh Control-plane
  session with no session-ID overlap with
  `session_012mAtoqKYzA7PiXbkGkD7rY` per Law 4.

## 2026-09-16 — FTF-0004 — Docker local stack
- **Commit:** bc603a6 (branch `claude/wizardly-fermat-3mh1pd`)
- **Real DoD source:** `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolved via
  `python tools/r1_handoff.py packet FTF-0004 --plan FINE_TUNING_FACTORY_RELEASE_1.md`.
- **Environment finding before implementation:** this session's interactive
  dev sandbox has a working Docker daemon (`dockerd` starts cleanly, root
  access confirmed) but **no container-registry egress** —
  `docker pull hello-world` failed with a 403 from the session's own
  enforced proxy fetching `production.cloudfront.docker.com`
  (`/root/.ccr/README.md`: "the destination host is not allowed by your
  organization's egress policy for this session... do not retry or route
  around it"). Docker Hub's *metadata* API (`hub.docker.com/v2/...`) is
  reachable, so exact image digests were verified to exist without
  needing a blob pull. Per this task's own `resource_gate`
  ("unavailable required evidence is BLOCKED_EXTERNAL, never skipped")
  this session's own sandbox cannot produce the real container-boot
  evidence directly — the live user directed routing it through
  `.github/workflows/ci.yml` on GitHub Actions' hosted runner instead
  (real registry egress there), which is exactly the documented route
  this session's own operating instructions name for external-resource
  constraints, not a workaround.
- **Delivered:**
  - `infra/docker/docker-compose.yml`: `postgres` (`postgres:16-alpine`,
    digest-pinned), `temporal` (`temporalio/auto-setup:1.24.2`,
    digest-pinned, backed by the same Postgres via
    `POSTGRES_SEEDS=postgres`), `s3` (`chrislusf/seaweedfs:3.71`,
    digest-pinned — the reuse-architecture's qualified S3-compatible
    fixture per Section 77.3, satisfying this task's own acceptance
    clause against "an automatic new archived-MinIO install"), and `api`
    (a stdlib-only Python health-bootstrap process, digest-pinned base
    image). One documented boot command:
    `docker compose -f infra/docker/docker-compose.yml up -d`.
  - `apps/api/app/config/docker_local_stack.py`: `StackConfig`/
    `check_tcp_reachable`/`check_stack_readiness`/`is_ready` and a
    `GET /health` stdlib `http.server` endpoint (200 when every
    dependency's TCP port is reachable, 503 otherwise). No third-party
    runtime dependency added — FastAPI/SQLAlchemy/Temporal-SDK/boto3
    remain the frozen target stack for actual request handlers
    (Section 4); adding any of them now solely to host a health check
    would itself be a new-runtime-dependency ask-first item under
    CLAUDE.md (same reasoning FTF-0005 used to avoid pydantic).
  - **`web` deliberately excluded from this stack** — `apps/web` has no
    server-side runtime at all yet (no Next.js/React installed); adding
    either now would itself be a new-runtime-dep ask-first item. Documented
    in the compose file's own header comment as a follow-up for whichever
    task first builds the real web app, not silently invented here or
    faked with a placeholder.
  - `scripts/verify_docker_local_stack.py`: boots the stack for real, polls
    `/health` for genuine readiness, writes a marker row to Postgres, stops
    the `postgres` container, asserts `/health` flips to 503/unhealthy
    (both the aggregate `ready` flag and the `postgres` entry specifically),
    restarts it, asserts recovery, then asserts the marker row survived —
    proving the negative_acceptance clause ("test restart/persistent state
    instead of canned readiness") against the real containers, not a
    fixture.
  - `.github/workflows/ci.yml`: new `docker-local-stack-integration` job
    (separate from `gate`, `ubuntu-latest`) running
    `python3 scripts/verify_docker_local_stack.py` — this is where the
    real container-boot + negative-acceptance evidence is produced, since
    GitHub Actions' hosted runner has real registry egress. Also added
    `tests/tasks/test_ftf_0004.py` as a step in the existing `gate` job.
- **Test:** `tests/tasks/test_ftf_0004.py` — 9 checks, runnable in this
  sandbox with no Docker/network dependency: uses real local TCP listeners
  (bind/close) to prove `check_tcp_reachable`/`check_stack_readiness`/
  `is_ready` and the `/health` HTTP handler are genuine, non-canned checks.
- **Evidence — shown able to fail (Law 1):** real fault injection against
  the tracked module — temporarily replaced `check_tcp_reachable` with a
  hardcoded `ReadinessResult(..., True, "canned healthy...")`, re-ran
  `tests/tasks/test_ftf_0004.py`: 4 of 9 checks failed for real (the
  unreachable-port case, the live open/close flip, the mixed-stack
  not-ready case, and the detail-message assertion — the one check that
  happened to still pass, "healthy while open", is a true positive the
  canned stub also satisfies by coincidence, expected and consistent).
  Restored the file byte-identical from a pre-edit copy (`diff` confirmed
  empty), re-ran clean (9/9 pass).
- **Regression check:** `uv run ruff check .` / `uv run ruff format
  --check .` / `uv run mypy .` clean; `uv run pytest tests/unit` (3
  passed); `tests/tasks/test_ftf_0002.py`, `test_ftf_0003.py`,
  `test_ftf_0005.py`, and `tests/test_ftf_0001_monorepo_layout.py` all
  still pass unmodified — no regression to certified evidence.
  `pnpm run lint`/`typecheck`/`format:check` and `pnpm exec vitest run`
  (2 passed) clean — no TypeScript files touched by this change.
  `product_truth_check.py --fail-on-unwired` stays `0/0 product-true`
  (no proving_symbol yet — no live caller wires this into a running
  service, same rationale as FTF-0002/0003/0005). `docker compose -f
  infra/docker/docker-compose.yml config` parses cleanly (validated
  locally without pulling any image).
- **CI:** YAML-validated (`yaml.safe_load`); real run status (both the
  `gate` job's new test step and the new `docker-local-stack-integration`
  job actually booting the stack) to be confirmed on the opened PR before
  merge — this is where FTF-0004's real container-integration and
  negative-acceptance evidence will actually be produced.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4, which
  should specifically confirm the `docker-local-stack-integration` CI job
  passed for real (not merely that it exists) before certifying.

### Control plane review — 2026-09-16 — NOT CERTIFIED (not in a certifiable state)
- **PR:** https://github.com/SJZLab/Sovra/pull/35 (`claude/wizardly-fermat-3mh1pd`
  → `main`), head `330be28db5e60ab2999c47f6d91a60fc03044ee4`. Item: FTF-0004
  (Docker local stack), submitted as PROVISIONAL/L1-PROVEN.
- **Session-ID check:** all 5 commits on PR #35 (`5fefe0a`, `f2c8a2c`,
  `3aaab6f`, `bc603a6`, `330be28`) carry
  `Claude-Session: .../session_01NV9ZvW9ptNnx8RzFWUoZQi`; this reviewing
  session's ID does not match — independent, not self-certification. (Not
  that it matters here — this rejection is on structural grounds below,
  not a content defect.)
- **Blocker 1 — not mergeable:** `pull_request_read` reports
  `mergeable_state: "dirty"` (a real, verified GitHub-computed conflict,
  not a guess). Reproduced independently via `git merge-tree` against the
  actual current `main` (`4ea6784`): genuine conflict markers in both
  `PROGRESS.md` and `QUEUE.md` — this branch's tracking commit
  (`330be28`, "Rebased QUEUE.md/PROGRESS.md onto main's current state...")
  claims to be rebased onto main's post-FTF-0005-certification state, but
  its merge-base with the actual current `main` is `9de083b`, an older
  point in history predating both PR #30 (FTF-0005 fix merge, `5f5a34d`)
  and PR #32 (FTF-0005 certification, `4d34e9a`) — the claimed rebase did
  not happen against real, current `main`, only against a stale local
  copy. There is therefore no clean merge commit of this work onto `main`
  to certify against.
- **Blocker 2 — CI never ran:** `pull_request_read` method `get_check_runs`
  on PR #35 returns `total_count: 0` — zero check runs recorded, not a
  failure and not a stale/self-reported claim of success. The PR's own
  body flags the `docker-local-stack-integration` job as the one that
  "actually" produces this task's real container-boot and
  negative-acceptance evidence (this sandbox has no registry egress) and
  explicitly asks Control plane to "confirm this job passed for real
  before certifying" — it has not run at all, so per CERTIFY step 3
  ("re-check CI actually ran and passed on the real merged commit — not a
  stale or self-reported claim") there is nothing to re-check yet, for
  either the `gate` job or the new integration job.
- **Conclusion:** both blockers are structural, precede any evaluation of
  the delivered code's correctness, and are independently reproducible
  (GitHub's own `mergeable_state` field, GitHub's own check-runs count,
  and a local `git merge-tree` against the real current `main`) rather
  than inferred. Not evaluating the diff's substance further — a
  content-level review would be premature while the PR can't merge
  cleanly and has no CI evidence at all. Not merging, not certifying.
  Note this repo's own `docs/certifications/queue.md` and `spec-matrix.yaml`
  on `main` do not yet carry an FTF-0004 PROVISIONAL entry at all — the
  "2026-09-16 — FTF-0004 — Docker local stack" evidence writeup and
  `proof_state=L1-PROVEN` / `certification_state=PROVISIONAL` claim exist
  only inside PR #35's own branch copy of these files (not yet merged),
  per commit `330be28` on that branch. `certification_state` for FTF-0004
  stays `PROVISIONAL` as that branch itself already states — not
  downgraded further, since nothing found here contradicts that L1
  evidence, which was produced independently of and is re-checkable
  regardless of `main`'s tip.
  **Action needed before resubmitting:** rebase/merge the actual current
  `main` (`4ea6784` or later) into `claude/wizardly-fermat-3mh1pd`,
  resolve the `PROGRESS.md`/`QUEUE.md` conflicts (both are pure
  tracking-doc conflicts, not code), push, and let CI actually run to
  completion — including the `docker-local-stack-integration` job —
  before requesting certification again.
- **Validated-by:** session https://claude.ai/code/session_01JwgxJqEYCMhQVzVk2bXdvT
  (independent — see session-ID check above), 2026-09-16.

## 2026-09-16 — FTF-0004 — Structural blockers fixed, resubmitted
- **PR:** https://github.com/SJZLab/Sovra/pull/35 (same PR — not a
  duplicate, per the review above's own instruction).
- **Fix for Blocker 1 (not mergeable):** ran a real `git fetch origin
  main` (`fe954e0`, which itself carries the review commit `2dfdecb`
  above) followed by a real `git merge origin/main --no-edit` — not
  another content copy. This produced genuine conflict markers in
  exactly the four tracking docs both branches had touched
  (`PROGRESS.md`, `QUEUE.md`, `docs/certifications/queue.md`,
  `docs/spec-matrix.yaml`) and zero conflicts anywhere in code —
  independently confirming the reviewer's own diagnosis that this was a
  tracking-doc-only divergence. Resolved each by keeping both sides'
  substantive content (this branch's FTF-0004 entries plus `main`'s real
  FTF-0005-certification and this NOT-CERTIFIED-review history, in
  chronological order) rather than discarding either.
- **Fix for Blocker 2 (CI never ran):** a natural consequence of pushing
  the merge commit — CI now runs on a real, mergeable head for the first
  time on this PR, including the new `docker-local-stack-integration`
  job.
- **Regression check on the merge itself:** re-ran the full gate after
  resolving conflicts, before pushing — `uv run ruff check`/`format
  --check`/`mypy` clean; `uv run pytest tests/unit` (3 passed);
  `tests/tasks/test_ftf_0002.py`/`test_ftf_0003.py`/`test_ftf_0004.py`/
  `test_ftf_0005.py` and `tests/test_ftf_0001_monorepo_layout.py` all
  pass; `pnpm run lint`/`typecheck`/`format:check` and `pnpm exec vitest
  run` clean; `product_truth_check.py --fail-on-unwired` stays `0/0`.
  No regression introduced by the merge.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL
  (unchanged — the delivered code is unchanged; only the branch's
  relationship to `main` was fixed). Not self-certified — awaiting
  Control plane to confirm `mergeable_state` is now clean and that CI,
  especially `docker-local-stack-integration`, actually ran to completion
  on the new merge commit before re-reviewing.

## 2026-09-16 — FTF-0004 — Real CI failure found and fixed on the merge commit
- **Commit:** c595897 (branch `claude/wizardly-fermat-3mh1pd`), on top of
  the merge commit `c9ab1c9` above.
- **What happened:** the merge fix above correctly made the PR mergeable
  and triggered CI for the first time. `docker-local-stack-integration`
  **failed for real** (GitHub Actions run 35095359741, job
  104791239843) — read via `get_job_logs`, not inferred from the red X:
  the stack booted, the marker row was written, `docker compose stop
  postgres` ran, then `poll_health(expect_ready=False)` timed out after
  120s and the job errored with `TimeoutError: health endpoint never
  reported ready=False (last error: HTTP Error 503: Service
  Unavailable)` — even though the health endpoint's own log line
  (`stack ready: {...}`) plus the 503 status in the error message prove
  it was correctly reporting unhealthy the entire time.
- **Root cause:** `urllib.request.urlopen()` raises `urllib.error.
  HTTPError` for any non-2xx response *before* the caller ever reads the
  response body — `poll_health`'s `except (urllib.error.URLError, ...)`
  clause caught the 503 as an opaque connection failure (HTTPError is a
  URLError subclass) and discarded it without ever parsing the very JSON
  body (`{"ready": false, ...}`) the negative-acceptance test needed to
  see, so it just kept retrying until the deadline. A real bug in the CI
  verification script, not in `docker_local_stack.py`'s readiness logic
  (already proven real by `tests/tasks/test_ftf_0004.py`), not a Docker/
  network flake, and not the containers' fault.
- **Fix:** `poll_health` now reads and parses the body from
  `urllib.error.HTTPError` the same way as a normal 2xx response, then
  checks the real `ready` field regardless of HTTP status code.
- **Verified locally before pushing (no Docker needed to reproduce this
  specific bug):** started the real stdlib health server
  (`docker_local_stack.serve`) pointed at closed local sockets
  (simulating every dependency being down, the same shape as a stopped
  postgres container) and called the *fixed* `poll_health(expect_ready=
  False, timeout_seconds=10)` against it — returned `ready: False`
  correctly in under a second, exercising the exact `HTTPError`-on-503
  code path the real CI traceback showed was broken. Also re-verified
  the healthy (200) path is unaffected using the same harness with all
  three local listeners open.
- **Regression check:** `uv run ruff check`/`format --check`/`mypy`
  clean; `tests/tasks/test_ftf_0004.py` (9/9, unaffected — it doesn't
  exercise `verify_docker_local_stack.py`, only the readiness module
  itself); `docker compose -f infra/docker/docker-compose.yml config`
  parses cleanly.
- **Real CI result after the fix (GitHub Actions run 35095834970):**
  `gate` — `success`. `docker-local-stack-integration` — `success`,
  confirmed via `get_check_runs` on the actual PR head SHA
  (`c59589736fe1001b550601e40741336aacef0f12`) — the genuine
  container-boot, restart, and marker-row-persistence evidence
  FTF-0004's negative_acceptance clause requires.
- **PR:** `mergeable_state: clean`; PR body's test-plan checklist updated
  to reflect the real green run and the full history (structural
  rejection → merge fix → real CI failure → real fix → green).
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review, which should now
  find a clean merge and a genuinely green `docker-local-stack-
  integration` run (not merely its presence) before certifying.

## 2026-09-16 — FTF-0006 — Observability foundation
- **PR:** to be opened against `main` from `claude/wizardly-fermat-6dwdsp`.
- **Commit:** 91eeff152e11dd0bdc17c4dded1e69e9d0b7fbed (implementation)
- **Real DoD source:** `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolved via
  `python tools/r1_handoff.py packet FTF-0006 --plan FINE_TUNING_FACTORY_RELEASE_1.md`.
  Dependencies FTF-0001/FTF-0004/FTF-0005 all `CERTIFIED` — unblocked.
- **Scope finding before implementation:** `apps/api` still has no
  `main.py` (no FastAPI app wired) and no `services/workflow-worker`
  exists (no Temporal workflow/activity code) — there is no live product
  API route or workflow to instrument yet. Building either now, solely to
  host an observability demo, would itself be a new-scope/new-runtime-dep
  ask-first item under CLAUDE.md. Followed the same precedent FTF-0004
  used for `apps/web`: build the real seam and prove it against a genuine
  local integration (a real stdlib HTTP server bound to a real socket,
  hit with a real HTTP request over the wire), and disclose the
  workflow-side gap as a follow-up rather than inventing a placeholder
  workflow.
- **Delivered:** `apps/api/app/config/observability_foundation.py` —
  stdlib-only (`dataclasses`/`enum`/`uuid`/`time`/`json`/`re`/`threading`,
  no third-party dependency added, same reasoning FTF-0004/0005 used to
  avoid FastAPI/pydantic/Prometheus/OpenTelemetry for this foundation
  layer):
  - `StructuredEvent`: a frozen dataclass with a fixed field set
    (`event`, `correlation_id`, `identity`, `status`, `duration_ms`,
    `timestamp_utc`, `cost_usd`) — no field capable of holding a raw
    prompt/source/token/signed URL exists at all; an unexpected keyword
    raises `TypeError` at construction (structural, not filtered).
  - `event`/`identity` are validated against a short safe-identifier
    pattern (`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`) — rejects prose with
    spaces/newlines, URLs with query strings (`?`, `&`, `=`, `/` are not
    in the allowed charset), and anything over 128 characters. Documented
    honestly as a format boundary, not a secret scanner: it cannot detect
    an arbitrary short opaque token shaped like a legitimate slug ID —
    the structural guarantee against a credential leaking is that no
    field is meant to hold one in the first place.
  - `MetricsRegistry`: in-memory numeric counters/duration sums, rejects
    any label outside a caller-predeclared finite set — cardinality is
    bounded by construction, proven quantitatively (50 requests with 50
    distinct correlation IDs still produce exactly 1 observed label).
  - `InstrumentedRequest`: a context manager wrapping one real
    request/activity — issues a correlation ID, times the handler,
    records a numeric duration keyed only by a bounded status label, and
    emits exactly one `StructuredEvent`. Never swallows an exception
    raised inside the `with` block (`__exit__` returns `None`).
- **Test:** `tests/tasks/test_ftf_0006.py` — 10 check functions.
- **Evidence — shown able to fail (Law 1):**
  1. Acceptance clause 1 ("one actual request crosses API/workflow
     instrumentation with correlation and numeric metrics"):
     `test_one_real_http_request_crosses_instrumentation` binds a real
     stdlib `ThreadingHTTPServer` to a real local socket, whose handler
     wraps the response in `InstrumentedRequest`, then issues a genuine
     HTTP GET over the wire via `http.client` (not an in-process function
     call) — asserts the response carries a correlation-ID header, the
     emitted `StructuredEvent`'s correlation ID matches the real request's,
     the recorded duration is numeric, and the numeric metric count is
     exactly 1 for that one real request.
  2. Negative_acceptance ("cardinality remains bounded"), proven
     quantitatively: `test_cardinality_stays_bounded_across_many_distinct_correlation_ids`
     issues 50 real, distinct correlation IDs through `InstrumentedRequest`
     and asserts `MetricsRegistry.cardinality()` stays at 1 (only the "ok"
     label was ever used) — correlation IDs are never themselves used as
     metric labels.
  3. Negative_acceptance ("never appear in logs/spans"): separate checks
     prove (a) an unexpected keyword (`raw_prompt=...`) is rejected with
     `TypeError` at construction — the schema structurally has no field
     for it; (b) prose/newlines/signed-URL querystrings/oversized blobs
     passed as `identity` are rejected with `ValueError`, with a clean
     short identity still constructing successfully (a real boundary, not
     a blanket rejection).
  4. Real fault injection against the tracked module itself (not just
     fixtures): temporarily removed `MetricsRegistry.record`'s bounded-
     label check — re-ran the test suite, exactly the cardinality-bound
     check failed for real (`a label outside the predeclared bounded set
     is rejected`); restored the file byte-identical from a pre-edit copy
     (`diff` confirmed empty), re-ran clean (all 10 checks pass).
- **Regression check:** `uv run ruff check .` / `uv run ruff format
  --check .` / `uv run mypy .` clean; `uv run pytest tests/unit` (3
  passed); `tests/tasks/test_ftf_0002.py`, `test_ftf_0003.py`,
  `test_ftf_0004.py`, `test_ftf_0005.py`, and
  `tests/test_ftf_0001_monorepo_layout.py` all still pass unmodified — no
  regression to certified evidence. `pnpm run lint`/`typecheck`/
  `format:check` and `pnpm exec vitest run` (2 passed) clean — no
  TypeScript files touched. `product_truth_check.py --fail-on-unwired`
  stays `0/0 product-true` — legitimate, no proving_symbol yet (no live
  caller wires this into a running API/workflow, same rationale as
  FTF-0002/0003/0004/0005).
- **CI:** wired `python3 tests/tasks/test_ftf_0006.py` into the existing
  `gate` job in `.github/workflows/ci.yml`, right after the FTF-0004 step;
  YAML-validated (`yaml.safe_load`). Real run status to be confirmed on
  the opened PR before merge.
- **Acceptance clause 2** ("independent review, exact merged commit, real
  relevant integration and negative-test evidence recorded") is satisfied
  by this queue entry plus the standard PR/CI/Control-plane-review flow,
  same as every other FTF task in this repo.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.

### Control plane review — 2026-09-16 — CERTIFIED
- **PR:** https://github.com/SJZLab/Sovra/pull/39 (`claude/wizardly-fermat-6dwdsp`
  → `main`), merge commit `268186bd5e70032a6c563796910059ab2684b684`.
- **Session-ID check:** all three PR commits (`91eeff1`, `7d5834f`,
  `0970fa8`) carry `Claude-Session: .../session_01GvzemvrJeMmGiZsAAPK5m1`;
  this reviewing session's ID (`session_01QPhpMGcPNZJXq3DMYkNFtJ`) does
  not match — independent, not self-certification.
- **Diff re-read; merged code confirmed byte-identical to the diff:**
  `git diff origin/main -- apps/api/app/config/observability_foundation.py
  tests/tasks/test_ftf_0006.py` empty on the checked-out tree.
- **CI verified for real:** `list_workflow_jobs` on run `35112030663`
  (not the PR body's claim) — both `gate` and
  `docker-local-stack-integration` `conclusion: success`, `head_sha`
  `0970fa8eb4b80635cd919f929d74f1ac49884325` (the PR's actual final head,
  confirmed via `get_commits`), including the discrete "FTF-0006
  negative-test proof (observability foundation catches real defects)"
  step (`gate` step 20, `success`).
  `python3 tests/tasks/test_ftf_0006.py` re-run directly: 10/10 pass.
- **Real fault injection, twice:** (a) reproduced the producer's own
  claim — removed `MetricsRegistry.record`'s bounded-label rejection —
  exactly 1 check failed (`a label outside the predeclared bounded set is
  rejected`); (b) an independent injection outside the producer's set —
  disabled `_validate_identifier`'s `_SAFE_IDENTIFIER.match` check —
  exactly 1 check failed (`free-form content ... cannot pass as
  identity`). Both restored byte-identical (`diff` confirmed empty),
  re-ran clean (10/10) after each.
- **product_truth_check.py:** `--fail-on-unwired` → `0/0 product-true`,
  legitimate — `docs/spec-matrix.yaml`'s FTF-0006 entry has
  `proving_symbol: ''` and no live FastAPI app or Temporal workflow exists
  yet to wire this seam into (independently confirmed: `apps/api` has no
  `main.py`, no `services/workflow-worker` exists).
- **Real contract pulled and checked:** `python tools/r1_handoff.py
  packet FTF-0006 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance
  clause 1 ("one actual request crosses API/workflow instrumentation with
  correlation and numeric metrics") met via the real stdlib
  `ThreadingHTTPServer`/real-socket/real-`http.client`-request test, a
  disclosed scope limitation consistent with FTF-0004's own precedent for
  `apps/web`, not a hidden gap; acceptance clause 2 (independent review,
  exact merged commit, real evidence recorded) met by this entry;
  negative_acceptance (content-free events, bounded cardinality) met per
  the fault injections above plus the quantitative 50-correlation-ID/1-label
  cardinality test.
- **Regression check:** `uv run ruff check .` / `format --check .` /
  `mypy .` clean; `uv run pytest tests/unit` (3 passed, unchanged). No
  regression to FTF-0001/0002/0003/0004/0005's certified evidence.
- **Conclusion:** the L1 evidence holds up under independent
  re-verification. Certified L2-CERTIFIED. `docs/spec-matrix.yaml`,
  `docs/certifications/ledger.md`, `QUEUE.md`, and `PROGRESS.md` updated
  accordingly.
- **Validated-by:** session https://claude.ai/code/session_01QPhpMGcPNZJXq3DMYkNFtJ
  (independent — see session-ID check above), 2026-09-16.
