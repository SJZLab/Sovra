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

## 2026-09-16 — FTF-H001 — Executable specification and gate bootstrap
- **PR:** https://github.com/SJZLab/Sovra/pull/43 (`claude/wizardly-fermat-43jlvy`
  → `main`).
- **Commit:** `6a1abc093f2c8897e86d8e33bd33667d1c2ee0f3` (implementation),
  `d4301b4` (tracking-doc hash fixup — same two-commit pattern as
  FTF-0006's `91eeff1` + `7d5834f`, since a commit can't embed its own
  hash).
- **Real DoD source:** `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolved via
  `python tools/r1_handoff.py packet FTF-H001 --plan FINE_TUNING_FACTORY_RELEASE_1.md`.
  Dependencies FTF-0001/FTF-0002/FTF-0003 all `CERTIFIED` — unblocked.
- **Why this task, not FTF-0101:** QUEUE.md's own "Queue (ordered by
  dependency)" section lists FTF-0101…0106 next, but FTF-0101's real
  dependency graph (from its own task packet) is `FTF-0004, FTF-0005,
  FTF-0102`, and FTF-0102 in turn depends on `FTF-0004, FTF-0005, FTF-H001`
  — FTF-H001 itself was `certification_state: NONE` /
  `implementation_state: ABSENT` in `docs/spec-matrix.yaml`, with its own
  prerequisites (FTF-0001/0002/0003) all CERTIFIED. It, not FTF-0101, was
  the actual dependency-ready frontier item. Confirmed via
  `tools/r1_handoff.py packet` for all three IDs before picking one, not
  assumed from QUEUE.md's summary ordering alone.
- **Scope finding before implementation:** this task's own
  `verification_commands` (`python scripts/validate_spec.py --strict`,
  `python scripts/verify_task.py --task FTF-H001 --stage task`) reference
  scripts that do not exist yet — FTF-H001 is precisely the task that
  creates them, matching Section 75.1's own bootstrap note ("implement/
  bootstrap its actual commands in FTF-H001; until then FTF-0001/0002/0003
  use their direct pinned commands"). The task's `files` list also names
  `packages/contracts/openapi/18-interface.yaml` and
  `jsonschema/18-domain.schema.json`; neither is required by this task's
  actual acceptance/negative_acceptance clauses (both concern the
  validator/verifier tooling and the task/evidence ledger only), and no
  real API endpoint exists yet to back an OpenAPI contract — inventing an
  empty, homeless interface stub would risk a placeholder under Law 11.
  Deliberately not created; disclosed as a follow-up in
  `docs/spec-matrix.yaml`'s notes, same precedent as FTF-0004's `web`
  exclusion.
- **Delivered:**
  - `scripts/spec_verification.py`: shared parser/verifier module.
    `load_tasks()` parses `spec/tasks.json` (the frozen 300-task ledger
    FTF-0001's own bootstrap produced) into a dependency graph;
    `check_structural()` runs Kahn's-algorithm cycle detection restricted
    to edges whose target actually exists (so a `missing_task` defect
    doesn't also mask a real cycle) and checks every
    `qualification_scenarios` reference against
    `spec/qualification-inventory.json`'s real 43-scenario set.
    `check_evidence()` validates `spec/verification-map.json` — for every
    task claiming merged evidence, its `commit` must resolve as a real git
    object in this repository (`git cat-file -e <hash>^{commit}`) and it
    must not record any skipped test.
  - `spec/verification-map.json`: the new, real "executable specification"
    — maps each of the 6 currently-CERTIFIED task IDs to its actual,
    currently re-executable command (`python3 tests/tasks/test_ftf_000X.py`
    or `tests/test_ftf_0001_monorepo_layout.py`, exactly as CI runs them),
    the real merge-commit `evidence_hash` recorded in
    `docs/spec-matrix.yaml`, and the literal success string each script
    prints on a clean run. A task absent from this file (294 of 300, all
    still `NOT_STARTED`) has no entry — `verify_task.py` reports it
    `BLOCKED`, never a fabricated `PASS`.
  - `scripts/validate_spec.py --strict`: CLI wrapping `validate()` — the
    schema-validated, nonzero-exit structural + evidence validator this
    task's contract requires.
  - `scripts/verify_task.py --task <ID> --stage task`: CLI wrapping
    `verify_task()` — resolves the real command(s) for one task from
    `spec/verification-map.json` and actually runs them against this
    checkout (no shell string interpolation — every command is stored and
    executed as an argv list). Rejects an unknown `--stage` (only `task` is
    currently defined; release-wide checking is `verify_release.py`'s
    separate, Tier-3 job). A task with no map entry is `BLOCKED`.
  - `scripts/verify_release.py`: whole-release gate — iterates every
    required task in `spec/tasks.json`, requires both a live-passing
    `verify_task()` result AND `certification_state: CERTIFIED` in
    `docs/spec-matrix.yaml`. Tier 3 / release-only per CLAUDE.md ("ship-gate
    = release only; relay never dispatches it") — built, not run as a
    per-task gate. Live run against the real repo today correctly reports
    `6/300 required task(s) have live-passing, CERTIFIED evidence` /
    `NOT READY` — no inflation.
- **Test:** `tests/tasks/test_ftf_h001.py` — 13 check functions.
- **Evidence — shown able to fail (Law 1), covering every fixture kind this
  task's own acceptance clause names:**
  1. A valid 3-task fixture (`FTF-A <- FTF-B <- FTF-C`, one known scenario
     each) produces zero defects.
  2. `missing_task` — a dependency on an unknown task ID is rejected.
  3. `duplicate_id` — the same task ID appearing twice is rejected (caught
     in `load_tasks()` itself, since a dict can't hold two same-keyed
     fixtures long enough to compare downstream).
  4. `cyclic_dependency` — `FTF-A -> FTF-C -> FTF-B -> FTF-A` is rejected.
  5. `missing_scenario` — a reference to an unknown scenario ID (`Q99`) is
     rejected.
  6. `skipped_test` — a verification-map entry recording
     `expected_skipped: 1` is rejected.
  7. `stale_commit` — a verification-map entry whose `commit` does not
     resolve in this repository is rejected.
  8. Negative_acceptance: `verify_task("FTF-DOES-NOT-EXIST", "task")`
     returns `BLOCKED`, never `PASS` — proving "absent... evidence must
     report blocked/fail rather than pass."
  9. An unknown `--stage` (`"release"`) is rejected (`FAIL`), not silently
     accepted.
  10. **Not fabricated, proven live:** the real, shipped
      `spec/verification-map.json` entry for FTF-0002 is re-run for real
      through the actual CLI (`python3 scripts/verify_task.py --task
      FTF-0002 --stage task`) against this exact checkout and genuinely
      passes — not just re-read as trusted text.
  11. The real `python3 scripts/validate_spec.py --strict` CLI is clean
      against the real, shipped ledger.
  12. **Real fault injection against the shipped map file itself (not just
      a fixture):** temporarily corrupted the real
      `spec/verification-map.json`'s `FTF-0002.commit` to `"f"*40` —
      `validate_spec.py --strict` genuinely failed with
      `stale_commit: FTF-0002` in its output; restored byte-identical
      (`MAP_FILE.read_text() == original` asserted directly, not just
      `git diff`), re-ran clean.
  13. **A second, independent fault injection:** temporarily corrupted
      FTF-0002's `expected_output_contains` marker to a string that can
      never appear — `verify_task.py` genuinely failed even though the
      underlying `test_ftf_0002.py` command still exits 0, proving the
      marker check is real, not decorative; restored byte-identical,
      re-ran clean.
  14. Additionally (outside the fixture set, module-level Law 1 evidence):
      temporarily deleted the `stale_commit` check block from
      `scripts/spec_verification.py` itself — this made `ruff check .`
      genuinely fail on the resulting unused-variable warning, which then
      cascaded into a real `FAIL` on FTF-0002's own live re-verification
      (since its mapped command is `ruff check .`'s own umbrella script) —
      confirming the whole gate catches a broken verifier end-to-end, not
      only its own unit fixtures. Restored via file copy, `diff` confirmed
      empty, re-ran clean.
- **Acceptance clause 2** ("No full release claim until all mapped
  scenarios and exact-revision evidence pass") is enforced structurally by
  `verify_release.py` requiring both live-passing evidence and
  `certification_state: CERTIFIED` for every required task — verified this
  is not just written but actually true today (6/300, NOT READY, no
  inflation).
- **Regression check:** `uv run ruff check .` / `uv run ruff format
  --check .` / `uv run mypy .` clean; `pnpm run lint` / `format:check` /
  `typecheck` clean; all 7 existing task negative-test-proof scripts
  (`test_ftf_0001_monorepo_layout.py` through `test_ftf_0006.py`, now
  joined by `test_ftf_h001.py`) still pass unmodified; `uv run pytest
  tests/unit` (3 passed) and `pnpm exec vitest run` (2 passed) both clean
  with 0 skipped either side (`check_test_harness.py` confirms);
  `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, `proving_symbol` deliberately empty (this is CI/gate
  tooling, not a live product-runtime path, same rationale as
  FTF-0002/0003). No already-certified task's own implementation file was
  modified — only additive tooling, avoiding any Law 15 requalification
  question for FTF-0001–0006's existing certified evidence.
- **CI:** wired two new steps into the existing `gate` job in
  `.github/workflows/ci.yml`, right after FTF-0006's step:
  `python3 scripts/validate_spec.py --strict` and `python3
  tests/tasks/test_ftf_h001.py`; YAML-validated (`yaml.safe_load`). Real
  run status to be confirmed on the opened PR before merge.
- **Not touched:** the existing FTF-0002–0006 CI steps still call their own
  test scripts directly (`python3 tests/tasks/test_ftf_000X.py`), not
  rerouted through `verify_task.py` — rerouting already-certified tasks'
  own gate invocation through new tooling would be a source/manifest
  change to their certified evidence requiring Law 15 requalification,
  out of this task's scope. `spec/verification-map.json` records their
  real commands as a parallel, additive index for the new verifier, not a
  replacement for their own CI steps.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.

## 2026-09-16 — FTF-R001 — Freeze seven-component architecture, ownership and version-lock schemas
- **Branch:** `claude/wizardly-fermat-1r7ubn` → `main` (no PR opened yet this
  shift; see relay note below).
- **Commits:** `f9c82e910cde344a08b910c5b059e85c25b59adf` (implementation),
  `42c1adf` (hash-fixup — evidence_hash + verification-map.json entry, same
  two-commit pattern as FTF-0006/FTF-H001, a commit can't embed its own hash).
- **Dependency check (real DoD, not improvised):** `tools/r1_handoff.py
  packet FTF-R001 --plan FINE_TUNING_FACTORY_RELEASE_1.md` resolved before
  claiming. Dependencies `FTF-0001`, `FTF-H001` both already CERTIFIED —
  confirmed dependency-ready (QUEUE.md's own "Owner actions" block only
  named FTF-0101/FTF-0102 as the frontier; FTF-0101 is actually blocked
  behind FTF-0102, and FTF-0102 itself is blocked on a genuine
  new-runtime-dependency owner ask — FTF-R001's own deps do not touch either
  of those, so it is a real, separate dependency-ready item this shift
  found by checking `docs/spec-matrix.yaml` directly rather than only
  QUEUE.md's summary).
- **Delivered:**
  - `packages/component-contracts/architecture.schema.json` — freezes
    `ComponentBinding` (exactly the seven canonical component keys; Axolotl/
    Inspect AI structurally barred from an always-on HTTP endpoint since
    they are worker-executed software, not assumed always-on HTTP services
    per Section 77.1; LiteLLM pinned to `existing_gateway`) and
    `ReleaseApprovalAuthority` (capped to exactly one Factory-owned entry,
    never a vendor component, per Section 77.4's "no competing authority").
  - `spec/component-lock.schema.json` — freezes `ComponentVersionLock`;
    rejects any `package_version`/`image_digest` that is a mutable tag
    (`latest`/`main`/`master`/`HEAD`) rather than a real pinned
    version/content digest.
  - `spec/deployment-profiles.yaml` — the four Section 79.2 placement
    profiles (`INTERNAL_COMPACT`, `INTERNAL_PRODUCTION`, `AIR_GAPPED`,
    `CONNECTED_MANAGED`) verbatim, plus the Section 79.5
    assertion-applicability worked example (Q12, Q03).
  - `docs/architecture/reuse-ownership.md` — ComponentBinding/VersionLock/
    ExternalRunBinding ownership, the Section 77.3 responsibility table, and
    the Section 77.6 "UI calls the Factory API, never a vendor dashboard"
    rule.
  - `scripts/architecture_lock.py` — a bounded JSON Schema validator matched
    to only the keywords the two frozen schemas use (`type`, `enum`,
    `const`, `pattern`, `minLength`, `required`, `properties`,
    `additionalProperties`, `items`, `minItems`/`maxItems`, `allOf`/`if`/
    `then`, local `$ref`), plus the one whole-set business rule no
    per-record schema keyword can express: the seven bindings/locks must be
    exactly the seven canonical keys, each exactly once.
  - Not created: `ExternalRunBinding` and
    `packages/integrations/{mlflow,argilla,litellm}/` — no live component
    run or adapter exists yet to back them; same disclosed-scope-limitation
    pattern as FTF-H001's undelivered `packages/contracts/` stub paths and
    FTF-0004's `web` exclusion.
- **Test:** `tests/tasks/test_ftf_r001.py` — 13 checks (16 individual
  `check()` assertions across them). Proves the schemas
  enumerate exactly the seven canonical keys and stay in lockstep with each
  other; a genuinely correct binding/lock document passes with zero errors;
  and all four of the task's own `negative_acceptance` cases are rejected:
  (1) missing component, (2) an invented component key substituted for a
  canonical one (both the whole-set check and the schema's own `enum`
  independently reject it), (3) an invented always-on HTTP endpoint on
  Axolotl and on Inspect AI (two separate cases), (4) a second release
  approval authority record and a vendor component (`mlflow`) claiming
  authority (two separate cases for "duplicate registry approval
  authority"), (5) a mutable `latest` `package_version` and a mutable
  `latest` `image_digest` (two separate cases for "mutable latest-version
  release lock").
- **Law 1 evidence (real fault injections, not fixture-only):**
  1. Temporarily replaced the whole-set key-check calls in
     `scripts/architecture_lock.py` with `pass` (business rule disabled) —
     re-ran the suite: exactly the missing/duplicate-component checks
     failed for real (3 failures, the ones that rule alone catches);
     restored byte-identical (`diff` confirmed empty against a pre-edit
     copy), re-ran clean (all 13 checks passed).
  2. Temporarily removed the Axolotl/Inspect-AI worker-package `allOf` rule
     from the shipped `architecture.schema.json` itself — re-ran the suite:
     exactly the two invented-endpoint checks failed for real; restored
     byte-identical, re-ran clean.
  3. Additionally confirmed (not a fixture, a structural fact about the
     schemas): `component_bindings` is schema-pinned to exactly 7 items, so
     a *missing* component always also trips item count — but a duplicate
     substituted for a missing key keeps the count at 7 with every
     individual binding still schema-valid, so schema-only validation
     (`validate_instance` with no whole-set check) genuinely passes that
     fixture (`test_law1_evidence_key_set_check_catches_what_schema_alone_cannot`),
     while the real validator (schema + whole-set check) rejects it —
     confirming the extra business-rule check is load-bearing, not
     decorative re-assertion of what the schema already guarantees.
- **Regression check:** `uv run ruff check .` / `ruff format --check .` /
  `mypy .` clean; `pnpm run lint` / `typecheck` / `format:check` clean;
  all 8 existing task negative-test-proof scripts
  (`test_ftf_0001_monorepo_layout.py` through `test_ftf_h001.py`, now joined
  by `test_ftf_r001.py`) still pass unmodified; `uv run pytest tests/unit`
  (3 passed) and `pnpm exec vitest run` (2 passed) both clean, 0 skipped;
  `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, `proving_symbol` deliberately empty (architecture/schema
  freeze, no live product-runtime caller yet, same rationale as
  FTF-0002/0003/H001). No already-certified task's own implementation file
  was touched — only additive schemas/docs/tooling, avoiding any Law 15
  requalification question for FTF-0001–0006/H001's existing certified
  evidence.
- **Real verifier re-run (not just local test):** `python3
  scripts/validate_spec.py --strict` clean; `python3 scripts/verify_task.py
  --task FTF-R001 --stage task` → `PASS` against the real shipped
  `spec/verification-map.json` entry, genuinely re-executed against this
  exact checkout, not re-read as trusted text. Negative_acceptance of the
  verifier itself reconfirmed unchanged: an unknown task ID still returns
  `BLOCKED`, an unknown stage still returns `FAIL`.
- **CI:** wired `python3 tests/tasks/test_ftf_r001.py` into the existing
  `gate` job in `.github/workflows/ci.yml`, right after FTF-H001's step;
  YAML-validated (`yaml.safe_load`). Real run status to be confirmed on the
  opened PR before merge.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4. PR to be
  opened against `main` this same shift; not merging without confirming CI
  green first.

## 2026-09-17 — FTF-H002 — Freeze release scope, compatibility and measured execution inputs
- **Branch:** `claude/wizardly-fermat-brui19` → `main` (PR to be opened this shift).
- **Real DoD source:** `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolved via
  `python tools/r1_handoff.py packet FTF-H002 --plan FINE_TUNING_FACTORY_RELEASE_1.md`.
  Dependencies `FTF-H001`, `FTF-0005` both CERTIFIED — unblocked (found via a
  direct `docs/spec-matrix.yaml` dependency sweep, same method the FTF-R001
  shift used, since QUEUE.md's own "Owner actions" block only named the
  blocked FTF-0101/FTF-0102 pair as the apparent frontier).
- **Scope finding before implementation:** Section 64.3's onboarding gate asks
  for real GPU/driver, Kubernetes CNI, LiteLLM gateway, managed-provider
  (Bedrock) account and coding-provider billing evidence. None of these real
  external resources exist in this sandbox (no GPU, no Kubernetes cluster, no
  LiteLLM gateway bound, no validated AWS/Bedrock access, no coding-provider
  billing API reachable). Section 64.3 itself names the correct response:
  "Missing inputs use BLOCKED_EXTERNAL with owner and exact next action... The
  developer can build contracts and isolated unit code while blocked, but
  cannot mark real integration or full release PASS." Built the real preflight
  and lock artifacts against that explicit contract rather than writing
  BLOCKER.md — the DoD was not unclear (the resolved packet is exact), and
  Section 64.3's own text is the same "build isolated unit code while blocked"
  precedent FTF-0004 (registry egress) and FTF-0005/0006 (ask-first new
  runtime deps) already established in this repo.
- **Delivered:**
  - `apps/api/app/services/environment_preflight.py` — stdlib-only (no new
    runtime dependency). `PreflightResult.__post_init__` structurally forbids
    constructing a `BLOCKED_EXTERNAL` result with an empty `owner`/
    `next_action` — the anti-fabrication guardrail Section 64.3 requires ("do
    not populate fabricated model IDs, URLs, credentials or prices").
    Real, non-canned checks: `check_python_runtime` (real interpreter
    version), `check_git_cli`/`check_kubernetes_cni` (real `shutil.which` +
    subprocess version call, never a hardcoded verdict), `check_gpu_driver`
    (real `/proc/driver/nvidia/version` filesystem check), `check_litellm_gateway`
    (reuses FTF-0004's `check_tcp_reachable`/`ServiceEndpoint` for a real TCP
    probe against whatever `SOVRA_LITELLM_GATEWAY_URL` names, deliberately
    independent of FTF-0005's full `Settings.from_env()` per this task's own
    negative_acceptance: "implement preflight independently of final
    qualification to avoid a dependency cycle"), `check_managed_provider_bedrock`
    (real AWS credential env-var presence check that still never reports
    AVAILABLE from presence alone — validated account/model access needs the
    AWS SDK, an unapproved new runtime dependency, so it stays BLOCKED_EXTERNAL
    either way), `check_coding_provider_billing` (always BLOCKED_EXTERNAL —
    no billing/usage API is reachable from this session).
  - `scripts/release_scope_lock.py` + generated `spec/release-scope.yaml` —
    Section 64.2's CAP-01..CAP-21 inventory. Never hand-authored: generated
    from `spec/qualification-inventory.json` (the single source of truth
    FTF-H001's bootstrap already froze), adding the `owner`/
    `supported_version_profile` columns Section 64.2 also requires.
    `validate_release_scope` is the whole-set business rule no YAML schema
    keyword alone expresses: exactly the 21 canonical CAP IDs, each
    `required: true` (never downgradable), each with its
    `implementation_tasks`/`qualification_scenarios` matching the frozen
    inventory exactly — rejecting a quietly cut required scope (this task's
    own negative_acceptance).
  - `scripts/generate_compatibility_lock.py` + generated
    `spec/compatibility-lock.json` — a real, live-regenerated snapshot of
    `run_preflight()` against this exact sandbox (`overall_status:
    BLOCKED_EXTERNAL` today — no GPU/Kubernetes/LiteLLM gateway/validated
    Bedrock access exists here), not a hand-typed or fabricated version table.
  - `docs/operations/budget-forecast.md` — Section 64.4's real vertical-slice
    forecast. Reports real, reproducible measurements from this repository's
    own `git log` (8 certified tasks to date; commit count/elapsed span
    reproducible on demand rather than pinned to a number that goes stale).
    Explicitly discloses the coding-provider billing gap as
    `BLOCKED_EXTERNAL` with a real owner/next action, and invents no
    per-token/per-hour/per-task dollar rate anywhere in the document (this
    task's own negative_acceptance: "Do not invent a model ID/rate").
  - Not delivered (disclosed, not hidden): real validated Bedrock
    account/model access (needs the AWS SDK — an ask-first new runtime
    dependency), a real bound LiteLLM gateway, Kubernetes cluster, GPU worker,
    or coding-provider billing export. Each is recorded as a required
    `next_action` with a real `owner` in the preflight's own output, not
    silently assumed away — same disclosed-scope-limitation pattern as
    FTF-0004's `web` exclusion and FTF-R001's `ExternalRunBinding` deferral.
- **Test:** `tests/tasks/test_ftf_h002.py` — 15 check functions.
- **Evidence — shown able to fail (Law 1):**
  1. `PreflightResult`'s owner/next_action guard is proven directly (a
     `None` or blank owner raises `ValueError`; a well-formed result
     constructs).
  2. Every check's real/positive branch is proven with a genuine probe, not
     a mock: `check_git_cli`/`check_kubernetes_cni` against a real empty-PATH
     temp directory (negative) and a real, genuinely-executable fake
     `kubectl` script on a real temp PATH entry that actually runs and whose
     real stdout is parsed (positive); `check_litellm_gateway` against a real
     bound-and-listening local socket (AVAILABLE) and that same socket after
     it is closed (BLOCKED_EXTERNAL, proving "stale LiteLLM routes... are
     explicit external blockers"); `check_gpu_driver` against this sandbox's
     real absence of `/proc/driver/nvidia/version`.
  3. `check_managed_provider_bedrock` proven to stay BLOCKED_EXTERNAL both
     with no AWS credential env var and with one present but unvalidated —
     two distinct detail messages, proving it never claims AVAILABLE from
     credential presence alone (this task's acceptance clause: "unavailable
     managed capabilities... are explicit external blockers").
  4. `release_scope_lock.validate_release_scope` proven to reject a removed
     CAP ID, a downgraded `required: false`, and a cut
     `implementation_tasks` set — and the real committed
     `spec/release-scope.yaml` passes it with zero errors.
  5. The committed `spec/compatibility-lock.json` proven not stale: its
     `overall_status` is asserted equal to a live re-run of `run_preflight()`
     against this exact checkout.
  6. `docs/operations/budget-forecast.md` proven to disclose
     `BLOCKED_EXTERNAL` and to contain no fabricated `$<number>/hour|token|task`
     pattern.
  7. **Real fault injection against the tracked module itself (not a
     fixture):** temporarily removed the `owner`/`next_action` guard block
     from `apps/api/app/services/environment_preflight.py`, then ran a
     genuinely fresh subprocess (a new Python interpreter, so the edited file
     is actually re-imported rather than reusing this test process's
     already-loaded class) attempting the same fabricated construction that
     the guard rejects — the guard-disabled subprocess let it through for
     real (`CONSTRUCTED_WITHOUT_RAISING`), confirming the guard is
     load-bearing, not decorative; restored byte-identical
     (`module_path.read_text() == original` asserted directly), re-ran clean.
- **Regression check:** `uv run ruff check .` / `ruff format --check .` /
  `mypy .` clean; `pnpm run lint` / `format:check` / `typecheck` clean; all 9
  existing task negative-test-proof scripts
  (`test_ftf_0001_monorepo_layout.py` through `test_ftf_r001.py`) still pass
  unmodified (ran `pnpm install --frozen-lockfile` first — this fresh
  container had no `node_modules`, unrelated to this change, same
  environment note as every prior shift); `uv run pytest tests/unit` (3
  passed) and `pnpm exec vitest run` (2 passed) both clean, 0 skipped either
  side; `product_truth_check.py --fail-on-unwired` stays `0/0 product-true`
  — legitimate, `proving_symbol` deliberately empty (this is a preflight/
  release-scope-freeze layer, not a live product-runtime path wired to a
  UI/API caller yet, same rationale as FTF-0002/0003/H001/R001). No
  already-certified task's own implementation file was touched — only
  additive modules/scripts/docs, avoiding any Law 15 requalification
  question for FTF-0001–0006/H001/R001's existing certified evidence.
  `python3 scripts/release_scope_lock.py` and
  `python3 scripts/generate_compatibility_lock.py` both re-run clean against
  the real committed artifacts.
- **CI:** wired `python3 scripts/release_scope_lock.py` and `python3
  tests/tasks/test_ftf_h002.py` into the existing `gate` job in
  `.github/workflows/ci.yml`, right after FTF-R001's step; YAML-validated
  (`yaml.safe_load`). Real run status to be confirmed on the opened PR before
  merge.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4. PR to be
  opened against `main` this same shift; not merging without confirming CI
  green first.

## 2026-09-16 — Control plane certification review — FTF-H002 (PR #49) — NOT CERTIFIED
- **Session-ID independence check:** this reviewing session is
  `session_01BNcWR4me2X2YrMtsW6uqfV`. Every commit on PR #49
  (`3f5e618`, `40b61d2`, `af2b0fa`, `3d235b4`) carries
  `Claude-Session: https://claude.ai/code/session_01KcK5jpotjXCpjrQj8qMPVp` —
  a different session. Independent reviewer confirmed (Law 4).
- **Diff re-read:** `apps/api/app/services/environment_preflight.py`,
  `scripts/release_scope_lock.py` + `spec/release-scope.yaml`,
  `scripts/generate_compatibility_lock.py` + `spec/compatibility-lock.json`,
  `docs/operations/budget-forecast.md`, `tests/tasks/test_ftf_h002.py` — all
  read directly against PR #49 head `3d235b4f484ad724b4252bd2aef7088181a14218`.
  Matches the queue entry's description; no unrelated files touched.
- **CI (real, re-checked on the actual PR head, not self-reported):**
  GitHub Actions run https://github.com/SJZLab/Sovra/actions/runs/35160635403
  on head `3d235b4` — `docker-local-stack-integration` succeeded, but the
  `gate` job **FAILED** at the `python3 tests/tasks/test_ftf_h002.py` step:
  `FAIL [FTF-H002] no kubectl is installed in this environment, so
  check_kubernetes_cni reports BLOCKED_EXTERNAL for real (not because it
  was told to)`. Root cause: GitHub-hosted `ubuntu-latest` runners ship
  `kubectl` preinstalled in the standard tool cache, so on the real CI
  runner `check_kubernetes_cni()` genuinely returns `AVAILABLE`, not
  `BLOCKED_EXTERNAL`. `test_kubernetes_cni_check_reflects_real_path_state`'s
  first assertion (`tests/tasks/test_ftf_h002.py` lines 158-165) calls
  `check_kubernetes_cni()` against the ambient, uncontrolled `PATH` and
  hard-assumes the negative branch, unlike its own positive-branch case
  a few lines below (and unlike `test_git_cli_check_reflects_real_path_state`,
  which correctly isolates *both* branches with a real empty-PATH temp
  directory). This is a genuine environment-dependent test bug, not a
  flake or an infra hiccup — it reproduced deterministically on this exact
  commit's only CI run so far. (Local runs in the relay's own sandbox pass
  because that sandbox happens to have no `kubectl` on `PATH` — that's the
  gap between "producer's own local run" and "real CI," exactly what this
  review step exists to catch.)
- **Fault-injection re-verification (independent, not the PR's own
  self-contained subprocess test):** confirmed `test_ftf_h002.py` genuinely
  fails when the feature is broken by an independent injection distinct
  from the one already in the test suite — reverted
  `check_managed_provider_bedrock`'s credential-present branch to report
  `AVAILABLE` from credential presence alone; re-ran
  `tests/tasks/test_ftf_h002.py` locally → 1 failure, exactly on that
  assertion; reverted the file back (`git diff` clean afterward). Confirms
  the test suite is load-bearing, independent of the CI failure above.
- **product_truth_check.py:** re-ran `--fail-on-unwired` → `0/0
  product-true`. Legitimate: `docs/spec-matrix.yaml`'s `FTF-H002` entry
  carries `proving_symbol: ''` (no live product-runtime caller claimed —
  this is a preflight/release-scope-freeze layer, same disclosed pattern
  as FTF-0002/0003/H001/R001), so 0/0 is a valid pass here, not a gap being
  waved through.
- **Task contract re-check:** `python tools/r1_handoff.py packet FTF-H002
  --plan FINE_TUNING_FACTORY_RELEASE_1.md` resolved against
  `docs/implementation/task-packets/FTF-H002.md` (committed in this PR).
  `acceptance`/`negative_acceptance` clauses verified against the delivered
  code: unknown prices/unavailable managed capabilities/stale LiteLLM
  routes/absent runtime integration are all genuinely disclosed as
  `BLOCKED_EXTERNAL` with real owner/next_action (not fabricated); no
  model ID/rate invented anywhere in `budget-forecast.md`; preflight reads
  `SOVRA_LITELLM_GATEWAY_URL` directly rather than through
  `Settings.from_env()`, matching "implement preflight independently of
  final qualification to avoid a dependency cycle." Also ran the packet's
  own `verification_commands` directly: `python scripts/validate_spec.py
  --strict` → clean; `python scripts/verify_task.py --task FTF-H002 --stage
  task` → `PASS` against evidence commit `40b61d2`. These clauses hold —
  the block below is on CI, not on the contract.
- **Verdict: certification withheld — do NOT merge PR #49.** This is a
  real, reproducible CI failure on the actual PR head, not a stale claim
  and not a flake (Law 1 requires evidence bound to the real commit; a red
  `gate` run is exactly what blocks a Law-1-compliant certification). Not
  an owner-decision item — this is a code/test bug for the relay to fix, so
  it is not added to QUEUE.md "Owner actions." See QUEUE.md "Provisional"
  for the routed fix instruction.
- **State:** proof_state stays L1-PROVEN, certification_state stays
  PROVISIONAL (not downgraded — the implementation and contract hold; only
  the test's environment assumption is wrong). `docs/spec-matrix.yaml` left
  unchanged. No ledger.md entry (nothing certified).
- **Validated-by:** session https://claude.ai/code/session_01BNcWR4me2X2YrMtsW6uqfV
  (independent — see session-ID check above), 2026-09-16.

## 2026-09-17 — FTF-H002 — CI fix confirmed, resubmitted for certification (superseding PR)
- **What changed since the NOT CERTIFIED review above:** a same-day
  follow-up commit on `claude/wizardly-fermat-brui19` (`419f9cc`, authored
  by the same producer session `session_01KcK5jpotjXCpjrQj8qMPVp` that
  opened PR #49 — a test fix, not a self-certification, so Law 4 is not
  implicated) rewrote
  `test_kubernetes_cni_check_reflects_real_path_state`'s first assertion
  to construct the negative branch explicitly via a real empty-PATH temp
  directory, exactly the pattern the review above named as the fix and
  the pattern `check_git_cli`'s negative branch and this same test's own
  positive branch already used. No other file changed in that commit.
- **This shift's independent re-check (session
  https://claude.ai/code/session_01BNQGXo9Q8t1PFdEmxQZXHF):** read the
  `419f9cc` diff directly (15 insertions, 3 deletions, one function
  body); confirmed via GitHub Actions run
  https://github.com/SJZLab/Sovra/actions/runs/35160854110 (queried live,
  not self-reported) that both the `gate` job (run
  105011005319) and `docker-local-stack-integration` job (run
  105011005022) completed with `conclusion: success` on head `419f9cc`
  — the exact commit this queue entry is bound to. No other change to
  the implementation, task contract, or any already-certified task's
  files since the NOT CERTIFIED review above (`git diff
  3d235b4..419f9cc` touches only the one test file).
- **Why this lands as a new PR instead of merging PR #49 directly:**
  attempting the merge (`gh`/GitHub merge API against PR #49) returned a
  real conflict — `main`'s tip had moved past PR #49's base to include
  the NOT CERTIFIED review commit itself (`81ebb41`, merged via PR #50),
  which edited the same two bookkeeping files (`QUEUE.md`,
  `docs/certifications/queue.md`) that PR #49's own bookkeeping commit
  (`3f5e618`) also touched — confirmed with a real trial merge
  (`git merge --no-commit --no-ff`), not assumed. Separately, this
  session's own git-branch policy requires developing only on
  `claude/wizardly-fermat-w4h9ys` and forbids pushing to any other
  branch, so pushing a conflict-resolution commit onto
  `claude/wizardly-fermat-brui19` itself was not available either way.
  Resolution: merged `main` into a local copy of `419f9cc` on
  `claude/wizardly-fermat-w4h9ys`, resolving both conflicts by keeping
  both sides' content (this file: kept both log entries in chronological
  order, as above; `QUEUE.md`: kept the current true state). Every other
  file in the merge was a clean fast-forward — byte-identical to
  `419f9cc`'s own tree, confirmed via `git diff` against the pre-merge
  tree touching only `QUEUE.md` and this file. Re-ran the full existing
  regression set against the merged tree before opening the new PR:
  `ruff check`/`format --check`/`mypy` clean; `pnpm run
  lint`/`format:check`/`typecheck` clean; all 9 task
  negative-test-proof scripts including `test_ftf_h002.py` (15 checks)
  pass; `pytest tests/unit` (3 passed) and `vitest run` (2 passed) clean;
  `product_truth_check.py --fail-on-unwired` stays `0/0` (unchanged,
  `proving_symbol` still deliberately empty); `validate_spec.py --strict`
  and `verify_task.py --task FTF-H002 --stage task` both pass.
- **Disposition of PR #49:** closed as superseded by the new PR — same
  commits' code (byte-identical), same CI-green evidence, same task; the
  new PR exists only because of the merge conflict and this session's
  branch constraint, not because of any change to the delivered work.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL
  (unchanged). Not self-certified — this is a resubmission for
  independent Control plane L2 review per Law 4, not a certification by
  this session.

## 2026-09-17 — FTF-H028 — Trusted CI and exact-revision release evidence gate
- **PR:** to be opened against `main` from `claude/wizardly-fermat-s54o5t`.
- **Commits:** `d502c89a1733c0dbff054e07e556f1a74d244c3b` (implementation),
  `183321c` (hash-fixup — same two-commit pattern as FTF-0006/FTF-H001/
  FTF-R001/FTF-H002, since a commit can't embed its own hash).
- **Real DoD source:** `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolved via
  `python tools/r1_handoff.py packet FTF-H028 --plan FINE_TUNING_FACTORY_RELEASE_1.md`.
  Dependencies FTF-0003/FTF-H001 both `CERTIFIED` — unblocked.
- **Why this task:** `docs/spec-matrix.yaml` swept directly for any item
  whose real `deps` were all CERTIFIED after FTF-H002 certified this same
  shift — FTF-H028 (deps FTF-0003, FTF-H001) was dependency-ready and
  matches QUEUE.md's own prior-shift note flagging it as the likely next
  candidate.
- **Scope finding before implementation:** this task's own acceptance
  clause ("verify actual branch protection through real repository
  settings") requires a real GitHub API read this session's credential
  cannot make — confirmed live, not assumed: a direct GET against
  `https://api.github.com/repos/SJZLab/Sovra/branches/main/protection`
  returned a real HTTP 403 `"Resource not accessible by integration"`.
  Section 52 itself names the correct handling: "Missing GitHub/GPU/
  provider access is BLOCKED_EXTERNAL and blocks full release" — so the
  branch-protection check is real (a genuine GET, real status-code
  branching, real required-setting validation on a 200), but honestly
  reports BLOCKED_EXTERNAL today rather than a fabricated PASS, following
  the exact precedent FTF-H002's `check_managed_provider_bedrock` already
  established for this repository. Release-manifest signing is likewise
  BLOCKED_EXTERNAL — Section 73.1's `evidence/release/manifest.json` and
  an approved signing service are not yet provisioned/built; disclosed as
  a later task's deliverable rather than inventing a fake signer (Law 11).
- **Delivered:**
  - `scripts/release_evidence_gate.py` (path refinement beyond the task's
    initial `files` list, disclosed in `docs/spec-matrix.yaml`'s notes —
    same rationale `scripts/spec_verification.py` already backs
    `scripts/verify_release.py`/`scripts/verify_task.py`): `GateResult`/
    `GateStatus` (PASS/FAIL/BLOCKED_EXTERNAL) mirroring FTF-H002's
    `PreflightResult` anti-fabrication guardrail (a BLOCKED_EXTERNAL result
    structurally requires a non-empty owner/next_action). Five real checks:
    `check_release_evidence_report` (every required check's real check-run
    report must exist, match the exact release SHA, be `completed`, and
    conclude `success` — missing/stale/incomplete/skipped/cancelled/
    timed_out/neutral/failure all rejected); `check_branch_protection` (a
    real GET against the branches/.../protection endpoint, with a 403/404
    honestly reported BLOCKED_EXTERNAL, and a real 200 validated against
    required review/required-status-check/no-force-push/no-deletion
    settings); `check_release_manifest_signature` (an injectable verifier —
    unsigned always fails, signed-with-no-configured-verifier is
    BLOCKED_EXTERNAL, never a fabricated PASS from presence alone);
    `check_required_workflows_have_no_forbidden_patterns` (a static regex
    scan of `.github/workflows/*.yml` for `continue-on-error: true`,
    `|| true`, a forced `; exit 0`, and test-focus markers — Section 52's
    named forbidden patterns); `check_no_untrusted_pull_request_target_checkout`
    (rejects a `pull_request_target`-triggered workflow that checks out
    `github.event.pull_request.head`, correctly handling PyYAML's `on:` ->
    boolean-`True` parsing quirk, confirmed empirically against the real
    `ci.yml` before relying on it).
  - `scripts/verify_release.py` wired to call the new gate behind an
    opt-in `--include-evidence-gate` flag — every existing caller
    (every prior shift/certifier invoking it with no flags) keeps its
    exact previous behavior/exit-code contract unchanged.
  - `.github/workflows/10-release-qualification.yml`: the real protected
    aggregator (`workflow_dispatch`-only — Tier 3/release-only per
    CLAUDE.md, the relay never dispatches it), `if: always()`, running the
    real evidence-gate CLI against the resolved release SHA plus the
    FTF-H028 negative-test-proof script.
  - `.github/CODEOWNERS`: the real repository owner/sole collaborator
    (`samerzakaria`, confirmed live via `get_me`/
    `list_repository_collaborators` — role `admin`) assigned to every
    protected gate path (`.github/workflows/`, this file itself, the
    spec/gate/release-evidence verifiers, the release-scope/compatibility/
    architecture locks, `CLAUDE.md`), per Section 84.6's "the
    implementation agent cannot self-approve changes to gates."
- **Test:** `tests/tasks/test_ftf_h028.py` — 33 checks.
- **Evidence — shown able to fail (Law 1):**
  1. Every check's real positive/negative branches proven via fixtures:
     missing check, stale/mismatched head_sha, incomplete run, all seven
     non-passing conclusions, a real PASS case (`check_release_evidence_report`);
     no-token, 403/404, missing-settings, all-settings-enabled
     (`check_branch_protection`); no-manifest, unsigned, signed-with-no-verifier,
     valid vs. tampered signature via an injected verifier
     (`check_release_manifest_signature`); each of the four forbidden
     patterns individually, a clean fixture, and an empty-report-fails-closed
     case (`check_required_workflows_have_no_forbidden_patterns`); an
     untrusted `pull_request_target` fixture rejected and a safe one
     accepted (`check_no_untrusted_pull_request_target_checkout`).
  2. **Real, live evidence, not only a fixture:** a genuine network call
     from inside the test to the real
     `api.github.com/repos/SJZLab/Sovra/branches/main/protection` endpoint,
     confirming this session's own credential is denied (403/404) today —
     matching the module's own docstring finding, reproduced independently
     inside the test rather than only asserted in prose.
  3. **Two independent real fault injections**, both restored
     byte-identical: (a) in-test, temporarily disabling
     `FORBIDDEN_WORKFLOW_PATTERNS` on the live imported module and
     confirming a real fixture that should be caught wrongly passes, then
     restoring and re-confirming detection is back; (b) external to the
     test file (run separately this shift, not embedded), disabling the
     `stale_commit`/head_sha-mismatch branch in the tracked
     `check_release_evidence_report` — the stale-head-sha test failed for
     real, restored via `diff`-confirmed byte-identical copy, re-ran clean.
  4. `overall_status`/`run_release_evidence_gate` composition proven:
     BLOCKED_EXTERNAL when any check is blocked, FAIL when any check
     fails, PASS only when every check passes; the real end-to-end call
     with no token/manifest (this sandbox's exact real state) never
     reports overall PASS.
- **Regression check:** full local gate re-run clean — `uv run ruff
  check`/`format --check`/`mypy .`; `pnpm run lint`/`format:check`/
  `typecheck`; all 10 existing task negative-test-proof scripts
  (`test_ftf_0001_monorepo_layout.py` through `test_ftf_h002.py`, now
  joined by `test_ftf_h028.py`) pass unmodified — no already-certified
  task's own implementation file was touched, only additive
  modules/workflow/CODEOWNERS, avoiding any Law 15 requalification
  question; `pytest tests/unit` (3 passed) and `vitest run` (2 passed)
  clean, 0 skipped either side; `validate_spec.py --strict`,
  `release_scope_lock.py`, both clean; `verify_task.py --task FTF-H028
  --stage task` → `PASS` against evidence commit `d502c89`.
  `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, no `proving_symbol` (CI/gate tooling layer, no live
  product-runtime caller, same rationale as every prior FTF-H00X/FTF-R00X
  task). `status.py` → `9/300 certified · 0 product-true` (unchanged —
  PROVISIONAL is not certified).
- **CI:** wired `python3 tests/tasks/test_ftf_h028.py` into
  `.github/workflows/ci.yml`'s existing `gate` job, right after the
  FTF-H002 step (YAML-validated with `yaml.safe_load`). Real run status to
  be confirmed on the opened PR before merge.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4, which
  should specifically confirm: (a) the real 403 branch-protection finding
  reproduces independently rather than being taken on this session's word,
  (b) `check_release_evidence_report`'s stale-SHA rejection actually
  distinguishes a report for the wrong commit (not merely "a report
  exists"), and (c) no check silently treats BLOCKED_EXTERNAL as
  equivalent to PASS anywhere in the composition.

## 2026-09-17 — FTF-H034 — Safe parallel scheduler and task/evidence ownership
- **PR:** to be opened against `main` from `claude/wizardly-fermat-twhvs6`.
- **Commit:** `7109100c9bfaff995a94203720e9161e095cb7bf` (implementation),
  hash-fixup to follow once this commit exists in history (same two-commit
  pattern as FTF-0006/FTF-H001/FTF-R001/FTF-H002/FTF-H028).
- **Real DoD source:** `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolved via
  `python tools/r1_handoff.py packet FTF-H034 --plan FINE_TUNING_FACTORY_RELEASE_1.md`.
  Dependencies FTF-H001, FTF-H002 both `CERTIFIED` — unblocked.
- **How this task was found:** `docs/spec-matrix.yaml`'s `deps=` field is
  empty for every not-yet-started task (it only tracks proof-state, not
  dependencies — the real dependency graph lives in `docs/TASK_INDEX.md`'s
  table, per CLAUDE.md's own "Full 300-record dependency graph" note).
  Swept `docs/TASK_INDEX.md` directly for any non-certified task whose
  real `Dependencies` column is entirely CERTIFIED: found FTF-0102
  (already blocked on the standing new-runtime-dep owner ask), FTF-1810,
  FTF-H034, FTF-R011, and FTF-R017. Picked FTF-H034: unlike FTF-R011
  (whose `files` include `spec/deployment-profiles.yaml`, a file
  FTF-H002's own certified evidence already delivered — editing it would
  raise a Law 15 requalification question) it only adds new files plus the
  two never-actually-updated tracking JSON files (`spec/tasks.json`,
  `docs/implementation/TRACKER.json`), avoiding any regression risk to
  already-certified evidence.
- **Scope finding before implementation:** this task's acceptance clause
  asks to "execute actual safe parallel wave." This relay session has no
  live multi-agent dispatch harness able to hand a task to a second, truly
  separate worker process today. Checked this task's own
  negative_acceptance before treating it as a blocker: "If runner lacks
  real subagent capability record orchestration blocker, never fabricate
  workers; plan steps requiring future services wait rather than fake
  integration" — this is the same resource_gate-style honest-disclosure
  pattern FTF-H002 used for GPU/Bedrock and FTF-0004 used for registry
  egress, not a DoD-clarity gap. Built and evidenced the parallel-wave
  proof at the level this sandbox can genuinely exercise instead: a real
  8-OS-thread race against the registry's own locking (a `threading.Barrier`
  forces genuinely simultaneous contention, not a fabricated sequential
  loop), asserting exactly one thread wins and the registry's state stays
  consistent afterward. Disclosed here rather than hidden.
- **Delivered:** `scripts/task_claim.py` — `TaskClaimRegistry`:
  - DAG readiness: a claim is denied (`DEPENDENCY_NOT_READY`) unless every
    dependency is `CERTIFIED` in `docs/spec-matrix.yaml`.
  - Overlapping claims: a task ID with an active claim cannot be claimed
    again (`TASK_ALREADY_CLAIMED`).
  - Expiring claims: every claim carries a real TTL; `complete()` on an
    expired claim is denied (`CLAIM_EXPIRED`) rather than silently
    accepted — "deny... stale completion."
  - Write-set and named-lock exclusivity: two active claims whose
    write-sets or named locks (e.g. a shared `root:quality-contract` or
    migration-chain lock) overlap are mutually exclusive
    (`WRITE_SET_CONFLICT` / `LOCK_CONFLICT`); released the moment the
    holder completes.
  - Bounded concurrency: the constructor refuses a configured limit above
    the hard ceiling of 6; the default limit of 4 denies a 5th concurrent
    claim (`CONCURRENCY_LIMIT_EXCEEDED`).
  - A GPU quota independent of general concurrency
    (`GPU_QUOTA_EXCEEDED`) — bookkeeping only, no real GPU exists in this
    sandbox to exercise scheduling against, same disclosed limit
    FTF-H002 used for its own GPU/Bedrock checks.
  - Deterministic merge order: `merge_order()` sorts by real claim
    creation time then task ID, independent of completion arrival order.
  - Stale-verification invalidation: completing a claim marks any earlier
    completion whose write-set overlaps it as stale — "invalidate stale CI
    after integration change."
  - Independent review: `complete()` structurally rejects a certifier
    identical to the claim's own claimant (`SELF_CERTIFICATION_DENIED`) —
    Law 4, the producer never certifies its own work.
  - `sync_certified_status()`: fixes a disclosed, pre-existing gap —
    `spec/tasks.json` and `docs/implementation/TRACKER.json` have carried
    every task as `NOT_STARTED`/empty evidence since the original
    bootstrap, contradicting their own header comments ("actual status,
    claims/leases, commits..."). Ran it once against the real repo files:
    both now genuinely show `status: CERTIFIED` and the real
    `evidence_hash` for exactly the 10 tasks `docs/spec-matrix.yaml`
    already shows CERTIFIED, and nothing else — no claim/lease fabricated
    for any not-yet-certified task. Diff limited to the `status`/`evidence`
    fields of those 10 entries (confirmed via `git diff`, using
    `ensure_ascii=False` to avoid a spurious repo-wide non-ASCII escaping
    diff from Python's default `json.dumps`).
- **Test:** `tests/tasks/test_ftf_h034.py` — 17 check functions covering
  every acceptance/negative_acceptance clause above via real fixtures.
- **Evidence — shown able to fail (Law 1):**
  1. Every acceptance/negative_acceptance branch above has a fixture-based
     positive and negative case (dependency readiness, overlapping claims,
     write-set/lock conflicts and their release on completion, the
     concurrency ceiling and ValueError on a config above it, the GPU
     quota being independent of general concurrency, stale completion
     denial via a fake, controllable clock, self-certification denial,
     claimant-mismatch denial, deterministic merge order fed completions
     in randomized arrival order, and stale-verification invalidation on
     an overlapping later completion).
  2. **Real safe-parallel-wave proof:** 8 real OS threads
     (`threading.Thread`) synchronized on a `threading.Barrier` so they
     hit `registry.claim()` for the *same* task ID at genuinely the same
     instant — asserted exactly 1 wins, the other 7 are denied
     `TASK_ALREADY_CLAIMED` specifically (not some other/undefined
     outcome), and the registry's own active-claim count is exactly 1
     afterward. This is real, OS-scheduled concurrency, not a simulated
     sequential loop.
  3. **Real fault injection against the tracked module itself:** removed
     the `certifier == claimant` guard block from `scripts/task_claim.py`,
     confirmed the exact block was present beforehand and the file
     genuinely changed, ran a **fresh subprocess** (new interpreter, so
     the edited file is actually re-imported) attempting the same
     self-certified completion — it succeeded for real
     (`CONSTRUCTED_WITHOUT_RAISING`) where the unmodified guard rejects
     it; restored byte-identical (`module_path.read_text() == original`
     asserted directly), re-ran clean (all 17 checks pass again).
  4. **Real fault injection performed independently by this shift outside
     the committed test suite** (ad hoc, not part of the merged evidence,
     recorded here for the record per Law 1): neutered the write-set/lock
     overlap-check loop body in a working copy — `test_lock_conflict_denied`
     failed for real (an uncaught `TASK_ALREADY_CLAIMED` exception
     propagated, since the neutered check let a conflicting claim through
     that the test's own control flow didn't expect); restored
     byte-identical from a pre-edit copy, re-ran clean.
  5. **Real, not fabricated, "actual status" fix:** before this task,
     `spec/tasks.json` and `docs/implementation/TRACKER.json` genuinely
     showed `"status": "NOT_STARTED"` for all 10 already-CERTIFIED tasks
     (verified directly, not assumed) — after running
     `sync_certified_status()` once against the real repo files, both
     genuinely show `"status": "CERTIFIED"` with the real evidence hash,
     confirmed both by direct file inspection and by
     `test_real_committed_tracker_and_tasks_reflect_actual_certified_set`.
- **Regression check:** full local gate re-run clean — `uv sync`,
  `pnpm install --frozen-lockfile` (fresh container, no `node_modules`,
  same environment note as every prior shift); `uv run ruff
  check`/`format --check`/`mypy .` clean; `pnpm run
  lint`/`format:check`/`typecheck` clean; all 11 task
  negative-test-proof scripts (`test_ftf_0001_monorepo_layout.py` through
  `test_ftf_h028.py`, now joined by `test_ftf_h034.py`) pass — no
  already-certified task's own implementation file was touched beyond the
  disclosed, additive `status`/`evidence` fields in `spec/tasks.json` and
  `docs/implementation/TRACKER.json`; `uv run pytest tests/unit` (3
  passed) and `pnpm exec vitest run` (2 passed) clean, 0 skipped either
  side; `validate_spec.py --strict` clean; `verify_task.py --task
  FTF-H034 --stage task` → `PASS` against evidence commit `7109100`;
  `verify_release.py` correctly non-inflated (`10/300`, `FAILED (1):
  ['FTF-H034']`, `NOT READY` — PROVISIONAL is not CERTIFIED);
  `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, no `proving_symbol` (scheduler/tracking tooling, no live
  product-runtime caller, same rationale as every prior FTF-H00X/FTF-R00X
  task).
- **CI:** wired `python3 tests/tasks/test_ftf_h034.py` into
  `.github/workflows/ci.yml`'s existing `gate` job, right after the
  FTF-H028 step (YAML-validated with `yaml.safe_load`). Real run status to
  be confirmed on the opened PR before merge.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4, which
  should specifically confirm: (a) the 8-thread parallel-wave test is
  actually exercising real OS-level concurrency and not a scheduling
  fluke that happens to pass, (b) the `sync_certified_status` diff to
  `spec/tasks.json`/`TRACKER.json` is limited to exactly the 10 already-
  independently-certified tasks' `status`/`evidence` fields and nothing
  else, and (c) the disclosed GPU-quota and no-live-subagent-harness
  limitations match this task's own negative_acceptance rather than
  quietly narrowing its scope.

## 2026-09-17 — FTF-1810 — ReleaseQualificationWorkflow
- **PR:** to be opened against `main` from `claude/wizardly-fermat-g3s0o0`.
- **Commit:** e7ddf75f0bcdeaf99f1b92ee5b1bc52fc74b650a (implementation),
  1a98057 (hash-fixup — same two-commit pattern as FTF-0006/FTF-H001/
  FTF-R001/FTF-H002/FTF-H028/FTF-H034).
- **Real DoD source:** `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolved via
  `python tools/r1_handoff.py packet FTF-1810 --plan FINE_TUNING_FACTORY_RELEASE_1.md`.
  Dependencies FTF-H001, FTF-H028 both `CERTIFIED` — unblocked (FTF-R011,
  FTF-R017, and FTF-R014 also became dependency-ready this shift once
  FTF-H034 certified, per `docs/spec-matrix.yaml`'s own `notes: deps=`
  field; FTF-1810 was picked as the prior shift's own flagged next
  candidate and because its scope is bounded tooling, not a new
  runtime dependency).
- **Scope finding before implementation:** the task's acceptance clause
  ("run the authoritative Q01-Q43 aggregator on exact release; bind result
  to current scope/hash/image/profile") does not require this shift to
  actually execute the 43 qualification scenarios (Section 54's full E2E
  program) — no product exists yet to run them against. It requires
  building the real, fail-closed binder/aggregator tooling itself, matching
  this repo's established resource_gate precedent (FTF-H002/FTF-H028): a
  real check that honestly reports BLOCKED_EXTERNAL/FAIL against today's
  actual repo state is the correct evidence, not a fabricated PASS.
- **Delivered:**
  `docs/operations/releasequalificationworkflow.py` — `ReleaseBinding`
  (scope_digest = sha256 of the real `spec/release-scope.yaml`, commit_sha
  = real `git rev-parse HEAD`, image_digest/deployment_profile = `None`
  when that real input doesn't exist yet, never a placeholder string).
  `evaluate_scenario_evidence` checks one scenario's evidence against a
  per-release ledger (`spec/qualification-evidence.json` — absent/empty
  today) and fails closed on, in order: missing, skipped, incomplete
  (missing `evidence_id`/`commit`/`exit_code`), stale (commit mismatch),
  nonzero `exit_code`, unsigned (no signature), a present signature with no
  configured verifier (`BLOCKED_EXTERNAL` — never a fabricated PASS from
  presence alone, same discipline as FTF-H028's
  `check_release_manifest_signature`), and tampered (verifier rejects the
  signature) — only a genuinely verified, exact-revision-bound, signed
  entry reaches `PASS`. `run_release_qualification_workflow` additionally
  gates the whole run on a real image digest and a real resolved
  deployment profile, both `BLOCKED_EXTERNAL` when absent. Reuses
  `scripts/release_evidence_gate.py`'s `GateResult`/`GateStatus`
  (including its `BLOCKED_EXTERNAL` requires-owner/next_action
  anti-fabrication guardrail) rather than reimplementing it.
  `tests/tasks/test_ftf_1810.py` (16 checks).
- **Evidence — shown able to fail (Law 1):**
  1. Every named rejection reason (missing/skipped/incomplete/stale/
     nonzero-exit/unsigned/tampered) is proven via a real fixture, plus the
     present-signature-no-verifier `BLOCKED_EXTERNAL` branch and the
     genuinely-valid-evidence `PASS` branch.
  2. Missing image digest / unresolved deployment profile each produce
     their own real `BLOCKED_EXTERNAL` result, not a silent skip.
  3. `overall_status` is proven to stay non-`PASS` while any required
     scenario is missing, and to reach `PASS` only once every required
     scenario in a 2-item fixture set is genuinely verified.
  4. **Not fabricated, proven against the real repo:** `current_commit_sha()`
     returns the real 40-character HEAD SHA; `load_required_scenarios()`
     reads the real, shipped `spec/qualification-inventory.json` (43
     required scenarios, confirmed by count, not assumed); running the real
     aggregator against the real repo with no evidence ledger populated
     reports `overall_status != PASS` and every one of the 43 real
     scenarios `missing` — the honest state today.
  5. **Real fault injection against the tracked module, committed in the
     test:** removed the `skipped`-check block — the real skipped-scenario
     fixture wrongly passed; restored byte-identical (`diff` confirmed
     empty via direct read-back), re-ran clean.
  6. **A second, independent fault injection performed ad hoc this shift
     (not committed, per Law 1's "shown able to fail" — reproduced here for
     the record):** removed the `stale`-commit check block entirely (distinct
     from the committed injection, which targets `skipped`) — exactly 1 test
     failed for real (`a scenario evidence entry bound to a different commit
     than the release is FAIL (stale)`); restored byte-identical from a
     pre-edit `/tmp` copy, `diff` confirmed empty, re-ran clean (16/16 pass).
- **Regression check:** `uv run ruff check .` / `ruff format --check .` /
  `mypy .` clean; `pnpm run lint`/`format:check`/`typecheck` clean (after
  `pnpm install --frozen-lockfile` — fresh container, no `node_modules`,
  same environment note as every prior shift); all 12 existing task
  negative-test-proof scripts (`test_ftf_0001_monorepo_layout.py` through
  `test_ftf_h034.py`, now joined by `test_ftf_1810.py`) pass — no
  already-certified task's own implementation file was touched beyond the
  disclosed, additive `docs/spec-matrix.yaml`/`spec/verification-map.json`
  entries for FTF-1810 itself, avoiding any Law 15 requalification
  question; `uv run pytest tests/unit` (3 passed) and `pnpm exec vitest run`
  (2 passed) clean, 0 skipped either side. `validate_spec.py --strict`
  clean; `verify_task.py --task FTF-1810 --stage task` → `PASS` against
  evidence commit `e7ddf75f0bcdeaf99f1b92ee5b1bc52fc74b650a`.
  `verify_release.py` correctly non-inflated: `11/300`/`FAILED (1):
  ['FTF-1810']`/`NOT READY` (FTF-1810 is PROVISIONAL, not yet CERTIFIED).
  `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, no `proving_symbol` (release-qualification aggregator
  tooling itself, no live product-runtime caller, same rationale as every
  prior FTF-H00X/FTF-R00X task).
- **CI:** wired `python3 tests/tasks/test_ftf_1810.py` into `.github/
  workflows/ci.yml`'s `gate` job, right after the FTF-H034 step, and the
  real `docs/operations/releasequalificationworkflow.py` itself (plus its
  own negative-test-proof step) into `.github/workflows/
  10-release-qualification.yml` — the same Tier 3, `workflow_dispatch`-only
  protected aggregator FTF-H028's `release_evidence_gate.py` step already
  runs in, never triggered by an ordinary PR and never dispatched by the
  relay. YAML-validated (`yaml.safe_load`). Real run status to be confirmed
  on the opened PR before merge. Note for Control plane: running this new
  step on the real aggregator workflow will genuinely fail today (every
  scenario missing, image/profile `BLOCKED_EXTERNAL`) — this is the correct,
  honest behavior for a Tier-3 release-only aggregator with no qualified
  product yet, not a defect to fix before certifying FTF-1810's own L1
  evidence.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.

## 2026-09-17 — FTF-R014 — Bind selected coding executor to the embedded Release 1 execution controls
- **PR:** https://github.com/SJZLab/Sovra/pull/68 (`claude/wizardly-fermat-czj1h6` → `main`), merged as `222c665843da206929df50a3857f659a10ec5dd6` after real green CI (`gate` + `docker-local-stack-integration`, run 35203455799, on head `cc9b7e2`) and no open review threads.
- **Commits:** `fc1822903741fae188b7b81767a7668b3ee9cc96` (implementation),
  `5592d07` (hash-fixup — same two-commit pattern as every prior
  FTF-H00X/FTF-R00X/FTF-1810 task).
- **Real DoD source:** `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolved via
  `python tools/r1_handoff.py packet FTF-R014 --plan FINE_TUNING_FACTORY_RELEASE_1.md`.
  Dependencies `FTF-R001`, `FTF-H034`, both `CERTIFIED` — unblocked.
- **Why this task:** swept `docs/TASK_INDEX.md`'s real `Dependencies` column
  directly against the certified set (FTF-0001–0006, FTF-H001, FTF-R001,
  FTF-H002, FTF-H028, FTF-H034, FTF-1810 — 12 tasks) for every non-certified
  task whose dependencies are entirely certified: found `FTF-0102` (already
  blocked on the standing owner ask, unchanged this shift — see QUEUE.md),
  `FTF-R011`, `FTF-R014`, `FTF-R017`. Picked `FTF-R014` over `FTF-R011`
  specifically: `FTF-R011`'s own `files`/`shared_lock_required` touch
  `spec/deployment-profiles.yaml`, evidence already delivered under
  `FTF-R001`'s certification — extending it is plausibly safe (additive
  `assertion_applicability` keys only) but was not attempted this shift
  without deliberately confirming the addition stays purely additive first,
  and `FTF-R014` carries no such shared-lock/already-certified-file
  question (`shared_lock_required: []`, its own `files` are all new paths).
  Picked over `FTF-R017` (real Docker/SeaweedFS storage-semantics
  integration, a heavier resource-gate surface) for the same
  keep-this-shift's-scope-bounded reason — not rejected, left for a future
  shift.
- **Scope finding before implementation:** this task's own
  `qualification_scenarios` are `Q33` and `Q41` only, not `Q42`. `Q33`'s
  real safe-parallel-wave evidence already exists — `FTF-H034`'s
  `scripts/task_claim.py`, proven with 8 genuinely racing OS threads,
  disclosed there as bounded by this sandbox having no live multi-agent
  dispatch harness able to hand a task to a second, truly separate worker
  process. This task does not re-claim or re-prove `Q33`; it implements
  `Q41`'s own, narrower scope only (selected-executor identity, digest
  binding, non-self-certification, protected-gate ownership) — a
  resource_gate to build honestly around, matching this repo's established
  precedent (`FTF-H002`/`FTF-H028`/`FTF-H034`), not a DoD-clarity gap.
  Confirmed no installed Agentic SDLC skill artifact exists in this
  repository (grepped for `v20.1`/"Agentic SDLC" — only the spec's own
  references, no adopted artifact) before recording `R1_EMBEDDED` (not
  `R1_EMBEDDED_PLUS_EXISTING_SKILL`) as the real, current decision.
- **Delivered:**
  - `tools/implementation/executor_contract.py`: `ExecutorReceipt`/
    `ExternalSkill` dataclasses and `validate_receipt()`. Digests
    (`source_file_digest`, `embedded_section_digest`) are computed live
    against the real, current `spec/RELEASE_1.md` and its Section 84 text
    on every call via `compute_expected_digests()` — never a pasted-once
    value. Enforces every negative_acceptance clause: missing/mismatched
    embedded-control digest; fabricated external-skill conformance
    (`R1_EMBEDDED` must carry `external_skill: null`;
    `R1_EMBEDDED_PLUS_EXISTING_SKILL` must carry a real, complete one);
    `SELF_CERTIFICATION_DENIED` when `validator_identity ==
    producer_identity` (same Law 4 rule `scripts/task_claim.py` already
    enforces); task isolation (`workspace_identity` cannot be `"main"` or
    empty); producer mutation of protected gates/signing material (a
    receipt whose `changed_files` touches a real path from the live
    `.github/CODEOWNERS` file must be validated by that file's actual sole
    reviewer, `owner:samerzakaria`, confirmed live in the FTF-H028 shift via
    `get_me`/`list_repository_collaborators` — not by an ordinary
    producer/validator pair); and claimed OpenHands qualification without
    OpenHands actually selected and run. Missing optional external skill
    does not block `R1_EMBEDDED`, per the same clause. Reused
    `scripts.architecture_lock.validate_instance` (FTF-R001's own bounded,
    dependency-free JSON Schema validator) to enforce
    `spec/implementation-executor.schema.json` rather than adding the
    `jsonschema` package as a new runtime dependency, and
    `scripts.release_evidence_gate`'s `GateResult`/`GateStatus` for the
    result shape — same reuse discipline FTF-1810 used.
  - `spec/implementation-executor.schema.json`: the frozen JSON Schema for
    Section 77.5's `ImplementationExecutorDecision` entity. Added to
    `.github/CODEOWNERS` alongside the new validator module itself as
    protected gate paths (Section 84.6: "after approval their hashes and
    scope are protected").
  - `docs/implementation/executor-decision.md`: this repository's actual,
    real decision record (executor, model route, `R1_EMBEDDED` profile
    rationale, digest-binding mechanism, Q33/Q42 scope disclosure).
  - No Factory production service (`apps/api`, any `workers/*` package)
    imports this module — confirmed via `grep`, matching this task's own
    acceptance clause ("no Factory runtime dependency on any coding-agent
    tooling").
- **Test:** `tests/implementation/test_r1_execution_binding.py` — 24 checks.
- **Evidence — shown able to fail (Law 1):**
  1. `compute_expected_digests()`'s output is checked against an
     independently recomputed sha256 (plain `hashlib`/regex, written
     separately in the test file itself) of the real, current
     `spec/RELEASE_1.md` and its actual Section 84 text — not trusted as a
     canned value.
  2. Every negative_acceptance case is exercised directly: stale
     `source_file_digest`/`embedded_section_digest` (each independently,
     confirming the section digest is checked even when the source digest
     is correct); `R1_EMBEDDED` fabricating an `external_skill`;
     `R1_EMBEDDED_PLUS_EXISTING_SKILL` missing one; `ExternalSkill`
     rejecting an empty field at construction; self-certification;
     unscoped `workspace_identity` (`"main"` and `""`); a protected-gate-
     touching receipt rejected when validated by an ordinary session and
     accepted when validated by the real release authority; an ordinary
     (non-protected) file change requiring no such authority; a claimed
     OpenHands qualification without a real run; and an unknown
     `execution_control_profile` value rejected by the real schema `enum`
     (not a hand-picked string comparison).
  3. **Real fault injection against the tracked module itself (not just
     fixtures):** removed the self-certification guard block from
     `tools/implementation/executor_contract.py` on disk, ran a fresh
     subprocess that imports the edited file and validates a
     self-certified receipt — it wrongly `PASS`ed; restored the file
     byte-identical from the in-memory original (`diff`-equivalent string
     comparison asserted directly in the test), re-ran the same subprocess
     probe — correctly `FAIL`ed again.
- **Regression check:** `uv run ruff check .`/`format --check .`/`mypy .`
  clean; `pnpm run lint`/`format:check`/`typecheck` clean (after `pnpm
  install --frozen-lockfile` — fresh container, no `node_modules`, same
  environment note as every prior shift); all 12 existing task
  negative-test-proof scripts (`test_ftf_0001_monorepo_layout.py` through
  `test_ftf_1810.py`) pass unmodified — no already-certified task's own
  implementation file was touched, only additive files plus FTF-R014's own
  `docs/spec-matrix.yaml`/`spec/verification-map.json` entries and two new
  `.github/CODEOWNERS` lines protecting this task's own new gate files,
  avoiding any Law 15 requalification question; `uv run pytest tests/unit`
  (3 passed) and `pnpm exec vitest run` (2 passed) clean, 0 skipped either
  side. `validate_spec.py --strict` clean; `verify_task.py --task FTF-R014
  --stage task` → `PASS` against evidence commit
  `fc1822903741fae188b7b81767a7668b3ee9cc96`. `verify_release.py` correctly
  non-inflated: `12/300`/`FAILED (1): ['FTF-R014']`/`NOT READY`.
  `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, no `proving_symbol` (build/implementation tooling, no live
  Factory product-runtime caller, same rationale as every prior FTF-H00X/
  FTF-R00X task).
- **CI:** wired `python3 tests/implementation/test_r1_execution_binding.py`
  into `.github/workflows/ci.yml`'s `gate` job, right after the FTF-1810
  step; YAML-validated (`yaml.safe_load`). Real run status to be confirmed
  on the opened PR before merge.
- **Note for Control plane — unrelated finding from this same shift, not
  part of this task's own evidence:** an open PR (#67, from a different
  prior session on this account) claims a live-session owner approval for
  the separate, still-open FTF-0102 new-runtime-dependency ask and edits
  QUEUE.md to remove it. This shift did not treat that claim as confirmed
  (no live user input this shift, no GitHub-visible corroboration on the
  PR itself), did not merge it, and left FTF-0102 blocked exactly as
  QUEUE.md already stated. Commented on PR #67 explaining this. Flagging
  here in case Control plane's own review touches that PR before the owner
  or a future live session resolves it directly — not a defect in this
  task's own delivered work.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.

## 2026-09-17 — FTF-R015 — Enforce isolated concurrent coding execution and independent integration
- **Commit:** `a1771c2c197cd2194cdb66d0963884182c180ee5` (implementation),
  `87c931e` (hash-fixup bookkeeping — same two-commit pattern as
  FTF-0006/FTF-H001/FTF-R001/FTF-H002/FTF-H028/FTF-H034/FTF-1810/FTF-R014,
  since a commit can't embed its own hash).
- **Real DoD source:** `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolved via
  `python tools/r1_handoff.py packet FTF-R015 --plan FINE_TUNING_FACTORY_RELEASE_1.md`.
  Dependencies FTF-R014, FTF-H028 both `CERTIFIED` — unblocked (FTF-R014
  certified earlier this shift's own prior review cycle, making this task
  newly dependency-ready).
- **Why this task, not FTF-R011/FTF-R017 (both also dependency-ready):**
  FTF-R011's own `files`/`shared_lock_required` list `spec/deployment-
  profiles.yaml` directly, territory FTF-H002's already-certified evidence
  delivered — editing it raises a Law 15 requalification question flagged
  and deferred across multiple prior shifts, not re-litigated here.
  FTF-R017 needs real Docker/SeaweedFS storage-semantics integration, a
  heavier external-resource surface. FTF-R015's own `files`
  (`tools/implementation/workspace_manager.py`, `executor_adapter.py`,
  `task_orchestrator.py`, `tests/implementation/
  test_safe_parallel_executor.py`) only *read* `spec/deployment-
  profiles.yaml` as a shared contract (`shared_lock_required: []`, verified
  directly from its own resolved packet) and touch no already-certified
  task's own evidence files — the most self-contained of the three, same
  reasoning that picked FTF-R014 over FTF-R011 in an earlier shift.
- **Scope finding before implementation:** Section 84.5's own text is
  explicit — "when the selected host cannot genuinely run parallel
  workers, record `BLOCKED_EXTERNAL` for parallel qualification and
  continue safe serial tasks as partial progress... do not fabricate
  parallel transcripts." This sandbox has no live multi-agent dispatch
  harness able to hand a task to a second, truly separate coding-host
  session/process — the same disclosed limitation FTF-H034 and FTF-R014
  already recorded. Q42's "overlapping execution of independent tasks" is
  therefore proven at the level this sandbox can genuinely exercise: real
  OS threads, each bound to its own isolated filesystem workspace and
  registry claim, synchronized on a `threading.Barrier` so their command
  executions provably overlap in wall time — not a fabricated claim that
  distinct external agent processes ran. True separate-host-session
  concurrency stays `BLOCKED_EXTERNAL`, disclosed here, not hidden.
- **Delivered:**
  - `tools/implementation/workspace_manager.py`: `WorkspaceManager` binds
    one real, genuinely new (never reused/stale) filesystem directory per
    task to one `scripts.task_claim.TaskClaimRegistry` claim — composition,
    not reimplementation, of FTF-H034's already-certified dependency-
    readiness/write-set/lock/TTL/concurrency/GPU-quota/non-self-
    certification logic. Adds exactly what that registry never covered:
    genuine per-task filesystem isolation and `NestedDelegationDenied`
    (opening a workspace from inside another active workspace's own
    execution context is refused structurally via a `threading.local`
    depth counter, not merely discouraged).
  - `tools/implementation/executor_adapter.py`: `execute_task` runs one
    real command per task inside its isolated workspace via a genuine
    `subprocess.run` call — real exit code/stdout/stderr/wall-clock
    timestamps, never simulated. `PermissionBypassDenied` rejects any
    command whose leading argv tokens aren't in a small explicit allowlist
    (`python3`, `git`) before a subprocess is ever spawned — "confirmation
    prompts are not security boundaries... test... direct-call paths"
    (Section 81.3) enforced structurally.
  - `tools/implementation/task_orchestrator.py`: `TaskOrchestrator.run_wave`
    adds the orchestration surface neither module above covers: intra-wave
    dependency ordering (a task listing an unfinished same-wave sibling as
    a dependency is deferred, not run — "unmerged dependencies"), a
    whole-wave budget ceiling (`BUDGET_EXCEEDED` — "whole-tree budgets"),
    protected-gate write denial (reusing FTF-R014's real CODEOWNERS-
    derived `load_protected_paths`; a task writing a protected path is
    denied `PROTECTED_GATE_WRITE_DENIED` unless the wave's reviewer is the
    real release authority — "producer writes to gate state"), a
    wave-level independent-reviewer gate (`SELF_CERTIFICATION_DENIED` when
    the reviewer is also one of the wave's own claimants, checked before
    any thread starts — Law 4, generalized from FTF-R014's single-receipt
    check to an entire wave), deterministic merge-order integration with a
    real, file-based "integrated CI" check (each approved task's own
    marker file — written by its real command — is copied into a shared
    integration directory in the registry's real deterministic merge
    order, then one more real subprocess verifies exactly the expected set
    is present; a task whose command never ran or whose CI failed leaves
    no marker and the integrated check genuinely fails for real, not by
    in-memory bookkeeping assumption), and CI-failure containment (a task
    whose own command exits nonzero is recorded `ci_failed` and never
    added to `approved_for_downstream` — "advancement after failed CI" is
    denied, while a genuinely passing sibling in the same wave still is).
- **Test:** `tests/implementation/test_safe_parallel_executor.py` — 32
  checks.
- **Evidence — shown able to fail (Law 1):**
  1. Acceptance clause 1 (real disjoint-task wave, isolated workspaces,
     independent integrated merge gate): a real 3-task wave completes with
     genuine wall-clock overlap (`threading.Barrier` forces simultaneous
     start; each task's own recorded `[started_at, finished_at]` interval
     is asserted to overlap every other's — `latest_start < earliest_finish`,
     not a coin flip: each command sleeps 0.2s before writing its marker,
     widening the overlap window past ordinary process-start jitter), all
     three approved for downstream advancement, and the integrated CI step
     genuinely passes.
  2. Real workspace isolation (not assumed): two tasks opened directly
     through `WorkspaceManager` get provably distinct directories; after
     each runs its real marker-writing command, workspace A's directory is
     asserted to contain only `R015-A.marker`, never `R015-B.marker`, and
     vice versa; closing without `keep_dir` is asserted to actually remove
     the directory from disk.
  3. `WRITE_SET_CONFLICT` — two tasks whose write-sets overlap cannot both
     open a workspace, reused directly from FTF-H034's registry.
  4. `CLAIM_EXPIRED` — completing a claim after an injected fake clock
     advances past its TTL is denied.
  5. "Unmerged dependencies" — a task listing an unfinished same-wave
     sibling as a dependency is deferred (not run), while the independent
     sibling still completes normally in the same wave.
  6. "Stale branch dependencies" — a real overlapping-completion sequence
     (two claims whose write-sets overlap, completed in order, the same
     mechanism FTF-H034's own registry already proves) marks the earlier
     task's evidence stale; a subsequent wave attempt for that task ID is
     rejected `STALE_DEPENDENCY` before its workspace ever opens.
  7. "Producer writes to gate state" — a task writing a real CODEOWNERS-
     protected path (`.github/workflows/ci.yml`) is denied
     `PROTECTED_GATE_WRITE_DENIED` for an ordinary reviewer identity, and
     the identical write is accepted (approved for downstream) when the
     reviewer is the real CODEOWNERS release authority
     (`owner:samerzakaria`).
  8. "Direct-tool permission bypass" — a disallowed command (`bash -c
     "echo hi"`) is denied both directly by the adapter and through a full
     wave (`PERMISSION_BYPASS_DENIED`), never silently run.
  9. "Advancement after failed CI" — a task whose command exits nonzero is
     recorded `ci_failed` and excluded from `approved_for_downstream`,
     while a genuinely passing sibling in the same wave is still approved,
     and the integrated CI step still passes for the approved subset alone
     (the failing task's own marker is correctly absent and correctly not
     expected).
  10. `NestedDelegationDenied` — opening a workspace from inside another
      already-active workspace's execution context (via
      `WorkspaceManager.active()`) is denied.
  11. The orchestrator's constructor refuses a configured concurrency above
      the hard ceiling of 6.
  12. `SELF_CERTIFICATION_DENIED` at the wave level — a wave whose declared
      reviewer is also one of its own claimants is denied before any
      thread starts.
  13. `BUDGET_EXCEEDED` — a wave whose total estimated cost exceeds its
      configured budget is denied.
  14. `CONCURRENCY_LIMIT_EXCEEDED` — a wave requesting more tasks than the
      configured concurrency limit is denied before any thread starts.
  15. **Real fault injection against the tracked module on disk:**
      temporarily removed the protected-gate-write guard block from
      `tools/implementation/task_orchestrator.py`, ran a fresh subprocess
      that imports the edited file and runs a wave writing a protected
      path from an ordinary reviewer — confirmed it wrongly gets approved;
      restored the file byte-identical (`diff`-equivalent string
      comparison asserted directly), re-ran the same subprocess probe —
      correctly denied again.
- **Regression check:** `uv run ruff check .`/`format --check .`/`mypy .`
  clean; `pnpm run lint`/`format:check`/`typecheck` clean (after `pnpm
  install --frozen-lockfile` — fresh container, no `node_modules`, same
  environment note as every prior shift, plus `uv pip install pyyaml` —
  `scripts/task_claim.py`'s existing PyYAML import isn't in this fresh
  container's `.venv` by default); all 13 existing task negative-test-proof
  scripts (`test_ftf_0001_monorepo_layout.py` through
  `test_r1_execution_binding.py`) pass unmodified — no already-certified
  task's own implementation file was touched, only additive files plus
  FTF-R015's own `docs/spec-matrix.yaml`/`spec/verification-map.json`
  entries, avoiding any Law 15 requalification question; `uv run pytest
  tests/unit` (3 passed) and `pnpm exec vitest run` (2 passed) clean, 0
  skipped either side. `validate_spec.py --strict` clean; `verify_task.py
  --task FTF-R015 --stage task` → `PASS` against evidence commit
  `a1771c2c197cd2194cdb66d0963884182c180ee5`. `verify_release.py` correctly
  non-inflated: `13/300`/`FAILED (1): ['FTF-R015']`/`NOT READY`.
  `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, no `proving_symbol` (build/implementation tooling, no live
  Factory product-runtime caller, same rationale as every prior FTF-H00X/
  FTF-R00X task).
- **CI:** wired `python3 tests/implementation/test_safe_parallel_executor.py`
  into `.github/workflows/ci.yml`'s `gate` job, right after the FTF-R014
  step; YAML-validated (`yaml.safe_load`). Real run status to be confirmed
  on the opened PR before merge.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.

### Control plane review — 2026-09-17 — NOT CERTIFIED (unaddressed acceptance-clause item)
- **PR:** https://github.com/SJZLab/Sovra/pull/70 (`claude/wizardly-fermat-dkivni`
  → `main`), head `667ce4c9b850ce245d5c5b47981b214536e4a7d9`, merged by the
  owner as merge commit `c055226b506df964fca67b5d3ca7b6e39d329a6c` while this
  review was in progress (merge is not certification — Law 4).
- **Session-ID check:** all four PR commits (`a1771c2`, `87c931e`, `57dea79`,
  `667ce4c`) carry `Claude-Session: .../session_01C4Vvhh8pRi5yqZTmnFce6A`;
  this reviewing session's ID (`session_01Wn6FwbAzgvmKf9AcqXKLbQ`) does not
  match — independent, not self-certification.
- **CI verified for real:** GitHub Actions run
  https://github.com/SJZLab/Sovra/actions/runs/35213712242, tied to the
  actual PR head SHA `667ce4c` (confirmed via `get_check_runs`, not a stale
  claim) — both `gate` and `docker-local-stack-integration` jobs
  `completed`/`success`.
- **Own re-run, fresh worktree at the real head:** `uv sync` + `uv pip
  install pyyaml`, `pnpm install --frozen-lockfile`; all 32
  `tests/implementation/test_safe_parallel_executor.py` checks pass; the
  full 13-task existing regression set (`test_ftf_0001_monorepo_layout.py`
  through `test_r1_execution_binding.py`) passes unmodified; `ruff
  check`/`format --check`/`mypy .` clean; `pnpm run lint`/`typecheck`/
  `format:check` and `pnpm exec vitest run` (2 passed) clean.
  `validate_spec.py --strict` clean; `verify_task.py --task FTF-R015
  --stage task` → `PASS`; `verify_release.py` correctly non-inflated
  (`13/300`, `FAILED (1): ['FTF-R015']`, `NOT READY`); `product_truth_check.py
  --fail-on-unwired` confirmed legitimately `0/0` (`proving_symbol: ''`,
  no live product-runtime caller, same rationale as every prior FTF-H00X/
  FTF-R00X task). Note: this session's own git checkout was initially
  shallow, which made `validate_spec.py --strict` falsely report
  `stale_commit: FTF-0001` (the pre-existing, already-certified
  `7ab3f6f` genuinely not resolving in a shallow clone) — `git fetch
  --unshallow` resolved it and both `validate_spec.py --strict` and
  `tests/tasks/test_ftf_h001.py` went clean; not a defect in this PR, a
  local environment artifact, disclosed here rather than silently
  worked around.
- **Own fault injection (distinct from the producer's own, which targeted
  `task_orchestrator.py`'s protected-gate-write guard):** removed the
  `ci_failed` bookkeeping in `TaskOrchestrator.run_wave`'s worker
  (`if not result.ci_passed: ci_failed.add(task.task_id)`) — re-ran the
  suite, exactly 3 checks failed for real (`the failing task is recorded
  CI-failed`, `a genuinely passing sibling in the same wave is still
  approved`, `the integrated CI step still passes for the approved subset
  alone`), proving the "advancement after failed CI" containment is real,
  not decorative. Restored byte-identical (`diff` confirmed empty), re-ran
  clean (32/32).
- **Acceptance-clause item not addressed or disclosed (CERTIFY step 6 —
  "confirm the acceptance/negative_acceptance clauses were actually met,
  not just 'tests pass'"):** `tools/r1_handoff.py packet FTF-R015`'s
  resolved contract states acceptance clause 1 as: "Execute disjoint real
  tasks using the selected existing host in isolated workspaces with
  default4/max6 only when safe; record actual overlap, **leases,
  commits**, tests and an independent integrated merge gate." Checked each
  named evidence category against the delivered code: overlap is proven
  (barrier-synchronized `[started_at, finished_at]` overlap), leases are
  reused from FTF-H034's `Claim`/`CompletedClaim`, tests are `ExecutionResult`,
  and the merge gate is the reviewer-independence + integrated-CI check.
  **"commits" has no corresponding evidence anywhere in this PR.**
  `ExecutionResult` (`tools/implementation/executor_adapter.py:34-47`)
  records `task_id`/`command`/`exit_code`/`stdout`/`stderr`/timestamps —
  no commit field. `Claim`/`CompletedClaim`
  (`scripts/task_claim.py:65-90`, reused unmodified) likewise carry no
  commit field. `executor_adapter.ALLOWED_COMMAND_PREFIXES` explicitly
  allows `git` alongside `python3` (line 23-26) — the only plausible reason
  a real coding-execution wave would need to run `git` at all is to make
  and evidence real commits — yet no code path captures a resulting commit
  SHA, and `tests/implementation/test_safe_parallel_executor.py` never
  exercises the `git` prefix or asserts anything commit-shaped (`grep -n
  git tests/implementation/test_safe_parallel_executor.py` — zero matches).
  Confirmed this is task-specific language, not generic task-card
  boilerplate that's satisfied elsewhere: pulled FTF-R014's and
  FTF-H034's own resolved acceptance clauses for comparison — neither
  mentions "commits" at all. The PR's own queue entry above states the
  32-check suite "proves every acceptance/negative_acceptance clause" —
  that claim is not accurate for this one item, and neither the PR body
  nor this queue entry discloses the gap as an intentional scope decision
  (unlike this same PR's own, correctly disclosed, BLOCKED_EXTERNAL
  treatment of true separate-host-session concurrency, or FTF-0004's `web`
  exclusion, or FTF-0006's `identity`-format-boundary note) — this reads as
  an overlooked contract item, not a disclosed limitation.
- **Conclusion:** the delivered isolation/permission/budget/CI-containment/
  merge-gate machinery is real and independently reproduced (session-ID
  clean, CI green on the real head, 32/32 own re-run, an own fault
  injection distinct from the producer's caught a real regression) — this
  is not a rejection of the module's core design. But one explicitly named
  acceptance-clause evidence category ("commits") is neither implemented
  nor tested nor disclosed as out of scope, so the clause is not fully met
  as written. Per Law 1/Law 5 and this repo's own FTF-0005 precedent (an
  unaddressed clause element blocks certification even when every written
  test passes), not certifying. `certification_state` stays `PROVISIONAL`
  (not downgraded further — the rest of the delivered evidence is sound;
  this is a scoped gap, not a redo).
  **Fix needed:** either (a) record the real commit SHA(s) a task's
  executed command actually produces — e.g. add a `git rev-parse HEAD`
  read (or parse from the command's own output) into `ExecutionResult`
  after a task's command runs inside its isolated workspace, with a test
  that runs a real `git commit` through `execute_task`/a wave and asserts
  the resulting SHA is captured — or (b) if real commit-producing tasks
  are genuinely out of this task's scope for this sandbox (no test in the
  delivered suite actually invokes `git`), say so explicitly in
  `docs/spec-matrix.yaml`'s FTF-R015 notes and in this queue entry, the
  same way this PR already discloses the separate-host-session limitation,
  rather than leaving the clause silently unaddressed. Re-submit for
  certification once either is done; no other part of this PR needs
  rework.
- **Validated-by:** session https://claude.ai/code/session_01Wn6FwbAzgvmKf9AcqXKLbQ
  (independent — see session-ID check above), 2026-09-17.

## 2026-09-17 — FTF-R015 — Fix for missing commit evidence (re-submitted)
- **Commit:** `519bba0ecbabcf36276df1dda3265673a7c2b702` (implementation),
  on branch `claude/sleepy-pasteur-c8ihmb` (PR #71 — the same PR that
  carries the NOT CERTIFIED review above; produced by
  `session_01Wn6FwbAzgvmKf9AcqXKLbQ`, the same session that wrote that
  review, at the live user's explicit direction to implement option (a)
  from it — this is the fix, not a self-certification of the original
  PR #70).
- **Fixes exactly what the review above found:** `execute_task`
  (`tools/implementation/executor_adapter.py`) now reads the real `git
  rev-parse HEAD` of a task's isolated workspace after its command runs
  and records it as `ExecutionResult.commit_sha`. A task whose command
  never touches git gets `None`, never a fabricated hash. No other part
  of FTF-R015 changed, per the review's own scoping.
- **Test:** `tests/implementation/test_safe_parallel_executor.py` gained
  `test_real_commit_is_captured_and_propagates_through_a_wave` (32 -> 36
  checks): a task's command runs a real `git init`/config/`commit
  --allow-empty` sequence; the resulting `commit_sha` is cross-checked
  against an independently run `git rev-parse HEAD` against the same
  workspace (not read back from the module's own claim); a
  marker-only/no-git task is proven to get `None`; and the same real SHA
  is shown to propagate through a full `TaskOrchestrator.run_wave`'s
  `execution_results`, not just direct `executor_adapter` use.
- **Evidence — shown able to fail (Law 1), reproducing the certifier's
  exact finding:** temporarily hardcoded `commit_sha=None` in
  `execute_task`, re-ran the suite — exactly the 3 new checks failed for
  real (`a real git commit... is captured as commit_sha`, `the captured
  commit_sha matches an independently read git HEAD...`, `the real commit
  SHA propagates through a full wave's execution_results...`), the other
  33 stayed green; restored the file byte-identical (`diff` confirmed
  empty), re-ran clean (36/36).
- **Regression check:** `uv run ruff check .`/`format --check .`/`mypy .`
  clean; `pnpm run lint`/`typecheck`/`format:check` and `pnpm exec vitest
  run` (2 passed) clean; the full 13-task existing regression set
  (`test_ftf_0001_monorepo_layout.py` through
  `test_r1_execution_binding.py`) passes unmodified; `uv run pytest
  tests/unit` (3 passed). `validate_spec.py --strict` clean; `verify_task.py
  --task FTF-R015 --stage task` → `PASS` against evidence commit
  `519bba0ecbabcf36276df1dda3265673a7c2b702`; `verify_release.py` correctly
  non-inflated (`13/300`, `FAILED (1): ['FTF-R015']`, `NOT READY`);
  `product_truth_check.py --fail-on-unwired` stays `0/0` (unchanged
  rationale — build/implementation tooling, no live product-runtime
  caller).
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL
  (unchanged). Not self-certified — this is the same producer session as
  the reviewing session for PR #70's original submission, so it cannot
  certify its own fix; awaiting a fresh Control-plane session with no
  session-ID overlap with `session_01Wn6FwbAzgvmKf9AcqXKLbQ` per Law 4.

## 2026-09-17 — FTF-R011 — Define immutable connected and air-gap assertion-level release matrix
- **Commit:** `18dd2823eff6171594fb53b6adf0beb953bada82` (implementation),
  `a3d7084` (hash-fixup bookkeeping — same two-commit pattern as
  FTF-0006/FTF-H001/FTF-R001/FTF-H002/FTF-H028/FTF-H034/FTF-1810/FTF-R014/
  FTF-R015, since a commit can't embed its own hash).
- **Real DoD source:** `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolved via
  `python tools/r1_handoff.py packet FTF-R011 --plan FINE_TUNING_FACTORY_RELEASE_1.md`.
  Dependencies FTF-R001, FTF-H001, FTF-H002 all `CERTIFIED` — dependency-ready.
- **Law 15 check (raised and deferred by a prior shift's FTF-R015 entry,
  addressed here rather than re-deferred):** this task's own `files` list
  edits `spec/deployment-profiles.yaml`, a file FTF-R001's task created.
  Directly verified before touching it, not assumed: `tests/tasks/
  test_ftf_r001.py` and `scripts/architecture_lock.py` (FTF-R001's entire
  certified evidence surface) never import or read `spec/deployment-
  profiles.yaml` — `grep` across both confirms zero references. FTF-R001's
  own certified `docs/spec-matrix.yaml` notes describe its frozen evidence
  as `packages/component-contracts/architecture.schema.json` and
  `spec/component-lock.schema.json` only; `deployment-profiles.yaml`'s
  `profiles:` section (the actual Section 79.2 binding text FTF-R001 wrote)
  is left completely untouched here (`git diff` confirms byte-identical),
  only the `assertion_applicability` section — explicitly FTF-R001's own
  header comment called these "Section 79.5's own worked examples", i.e.
  illustrative, not FTF-R001's complete certified deliverable — is
  extended. No re-run of `test_ftf_r001.py` was skipped either:
  it was re-run against the final state and still passes (see Regression
  below). No Law 15 requalification is triggered.
- **Delivered:**
  - `spec/deployment-profiles.yaml`: `assertion_applicability` extended
    from FTF-R001's original 2 worked-example entries (Q12, Q03 — kept
    byte-identical) to all 43 required scenarios. Each entry carries an
    `owner` (projected from `spec/release-scope.yaml`'s CAP ownership via
    each CAP's `qualification_scenarios` list, never invented) and a
    `status` restricted to `NOT_TESTED`/`PASS`/`FAIL`/`BLOCKED_EXTERNAL`
    (no `N/A`). Five scenarios (Q03, Q12, Q26, Q37, Q39) are `kind: split`
    with a genuinely distinct `connected_assertion` and `airgap_assertion`;
    the other 38 are `kind: uniform` (their own qualification-inventory
    text names no external/public dependency). Q26/Q37/Q39's split
    classification is disclosed authored judgment (see
    `scripts/validate_profile_scope.py`'s module docstring for the
    reasoning against Section 52/79.3/79.4's own text) — independently
    corroborated for Q26 by this task's own resolved
    `qualification_scenarios: [Q12, Q26, Q43]`, which the packet supplied
    without my having chosen it.
  - `scripts/validate_profile_scope.py`: `generate_assertion_applicability`/
    `validate_deployment_profiles` (whole-set completeness against the
    frozen 43-scenario inventory, owner provenance cross-checked against
    `release-scope.yaml`, split-name distinctness, status enum); Section
    84.7 asks the manifest exist before execution, and it does —
    `generate_expected_checks(profile)` is the preapproved per-profile
    expected-check list, structurally unable to place a `connected_*`
    assertion in `AIR_GAPPED`'s list or an `airgap_*` assertion in a
    connected profile's list; `assert_profile_qualified`/
    `assert_full_product_done` enforce that a split scenario's air-gap
    result can never satisfy its connected-positive requirement and that
    passing one profile's checks never registers as full-product done.
- **Test:** `tests/security/test_profile_scope.py` — 15 checks (11
  `check()` calls plus 4 embedded inside the fault-injection test).
- **Evidence — shown able to fail (Law 1):**
  1. Negative_acceptance #1 (`N/A` status): `test_na_status_is_rejected`
     rejects it both in the frozen document and in a results-reporting
     fixture where every other check genuinely passes.
  2. Negative_acceptance #2 (air-gap denial substituting for the required
     connected positive test): `test_airgap_denial_cannot_satisfy_connected_requirement`
     — a real fixture where every split scenario's *airgap* assertion is
     marked PASS and every connected assertion is left unrecorded; both
     `assert_profile_qualified("INTERNAL_PRODUCTION", ...)` and
     `assert_full_product_done(...)` correctly reject it.
  3. Negative_acceptance #3 (external provider called from air gap):
     `test_external_provider_never_expected_under_airgap` — structural
     proof that `generate_expected_checks("AIR_GAPPED", ...)` never
     contains any `connected_*` assertion, and no connected profile's
     manifest ever contains an `airgap_*` assertion.
  4. Negative_acceptance #4 (dropping an old ID): `test_dropping_an_old_id_is_rejected`
     removes Q10 from a copy of the document — rejected as `missing_scenario`.
  5. `test_worked_examples_preserved` — Q12/Q03's original FTF-R001 variant
     names survive this task's extension byte-for-byte.
  6. `test_profile_only_pass_is_not_full_product` — every `AIR_GAPPED`-
     expected check passing qualifies that profile but still correctly
     fails full-product completion (Section 79.5's
     `AIR_GAP_PROFILE_QUALIFIED` vs `FULL_PRODUCT_DONE` distinction).
  7. `test_genuinely_complete_results_pass_both_gates` — positive control:
     when every assertion is genuinely recorded PASS, all four profiles
     qualify and full-product completion passes with zero errors, proving
     the negative tests above fail for the injected defect specifically,
     not for everything.
  8. `test_split_scenario_cannot_collapse_to_one_name` /
     `test_missing_owner_is_rejected` / `test_dropping_a_profile_is_rejected`
     — additional whole-set guard checks mirroring FTF-H002/R001's own
     `release_scope_lock.py`/`architecture_lock.py` validator pattern.
  9. **Real fault injection against the tracked module on disk:**
     `test_fault_injection_against_tracked_module` temporarily removes the
     connected/airgap distinctness guard block from
     `scripts/validate_profile_scope.py`, imports the mutated module fresh,
     confirms the Q12 collapsed-name defect wrongly passes validation with
     the guard gone, restores the file byte-identical (asserted directly),
     and confirms validation is clean again.
  10. **Manually confirmed before committing, outside the test file too:**
      reverted `spec/deployment-profiles.yaml` to `git show HEAD:...` (its
      pre-FTF-R011 state, `assertion_applicability` holding only 2 of 43
      required scenarios) — `scripts/validate_profile_scope.py` genuinely
      fails with 43 `missing_scenario`/`must be a mapping` errors, and
      `tests/security/test_profile_scope.py` genuinely raises a
      `TypeError` (not a clean designed failure — the old format is a
      bare list, not a mapping, which is itself further proof the checks
      are load-bearing against real structural difference, not vacuous).
      File restored to its FTF-R011 state before committing.
- **Regression check:** `uv run ruff check .`/`format --check .`/`mypy .`
  clean; `pnpm run lint`/`format:check`/`typecheck` clean (after `pnpm
  install --frozen-lockfile` — fresh container, no `node_modules`); all 14
  existing task negative-test-proof scripts (`test_ftf_0001_monorepo_layout.py`
  through `test_safe_parallel_executor.py`, including `test_ftf_r001.py`
  specifically per the Law 15 check above) pass unmodified — no already-
  certified task's own implementation file was touched, only additive
  files plus `spec/deployment-profiles.yaml`'s `assertion_applicability`
  extension and FTF-R011's own `docs/spec-matrix.yaml`/
  `spec/verification-map.json` entries; `uv run pytest tests/unit` (3
  passed) and `pnpm exec vitest run` (2 passed) clean, 0 skipped either
  side. `validate_spec.py --strict` clean; `verify_task.py --task FTF-R011
  --stage task` → `PASS` against evidence commit
  `18dd2823eff6171594fb53b6adf0beb953bada82`. `verify_release.py` correctly
  non-inflated: `13/300`/`FAILED (2): ['FTF-R011', 'FTF-R015']`/`NOT READY`.
  `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, no `proving_symbol` (release-matrix governance/validator
  layer, no live Factory product-runtime caller, same rationale as every
  prior FTF-H00X/FTF-R00X task).
- **CI:** wired `scripts/validate_profile_scope.py` (validates the real
  committed document) and `python3 tests/security/test_profile_scope.py`
  into `.github/workflows/ci.yml`'s `gate` job, right after the FTF-R015
  step; YAML-validated (`yaml.safe_load`). Real run status to be confirmed
  on the opened PR before merge.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.

## 2026-09-17 — FTF-R017 — Qualify maintained internal storage and OSS dependency lifecycle
- **PR:** to be opened against `main` from `claude/wizardly-fermat-ci7leb`.
- **Commit:** `b76235989aca9b73bbefb9502435c8c25f9564ad` (implementation).
- **Real DoD source:** `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolved via
  `python tools/r1_handoff.py packet FTF-R017 --plan FINE_TUNING_FACTORY_RELEASE_1.md`.
  Dependencies FTF-R001/FTF-0004 both `CERTIFIED` — unblocked.
- **Why this task:** with `spec/tasks.json`'s real `dependencies` computed
  directly against the certified set (15/300), only two items were
  dependency-ready: FTF-0102 (blocked on the standing 2026-09-16
  new-runtime-dependency owner ask, unchanged — see QUEUE.md "Owner
  actions") and FTF-R017. FTF-R017 was the only claimable item this shift.
- **Scope finding before implementation:** Section 80.4 names eight
  storage semantics (multipart, checksum, versioning, immutable-
  publication, identity, retention/deletion, encryption, backup and
  restore) to qualify against either the organization's real S3 or the
  SeaweedFS offline fixture. This repo's interactive dev sandbox has no
  container-registry egress — reconfirmed this task with a real `docker
  pull` against this exact pinned SeaweedFS digest (`chrislusf/seaweedfs@
  sha256:2829e52c...`), the same reproducible 403 FTF-0004's own evidence
  already documents. Followed FTF-0004's own established precedent: build
  the real client/tests/CI wiring and let the live-container evidence run
  for real on GitHub Actions' hosted runner, never fabricate a local pass.
- **Delivered:**
  - `spec/component-maintenance.yaml` + `spec/component-maintenance.schema.json`
    + `scripts/validate_component_maintenance.py`: owner/support/license/
    security/version/export/restore record for all seven canonical
    components. Every license claim was fetched and verified against that
    project's own real, current `LICENSE` file this task via `WebFetch`
    (URLs recorded on each record as `license_evidence_url`) — not
    asserted from memory. All seven are permissive (Apache-2.0 or MIT).
    LiteLLM's separate proprietary `enterprise/` directory is explicitly
    disclosed and never used. The schema's
    `enterprise_or_proprietary_features_used` field is a structural
    `const: false` — this schema cannot accept a `true` value at all,
    satisfying the "unapproved license/enterprise dependency"
    negative_acceptance clause by construction. The validator reuses
    `scripts/architecture_lock.py`'s bounded JSON Schema validator and its
    `_check_exact_key_set` whole-set helper directly (imported, not
    duplicated) — the same exact-seven-canonical-keys discipline
    `spec/component-lock.schema.json` (FTF-R001) already established.
  - `apps/api/app/services/storage_semantics_client.py`: a real,
    stdlib-only AWS Signature Version 4 request-signing S3 client
    (`hashlib`/`hmac`/`urllib`/`datetime`/`base64` only — boto3/any S3 SDK
    would be a new runtime dependency, an ask-first CLAUDE.md item, so this
    implements the published SigV4 algorithm directly). `S3Endpoint`/
    `S3Credentials` are plain constructor arguments, never a hardcoded
    host, so the identical code path later qualifies the organization's
    real external S3 endpoint without a code migration (this task's own
    acceptance clause 2).
  - `infra/docker/seaweedfs-s3-identity.json` (real, non-anonymous S3
    identity — format verified against SeaweedFS's own
    `docker/compose/s3.json` example) + a one-line addition to
    `infra/docker/docker-compose.yml`'s already-certified `s3` service
    (`-s3.config=...` + the read-only volume mount). **Law 15 check
    performed before editing, not assumed:** `grep`'d
    `scripts/verify_docker_local_stack.py`/`tests/tasks/test_ftf_0004.py`/
    `apps/api/app/config/docker_local_stack.py` — FTF-0004's entire
    certified evidence surface only ever exercises **TCP-level**
    reachability on the S3 port, never S3 auth/API semantics; adding an S3
    identity config is purely additive to a dimension FTF-0004 never
    tested. No requalification triggered.
  - `infra/internal/storage-profile.yaml` + `scripts/validate_storage_profile.py`:
    declares the qualified backend (`seaweedfs_offline_fixture` today,
    `maintained_organizational_s3` once the owner provisions one — `minio`
    is not a value the schema/enum can select at all, satisfying "Reject
    automatic archived-MinIO default" by construction) and the exact eight
    Section-80.4 semantics. `qualified: true` structurally requires a
    non-placeholder `evidence` value — a bare `NOT_YET_RUN`/empty string
    cannot be hand-flipped to claim a result the container never produced
    (the "unimplemented S3 feature accepted" negative_acceptance clause).
  - `tests/integration/test_storage_semantics.py`: real, live-container
    proof of all eight semantics (multipart create/upload-parts/complete
    reconstructing exact bytes + abort never publishing a partial object;
    Content-MD5 checksum enforcement, correct and corrupted; bucket
    versioning with two independently retrievable versions;
    Object-Lock COMPLIANCE retention genuinely rejecting an early delete;
    an unconfigured access key genuinely rejected while the real
    configured identity succeeds; a non-retained delete producing a real
    delete marker with the prior version still recoverable; SSE-C
    encryption genuinely inaccessible without the correct key; and a real
    copy-out/delete/restore/hash-verify cycle). Boots
    `infra/docker/docker-compose.yml`'s `s3` service the same way
    `scripts/verify_docker_local_stack.py` boots the whole stack for
    FTF-0004. Wired into a new `storage-semantics-integration` CI job
    (GitHub Actions' hosted runner has real registry egress).
  - `tests/tasks/test_ftf_r017.py` — runnable without Docker. Proves the
    SigV4 signing math against a **real, externally-published AWS
    Signature Version 4 test vector** (the "get-vanilla" case from
    `github.com/mhart/aws4`, a mirror of AWS's own published test suite,
    and `github.com/saibotsivad/aws-sig-v4-test-suite`'s `index.json` for
    the config values) — fetched via `curl` (not the summarizing
    `WebFetch` path, to avoid any paraphrase risk) and independently
    re-verified live before being embedded: the canonical-request SHA-256
    was recomputed with plain `hashlib` and shown to match the published
    `.sts` file's third line byte-for-byte, and the tracked module's own
    `_signing_key` was shown to reproduce the published `.authz` file's
    exact signature (an initial attempt with a misremembered secret key
    constant failed — refetched the suite's real `index.json` rather than
    trusting memory, then matched). Also proves every negative_acceptance
    case named in this task's own contract, plus a real fault injection
    against the tracked `spec/component-maintenance.yaml` file on disk
    (flips one real component's `enterprise_or_proprietary_features_used`
    to `true`, confirms the real file on disk now fails validation,
    restores byte-identical, confirms clean again).
  - `docs/operations/component-lifecycle.md`: human-readable summary
    pointing at the two machine-validated records as the actual source of
    truth, restating no claim they don't already make.
- **Test:** `tests/tasks/test_ftf_r017.py` — 19 test functions, 25 check()
  assertions (SigV4 vector + structural/self-consistency +
  negative_acceptance + fault injection). Live-container semantics: `tests/integration/
  test_storage_semantics.py` — 8 real-container test functions, run in CI.
- **Evidence — shown able to fail (Law 1):** every negative_acceptance
  case this task's own contract names is proven rejected: a maintenance
  record set missing a canonical component; a record claiming enterprise/
  proprietary feature use; a record naming an unreviewed/copyleft license;
  records missing `export_plan`/`restore_plan`; selecting `minio` as the
  storage backend; dropping one of the eight required semantics;
  `qualified: true` paired with placeholder evidence. Plus a real fault
  injection against the tracked `spec/component-maintenance.yaml` file on
  disk (not just an in-memory fixture) — described above.
- **Regression:** `uv run ruff check .`/`format --check .`/`mypy .` clean;
  `pnpm run lint`/`format:check`/`typecheck` clean (after `git fetch
  --unshallow` + `pnpm install --frozen-lockfile` — fresh, shallow
  container with no `node_modules`); all 15 existing task
  negative-test-proof scripts (`test_ftf_0001_monorepo_layout.py` through
  `test_profile_scope.py`) pass unmodified — no already-certified task's
  own implementation file was touched, only the additive
  `infra/docker/docker-compose.yml` S3-identity line (Law 15-checked
  above) plus FTF-R017's own `docs/spec-matrix.yaml`/
  `spec/verification-map.json` entries; `uv run pytest tests/unit` (3
  passed) and `pnpm exec vitest run` (2 passed) clean, 0 skipped either
  side. `validate_spec.py --strict` clean; `verify_task.py --task
  FTF-R017 --stage task` → `PASS` against evidence commit
  `b76235989aca9b73bbefb9502435c8c25f9564ad`. `verify_release.py`
  correctly non-inflated: `15/300`/`FAILED (1): ['FTF-R017']`/`NOT READY`.
  `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, no `proving_symbol` (OSS-lifecycle/storage-qualification
  tooling, no Factory production service importing it yet, same rationale
  as every prior FTF-H00X/FTF-R00X task).
- **CI:** wired `scripts/validate_component_maintenance.py`,
  `scripts/validate_storage_profile.py` and `python3 tests/tasks/
  test_ftf_r017.py` into `.github/workflows/ci.yml`'s `gate` job, right
  after the FTF-R011 step; added a new `storage-semantics-integration` job
  running `tests/integration/test_storage_semantics.py` against the live
  container (same registry-egress rationale as FTF-0004's
  `docker-local-stack-integration` job). YAML-validated (`yaml.safe_load`).
  Real run status (both the new `gate` steps and the new
  `storage-semantics-integration` job actually booting the S3 container)
  to be confirmed on the opened PR before merge.
- **Disclosed, not hidden:** `infra/internal/storage-profile.yaml`'s eight
  `required_semantics` entries stay honestly `qualified: false`/
  `evidence: NOT_YET_RUN` in this commit — the real per-semantic result
  only exists once the `storage-semantics-integration` CI job actually
  runs against the live container; `scripts/validate_storage_profile.py`
  structurally refuses to accept `qualified: true` without real evidence,
  so this file cannot be hand-flipped ahead of that run. Exact pinned
  component versions remain `NOT_YET_RESOLVED` for all seven components,
  matching `spec/release-scope.yaml`'s own real state (Section 64.3
  onboarding gate not yet run) — not fabricated ahead of that gate.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4, which
  should specifically confirm the `storage-semantics-integration` CI job
  passed for real (not merely that it exists) before certifying.

## 2026-09-17 — FTF-R017 — Qualify maintained internal storage and OSS
  dependency lifecycle (relay submission, PR #75)
- **PR:** https://github.com/SJZLab/Sovra/pull/75
  (`claude/wizardly-fermat-ci7leb` → `main`). Note: this PROVISIONAL claim
  exists only inside PR #75's own branch (`docs/certifications/queue.md`
  entry, `docs/spec-matrix.yaml` state) — `main`'s own copies do not yet
  carry an FTF-R017 entry, same as the FTF-0004 precedent
  (2026-09-16, "not in a certifiable state").

### Control plane review — 2026-09-17 — NOT CERTIFIED (real CI failure on this PR's own new job)
- **Session-ID check:** all 4 PR commits (`b762359`, `6fd7801`, `9d7bf82`,
  `42cae14`) carry `Claude-Session: .../session_016bZ7g5DPgSU8eYMTo8H5NY`;
  this reviewing session's own ID
  (`https://claude.ai/code/session_01EF89AyieNmjbMTcNSvVkNo`) does not
  match — independent, not self-certification.
- **CI checked for real, not self-reported:** `pull_request_read
  get_check_runs` on the actual PR head. First head (`9d7bf82`, run
  35238767213): `storage-semantics-integration` — `completed`/`failure`
  (`gate` and `docker-local-stack-integration` still in progress at that
  point). The PR then advanced to a new head (`42cae14`, a QUEUE.md-only
  bookkeeping commit that touches no test/implementation code) which
  triggered a fresh run (35238865287); `storage-semantics-integration`
  failed again on this head too — same job, different (real) transient
  error text, same root cause. Not a stale/self-reported claim: both
  failures were read directly from `get_job_logs`.
- **Failure, reproduced twice, not a flake:** `tests/integration/
  test_storage_semantics.py`'s `main()` (lines ~1973-1983) calls
  `_wait_for_tcp(endpoint.host, endpoint.port, timeout_seconds=3.0)`,
  and on timeout runs `docker compose up -d s3` followed by
  `_wait_for_tcp(..., timeout_seconds=90.0)` before immediately running
  every `test_*` function against the live client. `_wait_for_tcp` only
  proves the TCP port is accepting connections — it does not prove the
  SeaweedFS S3 HTTP/API layer is actually ready to complete a full
  request/response cycle. Run 35238767213 (head `9d7bf82`): all 9 tests
  failed within ~0.3s of the port becoming reachable with
  `RemoteDisconnected('Remote end closed connection without response')`.
  Run 35238865287 (head `42cae14`, ~1 minute later): all 9 tests failed
  again, this time with `ConnectionResetError(104, 'Connection reset by
  peer')` — a different low-level symptom of the identical race (TCP
  accepting before the HTTP server behind it is actually serving), not a
  different bug and not infra flake (two independent runs, same shape,
  same 9/9 failure count, both within a few seconds of the container
  starting).
- **This is exactly the bug class this repo already has certified
  precedent for and should not have regressed on:** FTF-0004's own
  `docker_local_stack.py`/`verify_docker_local_stack.py` (CERTIFIED
  2026-09-16) deliberately polls the real `/health` HTTP endpoint — not
  bare TCP — for exactly this reason, and that task's own CI-failure/fix
  cycle (see the "Real CI failure found and fixed on the merge commit"
  entry above) already discovered and fixed an equivalent readiness gap
  once. `test_storage_semantics.py` reintroduces the weaker TCP-only
  check for a new integration path instead of reusing that lesson.
- **Conclusion — per CERTIFY step 3 ("re-check CI actually ran and passed
  on the real merged commit — not a stale or self-reported claim"):** CI
  has not passed on this PR's real head — the PR's own new
  `storage-semantics-integration` job fails for real, reproducibly, on
  every head pushed so far. Not merging, not certifying. Not evaluating
  the SigV4 client, the component-maintenance ledger, or the negative-
  acceptance test suite's content further — a content-level review would
  be premature while the PR's own required integration evidence doesn't
  exist yet. `certification_state` stays `PROVISIONAL` (not downgraded
  further — the non-Docker-dependent `tests/tasks/test_ftf_r017.py`
  evidence and the license/component-maintenance work are unaffected by
  this finding; this is a scoped fix, not a redo).
  **Fix needed:** replace (or precede) `_wait_for_tcp` with a real
  HTTP-level readiness probe — e.g. retry a cheap, real S3 API call
  (such as a `ListBuckets`/bucket-existence check via the already-built
  `S3Client`) in a bounded loop until it succeeds or times out — the same
  pattern `docker_local_stack.py`'s `/health` polling already
  establishes, rather than trusting TCP acceptance alone. Push the fix to
  this same PR #75 branch (`claude/wizardly-fermat-ci7leb`) and let
  `storage-semantics-integration` actually go green before resubmitting;
  do not open a duplicate PR.
- **Validated-by:** session
  https://claude.ai/code/session_01EF89AyieNmjbMTcNSvVkNo (independent —
  see session-ID check above), 2026-09-17.

**Addendum (same review session, same day):** the producer pushed a fix
for the TCP-readiness race above (commit `fb0c2ac`, "wait for real S3
HTTP readiness") independently and concurrently with this review, before
this review's own commit landed on `main`. Re-checked CI on the new head
for real: the TCP-readiness race is gone (the job now takes ~40s instead
of failing within ~1s), but `storage-semantics-integration` still fails
(run 35239135374, job 105262713015) — a different, real failure this
time: `S3Error(500, 'InternalError', ...)` on 7 of 9 tests
(`test_backup_and_restore`, `test_checksum`, `test_encryption`,
`test_identity`, `test_immutable_publication`, `test_multipart`,
`test_multipart_abort`) and `S3Error(501, 'NotImplemented', 'A header you
provided implies functionality that is not implemented')` on the
remaining 2 (`test_retention_deletion`, `test_versioning`). Not diagnosed
further by this session — could be a real SigV4-client request-shape bug
or a genuine SeaweedFS capability gap against the semantics this task
claims (retention/versioning headers SeaweedFS's S3 gateway may not
implement the way AWS does); either way this is the next relay shift's
root cause to find, not this review's to guess at. `certification_state`
stays `PROVISIONAL`, unchanged conclusion above (not merging, not
certifying) — updating this entry only so the next shift doesn't waste a
cycle re-fixing the already-fixed TCP race.

## 2026-09-17 — FTF-R017 (PR #75) — merge-conflict fix + per-request debug instrumentation (not yet a resubmission)
This shift found `pull_request_read.get` reporting `mergeable_state:
dirty` on PR #75 (a real, GitHub-computed conflict against `main`, caused
by the certifier session above having pushed its review/addendum commits
directly to `main` while this PR was open) — and, as a direct consequence
matching the FTF-0004 precedent exactly, `pull_request_read
get_check_runs` returning `total_count: 0` on every commit pushed after
that point (`a8decce`, and this shift's own first attempt): GitHub Actions
silently never generated check runs for either push. Fixed the same way
the FTF-0004 precedent did: fetched current `main`, merged it into
`claude/wizardly-fermat-ci7leb`, resolved the conflicts (all three were
pure tracking-doc conflicts — `PROGRESS.md`/`QUEUE.md`/this file — no code
conflict; kept both sides' substantive content in chronological order).
While resolving, caught and fixed two places where git's own 3-way merge
had silently picked the wrong side without flagging a conflict: `QUEUE.md`
had reverted `Provisional: 0` back to a stale `Provisional: 1 (FTF-R017,
PR #75...)` and re-introduced a stale `## Provisional` list entry for
FTF-R017 that `main`'s own certifier review had already correctly emptied
out when reclassifying this into `## Active` — both confirmed against
`git show origin/main:QUEUE.md` directly and fixed to match `main`'s real
state, not assumed correct because the merge didn't complain.

Separately (see this shift's own PROGRESS.md entry for the full
reasoning): before knowing about the merge-conflict/CI-never-ran issue,
reviewed the SigV4 signing implementation against the AWS spec (no defect
found) and researched known SeaweedFS S3-gateway issues for this exact
`InternalError`/`NotImplemented` wording (multiple plausible but
unconfirmed causes). Concluded the real gap is that no CI log has ever
shown which specific S3 operation inside each failing test actually
triggers the 500/501 — `tests/integration/test_storage_semantics.py`'s
`main()` only catches `S3Error` at the whole-test level. Added an opt-in
`debug` flag to `S3Client` (default `False`, zero behavior change for any
other caller) that prints each request's shape (method/canonical-URI/
query-key-names/header-names/body-length) before it executes and the real
response status/code/message after — never credentials, the
`Authorization` header, or signature material. Wired to `True` only in
the integration test's own client construction. Verified this task's own
non-Docker unit test and the full 15-script existing regression set,
pytest, and vitest all still pass unmodified.

Pushed both fixes together as commit `a43dff9` (debug instrumentation,
pushed before the mergeable_state issue was discovered) plus this merge
commit, to the same PR #75 branch — not a new PR. `certification_state`
stays `PROVISIONAL` (unchanged — still not certifiable; CI has not run
successfully on any of these commits yet, since it never ran at all until
this merge). Not a resubmission for certification yet — waiting for the
now-unblocked CI to actually run and produce real per-request debug
output before drawing or acting on any root-cause conclusion.

## 2026-09-17 — FTF-R017 — Real fix confirmed green: all 8 Section-80.4 storage semantics now proven, ready for L2 certification
Continuation of the same PR #75. Two real, source-verified fixes landed
this shift on top of the already-recorded capacity fix (`01e88fb`):

1. **`60c8182`** — bumped the pinned SeaweedFS fixture `3.71 -> 3.99`
   (`infra/docker/docker-compose.yml`, digest verified via Docker Hub's
   metadata API, no pull needed). 3.71 genuinely predates S3 object
   versioning/object-lock and returned a generic `500` instead of
   `BadDigest` for a corrupted upload; confirmed directly against 3.99's
   real Go source before pushing (not a web summary): `weed/s3api/
   s3api_bucket_handlers.go` has real, implemented
   `PutBucketVersioningHandler`/`GetBucketVersioningHandler` functions
   with genuine object-lock interaction, and `weed/s3api/
   s3api_object_handlers_put.go` maps a digest mismatch to
   `s3err.ErrBadDigest`. `-s3.config`/`-s3.port`/
   `-master.volumeSizeLimitMB` all unchanged in `weed/command/server.go`
   at the `3.99` tag — a pure version bump.
2. **`deaedc8`** — the 3.99 bump's own first CI run (35261643931) showed
   `test_checksum` fully fixed but 6 assertions still failing across
   `test_versioning`/`test_retention_deletion`/`test_immutable_publication`/
   `test_encryption`. Traced each by hand against that run's real
   request/response log (not assumed a second SeaweedFS gap): the
   container's own boot log showed the server genuinely sending
   `x-amz-version-id`, pointing at the client. `S3Client._request` built
   its response-header dict via `dict(resp.headers.items())`, which
   preserves the server's exact wire casing; every caller looked up
   lowercase keys. Confirmed against SeaweedFS 3.99's real source:
   `s3api_object_handlers_put.go:172` / `s3api_object_handlers_delete.go:
   92-93` set `x-amz-version-id`/`x-amz-delete-marker` via
   `Header().Set(...)`, which Go's `net/http` canonicalizes to
   `X-Amz-Version-Id`/`X-Amz-Delete-Marker` on the wire — the client's
   lowercase lookup always missed, silently returning `""`/`"false"`.
   `s3api_object_handlers_put.go:378/380` writes `w.Header()["ETag"]`
   directly, bypassing canonicalization — exactly why the ETag-only
   checks (`test_checksum`/`test_multipart`) already passed. An
   always-empty `version_id` explained the rest: `get_object(...,
   version_id="")` (falsy) silently fell back to an unversioned GET
   (both `test_versioning` failures, the `test_retention_deletion` 404
   cascade), and `delete_object(..., version_id="")` sent no `versionId`
   at all, so `test_immutable_publication`'s DELETE inserted a delete
   marker instead of ever touching the actual COMPLIANCE-retained
   version. Fixed by lowercasing every response-header key once in
   `_request` (HTTP header names are case-insensitive per RFC 7230
   §3.2 — a correctness fix, not a workaround) and updating the three
   now-mismatched `"ETag"`-cased lookups to `"etag"`.

   Separately corrected `test_encryption`'s wrong-SSE-C-key assertion
   from `exc.status_code == 400` to `== 403`: read
   `s3api_object_handlers.go:709-733` directly — a missing key raises
   `ErrSSECustomerKeyMissing` (400, already passing) while a wrong key's
   MD5 mismatch deliberately raises `ErrAccessDenied`, which
   `s3err/s3api_errors.go`'s own `errorCodeResponse` map confirms is
   `http.StatusForbidden`. This is SeaweedFS's real, intentional
   AWS-compatible behavior (a non-matching customer key is an
   authorization failure, not a malformed request) — the test's own
   assertion was wrong, not the server.

**Real, live evidence (Law 1) — not inferred from a green checkmark:**
read the actual job log for `deaedc8`'s own CI run
(https://github.com/SJZLab/Sovra/actions/runs/35262488236/job/105341398028)
directly: every one of the 9 `test_*` functions in
`tests/integration/test_storage_semantics.py` ran against the live
SeaweedFS 3.99 container, ending with the script's own printed line
`FTF-R017 storage semantics: all checks passed` — zero failures, all 8
Section-80.4 semantics (multipart, checksum, versioning,
immutable-publication, identity, retention/deletion, encryption,
backup/restore) genuinely proven against the qualified SeaweedFS
fixture, not `BLOCKED_EXTERNAL` and not a partial/disclosed-gap
qualification. The subsequent doc-only bookkeeping commit (`ce14699`)
re-ran the same `storage-semantics-integration` job on its own head
(run 35262651270, job 105341937947) — also `completed`/`success`,
confirming the fix isn't specific to one lucky run.

**Regression (this shift, fresh worktree at the PR's real final head
`ce14699`):** `uv run ruff check`/`format --check`/`mypy .` clean;
`pnpm run lint`/`format:check`/`typecheck` clean (`pnpm install
--frozen-lockfile` run first); all 16 existing task
negative-test-proof scripts pass, including this task's own
`tests/tasks/test_ftf_r017.py`; `uv run pytest tests/unit` (3 passed);
`pnpm exec vitest run` (2 passed); `scripts/validate_spec.py --strict`
clean; `python3 scripts/verify_task.py --task FTF-R017 --stage task` ->
`PASS` against evidence commit `b762359...` (the original two-commit-
pattern hash, unchanged — `verify_task.py`'s own check only needs that
commit to resolve as a real git object, which it does; the fix commits
are later, additive history on the same branch, same convention every
other multi-commit task in this repo already uses);
`python3 scripts/verify_release.py` non-inflated (`15/300`,
`FAILED (1): ['FTF-R017']` — the only item left uncertified,
`NOT READY`); `python3 scripts/product_truth_check.py --fail-on-unwired`
stays legitimately `0/0` (no `proving_symbol` — no live Factory
production service imports this client yet, same rationale as every
other FTF-R00X/FTF-H00X task so far).

`certification_state` stays `PROVISIONAL` (unchanged — not
self-certified, per Law 4). `proof_state` stays `L1-PROVEN`. This task
is now fully ready for Control-plane L2 review: real CI green on the
PR's actual current head, all acceptance and negative_acceptance clauses
independently traceable to a specific passing assertion in
`tests/integration/test_storage_semantics.py`/`tests/tasks/
test_ftf_r017.py`, and every finding (the capacity fix, the version
bump, the header-casing bug, the SSE-C assertion correction) backed by
direct source citations rather than assumption. Not merging or
certifying this session — that is Control plane's role, not the
relay's, per Law 4 and this shift's own STEP 4(a).

### Control plane review — 2026-09-17 (later) — CERTIFIED
- **Session-ID check:** all 16 commits on PR #75 carry `Claude-Session:`
  URLs for `session_016bZ7g5DPgSU8eYMTo8H5NY`,
  `session_01Nzct85anuqYiaChHWrojc7`, `session_01CuWKZ8HB6vaFGqxfYg8aVL`,
  and `session_01YVhNTy4brYqAztqcSzWLrs`; this reviewing session's own ID
  (`session_0197LBwCiZ8a682Vpm9iBzr8`) matches none of them — independent,
  not self-certification.
- **Real diff read directly**, not trusted from the PR body: 17 files,
  +2893/-155, centered on `apps/api/app/services/storage_semantics_client.py`
  (stdlib-only SigV4 client, no boto3/SDK dependency added),
  `spec/component-maintenance.yaml`, `infra/internal/storage-profile.yaml`,
  and the two test files.
- **CI checked for real on the actual head, twice:** `3526a0c` (run
  35263130329, job 105343546295) and, after this review found and fixed a
  recurring `mergeable_state: dirty` (PR #77's same-shift docs-only merge
  to `main` had advanced the PR's base again — the same structural issue
  FTF-0004/FTF-R011 already hit), the new merge commit `b6b3a68` (run
  35273905256, job 105379711235). Both job logs read directly, not the
  checkmark alone: genuine per-request S3 traffic against a live SeaweedFS
  3.99 container, real 200/400 BadDigest/403 AccessDenied/404 NoSuchKey
  responses across all 9 storage-semantics test functions, both ending
  `FTF-R017 storage semantics: all checks passed`.
- **Real, independent fault injection:** broke `_signing_key`'s AWS4
  key-derivation prefix (`"AWS4" + secret_key` -> `secret_key`) — re-ran
  `tests/tasks/test_ftf_r017.py`, failed for real with the exact expected
  signature-mismatch assertion, distinct from any check the producer
  already wrote. Restored byte-identical, re-ran clean.
- **Task contract re-pulled directly:** `tools/r1_handoff.py packet
  FTF-R017 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — both acceptance
  clauses and the negative_acceptance clause map one-to-one to specific,
  independently re-run tests (`test_minio_backend_is_rejected`,
  `test_unapproved_license_is_rejected`,
  `test_enterprise_or_proprietary_feature_use_is_rejected`,
  `test_missing_export_plan_is_rejected`,
  `test_missing_restore_plan_is_rejected`,
  `test_argilla_unsupported_deployment_note_is_present`,
  `test_unevidenced_qualified_claim_is_rejected`,
  `test_missing_semantic_is_rejected`), confirmed met, not asserted.
- **product_truth_check.py legitimately `0/0`:** `docs/spec-matrix.yaml`'s
  FTF-R017 entry carries `proving_symbol: ''` — no live Factory
  production service imports `storage_semantics_client.py` yet, same
  established, disclosed pattern as FTF-0004/FTF-R001/FTF-R011/FTF-R014/
  FTF-R015. A `0/0` pass here is legitimate, not an inflated claim.
- **Full regression** in a fresh, unshallowed worktree at the real merged
  head: `uv run ruff check`/`mypy .` clean; `pnpm install
  --frozen-lockfile` + `pnpm run lint`/`typecheck`/`format:check` clean;
  `validate_spec.py --strict` clean (a transient `stale_commit` defect
  seen mid-review traced to this review's own `--depth=50` shallow clone,
  confirmed spurious after `git fetch --unshallow`); `verify_task.py
  --task FTF-R017 --stage task` -> `PASS`; `verify_release.py` non-inflated
  (`16/300`, zero `FAILED`, up from `15/300`).
- **Conclusion:** the delivered SigV4 client, component-maintenance
  inventory and storage-profile qualification are real and independently
  reproduced. Merged PR #75 as `aacd745`. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`
  (`evidence_hash` kept at the implementation commit `b762359`, matching
  `spec/verification-map.json`'s own `commit` field — same convention as
  FTF-R011/FTF-R015). `scripts.task_claim.sync_certified_status()` run —
  diff limited to exactly FTF-R017's own `status`/`evidence` fields.
- **Validated-by:** session
  https://claude.ai/code/session_0197LBwCiZ8a682Vpm9iBzr8 (independent —
  see session-ID check above), 2026-09-17. Full ledger entry:
  `docs/certifications/ledger.md`.

## 2026-09-18 — FTF-0102 — Organization/workspace tenant schema
- **PR:** to be opened against `main` from `claude/wizardly-fermat-szi9tm`.
- **Commit:** `82c75f42231c32c2fa45988bb36e805534548ed1` (implementation).
- **Real DoD source:** `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolved via
  `python tools/r1_handoff.py packet FTF-0102 --plan FINE_TUNING_FACTORY_RELEASE_1.md`.
  Dependencies FTF-0004/FTF-0005/FTF-H001 all `CERTIFIED` — unblocked. The
  task's own new-runtime-dependency ask (SQLAlchemy + Alembic + a Postgres
  driver) was resolved 2026-09-18 by live owner confirmation in a direct
  conversation with the account holder (option 1, matching the master
  architecture table) — see `QUEUE.md`/`PROGRESS.md` for that record; PR
  #67's earlier, unverifiable self-reported claim of the same approval was
  separately closed as superseded, not relied on here.
- **Delivered:**
  - `apps/api/app/auth/organization_workspace_schema.py`: `Organization`/
    `Workspace` SQLAlchemy ORM models (Section 6.1/6.2) with constructor-
    level shape validation (slug pattern, name length, status enum) —
    `SLUG_PATTERN_SOURCE`/`NAME_MAX_LENGTH`/`ALLOWED_STATUSES` exposed
    (not underscore-prefixed) specifically so the frozen contract files
    below are tested against these exact values, not an independently
    retyped copy that could drift. `set_tenant_context()` issues a
    parameterized `SELECT set_config('app.current_organization_id',
    :org_id, true)` — never a string-formatted `SET`.
  - `apps/api/alembic/{alembic.ini,env.py,versions/0001_org_workspace_schema.py}`:
    the first real forward migration in this repo. Creates `organizations`/
    `workspaces` with the tenant-composite `UNIQUE (organization_id, slug)`
    constraint the acceptance clause requires, plus a distinct `sovra_app`
    role created `NOSUPERUSER NOBYPASSRLS` (Section 65.1: "application
    runtime role is not the table owner, a superuser or BYPASSRLS") with
    only row-level DML grants, and `FORCE ROW LEVEL SECURITY` on both
    tables with a `tenant_isolation` policy keyed off
    `NULLIF(current_setting('app.current_organization_id', true), '')::uuid`
    — a connection that never calls `set_tenant_context` sees/writes zero
    rows (`NULL = column` is never true), not every row. The role's
    password comes only from a required `SOVRA_APP_DB_PASSWORD` env var
    (no development-fallback credential), embedded into the `CREATE ROLE`
    DDL via Postgres dollar-quoting (`$sovra_app_pwd$...$sovra_app_pwd$`)
    rather than a bind parameter, because `CREATE ROLE ... PASSWORD` is a
    plain string-constant grammar position that does not accept one.
  - `packages/contracts/jsonschema/01-domain.schema.json` +
    `packages/contracts/openapi/01-interface.yaml`: the frozen phase-01
    shared interface/domain schemas the acceptance clause requires.
    `01-interface.yaml` declares `paths: {}` deliberately — no live
    FastAPI app exists yet (`apps/api/app` has no `main.py`, the same
    disclosed gap FTF-0006 already recorded), so declaring an HTTP route
    with no real handler would itself be a Law 11 placeholder; whichever
    task first wires a real `/api/v1` route adds its own path item here
    under the same `contracts:01` lock. `components.schemas` mirrors the
    JSON Schema file exactly (tested for drift, not just re-read).
  - `.github/workflows/ci.yml`: a new `organization-workspace-schema-integration`
    job (GitHub Actions' own `services: postgres` container, real registry
    egress on the hosted runner — matches the `postgres:16-alpine` digest
    FTF-0004 already pinned) running the real migration then
    `tests/integration/test_organization_workspace_schema.py`. Also added
    `uv run python3 tests/tasks/test_ftf_0102.py` to the existing `gate`
    job, and fixed a real, previously-latent gap this task's own
    dependency addition exposed: a bare `uv sync` only installs the root
    workspace package + dev group, **not** each member's own
    `dependencies` — confirmed empirically (it silently uninstalled
    apps/api's freshly-added sqlalchemy/alembic/psycopg the first time
    this task ran `uv sync` locally). Never mattered before because every
    prior task's `apps/api/pyproject.toml` had `dependencies = []`. Fixed
    with `uv sync --all-packages` in both the `gate` job and the new
    integration job.
- **Tests:**
  - `tests/tasks/test_ftf_0102.py` — 24 check functions, no DB required:
    domain-shape validation (bad slug/name/status rejected, valid shapes
    accepted), the tenant-composite unique constraint and FK are really
    declared on the ORM metadata, `set_tenant_context` genuinely uses a
    parameterized `set_config(...)` call (asserted against the real
    `MagicMock` call args, not the function's docstring), and static
    checks against the tracked migration file's own source text (FORCE
    ROW LEVEL SECURITY on both tables, `NOSUPERUSER`/`NOBYPASSRLS`, no
    hardcoded fallback password) plus the two frozen contract files
    (parses, Slug pattern/NAME_MAX_LENGTH/ALLOWED_STATUSES byte-identical
    to the real enforcing code, zero OpenAPI paths). Includes a tiny
    stdlib-only structural JSON-Schema-subset validator (type/pattern/
    maxLength/enum) used to prove real `Organization`/`Workspace`
    instances actually satisfy the frozen schema — not the third-party
    `jsonschema` package, which would itself be a second, unapproved
    new-runtime-dependency ask.
  - `tests/integration/test_organization_workspace_schema.py` — the real
    live-Postgres evidence: runs `alembic upgrade head` for real, seeds
    two real organizations/workspaces as the migration owner, then as the
    restricted `sovra_app` role proves (a) tenant context = org1 sees only
    org1's own rows, (b) missing tenant context sees zero rows, (c) a
    cross-tenant `organization_id` on `INSERT` is rejected, (d) an
    `UPDATE` retargeting an owned row to a different tenant is rejected,
    (e) an `UPDATE` reaching for a foreign tenant's row by ID affects zero
    rows (RLS filters it silently, a distinct code path from (c)/(d)), (f)
    a duplicate organization slug and a duplicate `(organization_id,
    slug)` are each rejected by real constraints, and (g) the same slug
    across two different organizations is allowed (tenant-composite, not
    global, uniqueness).
- **Evidence — shown able to fail (Law 1), against a real local PostgreSQL
  16 cluster (this sandbox has no container-registry egress to boot
  Postgres via Docker — confirmed again this task with a real `docker
  pull postgres:16-alpine`, same reproducible 403 FTF-0004/FTF-R017
  already documented; PostgreSQL 16 was already installed directly on
  this container, not via a container, which is what all of this
  evidence was produced against before being wired into CI's own
  `services: postgres` job):**
  1. Fixed a real bug this task's own first migration run surfaced before
     any fault injection: the initial revision ID
     (`0001_organization_workspace_schema`, 34 characters) overflowed
     Alembic's default `alembic_version.version_num VARCHAR(32)` column —
     `StringDataRightTruncation` on the very first `alembic upgrade head`.
     Shortened to `0001_org_workspace_schema` (25 characters), re-ran
     clean.
  2. Removing the explicit `WITH CHECK` clause from the workspaces policy
     (keeping only `USING`) was tried as a fault injection and found to
     **not** break anything — Postgres itself defaults an omitted `WITH
     CHECK` to the same expression as `USING` for a `FOR ALL` policy. Not
     a false claim left uncorrected: `tests/tasks/test_ftf_0102.py`'s own
     docstring and check description were written to state this
     explicitly (explicit `WITH CHECK` is redundant defense-in-depth, not
     something the omission was shown to exploit) rather than asserting a
     property that testing had just disproven.
  3. **The real, meaningful fault injection:** granting the `sovra_app`
     role `BYPASSRLS` instead of `NOBYPASSRLS` in a scratch copy of the
     migration made 6 of `tests/integration/test_organization_workspace_schema.py`'s
     checks genuinely fail (every tenant-isolation and negative_acceptance
     case — tenant-scoped visibility, missing-context denial, cross-tenant
     insert/update rejection all failed for real once RLS could be
     bypassed), confirming the suite actually depends on RLS being
     enforced rather than passing regardless. Restored the migration file
     byte-identical (`diff` empty) and re-ran both test files clean.
  4. A separate, targeted fault injection against `tests/tasks/test_ftf_0102.py`'s
     own static check (not the DB): removing the `FORCE ROW LEVEL
     SECURITY` line for `workspaces` from the tracked migration file made
     that suite's own check fail for real; restored byte-identical,
     re-ran clean — this file's own regression tests are proven, not
     decorative.
- **Regression check:** `uv run ruff check .`/`ruff format --check .`/
  `mypy .` clean (57 source files, including the 3 new/changed ones);
  `pnpm run lint`/`typecheck`/`format:check` clean (unaffected, no
  TypeScript touched); the full existing task-test set (FTF-0001 through
  FTF-R017, `tests/implementation/*`, `tests/security/*`) re-run clean in
  this same fresh container after `pnpm install --frozen-lockfile` +
  `git fetch --unshallow` (both needed for this exact container, matching
  the same documented environment-artifact pattern prior shifts have hit —
  confirmed spurious, not a real regression, by re-running clean
  immediately after); `uv run pytest tests/unit` (3 passed), `pnpm exec
  vitest run` (2 passed), `scripts/check_test_harness.py` clean,
  `scripts/validate_spec.py --strict` clean, `scripts/product_truth_check.py
  --fail-on-unwired` stays `0/0 product-true` (legitimate — no live
  FastAPI app imports `organization_workspace_schema.py` yet, same
  no-proving-symbol rationale as FTF-0002/0003/0004/0005/0006),
  `scripts/status.py` reports `16/300 certified · 0 product-true`
  (unchanged — this submission is PROVISIONAL, not yet certified).
- **`docs/spec-matrix.yaml`:** `FTF-0102` set to
  `certification_state: PROVISIONAL` / `proof_state: L1-PROVEN` /
  `evidence_hash: 82c75f42231c32c2fa45988bb36e805534548ed1`. Not touching
  `spec/verification-map.json`, `spec/tasks.json` or
  `docs/implementation/TRACKER.json` — those get their FTF-0102 entries
  from Control plane's own `sync_certified_status()` run at L2
  certification, same convention every prior task followed.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4, which
  should specifically re-verify the `organization-workspace-schema-integration`
  CI job passed for real (not merely that it exists) against a fresh
  registry-egress-capable runner, since this session's own sandbox could
  only produce this evidence against a directly-installed Postgres, not
  the pinned container image the CI job actually uses.

### Control plane review — 2026-09-18 — CERTIFIED
- **PR:** https://github.com/SJZLab/Sovra/pull/83 (`claude/wizardly-fermat-szi9tm`
  → `main`), merged as `1874932` (`18749323d091a6f6a23be2e9510692035911ac25`).
- **Session-ID check:** all 4 commits on PR #83 (`82c75f4`, `b2bb569`,
  `01ebde9`, `8521558`) carry `Claude-Session: .../session_01FLEHnA2RjFZB9RftzV4Lso`;
  this reviewing session's own ID does not match — independent, not
  self-certification.
- **Real diff read directly** in a dedicated worktree, not trusted from
  the PR body: 17 files, +2118/-25, centered on
  `apps/api/app/auth/organization_workspace_schema.py`,
  `apps/api/alembic/versions/0001_org_workspace_schema.py`, and the two
  test files.
- **CI checked for real on the actual head:** initial check (`01ebde9`)
  showed all wired jobs `in_progress`; re-checked after real independent
  work — head had advanced to `8521558` (tracking-doc-only, confirmed via
  `git diff`) with `mergeable_state: clean` and all 4 check runs
  `completed`/`success`. Read the
  `organization-workspace-schema-integration` job's log content directly:
  a genuine PostgreSQL 16.15 container, a real `alembic upgrade head` run,
  and the container's own Postgres log showing the actual RLS/
  unique-constraint errors the negative_acceptance test expects, ending
  `FTF-0102 real tenant-isolation proof: all checks passed`.
- **Real, independent reproduction against a live Postgres 16 cluster**
  (this sandbox's own directly-installed cluster, same no-registry-egress
  finding as FTF-0004/FTF-R017): ran the real migration, then
  `tests/integration/test_organization_workspace_schema.py` — all 11
  checks pass.
- **Real, independent fault injection matching the producer's own
  claim exactly:** granted `sovra_app` `BYPASSRLS` in a scratch copy of
  the migration — exactly 6 checks failed for real (every
  tenant-isolation/negative_acceptance case). Restored byte-identical,
  re-ran clean.
- **A second, independent fault injection** (not in the producer's own
  set): set the `workspaces` policy's `WITH CHECK` to `true`. The
  integration suite still passed — traced to Postgres enforcing the
  table's `SELECT` USING policy against the resulting row of both
  `INSERT ... RETURNING` (SQLAlchemy's default flush behavior) and
  `UPDATE`, independently of the (broken) `WITH CHECK`. A raw INSERT
  bypassing `RETURNING` — a code path that does not exist anywhere in
  this codebase — would exploit it. Not a blocking defect: the shipped
  migration's real `WITH CHECK` clauses are correct, and the only live
  application code path (ORM-only) is genuinely protected regardless.
  Recorded as a non-blocking observation in `docs/certifications/ledger.md`
  for whoever next touches this module's write paths.
- **Task contract re-pulled directly:** `tools/r1_handoff.py packet
  FTF-0102 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — both acceptance
  clauses and the negative_acceptance clause map one-to-one to specific,
  independently re-run tests and the fault injections above, confirmed
  met, not asserted.
- **product_truth_check.py legitimately `0/0`:** `proving_symbol: ''` —
  no live FastAPI app imports `organization_workspace_schema.py` yet,
  same established, disclosed pattern as FTF-0002/0003/0004/0005/0006/
  R001/R011/R014/R015/R017.
- **Full regression** in a fresh worktree at the real merged head: `uv run
  ruff check`/`mypy .` clean; `pnpm install --frozen-lockfile` + `pnpm run
  lint`/`typecheck`/`format:check` clean; every existing task test
  unmodified and clean; `validate_spec.py --strict` clean (a transient
  `stale_commit` defect traced to this review's own shallow clone,
  confirmed spurious after `git fetch --unshallow`); `verify_task.py
  --task FTF-0102 --stage task` -> `PASS`; `verify_release.py`
  non-inflated (`17/300`, zero `FAILED`, up from `16/300`).
- **Conclusion:** the delivered Organization/Workspace schema, migration
  and RLS tenant-isolation enforcement are real and independently
  reproduced. Merged PR #83 as `1874932`. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`
  (`evidence_hash` kept at the implementation commit `82c75f4`, matching
  `spec/verification-map.json`'s own new `commit` field). Added
  FTF-0102's entry to `spec/verification-map.json`.
  `scripts.task_claim.sync_certified_status()` run — diff limited to
  exactly FTF-0102's own `status`/`evidence` fields.
- **Validated-by:** session
  https://claude.ai/code/session_01WYf49qFkGAtWXsTMpiKbxu (independent —
  see session-ID check above), 2026-09-18. Full ledger entry:
  `docs/certifications/ledger.md`.

## PENDING — FTF-0104 (Audit event writer), PR #87, submitted 2026-09-18

Producer session: https://claude.ai/code/session_01FbdsNrj2DRZh92AB3edHUJ.
Implementation commit: `96e15980c1c375ed3f864b88024027ea4554e73b`.
`certification_state: PROVISIONAL` / `proof_state: L1-PROVEN` in
`docs/spec-matrix.yaml`. Awaiting independent Control-plane L2 review per
Law 4 (producer never certifies own work).

**What was delivered:**
- `apps/api/app/auth/audit_event_writer.py` — `AuditEvent` ORM model
  (Section 6.24) sharing FTF-0102's `Base`/metadata; `redact_details()`
  recursively redacts secret-shaped keys (case-insensitive) and
  secret-shaped values (Bearer/JWT-looking strings under an unexpected
  key) before construction; `write_audit_event()` only `session.add()`s
  — never commits — so the caller's own transaction boundary decides
  both-or-neither (this task's own "failed transaction cannot emit
  misleading success" negative_acceptance clause).
- `apps/api/alembic/versions/0002_audit_event_writer.py` — real forward
  migration, `down_revision = "0001_org_workspace_schema"` (single
  serialized chain, matching `database_changes`'s "Serialize migration
  chain" requirement); FORCE ROW LEVEL SECURITY on `audit_events` scoped
  to the same `app.current_organization_id` context FTF-0102 uses;
  `GRANT SELECT, INSERT ON audit_events TO "sovra_app"` — no `UPDATE`, no
  `DELETE` — enforcing append-only at the database's own GRANT surface
  (verified directly via `psql \dp` before any test was written:
  `sovra_app=ar/sovra_migrate`, i.e. SELECT+INSERT only).
- `tests/tasks/test_ftf_0104.py` (26 no-DB checks) +
  `tests/integration/test_audit_event_writer.py` (real live-Postgres
  proof, run against this sandbox's own directly-installed PostgreSQL 16
  — no container-registry egress here, same finding as
  FTF-0004/FTF-R017/FTF-0102).
- `.github/workflows/ci.yml`: new `gate`-job step + new
  `audit-event-writer-integration` job (real `services: postgres`
  container), mirroring `organization-workspace-schema-integration`.

**Fault injections (shown able to fail, Law 1), both restored
byte-identical and re-run clean:**
1. Removed `FORCE ROW LEVEL SECURITY` from the tracked migration file —
   the no-DB suite's own check failed for real.
2. Widened the migration's `GRANT` to `SELECT, INSERT, UPDATE, DELETE`
   and ran it against a fresh scratch Postgres database — exactly the 3
   append-only negative_acceptance checks in the live-Postgres suite
   failed for real (UPDATE rejected / DELETE rejected / row survives
   unchanged), no other check in that same run was affected.

**Full regression this shift** (same environment used throughout, not a
separate fresh worktree — flagging so the certifying session knows to
re-verify independently in its own): `ruff`/`mypy`/`eslint`/`tsc`/
`prettier`/`vitest`/`pytest` all clean; every existing task test
(FTF-0002 through FTF-R017) unmodified and still passing, including
FTF-0102's own two test files re-run clean against a fresh local Postgres
database (no regression to its RLS/tenant-isolation evidence);
`validate_spec.py --strict` clean (a transient `stale_commit` defect
traced to this session's own shallow clone, confirmed spurious after
`git fetch --unshallow`); `verify_task.py --task FTF-0104 --stage task`
correctly reports `BLOCKED` (no merged verification evidence yet —
expected at PROVISIONAL stage, matching FTF-0102's own precedent before
its L2 certification added the `spec/verification-map.json` entry, not a
defect); `product_truth_check.py --fail-on-unwired` legitimately `0/0`
(no live FastAPI app imports this module yet); `scripts/status.py`
unchanged at `17/300 certified`.

**Disclosed judgment call, flagged for the certifying session's own
review, not assumed correct:** this task's initial packet listed
`contracts:01` under `shared_lock_required`, but its concrete `files`
list and acceptance/negative_acceptance clauses require no HTTP-exposed
contract surface — no live FastAPI app exists yet to host an "audit read
API" route (same scope limit FTF-0102 already disclosed for its own 0
OpenAPI paths in `packages/contracts/openapi/01-interface.yaml`). Did not
touch `packages/contracts/{jsonschema,openapi}/01-*`.

**Also found and fixed** (before it reached `main`): this shift's own
drafted `docs/spec-matrix.yaml` notes text introduced two bare `word: `
sequences inside an unquoted YAML plain scalar, which broke
`yaml.safe_load` for the entire file — caught by this shift's own full
regression run (`tests/tasks/test_ftf_h034.py` failed with a raw
`yaml.scanner.ScannerError`). Reworded both to use `--` instead of `:`;
re-verified the whole file parses clean.

**Governance note (unrelated to this task's own evidence):** this shift
also closed PR #82 and PR #84 as stale/superseded — both predated
FTF-0102's real independent Control-plane certification (PR #85, already
on `main`) and carried `mergeable_state: dirty` against current `main`;
see PR #84's own closing comment for the full investigation (its premise
that PR #83 "was merged directly by the account owner... rather than
through Control-plane review" reflected a misreading of GitHub's merge
attribution, not an actual review gap — the real review is PR #85,
already merged).

PR: https://github.com/SJZLab/Sovra/pull/87

### Control plane review — 2026-09-18 — CERTIFIED
- **PR:** https://github.com/SJZLab/Sovra/pull/87 (`claude/wizardly-fermat-inchhv`
  → `main`), merged as `c682687` (`c682687af99230efcbbdd1942cefcf933844b826`).
- **Session-ID check:** all 5 commits on PR #87 (`96e1598`, `8ccbd21`,
  `8f8b0c9`, `46938eb`, `5efcbb9`) carry `Claude-Session: .../session_01FbdsNrj2DRZh92AB3edHUJ`;
  this reviewing session's own ID does not match — independent, not
  self-certification.
- **Real diff read directly** in a dedicated worktree at the real PR
  head: 9 files, +2611/-15, centered on
  `apps/api/app/auth/audit_event_writer.py` and
  `apps/api/alembic/versions/0002_audit_event_writer.py`, plus the two
  test files.
- **CI checked for real on the actual head:** the PR's head SHA advanced
  twice mid-review (further tracking-doc-only pushes while jobs were
  still `in_progress`); confirmed via `actions_get get_workflow_run` that
  the in-progress run's own `head_sha` matched the PR's real current head
  (`5efcbb9`) before trusting its result — all 5 check runs `completed`/
  `success`: `gate`, `docker-local-stack-integration`,
  `storage-semantics-integration`, `organization-workspace-schema-integration`,
  `audit-event-writer-integration`. Read the
  `audit-event-writer-integration` job's log content directly: a genuine
  PostgreSQL 16.15 container on GitHub's own registry-egress runner, real
  `ERROR: permission denied for table audit_events` lines for both the
  UPDATE and DELETE attempts the negative_acceptance test issues, ending
  `FTF-0104 audit event writer real proof: all checks passed`.
  `mergeable_state: clean`.
- **Real, independent reproduction against a live Postgres 16 cluster**
  (this sandbox's own directly-installed cluster, same no-registry-egress
  finding as FTF-0004/FTF-R017/FTF-0102): ran the real migration (0001
  then 0002 apply cleanly in sequence), inspected the real GRANT surface
  directly via `psql \dp audit_events` (`sovra_app=ar/sovra_migrate` —
  SELECT+INSERT only, matching the claim exactly), then
  `tests/tasks/test_ftf_0104.py` and
  `tests/integration/test_audit_event_writer.py` — all checks pass.
- **Own fault injection against the no-DB suite** (not in the producer's
  own set): removed the `actor_type` allow-list check from
  `AuditEvent.__init__` — exactly the "an unknown actor_type is rejected"
  check failed for real; restored byte-identical, re-ran clean.
- **Own fault injection directly against the live database** (not merely
  re-running the producer's own script): ran `GRANT UPDATE, DELETE ON
  audit_events TO sovra_app;` via `psql` against the scratch database,
  then re-ran the integration suite — exactly 3 append-only
  negative_acceptance checks failed for real (UPDATE rejected, DELETE
  rejected, row survives unchanged), matching the producer's own claimed
  fault-injection result exactly. Revoked the grant, re-ran clean.
- **Task contract re-pulled directly:** `tools/r1_handoff.py packet
  FTF-0104 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — both acceptance
  clauses and the negative_acceptance clause map one-to-one to specific,
  independently re-run tests and the fault injections above, confirmed
  met, not asserted. Section 6.24's own field list confirms "version" in
  the acceptance clause has no separate field in the real domain model —
  `action` is the field recording what changed, matching the shipped
  schema verbatim.
- **Disclosed scope note confirmed correct, not assumed:** the initial
  task packet's `shared_lock_required: contracts:01` does not apply in
  practice — this task's actual acceptance/negative_acceptance clauses
  and `files` list require no HTTP-exposed contract surface (no live
  FastAPI app exists yet), same disclosed, already-accepted scope limit
  as FTF-0102's own 0 OpenAPI paths.
- **Governance claims double-checked:** the PR body's claim that PR #82
  and PR #84 were closed as stale/superseded was confirmed directly via
  `pull_request_read` (`state: closed`, `merged: false` on both).
- **product_truth_check.py legitimately `0/0`:** `proving_symbol: ''` —
  no live FastAPI app imports `audit_event_writer.py` yet, same
  established, disclosed pattern as FTF-0002/0003/0004/0005/0006/R001/
  R011/R014/R015/R017/0102.
- **Full regression** in the same worktree, fresh `uv sync --all-packages`
  + `pnpm install --frozen-lockfile` + `git fetch --unshallow`: `uv run
  ruff check`/`mypy .` clean; `pnpm run lint`/`typecheck`/`format:check`
  clean; every existing task test unmodified and clean, including
  FTF-0102's own `tests/integration/test_organization_workspace_schema.py`
  re-run clean against the same fresh Postgres cluster (no regression to
  its own RLS/tenant-isolation evidence); `validate_spec.py --strict`
  clean; `verify_task.py --task FTF-0104 --stage task` -> `PASS`;
  `verify_release.py` non-inflated (`18/300`, zero `FAILED`, up from
  `17/300`).
- **Conclusion:** the delivered append-only, tenant-scoped audit event
  writer and its database-enforced GRANT surface are real and
  independently reproduced, including two fault injections not in the
  producer's own set. Merged PR #87 as `c682687`. `docs/spec-matrix.yaml`
  set to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`
  (`evidence_hash` kept at the implementation commit `96e1598`, matching
  `spec/verification-map.json`'s own new `commit` field). Added
  FTF-0104's entry to `spec/verification-map.json`.
  `scripts.task_claim.sync_certified_status()` run — diff limited to
  exactly FTF-0104's own `status`/`evidence` fields.
- **Validated-by:** session
  https://claude.ai/code/session_019oRVQmqyscTzBokcuJyXxw (independent —
  see session-ID check above), 2026-09-18. Full ledger entry:
  `docs/certifications/ledger.md`.

## FTF-1601 — Postgres backup — PROVISIONAL submission (2026-09-18)

**PR:** https://github.com/SJZLab/Sovra/pull/90 (`[relay] FTF-1601: Postgres
backup`), head commit `33f4cb5` (implementation commit `f5d3aee`).
**Producer session:** https://claude.ai/code/session_01B9eYtKy6gPX3Ym8Cvx3X9c.
**Dependencies:** FTF-0004, FTF-0102 — both CERTIFIED. This was QUEUE.md's
own flagged dependency-ready frontier item from the prior shift ("not
claimed this shift, certification-only"); claimed this shift.

**What was delivered:**
- `scripts/postgres_backup.py` — a stdlib-only backup/restore tool (no new
  pip dependency: `cryptography`/`pynacl` do not appear anywhere in this
  repo's `pyproject.toml`/`uv.lock`, confirmed by grep before starting;
  adding either would itself be an unapproved new-runtime-dependency
  ask-first item). Shells out to `pg_dump`/`pg_restore`/`psql`/`openssl`
  — OS binaries already present in this repo's Postgres tooling and CI
  environment (confirmed present via `which` before relying on them),
  never a new dependency.
  - `create_backup()`: real `pg_dump --format=custom` logical backup;
    `capture_metadata()` records the real server version, the live
    Alembic `alembic_version` head revision, public-schema table names,
    `pg_class.relforcerowsecurity` tables, `pg_policies` rows, and
    distinct `information_schema.role_table_grants` grantees (the
    "role/schema/RLS/version metadata" this task's acceptance clause
    requires); encrypts with `openssl enc -aes-256-cbc -pbkdf2` under
    `SOVRA_BACKUP_ENCRYPTION_KEY` (no development fallback, same rule as
    `SOVRA_APP_DB_PASSWORD`); then immediately self-verifies by
    decrypting the fresh ciphertext back and checksumming it against the
    original plaintext dump *before* ever returning a `BackupRecord` —
    this is this task's own negative_acceptance clause ("backup command
    success without verifiable usable data/key path is not done") made
    structural, not just tested for. The plaintext dump and the
    self-check scratch file are unlinked in a `finally` block regardless
    of success or failure; a failed self-verification also deletes the
    ciphertext/metadata sidecar so no half-verified artifact looks like a
    successful backup.
  - `restore_backup()`: verifies the backup file's own recorded
    `ciphertext_sha256` before ever decrypting, and the decrypted
    plaintext's `plaintext_sha256` before ever invoking `pg_restore` — a
    tampered file or the wrong key is rejected by checksum, never reaches
    `pg_restore`. After restoring, re-runs the identical metadata capture
    against the restored database plus real `SELECT count(*)` row counts,
    and computes `restoration_validated` as a real boolean conjunction of
    every one of those checks, not a bare exit-code pass-through.
  - Disclosed scope limit (not hidden): "role...metadata" means the role
    names this database's own `GRANT`s reference, not a `CREATE ROLE`
    capture — Postgres roles are cluster-wide, not per-database, and a
    full role-definition dump needs `pg_dumpall --roles-only` against
    privileges this per-database tool does not assume. Both this repo's
    local dev stack and its CI integration jobs run every task inside one
    already-provisioned cluster where `sovra_app`/`sovra_migrate` already
    exist cluster-wide, so this scope limit does not block the actual
    restoration test below.
- `packages/contracts/jsonschema/16-domain.schema.json` +
  `packages/contracts/openapi/16-interface.yaml`: frozen `BackupRecord`/
  `RestoreRecord` schemas, field shapes copied verbatim from the real
  enforcing dataclasses and tested against them directly (same
  discipline as FTF-0102's `01-*` contract files). This task's own
  acceptance clause 1 ("Freeze the minimum shared phase interface and
  domain schemas at the declared owner paths") and
  `contract_owner: FTF-1601` / `shared_lock_required: contracts:16`
  explicitly require this — unlike FTF-0104, where the same
  `shared_lock_required: contracts:01` boilerplate was disclosed as not
  literally applicable to that task's own concrete acceptance clauses.
  The OpenAPI file declares zero `paths` (Law 11 — no live FastAPI app
  exists yet), same disclosed pattern as every prior contract file in
  this repo.
- `tests/tasks/test_ftf_1601.py` — no Postgres required. Exercises the
  *real* `openssl` binary for genuine encrypt/decrypt round trips
  (correct-key round-trip and a wrong-key negative case), `sha256_file`/
  `key_fingerprint`/`_libpq_dsn`/`required_env` unit checks, and
  `create_backup()`/`restore_backup()`'s own control-flow logic via
  injected fake `dump_fn`/`metadata_fn` callables — including one fault
  injection that monkeypatches `_encrypt_file` to encrypt under a
  silently different key than the one used for self-verification (
  reproducing "the encrypt step used the wrong key" as a real code path,
  not a hypothetical) and confirms `create_backup()` genuinely raises and
  leaves no artifact behind, not merely that the encrypt call returned
  zero. Also validates real `BackupRecord`/`RestoreRecord` instances
  against the frozen contract files via a hand-rolled, stdlib-only
  structural validator (deliberately not the third-party `jsonschema`
  package — would itself be a second unapproved dependency, same
  reasoning FTF-0102's own suite already established), and confirms the
  validator genuinely rejects an unsupported cipher value and a
  non-boolean `restoration_validated` value (not a decorative
  always-pass check).
- `tests/integration/test_postgres_backup.py` — requires a real Postgres.
  Seeds one real `Organization` row through FTF-0102's own schema, takes
  a real encrypted backup of the live database, proves the wrong key and
  a tampered backup file (one flipped byte) are both rejected by
  `restore_backup()` before any `pg_restore` attempt against a real
  target database, then restores into a second, freshly created scratch
  database in the same cluster and proves the restored copy genuinely
  carries the `organizations` table, `FORCE ROW LEVEL SECURITY`, the
  `tenant_isolation` policy, and the exact seeded row (by primary key,
  name intact) — "a backup that has never been restored does not count
  as validated" (Section 71.2). Cleans up its own scratch database at the
  end.

**Local verification performed before opening the PR** (this sandbox has
no container-registry egress — same finding as
FTF-0004/FTF-R017/FTF-0102/FTF-0104 — so live evidence was produced
locally against a directly-installed PostgreSQL 16 cluster, `sovra_migrate`
granted `SUPERUSER` locally to match the `postgres:16-alpine` image's own
bootstrap-superuser `CREATE DATABASE` privilege the restore step needs):
- `uv run ruff check .` / `ruff format --check .` / `uv run mypy .` — all
  clean (63 source files).
- `pnpm run lint` / `format:check` / `typecheck` — all clean.
- Every `tests/tasks/test_ftf_*.py` run individually, including
  FTF-0102/FTF-0104's own no-DB suites — all pass, no regression.
- `tests/integration/test_organization_workspace_schema.py` and
  `tests/integration/test_audit_event_writer.py` re-run against the same
  local Postgres cluster this task's own integration test used — both
  pass, no regression to FTF-0102/FTF-0104's own live evidence.
- `tests/integration/test_postgres_backup.py` — passes for real against
  the live local cluster (see above).
- `uv run pytest tests/unit` (3 passed) + `pnpm exec vitest run` (2
  passed) + `scripts/check_test_harness.py` — clean.
- `python3 scripts/product_truth_check.py --fail-on-unwired` —
  legitimately `0/0` (`proving_symbol: ''`, no live FastAPI app imports
  `postgres_backup.py`, same established pattern as every non-product-true
  certified task).
- `python3 scripts/status.py` — `18/300 certified · 0 product-true`
  (unchanged; this PR is PROVISIONAL, not yet certified).
- `python3 scripts/validate_spec.py --strict` — clean (also caught and
  fixed a real defect this shift's own first draft of the
  `docs/spec-matrix.yaml` notes text introduced: two bare `word: `
  sequences inside an unquoted YAML plain scalar broke `yaml.safe_load`
  for the whole file — same class of defect FTF-0104's own history
  already hit once; fixed before this PR was opened, not left for
  review to catch).
- `python3 scripts/verify_task.py --task FTF-1601 --stage task` →
  `BLOCKED` ("no merged verification evidence yet") — correct and
  expected at this stage; `spec/verification-map.json` gets its FTF-1601
  entry from Control plane's own two-commit pattern at certification
  time, once the implementation commit already exists in history (same
  as every prior task).
- `python3 scripts/verify_release.py` — confirmed non-inflated: `18/300`,
  zero `FAILED`, `NOT READY`, identical to `main`'s own pre-existing
  state before this PR (verified by `git stash` + re-run on the
  unmodified tree) — this PR does not change the certified count on its
  own.
- CI on the actual PR head not yet observed by this producer session (PR
  just opened); Control plane's independent review should reconfirm
  green on the real head, per this repo's own established practice, and
  should specifically watch the new `postgres-backup-integration` job's
  `apt-get install postgresql-client-16` step, since it is the first job
  in this repo to install a Postgres client on the runner itself (every
  prior DB-integration job only ever spoke to Postgres via
  `psycopg`/SQLAlchemy).

**Not yet done (left for Control plane, per Law 4 — never self-certified):**
independent review of the diff, independent re-run of both the no-DB
suite and the live-Postgres integration test (ideally in a fresh worktree
against the actual PR head), an own fault injection distinct from the
ones in this PR's own test suite, confirmation CI is genuinely green on
the real merged head (not just this producer's local re-run), and — if
certified — `spec/verification-map.json`/`spec/tasks.json`/
`docs/implementation/TRACKER.json` updates via `sync_certified_status()`.

### Control plane review — 2026-09-18 — NOT CERTIFIED (structural blocker, content sound)
- **PR:** https://github.com/SJZLab/Sovra/pull/90 (`claude/wizardly-fermat-e9tyvg`
  → `main`), reviewed at head `f104ef60d16fbb009587c41c37bdadea6d8465e8`.
  Item: FTF-1601 (Postgres backup). Note: `main` does not yet carry an
  FTF-1601 `PROVISIONAL` entry above — its evidence writeup exists only on
  PR #90's own branch (same disclosed situation as the FTF-0004/PR #35
  precedent earlier in this file); reviewed from there directly.
- **Session-ID check:** all 4 commits on PR #90
  (`f5d3aee`, `33f4cb5`, `e8ca71d`, `f104ef6`) carry
  `Claude-Session: .../session_01B9eYtKy6gPX3Ym8Cvx3X9c`; this reviewing
  session's own ID does not match — independent, not self-certification.
- **Real diff read directly** in a dedicated worktree at the real PR head:
  7 substantive files (+1724/-6), centered on `scripts/postgres_backup.py`,
  its two test files, and the new `packages/contracts/{jsonschema,openapi}/16-*`
  contract pair.
- **CI checked for real on the actual head:** `get_check_runs` on PR #90
  → 6/6 `completed`/`success` (`gate`, `docker-local-stack-integration`,
  `storage-semantics-integration`, `organization-workspace-schema-integration`,
  `audit-event-writer-integration`, `postgres-backup-integration`);
  `actions_get get_workflow_run` on the run confirmed its own `head_sha`
  (`f104ef6`) matches the PR's real head before trusting the result — not
  a stale or self-reported claim.
- **Task contract re-pulled directly:** `tools/r1_handoff.py packet
  FTF-1601 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1
  (frozen contract + encrypted backup with role/schema/RLS/version
  metadata and a restoration test), acceptance clause 2 (independent
  review/exact-commit/real-integration/negative-test evidence, satisfied
  by this review itself), and the negative_acceptance clause ("backup
  command success without verifiable usable data/key path is not done")
  all map to specific, independently re-run tests below, confirmed met,
  not asserted. `database_changes: "No independent schema change"`
  respected — no Alembic migration file in this PR's diff.
- **Real, independent reproduction against a live Postgres 16 cluster**
  (this sandbox's own directly-installed cluster, same no-registry-egress
  finding as FTF-0004/FTF-R017/FTF-0102/FTF-0104; `sovra_migrate` granted
  `LOGIN SUPERUSER` locally to match the `postgres:16-alpine` image's own
  bootstrap-superuser `CREATE DATABASE` privilege the restore step needs):
  both `tests/tasks/test_ftf_1601.py` (no-DB) and
  `tests/integration/test_postgres_backup.py` (real backup, real restore
  into a second scratch database, real seeded-row survival) pass cleanly.
- **Own fault injection against the tracked module** (not merely
  re-running the producer's own script): patched `_encrypt_file` to
  encrypt under `key + "-INJECTED-FAULT"` while `create_backup`'s
  self-verification step still decrypts with the real, unmodified key —
  re-ran both suites: the no-DB suite failed for real at
  `test_create_backup_happy_path_produces_a_verifiable_artifact` with
  `BackupError: decryption failed -- wrong key or corrupted backup (...
  bad decrypt)`, and the live-Postgres integration test failed identically
  at the same `create_backup()` self-verification step. Restored
  byte-identical from a pre-edit copy (`diff` confirmed empty), re-ran —
  both suites clean again. This directly exercises the negative_acceptance
  clause end to end, live database included, not just the producer's own
  unit-level fault case (which targeted the same self-verification path
  via a different substitution).
- **Full regression** in the same worktree, fresh `uv sync --all-packages`
  + `pnpm install --frozen-lockfile`: `ruff`/`mypy`/`eslint`/`tsc`/
  `prettier`/`vitest`/`pytest` all clean; every existing task test
  unmodified and clean (FTF-0002 through FTF-R017, FTF-1810, FTF-H001/
  H002/H028/H034/R001), including FTF-0102's and FTF-0104's own live
  integration tests re-run clean against the same shared local Postgres
  cluster (no regression to their tenant-isolation/audit evidence).
  `validate_spec.py --strict` — one transient `stale_commit` defect
  traced to this worktree's own shallow clone, confirmed spurious after
  `git fetch --unshallow` (same class of issue this repo's own FTF-H034
  history already documents), clean thereafter. `verify_task.py --task
  FTF-1601 --stage task` → `BLOCKED` (no merged verification evidence
  yet — correct and expected pre-certification).
  `product_truth_check.py --fail-on-unwired` legitimately `0/0`
  (`proving_symbol: ''` on PR #90's own branch — no live FastAPI app
  imports `postgres_backup.py` yet, same established pattern as every
  prior non-product-true certified task). `verify_release.py`
  non-inflated (`18/300`, matching `main`'s current pre-existing state —
  this PR does not change the certified count on its own).
- **Contract files confirmed sound:** `packages/contracts/openapi/16-interface.yaml`
  declares zero `paths` (Law 11 — no live FastAPI app exists yet, same
  disclosed pattern as every prior contract file in this repo) and its
  `components.schemas` defines `BackupRecord`/`RestoreRecord`; both are
  validated by the suite's own hand-rolled, stdlib-only structural
  validator against real constructed instances.
- **`docs/spec-matrix.yaml` on PR #90's own branch correctly left at**
  `certification_state: PROVISIONAL` / `proof_state: L1-PROVEN` /
  `evidence_hash: f5d3aee56da747f52c335813fc03677052bf8949` (the real
  implementation commit) — not self-certified, correctly deferred to
  Control plane per Law 4.
- **Blocker — real, not a content defect:** attempted `merge_pull_request`
  on PR #90 (`expectedHeadSha: f104ef6`) — GitHub's merge API refused it:
  `405 Pull Request has merge conflicts`. Reproduced independently:
  `main` advanced from `6f330eb` (PR #90's recorded base) to `0c74f31`
  while this review was in progress — PR #91 (a same-day relay
  tracking-only shift, no code changes) merged in the interim, and both
  PRs touch `QUEUE.md`/`PROGRESS.md`. `git merge-tree --write-tree
  origin/main pr90-review` against the real current `main` reproduces
  genuine `CONFLICT (content)` markers in exactly those two files (pure
  tracking-doc content, not code) — not inferred from GitHub's status
  field alone. Same structural-blocker class as this file's own
  FTF-0004/PR #35 precedent above (2026-09-16): a tracking-doc-only
  divergence, not a defect in the delivered backup/restore code, which is
  independently confirmed sound by every check above.
- **Conclusion:** not merging, not certifying. `certification_state` for
  FTF-1601 stays `PROVISIONAL` as PR #90's own branch already states —
  not downgraded further, since nothing found here contradicts that L1
  evidence, which was produced independently of and is re-checkable
  regardless of `main`'s tip. **Action needed before resubmitting:**
  merge current `main` (`0c74f31` or later) into
  `claude/wizardly-fermat-e9tyvg`, resolve the `PROGRESS.md`/`QUEUE.md`
  conflicts (both pure tracking-doc conflicts, not code), push, and
  confirm CI stays green on the new merge commit before requesting
  certification again — no other part of this PR needs rework.
- **Validated-by:** session
  https://claude.ai/code/session_01Hqc673opTMxfCqsyXmGuoo (independent —
  see session-ID check above), 2026-09-18.

## FTF-1601 — Postgres backup — resubmitted under new PR #96, structural blocker resolved for real (2026-09-18)

**Background:** the resolution above (merging `main` into PR #90's own
branch, `claude/wizardly-fermat-e9tyvg`) worked once but the same
tracking-doc conflict recurred twice more over the following shifts as
`main` kept moving. Each of those shifts correctly declined to push
another fix directly to `e9tyvg`, since it was never their own designated
branch and CLAUDE.md forbids pushing to an unassigned branch without
explicit permission (no live user exists in a scheduled/automated shift to
grant it) — recorded in QUEUE.md/PROGRESS.md rather than worked around.

**This shift's action:** rather than record the same structural finding a
fourth time, merged FTF-1601's actual delivered code from
`claude/wizardly-fermat-e9tyvg` (a read of that branch's content, never a
push to it) into this session's own designated branch
(`claude/wizardly-fermat-vmg0oc`), confirmed via `git diff --stat` that
the code itself (`scripts/postgres_backup.py`,
`tests/integration/test_postgres_backup.py`, `tests/tasks/test_ftf_1601.py`,
the `16-*` contracts, the CI job, the task packet — 2,151 lines across 7
files) is byte-identical to what's already documented and reviewed above;
only `PROGRESS.md`/`QUEUE.md` needed manual conflict resolution.

**PR:** https://github.com/SJZLab/Sovra/pull/96 (supersedes #90, now
closed), real final head commit `2a98c9d4ed04c82cbd60b8640de0387d30a1a6fe`
on branch `claude/wizardly-fermat-vmg0oc` (the implementation-plus-merge
commit itself is `20aa4e5704d1c0da9d973c1b9f931d531324c63a`; one further
tracking-doc-only commit recording CI confirmation and PR #90's closure
followed).

**Regression re-run on the merged tree before pushing** (`uv sync
--all-packages` + `pnpm install --frozen-lockfile` + `git fetch
--unshallow` first, to avoid this sandbox's known shallow-clone
`stale_commit` false positive): `ruff check .` / `ruff format --check .` /
`mypy .` clean (63 files); `pnpm lint`/`typecheck`/`format:check` clean;
`uv run pytest tests/unit tests/tasks` — 209 passed, including FTF-1601's
own 14-check no-DB suite; `pnpm exec vitest run` — 2 passed; `uv run
python3 scripts/validate_spec.py --strict` clean; `uv run python3
scripts/status.py` — `18/300 certified · 0 product-true` (unchanged); `uv
run python3 scripts/product_truth_check.py --fail-on-unwired` — `0/0`
(unchanged); `uv run python3 scripts/verify_release.py` — `18/300`, zero
`FAILED` (unchanged). Did not re-run the live-Postgres integration test
locally — no code changed since the independent live-Postgres
reproduction already recorded above for this exact delivered code; PR
#96's own `postgres-backup-integration` CI job covers it on the real new
head.

**CI on the real final head (`2a98c9d`), confirmed directly via
`get_check_runs` (not inferred from a stale field):** all 6 jobs
`completed`/`success` (`gate`, `docker-local-stack-integration`,
`organization-workspace-schema-integration`,
`audit-event-writer-integration`, `postgres-backup-integration`,
`storage-semantics-integration`, run `35403943528`; also green on the
intermediate head `20aa4e5`, run `35403729104`, confirmed the same way
before the final doc-only commit landed). `pull_request_read get`
confirms `mergeable_state: "clean"`. PR #90 closed with an explanatory
comment linking here, rather than leaving two open PRs for the same task.

**Not self-certified, not merged:** this shift only unblocked the
structural conflict that had stalled three consecutive shifts and
resubmitted the exact same already-independently-reviewed-sound evidence
under a mergeable head — it did not certify or merge, per Law 4.
`certification_state` stays `PROVISIONAL`. **FTF-1601/PR #96 is ready for
a fresh, independent Control-plane L2 review** — the delivered evidence
itself needs no new content review (it is byte-identical to what a prior
Control-plane session already independently reproduced against a live
Postgres cluster, recorded above), only confirmation that this is in fact
the same code on a now-clean head.
- **Producer session (this continuation only — not the original
  implementation):** https://claude.ai/code/session_01RDefDg12JGVTtU732EEARs.

### Control plane review — 2026-09-18 — NOT CERTIFIED (real Law 1 gap: restoration completeness never proven able to fail)
- **PR:** https://github.com/SJZLab/Sovra/pull/96 (`claude/wizardly-fermat-vmg0oc`
  → `main`, supersedes closed PR #90), reviewed at real final head
  `902139b8f5a91191619f707600a5c347e98e8392`. The PR's own branch
  advanced twice mid-review (`20aa4e5` → `2a98c9d` → `902139b`, all three
  tracking-doc-only commits, no code delta) — re-fetched and re-checked
  CI on each new head rather than trusting the PR's self-reported "CI
  green" note, which itself referenced a since-superseded head.
- **Session-ID check:** all 11 commits on PR #96 carry
  `Claude-Session: .../session_01B9eYtKy6gPX3Ym8Cvx3X9c`,
  `.../session_01TkFfAaPq6htuVNNLXU9G2b`, or
  `.../session_01RDefDg12JGVTtU732EEARs`; this reviewing session's own ID
  (`session_01DPo17mjXAspEDoRvGvwtgn`) matches none of them — independent,
  not self-certification.
- **Real diff read directly** in a dedicated worktree (`git fetch
  origin refs/pull/96/head` + `git worktree add`) at the real PR head:
  code identical to PR #90's own already-reviewed delivery (confirmed via
  `git diff --stat` against `origin/main` — same 11 files, no drift), plus
  two additional tracking-doc-only commits recording the resubmission.
  `git merge-tree --write-tree origin/main <pr-head>` — clean, no
  conflict markers; `mergeable_state: "clean"` on the live PR — the prior
  review's structural blocker is genuinely resolved.
- **CI checked for real on the actual current head, not a stale claim:**
  `pull_request_read get_check_runs` on PR #96 initially showed CI
  `in_progress`/`queued` (the PR had just been opened when this session's
  webhook fired); waited for real completion rather than trusting the
  PR's own "CI reconfirmed green" commit, which named an intermediate
  head (`20aa4e5`/`2a98c9d`) that a later doc-only commit (`902139b`)
  superseded. `actions_get get_workflow_run` on the final run
  (`35404112803`) confirms its own `head_sha` is `902139b` — matching the
  PR's live head exactly, not inferred: all 6 jobs (`gate`,
  `docker-local-stack-integration`, `organization-workspace-schema-integration`,
  `audit-event-writer-integration`, `postgres-backup-integration`,
  `storage-semantics-integration`) `completed`/`success`.
- **Task contract re-pulled directly:** `tools/r1_handoff.py packet
  FTF-1601 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1
  ends in "...and restoration test"; Section 71.2 (embedded in the same
  packet) states explicitly "a backup that has never been restored does
  not count as validated." `RestoreRecord.restoration_validated` is this
  code's own chosen mechanism for satisfying that clause — a boolean
  conjunction of checksum verification plus three schema/RLS
  survival-subset checks (`tables`, `rls_forced_tables`, `rls_policies`).
- **Real, independent reproduction against a live Postgres 16 cluster**
  (this sandbox's own directly-installed cluster, same no-registry-egress
  finding as FTF-0004/FTF-R017/FTF-0102/FTF-0104/PR #90's own prior
  review): both `tests/tasks/test_ftf_1601.py` (14/14) and
  `tests/integration/test_postgres_backup.py` (real backup, real restore
  into a second scratch database, real seeded-row survival) pass cleanly
  on the actual PR content, fresh `uv sync --all-packages` +
  `pnpm install --frozen-lockfile`.
- **Own fault injection, distinct from the ones already in this PR's test
  suite and from the prior PR #90 review's own (which targeted the
  `create_backup` self-verification encrypt/decrypt path):** targeted
  `restore_backup()`'s `restoration_validated` conjunction itself
  (`scripts/postgres_backup.py`, the `and set(record["rls_policies"])...`
  term). Built a **real** scenario, not a mock: took a genuine encrypted
  backup of the live `sovra_ftf1601` database (real `organizations`/
  `workspaces`/`audit_events` tables, real `tenant_isolation` RLS
  policies), `pg_dump`/`pg_restore`'d it byte-for-byte into a second
  scratch database, then manually `DROP POLICY tenant_isolation ON
  organizations` on that scratch copy — a real, physically incomplete
  restoration (schema and data present, one required RLS policy silently
  missing). Called `restore_backup()` against that real scratch database
  with a no-op `restore_fn` (the "restore" already happened above) and
  the real, unmodified `metadata_fn` (queries the live scratch DB for
  real):
  - **Unpatched code (as shipped in this PR):** `restoration_validated`
    correctly comes back `False` — the code genuinely catches this real
    defect, exactly as its own docstring and Section 71.2 promise.
  - **With the RLS-policy-survival term removed from the conjunction**
    (`and set((p["table"], p["policy"]) for p in record["rls_policies"])
    .issubset(...)` deleted, everything else unchanged): the identical
    real scenario now reports `restoration_validated: True` — a silent
    false positive for a real, physically incomplete restore. Re-ran
    **both** `tests/tasks/test_ftf_1601.py` (14/14 still pass) and
    `tests/integration/test_postgres_backup.py` (still reports "all
    checks passed") against this faulted code — **neither test suite
    detects the fault.** Restored the file byte-for-byte from a pre-edit
    copy (`diff` confirmed empty) before finishing.
  - **Why neither existing test catches it:** the no-DB suite never
    exercises the happy-path restore-verification arithmetic at all (only
    the tamper/wrong-key *rejection* paths, which fail earlier via
    `pg_restore`'s/`openssl`'s own checksum-before-decrypt gates — those
    are genuinely covered). The live integration test's own restore
    always restores the *intact* backup, so it only ever exercises the
    conjunction's all-`True` branch; its one direct reference to
    `restoration_validated` (`tests/tasks/test_ftf_1601.py:578-583`) only
    proves the *field* is a real boolean, not that the *comparison logic
    producing it* can independently fail and be caught. The same
    reasoning applies symmetrically to the `tables` and
    `rls_forced_tables` subset terms — neither has negative-path coverage
    either, by the same gap.
- **Why this is a certification blocker, not a nice-to-have:** CLAUDE.md
  Law 1 is unconditional — "Nothing proven until test shown able to fail
  — evidence bound to criterion ID, test ID, commit, failure reason,
  hash." The acceptance clause's "restoration test" and Section 71.2's
  own stated purpose for `restoration_validated` existing at all is
  specifically to catch a restore that silently lost schema/RLS state —
  exactly the scenario reproduced above. That specific, load-bearing
  behavior has never been shown able to fail by any test this PR ships;
  it was only shown able to fail just now, in this review, against a
  temporarily-faulted copy of the code that was reverted immediately
  after. The shipped code is correct (independently confirmed above) —
  this is a real test-coverage gap on the acceptance criterion's own
  core mechanism, not a defect in the shipped logic itself.
- **Everything else checked out clean:** `ruff`/`mypy`/`eslint`/`tsc`/
  `prettier` all clean (63 source files); full existing regression
  (FTF-0002 through FTF-R017, FTF-1810, FTF-H001/H002/H028/H034/R001,
  FTF-0102/FTF-0104's own live integration tests) re-run with no
  regression; `validate_spec.py --strict` clean; `product_truth_check.py
  --fail-on-unwired` legitimately `0/0` (no live FastAPI app imports
  `postgres_backup.py` yet, same established pattern); `verify_release.py`
  — `18/300`, zero `FAILED`, matching `main`'s pre-existing state exactly
  (confirmed non-inflated via a from-scratch `uv sync --all-packages` +
  `pnpm install --frozen-lockfile` re-run — an initial run without
  `pnpm install` first showed 3 false `FAILED` entries from missing
  `node_modules`, not a real regression; resolved before relying on the
  result). PR #90 confirmed genuinely `closed`/`merged: false` (not
  reused, not silently abandoned). Contract files
  (`packages/contracts/{jsonschema,openapi}/16-*`) confirmed to match the
  real dataclasses field-for-field.
- **Conclusion:** not merging, not certifying. `certification_state` for
  FTF-1601 is not yet `PROVISIONAL` on `main` (this PR is unmerged;
  `docs/spec-matrix.yaml` on `main` still shows FTF-1601 as
  `certification_state: NONE` — nothing to downgrade there). On PR #96's
  own branch the PROVISIONAL entry stands as submitted — not altered by
  this review, since the *shipped code* is sound; what's missing is test
  coverage proving it. **Action needed before re-requesting
  certification:** add a negative test — no-DB is sufficient, following
  this repo's own established `dump_fn`/`metadata_fn`-injection pattern —
  that calls `restore_backup()` with a `metadata_fn` returning a restored
  state genuinely missing a table, a `FORCE ROW LEVEL SECURITY` flag, or
  an RLS policy the original `record` had, and asserts
  `restoration_validated is False` for each of the three subset terms
  independently (not just the checksum terms, which are already proven).
  Push that test to PR #96's own branch (no code change expected/needed —
  the underlying logic is already correct) and confirm CI stays green
  before resubmitting.
- **Validated-by:** this session (independent — see session-ID check
  above), 2026-09-18.

## 2026-09-19 — FTF-1601 (PR #96) — negative test added, no code change, resubmitted
- **Fixes exactly what the review above found:** four new checks in
  `tests/tasks/test_ftf_1601.py` — three independent negatives, one per
  `restoration_validated` subset term, plus one baseline positive:
  1. `test_restore_backup_reports_invalid_when_a_table_is_missing_from_the_restore`
  2. `test_restore_backup_reports_invalid_when_an_rls_forced_table_is_missing_from_the_restore`
  3. `test_restore_backup_reports_invalid_when_an_rls_policy_is_missing_from_the_restore`
  4. `test_restore_backup_reports_valid_for_a_genuinely_complete_restore` (baseline)
- **Scaffold:** `_restore_with_incomplete_metadata()` makes a real backup
  via `create_backup()` with the full `_fake_metadata` content, then
  restores it with a no-op `restore_fn` and a deliberately incomplete
  post-restore `metadata_fn` — the same `dump_fn`/`metadata_fn`-injection
  pattern this suite already uses elsewhere, per the review's own
  instruction.
- **A real wrinkle found and worked around, not silently avoided:**
  `restore_backup()`'s row-count step
  (`for table in row_count_tables or record["tables"]: ...
  _psql_rows(...)`) queries a live database directly for any table both
  the original backup and the restored metadata agree exists —
  unconditionally, regardless of `restoration_validated`'s own result,
  and unrelated to it (a separate reporting field). Passing
  `row_count_tables=[]` does **not** skip this — `[] or record["tables"]`
  evaluates to `record["tables"]` because an empty list is falsy in
  Python, a real quirk of the existing default-argument pattern, not a
  new one introduced by this fix. Worked around by monkeypatching
  `_psql_rows` for the scaffold's own scope only, the same way
  `test_create_backup_self_verification_catches_a_broken_encrypt_path`
  (already in this file, from the original PR) monkeypatches
  `_encrypt_file` — save the original, patch, restore in `finally`. This
  does not touch `restoration_validated`'s own computation in any way; it
  only avoids a network call this no-DB suite cannot make.
- **Evidence — shown able to fail (Law 1), reproducing the reviewer's
  exact finding three times over, once per subset term:** temporarily
  removed each of the three conjunction terms from
  `scripts/postgres_backup.py`'s `restoration_validated` in turn —
  `set(record["tables"]).issubset(set(tables_restored))`,
  `set(record["rls_forced_tables"]).issubset(set(rls_forced_tables_verified))`,
  and the `rls_policies` subset check — re-running the full suite after
  each single-term removal:
  - Removing the `tables` term: exactly
    `test_restore_backup_reports_invalid_when_a_table_is_missing_from_the_restore`
    failed (`FAIL [FTF-1601] restoration_validated is False when the
    restored database is missing a table the original backup recorded`);
    no other check failed.
  - Removing the `rls_forced_tables` term: exactly
    `test_restore_backup_reports_invalid_when_an_rls_forced_table_is_missing_from_the_restore`
    failed; no other check failed.
  - Removing the `rls_policies` term: exactly
    `test_restore_backup_reports_invalid_when_an_rls_policy_is_missing_from_the_restore`
    failed; no other check failed.
  Restored the file byte-identical from a pre-edit copy (`diff` confirmed
  empty) after each injection, re-ran clean (all 18 `test_ftf_1601.py`
  checks pass) before moving to the next.
- **Merge, not a fresh conflict:** current `main` (`0233470`) had moved
  past this branch's base while the review above was landing there;
  merged it into `claude/wizardly-fermat-vmg0oc` — conflicts confined to
  `QUEUE.md`/`docs/certifications/queue.md` (both pure tracking-doc
  prose, resolved chronologically, keeping both sides' entries, nothing
  dropped), zero conflict or drift in any code file.
- **Full regression on the merged-and-fixed tree** (`uv sync
  --all-packages` + `pnpm install --frozen-lockfile` + `git fetch
  --unshallow` first): `ruff check .`/`ruff format --check .`/`mypy .`
  clean (63 files); `pnpm lint`/`typecheck`/`format:check` clean; `uv run
  pytest tests/unit tests/tasks` — 213 passed (209 before this fix; +4,
  all in `test_ftf_1601.py`, now 18/18); `pnpm exec vitest run` — 2
  passed; `uv run python3 scripts/validate_spec.py --strict` clean; `uv
  run python3 scripts/status.py` — `18/300 certified · 0 product-true`
  (unchanged); `uv run python3 scripts/product_truth_check.py
  --fail-on-unwired` — `0/0` (unchanged); `uv run python3
  scripts/verify_release.py` — `18/300`, zero `FAILED`, `NOT READY`
  (unchanged). Did not re-run the live-Postgres integration test locally
  — no code changed, and the live-Postgres reproduction of this exact
  `restoration_validated` behavior is already independently recorded in
  the review above; PR #96's own `postgres-backup-integration` CI job
  re-covers it on the real new head regardless.
- **Pushed:** commit `353b82bab5827ad95998644f1151d98c1dcaf541` on branch
  `claude/wizardly-fermat-vmg0oc` (PR #96 unchanged, new commit; base
  merge landed first as `f3a3b49`).
- **CI reconfirmed green on that exact head, checked directly (not
  inferred from the `check_suite.completed` webhook alone):**
  `get_check_runs` against PR #96 — all 6 jobs `completed`/`success`
  (`gate`, `docker-local-stack-integration`,
  `organization-workspace-schema-integration`,
  `audit-event-writer-integration`, `postgres-backup-integration`,
  `storage-semantics-integration`), run `35411323316`.
  `pull_request_read get` confirms `mergeable_state: "clean"`;
  `get_comments` returns zero open review comments.
- **State:** `proof_state=L1-PROVEN`, `certification_state=PROVISIONAL`
  (unchanged — a scoped test-only fix per the review's own instruction,
  "no code change expected/needed"). Not self-certified — this relay
  session cannot certify its own fix; awaiting a fresh Control-plane L2
  review with no session-ID overlap with this one.

### Control plane review — 2026-09-19 — NOT CERTIFIED (Law 1 gap now closed; new structural blocker — merge conflict against current main)
- **PR:** https://github.com/SJZLab/Sovra/pull/96 (`claude/wizardly-fermat-vmg0oc`
  → `main`), reviewed at real head `19e18de098df77f5898e8bbaab9bec090f9ef1d2`.
  Triggered as a scheduled CERTIFY-mode session by PR #98's own
  `pull_request.opened` webhook (PR #98 itself is a tracking-doc-only
  relay shift confirming PR #96's fix was already pushed — no code, not
  itself a certification submission).
- **Session-ID check:** PR #96's 14 commits carry
  `Claude-Session: .../session_01B9eYtKy6gPX3Ym8Cvx3X9c`,
  `.../session_01TkFfAaPq6htuVNNLXU9G2b`, or
  `.../session_016ryLQ1vSJCNYTMHw9DrcSd`; this reviewing session's own ID
  (`session_01Ua5bz7foHCUWqcMhr5zSei`) matches none of them — independent,
  not self-certification.
- **Real diff read directly** in a dedicated git worktree at the real PR
  head (`git fetch origin claude/wizardly-fermat-vmg0oc` +
  `git worktree add`): 11 files, +3081/-53 against `origin/main`, content
  identical to the already-reviewed PR #90/#96 delivery plus the new
  353b82b fix commit (141 lines added to `tests/tasks/test_ftf_1601.py`
  only — four new checks, no production code touched, confirmed via
  `git show 353b82b --stat`).
- **CI checked for real on the actual head, not a stale claim:**
  `actions_get get_workflow_run` on run `35411607447` confirms its own
  `head_sha` is `19e18de0` — matching the PR's real head exactly: all 6
  jobs (`gate`, `docker-local-stack-integration`,
  `organization-workspace-schema-integration`,
  `audit-event-writer-integration`, `postgres-backup-integration`,
  `storage-semantics-integration`) `completed`/`success`.
  `get_review_comments` — zero open threads.
- **The prior review's exact Law 1 gap, independently re-verified as
  closed, not merely re-run:** the 353b82b fix adds a no-DB negative test
  per `restoration_validated` subset term (missing table, missing FORCE
  ROW LEVEL SECURITY flag, missing RLS policy) plus a baseline positive.
  Ran `tests/tasks/test_ftf_1601.py` as CI actually invokes it (bare
  `python3 tests/tasks/test_ftf_1601.py`, not `pytest` — this file is
  outside `pyproject.toml`'s `testpaths` and its own `check()` helper
  only appends to a module-level `FAILURES` list that `main()` checks at
  the end; running it under plain `pytest` trivially "passes" every
  `test_*` function regardless of the actual condition, which would have
  been a false-negative verification if not caught): 18/18 checks pass.
  Then performed a genuine fault injection distinct from re-running the
  producer's own: removed each of the three subset terms from
  `restoration_validated`'s boolean conjunction in
  `scripts/postgres_backup.py` **one at a time** (not the encrypt/decrypt
  path the PR #90 review already exercised) and re-ran the real script
  after clearing `__pycache__` each time (a first pass without clearing
  bytecode cache produced a misleading stale-`.pyc` false failure on the
  pristine baseline — caught and corrected before relying on any result):
  removing the `tables` term fails exactly
  `test_restore_backup_reports_invalid_when_a_table_is_missing_from_the_restore`;
  removing the `rls_policies` term fails exactly
  `test_restore_backup_reports_invalid_when_an_rls_policy_is_missing_from_the_restore`;
  removing the `rls_forced_tables` term fails exactly
  `test_restore_backup_reports_invalid_when_an_rls_forced_table_is_missing_from_the_restore`.
  Each injection restored byte-identical (`diff` empty) before the next,
  and the pristine file re-passes 18/18 both before and after the series.
  This is the same real defect class the 2026-09-18 review demonstrated
  against a live Postgres cluster, now independently confirmed caught by
  a no-DB unit test — the Law 1 gap is genuinely closed.
- **Task contract re-pulled directly:** `tools/r1_handoff.py packet
  FTF-1601 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1
  ("...restoration test"), Section 71.2's "a backup that has never been
  restored does not count as validated", and the negative_acceptance
  clause ("Backup command success without verifiable usable data/key path
  is not done") all map to the specific tests and fault injections above
  and to the encrypt/self-verification evidence the 2026-09-18 review
  already independently reproduced against a live Postgres cluster (code
  unchanged since then) — confirmed met, not asserted.
- **Full regression, fresh environment:** `uv sync --all-packages` +
  `pnpm install --frozen-lockfile` + `git fetch --unshallow` (this
  sandbox's shallow clone otherwise produces spurious `stale_commit`
  defects in `validate_spec.py --strict` — reproduced the false positive
  first, then confirmed clean after unshallowing, same known artifact
  this repo's own history already documents repeatedly). `ruff check .` /
  `mypy .` clean (63 files). Every existing `tests/tasks/test_ftf_*.py`
  and `tests/test_ftf_0001_monorepo_layout.py` re-run exactly as CI
  invokes each (bare `python3` or `uv run python3` per file) — all pass,
  no regression. `product_truth_check.py --fail-on-unwired` — `0/0`,
  confirmed legitimate via `docs/spec-matrix.yaml`'s own
  `proving_symbol: ''` for FTF-1601 (no live FastAPI app imports
  `postgres_backup.py` yet, same established disclosed pattern as every
  prior non-product-true certified task).
- **New structural blocker found — real, not a content defect:** while
  this review was in progress, PR #98 (a same-day relay tracking-doc-only
  shift, no code changes) merged into `main` (`0233470` → `a0448c6`).
  `pull_request_read get` on PR #96 now reports `mergeable_state:
  "unknown"` (GitHub recomputing); reproduced independently via
  `git fetch --unshallow origin && git fetch origin main && git
  merge-tree --write-tree origin/main <PR-96-head>` against the real
  current `main` tip — genuine `CONFLICT (content)` markers in exactly
  `PROGRESS.md` and `QUEUE.md` (pure tracking-doc content, not code; the
  11 real delivery files carry no conflict). Same structural-blocker class
  as this file's own FTF-0004/PR #35 and FTF-1601/PR #90 precedents.
- **Conclusion:** not merging, not certifying. `certification_state` for
  FTF-1601 stays `NONE` on `main` (unchanged — `main` never carried a
  PROVISIONAL entry for this task; nothing to downgrade). On PR #96's own
  branch the PROVISIONAL entry stands as submitted, and nothing found
  here contradicts it — the previously-identified Law 1 gap is now
  genuinely closed and every other check holds up under fresh, adversarial
  fault injection. **Action needed before resubmitting (relay, not
  Control plane — Law 4):** merge current `main` (`a0448c6` or later)
  into `claude/wizardly-fermat-vmg0oc`, resolve the `PROGRESS.md`/
  `QUEUE.md` conflicts (both pure tracking-doc conflicts, not code), push,
  and confirm CI stays green on the new merge commit before requesting
  certification again — no other part of this PR needs rework. On the
  next review, Control plane should not need to re-run the live-Postgres
  fault injection or the encrypt/self-verification checks again absent a
  code change — only re-confirm CI green on the new merge commit and that
  the merge itself introduced no code delta.
- **Validated-by:** this session (independent — see session-ID check
  above), 2026-09-19.

## 2026-09-19 — FTF-1601: structural blocker resolved, resubmitted from a new branch/PR
- **Actioned the review above's exact instruction:** merge current `main`
  into the branch carrying FTF-1601's code, resolve the tracking-doc
  conflicts, push, confirm CI green, resubmit.
- **Branch note:** this relay session's own designated branch is
  `claude/wizardly-fermat-rlp631`, not PR #96's
  `claude/wizardly-fermat-vmg0oc`. Per CLAUDE.md ("never push to a
  different branch without explicit permission") and this repo's own
  established precedent for the identical situation (PR #90 →
  `e9tyvg` superseded by PR #96 on `vmg0oc`, rather than pushing to a
  branch that shift wasn't assigned), read `vmg0oc`'s content (FTF-1601's
  delivered code, unchanged) and merged it into this session's own
  branch instead of pushing to `vmg0oc` directly.
- **Conflict resolution:** all three conflicts (`PROGRESS.md`, `QUEUE.md`,
  `docs/certifications/queue.md`) were between two logs of the same
  adjacent FTF-1601 events from different vantage points — no competing
  edits to the same fact. Resolved by interleaving both sides' entries in
  chronological order (cross-checked against each entry's own timestamps
  and referenced commit SHAs), keeping every entry, dropping nothing,
  touching zero code.
- **Full regression, fresh environment** (`uv sync --all-packages` +
  `pnpm install --frozen-lockfile`; repo was already a full, non-shallow
  clone): `ruff check .` / `ruff format --check .` / `mypy .` — clean (63
  files). `pnpm lint` / `pnpm typecheck` / `pnpm format:check` — clean.
  `uv run pytest tests/unit tests/tasks` — 213 passed (unchanged,
  includes FTF-1601's own 18/18). `pnpm exec vitest run` — 2 passed. `uv
  run python3 scripts/validate_spec.py --strict` — clean. `uv run python3
  scripts/status.py` — `18/300 certified · 0 product-true` (unchanged).
  `uv run python3 scripts/product_truth_check.py --fail-on-unwired` —
  `0/0` (unchanged). `uv run python3 scripts/verify_release.py` —
  `18/300`, zero `FAILED`, `NOT READY` (unchanged). Did not re-run the
  live-Postgres integration test locally — no code changed since Control
  plane's own independent live-Postgres reproduction of
  `restoration_validated`'s fixed behavior; the new PR's own
  `postgres-backup-integration` CI job covers it on the real new head.
- **Pushed:** commit `bc8868d` to `origin/claude/wizardly-fermat-rlp631`.
  Opening a new PR from this branch to supersede #96 (identical delivered
  code and evidence, now conflict-free against current `main`), then
  closing #96 with a comment pointing to it — same pattern as the
  #90→#96 supersession.
- **State:** `proof_state=L1-PROVEN`, `certification_state=PROVISIONAL`
  (unchanged — a merge-only fix, no code delta). Not self-certified —
  this relay session cannot certify its own fix; awaiting a fresh
  Control-plane L2 review with no session-ID overlap with this one.
- **Resolved:** opened https://github.com/SJZLab/Sovra/pull/100
  (`claude/wizardly-fermat-rlp631` → `main`) carrying this fix. CI
  confirmed green directly via `get_check_runs`/`get_workflow_run` on the
  real head `8496133a1e6d2dd02b2855e64cedee1ef995dd01`, run `35422556230`
  — 6/6 jobs `completed`/`success` (`gate`, `docker-local-stack-
  integration`, `organization-workspace-schema-integration`,
  `audit-event-writer-integration`, `postgres-backup-integration`,
  `storage-semantics-integration`); `pull_request_read get` confirms
  `mergeable_state: "clean"`, 11 files changed (+3203/-31, same delivered
  content as PR #96 plus the tracking-doc merge). Closed PR #96 with a
  comment pointing here (same #90→#96 supersession pattern). FTF-1601 is
  ready for a fresh, independent Control-plane L2 review — awaiting Law 4
  certification.

### Control plane review — 2026-09-19 — CERTIFIED
- **PR:** https://github.com/SJZLab/Sovra/pull/100 (`claude/wizardly-fermat-rlp631`
  → `main`), reviewed at real final head `4a2f3d38038ad70a93e8a1c6fd8c02a8dbfd0ee1`,
  merged as `f4e9b39` (`f4e9b39b5cb9e8cc1222dde8e03f9e4d6cf9dbf4`).
- **Session-ID check:** every commit unique to this PR over `origin/main`
  carries a `Claude-Session` trailer for session_01B9eYtKy6gPX3Ym8Cvx3X9c,
  session_01RDefDg12JGVTtU732EEARs, session_01TkFfAaPq6htuVNNLXU9G2b,
  session_016ryLQ1vSJCNYTMHw9DrcSd, or session_015rZiy6EjN6jvYYWzkGE2gh;
  this reviewing session's own ID (`session_01BpsxwNZKMCes4UuKAnRvSG`)
  matches none of them — independent, not self-certification.
- **Real diff read directly:** `git log origin/main..origin/claude/wizardly-fermat-rlp631`
  shows this PR carries the exact same implementation commit `f5d3aee`
  (encrypted, self-verified, RLS-surviving Postgres backup/restore) and
  fix commit `353b82b` (missing `restoration_validated` negative test)
  already independently reviewed and found sound by two prior, distinct
  Control-plane sessions — PR #90 (2026-09-18, structural-blocker-only,
  content sound) and PR #96 (2026-09-19, Law 1 gap on
  `restoration_validated`'s three subset terms, independently confirmed
  closed by `353b82b` via fresh fault injection against a live check).
  `git diff 19e18de 8496133 --stat` and a re-check against the PR's final
  head (`4a2f3d3`) confirm the only delta beyond that already-reviewed
  content is `PROGRESS.md`/`QUEUE.md`/`docs/certifications/queue.md`
  (tracking-doc merge-conflict resolution) — zero production code, test,
  or contract files touched.
- **CI checked for real on the actual current head, not a stale claim:**
  the PR advanced mid-review (`8496133` → `4a2f3d3`, a further doc-only
  commit) — re-fetched and re-checked rather than trusting the PR's own
  self-reported "CI green" note, which named the superseded head.
  `actions_get get_workflow_run` on run `35422662189` confirms its own
  `head_sha` is `4a2f3d3` — matching the PR's live head exactly: all 6
  jobs (`gate`, `docker-local-stack-integration`,
  `organization-workspace-schema-integration`,
  `audit-event-writer-integration`, `postgres-backup-integration`,
  `storage-semantics-integration`) `completed`/`success`, including the
  real live-Postgres `postgres-backup-integration` job on GitHub's own
  registry-egress runner. `get_review_comments` — zero open threads.
  `mergeable_state: clean`.
- **Task contract re-pulled directly:** `tools/r1_handoff.py packet
  FTF-1601 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1
  ("...restoration test"), Section 71.2 ("a backup that has never been
  restored does not count as validated"), and the negative_acceptance
  clause ("Backup command success without verifiable usable data/key path
  is not done") all map to the specific tests and fault injections the
  2026-09-18/2026-09-19 reviews already independently reproduced against a
  live Postgres cluster and a real no-DB fault injection — confirmed met,
  not asserted, and unaffected by this PR's tracking-doc-only delta.
- **Own regression** in a dedicated worktree at the real merged head
  (`f4e9b39`), fresh `uv sync --all-packages` + `pnpm install
  --frozen-lockfile` + `git fetch --unshallow`: `ruff check .` / `mypy .`
  clean (63 files); `pnpm run lint`/`typecheck`/`format:check` clean; `uv
  run python3 tests/tasks/test_ftf_1601.py` — "all checks passed" (18/18,
  exactly as CI invokes it); `uv run pytest tests/unit` — 3 passed; `pnpm
  exec vitest run` — 2 passed; `uv run python3 scripts/validate_spec.py
  --strict` clean; `product_truth_check.py --fail-on-unwired` legitimately
  `0/0` (`proving_symbol: ''` — no live FastAPI app imports
  `postgres_backup.py` yet, same established disclosed pattern as every
  prior non-product-true certified task). Did not re-run the live-Postgres
  integration test or repeat the encrypt/self-verification and
  `restoration_validated` fault injections locally — no code changed since
  the two prior independent Control-plane reviews already reproduced them
  against a live cluster and CI's own `postgres-backup-integration` job
  re-confirmed the same real `pg_dump`/`pg_restore`/`openssl` path green
  on this exact head.
- **Environment artifact noted, not a regression:** a bare (no `uv run`)
  `verify_release.py` run in a fresh worktree with no local Postgres
  cluster running showed `FTF-0102`/`FTF-0104` as false `FAILED`
  (`ModuleNotFoundError: No module named 'sqlalchemy'` — bare `python3`
  isn't the `uv` venv in this sandbox). `uv run python3
  tests/tasks/test_ftf_0102.py`/`test_ftf_0104.py` both independently
  re-run clean once invoked the way CI/the `uv` venv actually does. Not
  this PR's concern (touches neither task's files); noted for the record,
  not acted on further.
- **Conclusion:** the delivered encrypted, self-verified, RLS-surviving
  Postgres backup/restore tooling is real, was already independently
  reproduced against a live Postgres cluster and adversarially
  fault-injected by two prior, distinct Control-plane sessions across PR
  #90 and PR #96, and this PR's only delta over that already-sound content
  is a tracking-doc merge-conflict resolution against current `main`,
  confirmed here to introduce zero code change and to stay green on real
  CI at its actual final head. Merged PR #100 as `f4e9b39`.
  `docs/spec-matrix.yaml` set to `certification_state: CERTIFIED` /
  `proof_state: L2-CERTIFIED` (`evidence_hash` kept at the implementation
  commit `f5d3aee`, matching `spec/verification-map.json`'s own new
  `commit` field). Added FTF-1601's entry to `spec/verification-map.json`.
  Full ledger entry: `docs/certifications/ledger.md`.
- **Validated-by:** session
  https://claude.ai/code/session_01BpsxwNZKMCes4UuKAnRvSG (independent —
  see session-ID check above), 2026-09-19.

## 2026-09-19 — FTF-0101 (PR #103) — Control plane review: NOT CERTIFIED (PR stays open, unmerged)
- **PR:** https://github.com/SJZLab/Sovra/pull/103
  (`claude/wizardly-fermat-c0i7xl` → `main`), triggered for review by that
  PR's own `pull_request.opened` webhook. This entry is written on `main`
  because PR #103 itself is not certified and stays open/unmerged — its
  own submission writeup (delivered code, evidence, and disclosed gaps)
  lives on its own branch, not here; this entry only records this
  session's independent review and verdict.
- **Session-ID check:** all four commits unique to PR #103 over
  `origin/main` (`6b4c8ab`, `9b85fa7`, `a3817fe`, `513538e`) carry a
  `Claude-Session` trailer for session_015u9WhWDyzkFgkk74HzHJ9d; this
  reviewing session's own ID (`session_01VNPVrAHoQTUFSmfhV4UkCU`) matches
  none of them — independent, not self-certification.
- **Real diff read directly:** read `apps/api/app/auth/oidc_login.py`,
  `apps/api/app/auth/oidc_settings.py`, and `apps/api/app/main.py` in full
  in a dedicated worktree at the PR's real head (`513538e`, which the PR
  advanced to mid-review — a doc-only fix to a stale evidence_hash note in
  this same file on that branch; `git diff a3817fe 513538e -- .
  ':!docs/certifications/queue.md'` confirmed empty, zero code delta).
  Confirms the PR's own description: real signature verification via JWKS
  (`alg` allowlisted to `{RS256, ES256}` and checked *before* key lookup),
  authlib `claims_options`/`validate()` for `iss`/`aud`/`exp`/`sub`, nonce
  checked against a signed txn cookie, itsdangerous-signed HttpOnly/
  Secure/SameSite=Lax session cookie, real in-memory per-process logout
  revocation keyed by `jti`, and CSRF Origin+token enforcement on
  `POST /auth/logout`. `apps/api/app/main.py` wires
  `build_oidc_login_router` in unconditionally (not behind a flag) — a
  real product-runtime caller.
- **CI checked for real on the actual current head:** re-fetched after the
  PR advanced mid-review rather than trusting a stale claim.
  `actions_get get_workflow_run` on run `35429139124` confirms
  `head_sha: 513538e0e7da77401d82ea363580163f4ba05a22` — matches the PR's
  live head exactly, `conclusion: success`; all 6 jobs (`gate`,
  `docker-local-stack-integration`,
  `organization-workspace-schema-integration`,
  `audit-event-writer-integration`, `postgres-backup-integration`,
  `storage-semantics-integration`) `completed`/`success`.
- **Own fault injection, independent of the producer's own tests:** in a
  dedicated worktree (unshallowed; `uv sync --all-packages` +
  `pnpm install --frozen-lockfile`), ran `tests/tasks/test_ftf_0101.py`
  clean (24/24), then separately disabled (a) the `alg` allowlist check in
  `validate_id_token`, (b) the Origin/CSRF-token check in `/auth/logout`,
  and (c) the membership-active check in `/auth/callback`, one at a time,
  re-running the same test file after each: all three genuinely caught the
  induced break (`an unsigned alg=none token is denied before any
  signature check`; `logout without a CSRF token header is denied` +
  `logout from an unapproved Origin is denied`; `a revoked membership
  denies login...` + `the real, unmodified default denies login absent a
  wired Membership lookup`), then reverted and re-confirmed clean
  (24/24). Not a rubber-stamped re-run of the producer's own claim — this
  session's own independently-chosen breaks.
- **`product_truth_check.py` result confirmed legitimate:** `--fail-on-unwired`
  reports `1/1 product-true`; `build_oidc_login_router` is a real, non-test
  symbol imported and called unconditionally from `apps/api/app/main.py`'s
  `create_app()` — a genuine live-caller wiring, not a 0/0 pass with no
  proving_symbol.
- **Full regression, fresh environment:** `ruff check .` / `mypy .` clean
  (67 files); `pnpm run lint` / `typecheck` clean; `pnpm exec vitest run`
  — 5 passed; `uv run pytest tests/unit` — 3 passed; every existing
  `tests/tasks/test_ftf_*.py` re-run clean and unmodified (`0002`/`0003`/
  `h001` initially showed spurious failures traced to two environment
  artifacts, not defects: `pnpm install` not yet run in the fresh worktree,
  and a shallow clone — same `stale_commit` transient this file has
  documented before; `pnpm install --frozen-lockfile` + `git fetch
  --unshallow` resolved both and all three re-ran clean); `uv run python3
  scripts/validate_spec.py --strict` clean.
- **Task contract re-pulled directly:** `python tools/r1_handoff.py packet
  FTF-0101 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — this task has exactly
  two acceptance clauses and one negative_acceptance clause:
  1. **Acceptance clause 1** ("Real OIDC provider login/refresh/logout
     validates issuer+audience+subject and secure session") is **not
     met**. The PR's own test evidence exercises a bespoke stdlib
     `ThreadingHTTPServer` standing in for the provider's `/jwks`/`/token`
     endpoints, not a real OIDC provider — exactly as the PR itself
     discloses, citing Section 65.3 ("never a bypass/test-token login")
     and the still-open QUEUE.md ask for an owner-approved local
     test-provider package (`oidc-provider`/`idpy-oidc`). This is this
     task's *primary* acceptance clause (the whole point of "OIDC login"
     is exercising a real provider), not a peripheral or out-of-scope
     edge case — unlike, e.g., FTF-0102's Membership-table exclusion,
     which is a genuinely different task's scope. Per Law 1 ("nothing
     proven until test shown able to fail" — inapplicable here since the
     real dependency was never exercised at all) and this repo's own
     precedent that an unaddressed acceptance-clause element blocks
     certification even when every written test passes and the gap is
     disclosed (see this file's 2026-09-17 review of the safe-parallel-
     execution PR's undisclosed "commits" gap, which drew the same line
     between disclosed-and-accepted peripheral BLOCKED_EXTERNAL items and
     an unmet clause element that blocks certification) — this clause
     cannot be marked proven from a fake in-process test double, however
     well that double exercises this module's own logic.
  2. **Negative_acceptance** ("revoked membership... denied") *is*
     adequately proven for this task's own real scope: `default_membership_lookup`
     is a genuine fail-closed enforcement point (verified above via fault
     injection, not merely asserted), and no `Membership` table exists
     anywhere in this repository yet because it is a different task's
     (FTF-0103/FTF-0105) deliverable, not this one's — a disclosed,
     out-of-scope gap in the same accepted category as FTF-0102's own
     Membership exclusion, not a reason on its own to withhold
     certification.
- **Conclusion:** the delivered signature/session/CSRF/logout machinery is
  real, independently reproduced against a real local HTTP server, and
  genuinely fault-injectable (three independent, this-session-chosen
  breaks all caught) — this is not a rejection of the module's core
  design, and CI is green on the real final head. But acceptance clause 1
  — this task's central, load-bearing requirement — has never been
  exercised against a real OIDC provider, only a fake stand-in, exactly as
  the submitter itself discloses. That is not proven, so this task is
  **not certified**. `certification_state` stays `PROVISIONAL` /
  `proof_state` stays `L1-PROVEN` on PR #103's own branch (that PR is left
  open, unmerged — nothing from it is promoted to `main` by this review).
  **Fix needed:** the still-open QUEUE.md "Owner actions — current" ask
  (approve `oidc-provider` or `idpy-oidc` as a new dev/test-only runtime
  dependency) needs an owner decision; once approved, a follow-up must add
  real integration evidence against that actual local provider before this
  clause can be marked proven and this task certified. No other change
  requested — the rest of PR #103's evidence stands as submitted.
- **Validated-by:** session
  https://claude.ai/code/session_01VNPVrAHoQTUFSmfhV4UkCU (independent —
  see session-ID check above), 2026-09-19.

## 2026-09-19 — FTF-0101 (PR #112) — Relay: real idpyoidc provider integration evidence submitted, PROVISIONAL (awaiting independent Control plane review)
- **PR:** https://github.com/SJZLab/Sovra/pull/112
  (`claude/wizardly-fermat-qdxfc4` → `main`), superseding PR #103
  (`claude/wizardly-fermat-c0i7xl` → `main`, now closed as superseded).
  PR #103's own four commits are carried forward unchanged (merged into
  this branch first, confirmed zero code delta lost) — this submission
  adds new commits on top, it does not reimplement anything already
  reviewed above.
- **What's new relative to PR #103's own (not-certified) review:** the one
  fix that review asked for and nothing else. `tests/integration/
  test_oidc_login_real_provider.py` drives the same, unchanged
  `build_oidc_login_router` against a real `idpyoidc.server.Server` OpenID
  Provider over real HTTP sockets (a stdlib `ThreadingHTTPServer` wrapping
  the real `idpyoidc` endpoint objects — no OIDC logic of its own): a real
  PKCE-bound authorization code, a real RS256 id_token signed by the OP's
  own real key, a real `/jwks` fetch, real single-use-authorization-code
  enforcement (a replay of an already-consumed code against the real OP's
  own `/token` endpoint is genuinely rejected), and a real PKCE-verifier
  mismatch genuinely rejected by the real OP with a real `invalid_grant`.
  `idpyoidc>=5.0.0,<5.1` added to root `pyproject.toml`'s
  `[dependency-groups] dev` (dev/test-only, matching the owner's own
  2026-09-19 resolution — `grep`-confirmed not imported anywhere under
  `apps/api/app/`). New CI job `oidc-provider-integration`.
- **Unrelated regression found and fixed in the same PR:** `idpyoidc`
  transitively pulls in `requests`/`urllib3`, whose wheel ships a vendored
  emscripten `.js` file inside `.venv/site-packages`; `eslint.config.mjs`
  had no `.venv/**` ignore (never needed before this dependency), so
  `pnpm run lint` broke for real once `.venv` existed locally (6
  `no-undef` errors against vendored third-party JS, not this repo's own
  source). Fixed by adding `.venv/**` to the existing ignore list
  alongside `node_modules`/`dist`/`.next`.
- **Self-reported regression check (producer, not yet independently
  re-verified — see "Not done" below):** full local regression after
  `pnpm install --frozen-lockfile` + `git fetch --unshallow` (this
  sandbox starts with neither): `ruff check .` / `ruff format --check .` /
  `mypy .` clean; `pnpm run lint`/`typecheck`/`format:check` clean;
  `pnpm exec vitest run` 5/5; `uv run pytest tests/unit` 3/3; all 16
  existing `tests/tasks/test_ftf_*.py` files re-run clean; the 3 new
  real-provider integration tests pass; `validate_spec.py --strict`
  clean; `product_truth_check.py --fail-on-unwired` still `1/1`.
  `verify_task.py --task FTF-0101 --stage task` still reports `BLOCKED —
  no merged verification evidence yet`, expected pre-merge.
- **Not done this submission (left for the reviewing session, per "producer
  never certifies own work"):** CI has not yet been confirmed green on the
  real PR #112 head by an independent session; no independent fault
  injection against the new real-provider test; no independent
  session-ID check. `certification_state` stays `PROVISIONAL` /
  `proof_state` stays `L1-PROVEN`.
- **Submitted-by:** this relay shift (scheduled, non-interactive),
  2026-09-19.

## 2026-09-19 — FTF-0101 (PR #112) — Control plane independent review: NOT CERTIFIED (PR stays open, unmerged)
- **Trigger:** `pull_request.opened` webhook, PR #112
  (`claude/wizardly-fermat-qdxfc4` → `main`, head `a8f013d`), which
  supersedes PR #103 above with the one fix that review asked for: real
  `idpyoidc`-provider integration evidence for acceptance clause 1.
- **Session-independence check (done first, per procedure):** compared
  this reviewing session's own ID
  (`https://claude.ai/code/session_01CR9tW5L6fqoUJHfCyeEhVT`) against all
  9 commits on PR #112 — `session_015u9WhWDyzkFgkk74HzHJ9d` (PR #103's
  carried-forward commits) and `session_01Un2BpAFyPcvY7zUpk7ZHbe` (this
  PR's own new commits). No match — independent.
- **CI reconfirmed for real on the actual current head:** at first poll
  (webhook delivery time) 3 of 7 checks on `a8f013d` were still
  `in_progress`; re-polled rather than trusting the PR body's own
  "to be confirmed independently" line — all 7 (`gate`,
  `oidc-provider-integration`, `docker-local-stack-integration`,
  `organization-workspace-schema-integration`,
  `audit-event-writer-integration`, `postgres-backup-integration`,
  `storage-semantics-integration`) completed `success` on `a8f013d`
  itself.
- **Own fault injection, independent of the producer's claim:** in a fresh
  `git worktree` off `origin/claude/wizardly-fermat-qdxfc4`
  (`uv sync --all-packages`), ran the new
  `tests/integration/test_oidc_login_real_provider.py` directly — clean,
  "all checks passed" — then edited `apps/api/app/auth/oidc_login.py`'s
  `callback()` to pass a deliberately wrong `expected_audience` into
  `validate_id_token(...)` (this session's own chosen break), re-ran the
  same file: the real-provider login test genuinely failed
  (`AssertionError` on `session_cookie is not None` — the real,
  correctly-issued token from the real `idpyoidc` OP was correctly
  rejected once the app's own audience check was broken). Reverted
  (`git checkout --`) and re-confirmed clean. Real, live-wired validation,
  not a rubber stamp.
- **`product_truth_check.py` result confirmed legitimate:** `--fail-on-unwired`
  reports `1/1 product-true`; `grep` on `apps/api/app/main.py` confirms
  `build_oidc_login_router` is imported and called unconditionally from
  `create_app()` — a genuine live-caller wiring, not a 0/0 pass.
- **Task contract re-pulled directly:** `python tools/r1_handoff.py packet
  FTF-0101 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1
  reads exactly: **"Real OIDC provider login/refresh/logout validates
  issuer+audience+subject and secure session."** Three named flows:
  - **login:** now genuinely proven against a real `idpyoidc` OpenID
    Provider (this PR's own new evidence, independently fault-injected
    above) — **met**.
  - **logout:** local session-cookie revocation, independently
    fault-injectable (reviewed under PR #103), matches Section 65.3's
    actual requirement ("logout/session revocation"), which does not call
    for an OP round-trip — **adequately proven** for this task's own
    scope, unchanged from the prior review.
  - **refresh: not met, and not disclosed.** `apps/api/app/auth/oidc_login.py`'s
    own module docstring asserts "refresh handled server-side (no refresh
    token is ever sent to the browser)" as a delivered capability.
    `grep -i refresh` across that module, `oidc_settings.py`, and
    `main.py` turns up zero lines implementing it: no `refresh_token` is
    ever stored, exchanged, or used to mint a new access/id token anywhere
    in this codebase — the session simply expires after
    `session_ttl_seconds` with no renewal path. Neither the new real
    `idpyoidc`-backed test nor the pre-existing stdlib stand-in exercises
    any refresh behavior — there is nothing to exercise. This is not
    Section 84.4's allowed fake-double-for-unit-coverage pattern (used
    correctly for `login`/`logout`); it is a named acceptance-clause
    element with no implementation and no test at all, real or fake.
  - This differs from this task's other, already-disclosed gaps
    (revoked-membership fail-closed default, missing Next.js app shell, no
    secret-manager integration) in the one respect that matters for
    certification: it is **not disclosed anywhere** — not in
    `oidc_login.py`'s own "Disclosed, not hidden, gaps" docstring section,
    not in PR #112's body, not in this shift's own
    `docs/certifications/queue.md` entry above, not in
    `docs/spec-matrix.yaml`'s FTF-0101 notes (which this PR edits to read
    "MET (2026-09-19 follow-up, previously DISCLOSED/NOT MET) --
    acceptance clause 1's 'Real OIDC provider' integration evidence" with
    no qualification). The producer's own record represents clause 1 as
    now fully satisfied; it is not.
- **Conclusion:** everything this PR itself set out to prove, it proves for
  real — CI is green on the actual head, the new real-provider test is
  genuinely fault-injectable, and `product_truth_check.py`'s 1/1 is a real
  live-caller wiring. But acceptance clause 1 is a three-part compound
  requirement (login **and** refresh **and** logout), and the "refresh"
  third was never implemented, never tested, and is now claimed MET
  without disclosure. Per Law 1 (inapplicable here since refresh was never
  exercised at all, real or fake) and this file's own 2026-09-19 precedent
  on PR #103 (an unmet acceptance-clause element blocks certification even
  when every written test passes and every *other* element is disclosed)
  — an *undisclosed* unmet element is at least as disqualifying. This is
  not a rejection of the login-flow work, which is real and solid.
  `certification_state` stays `PROVISIONAL` / `proof_state` stays
  `L1-PROVEN` on PR #112's own branch. PR #112 is left open, unmerged —
  nothing from it is promoted to `main` by this review.
  **Fix needed:** either (a) implement a real server-side refresh
  mechanism — store the OP's `refresh_token` server-side per session,
  exchange it for a new access/id token against the real OP before/at
  session-TTL expiry, and add real-provider test coverage proving it,
  mirroring this PR's own login-flow pattern — or (b) if server-side
  refresh is deliberately out of scope given this task's session-TTL
  design (e.g. forcing re-login on expiry is the intended behavior), that
  scoping decision needs to be made explicit and disclosed — in the module
  docstring (replacing the current unqualified "refresh handled
  server-side" claim), in `docs/spec-matrix.yaml`'s notes, and in the next
  PR body — not silently assumed. Either way, clause 1 cannot be marked
  MET in full until the record matches reality. No other change requested
  — the rest of PR #112's evidence, including its login-flow real-provider
  evidence, stands as submitted and does not need to be redone.
- **Validated-by:** session
  https://claude.ai/code/session_01CR9tW5L6fqoUJHfCyeEhVT (independent —
  see session-ID check above), 2026-09-19.

## 2026-09-19 — FTF-0101 (PR #114) — Relay: real server-side refresh implemented and proven, PROVISIONAL (awaiting independent Control plane review)
- **PR:** https://github.com/SJZLab/Sovra/pull/114
  (`claude/wizardly-fermat-3il8f6` → `main`), superseding PR #112
  (`claude/wizardly-fermat-qdxfc4` → `main`, now closed as superseded).
  PR #112's own login/logout commits are carried forward unchanged (merged
  into this branch first, confirmed zero code delta lost) — this
  submission adds new commits on top.
- **What's new relative to PR #112's own (not-certified) review:** exactly
  the gap that review named — a real, working server-side refresh, not a
  disclosed scope-out. Acceptance clause 1 names "refresh" as a required
  flow and Section 45 requires "refresh handled server-side" outright, so
  the "implement it for real" branch of the review's own two named options
  was taken.
- **Empirical groundwork before writing app code:** a standalone scratch
  script drove a real `idpyoidc.server.Server` OP directly (not through
  the app under test) to find out what a real provider actually requires
  before it will mint a `refresh_token`. Confirmed for real: the OP
  silently omits `refresh_token` from the token response unless the
  authorization request includes `offline_access` scope, and errors
  (`"consent in prompt"`) if `offline_access` is requested without
  `prompt=consent`; separately, a refreshed id_token only comes back if
  the OP's own `refresh_token` grant_config lists `id_token` in
  `supports_minting` (PR #112's own OP config didn't). All three findings
  are now reflected in both the app code and the real-provider test's OP
  config.
- **App changes (`apps/api/app/auth/oidc_login.py`):**
  `build_authorization_redirect_url` now requests `offline_access` scope +
  `prompt=consent`; `validate_id_token`'s `expected_nonce` parameter is
  now `str | None` (OIDC Core 12.2 permits a provider to omit `nonce`
  entirely on a refreshed id_token — there's no fresh authentication
  transaction to bind it to); new `exchange_refresh_token()` (a real
  `grant_type=refresh_token` POST to the token endpoint, same shape as the
  existing `exchange_code_for_tokens`); new `_RefreshTokenStore`
  (in-memory, keyed by session `jti`, mirrors the existing
  `_SessionRevocationStore` structurally — the raw refresh_token is never
  sent to, or readable by, the browser); new `POST /auth/refresh` —
  validates the new id_token the same way `callback()` does, confirms the
  refreshed subject/issuer still match the session being refreshed (fails
  closed and ends the session if they don't, rather than silently
  swapping identity), then rotates the session: the old session id (`jti`)
  is revoked and a brand-new signed session cookie is issued. CSRF
  double-submit protected the same way as `logout()` (the shared
  Origin+token check was factored into a small `_csrf_error()` helper used
  by both routes).
- **Test changes:** `tests/tasks/test_ftf_0101.py` — the stdlib
  fake-provider stand-in now also serves a `refresh_token` grant (a new
  `refresh_responses` field on `_ProviderState`); 6 new test functions
  (`test_refresh_without_a_stored_refresh_token_is_denied`,
  `test_refresh_without_csrf_token_is_denied`,
  `test_refresh_wrong_origin_is_denied`,
  `test_refresh_with_valid_csrf_succeeds_and_rotates_the_session`,
  `test_refresh_identity_mismatch_is_denied`,
  `test_refresh_rejected_by_provider_ends_the_session`) — 58 checks total
  in this file now (was 24). `tests/integration/
  test_oidc_login_real_provider.py` — the real OP's `grant_config` now
  lists `id_token` in `refresh_token`'s `supports_minting`, and the client
  `cdb` entry now allows `offline_access` scope and the `refresh_token`
  grant type; 2 new tests
  (`test_refresh_grant_mints_a_new_real_id_token_and_rotates_the_session`,
  `test_refresh_without_csrf_token_is_denied_by_the_real_flow`) drive a
  real refresh grant end to end against the real, independent `idpyoidc`
  OP: a real new id_token minted by the real OP's own `/token` endpoint,
  real session rotation (the pre-refresh session cookie is genuinely
  revoked once refreshed — proven by both a direct `/auth/session` call
  and a replayed `/auth/refresh` call with it, both denied), and real CSRF
  enforcement — 20 checks total across 5 tests now (was 3 tests).
- **Own fault injection, independent of anything the prior review
  exercised:** removed the rotation `revoked_jtis.revoke(data["jti"])`
  call from `refresh()`'s happy path (leaving everything else intact) and
  re-ran both test files: `tests/tasks/test_ftf_0101.py` failed exactly
  `"the pre-refresh session id is revoked (rotation), not reusable
  afterwards"`; `tests/integration/test_oidc_login_real_provider.py`
  failed exactly `"the pre-refresh session cookie is revoked once
  refreshed (real rotation)"` — against the real provider, not a
  stand-in. Reverted (`git diff` confirmed byte-identical), re-ran both
  files clean.
- **Full regression, this sandbox** (after `uv sync --all-packages`,
  `pnpm install --frozen-lockfile`, and `git fetch --unshallow` — all
  needed once, this sandbox starts with none of them, matching every
  prior shift's documented environment-artifact note, not a real defect):
  `ruff check .` / `ruff format --check .` / `mypy .` clean (69 files);
  `pnpm run lint` / `typecheck` / `format:check` clean; `pnpm exec vitest
  run` — 5/5; `uv run pytest tests/unit` — 3/3; all 16 existing
  `tests/tasks/test_ftf_*.py` files re-run clean and unmodified in
  behavior; both updated FTF-0101 test files clean;
  `python3 scripts/validate_spec.py --strict` clean;
  `product_truth_check.py --fail-on-unwired` still `1/1` (unchanged,
  `build_oidc_login_router` is still the one live-wired symbol).
  `verify_task.py --task FTF-0101 --stage task` still reports `BLOCKED —
  no merged verification evidence yet`, expected pre-merge.
- **Doc updates:** `docs/spec-matrix.yaml`'s FTF-0101 notes now record all
  three of login/refresh/logout as MET (previously only login/logout,
  refresh DISCLOSED/NOT MET), with the new refresh evidence described and
  its own new disclosed gaps recorded (`_RefreshTokenStore` is
  in-memory/single-process like `_SessionRevocationStore`; this app does
  not call the provider's own RFC 7009 token-revocation endpoint on
  logout, matching the same session-local scope already accepted for
  logout/session-revocation itself); `evidence_hash` filled to this
  shift's own implementation commit (`e2c5423`) in a follow-up commit
  once it existed — same two-commit pattern as PR #103/#112's own
  evidence.
- **Not done this submission (left for the reviewing session, per
  "producer never certifies own work"):** no independent session-ID check
  against this submission's own commits; CI has not yet been confirmed
  green on the real PR #114 head by an independent session; no
  independent fault injection beyond the one performed above (a second,
  differently-chosen break would still be valuable, per this file's own
  established review pattern). `certification_state` stays `PROVISIONAL`
  / `proof_state` stays `L1-PROVEN`.
- **Submitted-by:** this relay shift (scheduled, non-interactive),
  2026-09-19.

## 2026-09-19 — FTF-0103 (RBAC middleware) — Relay: real role/tenant-scoped enforcement dependency submitted, PROVISIONAL (awaiting independent Control plane review)
- **Task:** FTF-0103, RBAC middleware. Dependencies FTF-0101 (OIDC login)
  and FTF-0102 (Organization/workspace schema) are both CERTIFIED; DoD
  pulled fresh via `python tools/r1_handoff.py packet FTF-0103 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`, not improvised from the task title
  alone.
- **Real constraint checked before writing any code:** this task's own
  `database_changes` contract reads "No independent schema change.
  Consume frozen contract; any required change is a separately claimed
  migration prerequisite" — not the schema-owning boilerplate FTF-0102/
  FTF-0104 carried ("Own forward Alembic migration for this resource").
  Confirmed directly: no `Membership` table exists anywhere in this
  repository (`apps/api/app/auth/organization_workspace_schema.py` has
  only `Organization`/`Workspace`), no frozen contract defines one either
  (`packages/contracts/jsonschema/01-domain.schema.json`'s own
  `definitions` are `Slug`/`EntityName`/`EntityStatus`/`Organization`/
  `Workspace` only), and no task in `docs/TASK_INDEX.md`/
  `docs/spec-matrix.yaml` owns creating it (FTF-0105 is "Tenant-isolation
  query guards" — DB/query-layer RLS enforcement, a different concern —
  and itself depends on FTF-0103, so it cannot be the schema's creator
  either). This is not a hidden gap: FTF-0101's own module docstring
  already named it ("Membership is FTF-0103/FTF-0105's concern") and its
  `default_membership_lookup` already established the precedent this
  submission follows — build the real enforcement primitive now, prove it
  fail-closed via an injected lookup, and disclose the missing live table
  rather than fabricate one this task is not permitted to create.
- **`apps/api/app/auth/rbac_middleware.py`:** `Role` enum matching
  Section 6.4's exact `Membership.role` set; `require_role(allowed_roles,
  ...)` — a FastAPI dependency factory that resolves the authenticated
  principal (`principal_resolver`, injectable, fail-closed default),
  resolves the target organization strictly from the URL path
  (`organization_id_from_path()`, never the JSON body), denies outright
  if a mutating request's own body also carries a disagreeing
  `organization_id` (`tenant_mismatch`, 403 — the body is never trusted
  to select or override the path-resolved tenant), then looks up a real
  role for `(issuer, subject, organization_id)` (`role_lookup`,
  injectable, fail-closed default) and denies unless it is in
  `allowed_roles`. Deliberately decoupled from FTF-0101's own concrete
  session-cookie implementation (a separate `principal_resolver`
  injection point, not a direct import of `oidc_login`'s internals) so
  that module's already-certified evidence is never put at risk by this
  task's own changes.
- **`tests/tasks/test_ftf_0103.py`:** unit coverage of the `Role` enum's
  exact value set, both fail-closed defaults, `organization_id_from_path`
  (valid/missing/malformed), and `require_role`'s own empty-allowed-roles
  guard — plus a real end-to-end proof against a locally-hosted
  demonstration FastAPI app: a real uvicorn server over a real local
  socket (the same pattern `tests/tasks/test_ftf_0101.py` established),
  driven over real HTTP via `http.client`, not an in-process function call
  or a mocked request object. The demo app's own principal resolution
  (`X-Test-Principal` header) and role lookup (a plain dict Membership
  fixture) are test doubles standing in for a real session reader and a
  real DB-backed lookup — Section 84.4 permits test doubles for exactly
  this isolated proof; neither exists live in this repository yet (see
  above). 12 checks total, covering this task's own negative_acceptance
  clause for all three roles it names: a viewer is denied creating an
  evaluation; an evaluator is denied approving a promotion; a guessed
  organization UUID the caller has no membership in is denied even though
  they hold that exact elevated role in a different real organization
  (proven for both a read and a mutating route); a principal with no
  Membership row anywhere is denied; a body-selected tenant that
  disagrees with the URL path is denied outright even though the path
  alone would have been permitted (and the same field, agreeing with the
  path, does not itself cause a denial); and a malformed path organization
  id is rejected with 400, not passed through.
- **Own fault injection (two, distinct vectors):** (1) disabled the
  body-tenant mismatch check (`if body_tenant is not _NO_BODY_TENANT and
  body_tenant != organization_id: raise ...` replaced with `pass`) —
  caught for real, exactly the one expected check failed
  (`negative_acceptance: a body-selected tenant ... is denied outright`);
  (2) replaced the `role not in allowed` denial with silently downgrading
  any unknown membership to `Role.VIEWER` instead of denying — caught for
  real, exactly the 5 expected role/tenant-boundary checks failed (viewer
  cannot create an evaluation; evaluator cannot approve a promotion; two
  guessed-UUID checks; no-membership-anywhere). Both reverted, confirmed
  byte-identical via `diff` against the pre-injection copy, re-ran clean.
- **Full regression, this sandbox** (`uv sync --all-packages`, `pnpm
  install --frozen-lockfile`, `git fetch --unshallow` — all needed once,
  this sandbox starts with none of them, same documented environment-
  artifact note as every prior shift, not a real defect): `ruff check .` /
  `ruff format --check .` / `mypy .` clean (72 files); `pnpm run lint` /
  `typecheck` / `format:check` clean; `pnpm exec vitest run` — 5/5; `uv
  run pytest tests/unit` — 3/3; all 17 existing `tests/tasks/
  test_ftf_*.py` files plus `tests/implementation/*.py` re-run clean and
  unmodified in behavior; `python3 scripts/validate_spec.py --strict`
  clean; `product_truth_check.py --fail-on-unwired` still `1/1`
  (unchanged — this module has no live product caller yet, see below);
  `scripts/status.py` still `20/300 certified · 1 product-true`.
  `verify_task.py --task FTF-0103 --stage task` reports `BLOCKED — no
  merged verification evidence yet`, expected pre-merge (its
  `spec/verification-map.json` entry is added by Control plane at
  certification time, per established convention — not touched by this
  submission).
- **Disclosed, not hidden (both already anticipated by FTF-0101's own
  module docstring and this task's own `database_changes` contract):** no
  live `Membership` table exists, so `default_role_lookup` fails closed
  until a DB-backed lookup is supplied by a separately claimed
  prerequisite — this task is not permitted to add that schema itself.
  No live `/api/v1` product route exists anywhere in this repository yet
  (`apps/api/app/main.py` only wires FTF-0101's `/auth/*` router), so this
  module has no live product caller and legitimately does not count
  toward product-true yet — same disclosed class already accepted for
  FTF-0005/FTF-0006/FTF-0102/FTF-0104 before each one's own first live
  caller existed. Role/tenant scoping is organization-level only;
  `Membership.workspace_id` (Section 6.4, nullable) and the `Project`
  entity (Section 6.8) are not live tables yet either.
- **Doc updates:** `docs/spec-matrix.yaml` FTF-0103 set to
  `certification_state: PROVISIONAL` / `implementation_state: IMPLEMENTED`
  / `proof_state: L1-PROVEN` / `proving_test:
  tests/tasks/test_ftf_0103.py` (`proving_symbol` stays `''` — no live
  caller yet, see above); `evidence_hash` filled to this shift's own
  implementation commit in a follow-up commit once it existed, same
  two-commit pattern as every prior task's own evidence. New CI step
  `FTF-0103 negative-test proof` (`uv run python3
  tests/tasks/test_ftf_0103.py`) added to `.github/workflows/ci.yml`'s
  `gate` job, immediately after FTF-0101's own step.
- **Not done this submission (left for the reviewing session, per
  "producer never certifies own work"):** no independent session-ID check
  against this submission's own commits; CI has not yet been confirmed
  green on the real PR head by an independent session; no independent
  fault injection beyond the two performed above.
  `certification_state` stays `PROVISIONAL` / `proof_state` stays
  `L1-PROVEN`.
- **Submitted-by:** this relay shift (scheduled, non-interactive),
  2026-09-19.

## 2026-09-19 — FTF-0103 — record the opened PR link
- **PR:** https://github.com/SJZLab/Sovra/pull/118
  (`claude/wizardly-fermat-vpmo65` → `main`).

## 2026-09-20 — FTF-0105 (Tenant-isolation query guards) — Relay: real query-layer guard submitted, PROVISIONAL (awaiting independent Control plane review)
- **Task:** FTF-0105, Tenant-isolation query guards. Dependencies FTF-0102
  (Organization/workspace schema) and FTF-0103 (RBAC middleware) are both
  CERTIFIED; DoD pulled fresh via `python tools/r1_handoff.py packet
  FTF-0105 --plan FINE_TUNING_FACTORY_RELEASE_1.md`, not improvised from
  the task title alone.
- **Real constraint checked before writing any code:** this task's own
  `database_changes` contract reads "No independent schema change.
  Consume frozen contract; any required change is a separately claimed
  migration prerequisite" and `shared_lock_required` is empty. Confirmed
  FTF-0102 already delivered the one real primitive this task builds on
  (`apps/api/app/auth/organization_workspace_schema.py::set_tenant_context`,
  a parameterized, transaction-local `set_config(..., is_local=true)`
  call) but that no application-layer guard existed anywhere yet forcing
  every caller to use it correctly — no session factory, no engine, no
  repository layer existed in this repository at all before this task
  (confirmed via `grep -rn "sessionmaker\|create_engine"
  apps/api --include='*.py'`, zero matches outside this task's own new
  module). This task's job is exactly that missing structural guard, not
  a new schema.
- **`apps/api/app/auth/tenant_isolation_query_guards.py`:**
  `tenant_scoped_session(session_factory, organization_id)` — the one
  blessed way to open a tenant-scoped unit of work for a request handler
  or a background job alike; fails closed (`TenantContextError`) before a
  session is even created when `organization_id` is not a real
  `uuid.UUID`, binds context immediately via FTF-0102's own
  `set_tenant_context`, always commits-or-rolls-back and closes.
  `execute_tenant_scoped(session, organization_id, statement, params)` —
  the guard a raw-SQL/background-job caller must use instead of
  `session.execute(...)` directly; reads back the session's own live
  transaction-local context via `require_tenant_context()` and refuses to
  run the caller's statement at all on mismatch or absence (never trusts
  the caller's stated intent). `install_tenant_pool_reset_guard(engine)`
  — a real SQLAlchemy pool `checkin` event listener that force-clears
  `app.current_organization_id` on every connection returned to the pool,
  defense-in-depth for the one gap the other two guards cannot see by
  construction: a raw DBAPI connection, or a raw-SQL path that used a bare
  (session-level) `SET` instead of the required `SET LOCAL` — which is
  *not* cleared by rollback and would otherwise survive a pool checkin.
- **`tests/tasks/test_ftf_0105.py`:** 30 no-DB checks — fail-closed
  validation on every guard function (missing/malformed/wrong-type
  organization ID, context mismatch, missing context), the pool-checkin
  reset's own parameterized `set_config(...)` call proven directly against
  a mocked DBAPI connection, a real `sqlite://` engine used to prove the
  checkin listener is genuinely wired to the pool's real lifecycle event
  (not merely defined and never attached), and a real fault injection
  against the tracked module on disk. That fault injection deliberately
  runs in a **fresh subprocess**, not `importlib.reload` — reloading this
  module in-process would silently create a second `TenantContextError`
  class object in the same module dict, breaking every other `except
  TenantContextError` in this suite via Python's ordinary reload-identity
  gotcha (found the hard way: an earlier draft using `importlib.reload`
  made unrelated, earlier-alphabetical tests fail nondeterministically
  depending on run order — fixed by switching to the same
  `subprocess.run([sys.executable, "-c", probe], ...)` pattern
  `tests/tasks/test_ftf_h002.py` already established for exactly this
  class of problem).
- **`tests/integration/test_tenant_isolation_query_guards.py`:** real
  live-Postgres proof against a directly installed PostgreSQL 16 cluster
  (this sandbox has no container-registry egress, same finding as
  FTF-0004/FTF-0102/FTF-0104/FTF-R017 already documented — a real `docker
  pull postgres:16-alpine` was not re-attempted this task since the
  finding is already established; local Postgres 16 was already installed
  in this sandbox and only needed starting). Acceptance: `tenant_scoped_session`
  applies context consistently across two independent units of work drawn
  from one `pool_size=1`-forced-reuse engine, proven via real DBAPI
  connection-identity equality (`id(session.connection().connection.dbapi_connection)`),
  not merely asserted. negative_acceptance "connection-pool reuse...
  cannot leak": the org2-scoped session, reusing org1's exact pooled
  connection, sees only its own row. negative_acceptance "background
  job... cannot leak": a session opened the naive way (bypassing
  `tenant_scoped_session`, mimicking an unreviewed background-worker code
  path) is still refused by `execute_tenant_scoped` on a claimed-tenant
  mismatch, and a fresh session on the same reused connection with no
  context set sees zero rows. negative_acceptance "raw SQL query...
  cannot leak": shown able to fail (Law 1), twice, against the same real
  pool/connection reuse rather than asserted from either side alone — a
  bare, session-level `SET` misuse (the one raw-SQL error class the two
  guards above cannot see by construction, since it never goes through
  either) genuinely leaks a stale tenant context across a real pool
  checkout **without** `install_tenant_pool_reset_guard` installed, and
  genuinely no longer leaks with the identical guard installed on the same
  pool configuration.
- **Own fault injections (two, distinct vectors, both reverted and
  re-verified byte-identical via `diff` against a pre-injection copy):**
  (1) removed the `organization_id` `uuid.UUID` validation from
  `tenant_scoped_session` — the 3 corresponding negative checks in
  `tests/tasks/test_ftf_0105.py` genuinely failed; (2) no-op'd the
  checkin reset's own `set_config(...)` call inside
  `_reset_dbapi_connection_tenant_context` — the "no longer leaks WITH
  the guard installed" check in the real-Postgres integration suite
  genuinely failed (flipped to "leaked" again), proving that check is not
  vacuously true.
- **Full regression, this sandbox** (`uv sync --all-packages`, `pnpm
  install --frozen-lockfile`, `git fetch --unshallow` — all needed once,
  this sandbox starts with none of them, same documented environment-
  artifact note as every prior shift, not a real defect): `ruff check .` /
  `ruff format --check .` / `mypy .` clean (73 source files, per mypy's own
  reported count); `pnpm run lint` /
  `typecheck` / `format:check` clean; `pnpm exec vitest run` — 5/5; `uv
  run pytest tests/unit` — 3/3; all 17 existing `tests/tasks/
  test_ftf_*.py` files plus `tests/implementation/*.py` and
  `tests/security/*.py` re-run clean and unmodified in behavior;
  `python3 scripts/validate_spec.py --strict` clean (a transient
  `stale_commit` defect against `FTF-R001`/`FTF-H002` was traced to this
  session's own shallow clone, same documented artifact as every prior
  session's finding — resolved by `git fetch --unshallow`, not a real
  defect); `product_truth_check.py --fail-on-unwired` still `1/1`
  (unchanged — this module has no live product caller yet, see below);
  `scripts/status.py` still `21/300 certified · 1 product-true`.
  `verify_task.py --task FTF-0105 --stage task` reports `BLOCKED — no
  merged verification evidence yet`, expected pre-merge (its
  `spec/verification-map.json` entry is added by Control plane at
  certification time, per established convention — not touched by this
  submission).
- **Disclosed, not hidden:** no live product caller (no FastAPI route or
  background-worker service) exists anywhere in this repository yet that
  imports this module, the same disclosed "no live caller yet" class
  already accepted for FTF-0005/FTF-0006/FTF-0102/FTF-0103/FTF-0104
  before each one's own first live caller existed —
  `product_truth_check.py` stays `1/1`, unchanged, not inflated. The
  guard's positive-path evidence is proven against FTF-0102's own two
  tenant-scoped tables (`organizations`/`workspaces`), the only real
  tenant-scoped tables that exist in this repository; a future
  tenant-scoped table gets the same guarantee automatically by going
  through `tenant_scoped_session`/`execute_tenant_scoped`, not by
  re-proving this task's own evidence. `install_tenant_pool_reset_guard()`
  is not yet wired to call at process startup against any real
  application engine, since no live FastAPI app/background-worker engine
  construction site exists yet to call it from; it is proven directly
  against a real engine/pool in the integration suite, not yet against a
  live product engine.
- **Unrelated finding, disclosed rather than silently fixed (out of this
  task's scope):** while hand-verifying `docs/spec-matrix.yaml` still
  parses as valid YAML after this task's own edit, discovered that
  several **already-certified** entries' `notes` fields are silently
  truncated by PyYAML's own comment-start rule (a space followed by `#`,
  as in a `(PR #87, ...)` reference inside an unquoted plain scalar, ends
  the scalar there with no error) — reproduced directly: `FTF-0101`'s
  parsed `notes` is 6354→5158 chars, `FTF-0102`'s 1831→1682,
  `FTF-0103`'s 5350→4061, `FTF-0104`'s 2064→1313. This is pre-existing
  (not introduced by this submission, which was written to avoid both
  `": "` and `" #"` sequences in its own new `notes` value) and does not
  affect `certification_state`/`proof_state`/`evidence_hash`/any
  machine-checked field — only the free-text `notes` prose silently loses
  its tail past the first ` #NN` occurrence. Not fixed here: editing
  another task's already-certified `notes` text is outside this task's
  write set and this relay shift's own scope (Section 84.1 — "log a
  specification conflict or new defect and continue independent
  unblocked work"); flagged for Control plane / a separately claimed
  follow-up to decide whether to requote those fields (e.g. YAML literal
  block scalars) or accept the loss.
- **Doc updates:** `docs/spec-matrix.yaml` FTF-0105 set to
  `certification_state: PROVISIONAL` / `implementation_state: IMPLEMENTED`
  / `proof_state: L1-PROVEN` / `proving_test:
  tests/tasks/test_ftf_0105.py` (`proving_symbol` stays `''` — no live
  caller yet, see above); `evidence_hash` to be filled to this shift's own
  implementation commit in a follow-up commit once it exists, same
  two-commit pattern as every prior task's own evidence. New CI job
  `tenant-isolation-query-guards-integration` added to
  `.github/workflows/ci.yml` (real `services: postgres` container,
  mirroring `organization-workspace-schema-integration`/
  `audit-event-writer-integration`) plus a new `gate` job step (`uv run
  python3 tests/tasks/test_ftf_0105.py`), immediately after FTF-0103's own
  step.
- **Not done this submission (left for the reviewing session, per
  "producer never certifies own work"):** no independent session-ID check
  against this submission's own commits; CI has not yet been confirmed
  green on the real PR head by an independent session; no independent
  fault injection beyond the two performed above.
  `certification_state` stays `PROVISIONAL` / `proof_state` stays
  `L1-PROVEN`.
- **Submitted-by:** this relay shift (scheduled, non-interactive),
  2026-09-20.

## 2026-09-20 — FTF-0105 — record the opened PR link
- **PR:** https://github.com/SJZLab/Sovra/pull/120
  (`claude/wizardly-fermat-daazqp` → `main`).

## 2026-09-20 — Control plane: FTF-0105 (PR#120) independently reviewed, CERTIFIED
- **Trigger:** scheduled Control-plane certifier shift, `pull_request.opened`
  webhook for PR#120.
- **Session-independence check (performed first):** this reviewing
  session's own ID (`session_01WAStZmrW1dFrLLivEgcmxN`) compared against
  all 3 commits on PR#120 (`e5df228`, `aa1cc7c`, `5590ae6`) — all three
  carry `Claude-Session: .../session_01J8DXF7NnsGdpJSPpuN7gCc`. No match —
  independent, per Law 4.
- **Diff re-read directly:** in a dedicated `git worktree` at the real PR
  head (`5590ae6`), 8 files/+1419/-20 — `apps/api/app/auth/
  tenant_isolation_query_guards.py`, `tests/tasks/test_ftf_0105.py`,
  `tests/integration/test_tenant_isolation_query_guards.py`,
  `.github/workflows/ci.yml`, `docs/spec-matrix.yaml`, plus tracking-doc
  updates (`QUEUE.md`/`PROGRESS.md`/`docs/certifications/queue.md`).
- **Own re-run against a real live Postgres, this sandbox:** stood up a
  directly-installed local PostgreSQL 16 cluster (`pg_ctlcluster 16 main
  start`; no container-registry egress here, same documented finding as
  FTF-0004/FTF-0102/FTF-0104/FTF-R017/FTF-1601), created the
  `sovra_migrate` superuser role and `sovra_ftf0105` database, then ran
  `tests/integration/test_tenant_isolation_query_guards.py` directly
  against it — all checks pass, including both Law-1 pool-reset-guard
  runs (leaks without the guard, does not leak with it, against the same
  real pool configuration). `uv run python3 tests/tasks/test_ftf_0105.py`
  (30/30 no-DB checks) clean in a fresh `uv sync --all-packages` sandbox
  (`git fetch --unshallow` needed once).
- **Own independent fault injection**, distinct from the producer's own
  two: edited `execute_tenant_scoped` on disk to replace `if active !=
  organization_id:` with `if False:`, disabling the mismatch check
  entirely. The no-DB suite's own fault-injection check (which greps for
  an exact source snippet to mutate) reported its expected snippet
  missing — a consequence of mutating the source directly with a
  different edit than the one it looks for, not a false pass — so this
  break was instead proven against the real, live-Postgres integration
  suite: re-ran it against the same real database with the break in
  place, and exactly the expected negative_acceptance check failed (`a
  raw SQL query whose caller claims a different tenant than the session's
  real live context is refused outright...`); reverted byte-identical
  (`git diff --stat` confirmed no residual change against the original
  worktree copy), re-ran both suites clean.
- **CI reconfirmed on the real final head:** the PR head moved once
  during submission (`aa1cc7c` → `5590ae6`, a tracking-doc-only commit);
  waited for completion rather than trusting a mid-run snapshot — all 8
  checks (`gate`, `tenant-isolation-query-guards-integration`,
  `organization-workspace-schema-integration`, `oidc-provider-integration`,
  `audit-event-writer-integration`, `postgres-backup-integration`,
  `docker-local-stack-integration`, `storage-semantics-integration`)
  completed `success` on `5590ae6` itself; fetched and read the new
  `tenant-isolation-query-guards-integration` job's own step log directly,
  confirmed `success`, not merely inferred from the green check.
- **`product_truth_check.py --fail-on-unwired`:** confirmed legitimately
  `1/1`, unchanged — `docs/spec-matrix.yaml` FTF-0105's `proving_symbol`
  is empty, so this criterion claims no live wiring at all (`grep`
  confirms no FastAPI route or background-worker service anywhere in this
  repository imports `tenant_isolation_query_guards.py` yet); the `1/1`
  is the unchanged FTF-0101 `build_oidc_login_router` wiring, not an
  inflated or fabricated pass for this task's own, disclosedly-unwired
  module.
- **Task contract re-pulled directly:** `python tools/r1_handoff.py packet
  FTF-0105 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1
  ("Repository/query layer consistently applies authenticated
  tenant/workspace and transaction-local context") proven for real against
  FTF-0102's own two tenant-scoped tables via genuine DBAPI
  connection-identity equality across two independent units of work drawn
  from a pool forced to reuse one connection, not merely asserted;
  acceptance clause 2 (independent review, exact merged commit, real
  relevant integration and negative-test evidence) satisfied by this
  review itself; negative_acceptance (connection-pool reuse, background
  job, raw SQL — all three named vectors) independently confirmed above.
  Every disclosed gap in `docs/spec-matrix.yaml`'s FTF-0105 notes (no live
  product caller, positive-path evidence scoped to FTF-0102's own two
  tables, pool-reset guard not yet wired to a real application engine's
  startup) matches the real repository state, not silently expanded past
  what the submission itself disclosed.
- **YAML-truncation finding (disclosed by the prior relay shift) —
  independently reproduced, and this review's own note written to avoid
  it:** this review's first draft of the FTF-0105 certification note
  truncated the same way the disclosed pre-existing findings did (a space
  followed by `#`, as in `(PR #120, ...)`, silently ends an unquoted
  `notes` scalar with no parse error) — reproduced directly (6908 → 1914
  chars). Fixed by writing `PR#120` (no space before `#`) throughout this
  review's own new note text and verifying the full 6908-char note
  round-trips through `yaml.safe_load` unchanged before committing. The
  pre-existing already-certified entries' own truncation
  (FTF-0101/0102/0103/0104) is unchanged by this review — still open,
  still a separately claimed follow-up, not this task's write set.
- **Full regression, this sandbox:** `uv run ruff check .` / `uv run ruff
  format --check .` / `uv run mypy .` clean; `pnpm install
  --frozen-lockfile` then `pnpm run lint` / `typecheck` / `format:check`
  clean; `pnpm run test` (Vitest) 5/5; `uv run pytest tests/unit` 3/3; all
  17 existing `tests/tasks/test_ftf_*.py` files re-run clean (including
  `test_ftf_h001.py`, whose first run showed 3 failures traced directly to
  `validate_spec.py --strict` reporting 9 `stale_commit` findings against
  FTF-0001/0002/0003/0004/0005/0006/H001/R001/H002 — a shallow-clone
  artifact of this review's own worktree, not a real defect, cleared by
  `git fetch --unshallow`); `validate_spec.py --strict` clean;
  `verify_task.py --task FTF-0105 --stage task` → `PASS`;
  `verify_release.py` confirmed non-inflated (`21/300` before this
  certification, `22/300`/zero `FAILED` after — the `FAILED` entries that
  surfaced mid-review for FTF-0002/FTF-0003/FTF-H001 were traced to this
  sandbox's Python venv not yet having `pnpm install`/`uv sync
  --all-packages` run against it, not a real regression; both installed,
  all three re-ran `PASS`).
- **Conclusion:** the delivered tenant-isolation query-layer guard is
  real, independently reproduced against a real local PostgreSQL 16
  cluster (not a mock or SQLite stand-in, which has no row-level security
  at all), genuinely fault-injectable via an own, this-session-chosen
  break distinct from the producer's own two and caught for real by the
  live-Postgres integration suite, and CI is green on the real final
  merged head (8/8). No blocking defect.
- **Action taken:** merged PR#120 (merge commit
  `14f785c688cd1134dfe110edd73bf71f5df6d708`). Set `docs/spec-matrix.yaml`
  FTF-0105 to `certification_state: CERTIFIED` / `proof_state:
  L2-CERTIFIED` (`evidence_hash` kept at `e5df228996c32c8d10c2a1a999effcc56d29aac6`,
  matching the new `spec/verification-map.json` entry's `commit` field).
  Added FTF-0105's own entry to `spec/verification-map.json`. Ran
  `sync_certified_status()` — diff limited to exactly FTF-0105's own
  `status`/`evidence` fields in `spec/tasks.json`/`docs/implementation/
  TRACKER.json`. Full ledger entry appended to
  `docs/certifications/ledger.md`. `QUEUE.md` updated (Certified 21 → 22,
  Provisional 1 → 0, "## Provisional"/"## Done" sections). `PROGRESS.md`
  updated with the full review narrative.
- **Validated-by:** session
  https://claude.ai/code/session_01WAStZmrW1dFrLLivEgcmxN (independent —
  see session-ID check above), 2026-09-20.

## 2026-09-20 — FTF-H003 (Cross-layer tenant isolation and relational identity) — Relay: real cross-layer IDOR guard submitted, PROVISIONAL (awaiting independent Control plane review)
- **Task:** FTF-H003, Cross-layer tenant isolation and relational
  identity. Dependencies FTF-0102 (Organization/workspace schema),
  FTF-0103 (RBAC middleware), FTF-0104 (audit event writer) and FTF-H001
  (spec/gate bootstrap) are all CERTIFIED; DoD pulled fresh via `python
  tools/r1_handoff.py packet FTF-H003 --plan FINE_TUNING_FACTORY_RELEASE_1.md`,
  not improvised from the task title alone. FTF-0106 (RBAC/IDOR security
  tests, the next item in QUEUE.md's own CAP-01 dependency order) also
  depends on this task and is still `ABSENT`/`NONE` in `docs/spec-matrix.yaml`
  — not started here, since it is not yet dependency-ready.
- **Real constraint checked before writing any code:** confirmed via
  `grep -rn` that no `apps/api/app/db/` package, no `resource_authorization.py`
  and no `packages/storage/` existed anywhere in this repository before
  this task, matching the task packet's own `files` list exactly (three
  new files, no existing module to reuse instead of duplicating). Also
  confirmed FTF-0103's `require_role` genuinely never queries a resource's
  real owning organization — it only resolves `organization_id` from the
  URL path and checks role membership in *that* organization — by reading
  `apps/api/app/auth/rbac_middleware.py` directly; that is the real,
  structural IDOR gap this task closes, not an invented one.
- **`apps/api/app/db/tenant_context.py`:** `TenantContext` — the one
  canonical, authenticated tenant identity type every downstream layer
  binds to, only ever constructed via `from_authorized_context()` from a
  real `AuthorizedContext` (FTF-0103's own `require_role` output), never
  accepted as a bare client-supplied UUID. `tenant_scoped_session_for()`
  is the `TenantContext`-typed entry point to FTF-0105's own
  `tenant_scoped_session`, delegating to it unchanged.
- **`apps/api/app/auth/resource_authorization.py`:**
  `authorize_resource_ownership(session, context, table, id_column,
  organization_id_column, resource_id)` — looks the resource up through
  FTF-0105's own `execute_tenant_scoped` guard (inheriting its fail-closed
  connection-pool-reuse safety and FTF-0102's FORCE-RLS enforcement, not a
  second independently-fallible raw query) and raises
  `ResourceAuthorizationError` identically whether the row is missing or
  belongs to a different real organization — a caller cannot use the
  distinction as an oracle to enumerate foreign-tenant IDs.
- **`packages/storage/tenant_policy.py`:** `build_tenant_scoped_object_key()`
  is the one way a storage key is ever constructed
  (`organizations/{organization_id}/{resource_type}/{resource_id}/{filename}`,
  real UUIDs and a restricted filename character set only).
  `validate_object_key_belongs_to_tenant()` fail-closed-denies (never
  raises) any path-traversal, absolute-path, backslash/null-byte or
  foreign-tenant-prefix key — Section 65.2's "no user-supplied filesystem
  or bucket path may bypass project ownership" as a guard, not a
  convention.
- **`tests/tasks/test_ftf_h003.py`:** no-DB unit/fail-closed checks for
  all three modules (`TenantContext` construction, `tenant_scoped_session_for`
  delegation via a mocked session/session_factory, `authorize_resource_ownership`
  against mocked sessions covering same-tenant/missing/foreign-tenant/
  session-context-mismatch cases with the missing- and foreign-tenant
  denial messages asserted byte-identical, and the storage-key policy's
  build/validate round-trip plus a battery of path-traversal/malformed-key
  negative cases), plus a real fault injection against the tracked
  `resource_authorization.py` module on disk — mutates the file to drop
  the foreign-tenant half of `if row is None or row[0] != context.organization_id`,
  runs a fresh subprocess (never `importlib.reload`, the same
  reload-identity gotcha FTF-0105's own suite already documented) to
  confirm the negative case genuinely flips to "authorized", then restores
  byte-identical.
- **`tests/integration/test_ftf_h003_cross_layer_isolation.py`:** real
  live-Postgres proof against a directly installed PostgreSQL 16 cluster
  (this sandbox has no container-registry egress, same finding as
  FTF-0004/FTF-0102/FTF-0104/FTF-0105/FTF-R017). Two real organizations
  (org1/org2) with one real workspace each seeded as the migration-owner
  role; every check runs as `sovra_app`. Proves this task's own acceptance
  clause 1 end to end: **read** — a guessed ID that is a real workspace
  belonging to org2 is denied while authenticated as org1 (live against
  Postgres/RLS, not a mocked assumption), worded byte-identically to a
  wholly missing ID; **write** — an `INSERT` claiming org2's
  `organization_id` from a session whose real live context is org1 is
  rejected by FTF-0102's own RLS `WITH CHECK` policy (a real database
  error, not an application-layer assumption); **download** — a storage
  key built for org1's real seeded UUID validates true for org1 and false
  for org2's real seeded UUID; **job** — a background-job-style session
  opened the naive way (bypassing `tenant_scoped_session_for`, direct
  `session_factory()` + manual `set_tenant_context`) is still refused
  org2's real workspace; **route** — a real uvicorn server over a real
  local socket (the same pattern `tests/tasks/test_ftf_0103.py`
  established) wires `require_role` and `authorize_resource_ownership`
  together on one route; an org1 `organization_admin` — a real, validly
  authorized role in org1 — substituting org2's real workspace ID in the
  path is denied (404), even though `require_role` alone would have let
  the request through on role/tenant grounds alone; an unauthenticated
  request to the same route is denied (401); **missing context** — a
  session with no tenant context ever bound at all is refused via
  FTF-0105's own `require_tenant_context` (`TenantContextError`), not
  treated as an ordinary zero-row denial.
- **Own regression proof (Law 1 — shown able to fail):** the fault
  injection above (dropping the foreign-tenant half of the check) was
  confirmed to genuinely flip the no-DB suite's mocked foreign-tenant-row
  case from "denied" to "authorized" before being restored — the module
  file diffed byte-identical to its original after restoration (`git
  diff --stat` clean).
- **Full local regression this shift:** `uv run ruff check .` / `uv run
  ruff format --check .` / `uv run mypy .` clean (81 source files, zero
  errors); `pnpm run lint` / `typecheck` / `format:check` clean; `pnpm run
  test` (Vitest) 5/5; `uv run pytest tests/unit` 3/3; all 19 existing
  `tests/tasks/test_ftf_*.py` files re-run clean, including this task's
  own new `test_ftf_h003.py`; the real live-Postgres integration suite
  above, run directly against a fresh, migrated `sovra_ftfh003` database,
  clean; `python3 scripts/validate_spec.py --strict` clean;
  `product_truth_check.py --fail-on-unwired` confirmed legitimately `1/1`
  (unchanged — this task's `proving_symbol` stays empty, no live-wiring
  claim); `scripts/status.py` confirmed non-inflated (`22/300 certified ·
  1 product-true`, unchanged — `PROVISIONAL` is not `CERTIFIED`);
  `verify_task.py --task FTF-H003 --stage task` reports `BLOCKED — no
  merged verification evidence yet`, expected pre-merge (its
  `spec/verification-map.json` entry is added by Control plane at
  certification time, per established convention — not touched by this
  submission); `verify_release.py` confirmed non-inflated (`22/300`
  before and after this submission, zero `FAILED` both times — a
  transient `ModuleNotFoundError: sqlalchemy` seen on one run was traced
  directly to invoking `python3` instead of the `uv`-managed venv's own
  interpreter, an artifact of this sandbox's own shell state, not a real
  regression; resolved by activating `.venv` before re-running, and a
  separate transient `stale_commit`/dependency-install set of failures
  earlier in this shift was traced to this sandbox's own shallow git
  clone and not-yet-installed `node_modules`, cleared by `git fetch
  --unshallow` and `pnpm install`, the same documented artifact class
  every prior shift's own review has recorded).
- **Disclosed, not hidden:** no live FastAPI route, background-worker job
  runner, cache, or model-alias layer anywhere in this repository yet
  constructs and passes a `TenantContext` or calls these new modules — the
  same disclosed "no live caller yet" class already accepted for
  FTF-0005/FTF-0006/FTF-0102/FTF-0103/FTF-0104/FTF-0105 before each one's
  own first live caller existed; `product_truth_check.py` stays `1/1`,
  unchanged, not inflated. This task's own `database_changes` contract
  text differs from FTF-0105's ("Schema change required under the
  single-writer migration lock..." vs. FTF-0105's "No independent schema
  change") but no new column or table was actually needed to satisfy this
  task's own acceptance/negative_acceptance clauses — the frozen FTF-0102
  schema already carries every tenant-owned field this task's evidence
  exercises; no migration file was added. `resource_authorization.py`'s
  positive-path evidence, like FTF-0105's, is proven against FTF-0102's
  own `workspaces` table, the only real tenant-scoped table besides
  `organizations` itself that exists in this repository; a future
  tenant-scoped table gets the same generic guarantee automatically by
  passing its own table/columns to `authorize_resource_ownership`, not by
  re-proving this task's own evidence.
- **Doc updates:** `docs/spec-matrix.yaml` FTF-H003 set to
  `certification_state: PROVISIONAL` / `implementation_state: IMPLEMENTED`
  / `proof_state: L1-PROVEN` / `proving_test: tests/tasks/test_ftf_h003.py`
  (`proving_symbol` stays `''` — no live caller yet, see above);
  `evidence_hash` to be filled to this shift's own implementation commit
  in a follow-up commit once it exists, same two-commit pattern as every
  prior task's own evidence. New CI job
  `ftf-h003-cross-layer-isolation-integration` added to
  `.github/workflows/ci.yml` (real `services: postgres` container,
  mirroring `tenant-isolation-query-guards-integration`) plus a new `gate`
  job step (`uv run python3 tests/tasks/test_ftf_h003.py`), immediately
  after FTF-0105's own step.
- **Not done this submission (left for the reviewing session, per
  "producer never certifies own work"):** no independent session-ID check
  against this submission's own commits; CI has not yet been confirmed
  green on the real PR head by an independent session; no independent
  fault injection beyond the one performed above.
  `certification_state` stays `PROVISIONAL` / `proof_state` stays
  `L1-PROVEN`.
- **Submitted-by:** this relay shift (scheduled, non-interactive),
  2026-09-20.

## 2026-09-20 — FTF-H003 — record the opened PR link
- **PR:** https://github.com/SJZLab/Sovra/pull/125
  (`claude/wizardly-fermat-hb5qby` → `main`).

## 2026-09-20 — FTF-0106 (RBAC/IDOR security tests) — Relay: real full six-role, two-tenant security-test harness submitted, PROVISIONAL (awaiting independent Control plane review)
- **Task:** FTF-0106, RBAC/IDOR security tests. Dependencies FTF-0102,
  FTF-0103, FTF-0104, FTF-0105 and FTF-H003 are all CERTIFIED (FTF-H003
  itself independently certified this same date via PR #125); DoD pulled
  fresh via `python tools/r1_handoff.py packet FTF-0106 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`, not improvised from the task title
  alone.
- **Real constraint checked before writing any code:** this task's own
  acceptance clause reads "Full real two-tenant role matrix exercises
  reads/writes/artifacts/approvals with guessed IDs" and its own
  negative_acceptance reads "No authorization test exclusively uses
  privileged DB connection or mocked membership result." Confirmed every
  prior security proof in this repository covers only a hand-picked
  subset of Section 6.4's six roles (FTF-0103: viewer/evaluator/approver;
  FTF-H003: a single organization_admin) -- none of them is the full
  six-role matrix this task's own acceptance clause names. Confirmed no
  live `ModelVersion`/`PromotionDecision`/artifact table exists anywhere
  in this repository yet (Section 6.20/6.21 are later-phase tasks) and
  this task's own `database_changes` contract forbids an independent
  schema change, so "artifacts/approvals" cannot be proven against a real
  artifact table -- scoped down honestly rather than fabricating one.
- **`apps/api/app/auth/rbac_idor_security_tests.py`:** a reusable
  harness, not a one-off fixture -- `docs/spec-matrix.yaml` shows this
  task's own dependents (FTF-1501 "Tenant isolation suite", FTF-1504
  "IDOR suite") depend on FTF-0106 directly, so the real, six-role,
  two-tenant fixture and demonstration-app builder are exposed as an
  importable module those later suites can build on rather than each
  re-deriving their own narrower one. `TwoTenantRoleMatrixFixture.build()`
  assigns all six roles to a distinct real principal in each of two real
  organizations (twelve distinct identities, never one principal reused
  across roles or organizations). `build_two_tenant_security_demo_app()`
  composes FTF-0103's `require_role` and FTF-H003's
  `authorize_resource_ownership`/`TenantContext`/`tenant_scoped_session_for`
  across three routes matching this task's own named operation classes:
  read (any real member), write (`ml_engineer`/`workspace_admin`/
  `organization_admin` per Section 45's own role descriptions), approve
  (`approver`/`organization_admin` per Section 45's own "approver --
  promotion approval" role description).
- **`tests/tasks/test_ftf_0106.py`:** 23 no-DB checks -- the fixture's own
  shape (exactly 12 distinct (org_label, role) principals), fail-closed
  `principal_resolver`/`role_lookup` behavior on malformed/unknown/
  cross-fixture input, the exact expected `READ_ROLES`/`WRITE_ROLES`/
  `APPROVE_ROLES` sets (hardcoded independently, not re-derived from the
  module under test), and the demo app's role-boundary gate genuinely
  denying a disallowed role before any database session is opened,
  proven against a real, locally-hosted demonstration FastAPI app (real
  uvicorn server, real local socket, same pattern
  `tests/tasks/test_ftf_0101.py`/`test_ftf_0103.py` established) with a
  test-double `session_factory` this suite can count calls against.
- **`tests/integration/test_ftf_0106_rbac_idor_security.py`:** 75 real
  live-Postgres checks against a directly installed PostgreSQL 16 cluster
  (this sandbox has no container-registry egress, same finding as
  FTF-0004/FTF-0102/FTF-0104/FTF-0105/FTF-H003/FTF-R017 already
  documented). For every one of 2 real organizations times 6 real roles
  times 3 operations times 2 targets (own real workspace / the other
  organization's real, guessed workspace ID), a real HTTP request through
  a real uvicorn server proves the exact expected outcome: 200 for an
  allowed role against the caller's own resource, 403 for a disallowed
  role, and 404 -- identical to a missing resource, never a
  distinguishable error -- for an allowed role whose resource ID is
  substituted with the other organization's real one. Also proves a
  session with no tenant context ever bound is refused via
  `TenantContextError`, not treated as an ordinary zero-row denial.
  negative_acceptance directly confirmed: the demo app's own database
  session is built from the restricted `sovra_app` role's connection URL
  (compared by engine username against the migration-owner role's),
  never the privileged owner role, which is used only to seed the two
  real organizations/workspaces -- matching every prior certified task's
  own established convention.
- **Own fault injections (three, distinct vectors, all reverted and
  re-verified byte-identical via `diff` against a pre-injection copy):**
  (1) widened `APPROVE_ROLES` to every role -- 4 corresponding checks in
  `tests/tasks/test_ftf_0106.py` genuinely failed; (2) widened
  `WRITE_ROLES` to every role -- this was **not** caught by the
  integration suite, since it imports `WRITE_ROLES` from the same module
  it exercises (both the enforcement and the test's own expected-status
  calculation move together on a semantic change to the constant); traced
  this to the intended, disclosed split of coverage rather than a real
  gap -- confirmed the same fault genuinely fails 5 checks in the no-DB
  suite's own `test_role_allow_lists_match_section_45`, which hardcodes
  its expected role sets independently; (3) aliased `write_dependency` to
  `READ_ROLES` while leaving the `WRITE_ROLES` constant itself correct
  (a pure enforcement-wiring fault, not a constant-value fault) --
  genuinely caught by the integration suite this time, 12 checks flipped
  from the expected `403` to `200`/`404` for every viewer/evaluator/
  approver write attempt across both organizations.
- **Full regression, this sandbox** (`uv sync --all-packages`, `pnpm
  install --frozen-lockfile`, local Postgres 16 already running from
  earlier this shift): `ruff check .` / `ruff format --check .` /
  `mypy .` clean (84 source files, per mypy's own reported count); `pnpm
  run lint` / `typecheck` / `format:check` clean; `pnpm exec vitest run`
  -- 5/5; `uv run pytest tests/unit` -- 3/3; all 19 existing `tests/tasks/
  test_ftf_*.py` files (including FTF-H003's own, certified earlier this
  same date via PR #125) plus `tests/implementation/*.py` and
  `tests/security/*.py` re-run clean and unmodified in behavior;
  `python3 scripts/validate_spec.py --strict` clean;
  `product_truth_check.py --fail-on-unwired` still `1/1` (unchanged --
  this module has no live product caller yet, see below);
  `scripts/status.py` still `23/300 certified · 1 product-true`.
  `verify_task.py --task FTF-0106 --stage task` reports `BLOCKED -- no
  merged verification evidence yet`, expected pre-merge (its
  `spec/verification-map.json` entry is added by Control plane at
  certification time, per established convention -- not touched by this
  submission).
- **Disclosed, not hidden:** no live product caller (no FastAPI route or
  background-worker service) exists anywhere in this repository yet that
  imports `rbac_idor_security_tests.py` -- same disclosed "no live caller
  yet" class already accepted for FTF-0005/FTF-0006/FTF-0102/FTF-0103/
  FTF-0104/FTF-0105/FTF-H003 before each one's own first live caller
  existed; `product_truth_check.py` stays `1/1`, unchanged, not inflated.
  "artifacts/approvals" is honestly scoped down to a real
  approval-shaped role gate over the existing `workspaces` table, since
  no `ModelVersion`/`PromotionDecision` table exists yet and this task's
  own `database_changes` contract forbids adding one -- the same
  disclosed scoping precedent FTF-0105/FTF-H003 already established for
  the same reason.
- **Not done this submission (left for the reviewing session, per
  "producer never certifies own work"):** no independent session-ID check
  against this submission's own commits; CI has not yet been confirmed
  green on the real PR head by an independent session; no independent
  fault injection beyond the three performed above.
  `certification_state` stays `PROVISIONAL` / `proof_state` stays
  `L1-PROVEN`.
- **Submitted-by:** this relay shift (scheduled, non-interactive),
  2026-09-20.

## 2026-09-20 — FTF-0106 — record the opened PR link
- **PR:** https://github.com/SJZLab/Sovra/pull/127
  (`claude/wizardly-fermat-daazqp` → `main`).

## 2026-09-20 — FTF-0106 (RBAC/IDOR security tests) — Control plane: independent CERTIFY review, CERTIFIED
- **Trigger:** scheduled Control-plane certifier shift, `pull_request.opened`
  webhook for PR #127.
- **Session-ID check:** all 4 commits on PR #127 (`9fe1957`, `ea8e963`,
  `ba8b3fe`, `6b226ee`) carry `Claude-Session: .../session_01J8DXF7NnsGdpJSPpuN7gCc`;
  this reviewing session's own ID is `session_01YYH2AJhFMjzPnDbKw3C44c` —
  no match, independent per Law 4.
- **Diff read directly:** 8 files, +1242/-23, against the real PR head
  `6b226ee` in a dedicated `git worktree`
  (`apps/api/app/auth/rbac_idor_security_tests.py`,
  `tests/tasks/test_ftf_0106.py`,
  `tests/integration/test_ftf_0106_rbac_idor_security.py`,
  `.github/workflows/ci.yml`, `docs/spec-matrix.yaml`, `QUEUE.md`,
  `PROGRESS.md`, `docs/certifications/queue.md`). Confirmed directly in
  `apps/api/app/auth/rbac_middleware.py` that `require_role` genuinely
  accepts the `principal_resolver`/`role_lookup` keyword overrides the
  harness passes.
- **CI reconfirmed on the real final head:** polled `get_check_runs` until
  completion (`gate`, `storage-semantics-integration`,
  `docker-local-stack-integration` were still `in_progress` on first
  poll); all 10 checks completed `success` on `6b226ee` itself;
  `mergeable_state` confirmed `clean` before merge.
- **Own re-run, dedicated worktree:** `uv sync --all-packages`; the no-DB
  suite (`tests/tasks/test_ftf_0106.py`, 23 checks) passed directly. Stood
  up a real local PostgreSQL 16 cluster (`sovra_migrate_review` role,
  `sovra_ftf0106_review` database — no container-registry egress in this
  sandbox, same finding as FTF-0004/FTF-0102/FTF-0104/FTF-0105/FTF-H003/
  FTF-R017); ran `tests/integration/test_ftf_0106_rbac_idor_security.py`
  (self-migrating via `alembic upgrade head`, 75 checks) — passed clean.
  Confirmed in the test source that the demo app's session engine binds
  to the real, restricted `sovra_app` role (`app_engine.url.username ==
  APP_ROLE_NAME`), never the migration owner.
- **Two own independent fault injections, distinct from the producer's
  own two:**
  1. Widened `APPROVE_ROLES` to include `Role.VIEWER` — a real
     privilege-escalation bug. Caught for real by the no-DB suite (4
     checks failed); NOT caught by the live suite, for the same disclosed
     same-constant-import reason already documented for `WRITE_ROLES` —
     confirming the disclosed coverage split holds for an independently
     chosen fault, not just the producer's own.
  2. Aliased the `approve` route's dependency to `write_dependency`
     instead of `approve_dependency` — a real wiring bug. NOT caught by
     the no-DB suite; caught for real by the live-Postgres integration
     suite (12 checks flipped).
  Both reverted byte-identical (`git diff --stat` clean) and both suites
  re-ran clean afterward.
- **Full regression, this sandbox:** `ruff check .` / `ruff format --check
  .` / `mypy .` clean (84 files); `pnpm install --frozen-lockfile` then
  `pnpm run lint` / `typecheck` / `format:check` clean; `pnpm exec vitest
  run` 5/5; `uv run pytest tests/unit` 3/3; all existing
  `tests/tasks/test_ftf_*.py` re-run clean, after one disclosed artifact:
  `test_ftf_h001.py` / `validate_spec.py --strict` first showed 10
  `stale_commit` findings, traced to this review's own shallow worktree
  clone, cleared by `git fetch --unshallow`; `verify_release.py` (after
  `pnpm install`, the same not-yet-installed `node_modules` artifact
  already documented in this task's own submission) reports `24/300`,
  zero `FAILED`.
- **`product_truth_check.py --fail-on-unwired`:** confirmed legitimately
  `1/1`, unchanged — `docs/spec-matrix.yaml` FTF-0106's `proving_symbol`
  is empty; `grep` confirms no FastAPI route or background-worker service
  anywhere in this repository imports `rbac_idor_security_tests.py` yet.
- **Task contract re-pulled directly:** `python tools/r1_handoff.py packet
  FTF-0106 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1
  ("Full real two-tenant role matrix exercises reads/writes/artifacts/
  approvals with guessed IDs") proven for real against two real
  organizations, twelve real (issuer, subject) principals covering all
  six Section 6.4 roles, and a real uvicorn server; "artifacts/approvals"
  honestly scoped down to an approval-shaped role gate over the real
  `workspaces` table (disclosed, matching the FTF-0105/FTF-H003
  precedent, no live artifact table exists yet); acceptance clause 2
  satisfied by this review itself; negative_acceptance (no privileged DB
  connection or mocked membership result used exclusively) independently
  confirmed above. All five dependencies (FTF-0102/0103/0104/0105/H003)
  confirmed genuinely `CERTIFIED` in `docs/spec-matrix.yaml` before
  relying on them.
- **Pre-existing inconsistency noticed, not remediated (outside
  CERTIFY-only scope):** FTF-H003's own `docs/spec-matrix.yaml`
  `evidence_hash` (the PR #125 merge commit `9407710...`) does not match
  its own `spec/verification-map.json` `commit` field (the implementation
  commit `6242412...`), contrary to that file's own documented
  convention. FTF-0106's own new entries use the implementation-commit
  convention throughout, consistent with FTF-0105/FTF-0103/FTF-0101.
  Flagged in `QUEUE.md` for the next Control-plane shift.
- **Conclusion:** the delivered full six-role, two-tenant RBAC/IDOR
  security harness is real, independently reproduced against a real
  local PostgreSQL 16 cluster, genuinely fault-injectable via two own,
  this-session-chosen breaks distinct from the producer's own two and
  each correctly caught by the coverage layer the disclosed design says
  should catch it, and CI is green on the real final merged head (10/10).
  No blocking defect.
- **Action taken:** merged PR#127 (merge commit
  `e21df287193bebe0feaa4ad77ad0b030d28c3a4f`). Set `docs/spec-matrix.yaml`
  FTF-0106 to `certification_state: CERTIFIED` / `proof_state:
  L2-CERTIFIED` (`evidence_hash` kept at
  `9fe19578b9a002000eb839708777db3de9813094`, matching the new
  `spec/verification-map.json` entry's `commit` field). Added FTF-0106's
  own entry to `spec/verification-map.json`. Ran `sync_certified_status()`
  — diff limited to exactly FTF-0106's own `status`/`evidence` fields in
  `spec/tasks.json`/`docs/implementation/TRACKER.json`. Full ledger entry
  appended to `docs/certifications/ledger.md`. `QUEUE.md` updated
  (Certified 23 → 24). `PROGRESS.md` updated with the full review
  narrative. This certification's own doc updates are pushed on a branch
  and opened as a PR against `main`, not pushed directly, per
  `BRANCH_PROTECTION`.
- **Validated-by:** session
  https://claude.ai/code/session_01YYH2AJhFMjzPnDbKw3C44c (independent —
  see session-ID check above), 2026-09-20.

## 2026-09-20 — FTF-0201 (LiteLLM connection CRUD) — Relay: real organization-scoped gateway connection CRUD submitted, PROVISIONAL (awaiting independent Control plane review)
- **Task:** FTF-0201, LiteLLM connection CRUD. Dependencies FTF-0103
  (RBAC middleware), FTF-0104 (audit event writer) and FTF-0005
  (configuration system) are all CERTIFIED; DoD pulled fresh via
  `python tools/r1_handoff.py packet FTF-0201 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`, not improvised from the task title
  alone.
- **Scope decision checked before writing any code (disclosed, not
  hidden — matching the FTF-0105/FTF-H003/FTF-0106 precedent of an
  explicit scope narrowing):** the task packet's own `api_contract`
  field names a broad surface ("/api/v1/connections/litellm;
  validate/sync-models/syncs; /models and /models/{id}/probe"), but its
  own `acceptance`/`files` fields scope only to freezing the shared
  interface and "Create/edit/revoke an organization-scoped gateway
  connection with secret refs and explicit TLS/URL policy" — a single
  CRUD module. Confirmed in `docs/TASK_INDEX.md` that FTF-0203
  ("Connection validation"), FTF-0204 ("`/v1/models` discovery") and
  FTF-0210 ("Real chat probe") are later, separately dependency-gated
  tasks that own the actual-gateway-calling surface. Implemented and
  wired ONLY create/get/list/edit/revoke as real, live FastAPI routes;
  declared no `validate`/`sync-models`/`syncs`/`/models`/`/models/{id}/probe`
  route at all, not even a stub or a `501` — the task's own binding text
  ("no placeholder endpoints") and Law 11 forbid declaring interface
  surface with no real backing handler.
- **Path refinement (disclosed):** the task packet's own abbreviated
  `api_contract` names no `{organization_id}` path segment, but
  FTF-0103's own `require_role` requires one (`organization_id_from_path()`)
  and Section 77.6 describes the analogous admin-only resource's route
  literally as `/organizations/{id}/component-bindings`. Routes are
  `/api/v1/organizations/{organization_id}/connections/litellm[/{connection_id}]`,
  matching that established shape and FTF-0106's own demo-route
  precedent (`/api/v1/orgs/{organization_id}/workspaces/{workspace_id}`).
- **Files path refinement (disclosed):** the task packet's own suggested
  `files` path is `packages/litellm-client/litellm_connection_crud.py`.
  This repository has no Python `packages/` workspace member —
  `packages/shared` is a TS/pnpm-workspace package, and the Python `uv`
  workspace lists only `apps/api` and the four `workers/*` members.
  Every prior certified live-app-bound domain/CRUD module (FTF-0102/
  0103/0104/0105/H003/0106) lives under `apps/api/app/`, and this task's
  module needs the identical FastAPI/SQLAlchemy/`TenantContext`
  composition those do. Placed the real implementation at
  `apps/api/app/litellm/{connection_schema,connection_crud,connection_routes}.py`
  instead of inventing a new, unprecedented Python package root and
  workspace-member wiring for one CRUD module.
- **Role gating (Section 45 applied literally):** every route, including
  reads, is `organization_admin`-only. Section 45's own role table names
  exactly one role for connections — "organization_admin: connections,
  security profiles, cost profiles" — unlike FTF-0106's own
  read/write/approve three-tier split, which had no single Section 45
  sentence to draw a line from. Section 77.6 independently describes the
  analogous `component-bindings` route as "Admin-only create/read/
  versioned update", confirming reads are not opened to a wider tier
  here.
- **`apps/api/app/litellm/connection_schema.py`:** the `litellm_connections`
  table (name/base_url/secret_ref/status/timestamps), with `validate_base_url`
  enforcing Section 3.1/65.4 literally — https:// only, no userinfo, no
  fragment, no `tls_verify=false`, no duplicate `/v1/v1` path segment —
  and `validate_secret_ref` accepting only a `vault://`/`openbao://`/
  `secret://` reference shape, independently rejecting a raw
  bearer-token/JWT-shaped value by pattern as well as by the scheme
  requirement. `apps/api/alembic/versions/0003_litellm_connections.py`
  FORCEs row-level security scoped to the same `app.current_organization_id`
  tenant context FTF-0102/FTF-0104 established, and grants the
  restricted `sovra_app` role SELECT/INSERT/UPDATE only — never DELETE,
  since revoke is a soft `status='revoked'` transition, confirmed
  directly in the integration suite by a raw DELETE attempt against the
  restricted role genuinely failing.
- **`apps/api/app/litellm/connection_crud.py`:** create/get/list/edit/
  revoke composed entirely from already-certified primitives —
  `TenantContext`/`tenant_scoped_session_for` (FTF-H003) open the unit of
  work, `authorize_resource_ownership` (FTF-H003) denies a foreign-tenant
  or missing connection ID identically before any row is read/mutated
  (this task's own negative_acceptance), `execute_tenant_scoped`
  (FTF-0105) is what every actual SELECT runs through. Every mutation
  writes an audit event via FTF-0104's `write_audit_event` in the same
  transaction. `apply_edits`/`apply_revoke` are pure, DB-free cores split
  out specifically so this task's own "status transitions, revoke
  idempotency" acceptance is provable without a database.
- **`apps/api/app/litellm/connection_routes.py`:** `build_litellm_connection_router`
  wires this up as real FastAPI routes, included into `apps/api/app/main.py`'s
  `create_app()` — the SECOND live-wired product-runtime path in this
  repository, after FTF-0101's `/auth/*`. The response serializer never
  includes `secret_ref` (this task's own negative_acceptance: "secret
  echo fails"). `main.py` now also builds a real SQLAlchemy engine from
  FTF-0005's own `Settings.database_url` and installs FTF-0105's
  `install_tenant_pool_reset_guard` on it (disclosed: trusts
  `SOVRA_DATABASE_URL` to already be a real `sovra_app`-role connection
  string in a real deployment, the same migration-owner/app-role
  operational split every integration test in this repository already
  assumes).
- **`packages/contracts/openapi/02-interface.yaml` +
  `packages/contracts/jsonschema/02-domain.schema.json`:** the
  `contracts:02` lock, modeled on `01-interface.yaml`/`01-domain.schema.json`'s
  own format. Declares exactly the CRUD paths implemented, nothing else.
  `LiteLLMConnection` (the read-facing schema) has no `secret_ref`
  property at all, with `additionalProperties: false`, so a leaked
  `secret_ref` would itself be a contract violation, not merely an
  undocumented extra field.
- **`tests/tasks/test_ftf_0201.py`:** 48 no-DB checks — `validate_base_url`/
  `validate_secret_ref`'s exact named rejection cases (non-https scheme,
  userinfo, fragment, `tls_verify=false`, duplicate `/v1/v1`, oversize, a
  raw bearer-token/JWT-shaped "secret_ref"); `LiteLLMConnection.__init__`
  itself refusing bad input before any session/database is touched;
  `apply_edits`/`apply_revoke`'s pure status-transition/idempotency
  rules; `_serialize` never including `secret_ref`; a real,
  locally-hosted `build_litellm_connection_router` (real uvicorn server,
  real local socket, same pattern `test_ftf_0101.py`/`test_ftf_0103.py`/
  `test_ftf_0106.py` established) denying unauthenticated/non-admin
  requests before `session_factory` is ever called, with a structural
  check (via the real parsed AST, not a docstring-fooled text search)
  that the route/CRUD layers genuinely delegate to the real, imported
  `require_role`/`authorize_resource_ownership`/`execute_tenant_scoped`
  rather than a hand-rolled duplicate check.
- **`tests/integration/test_ftf_0201_litellm_connection_crud.py`:** 28
  real live-Postgres checks against a directly installed PostgreSQL 16
  cluster (this sandbox has no container-registry egress, same finding
  as FTF-0004/FTF-0102/FTF-0104/FTF-0105/FTF-H003/FTF-0106 already
  documented). Two real organizations; create/get/list/edit/revoke for
  the owning organization; a rejected (non-https) `base_url` and a raw
  (non-reference-shaped) `secret_ref` both genuinely fail at creation
  and on edit (400); the core IDOR case — a real connection ID belonging
  to org1, requested/mutated under org2's own path by a real org2
  `organization_admin` — denied identically to missing (404) for
  GET/PATCH/DELETE alike, with the connection independently confirmed
  genuinely untouched afterward (this task's own negative_acceptance:
  "cannot mutate another organization gateway"); revoke idempotency (a
  second DELETE: same 200, same `revoked_at`, no error); editing a
  revoked connection refused (409); a session with no tenant context
  ever bound refused via `TenantContextError`; the restricted `sovra_app`
  role confirmed by connection username (never the migration-owner
  role) and confirmed structurally unable to issue a raw DELETE against
  `litellm_connections` directly (grant-surface proof, not merely a
  router-code choice).
- **Own fault injections (two, distinct vectors, both reverted and
  re-verified byte-identical via `git diff --stat` against the clean
  tree, both suites re-run clean afterward):**
  1. Widened `CONNECTION_ADMIN_ROLES` to include `Role.VIEWER` — a real
     privilege-escalation bug. Caught for real by both suites: 5 checks
     failed in `tests/tasks/test_ftf_0201.py` (the role-set check itself,
     the viewer-denied-403 checks, and the never-opens-a-session checks
     that depend on the denial actually happening), and 1 check failed
     in the live-Postgres suite (the viewer-denied-403 check — the
     no-DB suite's independently-hardcoded role-set assertion is what
     catches most of the blast radius here, the same disclosed coverage
     split FTF-0106 already documented for an analogous constant).
  2. Added `secret_ref` back into `_serialize`'s response body — a real
     secret-leak bug. Caught for real by both suites: 1 check failed in
     `tests/tasks/test_ftf_0201.py` (the direct in-memory serializer
     check), and 3 checks failed in the live-Postgres suite (the
     create/list/edit response-body checks, since this fault is visible
     over real HTTP too, unlike fault #1's role-set-import blast-radius
     nuance).
- **Full regression, this sandbox** (`uv sync --all-packages`, `pnpm
  install --frozen-lockfile`, local PostgreSQL 16 already running):
  `ruff check .` / `ruff format --check .` / `mypy .` clean; `pnpm run
  lint` / `typecheck` / `format:check` clean; `pnpm exec vitest run` —
  5/5; `uv run pytest tests/unit` — 3/3; all existing `tests/tasks/
  test_ftf_*.py` files (including FTF-0106's own) re-run clean and
  unmodified in behavior; `python3 scripts/validate_spec.py --strict`
  clean (one pre-existing `stale_commit` artifact traced to this
  session's own shallow clone, cleared by `git fetch --unshallow`, the
  same artifact FTF-0106's own certifying session already documented);
  `product_truth_check.py --fail-on-unwired` now reports `2/2`
  product-true — `build_oidc_login_router` (FTF-0101) plus
  `build_litellm_connection_router` (FTF-0201, this task's own real,
  non-empty `proving_symbol`, confirmed genuinely imported and called
  unconditionally from `apps/api/app/main.py`'s `create_app()`).
  `verify_task.py --task FTF-0201 --stage task` reports `BLOCKED — no
  merged verification evidence yet`, expected pre-merge (its
  `spec/verification-map.json` entry is added by Control plane at
  certification time, per established convention — not touched by this
  submission, matching FTF-0105/FTF-H003/FTF-0106's own PROVISIONAL-stage
  diffs, which likewise left `spec/tasks.json`/`spec/verification-map.json`/
  `docs/implementation/TRACKER.json` untouched until their own later
  certification commits).
- **Disclosed, not hidden:** (1) secret handling — unlike FTF-0101, this
  task never resolves `secret_ref` to a real value at all; it only
  stores/validates the reference shape, since this task never calls the
  actual LiteLLM gateway (resolving `secret_ref` to bytes is FTF-0202's
  job, and calling the gateway with it is FTF-0203's). (2) no
  request-fingerprint idempotency-key mechanics are implemented for
  `POST create` (Section 84.3's general "every card inherits" contract
  names this, but this task's own specific acceptance/negative_acceptance
  clauses do not) — a repeated POST creates a genuinely new row unless
  the `(organization_id, name)` unique constraint collides; no
  outbox/idempotency-key infrastructure exists anywhere in this
  repository yet. (3) `principal_resolver`/`role_lookup` stay at their
  fail-closed defaults, same disclosed "no live Membership table /
  session-reader wired in yet" gap FTF-0103/FTF-0106 already carry.
- **Not done this submission (left for the reviewing session, per
  "producer never certifies own work"):** no independent session-ID
  check against this submission's own commits; CI has not yet been
  confirmed green on the real PR head by an independent session; no
  independent fault injection beyond the two performed above.
  `certification_state` stays `PROVISIONAL` / `proof_state` stays
  `L1-PROVEN`.
- **Submitted-by:** this relay shift (scheduled, non-interactive),
  2026-09-20.

## 2026-09-20 — FTF-0201 (LiteLLM connection CRUD) — Control plane: independent CERTIFY review, CERTIFIED
- **Trigger:** scheduled Control-plane certifier shift, `pull_request.opened`
  webhook for PR #133.
- **Session-ID check:** all 7 commits on PR #133 (`b7756b3`, `e445185`,
  `417477e`, `37ec61c`, `2ed384b`, `96eac79`, `1815080`, `d0aec3b`) carry
  `Claude-Session: .../session_01MV1wn1CJHoHmds4eRKKVaV`; this reviewing
  session's own ID is `session_01RLyWKThYBPJkaqeDp2HZsN` — no match,
  independent per Law 4.
- **Diff read directly:** 16 files, +3533/-22, against the real PR head
  `d0aec3b` (workspace already checked out to it by the triggering event):
  `apps/api/app/litellm/connection_schema.py`,
  `apps/api/alembic/versions/0003_litellm_connections.py`,
  `apps/api/app/litellm/connection_crud.py`,
  `apps/api/app/litellm/connection_routes.py`, `apps/api/app/main.py`,
  `packages/contracts/openapi/02-interface.yaml`,
  `packages/contracts/jsonschema/02-domain.schema.json`,
  `tests/tasks/test_ftf_0201.py`,
  `tests/integration/test_ftf_0201_litellm_connection_crud.py`,
  `.github/workflows/ci.yml`, `docs/spec-matrix.yaml`, `QUEUE.md`,
  `PROGRESS.md`, `docs/certifications/queue.md`. Confirmed directly in
  `apps/api/alembic/versions/0003_litellm_connections.py`: real
  `GRANT SELECT, INSERT, UPDATE` (no DELETE) plus
  `ALTER TABLE ... FORCE ROW LEVEL SECURITY` and a real
  `CREATE POLICY tenant_isolation`. Confirmed directly in
  `apps/api/app/main.py`: `build_litellm_connection_router` wired
  unconditionally via `app.include_router(...)` in `create_app()`.
  Confirmed directly in `packages/contracts/jsonschema/02-domain.schema.json`:
  `LiteLLMConnection` has no `secret_ref` property, `additionalProperties: false`.
- **Merge-readiness confirmed before merge:** PR #133 up to date with
  `main` (base `dee3931` an ancestor of the PR head), `mergeable_state:
  clean`.
- **CI reconfirmed on the real final head:** polled `get_check_runs` until
  completion (`gate` and `docker-local-stack-integration` were still
  `in_progress` on first poll); all 11 checks completed `success` on
  `d0aec3b2cd5862bb046104d4e8ca822bc48b10a9` (run `35496794399`),
  including the new `litellm-connection-crud-integration` job.
- **Own re-run, this sandbox:** `git fetch --unshallow` first (shallow
  clone, same pre-existing artifact FTF-0106's own certifying session
  already documented). Stood up a real, directly-installed local
  PostgreSQL 16 cluster (`sovra_migrate`/`sovra_ftf0201` — no
  container-registry egress in this sandbox, same finding as
  FTF-0004/FTF-0102/FTF-0104/FTF-0105/FTF-H003/FTF-0106/FTF-R017/FTF-1601).
  `uv run python3 tests/tasks/test_ftf_0201.py` → "all checks passed";
  `uv run python3 tests/integration/test_ftf_0201_litellm_connection_crud.py`
  → "all checks passed" (28 real live-Postgres checks). **Noted for the
  record:** running `tests/tasks/test_ftf_0201.py` under `pytest` instead
  of directly reports a misleadingly-green "24 passed" regardless of real
  outcomes — that file's own `check()` helper only appends to a
  module-level `FAILURES` list rather than asserting inside the `test_*`
  functions `pytest` collects. Confirmed this directly: re-ran it under
  `pytest` both before and after a real fault injection and got "24
  passed" both times, while the script's own direct invocation (the way
  CI actually runs it, per `.github/workflows/ci.yml` line 142) correctly
  reported the failures. Future reviewing sessions should invoke it
  directly, not via `pytest`, to get the real signal.
- **Own fault injections, distinct from the producer's own two (widening
  `CONNECTION_ADMIN_ROLES`; leaking `secret_ref` back into the response
  serializer):**
  1. Widened `validate_base_url`'s allowed scheme set from `("https",)`
     to `("https", "http")` — a real TLS-downgrade bug (Section 3.1/65.4
     requires https-only). Caught for real by both suites: 3 checks
     failed in the no-DB suite (the named http-scheme rejection case plus
     two downstream checks), 1 check failed in the live-Postgres suite
     (the creation-time 400-rejection check). Reverted byte-identical
     (`git diff --stat` clean) and both suites re-ran clean afterward.
  2. Also tried removing the `authorize_resource_ownership` call from
     `_fetch_or_raise` entirely — neither suite caught it. Did not stop
     at "not caught": traced it to a real, non-defective reason — `FORCE
     ROW LEVEL SECURITY` plus `execute_tenant_scoped`'s own RLS-bound
     session already block the cross-tenant row at the database level
     regardless of the application-layer check, so
     `authorize_resource_ownership` here is defense-in-depth on top of a
     DB-level guarantee, not the sole IDOR control. Not a defect;
     disclosed in `docs/spec-matrix.yaml`'s own FTF-0201 notes for the
     next session rather than silently dropped.
- **Full regression, this sandbox:** `ruff check .` / `ruff format --check
  .` / `mypy .` clean (91 files); `pnpm install --frozen-lockfile` then
  `pnpm run lint` / `typecheck` / `format:check` clean; `pnpm exec vitest
  run` 5/5; `uv run pytest tests/unit` 3/3; all existing
  `tests/tasks/test_ftf_*.py` re-run clean; `python3
  scripts/validate_spec.py --strict` clean (after unshallowing);
  `sha256sum CLAUDE.md` matches the committed `CLAUDE.md.sha256`;
  `scripts/status.py` confirmed non-inflated (`24/300` before, `25/300`
  / zero FAILED after; "Product-true" `1` before, `2` after).
- **`product_truth_check.py --fail-on-unwired`:** independently
  reconfirmed `2/2` (`build_oidc_login_router` plus
  `build_litellm_connection_router`, both confirmed genuinely imported
  and called unconditionally from `apps/api/app/main.py`'s
  `create_app()` by direct source read).
- **Task contract re-pulled directly:** `python tools/r1_handoff.py packet
  FTF-0201 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1
  ("Freeze the minimum shared phase interface and domain schemas at the
  declared owner paths, with schema/client validation and no placeholder
  endpoints. Then: Create/edit/revoke an organization-scoped gateway
  connection with secret refs and explicit TLS/URL policy") met by the
  delivered CRUD module plus the `contracts:02` freeze; acceptance clause
  2 satisfied by this review itself; negative_acceptance ("Foreign
  connection ID, rejected URL or secret echo fails; cannot mutate another
  organization gateway") independently confirmed above via the
  live-Postgres IDOR/rejection/no-secret-echo checks. Confirmed the
  disclosed scope narrowing (no `validate`/`sync-models`/`/models`/
  `/models/{id}/probe` routes) is genuinely justified against
  `docs/TASK_INDEX.md` — FTF-0203/FTF-0204/FTF-0210 are later, separately
  dependency-gated tasks that own that surface, not a hidden cut in the
  producer's favor; declaring that surface here with no real backing
  handler would itself violate Law 11. All three dependencies
  (FTF-0103/FTF-0104/FTF-0005) confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- **Conclusion:** the delivered organization-scoped LiteLLM gateway
  connection CRUD (create/get/list/edit/revoke) is real, live-wired into
  `apps/api/app/main.py`, independently reproduced against a real local
  PostgreSQL 16 cluster, genuinely fault-injectable via an own,
  this-session-chosen break distinct from the producer's own two and
  correctly caught by both suites, with CI green on the real final merged
  head (11/11) and `product_truth_check.py` legitimately reporting `2/2`.
  No blocking defect.
- **Action taken:** merged PR#133 (merge commit
  `3588e4e5e8f0fcb81f0f458869f2d1e252ea1305`). Set `docs/spec-matrix.yaml`
  FTF-0201 to `certification_state: CERTIFIED` / `proof_state:
  L2-CERTIFIED` (`evidence_hash` kept at
  `1815080980fb0280f33a864b5f9ad4850ce997de`, matching the new
  `spec/verification-map.json` entry's `commit` field, the same
  implementation-commit convention FTF-0105/FTF-H003/FTF-0106 used). Added
  FTF-0201's own entry to `spec/verification-map.json`. Ran
  `sync_certified_status()` — diff limited to exactly FTF-0201's own
  `status`/`evidence` fields in `spec/tasks.json`/
  `docs/implementation/TRACKER.json`. Full ledger entry appended to
  `docs/certifications/ledger.md`. `QUEUE.md` updated (Certified
  24 → 25, Product-true 1 → 2). `PROGRESS.md` updated with the full review
  narrative. This certification's own doc updates are pushed on a branch
  and opened as a PR against `main`, not pushed directly, per
  `BRANCH_PROTECTION`.
- **Validated-by:** session
  https://claude.ai/code/session_01RLyWKThYBPJkaqeDp2HZsN (independent —
  see session-ID check above), 2026-09-20.

### Control plane review — 2026-09-20 — FTF-0301 (PR #135) — NOT CERTIFIED (real Law 1 gap: the "unknown owner" negative_acceptance leg never proven able to fail against live Postgres)
- **Trigger:** scheduled Control-plane certifier shift, `pull_request.opened`
  webhook for PR #135
  (https://github.com/SJZLab/Sovra/pull/135, `claude/wizardly-fermat-h7e3n7`
  → `main`).
- **Session-ID check:** all 3 commits on PR #135 (`0f50c1e`, `30ba359`,
  `0dd3473`) carry `Claude-Session: .../session_01UFUfcwwet85jUWc3BNVoJZ`;
  this reviewing session's own ID is `session_01GfsUDjXzko44RWa1HfAxwz` —
  no match, independent per Law 4.
- **Diff read directly** against the real PR head `0dd34733...05790`
  (`git checkout -B review-pr135 origin/claude/wizardly-fermat-h7e3n7`,
  `git rev-parse HEAD` confirmed it matches the PR's own reported head
  exactly): 18 files, +6722/-27
  (`apps/api/app/artifacts/{artifact_schema,artifact_crud,artifact_routes}.py`,
  `apps/api/alembic/versions/0004_trainable_artifacts.py`,
  `apps/api/app/main.py`, `packages/contracts/openapi/03-interface.yaml`,
  `packages/contracts/jsonschema/03-domain.schema.json`,
  `tests/tasks/test_ftf_0301.py`,
  `tests/integration/test_ftf_0301_trainable_artifact_schema_api.py`,
  `.github/workflows/ci.yml`, `docs/spec-matrix.yaml`, `QUEUE.md`,
  `PROGRESS.md`, `docs/certifications/queue.md`, three task-packet docs).
  Confirmed directly in `apps/api/app/main.py`: `build_trainable_artifact_router`
  wired unconditionally via `app.include_router(...)` in `create_app()`
  (the third live-wired product-runtime path). Confirmed directly in
  `apps/api/alembic/versions/0004_trainable_artifacts.py`: real
  `GRANT SELECT, INSERT, UPDATE` (no DELETE), `FORCE ROW LEVEL SECURITY`,
  and a real `CREATE POLICY tenant_isolation`.
- **CI reconfirmed on the real final head:** `get_check_runs` on PR #135
  — 12/12 `completed`/`success` (run `35501775524`), including the new
  `trainable-artifact-schema-api-integration` job.
- **Own reproduction, this sandbox:** stood up a real, directly-installed
  local PostgreSQL 16 cluster (`sovra_migrate_review` role,
  `sovra_ftf0301_review` database — no container-registry egress in this
  sandbox, same finding as every prior integration-tested task) and ran
  the real forward migration chain (0001→0004) clean. `uv run python3
  tests/tasks/test_ftf_0301.py` → "all checks passed" (exit 0); `uv run
  python3 tests/integration/test_ftf_0301_trainable_artifact_schema_api.py`
  → "all checks passed" (exit 0) against the real cluster.
  `product_truth_check.py --fail-on-unwired` → real `3/3`
  (`build_oidc_login_router`, `build_litellm_connection_router`,
  `build_trainable_artifact_router`, all confirmed genuinely imported and
  called unconditionally). `ruff check .` / `mypy .` clean (98 files);
  `validate_spec.py --strict` clean (after `git fetch --unshallow`, same
  transient shallow-clone artifact every prior certifying session's own
  ledger entry already documents); `sha256sum CLAUDE.md` matches the
  committed `CLAUDE.md.sha256`.
- **Task contract re-pulled directly:** `python tools/r1_handoff.py
  packet FTF-0301 --plan FINE_TUNING_FACTORY_RELEASE_1.md` —
  negative_acceptance reads exactly "Unsafe URI, unknown owner or mutable
  unresolved revision cannot enter training as ready," three independent
  conditions. Both dependencies (FTF-0103, FTF-0104) confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- **Own fault injection, distinct from the producer's own (which widened
  `is_pinned_revision` to unconditionally return `True`, already caught
  by both suites per the PR's own submission entry):** targeted the
  "unknown owner" leg specifically — in `apply_mark_ready`
  (`apps/api/app/artifacts/artifact_crud.py`), disabled only the
  `license_accepted_by`/`license_accepted_at` check (wrapped its
  condition in `False and (...)`, so it can never contribute a refusal
  reason), leaving the `source_uri` and `weights_digest`/
  `is_pinned_revision` checks untouched.
  - **No-DB suite (`tests/tasks/test_ftf_0301.py`):** caught for real — 1
    check failed (`negative_acceptance: mark_ready is refused with no
    license acceptance and no resolved weights_digest`). Exit 1.
  - **Live-Postgres suite
    (`tests/integration/test_ftf_0301_trainable_artifact_schema_api.py`):**
    **did NOT catch it** — re-ran against the real cluster with the fault
    still in place: "all checks passed", exit 0, zero `FAIL [...]` lines.
  - Reverted byte-identical (`git diff --stat
    apps/api/app/artifacts/artifact_crud.py` empty) immediately after;
    both suites re-ran clean on the unfaulted code.
- **Root cause, read directly in the test file:** the live-Postgres
  suite's own mark-ready sequence (lines ~356-446) is: (1) assert refused
  with nothing satisfied; (2) **accept the license immediately next**;
  (3) assert refused with license accepted but revision+digest
  unresolved; (4) resolve `weights_digest` alone, assert still refused
  (the mutable-revision isolation the producer's own fault-injection note
  says it added, for exactly this reason, to catch a broken
  `is_pinned_revision`); (5) resolve the revision, assert success. The
  license is accepted at step (2) and never revisited — there is no step
  that holds the license unaccepted while `weights_digest` and
  `base_model_revision` are both already resolved, so the "unknown owner"
  leg is never exercised in isolation the way the mutable-revision leg
  now deliberately is. Step (1) still reports refused when the fault is
  present only because the *other two* unsatisfied conditions (mutable
  revision, unresolved digest) independently produce the same 409 — the
  same class of false-cover the producer's own submission already
  diagnosed and fixed for the mutable-revision leg ("the integration
  suite's *first* version did not catch it, because it resolved
  `base_model_revision` and `weights_digest` together in one PATCH"), but
  the identical fix was never applied to the license-acceptance leg.
- **Why this is a certification blocker, not a nice-to-have (matching the
  FTF-1601/PR #96 precedent for the identical class of gap):** CLAUDE.md
  Law 1 is unconditional — "Nothing proven until test shown able to fail
  — evidence bound to criterion ID, test ID, commit, failure reason,
  hash." This task's own negative_acceptance clause names three
  independent conditions; the PR's own queue.md submission entry
  explicitly claims "the mark-ready negative_acceptance gate proven with
  three separate real HTTP round-trips isolating each of its three
  conditions in turn" — that claim is not accurate as shipped. Only two
  of the three conditions (mutable revision, unresolved digest) are ever
  isolated against a real live database; the third ("unknown owner") is
  proven able to fail only by the no-DB suite, never by the "real
  relevant integration" evidence acceptance clause 2 requires. The
  shipped production code is correct — `apply_mark_ready`'s real,
  unfaulted logic genuinely enforces all three conditions, confirmed
  directly above — this is a test-coverage gap on one leg of the
  acceptance criterion's own live-database evidence, not a defect in the
  shipped logic itself.
- **Action needed before re-requesting certification:** add one more
  isolated live-Postgres negative case to
  `tests/integration/test_ftf_0301_trainable_artifact_schema_api.py`,
  mirroring the mutable-revision isolation already present — create an
  artifact, resolve `weights_digest` and pin `base_model_revision` (both
  via PATCH) *without* ever calling `accept-license`, and assert
  `mark-ready` still returns 409 with the license/owner reason named,
  before then accepting the license and asserting success. No production
  code change is expected or needed (the underlying logic is already
  correct, per the no-DB suite's own existing, passing coverage and this
  review's own fault-injection confirmation above). Push that test to PR
  #135's own branch (`claude/wizardly-fermat-h7e3n7`) and confirm CI
  stays green before resubmitting.
- **Not merged, not certified, per Law 4/Law 1:** `docs/spec-matrix.yaml`
  for FTF-0301 is not yet `PROVISIONAL` on `main` — PR #135 is unmerged,
  so `main` still shows `certification_state: NONE` /
  `implementation_state: ABSENT` for FTF-0301; nothing to downgrade
  there. On PR #135's own branch the PROVISIONAL entry stands as
  submitted — not altered by this review, since the shipped code is
  sound; what's missing is the one live-Postgres negative case named
  above. PR #135 stays open, unmerged.
- **Validated-by:** this session (independent — see session-ID check
  above), https://claude.ai/code/session_01GfsUDjXzko44RWa1HfAxwz,
  2026-09-20.

### Relay follow-up — 2026-09-20 — FTF-0301 fix for the NOT CERTIFIED gap above — resubmitted PROVISIONAL, new PR
- **Trigger:** scheduled relay shift, picked up the exact "Action needed"
  item from the review directly above (no proposing/designing — a fully
  decided, sliced item with its own DoD spelled out by the certifier).
- **Branch note:** this session's own harness assigns it a fresh, dedicated
  branch per shift and does not permit pushing to a different branch
  (`claude/wizardly-fermat-h7e3n7`, PR #135's own branch, belongs to a
  different session). Matching this repo's own established supersession
  precedent for the identical situation (FTF-1601's #90→#96→#100 chain,
  FTF-0101's #103→#112→#114 chain — each fix landed as a new PR from a new
  branch rather than a push to the flawed PR's own branch), this fix is a
  new commit (`86397f123998a251ea7c459770290807f00c86da`) on this session's
  own branch (`claude/wizardly-fermat-t8m4ps`), built on top of PR #135's
  real head (`0dd3473`, merged in cleanly — zero code conflicts, only the
  three tracking docs conflicted and were resolved by keeping `main`'s
  fuller, already-reconciled history, the same resolution FTF-R017/FTF-0004
  established for this exact class of conflict) plus `main`'s own later
  doc-only commit (`8526654`/PR #136, the review above). PR #135 is left
  open; a follow-up PR from this branch supersedes it and should close it
  on merge — no code exists on `main` yet for FTF-0301, so nothing is lost
  by superseding rather than reusing the old PR number.
- **What changed:** exactly the fix named in the "Action needed" section
  above, no more — one new isolated case in
  `tests/integration/test_ftf_0301_trainable_artifact_schema_api.py`
  mirroring the existing mutable-revision isolation: a separate artifact
  (`license-isolation-check`) has `weights_digest` resolved and
  `base_model_revision` pinned via two independent PATCH calls, with
  `accept-license` never called; asserts `mark-ready` still returns 409;
  then accepts the license and asserts `mark-ready` succeeds (200). No
  production code touched — the review above already confirmed the
  shipped logic is correct.
- **Own fault injection, reproducing the certifier's exact fault:** in
  `apply_mark_ready` (`apps/api/app/artifacts/artifact_crud.py`), wrapped
  the `license_accepted_by is None or license_accepted_at is None` check
  in `False and (...)`, identical to the review's own injection. Re-ran the
  live-Postgres suite with the fault in place: exactly 1 check failed —
  this new isolated case (`negative_acceptance: mark-ready is refused
  (409) with a resolved digest and a pinned revision but no license
  acceptance...`) — nothing else. Reverted byte-identical
  (`git diff --stat` empty), both suites re-ran clean (0 failures each).
  This closes the exact Law 1 gap the review identified: the "unknown
  owner" leg is now provably able to fail against a real, live-Postgres
  round-trip, isolated from the other two conditions.
- **Full regression, this session's own sandbox** (real, directly-installed
  local PostgreSQL 16 — no container-registry egress here, same finding as
  every prior integration-tested task; `git fetch --unshallow` run first —
  the initial `validate_spec.py --strict` 11-defect `stale_commit` report
  was the same transient shallow-clone artifact every prior certifying
  session's own ledger entry already documents, clean after unshallowing):
  `ruff check .` / `ruff format --check .` / `mypy .` clean (98 files);
  `pnpm run lint` / `pnpm run typecheck` / `pnpm run format:check` /
  `vitest run` (5/5) clean; all 22 `tests/tasks/test_*.py` scripts clean;
  `tests/integration/test_ftf_0301_trainable_artifact_schema_api.py` and
  `tests/tasks/test_ftf_0301.py` both clean; the five other live-Postgres
  integration suites sharing this same migrated database
  (`test_organization_workspace_schema`, `test_audit_event_writer`,
  `test_tenant_isolation_query_guards`, `test_ftf_h003_cross_layer_isolation`,
  `test_ftf_0106_rbac_idor_security`, `test_ftf_0201_litellm_connection_crud`)
  all re-ran clean, confirming no regression from the merge; `validate_spec.py
  --strict` clean; `product_truth_check.py --fail-on-unwired` → `3/3`;
  `sha256sum CLAUDE.md` matches the committed `CLAUDE.md.sha256`.
  `docs/spec-matrix.yaml`'s FTF-0301 `evidence_hash` updated to this fix's
  real commit SHA (`86397f123998a251ea7c459770290807f00c86da`); it stays
  `PROVISIONAL`/`L1-PROVEN` — this relay shift does not self-certify
  (Law 4), a fresh independent Control-plane session must review this
  fix's own diff, re-run both suites, and perform its own fault injection
  before certifying.
- **Not self-certified, per Law 4:** submitted PROVISIONAL; awaiting a
  fresh independent Control-plane L2 review of this specific fix (not the
  same reviewing session that found the gap, and not this producing
  session).

### Control plane review — 2026-09-20 — FTF-0301 (PR #137) — CERTIFIED
- **Trigger:** scheduled Control-plane certifier shift, `pull_request.opened`
  webhook for PR #137 (https://github.com/SJZLab/Sovra/pull/137,
  `claude/wizardly-fermat-t8m4ps` → `main`).
- **Session-ID check:** all 6 commits on PR #137 (`0f50c1e`, `30ba359`,
  `0dd3473`, `d0bba9b`, `86397f1`, `9bc4923`) carry `Claude-Session:
  .../session_01UFUfcwwet85jUWc3BNVoJZ` or
  `.../session_011RJ3Ct8ASzprr1uy78r6po`; this reviewing session's own ID
  is `session_01MWFKEi3BV8s5pzu4uymi5v` — no match with either producing
  session, independent per Law 4.
- **Diff read directly** against the real PR head `9bc4923`
  (workspace already checked out to it by the triggering event): 18
  files, +6610/-19. Confirmed the one actual behavioral change from PR
  #135's own head is exactly the claimed new isolated case in
  `tests/integration/test_ftf_0301_trainable_artifact_schema_api.py`
  (separate artifact, `weights_digest` resolved and `base_model_revision`
  pinned via two independent `PATCH` calls, `accept-license` never
  called, `mark-ready` asserted refused (409), then license accepted and
  `mark-ready` asserted to succeed (200)); `apps/api/app/artifacts/artifact_crud.py`
  (including `apply_mark_ready`) confirmed byte-identical to PR #135's
  own head — no production code touched by this fix. Confirmed directly
  in `apps/api/app/main.py`: `build_trainable_artifact_router` wired
  unconditionally via `app.include_router(...)` in `create_app()`.
  Confirmed directly in `apps/api/alembic/versions/0004_trainable_artifacts.py`:
  real `GRANT SELECT, INSERT, UPDATE` (no DELETE), `FORCE ROW LEVEL
  SECURITY`, and a real `CREATE POLICY tenant_isolation`.
- **Merge-readiness confirmed before merge:** PR #137 up to date with
  `main` (base `257a0a0` an ancestor of the PR head), `mergeable_state:
  clean`.
- **CI reconfirmed on the real final head:** `get_check_runs` on PR #137
  — 12/12 `completed`/`success` (run `35506579292`), including
  `trainable-artifact-schema-api-integration`.
- **Own reproduction, this sandbox:** `git fetch --unshallow` first
  (shallow clone, same pre-existing artifact every prior certifying
  session's own ledger entry already documents). Stood up a real,
  directly-installed local PostgreSQL 16 cluster
  (`sovra_migrate_cert`/`sovra_ftf0301_cert` — no container-registry
  egress in this sandbox, same finding as every prior integration-tested
  task). `uv run python3 tests/tasks/test_ftf_0301.py` → "all checks
  passed"; `uv run python3
  tests/integration/test_ftf_0301_trainable_artifact_schema_api.py` →
  "all checks passed" against the real cluster.
- **Own fault injection, reproducing the certifier's exact fault from the
  prior PR #135 NOT CERTIFIED review** (the point of this review is to
  confirm the *named* gap is actually closed, not to find a new one): in
  `apply_mark_ready`, wrapped the `license_accepted_by is None or
  license_accepted_at is None` check in `False and (...)`. Re-ran both
  suites with the fault in place:
  1. **Live-Postgres suite:** exactly 1 check failed — the new isolated
     case itself ("negative_acceptance: mark-ready is refused (409) with
     a resolved digest and a pinned revision but no license
     acceptance..."). This is the exact leg the prior review found
     uncaught.
  2. **No-DB suite:** 1 check failed (the pre-existing "mark_ready is
     refused with no license acceptance and no resolved weights_digest"
     case — this suite already caught the fault before this fix; the fix
     closes the live-Postgres gap specifically).
  Reverted byte-identical (`git diff --stat` empty), both suites re-ran
  clean (0 failures each) afterward. **This confirms the exact Law 1 gap
  the prior review identified is now genuinely closed.**
- **Full regression, this sandbox** (matching CI's own tool invocations
  exactly — `uv run ruff`/`uv run mypy`, not a bare global install; an
  earlier bare `ruff check .`/`mypy .` in this sandbox surfaced 7/18
  false-positive errors from a different, non-project toolchain on
  `PATH`, resolved by using `uv run`): `uv run ruff check .` / `uv run
  ruff format --check .` / `uv run mypy .` clean (98 files); `pnpm
  install --frozen-lockfile` then `pnpm run lint` / `typecheck` /
  `format:check` clean; `pnpm exec vitest run` 5/5; `uv run pytest
  tests/unit` 3/3; all 22 `tests/tasks/test_ftf_*.py` scripts re-run
  clean (`uv run python3` for the FastAPI/SQLAlchemy-dependent ones); the
  five other live-Postgres integration suites sharing this migrated
  database (`test_organization_workspace_schema`, `test_audit_event_writer`,
  `test_tenant_isolation_query_guards`, `test_ftf_h003_cross_layer_isolation`,
  `test_ftf_0106_rbac_idor_security`, `test_ftf_0201_litellm_connection_crud`)
  all re-ran clean, confirming no regression from the merge;
  `validate_spec.py --strict` clean (after unshallowing); `sha256sum
  CLAUDE.md` matches the committed `CLAUDE.md.sha256`; `scripts/status.py`
  confirmed non-inflated (`25/300` before, `26/300`/zero FAILED after;
  "Product-true" `2` before, `3` after).
- **`product_truth_check.py --fail-on-unwired`:** independently
  reconfirmed `3/3` (`build_oidc_login_router`,
  `build_litellm_connection_router`, `build_trainable_artifact_router`,
  all confirmed genuinely imported and called unconditionally from
  `apps/api/app/main.py`'s `create_app()` by direct source read).
- **Task contract re-pulled directly:** `python tools/r1_handoff.py
  packet FTF-0301 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance
  clause 1 ("Register versioned artifact URI/revision/license with
  source grants and immutable identity after resolution") met by the
  delivered CRUD module (unchanged from PR #135's own already-reviewed
  shipped logic); acceptance clause 2 satisfied by this review itself;
  negative_acceptance ("Unsafe URI, unknown owner or mutable unresolved
  revision cannot enter training as ready") — all three conditions now
  independently confirmed isolated against live Postgres, closing the
  specific gap the prior review named. Both dependencies (FTF-0103,
  FTF-0104) confirmed genuinely `CERTIFIED` in `docs/spec-matrix.yaml`
  before relying on them.
- **Conclusion:** the delivered organization-scoped TrainableArtifact
  schema/API (register/get/list/edit/accept-license/mark-ready/archive)
  is real, live-wired into `apps/api/app/main.py`, independently
  reproduced against a real local PostgreSQL 16 cluster, and the specific
  Law 1 gap the prior independent review found (the "unknown owner"
  negative_acceptance leg never isolated against live Postgres) is now
  genuinely closed — confirmed by this session's own fault injection
  reproducing that exact gap and observing it caught. CI green on the
  real final merged head (12/12) and `product_truth_check.py` legitimately
  reporting `3/3`. No blocking defect.
- **Action taken:** merged PR #137 (merge commit
  `634d59775253600a83fb0ff7c94793fa4fbb89de`). Closed PR #135 (the
  superseded first submission) with a comment pointing to this
  certification. Set `docs/spec-matrix.yaml` FTF-0301 to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`
  (`evidence_hash` kept at the fix's real commit SHA `86397f1`, matching
  the new `spec/verification-map.json` entry's `commit` field). Added
  FTF-0301's own entry to `spec/verification-map.json`. Ran
  `sync_certified_status()` — diff limited to exactly FTF-0301's own
  `status`/`evidence` fields in `spec/tasks.json`/
  `docs/implementation/TRACKER.json`. Full ledger entry appended to
  `docs/certifications/ledger.md`. `QUEUE.md` updated (Certified
  25 → 26, Product-true 2 → 3). `PROGRESS.md` updated with the full
  review narrative. This certification's own doc updates are pushed on a
  branch and opened as a PR against `main`, not pushed directly, per
  `BRANCH_PROTECTION`.
- **Validated-by:** session
  https://claude.ai/code/session_01MWFKEi3BV8s5pzu4uymi5v (independent —
  see session-ID check above), 2026-09-20.

## 2026-09-20 — FTF-0401 — Project schema/API/UI
- **PR:** https://github.com/SJZLab/Sovra/pull/139
  (`claude/wizardly-fermat-wc06b1` → `main`, title
  `[relay] FTF-0401: Project schema/API/UI`).
- **Commit:** 141be0f381a00f4c68664c9358404922837c1d29 (implementation);
  `docs/spec-matrix.yaml`/`PROGRESS.md`/`QUEUE.md`/this entry pushed as a
  second, tracking-doc-only commit on the same branch.
- **Dependency-ready pick reasoning:** FTF-0202 ("Secret-manager
  integration") became dependency-ready this shift (FTF-0005/FTF-0103/
  FTF-0201 all CERTIFIED), but its own acceptance clause 1 ("Store/
  resolve/rotate short-lived secret refs through the actual selected
  secret backend") names no concrete backend anywhere in this
  repository's architecture docs — grepped
  `docs/architecture/REFERENCE_REPOSITORIES.md`, `SPEC.md` and this
  plan's own Section 77/81 seven-component list; a secret manager is not
  one of the seven reused components. Only a `vault://`/`openbao://`/
  `secret://` reference-string *shape* convention already exists
  (`apps/api/app/litellm/connection_schema.py`,
  `apps/api/app/auth/oidc_settings.py`), which fixes the shape of a
  stored reference, not the backend it resolves against. Standing up a
  real resolver means selecting and deploying a real external
  secret-management system — a new runtime dependency and a keyed
  external service, both explicit "Ask owner first" hard stops per
  CLAUDE.md — so this shift logged the ask in `QUEUE.md`'s "Owner
  actions — current" (matching the FTF-0101 new-runtime-deps precedent)
  and picked the next dependency-ready item with no such block instead.
  Of the remaining dependency-ready items (FTF-0401/CAP-05, FTF-0305/
  CAP-06, FTF-1501/CAP-14), FTF-0401 has the lowest CAP number and no
  external-service ask of its own.
- **Test:** `tests/tasks/test_ftf_0401.py` (no-DB, 22 test functions / 45
  `check()` assertions) + `tests/integration/test_ftf_0401_project_schema_api.py`
  (real live-Postgres, 32 `check()` assertions) +
  `apps/web/tests/ftf_0401.spec.ts` (real component/DOM, 9 tests, Vitest +
  React Testing Library).
- **Evidence:** all three suites ran clean in this sandbox (no
  container-registry egress here — same documented finding as every
  prior integration-tested task; produced against a directly-installed
  local PostgreSQL 16 cluster, wired into CI's new dedicated
  `project-schema-api-integration` job for real registry egress on the
  hosted runner). Own fault injections (Law 1, three, distinct vectors,
  all reverted byte-identical via `git diff --stat` and all affected
  suites re-run clean afterward):
  1. Commented out the workspace-ownership `authorize_resource_ownership`
     call in `create_project` — caught for real by the live-Postgres
     suite (the cross-org `workspace_id` case, expecting 404, failed).
  2. Widened `initial_status_for` to unconditionally return `"active"` —
     caught for real by both the no-DB suite (the exact named
     agentic-always-`setup_incomplete` check) and the live-Postgres suite
     (3 checks, including the immutability-under-PATCH and
     untouched-after-IDOR checks whose expected values assume
     `setup_incomplete`).
  3. (Frontend) leaked `project_type` into the edit-mode PATCH request
     body — caught for real by the UI spec's own "never project_type"
     assertion.
  Also reconfirmed no regression: re-ran every other live-Postgres
  integration suite sharing this migrated database
  (`test_organization_workspace_schema`, `test_audit_event_writer`,
  `test_tenant_isolation_query_guards`, `test_ftf_h003_cross_layer_isolation`,
  `test_ftf_0106_rbac_idor_security`, `test_ftf_0201_litellm_connection_crud`,
  `test_ftf_0301_trainable_artifact_schema_api`) — all clean. Full
  regression: `uv run ruff check .`/`ruff format --check .`/`mypy .`
  clean (105 source files); `pnpm run lint`/`typecheck`/`format:check`
  clean; `pnpm exec vitest run` — 14/14 (5 pre-existing + 9 new);
  `uv run pytest tests/unit` — 3/3; every existing
  `tests/tasks/test_ftf_*.py` re-run clean (`git fetch --unshallow` first
  — same pre-existing shallow-clone `stale_commit` artifact every prior
  certifying/relay session has already documented);
  `python3 scripts/validate_spec.py --strict` clean;
  `product_truth_check.py --fail-on-unwired` — `4/4` (this entry's own
  `proving_symbol`, `build_project_router`, set at this PROVISIONAL
  submission — see State below — matching the FTF-0201/FTF-0301
  producer-shift precedent; a real caller found: `apps/api/app/main.py`'s
  `create_app()`).
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.
  `proving_symbol` set to `build_project_router` (`proving_test`
  `tests/tasks/test_ftf_0401.py`) at this PROVISIONAL submission, matching
  the FTF-0201/FTF-0301 producer-shift precedent — only
  `certification_state`/`proof_state`'s `CERTIFIED`/`L2-CERTIFIED` values
  are the certifying session's own to set, confirmed genuinely,
  unconditionally wired into `apps/api/app/main.py`'s `create_app()` —
  the fourth live-wired product-runtime path in this repository.
  `scripts/status.py`'s own stricter "product-true" count (which also
  requires `certification_state in (CERTIFIED, PROMOTED)`) correctly
  stays 3 until an independent Control-plane review certifies this task.
- **Full acceptance/negative_acceptance mapping:** acceptance clause 1
  ("Create/edit agentic or generic project with versioned security/
  budget/owner settings and saved UI state") — create/edit implemented
  for both `project_type` values; `security_profile`/`created_by` are the
  "settings/owner" fields Section 6.8 names; `row_version` is the
  versioning Section 6's own preamble requires; `ui_state_json` plus the
  dedicated `POST .../ui-state` route is the "saved UI state" this task's
  own file list and the React component both implement for real (a
  genuine backend round-trip, not client-only storage). Acceptance clause
  2 (independent review/evidence) is this submission's own job to enable,
  not to satisfy itself (Law 4). negative_acceptance ("Agentic project
  without approved target/runtime remains setup-incomplete; no silent
  generic downgrade") is enforced by construction — `project_type` is
  immutable (`apply_edits`'s own signature has no such parameter) and
  `initial_status_for` makes an agentic project always start (and, within
  this task's own surface, always stay) `setup_incomplete` — proven both
  at the pure-function level and over a real HTTP round-trip attempting
  to smuggle `project_type` through a PATCH.
- **Disclosed, not hidden:** a duplicate-name create within the same
  workspace is rejected by the real database unique constraint but
  surfaces as an unhandled `IntegrityError` (HTTP 500) rather than a
  clean 400/409, matching FTF-0201/FTF-0301's own identical, already-
  CERTIFIED precedent; `/requirements`/`/tools` routes and real
  security-profile/budget-policy enforcement are explicitly NOT this
  task's scope (FTF-0402/FTF-0403/FTF-0404/FTF-0407/FTF-0408's own,
  later, separately dependency-gated tasks); `principal_resolver`/
  `role_lookup` stay at their fail-closed defaults, the same disclosed
  gap FTF-0103/FTF-0106/FTF-0201/FTF-0301 already carry.

### Control plane review — 2026-09-20 — FTF-0401 (PR #139) — CERTIFIED
- **Trigger:** scheduled Control-plane certifier shift, `pull_request.opened`
  webhook for PR #139 (https://github.com/SJZLab/Sovra/pull/139,
  `claude/wizardly-fermat-wc06b1` → `main`).
- **Session-ID check:** all 3 commits on PR #139 (`141be0f`, `24b36c8`,
  `5b4dc4c`) carry `Claude-Session: .../session_01HCDh4VoHfgGL9NkLs6Q4YG`;
  this reviewing session's own ID is `session_01Jnn617YvqzzmxdTV1RgiFD` —
  no match, independent per Law 4.
- **Diff read directly** against the real PR head `5b4dc4c` (19 files,
  +5576/-31): confirmed `build_project_router` wired unconditionally into
  `apps/api/app/main.py`'s `create_app()` — the fourth live-wired
  product-runtime path. Confirmed `apply_edits`'s own signature
  (`apps/api/app/projects/project_crud.py`) has no `project_type`
  parameter at all; `create_project` calls `_authorize_workspace_or_raise`
  before ever constructing the `Project` row; the PATCH route
  (`project_routes.py`) never reads `project_type` from the request body
  at all. Confirmed the migration (`0005_projects.py`) grants only
  SELECT/INSERT/UPDATE (no DELETE), FORCEs row-level security, and adds
  the real composite foreign key via a purely additive
  `UNIQUE(organization_id, id)` on `workspaces`.
- **Merge-readiness confirmed before merge:** PR #139 `mergeable_state:
  clean`, base `0ec0b73` an ancestor of the PR head.
- **CI reconfirmed on the real final head:** `get_check_runs` — 13/13
  `completed`/`success` (run `35512925027`), including
  `project-schema-api-integration`.
- **Own reproduction, this sandbox:** `git worktree add` against the real
  PR head; `uv sync --all-packages` (root `uv sync` alone under-installs
  workspace-member deps in this sandbox — pre-existing environment
  quirk); stood up a real local PostgreSQL 16 cluster (no
  container-registry egress in this sandbox, same finding as every prior
  integration-tested task). `uv run python3 tests/tasks/test_ftf_0401.py`
  → "all checks passed"; `uv run python3
  tests/integration/test_ftf_0401_project_schema_api.py` → "all checks
  passed" against the real cluster; `pnpm exec vitest run` — 14/14.
- **Own fault injection, two, distinct vectors** (Law 1): both reverted
  byte-identical, all affected suites re-run clean afterward.
  1. `initial_status_for` changed to unconditionally `return "active"`:
     no-DB suite — 1 failure (the exact named agentic-always-
     `setup_incomplete` case); live-Postgres suite — 3 failures.
  2. `_authorize_workspace_or_raise` call removed from `create_project`:
     live-Postgres suite — 1 failure (cross-org `workspace_id` case no
     longer refused with 404).
- **Full regression, this sandbox:** `uv run ruff check .`/`ruff format
  --check .`/`mypy .` clean (105 source files); `pnpm run lint`/
  `typecheck`/`format:check` clean; `uv run pytest tests/unit` — 3/3; the
  seven other live-Postgres integration suites sharing this migrated
  database all re-ran clean; `validate_spec.py --strict` clean;
  `sha256sum CLAUDE.md` matches the committed `CLAUDE.md.sha256`.
- **`product_truth_check.py --fail-on-unwired`:** independently
  reconfirmed `4/4`.
- **Task contract re-pulled directly:** `python tools/r1_handoff.py packet
  FTF-0401 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance and
  negative_acceptance clauses both independently confirmed met; the
  abbreviated `api_contract`'s `/requirements`/`/tools` routes confirmed
  genuinely out of scope via `docs/TASK_INDEX.md` (FTF-0404/FTF-0407/
  FTF-0408 each list FTF-0401 as their own dependency, not the reverse).
  Both dependencies (FTF-0102, FTF-0103, FTF-0104) confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- **Conclusion:** the delivered workspace-scoped Project schema/API/UI is
  real, live-wired into `apps/api/app/main.py`, independently reproduced
  against a real local PostgreSQL 16 cluster, and this task's own
  negative_acceptance (immutable `project_type`,
  agentic-always-`setup_incomplete`) is confirmed enforced by
  construction — proven by this session's own two independent fault
  injections, both caught for real. CI green on the real final merged
  head (13/13) and `product_truth_check.py` legitimately reporting `4/4`.
  No blocking defect.
- **Action taken:** merged PR #139 (merge commit
  `59e14e71a446bbc68b8488ecc221ae5877aba14a`). Set
  `docs/spec-matrix.yaml` FTF-0401 to `certification_state: CERTIFIED` /
  `proof_state: L2-CERTIFIED`. Added FTF-0401's own entry to
  `spec/verification-map.json`. Ran `sync_certified_status()` — diff
  limited to exactly FTF-0401's own `status`/`evidence` fields in
  `spec/tasks.json`/`docs/implementation/TRACKER.json`. Full ledger entry
  appended to `docs/certifications/ledger.md`. `QUEUE.md` updated
  (Certified 26 → 27, Product-true 3 → 4). `PROGRESS.md` updated with the
  full review narrative. This certification's own doc updates are pushed
  on a branch and opened as a PR against `main`, not pushed directly, per
  `BRANCH_PROTECTION`.
- **Validated-by:** session
  https://claude.ai/code/session_01Jnn617YvqzzmxdTV1RgiFD (independent —
  see session-ID check above), 2026-09-20.

## 2026-09-20 — FTF-0402 — Project security profile
- **PR:** https://github.com/SJZLab/Sovra/pull/141
  (`claude/wizardly-fermat-dj7v5w` → `main`, title
  `[relay] FTF-0402: Project security profile`).
- **Commit:** 99a63b0b7413c75d36707ef928a2de2b3a33a01d (implementation);
  `docs/spec-matrix.yaml`/`PROGRESS.md`/`QUEUE.md`/this entry pushed as a
  second, tracking-doc-only commit on the same branch, matching the
  FTF-0401 two-commit precedent.
- **Dependency-ready pick reasoning:** this shift found 8 dependency-ready
  items (FTF-0202, FTF-0305, FTF-0402, FTF-0403, FTF-0404, FTF-1001,
  FTF-1401, FTF-1501 — computed from `spec/tasks.json`'s own dependency
  graph against `docs/spec-matrix.yaml`'s live certification state).
  FTF-0202 stays logged as an owner ask below (unchanged this shift — no
  concrete secret backend is named anywhere in this repository's
  architecture docs, same finding as the prior shift that first raised
  it). Of the remaining seven, FTF-0402 has the lowest CAP number (CAP-05,
  tied with FTF-0404) with no external-service ask of its own, and
  unblocks the most downstream work once CERTIFIED: FTF-0408, FTF-0500D,
  FTF-0514, FTF-0617, FTF-0901, FTF-0902 and FTF-1206 all name FTF-0402 as
  a dependency (`docs/TASK_INDEX.md`).
- **Task contract:** `python tools/r1_handoff.py packet FTF-0402 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1 ("Enforce
  selected privacy profile across datasets, source, worker, models,
  judges and telemetry"), negative_acceptance ("User cannot relax
  inherited organization policy by changing a project field or using
  direct API"), `database_changes: "No independent schema change"`,
  `files: [apps/api/app/services/project_security_profile.py,
  tests/tasks/test_ftf_0402.py]`. Six of the acceptance clause's own
  named surfaces (datasets/source/worker/models/judges/telemetry) do not
  exist as live modules in this repository yet — they are FTF-0500s/
  0600s/0800s/FTF-0901's own, later, separately dependency-gated tasks
  (`docs/TASK_INDEX.md`). Neither Section 6 (Organization/Project) nor
  any other section this packet cites fixes a concrete security-profile
  enumeration or ordering — `Organization.default_security_profile`/
  `Project.security_profile` are both typed as opaque strings today
  (FTF-0102/FTF-0401, both already CERTIFIED). This task's own job is to
  make that real.
- **Scope decision (disclosed, matching the FTF-0301/FTF-0401 precedent
  of an explicit, non-hidden scope narrowing):** implements the real,
  closed, ordered taxonomy and the org-inheritance/no-relaxation rule at
  the one surface that already exists today — project creation and edit
  (FTF-0401's own `create_project`/`update_project`). Does NOT implement
  enforcement inside datasets/source/worker/models/judges/telemetry
  themselves (those six surfaces do not exist as live code yet);
  `require_profile_at_least` is shipped as the general-purpose primitive
  those later tasks will call once they exist, matching the disclosed-gap
  precedent FTF-0103/FTF-0104/FTF-0105/FTF-H003 already established.
  Taxonomy naming (`standard`/`zero_egress`/`air_gapped`) is grounded
  directly in this specification's own text — Law 17/Section 11.1 names
  `ZERO_EGRESS` as a real enforced posture, and the spec's own `AIR_GAPPED`
  description ("adds a deployment constraint to the existing ZERO_EGRESS
  policy") fixes it as strictly more restrictive — not invented; `standard`
  is this task's own least-restrictive floor, matching both columns' own
  existing `"standard"` server default.
- **Test:** `tests/tasks/test_ftf_0402.py` (no-DB, 8 test functions / 23
  `check()` assertions) + `tests/integration/test_ftf_0402_project_security_profile.py`
  (real live-Postgres, 15 `check()` assertions).
- **Evidence:** both suites ran clean in this sandbox (no
  container-registry egress here — same documented finding as every
  prior integration-tested task; the live-Postgres suite was produced
  against a directly-installed local PostgreSQL 16 cluster, and is wired
  into CI's new dedicated `project-security-profile-integration` job for
  real registry egress on the hosted runner). Own fault injections (Law
  1, two, distinct vectors, both reverted byte-identical via `diff`/`git
  diff --stat` and both affected suites re-run clean afterward):
  1. Replaced the `if requested_rank < org_rank:` relaxation check inside
     `enforce_security_profile_selection` with `if False:` — caught for
     real by the no-DB suite (exactly the 3 named negative_acceptance
     checks failed: org=air_gapped/requested=standard,
     org=zero_egress/requested=standard, org=air_gapped/requested=
     zero_egress).
  2. Removed the `enforce_security_profile_selection(...)` call from
     `create_project` (leaving the fetched
     `organization_default_security_profile` unused) — caught for real
     by the live-Postgres suite (exactly the 3 named creation-time
     negative_acceptance checks failed: the omitted-field/client-default
     case, the explicit-relaxation case, and the unrecognized-profile-
     name case).
  Also reconfirmed no regression: re-ran FTF-0401's own no-DB
  (`tests/tasks/test_ftf_0401.py`) and live-Postgres
  (`tests/integration/test_ftf_0401_project_schema_api.py`) suites after
  this change — both still "all checks passed" (this task's own
  `apply_edits` core is untouched; only the impure `create_project`/
  `update_project` wrappers gained a new check). Full regression: `uv run
  ruff check .`/`ruff format --check .`/`mypy .` clean (108 source
  files); `pnpm run lint`/`typecheck`/`format:check` clean; `pnpm exec
  vitest run` — 14/14 (unchanged, no frontend files touched); `uv run
  pytest tests/unit` — 3/3; every existing `tests/tasks/test_ftf_*.py`
  re-run clean (`git fetch --unshallow` first — same pre-existing
  shallow-clone `stale_commit` artifact every prior certifying/relay
  session has already documented); `python3 scripts/validate_spec.py
  --strict` clean; `product_truth_check.py --fail-on-unwired` — `5/5`
  (this entry's own `proving_symbol`, `enforce_security_profile_selection`,
  set at this PROVISIONAL submission — see State below — matching the
  FTF-0201/FTF-0301/FTF-0401 producer-shift precedent; real callers
  found in both `apps/api/app/services/project_security_profile.py`
  itself and `apps/api/app/projects/project_crud.py`'s
  `create_project`/`update_project`).
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.
  `proving_symbol` set to `enforce_security_profile_selection`
  (`proving_test` `tests/tasks/test_ftf_0402.py`) at this PROVISIONAL
  submission, matching the FTF-0201/FTF-0301/FTF-0401 producer-shift
  precedent — only `certification_state`/`proof_state`'s
  `CERTIFIED`/`L2-CERTIFIED` values are the certifying session's own to
  set. `scripts/status.py`'s own stricter "product-true" count (which
  also requires `certification_state in (CERTIFIED, PROMOTED)`) correctly
  stays 4 until an independent Control-plane review certifies this task.
- **Full acceptance/negative_acceptance mapping:** acceptance clause 1
  ("Enforce selected privacy profile across datasets, source, worker,
  models, judges and telemetry") is implemented at the one surface that
  exists today (project creation/edit); the other six named surfaces are
  disclosed as later, separately dependency-gated tasks above, not
  claimed here. Acceptance clause 2 (independent review/evidence) is this
  submission's own job to enable, not to satisfy itself (Law 4).
  negative_acceptance ("User cannot relax inherited organization policy
  by changing a project field or using direct API") is proven both at the
  pure-function level (`enforce_security_profile_selection` itself) and
  over real HTTP round-trips against a real Postgres database, at both
  project creation (including the "omitted field defaults to standard,
  which is itself checked" case — a client cannot bypass the floor merely
  by not naming a profile) and project edit (a PATCH cannot relax an
  existing project's profile; the project is confirmed genuinely
  untouched after a refused attempt).
- **Disclosed, not hidden:** enforcement lives in `project_crud.py`'s
  impure `create_project`/`update_project` wrappers, not inside
  `apply_edits` itself — `apply_edits` is FTF-0401's own certified pure,
  DB-free core, and adding a DB-derived organization-default parameter to
  it would have required editing FTF-0401's own already-certified no-DB
  test file's calls to match a new required argument; keeping the
  enforcement in the impure layer (where the organization lookup already
  naturally belongs) avoids that regression risk entirely while still
  covering both real call sites. `validate_security_profile`
  (`project_schema.py`, shape-only: non-empty, ≤64 chars) is left
  unchanged — this task's own enum+relaxation check in the new module is
  additional, not a replacement, defense-in-depth. Datasets/source/
  worker/models/judges/telemetry enforcement is explicitly NOT this
  task's scope — see Scope decision above.

## 2026-09-20 — FTF-0305 — License acceptance workflow
- **PR:** https://github.com/SJZLab/Sovra/pull/144
  (`claude/wizardly-fermat-34mau8` → `main`, title
  `[relay] FTF-0305: License acceptance workflow`).
- **Commit:** 66aff8b6f8b81846891d5da43c3d1eb1e11f708a (implementation);
  `docs/spec-matrix.yaml`/`PROGRESS.md`/`QUEUE.md`/this entry pushed as a
  second, tracking-doc-only commit on the same branch, matching the
  FTF-0401/FTF-0402 two-commit precedent.
- **Dependency-ready pick reasoning:** recomputed the frontier fresh
  against `docs/spec-matrix.yaml`'s live certification state rather than
  assuming the prior shift's own list was still current (per that
  shift's own "Next shift" note). FTF-0402 CERTIFIED this shift (before
  this one), newly unblocking FTF-0408/FTF-0500D/FTF-0514/FTF-0617/
  FTF-0901/FTF-0902/FTF-1206 — none of which are dependency-ready yet
  (each names other, still-uncertified dependencies too). The recomputed
  dependency-ready set is unchanged from the prior shift's own list minus
  FTF-0402 itself: FTF-0202 (owner ask, unchanged — no concrete secret
  backend named anywhere in this repository's architecture docs),
  FTF-0305, FTF-0403, FTF-0404, FTF-1001, FTF-1401, FTF-1501. Of these,
  FTF-0305 has the lowest CAP number (CAP-06) with no external-service
  ask of its own.
- **Task contract:** `python tools/r1_handoff.py packet FTF-0305 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1 ("Authorized
  user records actual license/usage terms and source acceptance bound to
  model revision"), negative_acceptance ("Unaccepted/revoked license
  blocks training/serving; no blanket approval based on provider name"),
  `database_changes: "No independent schema change. Consume frozen
  contract"`, `files: [packages/model-catalog/license_acceptance_workflow.py,
  tests/tasks/test_ftf_0305.py]`, `dependencies: [FTF-0301]` (CERTIFIED),
  `contract_owner: FTF-0301`.
- **Path refinement (disclosed, matching the FTF-0301/FTF-0201 precedent
  of refining an abbreviated/unusable packet path to a real one):**
  `packages/model-catalog` is not a `uv` workspace member
  (`pyproject.toml`'s own `[tool.uv.workspace] members` list) and, being
  hyphenated, is not a valid Python import path at all. FTF-0301 already
  refined its own identical `packages/model-catalog/...` suggestion into
  `apps/api/app/artifacts/*` instead of creating an unimportable package;
  this task follows the same refinement into
  `apps/api/app/artifacts/license_acceptance_workflow.py`, reusing the
  existing artifacts module family rather than scattering a second,
  hyphenated top-level package for one file.
- **Scope decision (disclosed, matching the FTF-0301/FTF-0401/FTF-0402
  precedent of an explicit, non-hidden scope narrowing):** FTF-0301
  already stores `license_id`/`license_accepted_by`/`license_accepted_at`
  on `TrainableArtifact` and already gates `mark_ready` on acceptance —
  this task's own job is not to duplicate that state
  (`database_changes` forbids an independent schema change) but to add
  what FTF-0301 left open: revocation. `apply_revoke_license`/
  `revoke_license` are wholly new, purely additive operations using only
  FTF-0301's own existing nullable columns. An earlier version of this
  submission also auto-invalidated acceptance inside `apply_edits`
  whenever `source_uri`/`base_model_revision`/`license_id` changed, to
  make "acceptance bound to model revision" more actively enforced — but
  running FTF-0301's own live-Postgres suite against that version
  surfaced a real regression: that suite's own certified flow (accept a
  license while `base_model_revision` is still the mutable alias
  `"main"`, then PATCH `base_model_revision` to a pinned value, then
  expect `mark-ready` to succeed) failed, because the new hook cleared
  the acceptance the PATCH itself introduced. This is a genuine Law 15
  ("a material source change invalidates affected evidence until
  requalification") situation this task is not chartered to open
  unilaterally — FTF-0301 is already CERTIFIED and merged. The hook was
  removed; "acceptance bound to model revision" is instead satisfied by
  construction (the acceptance record lives on the exact same row as
  `base_model_revision` — there is no free-floating, artifact-independent
  acceptance fact anywhere in this schema), and revocation is always an
  explicit, on-demand action, never an automatic side effect of an
  unrelated edit. `assert_license_accepted_for_use` has no live
  training/serving caller yet — TrainingExperiment/Deployment are later,
  separately dependency-gated tasks (`docs/TASK_INDEX.md`) — the same
  disclosed-gap class FTF-0103/FTF-0104/FTF-0105/FTF-H003 already
  established before FTF-0201 gave each of them a first real caller.
- **Test:** `tests/tasks/test_ftf_0305.py` (no-DB, 15 test functions,
  including a real locally-hosted `build_trainable_artifact_router` over
  a real uvicorn server for the new route's own role-boundary proof) +
  `tests/integration/test_ftf_0305_license_acceptance_workflow.py` (real
  live-Postgres, 15 `check()` assertions).
- **Evidence:** both suites ran clean in this sandbox (no
  container-registry egress here — same documented finding as every
  prior integration-tested task; the live-Postgres suite was produced
  against a directly-installed local PostgreSQL 16 cluster — freshly
  recreated database and `sovra_app` role each run, since roles are
  cluster-wide and survive a `DROP DATABASE` — and is wired into CI's new
  dedicated `license-acceptance-workflow-integration` job for real
  registry egress on the hosted runner). Own fault injections (Law 1,
  two, distinct vectors, both reverted byte-identical via `diff` and both
  affected suites re-run clean afterward):
  1. Replaced `evaluate_license_gate`'s own
     `if artifact.license_accepted_by is None or ...` unaccepted-branch
     condition with `if False:` — caught for real by the no-DB suite
     (`AttributeError: 'NoneType' object has no attribute 'isoformat'`
     the moment a fresh, unaccepted artifact is evaluated — the gate
     unconditionally tried to format a `None` timestamp as accepted).
  2. Removed the `if artifact.status == "ready": artifact.status =
     "registered"` demotion from `apply_revoke_license` — caught for real
     by the no-DB suite's own two named negative_acceptance checks
     ("revoking a ready artifact's license demotes it back to
     registered" and "a subsequent mark-ready attempt is refused once the
     license has been revoked").
  Also independently reconfirmed no regression to FTF-0301's own
  certified evidence: re-ran its no-DB (`tests/tasks/test_ftf_0301.py`)
  and live-Postgres
  (`tests/integration/test_ftf_0301_trainable_artifact_schema_api.py`)
  suites against a freshly recreated database/role after this task's
  final code — both still "all checks passed", including the exact
  accept-then-pin-revision-then-mark-ready sequence the removed
  auto-invalidation hook would have broken. Full regression: `uv run
  ruff check .`/`ruff format --check .`/`mypy .` clean (114 source
  files); `pnpm run lint`/`typecheck`/`format:check` clean (no frontend
  files touched); `pnpm exec vitest run` — 14/14 (unchanged); `uv run
  pytest tests/unit` — 3/3; every existing `tests/tasks/test_ftf_*.py`
  re-run clean (`git fetch --unshallow` first — same pre-existing
  shallow-clone `stale_commit` artifact every prior certifying/relay
  session has already documented); `python3 scripts/validate_spec.py
  --strict` clean; `product_truth_check.py --fail-on-unwired` — `6/6`
  (this entry's own `proving_symbol`, `apply_revoke_license`, set at this
  PROVISIONAL submission — see State below — matching the
  FTF-0201/FTF-0301/FTF-0401/FTF-0402 producer-shift precedent; real
  callers found in both `license_acceptance_workflow.py` itself and
  `artifact_crud.py`'s own new `revoke_license`, which the new live-wired
  `revoke-license` route calls).
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.
  `proving_symbol` set to `apply_revoke_license` (`proving_test`
  `tests/tasks/test_ftf_0305.py`) at this PROVISIONAL submission, matching
  the FTF-0201/FTF-0301/FTF-0401/FTF-0402 producer-shift precedent — only
  `certification_state`/`proof_state`'s `CERTIFIED`/`L2-CERTIFIED` values
  are the certifying session's own to set. `scripts/status.py`'s own
  stricter "product-true" count (which also requires
  `certification_state in (CERTIFIED, PROMOTED)`) correctly stays 5 until
  an independent Control-plane review certifies this task.
- **Full acceptance/negative_acceptance mapping:** acceptance clause 1
  ("Authorized user records actual license/usage terms and source
  acceptance bound to model revision") is satisfied by FTF-0301's own
  already-certified `accept_license` plus this task's own "bound by
  construction" reasoning above (disclosed, not a new mechanism claimed).
  Acceptance clause 2 (independent review/evidence) is this submission's
  own job to enable, not to satisfy itself (Law 4). negative_acceptance
  ("Unaccepted/revoked license blocks training/serving; no blanket
  approval based on provider name") is proven at the pure-function level
  (`apply_revoke_license` demotes `ready` back to `registered`;
  `assert_license_accepted_for_use`/`evaluate_license_gate` take no
  provider/source_type/model_family parameter, proven both by signature
  introspection and by exercising all four of Section 6.7's own allowed
  `source_type` values identically) and over real HTTP round-trips
  against a real Postgres database (revoke → mark-ready refused again;
  the foreign-artifact IDOR case on the new route; revoke idempotency;
  revoking an archived artifact's license refused).
- **Disclosed, not hidden:** `assert_license_accepted_for_use` has no
  live training/serving caller yet (see Scope decision above); "training/
  serving" is proven at the one concrete admission point that already
  exists today (`status == "ready"`), not at a training/serving system
  this repository does not yet have. This task deliberately does not
  touch `apply_edits` — see Scope decision above for the full Law 15
  reasoning, including the regression this submission's own earlier
  version surfaced and reverted before ever being committed.

## 2026-09-20 — FTF-0404 — Tool schema CRUD
- **PR:** https://github.com/SJZLab/Sovra/pull/146
  (`claude/wizardly-fermat-vkr3sq` → `main`, title
  `[relay] FTF-0404: Tool schema CRUD`).
- **Commit:** `b5d4cb7` (implementation); `docs/spec-matrix.yaml`/
  `PROGRESS.md`/`QUEUE.md`/this entry pushed as a second, tracking-doc-only
  commit on the same branch, matching the FTF-0201/FTF-0301/FTF-0401/
  FTF-0402/FTF-0305 two-commit precedent.
- **Dependency-ready pick reasoning:** recomputed the frontier fresh
  against `docs/spec-matrix.yaml`'s live certification state rather than
  assuming the prior shift's own list was still current. The recomputed
  dependency-ready set: FTF-0403, FTF-0404, FTF-0902, FTF-1001, FTF-1401,
  FTF-1501 (FTF-0202 deliberately excluded — its own ask is not genuinely
  resolved; see the flagged note below). Of these, FTF-0404 has the
  lowest CAP number (CAP-05) with no external-service ask of its own.
- **Task contract:** `python tools/r1_handoff.py packet FTF-0404 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1 ("Create
  immutable-version tool schemas with side-effect policy, evidence and
  project ownership"), negative_acceptance ("Editing used tool schema
  creates new version and drift, never changes historical manifest in
  place"), `database_changes: "Own forward Alembic migration for this
  resource; tenant/composite constraints/RLS and upgrade tests"`,
  `files: [apps/api/app/services/tool_schema_crud.py,
  tests/tasks/test_ftf_0404.py]`, `dependencies: [FTF-0401]` (CERTIFIED),
  `contract_owner: FTF-0401`, `shared_lock_required: [contracts:04,
  migration:application]`.
- **Path refinement (disclosed, matching the FTF-0201/FTF-0301/FTF-0401
  precedent of refining an abbreviated packet path into the real module
  split a new resource actually needs):** the packet's own single
  `apps/api/app/services/tool_schema_crud.py` is a real, importable path
  (unlike FTF-0201/FTF-0301's own hyphenated `packages/...` suggestions),
  but this task's own packet text explicitly warns "Do not scatter domain
  logic into one file per ticket simply because a ticket exists" — a new
  resource with a schema, a CRUD layer and real live routes is exactly
  the shape FTF-0201/FTF-0301/FTF-0401 each already split into a
  schema/crud/routes triplet under its own subpackage. This submission
  follows that same established split:
  `apps/api/app/tools/{tool_definition_schema,tool_definition_crud,
  tool_definition_routes}.py`.
- **Contract ownership (disclosed):** `contracts:04` is already owned and
  frozen by FTF-0401 (`packages/contracts/{openapi,jsonschema}/04-*`),
  whose own file explicitly says "Whichever later task adds its own real
  route extends this same file under this same contract-owner lock." This
  submission extends both files in place (new `/tools` paths and
  `ToolDefinition`/`ToolDefinitionCreateRequest`/`ToolDefinitionEditRequest`
  schemas) rather than creating a second phase-04 file, and updates the
  interface file's own top-level description to note that `/tools` is now
  real while `/requirements` still is not (FTF-0407/FTF-0408's own, later
  scope) — the same disclosed, non-hidden scope narrowing FTF-0401's own
  original text already used for `/tools` itself.
- **Design (disclosed):** Section 6.9 names no status/lifecycle field for
  `ToolDefinition`, and this task's own negative_acceptance is entirely
  about *editing*, not deleting — so `superseded_at IS NULL` marks the one
  current version per `(project_id, name)` (enforced by a real partial
  unique index, not only application code), and `apply_new_version` (the
  one pure, DB-free core that ever produces a new version) never mutates
  the row it replaces except to set `superseded_at`/`updated_at`. A
  version is never deleted (the `sovra_app` grant is SELECT/INSERT/UPDATE
  only, matching the "grant surface matches actual DML need" discipline
  every prior migration in this chain already applies) — a real,
  DB-enforced immutable-lineage record, not merely a documented
  convention. `previous_version_id` self-references `tool_definitions.id`
  (`ON DELETE RESTRICT`, since nothing in this schema ever deletes a row).
  `list_tool_definitions` shows only current versions; a superseded
  version's own full historical content stays reachable by its own ID
  (`get_tool_definition`), the same "list shows current state, history is
  reachable by ID" shape a versioned resource needs. Role gating follows
  FTF-0401's own exact precedent: Section 45 names no role for tool-schema
  authoring specifically, so `TOOL_WRITE_ROLES` is `{workspace_admin,
  organization_admin}` (the nearest, and only, sentence naming a role for
  this class of per-project configuration work — "workspace_admin:
  project administration"), `TOOL_READ_ROLES` is every role Section 45
  names.
- **Test:** `tests/tasks/test_ftf_0404.py` (no-DB, 22 test functions,
  including a real locally-hosted `build_tool_definition_router` over a
  real uvicorn server for the route's own role-boundary proof) +
  `tests/integration/test_ftf_0404_tool_schema_crud.py` (real
  live-Postgres, 20 `check()` assertions).
- **Evidence:** both suites ran clean in this sandbox against a real,
  directly-installed local PostgreSQL 16 cluster (no container-registry
  egress here — same documented finding as every prior integration-tested
  task; wired into CI's new dedicated `tool-schema-crud-integration` job
  for real registry egress on the hosted runner). Two real organizations,
  three real projects (two under org1, one under org2), a real uvicorn
  server proving: create (version 1, no lineage yet) → a duplicate current
  name in the same project rejected by the real database's own partial
  unique constraint → edit (version 2, `previous_version_id` threading
  back to version 1's own real ID, a genuinely different `schema_hash`) →
  the historical version 1, fetched by its own ID afterward, is still
  byte-identical to its original `description`/`input_schema`/
  `schema_hash`/`version`, and is marked superseded → attempting to edit
  version 1 directly again is refused (409) → listing shows exactly the
  current version 2, not the superseded version 1 → a *different* project
  in the same organization cannot reach the tool via its own path (404,
  identical to missing) → the cross-organization IDOR case for GET/PATCH
  (404 identical to missing, and the row confirmed genuinely untouched
  afterward) → a session with no tenant context bound is refused
  (`TenantContextError`, not treated as an ordinary zero-row denial) → the
  restricted `sovra_app` role cannot `DELETE` a `tool_definitions` row
  directly. Own fault injections (Law 1, two, distinct vectors, both
  reverted byte-identical via `diff` and both affected suites re-run
  clean afterward):
  1. Replaced `apply_new_version`'s own
     `if current.superseded_at is not None: raise ...` guard with
     `if False:` — caught for real by the no-DB suite's own
     "editing an already-superseded version is refused" check, and by the
     live-Postgres suite's own "re-editing version 1 directly is refused
     (409)" check (which instead returned 200, silently letting a
     historical version be edited again).
  2. Added `current.input_schema_json = new_input_schema` immediately
     after marking `current` superseded in `apply_new_version` — caught
     for real by the no-DB suite's own "the historical row's own
     input_schema_json is untouched" check, and by the live-Postgres
     suite's own "fetching the historical version 1 by its own ID still
     returns its exact original, byte-identical content" check (which
     instead showed the new, edited schema on the old, historical row —
     exactly the drift this task's own negative_acceptance forbids).
  Also independently reconfirmed no regression to FTF-0401's and
  FTF-0402's own certified evidence: re-ran both of FTF-0401's own suites
  (`tests/tasks/test_ftf_0401.py`, `tests/integration/
  test_ftf_0401_project_schema_api.py`) and FTF-0402's own integration
  suite (`tests/integration/test_ftf_0402_project_security_profile.py`)
  against a freshly recreated database/role after this task's own
  migration — all still "all checks passed" (the new, purely additive
  `UNIQUE(organization_id, id)` constraint on `projects` is trivially
  satisfied by existing data, the same reasoning FTF-0401's own migration
  already used for the identical constraint on `workspaces`). Full
  regression, this sandbox: `git fetch --unshallow origin` first (this
  sandbox's own clone is shallow by default — the same pre-existing
  `stale_commit` artifact every prior certifying/relay session has
  already documented; cleared, then `validate_spec.py --strict` reported
  clean). `uv run ruff check .`/`ruff format --check .`/`mypy .` clean
  (122 source files); `pnpm run lint`/`typecheck`/`format:check` clean (no
  frontend files touched); `pnpm exec vitest run` — 14/14 (unchanged);
  `uv run pytest tests/unit` — 3/3; every existing `tests/tasks/
  test_ftf_*.py` file re-run clean (27 files); `product_truth_check.py
  --fail-on-unwired` — `7/7` (this entry's own `proving_symbol`,
  `build_tool_definition_router`, set at this PROVISIONAL submission —
  see State below — matching the FTF-0201/FTF-0301/FTF-0401/FTF-0402/
  FTF-0305 producer-shift precedent; real caller found in
  `apps/api/app/main.py`'s own `create_app`).
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.
  `proving_symbol` set to `build_tool_definition_router` (`proving_test`
  `tests/tasks/test_ftf_0404.py`) at this PROVISIONAL submission, matching
  the FTF-0201/FTF-0301/FTF-0401 producer-shift precedent of naming the
  router builder itself for a full create/get/list/edit surface (as
  opposed to FTF-0402's own narrower single-primitive `proving_symbol`) —
  only `certification_state`/`proof_state`'s `CERTIFIED`/`L2-CERTIFIED`
  values are the certifying session's own to set. `scripts/status.py`'s
  own stricter "product-true" count (which also requires
  `certification_state in (CERTIFIED, PROMOTED)`) correctly stays 6 until
  an independent Control-plane review certifies this task.
- **Full acceptance/negative_acceptance mapping:** acceptance clause 1
  ("Create immutable-version tool schemas with side-effect policy,
  evidence and project ownership") is met directly — every version is a
  real, distinct, immutable row; `side_effect_class` is a real, closed,
  DB-checked enum; `created_by`/`created_at`/`organization_id`/
  `project_id` are the real evidence/ownership fields, proven both at the
  pure-function level and over real HTTP round-trips. Acceptance clause 2
  (independent review/evidence) is this submission's own job to enable,
  not to satisfy itself (Law 4). negative_acceptance ("Editing used tool
  schema creates new version and drift, never changes historical manifest
  in place") is proven at the pure-function level (`apply_new_version`
  itself, plus the two fault injections above) and over real HTTP
  round-trips against a real Postgres database (edit → new version →
  historical row byte-identical → re-edit of history refused).
- **Disclosed, not hidden:** the Agent Manifest `tool_contracts_json`
  consumer (FTF-0408 and later) has no live caller of this task's own
  `ToolDefinition` rows yet — the same disclosed-gap class FTF-0103/
  FTF-0104/FTF-0105/FTF-H003/FTF-0201/FTF-0301 already established before
  each one's own first live caller existed; here the live caller is this
  task's own router itself (`main.py`), so `product_truth_check.py`
  already counts it product-true — the *consumer* of the schema's
  content, not the schema's own CRUD surface, is what remains undelivered
  (a later task's own scope). `input_schema_json`/`output_schema_json`
  are validated only as real, bounded JSON objects, not as conforming
  JSON Schema meta-schema documents — this task's own
  `database_changes: "No independent schema change"`-adjacent narrow
  charter (Section 6.9 names no meta-schema validation requirement)
  leaves deeper schema-shape validation to whichever later task actually
  consumes these schemas against a real tool-calling runtime.

**Flagged this shift, not silently acted on (Control-plane/owner judgment
call, not a relay one):** PR #143 ("Owner decision: FTF-0202 backend
selected (OpenBao)") is open and unmerged against `main`, claiming FTF-
0202's own new-runtime-dependency/keyed-external-service ask (a CLAUDE.md
hard stop) is resolved ("Owner confirmed selection of OpenBao"). Unlike
every genuine resolution already recorded in this repository's own
history (FTF-0101's stack ask, FTF-0101's local-OIDC-provider ask,
FTF-0102's ORM ask — each naming a specific live conversation and a
specific option number), PR #143's own body and diff carry none of that
detail: no live-conversation citation, no option enumerated, nothing
beyond the bare assertion, and it deletes the entire existing, reasoned
"Owner actions — current" analysis in `QUEUE.md` outright rather than
resolving it against evidence. This session found no corroborating record
anywhere in `PROGRESS.md`/`QUEUE.md` on `main` of an actual owner exchange
about OpenBao. This is exactly the class of "unearned status claim"
`CLAUDE.md`'s own `BRANCH_PROTECTION` note already tracks (two prior
sessions each pushed an unearned "gap resolved" claim). This relay shift
did **not** merge PR #143, did **not** treat FTF-0202 as unblocked, and
left the ask exactly as the last genuine session recorded it in
`QUEUE.md`. Recommend the owner either confirm directly (in which case
merge PR #143 with that confirmation recorded, matching this file's own
evidence bar) or close PR #143 without merging.

## 2026-09-20 — Control plane: FTF-0405 (PR #148) independently reviewed, CERTIFIED
This entry is appended by the certifying session, not the producer —
PR #148 ("[relay] FTF-0405: JSON Schema tool validation") did not itself
append a PROVISIONAL submission entry here (only `docs/spec-matrix.yaml`,
the two test files, `tool_definition_schema.py` and the new
`json_schema_tool_validation.py` were touched by its single commit); the
full PROVISIONAL write-up is preserved in that PR's own description and
in `docs/spec-matrix.yaml`'s own `notes` field prior to this
certification (still visible in `git show 9138f46:docs/spec-matrix.yaml`).

**Session-ID check:** the sole commit on PR #148 (`9138f46`) carries
`Claude-Session: .../session_01ULAxA48EZT6uh2Kg6DziPL`; this reviewing
session's own ID is `session_01XbdXkopSW5Eh19JXpG2EBG` — no match,
independent per Law 4.

**Diff read directly** against the real PR head `9138f46` (6 files,
+2513/-14): `apps/api/app/services/json_schema_tool_validation.py` is a
new, pure, DB-free, network-free module implementing a pinned, safe
subset of JSON Schema Draft 2020-12 (`validate_json_schema_document` for
schema-definition validation, real live caller; `validate_instance` for
call-time instance validation, no live caller yet, disclosed in-module,
matching the FTF-0103/FTF-0104/FTF-0105/FTF-H003/FTF-0305 disclosed-gap
precedent). `$ref` resolution is local-only by construction
(`_check_ref_target` rejects any pointer that is not a literal
`#/$defs/<name>` prefix before any resolution is attempted — no network
I/O anywhere in the module), and `$defs` reference cycles are caught by a
real DFS (`_detect_ref_cycles`) at schema-validation time, independent of
separate `MAX_SCHEMA_DEPTH`/`MAX_SCHEMA_NODES`/`MAX_REF_HOPS` bounds.
`apps/api/app/tools/tool_definition_schema.py`'s `validate_schema_object`
now calls `validate_json_schema_document` for both
`input_schema_json`/`output_schema_json`, on the already-CERTIFIED,
already-live-wired FTF-0404 `ToolDefinition.__init__` path — real,
source-inspection-confirmed live wiring, not merely imported.
`tests/tasks/test_ftf_0404.py`'s own diff is exactly one filler-value
change (`{"type": "different"}` → `{"type": "boolean"}`) in a test whose
own assertions only ever concerned "editing never mutates the historical
row," not schema-shape correctness — not a weakening of coverage.

**CI reconfirmed on the real final head:** GitHub Actions run
`35537769604` — all 16 check runs `completed`/`success` (the `gate` job
was still `in_progress` when this session first checked; re-checked after
genuine completion), including `tool-schema-crud-integration`.

**Own reproduction, this sandbox:** `git checkout pr-148 -- <changed
files>` on top of a fresh `main`; `uv sync --all-packages`; `uv run
python3 tests/tasks/test_ftf_0405.py` → "all checks passed" (23 test
functions, `grep -c "^def test_"`-confirmed -- corrected from PR #148's
own body claim of 25); `uv run python3 tests/tasks/test_ftf_0404.py` → "all checks
passed" (no regression).

**Own fault injection, two, distinct from the producer's own two named in
the PR** (Law 1), both reverted byte-identical (`diff`-confirmed against
the original file) and both suites re-run clean afterward:
1. Removed the non-local-`$ref` rejection in `_check_ref_target`
   (replaced the `_fail_security(...)` call with `pass`): caught for real
   — `FAIL [FTF-0405] negative_acceptance: a non-local $ref
   ('urn:not-a-local-pointer') is refused, as a security-classed error`.
   Directly proves the "remote schema SSRF" negative_acceptance case is
   enforced by real code.
2. Removed the `additionalProperties: false` excess-property rejection in
   `_validate_object_instance` (replaced the `_fail(...)` call with
   `pass`): caught for real — `FAIL [FTF-0405] negative_acceptance: an
   instance carrying a property beyond what additionalProperties: false
   permits is refused explicitly`. Directly proves the "excess arguments"
   negative_acceptance case.

**No regression:** FTF-0404's own no-DB suite re-run clean after this
change.

**Full regression, this sandbox:** `uv run ruff check`/`uv run mypy`
clean on both changed source files; every existing
`tests/tasks/test_ftf_*.py` file re-run (27 files) — all pass except
FTF-0002/FTF-0003/FTF-H001's own frontend-tooling-dependent checks
(`pnpm run lint`/`typecheck`, `vitest run`, `check_test_harness.py`),
independently reproduced as failing identically on `main` *before* this
PR (`git stash` back to the pre-PR tree, same three files fail the same
way) — a pre-existing sandbox artifact (no frontend `node_modules` build
performed in this sandbox), not a regression this PR introduces. `python3
scripts/validate_spec.py --strict` clean once unshallowed (`git fetch
--unshallow origin` first — the same pre-existing shallow-clone artifact
every prior certifying/relay session has already documented).

**`product_truth_check.py --fail-on-unwired`:** `7/7` before this
certification (matching the PR's own pre-certification claim), `8/8`
after — `validate_json_schema_document` (this task's own
`proving_symbol`) has a real, source-inspection-confirmed live caller, so
it is correctly newly counted once `docs/spec-matrix.yaml` is set to
`CERTIFIED` here; a genuine evidence-backed increase, not inflation
(Law 2).

**Task contract re-pulled directly:** `python tools/r1_handoff.py packet
FTF-0405 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1
("Validate JSON Schema using pinned dialect and schema references under
safe limits") confirmed met: `PINNED_DIALECT` is checked against
`$schema` when present, and `MAX_SCHEMA_DEPTH`/`MAX_SCHEMA_NODES`/
`MAX_REF_HOPS` are real, enforced, tested bounds. `negative_acceptance`
("Recursive/reference bombs, remote schema SSRF, invalid required/type
constraints and excess arguments fail explicitly") confirmed met against
all four named cases: reference cycles (`_detect_ref_cycles`, real DFS),
remote schema SSRF (rejected by construction, independently reproduced
above), invalid required/type constraints (`ALLOWED_TYPES` closed
vocabulary plus `required`-vs-`properties` consistency checking), excess
arguments (`additionalProperties: false` enforcement, independently
reproduced above). `database_changes: "No independent schema change"`
confirmed — no migration files in the diff. Both dependencies (FTF-0401,
FTF-0404) confirmed genuinely `CERTIFIED` in `docs/spec-matrix.yaml`
before relying on them.

**No blocking defect found.**

**Action taken:** merged PR #148 (merge commit
`82e2851be2c02b8e9257713404d11f66147efcb0`). Set
`docs/spec-matrix.yaml` FTF-0405 to `certification_state: CERTIFIED` /
`proof_state: L2-CERTIFIED` (`evidence_hash` set to the merge commit).
Added FTF-0405's own entry to `spec/verification-map.json`. Ran
`sync_certified_status()`. `scripts/status.py` confirmed non-inflated
(`30/300` certified · `7` product-true before, reproduced directly via
`git stash`; `31/300` · `8` after, matching `product_truth_check.py`'s
own `8/8`). Full ledger entry appended to `docs/certifications/ledger.md`.
`QUEUE.md`/`PROGRESS.md` updated. This certification's own doc updates
are pushed on a branch and opened as a PR against `main`, not pushed
directly, per `BRANCH_PROTECTION` in `CLAUDE.md`.

**Conclusion:** Certified L2-CERTIFIED.

**Process note, flagged not blocking:** PR #148 skipped this repo's own
established two-commit tracking-doc precedent (no `QUEUE.md`/
`PROGRESS.md`/`docs/certifications/queue.md` update alongside the
implementation commit). This did not affect the substance of the
review — `docs/spec-matrix.yaml` and the PR body itself carried the full
PROVISIONAL evidence — but the next relay/producer shift should restore
that precedent rather than let tracking docs drift further out of sync
with `docs/spec-matrix.yaml`.

## 2026-09-20 — FTF-0406 — Side-effect classification
- **PR:** https://github.com/SJZLab/Sovra/pull/153
  (`claude/wizardly-fermat-coeg4t` → `main`, title
  `[relay] FTF-0406: Side-effect classification`).
- **Commit:** `6723131` (implementation); `docs/spec-matrix.yaml`/
  `PROGRESS.md`/`QUEUE.md`/this entry pushed as a second,
  tracking-doc-only commit on the same branch — restoring the
  FTF-0201/FTF-0301/FTF-0401/FTF-0402/FTF-0305/FTF-0404 two-commit
  precedent that PR #148 (FTF-0405) skipped (flagged, not silently
  repeated, in that PR's own certifier review directly above).
- **Ground truth checked before picking work:** PR #152 (open, unmerged,
  doc-only) claims FTF-0406 is the lowest-CAP dependency-ready item; PR
  #143 (FTF-0202 backend-selection claim) remains open, unmerged, and
  unverified — neither was merged or trusted at face value. Independently
  recomputed the dependency-ready frontier from scratch by parsing
  `docs/TASK_INDEX.md`'s own `deps`/CAP columns against
  `docs/spec-matrix.yaml`'s live `certification_state`: FTF-0401/
  FTF-0404/FTF-0405 are all genuinely `CERTIFIED`, and FTF-0406 (CAP-05)
  and FTF-1501 (CAP-14) are the only two dependency-ready items —
  independently arriving at the same answer PR #152 claims, not merely
  trusting it. FTF-0406 has the lower CAP number and no external-service
  ask of its own.
- **Task contract:** `python tools/r1_handoff.py packet FTF-0406 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1 ("Map real
  tool operations to read/write/financial/privileged risk with reviewed
  enforcement proof"), negative_acceptance ("Unclassified or unresolved
  sensitive tool cannot default to read-only; user mandate text cannot
  disable actual guard"), `database_changes: "No independent schema
  change. Consume frozen contract"`, `files: [apps/api/app/services/
  side_effect_classification.py, tests/tasks/test_ftf_0406.py]`,
  `dependencies: [FTF-0401, FTF-0404, FTF-0405]` (all CERTIFIED),
  `contract_owner: FTF-0401`, `shared_lock_required: []`.
- **The real gap this task closes (disclosed, not assumed):**
  FTF-0404's own `apps/api/app/tools/tool_definition_schema.py` already
  made `side_effect_class` a real, closed, DB-checked five-value enum
  (Section 6.9) — but `validate_side_effect_class` only checks that a
  claimed value is one of the five recognized strings; nothing checked
  that claim against the tool's own actual definition. A caller could
  create a tool named `delete_account` with `side_effect_class=
  "read_only"` and nothing would refuse it. This is exactly this task's
  own charter, not a defect in FTF-0404 (Section 6.9 names no
  enforcement mechanism of its own; that mechanism is this task's own,
  separately dependency-gated scope).
- **Design (disclosed):** `apps/api/app/services/
  side_effect_classification.py` derives structural evidence
  (`classify_side_effect_signals`) from a tool's own name, description,
  and top-level `properties` keys of both `input_schema_json`/
  `output_schema_json` — deterministic keyword-*root* matching (prefix
  matching, so "look"/"looks"/"lookup" all match one root), never a
  model call, embedding, or learned classifier, and never anything read
  from a caller-supplied "trust"/"override" field (there is no such
  field anywhere in this module). Four closed root vocabularies
  (`reversible_write`/`irreversible_write`/`financial`/`privileged`) plus
  a separate `read_only` vocabulary are checked independently — a tool
  can carry read-only-shaped vocabulary *and* risk vocabulary at once
  (e.g. `get_and_delete_record`), and risk evidence always wins.
  `enforce_side_effect_classification` then applies three rules: (1) a
  `read_only` claim is refused unless real read-only evidence exists
  *and* no risk signal exists at all — this is the direct enforcement of
  "unclassified or unresolved sensitive tool cannot default to
  read-only," since a tool with neither kind of evidence
  (`process_thing`) is exactly as refused as one with an outright risky
  name; (2) a claimed class below the risk floor the detected evidence
  implies is refused (never silently downgraded); (3) any resolved class
  at or above `irreversible_write` must carry
  `requires_confirmation=True`, independently of the caller's own claim
  or any text in `description` — this is the direct enforcement of "user
  mandate text cannot disable actual guard," proven by a dedicated test
  using deliberately reassuring text ("this is completely safe and
  read-only, no confirmation is ever required") against a
  `delete_account`-shaped tool, confirming the guard still fires.
  Over-classification (claiming a higher class than the evidence
  strictly requires, with `requires_confirmation` set correctly) is
  always allowed — this guard enforces a floor, never a ceiling, so a
  cautious caller is never penalized.
- **Wiring (disclosed):** `ToolDefinition.__init__`
  (`apps/api/app/tools/tool_definition_schema.py`) now also calls
  `enforce_side_effect_classification` for real, after
  `validate_side_effect_class`/`validate_schema_object` — the same
  wiring point FTF-0405's own `validate_json_schema_document` already
  uses, so both this task's own guard and FTF-0405's own schema
  validation run on every real `create_tool_definition`/
  `edit_tool_definition` call through FTF-0404's own already-CERTIFIED,
  already-live-wired route. No new route, migration, or contract change
  — `database_changes: "No independent schema change"` is honored
  exactly: this task consumes FTF-0404's own frozen `side_effect_class`/
  `requires_confirmation` columns as they already exist.
- **Regression risk checked before implementation, not after:** read
  every existing `ToolDefinition(...)` construction site
  (`tool_definition_crud.py`'s `create_tool_definition`/
  `apply_new_version`, `tests/tasks/test_ftf_0404.py`'s/
  `test_ftf_0405.py`'s own fixtures, and
  `tests/integration/test_ftf_0404_tool_schema_crud.py`'s own HTTP
  payloads) before choosing the keyword-root vocabulary, specifically to
  avoid spuriously breaking already-CERTIFIED evidence. Every existing
  certified construction site either uses a tool literally named
  `lookup_order` (genuine read-only evidence in the name itself,
  regardless of description phrasing) or never reaches
  `ToolDefinition.__init__` at all (role/project-ownership checks in
  `create_tool_definition`/the route layer deny the request first, for
  the `should_be_denied`/`cross_org_project` integration-test cases).
  Independently re-ran `tests/tasks/test_ftf_0401.py`/`test_ftf_0404.py`/
  `test_ftf_0405.py` after implementation — all three "all checks
  passed," confirming this design choice held in practice, not only in
  the pre-implementation read-through.
- **Test:** `tests/tasks/test_ftf_0406.py` (no-DB, 27 test functions):
  `classify_side_effect_signals` detects each of the four risk
  categories from representative vocabulary and marks a genuinely
  ambiguous tool unresolved; `enforce_side_effect_classification` proves
  acceptance of a genuine read-only tool, refusal of the unclassified and
  the unresolved-sensitive-at-read_only cases, refusal of under-claiming
  a detected risk, the `requires_confirmation` guard at and above
  `irreversible_write`, that over-classification is allowed, the
  reassuring-description negative case, and refusal of an unrecognized
  claimed class; three further tests construct a real `ToolDefinition`
  proving the guard runs before any session or database is touched and
  that a correctly classified/confirmed tool still constructs
  successfully.
- **Evidence — own fault injection (Law 1, two, distinct vectors, both
  reverted byte-identical, both reconfirmed clean afterward):**
  1. Disabled the read_only/unresolved guard
     (`if False and claimed_class == "read_only" and
     signals.is_unresolved:`) — caught for real:
     `FAIL [FTF-0406] negative_acceptance: an unclassified tool (no
     read-only, no risk evidence) cannot default to read_only`. Directly
     proves the "unclassified...cannot default to read-only" half of
     this task's own negative_acceptance is enforced by real code, not
     merely asserted.
  2. Disabled the `requires_confirmation` guard the same way — caught
     for real by all three checks that name it:
     `constructing an irreversible_write tool without
     requires_confirmation=True fails`,
     `negative_acceptance: the same reassuring text cannot disable the
     requires_confirmation guard either`, and
     `negative_acceptance: irreversible_write claimed without
     requires_confirmation=True is refused`. Directly proves the "user
     mandate text cannot disable actual guard" half.
  Both reverted by re-applying the exact original source
  (`grep -n "False and"` confirmed no match remained afterward), both
  suites re-run clean.
- **No regression:** re-ran `tests/tasks/test_ftf_0401.py`/
  `test_ftf_0404.py`/`test_ftf_0405.py` after this change — all three
  still "all checks passed."
- **Full regression, this sandbox:** installed API dependencies into an
  isolated `uv venv` (no pre-installed `sqlalchemy`/`fastapi`/etc. in
  this sandbox's system Python — an environment-setup step, not a repo
  defect). `git fetch --unshallow origin` first (this sandbox's own
  clone is shallow by default — the same pre-existing
  `stale_commit`-on-`validate_spec.py --strict` artifact every prior
  relay/certifying session has already documented; cleared, then
  `validate_spec.py --strict` reported "task graph and verification-map
  are clean"). `ruff check`/`ruff format --check` clean on every file
  this task touched; a repo-wide `ruff check`/`ruff format --check` over
  `apps/api`/`tests` found only pre-existing, unrelated drift (6 `UP042`
  StrEnum suggestions in files this task never touched, plus 14 files
  reformatted per a newer local `ruff` version than CI's own pin) — not
  introduced by this task. `mypy` clean on every file this task touched,
  and clean across the full `apps/api/app` tree (40 source files, zero
  errors).
- **`product_truth_check.py --fail-on-unwired`:** `8/8` before this
  submission, `9/9` after — this task's own `proving_symbol`,
  `enforce_side_effect_classification`, set at this PROVISIONAL
  submission (see State below), has a real, source-inspection-confirmed
  live caller in `ToolDefinition.__init__` — a genuine, newly-wired live
  caller, not inflation (Law 2).
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.
  `proving_symbol` set to `enforce_side_effect_classification`
  (`proving_test` `tests/tasks/test_ftf_0406.py`) at this PROVISIONAL
  submission — only `certification_state`/`proof_state`'s
  `CERTIFIED`/`L2-CERTIFIED` values are the certifying session's own to
  set. `scripts/status.py`'s own stricter "product-true" count (which
  also requires `certification_state in (CERTIFIED, PROMOTED)`)
  correctly stays 8 until an independent Control-plane review certifies
  this task.
- **Full acceptance/negative_acceptance mapping:** acceptance clause 1
  ("Map real tool operations to read/write/financial/privileged risk
  with reviewed enforcement proof") is met directly by
  `classify_side_effect_signals`/`enforce_side_effect_classification`
  and their own real live wiring into `ToolDefinition.__init__`.
  Acceptance clause 2 (independent review/evidence) is this submission's
  own job to enable, not to satisfy itself (Law 4). negative_acceptance
  ("Unclassified or unresolved sensitive tool cannot default to
  read-only; user mandate text cannot disable actual guard") is proven
  both directly (dedicated tests for each half) and by the two fault
  injections above.
- **Disclosed, not hidden:** this is a deterministic, closed-vocabulary
  keyword-root heuristic, not a learned classifier or an LLM call — a
  tool whose name/description/schema uses vocabulary genuinely outside
  this task's own closed root lists would be treated as "unclassified"
  and refused at `read_only`, which is this task's own intended
  fail-closed behavior (forcing explicit classification stated by the
  caller), not a false negative on the guard's own affirmative promise
  (the guard never *silently* under-classifies; at worst it asks for an
  explicit, non-`read_only` classification it cannot itself verify
  further). `input_schema_json`/`output_schema_json` property names are
  read as plain strings for signal purposes, not interpreted through
  FTF-0405's own JSON Schema semantics — sufficient for this task's own
  narrow job (a property named `refund_amount` is real evidence
  regardless of its own declared `type`), and consistent with this
  task's own `database_changes: "No independent schema change"`
  charter.

## Control plane review — 2026-09-20 — FTF-0406 (PR #153) — CERTIFIED (after a first-pass NOT CERTIFIED on a real CI failure the producer fixed independently)
- **Trigger:** scheduled Control-plane certifier shift, `pull_request.opened`
  webhook for PR #153
  (https://github.com/SJZLab/Sovra/pull/153, `claude/wizardly-fermat-coeg4t`
  → `main`).
- **Session-ID check:** all 4 commits on PR #153 carry `Claude-Session:
  .../session_01WFunvdUFkEnkkpFRCVm2ga`; this reviewing session's own ID
  is `session_01GtkZi1xVFgaMfPbXjiPPz4` — no match, independent per Law 4.
- **First pass — real CI failure, filed NOT CERTIFIED:** CI checked on
  the real head, not a stale/in-progress snapshot (subscribed to the
  PR's own activity and waited for the real `check_run.completed`
  event): the `gate` job's own `uv run mypy .` step genuinely failed on
  head `2885a40` — 3 errors at `tests/tasks/test_ftf_0406.py:110`
  (`_enforce`'s own `**dict[str, object]` unpack could not be verified by
  mypy against `enforce_side_effect_classification`'s heterogeneous
  keyword-only parameter types: `name: str`, `description: str | None`,
  `requires_confirmation: bool`). Confirmed from the real CI job log
  (`get_job_logs`, not a self-report) and reproduced identically in this
  session's own sandbox, byte-for-byte matching the CI error text and
  line. Filed as its own PR (#154) with the exact fix needed, since the
  underlying production logic drew no mypy errors — a test-helper typing
  gap, not a logic defect.
- **Superseded before PR #154's own CI finished:** while that review was
  being written, the same producer session (same session ID) independently
  found and fixed the identical gap on PR #153's own branch — commit
  `e61bd45`, pushed at 23:11:36Z (before this review's own PR #154 was
  even opened at 23:16:42Z). Retyped `_enforce` to explicit,
  individually-typed optional keyword parameters instead of the generic
  `**overrides: object` unpack — no behavior change, every call site
  already passed only recognized keyword names (confirmed by reading the
  fix commit's own diff directly: `+17/-11`, `tests/tasks/test_ftf_0406.py`
  only). Closed PR #154 without merging — merging a "NOT CERTIFIED,
  action needed" doc state immediately alongside an already-fixed,
  already-green reality would misrepresent current status, and it would
  conflict with PR #153's own tracking-doc commit on the same
  `QUEUE.md`/`PROGRESS.md`/`docs/certifications/queue.md` sections — and
  re-reviewed the fixed head from scratch instead, folding the full story
  (including the NOT CERTIFIED finding) into this entry and the ledger.
- **CI reconfirmed on the real final head:** GitHub Actions run
  `35543916766` — all 16 check runs `completed`/`success`. Pulled the
  real `gate` job log directly (job `106166360499`, not a self-report):
  `uv run ruff check .` → "All checks passed!"; `uv run ruff format
  --check .` → "122 files already formatted"; `uv run mypy .` → "Success:
  no issues found in 122 source files" (the prior 3 errors gone, zero
  remaining — note this differs from this session's own sandbox
  reproduction, which still shows 7 pre-existing `alembic` "no attribute
  op/context" errors in migration files this PR does not touch; CI's own
  real environment does not report them at all, a sandbox/CI environment
  difference, not a real gate failure — CI's own conclusion is
  authoritative); `product_truth_check.py --fail-on-unwired` → "9/9
  product-true"; `scripts/status.py` → "31/300 certified · 8 product-true"
  (pre-certification baseline, as expected — this task was still
  PROVISIONAL at that point in the run).
- **Diff of the fix commit read directly** (`get_commit` with
  `full_patch`): touches only `tests/tasks/test_ftf_0406.py`'s own
  `_enforce` helper (17 insertions/11 deletions) —
  `apps/api/app/services/side_effect_classification.py` and
  `apps/api/app/tools/tool_definition_schema.py` (the shipped production
  logic and its live wiring) are byte-identical to the first head; no new
  `# type: ignore`, no `Any` widening, no behavior change to any call
  site (every existing call already passed only the six recognized
  keyword names, now explicit parameters instead of a dict unpack).
- **Own reproduction, this sandbox:** `git worktree add` on the real PR
  head `e61bd45`, `uv sync --all-packages`. `uv run mypy .` — 0 errors at
  `tests/tasks/test_ftf_0406.py` (the fix holds); 7 pre-existing
  `alembic` errors remain in files this PR does not touch, not counted
  against it (see above). `python3 scripts/product_truth_check.py
  --fail-on-unwired` → `9/9`, reconfirmed.
- **Own fault injection, distinct from the producer's own two** (which
  targeted the `read_only`/unresolved guard and the
  `requires_confirmation` guard specifically): disabled the risk-floor
  check itself in `enforce_side_effect_classification` —
  `if False and claimed_rank < signals.minimum_required_rank:` — caught
  for real: 4 checks failed (`constructing a ToolDefinition with a risky
  name claimed as read_only fails at construction time...`,
  `negative_acceptance: reassuring free text in description cannot
  override a real risk signal...`, `a financial-risk tool claimed only as
  reversible_write (below its own detected floor) is refused`,
  `negative_acceptance: a tool with real risk evidence in its own name
  cannot be claimed as read_only`). Directly proves the risk-floor
  enforcement itself — the core of acceptance clause 1 — is real code,
  not merely asserted. Reverted byte-identical (`git diff --stat` empty
  for `side_effect_classification.py`); `tests/tasks/test_ftf_0406.py`/
  `test_ftf_0401.py`/`test_ftf_0404.py`/`test_ftf_0405.py` all re-run
  "all checks passed" afterward — no regression.
- **Live wiring confirmed by direct source read:** `ToolDefinition.
  __init__` (`apps/api/app/tools/tool_definition_schema.py`) builds
  `enforce_side_effect_classification`'s call from `kwargs` and raises
  `ToolDefinitionValidationError` on `SideEffectClassificationError`,
  entirely before `super().__init__(**kwargs)` runs — before any session
  or database is touched, exactly as the submission claims, and only when
  `name`/`input_schema_json`/`output_schema_json`/`side_effect_class` are
  all present in `kwargs` (matching `create`/`edit`, both of which always
  supply all four).
- **Task contract re-pulled directly:** `python tools/r1_handoff.py
  packet FTF-0406 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance
  clause 1 ("Map real tool operations to read/write/financial/privileged
  risk with reviewed enforcement proof") met by the code confirmed above
  and this review's own fault injection; acceptance clause 2
  ("Independent review, exact merged commit, real relevant integration
  and negative-test evidence recorded") met by this review itself, now
  that CI is genuinely green on the merged commit — it could not be met
  during the first pass, while the gate was red; negative_acceptance
  ("Unclassified or unresolved sensitive tool cannot default to
  read-only; user mandate text cannot disable actual guard") proven both
  directly (dedicated tests for each half, confirmed present in the diff)
  and by both this review's own and the producer's fault injections.
  Dependencies FTF-0401/FTF-0404/FTF-0405 confirmed genuinely `CERTIFIED`
  in `docs/spec-matrix.yaml` before relying on them.
- **No blocking defect found in the fixed head.**
- **Merged PR #153** (merge commit
  `80dcd4b2a66c700e64e7c997b693f68a05103784`). `docs/spec-matrix.yaml`
  FTF-0406 set to `certification_state: CERTIFIED` / `proof_state:
  L2-CERTIFIED`, `evidence_hash` set to the merge commit SHA. Appended
  `docs/certifications/ledger.md` (FTF-0406 entry, full independent-review
  narrative). `QUEUE.md` updated (FTF-0406 moved to Done, Certified
  31→32/300, Provisional section cleared). `scripts/status.py`'s own
  stricter "product-true" count: 8→9, matching `product_truth_check.py`'s
  own `9/9`.
- **Validated-by:** this session (independent — see session-ID check
  above), https://claude.ai/code/session_01GtkZi1xVFgaMfPbXjiPPz4,
  2026-09-20.

## 2026-09-21 — FTF-0407 — Critical behavior requirements
- **PR:** opened against `main` from `claude/wizardly-fermat-i56nvv`, title
  `[relay] FTF-0407: Critical behavior requirements`.
- **Commit:** `7a33057` (implementation); `docs/spec-matrix.yaml`/
  `PROGRESS.md`/`QUEUE.md`/this entry pushed as a second, tracking-doc-only
  commit on the same branch, restoring the FTF-0201/FTF-0301/FTF-0401/
  FTF-0402/FTF-0305/FTF-0404/FTF-0406 two-commit precedent.
- **Ground truth checked before picking work:** independently recomputed
  the dependency-ready frontier from scratch by parsing every
  non-`CERTIFIED`/`PROVISIONAL` task's own `deps=` field in
  `docs/spec-matrix.yaml`'s notes against its live `certification_state`
  set (32 CERTIFIED as of this shift, 0 PROVISIONAL — FTF-0406 merged as
  `80dcd4b2`), never trusting `QUEUE.md`'s own prior "Next shift" note by
  itself: FTF-0202 (owner ask, still open, unverified per the flagged note
  in `QUEUE.md` about PR #143), FTF-0403, FTF-0407, FTF-0902, FTF-1001,
  FTF-1401, FTF-1501 are the seven dependency-ready items. FTF-0407
  (CAP-05) has the lowest CAP number with no external-service ask of its
  own — matching `QUEUE.md`'s own prediction that FTF-0406 reaching
  CERTIFIED would newly unblock it.
- **Task contract:** `python tools/r1_handoff.py packet FTF-0407 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance ("Each critical
  responsibility/forbidden action links to deterministic outcome tests
  and runtime enforcement"; "Independent review, exact merged commit,
  real relevant integration and negative-test evidence recorded"),
  negative_acceptance ("Unmeasurable or conflicting critical mandate
  blocks activation; no model-generated self-certification"),
  `database_changes: "No independent schema change. Consume frozen
  contract"`, `api_contract: "/api/v1/projects; /requirements; /tools;
  project privacy and budget policy"`, `files: [apps/web/components/
  project-mandate/critical_behavior_requirements.tsx, apps/web/tests/
  ftf_0407.spec.ts]` (the packet's own single abbreviated suggestion —
  refined below, the same FTF-0404/FTF-0406 precedent for a ticket whose
  packet names one file but whose real charter is a backend enforcement
  gap plus its own new API surface), `dependencies: [FTF-0401, FTF-0406]`
  (both CERTIFIED), `contract_owner: FTF-0401`, `shared_lock_required: []`.
- **The real gap this task closes (disclosed, not assumed):** Section
  7A.6's own mandate fields ("critical behaviors", "forbidden
  responsibilities") and Section 6.8D's `AgentManifest.mandate_json`/
  `forbidden_actions_json` do not exist as real, queryable storage
  anywhere in this repository yet — `AgentManifest` is FTF-04A19's own,
  later, separately dependency-gated schema, and `docs/TASK_INDEX.md`
  records that task as depending on *this* one (FTF-0407), not the other
  way around. This task's own `database_changes` charter rules out
  inventing a new mandate table to fill that gap early. The one real,
  already-frozen structural analog that exists today is FTF-0404's own
  `ToolDefinition.is_critical` field, checked for the first time in this
  task against `side_effect_class`/`requires_confirmation`: a purely
  `read_only` tool could be created `is_critical=True` (nothing for that
  claim to bind a deterministic outcome test or runtime enforcement
  mechanism to), and a `reversible_write` tool could be created
  `is_critical=True` with `requires_confirmation=False` (a "critical"
  claim with zero runtime guard behind it, since FTF-0406's own
  confirmation floor only starts at `irreversible_write`). This is exactly
  this task's own charter, not a defect in FTF-0404/FTF-0406 (neither
  names an `is_critical` cross-check as its own scope).
- **Design (disclosed):** `apps/api/app/services/
  critical_behavior_requirements.py`'s `enforce_critical_behavior_requirement`
  is a no-op whenever `is_critical=False` — this module never requires a
  tool to be marked critical, only refuses an unmeasurable or unenforced
  claim that it is. Two rules, both matching this task's own
  negative_acceptance literally: (1) **unmeasurable** — `is_critical=True`
  on a `read_only`-resolved tool is refused, since a read-only operation
  has no enforced side effect for a test/enforcement mechanism to bind to
  (a `read_only` resolution is itself only ever reached once FTF-0406's
  own guard already found real read-only evidence and no risk signal at
  all, so this can never be a false refusal of a genuinely dangerous
  tool); (2) **conflicting** — `is_critical=True` on a tool with a real
  side effect but `requires_confirmation=False` is refused, since the
  critical claim conflicts with its own complete lack of runtime
  enforcement (this only adds real bite at `reversible_write`, the one
  rank FTF-0406's own confirmation floor does not itself reach).
  Deliberately **not** enforced the other way: a tool resolved at or
  above `irreversible_write` with `is_critical=False` is left alone —
  `is_critical` is a distinct, user-authored "critical business
  responsibility" flag (Section 22 Screen D), not a mechanical
  restatement of the risk floor, and forcing it would regress FTF-0406's
  own already-CERTIFIED evidence (see Regression risk below).
- **Wiring (disclosed):** `ToolDefinition.__init__`
  (`apps/api/app/tools/tool_definition_schema.py`) now also calls
  `enforce_critical_behavior_requirement` for real, after
  `side_effect_class` has already been resolved by
  `enforce_side_effect_classification` — the same wiring point
  FTF-0402/FTF-0405/FTF-0406 already use, so this task's own guard runs
  on every real `create_tool_definition`/`edit_tool_definition` call
  through FTF-0404's own already-CERTIFIED, already-live-wired route.
  Also adds a genuinely new, real, read-only route — this task's own
  `api_contract` names `/requirements` as new surface FTF-0404's own
  contract deliberately left undeclared (see
  `packages/contracts/openapi/04-interface.yaml`'s own module docstring,
  updated by this task): `apps/api/app/tools/
  critical_behavior_requirement_routes.py`'s
  `build_critical_behavior_requirement_router` serves
  `GET /api/v1/organizations/{organization_id}/projects/{project_id}/requirements`,
  a derived projection over already-enforced `ToolDefinition` rows (never
  a separately stored claim), wired into `apps/api/app/main.py` — the
  sixth live-wired product-runtime path in this repository. Role gating
  matches `tool_definition_routes.py`'s own `TOOL_READ_ROLES` exactly (a
  read view over the same underlying resource). `packages/contracts/
  {openapi/04-interface.yaml,jsonschema/04-domain.schema.json}` extended
  under the existing `contracts:04` lock (owned by FTF-0401) with the new
  path and a `CriticalBehaviorRequirement` schema. `database_changes`
  honored exactly — no migration, consumes FTF-0404's own frozen
  `is_critical`/`side_effect_class`/`requires_confirmation` columns as
  they already exist.
- **Regression risk checked before implementation, not after:** read
  every existing `ToolDefinition(...)` construction site
  (`tool_definition_crud.py`'s `create_tool_definition`/
  `apply_new_version`, `tests/tasks/test_ftf_0404.py`'s/
  `test_ftf_0405.py`'s/`test_ftf_0406.py`'s own fixtures) before choosing
  the enforcement direction, specifically because
  `test_ftf_0406.py::test_construction_accepts_a_correctly_confirmed_high_risk_tool`
  constructs an `irreversible_write`, confirmed, `is_critical=False` tool
  — an initial design that also refused a high-risk tool left
  `is_critical=False` would have regressed that already-CERTIFIED
  evidence (Law 14). Confirmed no such combination exists as
  `is_critical=True` with `requires_confirmation=False` at rank ≥1
  anywhere in the existing certified fixtures before finalizing the rule.
  Independently re-ran `tests/tasks/test_ftf_0401.py`/`test_ftf_0404.py`/
  `test_ftf_0405.py`/`test_ftf_0406.py` after implementation — all four
  "all checks passed," confirming this design choice held in practice.
- **Test:** `tests/tasks/test_ftf_0407.py` (no-DB, 22 test functions):
  `enforce_critical_behavior_requirement` is a no-op whenever
  `is_critical=False` across every `side_effect_class`/
  `requires_confirmation` combination tried; refuses the unmeasurable
  read_only-critical claim (including when `requires_confirmation` is
  meaninglessly set `True`); refuses the conflicting unenforced-critical
  claim at `reversible_write` and above; accepts a genuinely enforced
  critical claim at every real-effect class; refuses an unknown
  `side_effect_class`; explicitly proves no regression to FTF-0406's own
  certified high-risk/non-critical/confirmed fixture shape; real
  `ToolDefinition` construction tests prove the guard runs before any
  session or database is touched, that a critical/real-effect/confirmed
  tool constructs successfully, and that both the FTF-0406 and the plain
  FTF-0404/FTF-0405 default fixture shapes still construct (no
  regression); two tests prove `derive_critical_behavior_requirement_view`'s
  own `measurable`/`runtime_enforced` projection for a `read_only` row and
  a real-effect row.
- **Integration test:** `tests/integration/
  test_ftf_0407_critical_behavior_requirements.py` (real Postgres 16, one
  real organization/workspace/project, a real uvicorn server serving both
  `build_tool_definition_router` and this task's own
  `build_critical_behavior_requirement_router`, 15 real checks): the
  unmeasurable (`read_only`+`is_critical=True`) and conflicting
  (`reversible_write`+`is_critical=True`+`requires_confirmation=False`)
  cases are both refused (400) over a real HTTP create; a genuinely
  enforced critical tool (`reversible_write`+`is_critical=True`+
  `requires_confirmation=True`) is accepted (201); a plain read_only/
  non-critical tool and a high-risk/confirmed/non-critical tool
  (FTF-0404's/FTF-0406's own certified shapes) both still create (201) —
  no regression; the real `/requirements` route lists exactly the three
  real tools created, each correctly annotated (`measurable`/
  `runtime_enforced`); an unauthenticated `/requirements` request is
  denied (401).
- **Evidence — own fault injection (Law 1, two, distinct vectors, both
  reverted byte-identical, both suites reconfirmed clean afterward,
  including over real Postgres):**
  1. Disabled the live wiring in `ToolDefinition.__init__`
     (`if False and "is_critical" in kwargs:`) — caught for real by the
     two named live-wiring tests in the no-DB suite
     (`test_construction_refuses_unmeasurable_critical_read_only_tool_before_any_db`,
     `test_construction_refuses_conflicting_unenforced_critical_tool`) and
     by three checks in the live-Postgres suite, including a real
     downstream unique-constraint collision — the wrongly admitted
     unmeasurable tool occupied the `update_order_status` name slot the
     next real create needed, so `IntegrityError` surfaced independently
     of the intended `check()` failures.
  2. Disabled the `requires_confirmation` conflict check inside
     `enforce_critical_behavior_requirement` itself
     (`if False and not requires_confirmation:`) — caught for real by the
     exact named negative_acceptance checks in both suites
     (`test_enforce_refuses_conflicting_unenforced_critical_claim` in the
     no-DB suite; the "conflicting, unenforced claim" check in the
     live-Postgres suite).
  Both reverted by restoring the exact original source (`git diff --stat`
  confirmed empty for the touched files afterward), both suites re-run
  clean each time.
- **No regression:** re-ran `tests/tasks/test_ftf_0401.py`/
  `test_ftf_0404.py`/`test_ftf_0405.py`/`test_ftf_0406.py` after this
  change — all four still "all checks passed." Re-ran
  `tests/integration/test_ftf_0404_tool_schema_crud.py` against a freshly
  recreated local PostgreSQL 16 database/role — still "all checks
  passed."
- **Full regression, this sandbox:** `uv sync --all-packages` (no
  pre-installed `sqlalchemy`/`fastapi`/etc. in this sandbox's system
  Python — an environment-setup step, not a repo defect). `ruff check
  --fix .`/`ruff format .` needed one real fixup this task introduced
  (import-order in `tool_definition_schema.py`'s own new import, and
  reformatting this task's own new `critical_behavior_requirements.py`)
  — both applied and reconfirmed clean afterward; `ruff check .`/`ruff
  format --check .` clean across the full `apps/api` tree (49 files).
  `mypy .` clean on every file this task touched; the 2 remaining errors
  repo-wide (`authlib.jose` missing stubs in `oidc_login.py`) are
  confirmed pre-existing and identical on `main` (`git stash`-verified:
  same 2 errors, same file, before this task's own commit). `git fetch
  --unshallow origin` first (this sandbox's own clone is shallow by
  default, the same pre-existing artifact every prior relay/certifying
  session has already documented); `validate_spec.py --strict` →
  "task graph and verification-map are clean".
  `product_truth_check.py --fail-on-unwired` — `9/9` before this task's
  own `docs/spec-matrix.yaml` update, `10/10` after (this task's own
  `proving_symbol`, `enforce_critical_behavior_requirement`, has a real,
  source-inspection-confirmed live caller in `ToolDefinition.__init__` —
  not a TRACKER). `scripts/status.py` → "32/300 certified · 9
  product-true" (correctly still 9, not 10 — a PROVISIONAL task does not
  count toward the stricter CERTIFIED-only metric until an independent
  Control-plane review certifies it, matching the FTF-0406 submission's
  own precedent exactly).
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.
  `proving_symbol` set to `enforce_critical_behavior_requirement`
  (`proving_test` `tests/tasks/test_ftf_0407.py`) at this PROVISIONAL
  submission — only `certification_state`/`proof_state`'s
  `CERTIFIED`/`L2-CERTIFIED` values are the certifying session's own to
  set. Certified stays 32/300.
- **Full acceptance/negative_acceptance mapping:** acceptance clause 1
  ("Each critical responsibility ... links to deterministic outcome tests
  and runtime enforcement") is met, for the "critical responsibility"
  half, directly by `enforce_critical_behavior_requirement` and its own
  real live wiring into `ToolDefinition.__init__`, plus the real
  `/requirements` view surfacing that enforced state. The "forbidden
  action" half is disclosed, not hidden, as FTF-04A19/FTF-04A22's own
  later scope once `AgentManifest` is real (see "the real gap this task
  closes" above). Acceptance clause 2 (independent review/evidence) is
  this submission's own job to enable, not to satisfy itself (Law 4).
  negative_acceptance ("Unmeasurable or conflicting critical mandate
  blocks activation; no model-generated self-certification") is proven
  both directly (dedicated tests for each half, in both suites) and by
  the two fault injections above. "No model-generated self-certification"
  is met by construction — there is no LLM call, embedding, or
  model-scored signal anywhere in this module; the only inputs are the
  caller's own `is_critical` claim and the already-structurally-derived
  `side_effect_class`/`requires_confirmation`.
- **Disclosed, not hidden:** this task's own `files` suggestion named a
  single frontend component (`apps/web/components/project-mandate/
  critical_behavior_requirements.tsx`) — refined into a real backend
  enforcement module plus its own new API route instead, the same
  FTF-0404/FTF-0406 precedent for a packet whose abbreviated `files`
  suggestion is this repo's own standard shorthand, not a literal
  constraint (that packet's own text explicitly warns against scattering
  domain logic into one file per ticket). No frontend UI component is
  built by this task — Section 22 Screen D's full mandate-authoring
  experience and `FTF-0408`'s own, later, separately dependency-gated
  "Requirements UI" task assemble this view alongside project privacy/
  budget policy into one screen; this task ships the real enforcement and
  the real API surface that screen will read from, not the screen itself.

## 2026-09-21 — FTF-0407 — record the opened PR link
- **PR:** https://github.com/SJZLab/Sovra/pull/156
  (`claude/wizardly-fermat-i56nvv` → `main`).

## Control plane review — 2026-09-21 — FTF-0407 (PR #156) — CERTIFIED
- **Trigger:** `pull_request.opened` webhook for PR #156.
- **Session-ID check:** all 4 commits on PR #156 carry `Claude-Session:
  .../session_01QNESA4CVHysioQybmQCNPB`; this reviewing session's own ID
  is `session_0185UQFPYhDkeasgkhTHrE5K` — no match, independent per Law 4.
- **CI checked on the real head, not a stale claim:** subscribed to the
  PR's own activity immediately (16 checks were `in_progress` when the PR
  had just opened). The producer session independently pushed a real fix
  (`00641ac`) for a genuine `ruff` import-sort failure
  (`tests/integration/test_ftf_0407_critical_behavior_requirements.py`'s
  own import block — the producer's own pre-push `ruff check` had been
  scoped to `apps/api` only, missing the repo-root `tests/` tree CI
  actually lints) and a genuine `mypy` dict-variance error
  (`tests/tasks/test_ftf_0407.py`'s own unannotated `_make_tool` schema
  dict literals, mypy-narrowed to `dict[str, Collection[str]]` against
  `compute_schema_hash`'s `dict[str, object]` parameter — the same fix
  shape FTF-0404's own already-CERTIFIED test file already used) before
  this review acted on the `check_suite.completed` webhook. CI on the
  real final head (`00641ac`, GitHub Actions run `35550374321`) is
  genuinely green: all 16 check runs `completed`/`success`, `gate`
  included.
- **Diff read directly** (`git diff` against the real PR base
  `f73bbd6ced4d9eaaa95b177ba05dcdee025d9ff5`): 13 files, no `alembic`
  migration file among them — `database_changes` charter honored. Read
  `apps/api/app/services/critical_behavior_requirements.py` in full and
  the exact `ToolDefinition.__init__` wiring
  (`apps/api/app/tools/tool_definition_schema.py`):
  `enforce_side_effect_classification` is a pure validator that raises
  rather than mutating `kwargs`, so `requires_confirmation` is already
  structurally consistent with `side_effect_class` by the time
  `enforce_critical_behavior_requirement` reads it — no staleness gap
  between the two guards. Confirmed `apply_new_version`
  (`tool_definition_crud.py`) always constructs a fresh
  `ToolDefinition(...)` with `is_critical` present, so the guard runs on
  both the create and the edit path, not create-only.
- **Own fault injection, this session, distinct run from the producer's
  own two** (`git worktree add` on the real merged head, `uv sync
  --all-packages`), three vectors, each reverted byte-identical
  (`git checkout --`, `git diff --stat` empty afterward) and reconfirmed
  clean immediately after:
  1. Disabled the unmeasurable (`read_only`) refusal (`if False and rank
     == RISK_RANK["read_only"]:`) — 1 test failed for real (`the same
     refusal holds even if requires_confirmation is (meaninglessly) set
     True`).
  2. Disabled the conflicting-claim (`requires_confirmation`) refusal
     (`if False and not requires_confirmation:`) — 3 tests failed for
     real, including the direct negative_acceptance check and the
     higher-rank defense-in-depth check.
  3. Disabled the live wiring call site itself in `ToolDefinition.
     __init__` (`if False and "is_critical" in kwargs:`) — 2 tests failed
     for real, including the direct real-`ToolDefinition`-construction
     proof.
- **No regression, independently re-run:** `tests/tasks/
  test_ftf_0401.py`/`test_ftf_0404.py`/`test_ftf_0405.py`/
  `test_ftf_0406.py`/`test_ftf_0407.py` — all five "all checks passed."
  This task's own real live-Postgres suite
  (`tests/integration/test_ftf_0407_critical_behavior_requirements.py`)
  run against a freshly created local PostgreSQL 16 database/role —
  "all checks passed" (15/15 real checks, a real uvicorn server serving
  both `build_tool_definition_router` and this task's own
  `build_critical_behavior_requirement_router`). FTF-0404's own
  live-Postgres suite (`tests/integration/test_ftf_0404_tool_schema_crud.py`)
  separately re-run against another freshly recreated database/role —
  "all checks passed," no regression.
- **Static checks re-run clean, this session, from the repo root**
  (matching CI's own working directory): `ruff check .`/`ruff format
  --check .` clean; `mypy .` (from `apps/api`) — 2 errors, both the
  pre-existing, unrelated `authlib.jose` missing-stub gap in
  `oidc_login.py`, confirmed identical on `main`.
- **`scripts/validate_spec.py --strict`:** this reviewing sandbox's own
  shallow clone initially surfaced 17 `stale_commit` defects, none
  touching FTF-0407 (FTF-0001–0006, FTF-H001/H002/H028/H034, FTF-1810,
  FTF-1601, FTF-R011/R014/R015/R017); `git fetch --unshallow` resolved
  every one of them, confirming a sandbox artifact (this repository's own
  already-documented shallow-clone precedent), not a real defect. Clean
  ("task graph and verification-map are clean") after unshallowing.
- **Task contract re-pulled directly:** `python tools/r1_handoff.py
  packet FTF-0407 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance
  clause 1's "critical responsibility" half (deterministic outcome tests
  and runtime enforcement) met directly by
  `enforce_critical_behavior_requirement`'s confirmed live wiring plus
  the real `/requirements` view; the "forbidden action" half's deferral
  to FTF-04A19/FTF-04A22 confirmed accurate by direct inspection of
  `docs/TASK_INDEX.md` (FTF-04A19's own row lists FTF-0407 as its own
  dependency, not the reverse — the deferral is real, not a dodge, and
  FTF-04A22's own row separately lists FTF-0408, not FTF-0407, matching
  the submission's own claim exactly). Acceptance clause 2 (independent
  review, exact merged commit, real integration/negative-test evidence)
  satisfied by this review itself. negative_acceptance ("Unmeasurable or
  conflicting critical mandate blocks activation; no model-generated
  self-certification") proven both directly (dedicated tests for each
  half, confirmed present in both suites) and by this review's own three
  fault injections above. Dependencies FTF-0401/FTF-0406 confirmed
  genuinely `CERTIFIED` in `docs/spec-matrix.yaml` before relying on
  them.
- **No blocking defect found.**
- **Merged PR #156** (merge commit
  `5da3834194b0e2b8a84cdd3e7161ff8d43a29885`). `docs/spec-matrix.yaml`
  FTF-0407 set to `certification_state: CERTIFIED` / `proof_state:
  L2-CERTIFIED`, `evidence_hash` set to the merge commit SHA. Ran
  `sync_certified_status()` (`scripts/task_claim.py`) to write the real
  status into `spec/tasks.json`/`docs/implementation/TRACKER.json` —
  `['FTF-0407']` updated (pre-empting the same CI gate failure mode
  FTF-0406's own certification hit and fixed in a follow-up commit).
  Appended `docs/certifications/ledger.md` (FTF-0407 entry, full
  independent-review narrative). `QUEUE.md` updated (FTF-0407 moved to
  Done, Certified 32→33/300, Provisional section cleared).
  `scripts/status.py`'s own stricter "product-true" count: 9→10, matching
  `product_truth_check.py`'s own `10/10`.
- **Validated-by:** this session (independent — see session-ID check
  above), https://claude.ai/code/session_0185UQFPYhDkeasgkhTHrE5K,
  2026-09-21.

## 2026-09-21 — FTF-1501 — Tenant isolation suite
- **PR:** to be opened against `main` from `claude/wizardly-fermat-bhjs9m`,
  title `[relay] FTF-1501: Tenant isolation suite`.
- **Commit:** `778f6f2` (implementation, plus the FTF-0406/FTF-0407
  CI-wiring-gap fix below); `docs/spec-matrix.yaml`/`PROGRESS.md`/
  `QUEUE.md`/this entry pushed as a second, tracking-doc-only commit on
  the same branch, matching the FTF-0201/FTF-0301/FTF-0401/FTF-0402/
  FTF-0305/FTF-0404/FTF-0406/FTF-0407 two-commit precedent.
- **Ground truth checked before picking work:** independently recomputed
  the dependency-ready frontier from scratch against `docs/spec-matrix.yaml`'s
  live `certification_state` (33 CERTIFIED as of this shift, FTF-0407
  merged as `5da3834`/certified as `5100bac`), never trusting `QUEUE.md`'s
  own prior "Next shift" note by itself: FTF-0902, FTF-1001, FTF-1401,
  FTF-1501, FTF-1701 (newly unblocked once FTF-0407 reached CERTIFIED,
  per `docs/TASK_INDEX.md`) are dependency-ready; FTF-0403 is also
  dependency-ready but already has a complete, CI-green PROVISIONAL PR
  open (#158) awaiting Control-plane L2 review — picking it again would
  duplicate an already-submitted item, so this shift excluded it from the
  pick; FTF-0202 stays excluded per the standing flagged owner-ask note
  (PR #143's bare claim, no corroborating live-conversation record found).
  Of the remaining five, FTF-1501 (CAP-14) is the only one carrying an
  actual `CAP-xx` requirements tag (the rest are `AF-xx`), matching this
  repo's own established "lowest CAP number, no external-service ask"
  pick precedent (FTF-0402/FTF-0404/FTF-0407 were each picked the same
  way).
- **Real gate-integrity gap found and fixed first (Law 9):** `tests/tasks/
  test_ftf_0406.py` and `tests/tasks/test_ftf_0407.py` (both already
  CERTIFIED and merged) had no `gate` job step, and `tests/integration/
  test_ftf_0407_critical_behavior_requirements.py` had no live-Postgres
  integration job — `.github/workflows/ci.yml` never ran any of these
  three files, confirmed by `git log --oneline -- .github/workflows/
  ci.yml` (last touch: `2bc435b`, FTF-0405's own fix for the identical
  class of gap on FTF-0404) and a direct `grep -in "0406\|0407"
  .github/workflows/ci.yml` returning zero matches before this shift.
  This calls into question what the FTF-0406/FTF-0407 certifier reviews'
  own "CI reconfirmed green (16/16)"/"(17/17)" checks actually verified —
  those counts necessarily came from jobs unrelated to these two tasks'
  own new test files, since no job invoked them. Reconfirmed both
  no-DB suites and FTF-0407's own live-Postgres suite genuinely pass
  locally (`uv run python3 tests/tasks/test_ftf_0406.py`/`test_ftf_0407.py`
  → "all checks passed"; `tests/integration/
  test_ftf_0407_critical_behavior_requirements.py` against a freshly
  created local PostgreSQL 16 database/role → "all checks passed") before
  wiring them into `ci.yml`'s `gate` job and a new
  `critical-behavior-requirements-integration` job. This is disclosed,
  not silently patched over — a future Control-plane session should treat
  the FTF-0406/FTF-0407 certifier reviews' own CI-green claims as
  needing re-verification now that these jobs actually exist.
- **Task contract:** `python tools/r1_handoff.py packet FTF-1501 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance ("Freeze the minimum
  shared phase interface and domain schemas at the declared owner paths
  ... Exercise real two-tenant API/DB/worker/storage/cache/model paths
  under restricted identities"; "Independent review, exact merged commit,
  real relevant integration and negative-test evidence recorded"),
  negative_acceptance ("No security result from mocked auth or
  superuser-only DB tests"), `database_changes: "No independent schema
  change"`, `api_contract: "No production authorization bypass; security
  scenario inputs and immutable evidence outputs"`, `files:
  [tests/security/tenant_isolation_suite.py, tests/tasks/test_ftf_1501.py,
  packages/contracts/openapi/15-interface.yaml,
  packages/contracts/jsonschema/15-domain.schema.json]`,
  `dependencies: [FTF-0106, FTF-H003]` (both CERTIFIED),
  `shared_lock_required: [contracts:15]` (first task to claim contract
  slot 15 — no `15-interface.yaml`/`15-domain.schema.json` existed before
  this task).
- **Scope, honestly narrowed (disclosed, not assumed):** this task's own
  acceptance clause names "real two-tenant API/DB/worker/storage/cache/
  model paths". Directly inspected what actually exists as live
  infrastructure in this repository before writing a single check:
  `apps/api/app/main.py`'s own module docstring enumerates exactly six
  live-wired routes (auth, litellm-connections, artifacts, projects,
  tools, requirements); `find workers -maxdepth 2` shows
  `workers/{training,evaluation,data,deploy}` are still README/
  pyproject-only scaffolds, no real Temporal worker or job runner exists;
  `packages/storage/tenant_policy.py` (FTF-H003) has zero live callers
  anywhere in `apps/api/app` (grepped directly) — a pure, still-unwired
  policy module, not a live storage path; no cache or model-serving path
  exists at all. Declaring worker/storage/cache/model checks against
  infrastructure that does not exist would be exactly the fabricated
  evidence Law 11/Section 84.4 forbids. This task is narrowed to what
  genuinely has live existence: API + DB, matching the same disclosed-
  narrowing precedent already independently certified for FTF-0103/
  FTF-0104/FTF-0105/FTF-H003/FTF-0301/FTF-0305/FTF-0402/FTF-0407.
- **Design:** rather than re-deriving a two-tenant fixture, this task
  reuses FTF-0106's own `apps/api/app/auth/rbac_idor_security_tests.py`
  `TwoTenantRoleMatrixFixture` — that module's own docstring names FTF-1501
  directly as an intended dependent ("the later full-scale suites this
  task's own dependents name (FTF-1501 'Tenant isolation suite', FTF-1504
  'IDOR suite') have a real six-role, two-tenant fixture ... to build on
  instead of each re-deriving their own narrower one") — and FTF-H003's
  own `authorize_resource_ownership`/`tenant_scoped_session_for` for the
  real DB-layer cross-tenant guard, rather than a hand-rolled query.
  `tests/security/tenant_isolation_suite.py` seeds one real connection/
  artifact/project/tool in org1 (via the already-live-wired real routers),
  then for each of connection/artifact/project/tool proves:
  `owner_read_reachable` (200, Law 5 — a resource that is never shown
  reachable first is not evidence of anything when its denial is proven
  next), `cross_tenant_api_read_denied` (403, org2's own real admin
  principal against org1's own path — denied by `require_role`'s
  `role_lookup` before ever reaching the resource), `cross_tenant_api_
  write_denied` (403, same shape, `PATCH`), `unauthenticated_denied`
  (401), and `cross_tenant_db_read_denied` (a real, restricted
  `sovra_app`-role session scoped to org2's own tenant context, probing
  for org1's real row by its real ID through `authorize_resource_
  ownership` — the exact same production guard every CRUD module in this
  repository already calls). Plus one cross-layer check specific to the
  nested `tool` resource (FTF-H003's own contribution exercised directly):
  org1's own admin, legitimately authorized for org1, is denied (404)
  against a tool reached via a real project that genuinely belongs to
  org2, not org1 — proving the guard checks the *parent* resource's real
  ownership, not just the caller's own top-level tenant membership. The
  read-only `/requirements` view gets its own three checks: owner
  reachable, cross-tenant denied (403), and a cross-layer foreign-project
  request — which this task's own first implementation attempt wrongly
  expected to return 404, until running it against the real server
  showed 200 with an empty list; `/requirements` is a list view, not an
  item lookup, so a foreign `project_id` naturally matches zero rows in
  the real tenant-scoped query, exactly matching `tool_definition_routes.py`'s
  own `list_all` convention for "no matches" — the suite's own check was
  corrected to assert the empty list (proving no foreign data leaked)
  rather than a 404, a real, disclosed self-correction rather than
  silently adjusting the production code to match a wrong test
  expectation. 25 real checks total against a live PostgreSQL 16 cluster.
- **negative_acceptance made real:** "No security result from mocked auth
  or superuser-only DB tests" is enforced two ways. (1)
  `assert_not_superuser_role` refuses to proceed (raises
  `TenantIsolationSuiteError`) if the connection's own current role
  resolves as a real Postgres superuser via `pg_roles.rolsuper` — checked
  before this suite ever opens a tenant-scoped session, since RLS is not
  even enforced against a superuser/`BYPASSRLS` role (Section 65.1), so
  any result collected through one would not be real tenant-isolation
  evidence; also fails closed (raises) if `current_user` cannot be
  resolved against `pg_roles` at all, never silently passing. Proven
  against a plain stub (not a real database) in `tests/tasks/
  test_ftf_1501.py`: raises for `rolsuper=True`, raises for an unresolved
  answer, does not raise for a genuine `rolsuper=False` answer. (2) every
  HTTP call in the live suite goes through a real socket
  (`http.client.HTTPConnection`) to a real, locally-hosted `uvicorn`
  server — never a mocked auth stub or an in-process function call — the
  same discipline every prior integration test in this repository already
  establishes.
- **Contract freeze:** `packages/contracts/openapi/15-interface.yaml` and
  `packages/contracts/jsonschema/15-domain.schema.json` declare
  `TenantIsolationScenarioResult`/`TenantIsolationEvidence`, matching
  `tests/security/tenant_isolation_suite.py`'s own `ScenarioResult`/
  `TenantIsolationEvidence` dataclasses field-for-field (tested directly
  in `tests/tasks/test_ftf_1501.py` via a tiny stdlib-only structural
  validator, the same approach `test_ftf_1601.py` already established —
  proven non-decorative by a negative case that genuinely rejects a
  corrupted instance). `paths: {}` deliberately — this task adds no new
  production HTTP route of its own, matching `16-interface.yaml`'s own
  precedent for a pure evidence-record contract (FTF-1601).
- **Evidence:** 25 no-DB checks in `tests/tasks/test_ftf_1501.py` (pure
  `build_evidence`/`assert_not_superuser_role` logic, content-free-by-
  construction field introspection, contract conformance) + 25 real
  live-Postgres/live-HTTP checks in `tests/security/
  tenant_isolation_suite.py`. Two independent fault injections, both
  reverted byte-identical and both suites re-run clean afterward: (1)
  disabled `build_evidence`'s own empty-results guard (`if False and not
  results:`) — caught for real by the no-DB suite's named Law-1 check;
  (2) disabled the real, already-CERTIFIED `authorize_resource_ownership`'s
  own ownership check (`if False and (row is None or row[0] !=
  context.organization_id):`) — caught for real by all 4 `cross_tenant_
  db_read_denied` checks in the live suite (25/25 → 21/25, the exact 4
  expected failures, no false negatives and no false positives
  elsewhere). Independently reconfirmed no regression: `tests/tasks/
  test_ftf_h003.py`/`test_ftf_0106.py` both still "all checks passed"
  after the second fault injection's revert.
- **Full regression re-run clean:** every `tests/tasks/test_ftf_*.py`
  file that needs `uv run` (0102, 0104, 0101, 0103, 0105, h003, 0106,
  0201, 0301, 0401, 0402, 0305, 0404, 0405, 0406, 0407, 1501) — all "all
  checks passed". `ruff check .`/`ruff format --check .`/`mypy .` clean
  across the full tree (128 source files). `python3
  scripts/validate_spec.py --strict` clean (`git fetch --unshallow
  origin` first — this sandbox's own clone is shallow by default, the
  same pre-existing artifact every prior relay/certifying session has
  already documented). `python3 scripts/product_truth_check.py
  --fail-on-unwired` — `10/10` before and after this shift (this task's
  own `proving_symbol` is deliberately empty: no new apps/api production
  module, no live-wiring claim — matching the FTF-0106/FTF-H003
  precedent, not a regression or an omission). `python3 scripts/status.py`
  — `33/300 certified · 10 product-true`, unchanged (PROVISIONAL does not
  move the certified count).
- **Submission state:** PR opened as PROVISIONAL; `docs/spec-matrix.yaml`
  FTF-1501 set to `certification_state: PROVISIONAL` /
  `proof_state: L1-PROVEN`, `evidence_hash: 778f6f2`, `proving_symbol: ''`
  (no live-wiring claim), `proving_test:
  tests/tasks/test_ftf_1501.py`. Awaiting independent Control-plane L2
  certification — not self-certified (Law 4). Certified stays 33/300;
  product-true stays 10/10.

## 2026-09-21 — FTF-1501 — record the opened PR link
- **PR:** https://github.com/SJZLab/Sovra/pull/159
  (`claude/wizardly-fermat-bhjs9m` → `main`).

## 2026-09-21 — Owner ask resolved: FTF-0202 secret backend (OpenBao)
Live owner conversation, this session (same session as the FTF-1501
submission above). The owner directly confirmed **OpenBao** (self-hosted,
Vault's open-source fork) for FTF-0202's secret-management backend, with
reasoning: a self-hosted/self-built custom solution was judged too
complex to deploy, and a cloud-provider-managed secret store introduces
both an external dependency and ongoing cost — OpenBao avoids both while
matching the existing `vault://`/`openbao://` reference-string shape
convention FTF-0101/FTF-0201 already validate against.

This supersedes PR #143 ("Owner decision: FTF-0202 backend selected
(OpenBao)"), flagged by a 2026-09-20 Control-plane certifier shift (see
above) for carrying no corroborating evidence — no live-conversation
citation, no reasoning tied to the architecture — despite being opened
from the owner's own GitHub account (`samerzakaria`). PR #143 had also
gone stale (`mergeable_state: dirty` against the current `main`,
confirmed via `mcp__github__pull_request_read` before acting). Closed
without merging; the properly-evidenced resolution is recorded in
`QUEUE.md`'s "Owner actions — current" section, meeting the same
evidence bar every other resolved ask in this repository's history
already meets. FTF-0202 is now genuinely dependency-ready — implementing
it (a real OpenBao resolver against live secret refs) is a separate task
for a future relay shift's own PEV cycle, not done as part of this
resolution.
## 2026-09-21 — FTF-1001 — ModelVersion schema/API
- **PR:** https://github.com/SJZLab/Sovra/pull/160
  (`claude/wizardly-fermat-4ez7fq` → `main`), title `[relay] FTF-1001:
  ModelVersion schema/API`.
- **Commit:** `bd53802` (implementation + tests + migration + CI wiring);
  `docs/spec-matrix.yaml`/`QUEUE.md`/`PROGRESS.md`/this entry pushed as a
  second, tracking-doc-only commit on the same branch, matching the
  FTF-0201/FTF-0301/FTF-0401/FTF-0402/FTF-0305/FTF-0404/FTF-0406/FTF-0407
  two-commit precedent.
- **Ground truth checked before picking work:** independently recomputed
  the dependency-ready frontier fresh against `docs/spec-matrix.yaml`'s
  own live `certification_state` set (33 CERTIFIED, 0 PROVISIONAL on
  `main` at shift start — FTF-0407 merged as `5da3834`), never trusting
  `QUEUE.md`'s own prior "Next shift" note by itself (parsed every
  non-`CERTIFIED` task's own `deps=` field directly). Confirmed FTF-0403
  and FTF-1501 already have open, unmerged `[relay]` PRs from other
  concurrent shifts (PR #158, PR #159 — both still `certification_state:
  NONE` on `main`, i.e. genuinely in-flight, not yet a completed claim),
  so this shift did not duplicate either. Of the remaining
  dependency-ready, unclaimed items (FTF-0202 — owner ask, still open;
  FTF-0902; FTF-1001; FTF-1401), FTF-0902's own real acceptance clause
  ("Install and test actual CNI/firewall/DNS/IPv6/metadata restrictions")
  and negative_acceptance ("NetworkPolicy YAML on a nonenforcing cluster
  is not a pass") require a real, policy-enforcing Kubernetes cluster this
  sandbox does not have — a genuine `resource_gate`/`BLOCKED_EXTERNAL`
  risk, not something to improvise around — so it was set aside rather
  than picked into a shift it would likely block. FTF-1001 matches the
  schema/crud/routes-triplet-plus-migration pattern FTF-0201/FTF-0301/
  FTF-0401/FTF-0404 already established repeatedly in this repository,
  with no external-service ask of its own; picked over FTF-1401 (API load
  test) for the lower AF number among the two remaining safe candidates.
- **Task contract:** `python tools/r1_handoff.py packet FTF-1001 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance ("Freeze the minimum
  shared phase interface and domain schemas at the declared owner paths
  ... Then: ModelVersion supports endpoint-only/full-weight/adapter
  imports and Factory training lineage"; "Independent review, exact
  merged commit, real relevant integration and negative-test evidence
  recorded"), negative_acceptance ("Required nonexistent weights/hash for
  endpoint import, mutable production artifact or cross-tenant parent
  denied"), `database_changes: "Own forward Alembic migration for this
  resource; tenant/composite constraints/RLS and upgrade tests"`,
  `api_contract: "/api/v1/model-versions; /model-imports; /deployments;
  owned health adapters and serving client"`, `files:
  [services/serving-controller/vllm/modelversion_schema_api.py,
  tests/tasks/test_ftf_1001.py, packages/contracts/openapi/
  10-interface.yaml, packages/contracts/jsonschema/10-domain.schema.json]`
  (the packet's own single abbreviated suggestion — refined below, the
  same FTF-0404/FTF-0406/FTF-0407 precedent for a ticket whose packet
  names one file but whose real charter spans a schema/crud/routes
  triplet), `dependencies: [FTF-0301, FTF-0401, FTF-0103]` (all
  CERTIFIED), `contract_owner: FTF-1001`, `shared_lock_required:
  [contracts:10, migration:application]`.
- **Scope resolved against real, already-established precedent, not
  invented fresh:** the packet's own `files`/`api_contract` fields sketch
  a much larger surface (`/model-imports`, `/deployments`, "owned health
  adapters and serving client", a full vLLM serving controller) than this
  task's own acceptance/negative_acceptance actually names. Checked
  `docs/TASK_INDEX.md` directly before scoping down: FTF-1003 ("vLLM
  serving controller"), FTF-1005 ("Serving health checks") and FTF-1008
  ("Staging deployment") each list `FTF-1001` as their own dependency —
  the reverse of a task this one could borrow scope from — confirming the
  serving/deployment/health surface is genuinely later, separately
  dependency-gated work, not a gap this task's own contract requires
  filling. Likewise, FTF-1101 ("PromotionPolicy engine") lists `FTF-1001`
  as its own dependency and Section 35's own tag vocabulary
  (`candidate`/`eval-passed`/`approved`/`staging`/`production`/`retired`/
  `revoked`) and "one production version per deployment slot" rule belong
  to that later promotion-governance layer, not to this schema/API task —
  no tag/production-slot field or enforcement is added here. This task is
  narrowed to exactly its own acceptance clause: a real ModelVersion
  schema/CRUD/API supporting the three named import kinds and a real
  lineage link back to a `TrainableArtifact`, `contract_owner`'s own
  `packages/contracts/openapi/10-interface.yaml`/`packages/contracts/
  jsonschema/10-domain.schema.json` freeze deliberately narrowed the same
  way FTF-1601's own `contract_owner: FTF-1601` precedent did for
  section 16 — checked directly (`git log --all --diff-filter=A --
  packages/contracts/`) that no other task has ever claimed section 10
  before writing to it.
- **negative_acceptance resolved against the real fields this task
  actually has, disclosed not hidden** (the full reasoning lives in
  `model_version_schema.py`'s own module docstring, not only here):
  "Required nonexistent weights/hash for endpoint import" -- an
  `endpoint` import has no local weight bytes to hash at all (Law 12's
  own `inference_only` capability), so it structurally forbids
  `trainable_artifact_id`/`weights_digest`/`base_model_revision`, checked
  both in `ModelVersion.__init__` (pure, construction-time) and by a real
  `ck_model_versions_kind_consistency` database `CHECK` constraint
  (defense in depth -- never only an application-level check a bypassing
  write could evade). "mutable production artifact" -- a `full_weight`/
  `adapter` import's parent `TrainableArtifact` must already be `ready`
  (FTF-0301's own immutable, license-accepted, digest-resolved state),
  never `registered` (still mutable -- FTF-0301's own `apply_edits`
  refuses to touch anything but a `registered` row precisely because its
  identity can still change) or `archived` (a closed record); checked in
  `model_version_crud.py`'s own `create_model_version`, since it requires
  a real database fetch this schema module cannot make alone. "cross-tenant
  parent" -- the referenced artifact must belong to the *same*
  organization, proven by reusing FTF-0301's own already-certified
  `get_artifact` (built on FTF-H003's `authorize_resource_ownership`)
  directly rather than re-deriving that IDOR guard a second time -- no new
  authorization primitive is introduced, only composition, the same
  discipline FTF-0301/FTF-0404 already applied to a different parent
  relationship. `weights_digest`/`base_model_revision` are never accepted
  as a disconnected client claim for `full_weight`/`adapter` -- they are
  always copied from the real, already-resolved parent row
  `create_model_version` itself fetches, matching Section 65.1's "never
  trust an arbitrary client-supplied value where a real source of truth
  already exists" discipline FTF-0301 already established for license-
  acceptance attribution.
- **Immutability enforced at three independent layers, not merely
  claimed:** (1) no edit/delete route exists in
  `model_version_routes.py` at all; (2) the migration's own database
  grant is `SELECT, INSERT` only -- never `UPDATE`/`DELETE`, a stronger
  real guarantee than every prior versioned resource's own SELECT/INSERT/
  UPDATE grant, since this table has no legitimate in-place mutation at
  all; (3) proven directly this session by a real `UPDATE` attempt through
  the restricted `sovra_app` role against a real row, refused by Postgres
  itself (see fault injection/grant-surface proof below).
- **Path shape refined from the packet's own abbreviated suggestion:**
  nested under a specific project
  (`/api/v1/organizations/{organization_id}/projects/{project_id}/
  model-versions`), matching FTF-0404's own `/tools` precedent (a
  `ModelVersion` genuinely belongs to one project for its entire
  lifetime, Section 6.8's own project-owns-its-models framing) rather
  than FTF-0301's own top-level `/artifacts` shape (a `TrainableArtifact`
  can back more than one project's own models, Section 68.4).
- **Tests:** 24 no-DB test functions
  (`tests/tasks/test_ftf_1001.py`): `validate_import_kind`/
  `validate_endpoint_reference` real bounded/closed-set validation;
  `ModelVersion.__init__` refuses every structurally inconsistent
  combination this task's own negative_acceptance names before any
  session or database is ever touched (endpoint carrying local-weight
  fields, full_weight/adapter missing lineage/digest/revision, a
  malformed `weights_digest` shape, an unknown `import_kind`); the real,
  locally-hosted `build_model_version_router` denies an unauthenticated
  request and a role outside `MODEL_VERSION_WRITE_ROLES` on a write
  before `session_factory` is ever called, and lets a read-only role
  reach the session layer for a GET; a module-source check confirms
  `model_version_crud.py` composes `authorize_resource_ownership`/
  `execute_tenant_scoped`/FTF-0301's own `get_artifact` directly, never a
  second, hand-rolled primitive.
  26 real live-Postgres checks
  (`tests/integration/test_ftf_1001_model_version_schema_api.py`, two
  real organizations, three real `TrainableArtifact` fixtures --
  `ready`/`registered`/a second `ready` artifact belonging to the other
  organization): unauthenticated/role-boundary denial; project-ownership
  cross-org denial (404); a real endpoint import (201, version_number 1,
  no local weight lineage); the mutable-artifact negative_acceptance case
  (409); the cross-tenant-parent negative_acceptance case (404, identical
  to missing); a genuinely nonexistent `trainable_artifact_id` (404); a
  full_weight import with no lineage at all (400); a real full_weight
  import (201, version_number 2, `weights_digest`/`base_model_revision`
  copied server-side from the real resolved parent artifact, never from
  the request payload -- the route accepts no such fields at all);
  listing in real version order; cross-project scoping (404); the core
  IDOR case (404); a raw INSERT bypassing the ORM's own `__init__` check
  refused by the real `ck_model_versions_kind_consistency` CHECK
  constraint; `sovra_app` genuinely cannot `UPDATE` a `model_versions` row
  directly.
- **Own fault injection (Law 1), three distinct vectors, each reverted
  byte-identical (`git diff --stat` empty afterward) and reconfirmed
  clean immediately after:**
  1. Disabled the mutable-artifact guard in `create_model_version`
     (`if False:` in place of `if artifact.status != "ready":`) — the
     live-Postgres suite's own negative_acceptance check flipped for real
     (409 became 201; two further checks cascaded, since the
     wrongly-admitted row shifted `version_number` sequencing downstream
     — expected, not a bug).
  2. Disabled the structural endpoint-vs-artifact consistency block in
     `ModelVersion.__init__` (`if False:` in place of `if "import_kind"
     in kwargs:`) — exactly the 7 named structural checks in the no-DB
     suite failed for real.
  3. Dropped the real `ck_model_versions_kind_consistency` CHECK
     constraint directly against the live test database
     (`ALTER TABLE ... DROP CONSTRAINT`) and re-ran the exact
     bypassing-raw-INSERT statement the integration suite's own defense-
     in-depth check uses — it succeeded (previously refused), confirming
     the constraint, not something else, is genuinely what catches it.
     Database rebuilt from a fresh migration afterward (constraint
     restored via `alembic upgrade head` on a clean database, not a
     partial patch).
- **No regression, independently re-run this session:**
  `tests/tasks/test_ftf_0301.py`/`test_ftf_0401.py`/`test_ftf_0404.py`/
  `test_ftf_0406.py`/`test_ftf_0407.py` — all five "all checks passed."
  FTF-0301's own live-Postgres suite
  (`tests/integration/test_ftf_0301_trainable_artifact_schema_api.py`)
  and FTF-0404's own
  (`tests/integration/test_ftf_0404_tool_schema_crud.py`) — both re-run
  against this task's own already-migrated database (this task's
  migration adds a purely additive `UNIQUE(organization_id, id)`
  constraint to `trainable_artifacts`, the same precedent FTF-0401/
  FTF-0404's own migrations already established for `workspaces`/
  `projects`) — both "all checks passed."
- **Static checks:** `ruff check`/`ruff format --check` clean on every
  file this task touched, and clean across the full `apps/api` tree (the
  6 pre-existing `UP042` errors in `apps/api/app/config/
  privacy_purpose_taxonomy.py`/`observability_foundation.py`/
  `apps/api/app/services/environment_preflight.py` confirmed
  byte-identical on `main` via `git stash` before/after comparison — not
  this task's own). `mypy .` clean on every file this task touched; the
  repo-wide `alembic.op`/`alembic.context` stub-attribute gap is the same
  pre-existing class of error every migration file in this chain already
  carries (confirmed identical error text/line pattern for
  `0001`-`0006`), not a new defect this migration introduces.
- **`scripts/validate_spec.py --strict`:** clean (`git fetch --unshallow
  origin` first — this sandbox's own clone is shallow by default, the
  same pre-existing artifact every prior relay/certifying session has
  already documented).
- **`scripts/product_truth_check.py --fail-on-unwired`:** `10/10` before
  this shift, `11/11` after (this task's own `proving_symbol`,
  `build_model_version_router`, has a real, non-test caller in
  `apps/api/app/main.py` — not a TRACKER). `scripts/status.py`'s own
  stricter "product-true" count (CERTIFIED-only) stays `10` — PROVISIONAL
  does not count until independently certified (Law 2).
- **Discovered but not acted on, flagged for visibility:** `test_ftf_0406.py`
  and `test_ftf_0407.py` are not wired into `.github/workflows/ci.yml` at
  all (neither a `gate` job step nor a dedicated integration job exists
  for either, confirmed by direct `grep` — `0406`/`0407` do not appear
  anywhere in the file before this task's own commit). Both tasks'
  own PROGRESS.md/queue.md submission entries state CI ran green, which
  is consistent with the *existing* checks all passing, but the new tests
  those two tasks each added were apparently never actually exercised by
  CI. This is outside this task's own scope to fix (touching
  already-CERTIFIED tasks' own locked evidence is a different task's
  concern, not a relay shift's to improvise), so it is left exactly as
  found — not silently worked around, not fixed here, flagged here and in
  `QUEUE.md`/`PROGRESS.md` for Control plane/a future shift to pick up as
  its own dependency-ready item.
- **PR opened as PROVISIONAL** (`#160`, title `[relay] FTF-1001:
  ModelVersion schema/API`); `docs/spec-matrix.yaml` FTF-1001 set to
  `certification_state: PROVISIONAL` / `proof_state: L1-PROVEN`,
  `proving_symbol: build_model_version_router`. Awaiting independent
  Control plane certification — not self-certified (Law 4). Certified
  stays 33/300; `scripts/status.py`'s own stricter "product-true" count
  stays 10.
## 2026-09-21 — FTF-1401 — API load test
- **PR:** https://github.com/SJZLab/Sovra/pull/161
  (`claude/wizardly-fermat-uec7js` → `main`), title
  `[relay] FTF-1401: API load test`.
- **Commit:** implementation + contracts + CI wiring on one commit;
  `docs/spec-matrix.yaml`/`PROGRESS.md`/`QUEUE.md`/this entry pushed as a
  second, tracking-doc-only commit on the same branch, restoring the
  FTF-0201/FTF-0301/FTF-0401/FTF-0402/FTF-0305/FTF-0404/FTF-0406/FTF-0407
  two-commit precedent.
- **Ground truth checked before picking work:** ran the CLAUDE.md.sha256
  self-check (matched — no BLOCKER), read PROGRESS.md/QUEUE.md/this file
  fresh, confirmed `cognee-cli` is not installed in this sandbox
  (text-search fallback declared). Confirmed this session's branch sat
  exactly at `main`'s tip (`5100bac`, FTF-0407 CERTIFIED, 33/300, 10
  product-true) before any change. Checked GitHub directly for open PRs
  rather than trusting `QUEUE.md` alone: found PR #158 (FTF-0403), #159
  (FTF-1501), #160 (FTF-1001) already open from other, more recent relay
  shifts than `QUEUE.md`'s own last update — none of these had merged
  into `main` yet (`docs/spec-matrix.yaml` on `main` still showed 0
  PROVISIONAL), so they were treated as already claimed, not re-done.
  Independently recomputed the dependency-ready frontier from
  `docs/TASK_INDEX.md`'s own `deps=` fields against the live 33-CERTIFIED
  set in `docs/spec-matrix.yaml`: FTF-0202 (owner ask, still open/
  unverified per `QUEUE.md`'s own flagged note), FTF-0403, FTF-0902,
  FTF-1001, FTF-1401, FTF-1501, FTF-1701 (newly unblocked now that
  FTF-0407 is CERTIFIED) are the seven dependency-ready items. Excluding
  the three already claimed by open PRs above and the owner-blocked
  FTF-0202 left FTF-0902, FTF-1401, FTF-1701. Read FTF-0902's own task
  packet first: its acceptance clause requires installing and testing
  actual CNI/firewall/DNS/IPv6/metadata restrictions on a real enforcing
  Kubernetes cluster ("NetworkPolicy YAML on nonenforcing cluster is not
  pass") — no such cluster is reachable from this sandbox, so it was set
  aside rather than attempted with a fabricated pass. FTF-1401 was picked
  over FTF-1701 as concretely achievable with only this sandbox's real
  local Postgres + uvicorn (no target-agent container/runtime-adapter
  infrastructure needed, unlike FTF-1701's own Section 66 scope).
- **Task contract:** `python tools/r1_handoff.py packet FTF-1401 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance ("Freeze the minimum
  shared phase interface and domain schemas... Run declared API request
  mix/load/resource profile and report p50/p95/p99/errors/backpressure";
  "Independent review, exact merged commit, real relevant integration and
  negative-test evidence recorded"), negative_acceptance ("Exclude
  provider latency only where specified; no reduced hidden load or
  dropped timed-out requests"), `database_changes: "No independent schema
  change. Consume frozen contract"`, `files: [tests/performance/
  api_load_test.py, tests/tasks/test_ftf_1401.py, packages/contracts/
  openapi/14-interface.yaml, packages/contracts/jsonschema/
  14-domain.schema.json]`, `dependencies: [FTF-0006, FTF-0401, FTF-0106]`
  (all CERTIFIED), `contract_owner: FTF-1401`, `shared_lock_required:
  [contracts:14]`.
- **Disclosed partial profile (Section 49's own "if the environment is
  smaller, report a partial profile, not full-scale PASS" rule):**
  Section 49's full "Control API" profile targets 2 API replicas (2
  vCPU/4GiB each), a 4 vCPU/16GiB database, 10 organizations/100
  projects/1,000 models, 50 RPS for 30 minutes. This sandbox (and the new
  CI job) run 1 API process against 1 local Postgres instance, 3
  organizations/9 projects, for 15-30s — every run's `WorkloadSpec.
  is_partial_profile` is `True` and `disclosed_gaps` names every
  reduction; nothing is reported as a full-scale PASS. "Models"
  (`ModelVersion`/FTF-1001) is not a CERTIFIED feature as of this task —
  the request mix covers only the projects/tools surface genuinely live
  today. Section 70's durable job-admission/idempotency-key contract does
  not exist anywhere in this repository yet, so "no lost/duplicate
  admitted jobs" is substituted with a real, independently re-queried
  DB-count-vs-response-count check on tool creation (successful 201
  responses vs. real `ToolDefinition` row count) — a real "nothing
  admitted was lost" check bound to durable state, not a fabricated
  duplicate-admission proof; genuine dedup stays a disclosed gap for
  whichever later task builds Section 70. No explicit backpressure/
  rate-limiting (429/503) exists yet — `backpressure_signal_count` is a
  genuine, expected-zero measurement.
- **Design:** `tests/performance/api_load_test.py` holds both the pure,
  no-DB measurement/aggregation logic (`compute_percentile`,
  `summarize_latencies`, `build_workload_plan`, `evaluate_acceptance`,
  `execute_request`, `build_report`) and the real, live-Postgres/
  live-uvicorn run (`main()`): seeds 3 real organizations/9 real projects
  via direct DB insert (same `Organization`/`Workspace`/`Project`
  construction pattern `test_ftf_0407_critical_behavior_requirements.py`
  already established), builds a real `FastAPI` app from the already-live
  `build_project_router`/`build_tool_definition_router` (FTF-0401/
  FTF-0404, both CERTIFIED), serves it over a real local uvicorn thread,
  then runs N worker threads issuing real HTTP requests (stdlib
  `http.client`, no new dependency) at an 80/20 read/write mix for a
  bounded duration, recording one `RequestSample` per request. Reads:
  `GET .../projects/{id}`. Writes: `POST .../tools` creating a uniquely
  named `read_only` tool each time. negative_acceptance ("no ... dropped
  timed-out requests") is enforced by construction: `execute_request`
  always returns exactly one `RequestSample`, including on `OSError`
  (connection failure/timeout) — proven directly against a real closed
  local TCP port in the no-DB suite, not a mock.
- **Own fault injection (Law 1, two distinct vectors, both applied
  directly to the real shipped source and reverted byte-identical
  afterward, confirmed via `grep -n "FAULT INJECTION"` finding no match
  post-revert):**
  1. Removed the `except OSError` guard in `execute_request` (made it
     re-raise) — `test_execute_request_never_raises_on_an_unreachable_host`
     failed for real: `FAIL [FTF-1401] negative_acceptance: a request
     against an unreachable host never raises out of execute_request --
     it is reported as a real, status_code=0 sample, not dropped or
     crashed on`. Reverted, re-ran clean.
  2. Removed the unexpected-5xx-rate branch in `evaluate_acceptance` —
     `test_evaluate_acceptance_fails_when_5xx_rate_exceeds_threshold`
     failed for real: `FAIL [FTF-1401] negative_acceptance: a 0.5%
     unexpected-5xx rate (threshold 0.1%) fails, and the reason names the
     5xx clause`. Reverted, re-ran clean.
- **Tests:** `tests/tasks/test_ftf_1401.py` — 17 no-DB test functions
  (percentile/latency-summary edge cases, workload-plan proportion and
  real-shuffle proofs, all three acceptance-threshold negative cases plus
  a no-traffic-category positive case, the two fault-injected negative
  cases above, a `build_report`-never-drops-a-sample proof, the
  14-interface.yaml placeholder-path check, and a real `LoadTestReport`
  validated against `14-domain.schema.json` via a hand-rolled stdlib-only
  structural validator — same reasoning as FTF-1601's own suite for not
  adding the third-party `jsonschema` package). All 17 pass:
  `FTF-1401 no-DB proof: all checks passed (17 test functions)`.
- **Real live-Postgres/live-uvicorn run** (reproduced identically at the
  exact CI profile — 15s duration, 10 workers — before pushing): real
  local PostgreSQL 16, `alembic upgrade head` run for real, real uvicorn
  server, 3,831 real HTTP requests over 15.0s (254.8 achieved RPS): read
  P95 48.9ms / write P95 48.8ms (Section 49's own Control-API thresholds:
  500ms/1000ms), 0 unexpected 5xx, 0 failed/timed-out requests,
  `acceptance_passed: true`. The independent DB-count-vs-response-count
  check confirmed every successful (201) write response corresponds to a
  real, durably persisted `ToolDefinition` row — nothing admitted was
  lost. `FTF-1401 real API load-test proof: all checks passed`.
- **Full regression, this sandbox:** `ruff check`/`ruff format --check`
  clean repo-wide (100 files, `apps/api` + `tests`); `mypy .` clean
  repo-wide (128 source files, 0 errors). Re-ran
  `tests/tasks/test_ftf_0401.py`/`test_ftf_0404.py`/`test_ftf_0405.py`/
  `test_ftf_0406.py`/`test_ftf_0407.py` — all five "all checks passed,"
  no regression. `git fetch --unshallow origin` (this sandbox's clone is
  shallow by default, the same pre-existing artifact every prior
  session has documented), then `python3 scripts/validate_spec.py
  --strict` → "task graph and verification-map are clean."
  `product_truth_check.py --fail-on-unwired` unchanged at `10/10`
  (`proving_symbol` is deliberately `''` for this task, matching
  FTF-1601's own precedent — a measurement tool is not a new served
  production route). `scripts/status.py` unchanged at `33/300 certified ·
  10 product-true` (correct — this submission is PROVISIONAL, not yet
  independently certified).
- **CI wiring:** added a new no-DB gate step (`uv run python3
  tests/tasks/test_ftf_1401.py`, right after FTF-0405's own step) and a
  new `api-load-test-integration` job (`services: postgres`, same
  pinned-digest pattern as `tool-schema-crud-integration`, CI-profile
  `SOVRA_LOAD_TEST_DURATION_SECONDS=15`/`SOVRA_LOAD_TEST_WORKERS=10`) to
  `.github/workflows/ci.yml`. Verified the whole file still parses as
  valid YAML (`yaml.safe_load`) before pushing.
- **Disclosed, not fixed by this task (flagged for the next shift/
  Control plane — out of this task's own scope):** while adding this
  task's own CI wiring, found that FTF-0406's and FTF-0407's own no-DB
  and live-Postgres test suites are **not** wired into
  `.github/workflows/ci.yml` at all — no gate step or integration job
  anywhere in that file references `test_ftf_0406.py`, `test_ftf_0407.py`,
  or their `tests/integration/` counterparts (confirmed by direct
  `grep -n "0406\|0407"` over the whole file finding zero matches, and by
  reading the full gate-job step list, which stops at FTF-0405). Their
  CERTIFIED evidence was genuinely reproduced by their own independent
  certifier reviews (see this file's own FTF-0406/FTF-0407 CERTIFIED
  entries above), so this is not a false-certification finding — but that
  evidence is not continuously re-enforced by CI on every future PR the
  way every other CERTIFIED task's is, which is exactly the kind of
  gate-weakening-by-omission Law 9 exists to catch. Not fixed here: it
  touches two other tasks' own already-CERTIFIED evidence/CI surface, not
  this task's own declared `files`/`shared_lock_required`, and the relay
  role does not improvise scope beyond a decided, sliced item's own DoD.
- **State:** `proof_state=L1-PROVEN`, `certification_state=PROVISIONAL`.
  Not self-certified — awaiting Control plane L2 review per Law 4.
## 2026-09-21 — FTF-0403 — Budget policy
- **PR:** https://github.com/SJZLab/Sovra/pull/163
  (`claude/wizardly-fermat-zl5g8t` → `main`, title
  `[relay] FTF-0403: Budget policy`).
- **Commit:** `48eb4c6cb46fe2aca621d1c9fed8d3438ed7dce8` (implementation);
  `docs/spec-matrix.yaml`/`PROGRESS.md`/`QUEUE.md`/this entry pushed as a
  second, tracking-doc-only commit on the same branch, matching the
  FTF-0201/FTF-0301/FTF-0401/FTF-0402/FTF-0404/FTF-0406/FTF-0407
  two-commit precedent.
- **Ground truth checked before picking work:** this shift's own
  designated branch carried only already-merged history (a prior PR had
  landed as part of `main`), so it was restarted fresh from
  `origin/main` per this relay's own standing instruction rather than
  stacked on stale history. `PROGRESS.md`'s own last entry ends mid-way
  through the FTF-0406 shift, but `git log origin/main`/
  `docs/spec-matrix.yaml` show FTF-0406 and FTF-0407 both reached
  CERTIFIED since (PR #153/#156) without `PROGRESS.md` itself ever being
  updated to record it — noticed, not silently trusted or silently
  backfilled; state was independently reconciled directly from
  `git log`/`docs/spec-matrix.yaml`'s own live `certification_state`
  fields before picking any work. Independently recomputed the
  dependency-ready frontier from scratch by parsing `docs/TASK_INDEX.md`'s
  own `deps` column against `docs/spec-matrix.yaml`'s live
  `certification_state`: FTF-0202 (owner ask, still open/unverified),
  FTF-0403, FTF-0902, FTF-1001, FTF-1401, FTF-1501, FTF-1701 are the
  seven dependency-ready items. FTF-0403 has the lowest task-ID ordering
  among the non-blocked items and no external-service ask of its own.
- **Task contract:** `python tools/r1_handoff.py packet FTF-0403 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1 ("Persist
  decimal budget authorization, cost currency, scopes and approval
  history"), negative_acceptance ("Negative/unknown rates and concurrent
  allocation beyond available reservation cannot be accepted"),
  `database_changes: "Own forward Alembic migration for this resource;
  tenant/composite constraints/RLS and upgrade tests per Sections
  6/65/71. Serialize migration chain."`, `files: [apps/api/app/services/
  budget_policy.py, tests/tasks/test_ftf_0403.py]`,
  `dependencies: [FTF-0104, FTF-0401]` (both CERTIFIED),
  `contract_owner: FTF-0401`, `shared_lock_required: [contracts:04,
  migration:application]`.
- **The real gap this task closes (disclosed, not assumed):** FTF-0401's
  own `Project.budget_policy_id` column has existed, deliberately
  unwritten, since that task's own migration (`0005_projects.py`) — its
  own `project_routes.py` module docstring says so explicitly: "stays a
  schema column this task's own routes never accept, for a later task to
  write through." This is that later task.
- **Path refinement (disclosed, not silently taken):** the packet's own
  suggested single `apps/api/app/services/budget_policy.py` path was
  refined to a dedicated `apps/api/app/budget/` package split across
  `budget_policy_schema.py`/`_crud.py`/`_routes.py` — `services/` is this
  repository's own convention for a *pure, DB-free* enforcement module
  with no table of its own (`project_security_profile.py`,
  `side_effect_classification.py`, `critical_behavior_requirements.py`);
  this task genuinely needs a real, persisted table, so the closer
  structural analog is FTF-0404's own `apps/api/app/tools/` package. The
  same refinement precedent FTF-0305 already established for its own
  suggested path.
- **Design (disclosed):** `BudgetPolicy` (`budget_policy_schema.py`)
  persists a real `decimal.Decimal` `total_amount` (never a float,
  validated finite/positive/scale-bounded at construction), a closed,
  real-ISO-4217 `currency` allowlist, and a closed `scopes_json`
  vocabulary grounded directly in Section 41's own named cost-bearing
  Temporal workflow list (`TrainingWorkflow`/`EvaluationWorkflow`/
  `DeploymentWorkflow` → `training`/`evaluation`/`serving`) — no upstream
  section fixes a richer vocabulary for either. `reserve_budget_
  allocation`/`release_budget_allocation` (`budget_policy_crud.py`) are
  this task's own central negative_acceptance made real: each issues a
  single, conditional SQL `UPDATE` against the row's own aggregate
  `reserved_amount`, guarded in the *same statement* by
  `reserved_amount +/- amount` vs. `total_amount`/`0` — never a
  read-then-compare-then-write sequence two concurrent requests could
  both pass before either commits — backed by a real DB
  `CHECK (reserved_amount <= total_amount)`/`CHECK (reserved_amount >=
  0)` constraint as a second, independent backstop
  (`0007_budget_policies.py`). "Approval history" (acceptance clause 1)
  is carried by FTF-0104's own already-CERTIFIED, append-only
  `audit_events` table — every create/revise/reserve/release writes one
  in the same transaction, the same discipline FTF-0201/FTF-0301/
  FTF-0401/FTF-0404 already established — rather than a second,
  independent approval table, matching `database_changes`'s own "Own
  forward Alembic migration for this resource" (singular).
- **Wiring (disclosed):** the real, DB-enforced composite foreign key
  `projects(organization_id, budget_policy_id) ->
  budget_policies(organization_id, id)` is added purely additively
  (`budget_policy_id` stays nullable — no existing certified `Project`
  row is invalidated); `create_budget_policy` writes it through for
  real. `app.main` now includes `build_budget_policy_router` — the
  seventh live-wired product-runtime path in this repository, following
  the exact `TenantContext`/`authorize_resource_ownership`/
  `execute_tenant_scoped` composition FTF-0201/FTF-0301/FTF-0401/
  FTF-0404 already established, with the same `{workspace_admin,
  organization_admin}` write-role split FTF-0401/FTF-0404 already
  applied (Section 45 names no dedicated sentence for budget
  authorization specifically). Deliberately does NOT bump
  `project.row_version` when attaching a budget policy — that field is
  FTF-0401's own optimistic-concurrency contract over a different set of
  fields (`project_crud.py`'s own `apply_edits`); attaching a budget
  policy is additive to a column that contract never covers.
- **Regression risk checked before implementation, not after:** read
  `project_crud.py`'s own `apply_edits`/`ProjectConcurrencyError` and
  `tool_definition_crud.py`'s own `apply_new_version` to confirm neither
  touches `budget_policy_id` and neither would be affected by this
  task's own new FK/column-write. Re-ran FTF-0401/FTF-0402/FTF-0404/
  FTF-0405/FTF-0406/FTF-0407's own certified no-DB suites (all six "all
  checks passed") and FTF-0404's own live-Postgres suite (re-run "all
  checks passed" against a database carrying this task's own new
  `0007_budget_policies` migration) — no regression.
- **Evidence:** `tests/tasks/test_ftf_0403.py`, 20 no-DB test functions —
  validation shape checks, `BudgetPolicy.__init__` construction guards,
  `apply_revise_budget_policy`'s own row_version/reservation-floor
  guards, a source-inspection proof that reserve/release build the real
  atomic conditional-`UPDATE` shape and route every query through the
  real, imported `execute_tenant_scoped`/`authorize_resource_ownership`,
  and a real local uvicorn server proving unauthenticated (401)/
  role-boundary (403) denial never opens a session. Own fault injection
  (Law 1, two distinct vectors, both reverted byte-identical via
  re-applying the exact original source and reconfirmed by `diff`/
  re-running the suite clean afterward): (1) disabled the row_version
  staleness guard (`if False and expected_row_version !=
  policy.row_version:`) — caught for real by the exact named stale-
  row_version check; (2) disabled the reservation-floor guard (`if False
  and total_amount < policy.reserved_amount:`) — caught for real by the
  exact named negative_acceptance check.
  `tests/integration/test_ftf_0403_budget_policy.py`: a real Postgres
  proof (two real organizations, four real projects, a real uvicorn
  server bound to the real, restricted `sovra_app` role) covering
  create/duplicate-refusal(409)/cross-project-and-cross-organization
  IDOR(404)/negative-unknown-rate-refusal(400)/reserve/unauthorized-
  scope-refusal(400)/over-headroom-refusal(409)/revise(shrink-below-
  reserved and stale-row_version both 409, real change 200)/release
  (within/over reserved)/real approval-and-allocation history in
  order/**two real, concurrently issued HTTP reserve requests that
  together exceed the policy's own total (60+60=120 > 100) never both
  succeed** (exactly one 200/one 409, final `reserved_amount` reflecting
  exactly the one reservation that committed)/missing-tenant-context
  refusal/`sovra_app` cannot `DELETE` a row directly/the real
  `UNIQUE(project_id)` constraint refuses a second row even at the raw
  SQL level. Own fault injection (Law 1, two distinct vectors against
  the live database, both reverted byte-identical and the suite re-run
  clean on a fresh database afterward): (1) disabled the reserve
  headroom guard — caught for real by both the over-headroom-refusal and
  concurrency checks, *and* independently by the real DB `CHECK`
  constraint itself (`IntegrityError`) — a genuine second, independent
  backstop; (2) disabled the "project already has a budget policy" guard
  — caught for real by the duplicate-create-refusal check, *and*
  independently by the real DB `UNIQUE(project_id)` constraint itself
  (`IntegrityError`).
- **Tooling:** `ruff check`/`ruff format --check`/`mypy` clean repo-wide
  (`uv run ruff check .` / `uv run mypy .`, 133 source files).
  `scripts/validate_spec.py --strict` clean (after `git fetch
  --unshallow`, this sandbox's own documented shallow-clone artifact).
  `pnpm install --frozen-lockfile` had never been run in this sandbox
  before this shift (pre-existing environment gap, not caused by this
  task — confirmed by re-running FTF-0002/FTF-0003/FTF-H001's own no-DB
  suites clean immediately after installing); full repo-wide
  `tests/tasks/test_ftf_*.py` sweep clean afterward.
  `product_truth_check.py --fail-on-unwired` rises to `11/11`
  (`reserve_budget_allocation`, called for real by the real
  `/budget-policy/reserve` route — a genuine new live caller, not
  inflation); `scripts/status.py`'s own stricter CERTIFIED-only count
  stays `33/300 certified · 10 product-true` until this submission is
  independently certified.
- **Disclosed scope boundary:** this task is the *policy* authorization
  layer, not the full per-job atomic reservation/settlement system
  Section 70.3 itself still leaves open — `docs/TASK_INDEX.md` records
  FTF-H018 ("Atomic cost reservations and bounded stop-lag controls") as
  depending on this task, not the reverse.
- **Separately, outside this task's own DoD:** closed a real, disclosed
  CI gate-wiring gap noticed while this shift was already touching
  `.github/workflows/ci.yml` for this task's own new
  `budget-policy-integration` job — `tests/tasks/test_ftf_0406.py`/
  `test_ftf_0407.py` were never added to the `gate` job's own step list
  after those tasks reached CERTIFIED (PR #153/#156), so CI never
  actually re-ran their suites on any later PR. Both confirmed passing
  locally before being wired in as two additional `gate` steps in the
  same commit that touches this file for this task's own reason. A
  Control-plane/owner judgment call on whether anything further is
  needed here (e.g. auditing other CERTIFIED tasks for the same gap) —
  not expanded further by this producer session, to stay inside this
  task's own write set.
- **PR opened as PROVISIONAL; not self-certified (Law 4)** — awaiting
  independent Control-plane L2 review.

## 2026-09-21 — Control plane: FTF-0403 (PR#163) independently reviewed, CERTIFIED
Ran the CERTIFY procedure end to end (docs/CONTROL_PLANE_MODES.md),
triggered by the `pull_request.opened` webhook for PR #163.

- **Session-independence check:** this reviewing session's own ID is
  `session_01VV6XRzSwbobfnvyZfLfg6q`; all 3 commits on PR #163 carry
  `Claude-Session: .../session_017jneh8CPxEPmfPrbC5PJrg` — no match,
  independent per Law 4.
- **CI checked on the real head, not a stale claim:** subscribed while 16
  of 17 checks were still `in_progress`/`queued`; polled to completion —
  all 17 real GitHub Actions check runs `completed`/`success` on the real
  final head (`6a59384e09326c8cecfd0e3dd78472376486b0df`, run
  `35592841192`), `gate` included. Pulled the real `gate` job log
  directly (`get_job_logs`): `Product-truth: 11/11 product-true`,
  `Status: 33/300 certified · 10 product-true` (status.py's own
  CERTIFIED-only count, still 10 at CI time — expected).
- **Diff read directly:** `apps/api/app/budget/budget_policy_crud.py`/
  `budget_policy_schema.py`/`budget_policy_routes.py`/
  `apps/api/alembic/versions/0007_budget_policies.py`/
  `apps/api/app/main.py`. Confirmed `reserve_budget_allocation`/
  `release_budget_allocation` are each a single conditional SQL `UPDATE`,
  never read-then-write, backed by the real `CHECK`/`UNIQUE(project_id)`
  constraints as an independent DB-level backstop. Confirmed `app.main`
  live-wires `build_budget_policy_router` by direct source read.
- **Task contract re-pulled directly:** `python tools/r1_handoff.py
  packet FTF-0403 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — the
  committed `docs/implementation/task-packets/FTF-0403.md` matched the
  tool's own deterministic output byte-for-byte. Acceptance clause 1 and
  negative_acceptance both confirmed met by the code above and by this
  review's own fault injections.
- **Own reproduction, this sandbox** (`git worktree add` on the real
  merged PR head, `uv sync --all-packages`, a freshly created local
  PostgreSQL 16 database/role matching CI's own `sovra_migrate`/
  `sovra_app` credentials): `tests/tasks/test_ftf_0403.py` and
  `tests/integration/test_ftf_0403_budget_policy.py` (including the real
  concurrent-HTTP reservation proof) both "all checks passed."
- **Own fault injection, distinct from the producer's own two, both
  reverted byte-identical and reconfirmed clean afterward:**
  1. Disabled the reserve headroom guard — 2 checks failed for real (the
     over-headroom-refusal check and the central concurrency
     negative_acceptance check); independently, the real DB `CHECK`
     constraint itself raised `IntegrityError`.
  2. Disabled the duplicate-budget-policy guard — 1 check failed for
     real (duplicate-create-refusal); independently caught by the real
     DB `UNIQUE(project_id)` constraint itself (`IntegrityError`).
- **No regression, independently re-run:** `tests/tasks/
  test_ftf_0401.py`/`test_ftf_0402.py`/`test_ftf_0404.py`/
  `test_ftf_0405.py`/`test_ftf_0406.py`/`test_ftf_0407.py` — all six
  "all checks passed."
- **Static checks re-run clean:** `ruff check .`/`ruff format --check .`
  clean; `mypy .` (from `apps/api`) — 2 errors, both the pre-existing,
  unrelated `authlib.jose` missing-stub gap in `oidc_login.py`, confirmed
  identical on `main`. `scripts/validate_spec.py --strict` clean after
  `git fetch --unshallow`. `scripts/product_truth_check.py
  --fail-on-unwired` — own run, `11/11` product-true.
- **CI gate-wiring fix confirmed live:** `tests/tasks/test_ftf_0406.py`/
  `test_ftf_0407.py` steps present and `completed`/`success` in the real
  `gate` job's own step list.
- Dependencies FTF-0104/FTF-0401 confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- **No blocking defect found.** Merged PR #163 (merge commit
  `4afab390b60028cccc489c439d8401252a2c1d8f`). `docs/spec-matrix.yaml`
  set to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit. Ran `sync_certified_status()`
  to write the real status into `spec/tasks.json`/
  `docs/implementation/TRACKER.json` — `['FTF-0403']` updated. Certified:
  34/300, Product-true: 11/11.

Full entry: docs/certifications/ledger.md.

## 2026-09-21 — FTF-1701 — Reference agent specification
- **PR:** opened from `claude/wizardly-fermat-k8uxnh` → `main`, title
  `[relay] FTF-1701: Reference agent specification`.
- **Commit:** `408ac045c623112b25397443f665794af6207621` (implementation);
  `docs/spec-matrix.yaml`/`PROGRESS.md`/`QUEUE.md`/this entry pushed as a
  second, tracking-doc-only commit on the same branch, matching the
  established two-commit precedent.
- **Ground truth checked before picking work:** independently recomputed
  the dependency-ready frontier from `docs/spec-matrix.yaml`'s live
  `certification_state` against every non-`CERTIFIED` task's own `deps=`
  field, rather than trusting the prior shift's own "Next shift" note.
  Found FTF-0202 (owner ask), FTF-0408 (newly dependency-ready — FTF-0403
  reached CERTIFIED since the prior note), FTF-0902, FTF-1001, FTF-1401,
  FTF-1501, FTF-1701. Cross-checked live GitHub state via
  `list_pull_requests` before picking (not just this repo's own docs) and
  found QUEUE.md's own "Provisional" section was stale: PR #159/#160/#161
  (FTF-1501/FTF-1001/FTF-1401) are real, open, unmerged PROVISIONAL
  submissions it did not reflect; PR #158 duplicates the already-merged
  FTF-0403 (PR #163); PR #162 claims to resolve the FTF-0202 owner ask via
  an unverifiable "live conversation" this scheduled/automated shift has
  no means to check. Full reasoning in QUEUE.md's own newly flagged note
  (Law 9) — none of these five PRs were merged, closed, or otherwise
  acted on by this shift beyond flagging.
  FTF-1001/FTF-1401/FTF-1501 were not re-implemented (would duplicate PRs
  #160/#161/#159). FTF-0408's own acceptance clause reads on
  `AgentManifest.mandate_json` fields (Section 22 Screen D) that do not
  exist as real storage anywhere in this repository yet (`AgentManifest`
  is FTF-04A19's own, later, separately dependency-gated schema), and
  `Project` itself has no mandate/scope/languages/outputs/SLO columns to
  edit — a real DoD-clarity gap, disclosed rather than improvised.
  FTF-0902's own acceptance clause names a real enforcing Kubernetes/CNI
  cluster as its own `resource_gate` prerequisite, unavailable in this
  sandbox. FTF-1701 is the lowest-task-ID item among what remains
  genuinely unclaimed with no external-service/infra ask, both its own
  deps (FTF-0407, FTF-0405) already CERTIFIED.
- **Task contract:** `python tools/r1_handoff.py packet FTF-1701 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1 ("Specify
  bounded actual reference agent with approved tools, deterministic
  business state and explicit risk/language profile"),
  negative_acceptance ("Reference success cannot claim every arbitrary
  customer agent is production-qualified"), `database_changes: "No
  independent schema change. Consume frozen contract."`,
  `files: [evals/reference/reference_agent_specification.py,
  tests/reference/test_ftf_1701.py]`, `dependencies: [FTF-0407, FTF-0405]`
  (both CERTIFIED), `shared_lock_required: [contracts:17]`.
- **The real gap this task closes (disclosed, not assumed):** Section
  55's own named reference tools (`search_records`, `get_record`,
  `create_note`, `request_approval`, `execute_approved_action`) and its
  "deterministic independent server [that] owns permission/approval
  records" so that "tool clients cannot fabricate grants" did not exist
  anywhere in this repository. The full reference *product* (Section 54's
  own end-to-end release qualification scenario — a real connected
  repository, >=1,000 training traces, a sealed final suite, real
  training/evaluation runs) is a chain of later, separately
  dependency-gated tasks (`docs/TASK_INDEX.md`: FTF-1702 "Tool
  environment" through FTF-1715 "Final cost/quality report" all name this
  task as their own dependency, not the reverse) — this task's own
  narrower acceptance clause is what was implemented, disclosed explicitly
  in the module's own docstring.
- **Design (disclosed):** `evals/reference/reference_agent_specification.py`
  defines the five tools as `ReferenceToolSpec` dataclasses and constructs
  each as a real `app.tools.tool_definition_schema.ToolDefinition` row —
  genuinely passing FTF-0404/FTF-0406/FTF-0407's own already-CERTIFIED
  construction-time guards (`enforce_side_effect_classification`,
  `enforce_critical_behavior_requirement`), not merely matching their
  shape. `ReferenceBusinessServer` is a deterministic, in-memory
  implementation of the described business domain: `_authorized_record`
  enforces tenant scoping and per-record subject/resource ownership
  (cross-tenant and cross-subject/IDOR access both refused);
  `create_note`/`execute_approved_action` are idempotent on a caller-
  supplied `idempotency_key`; `decide_approval`/`revoke_approval` are the
  only methods that can move an `ApprovalRequest` into `approved`/
  `denied`/`revoked` and are deliberately never registered as one of the
  five dispatchable tool names — `execute_approved_action` only ever
  reads that state, making "tool clients cannot fabricate grants" true by
  construction. `dispatch_tool_call` is the real first live caller of
  FTF-0405's own `validate_instance` (disclosed in that module's own
  docstring as having no live caller yet): both call arguments and
  handler results are validated against the tool's own frozen input/
  output schema. `ReferenceAgentProfile.qualifies_this_reference_
  workload_only` is always `True` with no constructor path to set it
  `False`, making this task's own negative_acceptance real by
  construction rather than by policy text.
- **Path refinement (disclosed):** none — the packet's own declared
  `evals/reference/reference_agent_specification.py`/
  `tests/reference/test_ftf_1701.py` paths were used exactly as given,
  the first files in either directory (added `evals/__init__.py`/
  `evals/reference/__init__.py` for real Python package resolution —
  `mypy` genuinely failed with "Source file found twice under different
  module names" without them, confirmed directly, not a guess).
- **Contracts:** `packages/contracts/{openapi,jsonschema}/17-*` (contract
  slot 17, first claim) freeze the real `ReferenceToolSpec`/
  `ReferenceAgentProfile`/`ApprovalRequestRecord` shapes; `paths: {}`
  since this task owns no live HTTP route, matching the FTF-1401/
  FTF-1501/FTF-1601 precedent.
- **Tests:** `tests/reference/test_ftf_1701.py` — 24 no-DB test
  functions: construction-guard conformance (all five specs), profile
  derivation, cross-tenant/cross-subject IDOR denial, idempotency
  (`create_note`/`execute_approved_action`), denial/rollback state tested
  directly (pending/denied/revoked, plus re-execution under a different
  idempotency key), the "clients cannot fabricate grants" proof
  (`decide_approval`/`revoke_approval` are not dispatchable), the
  dispatcher's own input/output schema enforcement (including a
  monkeypatched out-of-contract handler result), a full five-tool happy
  path through the real dispatcher, and frozen-contract validation
  against real constructed instances (with two genuine-rejection cases
  proving the hand-rolled structural validator — the same one FTF-1601's
  own suite established, not the third-party `jsonschema` package — is
  not a decorative always-pass check).
- **Own fault injection (Law 1, two distinct vectors, both reverted
  byte-identical and the suite re-run clean afterward):**
  1. Disabled the resource-scoped ownership check in `_authorized_record`
     (`if False and record.owner_subject_id != subject_id:`) — failed
     with exactly `FAIL [FTF-1701] cross-subject (IDOR) get_record within
     the same tenant is refused`.
  2. Disabled the "must be approved" guard in `execute_approved_action`
     (`if False and approval.status != "approved":`) — failed with
     exactly four named checks: the pending-, denied-, and
     revoked-approval denial checks, plus the different-idempotency-key
     re-execution check.
- **Incidental, real evidence the reused guards are genuinely live, not
  merely imported:** the first run of this task's own test suite
  genuinely failed — two of the five tool specs' own first-draft
  descriptions (`request_approval`, `execute_approved_action`) tripped
  FTF-0406's real `enforce_side_effect_classification` risk-floor
  detector for real (keyword roots in ordinary English description text:
  "archive"/"escalate", then "grant"/"set"), before either description
  was reworded. Not a fault injection (no test asserted this), but direct
  evidence the guard runs on real input, not a mock.
- **No regression, independently re-run:** full `tests/tasks/
  test_ftf_*.py` sweep (24 files) all "all checks passed" or equivalent;
  `ruff check .`/`ruff format --check .`/`mypy .` clean repo-wide (137
  source files); `pnpm run lint`/`typecheck`/`format:check` clean.
  `scripts/validate_spec.py --strict` clean after `git fetch
  --unshallow`. `scripts/product_truth_check.py --fail-on-unwired` —
  `11/11` (unchanged; `proving_symbol` deliberately empty, this task adds
  no live caller into any `SOURCE_ROOTS` path, matching FTF-1401/
  FTF-1601's own precedent, not inflation). `python3 scripts/status.py` —
  `34/300 certified · 11 product-true` (unchanged — PROVISIONAL, not yet
  CERTIFIED).
- **CI:** one new `gate` job step (`uv run python3
  tests/reference/test_ftf_1701.py`) added to `.github/workflows/ci.yml`
  right after the FTF-0403 step; no new integration job, since this task
  touches no database/network/GPU resource. Confirmed the workflow file
  still parses as valid YAML after the edit.
- Dependencies FTF-0407/FTF-0405 confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- **Submitted PROVISIONAL, not self-certified (Law 4).**
  `docs/spec-matrix.yaml` FTF-1701 set to `certification_state:
  PROVISIONAL` / `proof_state: L1-PROVEN`, `evidence_hash` set to the
  implementation commit, `proving_test:
  tests/reference/test_ftf_1701.py`. Certified stays 34/300; Product-true
  stays 11/11. Awaiting independent Control-plane L2 review.

## 2026-09-21 — Control plane: FTF-1701 (PR#165) independently reviewed, CERTIFIED
Ran the CERTIFY procedure end to end (docs/CONTROL_PLANE_MODES.md),
triggered by the `pull_request.opened` webhook for PR #165.

- **Session-independence check:** this reviewing session's own ID is
  `session_01HFJZbt5ECMgJbGqx6Yx7Tq`; both commits on PR #165 carry
  `Claude-Session: .../session_01WvavBhWpFYv4MAW3yytpgt` — no match,
  independent per Law 4.
- **CI checked on the real head, not a stale claim:** subscribed while
  `docker-local-stack-integration`/`gate` were still `in_progress`;
  polled to completion — all 17 real GitHub Actions check runs
  `completed`/`success` on the real final head
  (`f3d06e5e5fb581b243cbd511a91d45368a632ba4`, run `35603489043`), `gate`
  included, with the new FTF-1701 gate step confirmed present in
  `.github/workflows/ci.yml`.
- **Diff read directly:** `evals/reference/reference_agent_specification.py`,
  `tests/reference/test_ftf_1701.py`,
  `packages/contracts/{openapi,jsonschema}/17-*`. Confirmed the
  approval-granting surface (`decide_approval`/`revoke_approval`) is
  genuinely unreachable through `dispatch_tool_call`, and that both call
  arguments and handler results are validated against the tool's own
  frozen schema.
- **Task contract re-pulled directly:** `python tools/r1_handoff.py
  packet FTF-1701 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — the
  committed `docs/implementation/task-packets/FTF-1701.md` matched the
  tool's own deterministic output byte-for-byte. Acceptance clause 1 and
  negative_acceptance both confirmed met by the code above.
- **Own reproduction, this sandbox** (`uv sync --all-packages`, `pnpm
  install`, `git fetch --unshallow`): `tests/reference/test_ftf_1701.py`
  — "all checks passed" (24/24).
- **Own fault injection, distinct from the producer's own two, both
  reverted byte-identical and reconfirmed clean afterward:**
  1. Reproduced the producer's own cross-subject-IDOR injection — failed
     with exactly the same named check.
  2. A second, independent vector the producer did not claim: disabled
     the requester-only guard in `execute_approved_action` — failed with
     exactly the named "only the original requester may execute their
     own approved action" check.
- **No regression, independently re-run:** full `tests/tasks/
  test_ftf_*.py` sweep (30 files) all "all checks passed" or equivalent;
  `ruff`/`mypy`/`pnpm lint+typecheck+format` all clean repo-wide.
- **Static checks re-run clean:** `scripts/validate_spec.py --strict`
  clean after `git fetch --unshallow`. `scripts/product_truth_check.py
  --fail-on-unwired` — own run, `11/11` product-true, unchanged
  (`proving_symbol` confirmed deliberately empty — 0/0 legitimate for
  this no-live-route task).
- Dependencies FTF-0407/FTF-0405 confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- **No blocking defect found.** Merged PR #165 (merge commit
  `bbdf0c8278d39d1f3fa7ed87c11e240a109075a3`). `docs/spec-matrix.yaml`
  set to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit. Ran `sync_certified_status()`
  to write the real status into `spec/tasks.json`/
  `docs/implementation/TRACKER.json` — `['FTF-1701']` updated. Certified:
  35/300, Product-true: 11/11.

Full entry: docs/certifications/ledger.md.

## 2026-09-21 — FTF-1702 — Tool environment
- **PR:** opened from `claude/wizardly-fermat-hlz51t` → `main`, title
  `[relay] FTF-1702: Tool environment`.
- **Commit:** `2d36085547b597625ddcfc0feb394c0c4027f0be` (implementation);
  `docs/spec-matrix.yaml`/`PROGRESS.md`/`QUEUE.md`/this entry pushed as a
  second, tracking-doc-only commit on the same branch, matching the
  established two-commit precedent.
- **CLAUDE.md integrity self-check:** `sha256sum CLAUDE.md` matched the
  committed `CLAUDE.md.sha256` byte-for-byte before any other step, per
  this shift's own standing instruction.
- **Ground truth checked before picking work:** `docs/spec-matrix.yaml`
  itself carries no populated `deps` field on any non-`CERTIFIED` task, so
  the dependency-ready frontier was recomputed by a fresh script-driven
  join of every `docs/TASK_INDEX.md` row's real `Dependencies` column
  against `docs/spec-matrix.yaml`'s live `certification_state` set, not
  merely trusting the prior shift's own "Next shift" note. Found
  dependency-ready: FTF-0902, FTF-0202 (owner ask), FTF-1001, FTF-1401,
  FTF-1301 (CostProfile schema/UI — newly ready now that FTF-0403 reached
  CERTIFIED, not present in the prior shift's own frontier), FTF-0408,
  FTF-1702, FTF-1501, FTF-R016. Cross-checked live GitHub state via
  `list_pull_requests` before picking: PR #158/#159/#160/#161/#162 are
  still open exactly as the prior shift's own flagged note described (no
  certifier-only action taken on any of them by this relay shift — full
  reasoning in QUEUE.md's own carried-forward flagged note). FTF-1001/
  FTF-1401/FTF-1501 were not re-implemented (would duplicate PRs
  #160/#161/#159). FTF-0202 stays an owner ask. FTF-0408's own disclosed
  DoD-clarity gap (`AgentManifest.mandate_json` fields with no real
  storage anywhere in this repository yet) and FTF-0902's own disclosed
  `resource_gate` prerequisite (a real enforcing Kubernetes/CNI cluster,
  unavailable in this sandbox) are both unchanged from the prior shift's
  own findings — re-checked, not re-litigated. FTF-1301 and FTF-R016 are
  both genuinely unclaimed with no open PR, but FTF-1702 has the lowest
  task-ID among items with no disclosed DoD gap, no missing external
  resource, and no duplicated in-flight PR — and is exactly the item the
  prior (FTF-1701) shift's own "Next shift" note predicted would become
  dependency-ready once FTF-1701 reached CERTIFIED.
- **Task contract:** `python tools/r1_handoff.py packet FTF-1702 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1 ("Implement
  real disposable seeded backend for search/get/create-note/
  request-approval/execute-approved-action"), negative_acceptance
  ("Canned success tools and unguarded financial writes cannot satisfy
  target-product test environment"), `database_changes: "No independent
  schema change. Consume frozen contract; any required change is a
  separately claimed migration prerequisite."`, `api_contract: "Reference
  target container/HTTP protocol and standard Factory APIs only."`,
  `files: [evals/reference/tool_environment.py,
  tests/reference/test_ftf_1702.py]`, `dependencies: [FTF-0004,
  FTF-1701]` (both CERTIFIED), `shared_lock_required: []`,
  `contract_owner: FTF-1701`.
- **The real gap this task closes (disclosed, not assumed):** FTF-1701
  built the five reference tools and a deterministic business-state
  server, but every test built on top of them until now would have had to
  construct and seed its own `ReferenceBusinessServer` by hand, with no
  shared, tested way to guarantee one qualification case's mutations
  cannot leak into the next. Section 66.3's own canonical runtime adapter
  contract names the exact operation this gap corresponds to: `reset` —
  "destroy/reseed disposable namespace between independent cases." This
  task's own acceptance clause is narrower than the full six-operation
  adapter contract (`describe`/`preflight`/`invoke`/`cancel`/
  `collect_outcome`/`attest`) — it names only the disposable, seeded
  backend itself, disclosed explicitly in the module's own docstring as
  not covering the other six operations, which belong to whichever later
  task wires this environment behind a real runtime adapter.
- **Design (disclosed):** `evals/reference/tool_environment.py`'s
  `ToolEnvironment` owns exactly one `ReferenceBusinessServer` at a time,
  built from a declared `ToolEnvironmentSeed` (tenants, subjects-by-
  tenant, and `(tenant_id, owner_subject_id, title)` record triples — the
  same shape `ReferenceBusinessServer.seed_record` already accepts, no
  competing representation). `reset()` calls the same private `_reseed`
  path the constructor uses: it discards the current server object
  outright and builds an entirely new one, rather than clearing
  collections on the existing instance in place — proven directly by a
  dedicated test that mutates state (creates a note), resets, and
  confirms both that the old record ID no longer resolves and that the
  reseeded record carries a freshly generated ID of its own.
  `dispatch()` is a thin, real caller of FTF-1701's own
  `dispatch_tool_call` — it adds exactly one guard of its own (refusing a
  tenant/subject this environment's own seed spec never declared) before
  delegating, never short-circuiting to a canned result and never
  weakening FTF-1701's own real authorization/idempotency/schema-
  validation behavior underneath it.
- **Path refinement (disclosed):** none — the packet's own declared
  `evals/reference/tool_environment.py`/`tests/reference/
  test_ftf_1702.py` paths were used exactly as given.
- **Contracts:** none touched — `shared_lock_required` is empty and
  `contract_owner` is FTF-1701's own; this task extends no existing
  frozen shape and freezes no new one.
- **Tests:** `tests/reference/test_ftf_1702.py` — 12 no-DB test
  functions: seeded-record resolution by fixture triple (plus a real
  `ToolEnvironmentError` for an unseeded triple), a full five-tool happy
  path through the real dispatcher (search → get → create a note with
  idempotency replay → request an approval → decide it out-of-band
  directly against `env.server` → execute it → confirm the record's real
  post-execution state), cross-tenant IDOR still refused unmodified
  through this environment (no regression from wrapping FTF-1701's own
  dispatcher), the new unseeded-tenant/unseeded-subject guards, reset as
  a genuine disposal (not an in-place clear, both directions tested),
  `reset_count` accounting across two real resets, two independently
  built environments never sharing state, a custom `ToolEnvironmentSeed`
  genuinely honored (its own tenant reachable, `DEFAULT_SEED`'s own
  tenant refused), and `ToolEnvironment()` constructed directly behaving
  identically to the `build_tool_environment()` factory function.
- **Own fault injection (Law 1, two distinct vectors, both reverted
  byte-identical and the suite re-run clean afterward):**
  1. Disabled the unseeded-tenant/subject guard in `dispatch()` (replaced
     both `if` checks with `pass`) — failed with exactly the three named
     checks: "dispatch refuses a tenant this environment's own seed never
     declared", "dispatch refuses a subject never declared for an
     otherwise real, seeded tenant", and "DEFAULT_SEED's own tenant is
     refused when a different seed spec was declared".
  2. Replaced `reset()`'s real discard-and-rebuild body with an in-place
     mutation (reset each seeded record's own `status`/`note_ids` on the
     same server object, same record IDs, `reset_count` still
     incremented) — failed with exactly the three named disposal checks:
     "reset() discards the old server rather than mutating it", "reset()
     refuses to serve the pre-reset record ID", and "reset() assigns a
     freshly generated ID to the reseeded record, never reusing the old
     one".
- **No regression, independently re-run:** `tests/reference/
  test_ftf_1701.py` — "all checks passed" (24/24), unchanged. Full
  `tests/tasks/test_ftf_*.py` sweep (30 files) all "all checks passed" or
  equivalent after `pnpm install --frozen-lockfile` resolved the
  pre-existing, previously-documented node_modules-missing sandbox gap
  (confirmed by re-running `test_ftf_0002.py`/`test_ftf_0003.py`/
  `test_ftf_h001.py` clean immediately afterward — a sandbox artifact,
  not caused by this task). `ruff check .`/`ruff format --check .`/
  `mypy .` clean repo-wide (139 source files; the 2 remaining mypy errors
  are the pre-existing, unrelated `authlib.jose` stub gap, confirmed
  identical on `main`). `scripts/validate_spec.py --strict` clean after
  `git fetch --unshallow origin` (this sandbox's own clone is shallow by
  default). `scripts/product_truth_check.py --fail-on-unwired` — `11/11`
  (unchanged; `proving_symbol` deliberately empty, this task adds no live
  caller into any `SOURCE_ROOTS` path, matching FTF-1701/FTF-1401/
  FTF-1601's own precedent, not inflation). `python3 scripts/status.py` —
  `35/300 certified · 11 product-true` (unchanged — PROVISIONAL, not yet
  CERTIFIED).
- **CI:** one new `gate` job step (`uv run python3
  tests/reference/test_ftf_1702.py`) added to `.github/workflows/ci.yml`
  right after the FTF-1701 step; no new integration job, since this task
  touches no database/network/GPU resource.
- Dependencies FTF-0004/FTF-1701 confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- **Submitted PROVISIONAL, not self-certified (Law 4).**
  `docs/spec-matrix.yaml` FTF-1702 set to `certification_state:
  PROVISIONAL` / `proof_state: L1-PROVEN`, `evidence_hash` set to the
  implementation commit, `proving_test:
  tests/reference/test_ftf_1702.py`. Certified stays 35/300; Product-true
  stays 11/11. Awaiting independent Control-plane L2 review.

## 2026-09-21 — FTF-1702 — record the opened PR link
- **PR:** https://github.com/SJZLab/Sovra/pull/167
  (`claude/wizardly-fermat-hlz51t` → `main`).
## 2026-09-21 — FTF-1301 — CostProfile schema/UI
- **PR:** opened from `claude/wizardly-fermat-togs1a` → `main`, title
  `[relay] FTF-1301: CostProfile schema/UI`.
- **Commit:** `61995ac572eb732392e698f53565a5fec3bf5064` (implementation);
  `docs/spec-matrix.yaml`/`PROGRESS.md`/`QUEUE.md`/this entry pushed as a
  second, tracking-doc-only commit on the same branch, matching the
  established two-commit precedent.
- **Ground truth checked before picking work:** STEP 0 verified
  `sha256sum CLAUDE.md` against the committed `CLAUDE.md.sha256` — clean.
  Found the designated branch already fully merged into `main` (its own
  head, `f3141c4`, an ancestor of `origin/main`; the remote branch no
  longer existed) and restarted it fresh from `origin/main`, per this
  session's own instructions, rather than stacking on already-merged
  history. Cross-checked live GitHub state via `list_pull_requests`
  before picking (not just this repo's own docs): PR #167 ("[relay]
  FTF-1702: Tool environment") had already been opened by a prior shift
  minutes before this one fired — genuinely claimed, not picked again.
  PR #158 (stale FTF-0403 duplicate)/#159 (FTF-1501)/#160 (FTF-1001)/#161
  (FTF-1401)/#162 (unverified FTF-0202 claim) remain open, unmerged,
  Control-plane review items this relay has no standing to act on.
  Recomputed the dependency-ready frontier directly against
  `docs/spec-matrix.yaml`'s live `certification_state`: FTF-0202 stays a
  genuine, unresolved owner ask blocking a large fraction of the graph
  transitively through `FTF-H006`/`FTF-0203`+; FTF-0408 still carries the
  same real DoD-clarity gap the FTF-1701 shift already found (re-
  confirmed by re-pulling `docs/implementation/task-packets/FTF-0408.md`
  fresh — its own acceptance clause reads on `AgentManifest.mandate_json`,
  a field with no real storage anywhere in this repository yet); FTF-0902
  still names a real enforcing Kubernetes/CNI cluster as its own
  `resource_gate` prerequisite, unavailable in this sandbox. FTF-1301 is
  the lowest-task-ID item among what remains genuinely unblocked and
  unclaimed by any open PR: all three of its own dependencies
  (FTF-0401/FTF-0403/FTF-0103) independently confirmed `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- **Task contract:** `python tools/r1_handoff.py packet FTF-1301 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1 ("Freeze the
  minimum shared phase interface and domain schemas... Record dated
  provider/local hardware TCO rates/currency/source and display unknowns
  honestly"), negative_acceptance ("Token price zero is not local compute
  free; unverified/expired rate cannot certify cheapest"),
  `database_changes: "Own forward Alembic migration for this resource"`,
  `api_contract: "/api/v1/projects/{id}/costs/cost-per-success/search;
  /organizations/{id}/cost-profiles."`, `files:
  [apps/web/components/cost-and-model-search/costprofile_schema_ui.tsx,
  apps/web/tests/ftf_1301.spec.ts,
  packages/contracts/openapi/13-interface.yaml,
  packages/contracts/jsonschema/13-domain.schema.json]`, `dependencies:
  [FTF-0401, FTF-0403, FTF-0103]` (all three CERTIFIED),
  `shared_lock_required: [contracts:13, migration:application]`.
- **Path/file-set refinement (disclosed, not hidden):** the packet's own
  `files` list names only frontend + contract paths, no `apps/api` path
  — but acceptance clause 1 and the `database_changes` charter genuinely
  need a real table, so an `apps/web`-only file set cannot satisfy them.
  Added `apps/api/app/costs/` (schema/crud/routes) and
  `apps/api/alembic/versions/0008_cost_profiles.py`, the same class of
  refinement FTF-0403's own `budget_policy_schema.py` already disclosed
  for its own packet (citing FTF-0305's precedent for exactly this move).
- **Design (disclosed):** `CostProfile` is an organization-scoped,
  **immutable** rate observation (a new dated observation is always a new
  row, never an edit of an old one — "Record dated ... rates," plural,
  read as a real price history, the same reasoning FTF-0404's own
  `ToolDefinition` already applied, not FTF-0403's own single-mutable-row
  `BudgetPolicy` pattern). The one field that ever changes after creation
  is `verified_at` (`verify_cost_profile`). `currency` reuses FTF-0403's
  own already-CERTIFIED `ALLOWED_CURRENCIES`/`validate_currency` directly
  (no duplicate ISO-4217 vocabulary). `is_certifiable_cheapest`
  (`cost_profile_schema.py`) is this task's own negative_acceptance made
  real: `(1) expired` — `expires_at` set and passed; `(2) unverified` —
  `verified_at is None`; `(3) zero_provider_rate_unconfirmed` — a
  `provider` rate of exactly zero, which never certifies **even once
  verified** (a structural rule, not a staleness one — "Token price zero
  is *not* local compute free" reads as absolute, not "until verified").
  A verified, unexpired `local_hardware` zero rate DOES certify, proving
  the zero-rate rule is provider-specific, not a blanket refusal —
  covered by both a no-DB positive test and a live-database proof.
  `search_cost_profiles` resolves a caller-named `project_id` to its own
  organization (IDOR-checked identically to every other project lookup
  in this repository), then returns every matching `CostProfile` in that
  organization's rate card, each annotated with its own certifiability
  verdict — "display unknowns honestly" read as: a disqualified rate is
  still returned with its own named reason, never silently dropped;
  `current_cheapest_certified` is `null`, never a fabricated fallback,
  when nothing yet qualifies.
- **Scope disclosed, not hidden:** this task does NOT compute an actual
  cost-per-success figure against real usage/billing data — that full
  Section-32 pipeline (`provider_call_cost`/`window_self_hosted_cost`/
  `cost_per_success`) is FTF-1304's own, later, separately
  dependency-gated task (`docs/TASK_INDEX.md` records FTF-1302 "Local
  compute costing"/FTF-1303 "Provider token costing"/FTF-1304
  "Cost-per-success calculator" as this task's own dependents, all three
  naming FTF-1301 as their own dependency, never the reverse).
- **Real, live-wired:** `app.main` now includes
  `build_cost_profile_router` (the eighth live-wired product-runtime path
  in this repository), gated to `organization_admin` alone for every
  route including reads and the search endpoint — Section 45's own
  literal role table names "cost profiles" explicitly ("organization_
  admin: connections, security profiles, cost profiles"), the same
  "admin-only, including reads" precedent FTF-0201's own `connection_
  routes.py` already applied to "connections," named in the identical
  sentence. Real forward Alembic migration
  (`0008_cost_profiles.py`) FORCEs row-level security scoped to the same
  `app.current_organization_id` tenant context every prior migration
  already established, grants `sovra_app` SELECT/INSERT/UPDATE only
  (never DELETE), and adds a real `CHECK(rate_amount >= 0)` constraint.
  Freezes `packages/contracts/{openapi,jsonschema}/13-*` (contract slot
  13, first claim).
- **Own genuine mid-implementation failures, fixed before any test was
  written to expect them (direct evidence the tests exercise real code):**
  (1) `_serialize`'s own decimal-to-string quantization used
  `str(Decimal)`, which Python switches to scientific notation for this
  column's own real small-magnitude range (a $0.00000015-per-token rate)
  — the no-DB round-trip-shape test genuinely failed on first run; fixed
  with `format(value, "f")`, matching `budget_policy_routes.py`'s own
  `_money` helper's plain-decimal guarantee. (2) the live search route
  computed `current_cheapest_certified` from detached ORM instances
  *after* their owning `tenant_scoped_session_for` block had already
  closed, raising a real `sqlalchemy.orm.exc.DetachedInstanceError`
  against the real database on first run; fixed by moving that
  computation inside the session block.
- **Failure output shown by fault injection (Law 1, two distinct vectors,
  both reverted byte-identical and the suite re-run clean afterward):**
  (1) disabled the zero-provider-rate disqualification in
  `is_certifiable_cheapest` (`if False and profile.profile_type ==
  "provider" and ...`) — `uv run python3 tests/tasks/test_ftf_1301.py`
  failed with exactly the named negative_acceptance check ("token price
  zero is not local compute free... still cannot certify cheapest"); (2)
  disabled the negative-rate refusal in `validate_rate_amount` (`if
  False and value < 0:`) against the real live database — `uv run
  python3 tests/integration/test_ftf_1301_cost_profile.py` failed with
  exactly the named "a negative rate is refused (400)" check, and the
  malformed row was independently caught a second time by the real,
  migration-defined `CHECK(rate_amount >= 0)` constraint itself (a raw
  `IntegrityError` surfaced as an uncaught 500 through the route,
  proving the DB-level backstop is live even when the application-level
  guard is bypassed — the same defense-in-depth class FTF-0403's own
  fault injection already demonstrated). Both vectors reverted
  byte-identical (`diff` against the pre-injection copy confirmed empty)
  and both suites re-confirmed clean afterward.
- **No regression, independently re-run:** full `tests/tasks/
  test_ftf_*.py` sweep (25 files) all "all checks passed" or equivalent
  (after `pnpm install --frozen-lockfile` closed a pre-existing,
  previously-undocumented frontend-dependency gap in this sandbox —
  confirmed pre-existing, not caused by this task, by `test_ftf_0003.py`/
  `test_ftf_0002.py` both failing identically before and passing clean
  immediately after, with no other change); `ruff check .`/`ruff format
  --check .` clean repo-wide; `mypy .` (from `apps/api`) — 2 errors, both
  the pre-existing, unrelated `authlib.jose` missing-stub gap in
  `oidc_login.py`, confirmed identical on `main`; `pnpm run
  lint`/`typecheck`/`format:check` clean; `pnpm run test` (vitest) —
  19/19 passed across all 5 spec files, no regression to FTF-0401's own
  suite. `git fetch --unshallow origin` first (this sandbox's own
  documented shallow-clone artifact), then `scripts/validate_spec.py
  --strict` clean. `scripts/product_truth_check.py --fail-on-unwired`
  rises to `12/12` (`proving_symbol` set to `is_certifiable_cheapest`,
  called for real by `cost_profile_crud.py`'s own `search_cost_profiles`
  — a real, newly-wired live caller, not inflation). `python3
  scripts/status.py`'s own stricter, CERTIFIED-only count stays `35/300
  certified · 11 product-true` (unchanged — PROVISIONAL, not yet
  CERTIFIED), matching FTF-0403's own precedent for this same
  CERTIFIED-vs-PROVISIONAL count distinction.
- **CI:** one new `gate` job step (`uv run python3
  tests/tasks/test_ftf_1301.py`) plus a new dedicated
  `cost-profile-integration` job (real `services: postgres` container,
  same pattern as `budget-policy-integration`) added to
  `.github/workflows/ci.yml` — no other job's steps touched. Confirmed
  the workflow file still parses as valid YAML after the edit.
- Dependencies FTF-0401/FTF-0403/FTF-0103 confirmed genuinely `CERTIFIED`
  in `docs/spec-matrix.yaml` before relying on them.
- **Submitted PROVISIONAL, not self-certified (Law 4).**
  `docs/spec-matrix.yaml` FTF-1301 set to `certification_state:
  PROVISIONAL` / `proof_state: L1-PROVEN`, `evidence_hash` set to the
  implementation commit, `proving_symbol: is_certifiable_cheapest`,
  `proving_test: tests/tasks/test_ftf_1301.py`. Certified stays 35/300;
  Product-true rises to 12/12. Awaiting independent Control-plane L2
  review.

## 2026-09-21 — Control plane: FTF-1301 (PR#168) independently reviewed, CERTIFIED
Ran the CERTIFY procedure end to end (docs/CONTROL_PLANE_MODES.md),
triggered by the `pull_request.opened` webhook for PR #168.

- **Session-independence check:** this reviewing session's own ID is
  `session_01GWAaJpcZSaNqKJ9zmk2CDz`; both commits on PR #168
  (`61995ac`, `cadb320`) carry `Claude-Session: .../
  session_0175jrFsfGmpVqpjJFqPTiAE` — no match, independent per Law 4.
- **CI checked on the real head, not a stale claim:** the webhook fired
  at PR-open time, before CI had finished (`gate` still `queued` on the
  first check) — polled `get_check_runs` to completion rather than
  trusting a self-report. All 18 real GitHub Actions check runs
  `completed`/`success` on the real final head
  (`cadb3202103ab3c56811af722406bfb1bac38eeb`, run `35631327707`), `gate`
  and the new `cost-profile-integration` job both included.
- **Merge:** the PR was still open/unmerged at review time (the relay's
  own shift had not yet reached its own merge-on-green step before this
  webhook fired). Merged it directly (`merge_pull_request`,
  `expectedHeadSha` pinned to the confirmed green head) — pre-authorized
  under CLAUDE.md's "Do without asking: ... merge on green + L1 doc
  (PROVISIONAL)". Merging is a distinct step from certifying (Law 4
  governs the latter, not the former) and does not substitute for the
  independent review below.
- **Diff read directly:** `apps/api/app/costs/cost_profile_schema.py`,
  `cost_profile_crud.py`, `cost_profile_routes.py`,
  `apps/api/alembic/versions/0008_cost_profiles.py`,
  `apps/web/components/cost-and-model-search/costprofile_schema_ui.tsx`,
  `packages/contracts/{openapi,jsonschema}/13-*`. Confirmed
  `is_certifiable_cheapest`'s three disqualifying conditions (expired,
  unverified, zero-provider-rate) match the negative_acceptance clause
  exactly, including that a verified, unexpired `local_hardware` zero
  rate is NOT disqualified; confirmed `validate_rate_amount` refuses
  negative amounts at the application layer; confirmed the migration
  FORCEs row-level security, grants `sovra_app` SELECT/INSERT/UPDATE only
  (no DELETE), and adds a real `CHECK(rate_amount >= 0)` constraint;
  confirmed every route (including reads and search) is gated to
  `organization_admin` alone, matching Section 45's literal role table;
  confirmed `app.main` includes `build_cost_profile_router` for real.
- **Task contract re-pulled directly:** `python tools/r1_handoff.py
  packet FTF-1301 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — the
  committed `docs/implementation/task-packets/FTF-1301.md` matched.
  Acceptance clause 1 ("Record dated provider/local hardware TCO
  rates/currency/source and display unknowns honestly") and
  negative_acceptance ("Token price zero is not local compute free;
  unverified/expired rate cannot certify cheapest") both confirmed met
  by the code above.
- **Own reproduction, this sandbox** (`uv sync --all-packages`, `pnpm
  install`, `git fetch --unshallow`, a real directly-installed
  PostgreSQL 16 cluster — no container-registry egress in this sandbox,
  migrated by the integration script itself via
  `SOVRA_DATABASE_URL`/`SOVRA_APP_DB_PASSWORD`): `tests/tasks/
  test_ftf_1301.py` and `tests/integration/test_ftf_1301_cost_profile.py`
  against the live database both — "all checks passed".
- **Own fault injection, reproducing both of the producer's own vectors,
  both reverted byte-identical (`diff` confirmed empty) and the suites
  re-confirmed clean afterward:**
  1. Disabled the zero-provider-rate disqualification in
     `is_certifiable_cheapest` — `tests/tasks/test_ftf_1301.py` failed
     with exactly the named negative_acceptance check, exit code 1.
  2. Disabled the negative-rate application-layer guard in
     `validate_rate_amount` against the real live database —
     `tests/integration/test_ftf_1301_cost_profile.py` failed with
     exactly the named "a negative rate is refused (400)" check; the raw
     INSERT reached the database and was independently refused a second
     time by the real `CHECK(rate_amount >= 0)` constraint
     (`sqlalchemy.exc.IntegrityError`), confirming the defense-in-depth
     claim is real, not asserted.
- **No regression, independently re-run:** full `tests/tasks/
  test_ftf_*.py` sweep all "all checks passed" or equivalent, after
  `pnpm install` resolved a pre-existing, undocumented
  frontend-dependency gap in this sandbox affecting
  FTF-0002/FTF-0003/FTF-H001 (confirmed a sandbox artifact, not a
  regression); `ruff`/`mypy`/`pnpm lint+typecheck+format` all clean
  repo-wide (2 pre-existing, unrelated `authlib.jose` mypy stub gaps
  only); `pnpm run test` (vitest) — 19/19 passed across all 5 spec
  files.
- **Static checks re-run clean:** `scripts/validate_spec.py --strict`
  clean after `git fetch --unshallow` (18 `stale_commit` defects present
  before unshallowing, none after). `scripts/product_truth_check.py
  --fail-on-unwired` — own run, `12/12` product-true;
  `is_certifiable_cheapest` confirmed a genuine, non-test live caller in
  `cost_profile_crud.py`'s own `search_cost_profiles` (grepped directly
  — not 0/0, not inflation).
- Dependencies FTF-0401/FTF-0403/FTF-0103 confirmed genuinely `CERTIFIED`
  in `docs/spec-matrix.yaml` before relying on them.
  `shared_lock_required: contracts:13` confirmed as the first and only
  claim on that contract slot.
- **No blocking defect found.** Merged PR #168 (merge commit
  `9a47458118ee46095270f4aa9758b5ec35efeea5`). `docs/spec-matrix.yaml`
  set to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit. Ran `sync_certified_status()`
  to write the real status into `spec/tasks.json`/
  `docs/implementation/TRACKER.json` — `['FTF-1301']` updated. Certified:
  36/300, Product-true: 12/12.
- **Out of scope, flagged not silently dropped:** PR #159/#160/#161/#167
  (FTF-1501/FTF-1001/FTF-1401/FTF-1702) remain genuinely open and
  PROVISIONAL with no `docs/certifications/queue.md` entry of their own
  — not reviewed this shift (this shift's trigger and scope was PR #168
  only); left flagged in QUEUE.md for a future Control-plane pass.

Full entry: docs/certifications/ledger.md.

## 2026-09-21 — FTF-1302 — Local compute costing
- **PR:** https://github.com/SJZLab/Sovra/pull/170
  (`claude/wizardly-fermat-c7g9v1` → `main`), title
  `[relay] FTF-1302: Local compute costing`.
- **Commit:** `2ebf9788ac644fc250e8cc9bc621a42815b549f7` (implementation);
  `docs/spec-matrix.yaml`/`PROGRESS.md`/`QUEUE.md`/this entry pushed as a
  second, tracking-doc-only commit on the same branch, matching the
  established two-commit precedent.
- **Ground truth checked before picking work:** STEP 0 verified
  `sha256sum CLAUDE.md` against the committed `CLAUDE.md.sha256` — clean.
  Designated branch `claude/wizardly-fermat-c7g9v1` did not exist on the
  remote yet (fresh from `origin/main`, nothing to restart). Cross-checked
  live GitHub state via `list_pull_requests` before picking, not just this
  repo's own docs: six PRs were open and unmerged — #158 (stale FTF-0403
  duplicate, superseded by the already-merged PR #163), #159 (FTF-1501),
  #160 (FTF-1001), #161 (FTF-1401), #162 (unverified FTF-0202 claim), #167
  (FTF-1702) — all genuine Control-plane review items this relay shift has
  no standing to act on. Recomputed the dependency-ready frontier fresh by
  parsing every non-CERTIFIED task's own `deps=` field in
  `docs/spec-matrix.yaml`'s `notes` against the live `certification_state`
  set (not trusted from any prior shift's note by itself): FTF-0202
  (owner ask, still open — see QUEUE.md "Owner actions — current"),
  FTF-0408 (same real `AgentManifest.mandate_json` DoD-clarity gap prior
  shifts already found and disclosed), FTF-0902 (same real enforcing
  Kubernetes/CNI `resource_gate` prerequisite, unavailable in this
  sandbox), FTF-1001/FTF-1401/FTF-1501/FTF-1702 (already claimed by the
  open PRs above), FTF-1302 and FTF-R016 (both newly dependency-ready now
  that FTF-1301 reached CERTIFIED, neither claimed by any open PR).
  Pulled both newly-ready packets fresh
  (`tools/r1_handoff.py packet FTF-1302`/`FTF-R016`) before choosing:
  FTF-R016 ("Measure selected coding-model/executor pilot cost") is
  build-tooling metadata about the coding executor's own pilot run, not a
  product feature, and its own acceptance clause ("Measure one
  backend/UI/integration task plus parallel run with real tokens/CPU/
  CI/retries") is self-referential to a live multi-worker parallel
  implementation session this relay shift is not — picking it would risk
  exactly the kind of DoD improvisation this relay does not do; not
  picked, disclosed rather than silently skipped. FTF-1302's own
  `resource_gate` field is the same generic boilerplate string already
  present on FTF-1301/FTF-0403's own packets (confirmed by direct `grep`
  comparison) — those two tasks were fully implementable in this sandbox
  without any live GPU despite carrying the identical field text, unlike
  FTF-0902 where the *acceptance clause itself*, not just the boilerplate
  resource_gate field, names a live enforcing cluster with no narrower
  reading possible. FTF-1302 is the lower-task-ID, genuinely unblocked,
  unclaimed item with a tractable narrowing — picked this shift.
- **Task contract:** `python tools/r1_handoff.py packet FTF-1302 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance ("Allocate real total
  service-window GPU/CPU/storage/idle cost to declared completed
  workload"), negative_acceptance ("Per-request overlapping GPU seconds
  cannot double count batches or omit idle/multi-replica capacity"),
  `database_changes: "No independent schema change. Consume frozen
  contract; any required change is a separately claimed migration
  prerequisite."`, `api_contract: "/api/v1/projects/{id}/costs/
  cost-per-success/search; /organizations/{id}/cost-profiles."` (FTF-1301's
  own already-frozen contract slot 13; `shared_lock_required: []` — this
  task claims no new lock), `files: [apps/api/app/services/
  local_compute_costing.py, tests/tasks/test_ftf_1302.py]`,
  `dependencies: [FTF-0006, FTF-1301]` (both CERTIFIED),
  `contract_owner: FTF-1301`.
- **Scope disclosed, not hidden:** there is no GPU worker, Temporal
  workflow, or resource-lease table anywhere in this repository yet to
  auto-emit real per-request GPU-second telemetry from (FTF-0901's own,
  later, separately dependency-gated GPU worker family). This module
  therefore takes a **declared**, already-reconciled real service-window
  bill (`ServiceWindow.total_billed_gpu_cpu_ram_cost` — itself the real
  bill, inclusive of idle time and every replica's own capacity, never
  re-derived from request-level data) and a declared workload
  (`RequestUsage`, Section 69.2's own closed terminal-outcome
  vocabulary), the same "no fake metrics; a real, disclosed, entered
  number, not an auto-scraped one" pattern FTF-1301's own `CostProfile`
  rate entry already established.
- **Design:** `window_total_cost` sums Section 32's own four named cost
  categories exactly once each. `unique_successful_task_ids` dedupes
  retries of the same `request_id` to a single success, per Section
  69.2's own "multiple turns/repeated seeds ... are not independent
  tasks" rule. `cost_per_success` divides the real window total by the
  unique success count, raising explicitly on zero successes (Section 32:
  "undefined/infinite," never a silent $0). The negative_acceptance is
  enforced by construction, not a best-effort filter:
  `cost_per_success`/`window_total_cost` never sum, integrate or
  otherwise derive a dollar figure from `RequestUsage.gpu_seconds_start`/
  `gpu_seconds_end` at all — there is structurally no code path here
  capable of re-summing continuously-batched, overlapping per-request GPU
  seconds into a second, larger total (the double-count failure mode),
  nor one capable of crediting only the replica-seconds some request
  happened to touch (the "omit idle/multi-replica capacity" failure
  mode). `RequestUsage.gpu_seconds_start`/`gpu_seconds_end`/`replica_id`
  are still real, typed fields a caller must declare — they are simply
  never load-bearing for the cost figure itself.
- **15 no-DB test functions** (`tests/tasks/test_ftf_1302.py`):
  construction guards for both `ServiceWindow`/`RequestUsage` (negative
  amounts, non-Decimal amounts, non-positive window/interval, unknown
  `task_outcome`, empty `request_id`, non-positive `replica_count`);
  `window_total_cost`'s own four-category sum; `unique_successful_
  task_ids`'s own outcome-filter and retry-dedup behavior; Section 32's
  own literal golden example ($12 GPU bill including idle, $2 CPU, $1
  other cost, 100 successful tasks → exactly $0.15 per success); a
  positive proof that fully overlapping per-request GPU-second intervals
  do not change `cost_per_success`; and the negative_acceptance case
  (zero successes raises explicitly, never $0).
- **Failure output shown by fault injection (Law 1, two distinct vectors,
  both reverted byte-identical and the suite re-confirmed clean
  afterward):** (1) replaced `cost_per_success`'s own real window-total
  division with exactly the naive "sum each request's own GPU-second
  interval at a bill-implied per-second rate" mistake this task's own
  negative_acceptance forbids — `uv run python3
  tests/tasks/test_ftf_1302.py` failed with 6 named checks across the
  golden example, the overlap case, and both dedicated fault-injection
  comparison tests, all producing the wrong dollar figure; (2) disabled
  the non-negative-amount guard in `_validate_non_negative_decimal` (`if
  False and value < 0:`) — failed with exactly the named "storage_cost
  must not be negative" construction-guard check. Both vectors reverted
  byte-identical (`diff` against the pre-injection copy confirmed empty)
  and both re-confirmed clean afterward.
- **No regression, independently re-run:** full `tests/tasks/
  test_ftf_*.py` sweep (32 files, all "all checks passed" or equivalent)
  after `uv sync --all-packages`/`pnpm install --frozen-lockfile` (this
  sandbox's own pre-existing, undocumented-until-now dependency gap —
  confirmed pre-existing, not caused by this task) and `git fetch
  --unshallow origin` (this sandbox's own documented shallow-clone
  artifact — `scripts/validate_spec.py --strict` genuinely reported 18
  `stale_commit` defects before unshallowing, none after). `uv run ruff
  check .`/`uv run ruff format --check .`/`uv run mypy .` clean
  repo-wide (146 source files) — confirmed the pinned `uv run` tool
  versions are what CI actually runs after first reproducing false-
  positive mismatches against this sandbox's own newer, globally-
  installed `ruff 0.15.8`/a stray `mypy` picking up `alembic`'s own
  runtime-generated `op`/`context` attributes as missing; resolved by
  using `uv run ruff`/`uv run mypy` throughout, matching the project's
  own pinned `pyproject.toml` versions (`ruff>=0.6.9,<0.7`). `pnpm run
  lint`/`typecheck`/`format:check` clean; `pnpm run test` (vitest) —
  19/19 passed across 5 spec files, no regression. `scripts/status.py`
  stays `36/300 certified · 12 product-true` (unchanged — FTF-1302 is
  PROVISIONAL, not yet CERTIFIED). `scripts/product_truth_check.py
  --fail-on-unwired` stays `12/12` (unchanged, as disclosed above — this
  task's own `proving_symbol` is genuinely empty, no live HTTP route
  added; matches the FTF-1401/FTF-1601/FTF-1701/FTF-1810 precedent for a
  computation module with no live product-runtime caller in this
  repository yet).
- **CI:** one new `gate` job step (`uv run python3
  tests/tasks/test_ftf_1302.py`) added to `.github/workflows/ci.yml` — no
  dedicated integration job needed (no database, network egress, or
  GPU/external resource of any kind is touched anywhere in this task) —
  no other job's steps touched.
- Dependencies FTF-0006/FTF-1301 confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- **Submitted PROVISIONAL, not self-certified (Law 4).**
  `docs/spec-matrix.yaml` FTF-1302 set to `certification_state:
  PROVISIONAL` / `proof_state: L1-PROVEN`, `evidence_hash` set to the
  implementation commit, `proving_symbol: ''` (deliberately empty, no
  live route added), `proving_test: tests/tasks/test_ftf_1302.py`.
  Certified stays 36/300; Product-true stays 12/12. Awaiting independent
  Control-plane L2 review.
### Control plane review — 2026-09-21 — FTF-1302 (PR #170) — NOT CERTIFIED (CI cannot show a genuine pass; GitHub Actions runner-provisioning failure, not a code defect)
- **Trigger:** scheduled Control-plane certifier shift, `pull_request.opened`
  webhook for PR #170 (https://github.com/SJZLab/Sovra/pull/170,
  `claude/wizardly-fermat-c7g9v1` → `main`).
- **Session-ID check:** every commit on PR #170 (`2ebf978`, `2e0065d`,
  `a631d11`, and two later pushes `718a908`/`9faf7ea` the producer made
  while this review was already in progress) carries `Claude-Session:
  .../session_01Fy9YNo5oaA2HZLWRud1Xma`; this reviewing session's own ID
  is `session_01U9fapCAPqWfq9QZeRdwpkM` — no match, independent per Law 4.
- **Diff read directly:** the implementation commit (`2ebf978`) touches
  exactly the two files the task packet's own `files` list names
  (`apps/api/app/services/local_compute_costing.py`,
  `tests/tasks/test_ftf_1302.py`) plus one new `gate` step in
  `.github/workflows/ci.yml` — no scope creep.
- **Own reproduction, this sandbox (workspace was pre-populated with this
  exact PR content; `git diff` against `origin/claude/wizardly-fermat-
  c7g9v1` showed only two cosmetic PR-link-text lines, confirmed
  byte-identical otherwise):** `uv run python3 tests/tasks/test_ftf_1302.py`
  → "all checks passed". Own fault injection, both vectors, distinct
  execution from the producer's own (I edited the shipped module directly
  rather than trusting the producer's own account): (1) replaced
  `cost_per_success`'s real window-total division with the naive
  per-request GPU-second-summing mistake — 5 checks failed for real
  (`uv run python3 tests/tasks/test_ftf_1302.py` exit 1); (2) disabled the
  non-negative-amount guard (`if False and value < 0:`) — 1 named check
  failed for real. Both reverted byte-identical (`diff` against the
  pre-injection copy empty) and the suite re-confirmed clean immediately
  after each. Full sweep `tests/tasks/test_ftf_*.py` (32 files, including
  `test_ftf_0002`/`test_ftf_0003`/`test_ftf_h001` after `pnpm install
  --frozen-lockfile` + `git fetch --unshallow` closed this sandbox's own
  pre-existing, already-documented setup gaps) — all clean. `uv run ruff
  check .` / `uv run ruff format --check .` / `uv run mypy .` clean
  repo-wide (146 files); `pnpm run lint`/`typecheck`/`format:check` clean;
  `pnpm run test` (vitest) 19/19. `scripts/validate_spec.py --strict`
  clean. `scripts/product_truth_check.py --fail-on-unwired` → real `12/12`
  (unchanged — `proving_symbol` genuinely empty, a disclosed 0/0, not
  inflation, matching the FTF-1401/FTF-1601/FTF-1701/FTF-1810 precedent).
  `scripts/status.py` → `36/300 certified · 12 product-true` (unchanged).
- **Task contract re-pulled directly:** `python tools/r1_handoff.py packet
  FTF-1302 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance
  ("Allocate real total service-window GPU/CPU/storage/idle cost to
  declared completed workload") and negative_acceptance ("Per-request
  overlapping GPU seconds cannot double count batches or omit idle/
  multi-replica capacity") both confirmed met by the fault-injection
  evidence above; `files`/`database_changes`/`api_contract` all match the
  shipped diff exactly. Dependencies FTF-0006/FTF-1301 confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- **The shipped code and its evidence are sound — this review's finding
  is not a code defect.** What blocks certification is that GitHub
  Actions is not provisioning runners for this repository at all, so no
  genuine passing CI conclusion exists to confirm on the real head:
  `get_check_runs` on PR #170's original head (`a631d11`) showed 11/18
  jobs (`gate` included) `completed`/`failure` with no `steps` array at
  all and 2–12s completion times, vs. 2m12s for `gate`'s real full run on
  PR #168 minutes earlier; `get_job_logs` returned HTTP 404 for every
  failed job (no runner was ever assigned, so no log exists). I
  independently triggered `rerun_failed_jobs` on that run myself (before
  discovering the producer had already used, and disclosed using, this
  repo's own one-re-run CI-red allowance) — by the time it completed the
  PR head had advanced to a documentation-only commit (`9faf7ea`), and
  the resulting rerun showed **18/18** jobs failing identically fast, ruling
  out ordinary matrix concurrency as the explanation. This independently
  reproduces and confirms the producer's own finding, already correctly
  recorded as the sole "Owner actions — current" item in QUEUE.md on PR
  #170's own branch (added by the producer session itself, not by this
  review) and posted as a PR comment
  (https://github.com/SJZLab/Sovra/pull/170#issuecomment-5766005518).
  Per CLAUDE.md's "Owner actions live in ONE place" rule, that ask is not
  yet visible on `main` (PR #170 is unmerged) — a separate small,
  doc-only PR from this review lands the same ask on `main` directly
  (crediting the producer's finding, adding this review's own independent
  confirmation), matching the FTF-0202/OpenBao precedent
  (`docs/certifications/queue.md`, 2026-09-21) for the same
  branch-visibility gap.
- **Not merged, not certified, per Law 1/Law 4.** `docs/spec-matrix.yaml`
  for FTF-1302 is not yet `PROVISIONAL` on `main` — PR #170 is unmerged,
  so `main` still shows `certification_state: NONE` for FTF-1302; nothing
  to downgrade there. On PR #170's own branch the PROVISIONAL entry stands
  as submitted — not altered by this review, since the shipped code is
  sound; what's missing is a genuine passing CI run, which needs the
  GitHub Actions runner-provisioning issue resolved first. PR #170 stays
  open, unmerged. **Action needed before re-requesting certification:**
  once GitHub Actions is confirmed provisioning runners again (owner
  action, tracked in QUEUE.md "Owner actions — current"), re-run PR #170's
  CI to get a genuine passing conclusion on the real head, then a fresh
  Control-plane review can certify on that basis alone — no code change
  is expected or needed.
- **Validated-by:** this session (independent — see session-ID check
  above), https://claude.ai/code/session_01U9fapCAPqWfq9QZeRdwpkM,
  2026-09-21.

## 2026-09-21 — FTF-R016 — Measure selected coding-model/executor pilot cost and enforce budget tree
- **PR:** https://github.com/SJZLab/Sovra/pull/172 (claude/wizardly-fermat-dtzhzo → main)
- **Commit:** c4f55353f6ffe712f3149cc5853445dfa3ccc2e0
- **Deps:** FTF-R015, FTF-H002, FTF-0403 — all confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- **Test:** tests/implementation/test_executor_budget_limits.py — 34 checks
- **Scope:** `tools/implementation/usage_ledger.py` (`UsageMeasurement`,
  `accepted_from_wave`, `measure_real_command`, `execute_with_real_retries`,
  `cost_per_success`, `compare_to_baseline`, `BudgetNode`/`BudgetExceeded`,
  `run_within_budget`/`BoundedTermination`,
  `enforce_model_route_policy`/`ExternalRoutePolicyViolation`),
  `scripts/report_executor_pilot.py` (runs acceptance clause 1's one real
  backend task plus one real parallel wave end to end), one new `gate`
  step in `.github/workflows/ci.yml`, and the matching
  `spec/verification-map.json` entry. Build/implementation tooling only —
  no Factory production package (`apps/api`, any `workers/*`) imports
  anything here, matching FTF-R014/R015's own disclosed scope.
- **Dependency-frontier bug caught before implementing anything:** this
  shift's own throwaway script for recomputing the dependency-ready
  frontier initially mis-parsed `docs/spec-matrix.yaml`'s `notes:` field
  when it YAML-folds `deps=...` onto a second line, silently treating it
  as `deps=none`. That falsely made FTF-R006 (whose real dependencies —
  FTF-R002/FTF-0500/FTF-0506/FTF-0516 — are all still `NONE`) look
  dependency-ready. Caught by cross-checking the task packet's own
  `dependencies` list directly against `docs/spec-matrix.yaml` before
  writing any code; the script was fixed and FTF-R016 (genuinely
  dependency-ready, no open PR naming it) was picked instead.
- **Resource-gate disclosure (Section 32/64.4's own "explicit unknown cost
  state" clause, this task's own `api_contract`):** this sandbox exposes
  no live token-usage-reporting API to the executing coding-agent session
  — no tool surfaces a per-request token count back to this process.
  `token_cost_usd` is therefore always `None` (UNKNOWN), the same class of
  disclosed sandbox limitation FTF-R015 already recorded for true
  multi-host dispatch. `cost_per_success` propagates that `None` rather
  than ever substituting `Decimal("0")` — this task's own named
  negative_acceptance ("missing usage counted free") made structurally
  impossible by construction.
- **Two genuine first-run failures before any check was declared passing
  (Law 1):** (1) the first draft's parallel-wave fixture wrote marker
  files named `a.marker`/`b.marker`; `TaskOrchestrator`'s own real
  integrated-CI step expects `{task_id}.marker`, so the mismatch made the
  real integrated-CI check genuinely fail closed (`approved_for_downstream`
  reset to the empty set) — caught directly by the "the passing task is
  independently accepted" check, fixed by using the correct marker
  filenames. (2) The fault-injection probe for the unknown-cost guard
  initially crashed (`TypeError` summing `Decimal + None`) instead of
  demonstrating the vulnerability, correctly flagged as not matching the
  expected outcome — fixed by rewriting the guarded arithmetic to
  `m.token_cost_usd or Decimal("0")`, so removing the guard produces
  exactly the silent-zero-fallback the negative_acceptance names.
- **Own fault injection (Law 1, reverted byte-identical, suite re-run
  clean afterward):** removed the `if any(m.has_unknown_cost() for m in
  accepted): return None` guard from the tracked `cost_per_success` — a
  fresh subprocess wrongly computed a numeric (zero-substituted) cost from
  a measurement with unknown `token_cost_usd`, caught by the exact named
  "missing usage counted free" check; restoring the file byte-identical
  (`diff` confirmed empty) made it correctly report `None` again.
- **Other real, non-simulated behavior proven directly:** `BudgetNode`'s
  delegated tree — a child's own large declared `limit_usd=100` cannot
  escape its parent's real remaining balance ($2 of $10 after an $8
  reservation), `reserve()` raises `BudgetExceeded` for the excess
  ("budget reset per subagent" refused by construction); `run_within_budget`
  genuinely `SIGTERM`s a real 5-second-sleeping child process once a tiny
  ($0.05) budget is reached, confirmed by a marker file (written only
  after the full sleep) being genuinely absent; `execute_with_real_retries`
  against a real flaky command (a persisted counter file) reports
  `retries == 2` for a command needing two real failed attempts before
  succeeding, and correctly exhausts `max_retries` for a command that
  always fails; `enforce_model_route_policy` denies a real external URL
  under `private_mode=True` and allows an approved internal route; no
  hardcoded `$100`/`$300`/"guarantee" figure anywhere in either new file.
- **Regression:** unshallowed this sandbox's own shallow clone, ran
  `uv sync --all-packages`/`pnpm install --frozen-lockfile` (both
  pre-existing environment gaps, closed not silently worked around —
  confirmed pre-existing by the full `tests/tasks/test_ftf_*.py` sweep
  failing identically on missing `uvicorn`/`sqlalchemy`/stale-commit
  errors before, and passing clean immediately after, with no other
  change). Full `tests/test_ftf_0001_monorepo_layout.py` +
  `tests/tasks/test_ftf_*.py` + `tests/implementation/test_*.py` +
  `tests/reference/test_ftf_1701.py` sweep (34 files) all clean.
  `ruff check .`/`ruff format --check .` clean; `uv run mypy .` clean
  (147 source files, 0 errors). `pnpm run lint`/`typecheck`/`format:check`
  clean (this task touches no frontend file). `validate_spec.py --strict`
  clean; `scripts/verify_task.py --task FTF-R016 --stage task` — PASS
  against evidence commit `c4f5535`. `product_truth_check.py
  --fail-on-unwired` stays `12/12` (`proving_symbol` deliberately empty —
  build tooling, not a served product route, matching the
  FTF-R014/R015/R017 precedent). `scripts/status.py` — `36/300 certified
  · 12 product-true` (unchanged — PROVISIONAL, not yet CERTIFIED).
- **CI-runner-provisioning owner-ask (see QUEUE.md "Owner actions —
  current"):** re-confirmed still genuinely open as of this shift (PR
  #170's checks and the control plane's own 19:17Z re-confirmation both
  still `failure`/`runner_id: 0`; no workflow run since). Not re-triggered
  a third time — already independently re-confirmed twice. A genuine
  passing CI conclusion on this PR's own head is expected to be blocked by
  the same issue until it clears; that is an infrastructure gap, not a
  defect in this submission.
- **State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
  self-certified — awaiting Control plane L2 review per Law 4.
### Control plane review — 2026-09-21 — FTF-R016 (PR #172) — NOT CERTIFIED (CI cannot show a genuine pass; same GitHub Actions runner-provisioning failure as PR #170, not a code defect)
- **Trigger:** scheduled Control-plane certifier shift, `pull_request.opened`
  webhook for PR #172 (https://github.com/SJZLab/Sovra/pull/172,
  `claude/wizardly-fermat-dtzhzo` → `main`).
- **Session-ID check:** all three commits on PR #172 (`c4f5535`, `867b5ce`,
  `6336535`) carry `Claude-Session: .../session_019xvKNprddWsv6qXhodDCkZ`;
  this reviewing session's own ID is
  `https://claude.ai/code/session_01AWerxtiNNPmTwcW3rPzZRy` — no match,
  independent per Law 4.
- **Diff read directly:** `git diff origin/main...pr-172 --stat` — exactly
  the task packet's own `files` list (`tools/implementation/usage_ledger.py`,
  `scripts/report_executor_pilot.py`,
  `tests/implementation/test_executor_budget_limits.py`) plus the expected
  tracking-doc set (`docs/spec-matrix.yaml`, `QUEUE.md`, `PROGRESS.md`,
  `docs/certifications/queue.md`, `docs/implementation/task-packets/
  FTF-R016.md`, `spec/verification-map.json`) and one new `gate` step in
  `.github/workflows/ci.yml` — no scope creep.
- **Own reproduction, fresh:** checked out the three new files from `pr-172`
  onto a clean `origin/main` base (this session's designated branch had to
  be reset first — it was pre-populated with the producer's own PR172
  commits rather than main, corrected before any review commit was made).
  `python3 tests/implementation/test_executor_budget_limits.py` — "All 34
  checks passed." Own fault injection, two vectors distinct from the
  producer's own two (marker-filename mismatch was a first-run bug, not a
  fault-injection claim; the producer's one real fault-injection vector was
  the unknown-cost guard in `cost_per_success`) — I edited the shipped
  module directly: (1) `BudgetNode.available_usd()` changed to return only
  `self.limit_usd - self._reserved_usd`, ignoring the parent chain — 2
  checks failed for real (`a child's own large declared limit cannot escape
  the real parent remaining balance`, `a denied reservation leaves the
  tree's balance unchanged`), confirming the "budget reset per subagent"
  negative_acceptance guard is genuinely load-bearing; (2) short-circuited
  `enforce_model_route_policy`'s guard with `if False and private_mode and
  ...` — 1 named check failed for real (`an external route is denied under
  private_mode`), confirming the Section 81.3 negative_acceptance guard is
  genuinely load-bearing. Both reverted byte-identical (`diff` against the
  pre-injection copy empty) and the suite re-confirmed clean immediately
  after each. No regression: `tests/implementation/test_r1_execution_binding.py`
  (24/24) and `tests/implementation/test_safe_parallel_executor.py` (36/36)
  both still pass. `ruff check` on the three new files — clean.
  `scripts/product_truth_check.py --fail-on-unwired` — real `12/12`
  (unchanged — `proving_symbol` genuinely empty, matching the
  FTF-R014/R015/R017 precedent for build tooling, not inflation). `.github/
  workflows/ci.yml` confirmed wired with the new gate step.
- **Task contract re-pulled directly:** `python tools/r1_handoff.py packet
  FTF-R016 --plan FINE_TUNING_FACTORY_RELEASE_1.md`, then read
  `docs/implementation/task-packets/FTF-R016.md` for the resolved
  acceptance/negative_acceptance clauses. Acceptance clause 1 ("Measure one
  backend/UI/integration task plus parallel run with real tokens/CPU/CI/
  retries and independent accepted-task denominator") confirmed met by
  `test_real_pilot_report_end_to_end` — a real parallel wave with a
  genuinely failing task (`R016-B`) correctly excluded from
  `independent_accepted_task_ids`, and `cost_per_success_usd` honestly
  reported `"UNKNOWN"` rather than fabricated or `$0`. Acceptance clause 2
  ("Compare chosen executor/model to approved baseline... enforce
  delegated-tree reservations and bounded termination") confirmed met by
  `compare_to_baseline` (returns `INSUFFICIENT_EVIDENCE` on a missing
  figure, never manufactures a winner) and by this review's own fault
  injection above plus the producer's own real-SIGTERM/marker-file proof.
  `negative_acceptance` ("reject $100/$300 guarantee, missing usage counted
  free, task self-declared accepted, budget reset per subagent, external
  route in private source-code policy") — all five legs independently
  confirmed: the first four already covered above, `accepted_from_wave`
  reading only `approved_for_downstream` (never a task's own flag) closes
  the "self-declared accepted" leg by construction (source-read, not just
  test-asserted). Dependencies FTF-R015/FTF-H002/FTF-0403 confirmed
  genuinely `CERTIFIED` in `docs/spec-matrix.yaml` on `main` before relying
  on them. `evidence_hash` in the PR's own `docs/spec-matrix.yaml` diff
  (`c4f5535`) matches the real implementation commit.
- **The shipped code and its evidence are sound — this review's finding is
  not a code defect.** What blocks certification is the same,
  already-disclosed GitHub Actions runner-provisioning outage first found
  on PR #170 (see the entry immediately above and QUEUE.md "Owner actions —
  current"), now independently reconfirmed a further time on a completely
  different PR/branch/task, which rules out anything PR #170-specific as
  the cause: `list_workflow_jobs` on this PR's own run (`35655618099`)
  showed jobs transitioning `queued` → `completed`/`failure` in ~3 seconds
  each (`storage-semantics-integration`, `trainable-artifact-schema-api-
  integration`), `get_workflow_job` on the first confirmed `runner_id: 0`,
  `runner_name: ""`, and `get_job_logs` returned HTTP 404 (no runner was
  ever assigned, so no log exists) — the identical signature already
  recorded for PR #170. The remaining jobs, including `gate`, were still
  `queued` (not yet even attempted) at the time of this review. No genuine
  passing CI conclusion exists on the real head to confirm.
- **Not merged, not certified, per Law 1/Law 4.** `docs/spec-matrix.yaml`
  for FTF-R016 stays `PROVISIONAL`/`L1-PROVEN` exactly as the producer
  submitted it — not altered by this review, since the shipped code is
  sound; what's missing is a genuine passing CI run, which needs the
  GitHub Actions runner-provisioning issue resolved first (owner-level,
  outside any session's access). PR #172 stays open, unmerged. **Action
  needed before re-requesting certification:** once GitHub Actions is
  confirmed provisioning runners again, re-run PR #172's CI to get a
  genuine passing conclusion on the real head, then a fresh Control-plane
  review can certify on that basis alone — no code change is expected or
  needed.
- **Validated-by:** this session (independent — see session-ID check
  above), https://claude.ai/code/session_01AWerxtiNNPmTwcW3rPzZRy,
  2026-09-21.

## 2026-09-22 — FTF-0202 — Secret-manager integration
**Submitted by:** scheduled relay shift, session
https://claude.ai/code/session_01CKZy4kupyKhLZEjNUTzcpC. Branch
`claude/wizardly-fermat-3c0uoh`, implementation commit `c0c8409d80c1e2e0222f8fed6912dc0355a046d2`.

**Dependency readiness:** FTF-0005/FTF-0103/FTF-0201 all independently
confirmed `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
The FTF-0202 owner-ask itself (secret backend selection) is resolved on
`main` (commit `e9e63b4`, "OpenBao, anchored to a live conversation",
merged via PR #173) — verified directly via `git log origin/main --
QUEUE.md` rather than trusted from this file's own prose alone, and the
three earlier, unverifiable "OpenBao confirmed" claims (PR #143, a commit
on PR #159, PR #162) confirmed closed/unmerged, not acted on.

**Acceptance clause 1** ("Store/resolve/rotate short-lived secret refs
through the actual selected secret backend"): `packages/litellm_client/
secret_manager_integration.py` resolves `openbao://<mount>/<path>/<field>`
refs (a strict subset of FTF-0201/FTF-0005's own already-certified, frozen
`^[a-z]+://[A-Za-z0-9_\-./]+$` shape — neither certified validator was
touched) against a real OpenBao backend via stdlib-only `urllib.request`
(no new third-party runtime dependency, e.g. `hvac` — deliberately
avoiding a CLAUDE.md "Ask owner first" ask). `store_secret_ref`/
`rotate_secret_ref` merge onto (never silently replace) a co-located
field at the same KV v2 path; `rotate_secret_ref` reads the current
version first, writes the new one, then soft-deletes exactly the version
it superseded (never a hard-coded number) and refuses to rotate a ref
with no existing secret. `_request` retries only a real 429/5xx, up to
three attempts (Section 3.3), honoring a real `Retry-After` header.

**Acceptance clause 2** ("Independent review, exact merged commit, real
relevant integration and negative-test evidence recorded"): every real
backend interaction was verified directly against a real OpenBao server
*before* any test was written (Law 1) — no prebuilt release binary or
container image was reachable from this sandbox (the same documented
class of gap FTF-0004/FTF-0102/FTF-R017 already established for
Postgres/Docker/SeaweedFS), so a real OpenBao server was built from
source (`go build` against `github.com/openbao/openbao/v2` commit
`e98dc184344fe84ee4e69965ac3f4217d5b6a45d`, both a real Git remote and the
Go module proxy reachable) and run locally in `-dev` mode. That direct
verification caught a genuine, load-bearing surprise: reading a
soft-deleted or destroyed KV v2 version answers real HTTP 404 with a JSON
body still attached, not HTTP 200 with a null payload as an uncontrolled
first `curl | json.tool` pass (no `-w '%{http_code}'`) misleadingly
suggested — caught and corrected before any test was written to expect
the wrong shape, not discovered after a false-negative test passed.

**negative_acceptance** ("Unknown/expired/ref-denied secrets block call
and are never returned to browser or plaintext DB") — all three legs
independently proven: an unknown path and a soft-deleted/destroyed
version both raise `SecretNotFoundError` via the same ordinary 404
handling; a denied token raises `SecretAccessDeniedError` (HTTP 403); a
secret past its own recorded `custom_metadata.expires_at` (a Factory-level
honesty layer this module adds, since OpenBao's own KV v2 secrets do not
expire on their own) raises `SecretExpiredError` and is never returned
even though the backend itself would still serve it. "Never... plaintext
DB" holds by construction, not by convention: a source-level test
confirms no `sqlalchemy`/`fastapi`/`hvac`/`requests` import exists
anywhere in the module — it is structurally incapable of writing this
repository's own Postgres or serving a browser route.

**Tests:** `tests/tasks/test_ftf_0202.py` — 22 no-DB, no-network checks
against a fake `urllib.request.urlopen` transport (ref parsing/building/
redaction, `OpenBaoConfig.from_env` validation, the full
negative_acceptance in isolation, the retry policy including a real
`Retry-After` header and the three-attempt ceiling, store/rotate merge
behavior, and the source-level no-DB/no-web-framework-import proof).
`tests/integration/test_ftf_0202_secret_manager.py` — 12 real checks
against a real, locally-built-and-spawned OpenBao `-dev` server (store,
resolve, cross-field merge, rotate + real old-version unreadability
confirmed via a raw version-pinned read, rotate-of-nothing refusal,
unknown path, access-denied, expired, not-yet-expired, real hard-destroy
unreadability).

**Own fault injection (Law 1, two distinct vectors, both reverted
byte-identical and both suites re-confirmed clean immediately after
each):**
1. Disabled the expiry check in `resolve_secret_ref` (`if False and
   isinstance(expires_raw, str) and expires_raw:`) — both suites failed
   with exactly the named "expired secrets block call" check.
2. Disabled the 404-to-`SecretNotFoundError` mapping in `_request` (`if
   False and status == 404:`) — the no-DB suite failed 2 named checks
   (unknown path, rotate-of-nothing refusal), and the real integration
   suite crashed with a genuine `SecretBackendError` instead of completing
   cleanly, proving the specific 404 classification is real and
   load-bearing, not decorative.

**No regression:** full `tests/tasks/test_ftf_*.py` sweep (32 files) all
"all checks passed" or equivalent, after `uv sync --all-packages`/`pnpm
install --frozen-lockfile` (both pre-existing, previously-undocumented-
this-shift sandbox gaps, confirmed pre-existing by FTF-0002/FTF-0003/
FTF-H001 all failing identically before and passing clean immediately
after, with no other change); `ruff check .`/`ruff format --check .`
clean repo-wide (148 source files); `mypy .` — 9 errors, all the
pre-existing, unrelated `alembic` `op`/`context` missing-stub gap across
every existing migration file, confirmed byte-for-byte identical on
`main` via `git stash`; `pnpm run lint`/`typecheck`/`format:check` clean;
`pnpm exec vitest run` — 19/19; `uv run pytest tests/unit` — 3/3. `git
fetch --unshallow origin` first (this sandbox's own documented
shallow-clone artifact), then `validate_spec.py --strict` clean.
`product_truth_check.py --fail-on-unwired` stays `12/12` (`proving_symbol`
deliberately empty — this task owns no live HTTP route or wired
product-runtime caller yet; FTF-0203 "Connection validation" is the later
task that will call this module for real). `scripts/status.py` — `36/300
certified · 12 product-true` (unchanged — PROVISIONAL, not CERTIFIED).

**CI:** `.github/workflows/ci.yml` gets one new `gate` job step (`uv run
python3 tests/tasks/test_ftf_0202.py`) plus a new dedicated
`secret-manager-integration` job that builds the identical pinned OpenBao
commit fresh on the hosted runner's own real network and runs it as a
real local `-dev` process (no `services:` container needed, since
OpenBao's dev mode is a single self-contained process) — no other job's
steps touched.

**Not self-certified (Law 4).** `docs/spec-matrix.yaml` set to
`certification_state: PROVISIONAL` / `proof_state: L1-PROVEN`,
`evidence_hash: c0c8409d80c1e2e0222f8fed6912dc0355a046d2`, `proving_test:
tests/tasks/test_ftf_0202.py`, `proving_symbol: ''`. Awaiting independent
Control-plane L2 certification.

## 2026-09-22 — Control plane: FTF-0202 (PR#176) independently reviewed, CERTIFIED
Reviewing session (scheduled certifier, `pull_request.opened` webhook for
PR #186): `session_01GngkvFCtGtTnDnW9d2HC1j`. Session-ID compared against
PR #176's commits (`session_01CKZy4kupyKhLZEjNUTzcpC`) — no overlap,
independent per Law 4. A separate prior candidate certification PR (#179,
opened by a third session `session_01DzyLpJHy35M2BeGRfHfwL5`) was found
open and unmerged, had gone stale against `main`'s subsequent unrelated
FTF-R016 merge (`mergeable_state: dirty`), and its own certification
changes were never landed — this review reproduces the work fresh rather
than trusting that PR's prose, and closes it as a duplicate once this
entry lands.

Re-ran the full CERTIFY procedure in a fresh git worktree at `origin/main`
(`sha256sum CLAUDE.md` matched `CLAUDE.md.sha256` first):
- Confirmed PR #176's implementation commit (`c0c8409d80c1e2e0222f8fed6912dc0355a046d2`)
  is a real ancestor of `main`'s current tip via `git merge-base
  --is-ancestor` (merged directly, not through GitHub's own merge button —
  consistent with `merged: false`/`state: closed` on the GitHub API
  despite the commit being genuinely on `main`).
- `get_check_runs` on PR #176's real head — 19/19 jobs `success`, the
  dedicated `secret-manager-integration` job ran ~5m25s (05:38:11Z–
  05:43:36Z), a real build-from-source duration, not a runner-provisioning
  ghost job.
- `python3 tests/tasks/test_ftf_0202.py` reproduced clean from scratch —
  22/22.
- Own fault injection, a vector distinct from both the producer's two and
  PR #179's one: disabled the field-presence guard in `resolve_secret_ref`
  — the named "field is absent" check failed for exactly the right reason;
  reverted byte-identical (`diff` confirmed empty); suite re-confirmed
  clean.
- `scripts/product_truth_check.py --fail-on-unwired` — `13/13`
  product-true on `main`'s current tip (unrelated work merged since this
  task's own submission moved the total from 12 to 13; FTF-0202 itself
  still legitimately contributes `proving_symbol: ''`, not inflation).
- `python3 tools/r1_handoff.py packet FTF-0202 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — both acceptance clauses and the full
  negative_acceptance clause confirmed genuinely met by the diff, not
  merely "tests pass".
- Dependencies FTF-0005/FTF-0103/FTF-0201 confirmed genuinely `CERTIFIED`
  in `docs/spec-matrix.yaml`.
- `scripts/validate_spec.py --strict` — 19 `stale_commit` defects before
  `git fetch --unshallow origin` (pre-existing shallow-clone artifact),
  clean after.

No blocking defect found. `docs/spec-matrix.yaml` set to
`certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
`evidence_hash` set to the merge commit
(`6dfd97d2444c43a9b981099c8bb9f60368ad8b21`). Ledger entry appended
(`docs/certifications/ledger.md`, "FTF-0202 — Secret-manager
integration"). Ran `scripts.task_claim.sync_certified_status()` —
`spec/tasks.json`/`docs/implementation/TRACKER.json` updated for
`['FTF-0202']`. `QUEUE.md`/`PROGRESS.md` updated: FTF-0202 moved to Done,
Certified count bumped to 37/300 (product-true stays at whatever
`main`'s current tip independently shows, 13/13 as of this review).

## 2026-09-22 — Control plane: FTF-1001 (PR#160) independently reviewed, CERTIFIED
Reviewing session (scheduled certifier, `pull_request.opened` webhook for
PR #186): `session_01GngkvFCtGtTnDnW9d2HC1j`. Session-ID compared against
PR #160's commits (`session_017j5b6PyPwyvoYyubyzrAcr`) — no overlap,
independent per Law 4. A separate stale, unmerged candidate certification
(PR #181, opened by a third session `session_01DzyLpJHy35M2BeGRfHfwL5`)
is superseded and closed by this entry rather than trusted at face value.

Re-ran the CERTIFY procedure in a fresh git worktree at `origin/main`:
- Confirmed PR #160's merge commit (`8b51c85edd35652ba3263dd6a6fcbf590f2f81bd`)
  is a real ancestor of `main`'s current tip.
- `get_check_runs` on PR #160's real head — 22/22 jobs `success`, real
  non-instant per-job durations (12s–390s), no runner-provisioning ghost
  pattern.
- `tests/tasks/test_ftf_1001.py` and the real-Postgres integration suite
  both reproduced clean from scratch against a freshly isolated database.
- Own fault injection, a vector distinct from the producer's three and PR
  #181's one: disabled the cross-project-within-same-org scoping guard in
  `_fetch_or_raise` — the named cross-project-404 check failed for exactly
  the right reason; reverted byte-identical; suite re-confirmed clean.
- `product_truth_check.py --fail-on-unwired` — `13/13`; confirmed
  `build_model_version_router` is a genuine live caller in
  `apps/api/app/main.py`, not a 0/0 or existence-only claim.
- `tools/r1_handoff.py packet FTF-1001` — both acceptance clauses and the
  full negative_acceptance (three denial paths) confirmed genuinely met.
- Dependencies FTF-0301/FTF-0401/FTF-0103 confirmed genuinely `CERTIFIED`.
- `scripts/validate_spec.py --strict` clean.
- **Evidence-count correction:** PR #160/#181 both record "24 no-DB test
  functions" / "26 real live-Postgres checks." Actual counts on the real
  merged code: 21 `def test_*` functions (43 `check()` calls once a loop
  over `ALLOWED_IMPORT_KINDS` is accounted for) in the no-DB suite, and 19
  `check()` calls at runtime in the integration suite. Both suites
  genuinely pass and genuinely cover the required surface — a
  documentation inaccuracy in the recorded counts, not a fabricated pass —
  corrected in `docs/spec-matrix.yaml`'s FTF-1001 notes rather than
  propagated further.

No blocking defect found. `docs/spec-matrix.yaml` set to
`certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
`evidence_hash` set to the merge commit
(`8b51c85edd35652ba3263dd6a6fcbf590f2f81bd`). Ledger entry appended
(`docs/certifications/ledger.md`, "FTF-1001 — ModelVersion schema/API").
Ran `scripts.task_claim.sync_certified_status()` —
`spec/tasks.json`/`docs/implementation/TRACKER.json` updated for
`['FTF-1001']`. `QUEUE.md`/`PROGRESS.md` updated: FTF-1001 moved to Done,
Certified count bumped to 38/300 (product-true stays 13/13).

## 2026-09-22 — Control plane: FTF-1302 (PR#170) independently reviewed, CERTIFIED
Reviewing session (scheduled certifier, `pull_request.opened` webhook for
PR #186): `session_01GngkvFCtGtTnDnW9d2HC1j`. This task was previously
reviewed sound but NOT CERTIFIED (2026-09-21) solely because CI could not
show a genuine passing run — a runner-provisioning outage (ghost jobs),
not a code defect. Session-ID compared against PR #170's commits
(`session_01Fy9YNo5oaA2HZLWRud1Xma`) — no overlap, independent per Law 4.
A separate stale, unmerged candidate certification (PR #184, opened by a
third session `session_01DzyLpJHy35M2BeGRfHfwL5`) is superseded and
closed by this entry rather than trusted at face value.

Re-ran the CERTIFY procedure in a fresh git worktree at `origin/main`:
- Re-confirmed the runner-provisioning outage is genuinely cleared, not
  merely trusted from prior prose: `get_check_runs` on PR #170's real
  merge-head run — 23/23 jobs `success`, real distinct non-zero
  `runner_id`s and genuine multi-second/minute per-job durations
  (`secret-manager-integration` ~5m43s) — structurally the opposite of
  the prior ghost-job signature (`runner_id: 0`, ~3s completions).
- `tests/tasks/test_ftf_1302.py` reproduced clean from scratch.
- Own fault injection: disabled the retry-dedup in
  `unique_successful_task_ids` — two named checks failed for exactly the
  right reason; reverted byte-identical; suite re-confirmed clean.
- `product_truth_check.py --fail-on-unwired` — `13/13`; FTF-1302's own
  `proving_symbol` confirmed legitimately `''` (pure computation, no live
  route).
- `tools/r1_handoff.py packet FTF-1302` — both acceptance clauses and the
  negative_acceptance (no double-counting by construction) confirmed
  genuinely met.
- Dependencies FTF-0006/FTF-1301 confirmed genuinely `CERTIFIED`.
- `scripts/validate_spec.py --strict` clean.
- **Documentation-accuracy correction:** PR #184's own body attributed
  `main`'s product-true count rising 12→13 to FTF-1302's own
  certification — independently confirmed this is inaccurate (the 13/13
  figure was already reached by unrelated work before FTF-1302's
  certification; FTF-1302 contributes `proving_symbol: ''` either way).
  Not repeated here.

No blocking defect found. `docs/spec-matrix.yaml` set to
`certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
`evidence_hash` set to the merge commit
(`acce2b4d9a3d636b06065f11de3958239ba5b917`). Ledger entry appended
(`docs/certifications/ledger.md`, "FTF-1302 — Local compute costing").
Ran `scripts.task_claim.sync_certified_status()` —
`spec/tasks.json`/`docs/implementation/TRACKER.json` updated for
`['FTF-1302']`. `QUEUE.md`/`PROGRESS.md` updated: FTF-1302 moved to Done,
Certified count bumped to 39/300 (product-true stays 13/13).

## 2026-09-22 — Control plane: FTF-1401 (PR#161) independently reviewed, CERTIFIED
Reviewing session (scheduled certifier, `pull_request.opened` webhook for
PR #186): `session_01GngkvFCtGtTnDnW9d2HC1j`. Session-ID compared against
PR #161's commits (`session_01CDP3isFaUkJtHPU91hNz2j`) — no overlap,
independent per Law 4. A separate stale, unmerged candidate certification
(PR #182, opened by a third session `session_01DzyLpJHy35M2BeGRfHfwL5`)
is superseded and closed by this entry rather than trusted at face value.

Re-ran the CERTIFY procedure in a fresh git worktree at `origin/main`:
- `get_check_runs` on PR #161's real head — 23/23 jobs `success`, real
  non-instant per-job durations, no runner-provisioning ghost pattern.
- `tests/tasks/test_ftf_1401.py` reproduced clean from scratch (17
  no-DB test functions).
- Own fault injection, a vector distinct from the producer's two and PR
  #182's one: disabled the read P95 threshold branch in
  `evaluate_acceptance` — the named check failed for exactly the right
  reason; reverted byte-identical; suite re-confirmed clean.
- `product_truth_check.py --fail-on-unwired` — `13/13`; FTF-1401's own
  `proving_symbol` confirmed legitimately `''` (measurement tool, no
  served route).
- `tools/r1_handoff.py packet FTF-1401` — both acceptance clauses and the
  negative_acceptance confirmed genuinely met.
- Dependencies FTF-0006/FTF-0401/FTF-0106 confirmed genuinely
  `CERTIFIED`. Confirmed FTF-1001 is not actually a dependency of
  FTF-1401 at all — the diff's exclusion of ModelVersion traffic from
  the request mix is an honest scope reduction, not a relied-upon
  dependency, so FTF-1001's prior PROVISIONAL state was never a
  disqualifying gap (and FTF-1001 is itself now CERTIFIED anyway).
- `scripts/validate_spec.py --strict` clean.
- **Disclosed, unresolved gap (not this task's own to fix):**
  FTF-0406/FTF-0407's own test suites remain unwired from `ci.yml` —
  flagged again for a future relay/control-plane shift.

No blocking defect found. `docs/spec-matrix.yaml` set to
`certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
`evidence_hash` set to the merge commit
(`c2fda5b6ad472aa6d38a977896b339a484a9a483`). Ledger entry appended
(`docs/certifications/ledger.md`, "FTF-1401 — API load test"). Ran
`scripts.task_claim.sync_certified_status()` —
`spec/tasks.json`/`docs/implementation/TRACKER.json` updated for
`['FTF-1401']`. `QUEUE.md`/`PROGRESS.md` updated: FTF-1401 moved to Done,
Certified count bumped to 40/300 (product-true stays 13/13).

## 2026-09-22 — Control plane: FTF-1501 (PR#159) independently reviewed, CERTIFIED
Reviewing session (scheduled certifier, `pull_request.opened` webhook for
PR #186): `session_01GngkvFCtGtTnDnW9d2HC1j`. Session-ID compared against
PR #159's four original commits (`session_01X4QYt5B4DQroufdM796nBD`) —
no overlap, independent per Law 4. A separate stale, unmerged candidate
certification (PR #180, opened by a third session
`session_01DzyLpJHy35M2BeGRfHfwL5`) is superseded and closed by this
entry rather than trusted at face value. Noted, not blocking: PR #180's
own certifying session pushed 2 infrastructure-only commits (merge +
YAML fix) directly onto PR #159's branch before merging — disclosed, does
not touch the security-test code itself.

Re-ran the CERTIFY procedure in a fresh git worktree at `origin/main`:
- `get_check_runs` on PR #159's real final head — 21/21 jobs `success`,
  real non-instant per-job durations.
- Stood up a fully isolated local PostgreSQL database from scratch
  (temporarily renamed the shared `sovra_app` role aside so migration
  0001's unconditional `CREATE ROLE` could run clean, then restored it
  byte-for-byte). `tests/tasks/test_ftf_1501.py` and
  `tests/security/tenant_isolation_suite.py` both reproduced clean
  (25/25 on the live suite).
- Own fault injection, reproducing the producer's own documented vector
  for real: disabled the application-level ownership check in
  `authorize_resource_ownership` (RLS left untouched) — exactly 21/25,
  the 4 named `cross_tenant_db_read_denied` checks failing, confirming
  genuine defense-in-depth (RLS and the app-level check are each
  independently load-bearing). Reverted byte-identical; suite
  re-confirmed clean (25/25) against a fresh database.
- `product_truth_check.py --fail-on-unwired` — `13/13`; `proving_symbol`
  confirmed legitimately `''` (consolidated evidence suite, no new
  live-wiring claim).
- `tools/r1_handoff.py packet FTF-1501` — acceptance clause 1's honest
  narrowing to API+DB paths confirmed genuinely warranted (workers/
  storage/cache/model paths have no real live wiring yet); the
  negative_acceptance (no mocked auth, no superuser-only DB tests) holds
  by construction.
- Dependencies FTF-0106/FTF-H003 confirmed genuinely `CERTIFIED`.
- `scripts/validate_spec.py --strict` clean.

No blocking defect found. `docs/spec-matrix.yaml` set to
`certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
`evidence_hash` set to the merge commit
(`8b8a9c12e04277a62149ab28de2c9c2db3a9ff16`). Ledger entry appended
(`docs/certifications/ledger.md`, "FTF-1501 — Tenant isolation suite").
Ran `scripts.task_claim.sync_certified_status()` —
`spec/tasks.json`/`docs/implementation/TRACKER.json` updated for
`['FTF-1501']`. `QUEUE.md`/`PROGRESS.md` updated: FTF-1501 moved to Done,
Certified count bumped to 41/300 (product-true stays 13/13).

## 2026-09-22 — Control plane: FTF-1702 (PR#167) independently reviewed, CERTIFIED
Reviewing session (scheduled certifier, `pull_request.opened` webhook for
PR #186): `session_01GngkvFCtGtTnDnW9d2HC1j`. Session-ID compared against
PR #167's commits (`session_01CVWSRiMU43ZUy6H6C9FMez`) — no overlap,
independent per Law 4. A separate stale, unmerged candidate certification
(PR #183, opened by a third session `session_01DzyLpJHy35M2BeGRfHfwL5`)
is superseded and closed by this entry rather than trusted at face value.

Re-ran the CERTIFY procedure in a fresh git worktree at `origin/main`:
- `get_check_runs` on PR #167's real head — 23/23 jobs `success`, real
  non-instant per-job durations, no runner-provisioning ghost pattern.
- `tests/reference/test_ftf_1702.py` reproduced clean from scratch.
- Own fault injection, a vector distinct from the producer's own and PR
  #183's own: disabled the unseeded-subject guard in
  `ToolEnvironment.dispatch` — the named check failed for exactly the
  right reason; reverted byte-identical; suite re-confirmed clean.
- `product_truth_check.py --fail-on-unwired` — `13/13`; `proving_symbol`
  confirmed legitimately `''` (test-infrastructure module wrapping
  FTF-1701's dispatcher, no live product route).
- `tools/r1_handoff.py packet FTF-1702` — both acceptance clauses and the
  negative_acceptance confirmed genuinely met.
- Dependencies FTF-0004/FTF-1701 confirmed genuinely `CERTIFIED`.
- `scripts/validate_spec.py --strict` clean.

No blocking defect found. `docs/spec-matrix.yaml` set to
`certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
`evidence_hash` set to the merge commit
(`099a0753710ac6f2bb4cba68c209e99d3845e5d9`). Ledger entry appended
(`docs/certifications/ledger.md`, "FTF-1702 — Tool environment"). Ran
`scripts.task_claim.sync_certified_status()` —
`spec/tasks.json`/`docs/implementation/TRACKER.json` updated for
`['FTF-1702']`. `QUEUE.md`/`PROGRESS.md` updated: FTF-1702 moved to Done,
Certified count bumped to 42/300 (product-true stays 13/13).

## 2026-09-22 — Control plane: FTF-R016 (PR#172) independently reviewed, CERTIFIED
Reviewing session (scheduled certifier, `pull_request.opened` webhook for
PR #186): `session_01GngkvFCtGtTnDnW9d2HC1j`. This task was previously
reviewed sound but NOT CERTIFIED (2026-09-21) solely because CI could not
show a genuine passing run — a runner-provisioning outage, not a code
defect. Session-ID compared against PR #172's commits
(`session_019xvKNprddWsv6qXhodDCkZ`/`session_01SwwQG6gYUUQiifu9yj3QYM`) —
no overlap, independent per Law 4. A separate stale, unmerged candidate
certification (PR #185, opened by a fourth session
`session_01DzyLpJHy35M2BeGRfHfwL5`) is superseded and closed by this
entry rather than trusted at face value.

Re-ran the CERTIFY procedure in a fresh git worktree at `origin/main`:
- Re-confirmed the runner-provisioning outage is genuinely cleared:
  `get_check_runs` on PR #172's real merge-head run — 23/23 jobs
  `success`, real distinct non-zero `runner_id`s, genuine multi-minute
  per-job durations.
- `tests/implementation/test_executor_budget_limits.py` reproduced clean
  from scratch (34/34).
- Own fault injection, a vector distinct from the producer's own, the
  first reviewer's, and PR #185's own: disabled the mid-execution
  termination guard in `run_within_budget`. Reproduced for real with a
  bounded 2-second budget against a real 5-second `sleep` subprocess —
  with the guard intact the process is genuinely killed at ~2.0s; with
  it disabled the same process runs the full ~5.0s undetected. The
  dedicated suite caught this: 3 named checks failed for exactly the
  right reason. Reverted byte-identical; suite re-confirmed clean.
- `product_truth_check.py --fail-on-unwired` — `13/13`; `proving_symbol`
  confirmed legitimately `''` (build/implementation tooling, not a
  served route).
- `tools/r1_handoff.py packet FTF-R016` — both acceptance clauses and
  the full negative_acceptance (no hardcoded guarantee, unknown cost
  never free, acceptance never self-reported, budget tree never resets
  per subagent, private-mode route policy enforced) confirmed genuinely
  met.
- Dependencies FTF-R015/FTF-H002/FTF-0403 confirmed genuinely
  `CERTIFIED`.
- `scripts/validate_spec.py --strict` clean.
- **Note:** fixed a genuine YAML-parsing hazard found while writing this
  entry — FTF-R016's own `notes:` field used a plain unquoted multi-line
  scalar, which broke `yaml.safe_load` once this entry's own text added
  a `: ` sequence; converted to the file's own established `notes: >-`
  block-scalar convention (matching FTF-0202's own precedent), no
  content lost, verified by re-parsing.

No blocking defect found. `docs/spec-matrix.yaml` set to
`certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
`evidence_hash` set to the merge commit
(`ce1d2f062a830cbb69bfd00353e329585f8efff5`). Ledger entry appended
(`docs/certifications/ledger.md`, "FTF-R016 — Measure selected
coding-model/executor pilot cost and enforce budget tree"). Ran
`scripts.task_claim.sync_certified_status()` —
`spec/tasks.json`/`docs/implementation/TRACKER.json` updated for
`['FTF-R016']`. `QUEUE.md`/`PROGRESS.md` updated: FTF-R016 moved to
Done, Certified count bumped to 43/300 (product-true stays 13/13).

This is the seventh and last of the seven PROVISIONAL items found at
the start of this shift (FTF-0202/FTF-1001/FTF-1302/FTF-1401/FTF-1501/
FTF-1702/FTF-R016) — all now independently CERTIFIED, and all six
stale duplicate "Control plane: certify" candidate PRs (#179-185)
closed as superseded.

## 2026-09-22 — FTF-0500 — DatasetSourceConnection schema/API/RBAC
- **PR:** opened from `claude/wizardly-fermat-i9x1tk` → `main`, title
  `[relay] FTF-0500: DatasetSourceConnection schema/API/RBAC`.
- **Commits:** `ec6a3e7` (implementation: schema/CRUD/routes/migration/
  tests/CI wiring), `ce824dd` (phase-05 contract freeze), and a third,
  tracking-doc-only commit (`docs/spec-matrix.yaml`/`QUEUE.md`/
  `PROGRESS.md`/this entry) on the same branch, matching the established
  two/three-commit precedent.
- **Ground truth checked before picking work:** STEP 0 verified
  `sha256sum CLAUDE.md` against the committed `CLAUDE.md.sha256` — clean.
  This session's own branch already matched `main`'s tip at `8f33de0`
  (PR #193, the last of the seven Control-plane certifications). Cross-
  checked live GitHub state via `list_pull_requests` before picking:
  two open PRs (#194/#195), both `[relay] FTF-0304: Local artifact
  resolver` from other sessions — not this relay's own submissions, not
  picked to avoid duplicating real work. Recomputed the dependency-ready
  frontier fresh by joining `docs/spec-matrix.yaml`'s live
  `certification_state` against `docs/TASK_INDEX.md`'s real
  `Dependencies` column (not trusted from any prior shift's note alone):
  `FTF-0304` (claimed by #194/#195), `FTF-0408`/`FTF-0902` (both re-
  confirmed still genuinely blocked — no real `AgentManifest.mandate_json`
  storage exists anywhere in this repository yet; no real CNI-enforcing
  Kubernetes cluster in this sandbox), `FTF-1503`, `FTF-1506`, `FTF-H006`
  (higher task IDs, not yet examined once a lower, genuinely dependency-
  ready, unclaimed item was found). `FTF-0500`'s own dependencies
  (`FTF-0401`, `FTF-0202`, `FTF-H003`) were independently confirmed
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- **Task contract:** `python tools/r1_handoff.py packet FTF-0500 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance ("Freeze the minimum
  shared phase interface and domain schemas at the declared owner paths
  ... Create purpose-scoped source grants and authorization before
  import; link every dataset to approved source"), negative_acceptance
  ("Source allowed only evaluation cannot be trained; grant revocation
  propagates to future derivative use"), `database_changes: "Own forward
  Alembic migration for this resource; tenant/composite constraints/RLS
  and upgrade tests per Sections 6/65/71. Serialize migration chain."`,
  `shared_lock_required: [contracts:05, migration:application]`,
  `dependencies: [FTF-0401, FTF-0202, FTF-H003]` (all CERTIFIED),
  `contract_owner: FTF-0500` (this task is the phase-05 interface owner —
  `packages/contracts/openapi/05-interface.yaml`/`packages/contracts/
  jsonschema/05-domain.schema.json` did not exist before this task,
  confirmed by direct listing).
- **Scope disclosed, not hidden:** Section 6.8F's own literal field list
  (`DatasetSourceConnection`) is this task's real scope. Section 67.4's
  broader `DataUseGrant` (approver identity, processing boundary,
  retention, expiry, underlying-rights evidence) is not one of Section
  6's own listed entities and no dependency task certifies it —
  `allowed_purposes_json`, declared once at creation from Section 6.8F's
  own closed purpose vocabulary, is this task's own real, narrower
  "purpose-scoped grant." No `Dataset`/`DatasetRecord` entity (Section
  6.10/6.11, Section 8's import pipeline) exists anywhere in this
  repository yet — "link every dataset to approved source" is read as
  building the real, callable authorization primitive
  (`authorize_purpose_use`/`authorize_dataset_source_purpose`, also a
  live `POST .../authorize` route) a later Dataset-import task
  (FTF-0501+) will call, not building the Dataset entity itself.
- **Design:** `DatasetSourceConnection` (Section 6.8F fields plus
  `organization_id` for direct RLS, per Section 65.1's own established
  refinement) with closed-set validators for `source_type`/
  `allowed_purposes`/`security_classification`/`connection_status`.
  `credential_secret_ref`/`connection_config_encrypted_ref` only ever
  store an `openbao://...` reference (`validate_secret_ref_or_none`) —
  structurally incapable of accepting a raw value. `authorize_purpose_use`
  raises `DatasetSourceAuthorizationError` (never a bare `False`) when the
  connection is not `active` (covers "grant revocation propagates to
  future derivative use" — a revoked connection denies every purpose,
  including one it previously granted) or the requested purpose is not in
  `allowed_purposes_json` (covers "Source allowed only evaluation cannot
  be trained"). Role gating: Section 45's literal "ml_engineer:
  datasets/training" sentence (the only one naming a role for this class
  of work) plus the standing project-administration superset —
  `{ml_engineer, workspace_admin, organization_admin}`, gating every
  operation including reads (this credential-bearing resource does not
  open reads wider than writes, matching FTF-0201's own
  `LiteLLMConnection` precedent rather than FTF-0401/FTF-0404's own
  wide-read convention for a non-secret-bearing resource).
- **First real caller of FTF-0202:** unlike FTF-0201 (which predates
  FTF-0202 and only ever validated a generic reference shape),
  `create_dataset_source_connection` calls `store_secret_ref` for real —
  a supplied `credential`/`connection_config` value is written to a real
  OpenBao KV v2 path before the row is ever inserted, and only the
  resulting reference is persisted; `_serialize` never includes either
  secret-ref field, only `has_credential`/`has_connection_config`
  booleans (Section 46).
- **Failure found and fixed by real fault injection (Law 1), not merely
  described:** before writing any test, ran this task's own real backend
  interaction directly against a real local OpenBao server (built from
  source, the same pinned commit `e98dc184344fe84ee4e69965ac3f4217d5b6a45d`
  FTF-0202's own CI job uses) and a real local PostgreSQL 16 cluster — no
  prebuilt release binary/container image reachable from this sandbox,
  the same documented class of gap FTF-0004/FTF-0102/FTF-0202/FTF-R017
  already established. The original design used a dedicated
  `dataset-sources` OpenBao mount; a fresh `-dev` server only auto-enables
  the `secret` mount, so every `store_secret_ref`/`resolve_secret_ref`
  call against the new mount answered a real HTTP 404, indistinguishable
  from `SecretNotFoundError` — reproduced directly (`create` returned
  500, traced to `SecretNotFoundError: secret backend has no data at
  /v1/dataset-sources/data/...`). Fixed by reusing FTF-0202's own
  already-proven `secret` mount with a `dataset-sources/` path prefix
  instead of inventing a second, unprovisioned mount
  (`_SECRET_MOUNT = "secret"`, `_SECRET_PATH_PREFIX = "dataset-sources"`);
  re-ran the full real-backend integration proof afterward — clean.
- **27 no-DB test functions** (`tests/tasks/test_ftf_0500.py`): every
  field validator's own closed-set/shape rule (`validate_source_type`/
  `validate_allowed_purposes`/`validate_security_classification`/
  `validate_secret_ref_or_none`); `DatasetSourceConnection.__init__`
  refusing bad input (empty display_name, unknown source_type, empty
  allowed_purposes, unknown connection_status, a raw non-ref credential)
  before any session; `apply_edits`/`apply_revoke`'s pure DB-free cores
  (refuses to edit a revoked connection; requires at least one field;
  idempotent revoke); `authorize_purpose_use`'s own negative_acceptance
  in full at the pure-function level (a source granted only
  `held_out_evaluation` denies `training`; revoking a connection denies a
  previously-granted purpose); a real, locally-hosted uvicorn server
  proving unauthenticated (401) and wrong-role/viewer (403) denial never
  opens a session while `ml_engineer` reaches the session layer; the real
  imported `require_role`/`authorize_resource_ownership`/
  `execute_tenant_scoped`/`store_secret_ref` dependencies confirmed (not
  hand-rolled duplicates); `_serialize` never exposing either secret-ref
  field.
- **Real Postgres + real OpenBao integration proof**
  (`tests/integration/test_ftf_0500_dataset_source_connection.py`, new CI
  job `dataset-source-connection-integration`): two real organizations
  (each with a real workspace/project) seeded as the migration-owner
  role; every request through the restricted `sovra_app` role, confirmed
  distinct from the migration owner; a real create with a real credential
  + connection_config, independently resolved back from the real live
  OpenBao server at the exact path this task's own code writes to (never
  merely asserted from this task's own claim); a duplicate `display_name`
  within the same project rejected by the real database's own unique
  constraint; a real cross-organization/cross-project read refused (404),
  identical to missing; both negative_acceptance clauses reproduced
  against real live database rows — authorizing `training` against a
  source granted only `held_out_evaluation` denied (403), then the same
  source's own granted purpose authorized (200); `training` authorized
  while a second connection is active, then genuinely denied (403) after
  revocation, the same previously-authorized purpose; editing an already-
  revoked connection refused (409); revoke idempotency (200 on repeat); a
  credential supplied with no secret-manager backend configured refused
  (503) with zero rows created (verified directly via a `count(*)` query
  as the migration owner); the restricted `sovra_app` role's own real
  grant surface confirmed unable to `DELETE` a row directly (revoke is
  the only real transition).
- **No regression, independently re-run:** full `tests/tasks/
  test_ftf_*.py` sweep run directly — every file passes except the same
  three pre-existing sandbox gaps already on record before this task
  touched anything (`test_ftf_0002.py`/`test_ftf_0003.py`/
  `test_ftf_h001.py` — a missing `pnpm`/frontend-tooling install,
  confirmed identical via a `git stash` before/after comparison, not a
  regression this task introduced). `uv run ruff check .`/`ruff format
  --check .`/`mypy .` clean repo-wide (177 source files). `alembic heads`
  confirms a single linear head (`0010_dataset_source_connections`) —
  no fork introduced. `scripts/validate_spec.py --strict` clean after
  `git fetch --unshallow origin` (this sandbox's own pre-existing,
  already-documented shallow-clone artifact, confirmed pre-existing via a
  direct before/after check). `scripts/product_truth_check.py
  --fail-on-unwired`: `13/13` → `14/14` (this task's own real live route,
  `build_dataset_source_connection_router`, called for real from
  `apps/api/app/main.py` as the tenth live product-runtime path).
  `scripts/status.py` stays `43/300 certified · 13 product-true`
  (unchanged — FTF-0500 is PROVISIONAL, not yet CERTIFIED, Law 2).
- Dependencies FTF-0401/FTF-0202/FTF-H003 confirmed genuinely `CERTIFIED`
  in `docs/spec-matrix.yaml` before relying on them.
- **Submitted PROVISIONAL, not self-certified (Law 4).**

## 2026-09-22 — FTF-H006 — Shared outbound URL and credential guard
- **PR:** to be opened from `claude/wizardly-fermat-83mm0h` → `main`,
  title `[relay] FTF-H006: Shared outbound URL and credential guard`.
- **Commits:** `db727c2` (generated task packet), `ae2dcb9` (implementation
  — `packages/security/url_policy.py`/`outbound_guard.py` and
  `tests/tasks/test_ftf_h006.py`), and a third, tracking-doc-only commit
  (`docs/spec-matrix.yaml`/`QUEUE.md`/`PROGRESS.md`/this entry) on the
  same branch, matching the established two/three-commit precedent.
- **Ground truth checked before picking work:** STEP 0 verified
  `sha256sum CLAUDE.md` against the committed `CLAUDE.md.sha256` — clean.
  This session's designated branch did not exist on `origin` yet; its
  local starting history turned out to be identical to PR #196 (FTF-0500,
  merged), so it was reset to fresh `origin/main` per the merged-branch
  instruction rather than stacked on already-merged history. Recomputed
  the dependency-ready frontier fresh by joining `docs/spec-matrix.yaml`'s
  live `certification_state` against `docs/TASK_INDEX.md`'s real
  `Dependencies` column: `FTF-0304` (two open, unmerged candidate PRs
  #194/#195 from other sessions — not picked, to avoid duplicating real
  work), `FTF-0408`/`FTF-0902` (both re-confirmed still genuinely blocked
  for the same disclosed reasons already on record), `FTF-1503`,
  `FTF-1506`, `FTF-H006` (all three genuinely dependency-ready and
  unclaimed). Picked `FTF-H006` over the other two dependency-ready items
  because `docs/TASK_INDEX.md` lists it as a direct dependency of the most
  other still-blocked tasks (`FTF-0203`, `FTF-0302`, `FTF-0303`,
  `FTF-04A01`, `FTF-0500B`, `FTF-0500C`, `FTF-H007`, `FTF-H012`,
  `FTF-H027`) — the highest-leverage unblocked item this shift, not merely
  the lowest task ID. `FTF-H006`'s own dependencies (`FTF-0005`,
  `FTF-0201`, `FTF-0202`, `FTF-H003`) were independently confirmed
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- **Task contract:** `python tools/r1_handoff.py packet FTF-H006 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — goal ("Validate scheme/host/port/
  redirect/DNS resolved addresses against explicit connection policies,
  with SSRF and cloud metadata protections for all outbound clients"),
  acceptance ("Exercise real controlled HTTP endpoints and DNS/redirect
  fixtures covering loopback, private allowlist, link-local metadata,
  IPv6, alternate encodings and rebinding"), negative_acceptance ("Private
  destinations can be explicitly authorized; a blanket private-address
  rejection cannot break intended internal LiteLLM/storage while metadata
  remains denied"), `shared_lock_required: [security:trust-boundary]`,
  `files: [packages/security/outbound_guard.py,
  packages/security/url_policy.py]`, `dependencies: [FTF-0005, FTF-0201,
  FTF-0202, FTF-H003]` (all CERTIFIED).
- **Scope disclosed, not hidden:** this task's own `files:` list scopes it
  to the shared guard library itself, not to wiring it into any existing
  route. `apps/api/app/litellm/connection_schema.py`'s own
  `validate_base_url` already states this in its own docstring ("this
  module never dials the URL -- that is FTF-0203's job") — `FTF-0203`
  ("Connection validation") is this task's own first listed dependent in
  `docs/TASK_INDEX.md`, and is the task that will actually construct a
  `ConnectionPolicy`/call `guarded_fetch` from a live route. No live
  FastAPI route does either yet; `product_truth_check.py` stays `14/14`,
  unchanged, same disclosed "no live caller yet" class already accepted
  for FTF-0005/FTF-0006/FTF-0102/FTF-0103/FTF-0104/FTF-0105/FTF-H003
  before each one's own first live caller existed.
- **Design:** `packages/security/url_policy.py` — `ConnectionPolicy`
  (allowed schemes/ports, `explicit_private_allowlist` of real internal
  networks, `allow_redirects`/`max_redirects`); `validate_url_shape`
  refuses `file`/`gopher`/`ftp` schemes and URL userinfo unconditionally
  (no policy can re-enable them), and any scheme/port outside the
  policy's own allowlist; `validate_resolved_address` denies loopback/
  link-local/private/reserved/multicast/unspecified addresses by default,
  overridable per policy, **except** cloud-metadata addresses
  (`169.254.169.254`, `100.100.100.200`, `fd00:ec2::254`, and the literal
  metadata hostnames), which are refused even under a broad allowlist
  entry that would otherwise cover them — this task's own
  negative_acceptance read literally: an allowlist authorizes a real
  internal destination, never the metadata identity itself;
  `normalize_host_literal` recognizes every alternate IPv4/IPv6 encoding
  named in the acceptance clause (bare 32-bit integer, octal/hex dotted-
  quad, the legacy `inet_aton` "fewer than four parts" collapsing form,
  bracketed/IPv4-mapped IPv6) so a URL host component can never smuggle a
  real address past a naive string check. `packages/security/
  outbound_guard.py` — `guarded_fetch` is the only function in this
  repository allowed to open a socket on a caller-supplied host:
  `resolve_and_validate` resolves via real `socket.getaddrinfo` and
  validates **every** returned address (fail-closed on any one disallowed
  answer, not just the one used), then the connection is pinned to that
  exact validated address via `_PinnedHTTPConnection`/
  `_PinnedHTTPSConnection` (custom `http.client` subclasses overriding
  `connect()`) — a later, separate DNS answer can never substitute a
  different address for the one actually checked (the rebinding defense
  this task's own acceptance clause names), while TLS still validates the
  real hostname via `server_hostname` despite the pinned-IP connect. A
  3xx response is returned as-is unless `policy.allow_redirects`, in which
  case the target is fully revalidated and redialed from scratch via a
  recursive `guarded_fetch` call, bounded by `max_redirects` — both halves
  of Section 65.4's "default no redirects... revalidate ... every allowed
  redirect." No new runtime dependency: stdlib `socket`/`ssl`/
  `http.client` only, consistent with CLAUDE.md's ask-first bar for new
  runtime deps.
- **Two real defects found and fixed before submission (Law 1, disclosed
  rather than hidden), neither merely described:**
  1. `resolve_and_validate` originally returned `(ip_str, family)` tuples
     while `guarded_fetch` destructured them as `(family, ip_str)` —
     invisible until the first real end-to-end `guarded_fetch` call
     against this suite's own local HTTP server actually attempted a
     connection and the raw socket-family integer (not an IP string) hit
     `socket.create_connection`, raising immediately. Fixed by making the
     return order match what the caller actually consumes.
  2. The DNS-rebinding proof test's own `socket.getaddrinfo` mock
     initially escalated to a disallowed address on *every* second call
     to it, not scoped to the hostname under test — since the real
     `socket.create_connection` internally re-resolves even a literal IP
     it was already given, this tripped the test's own hostile-second-
     answer logic on that unrelated internal call and produced a spurious
     connection timeout, not a real defect in `outbound_guard.py` itself.
     Fixed by scoping the mock's escalation to the specific test hostname
     and passing every other host straight through to the real resolver
     — matching how DNS rebinding actually works (a literal IP is never
     subject to it; only a named host is).
- **Evidence:** `tests/tasks/test_ftf_h006.py`, 33 no-DB, no-external-
  egress checks. Real, not mocked, wherever real is possible: a genuine
  `http.server.ThreadingHTTPServer` on `127.0.0.1` this suite itself
  starts and drives `guarded_fetch` against — denied by default, reachable
  once explicitly allowlisted, unreachable via a decimal/hex/octal-encoded
  host string until that same encoding normalizes to the same allowlisted
  loopback address; a default (no `allow_redirects`) fetch against a real
  redirecting endpoint returns the 3xx itself rather than following it; an
  `allow_redirects` fetch genuinely follows and re-dials; a real redirect
  loop is bounded by `max_redirects`, not infinite. Cloud-metadata denial
  is proven at the pure policy layer (this sandbox has no route to a real
  metadata service and must not attempt one — a real connection attempt
  would consult exactly this same check first). The rebinding proof mocks
  only DNS resolution (this sandbox controls no real DNS zone) while the
  socket connection stays real: `getaddrinfo` is proven to run exactly
  once per fetch and the live connection is proven to land on exactly the
  one validated address, even though a rigged second resolution would
  have answered with a disallowed one. A real fault-injection test
  disables the cloud-metadata check on the tracked `url_policy.py` module
  on disk, confirms in a fresh subprocess that the metadata address this
  suite depends on being denied genuinely flips to allowed, then restores
  byte-identical (`read_bytes()` comparison, not merely re-running the
  suite).
- **IPv6 disclosed, not hidden:** this sandbox has no IPv6 stack at all
  (confirmed via a real `AF_INET6` bind attempt to `::1`, not merely
  `socket.has_ipv6`) — disclosed explicitly at test-module load time. The
  live `::1` `guarded_fetch` sub-check is skipped only in that one sandbox
  condition; GitHub Actions' own `ubuntu-latest` runners support IPv6
  loopback, so CI exercises it for real. The pure IPv6 policy checks
  (`::1` denial, an IPv4-mapped IPv6 literal normalizing correctly) do not
  depend on IPv6 sockets at all and always run regardless.
- **Full regression:** `ruff check .`/`ruff format --check .` clean for
  this task's own files; 7 pre-existing `UP042` findings and 34
  pre-existing reformat-needed files confirmed unrelated (none are files
  this task touched). `mypy .`: found and fixed one real typing gap in
  this task's own new code — typeshed's `HTTPSConnection` stub omits the
  real (but underscore-prefixed) `_context` attribute CPython actually
  sets, so `_PinnedHTTPSConnection` stores the TLS context under its own
  attribute name instead — clean after the fix. `pnpm run lint`/
  `typecheck` clean. `scripts/validate_spec.py --strict` clean, after
  `git fetch --unshallow` — this sandbox's own pre-existing, already-
  documented shallow-clone artifact (confirmed pre-existing via a before/
  after check on `tests/tasks/test_ftf_h001.py`'s own `stale_commit`
  defects, not caused by this task). Full `tests/tasks/test_ftf_*.py`
  sweep run directly: every file passes after `uv sync --all-packages` +
  `pnpm install` resolved this fresh sandbox's own missing `uvicorn`/
  `sqlalchemy`/`node_modules` (same documented precedent as prior shifts).
  `scripts/product_truth_check.py --fail-on-unwired`: `14/14`, unchanged
  (this task adds no live caller by design). `scripts/status.py` stays
  `43/300 certified · 13 product-true` (unchanged — FTF-H006 is
  PROVISIONAL, not yet CERTIFIED, Law 2).
- Dependencies FTF-0005/FTF-0201/FTF-0202/FTF-H003 confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- **Submitted PROVISIONAL, not self-certified (Law 4).**

## 2026-09-22 — Control plane: FTF-0500 (PR#196) independently reviewed, CERTIFIED
Reviewing session (scheduled certifier, `pull_request.opened` webhook for
PR #197): `session_0179fcCZuwbpcEh8NMQVj997`. This review independently
picked up PR #196 (FTF-0500), the other PROVISIONAL item already sitting
in this file at the time this shift started, alongside PR #197
(FTF-H006, still CI-in-progress and not yet merged at review time — left
PROVISIONAL, not reviewed in this entry).

Session-ID compared against PR #196's commits (all three carry
`session_013G1NHePkjpX2VLPJb57on2`) — no overlap, independent per Law 4.

Re-ran the CERTIFY procedure in this session's own worktree at the real
merged tip:
- `pull_request_read` on PR #196 — `state: closed`, `merged: true`,
  `merged_by: samerzakaria`, merge commit
  `07b831d44d3c5a5875c5a63b40382c687455d1de` (also PR #197's own stated
  base — confirms ordering).
- `get_check_runs` on PR #196's real pre-merge head
  (`d016f751511073d41f63e1f52ffbd3a8cc214e76`, run 35760776024) — 24/24
  jobs `success`, including `gate` and the new
  `dataset-source-connection-integration` job this task added.
- Diff read directly (`git diff 8f33de0..d016f75`): schema/CRUD/routes,
  a real forward Alembic migration with FORCE ROW LEVEL SECURITY and a
  real tenant-scoped `CREATE POLICY` (confirmed by direct grep), the
  phase-05 contract freeze, CI wiring.
- Own reproduction in a fresh git worktree: `uv run pytest
  tests/tasks/test_ftf_0500.py -q` — 22/22. Noted this suite is
  script-style (CI runs it as `uv run python3 tests/tasks/test_ftf_0500.py`,
  aggregating a `FAILURES` list and calling `sys.exit(1)`, not via
  `pytest`) — re-ran it CI's own way for a trustworthy fault-injection
  signal.
- Own fault injection, a vector distinct from the producer's own two:
  disabled the `connection_status != "active"` denial in
  `authorize_purpose_use` — re-ran CI's own invocation, exit 1, "2
  failed", naming exactly the two negative_acceptance checks this covers
  ("once revoked, a PREVIOUSLY granted purpose (training) is now
  denied", "once revoked, every other declared purpose is denied too").
  Reverted from a pre-edit backup, `diff` confirmed byte-identical,
  re-ran clean (exit 0, "all checks passed").
- `scripts/product_truth_check.py --fail-on-unwired` — own run, `14/14`;
  `proving_symbol: build_dataset_source_connection_router` confirmed a
  real, non-test live caller in `apps/api/app/main.py` (grepped
  directly), matching the task's own claimed `13/13` → `14/14` delta.
- `python3 tools/r1_handoff.py packet FTF-0500 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — both acceptance clauses and the
  full negative_acceptance clause confirmed genuinely met by the diff
  and this session's own fault injection above, not merely "tests
  pass". `contract_owner: FTF-0500` confirmed real — both
  `packages/contracts/openapi/05-interface.yaml` and
  `packages/contracts/jsonschema/05-domain.schema.json` are new files in
  this diff, did not exist before.
- Dependencies FTF-0401/FTF-0202/FTF-H003 confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml`.
- `ruff check` on the touched files clean. `scripts/validate_spec.py
  --strict` clean after `git fetch --unshallow origin` (this sandbox's
  own pre-existing shallow-clone artifact, not caused by this task).

No blocking defect found. `docs/spec-matrix.yaml` set to
`certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
`evidence_hash` set to the merge commit
(`07b831d44d3c5a5875c5a63b40382c687455d1de`). Ledger entry appended
(`docs/certifications/ledger.md`, "FTF-0500 — DatasetSourceConnection
schema/API/RBAC"). Ran `scripts.task_claim.sync_certified_status()` —
`spec/tasks.json`/`docs/implementation/TRACKER.json` updated for
`['FTF-0500']`. `QUEUE.md`/`PROGRESS.md` updated: FTF-0500 moved to
Done, Certified count bumped to 44/300, Product-true 14/14.

FTF-H006 (PR #197) remains PROVISIONAL — this scheduled shift's
`pull_request.opened` trigger event, but CI had not finished on its head
commit at review time (6 of 24 jobs still `in_progress`, including
`gate`). Not certified in this entry; left for a follow-up review once
CI concludes and, if applicable, the PR merges.

## 2026-09-22 — Control plane: FTF-H006 (PR#197) independently reviewed, CERTIFIED
Reviewing session (scheduled certifier, `pull_request.opened` webhook for
PR #197): `session_0179fcCZuwbpcEh8NMQVj997`. Follow-up to the entry
above: PR #197's CI had not finished at first review; by the time
FTF-0500's own certification PR (#198) merged, PR #197 could be checked
again.

Session-ID compared against PR #197's original three commits (all carry
`session_019ApmY6ESBah3nPdBDYq1XL`) — no overlap, independent per Law 4.

- `get_check_runs` on PR #197's original head — now 24/24 `success`.
- `merge_pull_request` failed with `405 ... merge conflicts` — real, not
  assumed: PR #198 (merged just before) touched the same tracking files
  (`QUEUE.md`, `docs/certifications/queue.md`) PR #197's own tracking
  commit touched. Resolved directly: checked out PR #197's head, merged
  `origin/main` in, resolved both conflicts by keeping both sides'
  content in full (the producer's own FTF-H006 submission narrative,
  then this session's own FTF-0500 certification entry, concatenated,
  nothing dropped), re-validated (`scripts/status.py`,
  `product_truth_check.py --fail-on-unwired`, `validate_spec.py
  --strict`, `CLAUDE.md.sha256`) — all clean, pushed the real merge
  commit (`ea1862687e026d9c7855b3489487b9b0cdce24fc`) to PR #197's own
  branch (a merge, not a rewrite of the producer's own commits).
  `get_check_runs` on this new head — 24/24 `success`, including `gate`
  and the two slower integration jobs. Merged PR #197
  (`99e3d3d0be60d7f5491e99965809a29c62efb4ce`).
- Diff read directly: `packages/security/url_policy.py` (scheme/
  userinfo/port shape validation, resolved-address validation with an
  explicit private-destination allowlist, unconditional cloud-metadata
  denial checked before the general private-range rule, alternate-IP-
  literal-encoding normalization) and `packages/security/
  outbound_guard.py` (`guarded_fetch` — DNS-resolve-and-validate-once,
  connect pinned to the exact validated address via a custom
  `http.client` connection subclass, default-no-redirects with full
  revalidation on explicit opt-in).
- Own reproduction, fresh git worktree: `uv run python3
  tests/tasks/test_ftf_h006.py` — "all checks passed" (run CI's own way,
  a script-style suite like FTF-0500's own).
- Own fault injection, a vector distinct from the producer's own two:
  disabled the cloud-metadata identity check in
  `validate_resolved_address`. The suite's own embedded fault-injection
  self-test failed for real (source-text assertion, exit 1). Also
  independently exercised the runtime behavior directly (not just the
  self-test): with the check disabled, an operator allowlisting
  `169.254.0.0/16` (the exact "blanket private-address rejection cannot
  break intended internal ... while metadata remains denied"
  negative_acceptance scenario) genuinely let the metadata address
  `169.254.169.254` through unvalidated — a real SSRF-to-cloud-
  credentials exposure. Reverted from a pre-edit backup, `diff`
  confirmed byte-identical, re-ran — correctly denied again; full suite
  re-confirmed clean.
- DNS-rebinding proof (`test_dns_resolved_once_and_connection_pinned_to_
  that_address`) read directly: real local HTTP server, mocked DNS
  resolution answering a hostile second address only for the hostname
  under test, spying `socket.create_connection` — confirms resolution
  happens exactly once and the real socket connects only to the
  first-validated address.
- `scripts/product_truth_check.py --fail-on-unwired` — own run, `14/14`,
  unchanged; `proving_symbol: ''` confirmed legitimate — this task
  builds the shared guard library, not a live route (no FastAPI route
  calls `guarded_fetch` yet, confirmed by direct grep; that is FTF-0203's
  job per `connection_schema.py`'s own docstring).
- `python3 tools/r1_handoff.py packet FTF-H006 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — the acceptance clause and the full
  negative_acceptance clause confirmed genuinely met by the diff and
  this session's own fault injection above.
- Dependencies FTF-0005/FTF-0201/FTF-0202/FTF-H003 confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml`.
- `ruff check` on the touched files clean. `scripts/validate_spec.py
  --strict` clean.

No blocking defect found. `docs/spec-matrix.yaml` set to
`certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
`evidence_hash` set to the merge commit
(`99e3d3d0be60d7f5491e99965809a29c62efb4ce`). Ledger entry appended
(`docs/certifications/ledger.md`, "FTF-H006 — Shared outbound URL and
credential guard"). Ran `scripts.task_claim.sync_certified_status()` —
`spec/tasks.json`/`docs/implementation/TRACKER.json` updated for
`['FTF-H006']`. `QUEUE.md`/`PROGRESS.md` updated: FTF-H006 moved to
Done, Certified count bumped to 45/300, Product-true stays 14/14
(unchanged — this task adds no live caller by design).

## 2026-09-22 — FTF-0203 — Connection validation
- **PR:** to be opened from `claude/wizardly-fermat-0knkdx` → `main`,
  title `[relay] FTF-0203: Connection validation`.
- **Commits:** `c3cac3a` (generated task packet), `712ebbf9467e5c8154d24391e1c9b0abad52fd59`
  (implementation — `packages/litellm_client/connection_validation.py`,
  `apps/api/app/litellm/connection_routes.py`, `apps/api/app/main.py`,
  `tests/tasks/test_ftf_0203.py`, `tests/integration/
  test_ftf_0203_connection_validation.py`, `.github/workflows/ci.yml`),
  and a third, tracking-doc-only commit (`docs/spec-matrix.yaml`/
  `QUEUE.md`/`PROGRESS.md`/this entry) on the same branch, matching the
  established two/three-commit precedent.
- **Ground truth checked before picking work:** STEP 0 verified
  `sha256sum CLAUDE.md` against the committed `CLAUDE.md.sha256` — clean.
  This session's designated branch (`claude/wizardly-fermat-0knkdx`) had
  no PR ever opened under that name anywhere in the repo (confirmed via
  `search_pull_requests`/`list_pull_requests`) and no longer existed on
  `origin` — reset to fresh `origin/main` per the merged-branch
  instruction. Recomputed the dependency-ready frontier fresh, joining
  `spec/tasks.json`'s real `dependencies` field against
  `docs/implementation/TRACKER.json`'s live `status`: `FTF-0203`,
  `FTF-0302`, `FTF-0303`, `FTF-0304`, `FTF-0408`, `FTF-04A01`,
  `FTF-0500A`, `FTF-0500D`, `FTF-0506`, `FTF-0902`, `FTF-1503`,
  `FTF-1506`, `FTF-R002` — 13 genuinely dependency-ready items.
  `FTF-0304` already has two open, unmerged candidate PRs (#194/#195)
  from other sessions — not picked, to avoid duplicating real work.
  Picked `FTF-0203`: lowest task ID among the remaining unclaimed items,
  no external-service ask of its own, and the task FTF-H006's own prior
  submission explicitly named as "this repository's first real caller"
  of both `guarded_fetch` and `resolve_secret_ref`.
- **Task contract:** `python tools/r1_handoff.py packet FTF-0203 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — `api_contract`
  ("/api/v1/connections/litellm; validate/sync-models/syncs; /models and
  /models/{id}/probe" — this task's own acceptance scopes it to
  `validate` only, per `connection_routes.py`'s own prior disclosure that
  `sync-models`/`/models`/probe are FTF-0204/FTF-0210's), acceptance
  ("Validate real gateway TLS/auth/reachability and safe
  management/inference capabilities separately"), negative_acceptance
  ("Timeout, certificate error, invalid token and management-403 produce
  distinct sanitized states, not connected badge"), `database_changes`:
  "No independent schema change", `files: [packages/litellm-client/
  connection_validation.py, tests/tasks/test_ftf_0203.py]`,
  `dependencies: [FTF-0201, FTF-0202, FTF-H006]` (all CERTIFIED).
- **Scope disclosed, not hidden:** `files:` path refined from the
  packet's own hyphenated `packages/litellm-client/` to
  `packages/litellm_client/` (a hyphenated directory segment is not a
  valid Python package/module name — same refinement precedent
  `secret_manager_integration.py` already established under the exact
  same `packages/litellm_client/` directory). The `api_contract`'s wider
  `sync-models`/`/models`/`/models/{id}/probe` surface is deliberately
  not declared here either, matching `connection_routes.py`'s own
  standing disclosure — those remain FTF-0204/FTF-0210's own, later,
  separately dependency-gated acceptance criteria.
- **Design:** `packages/litellm_client/connection_validation.py` —
  `validate_connection` resolves the connection's own `secret_ref`
  through FTF-0202's already-CERTIFIED `resolve_secret_ref` (this
  repository's first real caller of it for the LiteLLM connection
  family), then calls the real gateway's `GET /v1/models` (Section 3.1)
  through FTF-H006's already-CERTIFIED `guarded_fetch` (this
  repository's first real caller of it). `InferenceCheckState` gives
  every real distinct failure its own sanitized state (`invalid_token`
  on a real 401/403, `certificate_error` on a real
  `ssl.SSLCertVerificationError`, `timeout`, `dns_failed`,
  `connection_refused`, `destination_denied` at the policy layer,
  `malformed_response`, and every distinct secret-resolution failure) —
  never folded into a generic "not connected" and never fabricated as
  "connected". `GET /model/info` enrichment is a fully separate
  `ManagementCheckState` dimension, only attempted once the inference
  dimension itself reports `connected` (Section 3.1: "403/404 on
  enrichment does not invalidate a successful standard model list").
  `apps/api/app/litellm/connection_routes.py` adds the real `POST
  .../connections/litellm/{connection_id}/validate` route
  (`organization_admin` only, same tenant-scoped session and
  IDOR-denial pattern as every other route on this router — a foreign
  connection ID under the wrong organization's own path is denied
  identically to missing, 404, before the real gateway is ever dialed on
  the wrong tenant's behalf); refuses to validate a revoked connection
  (409, matching the existing edit-of-revoked precedent).
  `secret_manager_config_factory`/`validation_policy_factory` both
  follow the exact call-time-only DI convention FTF-0500 established.
- **Real design correction found before submission (Law 1, disclosed):**
  the route's default validation policy correctly denies loopback
  destinations (SSRF protection, Section 65.4) — which also meant the
  integration proof could never observe a real "connected" result
  against any locally reachable gateway, since this sandbox has no live
  external network to test against either (the relay sandbox's own
  standing "cannot reach external APIs with open egress" constraint).
  Fixed by adding `validation_policy_factory` as a new injectable,
  mirroring the existing `secret_manager_config_factory` DI convention
  exactly, rather than weakening the production default.
- **Evidence — `tests/tasks/test_ftf_0203.py`** (48 no-DB,
  no-external-egress checks): a real local HTTP server standing in for
  the OpenBao secret backend (exercising FTF-0202's own
  `resolve_secret_ref` unmodified, never mocked); real local HTTPS
  gateways, with a real, freshly generated CA this suite installs into
  the sandbox's own system trust store for the run's duration (removed
  again in `atexit`) so the "connected" happy path exercises real,
  unmodified `ssl.create_default_context()` certificate validation, and
  a second, deliberately never-installed self-signed leaf for the
  `certificate_error` path; a real accepted connection that never
  answers within the bounded timeout; a real closed TCP port for
  `connection_refused`; DNS-failure classification proven as a direct
  real-exception-instance unit check of the classifier (this sandbox has
  no egress-independent way to force a real `socket.gaierror` — Section
  84.4's "test doubles... exercise isolated unit... cases").
- **Own fault injection (Law 1):** a regex-based mutation of the real
  on-disk module (survives a future `ruff format` reflow, unlike a
  literal-text match) makes a denied secret-backend token silently
  fabricate a credential string instead of returning
  `SECRET_ACCESS_DENIED`; run in a fresh subprocess against a real
  gateway configured to accept that exact fabricated string, the mutated
  module genuinely reports `connected` — proving the removed
  `SecretAccessDeniedError` handling is load-bearing, not decorative.
  Reverted from the original bytes; `read_bytes()` comparison confirmed
  byte-identical; suite re-ran clean.
- **Evidence — `tests/integration/test_ftf_0203_connection_validation.py`**
  (real live-Postgres + real local gateway proof, 16 checks, mirroring
  FTF-0201's own fixture/IDOR pattern): two real organizations, a real
  uvicorn server, real create/validate/revoke through the actual FastAPI
  route (never a direct call to `validate_connection` bypassing the HTTP
  layer); a real reachable+authorized gateway reports `connected` with a
  real `model_count` and `management_state=available`; a real
  token-rejecting gateway reports `invalid_token`, distinct from
  `connected`, at the same 200 HTTP status (the negative_acceptance
  state lives in the response body); the cross-organization IDOR case (a
  real connection ID belonging to org1, requested under org2's own path
  by a real org2 `organization_admin`) denied identically to missing
  (404); a real revoke followed by a real validate attempt refused
  (409). Produced against a real, directly-installed local PostgreSQL 16
  (`sovra_migrate` role + `sovra_ftf0203` database, same precedent
  FTF-0004/FTF-0102/FTF-0201/FTF-0500 already established); `FTF-0201`'s
  own integration suite re-confirmed passing afterward against a fresh
  database (no regression to the shared `connection_routes.py` file).
  Disclosed, not hidden: this shared local cluster's `sovra_app` role is
  cluster-global, not per-database, so re-running two different tasks'
  migrations back to back in the same local cluster without dropping the
  role/database between runs collides on `CREATE ROLE sovra_app` — a
  real local-sandbox artifact of reusing one cluster across tasks, not a
  defect in either migration; CI's own per-job fresh `services: postgres`
  container never hits this.
- **CI:** `.github/workflows/ci.yml` wires the no-DB suite into the
  `gate` job and adds `connection-validation-integration` (real Postgres
  service, same pinned image digest every other integration job already
  uses). The integration suite's own secret backend is a real local HTTP
  server implementing exactly the OpenBao KV v2 read shape
  `secret_manager_integration.py` actually calls, not the real OpenBao
  binary `secret-manager-integration`/`dataset-source-connection-integration`
  build from source — FTF-0202's own backend behavior is already
  independently proven there; this task's own acceptance clause is about
  the gateway TLS/auth/reachability dimension, so a lighter, real (never
  mocked) HTTP fixture is the right-sized proof here, disclosed rather
  than silently reusing a heavier build for no added coverage.
- **Full regression:** `ruff check .`/`ruff format --check .` clean (180
  files). `mypy .` clean (180 files) — found and fixed one real typing
  gap in this task's own test file (`Path | None` module globals passed
  where a plain `Path` was declared; fixed by widening the parameter
  type and asserting once inside the shared helper). `pnpm run
  lint`/`typecheck` clean after `pnpm install` resolved this fresh
  sandbox's own missing `node_modules`. `scripts/validate_spec.py
  --strict` clean after `git fetch --unshallow` (this sandbox's own
  pre-existing, already-documented shallow-clone artifact). Full
  `tests/tasks/test_ftf_*.py` sweep run directly: every file passes.
  `product_truth_check.py --fail-on-unwired`: `14/14`, unchanged (Law 2
  — certification is a separate Control-plane act; `proving_symbol`
  deliberately left blank in `docs/spec-matrix.yaml` rather than the
  producer claiming its own product-true count — once certified,
  `validate_connection` would be a real, non-test source-root match in
  `connection_routes.py`'s own live `/validate` route). `scripts/status.py`
  stays `45/300 certified · 14 product-true` (unchanged — PROVISIONAL,
  not yet CERTIFIED, Law 2).
- Dependencies FTF-0201/FTF-0202/FTF-H006 confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- **Submitted PROVISIONAL, not self-certified (Law 4).**

## 2026-09-22 — Control plane: FTF-0203 (PR#200) independently reviewed, CERTIFIED
Reviewing session (scheduled certifier, `pull_request.opened` webhook for
PR #200): `session_013qTQ2DdZRMRjiGC8DDeVnS`. PR #200 was the sole
PROVISIONAL item in this file at shift start.

Session-ID compared against PR #200's four commits (all carry
`session_01AE9mEUKyK7BVB93dTEohRn`) — no overlap, independent per Law 4.

- `get_check_runs` on PR #200's original head (`ba5f27a`, run
  35785636597) — `connection-validation-integration` failed for real:
  `PermissionError: [Errno 13] Permission denied: '/usr/local/share/
  ca-certificates/ftf-0203-integration-test-ca.crt'` (read the actual
  job log directly). Root cause: the test suite writes the ephemeral CA
  into the system trust store without `sudo`, which succeeds on a
  root-run sandbox (this reviewing session's own — confirmed directly,
  `touch /usr/local/share/ca-certificates/...` succeeded trivially) but
  not on GitHub Actions' `ubuntu-latest` runner (non-root). The
  producer's own session diagnosed and fixed this identically while this
  review was already in progress (commit `9f94a3c` — switched both
  `tests/tasks/test_ftf_0203.py` and `tests/integration/
  test_ftf_0203_connection_validation.py` to `sudo cp`/`sudo
  update-ca-certificates`, with `_UPDATE_CA_AVAILABLE` additionally
  requiring `sudo -n true` to succeed first). This review watched the
  real re-run (head `9f94a3c`, run 35785873270) to completion — **25/25
  real jobs `success`**, including `connection-validation-integration`
  (40s) and `gate` (2m03s) — before proceeding, not a stale or
  self-reported claim.
- Diff read directly: `packages/litellm_client/connection_validation.py`
  (`validate_connection` — resolves `secret_ref` via FTF-0202's already-
  CERTIFIED `resolve_secret_ref`, dials `GET /v1/models` via FTF-H006's
  already-CERTIFIED `guarded_fetch`, reports the inference and
  management dimensions as two fully separate states, never raises for
  an ordinary connectivity/auth/secret failure) and
  `apps/api/app/litellm/connection_routes.py` (real `POST
  .../connections/litellm/{connection_id}/validate` route,
  `organization_admin`-only, reads `base_url`/`secret_ref` inside the
  same tenant-scoped session as every other route before releasing it
  and dialing the gateway outside that session, 404 on a foreign-tenant
  connection ID, 409 on a revoked one). Diff scope matches the task
  packet's own `files` list — no scope creep.
- Own reproduction, a separate git worktree at the real merged head
  (`9f94a3c`, not the producer's own claimed run): `uv sync
  --all-packages`; `uv run python3 tests/tasks/test_ftf_0203.py` — "all
  checks passed" (48/48). A real local PostgreSQL 16 role/db
  (`sovra_migrate`/`sovra_ftf0203`, `sovra_migrate` granted `SUPERUSER`
  to match the official `postgres` Docker image's `POSTGRES_USER`-is-
  superuser default GitHub Actions' `services: postgres:` container
  relies on — a local-sandbox setup detail, not a product defect); `uv
  run python3 tests/integration/test_ftf_0203_connection_validation.py`
  — "all checks passed" (16/16). `tests/tasks/test_ftf_0201.py`/
  `tests/integration/test_ftf_0201_litellm_connection_crud.py` re-run
  clean against a fresh database — no regression to the shared
  `connection_routes.py` file this task also touches.
- Own fault injection, a vector distinct from the producer's own two
  (`SecretAccessDeniedError` fabrication; DNS/timeout/refused-connection
  classification): disabled the `response.status in (401, 403)`
  invalid-token check in `validate_connection` (`if False and
  response.status in (401, 403):`) and re-ran the no-DB suite — exactly
  1 check failed for real: "a real gateway 401 on a resolved credential
  reports invalid_token, not connected" — precisely the
  negative_acceptance risk this task exists to prevent (a rejected
  credential silently reported as a connected badge). Reverted from a
  pre-edit backup, `diff` confirmed byte-identical, suite re-ran clean.
- `ruff check .`/`ruff format --check .` clean (180 files); `mypy .`
  clean (180 files, "Success: no issues found"). `scripts/
  validate_spec.py --strict` clean after `git fetch --unshallow origin`
  (this worktree's own pre-existing shallow-clone artifact).
  `CLAUDE.md.sha256` verified against `sha256sum CLAUDE.md` on the real
  merged head.
- `scripts/product_truth_check.py --fail-on-unwired` — own run, `14/14`
  before this certification; `proving_symbol: validate_connection`
  confirmed a real, non-test, live-caller match in
  `apps/api/app/litellm/connection_routes.py` (grepped directly, not
  assumed) — this repository's second live-wired product-runtime path
  (after FTF-0201's own CRUD routes) to actually call this symbol.
- `python3 tools/r1_handoff.py packet FTF-0203 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — both acceptance clauses ("Validate
  real gateway TLS/auth/reachability and safe management/inference
  capabilities separately"; "Independent review, exact merged commit,
  real relevant integration and negative-test evidence recorded") and
  the full negative_acceptance clause ("Timeout, certificate error,
  invalid token and management-403 produce distinct sanitized states,
  not connected badge") confirmed genuinely met by the diff and this
  session's own fault injection above, not merely "tests pass" —
  `contract_owner: FTF-0201` confirmed real.
- Dependencies FTF-0201/FTF-0202/FTF-H006 confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.

No blocking defect found. `docs/spec-matrix.yaml` set to
`certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
`proving_symbol: validate_connection`, `evidence_hash` set to the merge
commit (`c6e51a2fb267a6ef26791eb9fccc79cbfd0fc4b4`). Ledger entry
appended (`docs/certifications/ledger.md`, "FTF-0203 — Connection
validation"). Ran `scripts.task_claim.sync_certified_status()` —
`spec/tasks.json`/`docs/implementation/TRACKER.json` updated for
`['FTF-0203']`. `QUEUE.md`/`PROGRESS.md` updated: FTF-0203 moved to
Done, Certified count bumped to 46/300, Product-true 14/14 → 15/15.

## 2026-09-22 — FTF-0204 — `/v1/models` discovery
- **PR:** (relay branch `claude/wizardly-fermat-pkklwn` → main; opened as
  `[relay] FTF-0204: /v1/models discovery`)
- **Commit:** c1ac87cdeb6fef3b412c248ab2b01472614e6e02
- **Real DoD source:** task packet resolved via `python3
  tools/r1_handoff.py packet FTF-0204 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` (`docs/implementation/task-packets/
  FTF-0204.md`). Dependencies FTF-0201/FTF-0203 confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- **Delivered:** `packages/litellm_client/v1_models_discovery.py`
  (`discover_models`) resolves the connection's own `secret_ref` through
  FTF-0202's already-CERTIFIED `resolve_secret_ref`, calls the real
  gateway's `GET /v1/models` (Section 3.1) through FTF-H006's
  already-CERTIFIED `guarded_fetch`, and enriches each authorized model
  ID with `GET /model/info` on a strict left-join basis (Section 3.2's
  "no authorization union" — a name known only through enrichment is
  never added to the returned list, only ever sets a
  `metadata_status=enriched` flag on an already-authorized entry). Every
  call is stateless and fresh (Section 3.3) — nothing here merges with a
  previous result. A real sha256 `content_hash` over the sorted
  authorized ID set is recorded alongside a real `checked_at` timestamp
  and `model_count`, matching this task's own acceptance clause ("record
  timestamp/count/hash"). `apps/api/app/litellm/connection_routes.py`
  adds the real `GET .../connections/litellm/{connection_id}/models`
  route (this repository's first live caller of `discover_models`),
  `organization_admin`-only, same tenant-scoped IDOR-denial and
  revoked-connection-409 pattern FTF-0203's own `/validate` route
  already established, reusing the same injected
  `secret_manager_config_factory`/`validation_policy_factory` rather
  than adding a redundant pair. No independent schema change (this
  task's own `database_changes` clause): the result is computed fresh
  per call, never persisted onto `LiteLLMConnection`.
- **Test:** `tests/tasks/test_ftf_0204.py` (no DB, no external egress —
  real local HTTP/HTTPS servers, a real ephemeral CA this suite
  generates and installs into the sandbox's own system trust store for
  the run) — 12 test functions covering the
  discovered/invalid_token/certificate_error/timeout/connection_refused/
  malformed_response/secret-backend/destination_denied states, the
  content-hash determinism/order-independence, and this task's own two
  negative_acceptance cases through real, live round trips:
  1. `test_metadata_only_model_is_never_callable` — a real `/model/info`
     response advertising a name (`unlisted-model`) the real
     `/v1/models` response never returns; the result never contains it
     (Section 3.2's "no authorization union").
  2. `test_removed_model_is_not_retained_across_calls` — two real, live
     `discover_models` calls against the same mutable gateway; the
     second call's real response no longer contains a model the first
     call saw (`gpt-old`), and the result genuinely does not retain it
     (Section 3.3's "removed names immediately become unselectable").
  3. `test_auth_failure_fails_closed_not_retained` — a real successful
     discovery is followed by a real, live credential revocation (the
     gateway starts rejecting the same token); the next call reports
     `invalid_token` with an empty model list, `model_count=None`, and
     `content_hash=None` — never the previously observed inventory.
  `tests/integration/test_ftf_0204_v1_models_discovery.py` (real
  Postgres + real gateway, mirroring
  `test_ftf_0203_connection_validation.py`) drives the actual FastAPI
  route end-to-end: two real organizations, a real uvicorn server, real
  create/discover/revoke through the live route (never a direct call to
  `discover_models` bypassing the HTTP layer), a real metadata-only name
  never leaking through the live route, the cross-organization IDOR case
  denied identically to missing (404), and a revoked connection refused
  discovery (409).
- **Evidence — shown able to fail (Law 1):** a fault injection mutates
  the real, on-disk `v1_models_discovery.py` so the returned model list
  is built from `model_ids` unioned with `enrichment_ids` instead of
  `model_ids` alone — reintroducing exactly the Section 3.2
  "authorization union" bug this task's own negative_acceptance
  forbids. Run in a fresh subprocess against a real gateway configured
  with a metadata-only unauthorized name, the mutated module genuinely
  lets that name leak into the result (`"unlisted-model" in
  result.stdout`) — proving the left-join construction is load-bearing,
  not decorative — then the file is restored byte-identical (confirmed
  by a direct bytes comparison).
- `ruff check .`/`ruff format --check .` clean (183 files); `mypy .`
  clean (183 files, "Success: no issues found"). `scripts/
  validate_spec.py --strict` clean after `git fetch --unshallow origin`
  (this worktree's own pre-existing shallow-clone artifact, not caused
  by this task — same documented precedent as prior shifts).
  `CLAUDE.md.sha256` verified against `sha256sum CLAUDE.md`.
- `scripts/product_truth_check.py --fail-on-unwired` stays `15/15`
  (unchanged — Law 2, certification is a separate Control-plane act;
  `proving_symbol` deliberately left blank in `docs/spec-matrix.yaml`
  rather than the producer claiming its own product-true count).
  `scripts/status.py` stays `46/300 certified · 15 product-true`.
- Real live-Postgres integration evidence produced locally
  (`sovra_migrate` role + `sovra_ftf0204` database, same
  directly-installed PostgreSQL 16 precedent FTF-0201/FTF-0203 already
  established). `FTF-0201`'s and `FTF-0203`'s own integration suites
  re-confirmed passing afterward against the same fresh database — no
  regression to the shared `connection_routes.py` file.

**State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
self-certified — awaiting Control plane L2 review per Law 4.

## 2026-09-22 — Control plane: FTF-0204 (PR#203) independently reviewed, CERTIFIED
Reviewing session (scheduled certifier, `pull_request.opened` webhook for
PR #203): `session_014jayxcTTyU7RpJJXSP3B8p`. PR #203 was the sole
PROVISIONAL item in this file at shift start.

Session-ID compared against PR #203's two commits (`c1ac87c`, `15f96de`,
both carry `session_013yJsjReu7yKdQyMxjAxZgD`) — no overlap, independent
per Law 4.

- The webhook fired the instant PR #203 opened, with CI still
  `in_progress`. Rather than certify against a partial signal, this
  review polled `repos/SJZLab/Sovra/commits/15f96de.../check-runs`
  directly via the GitHub REST API until every job reported `completed`
  — **26/26 real jobs `success`**, including `model-discovery-integration`
  (real Postgres + real gateway) and `gate`, on the real, unmodified PR
  head (`15f96de`).
- Diff read directly: `packages/litellm_client/v1_models_discovery.py`
  (`discover_models` — resolves `secret_ref` via FTF-0202's already-
  CERTIFIED `resolve_secret_ref`, dials `GET /v1/models` via FTF-H006's
  already-CERTIFIED `guarded_fetch`, enriches each authorized model with
  `GET /model/info` on a strict left-join basis — Section 3.2's "no
  authorization union" — and is stateless/fresh per call, so a removed
  model or a credential that starts failing 401/403 is simply absent
  from the current `ModelDiscoveryResult`, never retained from a prior
  call) and `apps/api/app/litellm/connection_routes.py` (the new `GET
  .../connections/litellm/{connection_id}/models` route,
  `organization_admin`-only, same tenant-scoped session and IDOR-denial
  pattern as `/validate`, denies a foreign-tenant connection ID
  identically to missing (404), refuses a revoked connection (409)).
  Diff scope matches the task packet's own `files` list (plus the
  route/CI wiring the acceptance clause requires) — no scope creep.
- Own reproduction, a separate git worktree at the real merged head
  (`15f96de`, not the producer's own claimed run): `uv sync
  --all-packages`; `uv run python3 tests/tasks/test_ftf_0204.py` — "all
  checks passed". A real local PostgreSQL 16 role/db
  (`sovra_migrate`/`sovra_ftf0204`, `sovra_migrate` granted `SUPERUSER`
  to match the official `postgres` Docker image's `POSTGRES_USER`-is-
  superuser default GitHub Actions' `services: postgres:` container
  relies on); `uv run python3
  tests/integration/test_ftf_0204_v1_models_discovery.py` — "all checks
  passed". `tests/integration/test_ftf_0201_litellm_connection_crud.py`/
  `tests/integration/test_ftf_0203_connection_validation.py` re-run
  clean against the same fresh database — no regression to the shared
  `connection_routes.py` file this task also touches.
- Own fault injection, a vector distinct from the producer's own
  (reintroducing the Section 3.2 authorization-union bug and proving it
  in a subprocess): disabled the `response.status in (401, 403)`
  invalid-token check in `discover_models` (`if False and
  response.status in (401, 403):`) and re-ran the no-DB suite — exactly
  1 check failed for real: "a real gateway 401 on a resolved credential
  reports invalid_token, not discovered" — precisely the
  negative_acceptance risk this task exists to prevent (a revoked
  credential silently retaining a stale model list). Reverted from a
  pre-edit backup, `diff` confirmed byte-identical, suite re-ran clean.
- `ruff check .`/`ruff format --check .` clean (183 files); `mypy .`
  clean (183 files, "Success: no issues found"). `CLAUDE.md.sha256`
  verified against `sha256sum CLAUDE.md` on the real merged head.
- `scripts/product_truth_check.py --fail-on-unwired` — own run, `15/15`
  before this certification; `proving_symbol: discover_models`
  confirmed a real, non-test, live-caller match in
  `apps/api/app/litellm/connection_routes.py` (grepped directly, not
  assumed) — this repository's first live-wired product-runtime path to
  actually call this symbol.
- `python3 tools/r1_handoff.py packet FTF-0204 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — both acceptance clauses ("List
  current credential-visible model IDs through tested gateway route and
  record timestamp/count/hash"; "Independent review, exact merged
  commit, real relevant integration and negative-test evidence
  recorded") and the full negative_acceptance clause ("Metadata-only or
  stale deleted model is not callable; auth errors fail closed instead
  of retaining permission") confirmed genuinely met by the diff and this
  session's own fault injection above, not merely "tests pass" —
  `contract_owner: FTF-0201` confirmed real.
- Dependencies FTF-0201/FTF-0202/FTF-0203/FTF-H006 confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.

No blocking defect found. `docs/spec-matrix.yaml` set to
`certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
`proving_symbol: discover_models`, `evidence_hash` set to the merge
commit (`9a3dd7e52040cca60ec7ae72b38676d253a32824`). Ledger entry
appended (`docs/certifications/ledger.md`, "FTF-0204 — `/v1/models`
discovery"). Ran `scripts.task_claim.sync_certified_status()` —
`spec/tasks.json`/`docs/implementation/TRACKER.json` updated for
`['FTF-0204']`. `QUEUE.md`/`PROGRESS.md` updated: FTF-0204 moved to
Done, Certified count bumped to 47/300, Product-true 15/15 → 16/16.

## 2026-09-23 — FTF-0205 — `/v1/model/info` enrichment
- **PR:** relay branch `claude/wizardly-fermat-h60rso` → main; opened as
  `[relay] FTF-0205: /v1/model/info enrichment`
- **Commit:** 6545edb90975b768797e4a9e3d760c3185bfeb21
- **Real DoD source:** task packet resolved via `python3
  tools/r1_handoff.py packet FTF-0205 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` (`docs/implementation/task-packets/
  FTF-0205.md`). Dependencies FTF-0201/FTF-0203/FTF-0204 confirmed
  genuinely `CERTIFIED` in `docs/spec-matrix.yaml` before relying on
  them.
- **Delivered:** `packages/litellm_client/v1_model_info_enrichment.py`
  (`enrich_visible_models`) enriches exactly the caller's own
  already-authorized `visible_model_ids` (from a real `GET /v1/models`
  call, e.g. FTF-0204's own `discover_models`) with allowlisted `GET
  /model/info` fields, on a strict left join (Section 3.2's "no
  authorization union" applied to enrichment) — an id absent from
  `visible_model_ids` is never iterated into the result, satisfying this
  task's own negative_acceptance ("metadata-only aliases are neither
  stored nor exposed"). `_ALLOWLISTED_MODEL_INFO_FIELDS` is an allow
  list, not a denylist (Section 65.4) — the real gateway response's own
  sibling `litellm_params` (which carries `api_key`/`api_base`) is never
  read by `_parse_model_info` at all, and only named fields with plain
  JSON scalar values (never a nested dict/list) ever pass the filter,
  covering the negative_acceptance's "raw credentials... are neither
  stored nor exposed" even against a hypothetical misconfigured gateway
  that duplicates a credential-shaped field directly inside
  `model_info`. A denied/failed/malformed `/model/info` call (403/404,
  guard failure, secret failure, malformed body) never drops or replaces
  an already-visible id — every entry in `visible_model_ids` still gets
  a real result, downgraded to `metadata_status=limited` with empty
  fields, matching "retaining limited metadata when denied/absent"
  (Section 3.1's "403/404 on enrichment does not invalidate a
  successful standard model list", applied here).
- **Scope decision (disclosed, same precedent FTF-0202 already
  established for "no live HTTP route yet"):** this task's own packet
  `files` list names only the module and its test, not a route file.
  `docs/spec-matrix.yaml`'s own dependents show `FTF-0207` ("Alias
  reconciliation") is the later task depending on this one (plus
  FTF-0201/FTF-0204) and is the natural real caller — the same
  producer/later-wiring relationship FTF-0202/FTF-0203 already
  established. This module is therefore deliberately not wired into
  `apps/api/app/litellm/connection_routes.py` by this task;
  `proving_symbol` is left blank and `product_truth_check.py`/
  `scripts/status.py`'s product-true count is deliberately unchanged
  (Law 2).
- **Test:** `tests/tasks/test_ftf_0205.py` (no DB, no external egress —
  real local HTTP secret-backend server, real local HTTPS gateway
  servers with a real ephemeral CA installed into this sandbox's own
  system trust store, mirroring `tests/tasks/test_ftf_0204.py`'s own
  established fixture pattern exactly) — covers allowlisted fields
  surviving with real values; two credential-leak vectors proven absent
  in one test (`litellm_params.api_key` and a hypothetical
  credential-shaped field duplicated directly inside `model_info`); a
  metadata-only alias outside `visible_model_ids` proven absent from
  `entries`; an authorized-but-absent alias retaining
  `limited`/empty-fields; an admin-only-route 403 and a malformed
  response both degrading every visible id to `limited` without
  dropping any of them; duplicate visible ids collapsing to one entry;
  every real secret-backend failure state distinct and real; a real
  connection-policy destination-denial before any socket opens; an
  empty `visible_model_ids` never resolving a secret or dialing a
  gateway; the allowlist's own unit behavior (unknown keys dropped,
  non-scalar values under an allowlisted key dropped, non-dict raw
  input never raises).
- **Evidence — shown able to fail (Law 1), two distinct vectors, both
  reverted byte-identical and the suite re-confirmed clean immediately
  after each:**
  1. A fault injection mutates the real, on-disk
     `v1_model_info_enrichment.py` so `_allowlisted_fields` returns
     every raw field verbatim instead of filtering through
     `_ALLOWLISTED_MODEL_INFO_FIELDS`. Run in a fresh subprocess against
     a real gateway advertising a credential-shaped `model_info.api_key`
     field, the mutated module genuinely lets the secret string leak
     into the result (`"sk-fault-injection-leak" in result.stdout`) —
     proving the allow list is load-bearing, not decorative.
  2. A second, independent mutation (iterating
     `set(ids) | set(by_alias.keys())` instead of `ids` alone,
     reintroducing an authorization union) was run directly against the
     live in-process suite before this submission: caught for real (2
     checks failed with the exact "never added to the reported entries"
     assertion), then reverted and the suite re-confirmed clean.
- `ruff check .`/`ruff format --check .` clean on the two new files; the
  one `UP042` finding on `EnrichmentState(str, Enum)` matches this
  repository's own pre-existing convention (11 identical findings
  already present on `main` before this task, including
  `v1_models_discovery.DiscoveryState`/`ModelMetadataStatus`
  themselves) — confirmed via `git stash`, not a new defect. `mypy`
  clean on both new files (the one remaining repo-wide
  `packages/security/outbound_guard.py` finding is pre-existing,
  confirmed identical on `main` via `git stash`, unrelated to this
  change). `uv sync --all-packages` resolved this fresh sandbox's own
  missing `uvicorn` dependency; `tests/tasks/test_ftf_0201.py`,
  `test_ftf_0202.py`, `test_ftf_0203.py`, `test_ftf_0204.py` all re-run
  clean afterward (no regression to any shared module).
  `scripts/validate_spec.py --strict` clean after `git fetch
  --unshallow` (this sandbox's own pre-existing shallow-clone artifact,
  same documented precedent as prior shifts). `CLAUDE.md.sha256`
  verified against `sha256sum CLAUDE.md`.
- `scripts/product_truth_check.py --fail-on-unwired` stays `16/16`
  (unchanged — Law 2, no live caller yet, disclosed above).
  `scripts/status.py` stays `47/300 certified · 16 product-true`.

**State:** proof_state=L1-PROVEN, certification_state=PROVISIONAL. Not
self-certified — awaiting Control plane L2 review per Law 4.

## 2026-09-23 — Control plane: FTF-0205 (PR#205) independently reviewed, CERTIFIED
Reviewing session (scheduled certifier, `pull_request.opened` webhook for
PR #205): `session_01MUWNviq4gzsqxZ5NB7Yigz`. PR #205 was the sole
PROVISIONAL item in this file at shift start.

Session-ID compared against PR #205's two commits (`6545edb`, `d2c8e37`,
both carry `session_01RmL7kyQS87ukMEHHgUQ7fN`) — no overlap, independent
per Law 4.

- The webhook fired the instant PR #205 opened, with CI still
  `in_progress`. Polled `repos/SJZLab/Sovra/commits/d2c8e37.../check-runs`
  directly via the GitHub REST API until every job reported `completed`
  — **26/26 real jobs `success`**, including `gate`,
  `secret-manager-integration` and `dataset-source-connection-integration`
  on the real, unmodified PR head (`d2c8e37`). The latter two were still
  building a real from-source OpenBao binary when first observed;
  confirmed genuinely progressing (real non-zero `runner_id`s, real
  per-step timestamps advancing between polls) rather than a repeat of
  the earlier documented runner-provisioning outage.
- Diff read directly: `packages/litellm_client/v1_model_info_enrichment.py`
  (`enrich_visible_models` — attaches allowlisted `GET /model/info` fields
  onto exactly the caller's own already-authorized `visible_model_ids` on
  a strict left-join basis, Section 3.2's "no authorization union" applied
  to enrichment; `_ALLOWLISTED_MODEL_INFO_FIELDS` is an allow list, not a
  denylist — `litellm_params` is never read at all and only named fields
  with plain scalar values ever pass through; a denied/failed/malformed
  call never drops or replaces an already-visible id, downgrading it to
  `metadata_status=limited` with empty fields instead). Diff scope
  matches the task packet's own `files` list (module + test only) — no
  route wired by this task, the same disclosed producer/later-wiring
  scope decision FTF-0202/FTF-0203 already established (`FTF-0207` is the
  later real caller).
- Own reproduction, a separate git worktree at the real merged head
  (`d2c8e37`, not the producer's own claimed run): `uv sync
  --all-packages`; `uv run python3 tests/tasks/test_ftf_0205.py` — "all
  checks passed". `tests/tasks/test_ftf_0201.py`/`test_ftf_0202.py`/
  `test_ftf_0203.py`/`test_ftf_0204.py` all re-run clean afterward — no
  regression to any shared module.
- Own fault injection, a vector distinct from the producer's own two
  (widening the allowlist and reintroducing an authorization union):
  made a denied/invalid-token `/model/info` response return an empty
  `entries` tuple outright instead of downgrading every visible id to
  `limited` — re-ran the no-DB suite: exactly 1 check failed for real,
  "every visible id is still present, downgraded to limited, never
  dropped" — precisely the negative_acceptance risk this task exists to
  prevent. Reverted from a pre-edit backup, `diff` confirmed
  byte-identical, suite re-ran clean.
- `ruff check .`/`ruff format --check .` clean (repo-wide in this
  reproduction, 0 findings); `mypy .` clean (185 files, "Success: no
  issues found"). `CLAUDE.md.sha256` verified against `sha256sum
  CLAUDE.md` on the real merged head.
- `scripts/product_truth_check.py --fail-on-unwired` — own run, `16/16`;
  `proving_symbol` genuinely blank for this criterion (no live caller
  yet, disclosed) — 0/0 confirmed a legitimate pass, not a gap, for a
  criterion that claims no proving_symbol.
- `python3 tools/r1_handoff.py packet FTF-0205 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — both acceptance clauses ("Enrich
  only visible aliases with allowlisted fields, retaining limited
  metadata when denied/absent"; "Independent review, exact merged
  commit, real relevant integration and negative-test evidence
  recorded") and the full negative_acceptance clause ("Raw credentials,
  admin-only provider routes and metadata-only aliases are neither
  stored nor exposed") confirmed genuinely met by the diff and this
  session's own fault injection above, not merely "tests pass" —
  `contract_owner: FTF-0201` confirmed real.
- Dependencies FTF-0201/FTF-0203/FTF-0204 confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.

No blocking defect found. `docs/spec-matrix.yaml` set to
`certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
`evidence_hash` set to the merge commit
(`b421fb366870167dcb7ecaab7a658adf1c2c6510`); `proving_symbol` stays
blank (no live caller yet, disclosed above). Ledger entry appended
(`docs/certifications/ledger.md`, "FTF-0205 — `/v1/model/info`
enrichment"). Ran `scripts.task_claim.sync_certified_status()` —
`spec/tasks.json`/`docs/implementation/TRACKER.json` updated for
`['FTF-0205']`. `QUEUE.md`/`PROGRESS.md` updated: FTF-0205 moved to
Done, Certified count bumped to 48/300, Product-true stays 16/16
(unchanged — this task discloses no live wiring).
