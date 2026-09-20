# Certification Ledger (append-only, signed)
Format per entry: criterion ID · PR · commit · validated-by · date

## FTF-0001 — Initialize monorepo
- Criterion ID: FTF-0001
- PR: https://github.com/SJZLab/Sovra/pull/3
- Merge commit: 7ab3f6ff1ac7bcc240e546b764af8d67ae0c8561
- Validated-by: control-plane
- Date: 2026-09-15
- Evidence: GitHub Actions run 34995170054 (job `gate`), conclusion
  success, on the PR's merged head; `product_truth_check.py` 0/0 as
  expected (FTF-0001 claims no proving_symbol — structural layout, not a
  live-wired code path). Full evidence trail in
  docs/certifications/queue.md history for this entry (PR #3) and PR #4
  (unrelated pre-existing workflow-YAML fix required to get real CI
  running on PR #3).

SUPERSEDED — original certification was self-certified (PR #3 and PR #5
share session identity), invalid per Law 4, found by watchdog PR #13,
corrected 2026-09-15.

## FTF-0001 — Initialize monorepo (independent re-certification)
- Criterion ID: FTF-0001
- PR: https://github.com/SJZLab/Sovra/pull/3
- Merge commit: 7ab3f6ff1ac7bcc240e546b764af8d67ae0c8561
- Validated-by: session https://claude.ai/code/session_01Rs3uk7ApCoUpnFb9hHS9Uz
  (session-ID comparison performed and recorded before this entry: this
  session's ID does not match PR #3/PR #5's session
  session_01EN6u2CfDd8VdrHDTNyuH4g, nor watchdog PR #13's session
  session_01Y7WHsvr6Xi1RnUnkifEyRZ — independent of both the original
  producer/self-certifier and the watchdog that found the finding)
- Date: 2026-09-15
- Evidence independently re-verified this session, not taken on the
  original ledger entry's word:
  1. Read PR #3's full file diff (`get_files`) — 20 files, all scaffolding
     (root pnpm/uv workspace config, seven component dirs each with
     README + manifest, tests/test_ftf_0001_monorepo_layout.py); matches
     the committed description, nothing beyond FTF-0001's own scope.
  2. Fetched GitHub Actions run 34995170054 directly via the Actions API
     (not the PR body's claim about it): workflow `CI`, event
     `pull_request`, status `completed`, conclusion `success`, real actor
     `samerzakaria`, head_sha `2f63b7f` — a genuine run, not a 0-job parse
     failure.
  3. Ran `tests/test_ftf_0001_monorepo_layout.py` locally against the
     current tree: passes clean (0 failures). Then fault-injected by
     removing `apps/api/pyproject.toml` and re-ran: test correctly
     reddens (`FAIL [FTF-0001] apps/api/pyproject.toml exists`, exit 1) —
     confirms the test can actually fail, not just always pass. File
     restored, re-ran clean again.
  4. Confirmed `product_truth_check.py`'s `0/0 product-true` is legitimate
     for this criterion: `docs/spec-matrix.yaml`'s FTF-0001 entry has
     `proving_symbol: ''` (structural layout, no live-wired code path
     claimed), which is the one case CONTROL_PLANE_MODES.md's CERTIFY
     step 4 says 0/0 is a valid pass rather than an unwired claim.
- Conclusion: the underlying L1 evidence holds up under independent
  re-verification — the prior entry's defect was procedural
  (self-certification), not substantive. Re-certified L2-CERTIFIED.

## FTF-0002 — Add formatting/lint/type checks
- Criterion ID: FTF-0002
- PR: https://github.com/SJZLab/Sovra/pull/23
- Merge commit: 41f1493bca4f70aa74ab61516d1a22ee197daf79
- Validated-by: session https://claude.ai/code/session_01DY5FK4nK2VLrUMKHwNQQU2
  (session-ID comparison performed and recorded before this entry: both
  commits on PR #23 — e3f0c1a981db04bd5d16078f5fd72fcebfc866c7 and
  6928d1437264d7b5efa530cdd07861f8f72bd21b — carry
  `Claude-Session: .../session_01NuAjwmK5rwuxefyo5L7zgy`; this session's ID
  does not match, so this is an independent certification, not
  self-certification)
- Date: 2026-09-16
- Evidence independently re-verified this session, not taken on the PR
  description's word:
  1. Read the full diff (`git show e3f0c1a`) and every changed config file
     (`pyproject.toml`, `eslint.config.mjs`, `tsconfig*.json`,
     `.github/workflows/ci.yml`) — confirmed no `continue-on-error`, no
     softened severity, and the only exclusion (`tools/r1_handoff.py`,
     from ruff+mypy) is a documented, narrow exception to keep that file
     byte-identical to the master spec's own Section 86 — not a
     production-code exclusion.
  2. Ran `uv sync` and `pnpm install` from a clean state independently
     (not reusing the producer's environment) — both succeeded, confirming
     the pre-existing `hatchling`/`uv sync` defect fix actually holds.
  3. Ran `tests/tasks/test_ftf_0002.py` myself: all 6 checks pass fresh.
  4. Fetched GitHub Actions run 35070915230 directly via the Actions API:
     workflow `CI`, event `pull_request`, head_sha
     `6928d1437264d7b5efa530cdd07861f8f72bd21b` (confirmed via
     `pull_request_read` to be PR #23's actual merged head), conclusion
     `success` — a genuine run, not a 0-job parse failure. `ci.yml` only
     triggers on `pull_request`, so there is no separate push-triggered
     run on the merge commit to cross-check — expected, not a gap.
  5. Performed my own fault injection rather than trusting the commit
     message's claim: appended `import os` to the real, tracked
     `scripts/status.py`, ran the unmodified `ruff check .` — genuine
     failure (`F401` + `E402`, exit 1). Reverted (`git checkout --`),
     re-ran — clean (exit 0, `All checks passed!`). Confirmed zero leftover
     diff before moving on.
  6. Ran `python3 scripts/product_truth_check.py --fail-on-unwired`:
     `0/0 product-true`, exit 0 — legitimate per CONTROL_PLANE_MODES.md
     CERTIFY step 4, since `docs/spec-matrix.yaml`'s FTF-0002 entry has
     `proving_symbol: ''` (CI tooling, not a live product-runtime path).
  7. Pulled the real contract via
     `python tools/r1_handoff.py packet FTF-0002 --plan FINE_TUNING_FACTORY_RELEASE_1.md`
     and checked the delivered work against it, not just "tests pass":
     acceptance ("deliberately introduce formatting/lint/type defects for
     Python and TS; each required command exits nonzero") — met, shown in
     both the test file's fixtures and my own manual injection above;
     negative_acceptance ("no exclusion of production package, altered
     severity or continue-on-error to bypass a failing tool") — met, per
     item 1. The packet's `verification_commands`
     (`scripts/validate_spec.py`, `scripts/verify_task.py`) don't exist
     yet — expected, per the packet's own text: those are gated behind
     FTF-H001's verification-map bootstrap, and until then FTF-0001/2/3
     use direct pinned commands with captured raw output, which is what
     this task actually did.
- Conclusion: L1 evidence holds up under independent re-verification —
  fresh install, fresh test run, fresh fault injection, real CI on the
  actual merged head, and the delivered work matches the real contract's
  acceptance/negative_acceptance clauses. Certified L2-CERTIFIED.

## FTF-0003 — Add test harnesses
- Criterion ID: FTF-0003
- PR: https://github.com/SJZLab/Sovra/pull/26
- Merge commit: e2c2dae96142c95a78005fd7528df6ac7e3e5dfc
- Validated-by: session https://claude.ai/code/session_018gZRsspGdmcTmtxkqRDE59
  (session-ID comparison performed and recorded before this entry: both
  commits on PR #26 — d4482de2cbcdda17000f059886c47b894b680830 and
  678285f1a5d69b0453b3ed630da5603c740734c2 — carry
  `Claude-Session: .../session_01DY5FK4nK2VLrUMKHwNQQU2`; this session's ID
  does not match, so this is an independent certification, not
  self-certification)
- Date: 2026-09-16
- Evidence independently re-verified this session, not taken on the PR
  description's word:
  1. Read the full PR diff (19 files changed) and the CI workflow change —
     confirmed no `continue-on-error`, no softened severity; the harness
     is wired into `.github/workflows/ci.yml` as real required steps, not
     appended after the gate.
  2. Ran `uv sync` and `pnpm install --frozen-lockfile` from a clean git
     worktree checked out at the actual merge commit (`e2c2dae`, not the
     producer's branch or environment) — both succeeded fresh.
  3. Ran the real CI commands myself: `uv run pytest tests/unit
     --junitxml=... -ra` (3 passed) and `pnpm exec vitest run
     --reporter=junit --outputFile=...` (2 passed), then
     `scripts/check_test_harness.py` against both reports — exit 0,
     "all reports clean". Then ran `tests/tasks/test_ftf_0003.py` itself —
     "all checks passed".
  4. Fetched GitHub Actions run 35083644711 directly via the Actions API
     (not the PR body's claim): workflow `CI`, event `pull_request`,
     head_sha `678285f1a5d69b0453b3ed630da5603c740734c2` (PR #26's actual
     merged head), conclusion `success` — a genuine run, not a 0-job parse
     failure.
  5. Performed my own fault injection, independent of the PR's claimed
     evidence, against the checked-out merge commit: (a) ran
     `check_test_harness.py` against an empty pytest dir's JUnit
     output — genuine "zero collected tests" failure, exit 1; (b) added a
     real `pytest.mark.skip` fixture — genuine "skipped/focused test(s)"
     failure, exit 1; (c) added a real Vitest `test.only` fixture — same
     genuine failure, exit 1. All three reverted; working tree confirmed
     clean afterward. This reproduces the negative_acceptance clause
     first-hand rather than trusting the commit message's description of
     it.
  6. Ran `python3 scripts/product_truth_check.py --fail-on-unwired`:
     `0/0 product-true`, exit 0 — legitimate per
     CONTROL_PLANE_MODES.md CERTIFY step 4, since `docs/spec-matrix.yaml`'s
     FTF-0003 entry has `proving_symbol: ''` (test-harness tooling, not a
     live product-runtime path). Also ran `scripts/status.py` — consistent
     pre-certification count (2/300).
  7. Re-ran the full pre-existing gate (`ruff check`, `ruff format
     --check`, `mypy`, `pnpm run lint`, `pnpm run format:check`, `pnpm run
     typecheck`) on the checked-out merge commit — all still pass clean,
     confirming FTF-0003 introduced no regression to FTF-0002's certified
     evidence. Confirmed `tests/test_ftf_0001_monorepo_layout.py` and
     `tests/tasks/test_ftf_0002.py` are untouched by this PR's diff
     (absent from the 19 changed files) — no Law 15 requalification
     question for either's existing certified evidence.
  8. Pulled the real contract via `python tools/r1_handoff.py packet
     FTF-0003 --plan FINE_TUNING_FACTORY_RELEASE_1.md` and checked the
     delivered work against it, not just "tests pass": acceptance clause 1
     ("harnesses collect real assertions and publish machine-readable
     counts") — met, shown by the JUnit XML output above; acceptance
     clause 2 ("independent review... recorded") — met by this entry;
     negative_acceptance ("zero collected tests, focused .only or skipped
     required test must fail validation; remove template sample tests")
     — met, per item 5, and the two new `.test.ts` files plus
     `test_product_truth_check_helpers.py` each test a real (if trivial)
     existing constant/function, not template filler. The packet's
     `verification_commands` (`scripts/validate_spec.py`,
     `scripts/verify_task.py`) still don't exist in the checked-out tree
     — expected and already established at FTF-0002's certification:
     gated behind FTF-H001's verification-map bootstrap, not a new gap
     introduced by this task. The packet's suggested single file
     (`apps/api/app/config/add_test_harnesses.py`) was not created;
     the packet's own text says initial file paths are not binding and
     path refinement is allowed, and the delivered layout (harness
     configs + a shared gate script + task-evidence test) is the coherent
     structure for two-language test tooling, not a scattered
     one-file-per-ticket approach the packet explicitly warns against.
- Conclusion: L1 evidence holds up under independent re-verification on a
  freshly checked-out copy of the actual merge commit — fresh install,
  fresh test run, three independent fault injections (not the producer's),
  real CI on the actual merged head, no regression to prior certified
  evidence, and the delivered work matches the real contract's
  acceptance/negative_acceptance clauses. Certified L2-CERTIFIED.

## FTF-0005 — Configuration system
- Criterion ID: FTF-0005
- PR: https://github.com/SJZLab/Sovra/pull/30 (fix for the defect found in
  PR #28's review, see PR #29 / docs/certifications/queue.md "Control
  plane review — 2026-09-16 — NOT CERTIFIED (defect found)")
- Merge commit: 5f5a34d803a76e3261c8cc860bd5f21cd8290424
- Validated-by: session https://claude.ai/code/session_01WqPEZmJdJyFH7tk2cbakWP
  (session-ID comparison performed and recorded before this entry: both
  commits on PR #30 — 9f2dcbca6866871bf17edf68ea02fed7b61748ad and
  65824c56879b80e012f19c4b4f9b9e8b0098f5fe — carry
  `Claude-Session: .../session_012mAtoqKYzA7PiXbkGkD7rY`, the same
  producer session that authored PR #28; this session's ID does not
  match either that session or the prior reviewing session
  `session_01WjQKEb87DwDx51YLNRxT6o` that rejected PR #28, so this is an
  independent certification, not self-certification)
- Date: 2026-09-16
- Evidence independently re-verified this session, not taken on the PR
  description's word:
  1. Re-read the full PR #30 diff (6 files) — confirmed the change is
     exactly the scoped fix the prior rejection called for:
     `_redact_url()` now applied to `litellm_gateway_url` in
     `Settings.__repr__` (`apps/api/app/config/configuration_system.py`),
     matching how `database_url` was already handled, plus one new test
     case. No other line of the original FTF-0005 delivery was touched.
  2. Checked out the actual PR head commit
     (`65824c56879b80e012f19c4b4f9b9e8b0098f5fe`, which became the second
     parent of merge commit `5f5a34d`) into a fresh git worktree — not the
     producer's branch or environment — and ran `uv sync` fresh, then
     `python3 tests/tasks/test_ftf_0005.py`: 10/10 checks pass.
  3. Performed my own fault injection, independent of the PR's claimed
     evidence: reverted the one-line fix in the checked-out worktree
     (restored the raw, unredacted `litellm_gateway_url` interpolation),
     re-ran `tests/tasks/test_ftf_0005.py` — exactly 1 check failed for
     real, with the expected message ("an accepted gateway URL's embedded
     password never appears in Settings' repr"), reproducing the original
     certifier's exact finding from first principles rather than trusting
     the commit message's description of it. Restored the fix from a
     pre-edit copy, `diff` confirmed empty, re-ran clean (10/10 pass
     again).
  4. Re-ran the full pre-existing regression set on the same worktree:
     `ruff check .` / `ruff format --check .` / `mypy .` all clean;
     `pytest tests/unit` — 3 passed, unchanged. No regression to prior
     certified evidence (FTF-0001/0002/0003 are untouched by this diff).
  5. Fetched GitHub Actions check runs directly via the API for PR #30's
     actual head SHA (`65824c5`, matching `list_pull_requests`' reported
     head, not a stale or self-reported claim): job `gate`,
     run https://github.com/SJZLab/Sovra/actions/runs/35087235376,
     conclusion `success`, completed 2026-09-16T10:52:22Z.
  6. Ran `python3 scripts/product_truth_check.py --fail-on-unwired`:
     `0/0 product-true`, exit 0 — legitimate per CONTROL_PLANE_MODES.md
     CERTIFY step 4, since `docs/spec-matrix.yaml`'s FTF-0005 entry has
     `proving_symbol: ''` (no live caller wires this module into a running
     service yet — no `apps/api/app/main.py` exists, same rationale
     already established and unchanged from the original FTF-0005
     evidence).
  7. Pulled the real contract via `python tools/r1_handoff.py packet
     FTF-0005 --plan FINE_TUNING_FACTORY_RELEASE_1.md` and re-checked the
     delivered work against it: negative_acceptance ("No development
     fallback credentials or production tls_verify=false; sensitive
     values absent from errors") is now genuinely met for the field the
     prior review found unmet — an *accepted* gateway URL's embedded
     password is confirmed absent from `repr(settings)`, per item 3
     above, closing the exact gap the original evidence only tested in
     its *rejected*-URL form. The other 8 original checks (acceptance
     clause 1: typed rejection of missing/invalid URLs, policy enum,
     budget, secret refs; no other negative_acceptance regression) were
     not touched by this PR and remain as independently established in
     this same session's prior review of PR #28 (which did not dispute
     them).
- Conclusion: the scoped defect from the prior rejection is fixed, proven
  by independent fault injection (not the producer's own test run), CI is
  real and green on the actual merged head, and the delivered fix matches
  the real contract's negative_acceptance clause with no regression to
  the rest of FTF-0005 or to prior certified evidence. Certified
  L2-CERTIFIED.

## FTF-0004 — Docker local stack
- Criterion ID: FTF-0004
- PR: https://github.com/SJZLab/Sovra/pull/35
- Merge commit: a477e678b785d3248bdc985c91586b148411b4fe
- Validated-by: session https://claude.ai/code/session_0164zgaFH5z6hqEmpCyD1mLE
  (session-ID comparison performed and recorded before this entry: all
  seven commits on PR #35 — 5fefe0a, f2c8a2c, 3aaab6f, bc603a6, 330be28,
  c9ab1c9, c595897, 63b13ce — carry
  `Claude-Session: .../session_01NV9ZvW9ptNnx8RzFWUoZQi`; this session's
  ID does not match, so this is an independent certification, not
  self-certification)
- Date: 2026-09-16
- Evidence independently re-verified this session, not taken on the PR
  description's word:
  1. Confirmed `docs/spec-matrix.yaml` on `main` already showed
     FTF-0001/0002/0003/0005 all `CERTIFIED`/`L2-CERTIFIED` and FTF-0004
     as the sole `PROVISIONAL`/`L1-PROVEN` entry — the one real pending
     item, matching `docs/certifications/queue.md`'s bottom entry ("Real
     CI failure found and fixed on the merge commit").
  2. Read the full delivered diff on the checked-out merge commit:
     `infra/docker/docker-compose.yml` (postgres:16-alpine,
     temporalio/auto-setup:1.24.2, chrislusf/seaweedfs:3.71, all pinned
     by digest — SeaweedFS confirmed as the master spec's own qualified
     S3-compatible fixture, `FINE_TUNING_FACTORY_RELEASE_1.md` §77.3/S32
     and `docs/architecture/REFERENCE_REPOSITORIES.md`, not an invented
     substitute), `apps/api/app/config/docker_local_stack.py` (stdlib-only
     TCP-probe readiness + `/health` handler, no new runtime dependency),
     `scripts/verify_docker_local_stack.py`, `tests/tasks/test_ftf_0004.py`,
     and the `.github/workflows/ci.yml` `docker-local-stack-integration`
     job. No `continue-on-error`, no canned-healthy fallback path.
  3. Ran `tests/tasks/test_ftf_0004.py` myself on the checked-out merge
     commit: 9/9 checks pass.
  4. Performed my own fault injection, independent of the producer's
     claimed evidence: replaced `check_tcp_reachable` with a hardcoded
     `ReadinessResult(..., True, "canned healthy (fault injection)")` in
     the tracked module, re-ran the test — 4 of 9 checks failed for real
     (`the healthy detail names the actual host/port checked`, `a port
     nothing listens on is reported unhealthy`, `readiness flips to
     unhealthy the instant the socket is closed`, `any dependency
     unreachable -> stack is not ready`), reproducing the negative-
     acceptance property from first principles. Restored via `git
     checkout --`, `git diff` confirmed empty, re-ran clean (9/9).
  5. Fetched GitHub Actions run 35096166703 directly via the Actions API
     (not the PR body's claim): workflow `CI`, event `pull_request`,
     head_sha `63b13cec9a58822290e678231069427370678855` (PR #35's actual
     final head, confirmed via `pull_request_read get_commits`), both
     `gate` and `docker-local-stack-integration` jobs conclusion
     `success`. Read `docker-local-stack-integration`'s full job log
     directly (`get_job_logs`), not inferred from the green check: it
     shows the real sequence — stack booted, `stack ready: {...}` with
     all three services healthy, a marker row inserted into Postgres,
     `docker compose stop postgres`, readiness genuinely flipping to
     `{'ready': False, ...}` (real DNS-resolution failure once the
     container stopped, not a canned response), `docker compose start
     postgres`, recovery to `{'ready': True, ...}`, and the marker row
     confirmed to have survived the restart — the exact
     negative_acceptance evidence (restart/persistent state, not canned
     readiness) FTF-0004 requires.
  6. Ran `python3 scripts/product_truth_check.py --fail-on-unwired`:
     `0/0 product-true`, exit 0 — legitimate per CONTROL_PLANE_MODES.md
     CERTIFY step 4, since `docs/spec-matrix.yaml`'s FTF-0004 entry has
     `proving_symbol: ''` (no live caller wires this into a running
     product service yet — same rationale already established for
     FTF-0002/0003/0005).
  7. Verified `CLAUDE.md.sha256` matches `sha256sum CLAUDE.md` — no
     drift.
  8. Pulled the real contract via `python tools/r1_handoff.py packet
     FTF-0004 --plan FINE_TUNING_FACTORY_RELEASE_1.md` and checked the
     delivered work against its actual acceptance/negative_acceptance
     clauses, not just "tests pass": acceptance clause 1 ("one documented
     command boots real API/web/Postgres/Temporal/S3-compatible services
     and health probes pass") is met for API/Postgres/Temporal/S3 via
     `docker compose -f infra/docker/docker-compose.yml up -d`, confirmed
     against the real CI log above; `web` is the one component not
     booted — verified independently that `apps/web` genuinely has no
     server-side runtime on disk (only a trivial `src/index.ts` constant,
     `README.md`, `package.json`, `tsconfig.json` — no Next.js/React
     installed), so there is nothing real to boot, and inventing a
     placeholder web server to satisfy the clause literally would itself
     violate Law 11 (no placeholder routes) while adding the real
     Next.js runtime now would be a new-runtime-dependency ask-first item
     under CLAUDE.md, out of FTF-0004's own scope and dependency set
     (FTF-0001/0003/0005, none of which build the web app). This is a
     disclosed, reasoned scope limitation — documented in the compose
     file's own header, `docs/spec-matrix.yaml`'s notes, and this queue
     history, not a silently dropped requirement — and is accepted as
     meeting the clause's intent given the current, honestly-represented
     state of the repo; tracked as a required follow-up for whichever
     task first builds the real web app. Acceptance clause 3 (qualified
     SeaweedFS fixture, not an automatic MinIO install) is met, per item
     2. Negative_acceptance ("unhealthy DB/storage/Temporal prevents
     readiness; test restart/persistent state instead of canned
     readiness") is met, per items 4 and 5 (both a synthetic fault
     injection and the real container restart/persistence evidence).
- Conclusion: the L1 evidence holds up under independent re-verification
  — fresh fault injection, the actual CI job log read directly (not just
  its green status), `product_truth_check.py`'s 0/0 confirmed legitimate,
  and the delivered work matches the real contract's acceptance/
  negative_acceptance clauses, with the one disclosed scope limitation
  (`web` excluded) being a sound, transparent engineering call rather
  than a hidden gap. Certified L2-CERTIFIED.

## FTF-0006 — Observability foundation
- Criterion ID: FTF-0006
- PR: https://github.com/SJZLab/Sovra/pull/39
- Merge commit: 268186bd5e70032a6c563796910059ab2684b684
- Validated-by: session https://claude.ai/code/session_01LPY7Wy6ASA6BiQnvu9eyvF
  (session-ID comparison performed and recorded before this entry: both
  commits on PR #39 — 91eeff1, 7d5834f — plus the PR's own head commit
  0970fa8 all carry `Claude-Session: .../session_01GvzemvrJeMmGiZsAAPK5m1`;
  this session's ID does not match, so this is an independent
  certification, not self-certification)
- Date: 2026-09-16
- Evidence independently re-verified this session, not taken on the PR
  description's word:
  1. Confirmed `docs/spec-matrix.yaml` on `main` showed
     FTF-0001/0002/0003/0004/0005 all `CERTIFIED`/`L2-CERTIFIED` and
     FTF-0006 as the sole `PROVISIONAL`/`L1-PROVEN` entry — matching
     `docs/certifications/queue.md`'s bottom entry.
  2. Read the full delivered diff: `apps/api/app/config/
     observability_foundation.py` (stdlib-only `StructuredEvent`/
     `MetricsRegistry`/`InstrumentedRequest`, no new runtime dependency),
     `tests/tasks/test_ftf_0006.py`, and the `.github/workflows/ci.yml`
     `gate`-job wiring.
  3. Checked out the PR's actual head commit (`0970fa8`, confirmed via
     `pull_request_read get_commits`/`get` against the live PR, not an
     older SHA) into an isolated git worktree; ran `tests/tasks/
     test_ftf_0006.py` myself — 10/10 pass.
  4. Performed my own fault injection, independent of the producer's
     claimed evidence (which targeted `MetricsRegistry.record`'s
     bounded-label check): replaced `_SAFE_IDENTIFIER`'s regex with an
     unrestricted `.*` pattern in the tracked module, re-ran the test
     suite — exactly 1 check failed for real (`free-form content
     (prose/newlines/signed-URL querystrings/oversized blobs) cannot
     pass as identity`), reproducing the negative-acceptance content-
     exclusion property from first principles. Restored the file
     byte-identical from a pre-edit copy (`diff` confirmed empty),
     re-ran clean (10/10).
  5. Ran the full regression suite myself in the same worktree (fresh
     `uv sync`/`pnpm install --frozen-lockfile`): `ruff check`/`ruff
     format --check`/`mypy` clean; `pytest tests/unit` (3 passed);
     `tests/tasks/test_ftf_0002.py`/`test_ftf_0003.py`/`test_ftf_0004.py`/
     `test_ftf_0005.py` and `tests/test_ftf_0001_monorepo_layout.py` all
     pass unmodified; `pnpm run lint`/`typecheck`/`format:check` and
     `pnpm exec vitest run` (2 passed) clean — no regression to prior
     certified evidence.
  6. Ran `product_truth_check.py --fail-on-unwired` myself: `0/0
     product-true`, exit 0 — legitimate per CONTROL_PLANE_MODES.md
     CERTIFY step 4, since `docs/spec-matrix.yaml`'s FTF-0006 entry has
     `proving_symbol: ''` (no live caller wires this into a running
     product service yet — same rationale already established for
     FTF-0002/0003/0004/0005).
  7. Fetched GitHub Actions run 35112030663 directly via the Actions API
     (not the PR body's claim): event `pull_request`, `head_sha`
     `0970fa8eb4b80635cd919f929d74f1ac49884325` (confirmed as the PR's
     actual final head), both `gate` and `docker-local-stack-integration`
     jobs conclusion `success`. Read the `gate` job's full log directly
     (`get_job_logs`): shows the real sequence — `FTF-0006 observability
     foundation: all checks passed` followed by `Product-truth: 0/0
     product-true` and `Status: 5/300 certified · 0 product-true` (the
     pre-certification count, as expected on that commit), not inferred
     from the green check alone.
  8. Verified `CLAUDE.md.sha256` matches `sha256sum CLAUDE.md` — no
     drift.
  9. Pulled the real contract via `python tools/r1_handoff.py packet
     FTF-0006 --plan FINE_TUNING_FACTORY_RELEASE_1.md` and checked the
     delivered work against its actual acceptance/negative_acceptance
     clauses: acceptance clause 1 ("one actual request crosses
     API/workflow instrumentation with correlation and numeric metrics")
     is met by `test_one_real_http_request_crosses_instrumentation` — a
     real `ThreadingHTTPServer` bound to a real socket, hit via
     `http.client` over the wire (not an in-process call), whose response
     carries a correlation-ID header matching the emitted `StructuredEvent`,
     with a numeric duration and a metric count of exactly 1 recorded.
     Negative_acceptance ("training prompts, raw source, tokens and signed
     URLs never appear in logs/spans; cardinality remains bounded") is met
     for its concrete, tested failure modes: `StructuredEvent`'s fixed
     field set structurally has no field for raw content (an unexpected
     keyword raises `TypeError` at construction — reproduced independently
     in item 4's spirit); prose, newlines, signed-URL querystrings and
     oversized blobs passed as `identity` are rejected (proven by my own
     fault injection above); cardinality is proven bounded quantitatively
     (50 distinct correlation IDs -> 1 observed metric label, correlation
     IDs never themselves used as labels).
     **Disclosed residual gap, not a hidden defect:** the safe-identifier
     format boundary on `identity` cannot distinguish a legitimate short
     slug-style ID from an opaque token of the same shape — the module's
     own docstring, the validator's error message, and the test file's
     own comment all state this explicitly rather than claiming full
     token exclusion. This differs from the FTF-0005 PR #28 defect (an
     *accepted*, in-scope field — a valid gateway URL with embedded
     credentials — silently unredacted in `repr()`, contradicting that
     module's own stated contract): here `identity` is not designed to
     carry credentials at all, no live caller exists yet to pass one
     through it, and the limitation is honestly scoped down rather than
     falsely claimed as closed. Recorded as a required follow-up in
     `docs/spec-matrix.yaml`'s notes for whichever task first wires a
     real caller into this seam, not accepted as silently closed.
- Conclusion: the L1 evidence holds up under independent re-verification
  — fresh fault injection targeting a different code path than the
  producer's own, the actual CI job log read directly (not just its green
  status) on the PR's real final head, `product_truth_check.py`'s 0/0
  confirmed legitimate, and the delivered work matches the real
  contract's acceptance/negative_acceptance clauses for every failure
  mode they concretely test, with one disclosed residual gap (opaque
  tokens shaped like slug IDs) that is honest, scoped, and inert while no
  live caller exists — tracked as a follow-up rather than a blocker.
  Certified L2-CERTIFIED.

## FTF-H001 — Executable specification and gate bootstrap
- Criterion ID: FTF-H001
- PR: https://github.com/SJZLab/Sovra/pull/43 (`claude/wizardly-fermat-43jlvy`
  → `main`), merged as `be6494c3ec97615417f9f1e9fabad9dfa83feaf4`. The
  follow-up PR #44 on the same branch touched only `PROGRESS.md` (a
  tracking-doc note recording PR #43's PROVISIONAL merge) — confirmed via
  `git diff be6494c 37e68bb --stat`, no code or spec-matrix/queue change,
  so it does not affect this certification.
- Merge commit: 6a1abc093f2c8897e86d8e33bd33667d1c2ee0f3 (implementation;
  `evidence_hash` recorded in `docs/spec-matrix.yaml`)
- Validated-by: session https://claude.ai/code/session_01AmkjgSrjZzfKJXAYH7f8Hp
  (session-ID comparison performed and recorded before this entry: all 6
  commits on PR #43 — 6a1abc0, d4301b4, de41d12, 3bd23c9, 86ed2bc, ca53fe5
  — carry `Claude-Session: .../session_01FJDW152aaaSm3uNPj9NCXH`; this
  session's ID does not match, so this is an independent certification,
  not self-certification)
- Date: 2026-09-16
- Evidence independently re-verified this session, not taken on the PR
  description's or queue.md entry's word:
  1. Confirmed `docs/spec-matrix.yaml` on `main` showed
     FTF-0001–FTF-0006 all `CERTIFIED`/`L2-CERTIFIED` and FTF-H001 as the
     sole `PROVISIONAL`/`L1-PROVEN` entry, matching
     `docs/certifications/queue.md`'s bottom entry and `ledger.md`'s own
     last prior entry (FTF-0006).
  2. Confirmed `CLAUDE.md.sha256` matches `sha256sum CLAUDE.md` on the
     merged tree — no drift.
  3. Confirmed via GitHub's own data (not the PR body's claim) that PR
     #43's actual final head is `ca53fe5801f7f78ea42445aea1b6431c90d1754f`
     and that `pull_request_read get_check_runs` reports both `gate` and
     `docker-local-stack-integration` `completed`/`success` tied to that
     exact head SHA. Confirmed `git diff be6494c ca53fe5 --stat` is empty
     — the merge commit on `main` is tree-identical to the CI-verified PR
     head, not a manual re-edit.
  4. Read the full delivered diff directly from a clean worktree checkout
     of the merged `main` tip (`git worktree add --detach`, not trusting
     the PR body's own description): `scripts/spec_verification.py`
     (`load_tasks`/`check_structural`/`check_evidence`/`validate`/
     `verify_task`), `scripts/validate_spec.py`, `scripts/verify_task.py`,
     `scripts/verify_release.py`, `spec/verification-map.json`, and
     `tests/tasks/test_ftf_h001.py`. All subprocess calls use argv lists
     (`shell=False`), no shell-injection surface; an unmapped task or
     unknown `--stage` returns `BLOCKED`/`FAIL` structurally, never a
     fabricated `PASS`.
  5. Set up the real toolchain (`uv sync`, `pnpm install` — neither was
     present in the fresh checkout) and re-ran every command for real:
     `python3 scripts/validate_spec.py --strict` → clean; `python3
     scripts/verify_task.py --task FTF-H001 --stage task` → `PASS` (this
     task's own two `verification_commands` from its real contract, both
     genuinely re-run, not assumed). Before installing `pnpm`'s
     dependencies this same command genuinely `FAIL`ed for a mundane
     reason (missing `node_modules`, not a code defect) — confirmed by
     running `tests/tasks/test_ftf_0002.py` directly and seeing the same
     eslint/tsc failures, then re-confirming clean after `pnpm install`;
     recorded here as evidence the re-verification was live, not
     rubber-stamped.
  6. Ran the full regression set clean on the merged tree: `ruff check .`
     / `mypy .` / `pytest tests/unit` (3 passed) / `pnpm run lint` /
     `pnpm run typecheck`, and all 7 task negative-test-proof scripts
     (`test_ftf_0001_monorepo_layout.py` through `test_ftf_0006.py`, plus
     the new `test_ftf_h001.py`) each print their own `all checks passed`
     line.
  7. `product_truth_check.py --fail-on-unwired` → `0/0 product-true`,
     confirmed legitimate per CERTIFY step 4: `docs/spec-matrix.yaml`'s
     FTF-H001 entry has `proving_symbol: ''` — this is CI/gate tooling,
     not a live product-runtime path, same disclosed rationale already
     established for FTF-0002/0003.
  8. `verify_release.py` run live on the merged tree (before this
     certification) correctly reported `6/300 required task(s) have
     live-passing, CERTIFIED evidence` / `NOT READY`, with FTF-H001 itself
     listed under `FAILED` because its own `certification_state` was
     still `PROVISIONAL` at that point — proving the release gate is not
     inflated and correctly requires both live-passing evidence and a
     real `CERTIFIED` state, not just one or the other.
  9. **Real fault injection against the tracked module, targeting a
     different code path than either of the producer's own two fault
     injections (which corrupted `commit` and `expected_output_contains`
     in the shipped map file):** disabled the Kahn's-algorithm
     cycle-detection result in `scripts/spec_verification.py`'s
     `check_structural` (`if False and seen != len(nodes):`) — re-ran
     `tests/tasks/test_ftf_h001.py`: exactly the expected check failed
     for real (`cyclic_dependency fixture (A -> C -> B -> A) is
     rejected`), all others stayed green. Restored the file
     byte-identical from a pre-edit copy, confirmed via `diff`, re-ran
     clean (all checks pass again).
  10. Pulled the real contract via `python tools/r1_handoff.py packet
      FTF-H001 --plan FINE_TUNING_FACTORY_RELEASE_1.md` and checked the
      delivered work against its actual acceptance/negative_acceptance
      clauses, not just "tests pass": acceptance clause 1 ("every invalid
      fixture fails" across missing task/cyclic dependency/duplicate
      ID/missing scenario/skipped test/stale commit) is met by
      `test_missing_task_fixture_rejected` /
      `test_cyclic_dependency_fixture_rejected` /
      `test_duplicate_id_fixture_rejected` /
      `test_missing_scenario_fixture_rejected` /
      `test_skipped_test_fixture_rejected` /
      `test_stale_commit_fixture_rejected`, each independently reproduced
      by item 9's fault-injection method in spirit (item 9 itself
      exercises the cyclic case directly). Acceptance clause 2 ("no full
      release claim until all mapped scenarios and exact-revision
      evidence pass") is enforced structurally by `verify_release.py`,
      confirmed live and unstated in item 8. Acceptance clause 3
      ("independently protected real verifier inputs... prove the
      bootstrapped tracker has no fabricated completion evidence") is met
      by `test_real_certified_task_reverifies_live` (the real FTF-0002
      map entry re-verified live through the actual CLI against this
      checkout, not fabricated) and the `stale_commit`/`skipped_test`
      checks operating against the real shipped map file. Negative
      acceptance ("zero selected tests or absent external credentials
      must report blocked/fail rather than pass; preserve original task
      IDs and expected test counts") is met by
      `test_negative_acceptance_blocked_not_fabricated_pass`
      (`verify_task("FTF-DOES-NOT-EXIST", "task")` → `BLOCKED`, never
      `PASS`) and `test_unknown_stage_rejected`.
  11. Scope check: `packages/contracts/openapi/18-interface.yaml` and
      `jsonschema/18-domain.schema.json`, named in the task packet's
      `files`, are not required by any acceptance/negative_acceptance
      clause above and no real endpoint exists yet to back them — the
      queue entry's disclosed exclusion is accurate, not a hidden gap.
- Conclusion: the L1 evidence holds up under independent re-verification
  — a fresh fault injection targeting a different code path than the
  producer's own, the actual CI check-runs and merge-tree identity
  confirmed via GitHub's own data (not the PR's self-report), the real
  toolchain installed and every verification command genuinely re-run
  (catching and resolving one mundane missing-dependency failure along
  the way, not silently working around it), `product_truth_check.py`'s
  0/0 and `verify_release.py`'s `6/300`/`NOT READY` both confirmed
  legitimate and non-inflated, and the delivered work matches the real
  task-packet contract's acceptance/negative_acceptance clauses item by
  item. No open defect. Certified L2-CERTIFIED.

## FTF-R001 — Freeze seven-component architecture, ownership and version-lock schemas
- Criterion ID: FTF-R001
- PR: https://github.com/SJZLab/Sovra/pull/47 (`claude/wizardly-fermat-1r7ubn`
  → `main`), merged as `2e3e1af0b81789da4a21e68bd536c3baae9f2ac2`.
- Merge commit: f9c82e910cde344a08b910c5b059e85c25b59adf (implementation;
  `evidence_hash` recorded in `docs/spec-matrix.yaml`)
- Validated-by: session https://claude.ai/code/session_01MrAN34weU7YzSCe6khm4H4
  (session-ID comparison performed and recorded before this entry: all 4
  commits on PR #47 — f9c82e9, 42c1adf, 20ddc53, 924f698 — carry
  `Claude-Session: .../session_014i1TTcqPb3qynFFHsBfkvo`; this session's ID
  does not match, so this is an independent certification, not
  self-certification)
- Date: 2026-09-16
- Evidence independently re-verified this session, not taken on the PR
  description's or queue.md entry's word:
  1. Confirmed `docs/spec-matrix.yaml` on `main` showed FTF-0001–FTF-0006
     and FTF-H001 all `CERTIFIED`/`L2-CERTIFIED` and FTF-R001 as the sole
     `PROVISIONAL`/`L1-PROVEN` entry before this certification, matching
     `docs/certifications/queue.md`'s bottom entry.
  2. Confirmed `CLAUDE.md.sha256` matches `sha256sum CLAUDE.md` on the
     merged tree — no drift.
  3. Confirmed via GitHub's own data (not the PR body's claim) that PR
     #47's `get_check_runs` reports both `gate` and
     `docker-local-stack-integration` `completed`/`success`, tied to the
     PR's actual head SHA `924f69853155855a22e7cc3406561447fc4154b3` — the
     CI run did not exist yet at the time this session first read the PR
     (`status: queued`); subscribed to PR activity and waited for the real
     `check_suite.completed` webhook event rather than assuming success.
     `mergeable_state: clean` confirmed directly from GitHub before merging.
  4. Merged PR #47 myself (green CI, clean merge state, L1-PROVEN evidence
     already on record — permitted under CLAUDE.md's "Do without asking:
     merge on green + L1 doc (PROVISIONAL)"), then re-verified against the
     real merged tip in a fresh `git worktree add --detach`, not the PR
     branch — `git diff` of the merge commit against the PR's CI-verified
     head is empty (tree-identical), confirming the merge did not alter the
     reviewed content.
  5. Read the full delivered diff directly (13 files, +1602/-17, matching
     the PR's own stat): `packages/component-contracts/architecture.schema.json`,
     `spec/component-lock.schema.json`, `spec/deployment-profiles.yaml`,
     `docs/architecture/reuse-ownership.md`, `scripts/architecture_lock.py`,
     `tests/tasks/test_ftf_r001.py`. Confirmed the whole-set business rule
     (exactly seven canonical keys, no missing/duplicate/substituted) lives
     in `scripts/architecture_lock.py`, not only in JSON Schema
     `minItems`/`maxItems`, and that the Axolotl/Inspect-AI worker-package
     bar and the mutable-tag rejection are real schema constraints
     (`allOf`/`if`/`then` and a semver-only `pattern`), not decorative.
  6. Set up the real toolchain (`uv sync`, `pnpm install` — neither was
     present in the fresh checkout) and re-ran every command for real:
     `python3 scripts/validate_spec.py --strict` → clean; `python3
     scripts/verify_task.py --task FTF-R001 --stage task` → `PASS` (this
     task's own two `verification_commands` from its real contract).
  7. Ran the full regression set clean on the merged tree: `ruff check .`
     / `ruff format --check .` / `mypy .` / `pytest tests/unit` (3 passed)
     / `pnpm run lint` / `pnpm run typecheck` / `pnpm run format:check` /
     `pnpm exec vitest run` (2 passed), and all 8 task negative-test-proof
     scripts (`test_ftf_0001_monorepo_layout.py` through the new
     `test_ftf_r001.py`) each print their own `all checks passed` line.
  8. `product_truth_check.py --fail-on-unwired` → `0/0 product-true`,
     confirmed legitimate per CERTIFY step 4: `docs/spec-matrix.yaml`'s
     FTF-R001 entry has `proving_symbol: ''` — architecture/schema freeze,
     no live product-runtime caller yet, same disclosed rationale already
     established for FTF-0002/0003/H001.
  9. `verify_release.py` run live on the merged tree (before this
     certification) correctly reported `7/300 required task(s) have
     live-passing, CERTIFIED evidence` / `NOT READY`, with FTF-R001 itself
     listed under `FAILED` because its own `certification_state` was still
     `PROVISIONAL` at that point — proving the release gate is not inflated.
  10. **Real fault injections against the tracked modules, none reused
      verbatim from the producer's own two (which disabled the whole-set
      key-check function body and removed the Axolotl/Inspect-AI `allOf`
      rule from the schema):**
      - Widened `spec/component-lock.schema.json`'s `package_version`
        `pattern` to `.*` — a lock entry pinned to the mutable tag
        `"latest"` then passed `validate_component_lock_document` with
        zero errors, proving the real semver-only pattern (not a fixture
        assumption) is what rejects mutable tags in
        `test_mutable_latest_package_version_rejected`.
      - Removed `release_approval_authorities`' `maxItems: 1` cap and
        loosened `releaseApprovalAuthority.component_key`'s `type` to
        allow a string — a document with two release-approval-authority
        records, one naming `mlflow` as a vendor authority, then passed
        with zero errors, proving the real schema constraints (not the
        test fixtures alone) are what reject a duplicate/vendor-claimed
        release authority.
      Both edits restored byte-identical from pre-edit copies (`diff`
      confirmed empty), re-ran `tests/tasks/test_ftf_r001.py` clean
      (`all checks passed`) after each restore.
  11. Pulled the real contract via `python tools/r1_handoff.py packet
      FTF-R001 --plan FINE_TUNING_FACTORY_RELEASE_1.md` and checked the
      delivered work against its actual acceptance/negative_acceptance
      clauses, not just "tests pass": acceptance clause 1 ("exactly seven
      selected component keys ... pin all runtime dependencies before
      qualification") is met by the seven-key enum plus whole-set rule
      (item 5, item 10) and `component-lock.schema.json`'s pinned-version/
      digest requirements (item 10). Acceptance clause 2 ("Define
      ComponentBinding/VersionLock/ExternalRunBinding and ownership") is
      met for `ComponentBinding`/`ComponentVersionLock` by the two frozen,
      machine-validated schemas; `ExternalRunBinding` is documented in
      `docs/architecture/reuse-ownership.md` with the exact field set from
      `FINE_TUNING_FACTORY_RELEASE_1.md` Section 77.5 but has no frozen
      JSON Schema, disclosed as deferred because no live component run
      exists yet to bind — checked this against the same, already-certified
      precedent (FTF-0004's `web` exclusion, FTF-H001's undelivered
      `packages/contracts/` stub paths) rather than accepting the analogy
      on the producer's word alone; the task's own `negative_acceptance`
      clause does not name `ExternalRunBinding`, so no test gap results.
      "UI calls internal Factory API ... not separate dashboards" is
      recorded in `reuse-ownership.md`'s Section 77.6 summary — no UI code
      exists yet in this repo to contradict it. Negative_acceptance
      ("Reject missing/extra substituted component, invented always-on
      Axolotl/Inspect HTTP endpoint, duplicate registry approval authority
      and mutable latest-version release lock") — all four cases have a
      real test (`test_missing_component_rejected`,
      `test_extra_substituted_component_rejected`,
      `test_duplicate_component_rejected`,
      `test_invented_axolotl_http_endpoint_rejected`,
      `test_invented_inspect_ai_http_endpoint_rejected`,
      `test_second_release_approval_authority_rejected`,
      `test_vendor_component_claiming_release_authority_rejected`,
      `test_mutable_latest_package_version_rejected`,
      `test_mutable_latest_image_digest_rejected`) and item 10's
      independent fault injections directly confirm the underlying schema
      constraints — not the fixtures alone — are what does the rejecting
      for the mutable-tag and duplicate/vendor-authority cases.
- Conclusion: the L1 evidence holds up under independent re-verification —
  CI genuinely completed green on the real PR head (waited for the actual
  webhook rather than assuming), the merge commit is tree-identical to the
  CI-verified head, the real toolchain installed and both verification
  commands genuinely re-run, two fresh fault injections (distinct from the
  producer's own) confirming the mutable-tag and duplicate/vendor-authority
  rejections are schema-enforced rather than fixture-assumed,
  `product_truth_check.py`'s 0/0 and `verify_release.py`'s `7/300`/
  `NOT READY` both confirmed legitimate and non-inflated, and the delivered
  work matches the real task-packet contract's acceptance/negative_acceptance
  clauses item by item, including an explicit check that the disclosed
  `ExternalRunBinding` scope deferral follows existing certified precedent
  rather than being taken on trust. No open defect. Certified L2-CERTIFIED.

## FTF-H002 — Freeze release scope, compatibility and measured execution inputs
- Criterion ID: FTF-H002
- PR: https://github.com/SJZLab/Sovra/pull/51 (`claude/wizardly-fermat-w4h9ys`
  → `main`), superseding PR #49 (`claude/wizardly-fermat-brui19`, closed as
  superseded — same code, byte-identical). Merged `2026-09-17` by
  `samerzakaria` (human) as merge commit `b0f818498660fa0f376fdf1d2a0db42baac25e43`.
- Evidence commit: `40b61d221238a25a2420cabc945d0d41d91b36e3` (implementation;
  `evidence_hash` recorded in `docs/spec-matrix.yaml`).
- Validated-by: session https://claude.ai/code/session_01ED5D84zpb2djpogKY9fktw
  (session-ID comparison performed and recorded before this entry: read every
  commit on PR #51 — `40b61d2`, `3f5e618`, `af2b0fa`, `3d235b4`, `419f9cc`
  carry `Claude-Session: .../session_01KcK5jpotjXCpjrQj8qMPVp`; `1df4f83`,
  `55d3ba6`, `c6985e0` carry `.../session_01BNQGXo9Q8t1PFdEmxQZXHF`; the
  merge commit `b0f8184` carries none (a human GitHub merge). This session's
  ID matches neither producer session — independent, not self-certification).
- Date: 2026-09-17
- Prior review on record: `docs/certifications/queue.md`'s 2026-09-16 entry
  (session `session_01BNcWR4me2X2YrMtsW6uqfV`) withheld certification on PR
  #49 for a real `gate` CI failure (`check_kubernetes_cni()`'s test
  hard-assumed `kubectl` absent, which is false on GitHub-hosted
  `ubuntu-latest`). Confirmed that fix (`419f9cc`, rewriting
  `test_kubernetes_cni_check_reflects_real_path_state`'s negative branch to
  use a real empty-PATH temp directory, matching `check_git_cli`'s own
  pattern) is what actually shipped in PR #51 — read the diff directly, not
  taken on the queue entry's word.
- Evidence independently re-verified this session:
  1. Read the full diff directly (`git diff 9cbc1b3..b0f8184 --stat` and the
     changed files in full): `apps/api/app/services/environment_preflight.py`
     (290 lines — every `check_*` function performs a genuine probe:
     `shutil.which`+real subprocess for git/kubectl, a real
     `/proc/driver/nvidia/version` filesystem check for GPU, a real TCP
     connect via `app.config.docker_local_stack.check_tcp_reachable` for the
     LiteLLM gateway, real AWS-credential-env-var presence for Bedrock that
     never escalates to `AVAILABLE` from presence alone), `scripts/
     release_scope_lock.py` + `spec/release-scope.yaml`, `scripts/
     generate_compatibility_lock.py` + `spec/compatibility-lock.json`,
     `docs/operations/budget-forecast.md`, and `tests/tasks/test_ftf_h002.py`
     (15 checks). No unrelated file touched.
  2. CI verified for real, on the actual PR head, not self-reported: `pull_request_read
     get_check_runs` on PR #51 reports both `gate` (run
     35168539940, job 105034980267) and `docker-local-stack-integration`
     (job 105034980058) `completed`/`success`, tied to PR #51's actual head
     `c6985e064052a64b031b015e28be195770897d28` (confirmed via `pull_request_read
     get` — `head.sha` matches). `git diff c6985e0 b0f8184 --stat` is empty
     for every file except the merge itself resolving cleanly — the merge
     commit on `main` carries the CI-verified head's tree unchanged.
  3. Set up the real toolchain (`uv sync`, `uv pip install pyyaml` — CI
     installs it via a separate `pip install pyyaml --quiet` step ahead of
     `uv sync`, confirmed in `.github/workflows/ci.yml`; `pnpm install
     --frozen-lockfile` — neither present in the fresh checkout) and re-ran
     every command for real: `python3 scripts/validate_spec.py --strict` →
     clean; `python3 scripts/verify_task.py --task FTF-H002 --stage task` →
     `PASS` against evidence commit `40b61d2` (this task's own two
     `verification_commands`, both genuinely re-run). `tests/tasks/
     test_ftf_h002.py` → all 15 checks pass directly.
  4. Ran the full regression set clean on the merged tree: `uv run ruff
     check .` (clean, 29 source files), `uv run mypy .` (clean), `uv run
     pytest tests/unit` (3 passed); after `pnpm install --frozen-lockfile`,
     all 9 task negative-test-proof scripts (`test_ftf_0001_monorepo_layout.py`
     through `test_ftf_r001.py`, plus `test_ftf_h002.py`) each print their own
     `all checks passed` line — the FTF-0002/0003/H001 failures seen before
     installing `pnpm`'s dependencies were confirmed to be the same mundane
     missing-`node_modules` cause every prior certification shift has hit in
     a fresh checkout, not a regression from this change.
  5. `product_truth_check.py --fail-on-unwired` → `0/0 product-true`,
     confirmed legitimate per CERTIFY step 4: `docs/spec-matrix.yaml`'s
     FTF-H002 entry has `proving_symbol: ''` — this is a preflight/
     release-scope-freeze layer with no live product-runtime caller yet,
     same disclosed rationale as FTF-0002/0003/0004/0005/0006/H001/R001.
  6. `verify_release.py` run live on the merged tree, before this
     certification's own spec-matrix.yaml edit: `8/300 required task(s)
     have live-passing, CERTIFIED evidence` / `FAILED (1): ['FTF-H002']` /
     `NOT READY` — FTF-H002 itself correctly listed as `FAILED` because its
     `certification_state` was still `PROVISIONAL` at that point, even
     though its own test genuinely passes live — proving the release gate
     requires both live-passing evidence and a real `CERTIFIED` state, not
     just one or the other; no inflation.
  7. **Real fault injection against the tracked module, targeting a
     different code path than the producer's own fault injection (which
     targeted `PreflightResult`'s owner/next_action guard):** replaced
     `check_managed_provider_bedrock`'s credential-present branch so it
     reports `AVAILABLE` directly from AWS-credential-env-var presence alone
     (removing the "unvalidated account/region/model access" rejection) —
     re-ran `tests/tasks/test_ftf_h002.py`: exactly 1 failure, on
     `test_bedrock_check_never_claims_available_from_credential_presence_alone`'s
     assertion that credential presence alone must not yield `AVAILABLE`.
     Restored the file byte-identical from a pre-edit copy (`diff` confirmed
     empty), re-ran clean (all 15 checks pass again) — confirms the test
     suite is load-bearing against a real, independently chosen regression,
     not merely self-consistent with the producer's own fault injection.
  8. Pulled the real contract via `python tools/r1_handoff.py packet
     FTF-H002 --plan FINE_TUNING_FACTORY_RELEASE_1.md` and checked the
     delivered work against its actual acceptance/negative_acceptance
     clauses: acceptance clause 1 ("unknown prices, unavailable managed
     capabilities, stale LiteLLM routes or absent runtime integration are
     explicit external blockers") is met by `check_managed_provider_bedrock`
     (never `AVAILABLE` from credential presence alone, reconfirmed by item
     7's fault injection), `check_litellm_gateway` (a closed/stale socket is
     `BLOCKED_EXTERNAL`, reconfirmed by re-running
     `test_litellm_gateway_check_uses_a_real_tcp_probe` against a real bound
     socket immediately closed), and `check_coding_provider_billing`
     (always `BLOCKED_EXTERNAL`, no reachable billing API in this sandbox).
     Negative_acceptance ("Do not invent a model ID/rate ... implement
     preflight independently of final qualification to avoid a dependency
     cycle") is met: `docs/operations/budget-forecast.md` contains no
     `$<number>/hour|token|task` pattern (checked directly, not just via the
     test's own regex) and discloses `BLOCKED_EXTERNAL`; `check_litellm_gateway`
     reads `SOVRA_LITELLM_GATEWAY_URL` directly from the environment (line
     164-165 of `environment_preflight.py`), not through FTF-0005's
     `Settings.from_env()`, confirmed by reading the code directly rather
     than trusting the docstring's own claim.
  9. Confirmed `spec/release-scope.yaml` (the real committed artifact, not
     a fixture) validates with zero errors against
     `scripts/release_scope_lock.py`'s whole-set validator, and that the
     validator genuinely rejects a removed CAP ID, a downgraded `required:
     false`, and a cut `implementation_tasks` set (all three re-run directly
     as part of `tests/tasks/test_ftf_h002.py`, not assumed from the queue
     entry's description).
- Conclusion: the L1 evidence holds up under independent re-verification —
  CI genuinely completed green on the real PR head, the merge commit is
  tree-identical to the CI-verified head, the real toolchain installed and
  both verification commands genuinely re-run, a fresh fault injection
  (distinct from the producer's own) confirms the Bedrock
  never-AVAILABLE-from-credentials-alone rejection is code-enforced rather
  than fixture-assumed, `product_truth_check.py`'s 0/0 and
  `verify_release.py`'s `8/300`/`NOT READY` (pre-certification) both
  confirmed legitimate and non-inflated, and the delivered work matches the
  real task-packet contract's acceptance/negative_acceptance clauses item by
  item. No open defect. Certified L2-CERTIFIED.

## FTF-H028 — Trusted CI and exact-revision release evidence gate
- Criterion ID: FTF-H028
- PR: https://github.com/SJZLab/Sovra/pull/55 (`claude/wizardly-fermat-s54o5t`
  → `main`). Merged `2026-09-17` by `samerzakaria` (human) as merge commit
  `1de330d7cbf71ba36ad8a65565eae1645fbd1929` (parents `920d6ad` and the PR's
  actual final head `2040620`; `git diff 2040620..1de330d --stat` is empty —
  the merge commit's tree is identical to the CI-verified head, a clean
  fast-forward-shaped merge, no conflict resolution to re-check).
- Evidence commit: `d502c89a1733c0dbff054e07e556f1a74d244c3b` (implementation;
  `183321c` is the same-PR hash-fixup commit; `evidence_hash` recorded in
  `docs/spec-matrix.yaml` stays the implementation commit, matching the
  existing convention for every prior FTF-H00X/FTF-R00X ledger entry).
- Validated-by: session https://claude.ai/code/session_01W88XoZiR5AQjN9kiCQfCbB
  (session-ID comparison performed and recorded before this entry: all four
  commits on PR #55 — `d502c89`, `183321c`, `813ede7`, `2040620` — carry
  `Claude-Session: .../session_01D4UBNWAuJwWHKpfHwSuV7U`; the merge commit
  `1de330d` carries none (a human GitHub merge). This session's ID matches
  neither — independent, not self-certification).
- Date: 2026-09-17
- Evidence independently re-verified this session:
  1. Read the full diff directly (`git diff origin/main...813ede7 --stat` and
     every changed file in full): `scripts/release_evidence_gate.py` (new,
     528 lines — five real, fail-closed checks: `check_release_evidence_report`,
     `check_branch_protection`, `check_release_manifest_signature`,
     `check_required_workflows_have_no_forbidden_patterns`,
     `check_no_untrusted_pull_request_target_checkout`), `scripts/verify_release.py`
     (wired behind an opt-in `--include-evidence-gate` flag — confirmed the
     existing no-flag call path is byte-unchanged in behavior),
     `.github/workflows/10-release-qualification.yml` (new, `workflow_dispatch`-only),
     `.github/CODEOWNERS` (new, assigns the real repository admin
     `samerzakaria` to every protected gate path), `tests/tasks/test_ftf_h028.py`
     (698 lines, 33 checks). No already-certified task's own implementation
     file was touched.
  2. CI verified for real, on the actual PR head, not self-reported:
     `pull_request_read get_check_runs` on PR #55 reports both `gate`
     (run 35176632687, job 105059674271) and `docker-local-stack-integration`
     (job 105059674022) `completed`/`success`, tied to PR #55's actual head
     `204062058bbdc19f269480df236190d17e3648f2` (confirmed via
     `pull_request_read get` — `head.sha` matches; the check-runs query was
     re-run after the PR closed/merged and still returned the same two
     green runs against that exact SHA, not a stale in-progress snapshot —
     both jobs were genuinely `in_progress` at the start of this review and
     were re-polled to `completed`/`success` before this certification).
  3. Set up the real toolchain (`pip install pyyaml` — matching CI's own
     separate `pip install pyyaml --quiet` step ahead of `uv sync`, since
     the task test scripts run under system `python3`, not `uv run`;
     `uv sync`; `pnpm install --frozen-lockfile` — none present in the
     fresh checkout) and re-ran every command for real: `python3
     tests/tasks/test_ftf_h028.py` → all 33 checks pass directly; `python3
     scripts/validate_spec.py --strict` → clean; `python3 scripts/verify_task.py
     --task FTF-H028 --stage task` → `PASS` against evidence commit
     `d502c89` (this task's own two `verification_commands`, both genuinely
     re-run, not re-read as trusted text).
  4. Ran the full regression set clean on the merged tree: `uv run ruff
     check .` / `ruff format --check .` / `mypy .` clean (31 source files);
     `pnpm run lint`/`format:check`/`typecheck` clean; all 10 task
     negative-test-proof scripts (`test_ftf_0001_monorepo_layout.py` through
     `test_ftf_h002.py`, now joined by `test_ftf_h028.py`) each print their
     own `all checks passed` line — the FTF-0002/0003/H001 failures seen
     before installing `pnpm`'s dependencies were confirmed to be the same
     mundane missing-`node_modules` cause every prior certification shift
     has hit in a fresh checkout, not a regression from this change.
  5. `product_truth_check.py --fail-on-unwired` → `0/0 product-true`,
     confirmed legitimate per CERTIFY step 4: `docs/spec-matrix.yaml`'s
     FTF-H028 entry has `proving_symbol: ''` — this is a CI/gate-tooling
     layer (the release-evidence aggregator itself), not a live
     product-runtime path, same disclosed rationale as every prior
     FTF-H00X/FTF-R00X task.
  6. `verify_release.py` run live on the real committed tree, before this
     certification's own `spec-matrix.yaml` edit (confirmed via `git
     stash`/`git stash pop` around the check, not by memory): `9/300
     required task(s) have live-passing, CERTIFIED evidence` / `FAILED (1):
     ['FTF-H028']` / `NOT READY` — FTF-H028 correctly listed `FAILED`
     because its `certification_state` was still `PROVISIONAL` at that
     point even though its own test genuinely passes live, proving the
     release gate requires both live-passing evidence and a real
     `CERTIFIED` state, not just one or the other; no inflation. After this
     certification's edit, the same command reports `10/300`.
  7. **Real fault injection against the tracked module, targeting a
     different code path than the producer's own two fault injections
     (which targeted `FORBIDDEN_WORKFLOW_PATTERNS` and the stale-commit
     branch of `check_release_evidence_report`):** temporarily changed
     `check_branch_protection`'s `403`/`404` branch to return
     `GateStatus.PASS` instead of `GateStatus.BLOCKED_EXTERNAL` (simulating
     a real defect class this gate exists to prevent — a permission denial
     silently treated as a passing check). Re-ran `tests/tasks/test_ftf_h028.py`
     directly: exactly 1 failure — `FAIL [FTF-H028] a 403 (permission
     denial) reports BLOCKED_EXTERNAL, never a fabricated PASS or FAIL`.
     Restored the file byte-identical from a pre-edit copy (`diff` confirmed
     empty), re-ran clean (all 33 checks pass again) — confirms the test
     suite is load-bearing against a real, independently chosen regression,
     not merely self-consistent with the producer's own fault injections.
  8. **Live branch-protection finding reproduced independently, not taken
     on the PR's word:** issued a fresh, separate GET (not reusing
     `release_evidence_gate.py`'s own code path — a standalone
     `urllib.request` call written for this review) against
     `https://api.github.com/repos/SJZLab/Sovra/branches/main/protection`
     using this session's own `GITHUB_TOKEN` — got a real HTTP 403
     `{"message":"Resource not accessible by integration", ...}`, matching
     the module's docstring and the PR's own claim exactly.
  9. Confirmed `check_release_evidence_report`'s stale-SHA rejection
     actually distinguishes a report for the wrong commit from a missing
     one (per the prior queue entry's own open item (b)): read
     `test_release_evidence_report_stale_head_sha_fails` directly — it
     constructs a check-run whose `head_sha` differs from the requested
     release SHA and asserts `GateStatus.FAIL` with `"stale"` in the detail,
     a separate code path and separate test from
     `test_release_evidence_report_missing_check_fails`'s `"missing"` case.
  10. Confirmed no check silently treats `BLOCKED_EXTERNAL` as equivalent to
      `PASS` anywhere in the composition (per the prior queue entry's own
      open item (c)): read `overall_status()` directly — `PASS` only when
      every result is `PASS`, `FAIL` if any result is `FAIL` (checked before
      `BLOCKED_EXTERNAL`), `BLOCKED_EXTERNAL` otherwise; `test_overall_status_is_blocked_when_any_check_is_blocked`,
      `test_overall_status_is_fail_when_any_check_fails` and
      `test_overall_status_pass_only_when_every_check_passes` all reconfirmed
      passing directly.
  11. Pulled the real contract via `python tools/r1_handoff.py packet
      FTF-H028 --plan FINE_TUNING_FACTORY_RELEASE_1.md` and checked the
      delivered work against its actual acceptance/negative_acceptance
      clauses: acceptance ("Missing/skipped/stale/matrix-omitted/unsigned/tampered
      reports and untrusted PR workflow cannot pass; verify actual branch
      protection through real repository settings") is met by the five real
      checks above, each independently re-verified; negative_acceptance
      ("No required continue-on-error or hidden retry success; invalid
      release examples are negative tests, not weakened expected results")
      is met by `check_required_workflows_have_no_forbidden_patterns`
      (confirmed the real `.github/workflows/*.yml` files carry none of the
      four forbidden patterns) and the fault-injection evidence above
      showing the negative cases are genuinely enforced, not fixture-only.
- Conclusion: the L1 evidence holds up under independent re-verification —
  CI genuinely completed green on the real PR head, the merge commit is
  tree-identical to the CI-verified head, the real toolchain installed and
  both verification commands genuinely re-run, a fresh fault injection
  (distinct from the producer's own two) confirms the branch-protection
  403-is-never-PASS guarantee is code-enforced rather than fixture-assumed,
  the live 403 branch-protection finding was independently reproduced
  (not taken on the PR's word), the two open items from the prior
  submission cycle's own review note (stale-SHA discrimination and
  BLOCKED_EXTERNAL-never-equals-PASS composition) were both directly
  confirmed, `product_truth_check.py`'s `0/0` and `verify_release.py`'s
  `9/300`/`FAILED (1)`/`NOT READY` (pre-certification) both confirmed
  legitimate and non-inflated, and the delivered work matches the real
  task-packet contract's acceptance/negative_acceptance clauses item by
  item. No open defect. Certified L2-CERTIFIED.

## FTF-H034 — Safe parallel scheduler and task/evidence ownership
- Criterion ID: FTF-H034
- PR: https://github.com/SJZLab/Sovra/pull/59 (`claude/wizardly-fermat-twhvs6`
  → `main`). Merged `2026-09-17` by `samerzakaria` (human) as merge commit
  `adba85013f58712415e871ab258f4262bf1ae567` (parents `462de00` and the PR's
  actual final head `37d7bcd`; `git diff 37d7bcd..adba850 --stat` is empty —
  the merge commit's tree is identical to the CI-verified head, a clean
  merge, no conflict resolution to re-check). A separate doc-only PR #60
  (bookkeeping: recorded the PR #59 merge in QUEUE.md/PROGRESS.md while
  still PROVISIONAL) merged afterward as `7a16b99`; this certification is
  based on the real code tree, unaffected either way.
- Evidence commit: `7109100c9bfaff995a94203720e9161e095cb7bf` (implementation;
  `7c70b77` is the same-PR hash-fixup/PROVISIONAL-bookkeeping commit;
  `evidence_hash` recorded in `docs/spec-matrix.yaml` stays the
  implementation commit, matching the existing convention for every prior
  FTF-H00X/FTF-R00X ledger entry).
- Validated-by: session https://claude.ai/code/session_01TomYQSLSDkXcNVZzNsZzA3
  (session-ID comparison performed and recorded before this entry: all
  three commits on PR #59 — `7109100`, `7c70b77`, `37d7bcd` — carry
  `Claude-Session: .../session_01Q9LvDK2pPXKCw9Q1VSSjpF`; the merge commits
  `adba850`/`7a16b99` carry none (human GitHub merges). This session's ID
  matches neither — independent, not self-certification).
- Date: 2026-09-17
- Evidence independently re-verified this session:
  1. Read the full diff directly (`git diff origin/main...origin/claude/wizardly-fermat-twhvs6`,
     every changed file): `scripts/task_claim.py` (new, 346 lines —
     `TaskClaimRegistry`: DAG-readiness-gated claims, expiring TTL claims,
     write-set/named-lock exclusivity, bounded concurrency (default 4, hard
     ceiling 6), a separate GPU quota, deterministic merge order,
     stale-verification invalidation, and a self-certification guard) and
     `tests/tasks/test_ftf_h034.py` (new, 542 lines, 17 checks). Confirmed
     `spec/tasks.json`'s and `docs/implementation/TRACKER.json`'s diffs
     (`sync_certified_status()`'s effect) are limited to exactly the
     `status`/`evidence` fields of the 10 already-independently-certified
     task IDs (counted directly: `grep -c '"status": "CERTIFIED"'` == 10 in
     both files; no other field touched) — no already-certified task's own
     implementation file was touched.
  2. CI verified for real, on the actual PR head, not self-reported:
     `pull_request_read get_check_runs` on PR #59 reports both `gate`
     (run 35184262911, job 105082825586) and `docker-local-stack-integration`
     (job 105082825703) `completed`/`success`, tied to PR #59's actual head
     `37d7bcdab9ea7cbcd44de89129a66467d9327fdf` (confirmed via `pull_request_read
     get` — `head.sha` matches). Subscribed to PR activity and re-polled
     after both jobs were still `in_progress` at first check; both were
     genuinely green before this certification proceeded.
  3. Set up the real toolchain (system `python3` has `pyyaml` preinstalled,
     matching CI's own separate `pip install pyyaml --quiet` step ahead of
     `uv sync`, since the task test scripts run under system `python3`, not
     `uv run`; `uv sync`; `pnpm install --frozen-lockfile` — no
     `node_modules` in the fresh checkout) and re-ran every command for
     real: `python3 tests/tasks/test_ftf_h034.py` → all 17 checks pass
     directly; `python3 scripts/validate_spec.py --strict` → clean;
     `python3 scripts/verify_task.py --task FTF-H034 --stage task` → `PASS`
     against evidence commit `7109100` (this task's own two
     `verification_commands`, both genuinely re-run, not re-read as trusted
     text).
  4. Ran the full regression set clean on the merged tree: `uv run ruff
     check .` / `ruff format --check .` clean; `uv run mypy .` clean (33
     source files); `pnpm run lint`/`format:check`/`typecheck` clean
     (after `pnpm install`); all 11 task negative-test-proof scripts
     (`test_ftf_0001_monorepo_layout.py` through `test_ftf_h028.py`, now
     joined by `test_ftf_h034.py`) each print their own `all checks passed`
     line — the FTF-0002/0003/H001 failures seen before installing pnpm's
     dependencies were confirmed to be the same mundane missing-`node_modules`
     cause every prior certification shift has hit in a fresh checkout, not
     a regression from this change. `uv run pytest tests/unit` (3 passed);
     `pnpm exec vitest run` (2 passed).
  5. `product_truth_check.py --fail-on-unwired` → `0/0 product-true`,
     confirmed legitimate per CERTIFY step 4: `docs/spec-matrix.yaml`'s
     FTF-H034 entry has `proving_symbol: ''` — this is scheduler/tracking
     tooling, not a live product-runtime path, same disclosed rationale as
     every prior FTF-H00X/FTF-R00X task.
  6. `verify_release.py` run live on the real committed tree, before this
     certification's own `spec-matrix.yaml` edit: `10/300 required task(s)
     have live-passing, CERTIFIED evidence` / `FAILED (1): ['FTF-H034']` /
     `NOT READY` — FTF-H034 correctly listed `FAILED` because its
     `certification_state` was still `PROVISIONAL` at that point even
     though its own test genuinely passes live; no inflation. After this
     certification's edit, the same command reports `11/300`.
  7. **Real fault injection against the tracked module, targeting a
     different code path than the producer's own committed fault injection
     (which targeted the `certifier == claimant` self-certification guard
     in `complete()`):** temporarily removed the
     `_dependencies_ready_locked` guard block from `claim()` (the
     `DEPENDENCY_NOT_READY` check). Re-ran `tests/tasks/test_ftf_h034.py`
     directly: exactly 1 failure — `FAIL [FTF-H034] a task depending on a
     not-yet-CERTIFIED task is denied a claim`. Restored the file
     byte-identical from a pre-edit copy (`diff` confirmed empty), re-ran
     clean (all 17 checks pass again) — confirms the test suite is
     load-bearing against a real, independently chosen regression, not
     merely self-consistent with the producer's own fault injection.
     Separately reproduced the producer's own self-certification-guard
     fault injection too (same removal the committed
     `test_self_certification_guard_is_load_bearing_real_fault_injection`
     performs): confirmed a fresh, unguarded `complete()` call lets a
     self-certified completion through, restored byte-identical.
  8. Delivered work checked against FTF-H034's real acceptance/
     negative_acceptance clauses via `tools/r1_handoff.py packet FTF-H034
     --plan FINE_TUNING_FACTORY_RELEASE_1.md` and a direct read of
     `docs/implementation/task-packets/FTF-H034.md`: overlapping claims
     (`WRITE_SET_CONFLICT`/`LOCK_CONFLICT`), dependent starts
     (`DEPENDENCY_NOT_READY`), and stale completion (`CLAIM_EXPIRED`) are
     all denied with dedicated tests; the "actual safe parallel wave" is
     proven with a real 8-OS-thread `threading.Barrier` race
     (`test_real_parallel_wave_exactly_one_claim_wins` — exactly 1 thread
     wins, the other 7 are denied `TASK_ALREADY_CLAIMED` specifically, and
     `active_count()` is exactly 1 afterward); `merge_order()` is proven
     deterministic regardless of completion arrival order; stale-CI
     invalidation on an overlapping later completion is proven
     (`test_completion_invalidates_overlapping_earlier_verification`). The
     negative_acceptance clause ("if runner lacks real subagent capability,
     record orchestration blocker, never fabricate workers") is honestly
     disclosed in the module's own docstring and in the PR body — this
     sandbox has no live multi-agent dispatch harness to hand a task to a
     second, truly separate worker process, so the parallel-wave proof is
     built at real-OS-thread contention against the registry's own
     locking, not a fabricated multi-worker claim. No full-release claim is
     made anywhere (`verify_release.py` stays honestly `NOT READY`).
  9. **This certification's own bookkeeping edit surfaced a real,
     self-consistent gap, caught by the delivered test suite itself, not
     hidden:** after setting `docs/spec-matrix.yaml`'s FTF-H034
     `certification_state` to `CERTIFIED` (this entry's own action),
     re-running `tests/tasks/test_ftf_h034.py` failed 2/17 —
     `test_real_committed_tracker_and_tasks_reflect_actual_certified_set`
     correctly detected that `spec/tasks.json`/`TRACKER.json` still showed
     FTF-H034 itself as `NOT_STARTED`, since `sync_certified_status()` had
     only been run once, against the pre-certification state, as part of
     the PR. Re-ran `sync_certified_status()` against the real repo files
     post-certification — the diff is limited to exactly FTF-H034's own
     `status`/`evidence` fields in both files (confirmed via `git diff`,
     nothing else touched), and `tests/tasks/test_ftf_h034.py` returns to
     17/17. This is the tool doing exactly what it was built to do, not a
     defect in the delivered code — recorded here since it changed the
     committed tree as part of this certification.
- Conclusion: the L1 evidence holds up under independent re-verification —
  CI genuinely completed green on the real PR head, the merge commit is
  tree-identical to the CI-verified head, the real toolchain installed and
  both verification commands genuinely re-run, a fresh fault injection
  (distinct from the producer's own) confirms the DAG-readiness guard is
  code-enforced rather than fixture-assumed, the `sync_certified_status()`
  diff to `spec/tasks.json`/`TRACKER.json` is confirmed limited to exactly
  the 10 already-certified tasks' `status`/`evidence` fields (plus, after
  this certification's own edit, FTF-H034's own, re-run and reconfirmed
  minimal) and nothing else, `product_truth_check.py`'s `0/0` and
  `verify_release.py`'s `10/300`/`FAILED (1)`/`NOT READY`
  (pre-certification) → `11/300`/`NOT READY` (post-certification) both
  confirmed legitimate and non-inflated, and the delivered work matches
  the real task-packet contract's acceptance/negative_acceptance clauses
  item by item. No open defect. Certified L2-CERTIFIED.

## FTF-1810 — ReleaseQualificationWorkflow
- Criterion ID: FTF-1810
- PR: https://github.com/SJZLab/Sovra/pull/63
- Merge commit: 84b71cfc254ea1ff90441a11692f6d77260d7eb7 (PR head
  e4ac93bfca3346b87c43ff2e0ca721cfa4f8758c; `git diff` between the two
  confirmed empty — the merge commit is tree-identical to the CI-verified
  head)
- Validated-by: session https://claude.ai/code/session_01UbQN1CRhc9SQDCm3VoY6Vf
  (session-ID comparison performed and recorded before this entry: all
  three commits on PR #63 carry `Claude-Session:
  .../session_0154rGkMQgbT7Q5x8dgGJVep`; this session's ID does not
  match — independent, not self-certification)
- Date: 2026-09-17
- Evidence independently re-verified this session, not taken on the PR's
  own word:
  1. Read the full PR diff (10 files, 1807 insertions/11 deletions):
     `docs/operations/releasequalificationworkflow.py` (new module),
     `tests/tasks/test_ftf_1810.py` (new, 16 checks),
     `docs/implementation/task-packets/FTF-1810.md` (new task packet),
     CI wiring additions in `.github/workflows/ci.yml` (1 step) and
     `.github/workflows/10-release-qualification.yml` (2 steps, Tier 3
     `workflow_dispatch`-only, never triggered by an ordinary PR),
     `docs/spec-matrix.yaml` and `spec/verification-map.json` entries, and
     tracking-doc bookkeeping (`PROGRESS.md`/`QUEUE.md`/
     `docs/certifications/queue.md`). Nothing beyond FTF-1810's own
     declared scope; no existing certified task's implementation file
     touched.
  2. CI verified for real, on the actual PR head: this session subscribed
     to PR #63's activity while both `gate` and
     `docker-local-stack-integration` were still `in_progress`, waited for
     the real completion event rather than certifying against an
     unfinished run, then confirmed via `pull_request_read get_check_runs`
     that both completed `success` on SHA `e4ac93b` (run 35192517673) —
     not a stale/self-reported claim.
  3. Own re-run of the delivered test suite: `python3 tests/tasks/
     test_ftf_1810.py` — 16/16 pass, matching the PR's claim.
  4. **Real fault injection performed independently, distinct from both
     of the producer's own** (the committed `skipped`-check removal and
     the ad hoc `stale`-check removal recorded in the queue entry):
     removed the `incomplete`-evidence check block (the `missing_fields`
     guard for `evidence_id`/`commit`/`exit_code`) from
     `docs/operations/releasequalificationworkflow.py`'s
     `evaluate_scenario_evidence` — re-ran the test suite: exactly 3
     checks failed for real (`a scenario evidence entry missing
     'evidence_id'/'commit'/'exit_code' is FAIL (incomplete)`), all other
     13 checks stayed green; restored the file byte-identical from a
     pre-edit copy (`diff` confirmed empty), re-ran clean (16/16).
  5. **Not fabricated, proven against the real repo:** ran
     `python3 docs/operations/releasequalificationworkflow.py` directly
     (not just re-reading the PR's printed claim) — genuinely reports
     `overall_status: FAIL`, `scenario_count: 45` (43 scenarios + 2
     bindings), every one of the 43 required scenarios `missing`, and
     both `release_image_binding`/`release_profile_binding`
     `BLOCKED_EXTERNAL` — the honest current-repo state, matching the PR's
     disclosure exactly.
  6. `product_truth_check.py --fail-on-unwired` — `0/0 product-true`,
     confirmed legitimate by reading `docs/spec-matrix.yaml`'s FTF-1810
     entry directly: `proving_symbol: ''` (no live-wired caller claimed),
     so `0/0` is the correct result, not an unearned pass.
  7. Full regression set re-run with the actual pinned toolchain (not a
     stray system-wide one): `uv sync` then `uv run ruff check .` /
     `ruff format --check .` / `mypy .` clean; `pnpm install
     --frozen-lockfile` then `pnpm run lint`/`typecheck`/`format:check`
     and `pnpm exec vitest run` (2 passed) clean; `uv run pytest
     tests/unit` (3 passed) clean; all 12 existing task
     negative-test-proof scripts (`test_ftf_0001_monorepo_layout.py`
     through `test_ftf_h034.py`) pass unmodified — no regression to
     already-certified evidence. Note: an initial pass using this
     sandbox's stray system-wide `ruff` (0.15.8, not the project's pinned
     `ruff>=0.6.9,<0.7`) surfaced 5 `UP042` findings in
     `scripts/release_evidence_gate.py` and `apps/api/app/config/
     configuration_system.py` — confirmed these pre-exist on `main` before
     this PR (`git checkout <base-sha> -- .` reproduced the same 5
     findings) and disappear entirely under the actual pinned `uv run
     ruff` (0.6.9) — a sandbox-version artifact, not a real regression,
     and not this PR's to fix regardless.
  8. `python3 scripts/validate_spec.py --strict` clean;
     `python3 scripts/verify_task.py --task FTF-1810 --stage task` ->
     `PASS` against evidence commit `e7ddf75f0bcdeaf99f1b92ee5b1bc52fc74b650a`;
     `python3 scripts/verify_release.py` confirmed non-inflated:
     `11/300`/`FAILED (1): ['FTF-1810']`/`NOT READY` before this
     certification's `docs/spec-matrix.yaml` edit, `12/300`/`NOT READY`
     after (12/300 is correct — FTF-1810 alone was the remaining
     PROVISIONAL item).
  9. Confirmed FTF-1810's stated dependencies (FTF-H001, FTF-H028) are
     both genuinely `certification_state: CERTIFIED` in the real, current
     `docs/spec-matrix.yaml` — dependency-readiness was not assumed from
     the PR body alone.
  10. Delivered work checked against FTF-1810's real acceptance/
      negative_acceptance clauses via `tools/r1_handoff.py packet
      FTF-1810 --plan FINE_TUNING_FACTORY_RELEASE_1.md`: "run the
      authoritative Q01-Q43 aggregator on exact release; bind result to
      current scope/hash/image/profile" — met by
      `resolve_release_binding`/`run_release_qualification_workflow`
      binding to the real `scope_digest`/`commit_sha` and honestly
      `BLOCKED_EXTERNAL` image/profile fields; "missing/skipped/stale/
      unsigned/tampered/incomplete scenario cannot authorize release" —
      met and proven by fixture plus this session's own independent fault
      injection (finding 4 above) against the `incomplete` branch
      specifically, which the producer's own two fault injections did not
      target.
  11. After setting `certification_state: CERTIFIED`, re-ran
      `scripts.task_claim.sync_certified_status()` against the real repo
      files (the same tool FTF-H034 delivered and FTF-H034's own
      certification used) — diff confirmed limited to exactly FTF-1810's
      own `status`/`evidence` fields in `spec/tasks.json` and
      `docs/implementation/TRACKER.json`, nothing else touched.
- Conclusion: the L1 evidence holds up under independent re-verification —
  CI genuinely completed green on the real PR head, the merge commit is
  tree-identical to the CI-verified head, the real pinned toolchain
  installed and both verification commands genuinely re-run, a fresh
  fault injection (distinct from either of the producer's own, targeting
  the one named rejection reason neither of them exercised) confirms the
  fail-closed evidence gate is code-enforced rather than fixture-assumed,
  the real aggregator's `NOT READY`/all-scenarios-missing/both-bindings-
  `BLOCKED_EXTERNAL` result was independently reproduced rather than
  trusted from the PR body, `product_truth_check.py`'s `0/0` and
  `verify_release.py`'s `11/300`→`12/300` non-inflated transition are both
  confirmed legitimate, and the delivered work matches the real
  task-packet contract's acceptance/negative_acceptance clauses item by
  item. No open defect. Certified L2-CERTIFIED.

## FTF-R014 — Bind selected coding executor to the embedded Release 1 execution controls
- Criterion ID: FTF-R014
- PR: https://github.com/SJZLab/Sovra/pull/68
- Merge commit: 222c665843da206929df50a3857f659a10ec5dd6
- Validated-by: session https://claude.ai/code/session_01HCxXhRFSdHhWo7aT21GWmH
  (session-ID comparison performed and recorded before this entry: all four
  PR #68 commits — fc18229, 5592d07, 72acba7, cc9b7e2 — carry
  Claude-Session session_01WErQ4nvBQBFNgtrT5v2Tau; this reviewing session's
  ID does not match — independent, not self-certification)
- Date: 2026-09-17
- Evidence:
  1. CI reconfirmed green for real (not a self-reported claim) on the PR's
     actual final head via `pull_request_read.get_check_runs`: `gate` and
     `docker-local-stack-integration`, both `completed`/`success`, GitHub
     Actions run 35203455799, head `cc9b7e20d8e530eaf2208d4756f5c3935e1774e9`.
     `mergeable_state` was `clean` before merge.
  2. Independent re-run in a fresh git worktree at the PR's actual head,
     with full (unshallowed) history: `python3
     tests/implementation/test_r1_execution_binding.py` — 24/24 checks
     pass. `ruff check .`/`ruff format --check .`/`mypy .` clean; `pnpm run
     lint`/`format:check`/`typecheck` clean; `uv run pytest tests/unit` (3
     passed) and `pnpm exec vitest run` (2 passed) clean, 0 skipped either
     side; all 12 existing task negative-test-proof scripts
     (`test_ftf_0001_monorepo_layout.py` through `test_ftf_1810.py`) pass
     unmodified — no regression to already-certified evidence.
  3. Own real fault injection against the tracked module on disk (reproduced
     independently, not just trusting the producer's own probe): removed
     the `SELF_CERTIFICATION_DENIED` guard block from
     `tools/implementation/executor_contract.py`, ran a fresh subprocess
     that imports the edited file and validates a self-certified
     `R1_EMBEDDED` receipt for task `FTF-R014` with live-computed matching
     digests — it wrongly returned `PASS`; restored the file byte-identical
     (`diff` confirmed empty against the pre-edit copy), re-ran the same
     probe — correctly `FAIL`ed again with `SELF_CERTIFICATION_DENIED`.
     (One transcription slip using an unrelated `task_id` that doesn't
     match the schema's `^FTF-[A-Z0-9]+$` pattern briefly produced a
     false `FAIL` for an unrelated reason — schema violation, not the
     guard; corrected and reproduced clean once `task_id="FTF-R014"` was
     used, confirming the schema's `task_id` pattern check is itself real
     and independent of the self-certification guard.)
  4. `python3 scripts/validate_spec.py --strict` clean;
     `python3 scripts/verify_task.py --task FTF-R014 --stage task` ->
     `PASS` against evidence commit `fc1822903741fae188b7b81767a7668b3ee9cc96`;
     `python3 scripts/verify_release.py` confirmed non-inflated:
     `12/300`/`FAILED (1): ['FTF-R014']`/`NOT READY` before this
     certification's `docs/spec-matrix.yaml` edit, `13/300`/`NOT READY`
     (no FAILED entries) after.
     `python3 scripts/product_truth_check.py --fail-on-unwired` stays
     `0/0 product-true` — legitimate, `proving_symbol` is empty
     (build/implementation tooling, no Factory production service imports
     `tools/implementation/executor_contract.py`, confirmed directly via
     `grep` across `apps/`, `workers/`, `packages/`).
  5. Delivered work checked against FTF-R014's real acceptance/
     negative_acceptance clauses via `tools/r1_handoff.py packet FTF-R014
     --plan FINE_TUNING_FACTORY_RELEASE_1.md`: all three acceptance clauses
     (file-only `R1_EMBEDDED` binding via live Section-84/source-file
     digests; `R1_EMBEDDED_PLUS_EXISTING_SKILL` preserving a real skill's
     name/version/hash without fabricating conformance in the embedded-only
     path; scoped receipts with separate producer/validator/release
     authority and no Factory runtime dependency) and both
     negative_acceptance clauses (reject missing/mismatched digest,
     fabricated external-skill conformance, self-certification, producer
     mutation of protected gates without the real CODEOWNERS release
     authority, and claimed-without-run OpenHands qualification; a missing
     optional external skill must not block `R1_EMBEDDED`) are each
     covered by a distinct, real check in
     `tests/implementation/test_r1_execution_binding.py`, independently
     confirmed by reading the test file and the module under test line by
     line, not merely trusting the PR's own description. `shared_lock_required`
     is `[]` and `shared_contracts_read` (`packages/component-contracts/
     architecture.schema.json`, `spec/deployment-profiles.yaml`) are
     untouched by this PR's diff, confirmed via `git diff --stat`.
  6. `.github/CODEOWNERS` correctly protects the two new gate files
     (`tools/implementation/executor_contract.py`,
     `spec/implementation-executor.schema.json`) under
     `@samerzakaria`, matching `RELEASE_AUTHORITY_IDENTITY =
     "owner:samerzakaria"` used in the code — read directly, not assumed.
  7. After setting `certification_state: CERTIFIED`, re-ran
     `scripts.task_claim.sync_certified_status()` against the real repo
     files — diff confirmed limited to exactly FTF-R014's own
     `status`/`evidence` fields in `spec/tasks.json` and
     `docs/implementation/TRACKER.json`, nothing else touched.
- Conclusion: the L1 evidence holds up under independent re-verification —
  CI genuinely completed green on the real PR head, the merge commit
  (`222c665`) is the one GitHub actually merged from that exact head, the
  real pinned toolchain and both verification commands genuinely re-run
  clean, a fresh fault injection (reproducing, not just trusting, the
  producer's own claim) confirms the self-certification guard is
  code-enforced rather than fixture-assumed, `product_truth_check.py`'s
  `0/0` and `verify_release.py`'s `12/300`→`13/300` non-inflated
  transition are both confirmed legitimate, and the delivered work matches
  the real task-packet contract's acceptance/negative_acceptance clauses
  item by item. No open defect. Certified L2-CERTIFIED.

## FTF-R015 — Enforce isolated concurrent coding execution and independent integration
- Criterion ID: FTF-R015
- PR: https://github.com/SJZLab/Sovra/pull/71 (fix/re-review of PR #70,
  `claude/sleepy-pasteur-c8ihmb` → `main`)
- Merge commit: 8f4a6fd25df3439dac166e324ecf9b4e0e7d8034
- Evidence commit (verification-map/spec-matrix pointer): `519bba0ecbabcf36276df1dda3265673a7c2b702`
- Validated-by: session https://claude.ai/code/session_015y31yd94WYsU49RBaF4jXE
  (session-ID comparison performed and recorded before this entry: all
  three PR #71 commits — `1020089`, `519bba0`, `6a47f08` — carry
  `Claude-Session: .../session_01Wn6FwbAzgvmKf9AcqXKLbQ`, and all four
  original PR #70 commits carry `session_01C4Vvhh8pRi5yqZTmnFce6A`; this
  certifying session's ID matches neither — independent, not
  self-certification)
- Date: 2026-09-17
- Background: PR #70 (original FTF-R015 submission, isolation/permission/
  budget/CI-containment/merge-gate machinery) was merged to `main` by the
  owner as PROVISIONAL (merge commit `c055226`, merge ≠ certification, Law
  4). A prior independent Control-plane session
  (`session_01Wn6FwbAzgvmKf9AcqXKLbQ`) then reviewed it in CERTIFY mode and
  found one unaddressed item in FTF-R015's own resolved acceptance clause 1
  ("record actual overlap, leases, **commits**, tests and an independent
  integrated merge gate") — no field, code path, or test anywhere captured
  a task's real commit evidence, despite `executor_adapter.py` explicitly
  allowlisting `git`. Per Law 1/Law 5, that session did not certify
  (`certification_state` stayed `PROVISIONAL`) and recorded the exact gap
  and fix options in `docs/certifications/queue.md`. The same session then
  implemented the fix at the live user's direction and opened PR #71,
  re-submitting for certification by a fresh session with no overlap —
  which this entry is.
- Evidence (this session's own independent re-verification, not the
  producer's claim):
  1. Session-ID check performed first, as above — no overlap with either
     PR #70's or PR #71's commit authorship.
  2. Read the PR #71 diff directly: `tools/implementation/executor_adapter.py`
     gained one `_read_commit_sha(workspace_path)` helper (`git rev-parse
     HEAD` against the task's isolated workspace, `None` on any non-zero
     exit or subprocess error — never a fabricated hash) and one field
     (`ExecutionResult.commit_sha`); `tests/implementation/
     test_safe_parallel_executor.py` gained
     `test_real_commit_is_captured_and_propagates_through_a_wave` (32 -> 36
     checks), which drives a real `git init`/`commit --allow-empty`
     sequence through `execute_task` and cross-checks the captured SHA
     against an independently run `git rev-parse HEAD` in the same
     workspace — not read back from the module's own claim — plus a
     no-git case asserted `None` and propagation through a full
     `TaskOrchestrator.run_wave`.
  3. CI reconfirmed green for real (not self-reported) via
     `pull_request_read.get_check_runs` on PR #71's actual head
     `6a47f08e39da5212a435e9d5e935636f3df1be44`: both `gate` and
     `docker-local-stack-integration`, `completed`/`success`.
  4. Own re-run in a fresh git worktree (`origin/claude/sleepy-pasteur-c8ihmb`,
     unshallowed): all 36 `tests/implementation/test_safe_parallel_executor.py`
     checks pass; full 15-script existing regression set (`test_ftf_0001_
     monorepo_layout.py` through `test_ftf_r001.py` and
     `test_r1_execution_binding.py`) passes unmodified after `pnpm install
     --frozen-lockfile` (fresh container, no `node_modules`); `uv run ruff
     check .`/`format --check .`/`mypy .` clean; `pnpm run lint`/
     `typecheck`/`format:check` and `pnpm exec vitest run` (2 passed)
     clean; `uv run pytest tests/unit` (3 passed).
  5. Own fault injection, distinct from both the original producer's
     (protected-gate-write guard) and the prior reviewer's (`ci_failed`
     bookkeeping): mutated `_read_commit_sha` to return a fabricated
     40-hex-char SHA instead of `None` on a failed `git rev-parse` —
     re-ran the suite, exactly 1 check failed for real (`a task whose
     command never makes a commit leaves commit_sha genuinely None, not a
     fabricated value`); restored the file byte-identical (`diff`
     confirmed empty), re-ran clean (36/36) — proves the "never a
     fabricated hash" property is code-enforced, not merely claimed.
  6. `python tools/r1_handoff.py packet FTF-R015 --plan
     FINE_TUNING_FACTORY_RELEASE_1.md` re-pulled directly: acceptance
     clause 1's exact text ("...record actual overlap, leases, commits,
     tests and an independent integrated merge gate") confirmed verbatim;
     the delivered fix closes precisely the "commits" gap the prior review
     identified, with no other clause affected. Negative_acceptance
     clauses (overlapping/expired claims, nested unbounded delegation,
     producer writes to gate state, direct-tool permission bypass, stale
     branch dependencies, advancement after failed CI, no fabricated
     parallel pass) map one-to-one to `test_write_set_conflict_denied`,
     `test_expired_lease_denied`, `test_nested_delegation_denied`,
     `test_protected_gate_write_denied_without_authority`/
     `test_protected_gate_write_accepted_from_real_authority`,
     `test_permission_bypass_denied`, `test_stale_dependency_rejected`, and
     `test_advancement_after_failed_ci_denied` respectively — each
     independently re-run and confirmed passing above.
  7. `validate_spec.py --strict` clean (after `git fetch --unshallow`; a
     shallow clone falsely reports `stale_commit: FTF-0001` since the
     pre-existing certified `7ab3f6f` doesn't resolve in a shallow
     history — a local environment artifact, not a PR defect, disclosed
     rather than silently worked around); `verify_task.py --task FTF-R015
     --stage task` -> `PASS` against evidence commit `519bba0`;
     `product_truth_check.py --fail-on-unwired` legitimately `0/0`
     (`proving_symbol: ''` — build/implementation tooling, no Factory
     production service imports it, confirmed by the same rationale as
     FTF-R014/FTF-H034).
  8. Merged PR #71 (`mergeable_state: clean`) as merge commit `8f4a6fd`.
     Set `docs/spec-matrix.yaml` FTF-R015 to `certification_state:
     CERTIFIED` / `proof_state: L2-CERTIFIED` (`evidence_hash` kept at the
     real fix commit `519bba0`, matching `spec/verification-map.json`'s
     own `commit` field, same convention as every prior certified task).
     Re-ran `scripts.task_claim.sync_certified_status()` — diff confirmed
     limited to exactly FTF-R015's own `status`/`evidence` fields in
     `spec/tasks.json` and `docs/implementation/TRACKER.json`.
  9. `verify_release.py` re-run after the above, in a fully installed
     environment (`pnpm install --frozen-lockfile` run first — its absence
     had transiently made FTF-0002/0003/H001 report FAILED for the same
     missing-`node_modules` reason as item 4/7 above, not a real
     regression): `14/300` required tasks CERTIFIED with live-passing
     evidence, `FAILED (1): ['FTF-R011']` (correctly reported — FTF-R011 is
     PROVISIONAL, a separate item, not yet certified; not this entry's
     scope), non-inflated transition from the prior `13/300`.
- Conclusion: the fix genuinely closes the exact gap the prior independent
  review found — a real `git rev-parse HEAD` read, never a fabricated
  hash on failure (proven by an independent fault injection distinct from
  both prior sessions' own), cross-checked against an independently read
  HEAD rather than the module's own say-so, and shown to propagate through
  a full orchestrated wave. CI is green on the real merged head, the
  acceptance/negative_acceptance clauses are met item by item against the
  task's real resolved contract, and `verify_release.py`'s denominator
  moved non-inflated. No open defect. Certified L2-CERTIFIED.

## FTF-R011 — Define immutable connected and air-gap assertion-level release matrix
- Criterion ID: FTF-R011
- PR: https://github.com/SJZLab/Sovra/pull/72
- Merge commit: 71501cb3692ac34ee71e52ba80c424a7e021c40e
- Evidence commit (verification-map/spec-matrix pointer): `18dd2823eff6171594fb53b6adf0beb953bada82`
- Validated-by: session https://claude.ai/code/session_015y31yd94WYsU49RBaF4jXE
  (session-ID comparison performed and recorded before this entry: both PR
  #72 commits — `18dd282`, `a3d7084` — and its bookkeeping commit
  (`6ac7686`) carry `Claude-Session: .../session_018Whdzxf76ttaAQnag7YY2E`;
  this certifying session's ID does not match — independent, not
  self-certification)
- Date: 2026-09-17
- Background: this session's fixed CERTIFY-mode mandate is to sweep
  `docs/certifications/queue.md`/`docs/spec-matrix.yaml` for every
  PROVISIONAL item, not only the PR that triggered it (a
  `pull_request.opened` webhook for this same PR, #72). Found two
  PROVISIONAL items this pass: FTF-R015 (certified first, see the entry
  above) and FTF-R011 (this entry). PR #72's `mergeable_state` was `dirty`
  at review time because this session's own FTF-R015 certification
  bookkeeping (PR #73) had just advanced `main` past PR #72's base — fixed
  by merging current `main` into PR #72's branch (the only real conflict
  was in `PROGRESS.md`, both branches having prepended a new entry;
  resolved by chronological order, nothing dropped) and re-pushing; CI
  re-ran and passed on the new head before merging.
- Evidence:
  1. Session-ID check performed first, as above.
  2. Read `scripts/validate_profile_scope.py`,
     `spec/deployment-profiles.yaml`, and
     `tests/security/test_profile_scope.py` directly: the generator/
     validator enforces whole-set completeness against the real 43-
     scenario `qualification-inventory.json`, owner provenance against
     `release-scope.yaml`, a `status` enum that structurally excludes
     `N/A`, and split-scenario connected/airgap name distinctness;
     `generate_expected_checks(profile)` cannot place a connected-only
     assertion under `AIR_GAPPED` or an airgap assertion under a connected
     profile; `assert_profile_qualified`/`assert_full_product_done`
     correctly separate profile-qualified from full-product-done (a split
     scenario's airgap result never satisfies its connected-positive
     requirement).
  3. CI reconfirmed green for real via `pull_request_read.get_check_runs`
     on PR #72's actual final head (`6be8eab`, after the merge-conflict
     fix): both `gate` and `docker-local-stack-integration`,
     `completed`/`success`.
  4. Own re-run in a fresh, unshallowed git worktree at the real head:
     `tests/security/test_profile_scope.py` — all checks pass; full
     16-script existing regression set (`test_ftf_0001_monorepo_layout.py`
     through `test_safe_parallel_executor.py`, including
     `test_ftf_r001.py` specifically for the Law 15 check below) passes
     unmodified after `pnpm install --frozen-lockfile`; `ruff
     check`/`format --check`/`mypy .` clean; `pnpm run
     lint`/`typecheck`/`format:check` and `pnpm exec vitest run` (2
     passed) clean; `uv run pytest tests/unit` (3 passed).
  5. Own fault injection, distinct from the producer's own (which targeted
     the connected/airgap distinctness guard): widened `ALLOWED_STATUSES`
     to admit `"N/A"` — re-ran the suite, exactly 2 checks failed for real
     (`a required scenario's status cannot be turned into 'N/A'`, `the
     allowed result-status enum never contains 'N/A'`); restored the file
     byte-identical (`diff` confirmed empty), re-ran clean.
  6. Law 15 check independently re-verified, not just trusted from the PR
     body: `grep` across `tests/tasks/test_ftf_r001.py` and
     `scripts/architecture_lock.py` (FTF-R001's entire certified evidence
     surface) confirms zero references to `spec/deployment-profiles.yaml`;
     parsed both the pre-FTF-R011 (`git show
     c055226:spec/deployment-profiles.yaml`) and current `profiles:`
     mappings with `yaml.safe_load` and confirmed them equal — FTF-R001's
     own frozen deliverable is untouched, only `assertion_applicability`
     (FTF-R001's own two worked examples, disclosed as illustrative) was
     extended. `test_ftf_r001.py` re-run clean above. No requalification
     triggered.
  7. `python tools/r1_handoff.py packet FTF-R011 --plan
     FINE_TUNING_FACTORY_RELEASE_1.md` re-pulled directly: acceptance
     clause 1 ("Preserve all original/new capabilities and scenarios;
     separate connected positive and airgap denial assertions with owners
     and real environment evidence") and clause 2 ("Profile-qualified
     status distinct from full product; expected checks generated from
     preapproved scope before execution") both confirmed delivered.
     negative_acceptance ("Cannot turn failure into N/A, use disconnected
     denial instead of required full-product positive test, call external
     provider from air gap or drop old IDs") maps one-to-one to
     `test_na_status_is_rejected` (independently reproduced by item 5
     above), `test_airgap_denial_cannot_satisfy_connected_requirement`,
     `test_external_provider_never_expected_under_airgap`, and
     `test_dropping_an_old_id_is_rejected` respectively — all re-run and
     confirmed passing above.
  8. `validate_spec.py --strict` clean; `verify_task.py --task FTF-R011
     --stage task` -> `PASS` against evidence commit `18dd282`;
     `product_truth_check.py --fail-on-unwired` legitimately `0/0`
     (`proving_symbol: ''` — release-matrix governance/validator layer, no
     Factory production service imports it).
  9. Merged PR #72 (`mergeable_state: clean` after the merge-conflict fix)
     as merge commit `71501cb`. Set `docs/spec-matrix.yaml` FTF-R011 to
     `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`
     (`evidence_hash` kept at the real implementation commit `18dd282`,
     matching `spec/verification-map.json`'s own `commit` field). Re-ran
     `scripts.task_claim.sync_certified_status()` — diff confirmed limited
     to exactly FTF-R011's own `status`/`evidence` fields in
     `spec/tasks.json` and `docs/implementation/TRACKER.json`.
  10. `verify_release.py` re-run after the above: `15/300` required tasks
      CERTIFIED with live-passing evidence, zero FAILED, non-inflated
      transition from the prior `14/300`.
- Conclusion: the delivered validator/matrix machinery is real and
  independently reproduced — the fault injection (distinct from the
  producer's own) confirms the "never N/A" guard is code-enforced, the
  Law 15 concern raised and deferred by prior shifts is now resolved with
  direct, independent verification (not merely restated), CI is green on
  the real merged head, and the acceptance/negative_acceptance clauses are
  met item by item against the task's real resolved contract. No open
  defect. Certified L2-CERTIFIED.

## FTF-R017 — Qualify maintained internal storage and OSS dependency lifecycle
- Criterion ID: FTF-R017
- PR: https://github.com/SJZLab/Sovra/pull/75
- Merge commit: aacd74528ff39ef14b0efc42575fe987f27490d4
- Validated-by: session https://claude.ai/code/session_0197LBwCiZ8a682Vpm9iBzr8
  (session-ID comparison performed and recorded before this entry: all 16
  commits on PR #75 — b762359, 6fd7801, 9d7bf82, 42cae14, fb0c2ac, a8decce,
  a43dff9, a2b2123, 01e88fb, e691f6d, 60c8182, a298598, deaedc8, ce14699,
  3526a0c, b6b3a68 — carry `Claude-Session:` URLs for
  session_016bZ7g5DPgSU8eYMTo8H5NY, session_01Nzct85anuqYiaChHWrojc7,
  session_01CuWKZ8HB6vaFGqxfYg8aVL and session_01YVhNTy4brYqAztqcSzWLrs;
  none match this reviewing session's own ID, so this is an independent
  certification, not self-certification.
- Steps performed:
  1. Read `docs/certifications/queue.md`'s full FTF-R017 history: an
     earlier Control-plane pass on this same PR (2026-09-17) correctly
     found real CI failures (TCP-vs-HTTP readiness race, then SeaweedFS
     volume-capacity exhaustion, then a 3.71 version gap missing S3
     object-lock/versioning/BadDigest support) and did NOT certify —
     each was root-caused and fixed for real by later commits on the same
     branch/PR (no duplicate PR opened), ending with a 3.71->3.99 fixture
     bump and a real `S3Client` response-header case-sensitivity fix
     (`deaedc8`).
  2. Read the full delivered diff directly (`git diff origin/main...
     origin/claude/wizardly-fermat-ci7leb`, 17 files, +2893/-155):
     `apps/api/app/services/storage_semantics_client.py` (stdlib-only real
     AWS SigV4 client, no boto3/SDK dependency added — verified: no new
     entries in `apps/api/pyproject.toml`), `spec/component-maintenance.yaml`
     + schema + validator, `infra/internal/storage-profile.yaml` +
     validator, `infra/docker/seaweedfs-s3-identity.json`, and
     `tests/integration/test_storage_semantics.py` /
     `tests/tasks/test_ftf_r017.py`.
  3. Re-checked CI for real on the PR's actual head, not a stale/
     self-reported claim: `pull_request_read get_check_runs` on head
     `3526a0c` showed all 3 jobs `completed`/`success` (run 35263130329);
     read `storage-semantics-integration`'s own job log directly
     (job 105343546295) — genuine per-request S3 traffic against a live
     SeaweedFS 3.99 container (real 200/400 BadDigest/403 AccessDenied/404
     NoSuchKey responses across all 9 test functions), ending
     `FTF-R017 storage semantics: all checks passed`. This PR's base had
     since advanced (PR #77 merged docs-only bookkeeping to `main`),
     which made PR #75 `mergeable_state: dirty` again — the same
     structural failure this repo's history already documents for
     FTF-0004/FTF-R011. Fixed the same way: merged current `main` into
     `claude/wizardly-fermat-ci7leb` (two tracking-doc-only conflicts —
     `QUEUE.md`/`PROGRESS.md` — no code conflict; resolved by keeping
     `main`'s fuller, already-reconciled history rather than the branch's
     own condensed summary), pushed as merge commit `b6b3a68`, and
     re-confirmed a fresh, real CI run on that exact commit
     (run 35273905256, job 105379711235) — all 3 jobs `completed`/
     `success` again, log re-read directly and again ends `FTF-R017
     storage semantics: all checks passed` with genuine per-request S3
     traffic (different bucket-name suffixes than the prior run, proving
     it is not a cached/stale result).
  4. Own fault injection, distinct from the producer's own: broke
     `_signing_key`'s AWS4 key-derivation prefix (`"AWS4" + secret_key` ->
     `secret_key`) and re-ran `tests/tasks/test_ftf_r017.py` — failed for
     real (`FAIL [FTF-R017] the tracked module's own _signing_key produces
     the real published AWS test-suite signature (get-vanilla, generic
     'service')`), confirming the SigV4 signing math is genuinely
     code-enforced, not a canned pass. Restored the file byte-identical
     and re-ran clean.
  5. `python tools/r1_handoff.py packet FTF-R017 --plan
     FINE_TUNING_FACTORY_RELEASE_1.md` re-pulled directly: acceptance
     clause 1 ("Use maintained approved organizational S3 or qualified
     SeaweedFS fixture; prove required storage semantics and offline
     dependency/license inventory") is met by the real CI evidence above
     plus `spec/component-maintenance.yaml`'s per-component owner/
     support/license/security/version/export/restore records (all 7
     components' licenses independently fetched from each project's real
     `LICENSE` file, all permissive Apache-2.0/MIT, LiteLLM's separate
     proprietary `enterprise/` directory disclosed and unused). Acceptance
     clause 2 ("Record owner/support/version/security/export plan for
     Argilla and each dependency; maintain compatible old external S3
     source connection without silent migration") is met — Argilla's own
     record includes owner/support/license/security/version/export/restore
     plus its fixed self-hosted-only deployment note (no SaaS Argilla
     option); `old_external_s3_compatibility` in the same file states the
     no-silent-migration position, backed by the client's real
     `S3Endpoint`/`S3Credentials` constructor-argument design (verified by
     reading the file: never a hardcoded host). `negative_acceptance`
     ("Reject automatic archived-MinIO default, unimplemented S3 feature
     accepted, unapproved license/enterprise dependency, unsupported
     Argilla deployment or missing export/restore") maps one-to-one to
     `test_minio_backend_is_rejected`, `test_missing_semantic_is_rejected`
     / `test_unevidenced_qualified_claim_is_rejected`,
     `test_unapproved_license_is_rejected` /
     `test_enterprise_or_proprietary_feature_use_is_rejected`,
     `test_argilla_unsupported_deployment_note_is_present`, and
     `test_missing_export_plan_is_rejected` /
     `test_missing_restore_plan_is_rejected` respectively — all re-run and
     confirmed passing (see step 6).
  6. Full regression, fresh worktree at the real merged head:
     `uv run ruff check`/`mypy .` clean (50 source files); `pnpm install`
     + `pnpm run lint`/`typecheck`/`format:check` clean;
     `python3 tests/tasks/test_ftf_r017.py` clean (19 test functions / 25
     checks); `python3 scripts/validate_spec.py --strict` clean (task graph
     and verification-map clean — a `stale_commit` defect seen on an
     earlier, intermediate check of this same worktree was traced to this
     review's own shallow (`--depth=50`) clone, not a real defect: it
     disappeared after `git fetch --unshallow`); `verify_task.py --task
     FTF-R017 --stage task` -> `PASS` against evidence commit `b762359`;
     `product_truth_check.py` legitimately `0/0` (`proving_symbol: ''` on
     `docs/spec-matrix.yaml`'s FTF-R017 entry — a real, disclosed
     storage/OSS-lifecycle qualification layer with no live Factory
     production service importing `storage_semantics_client.py` yet, same
     established pattern as FTF-0004/FTF-R001/FTF-R011/FTF-R014/FTF-R015).
  7. Merged PR #75 (`mergeable_state: clean` after the merge-conflict fix
     above) as merge commit `aacd745`. Set `docs/spec-matrix.yaml`
     FTF-R017 to `certification_state: CERTIFIED` / `proof_state:
     L2-CERTIFIED` (`evidence_hash` kept at the real implementation commit
     `b762359`, matching `spec/verification-map.json`'s own `commit`
     field — same convention as FTF-R011/FTF-R015). Re-ran
     `scripts.task_claim.sync_certified_status()` — updated exactly
     `FTF-R017`'s own `status`/`evidence` fields in `spec/tasks.json` and
     `docs/implementation/TRACKER.json`.
  8. `verify_release.py` re-run after the above: `16/300` required tasks
     CERTIFIED with live-passing evidence for FTF-R017 itself (via
     `verify_task.py`; the live-container `storage-semantics-integration`
     evidence is proven in CI, not locally re-executable in this sandbox —
     same disclosed `resource_gate` pattern as FTF-0004's own
     `docker-local-stack-integration` job), non-inflated transition from
     the prior `15/300`. (This sandbox separately found `FTF-0002`/
     `FTF-0003`/`FTF-H001` reporting FAIL under `verify_release.py` in a
     fresh worktree before `pnpm install` — traced to this review's own
     missing `node_modules`, not a real regression: confirmed by
     `pnpm install` + a clean `pnpm run lint` immediately after; unrelated
     to FTF-R017 and out of this certification's scope.)
- Conclusion: the delivered SigV4 client, component-maintenance inventory
  and storage-profile qualification are real and independently
  reproduced — the fault injection confirms the signing math is
  code-enforced, the real live-container evidence is proven twice on two
  separate genuine CI runs (including one after this review's own
  merge-conflict fix), and the acceptance/negative_acceptance clauses are
  met item by item against the task's real resolved contract. No open
  defect. Certified L2-CERTIFIED.

## FTF-0102 — Organization/workspace schema
- Criterion ID: FTF-0102
- PR: https://github.com/SJZLab/Sovra/pull/83
- Merge commit: 1874932 (`18749323d091a6f6a23be2e9510692035911ac25`)
- Validated-by: session https://claude.ai/code/session_01WYf49qFkGAtWXsTMpiKbxu
  (session-ID comparison performed and recorded before this entry: all 4
  commits on PR #83 — 82c75f4, b2bb569, 01ebde9, 8521558 — carry
  `Claude-Session: .../session_01FLEHnA2RjFZB9RftzV4Lso`; none match this
  reviewing session's own ID, so this is an independent certification,
  not self-certification.)
- Steps performed:
  1. Read the full delivered diff directly in a dedicated worktree
     (`git diff d4d0a76..01ebde9`, 17 files, +2118/-25):
     `apps/api/app/auth/organization_workspace_schema.py` (Organization/
     Workspace SQLAlchemy models with constructor-level slug/name/status
     validation), `apps/api/alembic/versions/0001_org_workspace_schema.py`
     (the real forward migration: both tables, a `sovra_app`
     `NOSUPERUSER NOBYPASSRLS` role, `FORCE ROW LEVEL SECURITY` on both
     tables scoped to `app.current_organization_id`), the frozen
     `packages/contracts/{jsonschema,openapi}/01-*` files (zero OpenAPI
     paths, confirmed no live FastAPI app exists to back a route), and
     the two test files.
  2. Re-ran `tests/tasks/test_ftf_0102.py` (no DB required) in a fresh
     `uv sync --all-packages` environment — all checks pass.
  3. **Real, independent live-Postgres reproduction** (this sandbox has a
     directly-installed PostgreSQL 16 cluster, same documented
     no-registry-egress finding as FTF-0004/FTF-R017): created a scratch
     database and role, ran `alembic upgrade head` for real, then ran
     `tests/integration/test_organization_workspace_schema.py` against it
     — all 11 checks pass (tenant-scoped visibility, missing-context
     denial, cross-tenant insert/update rejection via real RLS errors,
     duplicate-slug and tenant-composite-uniqueness constraint
     violations).
  4. **Real fault injection reproducing the producer's own claim**:
     granted `sovra_app` role `BYPASSRLS` in a scratch copy of the
     migration, re-ran the integration suite against a fresh database —
     exactly 6 checks failed for real (every tenant-isolation and
     negative_acceptance case), matching the producer's claimed result
     precisely. Restored the file byte-identical (`diff` empty), re-ran
     clean (11/11 pass).
  5. **A second, independent fault injection not in the producer's own
     set**: set the `workspaces` table's RLS policy `WITH CHECK` clause
     to a permissive `true` (simulating a plausible real regression) and
     re-ran the integration suite — it still reported all checks passing.
     Investigated why: Postgres enforces the table's `SELECT` policy
     (`USING`) against the resulting row of both `INSERT ... RETURNING`
     (the SQLAlchemy ORM's default flush behavior) and a same-policy
     `UPDATE`, independently of a separate/broken `WITH CHECK` clause —
     confirmed with a minimal isolated table reproducing the same
     behavior. A raw `INSERT` bypassing `RETURNING` (a code path that
     does **not** exist anywhere in this codebase — the only application
     code is the ORM path exercised by the delivered test) *does* let a
     broken `WITH CHECK` write a cross-tenant row. Conclusion: the
     shipped migration's actual `WITH CHECK` clauses are correct (not
     mutated), the live application code path (ORM-only) is genuinely
     protected by defense-in-depth even against this specific fault, and
     no acceptance/negative_acceptance clause requires resilience against
     a non-ORM write path that doesn't exist in the delivered code. Not a
     blocking defect — recorded here as an observation: the INSERT-side
     negative_acceptance evidence is currently more incidental (an
     SQLAlchemy `implicit_returning` implementation detail) than
     intentional, and would be worth a dedicated `WITH CHECK`-only
     regression test (independent of `RETURNING`) if `apps/api` ever
     disables implicit returning (e.g. for PgBouncer transaction-pooling
     compatibility) or adds a non-ORM write path to these tables.
  6. **CI re-checked for real on the actual PR head**, not a stale
     claim: initial check (head `01ebde9`) showed all 3 wired jobs
     `in_progress`; polled again after real work — head advanced to
     `8521558` (a tracking-doc-only commit, confirmed via `git diff`) with
     `mergeable_state: clean` and all 4 check runs
     (`organization-workspace-schema-integration`, `gate`,
     `docker-local-stack-integration`, `storage-semantics-integration`)
     `completed`/`success`. Read the
     `organization-workspace-schema-integration` job's log content
     directly (not the checkmark alone): a genuine PostgreSQL 16.15
     container boots, `alembic upgrade head` runs for real, and the
     container's own Postgres log shows the actual RLS/unique-constraint
     errors the negative_acceptance test expects
     (`new row violates row-level security policy for table "workspaces"`
     ×2, `duplicate key value violates unique constraint
     "organizations_slug_key"`, `duplicate key value violates unique
     constraint "uq_workspaces_organization_id_slug"`), ending
     `FTF-0102 real tenant-isolation proof: all checks passed`.
  7. Task contract re-pulled directly: `tools/r1_handoff.py packet
     FTF-0102 --plan FINE_TUNING_FACTORY_RELEASE_1.md`. Acceptance clause
     1 ("freeze the shared interface/domain schemas ... create two
     organizations/workspaces, enforce unique names and tenant-composite
     foreign keys") maps to the frozen contract files plus
     `test_organization_slug_globally_unique`,
     `test_workspace_composite_unique_constraint_declared`,
     `test_workspace_organization_id_foreign_key_cascades`, and the
     integration suite's real duplicate-slug/tenant-composite-uniqueness
     checks ("unique names" read as the domain's actual unique
     identifier — `slug` — consistent with the delivered schema, which
     has no separate display-name uniqueness requirement anywhere in the
     task contract or Section 6). Negative_acceptance ("cross-tenant
     parent IDs and missing tenant context fail DB constraints/RLS,
     including privileged test-role mistakes") maps one-to-one to the
     integration suite's missing-context/cross-tenant-insert/
     cross-tenant-update checks and the independently-reproduced
     BYPASSRLS fault injection (step 4) — the "privileged test-role
     mistake" case specifically.
  8. Full regression, fresh worktree at the real merged head (`1874932`):
     `uv run ruff check .`/`ruff format --check .`/`mypy .` clean (57
     source files); `pnpm install --frozen-lockfile` + `pnpm run
     lint`/`typecheck`/`format:check` clean; every existing
     `tests/tasks/test_ftf_*.py` plus
     `tests/test_ftf_0001_monorepo_layout.py` re-run clean (no
     regression); `uv run pytest tests/unit` (3 passed); `pnpm exec
     vitest run` (2 passed); `python3 scripts/validate_spec.py --strict`
     clean (a `stale_commit` defect seen on an initial check of this same
     worktree was traced to its own shallow (`--depth=50`) clone, not a
     real defect — disappeared after `git fetch --unshallow`, same
     recurring artifact as FTF-R017's own review); `product_truth_check.py
     --fail-on-unwired` stays legitimately `0/0` (`proving_symbol: ''` —
     no live FastAPI app imports `organization_workspace_schema.py` yet,
     same disclosed pattern as FTF-0002/0003/0004/0005/0006/R001/R011/
     R014/R015/R017).
  9. Set `docs/spec-matrix.yaml` FTF-0102 to `certification_state:
     CERTIFIED` / `proof_state: L2-CERTIFIED` (`evidence_hash` kept at the
     implementation commit `82c75f4`, matching
     `spec/verification-map.json`'s own new `commit` field — same
     convention as FTF-R011/FTF-R015/FTF-R017). Added FTF-0102's own
     entry to `spec/verification-map.json` (the no-DB
     `tests/tasks/test_ftf_0102.py` command — the live-Postgres
     integration evidence is disclosed as CI-only/not locally
     re-executable in this sandbox, same as FTF-0004/FTF-R017). Ran
     `scripts.task_claim.sync_certified_status()` — diff limited to
     exactly FTF-0102's own `status`/`evidence` fields in
     `spec/tasks.json` and `docs/implementation/TRACKER.json`.
  10. `verify_task.py --task FTF-0102 --stage task` -> `PASS`.
      `verify_release.py` non-inflated: `17/300` required tasks CERTIFIED
      with live-passing evidence, zero `FAILED`, up from `16/300` (an
      intermediate `FAILED (3): ['FTF-0002', 'FTF-0003', 'FTF-H001']` seen
      in this same worktree before `pnpm install` was traced to missing
      `node_modules` in a fresh checkout, not a real regression — same
      recurring environmental artifact FTF-R017's own review already
      documented).
- Note for the next relay/certifier shift (not a certification defect,
  disclosed per this repo's own transparency norms): `git log` shows
  `b25ce80` ("Control plane: FTF-R017 CERTIFIED") landed directly on
  `main` with a single parent and committer `Claude <noreply@anthropic.com>`
  — a real, direct push, not a GitHub PR merge (contrast with `aacd745`,
  the actual PR #75 merge commit, which has two parents and committer
  `GitHub <noreply@github.com>`). This is exactly the pattern
  `CLAUDE.md`'s own `BRANCH_PROTECTION` note was written to close
  (dated 2026-09-15), recurring on 2026-09-17 after that note existed.
  This certification's own tracking-doc updates were instead opened as a
  pull request and merged through the normal GitHub flow, per
  `CLAUDE.md`'s current, explicit instruction. Flagging for the owner via
  `QUEUE.md`'s "Owner actions" — not otherwise in scope for this
  certification.
- Conclusion: the delivered Organization/Workspace schema, migration, and
  RLS tenant-isolation enforcement are real and independently reproduced,
  including a fault injection matching the producer's own claim exactly.
  A second, independent fault injection surfaced a genuine but
  non-blocking observation about the INSERT-path evidence's reliance on
  an SQLAlchemy implementation detail (recorded above, not a live
  defect — the shipped code and its only real call path are correct).
  The acceptance/negative_acceptance clauses are met item by item against
  the task's real resolved contract, and CI is genuinely green on the
  actual merged head. No blocking defect. Certified L2-CERTIFIED.

## FTF-0104 — Audit event writer
- Criterion ID: FTF-0104
- PR: https://github.com/SJZLab/Sovra/pull/87
- Merge commit: c682687 (`c682687af99230efcbbdd1942cefcf933844b826`)
- Validated-by: session https://claude.ai/code/session_019oRVQmqyscTzBokcuJyXxw
  (session-ID comparison performed and recorded before this entry: all 5
  commits on PR #87 — 96e1598, 8ccbd21, 8f8b0c9, 46938eb, 5efcbb9 — carry
  `Claude-Session: .../session_01FbdsNrj2DRZh92AB3edHUJ`; none match this
  reviewing session's own ID, so this is an independent certification,
  not self-certification.)
- Steps performed:
  1. Read the full delivered diff directly in a dedicated worktree at the
     real PR head (5efcbb9): `apps/api/app/auth/audit_event_writer.py`
     (`AuditEvent` ORM model sharing FTF-0102's `Base`/metadata;
     `redact_details()` recursively redacts secret-shaped keys and
     secret-shaped values under unexpected keys; `write_audit_event()`
     only `session.add()`s, never commits), and
     `apps/api/alembic/versions/0002_audit_event_writer.py` (real forward
     migration, `down_revision = "0001_org_workspace_schema"` — single
     serialized chain onto FTF-0102; `FORCE ROW LEVEL SECURITY` on
     `audit_events`; `GRANT SELECT, INSERT` only to `sovra_app`, no
     `UPDATE`/`DELETE`), plus the two test files.
  2. Re-ran `tests/tasks/test_ftf_0104.py` (no DB required) in a fresh
     `uv sync --all-packages` environment — all 26 checks pass.
  3. **Own independent fault injection against the no-DB suite** (not one
     the producer tried): removed the `actor_type` allow-list check from
     `AuditEvent.__init__` — exactly the "an unknown actor_type is
     rejected" check failed for real; restored byte-identical, re-ran
     clean.
  4. **Real, independent live-Postgres reproduction** (this sandbox has a
     directly-installed PostgreSQL 16 cluster, same documented
     no-registry-egress finding as FTF-0004/FTF-R017/FTF-0102): created a
     scratch role/database, ran `alembic upgrade head` for real (both
     0001 and 0002 apply cleanly in sequence), inspected the real GRANT
     surface directly via `psql \dp audit_events` — confirmed
     `sovra_app=ar/sovra_migrate` (SELECT+INSERT only, matching the
     claim exactly) — then ran
     `tests/integration/test_audit_event_writer.py` against it: all
     checks pass, including the acceptance case (a committed mutation +
     its audit event are both visible after commit) and the
     negative_acceptance cases (a rolled-back transaction leaves neither
     the mutation nor its audit event behind — "failed transaction
     cannot emit misleading success"; the restricted role's own
     UPDATE/DELETE against an existing audit row are rejected by the
     database).
  5. **Own independent fault injection against the live database** (not
     merely re-running the producer's own fixture): ran
     `GRANT UPDATE, DELETE ON audit_events TO sovra_app;` directly via
     `psql` against the scratch database, then re-ran
     `tests/integration/test_audit_event_writer.py` — exactly the 3
     append-only negative_acceptance checks failed for real (UPDATE
     rejected / DELETE rejected / row survives unchanged), matching the
     producer's own claimed fault-injection result exactly. Revoked the
     grant, re-ran clean.
  6. **CI verified for real on the actual PR head**, not a stale or
     self-reported claim: `pull_request_read get_check_runs` on PR #87
     initially showed 3 of 5 jobs still `in_progress` against an earlier
     commit (46938eb); the producer pushed one more tracking-doc-only
     commit (5efcbb9) mid-review — confirmed via `actions_get
     get_workflow_run` that workflow run 35338047691's own `head_sha`
     matched the PR's actual current head, not a stale run tied to an
     older commit. Waited for all 5 check runs to reach `completed`:
     `gate`, `docker-local-stack-integration`,
     `storage-semantics-integration`,
     `organization-workspace-schema-integration`, and
     `audit-event-writer-integration` — all `success`. Read the
     `audit-event-writer-integration` job's log content directly (not
     trusted from the green checkmark alone): a genuine PostgreSQL 16.15
     container on GitHub's own registry-egress runner, with the
     container's own Postgres log showing real
     `ERROR: permission denied for table audit_events` lines for both the
     UPDATE and DELETE attempts the negative_acceptance test issues,
     ending `FTF-0104 audit event writer real proof: all checks passed`.
     `mergeable_state: clean` on the final head.
  7. **Regression check**, fresh `uv sync --all-packages` +
     `pnpm install --frozen-lockfile` + `git fetch --unshallow` (the
     `stale_commit`/missing-`node_modules` artifacts this exact sequence
     is known to produce, per FTF-0102/FTF-R017's own prior review
     entries, disappeared after the fix, confirming they were
     environmental, not real defects): `ruff check .` / `mypy .` clean;
     `pnpm run lint`/`typecheck`/`format:check` clean; every existing
     `tests/tasks/test_ftf_*.py` and
     `tests/test_ftf_0001_monorepo_layout.py` re-run clean, including
     FTF-0102's own `tests/integration/test_organization_workspace_schema.py`
     re-run clean against the same fresh Postgres cluster (no regression
     to its RLS/tenant-isolation evidence); `validate_spec.py --strict`
     clean; `product_truth_check.py --fail-on-unwired` stays legitimately
     `0/0` (`proving_symbol: ''` — no live FastAPI app imports
     `audit_event_writer.py` yet, same disclosed pattern as
     FTF-0002/0003/0004/0005/0006/R001/R011/R014/R015/R017/0102).
  8. Task contract re-pulled directly: `tools/r1_handoff.py packet
     FTF-0104 --plan FINE_TUNING_FACTORY_RELEASE_1.md`. Acceptance clause
     1 ("each committed sensitive mutation emits append-only
     actor/resource/version/request evidence") maps one-to-one to the
     `AuditEvent` schema (Section 6.24's own field list has no separate
     "version" field — `action` is the real field recording what
     changed, matching the domain model verbatim) and the live-Postgres
     acceptance check (step 4 above). Negative_acceptance ("failed
     transaction cannot emit misleading success; redacted audit cannot be
     edited by normal app role") maps to steps 4 and 5 above exactly.
     `shared_lock_required` lists `contracts:01`, but the task's own
     `files` list and acceptance/negative_acceptance clauses require no
     HTTP-exposed contract surface — confirmed on review, same disclosed,
     already-accepted scope limit as FTF-0102's own 0 OpenAPI paths, not
     assumed correct without checking.
  9. Governance claims in the PR body double-checked, not taken on trust:
     PR #82 and PR #84 (closed as stale/superseded per the PR body) are
     both confirmed `state: closed`, `merged: false` via direct
     `pull_request_read`.
  10. Set `docs/spec-matrix.yaml` FTF-0104 to `certification_state:
      CERTIFIED` / `proof_state: L2-CERTIFIED` (`evidence_hash` kept at
      the implementation commit `96e1598`, matching
      `spec/verification-map.json`'s own new `commit` field). Added
      FTF-0104's own entry to `spec/verification-map.json` (the no-DB
      `tests/tasks/test_ftf_0104.py` command — the live-Postgres
      integration evidence is disclosed as CI-only/not locally
      re-executable in this sandbox, same as FTF-0004/FTF-R017/FTF-0102).
      Ran `scripts.task_claim.sync_certified_status()` — diff limited to
      exactly FTF-0104's own `status`/`evidence` fields in
      `spec/tasks.json` and `docs/implementation/TRACKER.json`.
  11. `verify_task.py --task FTF-0104 --stage task` -> `PASS`.
      `verify_release.py` non-inflated: `18/300` required tasks CERTIFIED
      with live-passing evidence, zero `FAILED`, up from `17/300`.
- Conclusion: the delivered append-only, tenant-scoped audit event writer
  and its database-enforced GRANT surface are real and independently
  reproduced, including two fault injections not in the producer's own
  set (the no-DB actor_type check, and a live-database GRANT widening run
  directly via `psql` rather than through the producer's own script). The
  acceptance/negative_acceptance clauses are met item by item against the
  task's real resolved contract, and CI is genuinely green on the actual
  merged head. No blocking defect. Certified L2-CERTIFIED.

## FTF-1601 — Postgres backup
- Criterion ID: FTF-1601
- PR: https://github.com/SJZLab/Sovra/pull/100 (supersedes closed PR #96,
  which itself superseded closed PR #90 — same underlying implementation
  commit `f5d3aee` throughout, unchanged since PR #90)
- Merge commit: f4e9b39 (`f4e9b39b5cb9e8cc1222dde8e03f9e4d6cf9dbf4`)
- Validated-by: session https://claude.ai/code/session_01BpsxwNZKMCes4UuKAnRvSG
  (session-ID comparison performed and recorded before this entry: every
  commit unique to PR #100 over `origin/main` — `f5d3aee`, `33f4cb5`,
  `e8ca71d`, `f104ef6`, `6c90f80`, `20aa4e5`, `2a98c9d`, `902139b`,
  `f3a3b49`, `353b82b`, `19e18de`, `bc8868d`, `8496133`, `4a2f3d3` — carries
  `Claude-Session` trailers for session_01B9eYtKy6gPX3Ym8Cvx3X9c,
  session_01RDefDg12JGVTtU732EEARs, session_01TkFfAaPq6htuVNNLXU9G2b,
  session_016ryLQ1vSJCNYTMHw9DrcSd, or session_015rZiy6EjN6jvYYWzkGE2gh;
  none match this reviewing session's own ID — independent, not
  self-certification.)
- Steps performed:
  1. Confirmed this is a resubmission, not new work: `git log
     origin/main..origin/claude/wizardly-fermat-rlp631` showed the exact
     same implementation commit `f5d3aee` (Postgres backup: encrypted,
     self-verified, RLS-surviving restore) and fix commit `353b82b` (adds
     the missing `restoration_validated` negative test) already
     independently reviewed by two prior Control-plane sessions on
     2026-09-18 (PR #90, structural-blocker-only, content sound) and
     2026-09-19 (PR #96, Law 1 gap on `restoration_validated`'s three
     subset terms — closed by `353b82b`, independently re-verified via
     fresh fault injection removing each subset term one at a time against
     the real script, each caught by the new negative test, each restored
     byte-identical), both already recorded above/in
     `docs/certifications/queue.md`.
  2. `git diff 19e18de 8496133 --stat` (PR #96's own already-certified-sound
     head vs. this PR's pre-final-doc-commit head) — confirmed the only
     changes are `PROGRESS.md`/`QUEUE.md`/`docs/certifications/queue.md`
     (tracking-doc merge-conflict resolution); zero production code, test,
     or contract files touched. Re-confirmed no drift after the PR's own
     final doc-only commit (`4a2f3d3`, "record PR #100 opened, CI green,
     PR #96 closed").
  3. Re-checked CI for real on the actual current head, not a stale claim:
     `pull_request_read get` on PR #100 initially returned head `8496133`;
     the PR advanced mid-review to `4a2f3d3` (a further doc-only commit) —
     re-fetched and re-checked. `actions_get get_workflow_run` on run
     `35422662189` confirmed its own `head_sha` is `4a2f3d3`, matching the
     PR's live head exactly: all 6 jobs (`gate`,
     `docker-local-stack-integration`,
     `organization-workspace-schema-integration`,
     `audit-event-writer-integration`, `postgres-backup-integration`,
     `storage-semantics-integration`) `completed`/`success`, including the
     real live-Postgres `postgres-backup-integration` job on GitHub's own
     registry-egress runner. Zero open review comment threads.
     `mergeable_state: clean`.
  4. Task contract re-pulled directly: `tools/r1_handoff.py packet
     FTF-1601 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause
     1 ("...restoration test"), Section 71.2 ("a backup that has never
     been restored does not count as validated"), and the
     negative_acceptance clause ("Backup command success without
     verifiable usable data/key path is not done") all map to the specific
     tests and fault injections the 2026-09-18/2026-09-19 reviews already
     independently reproduced against a live Postgres cluster and a real
     no-DB fault injection — confirmed met, not asserted, and unaffected
     by this PR's tracking-doc-only delta.
  5. Own regression in a dedicated worktree at the real merged head
     (`f4e9b39`), fresh `uv sync --all-packages` + `pnpm install
     --frozen-lockfile` + `git fetch --unshallow`: `ruff check .` / `mypy
     .` clean (63 files); `pnpm run lint`/`typecheck`/`format:check`
     clean; `uv run python3 tests/tasks/test_ftf_1601.py` — "all checks
     passed" (18/18, exactly as CI invokes it, bare `python3` under the
     `uv` venv); `uv run pytest tests/unit` — 3 passed; `pnpm exec vitest
     run` — 2 passed; `uv run python3 scripts/validate_spec.py --strict`
     clean; `product_truth_check.py --fail-on-unwired` legitimately `0/0`
     (`proving_symbol: ''` — no live FastAPI app imports
     `postgres_backup.py` yet, same established disclosed pattern as every
     prior non-product-true certified task). Did not re-run the
     live-Postgres integration test or repeat the encrypt/self-verification
     and `restoration_validated` fault injections locally — no code
     changed since the two prior independent Control-plane reviews already
     reproduced them against a live cluster and CI's own
     `postgres-backup-integration` job re-confirmed the same real
     `pg_dump`/`pg_restore`/`openssl` path green on this exact head; a
     third redundant reproduction of unchanged code was judged unnecessary
     effort, not skipped diligence.
  6. Noted a real, environment-specific artifact while re-running
     `verify_release.py` bare (no `uv run` prefix) in a fresh worktree with
     no local Postgres cluster running: it reported `FTF-0102`/`FTF-0104`
     as `FAILED` with `ModuleNotFoundError: No module named 'sqlalchemy'`.
     Reproduced this is purely a bare-`python3`-vs-`uv`-venv PATH artifact
     of this sandbox, not a real regression: `uv run python3
     tests/tasks/test_ftf_0102.py` and `tests/tasks/test_ftf_0104.py` both
     independently re-run clean once invoked the same way CI/the `uv`
     venv actually does. Not this PR's concern (touches neither task's
     files) and not acted on beyond this note.
  7. Set `docs/spec-matrix.yaml` FTF-1601 to `certification_state:
     CERTIFIED` / `proof_state: L2-CERTIFIED` (`evidence_hash` kept at the
     implementation commit `f5d3aee`, matching
     `spec/verification-map.json`'s own new `commit` field). Added
     FTF-1601's own entry to `spec/verification-map.json` (the no-DB
     `tests/tasks/test_ftf_1601.py` command — the live-Postgres
     encrypted-backup/restore/RLS-survival evidence is disclosed as
     CI-only/not locally re-executable in this sandbox, same as
     FTF-0004/FTF-R017/FTF-0102/FTF-0104).
- Conclusion: the delivered encrypted, self-verified, RLS-surviving
  Postgres backup/restore tooling is real, was already independently
  reproduced against a live Postgres cluster and adversarially
  fault-injected (encrypt/self-verification path, and separately each of
  `restoration_validated`'s three subset terms) by two prior, distinct
  Control-plane sessions across PR #90 and PR #96, and this PR's only
  delta over that already-sound content is a tracking-doc merge-conflict
  resolution against current `main`, independently confirmed to introduce
  zero code change and to stay green on real CI at its actual final head.
  No blocking defect. Certified L2-CERTIFIED.

## FTF-0101 — OIDC login
- Criterion ID: FTF-0101
- PR: https://github.com/SJZLab/Sovra/pull/114 (supersedes #112, which
  superseded #103 — both closed, neither merged)
- Merge commit: 5de1c58 (`5de1c58832ea7e34d0c91f3083654b1fa46e909a`)
- Evidence commit (`evidence_hash`, `spec/verification-map.json`
  `commit`): e2c5423 (`e2c54235584445f7e062f3b1b21a7b98f47308c2`) — the
  real refresh-implementation commit, per this repo's established
  two-commit pattern (implementation, then a hash-fixup commit pointing
  at it).
- Validated-by: session https://claude.ai/code/session_01TbPTaVC6QZGU54fpkSakxE
  (session-ID comparison performed and recorded before this entry: all 15
  commits on PR #114 carry either `Claude-Session:
  .../session_01BaakM658Kz9nuZXKF53kc1` (this PR's own new commits),
  `.../session_01Un2BpAFyPcvY7zUpk7ZHbe` (PR #112's carried-forward
  commits), or `.../session_015u9WhWDyzkFgkk74HzHJ9d` (PR #103's, carried
  forward further back); none match this reviewing session's own ID, so
  this is an independent certification, not self-certification.)
- Steps performed:
  1. Confirmed no prior Control-plane review had certified this task:
     `docs/certifications/queue.md`'s two prior 2026-09-19 reviews of
     PR #103 and PR #112 both concluded NOT CERTIFIED — PR #103 for
     acceptance clause 1's "Real OIDC provider" evidence resting only on a
     fake stdlib stand-in; PR #112 for implementing login/logout against a
     real provider but leaving "refresh" (a named third of the same
     compound clause) unimplemented, untested, and claimed MET without
     disclosure.
  2. Read the full delivered diff directly on the real PR #114 head:
     `apps/api/app/auth/oidc_login.py`'s new `exchange_refresh_token()`,
     `_RefreshTokenStore` (in-memory, keyed by session `jti`, mirrors the
     existing `_SessionRevocationStore`; the raw refresh_token is never
     sent to the browser), and `POST /auth/refresh` (validates the new
     id_token the same way `callback()` does, confirms subject/issuer
     continuity — fails closed and ends the session on a mismatch — then
     rotates the session: old `jti` revoked, new signed cookie issued;
     CSRF double-submit protected the same way as `logout()`); plus the
     `offline_access`/`prompt=consent` additions to
     `build_authorization_redirect_url` that a real provider requires
     before it will ever mint a `refresh_token` (confirmed empirically by
     the producer against the real OP before writing app code, per the
     module's own docstring and this shift's `queue.md`/`PROGRESS.md`
     entries).
  3. **CI reconfirmed for real on the actual current head, twice** — the
     PR head moved twice during this review (734068b, adding the missing
     `docs/certifications/queue.md` PROVISIONAL entry the initial
     `pull_request.opened` trigger caught before it existed; then
     40ef553, a `PROGRESS.md`-only commit) — re-polled rather than
     trusting the PR body's own "to be confirmed independently" line each
     time: all 7 checks (`gate`, `oidc-provider-integration`,
     `docker-local-stack-integration`,
     `organization-workspace-schema-integration`,
     `audit-event-writer-integration`, `postgres-backup-integration`,
     `storage-semantics-integration`) completed `success` on the real
     final head (40ef553) that was actually merged.
  4. **Own independent fault injection, distinct from the producer's own**
     (which removed the rotation-revoke call): in this sandbox, after
     `uv sync --all-packages`, disabled `refresh()`'s own
     subject/issuer-continuity check (`if claims.subject != data["sub"]
     or claims.issuer != data["iss"]:` forced to `if False:`). A `pytest
     -k` invocation of the affected test misleadingly reported PASSED —
     `tests/tasks/test_ftf_0101.py` uses a soft `check()`/`FAILURES`
     pattern that only becomes a real assertion via its own `main()`
     (`sys.exit(1)` if `FAILURES`), exactly how `.github/workflows/ci.yml`
     actually invokes it (`uv run python3 tests/tasks/test_ftf_0101.py`,
     not pytest) — re-ran it that way and the break was caught for real:
     `FAIL [FTF-0101] a refresh that would swap the session's subject is
     denied`, exit 1. Reverted (`cp` from a pre-edit backup, confirmed
     byte-identical via `git diff`), re-ran clean (exit 0, "all checks
     passed"); `tests/integration/test_oidc_login_real_provider.py` also
     re-confirmed clean and unaffected by the revert (its own tests don't
     exercise this particular path).
  5. **`product_truth_check.py` result confirmed legitimate:**
     `--fail-on-unwired` reports `1/1 product-true`; `grep` on
     `apps/api/app/main.py` confirms `build_oidc_login_router` is imported
     and called unconditionally from `create_app()` — a genuine
     live-caller wiring, not a 0/0 pass (this task's `proving_symbol` is
     filled in).
  6. Task contract re-pulled directly: `python tools/r1_handoff.py packet
     FTF-0101 --plan FINE_TUNING_FACTORY_RELEASE_1.md`. Acceptance clause
     1 ("Real OIDC provider login/refresh/logout validates
     issuer+audience+subject and secure session") is now met in full:
     login and logout were already proven against the real `idpyoidc` OP
     by PR #112's own (independently reviewed) evidence, carried forward
     unchanged; refresh is newly proven the same way by this PR's two new
     tests in `tests/integration/test_oidc_login_real_provider.py`
     (`test_refresh_grant_mints_a_new_real_id_token_and_rotates_the_session`,
     `test_refresh_without_csrf_token_is_denied_by_the_real_flow`) — read
     directly: a real login against the real OP, then a real `POST
     /auth/refresh` exchanging the real, server-held refresh_token for a
     real new id_token against the real OP's own `/token` endpoint
     (`grant_type=refresh_token`), with real session rotation and CSRF
     enforcement. Acceptance clause 2 (independent review + merged commit
     + real integration + negative-test evidence) is satisfied by this
     entry itself. The negative_acceptance clause (forged issuer/audience,
     expired token, state/nonce mismatch, revoked membership, CSRF all
     denied) was already independently proven in the PR #112 review and is
     unchanged here. `docs/spec-matrix.yaml`'s FTF-0101 notes read
     accurately against all of the above — every remaining gap (revoked-
     membership fail-closed default, in-memory refresh-token/session
     stores, no RFC 7009 provider-revocation call on logout, no Next.js
     app shell, no secret-manager integration) is disclosed, not silently
     expanded past what was already accepted in the PR #112 review.
  7. Set `docs/spec-matrix.yaml` FTF-0101 to `certification_state:
     CERTIFIED` / `proof_state: L2-CERTIFIED` (`evidence_hash` kept at the
     implementation commit `e2c5423`, matching the new
     `spec/verification-map.json` `commit` field, same convention as
     FTF-0102/FTF-0104/FTF-1601). Added FTF-0101's own entry to
     `spec/verification-map.json` (the no-DB
     `tests/tasks/test_ftf_0101.py` command — the live-provider
     login/refresh/logout evidence is disclosed as CI-only/not locally
     re-executable by `verify_task.py` in this sandbox, same established
     pattern as FTF-0004/FTF-R017/FTF-0102/FTF-0104/FTF-1601). Merged PR
     #114 (`5de1c58`).
  8. This certification's own PR (#116) first failed CI's `gate` job for
     real: `test_ftf_h034.py` genuinely failed 2 checks ("spec/tasks.json's
     status field genuinely reflects every currently-CERTIFIED task",
     "TRACKER.json's status field genuinely reflects every currently-
     CERTIFIED task") because setting `docs/spec-matrix.yaml` to CERTIFIED
     without also running `scripts/task_claim.py`'s `sync_certified_status()`
     left `spec/tasks.json`/`docs/implementation/TRACKER.json` still saying
     `NOT_STARTED`/no evidence for FTF-0101 — a real, disclosed process gap
     in this review's own first push, not silently worked around: ran
     `sync_certified_status()` directly, which updated exactly FTF-0101's
     own `status`/`evidence` fields in both files (diff confirmed
     minimal), re-ran `test_ftf_h034.py` and the full gate command sequence
     locally end to end (all pass), then pushed the fix as a second commit
     on the same certification PR before re-merging.
- Conclusion: the delivered OIDC login/refresh/logout machinery is real,
  independently reproduced against a real local `idpyoidc` OpenID
  Provider for all three named flows, genuinely fault-injectable (this
  review's own chosen break, distinct from both prior reviews' and the
  producer's own), and CI is green on the real final merged head. Both
  gaps the two prior Control-plane reviews found — missing real-provider
  evidence, then unimplemented/undisclosed refresh — are closed for real,
  not merely disclosed around. No blocking defect. Certified
  L2-CERTIFIED.

## FTF-0103 — RBAC middleware
- Criterion ID: FTF-0103
- PR: https://github.com/SJZLab/Sovra/pull/118
- Merge commit: b4b47c0 (`b4b47c0678422dace8e5994af5929586e9301166`)
- Validated-by: session https://claude.ai/code/session_01XZVCzf4rTC7QTXUCMwP65Q
  (session-ID comparison performed and recorded before this entry: all 5
  commits on PR #118 — 9d9e536, fcfa2fc, da86a068, 54dad4b, 801e2d0 —
  carry `Claude-Session: .../session_012jXC2eTsD2Vx47hmtLLiBu`; none match
  this reviewing session's own ID, so this is an independent
  certification, not self-certification.)
- Steps performed:
  1. Task contract pulled fresh via `python tools/r1_handoff.py packet
     FTF-0103 --plan FINE_TUNING_FACTORY_RELEASE_1.md`, not taken from the
     PR body alone: acceptance = ["Every route/operation maps to explicit
     role and scoped project policy on server.", "Independent review,
     exact merged commit, real relevant integration and negative-test
     evidence recorded."]; negative_acceptance = ["Guessed UUID or
     body-selected tenant cannot bypass viewer/evaluator/approver
     boundaries."]; `database_changes` reads "No independent schema
     change... any required change is a separately claimed migration
     prerequisite" — confirmed directly (`organization_workspace_schema.py`
     has only `Organization`/`Workspace`; no `Membership` table or frozen
     contract for one exists anywhere in this repository).
  2. Read the full delivered diff directly, including a dedicated `git
     worktree` at the real PR head (`801e2d0`):
     `apps/api/app/auth/rbac_middleware.py` (`Role` enum matching Section
     6.4's exact role set; `require_role(allowed_roles, ...)` — a FastAPI
     dependency factory resolving the organization strictly from the URL
     path via `organization_id_from_path()`, denying outright
     (`tenant_mismatch`, 403) if a mutating request's own JSON body names
     a disagreeing `organization_id`, then denying unless `role_lookup`
     returns a role in `allowed_roles`; `principal_resolver`/`role_lookup`
     both injectable and fail-closed by default), and
     `tests/tasks/test_ftf_0103.py` (a real uvicorn demonstration app over
     a real local socket, driven via `http.client`, not an in-process call
     or mocked request).
  3. Re-ran `uv run python3 tests/tasks/test_ftf_0103.py` in a fresh `uv
     sync --all-packages` environment (after `git fetch --unshallow`,
     needed once in this sandbox) — all checks pass.
  4. **Two own independent fault injections** (distinct vectors from the
     producer's own two, reproduced independently rather than trusted):
     (a) flipped the body-tenant-mismatch condition's `!=` to `==` in
     `require_role`'s dependency closure — exactly the two expected
     body-tenant checks failed
     (`negative_acceptance: a body-selected tenant ... is denied
     outright`, `a body tenant field that agrees with the URL path does
     not itself cause a denial`); reverted, `git diff --stat` confirmed no
     residual change, re-ran clean. (b) changed
     `role = role_lookup(...)` to `role = role_lookup(...) or
     Role.VIEWER`, auto-downgrading any unknown membership instead of
     denying — exactly the two expected guessed-UUID/no-membership checks
     failed (`a guessed org UUID is denied for a read too`, `a principal
     with no Membership row anywhere is denied`); reverted, confirmed
     clean the same way.
  5. Full regression, this sandbox (`uv run ruff check .` / `uv run ruff
     format --check .` / `uv run mypy .` clean — the plain, non-`uv run`
     `ruff` on this machine reports 7 UP042 findings from a newer,
     unpinned ruff version across pre-existing enum classes repo-wide, not
     just this PR's own `Role` enum; the actual CI/pinned invocation
     (`uv run ruff check .`) reports 0, confirmed directly, not a real
     defect); `pnpm install --frozen-lockfile` then `pnpm run lint` /
     `typecheck` / `format:check` clean; `pnpm exec vitest run` 5/5; `uv
     run pytest tests/unit` 3/3; all 17 existing `tests/tasks/
     test_ftf_*.py` files re-run clean; `python3 scripts/validate_spec.py
     --strict` clean (the first run, before `git fetch --unshallow`,
     showed 9 stale-commit findings — a shallow-clone artifact of this
     worktree, not a real defect, resolved by unshallowing and re-running
     clean).
  6. CI reconfirmed for real on the actual current head: at first poll 5
     of 7 checks on the original head (`da86a068`) were queued/running; a
     doc-only `PROGRESS.md` commit then moved the head to `801e2d0`
     (`git diff --stat` between the two heads confirmed only `PROGRESS.md`
     changed, no code delta) — re-polled the new head rather than trusting
     the producer's own "to be confirmed independently" checkbox, waited
     for completion rather than a snapshot mid-run: all 7 checks (`gate`,
     `oidc-provider-integration`, `docker-local-stack-integration`,
     `organization-workspace-schema-integration`,
     `audit-event-writer-integration`, `postgres-backup-integration`,
     `storage-semantics-integration`) completed `success` on `801e2d0`
     itself.
  7. `product_truth_check.py --fail-on-unwired` result confirmed
     legitimate: `1/1` — `docs/spec-matrix.yaml`'s FTF-0103
     `proving_symbol` is empty, so this criterion claims no live wiring at
     all (no `/api/v1` route exists yet in this repository to wire it
     into); the `1/1` is the unchanged FTF-0101 `build_oidc_login_router`
     wiring, not an inflated or fabricated pass for this task's own,
     disclosedly-unwired module.
  8. Acceptance clause 1 ("every route/operation maps to explicit role and
     scoped project policy on server") is read as applying to the real
     routes that exist — currently zero, since no `/api/v1` product route
     exists anywhere in this repository yet (confirmed by grep against
     `apps/api/app/main.py`, which only wires FTF-0101's `/auth/*`
     router) — so it is vacuously and disclosedly satisfied at this point
     in the dependency graph, the same treatment already given to
     FTF-0102's own zero-OpenAPI-paths scope. The module's own enforcement
     mechanism is proven end to end against three representative routes
     in a real demonstration app, which is the strongest evidence
     available before a real product route exists to wire it into.
     Acceptance clause 2 and the negative_acceptance clause are both met
     in full, per steps 2-4 above. `docs/spec-matrix.yaml`'s notes read
     accurately against all of the above — every disclosed gap (no live
     Membership table, no live `/api/v1` caller yet, organization-level-
     only scoping, `principal_resolver` decoupling from FTF-0101's
     concrete session implementation) matches the real repository state,
     not silently expanded past what the submission itself disclosed.
- Conclusion: the delivered RBAC enforcement primitive is real,
  independently reproduced against a real local uvicorn demonstration
  server (not an in-process call or mock), genuinely fault-injectable via
  two independent, this-session-chosen breaks covering both named
  negative_acceptance vectors (guessed UUID, body-selected tenant), and CI
  is green on the real final merged head. No blocking defect. Certified
  L2-CERTIFIED.

## FTF-0105 — Tenant-isolation query guards
- Criterion ID: FTF-0105
- PR: https://github.com/SJZLab/Sovra/pull/120
- Merge commit: 14f785c (`14f785c688cd1134dfe110edd73bf71f5df6d708`)
- Validated-by: session https://claude.ai/code/session_01WAStZmrW1dFrLLivEgcmxN
  (session-ID comparison performed and recorded before this entry: all 3
  commits on PR #120 — e5df228, aa1cc7c, 5590ae6 — carry `Claude-Session:
  .../session_01J8DXF7NnsGdpJSPpuN7gCc`; none match this reviewing
  session's own ID, so this is an independent certification, not
  self-certification.)
- Steps performed:
  1. Task contract pulled fresh via `python tools/r1_handoff.py packet
     FTF-0105 --plan FINE_TUNING_FACTORY_RELEASE_1.md`, not taken from the
     PR body alone: acceptance = ["Repository/query layer consistently
     applies authenticated tenant/workspace and transaction-local
     context.", "Independent review, exact merged commit, real relevant
     integration and negative-test evidence recorded."];
     negative_acceptance = ["Connection-pool reuse, background job and raw
     SQL query cannot leak previous tenant rows."]; `database_changes`
     reads "No independent schema change... any required change is a
     separately claimed migration prerequisite" — confirmed directly (the
     module only imports FTF-0102's already-frozen
     `organization_workspace_schema.py`, adding no migration of its own).
  2. Read the full delivered diff directly (`git diff origin/main..PR-120`
     in a dedicated `git worktree` at the real PR head, `5590ae6`):
     `apps/api/app/auth/tenant_isolation_query_guards.py`
     (`tenant_scoped_session()` — fails closed before a session is even
     created when `organization_id` is not a real `uuid.UUID`, binds
     FTF-0102's own `set_tenant_context()` immediately, always
     commits-or-rolls-back and closes; `execute_tenant_scoped()` — reads
     back the session's own live transaction-local context via
     `require_tenant_context()` and refuses to run a raw statement at all
     on mismatch or absence; `install_tenant_pool_reset_guard()` — a real
     SQLAlchemy pool `checkin` event listener force-clearing the tenant
     GUID on every connection returned to the pool), plus
     `tests/tasks/test_ftf_0105.py` (30 no-DB checks) and
     `tests/integration/test_tenant_isolation_query_guards.py` (the real
     live-Postgres proof).
  3. Stood up a real, directly-installed local PostgreSQL 16 cluster in
     this sandbox (no container-registry egress here, same finding as
     FTF-0004/FTF-0102/FTF-0104/FTF-R017/FTF-1601): created the
     `sovra_migrate` superuser role and `sovra_ftf0105` database, then ran
     `SOVRA_DATABASE_URL=postgresql+psycopg://sovra_migrate:...@localhost/
     sovra_ftf0105 SOVRA_APP_DB_PASSWORD=... uv run python3
     tests/integration/test_tenant_isolation_query_guards.py` directly —
     all checks pass, including both Law-1 pool-reset-guard runs (leaks
     without the guard, does not leak with it, against the same real pool
     configuration). Re-ran `uv run python3 tests/tasks/test_ftf_0105.py`
     in a fresh `uv sync --all-packages` environment (after `git fetch
     --unshallow`, needed once in this sandbox) — all checks pass.
  4. **Own independent fault injection** (a vector distinct from the
     producer's own two, which targeted `tenant_scoped_session`'s UUID
     validation and the checkin reset's `set_config` call): edited
     `execute_tenant_scoped` on disk to replace `if active !=
     organization_id:` with `if False:`, so the mismatch check can never
     fire. `tests/tasks/test_ftf_0105.py`'s own no-DB fault-injection check
     (which greps for an exact source snippet to mutate) correctly
     reported its expected snippet missing — an artifact of editing the
     source directly with a different mutation, not a false pass — so this
     fault was instead proven against the real, live-Postgres integration
     suite: re-ran `tests/integration/test_tenant_isolation_query_guards.py`
     against the same real database with the break in place, and exactly
     the expected negative_acceptance check failed
     (`negative_acceptance: a raw SQL query whose caller claims a
     different tenant than the session's real live context is refused
     outright...`); reverted byte-identical (`git diff --stat` confirmed no
     residual change against the original worktree copy), re-ran both
     suites clean.
  5. Full regression, this sandbox: `uv run ruff check .` / `uv run ruff
     format --check .` / `uv run mypy .` clean; `pnpm install
     --frozen-lockfile` then `pnpm run lint` / `typecheck` / `format:check`
     clean; `pnpm run test` (Vitest) 5/5; `uv run pytest tests/unit` 3/3;
     all 17 existing `tests/tasks/test_ftf_*.py` files re-run clean
     (including `test_ftf_h001.py`, whose first run showed 3 failures
     traced directly to `validate_spec.py --strict` reporting 9
     `stale_commit` findings against FTF-0001/0002/0003/0004/0005/0006/
     H001/R001/H002 — a shallow-clone artifact of this review's own
     worktree, not a real defect, confirmed by `git fetch --unshallow`
     clearing every one of them and both re-runs passing clean).
  6. CI reconfirmed for real on the actual current head: at first poll
     both `CI` runs on the PR (`aa1cc7c`, then `5590ae6` after a
     tracking-doc-only commit) were `in_progress`; polled again after
     completion rather than trusting the producer's own "to be confirmed
     independently" checkbox — all 8 checks (`gate`,
     `tenant-isolation-query-guards-integration`,
     `organization-workspace-schema-integration`,
     `oidc-provider-integration`, `audit-event-writer-integration`,
     `postgres-backup-integration`, `docker-local-stack-integration`,
     `storage-semantics-integration`) completed `success` on the real
     final head `5590ae6`; the new
     `tenant-isolation-query-guards-integration` job's own step log
     ("Prove the real connection-pool-reuse/raw-SQL/background-job
     tenant-isolation evidence against the live database") fetched and
     read directly, confirmed `success`, not merely inferred from the
     green check.
  7. `product_truth_check.py --fail-on-unwired` result confirmed
     legitimate: `1/1`, unchanged — `docs/spec-matrix.yaml`'s FTF-0105
     `proving_symbol` is empty, so this criterion claims no live wiring at
     all (`grep` confirms no FastAPI route or background-worker service
     anywhere in this repository imports
     `tenant_isolation_query_guards.py` yet); the `1/1` is the unchanged
     FTF-0101 `build_oidc_login_router` wiring, not an inflated or
     fabricated pass for this task's own, disclosedly-unwired module.
  8. Acceptance clause 1 ("repository/query layer consistently applies
     authenticated tenant/workspace and transaction-local context") is
     proven for real against FTF-0102's own two tenant-scoped tables (the
     only ones that exist in this repository yet), via genuine DBAPI
     connection-identity equality across two independent units of work
     drawn from a pool forced to reuse one connection — not merely
     asserted. Acceptance clause 2 and the negative_acceptance clause
     (connection-pool reuse, background job, raw SQL — all three named
     vectors) are both met in full, per steps 3-4 above.
     `docs/spec-matrix.yaml`'s notes read accurately against all of the
     above — every disclosed gap (no live product caller yet, positive-path
     evidence scoped to FTF-0102's own two tables, the pool-reset guard not
     yet wired to a real application engine's startup) matches the real
     repository state, not silently expanded past what the submission
     itself disclosed.
  9. Ran `sync_certified_status()` (`scripts/task_claim.py`) after setting
     `certification_state: CERTIFIED` — diff limited to exactly FTF-0105's
     own `status`/`evidence` fields in `spec/tasks.json` and
     `docs/implementation/TRACKER.json`, confirmed via `git diff`. Added
     FTF-0105's entry to `spec/verification-map.json` (same two-commit
     pattern as every prior certification); `validate_spec.py --strict`
     clean, `verify_task.py --task FTF-0105 --stage task` -> `PASS`,
     `verify_release.py` confirmed non-inflated (`21/300` before this
     certification, `22/300`/zero FAILED after — the pre-existing FAILED
     entries surfaced mid-review for FTF-0002/FTF-0003/FTF-H001 were traced
     to this sandbox's Python venv not yet having `pnpm install`/`uv sync
     --all-packages` run against it, not a real regression; both installed,
     all three re-ran `PASS`).
- Conclusion: the delivered tenant-isolation query-layer guard is real,
  independently reproduced against a real local PostgreSQL 16 cluster (not
  a mock or SQLite stand-in, which has no row-level security at all),
  genuinely fault-injectable via an own, this-session-chosen break distinct
  from the producer's own two and caught for real by the live-Postgres
  integration suite, and CI is green on the real final merged head (8/8).
  No blocking defect. Certified L2-CERTIFIED.

## FTF-H003 — Cross-layer tenant isolation and relational identity
- Criterion ID: FTF-H003
- PR: https://github.com/SJZLab/Sovra/pull/125
- Merge commit: 9407710 (`94077104515f85e0ba776a38a2d058413d2fcaed`)
- Validated-by: session https://claude.ai/code/session_01Xpih2RaGXTP37K2PM6JYAV
  (session-ID comparison performed and recorded before this entry: all 3
  commits on PR #125 — 6242412, 04ad18b, 312e9de — carry `Claude-Session:
  .../session_013xKMdEcTrkvS1WEQUwDWxV`; none match this reviewing
  session's own ID, so this is an independent certification, not
  self-certification.)
- Steps performed:
  1. Task contract pulled fresh via `python tools/r1_handoff.py packet
     FTF-H003 --plan FINE_TUNING_FACTORY_RELEASE_1.md`, not taken from the
     PR body alone: acceptance = ["Use two actual organizations and pooled
     DB sessions to prove cross-tenant read/write/download/job/route
     denial including guessed IDs and missing context.", "No full release
     claim until all mapped scenarios and exact-revision evidence pass."];
     negative_acceptance = ["Migration role cannot be used by the API or
     worker; test views, background activities and object presigned URLs,
     not API guards alone."]; dependencies FTF-0102/FTF-0103/FTF-0104/
     FTF-H001 confirmed CERTIFIED in `docs/spec-matrix.yaml` before review.
  2. Read the full delivered diff directly (`git diff origin/main...PR-125`
     against the real PR head, `312e9de`, in a dedicated `git worktree`):
     `apps/api/app/db/tenant_context.py` (`TenantContext`, only ever built
     from a real `AuthorizedContext` via `from_authorized_context()`),
     `apps/api/app/auth/resource_authorization.py`
     (`authorize_resource_ownership`, the IDOR guard, querying through
     FTF-0105's own `execute_tenant_scoped`), and
     `packages/storage/tenant_policy.py` (tenant-scoped object-key
     build/validate, fail-closed on path-traversal/foreign-tenant keys).
     No production mock, fake integration or placeholder route found.
  3. Stood up a real, directly-installed local PostgreSQL 16 cluster in
     this sandbox (no container-registry egress here, same finding as
     FTF-0004/FTF-0102/FTF-0104/FTF-0105/FTF-R017/FTF-1601): created the
     `sovra_migrate` superuser role and `sovra_ftfh003` database, then ran
     `SOVRA_DATABASE_URL=postgresql+psycopg://sovra_migrate:...@localhost/
     sovra_ftfh003 SOVRA_APP_DB_PASSWORD=... uv run python3
     tests/integration/test_ftf_h003_cross_layer_isolation.py` directly —
     all checks pass (read/write/download/job/route denial, plus missing
     context). Re-ran `uv run python3 tests/tasks/test_ftf_h003.py` in a
     fresh `uv sync --all-packages` environment (after `git fetch
     --unshallow`, needed once in this sandbox) — all checks pass.
  4. **Own independent fault injection, two vectors distinct from the
     producer's own** (which targeted `resource_authorization.py`'s
     foreign-tenant-row check via a no-DB fault injection only):
     (a) edited `packages/storage/tenant_policy.py`'s
     `validate_object_key_belongs_to_tenant` to `return True`
     unconditionally instead of `return key_organization_id ==
     organization_id` — `tests/tasks/test_ftf_h003.py`'s own
     `test_validate_object_key_denies_a_guessed_foreign_tenant_prefix`
     negative check correctly failed; reverted byte-identical (`git diff
     --stat` clean), re-ran clean.
     (b) edited `apps/api/app/auth/resource_authorization.py`'s own `if
     row is None or row[0] != context.organization_id:` down to `if row is
     None:` (dropping the app-layer foreign-tenant-row half only) and
     re-ran the live-Postgres integration suite with the break in place —
     this did **not** flip any check. Traced directly, not waved off: with
     `authorize_resource_ownership` querying through FTF-0105's own
     `execute_tenant_scoped`, and FTF-0102's tables under `FORCE ROW LEVEL
     SECURITY`, a foreign-tenant row is filtered out of the query result
     by Postgres itself before the row ever reaches the app-layer
     comparison — `row is None` alone still denies it. This confirms the
     dropped comparison is genuine defense-in-depth (the disclosed
     "inheriting FTF-0102's FORCE-RLS enforcement" framing in this task's
     own notes), not a redundant no-op and not a coverage gap: it is the
     literal branch the no-DB mocked suite (vector 4a here, and the
     producer's own no-DB fault injection) exercises for the scenario
     where RLS itself is bypassed or misconfigured, which a live-RLS-backed
     integration run cannot itself simulate. Reverted byte-identical,
     re-ran both suites clean.
     Also tried a third vector for completeness — removing
     `validate_object_key_belongs_to_tenant`'s leading `".."`/absolute-
     path/backslash/null-byte reject line — and confirmed it is genuinely
     redundant given the function's own later checks (an exact 5-segment
     split plus per-segment UUID/regex validation already rejects every
     `bad_keys` case in the existing test), not a missed test: recorded
     here for transparency rather than treated as a finding, since no
     acceptance/negative_acceptance clause depends on that specific line
     alone.
  5. Full regression, this sandbox: `uv run ruff check .` clean; `uv run
     mypy .` clean (81 source files); `pnpm install` then `pnpm run lint`
     / `typecheck` / `format:check` clean; `uv run pytest tests/unit` 3/3;
     all 20 existing `tests/tasks/test_ftf_*.py` files re-run clean
     (`test_ftf_0002.py`/`test_ftf_0003.py`/`test_ftf_h001.py` first showed
     failures traced directly to this sandbox's `node_modules` not yet
     being installed, cleared by `pnpm install`, all three re-ran `PASS`).
     `python3 scripts/validate_spec.py --strict` first showed 10
     `stale_commit` defects, traced to this review's own shallow worktree
     clone (same class already documented for FTF-0105's own review),
     cleared by `git fetch --unshallow`.
  6. CI reconfirmed for real on the actual current head, not the
     producer's own self-report: at first poll `gate` and
     `docker-local-stack-integration` were still `in_progress`; polled
     again after completion — all 9 checks (`gate`,
     `ftf-h003-cross-layer-isolation-integration`,
     `tenant-isolation-query-guards-integration`,
     `organization-workspace-schema-integration`, `oidc-provider-integration`,
     `audit-event-writer-integration`, `postgres-backup-integration`,
     `docker-local-stack-integration`, `storage-semantics-integration`)
     completed `success` on the real final head `312e9de`; `mergeable_state`
     confirmed `clean` before merge.
  7. `product_truth_check.py --fail-on-unwired` result confirmed
     legitimate: `1/1`, unchanged — `docs/spec-matrix.yaml`'s FTF-H003
     `proving_symbol` is empty, so this criterion claims no live wiring at
     all; `grep` confirms no FastAPI route or background-worker service
     anywhere in this repository imports `tenant_context.py`,
     `resource_authorization.py` or `tenant_policy.py` yet — the `1/1` is
     the unchanged FTF-0101 wiring, not an inflated or fabricated pass for
     this task's own, disclosedly-unwired modules. `verify_release.py`
     (after activating `.venv`, the same `python3` vs. `uv`-managed-venv
     artifact this task's own submission already documented) reports
     `22/300`, zero `FAILED`, matching the producer's own claim.
  8. Acceptance clause 1 is proven for real against two real seeded
     organizations and pooled sessions, live against Postgres — read,
     write (rejected by FTF-0102's own RLS `WITH CHECK` policy, a real
     database error), download (storage-key ownership validation), job
     (a naively-opened session bypassing `tenant_scoped_session_for`) and
     route (a real uvicorn server combining `require_role` +
     `authorize_resource_ownership`) denial, each including guessed IDs,
     plus missing-context denial via FTF-0105's own
     `require_tenant_context`. Acceptance clause 2 makes no full-release
     claim here (this is a single-task L2 certification, not a release
     gate — `verify_release.py` above confirms the release itself stays
     `NOT READY`). The negative_acceptance clause's migration-role/app-role
     separation is met (every check in the live suite runs as `sovra_app`;
     only seeding uses the migration-owner role) and its background-job
     vector is met (the "job" case above); its "object presigned URLs"
     phrase is honestly scoped down to storage-key ownership validation
     only, since no live presigned-URL-issuing route exists anywhere in
     this repository yet — disclosed accurately in this task's own
     `docs/spec-matrix.yaml` notes, the same disclosed-gap class already
     accepted for FTF-0105's own no-live-caller scope, not silently
     expanded past what the submission itself disclosed.
  9. Ran `scripts.task_claim.sync_certified_status()` after setting
     `certification_state: CERTIFIED` — diff limited to exactly FTF-H003's
     own `status`/`evidence` fields in `spec/tasks.json` and
     `docs/implementation/TRACKER.json`, confirmed via `git diff`. Added
     FTF-H003's entry to `spec/verification-map.json` (same two-commit
     pattern as every prior certification); `validate_spec.py --strict`
     clean, `verify_task.py --task FTF-H003 --stage task` -> `PASS`.
  10. This certification's own doc updates (this ledger entry,
      `docs/spec-matrix.yaml`, `QUEUE.md`, `PROGRESS.md`,
      `spec/verification-map.json`, `spec/tasks.json`,
      `docs/implementation/TRACKER.json`) are pushed on a branch and
      opened as a PR against `main`, not pushed directly — the
      `BRANCH_PROTECTION` instruction-tier norm in `CLAUDE.md` exists
      specifically because two prior certification commits
      (`docs/certifications/watchdog-findings.md`'s 2026-09-15 and
      2026-09-18 findings) landed on `main` by direct push, and this
      review found every certification-record commit since — including
      FTF-0101/FTF-0103/FTF-0105's own (`d4a2280`, `1873f17`, `8c602f8`,
      each a single-parent commit with no enclosing PR) — repeated that
      same pattern without a new watchdog finding being logged for it.
      Not remediated here (outside this session's CERTIFY-only scope per
      `CLAUDE.md`'s "never touch anything outside certification"), but
      recorded here for visibility rather than silently repeated a fourth
      time.
- Conclusion: the delivered cross-layer IDOR guard (`TenantContext`,
  `authorize_resource_ownership`, tenant-scoped storage-key policy) is
  real, independently reproduced against a real local PostgreSQL 16
  cluster under real `FORCE ROW LEVEL SECURITY`, genuinely fault-injectable
  via two own, this-session-chosen breaks distinct from the producer's own
  one and each correctly caught or explained, and CI is green on the real
  final merged head (9/9). No blocking defect. Certified L2-CERTIFIED.

## FTF-0106 — RBAC/IDOR security tests
- Criterion ID: FTF-0106
- PR: https://github.com/SJZLab/Sovra/pull/127
- Merge commit: e21df28 (`e21df287193bebe0feaa4ad77ad0b030d28c3a4f`)
- Validated-by: session https://claude.ai/code/session_01YYH2AJhFMjzPnDbKw3C44c
  (session-ID comparison performed and recorded before this entry: all 4
  commits on PR #127 — 9fe1957, ea8e963, ba8b3fe, 6b226ee — carry
  `Claude-Session: .../session_01J8DXF7NnsGdpJSPpuN7gCc`; none match this
  reviewing session's own ID, so this is an independent certification, not
  self-certification.)
- Steps performed:
  1. Task contract pulled fresh via `python tools/r1_handoff.py packet
     FTF-0106 --plan FINE_TUNING_FACTORY_RELEASE_1.md`, not taken from the
     PR body alone: acceptance = ["Full real two-tenant role matrix
     exercises reads/writes/artifacts/approvals with guessed IDs.",
     "Independent review, exact merged commit, real relevant integration
     and negative-test evidence recorded."]; negative_acceptance = ["No
     authorization test exclusively uses privileged DB connection or
     mocked membership result."]; dependencies FTF-0102/FTF-0103/FTF-0104/
     FTF-0105/FTF-H003 confirmed CERTIFIED in `docs/spec-matrix.yaml`
     before review.
  2. Read the full delivered diff directly (8 files, +1242/-23, against
     the real PR head `6b226ee` in a dedicated `git worktree`):
     `apps/api/app/auth/rbac_idor_security_tests.py`
     (`TwoTenantRoleMatrixFixture`, twelve real (issuer, subject)
     principals across two organizations and all six Section 6.4 roles;
     `build_two_tenant_security_demo_app`, wiring FTF-0103's `require_role`
     and FTF-H003's `authorize_resource_ownership`/`TenantContext`/
     `tenant_scoped_session_for` across read/write/approve routes) plus
     `tests/tasks/test_ftf_0106.py` and
     `tests/integration/test_ftf_0106_rbac_idor_security.py`. Confirmed
     directly in `apps/api/app/auth/rbac_middleware.py` that `require_role`
     genuinely accepts the `principal_resolver`/`role_lookup` overrides the
     harness passes — not a hallucinated signature.
  3. Ran both delivered suites myself in the worktree: `tests/tasks/
     test_ftf_0106.py` (23 no-DB checks) — all passed. For the live suite,
     stood up a real local PostgreSQL 16 cluster (`sovra_migrate_review`
     role, `sovra_ftf0106_review` database — this sandbox has no
     container-registry egress, same finding as FTF-0004/FTF-0102/
     FTF-0104/FTF-0105/FTF-H003/FTF-R017), ran `tests/integration/
     test_ftf_0106_rbac_idor_security.py` (75 checks, self-migrating via
     `alembic upgrade head`) — all passed. Confirmed directly in the test
     source that the demo app's session engine binds to the real `sovra_app`
     role (`app_engine.url.username == APP_ROLE_NAME`), never the migration
     owner — the negative_acceptance clause is genuinely proven, not
     asserted by convention.
  4. Two own fault injections, distinct from the producer's own two:
     (a) widened `APPROVE_ROLES` to include `Role.VIEWER` (a real
     privilege-escalation bug — a viewer could approve) — caught for real
     by the no-DB suite's own independently-hardcoded role-set check (4
     checks failed: `APPROVE_ROLES is exactly approver/organization_admin`
     and three downstream checks); the live suite did NOT catch it, for
     the same disclosed reason already documented for `WRITE_ROLES` (the
     integration suite imports the same constant it exercises, so a
     semantic change to the constant moves both sides together — a sound,
     disclosed split of coverage, confirmed genuinely to hold for an
     independently-chosen fault, not just the producer's own one).
     (b) aliased the `approve` route's dependency to `write_dependency`
     instead of `approve_dependency` (a real wiring bug) — the no-DB suite
     did NOT catch it (it exercises role-set semantics, not per-route
     wiring), but the live-Postgres integration suite caught it for real:
     12 checks flipped (every org/approver and org/workspace_admin,
     org/ml_engineer approve-operation expectation). Both faults reverted
     byte-identical (`git diff --stat apps/api/app/auth/
     rbac_idor_security_tests.py` clean) and both suites re-ran clean
     afterward.
  5. Full local regression in the worktree: `uv sync --all-packages` then
     `ruff check .` / `ruff format --check .` / `mypy .` clean (84 source
     files); `pnpm install --frozen-lockfile` then `pnpm run lint` /
     `typecheck` / `format:check` clean; `pnpm exec vitest run` 3 files/5
     tests passed; `uv run pytest tests/unit` 3 passed. All existing
     `tests/tasks/test_ftf_*.py` re-run clean, after one real, disclosed
     artifact: `test_ftf_h001.py` and `validate_spec.py --strict` first
     showed 10 `stale_commit` findings, traced directly to this review's
     own shallow worktree clone (`git rev-parse --is-shallow-repository`
     → true) rather than a real defect, cleared by `git fetch --unshallow`
     and both re-ran clean afterward.
  6. CI reconfirmed for real on the actual current head, not the
     producer's own self-report: polled `pull_request_read`
     `get_check_runs` until completion (three jobs — `gate`,
     `storage-semantics-integration`, `docker-local-stack-integration` —
     were still `in_progress` on first poll); all 10 checks (`gate`,
     `rbac-idor-security-integration`, `ftf-h003-cross-layer-isolation-
     integration`, `tenant-isolation-query-guards-integration`,
     `organization-workspace-schema-integration`, `oidc-provider-
     integration`, `audit-event-writer-integration`,
     `postgres-backup-integration`, `docker-local-stack-integration`,
     `storage-semantics-integration`) completed `success` on the real
     final head `6b226ee`; `mergeable_state` confirmed `clean` before
     merge.
  7. `product_truth_check.py --fail-on-unwired` result confirmed
     legitimate: `1/1`, unchanged — `docs/spec-matrix.yaml`'s FTF-0106
     `proving_symbol` is empty, so this criterion claims no live wiring at
     all; `grep` confirms no FastAPI route or background-worker service
     anywhere in this repository imports `rbac_idor_security_tests.py` —
     the `1/1` is the unchanged FTF-0101 wiring, not an inflated or
     fabricated pass for this task's own, disclosedly-unwired module.
     `verify_release.py` (after `pnpm install`, the same not-yet-installed
     `node_modules` artifact this task's own submission already
     documented) reports `24/300`, zero `FAILED`, matching the producer's
     own claim once the artifact was cleared.
  8. Acceptance clause 1 is proven for real against two real seeded
     organizations, twelve real (issuer, subject) principals covering all
     six Section 6.4 roles, and a real uvicorn server — read/write/approve
     denial for every (organization, role) pair against both the caller's
     own real workspace and the other organization's real (guessed/
     foreign) workspace ID, worded identically (404) to a missing
     resource whenever the role gate itself would have allowed the
     operation. "Artifacts/approvals" is honestly scoped down to an
     approval-shaped role gate against the real `workspaces` table, since
     no live `ModelVersion`/`PromotionDecision`/artifact table exists
     anywhere in this repository yet (Section 6.20/6.21 are later-phase
     tasks, and this task's own `database_changes` contract forbids an
     independent schema change) — the same disclosed scoping precedent
     FTF-0105/FTF-H003 already established and were certified against,
     not silently expanded past what the submission itself disclosed.
     Acceptance clause 2 makes no full-release claim here (this is a
     single-task L2 certification, not a release gate — `verify_release.py`
     above confirms the release itself stays `NOT READY`). The
     negative_acceptance clause is met per step 3 above.
  9. Ran `scripts.task_claim.sync_certified_status()` after setting
     `certification_state: CERTIFIED` — diff limited to exactly FTF-0106's
     own `status`/`evidence` fields in `spec/tasks.json` and
     `docs/implementation/TRACKER.json`, confirmed via `git diff`. Added
     FTF-0106's own entry to `spec/verification-map.json` (`commit` set to
     the implementation commit `9fe1957`, the same convention FTF-0105's
     own entry used — not the merge commit; `evidence_hash` in
     `docs/spec-matrix.yaml` left unchanged at `9fe1957` for the same
     reason). `validate_spec.py --strict` clean; `verify_task.py --task
     FTF-0106 --stage task` → `PASS`.
  10. Pre-existing inconsistency noticed, not this submission's own and not
      remediated here (outside this session's CERTIFY-only scope): FTF-H003's
      own `docs/spec-matrix.yaml` `evidence_hash` (`9407710...`, the PR #125
      merge commit) does not match its own `spec/verification-map.json`
      `commit` field (`6242412...`, the implementation commit) — the file's
      own documented convention says `commit` should equal the evidence_hash
      "at the time this entry was written". FTF-0106's own entries (this
      certification) use the implementation-commit convention throughout,
      consistent with FTF-0105/FTF-0103/FTF-0101 before it. Flagged in
      `QUEUE.md` for the next Control-plane shift, not fixed here.
  11. This certification's own doc updates (this ledger entry,
      `docs/spec-matrix.yaml`, `QUEUE.md`, `PROGRESS.md`,
      `spec/verification-map.json`, `spec/tasks.json`,
      `docs/implementation/TRACKER.json`) are pushed on a branch and
      opened as a PR against `main`, not pushed directly, per
      `BRANCH_PROTECTION` in `CLAUDE.md`.
- Conclusion: the delivered full six-role, two-tenant RBAC/IDOR security
  harness (`TwoTenantRoleMatrixFixture`, `build_two_tenant_security_demo_app`)
  is real, independently reproduced against a real local PostgreSQL 16
  cluster, genuinely fault-injectable via two own, this-session-chosen
  breaks distinct from the producer's own two and each correctly caught
  by the coverage layer the disclosed design says should catch it, and CI
  is green on the real final merged head (10/10). No blocking defect.
  Certified L2-CERTIFIED.
