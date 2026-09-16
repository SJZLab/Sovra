# PROGRESS — Sovra
Updated: 2026-09-16T12:00:00Z

## 2026-09-16 — FTF-0004 implemented (Provisional, awaiting L2 certification)
Live user session (direct instruction: "GO", following an "assess then"
step asking whether FTF-0004 is tractable in this sandbox). Read
PROGRESS.md/QUEUE.md/docs/certifications/queue.md, cross-checked against
`git fetch origin main` (unchanged at `4ea6784` — FTF-0005 CERTIFIED,
4/300, nothing else pending). QUEUE.md's own dependency order named
FTF-0004 as the next queued item; deps (FTF-0001/0003/0005) all
CERTIFIED. Real DoD resolved via `tools/r1_handoff.py packet FTF-0004`.

**Feasibility check first (STEP 1, before claiming):** confirmed this
session's interactive dev sandbox has a working Docker daemon
(`sudo dockerd` starts cleanly) but no container-registry egress —
`docker pull hello-world` got a 403 from the session's own enforced
proxy on `production.cloudfront.docker.com` (`/root/.ccr/README.md`:
a 403 means the destination isn't allowed by org egress policy, "do not
retry or route around it"). Initially assessed this as `BLOCKED_EXTERNAL`
per the task's own resource_gate clause. The live user pushed back: this
session's own operating instructions already name the documented route
for exactly this constraint — route external proof through CI Actions
workflows, which have real registry egress. Correct call — proceeded on
that basis rather than blocking.

STEP 2 (PEV):
- P: read the task packet in full. `files` lists only
  `apps/api/app/config/docker_local_stack.py` and its test — not a
  main.py or web app — confirming FTF-0004's real scope is
  bootstrap+health (`api_contract`: "Service bootstrap/configuration/
  health, no public job API changes"), not the full product API/UI.
  Checked `apps/api`/`apps/web` on disk: neither has a real server-side
  runtime yet (no FastAPI, no Next.js/React installed) — building either
  now would be a new-runtime-dependency ask-first item under CLAUDE.md
  (same reasoning FTF-0005 used to avoid pydantic). Decided: use only
  the stdlib for the `api` health-bootstrap process, and deliberately
  exclude `web` from this task's compose stack rather than fake a
  placeholder for a service that doesn't exist — documented as a
  follow-up for whichever task first builds the real web app.
- E: verified exact image tags exist via Docker Hub's *metadata* API
  (`hub.docker.com/v2/...`, reachable even though blob/CDN pulls are
  blocked) and pinned all three service images by digest: `postgres:
  16-alpine`, `temporalio/auto-setup:1.24.2` (backed by the same
  Postgres), `chrislusf/seaweedfs:3.71` as the S3-compatible service —
  the reuse-architecture's qualified fixture (Section 77.3), directly
  satisfying this task's own acceptance clause against "an automatic new
  archived-MinIO install". Wrote `infra/docker/docker-compose.yml`,
  `apps/api/app/config/docker_local_stack.py` (TCP-probe readiness +
  stdlib `http.server` `/health` endpoint), `scripts/
  verify_docker_local_stack.py` (real boot, poll-to-ready, stop postgres,
  assert unhealthy, restart, assert recovery + a marker row's
  persistence — the negative_acceptance clause), and
  `tests/tasks/test_ftf_0004.py` (9 checks using real local TCP
  listeners this process controls — runnable with no Docker at all).
  Law 1 evidence: temporarily replaced `check_tcp_reachable` with a
  canned "always healthy" stub, re-ran the test — 4 of 9 checks failed
  for real (unreachable-port case, live open/close flip, mixed-stack
  not-ready case, detail-message assertion); restored byte-identical
  from a pre-edit copy (`diff` confirmed empty), re-ran clean (9/9).
  Wired `tests/tasks/test_ftf_0004.py` into the existing `gate` CI job
  and added a new `docker-local-stack-integration` job running
  `scripts/verify_docker_local_stack.py` on GitHub Actions' hosted
  runner — where the real container-boot and negative-acceptance
  evidence will actually be produced.
- V: `product_truth_check.py --fail-on-unwired` stays `0/0
  product-true` — legitimate, no proving_symbol yet (no live caller,
  same rationale as FTF-0002/0003/0005). `docker compose -f
  infra/docker/docker-compose.yml config` parses cleanly (validated
  locally without pulling any image — full boot validated in CI).
Re-ran the full existing gate after all changes: `uv run ruff
check`/`format --check`/`mypy` clean; `uv run pytest tests/unit`
(3 passed); `tests/tasks/test_ftf_0002.py`, `test_ftf_0003.py`,
`test_ftf_0005.py`, and `tests/test_ftf_0001_monorepo_layout.py` all
still pass unmodified — no regression. `pnpm run lint`/`typecheck`/
`format:check` and `pnpm exec vitest run` (2 passed) also clean (no
TypeScript files touched).
Committed as `bc603a6` on branch `claude/wizardly-fermat-3mh1pd` (also
carries the generated `docs/implementation/task-packets/FTF-0006.md`
from the feasibility check done before pivoting back to FTF-0004 — no
FTF-0006 work claimed). Updated `docs/spec-matrix.yaml` (FTF-0004:
IMPLEMENTED/L1-PROVEN/PROVISIONAL, evidence_hash=bc603a6),
`docs/certifications/queue.md` (full entry), `QUEUE.md` (Active cleared,
Provisional entry added, bumped Provisional to 1). Certified count
unchanged at 4/300 — PROVISIONAL is not certified. Per Law 4, did not
self-certify. PR to be opened against `main`; not merging without
confirming CI green first — Control plane review should specifically
confirm the `docker-local-stack-integration` job passed for real (not
merely that it exists) before certifying.
STEP 4(a): stopping here on this item — FTF-0004 is fully L1-PROVEN,
awaiting Control plane L2 certification and the real CI integration run.
Next candidate once FTF-0004 certifies: FTF-0006 (observability, task
packet already pulled) or FTF-0101+ (identity/tenancy/RBAC/audit).

## 2026-09-16 — FTF-0005 CERTIFIED (fix independently re-reviewed)
Independent Control-plane session `session_01WqPEZmJdJyFH7tk2cbakWP`
reviewed PR #30 (the scoped repr-redaction fix from the entry below).
Session-ID check: both PR #30 commits carry
`Claude-Session: .../session_012mAtoqKYzA7PiXbkGkD7rY` — the producer
session, distinct from this reviewing session and from the prior
reviewer (`session_01WjQKEb87DwDx51YLNRxT6o`) — independent, not
self-certification.
Re-read the diff (one line: `_redact_url()` now applied to
`litellm_gateway_url` in `__repr__`, plus one new test case). Checked out
the actual PR head commit (`65824c5`) into a fresh git worktree, ran
`uv sync` fresh, then `tests/tasks/test_ftf_0005.py` — 10/10 pass. Own
fault injection: reverted the fix in the worktree, re-ran the test —
exactly 1 check failed for real with the expected message, reproducing
the original certifier's finding from first principles; restored from a
pre-edit copy (`diff` empty), re-ran clean. Confirmed the real GitHub
Actions `gate` check on PR #30's actual head SHA: run 35087235376,
conclusion `success`. `product_truth_check.py --fail-on-unwired`:
legitimately `0/0` (`proving_symbol: ''`, no live caller yet — unchanged
from the original FTF-0005 evidence). Pulled the real contract via
`tools/r1_handoff.py packet FTF-0005` — negative_acceptance ("sensitive
values absent from errors") is now genuinely met for the field the prior
review found unmet. No regression to FTF-0001/0002/0003's certified
evidence or to the other 8 originally-proven FTF-0005 checks (untouched
by this PR).
PR #30 (already merged by the repo owner, merge commit
`5f5a34d803a76e3261c8cc860bd5f21cd8290424`) reviewed and certified.
`docs/spec-matrix.yaml`: FTF-0005 now `certification_state: CERTIFIED`,
`proof_state: L2-CERTIFIED`, `evidence_hash: 5f5a34d...`. Ledger entry
appended (docs/certifications/ledger.md). QUEUE.md: Active/Provisional
cleared, FTF-0005 moved to Done, Certified count 3→4. Not yet PROMOTED —
L4 gate is release-only and hasn't run.

## 2026-09-16 — FTF-0005 repr secret-redaction defect fixed (still Provisional)
An independent Control-plane session (`session_01WjQKEb87DwDx51YLNRxT6o`,
PR #29, session-ID confirmed distinct from this one) reviewed PR #28 and
did NOT certify: real defect found by fault injection, not a flake —
`Settings.__repr__` redacted `database_url`'s embedded userinfo via
`_redact_url()` but not `litellm_gateway_url`'s, so an accepted gateway
URL with an embedded password (any environment, including production) had
that password echoed verbatim by `repr(settings)` — the delivered test
suite never exercised an *accepted* gateway URL with credentials against
`repr()`, only a *rejected* one. Full finding: PR #29 /
docs/certifications/queue.md.
This is the producer session (`session_012mAtoqKYzA7PiXbkGkD7rY`) picking
up the queued fix — legitimate relay work (fixing a reported defect), not
self-certification; the eventual re-certification still requires a
separate session per Law 4.
Fix: applied `_redact_url()` to `litellm_gateway_url` in `__repr__`,
matching `database_url` — one line. Added the missing test case to
`test_sensitive_values_absent_from_errors_and_repr` (accepted gateway URL
with embedded credentials → asserted absent from `repr(settings)`). Law 1
evidence: reverted the fix locally, re-ran the test — the new check
failed for real, reproducing the certifier's exact finding; restored from
a pre-edit copy (`diff` empty), re-ran clean (10/10 pass). Re-ran
ruff/mypy/pytest — all clean, no regression.
Committed as `9f2dcbc` on branch `relay/ftf-0005-fix-repr-redaction`
(fresh branch off `main`, since the original `relay/ftf-0005-...` branch
was already merged via PR #28 — same convention as this repo's other
merged-PR follow-ups). Updated `docs/spec-matrix.yaml` (evidence_hash=
9f2dcbc), `docs/certifications/queue.md` (re-submission entry),
`QUEUE.md` (Active cleared, Provisional entry updated). Certification
state stays PROVISIONAL — not self-certified. PR to be opened against
`main`.

## 2026-09-16 — FTF-0005 implemented (Provisional, awaiting L2 certification)
Autonomous relay shift (scheduled firing, no live user present). STEP 0:
`sha256sum CLAUDE.md` matched committed `CLAUDE.md.sha256` — no drift.
`cognee-cli` still not on PATH — text-search fallback active (unchanged).
STEP 0.1: read PROGRESS.md/QUEUE.md/docs/certifications/queue.md, then
cross-checked against live repo state — `git fetch origin main`: local
branch tip `135b3ac` matched `origin/main` exactly, `BLOCKER.md` held only
resolved history, no open PRs. QUEUE.md Active was empty with FTF-0005
(config) named as the next candidate ahead of FTF-0004 (docker local
stack, which itself depends on FTF-0005).
STEP 1: picked FTF-0005 — Configuration system (deps: FTF-0001, already
CERTIFIED). Real DoD resolved via `python tools/r1_handoff.py packet
FTF-0005 --plan FINE_TUNING_FACTORY_RELEASE_1.md` (not improvised).
STEP 2 (PEV): implemented a stdlib-only, fail-fast typed settings loader
(`apps/api/app/config/configuration_system.py`, `Settings.from_env()`) —
deliberately avoided adding a third-party runtime dependency (e.g.
pydantic) since CLAUDE.md lists "new runtime deps" as an ask-first hard
stop; validation uses only `dataclasses`/`enum`/`decimal`/`urllib.parse`/
`re`. Covers the acceptance criterion (typed settings reject
missing/invalid URLs, policy enum, budget and secret refs before a
`Settings` object — i.e. before serving — can be constructed) and the
negative_acceptance criterion (no development fallback credential, no
production `tls_verify=false`, no sensitive value in an error or in
`Settings`' own `__repr__`).
Law 1 evidence: `tests/tasks/test_ftf_0005.py` (9 checks) asserts each bad
case raises and each clean case constructs; additionally performed a real
fault injection against the tracked module itself (not just a fixture) —
temporarily removed the production/`tls_verify=false` rejection from
`_validate_tls_verify`, re-ran the test, watched 2 checks fail for real
(`SOVRA_TLS_VERIFY=false in production raises`, `the production/tls_verify
rejection names the actual rule`), then restored the file from a pre-edit
copy, confirmed `diff` empty, re-ran clean (9/9 pass).
Caught and fixed two real defects before they reached CI: `ruff check`
flagged UP037 (quoted type annotations under `from __future__ import
annotations`); `mypy .` failed with 12 real errors (enum-generic typing
via a plain `type` annotation, `dict` vs `Mapping[str, str]` argument
mismatch) — fixed with a `TypeVar` bound to `Enum` and `Mapping[str, str]`
parameter types, re-verified `mypy .` clean afterward.
Environment note (not a regression): this fresh container had no
`node_modules` — running `tests/tasks/test_ftf_0002.py`/`test_ftf_0003.py`
before `pnpm install` showed TS/lint/vitest failures unrelated to this
change; ran `pnpm install --frozen-lockfile`, both scripts then passed
clean, confirming no regression to FTF-0002/0003's certified evidence.
Re-ran the full existing gate after all changes: `ruff check .` / `ruff
format --check .` / `mypy .` clean, `pytest tests/unit` (3 passed),
`tests/test_ftf_0001_monorepo_layout.py` clean, `product_truth_check.py
--fail-on-unwired` still `0/0` (FTF-0005's `proving_symbol` deliberately
left empty — no live caller yet, `apps/api` has no `main.py`; same
rationale as FTF-0002/0003), `scripts/status.py` reports `3/300 certified
· 0 product-true` (unchanged — FTF-0005 is PROVISIONAL, not counted until
certified).
Wired `python3 tests/tasks/test_ftf_0005.py` into
`.github/workflows/ci.yml` (YAML-validated with `yaml.safe_load`).
Committed implementation as `b782a2c` on branch
`relay/ftf-0005-configuration-system`. Updated `docs/spec-matrix.yaml`
(FTF-0005: IMPLEMENTED/L1-PROVEN/PROVISIONAL, evidence_hash=b782a2c),
`docs/certifications/queue.md` (full entry), `QUEUE.md` (moved to
Provisional, cleared Active, bumped Provisional to 1). Certified count
unchanged at 3/300 — PROVISIONAL is not certified. Per Law 4, did not
self-certify. PR to be opened against `main`; not merging without
confirming CI green first.
STEP 4(a): stopping here — FTF-0005 is fully L1-PROVEN, awaiting Control
plane L2 certification (this session is the relay, not the certifier) and
eventually the L4 gate. Next relay shift's top candidate stays FTF-0004
once FTF-0005 certifies, or FTF-0006/FTF-0101+ if FTF-0004 needs a real
Docker/GPU resource this sandbox doesn't have — to be assessed then, not
assumed now.

## 2026-09-16 — FTF-0003 independently certified (Control plane, CERTIFY mode)
Scheduled control-plane certifier shift. Read docs/certifications/queue.md — one pending item,
FTF-0003 (PR #26, merged as e2c2dae96142c95a78005fd7528df6ac7e3e5dfc). Session-ID comparison
first, per docs/CONTROL_PLANE_MODES.md's explicit requirement: both PR #26 commits (d4482de,
678285f) carry `Claude-Session: .../session_01DY5FK4nK2VLrUMKHwNQQU2`; this session is
`session_018gZRsspGdmcTmtxkqRDE59` — no overlap, independent per Law 4.
Verified for real in a fresh `git worktree` checked out at the actual merge commit, not taken on
the PR description's word:
- Confirmed CLAUDE.md.sha256 matches `sha256sum CLAUDE.md` — no drift.
- Fresh `uv sync` and `pnpm install --frozen-lockfile` succeeded from clean state.
- Ran the real CI commands myself: `uv run pytest tests/unit` (3 passed), `pnpm exec vitest run`
  (2 passed), `scripts/check_test_harness.py` against both JUnit reports (exit 0, clean), and
  `tests/tasks/test_ftf_0003.py` (all checks passed).
- Fetched GitHub Actions run 35083644711 directly via the API: `pull_request` event, head_sha
  `678285f` (PR #26's actual merged head), conclusion `success` — genuine run.
- Performed three of my own fault injections (not the producer's claimed evidence): an empty
  pytest dir, a `pytest.mark.skip` fixture, and a Vitest `test.only` fixture — each independently
  made `check_test_harness.py` exit 1 with the expected message; reverted, tree confirmed clean.
- `product_truth_check.py --fail-on-unwired`: `0/0 product-true`, exit 0 — legitimate since
  FTF-0003's `proving_symbol` is empty (test-harness tooling, no live product-runtime path).
- Re-ran the full pre-existing gate (ruff/mypy/eslint/prettier/tsc) — all still clean, confirming
  no regression; confirmed `tests/test_ftf_0001_monorepo_layout.py` and `tests/tasks/test_ftf_0002.py`
  are untouched by this PR's diff.
- Pulled the real contract via `tools/r1_handoff.py packet FTF-0003` and checked delivered work
  against its actual acceptance/negative_acceptance clauses — both met; `verification_commands`
  still don't exist, expected per the FTF-0002 precedent (gated behind FTF-H001 bootstrap).
`docs/spec-matrix.yaml`: FTF-0003 → `certification_state=CERTIFIED`, `proof_state=L2-CERTIFIED`,
`evidence_hash=e2c2dae` (merge commit). `docs/certifications/ledger.md`: full signed entry with
session-ID comparison stated explicitly. `QUEUE.md`: FTF-0003 moved to Done, Certified bumped to
3/300, Provisional cleared. Not promoted — L4 gate is release-only and hasn't run.

## 2026-09-16 — FTF-0003 implemented (Provisional, awaiting L2 certification)
Done at the user's direct live instruction ("Go then next task"), not the autonomous relay's
normal cadence. Real DoD resolved via `tools/r1_handoff.py packet FTF-0003`. Commit
d4482de2cbcdda17000f059886c47b894b680830. Summary:
- Real pytest unit-test harness (Python, `tests/unit`) and real Vitest unit-test harness
  (TypeScript, `apps/web/src` + `packages/shared/src`), each collecting genuine assertions and
  publishing machine-readable JUnit XML counts.
- `scripts/check_test_harness.py`: parses the JUnit output and fails on zero collected tests or
  any skipped test — verified empirically that both pytest and Vitest report a `.only`-deselected
  test as "skipped," so this one check covers explicit skips and focused `.only` alike for both
  languages, satisfying FTF-0003's negative_acceptance clause without a separate grep-based scan.
- Law 1 evidence (tests/tasks/test_ftf_0003.py): real fault injection in scratch fixtures —
  empty pytest dir (pytest exits 5), empty Vitest root (exits 1), a `pytest.mark.skip` fixture,
  and a Vitest `.only` fixture — each shown to make the gate script fail for real, with clean
  equivalents shown to pass; plus a repo-wide clean run of the actual delivered harness.
- Caught two real bugs before they reached CI, not assumed away: `pnpm run test -- --reporter=
  junit ...` silently mis-forwards pnpm's `--` separator and never writes the JUnit file (fixed
  by calling `pnpm exec vitest run` directly in CI); `mypy .` genuinely failed with a dual-module-
  name error once a new test imported `scripts.product_truth_check` (fixed via
  `scripts/__init__.py`, re-verified clean).
- Deliberately left `tests/test_ftf_0001_monorepo_layout.py` and `tests/tasks/test_ftf_0002.py`
  (both already independently L2-certified) out of pytest's `testpaths` rather than editing them
  to fit pytest's assertion model — avoids any Law 15 requalification question for their existing
  certified evidence. Re-ran both directly afterward: unchanged, still pass.
- Re-ran the full existing gate suite (ruff/mypy/eslint/prettier/tsc + FTF-0001/0002 proofs)
  after all changes: still clean.
Updated docs/spec-matrix.yaml (FTF-0003: IMPLEMENTED/L1-PROVEN/PROVISIONAL, evidence_hash=
d4482de2...), docs/certifications/queue.md (full entry), QUEUE.md (moved to Provisional, cleared
Active). Certified count unchanged at 2/300 — PROVISIONAL is not certified. PR to be opened
against main; not merging without confirming CI green first.

## 2026-09-16 — FTF-0002 independently certified (Control plane, CERTIFY mode)
A separate session (this one) picked up docs/CONTROL_PLANE_MODES.md's CERTIFY procedure at the
user's direct live instruction ("Go with the certification process"). Session-ID comparison
first, per that doc's explicit requirement: both PR #23 commits (e3f0c1a, 6928d14) carry
`Claude-Session: .../session_01NuAjwmK5rwuxefyo5L7zgy`; this session is
`session_01DY5FK4nK2VLrUMKHwNQQU2` — no overlap, independent per Law 4.
Verified for real rather than trusting the PR description:
- Read the full diff and every changed config file (pyproject.toml, eslint.config.mjs,
  tsconfig*.json, ci.yml) — no continue-on-error, no softened severity; the one exclusion
  (tools/r1_handoff.py, from ruff+mypy) is documented and non-production.
- Ran `uv sync` and `pnpm install` fresh (not reusing the producer's environment) — both
  succeeded, confirming the pre-existing `hatchling` build-defect fix holds.
- Ran `tests/tasks/test_ftf_0002.py` myself: 6/6 pass.
- Fetched GitHub Actions run 35070915230 directly: `pull_request` event, head_sha matches PR
  #23's actual merged head (confirmed via `pull_request_read`), conclusion `success`.
- Performed my own fault injection: appended `import os` to the real `scripts/status.py`, ran
  unmodified `ruff check .` — real failure (F401+E402, exit 1); reverted, re-ran clean (exit 0).
- `product_truth_check.py --fail-on-unwired`: `0/0 product-true`, legitimate since FTF-0002's
  `proving_symbol` is empty (CI tooling, not a live product path).
- Pulled the real contract via `tools/r1_handoff.py packet FTF-0002` and checked delivered work
  against its actual acceptance/negative_acceptance clauses, not just "tests pass" — both met.
Certified: docs/spec-matrix.yaml (FTF-0002: certification_state=CERTIFIED, proof_state=
L2-CERTIFIED, evidence_hash=41f1493 the merge commit), docs/certifications/ledger.md (full
entry with session-ID comparison stated explicitly), QUEUE.md (moved FTF-0002 to Done, bumped
Certified to 2/300, cleared Provisional, noted FTF-0003 now unblocked for the relay). Not
promoted — L4 gate is release-only and hasn't run. Certified: 2/300 · Product-true: 0
(unchanged — FTF-0002 has no proving_symbol, doesn't count either way).
Did not act on this session's other two pending items (trigger-tooling durability
confirmation, FTF-0001 test-hardening/watchdog-finding question) — out of scope for this
CERTIFY-mode pass; left for a future OPERATE/UNBLOCK-mode check-in or explicit owner direction.

## 2026-09-16 — Relay shift: no workable item, FTF-0002 still awaiting independent L2 cert
STEP 0: `CLAUDE.md.sha256` matches `sha256sum CLAUDE.md` — no drift. `cognee-cli` still not on
PATH — text-search fallback active (unchanged from every prior shift).
STEP 0.1: read PROGRESS.md/QUEUE.md/docs/certifications/queue.md; cross-checked against live
repo state rather than trusting the docs alone (`git fetch origin main` — local branch tip
`41f1493` matches `origin/main` exactly, so nothing has landed since the last shift wrote these
files).
STEP 1: QUEUE.md Active is empty. FTF-0002 (PR #23, commit `41f1493`) is merged into `main` and
CI-green (verified via GitHub Actions run 35070915230 on the PR's head commit, conclusion
`success`) but remains `certification_state: PROVISIONAL` in `docs/spec-matrix.yaml` — no
Control-plane L2 certification has landed (no ledger.md entry for it, no open PRs of any kind
on the repo). QUEUE.md's own Active note says FTF-0003 should not start until FTF-0002 is
independently certified, to avoid stacking a second provisional item on an uncertified one.
This session was invoked as the relay, not Control plane; per Law 4 (producer never certifies
own work) and the relay/certifier role split in CLAUDE.md, it does not self-certify FTF-0002 —
that requires a separate control-plane session.
No source, spec-matrix, or certification-queue changes this shift — nothing was actionable.
Stopping per STEP 4(a): FTF-0002 is fully L1-PROVEN, awaiting Control plane certification and
(eventually) the L4 gate.

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
