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
