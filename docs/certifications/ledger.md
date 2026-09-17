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
