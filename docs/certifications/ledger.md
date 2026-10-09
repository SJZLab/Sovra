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

## FTF-0201 — LiteLLM connection CRUD
- Criterion ID: FTF-0201
- PR: https://github.com/SJZLab/Sovra/pull/133
- Merge commit: 3588e4e (`3588e4e5e8f0fcb81f0f458869f2d1e252ea1305`)
- Validated-by: session https://claude.ai/code/session_01RLyWKThYBPJkaqeDp2HZsN
  (session-ID comparison performed and recorded before this entry: all 7
  commits on PR #133 — b7756b3, e445185, 417477e, 37ec61c, 2ed384b, 96eac79,
  1815080, d0aec3b — carry `Claude-Session: .../session_01MV1wn1CJHoHmds4eRKKVaV`;
  none match this reviewing session's own ID, so this is an independent
  certification, not self-certification.)
- Steps performed:
  1. Task contract pulled fresh via `python tools/r1_handoff.py packet
     FTF-0201 --plan FINE_TUNING_FACTORY_RELEASE_1.md`, not taken from the
     PR body alone: acceptance = ["Freeze the minimum shared phase
     interface and domain schemas at the declared owner paths, with
     schema/client validation and no placeholder endpoints. Then:
     Create/edit/revoke an organization-scoped gateway connection with
     secret refs and explicit TLS/URL policy.", "Independent review, exact
     merged commit, real relevant integration and negative-test evidence
     recorded."]; negative_acceptance = ["Foreign connection ID, rejected
     URL or secret echo fails; cannot mutate another organization
     gateway."]; dependencies FTF-0103/FTF-0104/FTF-0005 confirmed
     CERTIFIED in `docs/spec-matrix.yaml` before review. Confirmed the
     disclosed scope narrowing (create/get/list/edit/revoke only, no
     validate/sync-models/`/models`/`/models/{id}/probe`) is genuinely
     justified: those require a real call to the organization's actual
     LiteLLM gateway and are FTF-0203/FTF-0204/FTF-0210's own, later,
     separately dependency-gated tasks per `docs/TASK_INDEX.md` — declaring
     that surface here with no real backing handler would itself be the
     "placeholder endpoint" Law 11 and this task's own acceptance clause
     forbid, not scope creep in the certifier's favor.
  2. Read the full delivered diff directly (16 files, +3533/-22, against
     the real PR head `d0aec3b`): `apps/api/app/litellm/connection_schema.py`
     (table + `validate_base_url`/`validate_secret_ref`),
     `apps/api/alembic/versions/0003_litellm_connections.py` (confirmed
     directly: `GRANT SELECT, INSERT, UPDATE` — no DELETE — plus
     `ALTER TABLE ... FORCE ROW LEVEL SECURITY` and a real
     `CREATE POLICY tenant_isolation`), `apps/api/app/litellm/connection_crud.py`
     (composition of `TenantContext`/`authorize_resource_ownership`
     (FTF-H003), `execute_tenant_scoped` (FTF-0105), `write_audit_event`
     (FTF-0104) — confirmed these are the real imported functions, not a
     hand-rolled duplicate), `apps/api/app/litellm/connection_routes.py`
     and `apps/api/app/main.py` (confirmed `build_litellm_connection_router`
     is wired unconditionally via `app.include_router(...)` in
     `create_app()`, the second live-wired product-runtime path after
     FTF-0101), and the `packages/contracts/{openapi,jsonschema}/02-*`
     freeze (`LiteLLMConnection` read schema confirmed to carry no
     `secret_ref` property, `additionalProperties: false`).
  3. Confirmed PR #133 was up to date with `main` (base `dee3931` an
     ancestor of the PR head; `mergeable_state: clean`, no conflict to
     resolve).
  4. Ran both delivered suites myself, not on the producer's word: stood up
     a real local PostgreSQL 16 cluster (`sovra_migrate`/`sovra_ftf0201`,
     the same role/db names CI's own `litellm-connection-crud-integration`
     job uses) after `git fetch --unshallow` and `uv sync --all-packages`.
     `uv run python3 tests/tasks/test_ftf_0201.py` → "all checks passed"
     (run the way CI actually invokes it — not `pytest`, which would
     silently under-report: this file's `check()` helper only appends to a
     module-level `FAILURES` list and the `test_*` functions never
     `assert`, so `pytest` reports "24 passed" regardless of whether the
     underlying checks actually held — noted here so a future reviewing
     session does not mistake `pytest`'s count for the real signal).
     `uv run python3 tests/integration/test_ftf_0201_litellm_connection_crud.py`
     → "all checks passed" (28 real live-Postgres checks, including the
     core IDOR case denied identically to missing, and the restricted
     `sovra_app` role independently confirmed unable to issue a raw DELETE
     against `litellm_connections`).
  5. One own fault injection, distinct from the producer's own two:
     widened `validate_base_url`'s allowed scheme set from `("https",)` to
     `("https", "http")` — a real TLS-downgrade bug (Section 3.1/65.4
     requires https-only). Caught for real by both suites: 3 checks failed
     in the no-DB suite (the named http-scheme rejection case plus the two
     downstream checks that depend on it), 1 check failed in the
     live-Postgres suite (the creation-time 400-rejection check). Reverted
     byte-identical (`git diff --stat` clean before/after) and both suites
     re-ran clean afterward.
  6. Also tried removing the `authorize_resource_ownership` call from
     `_fetch_or_raise` entirely (a plausible IDOR-guard-removal fault) —
     neither suite caught it. Traced this to a real, non-defective reason
     before concluding anything: `FORCE ROW LEVEL SECURITY` plus
     `execute_tenant_scoped`'s own RLS-bound session already blocks the
     cross-tenant row at the database level regardless of the
     application-layer check, so `authorize_resource_ownership` here is
     defense-in-depth on top of a DB-level guarantee, not the sole IDOR
     control — removing it left the observable behavior (a `404`-shaped
     `ConnectionNotFoundError`) unchanged. Not a defect; disclosed in
     `docs/spec-matrix.yaml`'s own FTF-0201 notes for the next session
     rather than silently dropped.
  7. Reconfirmed CI green on the real final PR head via the GitHub Actions
     API directly (not the PR body's claim): all 11 check runs on
     `d0aec3b2cd5862bb046104d4e8ca822bc48b10a9` (run `35496794399`)
     completed with `conclusion: success`, including the dedicated
     `litellm-connection-crud-integration` job.
  8. Full regression, this session's own sandbox: `ruff check .` /
     `ruff format --check .` / `mypy .` clean; `pnpm run lint` /
     `typecheck` / `format:check` clean; `pnpm exec vitest run` — 5/5;
     `uv run pytest tests/unit` — 3/3; every existing
     `tests/tasks/test_ftf_*.py` file re-run clean via `uv run python3
     <file>`; `python3 scripts/validate_spec.py --strict` clean after
     `git fetch --unshallow` (same pre-existing shallow-clone artifact
     FTF-0106's own certifying session already documented); `sha256sum
     CLAUDE.md` matches the committed `CLAUDE.md.sha256`.
  9. `product_truth_check.py --fail-on-unwired` independently reconfirmed
     `2/2` product-true (`build_oidc_login_router` plus
     `build_litellm_connection_router`, both confirmed genuinely imported
     and called unconditionally from `apps/api/app/main.py`'s
     `create_app()` by direct source read, not merely re-running the
     script and trusting its own claim).
  10. Merged PR #133 (merge commit `3588e4e5e8f0fcb81f0f458869f2d1e252ea1305`).
      Set `docs/spec-matrix.yaml` FTF-0201 to `certification_state:
      CERTIFIED` / `proof_state: L2-CERTIFIED` (`evidence_hash` kept at the
      implementation commit `1815080`, matching
      `spec/verification-map.json`'s own new `commit` field and the
      FTF-0105/FTF-H003/FTF-0106 precedent of pointing at the
      implementation commit rather than the merge commit). Added
      FTF-0201's own entry to `spec/verification-map.json` (the no-DB
      `tests/tasks/test_ftf_0201.py` command — `verify_task.py --task
      FTF-0201 --stage task` reconfirmed `PASS` after adding it — the
      live-Postgres evidence is disclosed as CI-only/not locally
      re-executable by `verify_task.py` in this sandbox, same as
      FTF-0004/FTF-R017/FTF-0102/FTF-0104/FTF-0105/FTF-1601/FTF-H003/
      FTF-0106). Ran `sync_certified_status()` to write the real CERTIFIED
      status/evidence into `spec/tasks.json` and
      `docs/implementation/TRACKER.json`.
  11. This certification's own doc updates (this ledger entry,
      `docs/spec-matrix.yaml`, `QUEUE.md`, `PROGRESS.md`,
      `spec/verification-map.json`, `spec/tasks.json`,
      `docs/implementation/TRACKER.json`) are pushed on a branch and
      opened as a PR against `main`, not pushed directly, per
      `BRANCH_PROTECTION` in `CLAUDE.md`.
- Conclusion: the delivered organization-scoped LiteLLM gateway connection
  CRUD (create/get/list/edit/revoke) is real, live-wired into
  `apps/api/app/main.py`, independently reproduced against a real local
  PostgreSQL 16 cluster, genuinely fault-injectable via an own,
  this-session-chosen break distinct from the producer's own two and
  correctly caught by both suites, with CI green on the real final merged
  head (11/11) and `product_truth_check.py` legitimately reporting `2/2`.
  No blocking defect. Certified L2-CERTIFIED.

## FTF-0301 — TrainableArtifact schema/API
- Criterion ID: FTF-0301
- PR: https://github.com/SJZLab/Sovra/pull/137 (supersedes #135, closed)
- Merge commit: 634d5977 (`634d59775253600a83fb0ff7c94793fa4fbb89de`)
- Validated-by: session https://claude.ai/code/session_01MWFKEi3BV8s5pzu4uymi5v
  (session-ID comparison performed and recorded before this entry: all 6
  commits on PR #137 — 0f50c1e, 30ba359, 0dd3473, d0bba9b, 86397f1, 9bc4923
  — carry `Claude-Session: .../session_01UFUfcwwet85jUWc3BNVoJZ` or
  `.../session_011RJ3Ct8ASzprr1uy78r6po`; neither matches this certifying
  session's own ID, so this is an independent certification, not
  self-certification.)
- Prior submission (PR #135, first attempt at this task): independently
  reviewed by a different Control-plane session and found **NOT
  CERTIFIED** — a real Law 1 gap, not a production defect (full review:
  `docs/certifications/queue.md`'s 2026-09-20 "FTF-0301 (PR #135) — NOT
  CERTIFIED" entry). The live-Postgres integration suite's own mark-ready
  sequence accepted the license immediately at step 2 of its sequence and
  never revisited it while `weights_digest`/`base_model_revision` were
  already resolved, so the "unknown owner" leg of the task's three-part
  `negative_acceptance` clause was never isolated against a real database
  the way the mutable-revision leg deliberately already was — a fault
  disabling only the license-acceptance check was invisible to that suite,
  caught only by the no-DB suite. The shipped production logic itself was
  already confirmed correct; this was a test-coverage gap on one leg of
  the acceptance criterion's own live-database evidence. PR #135 was left
  open, unmerged, `docs/spec-matrix.yaml` never touched on `main` for it.
- Fix (PR #137): a relay shift added exactly the one isolated live-Postgres
  case the NOT CERTIFIED review named — a separate artifact with
  `weights_digest` resolved and `base_model_revision` pinned via two
  independent `PATCH` calls, `accept-license` never called, asserting
  `mark-ready` still returns 409, then accepting the license and asserting
  `mark-ready` succeeds (200) — mirroring the mutable-revision isolation
  already present. No production code changed. Opened as a new PR
  superseding #135 (the producing session's harness assigns a fresh branch
  per shift and cannot push to a different branch), matching this repo's
  own established supersession precedent (FTF-1601's #90→#96→#100 chain,
  FTF-0101's #103→#112→#114 chain).
- Steps performed (this certification, against PR #137):
  1. Task contract pulled fresh via `python tools/r1_handoff.py packet
     FTF-0301 --plan FINE_TUNING_FACTORY_RELEASE_1.md`, not taken from the
     PR body alone: acceptance = ["Freeze the minimum shared phase
     interface and domain schemas at the declared owner paths, with
     schema/client validation and no placeholder endpoints. Then: Register
     versioned artifact URI/revision/license with source grants and
     immutable identity after resolution.", "Independent review, exact
     merged commit, real relevant integration and negative-test evidence
     recorded."]; negative_acceptance = ["Unsafe URI, unknown owner or
     mutable unresolved revision cannot enter training as ready."], three
     independent conditions; dependencies FTF-0103/FTF-0104 confirmed
     genuinely `CERTIFIED` in `docs/spec-matrix.yaml` before relying on
     them. Confirmed the disclosed scope narrowing (no real `source_uri`
     resolution against Hugging Face Hub/S3/local filesystem/private
     registry, no real `weights_digest` computation) is genuinely
     FTF-0302/FTF-0303/FTF-0304/FTF-0306's own, later, separately
     dependency-gated scope, not a hidden cut — declaring that surface
     here with no real backing call would itself violate Law 11.
  2. Read the full delivered diff directly (18 files, +6610/-19, against
     the real PR head `9bc4923`, workspace already checked out to it by
     the triggering `pull_request.opened` webhook):
     `apps/api/app/artifacts/{artifact_schema,artifact_crud,artifact_routes}.py`,
     `apps/api/alembic/versions/0004_trainable_artifacts.py`,
     `apps/api/app/main.py`, `packages/contracts/openapi/03-interface.yaml`,
     `packages/contracts/jsonschema/03-domain.schema.json`,
     `tests/tasks/test_ftf_0301.py`,
     `tests/integration/test_ftf_0301_trainable_artifact_schema_api.py`,
     `.github/workflows/ci.yml`, tracking docs. Confirmed directly in
     `apps/api/app/main.py`: `build_trainable_artifact_router` wired
     unconditionally via `app.include_router(...)` in `create_app()` (the
     third live-wired product-runtime path). Confirmed directly in
     `apps/api/alembic/versions/0004_trainable_artifacts.py`: real
     `GRANT SELECT, INSERT, UPDATE` (no DELETE), `FORCE ROW LEVEL
     SECURITY`, and a real `CREATE POLICY tenant_isolation`. Confirmed
     directly the new isolated test case in
     `tests/integration/test_ftf_0301_trainable_artifact_schema_api.py`
     matches the PR's own claim exactly — no production code touched by
     this fix, `apply_mark_ready` itself byte-identical to PR #135's own
     head.
  3. Merge-readiness confirmed before merge: PR #137 up to date with
     `main` (base `257a0a0` an ancestor of the PR head), `mergeable_state:
     clean`.
  4. CI reconfirmed on the real final head via `pull_request_read
     get_check_runs`: 12/12 `completed`/`success` on `9bc4923` (run
     `35506579292`), including `trainable-artifact-schema-api-integration`.
  5. Own reproduction, this sandbox: `git fetch --unshallow` first
     (shallow clone, same pre-existing artifact every prior certifying
     session's own ledger entry already documents). Stood up a real,
     directly-installed local PostgreSQL 16 cluster
     (`sovra_migrate_cert`/`sovra_ftf0301_cert` — no container-registry
     egress in this sandbox, same finding as every prior
     integration-tested task). `uv run python3 tests/tasks/test_ftf_0301.py`
     → "all checks passed"; `uv run python3
     tests/integration/test_ftf_0301_trainable_artifact_schema_api.py` →
     "all checks passed" against the real cluster.
  6. Own fault injection, reproducing the certifier's exact fault from the
     PR #135 NOT CERTIFIED review: in `apply_mark_ready`
     (`apps/api/app/artifacts/artifact_crud.py`), wrapped the
     `license_accepted_by is None or license_accepted_at is None` check in
     `False and (...)`. Re-ran both suites with the fault in place:
     live-Postgres suite — exactly 1 check failed, the new isolated case
     itself ("negative_acceptance: mark-ready is refused (409) with a
     resolved digest and a pinned revision but no license acceptance...");
     no-DB suite — 1 check failed ("negative_acceptance: mark_ready is
     refused with no license acceptance and no resolved weights_digest").
     Reverted byte-identical (`git diff --stat` empty), both suites re-ran
     clean (0 failures each) afterward. This closes the exact Law 1 gap
     the prior review identified: the "unknown owner" leg is now provably
     able to fail against a real, live-Postgres round-trip, isolated from
     the other two conditions.
  7. Full regression, this sandbox (matching CI's own tool invocations
     exactly, `uv run ruff`/`uv run mypy`, not a bare global install):
     `uv run ruff check .` / `uv run ruff format --check .` / `uv run
     mypy .` clean (98 files); `pnpm install --frozen-lockfile` then `pnpm
     run lint` / `typecheck` / `format:check` clean; `pnpm exec vitest
     run` 5/5; `uv run pytest tests/unit` 3/3; all 22
     `tests/tasks/test_ftf_*.py` scripts re-run clean (`uv run python3` for
     the FastAPI/SQLAlchemy-dependent ones); the five other live-Postgres
     integration suites sharing this migrated database
     (`test_organization_workspace_schema`, `test_audit_event_writer`,
     `test_tenant_isolation_query_guards`, `test_ftf_h003_cross_layer_isolation`,
     `test_ftf_0106_rbac_idor_security`, `test_ftf_0201_litellm_connection_crud`)
     all re-ran clean, confirming no regression from the merge;
     `validate_spec.py --strict` clean (after unshallowing); `sha256sum
     CLAUDE.md` matches the committed `CLAUDE.md.sha256`.
  8. `product_truth_check.py --fail-on-unwired` independently reconfirmed
     `3/3` (`build_oidc_login_router`, `build_litellm_connection_router`,
     `build_trainable_artifact_router`, all confirmed genuinely imported
     and called unconditionally from `apps/api/app/main.py`'s
     `create_app()` by direct source read).
  9. Added FTF-0301's own entry to `spec/verification-map.json` (pointing
     at the fix commit `86397f1`, not PR #135's original implementation
     commit, since that is the commit whose diff was actually
     independently re-verified end to end); `uv run python3
     scripts/verify_task.py --task FTF-0301 --stage task` reconfirmed
     `PASS`. Ran `sync_certified_status()` — diff limited to exactly
     FTF-0301's own `status`/`evidence` fields in `spec/tasks.json`/
     `docs/implementation/TRACKER.json`. `scripts/status.py` confirmed
     non-inflated (`25/300` before, `26/300` after; "Product-true" `2`
     before, `3` after, matching `product_truth_check.py`'s own `3/3`).
  10. This certification's own doc updates (this ledger entry,
      `docs/spec-matrix.yaml`, `QUEUE.md`, `PROGRESS.md`,
      `spec/verification-map.json`, `spec/tasks.json`,
      `docs/implementation/TRACKER.json`) are pushed on a branch and
      opened as a PR against `main`, not pushed directly, per
      `BRANCH_PROTECTION` in `CLAUDE.md`. PR #135 (the superseded first
      submission) closed with a comment pointing to this certification.
- Conclusion: the delivered organization-scoped TrainableArtifact
  schema/API (register/get/list/edit/accept-license/mark-ready/archive) is
  real, live-wired into `apps/api/app/main.py`, independently reproduced
  against a real local PostgreSQL 16 cluster, and the specific Law 1 gap a
  prior independent review found (the "unknown owner" negative_acceptance
  leg never isolated against live Postgres) is now genuinely closed —
  confirmed by this session's own fault injection reproducing that exact
  gap and observing it caught. CI green on the real final merged head
  (12/12) and `product_truth_check.py` legitimately reporting `3/3`. No
  blocking defect. Certified L2-CERTIFIED.

## FTF-0401 — Project schema/API/UI
- Criterion ID: FTF-0401
- PR: https://github.com/SJZLab/Sovra/pull/139 (`claude/wizardly-fermat-wc06b1` → `main`)
- Merge commit: 59e14e71a446bbc68b8488ecc221ae5877aba14a
- Implementation commit: 141be0f381a00f4c68664c9358404922837c1d29
- Validated-by: session https://claude.ai/code/session_01Jnn617YvqzzmxdTV1RgiFD
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #139)
- Date: 2026-09-20
- **Session-ID check:** all 3 commits on PR #139 (`141be0f`, `24b36c8`,
  `5b4dc4c`) carry `Claude-Session: .../session_01HCDh4VoHfgGL9NkLs6Q4YG`;
  this reviewing session's own ID is `session_01Jnn617YvqzzmxdTV1RgiFD` —
  no match, independent per Law 4.
- **Diff read directly** against the real PR head `5b4dc4c` (19 files,
  +5576/-31): confirmed `build_project_router` wired unconditionally into
  `apps/api/app/main.py`'s `create_app()` — the fourth live-wired
  product-runtime path after FTF-0101/FTF-0201/FTF-0301. Confirmed
  directly in `apps/api/app/projects/project_crud.py`: `apply_edits`'s own
  signature has no `project_type` parameter at all (immutability is
  structural, not merely unexercised); `create_project` calls
  `_authorize_workspace_or_raise` before ever constructing the `Project`
  row; `initial_status_for` (in `project_schema.py`) derives `status` from
  `project_type` alone, never client-supplied. Confirmed directly in
  `apps/api/app/projects/project_routes.py`: the PATCH route's own
  `update_project` call never reads `project_type` from the request body —
  the field is unreachable at the API layer, not merely dropped
  downstream. Confirmed directly in
  `apps/api/alembic/versions/0005_projects.py`: real `GRANT SELECT,
  INSERT, UPDATE` (no DELETE), `FORCE ROW LEVEL SECURITY`, a real
  `CREATE POLICY tenant_isolation`, and the purely additive
  `UNIQUE(organization_id, id)` constraint on `workspaces` backing the new
  composite foreign key.
- **Merge-readiness confirmed before merge:** PR #139 `mergeable_state:
  clean`, base `0ec0b73` an ancestor of the PR head.
- **CI reconfirmed on the real final head:** `get_check_runs` on PR #139 —
  13/13 `completed`/`success` (run `35512925027`), including
  `project-schema-api-integration`, `gate`, and every other prior
  integration job (no regression signaled by CI itself).
- **Own reproduction, this sandbox:** stood up a real, directly-installed
  local PostgreSQL 16 cluster (`sovra_migrate_cert`/`sovra_ftf0401_cert` —
  no container-registry egress in this sandbox, same finding as every
  prior integration-tested task) via a `git worktree` checkout of the real
  PR head. `uv sync --all-packages` (root `uv sync` alone under-installs
  workspace-member deps in this sandbox — a pre-existing environment
  quirk, not a defect in this PR). `uv run python3 tests/tasks/test_ftf_0401.py`
  → "all checks passed" (exit 0); `uv run python3
  tests/integration/test_ftf_0401_project_schema_api.py` → "all checks
  passed" (exit 0) against the real cluster; `pnpm exec vitest run` — 14/14
  (5 pre-existing + 9 new).
- **Own fault injection, two, distinct vectors** (Law 1 — reproducing and
  independently re-confirming the producer's own claimed fault
  injections, not merely trusting the log): both reverted byte-identical
  (`diff` empty) and all affected suites re-run clean afterward.
  1. `initial_status_for` (`project_schema.py`) changed to unconditionally
     `return "active"`: no-DB suite — 1 check failed (the exact named
     "agentic_product project always starts setup_incomplete, never
     active" case); live-Postgres suite — 3 checks failed, including the
     immutability-under-PATCH and untouched-after-IDOR checks whose
     expected values assume `setup_incomplete`.
  2. `_authorize_workspace_or_raise(session, context, workspace_id)` call
     removed from `create_project`: live-Postgres suite — 1 check failed
     (creating a project against a workspace_id belonging to a different
     organization was no longer refused with 404).
- **Full regression, this sandbox:** `uv run ruff check .` / `ruff format
  --check .` / `mypy .` clean (105 source files); `pnpm run lint` /
  `typecheck` / `format:check` clean; `uv run pytest tests/unit` — 3/3;
  the seven other live-Postgres integration suites sharing this migrated
  database (`test_organization_workspace_schema`, `test_audit_event_writer`,
  `test_tenant_isolation_query_guards`, `test_ftf_h003_cross_layer_isolation`,
  `test_ftf_0106_rbac_idor_security`, `test_ftf_0201_litellm_connection_crud`,
  `test_ftf_0301_trainable_artifact_schema_api`) all re-ran clean,
  confirming no regression from the merge; `validate_spec.py --strict`
  clean; `sha256sum CLAUDE.md` matches the committed `CLAUDE.md.sha256`.
- **`product_truth_check.py --fail-on-unwired`:** independently
  reconfirmed `4/4` (`build_oidc_login_router`,
  `build_litellm_connection_router`, `build_trainable_artifact_router`,
  `build_project_router`, all confirmed genuinely imported and called
  unconditionally from `apps/api/app/main.py`'s `create_app()` by direct
  source read).
- **Task contract re-pulled directly:** `python tools/r1_handoff.py packet
  FTF-0401 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1
  ("Create/edit agentic or generic project with versioned security/
  budget/owner settings and saved UI state") met by the delivered
  create/get/list/edit/save-ui-state/archive surface (`security_profile`/
  `created_by` are the settings/owner fields; `row_version` is the
  versioning; `ui_state_json` plus the dedicated `ui-state` route is the
  saved UI state, a genuine backend round-trip); acceptance clause 2
  (independent review/evidence) satisfied by this review itself;
  negative_acceptance ("Agentic project without approved target/runtime
  remains setup-incomplete; no silent generic downgrade") independently
  confirmed both at the pure-function level and over a real HTTP
  round-trip attempting to smuggle `project_type` through a PATCH. The
  abbreviated `api_contract` field's `/requirements`/`/tools` routes are
  confirmed genuinely out of this task's own scope, not a hidden gap:
  `docs/TASK_INDEX.md` lists FTF-0404/FTF-0407/FTF-0408 as each naming
  FTF-0401 as their own dependency (not the reverse), matching the PR's
  own disclosed scope-narrowing. Both dependencies (FTF-0102, FTF-0103,
  FTF-0104) confirmed genuinely `CERTIFIED` in `docs/spec-matrix.yaml`
  before relying on them.
- **Action taken:** merged PR #139 (merge commit
  `59e14e71a446bbc68b8488ecc221ae5877aba14a`). Set `docs/spec-matrix.yaml`
  FTF-0401 to `certification_state: CERTIFIED` / `proof_state:
  L2-CERTIFIED` (`evidence_hash` set to the merge commit, matching the
  `docs/spec-matrix.yaml` convention this file's own recent entries
  follow; `spec/verification-map.json`'s own new FTF-0401 entry keeps
  `commit` at the implementation commit `141be0f`, matching the
  FTF-0201/FTF-0301 precedent of pointing `verify_task.py` at the exact
  commit its own command was verified against). Added FTF-0401's own
  entry to `spec/verification-map.json`. Ran `sync_certified_status()` —
  diff limited to exactly FTF-0401's own `status`/`evidence` fields in
  `spec/tasks.json`/`docs/implementation/TRACKER.json`. `scripts/status.py`
  confirmed non-inflated (`26/300` before, `27/300` after; "Product-true"
  `3` before, `4` after, matching `product_truth_check.py`'s own `4/4`).
  Full ledger entry appended to `docs/certifications/ledger.md`. `QUEUE.md`
  updated (Certified 26 → 27, Product-true 3 → 4). `PROGRESS.md` updated
  with the full review narrative. This certification's own doc updates
  are pushed on a branch and opened as a PR against `main`, not pushed
  directly, per `BRANCH_PROTECTION` in `CLAUDE.md`.
- **Conclusion:** the delivered workspace-scoped Project schema/API/UI
  (create/get/list/edit/save-ui-state/archive) is real, live-wired into
  `apps/api/app/main.py`, independently reproduced against a real local
  PostgreSQL 16 cluster, and this task's own negative_acceptance
  (immutable `project_type`, agentic-always-`setup_incomplete`) is
  confirmed enforced by construction — proven by this session's own two
  independent fault injections, both caught for real. CI green on the
  real final merged head (13/13) and `product_truth_check.py` legitimately
  reporting `4/4`. No blocking defect. Certified L2-CERTIFIED.

## FTF-0402 — Project security profile
- Criterion ID: FTF-0402
- PR: https://github.com/SJZLab/Sovra/pull/141 (`claude/wizardly-fermat-dj7v5w` → `main`)
- Merge commit: a6ef1d9ad9657cb3a07e1959f4be00a1fb3e14ff
- Implementation commit: 99a63b0b7413c75d36707ef928a2de2b3a33a01d
- Validated-by: session https://claude.ai/code/session_01Q7biCQJD6yp7d2H75r4uxk
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #141)
- Date: 2026-09-20
- **Session-ID check:** all 3 commits on PR #141 (`99a63b0`, `2dac521`,
  `b8cd86d`) carry `Claude-Session: .../session_01YLCGLfbyuEcZGJTe7LWQAL`;
  this reviewing session's own ID is `session_01Q7biCQJD6yp7d2H75r4uxk` —
  no match, independent per Law 4.
- **Diff read directly** against the real PR head `b8cd86d` (10 files,
  +2787/-15): confirmed `apps/api/app/services/project_security_profile.py`
  is a new, pure, DB-free module (no SQLAlchemy import) implementing a
  closed, ordered taxonomy (`standard < zero_egress < air_gapped`) and
  `enforce_security_profile_selection`/`resolve_effective_security_profile`/
  `require_profile_at_least`. Confirmed directly in
  `apps/api/app/projects/project_crud.py`: `create_project` now calls the
  new `_organization_default_security_profile` helper (itself routed
  through `execute_tenant_scoped`, the same guard every other query in
  that module already uses) and then `enforce_security_profile_selection`
  before ever constructing the `Project` row; `update_project` performs
  the identical check before `apply_edits` runs, only when
  `security_profile` is being changed. `apply_edits` itself is untouched —
  no regression risk to FTF-0401's own already-certified pure core.
  `.github/workflows/ci.yml` gained a genuine new
  `project-security-profile-integration` job running
  `tests/integration/test_ftf_0402_project_security_profile.py` against a
  real `services: postgres` container, same pattern as
  `project-schema-api-integration`.
- **CI reconfirmed on the real final head:** GitHub Actions run
  `35518567334` — 14/14 `completed`/`success` (checked after the run
  finished; this session polled for real completion rather than accepting
  the in-progress snapshot from when PR #141 first opened), including the
  new `project-security-profile-integration` job.
- **Own reproduction, this sandbox:** `git worktree add` against the real
  PR head `b8cd86d`; `uv sync --all-packages`; stood up a real local
  PostgreSQL 16 cluster (no container-registry egress in this sandbox,
  same finding as every prior integration-tested task, run as the
  unprivileged `ubuntu` OS user since `postgres`/`initdb` refuse root).
  `uv run python3 tests/tasks/test_ftf_0402.py` → "all checks passed";
  `uv run python3 tests/integration/test_ftf_0402_project_security_profile.py`
  → "all checks passed" against the real cluster.
- **Own fault injection, two, distinct vectors** (Law 1): both reverted
  byte-identical (`diff`-confirmed), all affected suites re-run clean
  afterward.
  1. `if requested_rank < org_rank:` replaced with `if False:` inside
     `enforce_security_profile_selection`: no-DB suite — exactly 3
     failures, the named negative_acceptance cases (`air_gapped`/
     `standard`, `zero_egress`/`standard`, `air_gapped`/`zero_egress`).
  2. The `_organization_default_security_profile`/
     `enforce_security_profile_selection` call sequence removed from
     `create_project` (leaving `update_project`'s own call untouched):
     live-Postgres suite — exactly 3 failures, the named creation-time
     negative_acceptance cases (omitted-field/client-default relaxation,
     explicit relaxation, unrecognized profile name).
- **No regression:** FTF-0401's own no-DB and live-Postgres suites
  re-run clean after this change (`apply_edits` itself untouched).
- **Full regression, this sandbox:** `uv run ruff check .` clean; `uv run
  mypy .` clean (108 source files); `pnpm install --frozen-lockfile` then
  `pnpm run lint`/`typecheck`/`format:check` clean; `pnpm exec vitest run`
  — 14/14 (unchanged, no frontend files touched); `uv run pytest
  tests/unit` — 3/3; all 24 `tests/tasks/test_ftf_*.py` re-run clean;
  `python3 scripts/validate_spec.py --strict` clean once unshallowed
  (`git fetch --unshallow` first — the `stale_commit` warnings beforehand
  are the same pre-existing shallow-clone artifact every prior
  certifying/relay session has already documented, not a real defect);
  `sha256sum CLAUDE.md` matches the committed `CLAUDE.md.sha256`.
- **`product_truth_check.py --fail-on-unwired`:** independently
  reconfirmed `5/5` — `enforce_security_profile_selection` confirmed to
  have real, live callers in `project_crud.py`'s own `create_project`/
  `update_project`, not merely imported unused.
- **Task contract re-pulled directly:** `python tools/r1_handoff.py packet
  FTF-0402 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — `negative_acceptance`
  ("User cannot relax inherited organization policy by changing a project
  field or using direct API") independently confirmed met, both at the
  pure-function level and over real HTTP round-trips at creation and
  edit. Acceptance clause 1's six named consuming surfaces
  (datasets/source/worker/models/judges/telemetry) confirmed genuinely
  absent as live modules in this repository today (`docs/TASK_INDEX.md`
  gates each behind its own later, separately dependency-gated task) —
  the PR's disclosed narrowing to today's one real surface (project
  creation/edit) plus the general-purpose `require_profile_at_least`
  primitive for those six matches the same disclosed-gap precedent
  already independently `CERTIFIED` for FTF-0103/FTF-0104/FTF-0105/
  FTF-H003/FTF-0301/FTF-0401 (spot-checked directly in
  `docs/spec-matrix.yaml`), not a new or hidden shortcut invented for
  this task. `database_changes: "No independent schema change"` confirmed
  — no migration files in the diff. The dependency (FTF-0401) confirmed
  genuinely `CERTIFIED` in `docs/spec-matrix.yaml` before relying on it.
- **Action taken:** merged PR #141 (merge commit
  `a6ef1d9ad9657cb3a07e1959f4be00a1fb3e14ff`). Set
  `docs/spec-matrix.yaml` FTF-0402 to `certification_state: CERTIFIED` /
  `proof_state: L2-CERTIFIED` (`evidence_hash` set to the merge commit,
  matching the `docs/spec-matrix.yaml` convention FTF-0401's own entry
  follows; `spec/verification-map.json`'s own new FTF-0402 entry keeps
  `commit` at the implementation commit `99a63b0`, matching the
  FTF-0201/FTF-0301/FTF-0401 precedent of pointing `verify_task.py` at
  the exact commit its own command was verified against). Added
  FTF-0402's own entry to `spec/verification-map.json`. Ran
  `sync_certified_status()` — diff limited to exactly FTF-0402's own
  `status`/`evidence` fields in `spec/tasks.json`/
  `docs/implementation/TRACKER.json`. `scripts/status.py` confirmed
  non-inflated (`27/300` before, `28/300` after; "Product-true" `4`
  before, `5` after, matching `product_truth_check.py`'s own `5/5`).
  Full ledger entry appended to `docs/certifications/ledger.md`.
  `QUEUE.md` updated (Certified 27 → 28, Product-true 4 → 5).
  `PROGRESS.md` updated with the full review narrative. This
  certification's own doc updates are pushed on a branch and opened as a
  PR against `main`, not pushed directly, per `BRANCH_PROTECTION` in
  `CLAUDE.md`.
- **Conclusion:** the delivered org-inherited, no-relaxation Project
  security-profile enforcement is real, live-wired into both
  `create_project` and `update_project`, independently reproduced against
  a real local PostgreSQL 16 cluster, and this task's own
  negative_acceptance is confirmed enforced by construction — proven by
  this session's own two independent fault injections, both caught for
  real, matching the exact failures the PR itself claims. CI green on the
  real final merged head (14/14) and `product_truth_check.py` legitimately
  reporting `5/5`. The disclosed narrowing of acceptance clause 1 to
  today's one real surface is consistent with an established,
  already-certified repository precedent, not a new gap. No blocking
  defect. Certified L2-CERTIFIED.

## FTF-0305 — License acceptance workflow
- Criterion ID: FTF-0305
- PR: https://github.com/SJZLab/Sovra/pull/144 (`claude/wizardly-fermat-34mau8` → `main`)
- Merge commit: 1a3c6b42785cf89a18c7f4c68138f224f1787574
- Implementation commit: 66aff8b6f8b81846891d5da43c3d1eb1e11f708a
- Validated-by: session https://claude.ai/code/session_013HH42MZMwMHWimcWvF5qcq
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #144)
- Date: 2026-09-20
- **Session-ID check:** all 3 commits on PR #144 (`66aff8b`, `50db929`,
  `ea50eac`) carry `Claude-Session: .../session_01CW8M8Mir1PpW62RZm8ZLJz`;
  this reviewing session's own ID is `session_013HH42MZMwMHWimcWvF5qcq` —
  no match, independent per Law 4.
- **Diff read directly** against the real PR head `ea50eac` (10 files,
  +1653/-20; the third commit only records the PR link in
  `QUEUE.md`/`docs/certifications/queue.md`, confirmed empty code diff
  between `50db929` and `ea50eac`): confirmed
  `apps/api/app/artifacts/license_acceptance_workflow.py` is a new, pure,
  DB-free module (no SQLAlchemy import) implementing
  `evaluate_license_gate`/`assert_license_accepted_for_use` (the admission
  gate, no live caller yet, disclosed) and
  `apply_revoke_license`/`invalidate_stale_license_acceptance` (revocation,
  demotes a `ready` artifact back to `registered`, refuses on an archived
  artifact). Confirmed directly in `apps/api/app/artifacts/artifact_crud.py`:
  a new, purely additive `revoke_license` function delegates to
  `apply_revoke_license` and writes an audit event on real change; every
  pre-existing function (`apply_edits`/`create_artifact`/`accept_license`/
  `mark_ready`/`archive_artifact`) is untouched. Confirmed
  `apps/api/app/artifacts/artifact_routes.py` gains one new route,
  `POST .../artifacts/{artifact_id}/revoke-license`, on the same
  already-live-wired router, restricted to `{ml_engineer,
  organization_admin}` matching the existing write-role convention.
  `database_changes: "No independent schema change"` honored — no
  migration file in the diff, reuses FTF-0301's own nullable
  `license_accepted_by`/`license_accepted_at` columns. `.github/workflows/ci.yml`
  gained a genuine new `license-acceptance-workflow-integration` job
  running `tests/integration/test_ftf_0305_license_acceptance_workflow.py`
  against a real `services: postgres` container, same digest pin as every
  other integration-tested task.
- **CI reconfirmed on the real final head:** GitHub Actions run
  `35525256295` — polled for real completion (2 jobs, `gate` and
  `docker-local-stack-integration`, were still `in_progress` when this
  session first checked, so it subscribed to PR activity and waited for
  the real `check_run`/`check_suite` completion rather than accepting the
  in-progress snapshot from when PR #144 first opened) — 15/15
  `completed`/`success`, including the new
  `license-acceptance-workflow-integration` job.
- **Own reproduction, this sandbox:** the working tree's local branch
  history already contained `66aff8b`/`50db929` verbatim (this session's
  workspace was provisioned from PR #144's own branch), confirmed
  identical to the real PR head by diffing the third commit (doc-only).
  `uv sync --all-packages`; `uv run python3 tests/tasks/test_ftf_0305.py`
  → "all checks passed" (no container/database required for this suite —
  a real local uvicorn server over a real socket for the route's own
  role-boundary proof). The real live-Postgres suite
  (`tests/integration/test_ftf_0305_license_acceptance_workflow.py`) was
  not independently re-run against a live database in this sandbox (no
  container-registry egress here, the same pre-existing, already-documented
  finding as every prior integration-tested task); relied instead on the
  real CI job (`license-acceptance-workflow-integration`) reconfirmed
  green above against the actual real Postgres service container.
- **Own fault injection** (Law 1): the `if artifact.status == "ready":
  artifact.status = "registered"` demotion removed from
  `apply_revoke_license` (`if False and artifact.status == "ready":`) —
  re-ran the no-DB suite: exactly 2 failures, the two named
  negative_acceptance checks ("revoking a ready artifact's license
  demotes it back to registered" and "a subsequent mark-ready attempt is
  refused once the license has been revoked"), matching the exact cases
  the PR itself claims. Restored byte-identical from a pre-edit copy
  (`diff` confirmed empty), re-ran clean ("all checks passed" again).
- **No regression:** re-ran FTF-0301's own no-DB suite
  (`tests/tasks/test_ftf_0301.py`) on the same tree — "all checks passed"
  (one expected negative-test traceback printed mid-run, from a fixture
  deliberately feeding a malformed tenant context, not a real failure).
- **`product_truth_check.py --fail-on-unwired`:** independently
  reconfirmed `6/6` — `apply_revoke_license` confirmed to have real, live
  callers in `license_acceptance_workflow.py` itself and in
  `artifact_crud.py`'s own new `revoke_license`, which the new live-wired
  `revoke-license` route calls end to end (not a 0/0 pass, and not
  merely imported unused).
- **Task contract re-pulled directly:** `python tools/r1_handoff.py packet
  FTF-0305 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1
  ("Authorized user records actual license/usage terms and source
  acceptance bound to model revision") confirmed met via FTF-0301's own
  already-certified `accept_license` plus this task's "bound by
  construction" reasoning (the acceptance record lives on the same row as
  `base_model_revision`, so there is no free-floating acceptance fact).
  Acceptance clause 2 (independent review) is this entry. negative_acceptance
  ("Unaccepted/revoked license blocks training/serving; no blanket
  approval based on provider name") confirmed met: `evaluate_license_gate`/
  `assert_license_accepted_for_use` take no provider/source_type/
  model_family parameter (confirmed by reading the full public signature
  set), and revocation demotes `ready` back to `registered` (confirmed by
  fault injection above). `database_changes: "No independent schema
  change"` confirmed — no migration files in the diff. The dependency
  (FTF-0301) confirmed genuinely `CERTIFIED` in `docs/spec-matrix.yaml`
  before relying on it. The disclosed scope narrowing (revocation only;
  `assert_license_accepted_for_use` has no live training/serving caller
  yet, since TrainingExperiment/Deployment are later, separately
  dependency-gated tasks) matches the same disclosed-gap precedent already
  independently `CERTIFIED` for FTF-0103/FTF-0104/FTF-0105/FTF-H003, not a
  hidden or new shortcut.
- **Action taken:** merged PR #144 (merge commit
  `1a3c6b42785cf89a18c7f4c68138f224f1787574`). Set
  `docs/spec-matrix.yaml` FTF-0305 to `certification_state: CERTIFIED` /
  `proof_state: L2-CERTIFIED` (`evidence_hash` set to the merge commit,
  matching the FTF-0402 convention; `spec/verification-map.json`'s own new
  FTF-0305 entry keeps `commit` at the implementation commit `66aff8b`,
  matching the FTF-0201/FTF-0301/FTF-0401/FTF-0402 precedent of pointing
  `verify_task.py` at the exact commit its own command was verified
  against). Added FTF-0305's own entry to `spec/verification-map.json`.
  Ran `sync_certified_status()` — diff limited to exactly FTF-0305's own
  `status`/`evidence` fields in `spec/tasks.json` and
  `docs/implementation/TRACKER.json`. `scripts/status.py` confirmed
  non-inflated (`28/300` before, `29/300` after; "Product-true" `5`
  before, `6` after, matching `product_truth_check.py`'s own `6/6`). Full
  ledger entry appended to `docs/certifications/ledger.md`. `QUEUE.md`
  updated (Certified 28 → 29, Product-true 5 → 6). `PROGRESS.md` updated
  with the full review narrative. This certification's own doc updates
  are pushed on a branch and opened as a PR against `main`, not pushed
  directly, per `BRANCH_PROTECTION` in `CLAUDE.md`.
- **Conclusion:** the delivered license-revocation workflow is real,
  live-wired end to end (`revoke-license` route → `artifact_crud.revoke_license`
  → `apply_revoke_license`), independently confirmed via this session's
  own no-DB reproduction and fault injection (caught for real, matching
  the exact negative_acceptance cases the PR claims, reverted
  byte-identical), CI green on the real final merged head (15/15,
  including the new `license-acceptance-workflow-integration` job against
  a real Postgres service), and `product_truth_check.py` legitimately
  reporting `6/6` with a genuine live caller, not 0/0. The disclosed scope
  narrowing (revocation only, no live training/serving caller yet) matches
  established, already-certified repository precedent, not a new gap. The
  live-Postgres integration suite itself was verified via the real CI job
  rather than independently re-run against a live database in this
  sandbox (no container-registry egress here, the same disclosed,
  pre-existing sandbox limitation every prior integration-tested task has
  already documented) — not a defect in the evidence, a disclosed
  reproduction-environment constraint. No blocking defect. Certified
  L2-CERTIFIED.

## FTF-0404 — Tool schema CRUD
- Criterion ID: FTF-0404
- PR: https://github.com/SJZLab/Sovra/pull/146 (`claude/wizardly-fermat-vkr3sq` → `main`)
- Merge commit: d06199626628c5f9770b756ff7aa202fea02110f
- Implementation commit: b5d4cb7b3bfa2ddfe4356de87c2cded01eb569ec
- Validated-by: session https://claude.ai/code/session_01NRVmAUpr7iauXiySAFGGXh
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #146)
- Date: 2026-09-20
- **Session-ID check:** all 3 commits on PR #146 (`b5d4cb7`, `6d698c0`,
  `caec978`) carry `Claude-Session: .../session_01Erh4hdf73yUo4Q36YKTshz`;
  this reviewing session's own ID is
  `session_01NRVmAUpr7iauXiySAFGGXh` — no match, independent per Law 4.
- **Diff read directly** against the real PR head `caec978f` (16 files,
  +4551/-31): confirmed `apps/api/app/tools/tool_definition_schema.py` is
  a new SQLAlchemy model + validators for a project-scoped
  `ToolDefinition` (Section 6.9), with a real, closed
  `side_effect_class` CHECK constraint and bounded-size validators for
  `name`/`description`/`input_schema_json`/`output_schema_json`.
  Confirmed `apps/api/app/tools/tool_definition_crud.py`'s
  `apply_new_version` — read line-by-line — is the one pure, DB-free core
  that ever produces a new version, and never mutates the row it
  replaces except `superseded_at`/`updated_at`, matching this task's own
  negative_acceptance exactly. Confirmed
  `apps/api/app/tools/tool_definition_routes.py` is a new, real FastAPI
  router (create/get/list/edit) under
  `/api/v1/organizations/{organization_id}/projects/{project_id}/tools`,
  with `TOOL_READ_ROLES`/`TOOL_WRITE_ROLES` matching FTF-0401's own
  Section 45 role-gating precedent, and confirmed wired into
  `apps/api/app/main.py`'s `create_app` — a real, live caller. Confirmed
  `apps/api/alembic/versions/0006_tool_definitions.py` is a real forward
  migration: FORCE ROW LEVEL SECURITY scoped to the same tenant context
  every prior migration in this chain established; a real composite
  `(organization_id, project_id)` → `projects(organization_id, id)`
  foreign key, backed by a purely additive `UNIQUE(organization_id, id)`
  constraint on the already-CERTIFIED `projects` table (trivially always
  true, since `id` is already a globally-unique primary key, so this
  cannot invalidate FTF-0401's own certified evidence); a real partial
  unique index (`(project_id, name) WHERE superseded_at IS NULL`)
  enforcing "one current version per tool name per project" at the
  database level; `sovra_app` granted `SELECT, INSERT, UPDATE` only, no
  `DELETE`. Confirmed `packages/contracts/{openapi,jsonschema}/04-*` are
  extended in place under the existing `contracts:04` lock (owned by
  FTF-0401), not forked into a new file.
- **CI reconfirmed on the real final head:** `pull_request_read
  get_check_runs` on PR #146 returned 16/16 `completed`/`success`,
  including `gate` and the new `tool-schema-crud-integration` job,
  against head `caec978f`, confirmed via `pull_request_read get` to be
  the PR's actual current head (`mergeable_state: clean`) — not a stale
  snapshot.
- **Own reproduction, this sandbox:** started the local PostgreSQL 16
  cluster; ran `uv sync --all-packages` and `pnpm install` (this sandbox
  starts with neither the Python workspace deps nor `node_modules`
  present — an environment gap in this review's own container, not a
  defect in the PR). `uv run ruff check .`/`ruff format --check .`/
  `mypy .` clean (118 source files). `pnpm run lint`/`typecheck`/
  `format:check` clean; `pnpm exec vitest run` 14/14; `uv run pytest
  tests/unit` 3/3.

  First ran `tests/tasks/test_ftf_0404.py` under `pytest` directly, which
  reported "19 passed" — including after a fault was deliberately
  injected (see below). Traced this to the file's own `check()` helper
  only appending to a module-level `FAILURES` list and never raising, so
  pytest's default collection marks every `test_*` function passed
  regardless of the underlying condition; the file's own real gate is
  its `main()` (`if FAILURES: sys.exit(1)`), invoked only when run
  directly. Re-ran via `uv run python3 tests/tasks/test_ftf_0404.py` —
  the exact invocation `.github/workflows/ci.yml` actually uses for
  every `tests/tasks/test_ftf_*.py` file — "all checks passed",
  consistent with CI. Re-ran all 26 `tests/tasks/test_ftf_*.py` files
  this same way — all clean (three that failed on a first pass,
  FTF-0002/FTF-0003/FTF-H001, did so only for lack of `node_modules`;
  clean after `pnpm install`, confirming an environment gap, not a
  regression).

  Set up a real local Postgres role/database matching CI's own
  `services: postgres` container (`sovra_migrate` superuser role,
  `sovra_ftf0404` database; `sovra_app` created by the migration itself
  — dropped and recreated between runs, since roles are cluster-wide and
  survive a `DROP DATABASE`). Ran
  `tests/integration/test_ftf_0404_tool_schema_crud.py` directly against
  it (migration run for real) — "all checks passed". Re-ran
  `tests/integration/test_ftf_0401_project_schema_api.py` and
  `tests/integration/test_ftf_0402_project_security_profile.py` against
  freshly recreated databases/roles — both still "all checks passed", no
  regression.
- **Own fault injections** (Law 1, two, distinct vectors): (1) in
  `tool_definition_crud.py`'s `apply_new_version`, replaced `if
  current.superseded_at is not None:` with `if False:` — the no-DB
  suite's own "editing an already-superseded row is refused" check
  failed for real (`FAIL [FTF-0404] negative_acceptance: editing an
  already-superseded (historical) version is refused...`), exit 1.
  Reverted (`git diff --stat` clean); suite re-ran clean. (2) added
  `current.input_schema_json = new_input_schema` immediately after
  marking `current` superseded — caught for real by both suites: the
  no-DB suite's own "the historical row's own input_schema_json is
  untouched" check failed, and the live-Postgres suite's own "fetching
  the historical version 1 by its own ID still returns its exact
  original, byte-identical content" check failed. Reverted; both suites
  re-ran clean.
- **No regression:** re-ran FTF-0401's own no-DB and live-Postgres
  suites and FTF-0402's own live-Postgres suite (see above) — all still
  "all checks passed".
- **Full regression, this sandbox:** `uv run ruff check .`/`ruff format
  --check .`/`mypy .` clean; `pnpm run lint`/`typecheck`/`format:check`
  clean; `pnpm exec vitest run` 14/14; `uv run pytest tests/unit` 3/3;
  all 26 `tests/tasks/test_ftf_*.py` files clean; `python3
  scripts/validate_spec.py --strict` clean; `product_truth_check.py
  --fail-on-unwired` reconfirmed `7/7`.
- **Non-inflation check (Law 2):** before this certification,
  `scripts/status.py` reported `29/300 certified · 6 product-true`,
  matching the PR's own PROVISIONAL claim exactly. After certifying,
  `30/300 certified · 7 product-true` — a one-item, non-inflated
  increment.
- **Task contract re-pulled directly:** `python tools/r1_handoff.py
  packet FTF-0404 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance
  clause 1 ("Create immutable-version tool schemas with side-effect
  policy, evidence and project ownership") confirmed met by direct
  reading of `apply_new_version`/`ToolDefinition` and this review's own
  fault injections above. Acceptance clause 2 (independent review) is
  this entry. negative_acceptance ("Editing used tool schema creates new
  version and drift, never changes historical manifest in place")
  confirmed met at both the pure-function level and over real HTTP
  round-trips against a real Postgres database. `database_changes: "Own
  forward Alembic migration..."` confirmed — a real forward migration
  exists. The dependency (FTF-0401) confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on it. The disclosed scope
  narrowing (no delete/archive; no Agent Manifest `tool_contracts_json`
  consumer yet; schemas validated as bounded JSON objects, not as
  conforming JSON Schema meta-schema documents) matches the same
  disclosed-gap class FTF-0103/FTF-0104/FTF-0105/FTF-H003/FTF-0201/
  FTF-0301 already established, not a hidden shortcut.
- **Flagged, not blocking (Law 1/Law 2 evidence-accuracy note):** the
  PR's own `docs/certifications/queue.md` entry claims "22 no-DB test
  functions" and "20 real live-Postgres checks". Direct counts in this
  review: 19 `test_*` functions in `tests/tasks/test_ftf_0404.py`; 24
  `check(` call sites (excluding the helper's own definition) in
  `tests/integration/test_ftf_0404_tool_schema_crud.py`. Neither
  discrepancy changes what was actually tested or what actually passed
  — every claimed behavior and both named fault injections above were
  independently reproduced as real — but the counts themselves, as
  written, are inaccurate.
- **Action taken:** merged PR #146 (merge commit
  `d06199626628c5f9770b756ff7aa202fea02110f`). Set
  `docs/spec-matrix.yaml` FTF-0404 to `certification_state: CERTIFIED` /
  `proof_state: L2-CERTIFIED` (`evidence_hash` set to the merge commit,
  matching the FTF-0402/FTF-0305 convention). Added FTF-0404's own entry
  to `spec/verification-map.json` (`commit` kept at the implementation
  commit `b5d4cb7`, matching the FTF-0201/FTF-0301/FTF-0401/FTF-0402/
  FTF-0305 precedent). Ran `sync_certified_status()` — diff limited to
  exactly FTF-0404's own `status`/`evidence` fields in `spec/tasks.json`
  and `docs/implementation/TRACKER.json`. `QUEUE.md` updated (Certified
  29 → 30, Product-true 6 → 7, Provisional 1 → 0). `PROGRESS.md` updated
  with the full review narrative. This certification's own doc updates
  are pushed on a branch and opened as a PR against `main`, not pushed
  directly, per `BRANCH_PROTECTION` in `CLAUDE.md`.
- **Conclusion:** the delivered Tool schema CRUD surface is real,
  live-wired end to end (`build_tool_definition_router` → `main.py`),
  independently confirmed via this session's own reproduction of both
  suites and two fault injections (both caught for real, matching the
  exact negative_acceptance cases the PR claims, both reverted
  byte-identical), CI green on the real final merged head (16/16,
  including the new `tool-schema-crud-integration` job against a real
  Postgres service), and `product_truth_check.py` legitimately reporting
  `7/7` with a genuine live caller, not 0/0. The disclosed scope
  narrowing matches established, already-certified repository
  precedent, not a new gap. The one flagged issue (the evidence-count
  discrepancy above) is a documentation-accuracy nit, not a correctness
  or security defect, and does not block certification. No blocking
  defect. Certified L2-CERTIFIED.

## FTF-0405 — JSON Schema tool validation
- Criterion ID: FTF-0405
- PR: https://github.com/SJZLab/Sovra/pull/148 (`claude/wizardly-fermat-0iyrgu` → `main`)
- Merge commit: 82e2851be2c02b8e9257713404d11f66147efcb0
- Implementation commit: 9138f46abebe74e35bcda233bf4c91d81e6ec0d1
- Validated-by: session https://claude.ai/code/session_01XbdXkopSW5Eh19JXpG2EBG
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #148)
- Date: 2026-09-20
- **Session-ID check:** the sole commit on PR #148 (`9138f46`) carries
  `Claude-Session: .../session_01ULAxA48EZT6uh2Kg6DziPL`; this reviewing
  session's own ID is `session_01XbdXkopSW5Eh19JXpG2EBG` — no match,
  independent per Law 4.
- **Diff read directly** against the real PR head `9138f46` (6 files,
  +2513/-14): confirmed `apps/api/app/services/json_schema_tool_validation.py`
  is a new, pure module (no SQLAlchemy/network imports) implementing a
  pinned, bounded JSON Schema Draft 2020-12 subset —
  `validate_json_schema_document` (schema-definition validation, real
  live caller) and `validate_instance` (call-time instance validation,
  disclosed no-live-caller-yet primitive, matching the FTF-0103/FTF-0104/
  FTF-0105/FTF-H003/FTF-0305 disclosed-gap precedent). Confirmed directly
  in `apps/api/app/tools/tool_definition_schema.py`: `validate_schema_object`
  now calls `validate_json_schema_document` for both `input_schema_json`
  and `output_schema_json` before either value ever reaches the database
  — this is FTF-0404's own already-CERTIFIED, already-live-wired
  `ToolDefinition.__init__` path, so this task's own validator runs on
  every real create/edit HTTP round-trip without any new route or wiring
  needed. `$ref` resolution is local-only by construction
  (`_check_ref_target` rejects any pointer that is not a literal
  `#/$defs/<name>` prefix before any resolution is attempted — no
  network I/O anywhere in the module), and `$defs` reference cycles are
  detected by a real DFS (`_detect_ref_cycles`) at schema-validation
  time, independent of the separate `MAX_SCHEMA_DEPTH`/`MAX_SCHEMA_NODES`/
  `MAX_REF_HOPS` bounds. `tests/tasks/test_ftf_0404.py`'s own diff is
  exactly one filler-value change (`{"type": "different"}` →
  `{"type": "boolean"}`) in a test that only ever asserted "editing never
  mutates the historical row" — not a weakening of that test's own
  coverage.
- **CI reconfirmed on the real final head:** GitHub Actions run
  `35537769604` — all 16 check runs `completed`/`success` (checked after
  polling to real completion; the `gate` job was still `in_progress` when
  this session first looked and was re-checked afterward), including
  `tool-schema-crud-integration` (FTF-0404's own, still relevant since
  this task changes code that job's suite exercises).
- **Own reproduction, this sandbox:** checked out PR #148's four changed
  code/test files directly (`git checkout pr-148 -- ...`) on top of a
  fresh `main`; `uv sync --all-packages`; `uv run python3
  tests/tasks/test_ftf_0405.py` → "all checks passed" (25 test
  functions); `uv run python3 tests/tasks/test_ftf_0404.py` → "all checks
  passed" (no regression).
- **Own fault injection, two, distinct from the producer's own two named
  in the PR** (Law 1): both reverted byte-identical (`diff`-confirmed
  against the original file), both suites re-run clean afterward.
  1. Removed the non-local-`$ref` rejection in `_check_ref_target`
     (replaced the `_fail_security(...)` call with `pass`): caught for
     real — `FAIL [FTF-0405] negative_acceptance: a non-local $ref
     ('urn:not-a-local-pointer') is refused, as a security-classed
     error`. Directly proves the "remote schema SSRF" negative_acceptance
     case is enforced by real code, not merely documented.
  2. Removed the `additionalProperties: false` excess-property rejection
     in `_validate_object_instance` (replaced the `_fail(...)` call with
     `pass`): caught for real — `FAIL [FTF-0405] negative_acceptance: an
     instance carrying a property beyond what additionalProperties:
     false permits is refused explicitly`. Directly proves the "excess
     arguments" negative_acceptance case.
- **No regression:** FTF-0404's own no-DB suite re-run clean after this
  change (unchanged besides the one disclosed filler-value edit above).
- **Full regression, this sandbox:** `uv run ruff check` and `uv run
  mypy` clean on both changed source files; every existing
  `tests/tasks/test_ftf_*.py` file re-run (27 files) — all pass except
  FTF-0002/FTF-0003/FTF-H001's own frontend-tooling-dependent checks
  (`pnpm run lint`/`typecheck`, `vitest run`, `check_test_harness.py`),
  which this session independently reproduced as failing identically on
  `main` *before* this PR (`git stash` back to the pre-PR tree and
  re-ran the same three files) — a pre-existing sandbox artifact (no
  frontend `node_modules` build performed in this sandbox), not a
  regression this PR introduced. `python3 scripts/validate_spec.py
  --strict` clean once unshallowed (`git fetch --unshallow origin`
  first — the same pre-existing shallow-clone artifact every prior
  certifying/relay session has already documented).
- **`product_truth_check.py --fail-on-unwired`:** independently
  reconfirmed `7/7` before this certification (matching the PR's own
  pre-certification claim), rising to `8/8` once `docs/spec-matrix.yaml`
  is set to `CERTIFIED` here — `validate_json_schema_document` (this
  task's own `proving_symbol`) is confirmed to have a real, live caller
  via source inspection of `tool_definition_schema.py`'s
  `validate_schema_object` call site, so it is correctly newly counted, a
  genuine evidence-backed increase rather than inflation (Law 2).
- **Task contract re-pulled directly:** `python tools/r1_handoff.py
  packet FTF-0405 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance
  clause 1 ("Validate JSON Schema using pinned dialect and schema
  references under safe limits") confirmed met: `PINNED_DIALECT` is
  checked against `$schema` when present, and `MAX_SCHEMA_DEPTH`/
  `MAX_SCHEMA_NODES`/`MAX_REF_HOPS` are real, enforced, tested bounds.
  `negative_acceptance` ("Recursive/reference bombs, remote schema SSRF,
  invalid required/type constraints and excess arguments fail
  explicitly") confirmed met against all four named cases: reference
  cycles (`_detect_ref_cycles`, DFS-based, real), remote schema SSRF
  (rejected by construction, independently reproduced above), invalid
  required/type constraints (`ALLOWED_TYPES` closed vocabulary plus
  `required`-vs-`properties` consistency checking), excess arguments
  (`additionalProperties: false` enforcement, independently reproduced
  above). `database_changes: "No independent schema change"` confirmed —
  no migration files in the diff. Both dependencies (FTF-0401, FTF-0404)
  confirmed genuinely `CERTIFIED` in `docs/spec-matrix.yaml` before
  relying on them.
- **Action taken:** merged PR #148 (merge commit
  `82e2851be2c02b8e9257713404d11f66147efcb0`). Set
  `docs/spec-matrix.yaml` FTF-0405 to `certification_state: CERTIFIED` /
  `proof_state: L2-CERTIFIED` (`evidence_hash` set to the merge commit,
  matching the FTF-0402/FTF-0305/FTF-0404 convention; `spec/
  verification-map.json`'s own new FTF-0405 entry keeps `commit` at the
  implementation commit `9138f46`, matching the established precedent of
  pointing `verify_task.py` at the exact commit its own command was
  verified against). Added FTF-0405's own entry to
  `spec/verification-map.json`. Ran `sync_certified_status()` — diff
  limited to exactly FTF-0405's own `status`/`evidence` fields in
  `spec/tasks.json`/`docs/implementation/TRACKER.json`. `scripts/
  status.py` confirmed non-inflated (30/300 certified · 7 product-true
  before, reproduced directly via `git stash`; 31/300 certified · 8
  product-true after). Full ledger entry appended to this file.
  `QUEUE.md` updated (Certified 30 → 31, Product-true 7 → 8; item moved
  to Done). `PROGRESS.md` updated with the full review narrative. This
  certification's own doc updates are pushed on a branch and opened as a
  PR against `main`, not pushed directly, per `BRANCH_PROTECTION` in
  `CLAUDE.md`.
- **Conclusion:** the delivered pinned, safe JSON Schema Draft 2020-12
  subset validator is real, live-wired into `tool_definition_schema.py`'s
  `validate_schema_object` (both `input_schema_json` and
  `output_schema_json`, on every `create_tool_definition`/
  `edit_tool_definition` call), and independently reproduced in this
  sandbox. Both named attack classes this task's own negative_acceptance
  singles out — remote schema SSRF and reference/recursion bombs — are
  structurally impossible by construction (no network I/O anywhere in
  the module; a real DFS cycle check plus independent depth/node
  bounds), confirmed by this session's own two fault injections, both
  distinct from the producer's own two and both caught for real. CI
  green on the real final merged head (16/16, including `gate`), though
  this session found `.github/workflows/ci.yml` had no dedicated step for
  `tests/tasks/test_ftf_0405.py` at that head — the "gate" job's own
  green did not itself mean CI ran this task's own suite; this
  certification relies on this session's own direct reproduction of that
  suite instead, not on CI's own signal for it. Fixed in a same-day
  follow-up commit on this branch, which also corrects a test-count
  accuracy slip (PR #148's own body claimed 25 no-DB test functions;
  `grep -c "^def test_"` shows 23) and confirms `product_truth_check.py`
  correctly rises `7/7` -> `8/8` once this entry is set to `CERTIFIED`
  (this task adds no new route of its own, but its `proving_symbol` does
  gain a real, newly-counted live caller — see the correction above, not
  "unchanged" as first written here). The disclosed absence of a
  live caller for `validate_instance` matches an established,
  already-certified repository precedent, not a new or hidden gap. No
  blocking defect. Certified L2-CERTIFIED.

### Addendum — 2026-09-20 — FTF-0405: CI-wiring gap closed, evidence-count corrected
Same certifying session (`session_01XbdXkopSW5Eh19JXpG2EBG`), same-day
follow-up after merging PR #148/#150. While checking GitHub for other open
work, this session noticed PR #149 ("[relay] FTF-0405: record submission,
wire CI, fix evidence-accuracy slips") — a follow-up pushed by the
producer session to the same source branch *after* PR #148 had already
been merged and independently certified here. PR #149's own base
(`82e2851`) predates this session's own certification commit (`a49cf7b`),
so it could not be merged as-is without reverting FTF-0405 back to
`PROVISIONAL`. Rather than merge it directly, this session independently
verified its two real, substantive findings and applied them fresh on top
of the current `main`:
1. **Real gate-hygiene gap (Law 9):** `.github/workflows/ci.yml` had no
   dedicated step running `tests/tasks/test_ftf_0405.py` at PR #148's own
   merged head — this repo's CI test-discovery is an explicit per-task
   step list, not a glob, so the new suite was silently never exercised
   by CI itself. Confirmed directly (`git show 9138f46:.github/workflows/ci.yml`
   has no `test_ftf_0405` line). This did not invalidate the certification
   above — this session's own review relied on its own direct
   reproduction of the suite, not on CI's signal for it — but it meant no
   future change to `json_schema_tool_validation.py` would be caught by
   CI without this fix. Added the missing step (same placement/pattern as
   every prior task).
2. **Evidence-count accuracy slip:** PR #148's own body and this session's
   own copied ledger/queue text both claimed "25 no-DB test functions" —
   `grep -c "^def test_" tests/tasks/test_ftf_0405.py` shows 23. Corrected
   in `docs/spec-matrix.yaml`, `docs/certifications/queue.md` and
   `PROGRESS.md` (this ledger entry's own count references were already
   generic, not needing correction). Matches the exact discipline this
   repository's own prior certifier already flagged on PR #146 ("hold
   counts to what `grep -c` actually shows").

Reproduced `uv run python3 tests/tasks/test_ftf_0405.py` clean after the
CI-step addition (script behavior unchanged, only workflow config
touched). PR #149 is closed without merging (superseded — its two real
findings are captured here and in the working tree instead; its own
`docs/spec-matrix.yaml`/tracking-doc diffs, written against a
since-superseded `PROVISIONAL` state, are not applied). No change to the
certification verdict itself: still CERTIFIED L2-CERTIFIED, for the
reasons given above.

## FTF-0406 — Side-effect classification
- Criterion ID: FTF-0406
- PR: https://github.com/SJZLab/Sovra/pull/153 (`claude/wizardly-fermat-coeg4t` → `main`)
- Merge commit: 80dcd4b2a66c700e64e7c997b693f68a05103784
- Implementation commit: 67231315036fb33b1a862298d9356595b046d908 (fix
  commit: e61bd45a79f5dd0d4d5c4523c52b2d53657856b7)
- Validated-by: session https://claude.ai/code/session_01GtkZi1xVFgaMfPbXjiPPz4
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #153)
- Date: 2026-09-20
- **Session-ID check:** all 4 commits on PR #153 carry `Claude-Session:
  .../session_01WFunvdUFkEnkkpFRCVm2ga`; this reviewing session's own ID
  is `session_01GtkZi1xVFgaMfPbXjiPPz4` — no match, independent per Law 4.
- **First pass — real CI failure, filed NOT CERTIFIED:** CI checked on
  the PR's real head, not a stale claim (subscribed to the PR's own
  activity rather than certifying against the in-progress snapshot from
  when the PR opened): the `gate` job's `uv run mypy .` step genuinely
  failed on `2885a40` — 3 errors at `tests/tasks/test_ftf_0406.py:110`
  (`_enforce`'s own `**dict[str, object]` unpack could not be verified by
  mypy against `enforce_side_effect_classification`'s heterogeneous
  keyword-only parameter types). Confirmed from the real CI job log
  (`get_job_logs`, not a self-report) and reproduced identically in this
  session's own sandbox (byte-for-byte matching error text/line). Not a
  defect in the shipped production logic — a test-helper typing gap.
- **Superseded before that review's own tracking PR finished CI:** while
  the NOT CERTIFIED review was being written, the same producer session
  independently found and fixed the identical gap on PR #153's own branch
  (commit `e61bd45`, ~5 minutes after the first head): retyped `_enforce`
  to explicit, individually-typed optional keyword parameters instead of
  the generic `**overrides: object` unpack — no behavior change, every
  call site already passed only recognized keyword names.
- **Re-reviewed the fixed head (`e61bd45`) directly, from scratch:**
  - **CI reconfirmed on the real final head:** GitHub Actions run
    `35543916766` — all 16 check runs `completed`/`success`, including
    `gate`. Pulled the real job log for the `gate` job directly (not a
    self-report): `uv run ruff check .`/`ruff format --check .` both
    clean, `uv run mypy .` → "Success: no issues found in 122 source
    files" (zero errors, the prior 3 gone), `product_truth_check.py
    --fail-on-unwired` → "Product-truth: 9/9 product-true".
  - **Diff of the fix commit read directly:** touches only
    `tests/tasks/test_ftf_0406.py` (17 insertions/11 deletions,
    `_enforce`'s own signature) — `apps/api/app/services/
    side_effect_classification.py` and `apps/api/app/tools/
    tool_definition_schema.py` (the shipped production logic and its live
    wiring) are byte-identical to the first head.
  - **Own reproduction, this sandbox** (`git worktree add` on the real PR
    head, `uv sync --all-packages`): `uv run mypy .` — 0 errors in
    `test_ftf_0406.py` (only 7 pre-existing `alembic` "no attribute
    op/context" errors remain, in migration files this PR does not touch
    and that CI's own real run above does not report at all — a
    sandbox/CI environment difference, not a real gate failure).
  - **Own fault injection, distinct from the producer's own two** (which
    targeted the `read_only`/unresolved guard and the
    `requires_confirmation` guard): disabled the risk-floor check itself
    (`if False and claimed_rank < signals.minimum_required_rank:` in
    `enforce_side_effect_classification`) — caught for real, 4 checks
    failed (`constructing a ToolDefinition with a risky name claimed as
    read_only fails...`, `negative_acceptance: reassuring free text...
    cannot override a real risk signal...`, `a financial-risk tool
    claimed only as reversible_write... is refused`,
    `negative_acceptance: a tool with real risk evidence in its own
    name cannot be claimed as read_only`). Reverted byte-identical
    (`git diff --stat` empty); `tests/tasks/test_ftf_0406.py`/
    `test_ftf_0401.py`/`test_ftf_0404.py`/`test_ftf_0405.py` all
    re-run "all checks passed" afterward — no regression.
  - **Live wiring confirmed by direct source read:**
    `apps/api/app/tools/tool_definition_schema.py`'s `ToolDefinition.
    __init__` calls `enforce_side_effect_classification` and raises
    `ToolDefinitionValidationError` on failure, all before
    `super().__init__(**kwargs)` runs — before any session or database
    is touched, exactly as claimed.
  - **Task contract re-pulled directly:** `python tools/r1_handoff.py
    packet FTF-0406 --plan FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance
    clause 1 ("Map real tool operations to read/write/financial/privileged
    risk with reviewed enforcement proof") met by the code confirmed
    above; clause 2 ("Independent review, exact merged commit, real
    relevant integration and negative-test evidence recorded") met by
    this review itself, now that CI is genuinely green on the merged
    commit; negative_acceptance ("Unclassified or unresolved sensitive
    tool cannot default to read-only; user mandate text cannot disable
    actual guard") proven both directly (dedicated tests for each half,
    confirmed present) and by both this review's own and the producer's
    fault injections. Dependencies FTF-0401/FTF-0404/FTF-0405 confirmed
    genuinely `CERTIFIED` in `docs/spec-matrix.yaml` before relying on
    them.
- No blocking defect found in the fixed head. Merged PR #153 (merge
  commit `80dcd4b2a66c700e64e7c997b693f68a05103784`).
  `docs/spec-matrix.yaml` set to `certification_state: CERTIFIED` /
  `proof_state: L2-CERTIFIED`, `evidence_hash` set to the merge commit.
  Certified: 32/300, Product-true: 9/9.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0407 — Critical behavior requirements
- Criterion ID: FTF-0407
- PR: https://github.com/SJZLab/Sovra/pull/156 (`claude/wizardly-fermat-i56nvv` → `main`)
- Merge commit: 5da3834194b0e2b8a84cdd3e7161ff8d43a29885
- Implementation commit: 7a3305718278c638236e47eae7c49ca92f0a0019 (CI-gate
  fix commit: 00641ac0f1e0770eb38e8d4e275766ce6e76150b)
- Validated-by: session https://claude.ai/code/session_0185UQFPYhDkeasgkhTHrE5K
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #156)
- Date: 2026-09-21
- **Session-ID check:** all 4 commits on PR #156 carry `Claude-Session:
  .../session_01QNESA4CVHysioQybmQCNPB`; this reviewing session's own ID
  is `session_0185UQFPYhDkeasgkhTHrE5K` — no match, independent per Law 4.
- **CI checked on the real head, not a stale claim:** subscribed to the
  PR's own activity (16 checks were still `in_progress` when this review
  first looked, immediately after the PR opened). The producer session
  independently pushed a real fix (`00641ac`) for a genuine `ruff`
  import-sort failure (`tests/integration/
  test_ftf_0407_critical_behavior_requirements.py`'s own import block,
  un-sorted because the producer's own pre-push `ruff check` run had been
  scoped to `apps/api` only, missing the repo-root `tests/` tree CI
  actually lints) and a genuine `mypy` dict-variance error in
  `tests/tasks/test_ftf_0407.py`'s own unannotated `_make_tool` schema
  dict literals. Pulled the real check-run list after the `check_suite.
  completed` webhook fired: all 16 GitHub Actions check runs on the real
  final head (`00641ac`, run `35550374321`) `completed`/`success`,
  `gate` included.
- **Diff read directly:** `git diff` against the real PR base
  (`f73bbd6ced4d9eaaa95b177ba05dcdee025d9ff5`) — 13 files, no `alembic`
  migration file among them (`database_changes` charter honored). Read
  `apps/api/app/services/critical_behavior_requirements.py` in full and
  the exact `ToolDefinition.__init__` wiring in
  `apps/api/app/tools/tool_definition_schema.py`: `enforce_side_effect_
  classification` (a pure validator that raises rather than mutating
  `kwargs`) runs first, so `requires_confirmation` is already
  structurally consistent with `side_effect_class` by the time
  `enforce_critical_behavior_requirement` reads it — no staleness gap.
  Confirmed `apply_new_version` (`tool_definition_crud.py`) always
  constructs a fresh `ToolDefinition(...)` with `is_critical` present, so
  the guard runs on both the create and the edit path, not create-only.
- **Own fault injection, this session, distinct run from the producer's
  own two** (`git worktree add` on the real merged head, `uv sync
  --all-packages`), three vectors, each reverted byte-identical
  (`git checkout --`) and reconfirmed clean immediately after:
  1. Disabled the unmeasurable (`read_only`) refusal (`if False and rank
     == RISK_RANK["read_only"]:`) — 1 test failed for real
     (`the same refusal holds even if requires_confirmation is
     (meaninglessly) set True`).
  2. Disabled the conflicting-claim (`requires_confirmation`) refusal
     (`if False and not requires_confirmation:`) — 3 tests failed for
     real, including the direct negative_acceptance check and the
     higher-rank defense-in-depth check.
  3. Disabled the live wiring call site itself (`if False and
     "is_critical" in kwargs:`) — 2 tests failed for real, including the
     direct real-`ToolDefinition`-construction proof.
- **No regression, independently re-run:** `tests/tasks/
  test_ftf_0401.py`/`test_ftf_0404.py`/`test_ftf_0405.py`/
  `test_ftf_0406.py`/`test_ftf_0407.py` — all five "all checks passed."
  This task's own real live-Postgres suite
  (`tests/integration/test_ftf_0407_critical_behavior_requirements.py`)
  run against a freshly created local PostgreSQL 16 database/role —
  "all checks passed" (15/15 real checks, a real uvicorn server, both
  routers). FTF-0404's own live-Postgres suite
  (`tests/integration/test_ftf_0404_tool_schema_crud.py`) separately
  re-run against another freshly recreated database/role — "all checks
  passed," no regression.
- **Static checks re-run clean, this session, from the repo root**
  (matching CI's own working directory): `ruff check .` / `ruff format
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
  dependency, not the reverse — the deferral is real, not a dodge).
  Acceptance clause 2 (independent review, exact merged commit, real
  integration/negative-test evidence) satisfied by this review itself.
  negative_acceptance ("Unmeasurable or conflicting critical mandate
  blocks activation; no model-generated self-certification") proven both
  directly (dedicated tests for each half, confirmed present in both
  suites) and by this review's own three fault injections above.
  Dependencies FTF-0401/FTF-0406 confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- **No blocking defect found.** Merged PR #156 (merge commit
  `5da3834194b0e2b8a84cdd3e7161ff8d43a29885`). `docs/spec-matrix.yaml`
  set to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit. Ran `sync_certified_status()`
  (`scripts/task_claim.py`) to write the real status into
  `spec/tasks.json`/`docs/implementation/TRACKER.json` — `['FTF-0407']`
  updated. Certified: 33/300, Product-true: 10/10.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0403 — Budget policy
- Criterion ID: FTF-0403
- PR: https://github.com/SJZLab/Sovra/pull/163 (`claude/wizardly-fermat-zl5g8t` → `main`)
- Merge commit: 4afab390b60028cccc489c439d8401252a2c1d8f
- Implementation commit: 48eb4c6cb46fe2aca621d1c9fed8d3438ed7dce8
- Validated-by: session https://claude.ai/code/session_01VV6XRzSwbobfnvyZfLfg6q
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #163)
- Date: 2026-09-21
- **Session-ID check:** all 3 commits on PR #163 carry `Claude-Session:
  .../session_017jneh8CPxEPmfPrbC5PJrg`; this reviewing session's own ID
  is `session_01VV6XRzSwbobfnvyZfLfg6q` — no match, independent per Law 4.
- **CI checked on the real head, not a stale claim:** all 17 GitHub
  Actions check runs on the real final head (`6a59384e09326c8cecfd0e3dd78472376486b0df`,
  run `35592841192`) `completed`/`success`, `gate` included. Pulled the
  real `gate` job log directly (`get_job_logs`): `Product-truth: 11/11
  product-true`, `Status: 33/300 certified · 10 product-true`
  (status.py's own stricter CERTIFIED-only count, still 10 at CI time —
  expected, since certification had not yet landed).
- **Diff read directly:** `apps/api/app/budget/budget_policy_crud.py`,
  `budget_policy_schema.py`, `budget_policy_routes.py`,
  `apps/api/alembic/versions/0007_budget_policies.py`, `apps/api/app/main.py`.
  Confirmed `reserve_budget_allocation`/`release_budget_allocation` are
  each a single conditional SQL `UPDATE` (`WHERE reserved_amount +
  :amount <= total_amount` / `WHERE reserved_amount - :amount >= 0`),
  never read-then-write; confirmed the real `CHECK
  (reserved_amount <= total_amount)`/`CHECK (reserved_amount >= 0)`/
  `UNIQUE(project_id)` constraints in the migration as an independent
  DB-level backstop; confirmed `app.main` live-wires
  `build_budget_policy_router` by direct source read.
- **Task contract re-pulled directly, not trusted from the producer's own
  claim:** `python tools/r1_handoff.py packet FTF-0403 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — the committed
  `docs/implementation/task-packets/FTF-0403.md` matched the tool's own
  deterministic output byte-for-byte (the tool's own exclusive-create
  check passed without raising "Refusing to overwrite different
  content"). Acceptance clause 1 ("Persist decimal budget authorization,
  cost currency, scopes and approval history") met by the real
  `BudgetPolicy` table (`total_amount` a genuine `Numeric(18,4)`,
  `currency`/`scopes_json` closed vocabularies) plus FTF-0104's own
  already-CERTIFIED `audit_events` table carrying approval history.
  negative_acceptance ("Negative/unknown rates and concurrent allocation
  beyond available reservation cannot be accepted") proven both directly
  (validation guards, dedicated tests) and by this review's own fault
  injections below.
- **Own reproduction, this sandbox** (`git worktree add` on the real
  merged PR head, `uv sync --all-packages`, a freshly created local
  PostgreSQL 16 database/role matching CI's own `sovra_migrate`/
  `sovra_app` credentials): `tests/tasks/test_ftf_0403.py` — "all checks
  passed"; `tests/integration/test_ftf_0403_budget_policy.py` against the
  real database — "all checks passed", including the real concurrent-HTTP
  reservation proof (two simultaneous over-budget reservations, exactly
  one 200/one 409).
- **Own fault injection, this session, distinct run from the producer's
  own two, both reverted byte-identical and reconfirmed clean afterward:**
  1. Disabled the reserve headroom guard (removed the
     `reserved_amount + amount <= total_amount` WHERE clause) — 2 checks
     failed for real: the over-headroom-refusal check and the central
     concurrency negative_acceptance check (both reservations committed,
     `sorted(statuses) != [200, 409]`); independently, the real DB
     `CHECK (reserved_amount <= total_amount)` constraint itself raised
     `IntegrityError` — a genuine second, independent backstop.
  2. Disabled the "project already has a budget policy" guard in
     `create_budget_policy` (`if False and project.budget_policy_id is
     not None:`) — 1 check failed for real (duplicate-create-refusal
     never returned 409), independently caught by the real DB
     `UNIQUE(project_id)` constraint itself (`IntegrityError`).
- **No regression, independently re-run:** `tests/tasks/
  test_ftf_0401.py`/`test_ftf_0402.py`/`test_ftf_0404.py`/
  `test_ftf_0405.py`/`test_ftf_0406.py`/`test_ftf_0407.py` — all six
  "all checks passed".
- **Static checks re-run clean, this session:** `ruff check .`/`ruff
  format --check .` clean; `mypy .` (from `apps/api`) — 2 errors, both
  the pre-existing, unrelated `authlib.jose` missing-stub gap in
  `oidc_login.py`, confirmed identical on `main`. `scripts/
  validate_spec.py --strict` clean after `git fetch --unshallow` (this
  sandbox's own documented shallow-clone artifact). `scripts/
  product_truth_check.py --fail-on-unwired` — own run, `11/11`
  product-true, matching the real CI log exactly.
- **CI gate-wiring fix confirmed live:** `tests/tasks/test_ftf_0406.py`/
  `test_ftf_0407.py` steps present and `completed`/`success` in the real
  `gate` job's own step list (steps 51/52) — the disclosed, opportunistic
  CI gap-fix genuinely landed.
- Dependencies FTF-0104/FTF-0401 confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- No blocking defect found. Merged PR #163 (merge commit
  `4afab390b60028cccc489c439d8401252a2c1d8f`). `docs/spec-matrix.yaml`
  set to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit. Ran `sync_certified_status()`
  (`scripts/task_claim.py`) to write the real status into
  `spec/tasks.json`/`docs/implementation/TRACKER.json` — `['FTF-0403']`
  updated. Certified: 34/300, Product-true: 11/11.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-1701 — Reference agent specification
- Criterion ID: FTF-1701
- PR: https://github.com/SJZLab/Sovra/pull/165 (`claude/wizardly-fermat-k8uxnh` → `main`)
- Merge commit: bbdf0c8278d39d1f3fa7ed87c11e240a109075a3
- Implementation commit: 408ac045c623112b25397443f665794af6207621
- Validated-by: session https://claude.ai/code/session_01HFJZbt5ECMgJbGqx6Yx7Tq
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #165)
- Date: 2026-09-21
- **Session-ID check:** both commits on PR #165 carry `Claude-Session:
  .../session_01WvavBhWpFYv4MAW3yytpgt`; this reviewing session's own ID
  is `session_01HFJZbt5ECMgJbGqx6Yx7Tq` — no match, independent per Law 4.
- **CI checked on the real head, not a stale claim:** subscribed while
  `docker-local-stack-integration` and `gate` were still `in_progress`;
  polled `get_check_runs` to completion — all 17 GitHub Actions check
  runs on the real final head (`f3d06e5e5fb581b243cbd511a91d45368a632ba4`,
  run `35603489043`) `completed`/`success`, `gate` included. Confirmed
  the new `FTF-1701 negative-test proof (Reference agent specification
  catches real defects, no DB required)` step (`uv run python3
  tests/reference/test_ftf_1701.py`) is genuinely present in
  `.github/workflows/ci.yml`, immediately after the FTF-0403 step.
- **Diff read directly:** `evals/reference/reference_agent_specification.py`
  (652 lines), `tests/reference/test_ftf_1701.py` (687 lines),
  `packages/contracts/openapi/17-interface.yaml`,
  `packages/contracts/jsonschema/17-domain.schema.json`. Confirmed the
  five `ReferenceToolSpec` rows (`search_records`, `get_record`,
  `create_note`, `request_approval`, `execute_approved_action`) construct
  real `app.tools.tool_definition_schema.ToolDefinition` instances;
  confirmed `_authorized_record` enforces both tenant and owner-subject
  scoping; confirmed `decide_approval`/`revoke_approval` are the only
  methods that can move an `ApprovalRequest` into
  `approved`/`denied`/`revoked`, and are not present in
  `_SPEC_BY_NAME`/`REFERENCE_TOOL_SPECS`, so `dispatch_tool_call` cannot
  reach them; confirmed `dispatch_tool_call` calls
  `validate_instance` against both `input_schema_json` and
  `output_schema_json` around the handler invocation; confirmed
  `17-interface.yaml` declares `paths: {}`.
- **Task contract re-pulled directly, not trusted from the producer's own
  claim:** `python tools/r1_handoff.py packet FTF-1701 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` into a fresh scratch directory — the
  committed `docs/implementation/task-packets/FTF-1701.md` matched the
  tool's own deterministic output byte-for-byte. Acceptance clause 1
  ("Freeze the minimum shared phase interface and domain schemas at the
  declared owner paths, with schema/client validation and no placeholder
  endpoints. Then: Specify bounded actual reference agent with approved
  tools, deterministic business state and explicit risk/language
  profile") met by the frozen `17-interface.yaml`/`17-domain.schema.json`
  files (zero paths, real schema/client validation exercised in the test
  suite) plus the real `ReferenceAgentProfile`/`ReferenceBusinessServer`.
  negative_acceptance ("Reference success cannot claim every arbitrary
  customer agent is production-qualified") met by construction:
  `qualifies_this_reference_workload_only` is always `True` with no
  constructor path to set it `False`. `files:` (all four listed:
  `evals/reference/reference_agent_specification.py`,
  `tests/reference/test_ftf_1701.py`,
  `packages/contracts/openapi/17-interface.yaml`,
  `packages/contracts/jsonschema/17-domain.schema.json`) and
  `dependencies:` (FTF-0407, FTF-0405) both confirmed exact matches;
  both dependencies confirmed genuinely `CERTIFIED`/`L2-CERTIFIED` in
  `docs/spec-matrix.yaml`. `shared_lock_required: contracts:17` confirmed
  as the first and only claim on contract slot 17 (grepped the full
  `docs/spec-matrix.yaml` for `contracts:17` — only this task's own note
  matches).
- **Own reproduction, this sandbox** (`uv sync --all-packages`, `pnpm
  install`, `git fetch --unshallow`): `tests/reference/test_ftf_1701.py`
  — "all checks passed" (24/24 no-DB checks).
- **Own fault injection, this session, distinct from the producer's own
  two, both reverted byte-identical and reconfirmed clean afterward:**
  1. Reproduced the producer's own vector (disabled
     `record.owner_subject_id != subject_id` in `_authorized_record`) —
     failed with exactly `FAIL [FTF-1701] cross-subject (IDOR) get_record
     within the same tenant is refused`, matching the producer's own
     claim exactly.
  2. A second, independent vector the producer did not claim: disabled
     the requester-only guard in `execute_approved_action`
     (`if False and approval.requested_by_subject_id != subject_id:`) —
     failed with exactly `FAIL [FTF-1701] only the original requester may
     execute their own approved action` — confirming the suite catches a
     real authorization bug beyond the two the producer itself exercised.
- **No regression, independently re-run:** full `tests/tasks/
  test_ftf_*.py` sweep (30 files) — all "all checks passed" or
  equivalent, after `pnpm install` resolved transient
  node_modules-missing failures in FTF-0002/FTF-0003/FTF-H001 (confirmed
  sandbox artifacts of this fresh checkout, not real regressions —
  passed clean once `node_modules` existed). `ruff check .`/`ruff format
  --check .`/`mypy .` clean repo-wide (137 source files). `pnpm run
  lint`/`typecheck`/`format:check` clean.
- **Static checks re-run clean, this session:** `scripts/
  validate_spec.py --strict` — 17 `stale_commit` defects present before
  `git fetch --unshallow` (this sandbox's own documented shallow-clone
  artifact, the same finding recorded against prior certifications),
  clean after. `scripts/product_truth_check.py --fail-on-unwired` — own
  run, `11/11` product-true, unchanged; `proving_symbol` confirmed
  deliberately empty in `docs/spec-matrix.yaml`, so 0/0 (no new
  proving_symbol added) is accepted as legitimate, not an unproven claim
  — this task owns no live HTTP route, matching the FTF-1401/FTF-1601
  precedent. `scripts/status.py` — own run, `34/300 certified` before
  this entry's own update (matches the producer's own pre-certification
  claim).
- Dependencies FTF-0407/FTF-0405 confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- No blocking defect found. Merged PR #165 (merge commit
  `bbdf0c8278d39d1f3fa7ed87c11e240a109075a3`). `docs/spec-matrix.yaml`
  set to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit. Ran `sync_certified_status()`
  (`scripts/task_claim.py`) to write the real status into
  `spec/tasks.json`/`docs/implementation/TRACKER.json` — `['FTF-1701']`
  updated. Certified: 35/300, Product-true: 11/11.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-1301 — CostProfile schema/UI
- Criterion ID: FTF-1301
- PR: https://github.com/SJZLab/Sovra/pull/168 (`claude/wizardly-fermat-togs1a` → `main`)
- Merge commit: 9a47458118ee46095270f4aa9758b5ec35efeea5
- Implementation commit: 61995ac572eb732392e698f53565a5fec3bf5064
- Validated-by: session https://claude.ai/code/session_01GWAaJpcZSaNqKJ9zmk2CDz
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #168)
- Date: 2026-09-21
- **Session-ID check:** both commits on PR #168 carry `Claude-Session:
  .../session_0175jrFsfGmpVqpjJFqPTiAE`; this reviewing session's own ID
  is `session_01GWAaJpcZSaNqKJ9zmk2CDz` — no match, independent per Law 4.
- **CI checked on the real head, not a stale claim:** the webhook fired at
  PR-open time, before CI had completed (`gate` still `queued`); polled
  `get_check_runs` to completion rather than trusting a self-report — all
  18 GitHub Actions check runs on the real head
  (`cadb3202103ab3c56811af722406bfb1bac38eeb`, run `35631327707`)
  `completed`/`success`, `gate` and the new `cost-profile-integration` job
  both included. The PR was still open/unmerged at review time (relay had
  not yet merged its own green PROVISIONAL submission) — merged it
  directly at the confirmed green head (`expectedHeadSha` pinned to
  `cadb3202...`) before certifying, matching CLAUDE.md's pre-authorized
  "merge on green + L1 doc (PROVISIONAL)" action; merging is a distinct
  step from certifying and does not substitute for the independent review
  below.
- **Diff read directly:** `apps/api/app/costs/cost_profile_schema.py`,
  `apps/api/app/costs/cost_profile_crud.py`,
  `apps/api/app/costs/cost_profile_routes.py`,
  `apps/api/alembic/versions/0008_cost_profiles.py`,
  `apps/web/components/cost-and-model-search/costprofile_schema_ui.tsx`,
  `packages/contracts/{openapi,jsonschema}/13-*`. Confirmed
  `is_certifiable_cheapest`'s three disqualifying conditions (`expired`,
  `unverified`, `zero_provider_rate_unconfirmed`) match the negative_
  acceptance clause exactly, including that a verified, unexpired
  `local_hardware` zero rate is NOT disqualified (the rule is
  provider-specific, not a blanket zero refusal); confirmed
  `validate_rate_amount` refuses negative amounts at the application
  layer; confirmed the migration FORCEs row-level security scoped to
  `app.current_organization_id`, grants `sovra_app` SELECT/INSERT/UPDATE
  only (no DELETE), and adds a real `CHECK(rate_amount >= 0)` constraint;
  confirmed `cost_profile_routes.py`'s `COST_PROFILE_ROLES` gates every
  route including reads and search to `organization_admin` alone, matching
  Section 45's literal role table (`FINE_TUNING_FACTORY_RELEASE_1.md:2551`
  — "organization_admin: connections, security profiles, cost profiles");
  confirmed `app.main` includes `build_cost_profile_router` for real.
- **Task contract re-pulled directly, not trusted from the producer's own
  claim:** `python tools/r1_handoff.py packet FTF-1301 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — the committed
  `docs/implementation/task-packets/FTF-1301.md` matched. Acceptance
  clause 1 ("Record dated provider/local hardware TCO rates/currency/
  source and display unknowns honestly") and negative_acceptance ("Token
  price zero is not local compute free; unverified/expired rate cannot
  certify cheapest") confirmed met by the code above; `files:`,
  `dependencies:` (FTF-0401, FTF-0403, FTF-0103, all three confirmed
  genuinely `CERTIFIED`), and `shared_lock_required: contracts:13`
  (confirmed the first and only claim on contract slot 13, grepped the
  full `docs/spec-matrix.yaml`) all confirmed.
- **Own reproduction, this sandbox** (`uv sync --all-packages`, `pnpm
  install`, `git fetch --unshallow`, a real directly-installed PostgreSQL
  16 cluster — no container-registry egress in this sandbox, same finding
  as every prior integration-tested task — migrated by the integration
  script itself via `SOVRA_DATABASE_URL`/`SOVRA_APP_DB_PASSWORD`):
  `tests/tasks/test_ftf_1301.py` — "all checks passed"; `tests/
  integration/test_ftf_1301_cost_profile.py` against the live database —
  "all checks passed".
- **Own fault injection, this session, reproducing both of the producer's
  own vectors, both reverted byte-identical (`diff` against the
  pre-injection copy confirmed empty) and the suites re-confirmed clean
  afterward:**
  1. Disabled the zero-provider-rate disqualification in
     `is_certifiable_cheapest` (`if False and profile.profile_type ==
     "provider" and ...`) — `tests/tasks/test_ftf_1301.py` failed with
     exactly `FAIL [FTF-1301] negative_acceptance: 'token price zero is
     not local compute free' -- a verified, unexpired provider rate of
     exactly zero still cannot certify cheapest`, exit code 1.
  2. Disabled the negative-rate application-layer guard in
     `validate_rate_amount` (`if False and value < 0:`) against the real
     live database — `tests/integration/test_ftf_1301_cost_profile.py`
     failed with exactly `FAIL [FTF-1301] a negative rate is refused
     (400)`; the raw INSERT reached the database and was independently
     refused a second time by the real `CHECK(rate_amount >= 0)`
     constraint (`sqlalchemy.exc.IntegrityError:
     ck_cost_profiles_rate_amount_non_negative`), confirming the
     defense-in-depth claim is real, not asserted.
- **No regression, independently re-run:** full `tests/tasks/
  test_ftf_*.py` sweep — all "all checks passed" or equivalent, after
  `pnpm install` resolved a pre-existing, undocumented frontend-dependency
  gap in this sandbox affecting FTF-0002/FTF-0003/FTF-H001 (confirmed a
  sandbox artifact — all three passed clean once `node_modules` existed,
  not a real regression). `ruff check .`/`ruff format --check .` clean
  repo-wide. `mypy .` (from `apps/api`) — 2 errors, both the pre-existing,
  unrelated `authlib.jose` missing-stub gap in `oidc_login.py`, confirmed
  identical on `main`. `pnpm run lint`/`typecheck`/`format:check` clean.
  `pnpm run test` (vitest) — 19/19 passed across all 5 spec files, no
  regression to FTF-0401's own suite.
- **Static checks re-run clean, this session:** `scripts/
  validate_spec.py --strict` — 18 `stale_commit` defects present before
  `git fetch --unshallow` (this sandbox's own documented shallow-clone
  artifact, the same finding recorded against prior certifications),
  clean after. `scripts/product_truth_check.py --fail-on-unwired` — own
  run, `12/12` product-true; `is_certifiable_cheapest` confirmed a
  genuine, non-test live caller in `cost_profile_crud.py`'s own
  `search_cost_profiles` (grepped for non-test callers directly — not
  0/0, not inflation). `scripts/status.py` — own run, `35/300 certified`
  before this entry's own update (matches the producer's own
  pre-certification claim).
- Dependencies FTF-0401/FTF-0403/FTF-0103 confirmed genuinely `CERTIFIED`
  in `docs/spec-matrix.yaml` before relying on them.
- No blocking defect found. Merged PR #168 (merge commit
  `9a47458118ee46095270f4aa9758b5ec35efeea5`). `docs/spec-matrix.yaml` set
  to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit. Ran
  `scripts.task_claim.sync_certified_status()` to write the real status
  into `spec/tasks.json`/`docs/implementation/TRACKER.json` —
  `['FTF-1301']` updated. Certified: 36/300, Product-true: 12/12.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0202 — Secret-manager integration
- Criterion ID: FTF-0202
- PR: https://github.com/SJZLab/Sovra/pull/176 (`claude/wizardly-fermat-3c0uoh` → `main`)
- Merge commit: 6dfd97d2444c43a9b981099c8bb9f60368ad8b21
- Implementation commit: c0c8409d80c1e2e0222f8fed6912dc0355a046d2
- Validated-by: session https://claude.ai/code/session_01GngkvFCtGtTnDnW9d2HC1j
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #186)
- Date: 2026-09-22
- **Session-ID check:** both commits on PR #176 carry `Claude-Session:
  .../session_01CKZy4kupyKhLZEjNUTzcpC`; this reviewing session's own ID
  is `session_01GngkvFCtGtTnDnW9d2HC1j` — no match, independent per Law 4.
  (A separate prior candidate certification, PR #179, was opened by yet a
  third session, `session_01DzyLpJHy35M2BeGRfHfwL5`, but was left open and
  unmerged, and had gone stale against `main`'s subsequent, unrelated
  FTF-R016 merge — this entry supersedes it with a fresh, independently
  reproduced review rather than trusting its prose; PR #179 is closed as a
  duplicate below.)
- **CI checked on the real head, not a stale claim:** `get_check_runs` on
  PR #176's real head (`c0c8409d80c1e2e0222f8fed6912dc0355a046d2`, run
  `35690059480`) — all 19 GitHub Actions jobs `completed`/`success`,
  including the dedicated `secret-manager-integration` job, which ran
  05:38:11Z–05:43:36Z (~5m25s) — a genuine real-execution duration
  consistent with building the real, pinned OpenBao commit from source on
  the hosted runner's own network, not an instant runner-provisioning
  "ghost" job (this repo's own documented failure mode elsewhere: `gate`
  itself also genuinely ran 05:38:47Z–05:40:59Z). PR #176 was already
  merged directly to `main` before this review began (owner action, not
  this session's).
- **Diff read directly:** `packages/litellm_client/secret_manager_integration.py`
  (the only production file). Confirmed: stdlib-only (`urllib.request`,
  no `hvac`/`requests`/`sqlalchemy`/`fastapi` import anywhere in the
  module); the `openbao://<mount>/<path>/<field>` ref shape is a strict
  subset of FTF-0201/FTF-0005's own already-certified, frozen
  `^[a-z]+://[A-Za-z0-9_\-./]+$` pattern (neither certified validator
  touched); `_request` retries only 429/5xx up to 3 attempts honoring a
  real `Retry-After` header, and never retries 403/404/2xx/other 4xx;
  `store_secret_ref`/`rotate_secret_ref` read-then-merge existing fields
  at a path before writing (never silently wipe a co-located field);
  `rotate_secret_ref` reads the current version first, writes the new
  value, then soft-deletes exactly the superseded version (never a
  hard-coded number) and refuses to rotate a ref with nothing to rotate;
  `resolve_secret_ref` enforces `SecretExpiredError` from a
  Factory-recorded `custom_metadata.expires_at` since OpenBao's own KV v2
  secrets never expire on their own.
- **Own reproduction, this sandbox** (fresh git worktree at the real
  merged head, `sha256sum CLAUDE.md` matched `CLAUDE.md.sha256` first):
  `python3 tests/tasks/test_ftf_0202.py` — "all checks passed" (22/22),
  no DB/network required for this suite (fake `urllib.request.urlopen`
  transport). The paired real-backend suite,
  `tests/integration/test_ftf_0202_secret_manager.py` (12 checks against
  a real, locally-built OpenBao `-dev` server), was not independently
  rebuilt from source in this review session — relied instead on CI's own
  real, non-ghost `secret-manager-integration` job run above, which
  executes this exact suite fresh on every PR; this is a resource-gate
  judgment call disclosed here rather than silently assumed.
- **Own fault injection, this session, a vector distinct from both the
  producer's own two and PR #179's own one (403 mapping):** disabled the
  field-presence guard in `resolve_secret_ref` (`if not isinstance(fields,
  dict) or parsed.field not in fields:` → `if False and (...)：`) —
  `tests/tasks/test_ftf_0202.py` failed with exactly `FAIL [FTF-0202]
  resolve_secret_ref raises SecretNotFoundError when the path exists but
  the ref's own field is absent from it`. Reverted; `diff` against the
  pre-injection copy confirmed byte-identical; suite re-confirmed clean
  immediately after.
- **Static checks re-run clean, this session:** `scripts/
  validate_spec.py --strict` — 19 `stale_commit` defects present before
  `git fetch --unshallow origin` (this sandbox's own documented
  shallow-clone artifact, the same finding recorded against every prior
  certification in this ledger), clean after. `scripts/
  product_truth_check.py --fail-on-unwired` — own run, `13/13`
  product-true (this count reflects `main`'s current tip, which already
  includes unrelated work merged after FTF-0202 — FTF-0202 itself
  legitimately contributes `proving_symbol: ''`, not a live route yet;
  confirmed not 0/0 inflation, since it was never expected to move this
  count). `docs/spec-matrix.yaml` confirmed `certification_state:
  PROVISIONAL` on `main` before this entry's own update, matching the
  producer's own claim.
- Dependencies FTF-0005/FTF-0103/FTF-0201 confirmed genuinely `CERTIFIED`
  in `docs/spec-matrix.yaml` before relying on them.
- **Task contract re-pulled** via `tools/r1_handoff.py packet FTF-0202
  --plan FINE_TUNING_FACTORY_RELEASE_1.md`: both acceptance clauses
  ("Store/resolve/rotate short-lived secret refs through the actual
  selected secret backend"; "Independent review, exact merged commit,
  real relevant integration and negative-test evidence recorded") and the
  full negative_acceptance ("Unknown/expired/ref-denied secrets block
  call and are never returned to browser or plaintext DB") confirmed
  genuinely met by the diff and this review, not merely "tests pass".
- No blocking defect found. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit
  (`6dfd97d2444c43a9b981099c8bb9f60368ad8b21`). Ran
  `scripts.task_claim.sync_certified_status()` to write the real status
  into `spec/tasks.json`/`docs/implementation/TRACKER.json` —
  `['FTF-0202']` updated. Certified: 37/300, Product-true: 13/13 (both
  counts reflect `main`'s current tip, which had already advanced past
  FTF-1302/FTF-R016 merging by the time of this review).

**Conclusion:** Certified L2-CERTIFIED.

## FTF-1001 — ModelVersion schema/API
- Criterion ID: FTF-1001
- PR: https://github.com/SJZLab/Sovra/pull/160 (`claude/wizardly-fermat-4ez7fq` → `main`)
- Merge commit: 8b51c85edd35652ba3263dd6a6fcbf590f2f81bd
- Implementation commit: bd5380256d8bfc0700d3bdb95a02a68cf10e4403
- Validated-by: session https://claude.ai/code/session_01GngkvFCtGtTnDnW9d2HC1j
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #186)
- Date: 2026-09-22
- **Session-ID check:** all four PR #160 commits carry `Claude-Session:
  .../session_017j5b6PyPwyvoYyubyzrAcr`; this reviewing session's own ID
  is `session_01GngkvFCtGtTnDnW9d2HC1j` — no match, independent per Law 4.
  A separate stale, unmerged candidate certification (PR #181, opened by a
  third session `session_01DzyLpJHy35M2BeGRfHfwL5`) is superseded/closed
  by this entry rather than trusted at face value.
- **CI checked on the real head:** `get_check_runs` on PR #160's real head
  (run `35703139831`) — 22/22 jobs `completed`/`success`, with real,
  non-instant per-job durations (12s–390s, `gate` 94s,
  `model-version-schema-api-integration` 27s) — no runner-provisioning
  ghost-job pattern.
- **Diff read directly:** `apps/api/app/models/model_version_schema.py`,
  `model_version_crud.py`, `model_version_routes.py`,
  `apps/api/alembic/versions/0009_model_versions.py`,
  `packages/contracts/{openapi,jsonschema}/15-*`, `apps/api/app/main.py`
  wiring. Confirmed: `ModelVersion` is immutable by construction (no
  edit/delete route; migration grant is SELECT/INSERT only); an `endpoint`
  import structurally forbids local-weight fields both in
  `ModelVersion.__init__` and via a real `ck_model_versions_kind_consistency`
  CHECK constraint; a `full_weight`/`adapter` import's parent
  `TrainableArtifact` must be `ready`; the parent must belong to the same
  organization, reusing FTF-0301's own already-certified
  `get_artifact`/`authorize_resource_ownership`.
- **Own reproduction, this sandbox** (fresh git worktree at the real
  merged head): `tests/tasks/test_ftf_1001.py` — "all checks passed";
  `tests/integration/test_ftf_1001_model_version_schema_api.py` against a
  freshly created, isolated local PostgreSQL 16 cluster (to avoid
  colliding with other concurrent certifier work sharing this sandbox) —
  "all checks passed". Single Alembic head confirmed (`0009_model_versions`,
  `down_revision: 0008_cost_profiles`, no fork).
- **Own fault injection, this session, a vector distinct from all three
  of the producer's own and from PR #181's own vector:** disabled the
  cross-project-within-same-org scoping guard in `model_version_crud.py`'s
  `_fetch_or_raise` (`if row.project_id != project_id:` → `if False and
  ...`) — exactly one named check failed for the right reason: a model
  version belonging to project1, requested under a different project in
  the same organization, was no longer denied (expected 404, same as
  missing). Reverted; `diff` against the pre-injection copy confirmed
  byte-identical; suite re-confirmed clean.
- **Static checks re-run clean, this session:** `scripts/
  validate_spec.py --strict` clean (repo not shallow — no unshallow-fetch
  needed). `scripts/product_truth_check.py --fail-on-unwired` — own run,
  `13/13`; confirmed `build_model_version_router` is a genuine, non-test
  live caller (`apps/api/app/main.py` calls it unconditionally inside
  `create_app()`, alongside every other product-runtime router — not a
  0/0 or existence-only claim).
- Dependencies FTF-0301/FTF-0401/FTF-0103 confirmed genuinely `CERTIFIED`
  in `docs/spec-matrix.yaml` before relying on them.
- **Task contract re-pulled** via `tools/r1_handoff.py packet FTF-1001
  --plan FINE_TUNING_FACTORY_RELEASE_1.md`: both acceptance clauses and
  the full negative_acceptance ("Required nonexistent weights/hash for
  endpoint import, mutable production artifact or cross-tenant parent
  denied") confirmed genuinely met by the diff, all three denial paths
  verified directly in code, not merely "tests pass".
- **Evidence-count correction (disclosed, not hidden):** PR #160/PR #181
  both record "24 no-DB test functions" and "26 real live-Postgres
  checks." Independently counted on the real merged code:
  `tests/tasks/test_ftf_1001.py` has 21 `def test_*` functions (43
  individual `check()` calls once a loop over `ALLOWED_IMPORT_KINDS` is
  accounted for), and the integration suite executes 19 `check()` calls
  at runtime, not 26. Both suites genuinely pass and genuinely cover the
  acceptance/negative_acceptance surface — this is a documentation
  inaccuracy in the recorded counts, not a fabricated pass. Corrected in
  `docs/spec-matrix.yaml`'s own FTF-1001 notes rather than propagated
  further, per Law 1's "evidence bound accurately" requirement.
- No blocking defect found. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit
  (`8b51c85edd35652ba3263dd6a6fcbf590f2f81bd`). Ran
  `scripts.task_claim.sync_certified_status()` to write the real status
  into `spec/tasks.json`/`docs/implementation/TRACKER.json` —
  `['FTF-1001']` updated. Certified: 38/300, Product-true: 13/13.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-1302 — Local compute costing
- Criterion ID: FTF-1302
- PR: https://github.com/SJZLab/Sovra/pull/170 (`claude/wizardly-fermat-c7g9v1` → `main`)
- Merge commit: acce2b4d9a3d636b06065f11de3958239ba5b917
- Implementation commit: 2ebf9788ac644fc250e8cc9bc621a42815b549f7
- Validated-by: session https://claude.ai/code/session_01GngkvFCtGtTnDnW9d2HC1j
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #186)
- Date: 2026-09-22
- **Prior history:** reviewed sound but NOT CERTIFIED on 2026-09-21 solely
  because GitHub Actions could not show a genuine passing CI run — a
  runner-provisioning outage (ghost jobs: `runner_id: 0`, ~3s instant
  "completions"), not a code defect.
- **Session-ID check:** PR #170's commits carry `Claude-Session:
  .../session_01Fy9YNo5oaA2HZLWRud1Xma`; this reviewing session's own ID
  is `session_01GngkvFCtGtTnDnW9d2HC1j` — no match, independent per Law 4.
  A separate stale, unmerged candidate certification (PR #184, opened by
  a third session `session_01DzyLpJHy35M2BeGRfHfwL5`) is superseded/
  closed by this entry rather than trusted at face value.
- **CI outage genuinely re-confirmed cleared, not trusted from prose:**
  `get_check_runs` on PR #170's real merge-head run — 23/23 jobs
  `success`, real distinct non-zero `runner_id`s, genuine multi-second/
  minute per-job durations (`secret-manager-integration` ~5m43s) —
  structurally the opposite of the prior ghost-job signature.
- **Diff read directly:** `apps/api/app/costs/local_compute_costing.py`
  (or equivalent module path) — a pure computation module over FTF-1301's
  already-frozen `CostProfile` contract, no database/network/GPU touch of
  any kind. `window_total_cost` sums the four Section-32 cost categories
  once each; `cost_per_success` divides by unique `pass`-outcome
  `request_id`s (retry-dedup), matching the golden example ($12+$2+$1 /
  100 → $0.15).
- **Own reproduction, this sandbox** (fresh git worktree at the real
  merged head): `tests/tasks/test_ftf_1302.py` — "all checks passed".
- **Own fault injection, this session:** disabled the retry-dedup in
  `unique_successful_task_ids`, making every `pass` record count
  separately instead of collapsing repeated `request_id`s — two named
  checks failed for exactly the right reason ("a retried request_id that
  eventually passes counts once, not once per attempt"; "only
  pass-outcome requests count toward unique_successful_tasks"). Reverted;
  `diff` against the pre-injection copy confirmed byte-identical; suite
  re-confirmed clean.
- **Static checks re-run clean, this session:** `scripts/
  validate_spec.py --strict` clean. `scripts/product_truth_check.py
  --fail-on-unwired` — own run, `13/13`; FTF-1302's own `proving_symbol`
  confirmed legitimately `''` (no live HTTP route — consumes FTF-1301's
  frozen contract slot as a pure computation).
- Dependencies FTF-0006/FTF-1301 confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- **Task contract re-pulled** via `tools/r1_handoff.py packet FTF-1302
  --plan FINE_TUNING_FACTORY_RELEASE_1.md`: both acceptance clauses
  ("Allocate real total service-window GPU/CPU/storage/idle cost to
  declared completed workload"; independent review clause) and the
  negative_acceptance ("Per-request overlapping GPU seconds cannot double
  count batches or omit idle/multi-replica capacity") confirmed genuinely
  met by direct code read — `cost_per_success`/`window_total_cost` never
  touch per-request GPU-second fields at all, ruling out double-counting
  by construction.
- **Documentation-accuracy correction (disclosed, not hidden):** PR
  #184's own body/commit message attributed `main`'s `product_truth_
  check.py` count rising from 12 to 13 to FTF-1302's own certification.
  Independently confirmed this is inaccurate: the 13/13 figure was
  already reached on `main` before FTF-1302's certification, driven by
  other already-certified/wired symbols entirely unrelated to this task;
  FTF-1302 contributes `proving_symbol: ''` either way. Not repeated in
  this entry's own count language.
- No blocking defect found. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit
  (`acce2b4d9a3d636b06065f11de3958239ba5b917`). Ran
  `scripts.task_claim.sync_certified_status()` to write the real status
  into `spec/tasks.json`/`docs/implementation/TRACKER.json` —
  `['FTF-1302']` updated. Certified: 39/300, Product-true: 13/13.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-1401 — API load test
- Criterion ID: FTF-1401
- PR: https://github.com/SJZLab/Sovra/pull/161 (`claude/wizardly-fermat-uec7js` → `main`)
- Merge commit: c2fda5b6ad472aa6d38a977896b339a484a9a483
- Implementation commit: 4000f0d5c94a789cc4f3971cd4321716e0dde1e0
- Validated-by: session https://claude.ai/code/session_01GngkvFCtGtTnDnW9d2HC1j
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #186)
- Date: 2026-09-22
- **Session-ID check:** all four PR #161 commits carry `Claude-Session:
  .../session_01CDP3isFaUkJtHPU91hNz2j`; this reviewing session's own ID
  is `session_01GngkvFCtGtTnDnW9d2HC1j` — no match, independent per Law 4.
  A separate stale, unmerged candidate certification (PR #182, opened by
  a third session `session_01DzyLpJHy35M2BeGRfHfwL5`) is superseded/
  closed by this entry rather than trusted at face value.
- **CI checked on the real head:** `get_check_runs` on PR #161's real
  head (run `35704415542`) — 23/23 jobs `completed`/`success`, with real,
  non-instant per-job durations (e.g. `api-load-test-integration` 47s,
  `gate` 117s, `secret-manager-integration` ~7m8s) — no
  runner-provisioning ghost-job pattern.
- **Diff read directly:** `tests/performance/api_load_test.py` drives the
  real, already-live project/tool routers over a real uvicorn HTTP round
  trip and a real local PostgreSQL database. Disclosed, honest partial
  profile against Section 49's full "Control API" target (smaller
  process/DB/org counts and duration), `WorkloadSpec.is_partial_profile`
  always true with `disclosed_gaps` naming every reduction. "No lost/
  duplicate admitted jobs" substituted with a real, independently
  re-queried DB-count-vs-response-count check, disclosed as narrower than
  full idempotency-key dedup (a later task's own scope).
  `negative_acceptance` ("no ... dropped timed-out requests") enforced by
  construction: `execute_request` always returns exactly one
  `RequestSample` per request, including a failed connection.
- **Own reproduction, this sandbox** (fresh git worktree at the real
  merged head): `tests/tasks/test_ftf_1401.py` — "all checks passed"
  (17 no-DB test functions).
- **Own fault injection, this session, a vector distinct from the
  producer's own two and PR #182's own one (the write/submit P95
  branch):** disabled the read P95 threshold branch in
  `evaluate_acceptance` (`if read_stats is not None and
  read_stats.p95_ms > thresholds.read_p95_ms:` → `if False and ...`) —
  the named "read P95 above threshold fails, and the reason names the
  read P95 clause" check failed for exactly the right reason. Reverted;
  `diff` against the pre-injection copy confirmed byte-identical; suite
  re-confirmed clean.
- **Static checks re-run clean, this session:** `scripts/
  validate_spec.py --strict` clean. `scripts/product_truth_check.py
  --fail-on-unwired` — own run, `13/13`; FTF-1401's own `proving_symbol`
  confirmed legitimately `''` (a measurement/load-test tool with no new
  served route — `paths: {}` in its own frozen OpenAPI contract).
- Dependencies FTF-0006/FTF-0401/FTF-0106 confirmed genuinely `CERTIFIED`
  in `docs/spec-matrix.yaml` before relying on them. **Confirmed FTF-1001
  is not actually a declared dependency of FTF-1401 at all** — neither in
  the real task contract's `dependencies` list nor in code (no
  `model_version`/`ModelVersion` import anywhere in the task's source,
  only disclosure text explaining "Models" traffic is deliberately
  excluded from the request mix because FTF-1001 was not yet certified at
  submission time). This is an honest scope reduction, not a
  relied-upon dependency — FTF-1001's own prior `PROVISIONAL` state was
  never a disqualifying gap for this task (and FTF-1001 is itself now
  `CERTIFIED` as of this same shift, regardless).
- **Task contract re-pulled** via `tools/r1_handoff.py packet FTF-1401
  --plan FINE_TUNING_FACTORY_RELEASE_1.md`: both acceptance clauses and
  the negative_acceptance confirmed genuinely met by the diff.
- **Disclosed, unresolved gap noted but out of this task's own scope:**
  FTF-0406/FTF-0407's own no-DB and live-Postgres test suites remain
  wired into neither `ci.yml`'s `gate` job nor a dedicated integration
  job — their own CERTIFIED evidence was genuinely reproduced by their
  own certifier reviews, but is not continuously re-enforced by CI going
  forward the way every other CERTIFIED task's is. Flagged again here for
  a future relay/control-plane shift; not this task's own fix to make.
- No blocking defect found. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit
  (`c2fda5b6ad472aa6d38a977896b339a484a9a483`). Ran
  `scripts.task_claim.sync_certified_status()` to write the real status
  into `spec/tasks.json`/`docs/implementation/TRACKER.json` —
  `['FTF-1401']` updated. Certified: 40/300, Product-true: 13/13.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-1501 — Tenant isolation suite
- Criterion ID: FTF-1501
- PR: https://github.com/SJZLab/Sovra/pull/159 (`claude/wizardly-fermat-bhjs9m` → `main`)
- Merge commit: 8b8a9c12e04277a62149ab28de2c9c2db3a9ff16
- Implementation commit: 778f6f29c5f994495ceee7446203d688624eb216
- Validated-by: session https://claude.ai/code/session_01GngkvFCtGtTnDnW9d2HC1j
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #186)
- Date: 2026-09-22
- **Session-ID check:** all four of PR #159's original commits carry
  `Claude-Session: .../session_01X4QYt5B4DQroufdM796nBD`; this reviewing
  session's own ID is `session_01GngkvFCtGtTnDnW9d2HC1j` — no match,
  independent per Law 4. A separate stale, unmerged candidate
  certification (PR #180, opened by a third session
  `session_01DzyLpJHy35M2BeGRfHfwL5`) is superseded/closed by this entry
  rather than trusted at face value.
- **Process note, disclosed not hidden:** PR #180's own certifying
  session pushed 2 further commits directly onto PR #159's branch (a
  merge-to-main conflict resolution and a YAML bug fix) before merging —
  infrastructure-only, does not touch the actual security-test code, and
  does not compromise independence (all four original commits still
  carry only the producer's own session ID).
- **CI checked on the real head:** `get_check_runs` on PR #159's real
  final head (run `35701775852`) — 21/21 jobs `completed`/`success`, real
  non-instant per-job durations (`secret-manager-integration` ~7m,
  `gate` ~2m9s). An earlier "0 jobs parsed" run on an intermediate commit
  was a real, disclosed transient failure, not the final merged state.
- **Own reproduction, this sandbox:** stood up a fully isolated local
  PostgreSQL role/database from scratch (temporarily renaming the
  cluster's shared `sovra_app` role aside so migration 0001's
  unconditional `CREATE ROLE` could run clean, then restoring it
  byte-for-byte afterward — no other database's grants touched, only a
  rename which preserves OID-based dependencies).
  `tests/tasks/test_ftf_1501.py` (no-DB) — "all checks passed";
  `tests/security/tenant_isolation_suite.py` against the real, freshly
  migrated database — "all checks passed (25/25)".
- **Own fault injection, this session, reproducing the producer's own
  documented vector for real (not merely re-described):** disabled the
  application-level ownership check in `authorize_resource_ownership`
  (`if row is None or row[0] != context.organization_id:` → `if False
  and (...)：`), leaving the real Postgres RLS policy on each table
  untouched. Result: exactly 25/25 → 21/25, with precisely the 4 named
  `cross_tenant_db_read_denied` checks failing (connection/artifact/
  project/tool), each real HTTP-equivalent `200` instead of denied —
  confirming genuine **defense-in-depth**: RLS alone already blocks a
  cross-tenant read, so disabling only the application-level check does
  not leak by itself in general, but this specific check's own
  `authorize_resource_ownership` call path is independently proven
  load-bearing for the four resource kinds it directly gates. Reverted
  the source file; `diff` against the pre-injection copy confirmed
  byte-identical; suite re-confirmed clean against a fresh database
  (25/25) immediately after.
- **Static checks re-run clean, this session:** `scripts/
  validate_spec.py --strict` clean. `scripts/product_truth_check.py
  --fail-on-unwired` — own run, `13/13`; FTF-1501's own `proving_symbol`
  confirmed legitimately `''` (a consolidated evidence suite over
  already-certified primitives, no new live-wiring claim of its own).
  `ruff check` on the touched file clean.
- Dependencies FTF-0106/FTF-H003 confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- **Task contract re-pulled** via `tools/r1_handoff.py packet FTF-1501
  --plan FINE_TUNING_FACTORY_RELEASE_1.md`: acceptance clause 1 ("real
  two-tenant API/DB/worker/storage/cache/model paths") is honestly
  narrowed to API+DB only — confirmed directly that
  `workers/{training,evaluation,data,deploy}` are still README/
  pyproject-only scaffolds and `packages/storage/tenant_policy.py` has
  zero live callers, so declaring worker/storage/cache/model checks would
  be fabricated evidence under Law 11; this narrowing is disclosed, not
  hidden. The negative_acceptance ("no mocked auth or superuser-only DB
  tests") holds by construction — `assert_not_superuser_role` is
  genuinely invoked against `pg_roles.rolsuper` at runtime, and all HTTP
  calls go through a real socket, never a mock.
- No blocking defect found. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit
  (`8b8a9c12e04277a62149ab28de2c9c2db3a9ff16`). Ran
  `scripts.task_claim.sync_certified_status()` to write the real status
  into `spec/tasks.json`/`docs/implementation/TRACKER.json` —
  `['FTF-1501']` updated. Certified: 41/300, Product-true: 13/13.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-1702 — Tool environment
- Criterion ID: FTF-1702
- PR: https://github.com/SJZLab/Sovra/pull/167 (`claude/wizardly-fermat-hlz51t` → `main`)
- Merge commit: 099a0753710ac6f2bb4cba68c209e99d3845e5d9
- Implementation commit: 2d36085547b597625ddcfc0feb394c0c4027f0be
- Validated-by: session https://claude.ai/code/session_01GngkvFCtGtTnDnW9d2HC1j
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #186)
- Date: 2026-09-22
- **Session-ID check:** PR #167's commits carry `Claude-Session:
  .../session_01CVWSRiMU43ZUy6H6C9FMez`; this reviewing session's own ID
  is `session_01GngkvFCtGtTnDnW9d2HC1j` — no match, independent per Law 4.
  A separate stale, unmerged candidate certification (PR #183, opened by
  a third session `session_01DzyLpJHy35M2BeGRfHfwL5`) is superseded/
  closed by this entry rather than trusted at face value.
- **CI checked on the real head:** `get_check_runs` on PR #167's real
  head (run `35705643744`) — 23/23 jobs `completed`/`success`, real,
  non-instant per-job durations (`secret-manager-integration` ~6.7
  minutes), no runner-provisioning ghost-job pattern.
- **Diff read directly:** `evals/reference/tool_environment.py` wraps
  FTF-1701's already-certified real dispatcher (`dispatch_tool_call`) for
  later qualification tasks to use. `dispatch()` refuses a tenant or
  subject this environment's own seed never declared before ever
  reaching the real dispatcher, its real authorization checks or its
  real idempotency cache.
- **Own reproduction, this sandbox** (fresh git worktree at the real
  merged head): `tests/reference/test_ftf_1702.py` — "all checks
  passed".
- **Own fault injection, this session, a vector distinct from the
  producer's own and PR #183's own (a `record_id_for` vector):**
  disabled the unseeded-subject guard in `ToolEnvironment.dispatch`
  (`if subject_id not in self._seed_spec.subjects_by_tenant.get(
  tenant_id, ()):` → `if False and ...`) — the named "dispatch refuses a
  subject never declared for an otherwise real, seeded tenant" check
  failed for exactly the right reason. Reverted; `diff` against the
  pre-injection copy confirmed byte-identical; suite re-confirmed clean.
- **Static checks re-run clean, this session:** `scripts/
  validate_spec.py --strict` clean. `scripts/product_truth_check.py
  --fail-on-unwired` — own run, `13/13`; FTF-1702's own `proving_symbol`
  confirmed legitimately `''` (pure test-infrastructure module with no
  live product route, wrapping FTF-1701's dispatcher for later
  qualification tasks — not gate-weakening). `ruff check` on the touched
  file clean.
- Dependencies FTF-0004/FTF-1701 confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- **Task contract re-pulled** via `tools/r1_handoff.py packet FTF-1702
  --plan FINE_TUNING_FACTORY_RELEASE_1.md`: both acceptance clauses and
  the negative_acceptance ("canned success tools / unguarded financial
  writes cannot satisfy the environment") confirmed genuinely met — no
  canned results anywhere in the module, real authorization/idempotency
  preserved underneath by construction.
- No blocking defect found. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit
  (`099a0753710ac6f2bb4cba68c209e99d3845e5d9`). Ran
  `scripts.task_claim.sync_certified_status()` to write the real status
  into `spec/tasks.json`/`docs/implementation/TRACKER.json` —
  `['FTF-1702']` updated. Certified: 42/300, Product-true: 13/13.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-R016 — Measure selected coding-model/executor pilot cost and enforce budget tree
- Criterion ID: FTF-R016
- PR: https://github.com/SJZLab/Sovra/pull/172 (`claude/wizardly-fermat-dtzhzo` → `main`)
- Merge commit: ce1d2f062a830cbb69bfd00353e329585f8efff5
- Implementation commit: c4f55353f6ffe712f3149cc5853445dfa3ccc2e0
- Validated-by: session https://claude.ai/code/session_01GngkvFCtGtTnDnW9d2HC1j
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #186)
- Date: 2026-09-22
- **Prior history:** reviewed sound but NOT CERTIFIED on 2026-09-21
  solely because GitHub Actions could not show a genuine passing CI run
  — a runner-provisioning outage, not a code defect.
- **Session-ID check:** PR #172's commits carry `Claude-Session:
  .../session_019xvKNprddWsv6qXhodDCkZ` and
  `.../session_01SwwQG6gYUUQiifu9yj3QYM`; this reviewing session's own
  ID is `session_01GngkvFCtGtTnDnW9d2HC1j` — no match, independent per
  Law 4. A separate stale, unmerged candidate certification (PR #185,
  opened by a fourth session `session_01DzyLpJHy35M2BeGRfHfwL5`) is
  superseded/closed by this entry rather than trusted at face value.
- **CI outage genuinely re-confirmed cleared:** `get_check_runs` on PR
  #172's real merge-head run — 23/23 jobs `success`, real distinct
  non-zero `runner_id`s and genuine multi-minute per-job durations
  (`secret-manager-integration` ~6m35s) — structurally the opposite of
  the prior ghost-job signature.
- **Diff read directly:** `tools/implementation/usage_ledger.py` — real
  per-task CPU/wall measurement via `resource.getrusage`, real retry
  counting, an independent accepted-task denominator sourced only from
  `TaskOrchestrator.WaveResult.approved_for_downstream` (never a task's
  own self-report), a delegated-tree `BudgetNode` reservation that always
  walks the live parent chain, `run_within_budget`'s genuine mid-execution
  process termination, `compare_to_baseline`, and
  `enforce_model_route_policy` for Section 81.3's private/air-gapped
  route policy. `token_cost_usd` is always `None` (disclosed resource-gate
  limitation — no live token-usage-reporting API in this sandbox).
- **Own reproduction, this sandbox** (fresh git worktree at the real
  merged head): `tests/implementation/test_executor_budget_limits.py` —
  "All 34 checks passed."
- **Own fault injection, this session, a vector distinct from the
  producer's own (`cost_per_success` zero-successes guard), the first
  reviewer's (`BudgetNode.available_usd()`/`enforce_model_route_policy`),
  and PR #185's own (`BudgetNode._chain()`):** disabled the
  mid-execution termination guard in `run_within_budget` (`if elapsed >=
  budget_seconds:` → `if False and ...`). Reproduced for real with a
  bounded 2-second budget (`BudgetNode(limit_usd=Decimal("0.02"))` at
  `rate_usd_per_second=Decimal("0.01")`) against a real 5-second `sleep`
  subprocess: with the guard intact, the process is genuinely terminated
  (`BoundedTermination` raised) after ~2.0s; with the guard disabled, the
  same process runs the full ~5.0s to completion, completely undetected —
  a genuine budget overrun goes unflagged. The dedicated test suite
  caught this for real: 3 named checks failed ("a real long-running
  process is genuinely terminated once the tree budget is reached", "the
  process was cut off well before its natural 5s completion", "the
  marker file ... is genuinely absent"). Reverted; `diff` against the
  pre-injection copy confirmed byte-identical; suite re-confirmed clean
  (34/34) immediately after.
- **Static checks re-run clean, this session:** `scripts/
  validate_spec.py --strict` clean. `scripts/product_truth_check.py
  --fail-on-unwired` — own run, `13/13`; FTF-R016's own `proving_symbol`
  confirmed legitimately `''` (build/implementation tooling, not a
  served product route). `ruff check` on the touched file clean.
- Dependencies FTF-R015/FTF-H002/FTF-0403 confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- **Task contract re-pulled** via `tools/r1_handoff.py packet FTF-R016
  --plan FINE_TUNING_FACTORY_RELEASE_1.md`: both acceptance clauses and
  the full negative_acceptance ("Reject $100/$300 guarantee, missing
  usage counted free, task self-declared accepted, budget reset per
  subagent and external route in private source-code policy") confirmed
  genuinely met — no hardcoded `$100`/`$300` anywhere (grepped directly),
  unknown cost never substituted with `Decimal("0")`, the acceptance flag
  is structurally sourced from a real orchestrator result never a
  self-report, `BudgetNode` walks the live parent chain (now
  independently fault-proven three times: the first reviewer, PR #185,
  and this review's own vector against a different code path),
  `enforce_model_route_policy` denies non-approved routes under
  `private_mode=True`.
- **Note:** a genuine YAML-parsing hazard was found and fixed while
  writing this entry — FTF-R016's own `notes:` field used a plain
  (unquoted) multi-line scalar, which broke `yaml.safe_load` once this
  entry's own added text introduced a `: ` sequence inside a code
  reference; converted to this file's own established `notes: >-`
  block-scalar convention (matching FTF-0202's own precedent), with no
  content lost — verified by re-parsing and diffing the recovered text
  against what was written.
- No blocking defect found. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit
  (`ce1d2f062a830cbb69bfd00353e329585f8efff5`). Ran
  `scripts.task_claim.sync_certified_status()` to write the real status
  into `spec/tasks.json`/`docs/implementation/TRACKER.json` —
  `['FTF-R016']` updated. Certified: 43/300, Product-true: 13/13.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0500 — DatasetSourceConnection schema/API/RBAC
- Criterion ID: FTF-0500
- PR: https://github.com/SJZLab/Sovra/pull/196 (`claude/wizardly-fermat-i9x1tk` → `main`)
- Merge commit: 07b831d44d3c5a5875c5a63b40382c687455d1de
- Implementation commit: ec6a3e7239552024e13f19ac06d18792501a244e (contracts-freeze:
  ce824dd17196680b0441d21288fbc62a3257f225; tracking-doc commit:
  d016f751511073d41f63e1f52ffbd3a8cc214e76)
- Validated-by: session https://claude.ai/code/session_0179fcCZuwbpcEh8NMQVj997
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #197 — this review independently picked up PR #196, the other
  PROVISIONAL item already sitting in `docs/certifications/queue.md`,
  in the same shift)
- Date: 2026-09-22
- **Session-ID check:** PR #196's three commits all carry `Claude-Session:
  .../session_013G1NHePkjpX2VLPJb57on2`; this reviewing session's own ID is
  `session_0179fcCZuwbpcEh8NMQVj997` — no match, independent per Law 4.
- **Merge genuinely confirmed, not self-reported:** `pull_request_read` on
  PR #196 shows `state: closed`, `merged: true`, `merged_by: samerzakaria`
  (the human owner), merge commit `07b831d44d3c5a5875c5a63b40382c687455d1de`,
  which is also PR #197's own stated base — confirms #196 landed on `main`
  before this shift's other item (FTF-H006, PR #197) branched.
- **CI re-confirmed on the real pre-merge head, not a stale ref:**
  `get_check_runs` on PR #196's head `d016f751511073d41f63e1f52ffbd3a8cc214e76`
  (run 35760776024) — 24/24 jobs `success`, including the required `gate`
  job and the new `dataset-source-connection-integration` job this task
  itself added; job timestamps (17:27–17:34Z) fall between PR creation
  (17:27:06Z) and merge (17:35:33Z).
- **Diff read directly** (`git diff 8f33de0..d016f75`, the real range from
  the prior certified tip to this task's own tracking commit): new
  `apps/api/app/datasets/dataset_source_connection_{schema,crud,routes}.py`,
  a real forward Alembic migration (`0010_dataset_source_connections.py`,
  FORCE ROW LEVEL SECURITY + a real `CREATE POLICY tenant_isolation`
  scoped to `app.current_organization_id`, confirmed by direct grep — the
  same discipline every prior migration in this chain applies), the
  phase-05 contract freeze (`packages/contracts/openapi/05-interface.yaml`,
  `packages/contracts/jsonschema/05-domain.schema.json`), and the CI job
  wiring in `.github/workflows/ci.yml`.
- **Own reproduction, this session, fresh git worktree at the real merged
  head (`d016f75`):** `uv sync --all-packages` then
  `uv run pytest tests/tasks/test_ftf_0500.py -q` — 22/22 passed. Note:
  this file is a script-style suite (`main()` aggregates a module-level
  `FAILURES` list via its own `check()` helper and calls `sys.exit(1)` if
  non-empty) that CI itself invokes as `uv run python3
  tests/tasks/test_ftf_0500.py`, not via `pytest` — under plain `pytest`
  every `test_*` function still "passes" even if a `check()` call inside
  it silently records a failure, since `check()` never raises. Re-ran it
  CI's own way (`uv run python3 tests/tasks/test_ftf_0500.py`) both before
  and after the fault injection below to get a trustworthy signal.
- **Own fault injection (Law 1), a vector distinct from the producer's own
  two (tuple-order bug in a different module; OpenBao mount-provisioning
  gap):** disabled the `connection_status != "active"` denial in
  `authorize_purpose_use` (`apps/api/app/datasets/
  dataset_source_connection_crud.py`), i.e. the half of the negative_
  acceptance clause covering "grant revocation propagates to future
  derivative use." Re-ran `uv run python3 tests/tasks/test_ftf_0500.py`
  (CI's own invocation) — exit code 1, "2 failed", naming exactly the two
  broken checks ("once revoked, a PREVIOUSLY granted purpose (training) is
  now denied" and "once revoked, every other declared purpose is denied
  too"). Reverted from a pre-edit backup copy, confirmed `diff` empty
  (byte-identical), re-ran — "all checks passed", exit 0. This also
  surfaced a real gap in the suite's own `pytest` compatibility (noted
  above), not a defect in the shipped feature itself.
- **`product_truth_check.py --fail-on-unwired`** — own run on the real
  merged tip, `14/14`; `proving_symbol: build_dataset_source_connection_router`
  confirmed a real, non-test live caller wired into `apps/api/app/main.py`
  (grepped directly), not inflation — `13/13` before this task, `14/14`
  after, matching the task's own claimed delta.
- **Task contract re-pulled** via `tools/r1_handoff.py packet FTF-0500
  --plan FINE_TUNING_FACTORY_RELEASE_1.md`: both acceptance clauses
  ("Freeze the minimum shared phase interface and domain schemas at the
  declared owner paths... Create purpose-scoped source grants and
  authorization before import; link every dataset to approved source")
  and the full negative_acceptance ("Source allowed only evaluation cannot
  be trained; grant revocation propagates to future derivative use")
  confirmed genuinely met by the diff and this session's own fault
  injection above — not merely "tests pass". `contract_owner: FTF-0500`
  confirmed real: `packages/contracts/openapi/05-interface.yaml` and
  `packages/contracts/jsonschema/05-domain.schema.json` did not exist
  before this task (both are new files in the diff).
- **RLS confirmed real, not merely claimed:** `apps/api/alembic/versions/
  0010_dataset_source_connections.py` — direct grep confirms `ALTER TABLE
  dataset_source_connections ENABLE ROW LEVEL SECURITY`, `... FORCE ROW
  LEVEL SECURITY`, and a real `CREATE POLICY tenant_isolation` scoped to
  `current_setting('app.current_organization_id', true)`.
- **Full regression, this session:** `ruff check` on the touched
  `apps/api/app/datasets/`, `tests/tasks/test_ftf_0500.py`,
  `tests/integration/test_ftf_0500_dataset_source_connection.py`, and the
  new migration file — clean.
- Dependencies FTF-0401/FTF-0202/FTF-H003 confirmed genuinely `CERTIFIED`
  in `docs/spec-matrix.yaml` before relying on them.
- **Scope disclosed, not hidden, independently re-confirmed:** this task's
  own `files:` scope (per the packet) is the phase-05 interface/domain
  freeze plus the `DatasetSourceConnection` resource itself; it does not
  build the later, broader `DataUseGrant` entity (Section 67.4) or the
  Dataset/DatasetRecord import pipeline (Section 8) — no such entity
  exists anywhere in this repository yet, confirmed by direct search, so
  nothing is silently narrowed relative to what actually exists to check
  against.
- No blocking defect found. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit
  (`07b831d44d3c5a5875c5a63b40382c687455d1de`). Ran
  `scripts.task_claim.sync_certified_status()` to write the real status
  into `spec/tasks.json`/`docs/implementation/TRACKER.json` —
  `['FTF-0500']` updated. Certified: 44/300, Product-true: 14/14.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-H006 — Shared outbound URL and credential guard
- Criterion ID: FTF-H006
- PR: https://github.com/SJZLab/Sovra/pull/197 (`claude/wizardly-fermat-83mm0h` → `main`)
- Merge commit: 99e3d3d0be60d7f5491e99965809a29c62efb4ce
- Implementation commit: ae2dcb9d25983bd471d1e69bc57149d991aa56c7
  (task-packet commit: db727c2f2585f29302057018f231539258d7f569;
  tracking-doc commit: 0e1054c919d1d123b14745de424e5d217ba4f106)
- Validated-by: session https://claude.ai/code/session_0179fcCZuwbpcEh8NMQVj997
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #197 itself)
- Date: 2026-09-22
- **Session-ID check:** PR #197's three original commits all carry
  `Claude-Session: .../session_019ApmY6ESBah3nPdBDYq1XL`; this reviewing
  session's own ID is `session_0179fcCZuwbpcEh8NMQVj997` — no match,
  independent per Law 4.
- **First review pass, CI not yet finished:** at the triggering webhook,
  `get_check_runs` on PR #197's original head
  (`0e1054c919d1d123b14745de424e5d217ba4f106`, run 35771884617) showed 18
  of 24 jobs `success` and 6 still `in_progress` (including `gate`,
  which itself later completed `success` while two integration jobs had
  not). Per the CERTIFY procedure, a partial/in-progress CI signal is not
  a genuine pass — deferred rather than certifying from an incomplete
  run. (This session used the wait to independently certify FTF-0500, PR
  #196, the other PROVISIONAL item sitting in the queue at the time —
  see the entry above.)
- **CI re-checked, genuinely completed:** once PR #196/FTF-0500's own
  certification PR (#198) merged to `main`, PR #197's `mergeable_state`
  went `dirty` (a real conflict in `QUEUE.md`/`docs/certifications/
  queue.md`, both touched by both shifts' tracking-doc commits) — a
  `merge_pull_request` attempt failed with `405 ... merge conflicts`,
  confirmed directly rather than assumed. Resolved for real: checked out
  PR #197's head, merged `origin/main` in, resolved both conflicts by
  keeping both sides' content (the producer's own FTF-H006 PROVISIONAL
  submission narrative, unmodified, followed by this session's own
  FTF-0500 certification entry), re-ran `scripts/status.py`/
  `product_truth_check.py --fail-on-unwired`/`validate_spec.py --strict`/
  the `CLAUDE.md.sha256` check on the merged result (all clean), pushed
  the merge commit (`ea1862687e026d9c7855b3489487b9b0cdce24fc`) to PR
  #197's own branch (not a rewrite — a real merge commit, matching
  `BRANCH_PROTECTION`'s "never rewrite another session's branch history"
  constraint). `get_check_runs` on this real new head (run 35773705816)
  — 24/24 jobs `success`, including `gate` and the
  `dataset-source-connection-integration`/`secret-manager-integration`
  jobs that were still running at first check. Merged PR #197
  (`99e3d3d0be60d7f5491e99965809a29c62efb4ce`).
- **Diff read directly** (`git diff 07b831d..0e1054c`, the real range
  from the prior tip to this task's own tracking commit): new
  `packages/security/url_policy.py` (pure policy — scheme/userinfo/port
  shape, resolved-address validation, an explicit private-destination
  allowlist, unconditional cloud-metadata denial checked *before* the
  general private/link-local rule, and alternate-IP-literal-encoding
  normalization covering bare-integer/octal/hex-dotted/inet_aton-legacy
  forms) and `packages/security/outbound_guard.py` (`guarded_fetch` --
  resolves via real `socket.getaddrinfo`, validates every returned
  address, connects pinned to the exact validated address via
  `_PinnedHTTPConnection`/`_PinnedHTTPSConnection` custom `http.client`
  subclasses that override `connect()`, TLS still validated against the
  real hostname via `server_hostname`; a 3xx response is returned as-is
  unless `policy.allow_redirects`, in which case the target is fully
  revalidated and redialed from scratch, bounded by `max_redirects`). No
  new runtime dependency -- stdlib `socket`/`ssl`/`http.client`/
  `ipaddress` only.
- **Own reproduction, this session, fresh git worktree at the real
  merged head:** `uv run python3 tests/tasks/test_ftf_h006.py` -- "all
  checks passed" (this suite is script-style like FTF-0500's own, so run
  the same way CI invokes it).
- **Own fault injection (Law 1), a vector distinct from the producer's
  own (a tuple-order bug in `resolve_and_validate`'s return value; a DNS-
  rebinding-proof test mock leaking onto an unrelated internal call):**
  disabled the cloud-metadata identity check in `validate_resolved_address`
  (`packages/security/url_policy.py`, `if is_cloud_metadata_address(ip)
  or ...` -> `if False`). The suite's own embedded fault-injection self-
  test (`test_fault_injection_removing_the_metadata_check_is_caught`)
  failed for real via a source-text assertion (exit 1). To confirm this
  was a genuine, exploitable defect and not just a text-match artifact,
  independently exercised the runtime behavior directly: with the check
  disabled, `validate_resolved_address("169.254.169.254",
  ipaddress.ip_address("169.254.169.254"), ConnectionPolicy(name="test",
  explicit_private_allowlist=(ipaddress.ip_network("169.254.0.0/16"),)))`
  -- the exact scenario this task's own negative_acceptance clause names
  ("a blanket private-address rejection cannot break intended internal
  LiteLLM/storage while metadata remains denied") -- genuinely returned
  without raising (cloud-metadata address allowed once an operator
  allowlists the containing link-local range, a real SSRF-to-cloud-
  credentials exposure). Reverted from a pre-edit backup, `diff`
  confirmed byte-identical, re-ran the same call -- correctly denied
  ("... is a cloud-metadata destination -- never permitted, regardless
  of any explicit allowlist entry"); full suite re-confirmed "all checks
  passed".
- **DNS-rebinding proof read directly**
  (`test_dns_resolved_once_and_connection_pinned_to_that_address`): a
  real local `ThreadingHTTPServer`, a mocked `socket.getaddrinfo` that
  answers a safe address on the first call and a disallowed one on any
  second call for the hostname under test (passing every other host,
  including the real internal literal-IP lookup `create_connection`
  itself performs, straight through to the real resolver), and a spying
  `socket.create_connection` -- confirms `getaddrinfo` is called exactly
  once and the real socket connects only to the first, already-validated
  address, never a hypothetical hostile second answer.
- **`product_truth_check.py --fail-on-unwired`** -- own run on the real
  merged tip, `14/14`, unchanged; `proving_symbol: ''` confirmed
  legitimate (this task builds the shared guard library itself, not a
  live route -- `apps/api/app/litellm/connection_schema.py`'s own
  `validate_base_url` docstring states "this module never dials the URL
  -- that is FTF-0203's job", and no live FastAPI route calls
  `guarded_fetch` yet, confirmed by direct grep), the same disclosed "no
  live caller yet" class already accepted for FTF-0202/FTF-R016 and
  others before their own first live caller existed.
- **Task contract re-pulled** via `tools/r1_handoff.py packet FTF-H006
  --plan FINE_TUNING_FACTORY_RELEASE_1.md`: the acceptance clause
  ("Exercise real controlled HTTP endpoints and DNS/redirect fixtures
  covering loopback, private allowlist, link-local metadata, IPv6,
  alternate encodings and rebinding") and the full negative_acceptance
  ("Private destinations can be explicitly authorized; a blanket
  private-address rejection cannot break intended internal LiteLLM/
  storage while metadata remains denied") confirmed genuinely met by the
  diff and this session's own fault injection above, not merely "tests
  pass". `files:` scope (`packages/security/outbound_guard.py`,
  `packages/security/url_policy.py`) matches the diff exactly.
- IPv6 disclosed, not hidden, independently re-confirmed: this sandbox
  has no real `AF_INET6` bind to `::1` (confirmed directly, not merely
  `socket.has_ipv6`) -- the live `::1` `guarded_fetch` sub-check is
  skipped in this one sandbox condition and says so at test-module load
  time; the pure IPv6 policy checks (no socket required) always run.
- Dependencies FTF-0005/FTF-0201/FTF-0202/FTF-H003 confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- `ruff check packages/security/ tests/tasks/test_ftf_h006.py` clean.
  `scripts/validate_spec.py --strict` clean after `git fetch --unshallow
  origin` (this sandbox's own pre-existing shallow-clone artifact).
- No blocking defect found. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit
  (`99e3d3d0be60d7f5491e99965809a29c62efb4ce`). Ran
  `scripts.task_claim.sync_certified_status()` to write the real status
  into `spec/tasks.json`/`docs/implementation/TRACKER.json` --
  `['FTF-H006']` updated. Certified: 45/300, Product-true: 14/14
  (unchanged -- this task adds no live caller by design).

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0203 — Connection validation
- Criterion ID: FTF-0203
- PR: https://github.com/SJZLab/Sovra/pull/200 (`claude/wizardly-fermat-0knkdx` → `main`)
- Merge commit: c6e51a2fb267a6ef26791eb9fccc79cbfd0fc4b4
- Implementation commit: 712ebbf9467e5c8154d24391e1c9b0abad52fd59
  (task-packet commit: c3cac3a7e986edf2913628f3f50dc7c00ba4bfe6;
  tracking-doc commit: ba5f27ae975ee217cc2f491e324b4fb1017576ff;
  CI-fix commit: 9f94a3cac74e354d51dd9986ec402f4350522152)
- Validated-by: session https://claude.ai/code/session_013qTQ2DdZRMRjiGC8DDeVnS
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #200 itself)
- Date: 2026-09-22
- **Session-ID check:** all four commits on PR #200 carry `Claude-Session:
  .../session_01AE9mEUKyK7BVB93dTEohRn`; this reviewing session's own ID
  is `session_013qTQ2DdZRMRjiGC8DDeVnS` — no match, independent per Law 4.
- **CI re-checked, genuinely completed, not taken on faith:** PR #200's
  original head (`ba5f27a`, run 35785636597) showed a real, reproducible
  failure — `connection-validation-integration` failed for real with
  `PermissionError: [Errno 13] Permission denied:
  '/usr/local/share/ca-certificates/ftf-0203-integration-test-ca.crt'`
  (confirmed by reading the actual job log, not a summary) — the test
  suite wrote the ephemeral test CA directly to the system trust store
  without `sudo`, which works on a root-run sandbox but not on GitHub
  Actions' `ubuntu-latest` runner (confirmed independently: this
  reviewing session is itself root in its own sandbox, `touch
  /usr/local/share/ca-certificates/...` succeeded trivially there,
  reproducing exactly why the bug was masked pre-push). The producer's
  own session diagnosed and fixed this identically while this review was
  in progress (commit `9f94a3c`, switching to `sudo cp`/`sudo
  update-ca-certificates`) — this review independently confirmed the
  fix by watching the real re-run to completion: `get_check_runs` on the
  real final head (`9f94a3c`, run 35785873270) polled to **25/25 real
  jobs `success`**, including `connection-validation-integration` (40s)
  and `gate` (2m03s) — not a stale or self-reported claim.
- **Diff read directly:** `packages/litellm_client/connection_validation.py`
  (`validate_connection` — resolves `secret_ref` via FTF-0202's
  `resolve_secret_ref`, dials the gateway's `GET /v1/models` via
  FTF-H006's `guarded_fetch`, reports `InferenceCheckState`/
  `ManagementCheckState` as two fully separate dimensions, never raises
  for an ordinary connectivity/auth/secret failure) and
  `apps/api/app/litellm/connection_routes.py` (the new `POST
  .../connections/litellm/{connection_id}/validate` route —
  `organization_admin`-only, reads `base_url`/`secret_ref` inside the
  same tenant-scoped session as every other route before releasing it,
  denies a foreign-tenant connection ID identically to missing (404),
  refuses a revoked connection (409)). Diff scope matches the task
  packet's own `files` list exactly (`packages/litellm_client/
  connection_validation.py`, `tests/tasks/test_ftf_0203.py`, plus the
  route/CI wiring this task's own acceptance clause requires) — no
  scope creep.
- **Own reproduction, a separate git worktree at the real merged head
  (`9f94a3c`):** `uv sync --all-packages`; `uv run python3
  tests/tasks/test_ftf_0203.py` — "all checks passed" (48/48). A real
  local PostgreSQL 16 role/db (`sovra_migrate`/`sovra_ftf0203`) created
  fresh in this sandbox; `uv run python3
  tests/integration/test_ftf_0203_connection_validation.py` — "all
  checks passed" (16/16). `tests/integration/
  test_ftf_0201_litellm_connection_crud.py` and `tests/tasks/
  test_ftf_0201.py` re-run clean against a fresh database — no
  regression to the shared `connection_routes.py` file.
- **Own fault injection, a vector distinct from the producer's own two**
  (which targeted `SecretAccessDeniedError` fabrication and DNS/timeout/
  refused-connection classification): disabled the `response.status in
  (401, 403)` invalid-token check in `validate_connection`
  (`if False and response.status in (401, 403):`) and re-ran the no-DB
  suite — exactly 1 check failed for real: "a real gateway 401 on a
  resolved credential reports invalid_token, not connected" — this is
  precisely the negative_acceptance risk this task exists to prevent (a
  rejected credential silently reported as a connected badge). Reverted
  from a pre-edit backup; `diff` confirmed byte-identical; the suite
  re-ran clean immediately after.
- `ruff check .` / `ruff format --check .` clean (180 files); `mypy .`
  clean (180 files, "Success: no issues found"). `scripts/validate_spec.py
  --strict` clean after `git fetch --unshallow origin` (this worktree's
  own pre-existing shallow-clone artifact, not caused by this task).
  `CLAUDE.md.sha256` verified against `sha256sum CLAUDE.md` on the real
  merged head.
- `scripts/product_truth_check.py --fail-on-unwired` — own run, `14/14`
  before this certification; `proving_symbol: validate_connection`
  confirmed a real, non-test, live-caller match in `apps/api/app/litellm/
  connection_routes.py` (grepped directly, not assumed) — this
  repository's second live-wired product-runtime path after FTF-0201's
  own CRUD routes to actually call this symbol.
- `python3 tools/r1_handoff.py packet FTF-0203 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — both acceptance clauses ("Validate
  real gateway TLS/auth/reachability and safe management/inference
  capabilities separately"; "Independent review, exact merged commit,
  real relevant integration and negative-test evidence recorded") and
  the full negative_acceptance clause ("Timeout, certificate error,
  invalid token and management-403 produce distinct sanitized states,
  not connected badge") confirmed genuinely met by the diff and this
  session's own fault injection above, not merely "tests pass";
  `contract_owner: FTF-0201` confirmed real.
- Dependencies FTF-0201/FTF-0202/FTF-H006 confirmed genuinely `CERTIFIED`
  in `docs/spec-matrix.yaml` before relying on them.
- No blocking defect found. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `proving_symbol: validate_connection`, `evidence_hash` set to the
  merge commit (`c6e51a2fb267a6ef26791eb9fccc79cbfd0fc4b4`). Ran
  `scripts.task_claim.sync_certified_status()` to write the real status
  into `spec/tasks.json`/`docs/implementation/TRACKER.json` --
  `['FTF-0203']` updated. Certified: 46/300, Product-true: 15/15.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0204 — `/v1/models` discovery
- Criterion ID: FTF-0204
- PR: https://github.com/SJZLab/Sovra/pull/203 (`claude/wizardly-fermat-pkklwn` → `main`)
- Merge commit: 9a3dd7e52040cca60ec7ae72b38676d253a32824
- Implementation commit: c1ac87cdeb6fef3b412c248ab2b01472614e6e02
  (tracking-doc commit: 15f96de16d491643a663d36f06be882b8accabe0)
- Validated-by: session https://claude.ai/code/session_014jayxcTTyU7RpJJXSP3B8p
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #203 itself)
- Date: 2026-09-22
- **Session-ID check:** both commits on PR #203 carry `Claude-Session:
  .../session_013yJsjReu7yKdQyMxjAxZgD`; this reviewing session's own ID
  is `session_014jayxcTTyU7RpJJXSP3B8p` — no match, independent per Law 4.
- **CI re-checked, genuinely completed, not taken on faith:** watched the
  real CI run on PR #203's real head (`15f96de`, run 35795565157) to
  completion via the GitHub REST API (`gh api
  repos/SJZLab/Sovra/commits/15f96de.../check-runs`, polled directly, not
  a summary) — **26/26 real jobs `success`**, including
  `model-discovery-integration` and `gate`.
- **Diff read directly:** `packages/litellm_client/v1_models_discovery.py`
  (`discover_models` — resolves `secret_ref` via FTF-0202's already-
  CERTIFIED `resolve_secret_ref`, dials `GET /v1/models` via FTF-H006's
  already-CERTIFIED `guarded_fetch`, enriches via `GET /model/info` on a
  strict left-join basis so a metadata-only name never becomes callable,
  and is stateless/fresh per call so a removed model or revoked
  credential is never retained from a prior call) and
  `apps/api/app/litellm/connection_routes.py` (real `GET
  .../connections/litellm/{connection_id}/models` route,
  `organization_admin`-only, same tenant-scoped session, foreign-tenant
  404 and revoked-connection 409 pattern FTF-0203's own `/validate`
  route already established). Diff scope matches the task packet's own
  `files` list (plus the route/CI wiring the acceptance clause requires)
  — no scope creep.
- **Own reproduction, a separate git worktree at the real merged head
  (`15f96de`):** `uv sync --all-packages`; `uv run python3
  tests/tasks/test_ftf_0204.py` — "all checks passed". A real local
  PostgreSQL 16 role/db (`sovra_migrate`/`sovra_ftf0204`) created fresh
  in this sandbox; `uv run python3
  tests/integration/test_ftf_0204_v1_models_discovery.py` — "all checks
  passed". `tests/integration/test_ftf_0201_litellm_connection_crud.py`
  and `tests/integration/test_ftf_0203_connection_validation.py` re-run
  clean against the same fresh database — no regression to the shared
  `connection_routes.py` file.
- **Own fault injection, a vector distinct from the producer's own**
  (which targeted reintroducing the Section 3.2 authorization-union
  bug): disabled the `response.status in (401, 403)` invalid-token check
  in `discover_models` (`if False and response.status in (401, 403):`)
  and re-ran the no-DB suite — exactly 1 check failed for real: "a real
  gateway 401 on a resolved credential reports invalid_token, not
  discovered" — precisely the negative_acceptance risk this task exists
  to prevent (a revoked credential silently retaining a stale model
  list). Reverted from a pre-edit backup; `diff` confirmed
  byte-identical; the suite re-ran clean immediately after.
- `ruff check .` / `ruff format --check .` clean (183 files); `mypy .`
  clean (183 files, "Success: no issues found"). `CLAUDE.md.sha256`
  verified against `sha256sum CLAUDE.md` on the real merged head.
- `scripts/product_truth_check.py --fail-on-unwired` — own run, `15/15`
  before this certification; `proving_symbol: discover_models` confirmed
  a real, non-test, live-caller match in
  `apps/api/app/litellm/connection_routes.py` (grepped directly, not
  assumed).
- `python3 tools/r1_handoff.py packet FTF-0204 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — both acceptance clauses ("List
  current credential-visible model IDs through tested gateway route and
  record timestamp/count/hash"; "Independent review, exact merged
  commit, real relevant integration and negative-test evidence
  recorded") and the negative_acceptance clause ("Metadata-only or stale
  deleted model is not callable; auth errors fail closed instead of
  retaining permission") confirmed genuinely met by the diff and this
  session's own fault injection above, not merely "tests pass";
  `contract_owner: FTF-0201` confirmed real.
- Dependencies FTF-0201/FTF-0202/FTF-0203/FTF-H006 confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- No blocking defect found. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `proving_symbol: discover_models`, `evidence_hash` set to the merge
  commit (`9a3dd7e52040cca60ec7ae72b38676d253a32824`). Ran
  `scripts.task_claim.sync_certified_status()` to write the real status
  into `spec/tasks.json`/`docs/implementation/TRACKER.json` --
  `['FTF-0204']` updated. Certified: 47/300, Product-true: 16/16.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0205 — `/v1/model/info` enrichment
- Criterion ID: FTF-0205
- PR: https://github.com/SJZLab/Sovra/pull/205 (`claude/wizardly-fermat-h60rso` → `main`)
- Merge commit: b421fb366870167dcb7ecaab7a658adf1c2c6510
- Implementation commit: 6545edb90975b768797e4a9e3d760c3185bfeb21
  (tracking-doc commit: d2c8e373b68ae1bddf431341bcb73efe52d6b9c2)
- Validated-by: session https://claude.ai/code/session_01MUWNviq4gzsqxZ5NB7Yigz
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #205 itself)
- Date: 2026-09-23
- **Session-ID check:** both commits on PR #205 carry `Claude-Session:
  .../session_01RmL7kyQS87ukMEHHgUQ7fN`; this reviewing session's own ID
  is `session_01MUWNviq4gzsqxZ5NB7Yigz` — no match, independent per Law 4.
- **CI re-checked, genuinely completed, not taken on faith:** the
  `pull_request.opened` webhook fired while CI was still `in_progress`.
  Polled `repos/SJZLab/Sovra/commits/d2c8e373.../check-runs` directly via
  the GitHub REST API until every job reported `completed` — **26/26 real
  jobs `success`**, including `gate`, `secret-manager-integration` and
  `dataset-source-connection-integration`. The latter two were still
  building a real from-source OpenBao binary when first observed;
  confirmed genuinely progressing (not the previously documented
  runner-provisioning outage) via `actions/runs/.../jobs` showing real
  non-zero `runner_id`s and real per-step completion timestamps advancing
  between polls, on the real PR head (not a stale or self-reported
  claim).
- **Diff read directly:** `packages/litellm_client/
  v1_model_info_enrichment.py` (`enrich_visible_models` — takes the
  caller's own already-authorized `visible_model_ids`, attaches
  allowlisted `GET /model/info` fields on a strict left-join basis
  (Section 3.2's "no authorization union" applied to enrichment: an id
  absent from `visible_model_ids` is never iterated into the result);
  `_ALLOWLISTED_MODEL_INFO_FIELDS` is an allow list, not a denylist — the
  real gateway response's own `litellm_params` sibling, which carries
  `api_key`/`api_base`, is never read by `_parse_model_info` at all, and
  only named fields with plain JSON scalar values ever pass
  `_allowlisted_fields`'s filter; a denied/failed/malformed
  `/model/info` call never drops or replaces an already-visible id —
  every entry in `visible_model_ids` still gets a real result,
  downgraded to `metadata_status=limited` with empty fields). Diff scope
  matches the task packet's own `files` list (module + test only,
  disclosed scope decision matching the FTF-0202/FTF-0203
  producer/later-wiring precedent) — no scope creep, no route wired by
  this task.
- **Own reproduction, a separate git worktree at the real merged head
  (`d2c8e37`):** `uv sync --all-packages`; `uv run python3
  tests/tasks/test_ftf_0205.py` — "all checks passed".
- **Own fault injection, a vector distinct from the producer's own two**
  (which targeted widening the allowlist and reintroducing an
  authorization union): made a denied/invalid-token `/model/info`
  response (`response.status in (401, 403)`) return an empty
  `entries` tuple outright
  (`ModelInfoEnrichmentResult(EnrichmentState.INVALID_TOKEN, tuple(), ...,
  checked_at)`) instead of downgrading every visible id to `limited` —
  re-ran the no-DB suite: exactly 1 check failed for real, "every visible
  id is still present, downgraded to limited, never dropped" — precisely
  the negative_acceptance risk this task exists to prevent (a denied
  enrichment call silently losing an already-authorized model from the
  caller's result). Reverted from a pre-edit backup; `diff` confirmed
  byte-identical; the suite re-ran clean immediately after.
- `ruff check .` / `ruff format --check .` clean (repo-wide, in this
  reproduction — 0 findings, including no `UP042` on `EnrichmentState`;
  stricter than, not contradicting, the producer's own disclosed
  pre-existing-convention note). `mypy .` clean (185 source files,
  "Success: no issues found"). `CLAUDE.md.sha256` verified against
  `sha256sum CLAUDE.md` on the real merged head.
- `scripts/product_truth_check.py --fail-on-unwired` — own run, `16/16`;
  `proving_symbol` is genuinely blank for this criterion (this task
  discloses no live caller yet — `FTF-0207` is the later, separately
  dependency-gated task that wires it in) — 0/0 for this criterion is a
  legitimate pass, not a gap, matching the same disclosed-scope class
  `FTF-0202` established before `FTF-0203` wired it.
- Regression: `tests/tasks/test_ftf_0201.py` /
  `test_ftf_0202.py` / `test_ftf_0203.py` / `test_ftf_0204.py` all
  re-run clean afterward — no regression to any shared module.
- `python3 tools/r1_handoff.py packet FTF-0205 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — both acceptance clauses ("Enrich
  only visible aliases with allowlisted fields, retaining limited
  metadata when denied/absent"; "Independent review, exact merged
  commit, real relevant integration and negative-test evidence
  recorded") and the negative_acceptance clause ("Raw credentials,
  admin-only provider routes and metadata-only aliases are neither
  stored nor exposed") confirmed genuinely met by the diff and this
  session's own fault injection above, not merely "tests pass";
  `contract_owner: FTF-0201` confirmed real.
- Dependencies FTF-0201/FTF-0203/FTF-0204 confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- No blocking defect found. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit
  (`b421fb366870167dcb7ecaab7a658adf1c2c6510`); `proving_symbol` stays
  blank (no live caller yet, disclosed above). Ran
  `scripts.task_claim.sync_certified_status()` to write the real status
  into `spec/tasks.json`/`docs/implementation/TRACKER.json` --
  `['FTF-0205']` updated. Certified: 48/300, Product-true: 16/16
  (unchanged).

**Conclusion:** Certified L2-CERTIFIED.

## FTF-04A01 — TargetApplicationConnection schema/API/RBAC
- Criterion ID: FTF-04A01
- PR: https://github.com/SJZLab/Sovra/pull/209 (`claude/wizardly-fermat-iux3ti` → `main`)
- Merge commit: 46bcc0ad30e8090d2a921f1c287496bf916c38c5
- Implementation commit: e8d9c64c5bd1347a997baaa0890401e600a6a8f2
  (tracking-doc commit: 2ae27f7a4741e11e9f2c6125a3e0e513554bc3a3)
- Validated-by: session https://claude.ai/code/session_01EXCvSU1tz7PcbkneMnW558
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #209 itself)
- Date: 2026-09-23
- **Session-ID check:** both commits on PR #209 carry `Claude-Session:
  .../session_01EXsCz9KARjFVNap1uvhxVb`; this reviewing session's own ID
  is `session_01EXCvSU1tz7PcbkneMnW558` — no match, independent per Law 4.
- **Diff read directly:** exactly the task packet's own files
  (`target_application_connection_schema.py`/`_crud.py`/`_routes.py`, the
  `0011_target_app_connections` migration, `packages/contracts/openapi/
  04A-interface.yaml`/`packages/contracts/jsonschema/04A-domain.schema.json`,
  both test files) plus expected tracking docs (`docs/spec-matrix.yaml`,
  `QUEUE.md`, `PROGRESS.md`, the task packet, `docs/certifications/
  queue.md`) and two new CI steps (`gate`'s own FTF-04A01 step, a new
  `target-application-connection-integration` job). No scope creep.
- **CI re-checked, genuinely completed, not taken on faith:** the
  `pull_request.opened` webhook fired while CI was still `in_progress`.
  Polled PR #209's real head (`2ae27f7a...`) check runs directly until
  every job reported `completed` — **27/27 real jobs `success`**. Went
  one step further than a bare job-count check, learning directly from
  this control plane's own prior finding on FTF-0206/PR#207 (a job can
  pass 26/26 while never actually executing the task's own test): pulled
  `gate`'s own real step list and confirmed its new "FTF-04A01
  negative-test proof (TargetApplicationConnection schema/API/RBAC, no DB
  required)" step is present and `success`, and separately pulled the
  dedicated `target-application-connection-integration` job's own step
  list and confirmed "Prove the real TargetApplicationConnection evidence
  against the live database and live OpenBao" is present and `success` —
  the real Postgres 16 + real OpenBao integration proof genuinely ran on
  this exact commit, not merely claimed by the PR body.
- **Own independent verification, not the producer's local claims:**
  - Ran `tests/tasks/test_ftf_04a01.py` directly on the real PR head —
    all checks pass.
  - Own fault injection, distinct execution from the producer's own two
    disclosed ones: edited `validate_connector_type` in the shipped
    `target_application_connection_schema.py` to skip its closed-set
    check entirely. Re-ran the no-DB suite — **4 checks failed for
    real**, including this task's own "unsupported connector... fail
    server-side" negative_acceptance check and the Section 7A.1 "no mock
    connector" check. Reverted from a pre-edit backup; `diff` confirmed
    byte-identical; the suite re-ran clean immediately after.
  - Ran the full Alembic migration chain (`0001` through
    `0011_target_app_connections`) against a real, locally-installed
    PostgreSQL 16 cluster (this sandbox's own real sudo/service access,
    not a mock). Directly inspected the live database afterward
    (`\d target_application_connections`,
    `information_schema.role_table_grants`): the table shape, the
    composite foreign key `(organization_id, project_id) ->
    projects(organization_id, id)`, all three check constraints, and the
    real `FORCE ROW LEVEL SECURITY` `tenant_isolation` policy all match
    the PR's claims exactly; `sovra_app` genuinely holds only
    `SELECT`/`INSERT`/`UPDATE` on the new table, never `DELETE` —
    confirming the claimed "revoke is a soft transition, not row
    deletion" discipline against a real database, not merely the
    migration's own source text.
  - `scripts/product_truth_check.py --fail-on-unwired` — own run,
    **17/17** (up from 16/16) — `build_target_application_connection_router`
    (this task's own `proving_symbol`) is a real, genuine live caller from
    `apps/api/app/main.py`.
  - `uv run ruff check .` / `ruff format --check .` / `mypy
    apps/api/app/target_connectors` clean on every file this task
    touched.
- `python3 tools/r1_handoff.py packet FTF-04A01 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1 ("Create
  tenant-scoped read-only target connection and validate credentials
  without fetching unapproved data") confirmed met by
  `_validate_connector_shape`'s real structural check plus the real
  OpenBao `store_secret_ref` write; all three negative_acceptance clauses
  ("Cross-project target IDs, unsupported connector and excessive
  permission fail server-side") confirmed genuinely met — "unsupported
  connector" by this review's own fault injection above, "cross-project
  target IDs" and "excessive permission" via the real live-database
  integration test confirmed passing in the real CI job above.
- Dependencies FTF-0401/FTF-0103/FTF-0202/FTF-H006 confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- No blocking defect found. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit
  (`46bcc0ad30e8090d2a921f1c287496bf916c38c5`); `proving_symbol` stays
  `build_target_application_connection_router`. Ran
  `scripts.task_claim.sync_certified_status()` to write the real status
  into `spec/tasks.json`/`docs/implementation/TRACKER.json` --
  `['FTF-04A01']` updated. Certified: 48/300 → 49/300, Product-true:
  16 → 17.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0207 — Alias reconciliation
- Criterion ID: FTF-0207
- PR: https://github.com/SJZLab/Sovra/pull/212 (`claude/wizardly-fermat-7rbq4h`
  → `main`) — implementation and PROVISIONAL submission. Landed on `main`
  directly as merge commit `ab6473fc3854f71d782a8bb1671a95c33f413ba5`
  (PR #212 itself shows `merged: false` — see irregularity below).
- Merge commit (evidence): ab6473fc3854f71d782a8bb1671a95c33f413ba5
- Implementation commit: 194829be0acf8f95276aee74a1dc6430484f7242
  (tracking-doc commit: 1e19e3d06b126a265a9630e449fb606b730df669)
- Validated-by: session https://claude.ai/code/session_01A9BrV7gok9pZTKotAqZEuy
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #214 — found this item already PROVISIONAL and pending while
  checking `docs/certifications/queue.md` per this shift's own mandate)
- Date: 2026-09-23
- **Session-ID check:** both commits on PR #212 carry `Claude-Session:
  .../session_01AkayPLhxBFfVkDgfhY2Mnk`. A separate, already-opened PR
  #213 (doc-only, unmerged) carries an equivalent independent review from
  `session_01AsrpTcXDVyvY6AAYKGs45W`. This reviewing session's own ID is
  `session_01A9BrV7gok9pZTKotAqZEuy` — no match against either, independent
  per Law 4. (PR #213 is closed as superseded by this entry, not merged —
  see irregularity below.)
- **Diff read directly:** exactly the task packet's own files
  (`packages/litellm_client/alias_reconciliation.py`,
  `tests/tasks/test_ftf_0207.py`) plus expected tracking docs
  (`docs/spec-matrix.yaml`, `QUEUE.md`, `PROGRESS.md`,
  `docs/implementation/task-packets/FTF-0207.md`,
  `docs/certifications/queue.md`) and one new CI step (`gate`'s own
  FTF-0207 step). No scope creep; no route file, matching the disclosed
  "no live caller yet" precedent FTF-0202/FTF-0205 already established.
- **CI re-checked directly on the real PR #212 head (`1e19e3d`), not taken
  on faith:** 27/27 jobs `success`, `mergeable_state: clean`. Pulled the
  `gate` job's own step list and confirmed the "FTF-0207 negative-test
  proof (Alias reconciliation, no DB required)" step is genuinely present
  and `success` on this exact commit — not inferred from the aggregate
  green count (the same class of gap this control plane previously caught
  on FTF-0206/PR#207).
- **Own independent verification, not the producer's or PR #213's local
  claims:**
  - Ran `tests/tasks/test_ftf_0207.py` directly in a fresh `git worktree`
    at `ab6473f` (the real head of `main`) — all checks pass.
  - Own fault injection, a fourth distinct vector from the producer's own
    two (union instead of left-join; case-folded join key) and from PR
    #213's one (removed the duplicate-id "first wins" dedup guard):
    changed the "no matching enrichment entry" branch from falling back to
    `metadata_status=LIMITED` to `continue` (silently dropping the
    alias). Re-ran the suite — failed for real (`IndexError`, the first
    assertion expecting the alias present crashed), proving "a discovered
    alias missing from enrichment falls back to limited without being
    dropped" is load-bearing, not decorative. Reverted from a pre-edit
    backup; `diff` confirmed byte-identical; suite re-ran clean
    immediately after.
  - `uv run ruff check .` / `ruff format --check .` / `mypy .` — clean,
    whole repo.
  - `scripts/product_truth_check.py --fail-on-unwired` — own run, **17/17**,
    legitimately unchanged (blank `proving_symbol` is the correct, disclosed
    state — no live route wired to this module yet, same class already
    accepted for FTF-0202 before FTF-0203 wired it and for FTF-0205 before
    this task).
- `python3 tools/r1_handoff.py packet FTF-0207 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1 ("Left-join
  case-sensitive alias identities to metadata without replacing
  user-facing ID") confirmed met by the shipped module's exact-case
  dict-keyed join and its unconditional `id=discovered.id`;
  negative_acceptance ("Colliding aliases/upstream names never merge
  credentials or grant a model absent from current accessible set")
  confirmed met both structurally (neither input/output dataclass carries
  a credential field) and behaviorally (this review's own fault injection
  above plus the producer's own union-fault demonstration).
- Dependencies FTF-0201/FTF-0204/FTF-0205 confirmed genuinely `CERTIFIED`
  in `docs/spec-matrix.yaml` before relying on them.
- **Irregularity found and disclosed, not silently worked around:** the
  commit that actually landed this task's code onto `main`
  (`ab6473fc3854f71d782a8bb1671a95c33f413ba5`) was pushed directly by the
  `samerzakaria` account rather than merged through PR #212 (which itself
  shows `merged: false` on GitHub) — a `BRANCH_PROTECTION` violation this
  session did not commit. Its commit message ("Control plane: certify
  FTF-0207 ... Independently re-reviewed PR #212 ... all confirmed
  sound") reads as a completed L2 certification, but its actual diff
  (verified directly against its first parent) only ever set
  `certification_state: PROVISIONAL` / `proof_state: L1-PROVEN` — it
  never touched CERTIFIED/L2-CERTIFIED. The tracker itself was therefore
  never inflated (Law 2 held); only the commit message was misleading.
  This is the same class of incident as `docs/certifications/
  watchdog-findings.md`'s 2026-09-18 "Direct-push-to-main recurrence"
  entry, now recurring a second time — logged here for visibility; a
  formal watchdog finding entry is out of scope for this certifier shift
  (Watchdog's own role, not Control plane's).
- No blocking defect found. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to `ab6473fc3854f71d782a8bb1671a95c33f413ba5` (the
  commit that actually merged the implementation onto `main`).
  `proving_symbol` stays blank (no live caller yet, disclosed). Ran
  `scripts.task_claim.sync_certified_status()` — `['FTF-0207']` updated in
  `spec/tasks.json`/`docs/implementation/TRACKER.json`. Certified: 49/300
  → 50/300. Product-true: unchanged at 17/17.
## FTF-0506 — Canonical conversation normalizer
- Criterion ID: FTF-0506
- PR: https://github.com/SJZLab/Sovra/pull/214 (`claude/wizardly-fermat-fqjkvu`
  → `main`)
- Merge commit: 7ffaebcb9879acf9db1adee2f70e6f6e539dbf3d
- Implementation commit: 025b936d7011f30ca607f2038f1c61f4766c4640
  (tracking-doc commit: 5d4430044ae11ebac833339739507bc6ff9aa7ff)
- Validated-by: session https://claude.ai/code/session_01A9BrV7gok9pZTKotAqZEuy
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #214 itself)
- Date: 2026-09-23
- **Session-ID check:** both commits on PR #214 carry `Claude-Session:
  .../session_01WnuyMUVM6PHL3KX9pnGbAF`; this reviewing session's own ID
  is `session_01A9BrV7gok9pZTKotAqZEuy` — no match, independent per Law 4.
- **Diff read directly:** exactly the task packet's own files
  (`workers/data/canonical_conversation_normalizer.py`,
  `tests/tasks/test_ftf_0506.py`, new `workers/__init__.py`/
  `workers/data/__init__.py`) plus expected tracking docs
  (`docs/spec-matrix.yaml`, `QUEUE.md`, `PROGRESS.md`,
  `docs/implementation/task-packets/FTF-0506.md`,
  `docs/certifications/queue.md`) and one new CI step (`gate`'s own
  FTF-0506 step). No scope creep; no importer file, matching the
  disclosed "no live caller yet" scope FTF-0202/FTF-0205/FTF-0207 already
  established.
- **CI re-checked directly on the real PR #214 head (`5d44300`), not
  taken on faith:** 27/27 jobs `success`. Pulled the `gate` job's own
  real step list directly — step 68, "FTF-0506 negative-test proof
  (Canonical conversation normalizer, no DB required)", confirmed
  present and `success` on this exact commit, not inferred from the
  aggregate green count (the same class of gap this control plane
  previously caught on FTF-0206/PR#207).
- **Own independent verification, not the producer's local claims:**
  - Ran `tests/tasks/test_ftf_0506.py` directly in a fresh `git worktree`
    at PR #214's real head — all checks pass.
  - Own fault injection, a third distinct vector from the producer's own
    two (disabled duplicate tool-call-id guard; disabled
    no-validator-supplied fail-closed guard): changed the "tool result's
    `tool_call_id` references an existing pending tool call" check from
    raising `ConversationNormalizationError` to a silent
    `pending_calls.get(tool_call_id, name)` fallback. Re-ran the suite —
    **failed for real** (`KeyError` deleting an unknown `tool_call_id`
    from `pending_calls`), proving the "every tool result references an
    existing tool call" negative_acceptance-adjacent check is
    load-bearing, not decorative. Reverted from a pre-edit backup;
    `diff` confirmed byte-identical; suite re-ran clean immediately
    after.
  - `uv run ruff check .` / `ruff format --check .` / `mypy .` — clean,
    whole repo.
  - `scripts/product_truth_check.py --fail-on-unwired` — own run,
    **17/17**, legitimately unchanged (blank `proving_symbol` — no
    importer wired to call `normalize_conversation` yet, same disclosed
    "no live caller" precedent already accepted for FTF-0202/FTF-0205/
    FTF-0207).
- `python3 tools/r1_handoff.py packet FTF-0506 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1 ("Normalize
  roles/content/tool calls into canonical schema with lineage/group IDs
  and deterministic hash") confirmed met by the shipped module's real
  `RecordLineage`/`group_id`/`canonical_hash` implementation;
  negative_acceptance ("Missing/unmapped role or coerced information
  loss is rejected, not silently guessed") confirmed met both by the
  producer's own two fault-injection demonstrations and this review's
  own third one above.
- Dependencies FTF-0405/FTF-0500 confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- No blocking defect found. Merged PR #214 (merge commit
  `7ffaebcb9879acf9db1adee2f70e6f6e539dbf3d`). `docs/spec-matrix.yaml` set
  to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to that merge commit. `proving_symbol` stays blank
  (no live caller yet, disclosed). Ran
  `scripts.task_claim.sync_certified_status()` — `['FTF-0506']` updated
  in `spec/tasks.json`/`docs/implementation/TRACKER.json`. This is the
  second of two independent certifications from this same shift (the
  other, FTF-0207, is PR #215) — Certified count moves 49/300 → 50/300 →
  51/300 as both land on `main` in sequence (see QUEUE.md/PROGRESS.md for
  the reconciled final count once both are merged). Product-true:
  unchanged at 17/17.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0206 — `/model_group/info` optional enrichment
- Criterion ID: FTF-0206
- PR: https://github.com/SJZLab/Sovra/pull/207 (`claude/wizardly-fermat-bj3os5`
  → `main`) — implementation, PROVISIONAL submission, and (in a later
  commit by a different session) the CI gate-coverage fix a prior
  Control-plane review had named as this PR's blocker. Merged by this
  review as merge commit `7ee47f8c4fa2f7dbce7d6557a29bdb82b4369a53`.
- Merge commit (evidence): 7ee47f8c4fa2f7dbce7d6557a29bdb82b4369a53
- Implementation commit: 49d29681764d225afede1336c09a8a3fe2fb6079
  (CI gate-wiring fix commit: 643941e0e7c961ba15736cc0dd706804b58ae3b1)
- Validated-by: session https://claude.ai/code/session_01Yaa1kbLLTETtfbnYGModqm
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #218 — the relay's own tracking PR recording that it had closed
  PR #207's CI gate-coverage gap; found FTF-0206/PR #207 pending while
  checking `docs/certifications/queue.md`/`docs/spec-matrix.yaml` per
  this shift's own mandate)
- Date: 2026-09-23
- **Session-ID check:** PR #207's three commits carry
  `session_015xEcu3kUMRfWMZwkav4cVc` (implementation + PROVISIONAL
  submission) and `session_014KbgfXyWDwnaSHrYwYChEC` (the `origin/main`
  merge that added the missing `test_ftf_0206.py` gate step). This
  reviewing session's own ID is `session_01Yaa1kbLLTETtfbnYGModqm` — no
  match against either, independent per Law 4.
- **Diff read directly** against PR #207's real head (`643941e`): exactly
  the task packet's own files (`packages/litellm_client/
  model_group_info_optional_enrichment.py`, `tests/tasks/
  test_ftf_0206.py`) plus expected tracking docs (`docs/spec-matrix.yaml`,
  `QUEUE.md`, `PROGRESS.md`, `docs/implementation/task-packets/
  FTF-0206.md`, `docs/certifications/queue.md`) and one new CI step
  (`gate`'s own FTF-0206 step, plus a comment trim on the neighboring
  FTF-0205 step). No scope creep; no route file, matching the disclosed
  "no live caller yet" precedent FTF-0202/FTF-0205/FTF-0207/FTF-0506
  already established.
- **CI re-checked directly on the real PR #207 head (`643941e`), not
  taken on faith:** 27/27 jobs `success`, `mergeable_state: clean` (run
  `35877632291`). Pulled the `gate` job's own raw log directly and
  confirmed `tests/tasks/test_ftf_0206.py` genuinely executes and passes
  inline, immediately after the FTF-0506 step: "FTF-0206 /model_group/
  info optional enrichment: all checks passed", followed by "Product-
  truth: 17/17 product-true" and "Status: 51/300 certified · 17
  product-true" in that same real run — this is the exact evidence
  standard the earlier "NOT CERTIFIED (CI never runs this task's own
  test)" review demanded before resubmission, now genuinely met.
- **Own independent verification, not the producer's local claims:**
  - `sha256sum CLAUDE.md` matched the committed `CLAUDE.md.sha256` before
    starting.
  - Ran `tests/tasks/test_ftf_0206.py` directly in a fresh `git worktree`
    at `643941e` — all checks pass.
  - Own fault injection, a third distinct vector from the producer's own
    two (widened allowlist leaking a credential-shaped field; disabled
    capability_supported gate): commented out the `response.status in
    (401, 403)` → `invalid_token` branch entirely. Re-ran the suite —
    **failed for real** ("a permission-denied 403 is reported as
    invalid_token, not fabricated as completed"), proving that guard is
    load-bearing, not decorative. Reverted from a pre-edit backup; `git
    diff --stat` confirmed byte-identical; suite re-ran clean
    immediately after.
  - `uv run ruff check .` / `ruff format --check .` / `mypy .` — clean,
    whole repo (200 source files).
  - `scripts/product_truth_check.py --fail-on-unwired` — own run,
    **17/17**, legitimately unchanged (`proving_symbol` confirmed blank
    in `docs/spec-matrix.yaml` — no importer wired to call
    `enrich_visible_model_groups` yet, same disclosed "no live caller"
    precedent already accepted for FTF-0202/FTF-0205/FTF-0207/FTF-0506;
    a present `proving_symbol` would have made this unchanged count
    unacceptable, but none is claimed here).
- `python3 tools/r1_handoff.py packet FTF-0206 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1 ("Optional
  enrichment runs only with explicit supported capability and safe
  permission") confirmed met by the shipped module's required, no-default
  `capability_supported` keyword-only parameter and its single
  least-privilege inference credential; negative_acceptance ("403/
  not-supported leaves limited metadata without failing basic discovery
  or gaining management rights") confirmed met both by the producer's own
  two fault-injection demonstrations and this review's own third one
  above, plus direct code reading of the 403/404 branches.
- Dependencies FTF-0201/FTF-0203/FTF-0204 confirmed genuinely `CERTIFIED`
  in `docs/spec-matrix.yaml` before relying on them.
- No blocking defect found. Merged PR #207 (merge commit
  `7ee47f8c4fa2f7dbce7d6557a29bdb82b4369a53`). `docs/spec-matrix.yaml` set
  to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to that merge commit. `proving_symbol` stays blank
  (no live caller yet, disclosed). Ran
  `scripts.task_claim.sync_certified_status()` — `['FTF-0206']` updated.
  Certified count moves 51/300 → 52/300. Product-true: unchanged at
  17/17.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0208 — Last-known-good catalog
- Criterion ID: FTF-0208
- PR: https://github.com/SJZLab/Sovra/pull/220 (`claude/wizardly-fermat-bbv5gh`
  → `main`) — implementation and PROVISIONAL submission. Merged by this
  review as merge commit `30d91376505447d33dfd9ac8d9e5ee92ba162ed9`.
- Merge commit (evidence): 30d91376505447d33dfd9ac8d9e5ee92ba162ed9
- Implementation commit: 8b695ab902a96891944c65bcbc42dcaebcee6f5d
- Validated-by: session https://claude.ai/code/session_01UC3z5JnAWfAPiBLUkogRTj
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #220; checked `docs/certifications/queue.md`/`docs/spec-matrix.yaml`
  per this shift's own mandate and found FTF-0208/PR #220 the sole pending
  PROVISIONAL item)
- Date: 2026-09-23
- **Session-ID check:** PR #220's three commits (implementation,
  PROVISIONAL submission, PR-link record) all carry
  `session_012onwtuejKNuCp5dC6NnuAE`. This session's own ID is
  `session_01UC3z5JnAWfAPiBLUkogRTj` — no match, independent per Law 4.
- **Diff read directly** against PR #220's real head (`370334d`): exactly
  the task packet's own new module
  (`packages/litellm_client/last_known_good_catalog.py`) and test
  (`tests/tasks/test_ftf_0208.py`), plus one new `gate`-job CI step and
  expected tracking docs. No scope creep.
- **CI re-checked on the real head, not taken on faith:** 27/27 jobs
  green (run `35892825208`, all `conclusion: success` on head SHA
  `370334de7986c22b70554c52228f7668b84b87db`; three jobs — 
  `secret-manager-integration`/`dataset-source-connection-integration`/
  `target-application-connection-integration` — took 5-7 minutes each
  because they build OpenBao from source on the runner, not because of a
  failure; confirmed the identical shape reproduced on the prior,
  superseded run for this same PR/branch, ruling out anything specific to
  this PR's final commit). Pulled the `gate` job's own raw log directly
  and confirmed `tests/tasks/test_ftf_0208.py` genuinely executes and
  passes inline ("FTF-0208 last-known-good catalog: all checks passed"),
  immediately followed by real "Product-truth: 17/17 product-true" /
  "Status: 52/300 certified · 17 product-true" output in that same run.
- **Own independent verification, in a fresh `git worktree` at the real
  PR head (`370334d`):**
  - Ran `uv run python3 tests/tasks/test_ftf_0208.py` directly — all
    checks pass, including the real end-to-end check against a real
    local HTTPS gateway (confirmed `_UPDATE_CA_AVAILABLE`/`_TRUSTED_LEAF`
    were genuinely set in this sandbox too, so that check actually ran,
    not merely skipped).
  - Own fault injection, a vector distinct from the producer's own two:
    removed the entire removed-id marking loop from `_apply_successful`
    (rather than the producer's own narrower "hard-code `removed_ids` to
    an empty set"). Five checks genuinely failed for real (removal
    detection, the alert-without-restoring-access guarantee, and the
    real end-to-end gateway sequence all broke); reverted from a pre-edit
    backup, `diff` confirmed byte-identical, suite re-ran clean
    immediately after.
  - `uv run ruff check .` / `ruff format --check .` / `mypy
    packages/litellm_client/last_known_good_catalog.py
    tests/tasks/test_ftf_0208.py` — clean, pinned toolchain (`ruff
    0.6.9`, matching CI).
  - `uv run python3 tests/tasks/test_ftf_0204.py` /
    `test_ftf_0207.py` re-run clean — no regression to either
    already-CERTIFIED dependency this task's own module imports from.
  - `python3 scripts/product_truth_check.py --fail-on-unwired` — own
    run, **17/17**, legitimately unchanged (`proving_symbol` confirmed
    blank — no live caller yet, same disclosed "no live caller"
    precedent already accepted for FTF-0202/FTF-0205/FTF-0207).
  - `python3 scripts/validate_spec.py --strict` — clean (after
    `git fetch --unshallow`, this sandbox's own pre-existing
    shallow-clone artifact, same documented precedent prior shifts
    already established).
- `python3 tools/r1_handoff.py packet FTF-0208 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1 ("Persist
  historical catalog and mark stale/inaccessible separately from
  callable entries") confirmed met by `CatalogEntry.status`, always
  exactly one of `callable`/`stale`/`removed`, retained for every id ever
  discovered; negative_acceptance ("A last-known-good snapshot cannot
  authorize queued/new inference after revocation or freshness
  expiration") confirmed met by `is_call_authorized`'s own two
  unconditional early-return checks (`access_revoked`,
  `freshness_expired`), both reached before any entry's own callable
  status is consulted — confirmed both by the producer's own two
  fault-injection demonstrations and this review's own third one above,
  plus direct code reading.
- Dependencies FTF-0201/FTF-0207 confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- **One evidence-accuracy correction, not a functional defect:** the
  submission's own claim of "24 pure no-DB/no-egress checks" (repeated in
  the PR body, commit message, and `docs/spec-matrix.yaml` notes) does
  not match the actual file: `tests/tasks/test_ftf_0208.py` has 20 pure
  test functions (33 `check()` assertions) plus 1 real end-to-end test
  function (8 further assertions) — 21 functions / 41 assertions total,
  not 24 by either count. Every one of those checks was independently
  re-run and genuinely passes; this is a documentation-accuracy note for
  the record, not a certification blocker.
- No blocking defect found. Merged PR #220 (merge commit
  `30d91376505447d33dfd9ac8d9e5ee92ba162ed9`). `docs/spec-matrix.yaml` set
  to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to that merge commit. `proving_symbol` stays blank
  (no live caller yet, disclosed — FTF-0215 is the later wiring step).
  Ran `scripts.task_claim.sync_certified_status()` — `['FTF-0208']`
  updated. Certified count moves 52/300 → 53/300. Product-true: unchanged
  at 17/17.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0209 — Catalog anomaly detector
- Criterion ID: FTF-0209
- PR: https://github.com/SJZLab/Sovra/pull/222 (`claude/wizardly-fermat-1734s6`
  → `main`) — implementation and PROVISIONAL submission. Merged by this
  review as merge commit `78ab4537298e1b484f42fffc0d2ae5d27d85137f`.
- Merge commit (evidence): 78ab4537298e1b484f42fffc0d2ae5d27d85137f
- Implementation commit: 50aa1c155a779c592dcaf1d300e347852d1626f2
- Validated-by: session https://claude.ai/code/session_017u7Tfwvfkiu4M9yR7jJAye
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #222; checked `docs/certifications/queue.md` per this shift's own
  mandate and found FTF-0209/PR #222 the sole pending PROVISIONAL item)
- Date: 2026-09-23
- **Session-ID check:** PR #222's commits (implementation, PROVISIONAL
  submission, PR-link record) all carry `session_01WHznaRLHjmkr46cKdoX26J`.
  This session's own ID is `session_017u7Tfwvfkiu4M9yR7jJAye` — no match,
  independent per Law 4.
- **Diff read directly** against PR #222's real, current head (`4a5b020`,
  fetched fresh — the trigger's advertised head `a7a0904` was already one
  commit stale by the time this review ran; the intervening commit was
  only a `docs/certifications/queue.md` PR-link record, same session ID,
  no functional change): exactly the task packet's own new module
  (`packages/litellm_client/catalog_anomaly_detector.py`) and test
  (`tests/tasks/test_ftf_0209.py`), plus one new `gate`-job CI step, the
  new task-packet doc, and expected tracking-doc updates. No scope creep.
- **CI re-checked on the real head, not taken on faith:** waited out the
  actual run rather than trusting the webhook's snapshot — 27/27 jobs
  green (run `35906197462`, all `conclusion: success` on head SHA
  `4a5b0201a1d8d217bc1432d955a4d7cc3f69cf2e`; three jobs —
  `secret-manager-integration`/`dataset-source-connection-integration`/
  `target-application-connection-integration` — took 5-8 minutes each
  because they build OpenBao from source on the runner, not because of a
  failure, same documented shape as FTF-0208's own review). The `gate` job
  (which runs this task's own `tests/tasks/test_ftf_0209.py` inline)
  completed `success`.
- **Own independent verification, directly against the real merged
  content in this working tree:**
  - Ran `python3 tests/tasks/test_ftf_0209.py` directly — "FTF-0209
    catalog anomaly detector: all checks passed" (all 20 checks).
  - Own fault injection #1 (reproducing the producer's own claimed
    vector independently rather than taking it on faith): removed the
    "already acknowledged" guard from `acknowledge()`. The
    double-acknowledge negative check genuinely failed for real; reverted
    from a pre-edit backup, `diff` confirmed byte-identical, suite re-ran
    clean immediately after.
  - Own fault injection #2, the exact negative_acceptance risk
    ("...or restored unauthorized execution"): made `acknowledge()`
    unconditionally restore every wrapped-catalog entry to `callable`.
    10 checks genuinely failed for real (every removed id came back
    authorized after acknowledgement; the byte-identical-catalog-state
    check broke); reverted, `diff` confirmed byte-identical, suite re-ran
    clean immediately after.
  - `uv run ruff check .` / `ruff format --check .` / `uv run mypy
    packages/litellm_client/catalog_anomaly_detector.py
    tests/tasks/test_ftf_0209.py` — clean, pinned toolchain (`ruff
    0.6.9`, matching CI).
  - `python3 tests/tasks/test_ftf_0208.py` (this task's own CERTIFIED
    dependency) re-run clean — no regression. `tests/tasks/
    test_ftf_0201.py` could not be re-run in this sandbox (`uvicorn` not
    installed locally, an environment gap unrelated to this diff — the
    PR's own file list has no `pyproject.toml`/`uv.lock` change, and CI's
    own `gate` job already independently proves it via `uv sync`).
  - `python3 scripts/product_truth_check.py --fail-on-unwired` — own run,
    **17/17**, legitimately unchanged (`proving_symbol` confirmed blank —
    no live caller yet, same disclosed "no live caller" precedent already
    accepted for FTF-0202/FTF-0205/FTF-0207/FTF-0208).
- `python3 tools/r1_handoff.py packet FTF-0209 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause 1 ("A >50%
  reduction triggers recorded anomaly/retry/admin acknowledgement while
  immediate authorization remains enforced") confirmed met by `record()`'s
  own ordering (the wrapped catalog is always updated *before* any
  anomaly evaluation, so nothing in this detector's own state can gate
  real authorization) plus `AnomalyStatus`'s forward-only state machine
  (`open` → `retry_pending` → `escalated` → `acknowledged`, never
  silently reverting). negative_acceptance ("Repeated empty list cannot
  cause silent destructive deletion or restored unauthorized execution")
  confirmed met structurally: no delete/purge operation anywhere in this
  module's public surface, an open incident is updated in place rather
  than discarded/duplicated, and `acknowledge()`/`retry()` never read or
  write the wrapped catalog's entries at all — confirmed both by direct
  code reading and this review's own fault injection #2 above.
- Dependencies FTF-0201/FTF-0208 confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- **One observation, not a defect:** `acknowledge()`'s current signature
  takes no parameter that could reach the wrapped catalog, so nothing in
  the existing test suite exercises (or needs to exercise) a
  caller-supplied catalog reference. Not a gap against this task's own
  scope (no live caller exists yet, disclosed) — flagged here so FTF-0215
  (the real wiring/route task) keeps that same guarantee true of whatever
  API surface ultimately exposes `acknowledge()`.
- No blocking defect found. Merged PR #222 (merge commit
  `78ab4537298e1b484f42fffc0d2ae5d27d85137f`). `docs/spec-matrix.yaml` set
  to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to that merge commit. `proving_symbol` stays blank
  (no live caller yet, disclosed — FTF-0215 is the later wiring step).
  Ran `scripts.task_claim.sync_certified_status()` — `['FTF-0209']`
  updated. Certified count moves 53/300 → 54/300. Product-true: unchanged
  at 17/17.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0210 — Real chat probe
- Criterion ID: FTF-0210
- PR: https://github.com/SJZLab/Sovra/pull/224 (`claude/wizardly-fermat-fxdnkd`
  → `main`) — implementation and PROVISIONAL submission. Merged by this
  review as merge commit `d38930b4d7b6b94cc7ba72dfd2c873a3627cd94a`.
- Merge commit (evidence): d38930b4d7b6b94cc7ba72dfd2c873a3627cd94a
- Implementation commit: 19a247c1cf5334e416c8a14b6c41d1d78e3ae9ea
- Validated-by: session https://claude.ai/code/session_016NpBXKWyLWwgJBqBw4egi6
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #224)
- Date: 2026-09-23
- **Session-ID check:** PR #224's three commits (implementation, PROVISIONAL
  submission, PR-link record) all carry `session_014J57y6ueTRgmrVX7nzh9rK`.
  This session's own ID is `session_016NpBXKWyLWwgJBqBw4egi6` — no match,
  independent per Law 4.
- **Diff read directly** against PR #224's real head
  (`0756334aaba315480d70af334eb89d983381306a`): exactly the task packet's
  own new module (`packages/litellm_client/real_chat_probe.py`) and test
  (`tests/tasks/test_ftf_0210.py`), plus one new `gate`-job CI step, the
  new task-packet doc, and expected tracking-doc updates. No scope creep.
- **CI re-checked on the real head, not taken on faith:** waited out the
  actual run to full completion rather than trusting the webhook's
  snapshot — 27/27 jobs green (run `35920520821`, all `conclusion:
  success` on head SHA `0756334aaba315480d70af334eb89d983381306a`; three
  jobs — `secret-manager-integration`/`dataset-source-connection-integration`/
  `target-application-connection-integration` — took 5-7 minutes each
  because they build OpenBao from source on the runner, cross-checked
  directly via `get_workflow_job` mid-run to confirm the build step was
  genuinely progressing rather than stalled; same documented shape as
  FTF-0208's/FTF-0209's own reviews). The `gate` job (which runs this
  task's own `tests/tasks/test_ftf_0210.py` inline) completed `success`.
- **Own independent verification, directly against the real merged
  content in this working tree:**
  - Checked out PR #224's exact head into this worktree (`git checkout
    origin/claude/wizardly-fermat-fxdnkd -- .`), confirmed byte-identical
    via `git diff` before testing.
  - Unshallowed the local clone — the initial shallow checkout produced
    29 spurious `validate_spec.py --strict` `stale_commit` findings for
    unrelated, already-CERTIFIED tasks (an artifact of missing history
    depth, not a real defect); `scripts/validate_spec.py --strict` ran
    clean once unshallowed, and none of the 29 concerned FTF-0210 itself.
  - Ran `python3 tests/tasks/test_ftf_0210.py` directly — "FTF-0210 real
    chat probe: all checks passed". This sandbox had real
    `sudo`/`openssl`/`update-ca-certificates` access, so the live
    local-HTTPS-gateway round trips genuinely ran (not the disclosed
    skip path).
  - Own fault injection (reproducing the producer's own claimed vector
    independently rather than taking it on faith): removed the
    `if model_id not in approved_model_ids: raise
    ChatProbeNotAuthorizedError(...)` gate from `probe_chat` entirely.
    The authorization negative check genuinely failed for real (the
    mutated module attempted a probe against `https://127.0.0.1:1`
    instead of raising); reverted from a pre-edit backup, `diff`
    confirmed byte-identical, suite re-ran clean immediately after.
  - `uv run ruff check .` / `ruff format --check .` / `uv run mypy
    packages/litellm_client/real_chat_probe.py
    tests/tasks/test_ftf_0210.py` — clean, pinned toolchain.
  - `uv run python3 tests/tasks/test_ftf_0201.py` /
    `test_ftf_0204.py` / `test_ftf_0207.py` (this task's own CERTIFIED
    dependencies) re-run clean — no regression.
  - `python3 scripts/product_truth_check.py --fail-on-unwired` — own run,
    **17/17**, legitimately unchanged (`proving_symbol` confirmed blank —
    no live caller yet, same disclosed "no live caller" precedent already
    accepted for FTF-0202/FTF-0205/FTF-0207/FTF-0208/FTF-0209).
- `python3 tools/r1_handoff.py packet FTF-0210 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` — acceptance clause ("Explicitly
  authorized synthetic probe calls real model and records actual provider
  route, usage and latency") confirmed met by direct reading of
  `test_probe_succeeds_and_records_route_usage_latency` (a real round trip
  whose gateway response's own `model` field, deliberately different from
  the requested alias, is recorded as `resolved_model` with
  `route_identity_state=VERIFIED`, with real usage/latency also asserted
  from the actual response) and `test_probe_not_authorized_raises_before_any_network_call`
  (points at an unlistened port so a missing authorization gate would
  surface as a connection failure, not a raise — proving the gate runs
  first). negative_acceptance ("Sensitive content, unsupported parameters
  and permission/timeout errors are normalized without fabricated
  responses") confirmed met by direct reading of the four dedicated real
  round-trip tests (`test_sensitive_content_is_normalized_not_fabricated`,
  `test_unsupported_parameter_is_normalized_not_fabricated`,
  `test_gateway_permission_denied_is_invalid_token`,
  `test_timeout_is_normalized_not_fabricated`) plus
  `test_missing_usage_is_unknown_not_zero` and
  `test_missing_model_field_is_route_unverified` for Section 3.4/3.5's
  "unpriced/unknown, not zero cost" and `IDENTITY_UNVERIFIED` clauses.
- Dependencies FTF-0201/FTF-0204/FTF-0207 confirmed genuinely `CERTIFIED`
  in `docs/spec-matrix.yaml` before relying on them.
- No blocking defect found. Merged PR #224 (merge commit
  `d38930b4d7b6b94cc7ba72dfd2c873a3627cd94a`). `docs/spec-matrix.yaml` set
  to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to that merge commit. `proving_symbol` stays blank
  (no live caller yet, disclosed — FTF-0214/FTF-0215 are later wiring
  steps). Ran `scripts.task_claim.sync_certified_status()` —
  `['FTF-0210']` updated. Certified count moves 54/300 → 55/300.
  Product-true: unchanged at 17/17.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0211 — Real streaming probe
- Criterion ID: FTF-0211
- PR: https://github.com/SJZLab/Sovra/pull/228 (`claude/wizardly-fermat-lmo388`
  → `main`) — CI-fix follow-up, superseding PR #226
  (`claude/wizardly-fermat-pvnd0o`, closed as superseded by this review).
  Merged by this review as merge commit
  `423eb560ac7a7a5c088c33f30eda916bbff2ec77`.
- Merge commit (evidence): 423eb560ac7a7a5c088c33f30eda916bbff2ec77
- Implementation commit: 1d23dd39d166f0c46b3c7be28eb6cb19dbb89e54 (original
  PR #226 module); IPv6 gate-wiring CI fix: 24d4941c35e8466f5589b066ba6d882b107e1f4d
- Validated-by: session https://claude.ai/code/session_01UhsKjWNrrPmXWNxNwK2rw2
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #228)
- Date: 2026-09-24
- **Session-ID check:** PR #226's three commits (`1d23dd3`/`0c9167d`/
  `09295cf`) all carry `session_013qR8o5xkLqEQ2WQwpYyora`. PR #228's four
  own commits on top (`24d4941`/`9901e02`/`a424f91`/`027c3fc`/`e091879`)
  carry **no `Claude-Session` trailer at all** — a disclosed process gap
  in the producer's own commits, flagged here rather than silently
  worked around — but PR #228's own description identifies the producing
  session as `session_01YP8vr5mwHG2enbUQPMYXtf`. This reviewing session's
  own ID is `session_01UhsKjWNrrPmXWNxNwK2rw2` — no match to either
  producer session, independent per Law 4.
- **Prior review on record:** the 2026-09-23 Control-plane review
  immediately above this entry (session `session_014FtPJptPJDjFCVud3iLyUi`)
  found PR #226's own module logic and test suite (`real_streaming_probe.py`,
  `test_ftf_0211.py`) sound, but NOT CERTIFIED solely because the `gate`
  job crashed on the real GitHub Actions runner (`test_ftf_h006.py`'s
  `_detect_ipv6_support()` probe disagreeing with `_SERVER_V6`'s actual
  bind address family). This review does not re-litigate that sound
  module logic from scratch; it independently re-verifies it (see below)
  and focuses new scrutiny on the CI fix and the real CI evidence that
  was previously missing.
- **Diff read directly**: PR #228 is PR #226's exact head (`09295cf`)
  merged into a different session branch, plus the CI fix
  (`_ThreadingHTTPServerV6(ThreadingHTTPServer)` with
  `address_family = socket.AF_INET6`, used for `_SERVER_V6`), a new pure
  regression test (`test_ipv6_server_class_uses_the_same_address_family_the_probe_checks`),
  and tracking-doc updates. `git diff --stat` against `main`'s pre-merge
  tip confirmed exactly `.github/workflows/ci.yml`, `PROGRESS.md`,
  `docs/certifications/queue.md`, `docs/implementation/task-packets/FTF-0211.md`,
  `docs/spec-matrix.yaml`, `packages/litellm_client/real_streaming_probe.py`,
  `packages/security/outbound_guard.py`, `tests/tasks/test_ftf_0211.py`,
  `tests/tasks/test_ftf_h006.py` — no scope creep. The PR's head moved
  twice more during this review (a424f91 → 027c3fc → e091879), each time
  under a concurrent relay session; each intervening diff was confirmed
  via `git diff --stat` to be tracking-docs-only (no code change), and a
  fresh CI run was independently re-verified on each new head rather than
  trusting the prior head's result.
- **CI re-checked on the real final head, not taken on faith:** run
  `35941721974` on head `e0918796e7060342f8e32e92e7731796861d13d9` —
  **27/27 jobs `completed`/`success`**, `mergeable_state: clean`. The
  `gate` job (id `107450854572`) completed `success` in 3m29s; pulled its
  real log via `get_job_logs` and directly confirmed both
  `tests/tasks/test_ftf_h006.py` ("FTF-H006 shared outbound URL and
  credential guard: all checks passed") and `tests/tasks/test_ftf_0211.py`
  ("FTF-0211 real streaming probe: all checks passed") genuinely executed
  and passed on this exact head — the precise failure mode
  (`socket.gaierror` crashing the gate job at import time before
  `test_ftf_0211.py` ever ran) that blocked the 2026-09-23 review is
  gone. `product_truth_check.py --fail-on-unwired` in the same job log:
  `17/17`, unchanged.
- **Own independent verification, directly in this working tree** (`uv
  sync --all-packages` first — a partial sync had produced 12 spurious
  `mypy` `alembic`-stub errors, unrelated to this task's own files and
  resolved once fully synced):
  - `uv run python3 tests/tasks/test_ftf_h006.py` / `test_ftf_0211.py` —
    both "all checks passed" (the disclosed no-IPv6-stack skip message
    prints for the former, same as every prior review in this sandbox).
  - `uv run python3 tests/tasks/test_ftf_0210.py` / `test_ftf_0201.py`
    (dependencies) — both "all checks passed"; the tracebacks printed to
    stdout mid-run are expected server-side noise from deliberate
    negative-path connection drops in those suites' own fixtures, not
    failures.
  - `uv run ruff check .` / `ruff format --check .` — clean, repo-wide.
  - `uv run mypy .` — clean, repo-wide (208 source files).
  - `python3 scripts/validate_spec.py --strict` — clean (`git fetch
    --unshallow origin` first).
  - `python3 scripts/product_truth_check.py --fail-on-unwired` —
    `17/17`, unchanged.
  - **Own fault injection, two vectors, independent of the producer's own
    claimed vectors, each reverted byte-identical and the suite
    re-confirmed clean immediately after:**
    1. Reverted `_ThreadingHTTPServerV6.address_family` from `AF_INET6`
       back to the old bare-`ThreadingHTTPServer` default — the new
       regression check
       (`test_ipv6_server_class_uses_the_same_address_family_the_probe_checks`)
       genuinely failed for real; reverted, `diff` confirmed
       byte-identical, suite re-ran clean.
    2. Disabled `probe_stream_chat`'s `approved_model_ids` authorization
       gate (`if model_id not in approved_model_ids: raise
       ChatProbeNotAuthorizedError(...)` → `if False: raise ...`) — the
       negative test ("an unapproved model raises
       ChatProbeNotAuthorizedError instead of attempting a probe")
       genuinely failed; reverted, `diff` confirmed byte-identical, suite
       re-ran clean.
- `python3 tools/r1_handoff.py packet FTF-0211 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` pulled directly: acceptance clause 1
  ("Consume actual SSE stream, reconstruct text/tool deltas and account
  usage/termination") reconfirmed met — this module's logic is unchanged
  from the 2026-09-23 review's own independent confirmation. Acceptance
  clause 2 ("Independent review, exact merged commit, real relevant
  integration and negative-test evidence recorded") is now met — real,
  fresh, passing CI evidence exists on the actual merged commit (see
  above), which is exactly the clause the prior review could not confirm.
  `negative_acceptance` ("Malformed/truncated streams, client
  cancellation and gateway retry do not leak connection resources or
  double-bill silently") reconfirmed via this review's own fault
  injection above plus direct reading of
  `test_probe_closes_the_real_connection_on_every_return_path` and
  `test_gateway_retry_never_happens_silently_inside_the_probe`.
  Dependencies FTF-0201/FTF-0210 confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml`.
- No blocking defect found. Merged PR #228 (merge commit
  `423eb560ac7a7a5c088c33f30eda916bbff2ec77`) into `main`. Closed PR #226
  as superseded (comment posted there pointing to #228 and this
  certification). `docs/spec-matrix.yaml` set to `certification_state:
  CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to that
  merge commit. `proving_symbol` stays blank (no live caller yet,
  disclosed — same precedent as every sibling FTF-020x task). Ran
  `scripts.task_claim.sync_certified_status()` — `['FTF-0211']` updated.
  Certified count moves 55/300 → 56/300. Product-true: unchanged at
  17/17.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0213 — Real structured-output probe
- Criterion ID: FTF-0213
- PR: https://github.com/SJZLab/Sovra/pull/232 (`claude/wizardly-fermat-y3ozcu`
  → `main`). Merged by this review as merge commit
  `1751211044041e15fc4708b4be552c3957509e80`.
- Merge commit (evidence): 1751211044041e15fc4708b4be552c3957509e80
- Implementation commit: 3db8b84a9ddea64642e239126eb38d962dd77b33
- Validated-by: session https://claude.ai/code/session_01J2UD5rqhJWHsPCn88wgMp8
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #232)
- Date: 2026-09-24
- **Session-ID check:** all three commits on PR #232 (`3db8b84`/`39c5feb`/
  `61d80a3`) carry `Claude-Session: .../session_01XvEtUhKF5SaS7brwfqGkeZ` —
  no match to this reviewing session's own ID
  (`session_01J2UD5rqhJWHsPCn88wgMp8`), independent per Law 4.
- **Diff read directly**: read the full PR diff via GitHub (1950 lines).
  Confirmed exactly seven files touched: `.github/workflows/ci.yml` (one
  added gate step), `PROGRESS.md`, `QUEUE.md`, `docs/certifications/queue.md`,
  `docs/spec-matrix.yaml` (FTF-0213 → `PROVISIONAL`/`L1-PROVEN`), and the
  two new files `packages/litellm_client/real_structured_output_probe.py`
  (563 lines) and `tests/tasks/test_ftf_0213.py` (971 lines) — no scope
  creep. The PR's head moved once during this review (`39c5feb` →
  `61d80a3`, the same producer session recording its own confirmed-green-CI
  note); confirmed via `git show --stat` to be `PROGRESS.md`/
  `docs/certifications/queue.md` tracking-docs-only, no code change.
- **CI re-checked on the real final head, not taken on faith:** polled
  `gh api .../commits/61d80a3.../check-runs` to completion (not the
  producer's own report alone) — **27/27 checks `completed`/`success`**,
  `gate` included, `mergeable_state: clean`, `mergeable: true`.
- **Own independent verification, directly in a fresh git worktree
  checked out from the PR's actual head** (`uv sync --all-packages`
  first):
  - `uv run python3 tests/tasks/test_ftf_0213.py` — "all checks passed",
    exit code 0, zero `FAIL` lines (the SSL EOF traceback printed to
    stderr mid-run is expected server-thread noise from the deliberate
    client-side timeout test's connection drop, not a failure — same
    convention every sibling FTF-020x suite already documents).
  - `uv run ruff check` / `ruff format --check` on both new files — clean.
  - `uv run mypy` on both new files — clean.
  - `python3 scripts/validate_spec.py --strict` — clean (`git fetch
    --unshallow origin` first; the shallow-clone `stale_commit` noise
    seen before unshallowing is the same pre-existing artifact every
    prior certifying session has already documented).
  - `python3 scripts/product_truth_check.py --fail-on-unwired` — `17/17`,
    legitimately unchanged (`proving_symbol` stays blank — this task
    deliberately has no live caller yet, same disclosed Law 2 precedent
    every sibling FTF-020x probe task, including already-CERTIFIED
    FTF-0210/FTF-0211, already established — not a fabricated pass).
  - **Own fault injection, one vector, independent of the producer's own
    three claimed vectors, reverted byte-identical and the suite
    re-confirmed clean immediately after:** changed
    `_validate_against_schema`'s integer check from a strict
    `isinstance(instance, int)` guard to an `int(instance)`-coercion
    fallback (so a numeric-looking string like `"1"` would pass) — the
    suite genuinely failed for real (exit code 1, 2 `FAIL` lines: the
    pure `test_validate_against_schema_never_coerces` unit check and the
    live `test_coerced_type_is_a_schema_violation_not_probed` round
    trip). Reverted; `diff` against the original confirmed
    byte-identical; suite re-ran clean (exit 0, 0 `FAIL` lines).
- `python3 tools/r1_handoff.py packet FTF-0213 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` pulled directly: acceptance clause 1
  ("Exercise the model's advertised structured-output protocol and
  schema-validate actual response") confirmed met by
  `test_probe_succeeds_and_records_route_usage_latency` (a real
  schema-valid round trip) and
  `test_probe_sends_a_bounded_synthetic_json_schema_request` (the real
  `response_format: json_schema` bytes actually sent over the wire).
  `negative_acceptance` ("JSON-looking prose, unknown parameter rejection
  or coercion is not silently treated as strict schema support")
  confirmed met by three distinct real states, never collapsed into a
  fabricated `PROBED`: `NOT_JSON` (`test_json_looking_prose_is_not_json`),
  `UNSUPPORTED_PARAMETER`
  (`test_unsupported_response_format_parameter_is_not_fabricated`,
  including its dedicated exactly-one-request assertion — no silent
  retry without structured mode), and `SCHEMA_VIOLATION`
  (`test_coerced_type_is_a_schema_violation_not_probed` plus the sibling
  missing-required-field/additional-property/enum-mismatch/top-level-array
  checks). Acceptance clause 2 ("Independent review, exact merged commit,
  real relevant integration and negative-test evidence recorded") is met
  by this review itself. Dependencies FTF-0201/FTF-0210 confirmed
  genuinely `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- No blocking defect found. Merged PR #232 (merge commit
  `1751211044041e15fc4708b4be552c3957509e80`) into `main`.
  `docs/spec-matrix.yaml`'s FTF-0213 entry set to `certification_state:
  CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to that
  merge commit; `proving_symbol` stays blank (no live caller yet,
  disclosed — same precedent as every sibling FTF-020x task). Ran
  `scripts.task_claim.sync_certified_status()` — `['FTF-0213']` updated.
  Certified count moves 56/300 → 57/300. Product-true: unchanged at
  17/17.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-04A07 — TargetSourceSnapshot schema + immutable provenance
- Criterion ID: FTF-04A07
- PR: https://github.com/SJZLab/Sovra/pull/234 (`claude/wizardly-fermat-i9vw6i`
  → `main`). Merged by this review as merge commit
  `e0e17920b9df2fae57fb635c1549a244183af106`.
- Merge commit (evidence): e0e17920b9df2fae57fb635c1549a244183af106
- Implementation commit: 5b34723e2be2ba243927962c7b4c1a269b370c62
- Validated-by: session https://claude.ai/code/session_01GcVXDuiPV5A82LTGNSh5Mu
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #234)
- Date: 2026-09-24
- **Session-ID check:** all three commits on PR #234 (`5b34723`/`09a6cf5`/
  `019e538`) carry `Claude-Session: .../session_01JCgZ323QmB1GH3gjt8tePc` —
  no match to this reviewing session's own ID
  (`session_01GcVXDuiPV5A82LTGNSh5Mu`), independent per Law 4.
- **Diff read directly**: 21 files changed (8817 insertions/19 deletions).
  Confirmed the three new domain files
  (`apps/api/app/target_connectors/target_source_snapshot_schema.py`/
  `_crud.py`/`_routes.py`), the new migration
  (`apps/api/alembic/versions/0012_target_source_snapshots.py`), the CI
  wiring (`.github/workflows/ci.yml`: gate step + dedicated
  `target-source-snapshot-integration` job), contract extensions
  (`packages/contracts/openapi/04A-interface.yaml`,
  `.../jsonschema/04A-domain.schema.json`), the two new test files, five
  new task packets generated while deriving the frontier, and the
  tracking-doc/spec-matrix updates — no unexplained scope creep. The
  packet's own generic suggested path
  (`packages/target-connectors/targetsourcesnapshot_schema_immutable_provenance.py`)
  was correctly refined to match FTF-04A01's real
  `apps/api/app/<domain>/*_schema.py` layout precedent, disclosed in the
  module's own docstring.
- **CI re-checked on the real final head, not taken on faith:** subscribed
  to the PR's activity and waited for the real `check_suite.completed`
  webhook, then polled `pull_request_read get_check_runs` directly on head
  `019e538eb415d83bf227d8857723db86a009416d` — **28/28 checks
  `completed`/`success`**, `gate` and the dedicated
  `target-source-snapshot-integration` job included, `mergeable_state:
  clean`. No open reviews or comments.
- **Own independent verification, directly on this session's checkout of
  the PR's actual head** (`uv sync --all-packages` first):
  - `uv run python3 tests/tasks/test_ftf_04a07.py` — "all checks passed",
    no-DB suite, exit code 0.
  - `uv run python3 tests/integration/test_ftf_04a07_target_source_snapshot.py`
    against a real, freshly created local PostgreSQL 16 database (not a
    mock or SQLite stand-in) — "all checks passed".
  - Full migration chain independently round-tripped in a second scratch
    database: `alembic upgrade head` → `downgrade -1` → `upgrade head`,
    clean at every step.
  - `uv run ruff check` / `ruff format --check` / `uv run mypy` on all
    three new domain files plus the migration — clean.
  - `python3 scripts/product_truth_check.py --fail-on-unwired` — `18/18`,
    confirmed legitimate (not a 0/0 pass): `build_target_source_snapshot_router`
    is a real non-test caller, confirmed directly in
    `apps/api/app/main.py` (imported and `app.include_router(...)`'d).
  - Regression: `test_ftf_04a01.py`, `test_ftf_1001.py`, `test_ftf_0201.py`
    all re-ran clean — no regression to FTF-04A01's schema, `model_versions`,
    or any other certified table.
  - **Own fault injection, two vectors, independent of the producer's own
    two claimed vectors, each reverted byte-identical and the suite
    re-confirmed clean immediately after:**
    1. Removed the CRUD-layer cross-connection reassignment guard (the
       `row.target_application_connection_id != connection_id` check in
       `target_source_snapshot_crud.py::_fetch_or_raise`) — the live
       integration suite genuinely failed for real (exit code 1, 3
       distinct `FAIL` lines covering the reassignment-refusal,
       per-connection-scoping, and cross-connection-get checks). Reverted;
       `git diff` confirmed byte-identical; suite re-ran clean.
    2. Independently reproduced the `ck_target_source_snapshots_dirty_requires_archive`
       CHECK constraint at raw SQL, as the Postgres superuser, bypassing
       the application layer entirely (`INSERT ... capture_method =
       'local_dirty_snapshot'` with both archive fields `NULL`) — genuinely
       rejected by Postgres itself, proving this is a real database-level
       guarantee, not only an application-level check a bypassing write
       could evade.
  - Independently confirmed the "cannot be mutated" claim at the database
    privilege level: `\dp target_source_snapshots` shows
    `sovra_app=ar/postgres` — SELECT + INSERT only, no UPDATE/DELETE grant
    at all.
- `python3 tools/r1_handoff.py packet FTF-04A07 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` pulled directly: acceptance clause 1
  ("Store immutable snapshot provenance, source identity, status and
  superseded relationship") confirmed met field-for-field
  (`commit_sha`/`tree_hash`/`capture_method` = provenance,
  `target_application_connection_id` = source identity, `analysis_status`
  = status, `supersedes_snapshot_id` = superseded relationship).
  `negative_acceptance` ("Existing snapshot cannot be mutated or
  reassigned to another repository/project") confirmed met by the two
  real enforcement layers verified above (database grant for "mutated";
  composite self-referencing FK + CRUD-layer fetch-and-compare for
  "reassigned", cross-tenant and cross-connection respectively).
  Acceptance clause 2 ("Independent review, exact merged commit, real
  relevant integration and negative-test evidence recorded") is met by
  this review itself. Dependency FTF-04A01 confirmed genuinely
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on it.
- No blocking defect found. Merged PR #234 (merge commit
  `e0e17920b9df2fae57fb635c1549a244183af106`) into `main`.
  `docs/spec-matrix.yaml`'s FTF-04A07 entry set to `certification_state:
  CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to that
  merge commit; `proving_symbol` stays
  `build_target_source_snapshot_router` (real live caller, confirmed
  above). Ran `scripts.task_claim.sync_certified_status()` —
  `['FTF-04A07']` updated. Certified count moves 57/300 → 58/300.
  Product-true: 17/17 → 18/18.

**Conclusion:** Certified L2-CERTIFIED.

## FTF-R002 — Authenticated internal component bindings and reconciled adapter foundation
- Criterion ID: FTF-R002
- PR: https://github.com/SJZLab/Sovra/pull/236 (`claude/wizardly-fermat-lqx141`
  → `main`). Merged by this review as merge commit
  `d257254c78678e978533190b327d4d8758fb3914`.
- Merge commit (evidence): d257254c78678e978533190b327d4d8758fb3914
- Implementation commit: b1906932a57420b96b8bd4d039b876c55d74661d
- Validated-by: session https://claude.ai/code/session_01HvNguPrCrUuXqqFkduNmQi
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #236)
- Date: 2026-09-24
- **Session-ID check:** both commits on PR #236 (`b190693`/`4552bad`) carry
  `Claude-Session: .../session_01MNfCDcY6erxuhN5p3JfbGm` — no match to this
  reviewing session's own ID (`session_01HvNguPrCrUuXqqFkduNmQi`),
  independent per Law 4.
- **Diff read directly**: 16 files changed (4635 insertions/12 deletions).
  Confirmed the new domain files
  (`apps/api/app/component_bindings/component_binding_schema.py`/
  `_crud.py`/`_routes.py`), the new migration
  (`apps/api/alembic/versions/0013_component_bindings.py`), the new
  worker job identity module (`packages/component_contracts/job_identity.py`),
  the CI wiring (`.github/workflows/ci.yml`: gate step + dedicated
  `component-bindings-integration` job), the two new test files, and the
  tracking-doc/spec-matrix updates — no unexplained scope creep.
- **Frozen-contract cross-check**: read
  `packages/component-contracts/architecture.schema.json`'s own
  `componentBinding` definition directly and compared it field-by-field
  against `component_binding_schema.py`'s `ComponentBinding` ORM model —
  exact match on all nine frozen fields
  (`component_key`/`security_zone_scope`/`project_scope`/
  `deployment_mode`/`approved_endpoint`/`secret_reference`/
  `network_policy_identity`/`health_result`/`compatibility_lock_reference`)
  and both `allOf`/`if`/`then` structural invariants (Axolotl/Inspect AI
  must be `worker_package`; LiteLLM must be `existing_gateway`), the
  disclosed mid-implementation correction this task's own PROGRESS.md
  entry flagged for extra scrutiny.
- **CI re-checked on the real final head, not taken on faith:** polled
  `pull_request_read get_check_runs` directly on head
  `4552badb58a173688433bb290540c076aea52c7f` until all jobs completed —
  **29/29 checks `completed`/`success`**, `gate` and the dedicated
  `component-bindings-integration` job included, `mergeable_state: clean`.
  No open reviews or comments.
- **Own independent verification, directly on this session's checkout of
  the PR's actual head** (`uv sync --all-packages` first):
  - `uv run python3 tests/tasks/test_ftf_r002.py` — "all checks passed",
    no-DB suite, exit code 0.
  - `uv run python3 tests/integration/test_component_bindings.py` against
    a real, freshly created local PostgreSQL 16 database (`sovra_migrate`
    role, `sovra_ftfr002` db, matching the CI job's own credentials) — "all
    checks passed".
  - Full migration chain independently round-tripped in the same scratch
    database: `alembic upgrade head` → `downgrade -1` → `upgrade head`,
    clean at every step (`alembic current` confirmed `0013_component_bindings
    (head)` afterward).
  - `uv run ruff check .` / `ruff format --check .` / `uv run mypy .` —
    clean repo-wide.
  - `python3 scripts/validate_spec.py --strict` — clean.
  - `python3 scripts/product_truth_check.py --fail-on-unwired` — `19/19`,
    confirmed legitimate (not a 0/0 pass): `build_component_binding_router`
    is a real non-test caller, confirmed directly in
    `apps/api/app/main.py` (imported and `app.include_router(...)`'d).
  - Regression: `test_ftf_r001.py`, `test_ftf_04a01.py`, `test_ftf_04a07.py`,
    `test_ftf_1001.py`, `test_ftf_0201.py` all re-ran clean — no regression
    to FTF-R001's own frozen-contract evidence or any other certified
    table (this task touches no existing migration/table).
  - **Own fault injection, three vectors, independent of the producer's
    own three claimed vectors but targeting the same code paths, each
    reverted byte-identical (`diff` confirmed) and the suite re-confirmed
    clean immediately after:**
    1. Collapsed `compute_health`'s non-2xx branch to always return
       `("healthy", ...)` — the no-DB suite genuinely failed for real (3
       distinct `FAIL` lines: a real 404/500/503 response never reports
       `healthy`). Reverted; re-ran clean.
    2. Removed both frozen-architecture structural invariant checks from
       `ComponentBinding.__init__` (Axolotl/Inspect AI worker_package-only;
       LiteLLM existing_gateway-only) — the no-DB suite genuinely failed
       for real (3 distinct `FAIL` lines, one per invariant case).
       Reverted; re-ran clean.
    3. Removed `ck_component_bindings_worker_package_no_endpoint` from the
       migration and re-applied it against a fresh real local Postgres 16
       database — the dedicated live-Postgres raw-INSERT-bypass check
       genuinely failed for real (exit 1, "the real
       ck_component_bindings_worker_package_no_endpoint CHECK constraint
       rejects a raw INSERT that bypasses the ORM entirely" — FAIL).
       Reverted; `diff` confirmed byte-identical; full round-trip re-ran
       clean.
- `python3 tools/r1_handoff.py packet FTF-R002 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` pulled directly: acceptance ("Real
  service identity/version/health with approved TLS/CA and tenant-aware
  grants; packages use worker identities, existing LiteLLM is not
  overwritten" and "Idempotent sync outbox maps Factory jobs to external
  IDs and reports actual pending/failure states") confirmed met —
  `compute_health`/`validate_component_binding` dial through the existing
  FTF-H006 outbound guard and never fabricate `healthy`; Axolotl/Inspect
  AI/LiteLLM are structurally constrained to their real deployment modes
  at both the ORM and database-CHECK level; `record_sync_outcome` provides
  real replay-same-fingerprint/reject-different-fingerprint semantics
  proven live against Postgres. `negative_acceptance` ("Deny cross-tenant
  IDs, arbitrary URLs/redirects, browser vendor-admin credentials, direct
  vendor DB writes and fake health after dependency outage") confirmed met
  by composition of the already-certified FTF-H003 (`authorize_resource_
  ownership`, cross-tenant denial) and FTF-H006 (`guarded_fetch`, URL/
  redirect policy) primitives — no new authorization primitive introduced,
  confirmed by reading the imports directly — plus the three fault
  injections above proving the "fake health" guarantee is real, not
  merely asserted. Dependencies FTF-R001/FTF-H003/FTF-H006 confirmed
  genuinely `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- No blocking defect found. Merged PR #236 (merge commit
  `d257254c78678e978533190b327d4d8758fb3914`) into `main`.
  `docs/spec-matrix.yaml`'s FTF-R002 entry set to `certification_state:
  CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to that
  merge commit; `proving_symbol` stays `build_component_binding_router`
  (real live caller, confirmed above). Ran
  `scripts.task_claim.sync_certified_status()` — `['FTF-R002']` updated.
  Certified count moves 58/300 → 59/300. Product-true: 19/19 → 19/19
  (unchanged — the live caller was already wired and counted at PR #236's
  own merge, not newly added by certification).

**Conclusion:** Certified L2-CERTIFIED.

## FTF-04A06 — Local repository connector with root allowlist/path protections
- Criterion ID: FTF-04A06
- PR: https://github.com/SJZLab/Sovra/pull/238 (`claude/wizardly-fermat-azo96v`
  → `main`). Merged by this review as merge commit
  `249ff0663df74fe25f33f35673a319ea6d728b90`.
- Merge commit (evidence): 249ff0663df74fe25f33f35673a319ea6d728b90
- Implementation commit: 38ea4e450fcbf9e2f65032e78490bf632d30adac (Law 1
  fix commit: 911ae789cbc2f3f9b1c9977c087c9911d8cce7aa)
- Validated-by: session https://claude.ai/code/session_01TSZ3SyJvmLrNbuDqXVYRtE
  (scheduled Control-plane certifier shift, `pull_request.opened` webhook
  for PR #240 — this review also picked up PR #238, the other item
  awaiting L2 certification per `QUEUE.md`'s "## Provisional" section)
- Date: 2026-09-24
- **Session-ID check:** all seven commits on PR #238 carry
  `Claude-Session: https://claude.ai/code/session_0159tZvUhDCyk3rdhW7fMscf`
  (the merge commit `c122d5d` has no trailer, expected for a plain `git
  merge` commit) — no match to this reviewing session's own ID
  (`session_01TSZ3SyJvmLrNbuDqXVYRtE`), independent per Law 4.
- **Diff read directly**: 8 files changed (+2535/−27) — the new
  `apps/api/app/target_connectors/local_repository_connector.py` (431
  lines, read in full), the new `tests/tasks/test_ftf_04a06.py` (463
  lines, read in full), the new task packet
  `docs/implementation/task-packets/FTF-04A06.md`, a 2-line
  `.github/workflows/ci.yml` gate-step addition, and the expected
  PROGRESS.md/QUEUE.md/spec-matrix.yaml/certifications-queue.md
  tracking-doc updates — no unexplained scope creep. The packet's own
  generic suggested path
  (`packages/target-connectors/local_repository_connector_with_root_allowlist_path_protections.py`)
  was correctly refined to match FTF-04A01's/FTF-04A07's real
  `apps/api/app/target_connectors/*.py` layout precedent, disclosed in
  the module's own docstring.
- **Prior review history on this same PR:** a 2026-09-24 Control-plane
  review (different session) found the shipped `resolve_allowed_root`
  sound but flagged a real Law 1 evidence gap (no test for a
  prefix-colliding sibling allowlist pair, e.g. `allowed` vs
  `allowed-evil`) and left it **NOT CERTIFIED**. The producer pushed
  commit `911ae78` adding
  `test_resolve_allowed_root_refuses_a_prefix_colliding_sibling`,
  resolved a tracking-doc merge conflict against PR #239 (`c122d5d`), and
  re-ran CI fully green. This review independently verified that fix
  rather than trusting the branch's own account of it (below).
- **CI independently re-checked on the real final head, not taken on
  faith:** a fresh `pull_request_read get_check_runs` call on head
  `693f61949d6c2a8a8fc3bd21e8c9caf3f7cfa5de` confirmed all 29 checks,
  including `gate`, `status: completed` / `conclusion: success` — nothing
  pending or skipped. Pulled the `gate` job's own step-level log text
  directly (`get_job_logs`, job id `107615677654`): the step "FTF-04A06
  negative-test proof (Local repository connector, no DB required)" ran
  `uv run python3 tests/tasks/test_ftf_04a06.py` and printed "FTF-04A06
  local repository connector tests: all checks passed" — genuine
  execution in CI, not a self-reported claim.
- **Own local reproduction:** checked out the PR head locally and ran
  `python3 tests/tasks/test_ftf_04a06.py` directly — all 13 checks
  passed.
- **Own fault injection (Law 1), two vectors distinct from the
  producer's own two (symlink-escape guard, concurrent-drift-on-dirty-
  path guard) and distinct from the prior reviewer's own (missing
  prefix-collision test):**
  1. Replaced `resolve_allowed_root`'s real `Path.parents` structural
     check with a naive `str(real_root).startswith(str(real_allowed))`
     comparison — the new `test_resolve_allowed_root_refuses_a_prefix_
     colliding_sibling` check genuinely failed for real (1 FAIL) against
     the exact string-prefix defect its own docstring disclaims.
     Reverted; `diff` confirmed byte-identical; suite re-ran clean.
  2. Disabled the dirty-tree branch's own after-capture
     `ConcurrentModificationError` raise
     (`capture_local_repository`'s second `if dirty_after != dirty_before
     or head_after != commit_sha` guard, replaced with `if False`) — the
     dedicated `test_concurrent_drift_during_capture_is_refused` check
     genuinely failed for real (1 FAIL). Reverted; `diff` confirmed
     byte-identical; suite re-ran clean immediately after ("all checks
     passed").
- **`product_truth_check.py --fail-on-unwired` confirmed 19/19
  unchanged**, and confirmed this is a legitimate 0/0-class result, not
  an evasion: this task's `proving_symbol` in `docs/spec-matrix.yaml` is
  the empty string (a pure, DB-free capture module with no live HTTP
  caller yet, disclosed in both the module's own docstring and the PR
  body — the same accepted scope-narrowing precedent as
  FTF-1302/FTF-0005/FTF-0006/FTF-0102–0105/`storage_semantics_client.py`
  before each one's own first live caller existed). Read
  `scripts/product_truth_check.py`'s own logic directly: an entry with no
  `proving_symbol` is skipped entirely, never counted as a 0/0 pass
  against a claimed symbol — Law 5 is not being evaded here.
- **Task contract pulled directly** via `python3 tools/r1_handoff.py
  packet FTF-04A06 --plan FINE_TUNING_FACTORY_RELEASE_1.md`:
  `acceptance` ("Read approved local root, handle detached/dirty worktree
  and create immutable content manifest" — proven by
  `test_capture_clean_git_checkout_reports_real_commit_and_tree`/
  `test_capture_detached_head_reports_none_branch`/
  `test_capture_dirty_tree_is_deterministic_and_content_hashed`;
  "Independent review, exact merged commit, real relevant integration and
  negative-test evidence recorded" — this review) and
  `negative_acceptance` ("Path/symlink traversal and concurrent file
  drift fail; never edit the target working tree" — proven by the
  prefix-collision, symlinked-root, symlink-escape-inside-repository, and
  concurrent-drift tests, plus the dedicated before/after `git status`
  byte-identical assertion in
  `test_symlink_escape_inside_repository_is_refused_and_never_edits_the_tree`)
  are both genuinely met by real, independently-reproduced evidence, not
  merely "tests pass". `dependencies` FTF-04A01/FTF-04A07 confirmed
  already `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- No blocking defect found. Merged PR #238 (merge commit
  `249ff0663df74fe25f33f35673a319ea6d728b90`) into `main`.
  `docs/spec-matrix.yaml`'s FTF-04A06 entry set to `certification_state:
  CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to that
  merge commit; `proving_symbol` stays `''` (no live caller, confirmed
  legitimate above). Certified count moves 59/300 → 60/300. Product-true:
  19/19 → 19/19 (unchanged — this task adds no live caller).

**Conclusion:** Certified L2-CERTIFIED.

## FTF-1303 — Provider token costing
- Criterion ID: FTF-1303
- PR: https://github.com/SJZLab/Sovra/pull/245 (`claude/wizardly-fermat-8lsly3`
  → `main`, supersedes PR #240 `claude/wizardly-fermat-vwrwp6`, closed as
  superseded). Merged by this review as merge commit
  `f1e545745f7679ed19628d66ffc84955329a6f50`.
- Merge commit (evidence): f1e545745f7679ed19628d66ffc84955329a6f50
- Implementation commit: be9775f60ce248cd411cc22ccf82692415798c82
  (byte-identical on both PR #240 and PR #245 — confirmed by the PR #245
  author's own disclosure and independently by this review's own diff read)
- Validated-by: session https://claude.ai/code/session_017qNX6TzAVGoA2bi9CXwvix
  (Control-plane certifier shift, triggered by the `pull_request.opened`
  webhook for PR #245), 2026-09-24
- Date: 2026-09-24
- **Session-ID check:** commits on PR #245 (and the PR #240 history it
  carries forward) show `Claude-Session` trailers
  `session_01CuuVLc7aGhNUrpFbQmrpVk` (producer, implementation),
  `session_01TSZ3SyJvmLrNbuDqXVYRtE` (prior reviewer — found the PR #240
  submission sound but blocked only by a tracking-doc merge conflict, left
  PROVISIONAL, never certified it), and `session_017pYedfdGZ2ZUSt4PKRdqAn`
  (merge-conflict resolution that produced PR #245) — none match this
  reviewing session's own ID, independent per Law 4.
- **Diff read directly:** `apps/api/app/services/provider_token_costing.py`
  (262 lines) and `tests/tasks/test_ftf_1303.py` read in full — confirmed
  byte-identical to PR #240's own diff (the only production-code PR this
  task ever shipped); PR #245's own additional commits are a tracking-doc
  merge-conflict resolution only (`git diff --stat` between the two heads
  under review, `0ef89a0`→`a6e2742`, touched only
  `PROGRESS.md`/`QUEUE.md`/`docs/certifications/queue.md`, no code). The
  module implements `provider_call_cost(usage, rates)` -- Section 32's own
  two named `input_tokens`/`output_tokens` terms always billed via
  required rate fields, plus `cache_tokens`/`reasoning_tokens`/
  `billable_requests` each an independent `(usage, rate)` pair that is
  `None` when not applicable to a call, distinct from a declared `0`
  (real, billable-but-zero). `_priced_category` is the one function
  enforcing the negative_acceptance clause.
- **Task contract pulled directly** via `python3 tools/r1_handoff.py
  packet FTF-1303 --plan FINE_TUNING_FACTORY_RELEASE_1.md`: `acceptance`
  ("Calculate billed input/output/cache/reasoning/request charges using
  actual provider usage rules" — proven by the five-category golden
  example, $23.50, covering all named categories at once; "Independent
  review, exact merged commit, real relevant integration and negative-test
  evidence recorded" — this review) and `negative_acceptance` ("Missing
  usage/rate/unknown upstream cannot be replaced by invented or zero
  dollar value" — proven below by this review's own fault injection, plus
  the shipped suite's own three independent cache/reasoning/
  billable_requests cases and the declared-zero-vs-`None` equivalence
  case) are both genuinely met by real, independently-reproduced evidence,
  not merely "tests pass". `dependencies` FTF-0210/FTF-1301 confirmed
  already `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them;
  `contract_owner` FTF-1301's closed `RATE_UNITS` vocabulary (read
  directly in `apps/api/app/costs/cost_profile_schema.py`) confirmed to
  have no `per_cache_token`/`per_reasoning_token`/per-request member,
  supporting the module's own disclosed reason for taking an
  already-declared `ProviderRateCard` rather than querying `CostProfile`
  itself.
- **Own local reproduction:** checked out the PR head locally and ran
  `python3 tests/tasks/test_ftf_1303.py` directly — all 13 checks passed.
- **Own fault injection (Law 1), distinct from the producer's own vector
  (silent zero-fill) and from the FTF-04A06 reviewer precedent's own
  vectors:** patched `_priced_category` in
  `apps/api/app/services/provider_token_costing.py` so a missing rate
  paired with real, nonzero, declared usage invents a fallback rate
  (`Decimal("0.01")`) instead of raising — the "invented ... dollar value"
  failure mode this task's own negative_acceptance names directly (as
  opposed to the "zero dollar value" half other fault injections on this
  task already covered). Ran the real suite against the faulted code: 4
  genuine `FAIL [FTF-1303]` lines (the independent cache/reasoning/
  billable_requests negative_acceptance tests, plus the dedicated
  fault-injection test's own first assertion, which expects the real code
  to still refuse). Reverted the production file byte-identical (`diff`
  against the pre-fault copy empty) and re-ran the real suite immediately
  after: all 13 checks passed again.
- **`product_truth_check.py --fail-on-unwired` independently re-run:**
  19/19 — confirmed legitimate by reading the script's own skip logic
  directly: `proving_symbol` is deliberately empty for this task (a pure,
  DB-free computation module with no live HTTP route or caller in this
  repository yet, `api_contract`/`shared_lock_required` both pointing at
  FTF-1301's own already-frozen contract slot), the same disclosed 0/0
  precedent as FTF-1302/FTF-1401/FTF-1601/FTF-1701/FTF-1810 before their
  own first live caller existed — an entry with no `proving_symbol` is
  skipped entirely, never counted as a false 0/0 pass against a claimed
  symbol.
- **CI independently re-checked on the real final PR head, not taken on
  faith:** this PR's head moved twice while under review this shift
  (`0ef89a0` → `a6e2742`, confirmed via `git diff --stat` to be a
  tracking-doc-only commit recording the PR #245 link, no code). Waited
  for and confirmed a fresh, real GitHub Actions run on the actual final
  head `a6e27422c082ef0c6f32ecf442e408842ecc63f2` (workflow run
  `36016262466`): all 29 jobs including `gate` reported
  `status: completed` / `conclusion: success`, `mergeable_state: clean` —
  nothing pending, skipped, or stale. Pulled the `gate` job's own
  step-level log text directly (`get_job_logs`, job id `107689464196`):
  the step "Run uv run python3 tests/tasks/test_ftf_1303.py" genuinely
  executed and printed "FTF-1303 Provider token costing tests: all checks
  passed" in CI — real execution, not a self-reported claim.
- No blocking defect found. Merged PR #245 (merge commit
  `f1e545745f7679ed19628d66ffc84955329a6f50`) into `main`; PR #240 was
  already closed as superseded by its own author session before this
  review began. `docs/spec-matrix.yaml`'s FTF-1303 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
  `evidence_hash` set to the merge commit; `proving_symbol` stays `''`
  (no live caller, confirmed legitimate above). `sync_certified_status()`
  updated `spec/tasks.json` and `docs/implementation/TRACKER.json`.
  Certified count moves 60/300 → 61/300. Product-true: 19/19 → 19/19
  (unchanged — this task adds no live caller).

**Conclusion:** Certified L2-CERTIFIED.

## FTF-1304 — Cost-per-success calculator
- Criterion ID: FTF-1304
- PR: https://github.com/SJZLab/Sovra/pull/249 (`claude/wizardly-fermat-fkyn9x`
  → `main`). Merged by this review as merge commit
  `4b432a44d4d6ef511796bce5f30faec4d948b831`.
- Merge commit (evidence): 4b432a44d4d6ef511796bce5f30faec4d948b831
- Implementation commit: 0e0390aa5c7a75be1bd04c805733dacd4f2350b6
- Validated-by: session https://claude.ai/code/session_016j3oFawdjCmJveQAiaAyDy
  (Control-plane certifier shift, triggered by the `pull_request.opened`
  webhook for PR #249), 2026-09-24
- Date: 2026-09-24
- **Session-ID check:** all three commits on PR #249 carry `Claude-Session`
  trailer `session_017Uiok74naZssjNvuuhXncs` (producer) — no match against
  this reviewing session's own ID, independent per Law 4.
- **Diff read directly:** `apps/api/app/services/cost_per_success_calculator.py`
  (271 lines) and `tests/tasks/test_ftf_1304.py` (382 lines) read in full.
  This PR's head moved once while under review (`bbc9175` → `f7dd5d3`),
  confirmed via `git diff --stat` between the two heads that the new
  commit was tracking-doc-only (`PROGRESS.md`/`QUEUE.md`/
  `docs/certifications/queue.md`, adding the previously-missing queue
  append) with zero changes under `apps/api/` or `tests/` — the code
  already reviewed stayed the code that shipped.
- **CI independently watched to completion on the real final head, not
  taken on faith:** GitHub Actions run `36030893786` on head
  `f7dd5d3bd1a0cad69bc0d6686b09d25dcd9f6d02` polled to completion — all 29
  checks, `gate` included, `status: completed` / `conclusion: success`,
  `mergeable_state: clean`. Pulled the `gate` job's own real log text
  directly (`get_job_logs`, job id `107739065356`): the step "Run uv run
  python3 tests/tasks/test_ftf_1304.py" genuinely executed and printed
  "FTF-1304 Cost-per-success calculator tests: all checks passed" — real
  execution, not a self-reported claim.
- **Own local reproduction:** ran `python3 tests/tasks/test_ftf_1304.py`
  directly against the checked-out head — all 20 checks passed. `ruff
  check`/`ruff format --check` clean; `uv run mypy
  app/services/cost_per_success_calculator.py` clean.
- **Own fault injection (Law 1), distinct from the producer's own vector
  (dropping `pending_cost`/`fallback_cost` from `total_cost()`):** patched
  `AttemptCost.__post_init__` so `fallback_used=True` with no matching
  `fallback_cost` silently defaults `fallback_cost` to `Decimal("0")`
  instead of raising — the "invented or zero dollar value" half of this
  task's own negative_acceptance clause, applied to the fallback/judge/
  tool category specifically. Ran the real suite against the faulted
  code: 1 genuine `FAIL [FTF-1304]` line
  (`test_rejects_fallback_used_without_fallback_cost`). Separately
  confirmed by direct reproduction that the faulted code prices a
  declared, unobserved fallback usage at exactly $0 instead of refusing
  (`cost_per_success` returned `10.00` instead of raising, for an attempt
  with `settled_cost=10.00` and `fallback_used=True`/`fallback_cost=None`)
  — the concrete misleading-cheap-metric failure this task's own
  negative_acceptance forbids. Reverted byte-identical (`diff` against
  the pre-fault copy empty); re-ran the real suite immediately after —
  all 20 checks passed again.
- **`product_truth_check.py --fail-on-unwired` confirmed `19/19`
  unchanged, and confirmed legitimate rather than an evasion:** this
  task's `proving_symbol` is the empty string — a pure, DB-free
  computation module with no live HTTP route or caller in this repository
  yet (`api_contract`/`shared_lock_required` both point at FTF-1301's own
  already-frozen contract slot), the same accepted scope-narrowing
  precedent as FTF-1302/FTF-1303/FTF-1401/FTF-1601/FTF-1701/FTF-1810
  before each one's own first live caller existed. Read
  `scripts/product_truth_check.py`'s own logic directly: an entry with no
  `proving_symbol` is skipped entirely, never counted as a false 0/0 pass
  against a claimed symbol.
- **Task contract pulled directly** via `python3 tools/r1_handoff.py
  packet FTF-1304 --plan FINE_TUNING_FACTORY_RELEASE_1.md`: `acceptance`
  ("Total costs across all attempts divided by independently successful
  tasks, plus optional training amortization" — proven by the five-attempt
  golden example, $9.50, plus the amortization golden example, $12.00;
  "Independent review, exact merged commit, real relevant integration and
  negative-test evidence recorded" — this review) and `negative_acceptance`
  ("Zero successes, pending charges and unobserved fallback use cannot
  yield misleading cheap passing metric" — proven by the shipped suite's
  own zero-successes/pending/fallback cases plus this review's own
  fault injection above) both genuinely met, not merely "tests pass".
  `dependencies` FTF-1301/FTF-1302/FTF-1303 confirmed already `CERTIFIED`
  in `docs/spec-matrix.yaml` before relying on them.
- `validate_spec.py --strict` clean on this diff (after `git fetch
  --unshallow origin`, this sandbox's own pre-existing shallow-clone
  artifact).
- No blocking defect found. Merged PR #249 (merge commit
  `4b432a44d4d6ef511796bce5f30faec4d948b831`). `docs/spec-matrix.yaml`'s
  FTF-1304 entry set to `certification_state: CERTIFIED` / `proof_state:
  L2-CERTIFIED`, `evidence_hash` set to the merge commit; `proving_symbol`
  stays `''` (no live caller, confirmed legitimate above).
  `sync_certified_status()` updated `spec/tasks.json` and
  `docs/implementation/TRACKER.json` — `['FTF-1304']`. Certified count
  moves 61/300 → 62/300. Product-true: 19/19 → 19/19 (unchanged — this
  task adds no live caller).

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0507 — Tool-trace normalizer
- Criterion ID: FTF-0507
- PR: https://github.com/SJZLab/Sovra/pull/253 (`claude/wizardly-fermat-svcgeh`
  → `main`). Merged by this review as merge commit
  `c7d33a3ac5a25bd9237422b8dd16d36c27a62fb4`.
- Merge commit (evidence): c7d33a3ac5a25bd9237422b8dd16d36c27a62fb4
- Implementation commit: 83c865e8163387e4d016a60f544405fe345e6d11
- Validated-by: session https://claude.ai/code/session_01DfzHGRTG4C3bo11N4fu9N4
  (Control-plane certifier shift, triggered by the `pull_request.opened`
  webhook for PR #253), 2026-09-24
- Date: 2026-09-24
- **Session-ID check:** both commits on PR #253 carry `Claude-Session`
  trailer `session_01NCb1NKVWuvGDb5GfFvXxMh` (producer) — no match against
  this reviewing session's own ID (`session_01DfzHGRTG4C3bo11N4fu9N4`),
  independent per Law 4.
- **Diff read directly:** `workers/data/tool_trace_normalizer.py` (268
  lines) and `tests/tasks/test_ftf_0507.py` (18 test functions) read in
  full. This PR's head moved once while under review (`83c865e` →
  `69a5497`), confirmed via `git diff --stat` between the two heads that
  the new commit was tracking-doc-only (`PROGRESS.md`/`QUEUE.md`/
  `docs/certifications/queue.md`, adding the previously-missing queue
  append), zero production/test changes — the code already read stayed
  the code that shipped. Confirmed `normalize_tool_trace` genuinely
  composes FTF-0506's already-CERTIFIED `normalize_conversation` rather
  than reimplementing any of its guards; confirmed dependencies
  FTF-0500/FTF-0506 both `CERTIFIED` in `docs/spec-matrix.yaml` directly
  before relying on them.
- **Own local reproduction:** ran `python3 tests/tasks/test_ftf_0507.py`
  directly against the checked-out head — all 18 checks passed. `ruff
  check`/`ruff format --check` clean; `uv run mypy
  workers/data/tool_trace_normalizer.py` clean; `python3
  scripts/validate_spec.py --strict` clean.
- **CI independently watched to completion on the real final head, not
  taken on faith:** GitHub Actions run `36045751066` on head
  `69a5497266187cf5fba252c8121cea08fbd058f7` polled to completion — all 29
  checks, `gate` included, `status: completed` / `conclusion: success`,
  `mergeable_state: clean`. Pulled the `gate` job's own step-level log
  text directly (`get_job_logs`, job id `107788753568`): the step "Run uv
  run python3 tests/tasks/test_ftf_0507.py" genuinely executed and
  printed "FTF-0507 tool-trace normalizer: all checks passed" — real
  execution, not a self-reported claim.
- **Own fault injection (Law 1), distinct from the producer's own two
  vectors** (mixed-session guard, authorized-bool guard): disabled the
  trace-level duplicate tool-call-id guard across the whole batch (the
  `if call_id in authorizations: raise ...` check in
  `normalize_tool_trace`), so a repeated `id` on two different assistant
  tool calls within one trace was silently accepted instead of refused.
  Ran the real suite against the faulted code: 1 genuine `FAIL
  [FTF-0507]` line (`test_rejects_duplicate_tool_call_id_across_trace`).
  Reverted byte-identical (`diff` against the pre-fault copy empty,
  confirmed via `git diff --stat` showing no changes); re-ran the real
  suite immediately after — all 18 checks passed again.
- **`product_truth_check.py --fail-on-unwired` confirmed `19/19`
  unchanged, and confirmed legitimate rather than an evasion:** this
  task's `proving_symbol` is the empty string — no trace-source importer
  exists in this repository yet to call `normalize_tool_trace` (Section
  67.1's actual REST/trace connector is a separate, later,
  dependency-gated task), the same accepted scope-narrowing precedent as
  FTF-0506/FTF-0202/FTF-0205/FTF-0207/FTF-1302/FTF-1303/FTF-1304 before
  each one's own first live caller existed. Read
  `scripts/product_truth_check.py`'s own logic directly: an entry with no
  `proving_symbol` is skipped entirely, never counted as a false 0/0 pass
  against a claimed symbol.
- **Task contract pulled directly** via `python3 tools/r1_handoff.py
  packet FTF-0507 --plan FINE_TUNING_FACTORY_RELEASE_1.md`: `acceptance`
  ("Convert approved trace messages, tool IDs/results and outcomes
  preserving ordering and actual permissions" — proven by the shipped
  suite's out-of-order golden-trace and authorization-preservation cases;
  "Independent review, exact merged commit, real relevant integration and
  negative-test evidence recorded" — this review) and `negative_acceptance`
  ("Orphan/duplicate call IDs, malformed tool result and mixed-session
  traces cannot be accepted" — proven by the shipped suite's own cases
  plus this review's own fault injection above) both genuinely met, not
  merely "tests pass". `dependencies` FTF-0500/FTF-0506 confirmed already
  `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them.
- `validate_spec.py --strict` clean on this diff (ran after `git fetch
  --unshallow`, this sandbox's own pre-existing shallow-clone artifact).
- No blocking defect found. Merged PR #253 (merge commit
  `c7d33a3ac5a25bd9237422b8dd16d36c27a62fb4`). `docs/spec-matrix.yaml`'s
  FTF-0507 entry set to `certification_state: CERTIFIED` / `proof_state:
  L2-CERTIFIED`, `evidence_hash` set to the merge commit; `proving_symbol`
  stays `''` (no live caller, confirmed legitimate above).
  `sync_certified_status()` updated — `['FTF-0507']`. Certified count
  moves 62/300 → 63/300. Product-true: 19/19 → 19/19 (unchanged — this
  task adds no live caller).

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0508 — Record schema validator
- Criterion ID: FTF-0508
- PR: https://github.com/SJZLab/Sovra/pull/264 (`certify-ftf-0508-20260925`
  → `main`), a byte-identical re-submission of PR #257's own implementation
  commit onto current `main` (PR #257 was based on a stale parent, `39983ab`,
  3 commits behind `main` at review time, and reported real merge conflicts
  in the append-only tracking docs only, not the production code; closed as
  superseded). Merged by this review as merge commit
  `37c6abb19a6eff941273df6484162c2c1903410d`.
- Merge commit (evidence): 37c6abb19a6eff941273df6484162c2c1903410d
- Implementation commit: 4abc8b42d037e0487a18c0958600d21bb22eab35 (original,
  on PR #257; cherry-picked byte-identical as the sole commit on PR #264)
- Session-ID check: PR #257's producer session
  (`session_01CdCmbVsLB4MZNgVpyVMy52`) does not match this reviewing
  session (`session_01C1FfcUWN7E5S5aeiNFRmgb`) — independent (Law 4).
- Diff read directly: `workers/data/record_schema_validator.py` (378 lines)
  and `tests/tasks/test_ftf_0508.py` (358 lines) read in full.
  `validate_record_batch` is a pure, synchronous, no-I/O batch validator
  reusing FTF-0506's own already-CERTIFIED `SCHEMA_VERSION` constant and
  Section 6.8F's own frozen `DatasetSourceConnection.source_type` enum
  rather than reinventing either; it owns content-size bounding (Section
  67.3's 2MiB canonical single-record max), required-provenance-field
  presence, and required-quality-field enum validity for real, and returns
  a `RecordSchemaValidationReport` with per-row `batch_position`/
  `record_index` evidence instead of raising on the first violation (only a
  non-sequence/empty batch itself raises).
- Own local reproduction: `uv run python3 tests/tasks/test_ftf_0508.py` —
  20/20 checks pass. `ruff check`/`ruff format --check` clean; `uv run
  mypy workers/data/record_schema_validator.py` clean; `python3
  scripts/validate_spec.py --strict` clean; `python3
  scripts/product_truth_check.py --fail-on-unwired` unchanged at 19/19
  (`proving_symbol` genuinely empty — no dataset-import route exists yet
  to call this module, same disclosed "no live caller yet" precedent as
  FTF-0506/FTF-0507/FTF-1302/FTF-1303/FTF-1304).
- Dependencies FTF-0500/FTF-0506/FTF-0507 confirmed already `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- Own fault injection (Law 1), distinct from the producer's own two
  vectors (duplicate-`record_index` guard, `quality_status` enum guard):
  disabled the required-provenance-field loop (`for field in
  REQUIRED_PROVENANCE_FIELDS:` → `for field in []:`), so a candidate
  record missing both `record_sha256`/`normalized_sha256` was silently
  accepted instead of rejected. Ran the real suite against the faulted
  code: 1 genuine `FAIL [FTF-0508]` line
  (`test_rejects_missing_provenance_fields`). Reverted byte-identical
  (`diff` against the pre-fault copy empty); re-ran the real suite
  immediately after — all 20 checks passed again.
- Task contract pulled directly via `python3 tools/r1_handoff.py packet
  FTF-0508 --plan FINE_TUNING_FACTORY_RELEASE_1.md`: `acceptance`
  ("Validate schema/dialect/content size and required provenance/quality
  fields per canonical version" — proven by the five real per-record
  checks above; "Independent review, exact merged commit, real relevant
  integration and negative-test evidence recorded" — this review, plus
  PR #264's own real CI run) and `negative_acceptance` ("Unknown content
  or schema version and invalid sequence produces blocking issues with row
  evidence" — proven by the shipped suite's own unknown-content/
  unknown-schema-version/invalid-sequence cases, each carrying real row
  evidence) both genuinely met, not merely "tests pass". `shared_lock_
  required` confirmed empty in the same packet.
- CI independently watched to completion on PR #264's real head
  (`38f6a5b`, the cherry-picked commit): GitHub Actions run `36089980073`
  polled to completion — 29/29 checks, `gate` included,
  `status: completed` / `conclusion: success`.
- No blocking defect found. Merged PR #264 (merge commit
  `37c6abb19a6eff941273df6484162c2c1903410d`). `docs/spec-matrix.yaml`'s
  FTF-0508 entry set to `certification_state: CERTIFIED` / `proof_state:
  L2-CERTIFIED`, `evidence_hash` set to the merge commit; `proving_symbol`
  stays `''` (no live caller, confirmed legitimate above).
  `sync_certified_status()` updated — `['FTF-0508']`. Certified count
  moves 63/300 → 64/300. Product-true: 19/19 → 19/19 (unchanged — this
  task adds no live caller).
- Validated-by: session https://claude.ai/code/session_01C1FfcUWN7E5S5aeiNFRmgb
  (Control-plane certifier shift, triggered by the `pull_request.opened`
  webhook for PR #262 (FTF-1506); this item was picked up in the same
  shift per the certifier's standing duty to check `docs/certifications/
  queue.md` for every pending item, not only the one that triggered the
  webhook), 2026-09-25
- Date: 2026-09-25

**Conclusion:** Certified L2-CERTIFIED.

## FTF-1506 — Dependency scanning
- Criterion ID: FTF-1506
- PR: https://github.com/SJZLab/Sovra/pull/268 (`claude/wizardly-fermat-btkdtw`
  → `main`), remediation of the real CI dependency-scan finding this
  session's own prior review (`docs/certifications/queue.md`'s 2026-09-25
  FTF-1506/PR #262 entry) left NOT CERTIFIED. Merged by this review as
  merge commit `c805cd03e1c3cb03dfeced0888c373e00a547859`.
- Merge commit (evidence): c805cd03e1c3cb03dfeced0888c373e00a547859
- Implementation commits: `91eb1788` (original module, producer session
  `session_01QLGmt5BoGWrutFsFSHjZGz`), `d4c7e630` (fastapi/authlib/starlette
  bump, producer session `session_01ENwUzgKyzCgRiwm6HLPaKm`), `3a601596`
  (docs-only tracking-record follow-up, same session as the bump).
- Session-ID check: neither producer session overlaps this reviewing
  session (`session_01VDSvao4KMAGwSqasd9Q9sN`) — independent (Law 4).
- Diff read directly against the PR's real final head (`3a60159`, not the
  webhook's stale `pull_request.opened` SHA `d4c7e630`, which by the time
  this review ran had already been superseded by the docs-only follow-up —
  confirmed via a fresh `pull_request_read get`): the code change is
  exactly `apps/api/pyproject.toml`'s `fastapi` constraint (`>=0.130.0,
  <0.142`) and `authlib` constraint (`>=1.8.0,<1.9`), the resulting
  `uv.lock` re-resolution (`starlette==1.7.0`, `fastapi==0.141.1`,
  `authlib==1.8.0`), and the new `dependency-scanning-integration` CI job
  plus its `gate`-job step — nothing else. `tests/security/
  dependency_scan_policy.yaml` confirmed still genuinely empty (comments +
  `exceptions: []`) — the version bump alone cleared the prior CI finding,
  no exception was added.
- Own local reproduction, in a separate git worktree checked out at the
  real final head (`3a60159`): `uv sync --all-packages` resolved the same
  versions CI resolved; `uv run python3 tests/tasks/test_ftf_1506.py` —
  19/19 pass; `ruff check .` / `ruff format --check .` clean; `uv run mypy
  tests/security/dependency_scanning.py` clean; `python3 scripts/
  validate_spec.py --strict` clean (after `git fetch --unshallow`);
  `python3 scripts/product_truth_check.py --fail-on-unwired` unchanged at
  19/19; `python3 scripts/status.py` unchanged at 64/300 (before this
  certification's own `sync_certified_status()` run).
- Dependencies FTF-0002/FTF-0004/FTF-1501 reconfirmed `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them.
- Own fault injection (Law 1), distinct from the producer's own two
  vectors (dropping `UNKNOWN` from `BLOCKING_SEVERITIES`; disabling the
  expired-exception check): in `evaluate()`, replaced `excepted = any(
  exc.covers(dep.ecosystem, dep.name, vuln_id) for exc in
  valid_exceptions)` with `excepted = True`, so every finding was treated
  as exception-covered regardless of the actual policy. Ran the real suite
  against the faulted code: 5 genuine `FAIL [FTF-1506]` lines (the
  unpatched-CRITICAL-blocks, UNKNOWN-severity-blocks-by-default,
  expired-exception-still-blocks, blank-justification-exception-still-
  blocks, and mismatched-exception-never-covers cases). Reverted
  byte-identical (`diff` against the pre-fault copy empty); re-ran the
  real suite immediately after — all 19 checks passed again.
- Task contract pulled directly via `python3 tools/r1_handoff.py packet
  FTF-1506 --plan FINE_TUNING_FACTORY_RELEASE_1.md`: `acceptance` ("Scan
  pinned runtime/development dependencies under recorded vulnerability/
  patch policy"; "Independent review, exact merged commit, real relevant
  integration and negative-test evidence recorded") and
  `negative_acceptance` ("Unknown/unpatched critical/high finding cannot
  be silently ignored to satisfy cost or schedule") both confirmed
  genuinely met by the shipped code and this review, not merely "tests
  pass". `shared_lock_required` confirmed empty in the same packet.
- Real CI watched to genuine completion on the PR's actual final head
  (`3a60159`, GitHub Actions run `36096759389`): 30/30 checks `success`,
  `gate` included. `gate`'s own job log pulled directly (`get_job_logs`,
  job `107950529121`) confirmed `tests/tasks/test_ftf_1506.py` genuinely
  ran and printed "all checks passed", immediately followed by real
  `product_truth_check.py`/`status.py` runs printing `19/19`/`64/300` in
  the same job. `dependency-scanning-integration`'s own job log pulled
  directly (job `107950529479`) showed the real, live OSV.dev call: `295`
  dependencies scanned, `0` findings, `0` blocking — genuine proof the
  version bump actually cleared the 16 findings the prior PR (#262) had
  for real, not a self-reported or stale claim.
- No blocking defect found. Merged PR #268 (merge commit
  `c805cd03e1c3cb03dfeced0888c373e00a547859`). Closed PR #262 (the
  original, CI-blocked submission this PR fixed) as superseded.
  `docs/spec-matrix.yaml`'s FTF-1506 entry set to `certification_state:
  CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the
  merge commit. Ran `sync_certified_status()` — `['FTF-1506']` updated.
  `scripts/status.py`: `64/300` → `65/300` certified. Product-true
  unchanged at `19/19` (no live caller added by this certification).
- Validated-by: session https://claude.ai/code/session_01VDSvao4KMAGwSqasd9Q9sN
  (independent — see session-ID check above), 2026-09-25
- Date: 2026-09-25

**Conclusion:** Certified L2-CERTIFIED.

## FTF-1503 — CSRF suite
- Criterion ID: FTF-1503
- PR: https://github.com/SJZLab/Sovra/pull/272 (`ftf-1503-cherry-pick` -> `main`), a
  cherry-pick of PR #258's own two production/test commits onto current `main` (PR #258
  had gone stale, base several commits behind `main`, with real conflicts in the
  append-only tracking docs and `.github/workflows/ci.yml`'s step ordering, not the
  production code; closed as superseded). Merged by this review as merge commit
  `62b2c4be710dc9d7534becdeb799e06902cd1b77`.
- Merge commit (evidence): 62b2c4be710dc9d7534becdeb799e06902cd1b77
- Implementation commits: `e6f96159` (CSRF suite, producer session
  `session_01VDvxngihngbBfK5xfR5kt4`), `0fd8fc09` (repo-wide mypy fix, same session).
- Session-ID check: producer session does not overlap this reviewing session
  (`session_01VDSvao4KMAGwSqasd9Q9sN`) -- independent (Law 4).
- Diff read directly against the real merged content: `apps/api/app/auth/
  csrf_protection.py` (`require_csrf_protection`, a FastAPI dependency enforcing Origin
  equality plus a constant-time `X-CSRF-Token` check via a pluggable, fail-closed-by-
  default resolver; `InMemoryCsrfSessionStore`), `tests/security/csrf_suite.py` (7 real
  scenarios against a real, locally-hosted uvicorn demonstration app), `tests/tasks/
  test_ftf_1503.py` (no-DB gate proof that also reruns the live-server suite internally)
  -- matching the claimed scope exactly. Zero certified production files touched
  (`oidc_login.py`/`rbac_middleware.py`/`project_routes.py`/`main.py` all untouched).
- Own local reproduction: `uv run python3 tests/tasks/test_ftf_1503.py` -- all checks
  pass (including the internal live-server suite rerun); `uv run python3 tests/security/
  csrf_suite.py` -- 7/7 directly; `uv run ruff check .` / `uv run ruff format --check .`
  -- clean repo-wide; `uv run mypy .` -- clean repo-wide (240 source files, confirming
  the producer's own repo-wide mypy fix genuinely resolved the real CI failure);
  `python3 scripts/validate_spec.py --strict` -- clean; `python3 scripts/
  product_truth_check.py --fail-on-unwired` -- unchanged at 19/19.
- Dependencies FTF-0101/FTF-0401/FTF-1501 reconfirmed `CERTIFIED` in `docs/spec-
  matrix.yaml` before relying on them.
- Own fault injection (Law 1), distinct from the producer's own vector (disabled the
  `X-CSRF-Token` comparison): removed the Origin-equality check instead (the `origin !=
  allowed_origin` guard deleted). Ran the real suites against the faulted code: 1 genuine
  failure in the live 7-scenario suite (`foreign_origin_denied_even_with_real_token`) and
  2 genuine failures in the no-DB gate suite (missing-Origin-denied,
  foreign-Origin-denied-even-with-token). Reverted byte-identical (`diff` against the
  pre-fault copy empty); both suites re-ran clean immediately after.
- Task contract pulled directly via `python3 tools/r1_handoff.py packet FTF-1503 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: `acceptance` ("Real browser/session mutation
  requires anti-CSRF and correct origin policy" -- proven by the live demonstration app's
  real uvicorn server and real HTTP mutation attempts; "Independent review, exact merged
  commit, real relevant integration and negative-test evidence recorded" -- this review,
  plus PR #272's own real CI run) and `negative_acceptance` ("Cross-site forged
  form/fetch, missing token and unsafe CORS cannot mutate state" -- proven by the 7
  scenarios: missing-token, foreign-Origin, wrong-token, no-session, wrong-role and
  cross-session-token-reuse all denied) both genuinely met. `shared_lock_required`
  confirmed empty in the same packet.
- Real CI watched to genuine completion on PR #272's real head (`8514e18`): 30/30
  checks `success`, `gate` included. `gate`'s own job log pulled directly confirmed
  "FTF-1503 CSRF suite (no-DB) tests: all checks passed" genuinely printed by the real
  job (immediately after the already-certified FTF-1506 step), not a self-reported claim.
- No blocking defect found. Merged PR #272 (merge commit
  `62b2c4be710dc9d7534becdeb799e06902cd1b77`). Closed PR #258 as superseded, with an
  explanatory comment. `docs/spec-matrix.yaml`'s FTF-1503 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to
  the merge commit. Ran `sync_certified_status()` -- `['FTF-1503']` updated.
  `scripts/status.py`: `65/300` -> `66/300` certified. Product-true unchanged at `19/19`
  (no live caller added -- this task's own dependency is not yet wired into `main.py`,
  matching `require_role`'s own already-disclosed precedent).
- Validated-by: session https://claude.ai/code/session_01VDSvao4KMAGwSqasd9Q9sN
  (independent -- see session-ID check above), 2026-09-25
- Date: 2026-09-25

**Conclusion:** Certified L2-CERTIFIED.

## FTF-04A17 — DiscoveredAgentCandidate schema/API
- Criterion ID: FTF-04A17
- PR: https://github.com/SJZLab/Sovra/pull/278 (`claude/wizardly-fermat-66cdlv` -> `main`),
  the wiring-fix remediation this file's own preceding FTF-1503 entry's neighboring Law 9
  finding (`docs/certifications/queue.md`) specified for the next relay shift. Merged by
  this review as merge commit `fb8f626bfd7b9c4a38a19db7d824f51f28e7ffde`.
- Merge commit (evidence): fb8f626bfd7b9c4a38a19db7d824f51f28e7ffde
- Implementation commit: `2986e78533b05de4928eacb88e90c695854546de` (router-wiring fix +
  contract extension + new test, producer session `session_01YFs4HNmG9o3rdFB6JbFqe4`).
- Session-ID check: producer session does not overlap this reviewing session
  (`session_01DkbaexyroHG2XnmvqXTC7C`) -- independent (Law 4).
- Diff read directly against the real merged content: `apps/api/app/main.py` (the missing
  import + `app.include_router(build_discovered_agent_candidate_router(session_factory))`
  line, twelfth router, matching every sibling router's own convention exactly),
  `packages/contracts/openapi/04A-interface.yaml` (the real `/candidates` collection/item
  paths and their request/response schemas under their own `contracts:04A` lock),
  `tests/tasks/test_ftf_04a17.py` (new `test_router_is_wired_into_the_live_app`) -- matching
  the claimed scope exactly, zero scope creep, zero other production file touched.
- Own local reproduction, directly on the real PR head
  (`2986e78533b05de4928eacb88e90c695854546de`): `uv run ruff check .` / `uv run ruff format
  --check .` / `uv run mypy .` all clean repo-wide (246 source files); `uv run python3
  tests/tasks/test_ftf_04a17.py` -- 35/35; `uv run python3 scripts/product_truth_check.py
  --fail-on-unwired` -- 20/20, a real non-test text-search hit on `apps/api/app/main.py` for
  `build_discovered_agent_candidate_router` (not a vacuous 0/0 -- this criterion's
  `proving_symbol` is filled in, so 0/0 would not have been an acceptable pass).
- Own fault injection (Law 1), independently reproduced rather than trusting the producer's
  self-report: manually removed the import and `app.include_router(...)` line this PR added
  from `apps/api/app/main.py` and re-ran `tests/tasks/test_ftf_04a17.py` directly -- 1
  genuine `FAIL [FTF-04A17]` ("the real live app exposes the candidates collection route
  ..."), byte-identical to the PR's own disclosed before-fix failure. Restored the file from
  an untouched copy (`git status` confirmed clean, no stray diff) and re-ran -- all 35 checks
  pass again.
- Task contract re-pulled via `tools/r1_handoff.py packet FTF-04A17 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: acceptance clause 1 ("Store every candidate with
  traceable evidence and unresolved fields; expose paginated project-scoped API") is now
  genuinely met on the live path -- the route is reachable from `app.main.create_app()`, not
  merely defined; `negative_acceptance` ("Candidate priority/confidence is not calibrated
  probability or automatic in-scope selection") reconfirmed still held (unchanged
  schema/CRUD layer, restated explicitly in the new OpenAPI descriptions).
  `shared_lock_required` (`contracts:04A`, `migration:application`) respected -- only the
  named `contracts:04A` file touched, no competing migration added.
- Real CI watched to genuine completion on PR #278's real head
  (`2986e78533b05de4928eacb88e90c695854546de`): 31/31 checks `success`, `gate` and
  `discovered-agent-candidate-integration` both included -- confirmed via the GitHub
  check-runs API directly against the head SHA, not a self-reported claim.
- PR #276 (the earlier, incomplete self-certification this task's own Law 9 finding
  caught mid-flight) reconfirmed `state: closed`, `merged: false` -- the false-certification
  path never landed; `docs/spec-matrix.yaml`'s FTF-04A17 entry was never left `CERTIFIED`
  without this wiring fix.
- No blocking defect found. Merged PR #278 (merge commit
  `fb8f626bfd7b9c4a38a19db7d824f51f28e7ffde`). `docs/spec-matrix.yaml`'s FTF-04A17 entry set
  to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to
  the merge commit. Ran `sync_certified_status()` -- `['FTF-04A17']` updated.
  `scripts/status.py`: `66/300` -> `67/300` certified, product-true `19` -> `20` (the router
  is now a real, reachable live caller).
- Validated-by: session https://claude.ai/code/session_01DkbaexyroHG2XnmvqXTC7C
  (independent -- see session-ID check above), 2026-09-25
- Date: 2026-09-25

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0511 — Exact duplicate detector
- Criterion ID: FTF-0511
- PR: https://github.com/SJZLab/Sovra/pull/282 (`claude/wizardly-fermat-crhigs` -> `main`)
- Merge commit (evidence): 4602ba7d7b06063085d91d8782b33d946ef644d5
- Implementation commit: 5f2ee31ee698b035443e7811ed4de55ec305acd8 (single commit,
  producer session `session_019bmUswhdEXyBFy5F9JxppB`).
- Session-ID check: producer session does not overlap this reviewing session
  (`session_01PC5xR1bubjGorKuwzrtYXa`) -- independent (Law 4). Only one commit on the PR.
- Diff read directly: `workers/data/exact_duplicate_detector.py` (new,
  `detect_exact_duplicates` grouping raw/normalized hash duplicates per Section 10 methods
  1-2), `tests/tasks/test_ftf_0511.py` (new, 20 no-DB test functions),
  `.github/workflows/ci.yml` (+2 lines wiring the new test into the `gate` job),
  `docs/spec-matrix.yaml`/`docs/implementation/task-packets/FTF-0511.md`/tracking-doc
  updates. Scope matched the PR's own claims exactly -- no other production file touched.
- Real CI watched to genuine completion on PR #282's real head
  (`5f2ee31ee698b035443e7811ed4de55ec305acd8`): all 31 check runs `success`, including the
  `gate` job (run 36115955286, job 108010006784, completed 2026-09-25T09:03:14Z) and its own
  `FTF-0511 negative-test proof (Exact duplicate detector, no DB required)` step -- confirmed
  via the GitHub Actions/check-runs API directly against the head SHA, not a self-reported
  claim. Three integration jobs (`target-application-connection-integration`,
  `dataset-source-connection-integration`, `secret-manager-integration`) were still
  `in_progress` at first read; polled until each completed `success` before merging.
- Own fault injection (Law 1), independently reproduced rather than trusting the producer's
  self-report: forced `DuplicateGroup.is_blocking` to unconditionally `return False` in a
  local copy, re-ran `tests/tasks/test_ftf_0511.py` directly -- 2 genuine FAIL lines (the
  cross-purpose-raw-blocking and normalized-always-blocking checks), matching the PR's own
  disclosed before-fix failure exactly. Reverted from an untouched backup; `git status
  --short` confirmed clean; re-ran -- all checks pass again.
- `product_truth_check.py --fail-on-unwired`: 20/20, unchanged from pre-merge. This
  criterion's `proving_symbol` is blank (no dataset-import pipeline route exists yet in this
  repository to call `detect_exact_duplicates` -- same disclosed precedent as FTF-0506/
  FTF-0507/FTF-0508), so an unchanged 20/20 is a legitimate disclosed 0/0 for this task, not
  the "0/0 once proving_symbol is filled in" case the CERTIFY procedure warns against.
- Task contract re-pulled via `tools/r1_handoff.py packet FTF-0511 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: acceptance ("Raw hash duplicate groups remain
  deterministic and trace original record identity") verified by
  `test_groups_trace_original_record_identity`/`test_deterministic_group_ordering`;
  negative_acceptance ("Cross-purpose duplicate detection cannot be disabled to fill sample
  minimum; hash collisions/errors surfaced") verified by
  `test_cannot_disable_cross_purpose_detection` (real `inspect.signature` check, not prose)
  plus the missing/invalid-hash and invalid-purpose issue-surfacing tests. Dependencies
  FTF-0500/FTF-0508 reconfirmed already `CERTIFIED` before relying on their fields.
- `validate_spec.py --strict`: clean after `git fetch --unshallow` (this sandbox's own
  pre-existing shallow-clone artifact, same disclosed precedent as every prior shift) --
  confirmed independently, not accepted on the producer's word alone.
- No blocking defect found. Merged PR #282 (merge commit
  `4602ba7d7b06063085d91d8782b33d946ef644d5`). `docs/spec-matrix.yaml`'s FTF-0511 entry set
  to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to
  the merge commit. `scripts/status.py`: `67/300` -> `68/300` certified, product-true
  unchanged at `20` (no live caller yet, disclosed).
- Validated-by: session https://claude.ai/code/session_01PC5xR1bubjGorKuwzrtYXa
  (independent -- see session-ID check above), 2026-09-25
- Date: 2026-09-25

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0512 — Normalized duplicate detector
- Criterion ID: FTF-0512
- PR: https://github.com/SJZLab/Sovra/pull/284 (`claude/wizardly-fermat-8p4f3j` -> `main`)
- Merge commit (evidence): bdd9c0e0a07da1ca5b18306277c50f841acc8ee7
- Implementation commit: cb31ffd7a6717c4a5a2f9f3e068da28f81642c9d (single commit, producer
  session `session_01KURX1i2Vya6YPyZLXCAg7N`).
- Session-ID check: producer session does not overlap this reviewing session
  (`session_01HkpHGfs6aTsVNzWCRNoBNU`) -- independent (Law 4). Only one commit on the PR.
- Diff read directly: `workers/data/normalized_duplicate_detector.py` (new,
  `detect_normalized_duplicates`/`compute_normalized_text_hash` covering Section 10 method 3
  "text normalization match"), `tests/tasks/test_ftf_0512.py` (new, 20 no-DB test functions),
  `.github/workflows/ci.yml` (+2 lines wiring the new test into the `gate` job),
  `docs/spec-matrix.yaml`/`docs/implementation/task-packets/FTF-0512.md`/tracking-doc updates
  (`PROGRESS.md`/`QUEUE.md`/`docs/certifications/queue.md`). Scope matched the PR's own claims
  exactly -- no other production file touched.
- Own local reproduction: `python3 tests/tasks/test_ftf_0512.py` -- 20/20 pass.
- Own fault injection (Law 1), a different vector than the producer's own disclosed one
  (which dropped tool-call `arguments`): replaced a `json` content part's `value` with a
  fixed placeholder in a local copy of `_normalize_content_part`, re-ran the suite directly
  -- 1 genuine FAIL line
  (`test_different_json_content_value_is_not_a_duplicate`: two records differing only in a
  tool-result's shipped/cancelled status collapsed into one duplicate group). Reverted from
  an untouched backup; `diff` against the backup and `git status --short` both confirmed
  byte-identical; re-ran -- all checks pass again.
- `ruff check`/`ruff format --check`/`mypy` reconfirmed clean repo-wide (after `uv sync
  --all-packages`, this sandbox's own pre-existing freshness gap, same disclosed precedent as
  every prior shift). `validate_spec.py --strict` reconfirmed clean after `git fetch
  --unshallow` (this sandbox's own pre-existing shallow-clone artifact) -- confirmed
  independently, not accepted on the producer's word alone.
- `product_truth_check.py --fail-on-unwired`: 20/20, unchanged from pre-merge. This
  criterion's `proving_symbol` is blank (no dataset-import pipeline route exists yet in this
  repository to call `detect_normalized_duplicates` -- same disclosed precedent as
  FTF-0506/FTF-0507/FTF-0508/FTF-0511), confirmed a legitimate disclosed 0/0 (not the "0/0
  once proving_symbol is filled in" case the CERTIFY procedure warns against) by reading
  `scripts/product_truth_check.py`'s own `SOURCE_ROOTS` list directly: `workers/data` is a
  real, listed root.
- Task contract re-pulled via `tools/r1_handoff.py packet FTF-0512 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: acceptance ("Canonicalized hash matching preserves
  meaningful tool/ID semantics while detecting formatting duplicates") verified by reading
  the case/whitespace/punctuation/generated-UUID/generated-timestamp duplicate tests and
  `test_tool_call_id_name_preserved_verbatim_still_matches_when_identical` directly;
  negative_acceptance ("Normalization cannot collapse distinct customer outcomes or strip
  relevant security constraints") verified by
  `test_different_tool_call_arguments_is_not_a_duplicate`,
  `test_different_json_content_value_is_not_a_duplicate`,
  `test_different_security_word_is_not_a_duplicate`, and
  `test_different_business_id_in_text_is_not_a_duplicate`. Dependencies FTF-0500/FTF-0511
  reconfirmed already `CERTIFIED` in `docs/spec-matrix.yaml` before relying on their
  provenance-field contract.
- Real CI watched to genuine completion on PR #284's actual head
  (`cb31ffd7a6717c4a5a2f9f3e068da28f81642c9d`, run `36126898147`): polled the GitHub Actions
  check-runs/jobs API directly against the head SHA rather than accepting a single green
  summary -- all 31/31 check runs completed `success`, including `gate` (job `108044991017`,
  completed `2026-09-25T11:01:46Z`) and its own new `FTF-0512 negative-test proof (Normalized
  duplicate detector, no DB required)` step. Three integration jobs unrelated to this task
  (`secret-manager-integration`, `dataset-source-connection-integration`,
  `target-application-connection-integration`) were still `in_progress` at first read, each
  building the same pinned OpenBao Go binary from source (mirroring the identical pattern
  already disclosed in the FTF-0511 ledger entry above); polled each job's own step detail
  directly to confirm genuine, still-advancing build progress (not a hung runner) before
  continuing to wait, then polled each job individually until `completed`/`success` --
  matching the ~7-8 minute baseline observed on the immediately preceding PR #282/FTF-0511
  run for these same jobs -- before merging.
- No blocking defect found. Merged PR #284 (merge commit
  `bdd9c0e0a07da1ca5b18306277c50f841acc8ee7`). `docs/spec-matrix.yaml`'s FTF-0512 entry set
  to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to
  the merge commit. `sync_certified_status()` run (updated `spec/tasks.json`/
  `docs/implementation/TRACKER.json` for FTF-0512). `scripts/status.py`: `68/300` ->
  `69/300` certified, product-true unchanged at `20` (no live caller yet, disclosed).
- Validated-by: session https://claude.ai/code/session_01HkpHGfs6aTsVNzWCRNoBNU
  (independent -- see session-ID check above), 2026-09-25
- Date: 2026-09-25

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0514 — PII/secrets detector
- Criterion ID: FTF-0514
- PR: https://github.com/SJZLab/Sovra/pull/286 (`claude/wizardly-fermat-w9he1w` -> `main`)
- Merge commit (evidence): 1f859be5e520e114eeff08222230337a8a9acd0b
- Implementation commits: `5d0c1dd6f5d16dfe9981686ba7ae55b7fddcbefe` (implementation, producer
  session `session_015bL6zptzbAoWaYKBhFKooj`) plus a disclosed tracking-only follow-up
  `657e8d7162a31579520c70a4c70055ea01f578e5` (same session) pushed to the PR mid-review,
  touching only `PROGRESS.md`/`QUEUE.md`/`docs/certifications/queue.md` -- reconfirmed by
  diffing the two commits directly (no code or test file touched by the second commit).
- Session-ID check: producer session does not overlap this reviewing session
  (`session_015egHQyUmbCT14fMFfJCU2p`) -- independent (Law 4).
- Diff read directly: `workers/data/pii_secrets_detector.py` (new, `detect_pii_and_secrets`
  covering Section 8.1 step 14 "Run PII/secrets policy"), `tests/tasks/test_ftf_0514.py` (new,
  20 no-DB test functions), `.github/workflows/ci.yml` (+2 lines wiring the new test into the
  `gate` job), `docs/implementation/task-packets/FTF-0514.md` (new),
  `docs/spec-matrix.yaml`/tracking-doc updates (`PROGRESS.md`/`QUEUE.md`/
  `docs/certifications/queue.md`). Scope matched the PR's own claims exactly -- no other
  production file touched.
- Own local reproduction: `python3 tests/tasks/test_ftf_0514.py` -- 20/20 pass, "all checks
  passed".
- `python3 scripts/product_truth_check.py --fail-on-unwired` -- 20/20, unchanged. This task's
  `proving_symbol` is blank (no dataset-import pipeline route exists yet in this repository to
  call `detect_pii_and_secrets`, same disclosed precedent as FTF-0506/FTF-0507/FTF-0508/
  FTF-0511/FTF-0512) -- confirmed by reading `scripts/product_truth_check.py`'s own
  `SOURCE_ROOTS` list directly (`workers/data` is a real, listed root). Legitimate disclosed
  0/0, not the "0/0 once proving_symbol is filled in" case this procedure's own step 4 warns
  against.
- `python3 scripts/validate_spec.py --strict` -- clean after `git fetch --unshallow` (this
  sandbox's own pre-existing shallow-clone artifact, same disclosed precedent as every prior
  shift).
- `ruff check`/`ruff format --check`/`mypy` clean repo-wide (`uv sync --all-packages` needed
  first for `mypy` to resolve `uvicorn`/`alembic`, same disclosed precedent). `status.py`
  unchanged at `69/300` certified pre-certification (Law 2). Regression swept
  `test_ftf_0402.py`, `test_ftf_0500.py` (via `uv run`, needed for `uvicorn`), `test_ftf_0508.py`,
  `test_ftf_0511.py`, `test_ftf_0512.py` -- zero regressions.
- `python3 tools/r1_handoff.py packet FTF-0514 --plan FINE_TUNING_FACTORY_RELEASE_1.md` --
  pulled the real task contract directly rather than trusting the PR's restatement of it.
  `acceptance`: "Run secrets/PII detectors inside private boundary and quarantine required
  findings with human resolution" -- verified met by reading
  `test_finding_marks_requires_quarantine_but_status_stays_detected_not_quarantined`,
  `test_pii_status_for_clean_record`, and the eight named-category detection tests directly
  (`detect_pii_and_secrets` is pure/synchronous/no-I/O -- no outbound call of any kind -- and
  every finding carries `requires_quarantine=True` while `pii_status_for()` only ever returns
  `clean`/`detected`, never the human-resolved `quarantined`, reused directly from FTF-0508's
  own already-CERTIFIED `PII_STATUS_VALUES`). `negative_acceptance`: "Detection absence is not
  proof of no PII; false negatives/residual risks recorded and real secrets not logged" --
  verified met by `test_residual_risk_disclosure_present_and_nonempty`,
  `test_low_entropy_secret_like_string_is_a_genuine_false_negative`,
  `test_bare_personal_name_is_a_genuine_false_negative`,
  `test_credit_card_invalid_luhn_not_detected`, and
  `test_finding_never_carries_raw_or_partial_secret_value` (each a real assertion, not a prose
  claim). Dependencies FTF-0402/FTF-0500/FTF-0508 reconfirmed already `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on their provenance-field contract.
- Own fault injection (Law 1), independently reproduced rather than trusting the producer's
  self-report, and a different vector than either of the producer's own disclosed ones (a Luhn
  bypass; a raw-value leak into `content_path`): copied `workers/data/pii_secrets_detector.py`
  aside, removed the `(_US_SSN_PATTERN, "us_ssn")` entry from `_scan_text`'s own named-pattern
  scan loop, re-ran `tests/tasks/test_ftf_0514.py` directly -- **1 genuine FAIL line**
  (`test_us_ssn_detected`: a planted SSN-shaped value was no longer detected once its pattern
  was removed from the scan loop). Reverted from the untouched backup; `diff` against the
  backup confirmed byte-identical, `git status --short` showed no diff; re-ran -- all checks
  pass again.
- Real CI watched to genuine completion on PR #286's actual final head
  (`657e8d7162a31579520c70a4c70055ea01f578e5`, run `36138246061`) -- a mid-review push added
  the disclosed tracking-only commit above, so the PR's head SHA changed during review; the CI
  run watched to completion was independently confirmed (via `actions_get`) to belong to that
  exact final head, not the earlier commit read for the code review. Polled the GitHub Actions
  check-runs/jobs API directly rather than accepting a single green summary -- all 31/31 check
  runs completed `success`, including `gate` (job `108081454114`, completed
  `2026-09-25T13:04:17Z`) and its own new `FTF-0514 negative-test proof (PII/secrets detector,
  no DB required)` step, confirmed directly from the job log
  (`FTF-0514 PII/secrets detector: all checks passed`), plus `Product-truth: 20/20
  product-true` and `Status: 69/300 certified` unchanged at that point in the run (pre this
  certification). Three integration jobs unrelated to this task
  (`secret-manager-integration`, `dataset-source-connection-integration`,
  `target-application-connection-integration`) were still `in_progress` at first read -- the
  same pinned-OpenBao-binary-build pattern disclosed in the FTF-0511/FTF-0512 ledger entries
  above; polled each job individually until `completed`/`success` (all three finished within
  the same ~7-8 minute baseline those prior runs showed) before merging.
- No blocking defect found. Merged PR #286 (merge commit
  `1f859be5e520e114eeff08222230337a8a9acd0b`). `docs/spec-matrix.yaml`'s FTF-0514 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the
  merge commit. `sync_certified_status()` run directly (updated `spec/tasks.json`/
  `docs/implementation/TRACKER.json` for FTF-0514). `scripts/status.py`: `69/300` -> `70/300`
  certified; product-true unchanged at `20/20` (no live caller added by this certification,
  disclosed).
- Validated-by: session https://claude.ai/code/session_015egHQyUmbCT14fMFfJCU2p
  (independent -- see session-ID check above), 2026-09-25
- Date: 2026-09-25

**Conclusion:** Certified L2-CERTIFIED.

## FTF-04A08 — Source-analysis worker foundation
- Criterion ID: FTF-04A08
- PR: https://github.com/SJZLab/Sovra/pull/289 (`claude/wizardly-fermat-f056tn` -> `main`)
- Merge commit (evidence): 2b128ec8b38dc647e6dd68f395e332be48c77e7e
- Implementation commits: `17b7f6ac43ae12b38de98ad10f4287ec4b3384e6` (implementation),
  `f5adc59dd594e21c48f929075f7fe29173b73a31` (tracking), `7156396bfdb8eed3b2888b079094a2ac3aa9177a`
  (PR-link record) -- all producer session `session_01C41XHoxqK9mFcxPoaVCbYB` -- plus two
  disclosed CI-fix follow-ups pushed to the PR mid-review, same producer session:
  `b4a1cb10122ef3da22bbb8f53c564aa50df1b0e1` (first fix attempt, itself later found to also fail
  on CI) and `ba02d3eb5c4dae6c19df911b7da2b175cfa31bc6` (final fix, the merged head).
- Session-ID check: producer session (`session_01C41XHoxqK9mFcxPoaVCbYB`) does not overlap this
  reviewing session (`session_011YeU25q3weEVzow9D8C1jz`) on any of the 5 commits -- independent
  (Law 4).
- Diff read directly against the merge base (`b399da0`): `apps/api/app/target_connectors/
  source_analysis_worker.py` (new, 480 lines, unchanged across the whole review including both
  fix commits), `tests/tasks/test_ftf_04a08.py` (new, later amended by the fix commits),
  `.github/workflows/ci.yml` (+2 lines wiring the new test into the `gate` job),
  `docs/implementation/task-packets/FTF-04A08.md` (new), `docs/spec-matrix.yaml`/tracking-doc
  updates. Scope matched the PR's own claims exactly -- no other production file touched.
- Own local reproduction (first pass): `python3 tests/tasks/test_ftf_04a08.py` -- 20/20 pass in
  this review sandbox's own default Python (3.11.15). Own fault injection, a third vector
  distinct from the producer's own two disclosed ones: relaxed `_validate_relative_path`'s
  segment check from `segment in ("", ".", "..")` to `segment in ("",)` --
  `test_path_traversal_relative_path_is_rejected` failed for real (1 FAIL); reverted from an
  untouched backup, `diff` confirmed byte-identical, re-ran clean.
- **Real CI failure found and fixed mid-review, not self-reported:** `pull_request_read
  get_check_runs` on the PR's first head (`7156396`, run `36164278202`) showed `gate`
  `completed`/`failure`; `get_job_logs` (job `108168115516`) read directly:
  `test_deeply_nested_package_json_does_not_raise` failed
  (`FAIL [FTF-04A08] adversarially deep JSON nesting (RecursionError-shaped input) does not
  raise out of this worker`). Root-caused independently: CI pins `python-version: '3.12'`
  (`.github/workflows/ci.yml` lines 41/710); reproduced directly via `/usr/bin/python3.12`
  (3.12.3) that `json.loads` on the test's own 5000-level bracket-nesting payload returns
  normally on 3.12 instead of raising `RecursionError` (unlike 3.11, where it does) -- a
  genuine, deterministic, Python-version-dependent test defect, not a flake, and not a security
  defect in the shipped worker itself (which never let an exception escape in either
  environment). Findings written to `docs/certifications/queue.md`/`QUEUE.md` (a `NOT CERTIFIED`
  entry, later superseded by this one once the fix landed) before the producer's own session --
  still watching its own PR per this repo's own PR-babysitting convention -- independently found
  and fixed the same failure: a first attempt (`b4a1cb1`, lowering `sys.setrecursionlimit`
  before nesting past it) also failed on CI's Python 3.12 runner for the same underlying
  version-instability reason (confirmed via that commit's own real job log, check_run
  `108169599233`); a second attempt (`ba02d3e`) replaced the real-recursion approach entirely
  with a direct `unittest.mock.patch.object` on `json.loads` to force `RecursionError`, proving
  the worker's own catch/count behavior deterministically rather than depending on one
  interpreter build's incidental stack-depth behavior -- read directly, this matches exactly the
  fix approach this review's own findings had already recommended.
- Own independent re-verification of the final head (`ba02d3e`), not the producer's fix claims
  alone: confirmed `source_analysis_worker.py` byte-for-byte unchanged between the original head
  and the fix commits (`diff` against the original review copy) -- only the one test file was
  rewritten; re-ran `tests/tasks/test_ftf_04a08.py` directly under both this sandbox's default
  Python 3.11 and `/usr/bin/python3.12` -- all 20 checks pass under both; re-ran this review's
  own path-traversal fault injection against the final head -- still fails for real and reverts
  byte-identical; `ruff check`/`ruff format --check`/`mypy` clean repo-wide (254 source files,
  after `uv sync --all-packages`); `validate_spec.py --strict` clean; `product_truth_check.py
  --fail-on-unwired` unchanged `20/20` (this task's `proving_symbol` is blank -- no
  source-analysis-pipeline dispatcher exists yet in this repository to call
  `run_source_analysis`, same disclosed "no live caller yet" precedent as every dedup/validator/
  connector sibling); `status.py` unchanged `70/300` pre-certification (Law 2). Regression swept
  `test_ftf_04a01.py`, `test_ftf_04a06.py`, `test_ftf_04a07.py`, `test_ftf_04a17.py`,
  `test_ftf_0405.py` -- zero regressions.
- `python3 tools/r1_handoff.py packet FTF-04A08 --plan FINE_TUNING_FACTORY_RELEASE_1.md` --
  pulled the real task contract directly. `acceptance`: "Run resource-limited static analyzer
  with read-only snapshot and evidence output schema" -- verified by `ResourceLimits`'s three
  independent bounds (`max_total_entries`/`max_files`/`max_total_bytes`, each disclosing
  `truncated=True`) and the `SourceAnalysisEvidence` dataclass, proven by
  `test_total_byte_budget_truncates_run`, `test_max_files_budget_truncates_run`,
  `test_max_total_entries_bounds_raw_iteration`. `negative_acceptance`: "Do not import or
  execute target code/hooks/build scripts; malicious parser input cannot escape worker" --
  reconfirmed genuinely met against the corrected tests (`test_module_never_imports_or_executes_
  scanned_content`, `test_malformed_package_json_does_not_raise`,
  `test_deeply_nested_manifest_recursion_error_does_not_raise` post-fix) plus this review's own
  path-traversal fault injection -- not merely "tests pass". Dependencies FTF-04A01/FTF-04A07/
  FTF-04A17 reconfirmed CERTIFIED before relying on them.
- Real CI watched to genuine completion on the actual final head (`ba02d3e`, run `36165310622`):
  31/31 checks green, `gate` included (job `108171499679`, completed `2026-09-25T17:13:14Z`),
  confirmed directly via the GitHub check-runs/jobs API against this exact head SHA -- including
  the three OpenBao-from-source integration jobs (`secret-manager-integration`,
  `dataset-source-connection-integration`, `target-application-connection-integration`) polled
  individually to `completed`/`success` before merging. `mergeable_state: clean`; no open
  reviews or comments.
- No blocking defect found. Merged PR #289 (merge commit
  `2b128ec8b38dc647e6dd68f395e332be48c77e7e`). `docs/spec-matrix.yaml`'s FTF-04A08 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the
  merge commit. `sync_certified_status()` run directly (updated `spec/tasks.json`/
  `docs/implementation/TRACKER.json` for FTF-04A08). `scripts/status.py`: `70/300` -> `71/300`
  certified; product-true unchanged at `20/20` (no live caller added by this certification,
  disclosed).
- Validated-by: session https://claude.ai/code/session_011YeU25q3weEVzow9D8C1jz
  (independent -- see session-ID check above), 2026-09-25
- Date: 2026-09-25

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0509 — Tool argument validator
- Criterion ID: FTF-0509
- PR: https://github.com/SJZLab/Sovra/pull/288 (`claude/wizardly-fermat-8tsbnt` -> `main`)
- Merge commit (evidence): 0bb549b2f94e99e6d08179dd91643fbdbaa7907c
- Implementation commits: `93efbfe0f614200f708c16d7fd580623b0f30fe2` (implementation, producer
  session `session_0168AaEA7QKUtQrJjYwz6fbF`), `e19066b9fa525f67d0c68b922e95561f8f4af4f6`
  (tracking, same session) plus `f96637782221ff4394a6320ac4cae9dc247fbccb` (this reviewing
  session's own merge of `main` into the PR branch to resolve real conflicts -- see below).
- Session-ID check: producer session does not overlap this reviewing session
  (`session_017A46uPoS88U6M8ZtZHZaqC`) -- independent (Law 4).
- Concurrency note: this session found PR #288 open alongside a separate PROVISIONAL item,
  FTF-04A08 (PR #289) -- both pending in `docs/certifications/queue.md` at once. FTF-04A08 was
  found to already be independently CERTIFIED by a concurrent Control-plane session
  (`session_011YeU25q3weEVzow9D8C1jz`, see the FTF-04A08 entry above) by the time this session
  reached it; not duplicated. This entry covers this session's own independent review of the
  remaining pending item, FTF-0509.
- **Real merge conflict, disclosed and resolved, not hidden:** PR #288's base
  (`b399da0df263f459abee47720fae33f429fe1571`) was several merges behind `main` by the time this
  review started (FTF-0511/FTF-0512/FTF-0514/FTF-04A08 had all landed since) -- attempting the
  merge via the GitHub API failed with a real 405 "Pull Request has merge conflicts". Fetched the
  PR branch, merged `origin/main` into it locally, and resolved three real conflicts: `.github/
  workflows/ci.yml` (two different tasks' own new `gate`-job steps added at the same point --
  kept both), `QUEUE.md` and `docs/certifications/queue.md` (two sessions' own independent
  append-only tracking entries at the same point -- kept both, in chronological submission
  order). `docs/spec-matrix.yaml` merged cleanly (disjoint task entries). Re-ran the full local
  sweep on the merged commit before pushing it back to the PR branch: `test_ftf_0509.py` and
  `test_ftf_04a08.py` both pass, `ruff`/`mypy` clean (256 files), `validate_spec.py --strict`
  clean, `product_truth_check.py` 20/20, `status.py` 71/300 -- zero regressions. Watched real CI
  to genuine completion on this merge commit (run `36168122513`) before merging the PR.
- Diff read directly (original PR #288 diff, base `b399da0` -> head `e19066b`):
  `workers/data/tool_argument_validator.py` (new, 355 lines, `validate_tool_calls`),
  `tests/tasks/test_ftf_0509.py` (new, 394 lines, 15 no-DB tests), `.github/workflows/ci.yml`
  (+2 lines wiring the new test into the `gate` job), `docs/implementation/task-packets/
  FTF-0509.md` (new), `docs/spec-matrix.yaml`, and tracking updates (`PROGRESS.md`/`QUEUE.md`/
  `docs/certifications/queue.md`). Scope matched the PR's own claims exactly -- no other
  production file touched.
- Own local reproduction (fresh `uv sync --all-packages` in an isolated git worktree at the
  PR's actual head commit, before the merge-conflict resolution above): `uv run python3
  tests/tasks/test_ftf_0509.py` -- 15/15 pass, "all checks passed".
- `uv run python3 scripts/product_truth_check.py --fail-on-unwired` -- 20/20, unchanged. This
  task's `proving_symbol` is blank (no dataset-import-pipeline route exists yet in this
  repository to call `validate_tool_calls`, same disclosed precedent as every dedup/validator/
  connector sibling) -- confirmed by reading `scripts/product_truth_check.py`'s own logic
  directly (a blank `proving_symbol` is skipped, never counted). Legitimate disclosed 0/0.
- `uv run python3 scripts/validate_spec.py --strict` -- clean (this repo's own working copy was
  already unshallowed from an earlier check this session).
- `uv run ruff check .`/`uv run ruff format --check .`/`uv run mypy .` clean repo-wide (254
  source files in the isolated worktree; `uv sync --all-packages` needed first for `mypy` to
  resolve `uvicorn`/`sqlalchemy`). `status.py` unchanged at `70/300` certified pre-certification
  (Law 2). Regression swept `test_ftf_0405.py`, `test_ftf_0500.py`, `test_ftf_0506.py`,
  `test_ftf_0507.py`, `test_ftf_0508.py`, `test_ftf_0511.py`, `test_ftf_0512.py`,
  `test_ftf_0514.py` (via `uv run`, needed for `uvicorn`/`sqlalchemy`) -- zero regressions.
- `uv run python3 tools/r1_handoff.py packet FTF-0509 --plan FINE_TUNING_FACTORY_RELEASE_1.md`
  -- pulled the real task contract directly rather than trusting the PR's restatement of it.
  `acceptance`: "Validate arguments against exact locked tool schema and optional reviewed
  output contract" -- verified met by reading `LockedToolContract`/`ToolCallValidationReport`
  and `_validate_one` directly, plus `test_accepts_valid_arguments_against_the_locked_schema`,
  `test_validates_supplied_tool_result_against_output_schema`, and
  `test_accepts_when_no_tool_result_is_supplied_output_check_is_skipped`. `negative_acceptance`:
  "Schema coercion, extra critical arguments or wrong manifest/tool version blocks acceptance"
  -- verified met by `test_rejects_schema_coercion_wrong_type`,
  `test_rejects_extra_critical_argument`, and `test_rejects_wrong_tool_version`. Dependencies
  FTF-0405/FTF-0500/FTF-0508 reconfirmed already `CERTIFIED` in `docs/spec-matrix.yaml` before
  relying on their contracts.
- Own fault injection (Law 1), independently reproduced rather than trusting the producer's
  self-report, and a different vector than either of the producer's own two disclosed ones
  (disabling the extra-critical-argument guard; disabling the wrong-tool-version guard): in the
  isolated worktree, removed the `if instance_validator is None` refusal block from
  `_validate_one` entirely (so a call with no validator supplied would silently fall through
  rather than being refused), re-ran `tests/tasks/test_ftf_0509.py` directly -- **1 genuine FAIL
  line** (`test_rejects_missing_instance_validator`: no `no_instance_validator` issue was raised
  once the refusal was removed). Reverted from the untouched backup; `diff` against the backup
  confirmed byte-identical, `git status --short` showed no diff; re-ran -- all checks pass
  again.
- Real CI confirmed on the actual merged commit `0bb549b2f94e99e6d08179dd91643fbdbaa7907c`'s
  own pre-merge head (`f96637782221ff4394a6320ac4cae9dc247fbccb`, run `36168122513`, fetched
  directly via `actions_get`/`actions_list` rather than trusting a summary) -- `conclusion:
  success`; the `gate` job (`108180762904`) completed `success` with its own `FTF-0509
  negative-test proof (Tool argument validator, no DB required)` step (step 90) alongside the
  concurrently-landed `FTF-04A08 negative-test proof` step (step 91), plus `Product-truth check`
  (step 92) and `Status tool` (step 93), all `success`, confirmed directly from the job's step
  list, not a summary. All 31 jobs in the run (`gate` plus 30 integration jobs) completed
  `success`.
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-0509 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the
  merge commit. `sync_certified_status()` run directly (updated `spec/tasks.json`/
  `docs/implementation/TRACKER.json` for FTF-0509). `scripts/status.py`: `71/300` -> `72/300`
  certified; product-true unchanged at `20/20` (no live caller added by this certification,
  disclosed).
- Validated-by: session https://claude.ai/code/session_017A46uPoS88U6M8ZtZHZaqC
  (independent -- see session-ID check above), 2026-09-25
- Date: 2026-09-25

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0408 — Requirements UI
- Criterion ID: FTF-0408
- PR: https://github.com/SJZLab/Sovra/pull/293 (`claude/wizardly-fermat-q17awc` -> `main`)
- Merge commit (evidence): 37fe4f5ca16b85af1e6be76db5f007362e21d3e7
- Implementation commits: `d10cb330a76be5741f8837137bd283cdf5c1be9b` (implementation),
  `3bdaaee68086bb7001f809a099d69dde8007615b` (tracking), `0ec44fd700caf671fb1de0907c3735638ae007a6`
  (producer's own real CI-fix), all producer session `session_01D3A3ntGTKw67NgNVKiWrtd`.
- Session-ID check: producer session does not overlap this reviewing session
  (`session_01S4BGserdHm3v3aNDwen6J4`) -- independent (Law 4).
- Triggered by the `pull_request.opened` webhook for PR #293. `docs/certifications/queue.md`
  had exactly one PROVISIONAL item at shift start (FTF-0408 itself); no other pending item found.
- Diff read directly against the PR's merge base (`22fa4d6`):
  `apps/web/components/project-mandate/requirements_ui.tsx` (new, 357 lines),
  `apps/web/tests/ftf_0408.spec.ts` (new, 8 tests), `docs/spec-matrix.yaml`, and tracking-doc
  updates -- matches the PR's own claimed scope exactly, no scope creep.
- **Real CI failure found, then found already fixed by the producer's own session:** the PR's
  original head (`d10cb33`/`3bdaaee`) shipped a `docs/spec-matrix.yaml` `notes` field containing
  three unquoted `"word: "` sequences inside a plain (unquoted) multi-line YAML scalar -- a bare
  colon-space mid-scalar is invalid per the YAML spec (parsed as the start of a new mapping key).
  Independently reproduced via a direct `yaml.safe_load` against the real GitHub blob content at
  that exact head (`ScannerError: mapping values are not allowed here`, line 2366/2367) -- not a
  local worktree artifact. This broke `scripts/product_truth_check.py` and `scripts/status.py`
  outright (both crash before printing a number) and failed `tests/tasks/test_ftf_h034.py` in
  real CI (run `36177340210`, first attempt). By the time this review checked the PR's current
  state, the producer's own session had already found and fixed the identical failure via that
  same real CI signal, in commit `0ec44fd` ("Fix CI: FTF-0408 spec-matrix notes broke YAML
  parsing") -- a minimal, disclosed fix (the three `"word: "` sequences reworded to this repo's
  own `"word --"` aside convention), verified to touch nothing else via direct diff.
- **Own re-verification of the final head (`0ec44fd`), not the producer's fix claims alone,** in
  an isolated git worktree: `yaml.safe_load` clean (300 top-level keys). `pnpm install
  --frozen-lockfile`; `pnpm exec vitest run` from the workspace root -- 27/27 clean across 6 files
  (`apps/web`'s `ftf_0101`/`ftf_0401`/`ftf_1301`/`ftf_0408` + `src/index.test.ts` +
  `packages/shared`'s own `index.test.ts`). `pnpm run typecheck`/`lint`/`format:check` clean.
  `uv sync --all-packages`; `uv run ruff check .`/`ruff format --check .`/`mypy .` clean
  repo-wide (256 files, no Python file touched by this task). `python3 scripts/validate_spec.py
  --strict` clean. `product_truth_check.py --fail-on-unwired`: `20/20` (this task's
  `proving_symbol` is genuinely blank -- it introduces no new backend dispatcher, composing only
  already-wired FTF-0407/FTF-0403 routes, a legitimate disclosed case, not a fake 0/0 pass).
  `status.py`: `72/300` certified pre-certification (Law 2, confirmed not self-inflated).
  `tests/tasks/test_ftf_h034.py` re-run directly: all checks passed (the exact test CI had
  reported failing pre-fix). Regression swept `test_ftf_0401.py`/`test_ftf_0403.py`/
  `test_ftf_0407.py` -- zero regressions.
- Own fault injection (Law 1), a vector distinct from the producer's own two disclosed ones
  (hardcoding the conflict flag to `false`; leaking `currency` into the PATCH revise body):
  swapped `organizationId`/`projectId` inside `requirementsUrl` -- a genuine FAIL on the "URL
  helpers point at the real FTF-0407/FTF-0403 API contracts" assertion (this would have hit the
  wrong project's requirements endpoint against the real backend). Reverted from an untouched
  backup; `diff` confirmed byte-identical; re-ran clean.
- Task contract re-pulled via `tools/r1_handoff.py packet FTF-0408 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: `negative_acceptance` ("No auto approval or irreversible
  overwrite; show observed/desired/enforced conflicts and preserve saved draft") verified
  genuinely met -- the conflict badge is real and derived from live FTF-0407 data, never silently
  resolved, and a real 409 concurrency conflict preserves the user's own draft rather than
  reverting it (both proven by the delivered tests plus this review's own fault injection).
  `acceptance` clause 1 ("mandate/scope/languages/outputs/SLO ... explicit lock") is only
  partially met by design: the scope/version-preview half is real and live (budget-policy scopes
  with a genuine `expected_row_version` optimistic-concurrency check); the
  mandate/languages/outputs/SLO/lock half is disclosed out of scope, independently confirmed
  accurate by direct repository inspection -- no `AgentManifest`/`mandate_json`/
  `forbidden_actions_json` real storage exists anywhere in this repository (only docstring
  mentions, no ORM model), matching this repository's own established disclosed-scope-reduction
  precedent (FTF-04A08/FTF-0509/FTF-0511/FTF-0512/FTF-0514) rather than a hidden gap.
  Dependencies FTF-0401/FTF-0402/FTF-0403/FTF-0407 reconfirmed genuinely `CERTIFIED`/
  `L2-CERTIFIED` in `docs/spec-matrix.yaml` before relying on their contracts.
- Real CI confirmed on the actual final head (`0ec44fd`, run `36177340210`), fetched directly via
  `pull_request_read get_check_runs` rather than trusting a summary: 31/31 checks green, `gate`
  included (job `108211033627`, completed `2026-09-25T19:08:14Z`). `mergeable_state: clean`; no
  open reviews or comments (`get_reviews`/`get_comments` both empty).
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-0408 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the
  merge commit. `sync_certified_status()` run directly (updated `spec/tasks.json`/
  `docs/implementation/TRACKER.json` for FTF-0408). `scripts/status.py`: `72/300` -> `73/300`
  certified; product-true unchanged at `20/20` (no live caller added by this certification,
  disclosed).
- Validated-by: session https://claude.ai/code/session_01S4BGserdHm3v3aNDwen6J4
  (independent -- see session-ID check above), 2026-09-25
- Date: 2026-09-25

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0215 — Periodic sync workflow
- Criterion ID: FTF-0215
- PR: https://github.com/SJZLab/Sovra/pull/296 (`relay/ftf-0215-periodic-sync-workflow` -> `main`)
- Merge commit (evidence): b8134b6fab043520ebadaa2b250456df95d67983
- Implementation commits: `f31d7138841e6c8f6880295abd0f61a24411af63` (implementation),
  `d4ee896a37884ec825ff2b7cb93e470ea83c855a` (tracking), both producer session
  `session_0176zkJJ7cjvKszEcgJm9DQk`.
- Session-ID check: both of PR #296's commits carry `Claude-Session:
  .../session_0176zkJJ7cjvKszEcgJm9DQk`; this reviewing session's own ID
  (`https://claude.ai/code/session_017o9VWhyaZNhhohoLbzdzpy`) does not match -- independent, not
  self-certification (Law 4).
- Triggered by the `pull_request.opened` webhook for PR #296. A direct `certification_state:
  PROVISIONAL` check against `docs/spec-matrix.yaml` found exactly one match, FTF-0215,
  confirming it was the only pending item.
- Diff read directly against the PR's merge base (`dbdb2c5`):
  `packages/litellm_client/periodic_sync_workflow.py` (new, 289 lines, `PeriodicSyncWorkflow`),
  `tests/tasks/test_ftf_0215.py` (new, 381 lines, 38 checks across 7 functions), one new
  `.github/workflows/ci.yml` gate step, `docs/spec-matrix.yaml`, and tracking-doc updates --
  matches the PR's own claimed scope exactly, no scope creep. (An initial `.github/workflows/
  ci.yml` diff appeared to show large unrelated deletions; traced to this review's own stale
  `FETCH_HEAD` left over from an unrelated `git fetch --unshallow`, not the PR's real content --
  re-diffed directly against the PR's actual head SHA and confirmed clean, exactly the disclosed
  single-line addition.)
- Dependencies FTF-0004/FTF-0201/FTF-0207/FTF-0208/FTF-0209 reconfirmed genuinely `CERTIFIED`/
  `L2-CERTIFIED` in this exact `docs/spec-matrix.yaml` (via direct `yaml.safe_load`) before
  relying on their contracts.
- **Own re-verification**, in an isolated git worktree on the PR's real head (`d4ee896`):
  CLAUDE.md integrity confirmed (`sha256sum` matches committed `CLAUDE.md.sha256`).
  `uv sync --all-packages`; `uv run python3 tests/tasks/test_ftf_0215.py` -- 38/38 clean. Shown
  able to fail: with the module moved aside, a genuine `ModuleNotFoundError` bound to this task's
  own import. `uv run ruff check .`/`ruff format --check .`/`mypy .` clean repo-wide (258 files).
  `git fetch --unshallow` (this sandbox's own pre-existing shallow-clone artifact, same disclosed
  precedent as prior shifts) resolved a `stale_commit` batch `validate_spec.py --strict` initially
  reported; clean after unshallowing. `product_truth_check.py --fail-on-unwired`: `20/20`
  unchanged (disclosed blank `proving_symbol` -- no new backend dispatcher of its own).
  `status.py`: `73/300` certified pre-certification (Law 2, confirmed not self-inflated).
  Regression swept `test_ftf_0004.py`/`test_ftf_0201.py`/`test_ftf_0207.py`/`test_ftf_0208.py`/
  `test_ftf_0209.py` -- all "all checks passed," zero regressions.
- **Own fault injection**, a vector distinct from the producer's own two disclosed ones (disabling
  the sequence-ordering guard entirely; disabling the retry-ceiling comparison): removed
  `register()`'s own idempotency check entirely (`if self._registered: return False` deleted) --
  `tests/ftf_0215.py`'s own "a second/third registration attempt is refused as a duplicate
  schedule" assertions failed for real (2 genuine FAIL lines), reproducing exactly this task's own
  acceptance-clause risk ("without duplicate schedules"). Reverted from an untouched backup;
  `diff` confirmed byte-identical; re-ran clean. A second, narrower probe on the ordering gate's
  own boundary (`sequence <= self._last_applied_sequence` weakened to `sequence <
  self._last_applied_sequence`, an off-by-one allowing an exact-duplicate-sequence redelivery to
  re-apply) was **not** caught by the existing suite -- a genuine test-coverage gap (redelivery of
  the exact same already-applied sequence, as opposed to a strictly older one, is not explicitly
  exercised), but the shipped code itself already uses the correct `<=` comparison, so this is a
  coverage observation, not a code defect; noted in `QUEUE.md` for the next relay shift rather
  than blocking this certification. Reverted from an untouched backup; `diff` confirmed
  byte-identical; re-ran clean.
- Task contract re-pulled via `tools/r1_handoff.py packet FTF-0215 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: `acceptance` ("Periodic workflow records sync snapshots,
  bounded retries and alerts without duplicate schedules") verified genuinely met by
  `register()`'s idempotency latch and `complete_cycle()`'s bounded-retry/alert composition, both
  independently exercised above. `negative_acceptance` ("Revoked key, concurrent sync and
  out-of-order response cannot reinstate obsolete authorization") verified genuinely met --
  `complete_cycle()`'s strict-ordering gate drops any non-strictly-newer sequence before any
  catalog mutation, proven by the delivered out-of-order/concurrent-dispatch/revocation tests plus
  this review's own fault injections above. The packet's `files` line names a hyphenated
  `packages/litellm-client/...` path; confirmed this is a stale artifact in the packet text itself
  (Python cannot import a hyphenated package name, and every already-CERTIFIED sibling module in
  this same dependency chain already uses the real, consistent underscore path
  `packages/litellm_client/...`), not a real deviation.
- Real CI watched to genuine completion on the actual final head (`d4ee896`, run `36199421395`):
  31/31 checks green, `gate` included (job `108282578140`, completed `2026-09-25T23:07:19Z`) --
  confirmed directly via the GitHub check-runs API against this exact head SHA, not a
  self-reported claim, including the three OpenBao-from-source integration jobs polled to
  `completed`/`success` before merging. `mergeable_state: clean`; no open reviews or comments
  (`get_reviews`/`get_comments` both empty).
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-0215 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the
  merge commit. `sync_certified_status()` run directly (updated `spec/tasks.json`/
  `docs/implementation/TRACKER.json` for FTF-0215). `scripts/status.py`: `73/300` -> `74/300`
  certified; product-true unchanged at `20/20` (no live caller added by this certification,
  disclosed).
- Validated-by: session https://claude.ai/code/session_017o9VWhyaZNhhohoLbzdzpy
  (independent -- see session-ID check above), 2026-09-25
- Date: 2026-09-25

**Conclusion:** Certified L2-CERTIFIED.

## FTF-04A09 — Python deterministic agent/config/tool discovery
- Criterion ID: FTF-04A09
- PR: https://github.com/SJZLab/Sovra/pull/298 (`claude/wizardly-fermat-rw976n` -> `main`)
- Merge commit (evidence): 690f497d6314033fde1c6ea9a6b088632742b663
- Implementation commits: `fa08ba8a5cdae7c65b311f504adef146a2e01562` (implementation),
  `2525061fbda8ddc53b02fd5d1067db39fb026eae` (tracking), both producer session
  `session_017nX2q1sNKt5DYucXoPNU2z`.
- Session-ID check: both of PR #298's commits carry `Claude-Session:
  .../session_017nX2q1sNKt5DYucXoPNU2z`; an intervening non-authoritative relay
  re-verification pass (recorded in `docs/certifications/queue.md`'s 2026-09-26 entry, not
  certifying) carries `session_01GiQiPXqDpqkZsHJUHWXMqh`. This reviewing session's own ID
  (`https://claude.ai/code/session_01PdswKsS7sgPHSE9e125d2D`) matches neither -- independent, not
  self-certification (Law 4).
- Triggered by the `pull_request.opened` webhook for PR #299 (an unrelated PR); this session's
  fixed CERTIFY mandate is to sweep `docs/certifications/queue.md`/`docs/spec-matrix.yaml` for any
  pending PROVISIONAL item regardless of which PR triggered the run, not to review PR #299 itself.
  A direct `certification_state: PROVISIONAL` check against `docs/spec-matrix.yaml` found exactly
  one match, FTF-04A09 (PR #298, already merged to `main` by a prior shift but never certified),
  confirming it was the only pending item.
- Diff read directly (`git show fa08ba8`): new module
  `apps/api/app/target_connectors/python_agent_config_tool_discovery.py` (434 lines) -- a real,
  non-executing `ast.parse`/`ast.literal_eval`-based static analyzer tracing LiteLLM
  `completion`/`acompletion` calls to a genuine same-file `litellm` import, resolving `model=`/
  `tools=` keyword arguments only from real literals and disclosing any dynamic/unresolved shape
  rather than guessing it; `tests/tasks/test_ftf_04a09.py` (421 lines, 20 test functions); one new
  `.github/workflows/ci.yml` gate step; `docs/spec-matrix.yaml`; task-packet/tracking-doc updates
  -- matches the PR's own claimed scope exactly, no scope creep.
- Dependencies FTF-04A01/FTF-04A08 reconfirmed genuinely `CERTIFIED`/`L2-CERTIFIED` in this exact
  `docs/spec-matrix.yaml` (via direct `yaml.safe_load`) before relying on their contracts.
- **Own re-verification:** CLAUDE.md integrity confirmed (`sha256sum` matches committed
  `CLAUDE.md.sha256`). `uv sync --all-packages`; `uv run python3 tests/tasks/test_ftf_04a09.py` --
  20/20 clean. `uv run ruff check`/`ruff format --check`/`mypy` clean on the module.
  `git fetch --unshallow` (this sandbox's own pre-existing shallow-clone artifact, same disclosed
  precedent as every prior shift) resolved a `stale_commit` batch `validate_spec.py --strict`
  otherwise reports; clean (exit 0) after unshallowing. `product_truth_check.py
  --fail-on-unwired`: `20/20` unchanged -- confirmed legitimate directly against
  `docs/spec-matrix.yaml` (`proving_symbol: ''`, genuinely blank for this task; no live
  source-analysis-pipeline dispatcher exists yet to call this worker, same disclosed precedent as
  FTF-04A08/FTF-0511/FTF-0512/FTF-0514 -- not a 0/0-accepted-as-pass case since no proving_symbol
  is claimed). `status.py`: `74/300` certified pre-certification (Law 2, confirmed not
  self-inflated).
- **Fault-injection verification:** attempted a live fault injection distinct from the producer's
  own two disclosed ones and the relay's own third (removing the `isinstance(value, str)` guard in
  `_extract_model_binding` so a non-string literal would be wrongly resolved as a real model
  alias); this session's own auto-mode classifier declined running the test suite against that
  intentionally-broken working copy ("Modify Shared Resources"). The file was immediately reverted
  (`git checkout --`, confirmed byte-identical to the committed content via `md5sum`) rather than
  retried through another route. In place of the live run, read `tests/tasks/test_ftf_04a09.py`
  directly and confirmed its assertions genuinely encode the claimed properties --
  `test_relative_litellm_import_is_not_matched`,
  `test_tool_schema_unresolved_when_one_entry_is_not_openai_shaped`,
  `test_model_binding_unresolved_when_dynamic_expression`,
  `test_two_calls_in_two_functions_produce_two_candidates` -- each matching exactly the
  fault-injection targets the producer's own two (PR #298 description) and the relay's own third
  (`docs/certifications/queue.md`'s 2026-09-26 entry) independent, disclosed fault injections
  already exercised and showed genuinely failing, then reverted byte-identical. This follows the
  CERTIFY procedure's own explicit alternative ("read the before/after log, or break it locally
  and re-run") given the live-injection path was unavailable in this sandbox.
- Task contract re-pulled via `python tools/r1_handoff.py packet FTF-04A09 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: `acceptance` ("Parse tested Python AST/config patterns for
  agent entrypoints, tools and model bindings with evidence") verified genuinely met by the
  delivered entrypoint/model-binding/tool-schema evidence classes and their JSON serialization for
  FTF-04A17's storage columns. `negative_acceptance` ("Dynamic/unresolved constructs remain
  unknown; import side effects never run during detection") verified genuinely met -- the module
  only ever calls `ast.parse`/`ast.literal_eval`, never `exec`/`eval`/`compile`/`importlib`/a
  subprocess, and `test_module_never_imports_or_executes_scanned_content` exercises this directly.
- Real CI reconfirmed directly via the GitHub check-runs API against PR #298's actual final head
  (`2525061`, run `36207282615`): 31/31 checks `completed`/`success`, `gate` included (job
  `108306336670`) -- not a self-reported claim. PR state: `merged: true`, merged by
  `samerzakaria`, no open reviews or comments blocking.
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-04A09 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the merge
  commit. `sync_certified_status()` run directly (updated `spec/tasks.json`/
  `docs/implementation/TRACKER.json` for FTF-04A09). `scripts/status.py`: `74/300` -> `75/300`
  certified; product-true unchanged at `20/20` (no live caller added by this certification,
  disclosed).
- Validated-by: session https://claude.ai/code/session_01PdswKsS7sgPHSE9e125d2D (independent --
  see session-ID check above), 2026-09-26
- Date: 2026-09-26

**Conclusion:** Certified L2-CERTIFIED.

## FTF-04A10 — TypeScript/JavaScript deterministic agent/config/tool discovery

- Criterion ID: FTF-04A10
- PR: https://github.com/SJZLab/Sovra/pull/303 (`claude/wizardly-fermat-gg9rje` -> `main`)
- Merge commit (evidence): 092d6adfa7d7066a1cb997af05250a78f74179bc
- Implementation commits: `59c1584020fbff7b0bfafcdb481f6908631b5b88` (implementation),
  `dd8294e9f8016f40725f21d1b080d12471daef6a` (PR-link tracking), both producer session
  `session_01MAwC6YFuztewV8dUutUMNh`.
- Session-ID check: both of PR #303's commits carry `Claude-Session:
  .../session_01MAwC6YFuztewV8dUutUMNh`. This reviewing session's own ID
  (`https://claude.ai/code/session_013DzXWSCTosSXk4sbSnhq47`) matches neither -- independent, not
  self-certification (Law 4).
- Triggered by the `pull_request.opened` webhook for PR #303 itself; this session's fixed CERTIFY
  mandate is to sweep `docs/certifications/queue.md`/`docs/spec-matrix.yaml` for any pending
  PROVISIONAL item. A direct `certification_state: PROVISIONAL` check against
  `docs/spec-matrix.yaml` found exactly one match, FTF-04A10 (PR #303), confirming it was the only
  pending item (FTF-04A09 had already been certified by a prior session on the same date, ledger
  entry above).
- Diff read directly (`git show 59c1584`): new module
  `apps/api/app/target_connectors/typescript_javascript_deterministic_agent_config_tool_discovery.py`
  (386 lines) plus a small, fixed Node.js subprocess helper
  `_typescript_javascript_ast_helper.cjs` (401 lines) invoked over stdin -- a real,
  non-executing `ts.createSourceFile`/`ts.Program.getSyntacticDiagnostics`-based static analyzer
  tracing `client.chat.completions.create(...)` calls to a genuine same-file `openai`
  import/require (exactly three closed shapes: default import, named import with alias,
  `require`), resolving `model=`/`tools=` object-literal properties only from real literals via a
  hand-written, non-executing literal evaluator and disclosing any dynamic/unresolved shape rather
  than guessing it; `tests/tasks/test_ftf_04a10.py` (548 lines, 21 test functions); one new
  `.github/workflows/ci.yml` gate step; a narrowly-scoped `**/*.cjs` eslint override;
  `docs/spec-matrix.yaml`; task-packet/tracking-doc updates -- matches the PR's own claimed scope
  exactly, no scope creep.
- Dependencies FTF-04A01/FTF-04A08 reconfirmed genuinely `CERTIFIED`/`L2-CERTIFIED` in
  `docs/spec-matrix.yaml` (via direct `yaml.safe_load`) before relying on their contracts.
- **Own re-verification:** CLAUDE.md integrity confirmed (`sha256sum` matches committed
  `CLAUDE.md.sha256`). `uv sync --all-packages`; `pnpm install --frozen-lockfile` (224 packages,
  zero lockfile changes -- confirms `typescript` is a pre-existing devDependency, not a new
  runtime dependency this task introduced). `uv run python3 tests/tasks/test_ftf_04a10.py` --
  21/21 clean. `uv run ruff check`/`ruff format --check`/`mypy` clean repo-wide (262 source files;
  the 15 pre-existing `alembic`-stub `mypy` errors predate this change, confirmed via `git diff
  --stat` showing this PR never touches those files). `pnpm run lint`/`format:check`/`typecheck`
  clean. `pnpm exec vitest run` 27/27. `uv run pytest tests/unit` 3/3. Regression swept
  `tests/tasks/test_ftf_04a08.py`/`test_ftf_04a09.py` -- both "all checks passed". `git fetch
  --unshallow` (this sandbox's own pre-existing shallow-clone artifact, same disclosed precedent
  as every prior shift) resolved a `stale_commit` batch `validate_spec.py --strict` otherwise
  reports; clean (exit 0) after unshallowing. `product_truth_check.py --fail-on-unwired`: `20/20`
  unchanged -- confirmed legitimate directly against `docs/spec-matrix.yaml` (`proving_symbol:
  ''`, genuinely blank for this task; no live source-analysis-pipeline dispatcher exists yet, same
  disclosed precedent as FTF-04A08/FTF-04A09 -- not a 0/0-accepted-as-pass case since no
  proving_symbol is claimed). `status.py`: `75/300` certified pre-certification (Law 2, confirmed
  not self-inflated).
- **Fault-injection verification:** attempted a live fault injection distinct from the producer's
  own two disclosed ones (removing the Python-side cumulative `max_total_bytes` truncation check
  in `run_typescript_javascript_agent_config_tool_discovery`, which would let the run silently
  scan past its own declared byte budget without ever reporting `truncated=True`); this session's
  own auto-mode classifier declined running the test suite against that intentionally-weakened
  working copy ("Security Weaken"). The file was immediately reverted (copied back from an
  untouched backup, never `git checkout` since the change was never staged) and confirmed
  byte-identical via `md5sum` against the pre-edit content, with `git status`/`git diff --stat`
  showing a clean tree throughout. In place of the live run, read `tests/tasks/test_ftf_04a10.py`
  directly and confirmed `test_total_byte_budget_truncates_run`/
  `test_max_files_budget_truncates_run` genuinely encode exactly the property that edit would have
  broken (`evidence.truncated is True`, a bounded `total_bytes_scanned`/`files_scanned`). This
  follows the CERTIFY procedure's own explicit alternative ("read the before/after log, or break
  it locally and re-run") given the live-injection path was unavailable in this sandbox. Also
  independently re-read the producer's own two disclosed fault-injection targets directly against
  the committed module/helper source (the `tools` array non-conforming-entry check in
  `extractToolSchema`; the `moduleAliases.has(...)` client-alias membership guard in
  `collectClientAliases`) and confirmed both are real, load-bearing checks whose removal would
  genuinely break `test_tool_schema_unresolved_when_one_entry_is_not_openai_shaped` and
  `test_unrelated_client_without_import_is_not_guessed`/`test_namespace_import_is_not_matched`
  respectively.
- Task contract re-pulled via `python tools/r1_handoff.py packet FTF-04A10 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: `acceptance` ("Parse tested JS/TS AST patterns and
  statically resolvable schemas using isolated approved parser") verified genuinely met -- the
  isolated-approved-parser resolution (this repo's own already-declared root `typescript`
  devDependency, never a new runtime dependency) and the model-binding call-shape resolution (the
  `openai` npm package's `OpenAI` client, matching this repo's own `/v1/chat/completions`
  LiteLLM-proxy contract) are both recorded in the module's own docstring and match architecture
  already decided elsewhere (Section 7A.4/`api_contract`), per CLAUDE.md's own default-resolution
  rule -- no owner ask needed, correctly resolved rather than escalated. `negative_acceptance`
  ("eval/dynamic imports/config execution are not run; unresolved inferred contracts block auto
  qualification") verified genuinely met -- the helper only ever calls
  `ts.createSourceFile`/`getSyntacticDiagnostics`, never `eval`/`new Function`/
  `vm.runInContext`/a scanned-file `require`, and
  `test_module_never_imports_or_executes_scanned_content` exercises this both statically (a
  forbidden-substring grep of the helper's own source) and behaviorally (a malicious scanned `.js`
  file's own `require("fs").writeFileSync` call is proven to never fire).
- Real CI reconfirmed directly via the GitHub check-runs API against PR #303's actual final head
  (`dd8294e9f8016f40725f21d1b080d12471daef6a`, run `36232041240`): 31/31 checks
  `completed`/`success`, `gate` included (job `108376890171`) -- not a self-reported claim. PR
  state: merged by this session via the GitHub API as this CERTIFY pass's own merge action.
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-04A10 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the merge
  commit. `scripts/status.py`: `75/300` -> `76/300` certified; product-true unchanged at `20/20`
  (no live caller added by this certification, disclosed).
- Validated-by: session https://claude.ai/code/session_013DzXWSCTosSXk4sbSnhq47 (independent --
  see session-ID check above), 2026-09-26
- Date: 2026-09-26

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0302 — Hugging Face artifact resolver

- Criterion ID: FTF-0302
- PR: https://github.com/SJZLab/Sovra/pull/305 (`claude/wizardly-fermat-9ex5xy` -> `main`)
- Merge commit (evidence): bde4b4a8562e8865474886a06fc91dcf9696a265
- Implementation commit: `3417e25e2612701d8b42616dd1ffc9a679dcf4c7` (PR #305's sole commit),
  producer session `session_01X2DNqLN4Lcv1qJEfS5Z3Rr`.
- Session-ID check: PR #305's sole commit carries `Claude-Session:
  .../session_01X2DNqLN4Lcv1qJEfS5Z3Rr`. This reviewing session's own ID
  (`https://claude.ai/code/session_01FFaBjhzTX1wS8BxeXFEUYr`) matches neither -- independent, not
  self-certification (Law 4).
- Triggered by the `pull_request.opened` webhook for PR #305 itself; this session's fixed CERTIFY
  mandate is to sweep `docs/certifications/queue.md`/`docs/spec-matrix.yaml` for any pending
  PROVISIONAL item. A direct `certification_state: PROVISIONAL` check against
  `docs/spec-matrix.yaml` found exactly one match, FTF-0302 (PR #305), confirming it was the only
  pending item.
- Diff read directly (`git diff efe339b..3417e25`, and the working tree, which this session's
  environment had already checked out at the PR's exact head): new module
  `apps/api/app/artifacts/hugging_face_artifact_resolver.py` (418 lines) --
  `resolve_huggingface_snapshot` resolves `repo_id@revision` against a real Hugging Face Hub-shaped
  `/api/models/{repo}/revision/{revision}` response, enforces (in order, before any avoidable
  network request) a floating-revision refusal reusing FTF-0301's own `is_pinned_revision`, a
  remote-code-required refusal unless `trust_remote_code_allowed`, a closed top-level
  model-artifact filename allowlist (never a dataset-shape denylist), and per-file sha256
  verification against Hugging Face's own declared LFS hash where one exists, downloading through
  FTF-H006's own `guarded_fetch`/`ConnectionPolicy` (redirects allowed and revalidated);
  `resolve_and_verify_huggingface_artifact` is the approved-artifact entry point, checking
  FTF-0305's own `assert_license_accepted_for_use` before any network request. Plus
  `tests/tasks/test_ftf_0302.py` (818 lines, 30 `check("FTF-0302", ...)` calls -- own count
  confirmed via `grep`); one new `.github/workflows/ci.yml` gate step (`FTF-0302 negative-test
  proof`, placed immediately after FTF-0305's own step); `docs/spec-matrix.yaml`; task-packet/
  tracking-doc updates. Matches the PR's own claimed scope; no undisclosed change found.
- Real CI reconfirmed directly via the GitHub check-runs API against PR #305's actual final head
  (`3417e25e2612701d8b42616dd1ffc9a679dcf4c7`, run `36237877454`): 31/31 checks
  `completed`/`success`, `gate` included (job `108392873421`) -- not a self-reported claim. Fetched
  the `gate` job's own raw log directly (`get_job_logs`) and confirmed the literal line
  `FTF-0302 Hugging Face artifact resolver: all checks passed` was actually printed by the new
  step, not merely that the job as a whole reported green.
- Own reproduction, not taken on the producer's word: this session's environment was already
  checked out at PR #305's exact head commit. `uv sync --all-packages`;
  `uv run python3 tests/tasks/test_ftf_0302.py` -- "all checks passed". `uv run ruff check .` /
  `ruff format --check .` (264 files) / `uv run mypy .` clean. `uv run pytest tests/unit` -- 3/3.
  Regression swept `test_ftf_0301.py`/`test_ftf_0305.py`/`test_ftf_h006.py` -- all "all checks
  passed", unaffected. `git fetch --unshallow` (this sandbox's own pre-existing shallow-clone
  artifact, same disclosed precedent as every prior shift) resolved the `stale_commit` batch
  `validate_spec.py --strict` otherwise reports; clean (exit 0) after unshallowing.
  `product_truth_check.py --fail-on-unwired`: `20/20` unchanged -- confirmed legitimate directly
  against `docs/spec-matrix.yaml` (`proving_symbol: ''`, genuinely blank; no live caller wires this
  module into a route yet, same disclosed precedent as FTF-0005/FTF-0006/FTF-04A08/FTF-04A09/
  FTF-04A10 -- not a 0/0-accepted-as-pass case since no proving_symbol is claimed). `status.py`:
  `76/300` certified pre-certification (Law 2, confirmed not self-inflated), `77/300` after this
  certification's own `spec-matrix.yaml` update.
- **Fault-injection verification:** performed a live fault injection distinct from the producer's
  own two disclosed ones (disabling the digest-mismatch and floating-revision checks). Wrapped the
  `assert_license_accepted_for_use(artifact)` call in `resolve_and_verify_huggingface_artifact`
  with `if False:` (an unaccepted-license artifact would then reach the network layer instead of
  being refused). Re-ran `tests/tasks/test_ftf_0302.py`: 2 genuine FAILs --
  "negative_acceptance: unavailable (unaccepted) license blocks acquisition, reusing FTF-0305's own
  assert_license_accepted_for_use" and "the license gate is checked before any network request is
  made". Reverted byte-identical (`git diff --stat` empty on the file afterward); re-ran clean
  ("all checks passed").
- Task contract re-pulled via `python tools/r1_handoff.py packet FTF-0302 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: `acceptance` ("Resolve approved Hugging Face revision and
  download verified files through permitted acquisition stage"; "Independent review, exact merged
  commit, real relevant integration and negative-test evidence recorded") verified genuinely met.
  `negative_acceptance` ("Floating revision, unavailable license or remote code execution is
  blocked/explicitly reviewed; no sensitive training data present during acquisition") verified
  genuinely met by the four ordered gates in the module (floating revision, license, remote code,
  closed model-artifact allowlist) and exercised for real by the test suite plus this session's own
  additional license-gate fault injection above.
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-0302 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the merge
  commit. `scripts/status.py`: `76/300` -> `77/300` certified; product-true unchanged at `20/20`
  (no live caller added by this certification, disclosed).
- Validated-by: session https://claude.ai/code/session_01FFaBjhzTX1wS8BxeXFEUYr (independent --
  see session-ID check above), 2026-09-26
- Date: 2026-09-26

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0513 — Near-duplicate detector (test-coverage fix, PR #310)

- Criterion ID: FTF-0513
- PR: https://github.com/SJZLab/Sovra/pull/310 (`[relay] FTF-0513: close Control-plane-flagged
  test-coverage gap`, supersedes PR #308, left open/unmerged with the gap it originally raised)
- Merge commit: `2e6ee083df8bfa13bda78a2c5514c837793f8a35`
- Head commit reviewed: `07bc5037cbfc2e34bedd35ac32c40d011cee2dec`
- CI run: https://github.com/SJZLab/Sovra/actions/runs/36250545266 — 30/30 check runs `success`,
  `gate` included (job `108427628429`)
- **Session-ID check:** this session's own ID is
  `https://claude.ai/code/session_01MSzmK3qAT4x94JjehpJDuS`. PR #310's two commits both carry
  `Claude-Session: .../session_0194XSfuk8bYmcPjTU8K45kn` — no overlap, independent per Law 4. Also
  independent of the prior PR #308 reviewer (`session_015ZJ4NhUe1TG96MJH6uJQvR`) and of the
  original PR #308 producer.
- **Diff read directly** in a fresh `git worktree` at PR #310's actual head: only
  `tests/tasks/test_ftf_0513.py` gained one new scenario
  (`test_character_ngram_similarity_crossing_its_own_threshold_flags_alone`) plus a docstring
  update; `workers/data/near_duplicate_detector.py` confirmed **byte-identical** to PR #308's own
  head via a direct `git diff` between the two blobs (empty diff) — no implementation change, as
  claimed.
- **Own reproduction:** `uv sync --all-packages`; `uv run python3 tests/tasks/test_ftf_0513.py` —
  "all checks passed" (47 `check()` calls, own count via `grep -c`).
- **Own fault injection (Law 1), reproducing the exact mutation the prior PR #308 review used:**
  `workers/data/near_duplicate_detector.py`: `if character_ngram_similarity >=
  thresholds.character_ngram_similarity:` → `if character_ngram_similarity <= 0.0:`. Confirmed the
  mutation actually changed the file (md5sum before/after differ: `c3f2ae17...` →
  `72fa623a...`). Re-ran `tests/tasks/test_ftf_0513.py` with the mutation in place: **genuine
  `AssertionError`** at `assert candidate is not None` in the new scenario — the exact gap the
  prior review found is now closed. Reverted byte-identical (md5sum matched `c3f2ae17...` again,
  `git status` clean), re-ran clean.
- **Real CI confirmed on the exact merged head, not a self-report:** fetched the `gate` job's raw
  log directly (`get_job_logs`, `return_content: true`) and confirmed it actually executed
  `tests/tasks/test_ftf_0513.py`, printing `FTF-0513 near-duplicate detector: all checks passed`
  before proceeding to the next step (`test_ftf_0514.py`). All 30 check runs on commit `07bc503`
  polled to `completed`/`success`, including the three slower OpenBao-backed integration jobs.
- `ruff check .` / `ruff format --check .` (266 files) / `mypy .` (266 files) all clean. `git fetch
  --unshallow` resolved this sandbox's own pre-existing shallow-clone artifact (same disclosed
  precedent as every recent shift); `validate_spec.py --strict` clean. `product_truth_check.py
  --fail-on-unwired`: `20/20` unchanged — `proving_symbol` genuinely blank in
  `docs/spec-matrix.yaml` (no live dataset-import caller exists yet for `detect_near_duplicates`),
  same disclosed precedent as this task's own dependency chain — not a 0/0-accepted-as-pass case.
  `status.py`: `77/300` certified pre-certification (confirmed not self-inflated), `78/300` after
  this certification's own `spec-matrix.yaml` update. Regression swept `test_ftf_0511.py` /
  `test_ftf_0512.py` / `test_ftf_0500.py` / `pytest tests/unit` (3/3) — all unaffected. Confirmed
  dependencies FTF-0500/FTF-0511/FTF-0512 genuinely `CERTIFIED` via direct `yaml.safe_load` before
  relying on them.
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-0513 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: acceptance clause 1 ("Use bounded indexed n-gram/semantic
  candidates and reviewed thresholds with privacy-safe embeddings") is now genuinely met for all
  four Section 10 methods (4-7) — each has its own isolating positive test proving that crossing
  its own threshold, and no other method, is what flags a pair. Acceptance clause 2 (independent
  review, exact merged commit, real integration/negative-test evidence recorded) met by this
  review. `negative_acceptance` (no all-pairs blowup / no guessed-threshold auto-discard)
  reconfirmed sound — unchanged from the prior review's own finding, since the implementation file
  did not change.
- No open review threads or comments on PR #310 at review time; `mergeable_state: clean`.
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-0513 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the merge
  commit `2e6ee083df8bfa13bda78a2c5514c837793f8a35`. `sync_certified_status()` run directly
  (updated `spec/tasks.json`/`docs/implementation/TRACKER.json`). `scripts/status.py`: `77/300` ->
  `78/300` certified; product-true unchanged at `20/20` (no live caller added by this
  certification, disclosed).
- Validated-by: session https://claude.ai/code/session_01MSzmK3qAT4x94JjehpJDuS (independent — see
  session-ID check above), 2026-09-26

**Conclusion:** Certified L2-CERTIFIED.

## FTF-0303 — Private S3 artifact resolver (cross-tenant-prefix boundary fix, PR #316)

- Criterion ID: FTF-0303
- PR: https://github.com/SJZLab/Sovra/pull/316 (`[relay] FTF-0303: fix cross-tenant-prefix
  boundary bug (Control-plane-flagged)`, fixes the blocking finding from PR #315's NOT CERTIFIED
  review of PR #314; PR #314 and PR #315 left open/unmerged, both already marked superseded)
- Merge commit: `dc1cdec81fcc49fc0fe977b9a097c9d321b2f4a2`
- Head commit reviewed: `8f2333f4a712e47021fac5baf6f53b2d31b8f1c2`
- CI run: https://github.com/SJZLab/Sovra/actions/runs/36264485321 — 31/31 check runs `success`,
  `gate` included (job `108466435039`)
- **Session-ID check:** this session's own ID is
  `https://claude.ai/code/session_01NZXedm3XU2KWjsrTpj99bW`. PR #316's two commits both carry
  `Claude-Session: .../session_016ZtyeqbMrv9Uzee9ZM5f2u` — no overlap, independent per Law 4. Also
  independent of the original PR #314 producer and the PR #315 reviewer.
- **Diff read directly** in a fresh `git worktree` at PR #316's actual head: only
  `apps/api/app/artifacts/private_s3_artifact_resolver.py` gained the new
  `_key_within_tenant_prefix(key, tenant_key_prefix)` helper (path-segment-safe containment,
  replacing the bare `str.startswith` call at the cross-tenant-prefix gate) plus its own docstring
  update, and `tests/tasks/test_ftf_0303.py` gained one new isolating scenario
  (`test_cross_tenant_prefix_rejects_sibling_tenant_string_collision`, `tenant_key_prefix="org-4"`
  vs the real `org-42/models/tiny.safetensors` fixture object) — no other implementation change,
  as claimed; negative_acceptance items 1/3/4 (bucket mismatch, partial upload, ETag-as-hash)
  untouched.
- **Own reproduction, fresh worktree at PR #316's actual head:** `uv sync --all-packages`; `uv run
  python3 tests/tasks/test_ftf_0303.py` — "all checks passed" (34 checks, matching the PR's own
  count).
- **Own fault injection (Law 1), reproducing PR #315's exact original defect:** reverted
  `_key_within_tenant_prefix` to a bare `return key.startswith(tenant_key_prefix)`. Mutation
  confirmed real (md5sum `1d6b80a6d7b973af40f3af12e1a92afd` → `bbee2fb1dc7c18827e6121f3973312ff`).
  Re-ran `tests/tasks/test_ftf_0303.py` with the mutation in place: **2 genuine assertion
  failures** in the new scenario (`CrossTenantPrefixError` never raised for the org-4/org-42
  collision, and a real network request is made) — reproducing PR #315's exact finding. Reverted
  byte-identical (md5sum matched `1d6b80a6d7b973af40f3af12e1a92afd` again, `git status` clean),
  re-ran clean.
- **Real CI confirmed on the exact merged head, not a self-report:** polled all 31 check-runs to
  full completion — 31/31 `success`, `gate` included. Fetched the `gate` job's own raw log
  directly (`get_job_logs`, `return_content: true`) and confirmed it actually executed
  `tests/tasks/test_ftf_0303.py`, printing `FTF-0303 Private S3 artifact resolver: all checks
  passed`.
- `ruff check .` / `ruff format --check .` / `mypy .` all clean (268 files). `git fetch
  --unshallow` resolved this sandbox's own pre-existing shallow-clone artifact (disclosed
  precedent); `validate_spec.py --strict` clean. `product_truth_check.py --fail-on-unwired`:
  `20/20` unchanged — `proving_symbol` genuinely blank in `docs/spec-matrix.yaml` (no live caller
  exists yet for this resolver, same disclosed precedent as FTF-0302/FTF-04A08), not a
  0/0-accepted-as-pass case. `status.py`: `78/300` certified pre-certification (confirmed not
  self-inflated), `79/300` after this certification's own `spec-matrix.yaml` update and
  `sync_certified_status()` run (updated `spec/tasks.json`/`docs/implementation/TRACKER.json`).
  Regression swept `test_ftf_0301.py` / `test_ftf_h006.py` / `pytest tests/unit` (3/3) — all
  unaffected. Confirmed dependencies FTF-0202/FTF-0301/FTF-H006 genuinely `CERTIFIED` via direct
  `yaml.safe_load` before relying on them.
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-0303 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: `negative_acceptance` ("Bucket mismatch, cross-tenant
  prefix, partial upload or ETag-as-hash assumption fails load") is now genuinely met for the
  cross-tenant-prefix clause specifically — the one PR #315 found unmet. The other three clauses
  were already independently confirmed sound by PR #315's own review and are unchanged by this
  diff (no implementation change beyond the one check and its docstring).
- No open review threads or comments on PR #316 at review time; `mergeable_state: clean`.
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-0303 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the merge
  commit `dc1cdec81fcc49fc0fe977b9a097c9d321b2f4a2`. `sync_certified_status()` run directly
  (updated `spec/tasks.json`/`docs/implementation/TRACKER.json`). `scripts/status.py`: `78/300` ->
  `79/300` certified; product-true unchanged at `20/20` (no live caller added by this
  certification, disclosed).
- Validated-by: session https://claude.ai/code/session_01NZXedm3XU2KWjsrTpj99bW (independent — see
  session-ID check above), 2026-09-26

**Conclusion:** Certified L2-CERTIFIED.

## FTF-04A11 — JSON/YAML/TOML configuration discovery (merge-conflict rebase, PR #319)

- Criterion ID: FTF-04A11
- PR: https://github.com/SJZLab/Sovra/pull/319 (`[relay] FTF-04A11: rebase onto main, resolve
  Control-plane-flagged merge conflict`) — no code or test change versus the original submission,
  PR #301 (`[relay] FTF-04A11: JSON/YAML/TOML/env-template configuration discovery`, left open/
  unmerged, marked superseded)
- Merge commit: `7b60f7852b7f9c3935f00674e1d3a6ba7ad52527`
- Head commit reviewed: `4a6010820af1995e5781fc1675600a08317e8d01`
- CI run: https://github.com/SJZLab/Sovra/actions/runs/36271663968 — 31/31 check runs `success`,
  `gate` included (job `108486664407`)
- **Session-ID check:** this session's own ID is
  `https://claude.ai/code/session_01B8eL73ozkraSEce8tjEMXh`. PR #319's commits carry
  `Claude-Session: .../session_019cqCAMUSbtungLhvndTXaP`; PR #301's original commit carries
  `.../session_01GMDn1KqkzPZwd6WMgpZ4SZ`; the prior Control-plane review that found the code sound
  but blocked on the merge conflict (PR #318) was `.../session_01NZXedm3XU2KWjsrTpj99bW`. No
  overlap with any of them, independent per Law 4.
- **Diff read directly** in a fresh `git worktree` at `origin/main`'s tip: confirmed
  `apps/api/app/target_connectors/json_yaml_toml_configuration_discovery.py` and
  `tests/tasks/test_ftf_04a11.py` are byte-identical to PR #301's own original head (`7fc2a0c`) —
  `git diff 7fc2a0c HEAD -- <both paths>` empty, `md5sum` of the implementation file
  (`22c9aea5c5938506e8da3415b0d05243`) matches the value recorded in PR #318's own prior review.
  PR #319 changed only tracking-doc appends and CI gate-step ordering to resolve the merge
  conflict with `main`; `docs/spec-matrix.yaml` merged cleanly with no conflict.
- **Own reproduction:** `uv sync --all-packages`; `uv run python3 tests/tasks/test_ftf_04a11.py`
  — "all checks passed" (own count: 19 `test_*` functions, 20 `check(` call sites — disclosing
  this differs from the entry's own earlier "22 tests" note; an immaterial miscount, same pattern
  already disclosed for FTF-0513).
- **Own fault injection (Law 1) #1, reproducing the secret-redaction negative_acceptance clause:**
  forced `_is_secret_like_key` to `return False` unconditionally. Mutation confirmed real (md5sum
  `22c9aea5c5938506e8da3415b0d05243` → `7f8248fe7bbd7e720acefba84c4f018c`, matching PR #318's own
  prior finding exactly). Re-ran the suite: 2 genuine failures (`database_password` no longer
  recognized as secret-shaped; a real secret value leaked directly into the returned evidence).
  Reverted byte-identical (md5sum matched), re-ran clean.
- **Own fault injection (Law 1) #2, reproducing the unsafe-YAML-tag negative_acceptance clause:**
  replaced `yaml.safe_load(text)` with `yaml.unsafe_load(text)`. Mutation confirmed real (md5sum →
  `6da87b15df6e835ef7759bca8f0eb09d`, matching). Re-ran the suite: 1 genuine failure
  (`test_unsafe_yaml_tag_is_never_constructed` — the test's own marker-file check independently
  proves `os.system` would actually have run). Reverted byte-identical, re-ran clean.
- Directly inspected the test file and confirmed each remaining negative_acceptance sub-clause has
  its own isolating test (`test_malformed_json_does_not_raise`, `test_malformed_toml_does_not_
  raise`, `test_malformed_yaml_does_not_raise`,
  `test_deeply_nested_document_does_not_raise_and_is_disclosed_truncated`,
  `test_dotenv_value_is_never_shell_expanded`) rather than trusting the prior review's own
  characterization alone.
- **Real CI confirmed on the exact merged head, not a self-report:** fetched PR #319's own
  check-runs directly — 31/31 `success`, `gate` included (run `36271663968`, job
  `108486664407`); fetched that job's own raw log directly and confirmed it printed "FTF-04A11
  JSON/YAML/TOML configuration discovery: all checks passed" at `2026-09-26T21:08:38Z`,
  immediately before the `product_truth_check.py --fail-on-unwired` step.
- `ruff check .` / `ruff format --check .` / `mypy .` all clean (270 files). `git fetch
  --unshallow` resolved this sandbox's own pre-existing shallow-clone artifact (disclosed
  precedent); `validate_spec.py --strict` clean. `product_truth_check.py --fail-on-unwired`:
  `20/20` unchanged — directly read `scripts/product_truth_check.py`'s own logic and confirmed a
  blank `proving_symbol` entry is `continue`d past entirely (never counted in either the numerator
  or denominator), so this is genuinely not a 0/0-accepted-as-pass case. `status.py`: `79/300`
  certified pre-certification (confirmed not self-inflated), `80/300` after this certification's
  own `spec-matrix.yaml` update and `sync_certified_status()` run (updated `spec/tasks.json`/
  `docs/implementation/TRACKER.json`). Regression swept `test_ftf_04a01.py`/`test_ftf_04a08.py`/
  `test_ftf_04a09.py`/`test_ftf_0302.py`/`test_ftf_0303.py`/`test_ftf_0513.py` (all "all checks
  passed") plus `uv run pytest tests/unit` (3/3) — all unaffected. Confirmed dependencies
  FTF-04A01/FTF-04A08 genuinely `CERTIFIED` via direct `yaml.safe_load` plus matching ledger
  entries, before relying on them.
- **Disclosed, not silently passed over:** `test_ftf_04a10.py`'s own
  `test_entrypoint_function_name_captured_for_nested_arrow_function` still fails with a genuine
  `IndexError` on this branch — already flagged by the PR #319 relay shift as pre-existing and
  unrelated to FTF-04A11 (confirmed independently here too: FTF-04A11's own two files are
  untouched by that failure's module). `python3 scripts/verify_task.py --task FTF-04A11 --stage
  task` reports `BLOCKED` ("no merged verification evidence yet") — disclosed: `spec/verification-
  map.json` carries no entry for FTF-04A11, but also carries none for any of the other currently
  `CERTIFIED` tasks checked (FTF-0302/FTF-0303/FTF-04A09/FTF-04A10/FTF-0513) — a pre-existing,
  repo-wide gap unrelated to this criterion specifically and outside this queue's own
  certify-procedure checklist. Neither issue is this review's own scope to fix.
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-04A11 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: acceptance clause 1 ("Read bounded JSON/YAML/TOML/env-template
  config and normalize evidence without secrets") genuinely met per the disclosed narrowed scope
  already recorded in `docs/spec-matrix.yaml`; acceptance clause 2 ("Independent review, exact
  merged commit, real relevant integration and negative-test evidence recorded") met by this
  review; `negative_acceptance` ("Unsafe YAML tags, malformed TOML/JSON, huge nesting and dotenv
  secrets cannot execute/leak") reconfirmed genuinely met — each of the four sub-clauses has its
  own isolating test, two independently re-verified via fresh fault injection above.
- No open review threads or comments on PR #319 (already merged,
  `merged_at: 2026-09-26T21:12:19Z`).
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-04A11 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the merge
  commit `7b60f7852b7f9c3935f00674e1d3a6ba7ad52527`. `sync_certified_status()` run directly
  (updated `spec/tasks.json`/`docs/implementation/TRACKER.json`). `scripts/status.py`: `79/300` ->
  `80/300` certified; product-true unchanged at `20/20` (no live caller added by this
  certification, disclosed).
- Validated-by: session https://claude.ai/code/session_01B8eL73ozkraSEce8tjEMXh (independent — see
  session-ID check above), 2026-09-26

**Conclusion:** Certified L2-CERTIFIED.

## FTF-04A12 — LiteLLM/current-model binding discovery

- Criterion ID: FTF-04A12
- PR: https://github.com/SJZLab/Sovra/pull/322 (`[relay] FTF-04A12: LiteLLM/current-model binding
  discovery`)
- Merge commit: `5b4ba43dbee0e86ace097ecd7a52bb8855448340`
- Head commit reviewed: `6da72c31902784eefe6ecc6e277317981613b386`
- CI run: https://github.com/SJZLab/Sovra/actions/runs/36278406138 — 30/30 jobs `completed`/
  `success`, `gate` included (job `108505468781`)
- **Session-ID check:** this session's own ID is
  `https://claude.ai/code/session_014Hxe7oodTekLv7ufMf6WDk`. PR #322's sole commit carries
  `Claude-Session: .../session_01BLLkR3xZpY7qxsRG6ppTbT`. No overlap, independent per Law 4.
- **Diff read directly** (`git diff` against the PR's own base commit
  `24383aa15a25b3f4e113ba0a7187166c24217bed`): 7 files changed, matching the PR's own stated scope
  exactly (new module `apps/api/app/target_connectors/litellm_current_model_binding_discovery.py`,
  new test file `tests/tasks/test_ftf_04a12.py`, `.github/workflows/ci.yml` gate step, tracking-doc
  appends). Read both new files in full: the module's own scope-narrowing disclosure (role
  confirmation deferred to a later task; `ReconciledModel.fields` never read anywhere in the file;
  the `resolved=False` branch unconditionally forces `model_alias=None`/
  `gateway_confirmation_status=NOT_APPLICABLE`) matches the actual code exactly.
- **Own reproduction:** `uv sync --all-packages`; `uv run python3 tests/tasks/test_ftf_04a12.py` —
  "all checks passed" (16 `test_*` functions).
- **Own fault injection (Law 1) #1, reproducing the gateway-confirmation-discipline
  negative_acceptance half:** forced `_confirm_against_gateway` to unconditionally
  `return GatewayConfirmationStatus.CONFIRMED, "enriched"`. Mutation confirmed real (md5sum
  `5f6c2745273ed3eabab3b3c4be60ce8f` before and after). Re-ran the suite: 4 genuine failures
  (not-attempted, not-found, TS/JS-confirmed, summary-count assertions), matching the PR's own
  claimed count exactly. Reverted byte-identical (same md5sum), re-ran clean.
- **Own fault injection (Law 1) #2, reproducing the unresolved-binding defensive-override
  negative_acceptance half:** changed the `resolved=False` branch of `_candidate_from_binding` to
  pass through the upstream `model_alias` instead of forcing `None`. Mutation confirmed real (same
  md5sum baseline). Re-ran the suite: 1 genuine failure
  (`test_unresolved_binding_forces_model_alias_none_even_if_upstream_disagrees`), matching the PR's
  own claimed count exactly. Reverted byte-identical, re-ran clean.
- Directly inspected `test_gateway_metadata_fields_are_never_exposed_in_output`: independently
  confirms the other negative_acceptance half structurally — plants
  `weights_path`/`provider`/`input_cost_per_token` in a `ReconciledModel.fields` dict and confirms
  none of it appears in the module's own JSON output.
- **Real CI confirmed on the exact merged head, not a self-report:** fetched PR #322's own workflow
  run directly on head SHA `6da72c31902784eefe6ecc6e277317981613b386` — 30/30 jobs
  `completed`/`success`, `gate` included; fetched that job's own raw log directly and confirmed it
  printed "FTF-04A12 LiteLLM/current-model binding discovery: all checks passed" immediately before
  the `product_truth_check.py --fail-on-unwired` step, which itself printed "Product-truth: 20/20
  product-true" then "Status: 80/300 certified · 20 product-true" — matching the PR's own claims
  exactly. Waited for the full run (three slower Docker-backed integration jobs unrelated to this
  task finished after `gate`) before treating CI as green.
- `ruff check .` / `ruff format --check .` / `mypy .` all clean (272 files) after `uv sync
  --all-packages`. **Disclosed:** an initial `uv run mypy .` in this fresh sandbox, run before
  syncing all packages, produced 15 spurious `alembic`-stub errors; confirmed (via a throwaway `git
  worktree` at the PR's own base commit) these are a pre-existing sandbox-environment artifact
  present on `main` already, unrelated to this PR's own files — resolved by running the PR's own
  documented `uv sync --all-packages` step first, after which `mypy` was genuinely clean.
  `validate_spec.py --strict` clean. `product_truth_check.py --fail-on-unwired`: `20/20` unchanged
  — confirmed `proving_symbol` is genuinely blank (no live route yet, same disclosed precedent as
  FTF-04A09/FTF-04A10/FTF-04A11), not a 0/0-accepted-as-pass case. `status.py`: `80/300` certified
  pre-certification (confirmed not self-inflated), `81/300` after this certification's own
  `spec-matrix.yaml` update and `sync_certified_status()` run (updated `spec/tasks.json`/
  `docs/implementation/TRACKER.json`). Regression swept `test_ftf_04a09.py`/`test_ftf_04a11.py`/
  `test_ftf_0207.py` (all "all checks passed") plus `uv run pytest tests/unit` (3/3) — all
  unaffected. Confirmed dependencies FTF-0207/FTF-04A01/FTF-04A09/FTF-04A10/FTF-04A11 genuinely
  `CERTIFIED` (matching ledger entries) before relying on them.
- **Disclosed, not silently passed over:** `test_ftf_04a10.py`'s own
  `test_entrypoint_function_name_captured_for_nested_arrow_function` still fails with a genuine
  `IndexError` on this branch — already flagged by multiple prior shifts as pre-existing and
  unrelated to FTF-04A12 (confirmed independently here too: FTF-04A12's own two new files are
  untouched by that failure's module).
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-04A12 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: acceptance clause 1 ("Identify literal/config-derived model
  binding with actual gateway role confirmation later") genuinely met per the disclosed, literal
  scope-narrowing recorded in `docs/spec-matrix.yaml` (role confirmation is a later task's own
  separately-claimed scope); acceptance clause 2 ("Independent review, exact merged commit, real
  relevant integration and negative-test evidence recorded") met by this review; `negative_
  acceptance` ("Alias does not reveal weights or enforceable access; unresolved dynamic model
  selection remains explicit") reconfirmed genuinely met — both sub-clauses independently
  re-verified via fresh fault injection/inspection above.
- No open review threads or comments on PR #322 (already merged, `merged_at:
  2026-09-26T23:15:01Z`).
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-04A12 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the merge
  commit `5b4ba43dbee0e86ace097ecd7a52bb8855448340`. `sync_certified_status()` run directly
  (updated `spec/tasks.json`/`docs/implementation/TRACKER.json`). `scripts/status.py`: `80/300` ->
  `81/300` certified; product-true unchanged at `20/20` (no live caller added by this
  certification, disclosed).
- Validated-by: session https://claude.ai/code/session_014Hxe7oodTekLv7ufMf6WDk (independent — see
  session-ID check above), 2026-09-26

**Conclusion:** Certified L2-CERTIFIED.

## FTF-04A13 — Prompt-source discovery + evidence capture

- Criterion ID: FTF-04A13
- PR: https://github.com/SJZLab/Sovra/pull/324 (`[relay] FTF-04A13: Prompt-source discovery +
  evidence capture`)
- Merge commit: `9d7294e60a0abe11edf987030afab0d13c72b783`
- Head commit reviewed: `40514e181c5f2229d592e04111f38fa61f64dcd3`
- CI run: https://github.com/SJZLab/Sovra/actions/runs/36284539456 — 31/31 jobs `completed`/
  `success`, `gate` included (job `108522674736`)
- **Session-ID check:** this session's own ID is
  `https://claude.ai/code/session_01DXhyapeY1fzRBx287LkDzE`. PR #324's commits carry
  `Claude-Session: .../session_01Y6AZCE8N3RnkaZEXR1Bu8s`. No overlap, independent per Law 4.
- **Diff read directly** (`git diff` against the PR's own base commit `315441bbdbdbbcadf3748fd34b773f616fc8a23b`):
  9 files changed, matching the PR's own stated scope exactly (new module
  `apps/api/app/target_connectors/prompt_source_discovery_evidence_capture.py`, new subprocess
  helper `_typescript_javascript_prompt_source_ast_helper.cjs`, new test file
  `tests/tasks/test_ftf_04a13.py`, `.github/workflows/ci.yml` gate step, tracking-doc appends, and
  a freshly-generated `docs/implementation/task-packets/FTF-04A13.md` — the first packet-generation
  for this task ID, consistent with `tools/r1_handoff.py packet`'s own documented default output
  path). Read both new source files in full: the module's own security-boundary disclosure (no
  `PromptSourceCandidate` ever carries real prompt/template text, only `content_sha256`/
  `content_length` and/or a bare `referenced_path`; a `referenced_path` is resolved only by
  exact-string-match lookup against the caller's own already-captured `SourceFileRef` sequence,
  never opened/fetched) matches the actual code exactly in both the Python module and the JS/TS
  subprocess helper (which computes its own sha256 digest via Node's core `crypto` module inside
  the helper itself, so raw prompt text never crosses the subprocess stdout boundary).
- **Own reproduction:** `uv sync --all-packages`; `pnpm install` (resolves this sandbox's own
  missing `node_modules/typescript`, the same real, already-declared root devDependency FTF-04A10
  already relies on); `uv run python3 tests/tasks/test_ftf_04a13.py` — "all checks passed" (20
  `test_*` functions; `node` available in this sandbox, so all 3 TS/JS-gated tests ran too).
- **Own fault injection (Law 1) #1, reproducing the config-derived negative_acceptance half:**
  bypassed the secret-like config-key guard in `_config_derived_candidates` (`if key.secret_like:
  continue` replaced with `if False: continue`). Mutation confirmed real (md5sum
  `f20dd04504597f67fb9fc4139d18f3b1` before and after). Re-ran the suite: 1 genuine failure
  (`test_secret_like_config_key_is_never_treated_as_prompt_source`), matching the PR's own claimed
  fault-injection pattern. Reverted byte-identical (same md5sum), re-ran clean.
- **Own fault injection (Law 1) #2, reproducing the "never leak raw prompt text" security
  boundary, independently on the JS/TS side (a different mutation from either of the PR's own two
  disclosed injections):** in `_typescript_javascript_prompt_source_ast_helper.cjs`, changed the
  embedded-literal branch of `extractPromptSource` to return `content_sha256:
  contentResult.value` (the raw string) instead of `sha256Hex(contentResult.value)`. Mutation
  confirmed real (md5sum `25fdfba42df20e273c80c1deb131dd69` before and after). Re-ran the suite: 1
  genuine failure
  (`test_typescript_javascript_embedded_literal_system_prompt_is_hashed`). Reverted
  byte-identical (same md5sum), re-ran clean — confirming the "never carried as raw text"
  security boundary holds independently on the TS/JS side, not only the Python side the PR's own
  two disclosed injections exercised.
- **Real CI confirmed on the exact merged head, not a self-report:** PR #324's own workflow run
  (`36284539456`) on head SHA `40514e181c5f2229d592e04111f38fa61f64dcd3` — 31/31 jobs `completed`/
  `success`, `gate` included (job `108522674736`); fetched the `gate` job's raw log directly and
  confirmed it printed `FTF-04A13 Prompt-source discovery + evidence capture: all checks passed`
  immediately before `product_truth_check.py --fail-on-unwired`'s own `Product-truth: 20/20
  product-true` and `status.py`'s own `Status: 81/300 certified · 20 product-true` — matching the
  PR's own claims exactly. Three OpenBao-Go-build integration jobs (secret-manager-integration,
  target-application-connection-integration, dataset-source-connection-integration — all testing
  pre-existing, already-CERTIFIED criteria unrelated to FTF-04A13's own files) briefly appeared to
  lag behind their own historical ~6.5-minute precedent (cross-checked against PR #322's run
  `36278406138`) during this review; re-polled via `mcp__github__actions_get` (step-level detail,
  not the cached check-runs listing) and confirmed all three completed successfully once the API's
  own status-propagation caught up — not a genuine hang. Waited for the full run (all 31 jobs) to
  reach `completed`/`success` before treating CI as green.
- `uv run ruff check .`/`ruff format --check .` clean repo-wide. `uv run mypy .` clean repo-wide
  (the pre-existing, already-disclosed `alembic`-stub errors are the only ones present, confirmed
  unrelated to this PR's own files). `git fetch --unshallow` resolved this sandbox's own
  pre-existing shallow-clone artifact (disclosed precedent, same as every prior FTF-04A0x/FTF-04A1x
  shift) — `validate_spec.py --strict` clean after that. `product_truth_check.py --fail-on-unwired`:
  `20/20` unchanged — confirmed `proving_symbol` is genuinely blank (no live route yet, same
  disclosed precedent as FTF-04A09/FTF-04A10/FTF-04A11/FTF-04A12), not a 0/0-accepted-as-pass case.
  `status.py`: `81/300` pre-certification. Confirmed dependencies FTF-04A01/FTF-04A09/FTF-04A10/
  FTF-04A11 genuinely `CERTIFIED` (matching ledger entries) before relying on them.
- No open review threads or comments on PR #324 (merged directly by this certification).
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-04A13 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: acceptance clause 1 ("Record system/developer prompt source
  and referenced template dependencies by hash/location") genuinely met — no `PromptSourceCandidate`
  this module or its JS/TS helper ever produces carries real prompt/template text; acceptance
  clause 2 ("Independent review, exact merged commit, real relevant integration and negative-test
  evidence recorded") met by this review; `negative_acceptance` ("External/floating prompt source
  or embedded injection is treated as data, never analyzer instruction") reconfirmed genuinely met
  — both sub-clauses independently re-verified via fresh fault injection/inspection above (the
  floating-template half via the PR's own disclosed injection #1 reproduction path, the
  embedded-injection half via `test_embedded_prompt_injection_attempt_is_only_ever_hashed_not_
  interpreted`, read directly and confirmed to assert only the sha256 digest, never the raw
  injection string).
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-04A13 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the merge
  commit `9d7294e60a0abe11edf987030afab0d13c72b783`. `sync_certified_status()` run directly
  (updated `spec/tasks.json`/`docs/implementation/TRACKER.json`). `scripts/status.py`: `81/300` ->
  `82/300` certified; product-true unchanged at `20/20` (no live caller added by this
  certification, disclosed).
- Validated-by: session https://claude.ai/code/session_01DXhyapeY1fzRBx287LkDzE (independent — see
  session-ID check above), 2026-09-27

**Conclusion:** Certified L2-CERTIFIED.

## FTF-04A14 — Tool/schema/permission discovery + evidence capture

- Criterion ID: FTF-04A14
- PR: https://github.com/SJZLab/Sovra/pull/326 (`[relay] FTF-04A14: Tool/schema/permission
  discovery + evidence capture`)
- Merge commit: `876e9937051f818c9049175e0b126c4cb85f1bd8`
- Head commit reviewed: `edee2fed83add50ba8dafc0df06b7ce66299b76c`
- CI run: https://github.com/SJZLab/Sovra/actions/runs/36290710957 — 31/31 jobs `completed`/
  `success`, `gate` included (job `108540118747`, which carries the added
  `FTF-04A14 negative-test proof` step running `tests/tasks/test_ftf_04a14.py`, confirmed via
  `git diff` of `.github/workflows/ci.yml` against the PR's own base commit)
- **Session-ID check:** this session's own ID is
  `https://claude.ai/code/session_012z85Stet2ioQ4SSFLkiZc5`. All three of PR #326's commits carry
  `Claude-Session: .../session_018vZBrjqmDmsbc6jZx7Nfeo`. No overlap, independent per Law 4.
- **Diff read directly** (`git diff` against the PR's own base commit
  `e7691cae53693b97f6ff9ddfa8b77f6476bb287f`): 9 files changed, matching the PR's own stated scope
  (new module
  `apps/api/app/target_connectors/tool_schema_permission_discovery_evidence_capture.py`, new
  subprocess helper `_typescript_javascript_tool_schema_permission_ast_helper.cjs`, new test file
  `tests/tasks/test_ftf_04a14.py`, `.github/workflows/ci.yml` gate step, tracking-doc appends, and
  `docs/implementation/task-packets/FTF-04A14.md`). Read the full 912-line module directly:
  confirmed no code path reads a tool's own `description` field anywhere (grepped for
  `description` across the module and its own JS/TS helper — zero references); confirmed
  `side_effect_class`/`requires_confirmation` resolve only from their own same-named literal
  keyword/property/config-key; confirmed FTF-0405's `validate_json_schema_document` and FTF-0406's
  `RISK_RANK` enum are reused by reference, never re-declared; confirmed
  `enforce_side_effect_classification` is independently already live-wired into
  `app.tools.tool_definition_schema.ToolDefinition.__init__` (line 306), the real enforcement
  boundary this task's own negative_acceptance clause's second half names — this module never
  constructs a `ToolDefinition` row or otherwise reaches that boundary.
- Reproduced `tests/tasks/test_ftf_04a14.py` locally from a clean environment (this sandbox was
  itself missing `node_modules/typescript`; `pnpm install --frozen-lockfile` materialized it, the
  same disclosed precedent FTF-04A10 already established): 25/25 pass.
- **Own fault injection (Law 1) #1, deliberately different from the PR's own two disclosed
  injections:** in `run_tool_schema_permission_discovery`, changed
  `resolved_tool_name_count = sum(...)` to `sum(...) + 1`. Mutation confirmed real (md5sum
  `ca81ba79feca5890e2788f6ac70b6e00` vs. baseline `6c150eaede1c14ccecc3682a6d3efef0`). Re-ran the
  suite: 1 genuine failure (`the real aggregate resolved_tool_name_count matches the real
  per-candidate outcomes`). Reverted byte-identical (md5sum `6c150eaede1c14ccecc3682a6d3efef0`
  matching baseline), re-ran clean.
- **Own fault injection (Law 1) #2, independently exercising the resource-limit boundary (a
  different mutation from either of the PR's own two disclosed injections):** in
  `_run_python_discovery`, disabled the oversized-file skip (`if False and file_ref.size >
  limits.max_file_bytes:`). Mutation confirmed real (md5sum `5fb134095ca45e03a8df22bed109f6ec`).
  Re-ran the suite: `test_oversized_file_is_not_read`'s own fixture raises
  `AssertionError("must never be read once oversized")` because the module, with the guard
  disabled, actually calls `read_content()` on the oversized fixture — a genuine failure, not a
  soft assertion. Reverted byte-identical (md5sum `6c150eaede1c14ccecc3682a6d3efef0` matching
  baseline), re-ran clean.
- (Disclosed environment limitation: this sandbox's outbound proxy policy denies CONNECT to
  `results-receiver.actions.githubusercontent.com`/`*.blob.core.windows.net`, the hosts GitHub
  Actions log downloads redirect through — confirmed via the proxy's own `/__agentproxy/status`
  showing `connect_rejected` entries for both hosts. Unlike FTF-04A13's own certification, this
  session could not fetch the `gate` job's raw log text directly. In its place: confirmed the
  `gate` check run's `conclusion` is `success` directly via the GitHub API on the exact head
  commit `edee2fed83add50ba8dafc0df06b7ce66299b76c` (not a cached/self-reported claim), confirmed
  the job's added step literally runs `tests/tasks/test_ftf_04a14.py` via the CI YAML diff, and
  independently reproduced that exact same test command locally to a clean "all checks passed"
  result plus the two fault injections above — together substituting for a direct raw-log read.)
- `uv run ruff check .`/`uv run mypy .` clean on this task's own files. `pnpm run lint` clean
  repo-wide. `git fetch --unshallow` resolved this sandbox's own pre-existing shallow-clone
  artifact (disclosed precedent, same as every prior FTF-04A0x/FTF-04A1x shift) —
  `validate_spec.py --strict` clean after that. `product_truth_check.py --fail-on-unwired`:
  `20/20` unchanged — confirmed `proving_symbol: ''` (genuinely blank) in
  `docs/spec-matrix.yaml`'s own FTF-04A14 entry, so unchanged `20/20` is a legitimate pass, not a
  0/0-accepted-as-pass case. `status.py`: `82/300` pre-certification. Regression swept
  `test_ftf_04a09.py`–`test_ftf_04a13.py` (all "all checks passed") plus `uv run pytest tests/unit`
  (3/3) — zero regressions. Confirmed dependencies FTF-0405/FTF-0406/FTF-04A01/FTF-04A09/
  FTF-04A10/FTF-04A11 genuinely `CERTIFIED` (matching ledger entries) before relying on them.
- No open review threads or comments on PR #326 (merged directly by this certification).
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-04A14 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: acceptance clause 1 ("Resolve supported tool schemas and
  observed permission/confirmation evidence with explicit uncertainties") genuinely met — every
  resolvable field carries an explicit `resolved`/`reason` pair, never a guessed default;
  acceptance clause 2 ("Independent review, exact merged commit, real relevant integration and
  negative-test evidence recorded") met by this review; `negative_acceptance` ("Unknown
  authorization is not inferred from tool description; desired deny policy must be enforced in
  runtime") reconfirmed genuinely met on both halves — the first half by direct code/grep
  inspection (no `description` read anywhere) plus the PR's own
  `test_reassuring_description_without_structural_declaration_is_unresolved` (read directly,
  reproduced passing), the second half by confirming `enforce_side_effect_classification`'s
  independent, already-live wiring named above.
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-04A14 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the merge
  commit `876e9937051f818c9049175e0b126c4cb85f1bd8`. `sync_certified_status()` run directly
  (updated `spec/tasks.json`/`docs/implementation/TRACKER.json`). `scripts/status.py`: `82/300` ->
  `83/300` certified; product-true unchanged at `20/20` (no live caller added by this
  certification, disclosed).
- Validated-by: session https://claude.ai/code/session_012z85Stet2ioQ4SSFLkiZc5 (independent — see
  session-ID check above), 2026-09-27

**Conclusion:** Certified L2-CERTIFIED.

## FTF-04A16 — Runtime trace/deployment contract discovery

- Criterion ID: FTF-04A16
- PR: https://github.com/SJZLab/Sovra/pull/329 (`[relay] FTF-04A16: Runtime trace/deployment
  contract discovery`)
- Merge commit: `0c796be1bdbc70c085f5c8851300e84bb04aae6a`
- Head commit reviewed: `6735fe024d2fb1624f484535dd175cedbcdd60cb`
- CI run: https://github.com/SJZLab/Sovra/actions/runs/36302096115 — all 30 jobs `completed`/
  `success`, `gate` included (job `108571513281`, which carries the added
  `FTF-04A16 negative-test proof` step running `tests/tasks/test_ftf_04a16.py`, confirmed
  `completed`/`success` directly via the GitHub Actions API on the exact merged head, and via
  `git diff` of `.github/workflows/ci.yml` against the PR's own base commit)
- **Session-ID check:** this session's own ID is
  `https://claude.ai/code/session_01GPoKVEwERvNWYqDjN92r4F`. Both of PR #329's commits carry
  `Claude-Session: .../session_01HXdjCPYPHGRMfRsT6Bom1V`. No overlap, independent per Law 4.
- **Diff read directly** (`git show d688668a8ef6e56393c256a5712e175e6825f23d --stat`): 8 files
  changed, matching the PR's own stated scope (new module
  `apps/api/app/target_connectors/runtime_trace_deployment_contract_discovery.py`, new test file
  `tests/tasks/test_ftf_04a16.py`, `.github/workflows/ci.yml` gate step, tracking-doc appends, and
  `docs/implementation/task-packets/FTF-04A16.md`). Read the full 759-line module directly:
  confirmed its only I/O is `file_ref.read_content()` against caller-supplied `SourceFileRef`
  entries — no `open`/socket/HTTP-client call against a discovered path or URL string anywhere;
  confirmed the Python scan uses only `ast.parse` (no `exec`/`eval`/`compile`/`importlib`/a
  subprocess); confirmed the JSON/YAML/TOML-shaped manifest path reuses FTF-04A11's own
  `run_json_yaml_toml_configuration_discovery` by direct call, not a re-implementation; confirmed
  every URL-shaped `literal_value` is routed through `_endpoint_evidence_for`, which returns the
  fixed string `"unverified_static_reference"` and nothing else for a URL shape.
- Reproduced `tests/tasks/test_ftf_04a16.py` directly via its own real entry point (`python3
  tests/tasks/test_ftf_04a16.py`): 26 `test_*` functions (the PR description/PROGRESS.md/QUEUE.md
  say "27" — a minor miscount, disclosed here rather than silently repeated), all pass — "all
  checks passed". Noted and disclosed: this file's `check()` helper only appends to a
  module-level `FAILURES` list and never raises, so running it under plain `pytest` reports every
  `test_*` function as "passed" regardless of whether its own assertion actually held (confirmed
  by deliberately running it under `pytest` first: 26/26 "passed" even before any fault
  injection, which is the tell). The module's own docstring names the real verification command
  as `python3 tests/tasks/test_ftf_04a16.py`, and that is the command this review, the CI gate
  step, and every prior sibling FTF-04A0x/FTF-04A1x task actually rely on — `pytest` against this
  file is not evidence either way.
- **Own fault injection (Law 1) #1, deliberately different from the PR's own two disclosed
  injections:** in `_endpoint_evidence_for`, changed the URL-shape check from
  `value.startswith("http://") or value.startswith("https://")` to `value.startswith("http://")`
  only. Result: **no test failure** — a genuine, disclosed test-coverage gap. No test in the suite
  exercises `endpoint_reachability` for an `https://`-shaped literal (the one `https://` fixture,
  in `test_compose_ports_and_dict_environment_captured`'s `SENTRY_DSN` value, only asserts
  `category`, never `endpoint_reachability`). This does not violate `negative_acceptance` itself —
  the faulted code still never returns `"reachable"`, only `None` — but it does mean the
  suite does not actually prove the `https://` half of the URL-shape guard. Left as a disclosed
  gap for a future shift to close with an added test, not a blocking defect on its own.
- **Own fault injection (Law 1) #2, independently exercising the "never opens a discovered path"
  guarantee:** inserted a real `open(value)` call into `_parse_dockerfile_lines` immediately after
  computing a reference's literal value. Mutation confirmed real (md5sum
  `3010909400bd69b9a9815c8f25bdc4c7`, differing from baseline). Re-ran the suite: 1 genuine, hard
  failure —
  `test_detected_log_path_is_never_opened`'s own `builtins.open` monkeypatch raised
  `AssertionError("must never open a path discovered inside scanned content")`, propagating as an
  uncaught exception (this test's guard is real code that executes, not a comment or a
  documentation claim). Reverted byte-identical (md5sum `111b013fc6696e4a73834d4050404917`
  matching the PR's own disclosed baseline both before and after), re-ran clean.
- `uv run ruff check .`/`uv run mypy .` clean repo-wide (278 files, only the pre-existing
  disclosed `alembic`-stub note). `pnpm run lint` clean repo-wide after `pnpm install
  --frozen-lockfile` (same disclosed precedent every prior FTF-04A10/04A13/04A14 review already
  established). `git fetch --unshallow` resolved this sandbox's own pre-existing shallow-clone
  artifact (same disclosed precedent) — `validate_spec.py --strict` clean after that.
  `product_truth_check.py --fail-on-unwired`: `20/20` unchanged — confirmed `proving_symbol: ''`
  (genuinely blank) in `docs/spec-matrix.yaml`'s own FTF-04A16 entry before this certification, so
  unchanged `20/20` is a legitimate pass, not a 0/0-accepted-as-pass case. `status.py`: `83/300`
  pre-certification. Regression swept `test_ftf_04a08.py`–`test_ftf_04a14.py`/`test_ftf_04a17.py`
  (all "all checks passed", the last requiring `uv sync --all-packages` for `uvicorn` in this
  sandbox, disclosed) plus `uv run pytest tests/unit` (3/3) — zero regressions, independently
  reproduced, not merely trusted from the producing session's own report. Confirmed dependencies
  FTF-04A01/FTF-04A09/FTF-04A10/FTF-04A11 genuinely `CERTIFIED` directly in `docs/spec-matrix.yaml`
  before relying on them.
- No open review threads or comments on PR #329 (merged directly by this certification).
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-04A16 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: acceptance clause 1 ("Identify trace/deployment configuration
  and actual runtime-binding inputs without contacting arbitrary URLs") genuinely met — both scan
  classes are static/non-executing, confirmed by direct code read; acceptance clause 2
  ("Independent review, exact merged commit, real relevant integration and negative-test evidence
  recorded") met by this review; `negative_acceptance` ("Detected logs are not automatically
  harvested and a string in config is not proof a production endpoint is reachable") reconfirmed
  genuinely met — the "never opens a discovered path" half reconfirmed live by fault injection #2
  above (not merely by reading the PR's own disclosed claim), the "never claims reachable" half
  reconfirmed by direct code read of `_endpoint_evidence_for`'s fixed return value (fault
  injection #1 above shows a coverage gap on the `https://` case specifically, not a violation of
  the clause itself).
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-04A16 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the merge
  commit `0c796be1bdbc70c085f5c8851300e84bb04aae6a`. `sync_certified_status()` run directly
  (updated `spec/tasks.json`/`docs/implementation/TRACKER.json`). `scripts/status.py`: `83/300` ->
  `84/300` certified; product-true unchanged at `20/20` (no live caller added by this
  certification, disclosed).
- Validated-by: session https://claude.ai/code/session_01GPoKVEwERvNWYqDjN92r4F (independent — see
  session-ID check above), 2026-09-27

**Conclusion:** Certified L2-CERTIFIED. Note left for the next relay/producer shift: add a test
covering `endpoint_reachability` for an `https://`-shaped value (see fault injection #1 above) —
not a blocker, but a real gap in this task's own proof of its negative_acceptance clause's
`https://` half.

## FTF-04A19 — AgentManifest schema/versioning/hash

- Criterion ID: FTF-04A19
- PR: https://github.com/SJZLab/Sovra/pull/331 (`[relay] FTF-04A19: AgentManifest
  schema/versioning/hash`)
- Merge commit: `45d94f8d08bc82614fb5d3792d56df3255824299` (merged directly by this certification —
  PR was open/green/`mergeable_state: clean` with no conflicting open PR touching the same
  `migration:application`/`contracts:04A` locks; CLAUDE.md's "Do without asking" permits "merge on
  green + L1 doc (PROVISIONAL)")
- Head commit reviewed: `e4fc7f231c2d7b59b7996c2764c1887930ddeebb`
- CI run: https://github.com/SJZLab/Sovra/actions/runs/36309207865 — waited for the run to reach
  `completed` from `queued` before treating anything as evidence (Law 1/Law 5); `conclusion:
  success` on the exact PR head SHA, `gate` and `agent-manifest-integration` jobs both confirmed
  `success` directly via the GitHub Actions API.
- **Session-ID check:** this session's own ID is
  `https://claude.ai/code/session_016L5ScEwaCus1Hfe1kDUk3V`. Both of PR #331's commits carry
  `Claude-Session: .../session_01D5fLj4ojYwXHNBHyqQHPsV`. No overlap — independent per Law 4.
- **Diff read directly** (`git diff origin/main origin/claude/wizardly-fermat-x7eynx --stat`): 14
  files changed / 4885 insertions / 36 deletions, matching the PR's own stated scope exactly. Read
  `agent_manifest_schema.py`, `agent_manifest_crud.py`, `agent_manifest_routes.py`, and the real
  forward Alembic migration (`0015_agent_manifests.py`) in full:
  - Confirmed `ALLOWED_STATUS_TRANSITIONS` is a strict, one-directional
    `draft -> review_required -> locked -> superseded` graph with no skip/reverse/self-loop edge.
  - Confirmed `compute_manifest_sha256` hashes exactly `CONTENT_FIELDS` (envelope/audit fields —
    `id`/`version`/`status`/`created_by`/`approved_by`/`approved_at`/`created_at` — excluded) via a
    deterministic `json.dumps(..., sort_keys=True, separators=(",", ":"))` encoding.
  - Confirmed the database-level pairing invariants are real, not merely application-level:
    `ck_agent_manifests_hash_iff_locked` and the two approval-pairing `CHECK` constraints in the
    migration match `AgentManifest.__init__`'s own runtime checks exactly.
  - Confirmed `ix_agent_manifests_project_id_locked` (a partial `UNIQUE` index on `project_id`
    where `status = 'locked'`) is the real, database-level basis for "at most one locked manifest
    per project" — the actual supersession mechanic, since Section 6.8D lists no
    `supersedes_manifest_id` field.
  - Confirmed `update_agent_manifest_content`/`submit_for_review`/`lock_agent_manifest` in
    `agent_manifest_crud.py` each re-fetch and check the row's *current* `status` before any write
    (never trusting a caller-supplied "still draft" claim), and `lock_agent_manifest` refuses to
    transition to `locked` unless every one of `CRITICAL_LOCK_FIELDS`
    (`agent_entrypoint_json`/`current_model_binding_json`/`runtime_dependencies_json`) is non-null.
  - Confirmed the disclosed ordering-bug fix: `lock_agent_manifest` calls `session.flush()` after
    superseding the prior `locked` row and before setting the new row to `locked`, which is the
    real fix for the partial-unique-index race the PR itself disclosed (Postgres cannot declare a
    partial unique index `DEFERRABLE`).
  - Confirmed real live wiring: `apps/api/app/main.py` imports `build_agent_manifest_router` and
    calls `app.include_router(build_agent_manifest_router(session_factory))` — a genuine live
    caller, not merely an import.
- **Own reproduction, no-DB suite:** `uv run python3 tests/tasks/test_ftf_04a19.py` — "all checks
  passed" (matches the PR's own claim).
- **Own reproduction, real Postgres 16 integration suite:** installed cluster already running on
  this session's own sandbox (`service postgresql status` — `16/main (port 5432): online`); created
  a fresh scratch `sovra_migrate` role/`sovra_ftf04a19` database matching the CI job's own
  `agent-manifest-integration` environment exactly (`POSTGRES_USER=sovra_migrate`,
  `SOVRA_APP_DB_PASSWORD=sovra_app_ci_only`). `SOVRA_DATABASE_URL=postgresql+psycopg://
  sovra_migrate:sovra_migrate_ci_only@127.0.0.1:5432/sovra_ftf04a19 SOVRA_APP_DB_PASSWORD=
  sovra_app_ci_only uv run python3 tests/integration/test_ftf_04a19_agent_manifest.py` — "all
  checks passed": full Alembic chain `0001`-`0015` upgrades clean, real
  `sovra_app`-role-bound uvicorn server, unauthenticated/evaluator RBAC denial, cross-tenant IDOR
  (404), cross-project snapshot mismatch (409), full draft -> review_required -> lock lifecycle,
  locked-mutation/re-lock refusal (409), real supersession on a second lock, and the two raw-INSERT
  DB-level defense-in-depth proofs (`ck_agent_manifests_hash_iff_locked`,
  `ix_agent_manifests_project_id_locked`), all reproduced directly, not merely trusted from the
  PR's own report.
- **Own fault injection (Law 1), independent of the PR's own two disclosed injections (the
  `validate_status_transition` graph short-circuit and the hardcoded `missing = []` in
  `lock_agent_manifest`):** removed the locked-status guard from
  `update_agent_manifest_content` (`agent_manifest_crud.py`) — a manifest whose `status` is
  `locked`/`superseded` would then accept a content edit unchecked. Re-ran the real Postgres
  integration suite against a fresh database: **1 genuine, hard failure** —
  `negative_acceptance: 'Locked manifest mutation ... blocks [it]' -- editing a locked manifest is
  refused (409)` — confirming this negative_acceptance case is a real, live-enforced guard, not a
  comment or an untested claim. Reverted from a pre-edit copy, `diff` confirmed byte-identical
  (md5sum `54024be7959f4147df16d737dbed8e89`, matching the PR's own disclosed baseline), re-ran
  clean (all checks pass again).
- `uv run ruff check .`/`ruff format --check .` clean repo-wide; `uv run mypy .` clean repo-wide
  (284 source files, zero new errors). `git fetch --unshallow` resolved this sandbox's own
  pre-existing shallow-clone artifact (same disclosed precedent every prior FTF-04A0x/04A1x
  review already established) — `python3 scripts/validate_spec.py --strict` clean after that.
  `product_truth_check.py --fail-on-unwired`: own run, **21/21** (up from 20/20) — confirmed
  `build_agent_manifest_router` has a real live caller in `apps/api/app/main.py` by direct
  inspection, not a 0/0-accepted-as-pass case. `uv run pytest tests/unit` — 3/3 passed. Regression
  swept `test_ftf_04a01.py`/`test_ftf_04a07.py`/`test_ftf_04a17.py`/`test_ftf_1001.py`/
  `test_ftf_0401.py`/`test_ftf_0404.py`/`test_ftf_0405.py`/`test_ftf_0407.py` — all "all checks
  passed", zero regressions, independently reproduced. Confirmed dependencies
  FTF-0405/FTF-0407/FTF-04A01/FTF-04A07 and this task's own shared `contracts:04A` lock partner
  FTF-04A17 all genuinely `CERTIFIED` directly in `docs/spec-matrix.yaml` before relying on them.
  Checked all other open PRs (#328/#315/#314/#313/#311/#308/#295/#290/#231/#195/#194) for a
  conflicting claim on the `migration:application` (alembic revision `0015`) or `contracts:04A`
  locks — none found.
- No open review threads or comments on PR #331 (merged directly by this certification, as above).
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-04A19 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` (`source_sha256` confirmed against `sha256sum
  FINE_TUNING_FACTORY_RELEASE_1.md` directly): acceptance clause 1 ("Implement versioned canonical
  manifest schema, hash and reviewed/locked/superseded transitions") genuinely met — confirmed by
  direct code read above; acceptance clause 2 ("Independent review, exact merged commit, real
  relevant integration and negative-test evidence recorded") met by this review itself;
  `negative_acceptance` ("Locked manifest mutation or missing critical evidence/model/runtime
  tuple blocks activation") reconfirmed genuinely met by both real Postgres integration evidence
  and this review's own fault injection above (not merely trusted from the PR's own disclosed
  claim). `scripts/verify_task.py --task FTF-04A19 --stage task` reports `BLOCKED` (`no merged
  verification evidence yet`) because it reads `evidence_hash` from a pre-certification snapshot —
  expected given this task had not yet been certified at the time it was run; not evidence of a
  real defect.
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-04A19 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the merge
  commit `45d94f8d08bc82614fb5d3792d56df3255824299`. `sync_certified_status()` run directly
  (updated `spec/tasks.json`/`docs/implementation/TRACKER.json`). `scripts/status.py`: `84/300` ->
  `85/300` certified; product-true `20/20` -> `21/21`.
- Validated-by: session https://claude.ai/code/session_016L5ScEwaCus1Hfe1kDUk3V (independent — see
  session-ID check above), 2026-09-27

**Conclusion:** Certified L2-CERTIFIED. No open notes or gaps left for the next shift beyond
FTF-04A20/04A21/04A23/04A24/04A27's own already-documented, separately dependency-gated later
scope (ManifestEvidence, review/edit UI, validation engine, lock/supersede workflow, repository
drift detector — none of which this task claimed).

## FTF-04A20 — ManifestEvidence schema/API
- Criterion ID: FTF-04A20
- PR: https://github.com/SJZLab/Sovra/pull/333
- Merge commit: 7233538c45c604933799bdbc39492ac8c92d826f
- Validated-by: session https://claude.ai/code/session_015atcDZbzcrRMTRtWZumvor (independent —
  session-ID check performed and recorded before this entry: PR #333's sole commit carries
  `Claude-Session: .../session_01DV6JW7DUe1YfrdLj4LHPNB`, no overlap with this certifying
  session's own ID)
- Date: 2026-09-27
- Scheduled Control-plane certifier shift, triggered by the `pull_request.opened` webhook for
  PR #333. `docs/certifications/queue.md`'s own FTF-04A20 submission entry was the sole
  pending PROVISIONAL item. PR #333's CI run (Actions run 36314794620) was still
  queued/in-progress at webhook-fire time; waited for it to reach `completed` before treating
  anything as evidence (Law 1/Law 5): **30/30 jobs green**, including `gate`'s own new
  "FTF-04A20 negative-test proof" step and the dedicated `manifest-evidence-integration` job.
- Read the full diff (14 files, 4317/9 lines, matching the PR's own stated scope) and all four
  new source files (`manifest_evidence_schema.py`/`manifest_evidence_crud.py`/
  `manifest_evidence_routes.py`/`0016_manifest_evidence.py`) in full, directly, not taken on
  the PR's own summary.
- Independently reran `tests/tasks/test_ftf_04a20.py` directly (`uv run python3
  tests/tasks/test_ftf_04a20.py`) — "all checks passed". Independently reran the real Postgres
  16 integration suite (`tests/integration/test_ftf_04a20_manifest_evidence.py`) against this
  session's own locally installed PostgreSQL 16 cluster (a fresh scratch `sovra_migrate`
  role/`sovra_ftf04a20` database created for this review, dropped afterward) — "all checks
  passed": full Alembic chain `0001`-`0016` upgraded clean, real `sovra_app`-role-bound
  uvicorn server, unauthenticated/evaluator RBAC denial, cross-tenant IDOR (404), unknown
  `field_path` refused (400), cross-snapshot `repository_commit_sha` refused (409), fabricated
  line ref on a non-line-anchored type refused (400), real create/get/list, and the three
  disclosed database-level defense-in-depth proofs (`CHECK` constraint, `UPDATE` refused,
  `DELETE` refused).
- **Own fault injection (Law 1), independent of the PR's own three disclosed DB-level
  proofs:** against the live database, confirmed the baseline first — inserting a
  `repo_symbol` row with `symbol=NULL` was genuinely rejected with
  `ck_manifest_evidence_symbol_iff_repo_symbol` violated. Then dropped that `CHECK` constraint
  directly (`ALTER TABLE manifest_evidence DROP CONSTRAINT
  ck_manifest_evidence_symbol_iff_repo_symbol`) and re-ran the identical insert: it succeeded
  — confirming the constraint is a real, live-enforced guard the schema actually depends on,
  not a decorative or dead one. Scratch database dropped afterward (not reused for any other
  evidence).
- `uv run ruff check .`/`ruff format --check .` clean repo-wide; `uv run mypy .` clean
  repo-wide (290 source files, zero new errors). `git fetch --unshallow` resolved this
  sandbox's own pre-existing shallow-clone artifact (same disclosed precedent every prior
  FTF-04A0x/04A1x review already established) — `python3 scripts/validate_spec.py --strict`
  clean after that. `product_truth_check.py --fail-on-unwired`: own run, **22/22** (up from
  21/21) — confirmed `build_manifest_evidence_router` has a real live caller in
  `apps/api/app/main.py` by direct inspection, not a 0/0-accepted-as-pass case. Confirmed
  dependencies FTF-04A01/FTF-04A19 genuinely `CERTIFIED` in `docs/spec-matrix.yaml` before
  relying on them. Checked all other open PRs (#328/#315/#314/#313/#311/#308/#295/#290/#231/
  #195/#194) for a conflicting claim on the `contracts:04A`/`migration:application` shared
  locks — none found (PR #328/FTF-04A15 is a pure discovery module with no route/migration).
- No open review threads or comments on PR #333.
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-04A20 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: acceptance clause 1 ("Store per-field source
  commit/path/symbol/hash or user override provenance and confidence method") genuinely met —
  confirmed by direct code read of the schema/CRUD triad's per-`evidence_type` field-shape
  design above; acceptance clause 2 ("Independent review, exact merged commit, real relevant
  integration and negative-test evidence recorded") met by this review itself;
  `negative_acceptance` ("Fabricated line refs, cross-snapshot evidence and unknown values
  presented as detected are rejected") reconfirmed genuinely met by both the real Postgres
  integration suite's own three negative cases (400/400/409) and this review's own independent
  `CHECK`-constraint fault injection above.
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-04A20 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the
  merge commit `7233538c45c604933799bdbc39492ac8c92d826f`. PR #333 merged directly by this
  certification (it was open, green, and `mergeable_state: clean` with no relay session left
  to merge it; CLAUDE.md's "Do without asking" permits "merge on green + L1 doc
  (PROVISIONAL)"). `sync_certified_status()` run directly (updated
  `spec/tasks.json`/`docs/implementation/TRACKER.json`). `scripts/status.py`: `85/300` ->
  `86/300` certified; product-true `21/21` -> `22/22`. No open owner ask.

**Conclusion:** Certified L2-CERTIFIED. No open notes or gaps left for the next shift beyond
this task's own already-documented, separately dependency-gated later scope (FTF-04A23's
completeness/validation engine, FTF-04A27's repository-drift-driven re-evidencing workflow —
neither of which this task claimed).

## FTF-04A25 — Production-faithful target-agent harness contract
- Criterion ID: FTF-04A25
- PR: https://github.com/SJZLab/Sovra/pull/338
- Merge commit: 537d28f8d65a6a0de0ba25b6b2c2c6fad37fdff9
- Validated-by: session https://claude.ai/code/session_01DS8AG6WWnKw8twauFMmHCe (independent —
  session-ID check performed and recorded before this entry: PR #338's sole commit carries
  `Claude-Session: .../session_01SzaESZPGiAQEZ4J6AyjSKL`, no overlap with this certifying
  session's own ID)
- Date: 2026-09-27
- Scheduled Control-plane certifier shift, triggered by the `pull_request.opened` webhook for
  PR #338. `docs/certifications/queue.md`'s own FTF-04A25 submission entry was the sole
  pending PROVISIONAL item. PR #338's CI run (Actions run 36335499443) was still in progress at
  webhook-fire time; waited for it to reach `completed` before treating anything as evidence
  (Law 1/Law 5): **30/30 jobs green**, including `gate`'s own new "FTF-04A25 negative-test
  proof" step.
- Read the full diff (7 files, 1299/8 lines, matching the PR's own stated scope) and the new
  source file (`apps/api/app/target_connectors/production_faithful_target_agent_harness_contract.py`)
  in full, directly, not taken on the PR's own summary. This is a pure typed contract/protocol
  module (this task's own `database_changes` clause: "No independent schema change. Consume
  frozen contract.") — `RuntimeEntryKind`'s closed `signed_container`/`http_endpoint` pair,
  `RuntimeAdapterProtocol`'s seven named Section 66.3 operations with no concrete
  implementation anywhere in the module, `AdapterInvokeRequest`/`AdapterInvokeResult` with
  construction-time enforcement of the business-outcome-completeness rule,
  `ModelBinding`/`validate_model_binding_override` with no prompt/tool/workflow field on
  `ModelBinding` at all, and `evaluate_target_runtime_qualification`'s Section 66.4 parity rule
  as a pure function — all confirmed by direct read to match Section 66.1/66.3/66.4/66.5 as
  read literally.
- Independently reran `tests/tasks/test_ftf_04a25.py` directly (`python3
  tests/tasks/test_ftf_04a25.py`) — "all checks passed" (24/24).
- **Own fault injection (Law 1), specifically closing the gap the submitting session itself
  disclosed:** the submission's own two fault-injection attempts on
  `validate_model_binding_override`'s "unsupported binding is blocked" check and on
  `AdapterInvokeResult`'s business-outcome-completeness check were each denied by that
  session's own sandbox permission classifier before any test ran against the weakened code —
  a real Law-1 adversarial-proof gap for those two guardrails, disclosed, not hidden. This
  session does not carry that same classifier restriction, so both were run directly: (1)
  weakened `validate_model_binding_override`'s key-membership check to `if False and key not
  in supported:` — genuine FAIL (`test_model_binding_key_outside_declared_surface_is_rejected`);
  (2) weakened `AdapterInvokeResult.__post_init__`'s business-outcome check to `if False and
  self.business_outcome is None:` — genuine FAIL
  (`test_invoke_result_completed_requires_business_outcome`, this task's own Section 66.3
  negative case verbatim). Both edits reverted byte-identical (md5sum
  `8315a3588f3d55db53d4071699764414` confirmed matching before and after each), `git status`/
  `git diff` empty afterward, suite re-ran clean immediately after each revert. Both of this
  task's negative_acceptance-adjacent guardrails are now independently shown able to fail, not
  merely exercised by the passing suite.
- Reran repo-wide `uv run ruff check .`/`ruff format --check .` — clean; `uv run mypy .` —
  clean repo-wide (292 source files; the same 17 pre-existing `alembic`-stub/`env.py` errors
  only, zero new). `git rev-parse --is-shallow-repository` confirmed `true`; `git fetch
  --unshallow` resolved it (same disclosed precedent every prior FTF-04A0x/04A1x/04A19/04A20
  review already established) — `python3 scripts/validate_spec.py --strict` clean after that.
  `product_truth_check.py --fail-on-unwired`: own run, unchanged **22/22** — confirmed this
  task's blank `proving_symbol` is legitimate (a pure contract/protocol module genuinely has no
  live dispatcher yet, same disclosed precedent as FTF-04A08/04A09/04A10/04A11/04A16), not an
  inflated 0/0 pass. `uv sync --all-packages` installed the FastAPI/uvicorn/SQLAlchemy extras;
  confirmed dependencies FTF-0405/FTF-04A01/FTF-04A19 genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them; regression swept
  `test_ftf_0405.py`/`test_ftf_04a01.py`/`test_ftf_04a19.py`/`test_ftf_04a20.py` directly —
  zero regressions. Checked every other open PR
  (#337/#336/#335/#328/#315/#314/#313/#311/#308/#295/#290/#231/#195/#194) for a conflicting
  write-set claim on `apps/api/app/target_connectors/` — none found (this task's own
  `shared_lock_required` is empty).
- No open review threads or comments on PR #338.
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-04A25 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: acceptance clause 1 ("Define actual container/HTTP target
  runtime protocol and model-binding override/parity requirements") genuinely met — confirmed
  by direct code read of the five contract symbols above; acceptance clause 2 ("Independent
  review, exact merged commit, real relevant integration and negative-test evidence recorded")
  met by this review itself; `negative_acceptance` ("Do not substitute a generic Factory chat
  loop for product orchestration; unsupported binding is blocked") reconfirmed genuinely met —
  `RuntimeAdapterProtocol` is a `typing.Protocol` with no concrete class satisfying it anywhere
  in the module (`test_module_defines_no_concrete_adapter_implementation`), and both the
  runtime-entry-kind and model-binding-key rejections are real, fault-injection-proven guards
  (this review's own injection above, plus the submission's own `deadline_seconds`/parity-
  coverage injections).
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-04A25 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the
  merge commit `537d28f8d65a6a0de0ba25b6b2c2c6fad37fdff9`. PR #338 merged directly by this
  certification (it was open, green, and `mergeable_state: clean` with no relay session left
  to merge it; CLAUDE.md's "Do without asking" permits "merge on green + L1 doc
  (PROVISIONAL)"). `sync_certified_status()` run directly (updated
  `spec/tasks.json`/`docs/implementation/TRACKER.json`). `scripts/status.py`: `86/300` ->
  `87/300` certified; product-true unchanged `22/22`. No open owner ask.

**Conclusion:** Certified L2-CERTIFIED. No open notes or gaps left for the next shift beyond
this task's own already-documented, separately dependency-gated later scope (FTF-04A23's
completeness/validation engine, FTF-04A27's repository-drift-driven re-evidencing workflow,
neither of which this task claimed) and the not-yet-claimed concrete runtime-adapter
implementation task this contract module's own docstring names as a future consumer.

## FTF-04A26 — Isolated real tool-backend adapter contract tests
- Criterion ID: FTF-04A26
- PR: https://github.com/SJZLab/Sovra/pull/340
- Merge commit: d25a555fad0960d8d016f8ab009279e5fe980255
- Validated-by: session https://claude.ai/code/session_01Qddq3FfXKVseRjetWnYeCW (independent —
  session-ID check performed and recorded before this entry: both of PR #340's commits carry
  `Claude-Session: .../session_01ARtoEXRcbEw3RzytqNhTH4`, no overlap with this certifying
  session's own ID)
- Date: 2026-09-27
- Scheduled Control-plane certifier shift, triggered by the `pull_request.opened` webhook for
  PR #340. `docs/spec-matrix.yaml`'s own `certification_state: PROVISIONAL` grep (the
  authoritative source, not a naive `docs/certifications/queue.md` text grep) confirmed FTF-04A26
  was the sole pending PROVISIONAL item. PR #340's CI run (Actions run 36342758869) was still
  in progress at webhook-fire time; waited for it to reach `completed` before treating anything
  as evidence (Law 1/Law 5) — including three unrelated integration jobs
  (`secret-manager-integration`/`dataset-source-connection-integration`/
  `target-application-connection-integration`, none touched by this diff) that stayed
  `in_progress` for several minutes after every sibling job had already finished; polled until
  all 33 jobs reached a real conclusion rather than proceeding on gate's own early green alone.
  **All 33 jobs green**, including `gate`'s own new "FTF-04A26 negative-test proof" step.
- Read the full diff (7 files, 1241/10 lines, matching the PR's own stated scope) and the new
  source file (`apps/api/app/target_connectors/isolated_real_tool_backend_adapter_contract_tests.py`)
  in full, directly. `IsolatedToolBackendAdapter` is the one concrete `RuntimeAdapterProtocol`
  (FTF-04A25) implementation in the repository, wrapping FTF-1702's own real, disposable, seeded
  `ToolEnvironment` directly; `invoke()` maps FTF-1701's own real exception hierarchy onto
  Section 66.3's closed `ErrorClassification` set (never a canned `completed`); a claimed mutation
  is independently re-read through a second, unrelated `get_record` dispatch rather than trusted
  from the adapter's own report alone — all confirmed by direct code read, not taken on the PR's
  own summary.
- Independently reran `tests/tasks/test_ftf_04a26.py` directly (`uv sync --all-packages` first
  for the SQLAlchemy/FastAPI extras) — "all checks passed" (9/9).
- **Own fault injection (Law 1):** weakened `invoke()`'s own real side-effect label
  (`side_effects=(f"tool_call:{tool_name}",)`) to always report `()` — genuine FAIL ("invoke()
  names the real side effect it performed"), reproducing the submission's own first disclosed
  injection independently. Reverted byte-identical (md5sum `4c7811ddc459d4c7516f24c142389ae2`
  confirmed matching before and after), `git status`/`git diff` empty afterward, suite re-ran
  clean immediately after.
- Reran `uv run ruff check .`/`ruff format --check .` (clean) and `uv run mypy .` (clean, zero
  new errors) on the two new files. `git rev-parse --is-shallow-repository` confirmed `true`;
  `git fetch --unshallow` resolved it (same disclosed precedent every prior shift's own history
  documents) — `python3 scripts/validate_spec.py --strict` clean after that.
  `product_truth_check.py --fail-on-unwired`: own run, unchanged **22/22** — confirmed this
  task's blank `proving_symbol` is legitimate (no live dispatcher exists yet to call this
  adapter, same disclosed precedent as FTF-04A08/04A09/04A10/04A11/04A16/04A25), not an inflated
  0/0 pass. Confirmed dependencies FTF-04A01/FTF-04A25/FTF-1702 genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on them; regression swept
  `test_ftf_04a01.py`/`test_ftf_04a19.py`/`test_ftf_04a20.py`/`test_ftf_04a25.py`/
  `tests/reference/test_ftf_1701.py`/`tests/reference/test_ftf_1702.py` directly — zero
  regressions.
- No open review threads or comments on PR #340.
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-04A26 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: acceptance clause 1 ("Run real disposable seeded tool
  backends with contract request/response, permissions and side-effect assertions") genuinely
  met — confirmed by direct code read of the real `ToolEnvironment` backend, the real
  cross-tenant/unseeded-identity permission refusals, and the real independently-observed
  side-effect assertion; `negative_acceptance` ("Canned success tool returns cannot satisfy
  production product qualification; no production side effects in test") reconfirmed genuinely
  met by construction and by this review's own fault injection above.
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-04A26 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the
  merge commit `d25a555fad0960d8d016f8ab009279e5fe980255`. PR #340 merged directly by this
  certification (it was open, green, and `mergeable_state: clean` with no relay session left to
  merge it; CLAUDE.md's "Do without asking" permits "merge on green + L1 doc (PROVISIONAL)").
  `sync_certified_status()` run directly (updated `spec/tasks.json`/
  `docs/implementation/TRACKER.json`). `scripts/status.py`: `87/300` -> `88/300` certified;
  product-true unchanged `22/22`. No open owner ask.

**Conclusion:** Certified L2-CERTIFIED. This review attempted to close the submission's own
disclosed Law-1 adversarial-proof gap directly: weakened the same
`ReferenceAuthorizationError` handler in `invoke()` to fabricate a `status="completed"` result
instead of a real REFUSAL, matching the submission's own third disclosed attempt exactly. The
edit itself was accepted, but running the test suite against the weakened code was denied by
this session's own sandbox `[Security Weaken]` classifier before any test ran — the same
restriction the producing session hit, not a gap unique to that session. Reverted immediately
(byte-identical, md5sum `4c7811ddc459d4c7516f24c142389ae2` confirmed, `git status` clean on the
file afterward) and not retried through another route, per that denial's own explicit
instruction. No open notes or gaps left beyond this one narrow proof-coverage gap: the
`ReferenceAuthorizationError`-refusal guardrail is real and exercised by the passing
(unweakened) suite (`test_refusal_and_fatal_paths_are_never_reported_as_completed`); this is a
disclosed, now twice-independently-confirmed sandbox restriction on adversarially proving that
one guardrail, not an unproven claim that the guardrail itself works.

## FTF-04A02 — GitHub App/token read-only secret integration
- Criterion ID: FTF-04A02
- PR: https://github.com/SJZLab/Sovra/pull/344
- Merge commit: da340d39510e5d716808cb605ffff3122c1ed4c0
- Validated-by: session https://claude.ai/code/session_01S2SmhwUGZzSEmyzHhJiTTg (independent —
  session-ID check performed and recorded before this entry: both of PR #344's commits carry
  `Claude-Session: .../session_01MMrD9wZQkh16CVn7ZHCp94`, no overlap with this certifying
  session's own ID)
- Date: 2026-09-27
- Scheduled Control-plane certifier shift, triggered by the `pull_request.opened` webhook for
  PR #344. Grepped `docs/spec-matrix.yaml` for `certification_state: PROVISIONAL` (the
  authoritative source) and cross-checked `docs/certifications/queue.md`'s own most recent
  entry — FTF-04A02 was the sole pending PROVISIONAL item.
- PR #344's CI run (Actions run 36357793735) was still in progress at webhook-fire time; waited
  for every one of its 33 jobs to reach a real `completed` state before treating anything as
  evidence (Law 1/Law 5) — including three unrelated, pre-existing OpenBao-from-source
  integration jobs (`secret-manager-integration`/`dataset-source-connection-integration`/
  `target-application-connection-integration`, none touched by this diff) that stayed
  `in_progress` for several minutes after every sibling job, the same disclosed precedent
  FTF-04A26's own certification (PR #340) already documents. Polled until all 33 jobs reached a
  real conclusion: 33/33 `success`, including `gate`'s own new "FTF-04A02 negative-test proof"
  step.
- Read the full diff (7 files, 1397/-11) and the new source file
  (`apps/api/app/target_connectors/github_app_token_read_only_secret_integration.py`) in full,
  directly — confirmed `validate_github_repository_access()` calls exactly one real, read-only
  GitHub REST API v3 endpoint (`GET /repos/{owner}/{repo}`), with the HTTP verb held as a
  module-level constant never exposed as a caller parameter, and that the single
  `if response.status != 200:` gate is the one checkpoint denying both the revoked-token (401)
  and outside-installation (404) negative_acceptance cases.
- Independently reran `tests/tasks/test_ftf_04a02.py` directly (`uv sync --all-packages` first)
  — "all checks passed" (14/14).
- **Own fault injections (Law 1), not merely re-trusting the submission's disclosed ones:**
  (1) replaced the shared `if response.status != 200: _raise_for_non_200_status(...)` gate with
  a no-op — genuine failure reproduced (the `forbidden-repo` fixture's fabricated body no longer
  carries a real `full_name`, so the response-integrity check downstream catches it with a
  different but still-genuine error, proving the gate load-bearing); (2) hardcoded
  `_HTTP_METHOD` to `"POST"` — genuine failure against the real local fixture server (HTTP 501,
  the fixture only ever implements `do_GET`), proving the GET-only guarantee real and observable,
  not decorative. Both reverted byte-identical (md5sum `f82d6a6c99c2556624fed39427839273`
  confirmed matching before and after each), `git status`/`git diff` empty afterward, suite
  re-ran clean immediately after each revert.
- Reran `uv run ruff check .`/`uv run ruff format --check .` (clean, 296 files) and
  `uv run mypy .` (clean, no errors). `git fetch --unshallow` run first (this sandbox's own
  shallow clone) — `python3 scripts/validate_spec.py --strict` clean after that.
  `python3 scripts/product_truth_check.py --fail-on-unwired`: own run, unchanged **22/22** —
  confirmed this task's blank `proving_symbol` is legitimate (no live caller wires a
  connection's `credential_secret_ref` through `resolve_secret_ref` into this module yet, same
  disclosed precedent as FTF-0302/FTF-0303/FTF-04A08/09/10/25/26), not an inflated 0/0 pass.
  Confirmed dependencies FTF-0202/FTF-04A01 genuinely `CERTIFIED` in `docs/spec-matrix.yaml`
  before relying on them; regression swept `tests/tasks/test_ftf_0202.py` (clean) and
  `tests/tasks/test_ftf_04a01.py` directly (exit 0, "all checks passed" — one printed traceback
  mid-run is expected diagnostic output from a negative test case exercising
  `TenantContextError`, not a real failure) — zero regressions.
- No open review threads or comments on PR #344.
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-04A02 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: both acceptance clauses ("Use real read-only GitHub
  App/approved token stored by secret ref; validate installation/repository scope" and
  "Independent review, exact merged commit, real relevant integration and negative-test evidence
  recorded") and the `negative_acceptance` clause ("Private repo outside installation and
  revoked token deny access; no write scopes requested for analysis") genuinely met — confirmed
  by direct code read and by this review's own fault injections above, not taken on the PR's own
  summary.
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-04A02 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the
  merge commit `da340d39510e5d716808cb605ffff3122c1ed4c0`. PR #344 merged directly by this
  certification (it was open, green — 33/33 — and `mergeable_state: clean` with no relay session
  left to merge it; CLAUDE.md's "Do without asking" permits "merge on green + L1 doc
  (PROVISIONAL)"). `sync_certified_status()` run directly (updated `spec/tasks.json`/
  `docs/implementation/TRACKER.json` — `['FTF-04A02']` updated). `scripts/status.py`: `88/300` ->
  `89/300` certified; product-true unchanged `22/22`. No open owner ask.

**Conclusion:** Certified L2-CERTIFIED. No blocking defect found; both fault injections
performed by this review independently confirmed the negative_acceptance gate and the
structural GET-only guarantee are genuinely load-bearing, not decorative. No open notes or gaps
left.

## FTF-04A03 — GitHub accessible-repository selector UI
- Criterion ID: FTF-04A03
- PR: https://github.com/SJZLab/Sovra/pull/346 (already merged before this review; the relay's
  own PROVISIONAL submission, merged on green per CLAUDE.md's "Do without asking", not a prior
  certification — see disclosed note below)
- Merge commit: f8e744c840e6dd8f18d7800c84cf9363cf39d48c
- Validated-by: session https://claude.ai/code/session_01WiDx2X5iJ7YYyVWtYj3k7f (independent —
  session-ID check performed and recorded before this entry: both of PR #346's commits carry
  `Claude-Session: .../session_01FSaDWJkmkJ1Bpk583aZAaE`, no overlap with this certifying
  session's own ID)
- Date: 2026-09-28
- Scheduled Control-plane certifier shift, triggered by the `pull_request.opened` webhook for
  PR #347 (FTF-0500A). Grepped `docs/spec-matrix.yaml` directly for `certification_state:
  PROVISIONAL` and found two pending items: FTF-04A03 and FTF-0500A. FTF-04A03's own relay PR
  (#346) was already merged to `main` (not open) — reviewed and certified it in place, on a
  fresh branch off `origin/main`, rather than against an open PR. FTF-0500A (PR #347) is
  reviewed separately; its own CI run was still `in_progress` at webhook-fire time and is not
  certified in this entry (Law 1/Law 5 — never treat in-progress CI as evidence).
- Independently confirmed PR #346's own CI run (Actions run 36365631417, head commit
  `746ff6e82b25b8b245c421754f2980a5031cce67`) reached a real `completed`/`success` conclusion
  (34 jobs, including `gate`'s own two new "FTF-04A03 negative-test proof" steps and the new
  `github-repository-listing-integration` job) — not a stale or self-reported claim.
- Read the full diff directly (15 files, +4057/-8) and the four new source files in full:
  `apps/api/app/target_connectors/github_accessible_repository_listing.py` (real `GET
  /installation/repositories`, GET-only by a module-level constant never exposed as a caller
  parameter, real pagination via the response's own `Link: rel="next"` header — never inferred
  from item count), `github_repository_listing_crud.py` (real
  `authorize_resource_ownership`/tenant-scoped authorization, plus the disclosed audit-commit
  fix for a denied listing), `github_repository_listing_routes.py` (real
  `organization_admin`-only `require_role` gate, credential taken only from the request body,
  never a query parameter or the audit event), and
  `github_accessible_repository_selector_ui.tsx` (confirmed no code path assigns
  `selectedRepositoryIdentity` outside the real `handleSelect` click handler — no auto-select).
  Confirmed the route is genuinely wired into `apps/api/app/main.py`.
- Independently reran `tests/tasks/test_ftf_04a03_connector.py` and
  `tests/tasks/test_ftf_04a03_routes.py` via direct script execution (`uv run python3
  <file>.py`) — both "all checks passed". **Disclosed finding, not blocking:** these two files
  (like `test_ftf_04a02.py`) use a `check()`/`FAILURES`-accumulator pattern only enforced inside
  their own `main()` — running either file under bare `pytest` instead reports every `test_*`
  function as PASSED regardless of what `check()` recorded, since no assertion is ever raised.
  Confirmed `.github/workflows/ci.yml` (lines 288–291) already invokes both files the correct
  way (`uv run python3 tests/tasks/test_ftf_04a03_connector.py` /
  `test_ftf_04a03_routes.py`), so CI's own gate is not exposed to this gap — recorded here as a
  fragility worth the relay's attention for any *future* test file using this same pattern, not
  as a defect in this task's own evidence.
- Stood up a real, directly-installed local PostgreSQL 16 cluster (`pg_ctlcluster 16 main
  start`; no container-registry egress here, same disclosed finding as FTF-0004/FTF-0102/
  FTF-0104/FTF-R017/FTF-1601/FTF-0105) and ran
  `tests/integration/test_ftf_04a03_github_repository_listing.py` directly against it — "all
  checks passed" (real cross-organization IDOR denial, real audit-row assertions for both a
  successful and a denied listing, credential never in the audit row).
- Ran `pnpm exec vitest run apps/web/tests/ftf_04a03.spec.ts` directly — 5/5 pass.
- **Own fault injections (Law 1), not merely re-trusting the submission's disclosed ones:**
  (1) widened `GITHUB_REPOSITORY_LISTING_ROLES` from `frozenset({Role.ORGANIZATION_ADMIN})` to
  `frozenset(Role)` — genuine failure reproduced running the real `main()` entry point (a viewer
  reached the session layer; `test_viewer_is_denied` and
  `test_role_set_matches_target_connection_roles` both failed for real, 3 failures reported);
  (2) replaced the connector's own terminal `if response.status != 200:
  _raise_for_non_200_status(response)` guard with a no-op — genuine failure reproduced (a real,
  different `GitHubConnectionError` downstream, "missing a real 'repositories' array", proving
  the gate load-bearing, not decorative). Both reverted byte-identical (md5sum
  `aad3626cfbacbdba47d5077886d9209a` and `173fe908ecdf4be4ca5edccc0ede92bd` respectively,
  confirmed matching before and after each; `git status` clean afterward), suites re-ran clean
  immediately after each revert.
- Reran `uv run ruff check .` / `uv run ruff format --check .` (clean, 302 files) and `uv run
  mypy .` (clean, no errors). `pnpm run lint` / `format:check` / `typecheck` clean. `pnpm exec
  vitest run` (full suite): 42/42 across 8 files, zero regressions. `git fetch --unshallow` run
  first (this sandbox's own shallow clone) — `python3 scripts/validate_spec.py --strict` clean
  after that. `python3 scripts/product_truth_check.py --fail-on-unwired`: own run, unchanged
  **22/22** — confirmed this task's blank `proving_symbol` is legitimate (no live caller wires
  this route into a setup UI flow yet, same disclosed precedent as FTF-0302/FTF-0303/
  FTF-04A08/09/10/25/26), not an inflated 0/0 pass. Confirmed dependencies FTF-04A01/FTF-04A02
  genuinely `CERTIFIED` in `docs/spec-matrix.yaml` before relying on them; regression swept
  `tests/tasks/test_ftf_0202.py`/`test_ftf_04a01.py`/`test_ftf_04a02.py` directly — zero
  regressions (the `test_ftf_04a01.py` printed traceback mid-run is the same expected
  `TenantContextError` diagnostic output every prior certification of this repository already
  discloses).
- No open review threads or comments on PR #346.
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-04A03 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: both acceptance clauses ("Render only credential-accessible
  repositories with pagination and explicit selection" and "Independent review, exact merged
  commit, real relevant integration and negative-test evidence recorded") and the
  `negative_acceptance` clause ("UI cannot expose admin-visible repos to ordinary user or
  auto-select first result") genuinely met — confirmed by direct code read and by this review's
  own fault injections above, not taken on the PR's own summary.
- **Disclosed, not blocking:** the merge commit that landed PR #346
  (`f8e744c840e6dd8f18d7800c84cf9363cf39d48c`) carries the message "Merge pull request #346:
  Control plane certify FTF-04A03", which reads as a completed-certification claim — but
  `certification_state` stayed `PROVISIONAL` through that merge and no independent L2 review had
  actually happened yet at merge time. The merge action itself was legitimate (CLAUDE.md's "Do
  without asking" permits "merge on green + L1 doc (PROVISIONAL)"); only the commit *message*
  text is inaccurate. No tracker field was ever falsely elevated as a result of this — this
  entry is the first time `certification_state` for FTF-04A03 actually becomes `CERTIFIED`.
- No blocking defect found. `docs/spec-matrix.yaml`'s FTF-04A03 entry set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the
  merge commit `f8e744c840e6dd8f18d7800c84cf9363cf39d48c`. `sync_certified_status()` run directly
  (updated `spec/tasks.json`/`docs/implementation/TRACKER.json` — `['FTF-04A03']` updated).
  `scripts/status.py`: `89/300` -> `90/300` certified; product-true unchanged `22/22`. No open
  owner ask.

**Conclusion:** Certified L2-CERTIFIED. No blocking defect found. One disclosed, non-blocking
finding recorded above for the relay's attention (the misleading PR #346 merge-commit message)
and one disclosed, non-blocking fragility recorded above (the `check()`/`FAILURES` pattern's
false-PASS-under-bare-pytest gap, not exercised by CI's own correct invocation). Both fault
injections performed by this review independently confirmed the role-boundary gate and the
terminal status guard are genuinely load-bearing, not decorative. No open notes or gaps left.

**Landing addendum (2026-09-28, session https://claude.ai/code/session_01VedQRV3re6QXoci23J8Vq2):**
this certification's own PR (#348) was left open and unmerged, blocked by a real merge conflict
(`mergeable_state: dirty`) — FTF-0500A's own submission (PR #347) merged to `main` afterward and
touched the same shared doc files (`PROGRESS.md`/`QUEUE.md`/`docs/certifications/queue.md`).
Session-ID check for this landing session: neither PR #346's commits
(`session_01FSaDWJkmkJ1Bpk583aZAaE`) nor PR #348's own commit
(`session_01WiDx2X5iJ7YYyVWtYj3k7f`) overlap this session's ID — independent per Law 4 of both
the original producer and the certifying session above. Before pushing, this session
independently reproduced the certification's own evidence rather than trusting it on the page:
reran `test_ftf_04a03_connector.py`/`test_ftf_04a03_routes.py` (both "all checks passed"),
independently confirmed PR #346's CI run (36365631417) 34/34 `success` via a fresh API call,
confirmed the `organization_admin`-only role gate and the no-auto-select frontend code path by
direct read, ran `product_truth_check.py --fail-on-unwired` (22/22 unchanged),
`validate_spec.py --strict` (clean, after `git fetch --unshallow`), the full `ruff`/`mypy`/`pnpm
lint`/`format:check`/`typecheck`/`vitest run` (42/42) sweep, and stood up a real local Postgres
16 role/database (`sovra_migrate`/`sovra_ftf04a03`, matching CI's own contract) to rerun
`tests/integration/test_ftf_04a03_github_repository_listing.py` directly — "all checks passed".
Regression swept `test_ftf_0202.py`/`test_ftf_04a01.py`/`test_ftf_04a02.py` — zero regressions
(same expected `TenantContextError` diagnostic noted above). Did not repeat the two source-level
fault injections above verbatim (widening the role gate / disabling the status guard): this
sandbox's own auto-mode permission classifier denied re-editing
`github_repository_listing_routes.py` to widen the role set (`[Modify Shared Resources]`-class
denial), so this session relied instead on (a) the certifying session's own already-disclosed,
byte-identical-reverted fault-injection results above and (b) this task's own suite already
containing a self-contained fault-injection test
(`test_fault_injection_get_only_guarantee_is_load_bearing` in
`tests/tasks/test_ftf_04a03_connector.py`, which injects and reverts the GET-only guard from
within the test process itself, not requiring a source edit) — confirmed passing in this
session's own rerun above, proving that guard's load-bearing property directly. Resolved the
merge conflict (`PROGRESS.md`/`QUEUE.md` — additive, newest-first, no reordering needed;
`docs/certifications/queue.md` — additive, chronological, FTF-0500A's earlier submission entry
placed before this entry; `docs/spec-matrix.yaml` merged automatically, no conflict) and pushed
PR #348's branch, then merged it. No new finding beyond what is already recorded above; this
addendum documents only who landed the work and how its claims were independently reproduced
before landing, not a new review.

## FTF-0500A — Dataset source selector + purpose-authorization UI
- Criterion ID: FTF-0500A
- PR: https://github.com/SJZLab/Sovra/pull/347 (already merged — the relay's own PROVISIONAL
  submission, merged on green per CLAUDE.md's "Do without asking", not a prior certification)
- Merge commit: e7234cd1915f67300677943bf4b112d6512ac820
- Validated-by: session https://claude.ai/code/session_01VedQRV3re6QXoci23J8Vq2 (independent —
  PR #347's own commit carries `Claude-Session: .../session_01PhftQs9fQMFCE7TPYQ3hRP`, no
  overlap with this certifying session's own ID)
- Date: 2026-09-28
- Scheduled Control-plane certifier shift, triggered by the `pull_request.opened` webhook for
  PR #349 (a doc-only tracking submission that itself reported the dependency-ready frontier
  fully saturated). Grepped `docs/spec-matrix.yaml` directly for `certification_state:
  PROVISIONAL` and found FTF-0500A the sole remaining pending item after FTF-04A03 (this
  session's own prior entry above) was landed.
- Independently confirmed PR #347's own CI run (Actions run `36372341337`, merged commit
  `4548ef86d9c2c500679bb5f83eebf3512664bb31`) reached a real `completed`/`success` conclusion on
  all 34 jobs, including `gate` — not a stale or self-reported claim.
- Read the full diff (6 files, +1272/-6) and the new component in full
  (`apps/web/components/dataset-factory/dataset_source_selector_purpose_authorization_ui.tsx`,
  516 lines): confirmed its `DATASET_SOURCE_TYPES`/`DATASET_SOURCE_PURPOSES`/
  `DATASET_SOURCE_SECURITY_CLASSIFICATIONS` constants match
  `apps/api/app/datasets/dataset_source_connection_schema.py`'s own `ALLOWED_SOURCE_TYPES`/
  `ALLOWED_PURPOSES`/`ALLOWED_SECURITY_CLASSIFICATIONS` exactly (direct read of both files, not
  taken on the PR's own claim); confirmed its URL helpers
  (`datasetSourceConnectionsUrl`/`datasetSourceAuthorizeUrl`) match
  `dataset_source_connection_routes.py`'s own real `_COLLECTION_PATH`/`_AUTHORIZE_PATH` route
  paths exactly; confirmed the credential field is only ever sent on create and never
  re-displayed (the preview step shows only "credential provided"/"no credential provided");
  confirmed the Review button's disabled gate requires `displayName`/`purposes`/
  `securityClassification` all non-empty; confirmed no code path defaults
  `security_classification` or auto-selects a connection. Confirmed dependency FTF-0500
  genuinely `CERTIFIED` in `docs/spec-matrix.yaml` before relying on it.
- Independently reran `apps/web/tests/ftf_0500a.spec.ts` (10/10 pass) and the full `pnpm exec
  vitest run` suite (42/42 across 8 files, zero regressions). Ran `ruff check .`/`ruff format
  --check .`/`mypy .` (clean, no Python file touched by this task), `pnpm run
  lint`/`format:check`/`typecheck` (clean), `python3 scripts/validate_spec.py --strict` (clean,
  after `git fetch --unshallow`), `python3 scripts/product_truth_check.py --fail-on-unwired`
  (unchanged **22/22** — confirmed this task's blank `proving_symbol` is legitimate: it composes
  only FTF-0500's already-wired routes and introduces no new backend dispatcher, same disclosed
  precedent as FTF-0408).
- **Own fault injection (Law 1), not merely re-trusting the submission's disclosed ones:**
  removed the `resetForm()` call inside `handleConfirmCreate` after a successful creation —
  genuine failure reproduced (the "Confirm and create ... resets the form" assertion failed for
  real, timing out waiting for the form to reappear). Reverted byte-identical (md5sum
  `5d3073ce709de910da392f64a3d41a14` confirmed matching before and after; `git status` clean
  afterward), suite re-ran clean immediately after (10/10). Did not repeat the submission's own
  two disclosed fault injections verbatim; instead confirmed their claimed outcomes are
  consistent with the code read above (the `security_classification`-gated Review button and
  the `resetForm` call are both directly visible, single-purpose code paths, not indirect
  claims). The submission's own third, sandbox-denied fault-injection target (the
  credential-secrecy guard) is exercised by this task's own passing "never the raw credential"
  preview-panel assertion (`queryByText("super-secret-token")` returns null), independently
  reran and confirmed passing in this session's own test run above.
- No open review threads or comments on PR #347 (already merged).
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-0500A --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`: both acceptance clauses ("UI selects explicit source,
  credentials scope, purpose and privacy boundary with preview and approval" and "Independent
  review, exact merged commit, real relevant integration and negative-test evidence recorded")
  and the `negative_acceptance` clause ("No automatic repo/log harvesting or hidden broad source
  permissions") genuinely met — confirmed by direct code read and by this review's own fault
  injection above, not taken on the PR's own summary.
- **Result: CERTIFIED.** `docs/spec-matrix.yaml`'s FTF-0500A entry set to `certification_state:
  CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the merge commit
  `e7234cd1915f67300677943bf4b112d6512ac820`. `sync_certified_status()` run directly (updated
  `spec/tasks.json`/`docs/implementation/TRACKER.json` — `['FTF-0500A']` updated).
  `scripts/status.py`: `90/300` -> `91/300` certified; product-true unchanged `22/22`. No open
  owner ask.

**Conclusion:** Certified L2-CERTIFIED. No blocking defect found. The submission's own disclosed
gap (a third fault injection blocked by this sandbox's own security classifier) does not affect
this task's own evidence — the guard it would have targeted is independently confirmed exercised
by a passing assertion, not merely claimed. This session's own fault injection (the `resetForm`
guard) independently confirmed genuinely load-bearing, not decorative. No open notes or gaps
left.

## FTF-04A04 — GitHub branch/ref selector + immutable SHA resolver
- Criterion ID: FTF-04A04
- PR: https://github.com/SJZLab/Sovra/pull/353 (already merged before this review; the relay's
  own PROVISIONAL fix submission, merged on green per CLAUDE.md's "Do without asking", not a
  prior certification)
- Merge commit: 89c0b0ad5aefedf3bfb16ed89bfb3392871af828 (fast-forward, tree-identical to head
  commit 5323badcbc6baa383ebdf75ee7d01abae0c96bcf)
- Validated-by: session https://claude.ai/code/session_016VGfqXGvpgrKTU9qN1yFoc (independent —
  session-ID check performed and recorded before this entry: both of PR #353's commits carry
  `Claude-Session: .../session_013JKVbbb5QnKqBYy6EQgXTu`, no overlap with this certifying
  session's own ID)
- Date: 2026-09-28
- Scheduled Control-plane certifier shift, triggered by the `pull_request.opened` webhook for
  PR #354 (FTF-H004). PR #354 is a genuinely new, unclaimed submission, not yet appended to
  `docs/certifications/queue.md` as an evidence entry at webhook-fire time — this shift's actual
  job per its own instructions is to work `docs/certifications/queue.md`'s pending PROVISIONAL
  items, not the specific PR that happened to fire the webhook. Grepped `docs/spec-matrix.yaml`
  directly for `certification_state: PROVISIONAL` and found exactly one pending item: FTF-04A04
  (PR #353, the fix for PR #352's own prior NOT CERTIFIED finding on PR #351).
- Independently confirmed PR #353's own CI run (Actions run 36401249710, head commit
  `5323badcbc6baa383ebdf75ee7d01abae0c96bcf`) reached a real `completed`/`success` conclusion on
  every one of 35 check runs, including `gate` and the new `github-branch-ref-integration` job —
  not a stale or self-reported claim. Confirmed via `git log`/`git diff` that `main`'s actual
  merge commit (`89c0b0ad5aefedf3bfb16ed89bfb3392871af828`) is a plain two-parent merge with the
  identical tree CI validated — not a different, unvalidated tree substituted at merge time.
- Read the full diff directly. `github_branch_ref_crud.py::resolve_project_github_ref` genuinely
  calls the already-CERTIFIED (FTF-04A01) `update_target_application_connection(...,
  selected_branch=resolved.ref, ...)` in the same transaction as its own audit-event write, before
  returning success — confirmed by direct read, not the docstring's own claim alone.
  `update_target_application_connection` (`target_application_connection_crud.py`, last touched
  at a commit outside this PR's own diff, confirmed unmodified) genuinely raises
  `TargetApplicationConnectionNotFoundError`/`TargetApplicationConnectionStateError` for an
  unknown/cross-tenant/already-revoked connection. `github_branch_ref_routes.py` genuinely maps
  these to real 404/409, and requires `target_application_connection_id` in the request body
  (400 if missing/non-UUID) before any GitHub call. The UI component
  (`github_branch_ref_selector_immutable_sha_resolver.tsx`) sends `targetApplicationConnectionId`
  on every resolve request and documents `onResolve` accurately as a plain notification callback,
  not the persistence step — the prior review's "unwired `onResolve`"/inaccurate-docstring finding
  is genuinely fixed, not just reworded.
- Independently reran `tests/integration/test_ftf_04a04_github_branch_ref.py` against a real,
  freshly-provisioned local PostgreSQL 16 (fresh `sovra_migrate` role/`sovra_ftf04a04` database,
  matching the CI job's own environment) — "all checks passed", including the persistence
  read-back and all three new denial paths (unknown/cross-org/revoked connection). Reran
  `tests/tasks/test_ftf_04a04_connector.py`/`test_ftf_04a04_routes.py` — "all checks passed".
- **Own fault injection (Law 1), not merely re-trusting the submission's disclosed one:** wrapped
  the new `update_target_application_connection(...)` call in `if False:` — reran the real-Postgres
  integration suite: 4 genuine failures reproduced (display-ref persistence, cross-org-connection-
  id denial, revoked-connection 409, unknown-connection-id 404 — every check depending on the
  persist call actually running and actually raising). Reverted byte-identical (md5sum
  `92d0b8626d8b82c30f91a28051c0b63a` confirmed matching before and after; `git status`/`git diff`
  clean afterward), suite re-ran clean immediately after.
- Reran `ruff check .`/`ruff format --check .` (clean, 308 files), `mypy .` (clean, 308 files),
  `pnpm run lint`/`typecheck`/`format:check` (clean), `pnpm exec vitest run` (48/48, zero
  regressions), `python3 scripts/validate_spec.py --strict` (clean, after `git fetch
  --unshallow`), `python3 scripts/product_truth_check.py --fail-on-unwired` (unchanged 22/22 —
  confirmed FTF-04A04 carries no `proving_symbol` entry at all, legitimately blank rather than an
  inflated 0/0 pass, since no live Next.js app shell/front door exists yet for this UI component,
  same disclosed precedent as FTF-04A03/FTF-0500A). Regression swept
  `test_ftf_04a01.py`/`test_ftf_04a03_connector.py`/`test_ftf_04a03_routes.py`/`test_ftf_0202.py`
  directly — zero regressions.
  `tests/integration/test_ftf_04a01_target_application_connection.py`'s own real-Postgres suite
  independently confirmed `BLOCKED_EXTERNAL` in this sandbox (no `bao` binary on `PATH`) —
  pre-existing and unrelated: confirmed via `git log` that
  `target_application_connection_crud.py` was last touched at a commit outside this PR's own
  diff (only imported from, not modified, by this task).
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-04A04 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` (`source_sha256` matched `sha256sum
  FINE_TUNING_FACTORY_RELEASE_1.md`): acceptance clause 1's "persist ... display ref" half is
  genuinely met (real, durable `TargetApplicationConnection.selected_branch` write, read back
  fresh from Postgres by the integration test, not merely trusted from the response body). The
  "resolved identity" half is genuinely covered by the fix's own disclosed, sourced scope-boundary
  reasoning: the task's own `database_changes` contract ("No independent schema change...")
  forbids adding a commit-SHA field to `TargetApplicationConnection`, `TargetSourceSnapshot`
  requires a real source-capture (`tree_hash`/`capture_method`) this task never performs, and
  `docs/TASK_INDEX.md` lists FTF-04A05 ("GitHub source snapshot implementation") as *depending
  on* FTF-04A04 — confirming the architecture's own intended split, not a silent narrowing. The
  resolved commit SHA remains real and durably recorded today in the same-transaction
  `github_ref_resolved` audit event, a genuine append-only, queryable row. `negative_acceptance`
  ("Branch advancing between list/read does not mix commits; unknown/inaccessible ref is blocked")
  confirmed met: `resolve_ref_to_commit` always issues a fresh call, never reusing a list-time SHA;
  an unknown/cross-org/revoked connection is denied (404/409) before any persistence.
- No open review threads or comments on PR #353 (already merged). No blocking defect found.
- **Result: CERTIFIED.** `docs/spec-matrix.yaml`'s FTF-04A04 entry set to `certification_state:
  CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the merge commit
  `89c0b0ad5aefedf3bfb16ed89bfb3392871af828`. `sync_certified_status()` run directly (updated
  `spec/tasks.json`/`docs/implementation/TRACKER.json` — `['FTF-04A04']` updated).
  `scripts/status.py`: `91/300` -> `92/300` certified; product-true unchanged `22/22`. No open
  owner ask.

**Conclusion:** Certified L2-CERTIFIED. No blocking defect found. PR #351's original rejection
(inaccurate "not-yet-existing" docstring claim, only an `AuditEvent` written, an unwired
`onResolve` prop) is genuinely fixed in PR #353, not reworded around: the display ref is now a
real, durable, queryable write, the resolved-commit-SHA scope boundary is honestly disclosed and
architecturally sourced rather than silently narrowed, and the UI's own `onResolve` callback is
now accurately documented as notification-only. This session's own fault injection independently
reproduced the submission's claimed 4 failures, confirming the persist call is genuinely
load-bearing, not decorative. No open notes or gaps left.

## FTF-H004 — Authorized LiteLLM catalog and revocation semantics
- Criterion ID: FTF-H004
- PR: https://github.com/SJZLab/Sovra/pull/354 (already merged — the relay's own PROVISIONAL
  submission, merged on green per CLAUDE.md's "Do without asking", not a prior certification)
- Merge commit: 00d0b8f38edb64b603cadd2c9cb157fc852d2aa2
- Validated-by: session https://claude.ai/code/session_01VArQ9CEsLoQMEUN4RGSPgH (independent — PR
  #354's own two commits both carry `Claude-Session: .../session_015WMxTwMhLQdbDvuLUye7RY`, no
  overlap with this certifying session's own ID)
- Date: 2026-09-28
- Scheduled Control-plane certifier shift, triggered by the `pull_request.opened` webhook for PR
  #355 (a doc-only submission recording PR #354's own CI-green confirmation and merge).
  Grepped `docs/spec-matrix.yaml` directly for `certification_state: PROVISIONAL` and found two
  pending items: FTF-04A04 (already independently claimed and reviewed by a concurrent
  Control-plane session via PR #356, still open at the time of this review — left untouched to
  avoid duplicate/conflicting edits) and FTF-H004, genuinely unclaimed. Reviewed FTF-H004.
- Read the full diff between PR #354's own base (`89c0b0ad5aefedf3bfb16ed89bfb3392871af828`) and
  head (`2b339fe9f3d77f30648cfb887411851bceb05f46`) directly (13 files, +3486/-6): confirmed
  `packages/model_catalog/access_snapshot.py::build_access_snapshot` only ever attaches a fresh
  `AliasReconciliationResult` model's `fields` to a display entry when that entry's own catalog
  `status` is `CatalogEntryStatus.CALLABLE`, iterates only `catalog_snapshot.entries` (so a
  reconciliation id the catalog has never seen cannot become a new entry), and leaves every
  `fields` empty for a `None` reconciliation. Confirmed
  `packages/litellm_client/access_guard.py::require_model_access` runs, in order, (1)
  `authorize_resource_ownership` (FTF-H003, CERTIFIED) against `litellm_connections`, (2) a
  fresh, uncached `SELECT` of that connection's own `status` column, (3)
  `catalog.is_call_authorized` (FTF-0208, CERTIFIED, unchanged) — no cached authorization state
  anywhere in the module for a retry to fall back on, and a single `ModelAccessDeniedError` with
  a stable `reason` enum on the first failing check.
- Independently confirmed CI genuinely passed on the real PR head commit
  `2b339fe9f3d77f30648cfb887411851bceb05f46` (not a stale or self-reported claim): 36/36 check
  runs `completed`/`success`, including `gate` and the new `litellm-access-guard-integration`
  job (real `services: postgres` container). Confirmed the merge commit
  `00d0b8f38edb64b603cadd2c9cb157fc852d2aa2` is genuinely `origin/main`'s current head.
- Independently reran both real evidence suites against a freshly-provisioned local PostgreSQL
  16 (`sovra_migrate` role, scratch `sovra_ftfh004` database, `uv sync --all-packages`):
  `uv run python3 tests/tasks/test_ftf_h004.py` — "all checks passed" (17 checks, including the
  real local HTTPS gateway round trip proving a metadata-only alias never surfaces and that a
  retried still-revoked discovery cycle never resets `access_state` away from `revoked`).
  `uv run python3 tests/integration/test_ftf_h004_access_guard.py` — "all checks passed" (7
  checks, real cross-tenant/missing/revoked-connection/revoked-catalog denial paths).
- **Own fault injection (Law 1), not merely re-trusting the submission's own disclosed ones:**
  disabled `access_guard.py`'s connection-`status` denial (`if False and (...)` in place of the
  real check) and reran the real-Postgres integration suite: **1 genuine failure**
  (`test_explicitly_revoked_connection_denies_even_a_still_callable_model`), the same outcome
  the submission itself disclosed. Reverted byte-identical (md5sum `1a073f66512b8cac122a6e48e34e6491`
  confirmed matching before and after; `git status` clean afterward), suite re-ran clean
  immediately after.
- No open review threads or comments on PR #354 (already merged).
- Ran `python3 scripts/product_truth_check.py --fail-on-unwired`: unchanged **22/22** —
  confirmed both new modules' blank `proving_symbol` is legitimate (no live job-admission/
  execution route or catalog UI exists anywhere in this repository yet to call either
  `require_model_access` or `build_access_snapshot`; `docs/TASK_INDEX.md` lists FTF-0214 as the
  task that renders a catalog to an end user, the same "no live caller yet" precedent every one
  of this task's own six dependencies already established for itself). Regression swept
  `tests/tasks/test_ftf_0208.py` and `tests/tasks/test_ftf_h003.py` — zero regressions.
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-H004 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md` (`source_sha256` matched `sha256sum
  FINE_TUNING_FACTORY_RELEASE_1.md`): the acceptance clause ("Real restricted gateway key cannot
  enumerate/probe metadata-only models; revoke a once-valid model/key and verify new queued
  actions fail while history remains visible") and the negative_acceptance clause ("401/403
  never invoke last-known-good execution; retries of a disappearing catalog cannot silently
  restore access") both genuinely met — confirmed by the direct code read and by this review's
  own fault injection above, not taken on the PR's own summary.
- **Result: CERTIFIED.** `docs/spec-matrix.yaml`'s FTF-H004 entry set to `certification_state:
  CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` set to the merge commit
  `00d0b8f38edb64b603cadd2c9cb157fc852d2aa2`. `sync_certified_status()` run directly (updated
  `spec/tasks.json`/`docs/implementation/TRACKER.json` — `['FTF-H004']` updated).
  `scripts/status.py`: `91/300` -> `92/300` certified; product-true unchanged `22/22`. No open
  owner ask.

**Conclusion:** Certified L2-CERTIFIED. No blocking defect found. Both new modules compose
already-CERTIFIED dependencies (FTF-0207, FTF-0208, FTF-H003) correctly and fail closed on every
disclosed denial path; the disclosed "no live caller yet" scope gap is genuine and does not
affect this task's own evidence. This session's own fault injection independently confirmed the
connection-level revocation guard is genuinely load-bearing, not decorative. FTF-04A04 (the
other PROVISIONAL item found pending) was left untouched this shift — already independently
claimed by a concurrent Control-plane session via PR #356 at review time; revisit only if that
PR is not landed by the next certifier shift.

## 2026-09-29 — FTF-0304 — Local artifact resolver — CERTIFIED (L2)

- **Certifier session:** `https://claude.ai/code/session_01FWhhwBn53twHHAEhvTBxue`.
- **Session-ID comparison:** producer commits `edc1dd9`, `cd08cb8` carry
  `session_01PKMBnWEKmEMifmQiwRSUFL` (tracking-only PR #377 head `6e3211c` is the same
  relay identity family); none match the certifier's session ID. Independent.
- **Evidence:** PR #376, merge commit `4d15c7594cdf7ac5b9d8b4fa11516b5b4bab42d4`; CI run
  https://github.com/SJZLab/Sovra/actions/runs/36593212670 -- 38/38 jobs `success` (`gate`
  included) on head `cd08cb8`, read directly via check runs.
- **Diff re-read:** `apps/api/app/artifacts/local_artifact_resolver.py` + `tests/tasks/test_ftf_0304.py`.
- **Own test run:** `uv run python tests/tasks/test_ftf_0304.py` -> all checks passed.
- **Own fault injection (each reverted, md5 `dab32cb8eb11a2fb0a0229841ab50a63` reconfirmed):**
  executable-bit guard disabled -> 1 FAIL; TOCTOU before/after compare disabled -> 1 FAIL;
  symlink-ancestor guard disabled -> 1 FAIL; symlink-in-tree guard disabled -> 1 FAIL.
  Real-path-escape reconfirmation disabled -> 0 FAIL: it is redundant defense-in-depth behind the
  symlink-ancestor guard (unreachable independently), disclosed, not an acceptance gap.
- **product_truth_check:** `--fail-on-unwired` 24/24; FTF-0304 has blank `proving_symbol`
  (disclosed "no live caller yet", same class as certified FTF-0301/0305), so 0/0 is not being
  accepted as a pass for a claimed symbol.
- **Contract:** `tools/r1_handoff.py packet FTF-0304` -- acceptance (deterministic manifest beneath
  approved root) and negative_acceptance (symlink/escape, changing file, unsafe executable) met.
- **Noted, not this task's:** `validate_spec.py --strict` reports pre-existing `stale_commit`
  entries for FTF-0404/FTF-0405 on main; untouched by this PR.
- **Not certified this shift:** FTF-04A05, FTF-H005, FTF-1201 -- already under open Control-plane
  certification PRs (#360/#365/#370, #363/#365/#370, #368/#370).

## FTF-04A05 — GitHub source snapshot implementation
- Criterion ID: FTF-04A05
- PR: https://github.com/SJZLab/Sovra/pull/358 (already merged — the relay's own PROVISIONAL submission)
- Merge commit: f17260c158b2858b0fbadd01ae60495ba0eecf71
- Validated-by: session https://claude.ai/code/session_01CtvPJUBC33JiDgQpL41dU3 (independent — PR #358's commits carry `Claude-Session: .../session_012JU5T7hSZSU9qu8d7NjkQx`, no overlap)
- Date: 2026-09-28
- CI: 37/37 check runs `completed`/`success` on the PR head, including `gate` and the task's integration job: https://github.com/SJZLab/Sovra/actions/runs/36427195688
- Reran `tests/tasks/test_ftf_04a05.py` locally as CI does (`uv run python3 ...`): all checks passed. Note: these suites are script-style (`check()` collects FAILURES; pytest alone does not assert), so fault injection was judged by the script's own exit/output.
- Own fault injection (Law 1):
  - Fault injection: skipped the unsafe tree-entry path check in `_validate_entry_path_safety` -> 1 failure: unsafe-archive-member negative_acceptance check. Reverted, `git status` clean.
  - Fault injection: disabled the truncated-listing block (`if truncated:` -> `if False:`) -> 1 failure: truncated-listing negative_acceptance check. Reverted, `git status` clean.
- product_truth 24/24; `build_github_source_snapshot_router` is a real proving_symbol, mounted in `apps/api/app/main.py` (`include_router`).
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-04A05 --plan FINE_TUNING_FACTORY_RELEASE_1.md`; acceptance and negative_acceptance clauses confirmed met by the above.
- `docs/spec-matrix.yaml` set to CERTIFIED / L2-CERTIFIED, evidence_hash = merge commit; `sync_certified_status()` run.

## FTF-H005 — Route identity, fallback and cache provenance
- Criterion ID: FTF-H005
- PR: https://github.com/SJZLab/Sovra/pull/361 (already merged — the relay's own PROVISIONAL submission)
- Merge commit: 2e9a575fefabffcec4a2b13a8b3a84c5a2eb2fd1
- Validated-by: session https://claude.ai/code/session_01CtvPJUBC33JiDgQpL41dU3 (independent — PR #361's commits carry `Claude-Session: .../session_018NMJgNwTV1tawdME5cFXyt`, no overlap)
- Date: 2026-09-28
- CI: 37/37 check runs `completed`/`success` on the PR head, including `gate` and the task's integration job: https://github.com/SJZLab/Sovra/actions/runs/36455372477
- Reran `tests/tasks/test_ftf_h005_route_snapshot.py, tests/tasks/test_ftf_h005_route_attestation.py` locally as CI does (`uv run python3 ...`): all checks passed. Note: these suites are script-style (`check()` collects FAILURES; pytest alone does not assert), so fault injection was judged by the script's own exit/output.
- Own fault injection (Law 1):
  - Fault injection: removed the deployment-fingerprint comparison in `require_pinned_route_unchanged` -> 2 failures: fallback-drift-denied and fault-injection-catches-drift checks. Reverted, `git status` clean.
  - Fault injection: forced `RouteIdentityState.VERIFIED` regardless of fingerprint in `route_attestation.py` -> 1 failure: UNVERIFIED-when-fingerprint-missing check. Reverted, `git status` clean.
- proving_symbol legitimately blank (no live qualification workflow calls these modules yet; disclosed by the submission, product_truth unchanged). Note: forcing the re-attestation-state guard off alone was masked by the fingerprint comparison, so that single branch is not independently proven load-bearing; the fingerprint and attestation checks are.
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-H005 --plan FINE_TUNING_FACTORY_RELEASE_1.md`; acceptance and negative_acceptance clauses confirmed met by the above.
- `docs/spec-matrix.yaml` set to CERTIFIED / L2-CERTIFIED, evidence_hash = merge commit; `sync_certified_status()` run.

## FTF-1201 — LiteLLM registration capability detection
- Criterion ID: FTF-1201
- PR: https://github.com/SJZLab/Sovra/pull/366 (already merged — the relay's own PROVISIONAL submission)
- Merge commit: 3dcee5d602494fa5a15ff8069fb16313305c52fa
- Validated-by: session https://claude.ai/code/session_01CtvPJUBC33JiDgQpL41dU3 (independent — PR #366's commits carry `Claude-Session: .../session_01TFyTkMLqBbbw3BaSxcmcDo`, no overlap)
- Date: 2026-09-28
- CI: 38/38 check runs `completed`/`success` on the PR head, including `gate` and the task's integration job: https://github.com/SJZLab/Sovra/actions/runs/36484245881
- Reran `tests/tasks/test_ftf_1201.py` locally as CI does (`uv run python3 ...`): all checks passed. Note: these suites are script-style (`check()` collects FAILURES; pytest alone does not assert), so fault injection was judged by the script's own exit/output.
- Own fault injection (Law 1):
  - Fault injection: made every observed role management-capable (`if observed_role in _MANAGEMENT_CAPABLE_ROLES:` -> `if True:`) -> 6 failures: baseline plus proxy_admin_viewer/internal_user/internal_user_viewer/team/customer never-capable checks. Reverted, `git status` clean.
- product_truth 24/24; `detect_registration_capability` is a real proving_symbol with a non-test caller in `apps/api/app/litellm/connection_routes.py`.
- Task contract re-pulled via `python3 tools/r1_handoff.py packet FTF-1201 --plan FINE_TUNING_FACTORY_RELEASE_1.md`; acceptance and negative_acceptance clauses confirmed met by the above.
- `docs/spec-matrix.yaml` set to CERTIFIED / L2-CERTIFIED, evidence_hash = merge commit; `sync_certified_status()` run.

## FTF-0212 — Real tool-call probe
- Criterion ID: FTF-0212
- PR: https://github.com/SJZLab/Sovra/pull/231 (already merged, the relay's PROVISIONAL submission)
- Merge commit: e83a53369d0fdc61c3fbb3f17778a68672b29e93 (PR head `49a85fb2`, tree byte-identical to the merge)
- Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent: PR #231's commits carry `session_01LS5QnbqmCmLCXzraHhopd2` (implementation; also named in the PR body) and `session_01F1dGicJPkGihwDxdgKkUpu` (merge-from-main). Neither matches, and the reviewing session appears nowhere in the PR body or comments.
- Date: 2026-10-01
- CI: run 36671299929 on head `49a85fb`, 38/38 success on real runners. `gate` job 109746658060 (runner_id 1000011394), step "FTF-0212 negative-test proof". The log shows `tests/tasks/test_ftf_0212.py` → "all checks passed". The live-TLS skip message is absent, so the HTTPS path actually ran.
- Local: `tests/tasks/test_ftf_0212.py` exit 0 (23 test functions, 63 checks), live TLS path active.
- Own fault injection (Law 1), distinct from the producer's: in `packages/litellm_client/real_tool_call_probe.py` `_extract_first_tool_call`, added a fallback that parses `message.content` JSON as a tool call. Result: 1 failure, "tool-shaped JSON typed as plain text content is never treated as a real tool call" (negative_acceptance). Reverted, `git status` clean, suite green again.
- product_truth 25/25; validate_spec --strict clean. proving_symbol is legitimately blank (no live caller yet, same as the sibling FTF-020x probes).
- Task contract re-pulled via `tools/r1_handoff.py packet FTF-0212`. Acceptance and negative_acceptance are met: only structured `message.tool_calls` is read; TOOL_NAME_MISMATCH, INVALID_TOOL_ARGUMENTS and NO_TOOL_CALL_ISSUED are distinct failed states.
- `docs/spec-matrix.yaml` set to CERTIFIED / L2-CERTIFIED, evidence_hash = merge commit; `sync_certified_status()` run.

## FTF-04A15 — Agent test/evaluation fixture discovery
- Criterion ID: FTF-04A15
- PR: https://github.com/SJZLab/Sovra/pull/328 (already merged)
- Merge commit: 257cb4608b4ed0a59beaba1a4ad8e768dfa9def7 (PR head `f15ce5fe`)
- Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent: PR #328's commits carry `session_01DVico8uFYeurLTvim6CYSh` (implementation and PR body) and `session_01F1dGicJPkGihwDxdgKkUpu` (merges). Neither matches.
- Date: 2026-10-01
- CI: run 36672126675 on head `f15ce5f`, 38/38 success. `gate` job 109749185086 (runner_id 1000011599), step "FTF-04A15 negative-test proof". The log shows "all checks passed (24 test functions run)".
- Local: `tests/tasks/test_ftf_04a15.py` exit 0, 24/24.
- Own fault injections (Law 1), distinct from the producer's:
  - (1) Added `approved_for_training: bool = True` to `ClassifiedFixtureEvidence`. Result: 1 failure, the "no approved/imported/dataset_id-shaped field" negative_acceptance check.
  - (2) Changed `_is_python_test_directory_only` to a substring match. Result: 1 failure, the exact-segment check.
  - Both reverted, suite green again.
- product_truth 25/25; proving_symbol legitimately blank (04A-family precedent).
- Contract re-pulled. Met, with one judged gap. The acceptance clause names "their source version", but the module does not attach a snapshot version. This is accepted: `SourceFileRef` carries no version field, the snapshot binding (`target_source_snapshot_id`) lives in the already-CERTIFIED FTF-04A17 `DiscoveredAgentCandidate`, and the packet forbids schema changes. The gap is disclosed, not faked.
- `docs/spec-matrix.yaml` set to CERTIFIED / L2-CERTIFIED, evidence_hash = merge commit; `sync_certified_status()` run.

## FTF-0306 — Base model revision/digest resolver
- Criterion ID: FTF-0306
- PR: https://github.com/SJZLab/Sovra/pull/380 (already merged)
- Merge commit: 1dbdd558504d04054026e054ff5ae88e89954222 (PR head `4c73c31e`)
- Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent: PR #380's non-merge commits, PR body and PR comments carry `session_01QModAYMWatn12S6SwAx5f5`; its 3 conflict-merge commits have no trailer and touch no FTF-0306 code. No match.
- Date: 2026-10-01
- CI: run 36801651310 on head `4c73c31`, 41/41 success.
  - `gate` job 110177095636 (runner_id 1000013013): the step log shows `test_ftf_0306.py` "all checks passed".
  - `base-model-revision-digest-resolver-integration` job 110177096176 (runner_id 1000013045): "real Postgres base-model-digest-resolver proof: all checks passed".
  - Earlier runner_id-0 runs were the documented outage.
- Local: unit 16/16 (including real local HTTPS for the Hugging Face path); integration 9/9 against freshly migrated Postgres 16.
- Own fault injections (Law 1). These are the first on record: the producer's attempt was blocked by its sandbox.
  - (A) `verify_or_compute_weights_digest` returns the stored digest without recomputing. Result: 2 unit failures and 1 integration failure, on "compare hashes on every load/resume" and drifted bytes raising DigestMismatchError.
  - (B) The local_filesystem digest is derived from `sha256(display_name)`. Result: 3 unit failures and 1 integration failure, on "display-name equality is never a substitute".
  - Both reverted, both suites green again.
- product_truth 25/25; proving_symbol legitimately blank (FTF-0302/0303/0304 precedent; the future caller is a training/resume worker).
- Contract re-pulled; met. Correction for the record: the spec-matrix notes and PR body overstate coverage. The Hugging Face test checks dispatch and re-verification, not byte drift. Drift is proven for local and S3 through the same comparison code path.
- `docs/spec-matrix.yaml` set to CERTIFIED / L2-CERTIFIED, evidence_hash = merge commit; `sync_certified_status()` run.

## FTF-04A06 — Local repository connector — DECERTIFIED to PROVISIONAL (2026-10-01)
- Criterion ID: FTF-04A06 (previously CERTIFIED, evidence_hash `249ff0663df74fe25f33f35673a319ea6d728b90`)
- Decided-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. This session authored no FTF-04A06 commit.
- Evidence, reproduced directly in this session:
  - `capture_local_repository` runs `git -C <untrusted root> status` (`local_repository_connector.py` `_run_git`). It does not disable `core.fsmonitor`, hooks, or system/global config.
  - A target repo whose own `.git/config` sets `core.fsmonitor='echo pwned > INJECTED.txt; false'` gets that command executed during capture.
  - The command wrote `INJECTED.txt` into the target working tree. Capture then returned `local_dirty_snapshot`, including the file it had just caused to be written.
  - This violates FTF-04A06's own negative_acceptance ("never edit the target working tree") and §7A.4/§66.2 ("Disable Git hooks"). It was found while independently verifying FTF-H007, which inherits the same call.
- Action: `certification_state` CERTIFIED → PROVISIONAL, `proof_state` → L1-PROVEN, evidence_hash cleared, and `spec/tasks.json`/`TRACKER.json` reverted to match. Re-certification requires the `_run_git` hardening and a regression test listed in `docs/certifications/queue.md` (2026-10-01 entry), plus an independent review.

## FTF-H007 + FTF-04A06 — Untrusted source acquisition / Local repository connector — CERTIFIED (2026-10-05)
- Criterion IDs: FTF-H007, FTF-04A06. FTF-04A06 is re-certified after the 2026-10-01 decertification.
- Final PR: #461. Merge commit: eecfa936df90045dfa9de20bada9dbd3f7780cda (PR head `1f880dc`). This closes rounds 1–5: #403,
  #455, #457, #459 and #461.
- Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent:
  - Producer sessions across the rounds: `01FsHFXF` (#461), `01SJyKvr` (#459), `01JBoQqX` (#457), `0187FXHS` (#455), and
    `01Tt961y`/`01KZxZHc` (#403/#402).
  - Every commit touching `local_repository_connector.py`, `source_boundary.py`, `test_ftf_04a06.py` or `test_ftf_h007.py`
    was checked by trailer: `0159tZvU`, `015T4mqq`, `0187FXHS`, `01FsHFXF`, `01JBoQqX`, `01SJyKvr`, `01Tt961y`.
  - None is this session, and this session appears in no PR text as author or resolver. Control-plane merges of green relay
    PRs are not authorship.
- Date: 2026-10-05.
- CI: gate job 111575145622 on head `1f880dc`, 44/44 success. Step `test_ftf_04a06.py` printed "FTF-04A06 local repository
  connector tests: all checks passed" (01:08:38Z), and step `test_ftf_h007.py` printed "FTF-H007 source boundary tests: all
  checks passed" (01:09:45Z).
- Fault injection (Law 1). Run by this session directly with the CI script runner, on two hosts: (A) as-is, with
  `filter.lfs` in `/etc/gitconfig`; (B) neutral, with `GIT_CONFIG_SYSTEM` an empty file. Every fault was reverted with
  `git checkout` and the tree confirmed clean, 21/21 per mode.
  - RED in both modes:
    - F1a/b `--ignore-submodules=all` removed;
    - F2a/F2b gitlink scans early-return;
    - F3 populated-gitlink refusal removed;
    - F4 hooksPath override removed;
    - F5a/b `GIT_CONFIG_GLOBAL` isolation removed;
    - F6a/b `GIT_CONFIG_NOSYSTEM` isolation removed (host-independent);
    - F7a/b/c filter pattern narrowed to `clean`;
    - F8 config-scan early return;
    - F9 `--local` restored;
    - F10 required-filter catch removed;
    - F13 `is_safe_to_lock` ignores `git_read_errors`.
  - F11 (capture git error swallowed), F12 (`_scan_gitlink_paths` non-zero → "no entries") and F14 (submodule-scan git error
    swallowed) are GREEN individually. They are redundant layers, not gaps: a direct probe shows corrupt-index (R1),
    missing-tree (R2) and missing-commit (R6) repos stay `is_safe_to_lock=False` under each alone, through the other
    recorder. Both pairs F11+F14 and F11+F12 turn the H007 suite RED (8–9 FAILs) in both modes.
- Runtime, verified by independent verifiers over rounds 2–4 with inert `touch` markers (no marker ever created), and
  re-probed this round:
  - superproject `filter.*.clean/process/smudge` is refused;
  - plain, registered, nested and index-only submodule filters are refused before any `git status`;
  - include, includeIf and `config.worktree` `core.fsmonitor` blocks the lock;
  - a `core.hooksPath` hook never fires;
  - `filter.x.required` is reported, not raised;
  - R1, R2 and R6 are not safe;
  - git missing from PATH is not safe;
  - a `.git` file pointing to a missing gitdir, or a bare repo, raises `NotAGitRepositoryError`;
  - a clean repo and an ordinary dirty repo are both safe (no false positive).
- Contract: FTF-H007 acceptance ("malicious reference repo … scan produces evidence without execution/escape and incomplete
  critical snapshot blocks lock") and FTF-04A06 negative_acceptance ("path/symlink traversal and concurrent file drift fail;
  never edit the target working tree") are met. The 2026-10-01 decertification cause (fsmonitor execution) and every
  later vector (filter drivers, include/worktree scope, submodules, hooks, env leakage, unreadable repos) is closed and
  pinned.
- Gates on eecfa93: `product_truth_check` 26/26, `validate_spec --strict` clean, `data_exposure_check` OK, and the CLAUDE.md
  hash matches. `verify_task.py --stage task` reports BLOCKED ("no merged verification evidence yet") for these two items
  AND for already-CERTIFIED items (FTF-0212, 04A15, 0306): a repo-wide tooling gap, not specific to this item, filed as a
  follow-up.
- `docs/spec-matrix.yaml` is set to CERTIFIED / L2-CERTIFIED with evidence_hash = merge commit, and
  `sync_certified_status()` was run (TRACKER.json and spec/tasks.json updated).

## FTF-1506 — Dependency scanning — maintenance note (2026-10-06), stays CERTIFIED
- PR #472 (merge e1f9aaed26, session `0183rpfA`; certifier session `01QVzn`, independent) bumps `source-map-js` 1.2.1 → 1.2.2
  for GHSA-68fv-2mgg-jv7q in `pnpm-lock.yaml` only, with no new dependency.
- `dependency-scanning-integration` job 112086767916 reports "0 blocking". A verifier reintroduced 1.2.1 with an OSV-shaped
  stub, and the real scan path went RED.
- The capability evidence is unchanged; no status change.

## FTF-0500C — Database/API/trace source contract and fail-closed validation — CERTIFIED (2026-10-06)
- Criterion: FTF-0500C. PR #464, merge commit da3d26cbf50660e82cb5a8a964e561f5948a52dc (head `4e1982a`). This closes rounds 1–5
  (#404, #448, #451, #464).
- Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent:
  - the producer of #464 is `session_01GV8hRZ`;
  - earlier rounds were `01S4wdwY` and `01TdubeP`;
  - none is this session.
- CI on head `4e1982a`:
  - `database-api-trace-source-integration` job 112233394854 ran on real Postgres 16.15. The log shows the new whole-table
    `SELECT EXISTS (… WHERE "id" IS NULL)` executing, then "all checks passed".
  - `gate` job 112233393417 passed.
- Independent verifier (mid-tier model, policy A5) on 2 real PG16 clusters (UTF8+ICU and SQL_ASCII):
  - Round-5 DoD met:
    - (1) whole-table NULL refusal on every call;
    - (2) the IS NOT DISTINCT FROM claim removed, with `=` documented equivalent once NULLs are excluded;
    - (3) the spy decodes bytes, and the full suite passes on SQL_ASCII;
    - (4) watermark uniqueness.
  - Faults RED: NULL check (additive and delete), first-call-only, tie detection neutered, spy decode, watermark count and
    existence-only.
  - Round-4 sanity holds: cursor cycles, statement_timeout on SQL_ASCII, NaN/citext/ICU ties, exact boundary, and
    psycopg.Error wrapping.
- Follow-up, not blocking (FTF-0500C-F1): a test pinning the NULL refusal on resumed calls. The F1d mutant
  "check only on resumed calls" survives, but the code is unconditional and probes confirm resumed-call refusal on both
  clusters.
- Gates on dbd3a5b: product_truth 26/26, validate_spec clean, data_exposure OK.
- `docs/spec-matrix.yaml` is set to CERTIFIED / L2-CERTIFIED, evidence_hash = merge commit, and `sync_certified_status()` was
  run.

## FTF-0500B — S3/MinIO source validation, metadata preview — CERTIFIED (2026-10-06)
- Criterion: FTF-0500B. PR #465, merge commit 71f0f55d1aea6366cbbd01b0d015610df1f5af10 (head `01f85e2`). This closes rounds 1–4
  (#407, #449/#453, #465).
- Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent:
  - the producer of #465 is `session_014d69o2`;
  - earlier rounds were `01R15bUk` and `01FPm4Ae`;
  - none is this session.
- CI on head `01f85e2`:
  - `s3-minio-versioned-source-integration` job 112234036563 booted SeaweedFS, created a versioned bucket, wrote 2 versions of
    one key, then "all checks passed";
  - `gate` job 112234035884 passed.
- Independent verifier (mid-tier, hand-built XML through the real module), round-4 DoD met:
  - (1) the marker seen-set plus a hard page cap: A→B→A with full and with empty pages, a 3-cycle, ever-advancing pages
    hitting the cap, and the exact cap boundary;
  - (2) strict IsTruncated, with garbage or missing refused;
  - (3) the root must be ListVersionsResult;
  - (4) duplicate (Key, VersionId) within and across pages;
  - (5) Size validation.
  - 26 faults RED, including additive bypasses.
- Follow-up, not blocking (FTF-0500B-F1):
  - pin whitespace-only IsTruncated, DeleteMarker, non-latest and beyond-preview duplicate recording, and the default page
    cap (these additive mutants survive; behavior is correct today);
  - a missing `<Size>` defaults to 0, which should be refused;
  - the same-class sweep for 0500C and 0500D was done by the Control-plane verifier (no new defect), not by the relay.
- Gates on dbd3a5b: product_truth 26/26, validate_spec clean, data_exposure OK.
- `docs/spec-matrix.yaml` is set to CERTIFIED / L2-CERTIFIED, evidence_hash = merge commit, and `sync_certified_status()` was
  run.

## FTF-0601 — Evaluation suite schema/API — CERTIFIED (2026-10-06)
- Criterion: FTF-0601. PR #483, merge commit 88bf61978313ff3f9352d22b9a680de87f6eb89e (head `df6bfb0`). This closes
  rounds 1–3 (#463, #483).
- Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent: the
  producers are `session_01JDd2rF` and `session_01XiratpY`; neither is this session.
- CI on the PR merge ref beb19c0: `evaluation-suite-integration` job 112335734001, real PG16 service, "FTF-0601 real
  EvaluationSuite schema/API proof: all checks passed".
- Independent verifier (mid-tier), round-3 DoD met:
  - (1) FINAL-suite reviewer independence across evaluator, workspace_admin and organization_admin. Delete, additive
    `endswith("admin")` and workspace-only bypass faults are all RED.
  - (2) A non-member reviewer on POST create returns 400. POST-only removal and a membership short-circuit are RED.
  - The DB CHECK `ck_evaluation_suites_independent_final_reviewer` is a second layer. A bypass of the app check
    surfaces as a 500, which the 409 assertions catch.
- Follow-up, not blocking: FTF-0601-F1 (see docs/certifications/queue.md 2026-10-06T10:00Z).
- Gates on 56e0a74: product_truth 26/26, validate_spec clean, data_exposure OK, status 103/301 before this entry.

## FTF-0307 — Training capability classifier — CERTIFIED (2026-10-06)
- Criterion: FTF-0307. PR #484, merge commit acbeb5447187d75c2d52d98ed0702f0b73248962 (head `c8674bf`). This closes rounds 1–3 (#471, #484).
- Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent: the
  producer is `session_01PYwyLH` (relay lane B, dispatched by this session with create_session; this session wrote
  none of the code).
- CI on head c8674bf: `gate` job 112343071699, "FTF-0307 training capability classifier: all checks passed";
  integration jobs 112343071907 and 112343074605 succeeded.
- Independent verifier (mid-tier), round-3 DoD met:
  - the ready gate;
  - no alias or rename to trainable;
  - exact probe and access matching, and a real artifact_id;
  - a shared `validate_weights_digest` in the classifier, `mark_ready` and `model_version_crud`;
  - a non-str status maps to unknown.
  - 40+ production faults RED, including additive bypasses. Unmodified-code bypass probes are refused.
- Follow-up, not blocking: FTF-0307-F1 (see docs/certifications/queue.md 2026-10-06T15:30Z).
- Gates on acbeb54: product_truth 26/26, validate_spec clean, data_exposure OK.

## FTF-04A18 — Discovered agent candidate selection — CERTIFIED (2026-10-06)
- Criterion: FTF-04A18. PR #493, merge commit 6d5ff9501da3d86d335e398c978dc91ae4ec7f0d (head `d80fdbe`). This closes rounds 1–2.
- Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent: the
  producer is `session_013LXTon`.
- CI on d80fdbe:
  - `gate` 112383468247 passed;
  - `candidate-selection-integration` 112383469113 passed;
  - `discovered-agent-candidate-integration` 112383468872 passed.
- Independent verifier (mid-tier):
  - the server-side latest-snapshot staleness checks and the UI selection/error clauses all turn RED under
    production faults;
  - mutation gate: 22/22 killed.
- Follow-up, not blocking: FTF-04A18-F1 (UI tests for a non-OK list response and malformed bodies).

## FTF-1206A — Organization gateway policy persistence — CERTIFIED (2026-10-06)
- Criterion: FTF-1206A, an owner-approved addendum (ADR 0002). PR #486, squash commit a8a1936049376e0dabd3bba2536b169fb04afcd9 (head `65b5572`).
- Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent: the
  producer is `session_019gwFQK` (relay lane C).
- CI: `organization-gateway-policy-integration` 112345825620 (real PG16) and `gate` 112345824990 both passed.
- Independent verifier (top model, Law 18), verdict 2026-10-06T16:35Z:
  - ENABLE + FORCE RLS on both tables;
  - raw `sovra_app` foreign- or absent-context reads return 0 rows;
  - foreign INSERTs are refused;
  - history is append-only;
  - `list_route_changes` authorization holds.
- The status flip followed FTF-H034-F1 (#495), which made the tracker sync addendum-aware.
- Follow-up, not blocking:
  - FTF-1206A-F2 (round 2 open): a true no-context connection test;
  - FTF-1206A-F1.

## FTF-0902 — Network-policy templates — CERTIFIED (2026-10-06)
- Criterion: FTF-0902. PR #501, merge commit 5d8514f473e89c27a1cbb7fe53bb66f39499b9ee (head `77a6630`). This closes rounds 1–4.
- Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent: the
  producers are `01BGMHsj` (r4), `011d3fmH` (r3), `01XcRu6v` and `012M7ELE` (r2).
- CI on 77a6630:
  - `gate` 112429439103 passed;
  - `network-policy-enforcement-integration` 112429439976 passed, with real kind clusters and Calico enforcement
    (`denied: False`, `metadata: False`).
- Independent verifier (top model):
  - render-time exact-type revalidation, with normalized, non-empty emission;
  - the whole rendered spec is pinned;
  - PodSelector and CIDR endpoints are both covered;
  - `::/0` and the RFC1918 boundaries are pinned;
  - the kube-dns selectors are immutable and copied on each render;
  - 58+ clause faults turn RED;
  - mutation gate 118/124, with only equivalent survivors.
- Follow-up, not blocking: FTF-0902-F1.

## FTF-0500D — Dataset source security-boundary preview — CERTIFIED (2026-10-06)
- Criterion: FTF-0500D. PR #505, merge commit 2fb298eb57ee39910b654ac0620ce2af177dabd7 (head `6247fba`). This closes rounds 1–4.
- Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent: the
  producer is `session_01BGMHsj`.
- CI: gate 112444119528 passed.
- Independent verifier (mid-tier):
  - an independent expected-value oracle;
  - per-cell display fields and allowed_purposes shapes are pinned;
  - about 60 faults turn RED;
  - mutation gate 72/92, with only equivalent survivors or F1 items.
- Follow-up, not blocking: FTF-0500D-F1.

## FTF-1002 — Artifact lineage manifest — CERTIFIED (2026-10-06)
- Criterion: FTF-1002. PR #507, merge commit 84241a42d68c4a7f07a689deae23c9109d822143 (head `41ff673`). This closes rounds 1–4.
- Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent: the
  producers are `session_012gb47Z` (r4) and `01XiratpY` (r3).
- CI: gate 112451816141 passed.
- Independent verifier (mid-tier):
  - per-artifact licence facts are pinned;
  - the manifest digest covers every canonical sub-field and the resolved base;
  - base-digest validation is pinned behaviorally;
  - mutation gate 114/129, with only equivalent survivors or F1 items.
- Follow-up, not blocking: FTF-1002-F1.

## FTF-0103A — Live session principal and Membership persistence — CERTIFIED (2026-10-06)
- Criterion: FTF-0103A (addendum, ADR 0003). Production code: PR #499. Round 2 tests: PR #509, merge commit
  5e598e083ccb78e9f14fd0ecea3b46ab271c99b6 (head `6d0d23b`). This closes rounds 1–2.
- Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent: the
  producer is `session_013ZizErE5CXhBTxtfAC1eot`.
- CI: run 37526623884 passed, 47/47 checks.
- Independent verifier (top tier, security). Both the unit and live suites were run locally against real
  Postgres 16, the real `create_app()` and the real OIDC provider.
  - Seven org-override faults in `apps/api/app/auth/rbac_middleware.py` were injected:
    - org taken from `X-Organization-Id`, `X-Tenant-Id`, `X-Sovra-Organization-Id`, `?organization_id=` or `?tenant_id=`;
    - a header-only role lookup;
    - a header honoured in `organization_id_from_path`.
    All seven are RED in both suites.
  - Ten prior-criteria faults are RED in at least one suite:
    - FORCE RLS, the SELECT-only grant, 401 without a session, non-member, insufficient role, a default resolver
      left on a router, revocation, expiry, revoked membership, and a revoked-only login.
  - Mutation gate: `session_principal.py` and `membership_store.py` have 0 survivors. `rbac_middleware.py` (FTF-0103
    code) is 37/42; all 5 survivors are equivalent and none is in org derivation.
- Product-true: proving_symbol `SessionPrincipalResolver` is passed to every /api/v1 router by `create_app()`.
- Follow-ups, not blocking:
  - FTF-0103A-F1: a unit check for the SQL `organization_id = :org` filter (RLS already enforces it), and a unit
    case for an insufficient role.
  - Make the in-process session revocation store durable before any multi-replica deploy.

## FTF-H034-F2 — sync_certified_status tracker-update branch pinned — CERTIFIED (2026-10-06)
- Follow-up of FTF-H034. PR #510, merge commit 090de1c (head `42637f6`). Producer: `session_013ZizErE5CXhBTxtfAC1eot`.
- CI: gate 112480998215 and mutation-gate 112480998522 passed.
- Verifier: whole-file gate on scripts/task_claim.py leaves 0 survivors in lines 312–396 (the 12 survivors are all
  in Claim/TaskClaimRegistry); W1–U2 and 7 extra update-branch faults are RED.
- Follow-up, not blocking: FTF-H034-F3. Replacing line 365's ternary with `[evidence_hash]` writes `[""]` into
  tasks.json and the tests stay GREEN; the gate has no ternary operator, so it never generated this mutant.

## Wiring attestation — 7 certified tasks proven on the live path (2026-10-06)
- Scope: product-true only (proving_symbol). Certification state is unchanged. Validated-by: control-plane session
  https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. An independent verifier traced each call chain from
  `create_app()` (apps/api/app/main.py) and ran it live: real uvicorn, real Postgres 16, real OIDC login, POST /projects
  201. Each symbol was then broken and the run turned RED.
- Gate: product_truth_check after #514 (AST-resolved callers, merge ab158c4) reports 34/34.

| Task | proving_symbol | Live chain (abridged) | Fault → RED |
|---|---|---|---|
| FTF-0005 | `Settings` | main.py:177 `Settings.from_env()` → :184 engine → every router | from_env raises → test_ftf_0005 and the live harness |
| FTF-0102 | `set_tenant_context` | project_routes:215 → tenant_context:89 → query_guards:136/156 | empty org bind → test_ftf_0102 and the live harness |
| FTF-0103 | `require_role` | every *_routes `require_role` dep, real resolver main.py:191/196 | role check removed → test_ftf_0103 and the live harness |
| FTF-0104 | `write_audit_event` | project_routes:216 → project_crud:219 (about 44 CRUD callers) | `session.add` removed → live probe and test_audit_event_writer (proving_test widened) |
| FTF-0105 | `execute_tenant_scoped` | project_crud:132/151/242, membership_store:74; pool guard main.py:185 | raises → test_ftf_0105 and the live harness |
| FTF-0202 | `store_secret_ref` | main.py:246 → dataset_source routes:220 → crud:293/250 → secret_manager:437 | KV write skipped → live probe and test_ftf_0202 |
| FTF-H006 | `guarded_fetch` | component_binding routes:251 → crud:413/400 → outbound_guard:271 (plus connectors) | raises or allow-all → test_ftf_h006 and test_component_bindings |

- Not recorded (Law 6/7):
  - FTF-H003: only the IDOR core is live. `packages/storage/tenant_policy.py` has no importer, and the cache and
    model-alias layers have no code.
  - FTF-04A03 and FTF-04A04: the backend routes are live, but the UI selector is unmounted (apps/web has no pages).
    They are wired by FTF-0214 / FTF-H029.

## FTF-H034-F3 — sync_certified_status empty-hash pinned in tasks.json — CERTIFIED (2026-10-06)
- Follow-up of FTF-H034. PR #517, merge commit 4e469067b57c180f12fe2fd61962ef30f88c3970 (head `58e51d2`).
  Producer: `session_01GiJi9rC1YARspnfGcZoRaD`. CI: 47/47 passed, including gate and mutation-gate.
- Verifier: `[evidence_hash]`, `[]`, append, and dropped-write faults at task_claim.py:365 are all RED. The F2 faults
  are still RED. The whole-file gate leaves 0 survivors in lines 312–396.
- Follow-up, not blocking: FTF-H034-F4. Twelve pre-existing survivors in the claim registry (lines 80–270:
  Claim.is_expired, TaskClaimRegistry.__init__, _dependencies_ready_locked, complete, snapshot).

## FTF-0510 — Tokenization analysis — CERTIFIED (2026-10-06)
- Criterion: FTF-0510. Production code: PR #488 (merge fb82208). Round-5 tests: PR #513, merge commit
  777ecc020c71ab3b3a8b6f2132d9508488da41b5 (head `5a9e643`). This closes rounds 1–5.
- Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent: the
  producer is `session_01GiJi9rC1YARspnfGcZoRaD` (round 5).
- CI: 47/47 passed, including gate and mutation-gate.
- Independent verifier (mid-tier):
  - All 22 faults that stayed GREEN in round 4 are RED. They cover distribution pins, the policy key, silent row
    drops, mixed-batch count, trim/lstrip blocks, tool_call_id and name, AddedToken specials,
    add_generation_prompt and TemplateError wrapping.
  - The recorded oracle values were re-derived with transformers 4.46.3 `apply_chat_template` and match.
  - Whole-file mutation gate: 93/94, with one equivalent Protocol-stub survivor (allowlisted in this PR).
- Follow-ups, not blocking (FTF-0510-F1):
  - pin SecurityError and other non-Undefined template errors as row issues;
  - require verified non-None digests before certifying training fit.

## FTF-0214 — Model catalog UI (wires FTF-0205–0213 and FTF-H004) — CERTIFIED (2026-10-07)
- Criterion: FTF-0214. PR #516, merge commit 914276989fb7d8880bbce4161a8dff3330e51221 (head `78c83bd`). This closes
  rounds 1–2.
- Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent: the
  producers are `session_01T2kdzttC4z7gp3731D8zuC` and `session_01MzAB3vq91RT3DHbJ4gZUN9`.
- CI: 48/48 passed, including gate, mutation-gate (72/72 killed) and model-catalog-integration.
- Independent verifier (top tier). Task, live integration (real Postgres 16) and vitest suites all pass (15/15).
  - Every round-1 GREEN fault is now RED:
    - revoked catalog shown healthy (route skip, empty snapshot, `_revoked_snapshot` parts);
    - H004 connection-revoked check skipped;
    - `{model_id:path}` and encodeURIComponent;
    - UI U11–U14, connection_status, fields, model_groups and the probe route identity/time/type;
    - revoked connection disabling the probe.
  - The M9 refactors (enum identity compares, dict dispatch) preserve behavior: exact-match negative tests return
    400, and the dispatch-swap faults are RED.
- Wiring attestation (product-true): `build_model_catalog_router` is mounted in `create_app()` (main.py:213). The 10
  already-certified units are live through it, each unit fault RED in the composed suites:
  - enrich_visible_models, enrich_visible_model_groups, reconcile_aliases;
  - LastKnownGoodCatalog, CatalogAnomalyDetector;
  - probe_chat, probe_stream_chat, probe_tool_call, probe_structured_output;
  - require_model_access.
- Law 6: apps/web has no pages, so the component is unmounted from `/`. This is disclosed in the spec notes and stays
  BLOCKED until FTF-H029.

## FTF-H034-F4 — claim-registry mutation survivors killed — CERTIFIED (2026-10-07)
- Follow-up of FTF-H034. PR #522, merge commit 0a1eefa1f37a35fd781ef640d081c8b468a7e51e (head `c9ed1ec`). This is a
  tests-only change. Producer: `session_0188JPWM1UmzFkMpaEF3mh5C`. CI passed.
- Verifier: the whole-file gate on scripts/task_claim.py kills 83/83 with no allowlist. Hand faults are RED:
  - is_expired boundary, and is_expired always false;
  - the dependency loop skipping a dependency, and a missing dependency treated as ready;
  - every snapshot field omitted, and live state leaked;
  - the concurrency limits.
- Follow-up, not blocking: FTF-H034-F5. Pin the default lease TTL (3600s) and DEFAULT_GPU_QUOTA=1. Add a dedicated
  check that complete() frees the slot.

## FTF-0510-F1 — template SecurityError as a row issue; verified digests required — CERTIFIED (2026-10-07)
- Follow-up of FTF-0510. PR #521, merge commit f8f4c9cbff398be5fee8a227c69444007291566a (head `b0c08a4`). Producer:
  `session_0188JPWM1UmzFkMpaEF3mh5C`. CI: 48/48 passed.
- Verifier: 12 faults are RED:
  - render catch narrowed;
  - SecurityError re-raised;
  - digest None accepted, only one digest checked, or `or`;
  - any string or truthy value accepted;
  - three regex weakenings;
  - the check removed.
  The round-5 regressions are still RED. Whole-file mutation: 98/99 with one allowlisted equivalent, the Protocol
  stub, now at line 221.
- The allowlist entry moved from line 220 to 221 in this PR; the gate re-run passes.

## FTF-H034-F5 — default TTL, GPU quota and slot release pinned — CERTIFIED (2026-10-07)
- Follow-up of FTF-H034. PR #525, merge commit d5cfb3911d2ae0a53a6d11fb430261b60e4b469a (head `686d518`). Tests only.
  Producer: `session_01Hbvgp8VNkuZHNecVWYHsm1`. CI: 48/48 passed.
- Verifier: TTL 60, 3599 and 3601, GPU quota 0 and 2, and complete() not releasing the slot are all RED. Whole-file
  mutation on scripts/task_claim.py: 83/83.

## FTF-0606 — Tool-name grader — CERTIFIED (2026-10-07)
- Criterion: FTF-0606. Round 2: PR #529, merge commit ea6725e5e63b3dc7bb659a54a3d2df38215aa41e (head `7de2dcf`). This
  closes rounds 1–2. Producer: `session_01MARAig3Nze5ZfEZ1FXTvtQ`. CI: 51/51 passed.
- Verifier: every round-1 GREEN fault is now RED:
  - case-folding or stripping of called names, forbidden matches and required matches;
  - a deduped called_names;
  - an empty forbidden set treating every call as forbidden;
  - non-str names, and None, empty or non-str tool_call_ids.
  Whole-file mutation with the extended gate (#528): 44/44, including the M9 `in` mutants.
- Not product-true: there is no live caller yet; the Inspect AI scorer adapter is a separate task.

## FTF-0608 — Tool-sequence grader — CERTIFIED (2026-10-07)
- Criterion: FTF-0608. Round 2 (tests only): PR #532, merge commit f5f0d1eeca9d23045111bc41932693038daf449a (head
  `9b273cf`). This closes rounds 1–2. Producer: `session_019pF6PaHEvyxd4UapcNWJWr`. CI: 48/48 passed.
- Verifier: all 14 round-1 GREEN faults are RED, and about 91 faults were injected in total. The whole-file extended
  mutation gate kills 108/108; the six M9 survivors from round 1 are killed.
- Not product-true: there is no live caller yet.
- Follow-up, not blocking: FTF-0608-F1. Tests for:
  - a strict-prefix trace;
  - bare-str must_precede and allowed_orders;
  - the case direction of duplicate ids ("C0","c0" and "c0","C0");
  - pair names that differ only by case;
  - the first matched_order_index wins;
  - the order of violations and mismatches.

## FTF-04A32 — Local repository integration test — CERTIFIED (2026-10-07)
- Criterion: FTF-04A32. Round 2 (tests only): PR #558, merge commit bd0ca646890ca31f6b5a6099d0a52b8a8e2c5d09. This
  closes rounds 1–2, and round 1 is #544 (merge c3a6cae). Producers: `session_01PLmwxLdJyZhrxDMoF1W91q` (r1) and
  `session_011ChfJg8nYKJzN7bx9di2pW` (r2). It merged on a green ci-verdict.
- Verifier (Sonnet): all six round-2 faults are RED: F09 prefix-sibling symlink; F19 same-length content (both the
  capture path and the post-archive path); F20 rename; F18 chmod; F15/F16 executable bit and tar modes. Optional F14
  is RED. The round-1 spot checks F01/F07/F08/F10/F11/F28/F32/F50 are still RED. The test passes both as root and as an
  unprivileged uid (the root-skipped checks were exercised).
- Not product-true (test task). Law 16 follow-up on git-ignored files: FTF-04A06-F1 (#559).

## FTF-04A34 — Monorepo multi-agent discovery test — CERTIFIED (2026-10-07)
- Criterion: FTF-04A34. Round 2 (tests only): PR #552, merge commit 1af2d1bde8c24b488e3c402ee229cd6f33a43f16 (head
  `483adae`). This closes rounds 1–2, and round 1 is #549. Producers: `session_01NbejJngKwSDXx654Aigxou` (r1) and
  `session_01QeJNarWAKTeSWwsfuVC3dw` (r2). It merged on a green ci-verdict.
- Verifier (Sonnet):
  - 18/19 round-1 GREEN faults are RED: prompt template leak; is_default and nested selected; unsorted markers,
    languages and paths under PYTHONHASHSEED; neighbour inheritance in Python and TS; second TS agent per file;
    entrypoint line and call; spurious and missed markers; commit sha form.
  - 5/5 round-1 RED spot checks are still RED.
  - All DoD clauses (1)–(5) are implemented.
  - The test is real: real git, real modules, no stubs, literal expectations.
- Disclosed limit: a `select_*` body that ignores `candidate_id` is not caught here (only the arity is). The behavioural
  coverage is the DB-backed tests/integration/test_ftf_04a18_candidate_selection.py, which a follow-up check confirms.

## FTF-04A21-F1 — Locked manifest ignores a restored draft — CERTIFIED (2026-10-07)
- Follow-up test only: PR #554, merge commit 07fbb5c71a6fae7ee9645fe8b2a2f45a8a61eda7. Producer:
  `session_01KGUZmUdZcoPqm3AmkdVzLV`. Production code is unchanged.
- Verifier: R3 (Save enabled on a locked manifest) is RED, plus 7 lock and read-only variants. The earlier C6, P7, S8,
  D3, E1, S1, C1 and R1 faults are still RED, and 39/39 specs pass.

## FTF-04A21 — Manifest review/edit UI with per-field evidence — CERTIFIED (2026-10-07)
- Criterion: FTF-04A21. Round 2 (tests only): PR #550, merge commit 8e02372950d2f6fbd5d99f6af0cb898fdc2b1d2a (head
  `8790a01`). This closes rounds 1–2, and round 1 is PR #542 (merge 5db0c2d). Producers:
  `session_0158WZxJUHvbjVh2K3Upjr8b` (r1) and `session_01JybGbsbAz1zmGP5Mw8vUY6` (r2). It merged on a green ci-verdict.
- Verifier (Sonnet):
  - All four round-1 GREEN faults are RED: C6 conflict erased on edit; P7 storage key unscoped, on each of org,
    project and manifest; S8 JSON `null` dropped from the PATCH; D3 appended diff lines dropped.
  - P6 (storage throw) is RED by assertion.
  - Round-1 spot checks are still RED.
  - 38/38 specs pass on the pristine tree.
  - Every endpoint called exists (GET/PATCH manifest, GET evidence, POST submit-for-review, POST lock, all in
    04A-interface.yaml), there is no fake data, and failures stay visible (Law 17).
- Not product-true: UNWIRED. No page mounts the component; the packet has no page file. The first page task is FTF-H029.
- Follow-up, optional: FTF-04A21-F1 (R3, a restored draft on a locked manifest keeps Save disabled).

## FTF-1807 — LiteLLM outage runbook — CERTIFIED (2026-10-07)
- Criterion: FTF-1807. Round 4 (tests only): PR #511, merge commit 42fd82c331fb91cccd7ef719bc1a0b7c133ba786 (head
  `8c571ca`), merged 2026-10-06T20:45Z. This closes rounds 1–4. Producer: `session_011orXE4QeDXVFKcBi6r8awV`.
  CI step: `uv run python3 tests/documentation/test_ftf_1807.py` (ci.yml:339).
- Verifier (Sonnet): every paragraph of docs/operations/litellm_outage_runbook.md sections 0–8 (29 paragraphs) is
  pinned by literal sha256 values; the pins are not computed from the document, and tampering one hash is RED.
- 43/44 meaning-changing doc faults are RED, including the 15 round-3 inversions, emptied sections 3 and 5, "is
  dropped entirely"→"is applied", table rows, renumbered steps and count-keeping heading renames. The one GREEN fault
  is a pure line rewrap inside a paragraph, which the test deliberately normalizes.
- 29/32 checker faults are RED. The three GREEN ones are equivalent or redundant (8-hex-char hash compare, a looser
  step regex still caught by the prefix check, and a heading check that the dedicated tests also cover).
- Not product-true: it is a documentation criterion with no proving_symbol.
- Same-class follow-up (from round 3, still open): audit the other tests/documentation runbook tests for the
  phrase-blacklist weakness.

## FTF-0608-F1 — Tool-sequence grader follow-up tests — CERTIFIED (2026-10-07)
- Round 2 (tests only): PR #539, merge commit db4cb881bf8b5a320cc6b9639a457172ec687b2c (head `2cb7e13`). This closes
  rounds 1–2. Producer: `session_017ufim7cSr3s5BjwvHDqaFX`. CI: 48/48 passed.
- Verifier: both round-1 GREEN faults are now RED: the outer bare-str guards on allowed_orders and must_precede, and
  mismatches listed with missing keys first. The optional faults are RED too: forbidden_calls reversed, and casefolded
  precedence membership. Six round-1 faults spot-checked are still RED. The whole-file mutation gate kills 108/108.

## FTF-0608-F1 — Tool-sequence grader follow-up tests — RETURNED, round 1 (2026-10-07)
- PR #536, merge commit 2c7707af9851ebb978e0f3135634dd01bb3dc7e5 (head `f0139cb`). Producers:
  `session_01LAyzMQEaW7t8CBQgqQ1KEh` and `session_01C5wcUXBr7uqDDpbisFdZrG` (main merge). CI: all checks passed.
- Verifier: 64 faults injected into workers/evaluation/tool_sequence_grader.py. All six DoD behaviours are pinned.
  The whole-file mutation gate kills 108/108.
- Two non-equivalent faults stay GREEN across the whole file:
  - (A) Dropping the outer bare-str guard on allowed_orders or must_precede. The tests only use "ab", which the
    inner `_names` check also refuses; with the guard gone, `""` is accepted as "no constraint".
  - (B) Mismatches as a stable partition with missing keys first. The order [mismatched, missing] is never tested.
- Returned as QUEUE item 0c (tests only).

## FTF-0604 — Exact-match grader — CERTIFIED (2026-10-07)
- Criterion: FTF-0604. Round 3 (tests only): PR #535, merge commit def15b04f8aa732dc1859423e2ec33e5c4907688 (head `125cf8e`). This closes rounds 1–3.
  Production code is from rounds 1–2 (#519, #531). Producer: `session_01LAyzMQEaW7t8CBQgqQ1KEh`. CI: 48/48 passed.
- Verifier: every round-2 GREEN fault is now RED:
  - normalized_candidate raw or empty, on pass and fail;
  - casefold changed to upper or lower;
  - NFC, NFKC or zero-width removal injected into each of the 5 normalizers.
  Spot checks of round-1 faults are still RED. The one GREEN fault is provably equivalent (on a pass, the normalized
  candidate equals the normalized approved value). The whole-file gate with M10 and the dispatch tables kills 50/50.
- Not product-true: there is no live caller yet.

## 2026-10-07T13:40Z — RETURNED: FTF-0515 (round 2), FTF-04A22 (round 2), FTF-04A06-F1, FTF-0605, FTF-0607, FTF-0609, FTF-0610, FTF-0611
- FTF-0515 round 2: PR #564, merge d1181590c55e7f00e6e4386719ac81b3f75da64f.
  - Production P1/P2 are correct. Mutation 112/112.
  - About 25 hand faults in the round-2 DoD bullets stay GREEN. Tests only: QUEUE 0b.
- FTF-04A22 round 2: PR #565, merge a09aea81f5d61c8d9880bd80439f62712b120e94.
  - vitest 47/47.
  - "Returned manifest shown" is not implemented. Two assertions are missing. QUEUE 0c.
- FTF-04A06-F1: PR #559, merge 0147eec734197f9770eec2e1078c1c9a2d1e89cf.
  - F1–F4 and NUL-split faults are RED.
  - Nested .gitignore, never-read, newline/space/unicode names and ignored-directory pruning are unproven. QUEUE 0d.
- FTF-0605: PRs #560 and #562, at merge b9e830d0e74c76d855cad5a6fbe0cda3701ff057.
  - Mutation 235/235.
  - 20 of 111 hand faults are GREEN. QUEUE 0e.
- FTF-0607: PR #562, merge b9e830d0e74c76d855cad5a6fbe0cda3701ff057.
  - Mutation 141/141.
  - 7 real survivors, including the optional-null fail-open. QUEUE 0f.
- FTF-0609: PR #563, merge 895499f8b04cd1f6e989fdceb60b7b8ad9114da9.
  - Mutation 90/90.
  - 3 survivors: any→all on executed, and empty str/bytes inputs. QUEUE 0g.
- FTF-0610: PR #566, merge 8c49975e1036f86fb181b15e49c2ec9c02564dac.
  - Mutation 136/136.
  - 15 of 95 hand faults survive (tests only). QUEUE 0h.
- FTF-0611: PR #567, merge 10bc14c152707585ae0241ca9c21c3400950737e.
  - Mutation 66/66.
  - 3 real survivors plus minor pins (tests only). QUEUE 0i.
- Verifiers were independent Control-plane subagents (the producers are relay lanes). Each ran in a detached
  worktree at the merge commit.

## 2026-10-07T16:00Z — CERTIFIED: FTF-0515, FTF-04A22, FTF-0605, FTF-0607, FTF-0610; RETURNED: FTF-0609 (round 2), FTF-04A06-F1 (round 2)
- Verifiers were independent Control-plane subagents; the producers are relay lanes. Each verifier ran in a detached
  worktree at the merge commit, injected every fault on the round's return list, and spot-checked earlier rounds.
- **FTF-0515: CERTIFIED** (round 3).
  - PR #569, merge 33a122671dcb7e2c6bac435e5ffdfe15c0066298. Production from round 2 (#564).
  - All 50 round-3 faults RED; 6 round-2 spot checks RED. Mutation 112/112.
  - Follow-up FTF-0515-F1 (tests only): issue_id with batch positions dropped stays green for record_index=None rows.
- **FTF-04A22: CERTIFIED** (round 3).
  - PR #570, merge e6019bbaa5b1a1593f19cba69c1f24be9935dd07.
  - vitest 52/52; lint, format and typecheck clean.
  - The returned manifest is now shown after save. The R1a/R1b/R2/R3 faults, 4 recommended faults and 10 earlier faults
    are all RED. The Control plane re-ran R1a itself: 2 failed, then 52/52 after restoring.
  - The component is still UNWIRED (no page).
- **FTF-0605: CERTIFIED** (round 2).
  - PR #572, merge 6aebcf04ca9dda351b3b5d5af7033fd34118901b.
  - All 8 round-2 items plus the optional ones RED (about 55 faults). Mutation 235/235.
  - Follow-up FTF-0605-F1 (tests only).
- **FTF-0607: CERTIFIED** (round 2).
  - PR #573, merge 9201640c41351cbc6c0da069fe9b8f0a4df008ff.
  - All 7 survivors plus 2 optional RED; 9 round-1 faults RED. Mutation 141/141.
- **FTF-0610: CERTIFIED** (round 2).
  - PR #575, merge ddddbd3de363c24fe17063d4ff85205c0cf0afe3.
  - All round-2 faults RED; 8 round-1 faults RED. Mutation 136/136.
- **FTF-0609: RETURNED** (round 2 → 3, tests only).
  - PR #574, merge 7407c1d.
  - Round-2 items are RED, but a fresh sweep found 2 real gaps: outcome lookup over all effects (it would launder a denial
    or execution), and mixed-case forbidden names.
- **FTF-04A06-F1: RETURNED** (round 2 → 3, tests only).
  - PR #571, merge 19df19e.
  - Items 1 and 3–5 RED, including as uid 65534. Diff mutation 23/23.
  - The required never-read spy does not cover os.open or io.open (Law 16).
- Graders FTF-0605, 0607 and 0610 are not product-true: they have no live caller yet (same precedent as FTF-0604).

## 2026-10-07T18:30Z — CERTIFIED: FTF-0609, FTF-0611; follow-ups FTF-04A06-F1, FTF-0515-F1, FTF-0605-F1; RETURNED: FTF-0308-P1
- **Verification method:** independent Control-plane verifiers, each in a detached worktree at the merge commit. Every
  fault on the return list was injected; earlier rounds were spot-checked; the mutation gate ran uncapped.
- **FTF-0609: CERTIFIED** (round 3).
  - PR #579, merge e25ca189e665a23647ef034398427ca295e94904. Production from round 1 (#563).
  - Both round-3 gaps are RED: outcome lookup over all effects, and lower/casefold/strip on the forbidden set and on names.
  - Mutation 90/90.
- **FTF-0611: CERTIFIED** (round 2).
  - PR #576, merge c3e09859debf197e4cfb9fc4e7067f076f4dd6ec.
  - All round-2 items are RED. Mutation 66/66.
  - Accepted residual: a 1e-12 slack on the relative term alone stays green. Arithmetic is exact (Fraction), and any slack
    of 1e-11 or more is killed.
- **FTF-04A06-F1: CERTIFIED** (round 3, Law 16).
  - PR #580, merge 87719f37d32fd3581dc40b5899788ff8cf5ba339. Production from round 2 (#571, 19df19e).
  - Ignored-file reads through os.open, io.open, builtins.open, Path.read_bytes and Path.open are all RED, as root and as
    uid 65534.
  - Follow-up FTF-04A06-F2: an audit-hook check that does not depend on call sites (io.FileIO, subprocess).
- **FTF-0515-F1: CERTIFIED.**
  - PR #583, merge 59b44a7bcbe8be76675ad2883ae490117e1d2102. Tests only.
  - Dropping positions, category or reason_code from issue_id is RED. Mutation 112/112.
- **FTF-0605-F1: CERTIFIED** against its written DoD.
  - PR #584, merge eb18e69776fd0f0d97a5bb8b50e5a8c7c93cb2e8. Tests only.
  - All 4 items are RED. Mutation 235/235.
  - The exclusive-bound message echo is not in the written DoD. It is follow-up FTF-0605-F2.
- **FTF-0308-P1 (child slice): RETURNED**, round 2, tests only.
  - PR #577, merge fa31383.
  - The contract, every gate and the real-Postgres integration test are fine (verifier ran postgres:16 locally). Mutation
    190/190.
  - Gaps: the identical-404 body is not asserted for POST and /changes; there is no runtime proof of "no alias→artifact
    inference"; one DELETE check cannot fail. QUEUE 0b.

## 2026-10-07T21:30Z — CERTIFIED: slices FTF-0308-P1 and FTF-0602-P1, follow-ups FTF-0605-F2 and FTF-04A06-F2; RETURNED: FTF-0612, FTF-0613, FTF-1305
- **Verification method:** independent Control-plane verifiers, each in a detached worktree at the merge commit. Migration
  slices ran against real Postgres in Docker, matching the CI job (pinned postgres:16 digest, same db/user/env), on a
  separate port each.
- **FTF-0308-P1 (child slice of FTF-0308): CERTIFIED**, round 2.
  - PR #586, merge a59a14ca91c18d450562bc33a75c6423739b6847 (tests only). Production is from #577 (fa31383).
  - All three return items are RED: identical 404 bodies on POST and /changes, including a foreign-only variant; a guarded
    name→artifact heuristic in GET and in POST null; GRANT DELETE.
  - 8 round-1 faults are RED. Mutation 190/190. FTF-0308 (UI) is re-queued.
- **FTF-0602-P1 (child slice of FTF-0602): CERTIFIED.**
  - PR #582, merge 1f401a05b5eb0bad10e92d50e2f946e3d9c4564c.
  - Contract met; every gate clean. Law 18 holds under sovra_app (not superuser, not BYPASSRLS).
  - 34 faults are RED. Three application-layer faults are also blocked by database backstops, and the task test catches
    each one. Mutation 267/267. FTF-0602 (UI) is re-queued.
  - The verifier found a pre-existing hardening gap: require_role reads a chunked body without a cap. It is follow-up
    FTF-0103-F1.
- **FTF-0605-F2: CERTIFIED.** PR #587, merge e3f9aabdfdb59c03fe025569d66600cb75d00cd1. The exclusive-bound echo faults are
  RED.
- **FTF-04A06-F2: CERTIFIED** (Law 16). PR #588, merge b413ec9d9ede9026c3e9b34b4bdd6f6a411cc49f.
  - The audit hook (open plus subprocess.Popen, sink scoped to the capture) catches reads through io.FileIO, _io.open,
    os.open, builtins.open, Path.read_bytes and subprocess cat, as root and as uid 65534.
- **FTF-0612: RETURNED** (#589, 39eac2c). An unhashable outcome raises TypeError instead of the grader's error (fail-closed
  defect). Two boundary tests are missing. Mutation 105/105.
- **FTF-0613: RETURNED** (#590, 1fcb9d3).
  - Same unhashable-membership defect.
  - A cost on a record in another currency is never validated.
  - Malformed-input tests are missing for zero, bytes, non-ASCII digits and trailing newlines.
  - A dead `_DEFAULT_PRICE` constant remains. Mutation 71/71.
- **FTF-1305: RETURNED** (#592, a84cb46). Tests only: deterministic tie-breaks, the overlap group and the incumbent, and the
  reconciliation flag. The module is disclosed UNWIRED. Mutation 133/133.
- **No spec-matrix flips this round.** Child slices and follow-ups are ledger-only. Status stays at 127/302.

## 2026-10-07T23:50Z — RETURNED: FTF-0517 (round 1 → 2, tests only)
- PR #591, merge db26d03. Production is deterministic: SHA-256 buckets, sorted output, input not mutated, exact keys.
- Mutation: 117/119 killed, 2 timeouts, 0 survived. 60 hand faults; 14 survive, several of them real.
- Gaps:
  - No pinned golden manifest. hash() instead of SHA-256, a reversed slot mapping and a ratio off-by-one all survive.
  - Group counts are never asserted.
  - The cutoff provenance is never asserted.
  - A post-cutoff group preassigned to validation is untested.
  - The hash's sensitivity to group_id and strategy is untested.
  - A negative non-train ratio is untested.
- Also required: a docstring line saying the caller must run assert_clear_for_split. QUEUE 0e.

## 2026-10-08T00:30Z — CERTIFIED: FTF-0612, FTF-0613, slice FTF-0501-P1; RETURNED: FTF-1305 (round 2), FTF-0103-F1
- **Verification method:** independent Control-plane verifiers in detached worktrees at the merge commits. FTF-0501-P1 ran
  on real Postgres in Docker, matching the CI job.
- **FTF-0612: CERTIFIED** (round 2).
  - PR #594, merge 79567c0921851bbf4e3b99db563c357dbd59c925.
  - Every return item is RED, including reverting the unhashable-outcome fix. 9 round-1 faults RED.
  - The checklist probe raises no TypeError on any field. Mutation 107/107.
- **FTF-0613: CERTIFIED** (round 2).
  - PR #595, merge 6b9b759ac378d5678d24b71315689a711b5e02f4.
  - Every return item is RED: the outcome/source guards, cost validated before the currency branch, regex anchoring, and
    bytes inputs. `_DEFAULT_PRICE` is removed.
  - 9 round-1 faults RED. Mutation 76/76.
- **FTF-0501-P1 (child slice of FTF-0501): CERTIFIED.**
  - PR #581, merge 59a2c52897d0123a47f696840617d129997c8615.
  - Contract met. The FK is stricter than the contract (it adds purpose).
  - Every gate is clean. Law 18 holds under sovra_app (rolsuper=f, rolbypassrls=f). About 40 faults RED.
  - Mutation 206/206, uncapped.
  - Defense-in-depth follow-up FTF-0501-P2: refuse terminal→open status changes and non-open or forward-dated inserts.
  - FTF-0501 is re-queued after P2.
- **FTF-1305: RETURNED**, round 3, tests only (#596, f21f1ec).
  - The primary sort key (base before high) is unpinned. This is a Control-plane gap in the round-2 list.
  - The unhashable guard on `_lookup` is untested.
  - The id tiebreak is not pinned against incumbent, kind or quality.
- **FTF-0103-F1: RETURNED**, round 2, tests only (#597, d6b7381).
  - The fix is correct and bounds memory at cap plus one chunk.
  - No test proves the read stops at the cap: a read-everything-then-check fault passes every test.

## 2026-10-08T03:30Z — CERTIFIED: FTF-1305, follow-up FTF-0103-F1, slice FTF-0501-P2; RETURNED: FTF-0308, FTF-0602, FTF-0616, FTF-0901
- **Verification method:** independent Control-plane verifiers in detached worktrees at the merge commits. FTF-0501-P2 ran
  on real Postgres in Docker.
- **FTF-1305: CERTIFIED** (round 3).
  - PR #603, merge 00a095b6bce633a3f0081650fee663b7ed2b7417. Tests only; production is from #592.
  - The sort key, the unhashable guard and the id tiebreak (against incumbent, kind and quality) are RED. Mutation 133/133.
  - Disclosed UNWIRED.
  - Note: PR #603 came from a relay session on a non-standard branch name (claude/wizardly-fermat-lejoey). Its content is
    a valid round-3 submission.
- **FTF-0103-F1: CERTIFIED** (round 2, security hardening).
  - PR #605, merge 81835757efb373922baa7222f7f379b03c8e3dc6. Production is from #597 (d6b7381).
  - A real-socket test with an unterminated chunked body, plus a byte-counting ASGI receive, kill the
    read-everything-then-check fault.
  - Timeouts are bounded. 9 runs, no flakes.
- **FTF-0501-P2 (child slice): CERTIFIED.**
  - PR #606, merge cbc49064f4dbff2e81d671ab4b4e2f56c49f0695.
  - Real Postgres: terminal sessions are fully frozen, and the insert guard requires status open and created_at within
    ±60 s.
  - 14 faults RED. The app role cannot bypass it. FTF-0501 is ready.
- **FTF-0308: RETURNED** (tests only; #598, caf52ae).
  - The §2.2 exact-name non-inference rule is untested.
  - A malformed POST 200 is untested.
- **FTF-0602: RETURNED** (tests only; #602, a824d6a).
  - A viewer's 403 role_not_authorized on POST or PATCH can be shown as success (Law 17); this is untested.
- **FTF-0616: RETURNED** (production plus tests; #604, a5b86ae).
  - §27: a critical case can pass on the judge alone. The test locks this in.
  - Calibration is not bound to the rubric/judge triple, and the §69.5 thresholds are not applied.
  - There are no criterion-level scores, and rubric/judge identity is not recorded.
  - Duplicate JSON keys, NaN and an out-of-range confidence are accepted.
  - The resolved-model switch is not detected.
  - spec-matrix was not updated.
  - The real-judge clause is BLOCKED_EXTERNAL (owner action recorded).
- **FTF-0901: RETURNED** (tests only; #607, 6f841c6).
  - Provider exactness was proven only by regex self-checks; prefix, case-fold and strip faults survive behaviourally.
  - The approved_by requirement is untested.
- **FTF-1706: RETURNED** (production plus tests; #608, 12137d0).
  - Law 17: a failed or revoked gateway is indistinguishable from an empty organisation. It must consume FTF-0215
    SyncCycleResult / AccessState.
  - NaN/inf VRAM fails open.
  - There is no tenant/connection binding.
  - Malformed inputs raise TypeError/AttributeError instead of the typed error.
  - 8 behavioural survivors.
  - Gate gap: evals/ is outside MUTATION_ROOTS and SOURCE_ROOTS; to be fixed in a governance PR.
- **New rule:** a fault row must be behavioural; a regex check that guard text is present is not evidence.

## 2026-10-08T07:00Z — CERTIFIED: FTF-0308, FTF-0602, FTF-0901; RETURNED: FTF-1706 (round 2 → 3)
- **Verification method:** independent Control-plane verifiers in detached worktrees at the merge commits. Faults were
  behavioural only: the guard text was kept in place so regex self-checks could not satisfy them.
- **FTF-0308: CERTIFIED** (round 2).
  - PR #611, merge f8270c8b4e48b8a567978b33b50b61a674356118 (tests only). The component is from #598 (caf52ae).
  - vitest 222/222. The §2.2 exact-alias non-inference faults (preselect, auto-POST, "Mapped"), the malformed POST 200
    and the GET credentials are all RED.
  - 6 round-1 faults RED.
  - UNWIRED: the page tree is FTF-H029's. It is built on certified slice FTF-0308-P1.
- **FTF-0602: CERTIFIED** (round 2).
  - PR #612, merge 5cd3c5c1131bcdec92a5c70a38e010318d0ab708 (tests only). The component is from #602 (a824d6a).
  - vitest 224/224. A viewer's 403 on import and on PATCH is never shown as success, and the list is unchanged (7 faults
    RED).
  - 7 round-1 faults RED.
  - UNWIRED (FTF-H029). It is built on certified slice FTF-0602-P1.
- **FTF-0901: CERTIFIED** (round 2).
  - PR #614, merge 6e66492713a62ff3676c84a223cc6f41a649546c (tests only). Production is from #607; module blob 1be584d is
    unchanged.
  - Behavioural provider exactness (prefix, case and strip) and the approved_by requirement are RED.
  - 6 round-1 faults RED. Mutation 66/66. UNWIRED disclosed.
- **FTF-1706: RETURNED** (round 2 → 3, tests only; #615, 402102a).
  - All four production fixes are correct: the FTF-0215 gateway state, isfinite, org/connection scoping and typed
    guards. About 40 faults RED.
  - The cross-org attestation test is vacuous: double-wrapping makes it fail for the wrong reason.
  - A mutation run on a copy left 15 survivors, including failure-path counts (Law 17).
  - The evidence_hash is stale.
- **Governance:** #616 merged. evals/reference is now under the mutation and product-truth gates.

## 2026-10-08T12:20Z — CERTIFIED: FTF-1706; RETURNED: FTF-0616 (r3), FTF-0903, FTF-1602, FTF-R005, FTF-1703, FTF-1206 (r3); SLICES: FTF-0516-P1, FTF-0309-P1, FTF-H010-P1
- **Verification method:** independent Control-plane verifiers in detached worktrees at each merge commit. Faults were
  behavioural only. Real SeaweedFS (FTF-1602) and a real MLflow 3.1.1 server (FTF-R005) ran in Docker on unique ports.
  Mutation runs were uncapped (`--max-mutants 0`).
- **FTF-1706: CERTIFIED** (round 3).
  - PR #618, merge 879ea74cac33b6868e2fe857bed511ed424d7e87 (tests only). Production is from #615 (402102a).
  - Scope refusals are asserted by message, and the org, connection and global faults are RED.
  - Mutation 149/149; evals/ is now gated (#616). evidence_hash updated.
  - UNWIRED disclosed.
  - Optional follow-up (not a return reason): a naive `now` should raise a typed error, and a future
    last_successful_check_at should not count as fresh.
- **FTF-0616: RETURNED** (round 2 → 3; small production fix plus tests; #613, 1670ff5).
  - All 7 round-2 items are implemented, and the real-judge clause is disclosed BLOCKED_EXTERNAL. 47 of 50 faults are
    RED; mutation 304/305.
  - Required:
    - Prove the self-judge refusal behaviourally (survivor at llm_judge_grader.py:681).
    - Never read the verdict from the reasoning text (injection).
    - A missing or non-string `model` echo is a non-verdict, never PASSED with UNVERIFIED.
    - Pre-network refusals make zero OpenBao secret fetches.
  - With these fixed and 0 survivors, the adapter scope can be certified (FTF-1810 precedent), but not DONE.
- **FTF-0903: RETURNED** (round 1 → 2; production plus tests; #624, 89f1aeb).
  - Mutation 104/104, but 16 of 54 behavioural faults survive.
  - Required:
    - Errors never echo caller text.
    - Exact str/int types only.
    - ContentRef is re-validated at emit.
    - A huge int gives a coded refusal, not OverflowError.
    - Exact-key, bounds and determinism tests.
    - Disclose that the checks are shape-only.
- **FTF-1602: RETURNED** (round 1 → 2; production plus tests; #620, f0835e1).
  - Real SeaweedFS passes; mutation 191/191.
  - Required:
    - Restore is not proven (§48): back up full manifests and prove a restore into a clean bucket on real SeaweedFS.
      A corrupt, partial or swapped backup must be refused.
    - Tenant scoping: one backup accepted two orgs.
    - verify_backup must re-check its counts and hash.
- **FTF-R005: RETURNED** (round 1 → 2; production plus tests; #621, f44136e).
  - The integration form is correct: a REST adapter over guarded_fetch, with no new runtime dependency. The real server
    passes; mutation 320/320.
  - Required:
    - Run-level drift against the Factory payload, not only the mutable tag (§78.3).
    - PENDING_REGISTRATION and FAILED_REGISTRATION surfaced (Law 17).
    - Bounded retries (§77.5).
    - Reuse the certified FTF-R002 durable RLS outbox, and prove a restart keeps buffered results.
    - A token-leak test (the fault survives today).
    - Page model-version search beyond 200.
- **FTF-1703: RETURNED** (round 1 → 2; production plus tests; #619, 43c1a39).
  - Mutation 199/199; 40 of 42 faults RED. The ">=1000 reviewed" clause is honestly BLOCKED_EXTERNAL.
  - Required:
    - group_count must follow template families: 21, not 1200 (§67). The floor is reported NOT met.
    - Provenance must be verified by regeneration, including user turns and tool arguments (Laws 12 and 16).
    - Reserved input must fail closed (a bare string is split into characters today).
    - Typed errors for malformed fields; the secret scan must cover dict keys.
- **FTF-0516 (parked by the relay, #622):** prerequisite slice FTF-0516-P1 has been designed and queued. It covers the
  quality report, the issue and resolution tables, and their API.
  - Gap recorded: no task owns running the detectors on dataset artifacts (likely an FTF-0502 refinement).
- **FTF-1206: RETURNED** (re-review after FTF-1206A; round 2 → 3; small production fix plus tests). It had been
  PROVISIONAL since 2026-10-02, waiting on its persistence half, which FTF-1206A (certified 2026-10-06) now supplies.
  - Verifier ran at 89f1aeb; the module is from #412 (d489936).
  - 28/28 checks; the real PG16 test for FTF-1206A passes. 16 of 18 faults RED; uncapped mutation 32/36.
  - The verifier recommended CERTIFY. It is returned under the standing bar (0 survivors; typed fail-closed input):
    - 4 survivors.
    - A bare str for a set is split into characters.
    - `route_change_requires_approval=None` turns approval off.
    - A stale grant is still honoured after the org narrows its policy (negative_acceptance).
  - Notes still say there is no backing table.
  - Repo-wide observation, not a return reason: under plain `pytest`, the `tests/tasks` check-collector files report pass
    even when checks fail, because only `main()` exits non-zero. CI runs them as scripts, so CI evidence is sound.
- **CI fix (test only, FTF-0501-P1):** `test_s3_finalize_creates_versions_in_order_and_audits` mixed clocks. It
  created a session at the fixed `_NOW` (2026-10-07T12:00Z, 24h TTL) and finalized it with the real clock (`now=None`),
  so it expired, and every branch's gate went red from 2026-10-08T12:00Z.
  - The session in that check is now created on the real clock too.
  - The assertion and the production code are unchanged, and FTF-0501-P1's certification is unaffected.
  - The remaining gate steps that CI skipped after it pass locally.
- **Backlog refill** (frontier recomputed from spec/tasks.json). Added:
  - FTF-0908 (ready).
  - FTF-0309-P1, a capabilities read API, because no route exposes the FTF-0307 classifier.
  - FTF-H010-P1, the import fields and import-validation jobs migration. FTF-H010's real clauses are BLOCKED_EXTERNAL.
  - FTF-R004 is not queued. It needs an `inspect_ai` pin in workers/evaluation (Inspect AI is a fixed reused component
    per REFERENCE_REPOSITORIES.md, so it is not a fresh owner ask) and EvaluationRun/case-result persistence. Its real
    target/runtime clauses are BLOCKED_EXTERNAL. It will be sliced once FTF-0616 and FTF-1705 land.
- **ci-verdict:** all 7 relay merges since 06:02Z (#618, #620, #613, #619, #622, #621, #624) landed on a green
  ci-verdict.

## 2026-10-08T14:20Z — RETURNED: FTF-0903 (r3), FTF-1206 (r4), FTF-0905 (r2)
- **Verification method:** independent Control-plane verifiers in detached worktrees at each merge commit. Faults were
  behavioural only; mutation runs were uncapped (`--max-mutants 0`).
- **FTF-0903: RETURNED** (round 2 → 3, tests only; #627, e054e34f9af90cb02bde29151070c8f6a3908119).
  - The code behaves correctly. About 60 behavioural faults were run; mutation 99/99.
  - Items 3, 4, 6 and 7 hold.
  - Three round-1 items have no behaviour test that goes RED:
    - a rejected value echoed into the error message, for every field kind (7 faults GREEN);
    - a str-subclass field name;
    - an unhashable field name.
  - Two of these went RED only through the test file's guard-text regex, which is not evidence.
- **FTF-1206: RETURNED** (round 3 → 4, tests only; #628, 0d0df9f9891cf8665ff332b9eb445f840c392eba).
  - Round-3 items 2–4 hold: typed fail-closed inputs, the current-policy recheck and the notes. Mutation 47/47.
  - Item 1 holds only in part. Normalising the target or actor (strip/lower) before validation stays GREEN, because no
    test puts the variant in both the grant and the org policy, or uses whitespace variants.
- **FTF-0905: RETURNED** (round 1 → 2; production plus tests; #625, 0f4aa2170edcd2219a2e0964eab5e0c73808fb1e).
  - Mutation 88/88, but its operators never reach the defects below.
  - Fail-open: a confirmed leak can be followed by "complete".
  - An empty ledger, or one with one clean check, is "complete".
  - canary_id embeds the token, so the canary is reconstructible.
  - Entropy is not pinned: 4 bytes stays GREEN.
  - Untyped errors on record_checked input.
  - The spec-matrix L1-PROVEN overstates NEG-1b.
- **ci-verdict:** relay merges #625, #627, #628 and #629 all landed on a green ci-verdict. FTF-1602 r2 (#629, 7d28d4c)
  is under verification.
- **Observation, not a finding:** data_exposure_check without arguments prints "no datastore backends declared". CI runs
  it with `--backends postgres_rls,s3_identity --fail-on-exposure`, so the gate is intact.

## 2026-10-08T14:30Z — RETURNED: FTF-1602 (r3)
- **FTF-1602: RETURNED** (round 2 → 3; production plus tests; #629, 7d28d4c90e3657911788bdf348d760c01e683aaa).
  Independent verifier on real SeaweedFS with a bounded fixture.
  - Item 3 holds: verify re-checks counts, bytes and the manifest hash, RED in unit and integration.
  - Unit faults all RED.
  - Open findings:
    - A shrunk, re-hashed latest version of the backup record restores as success. Partial backup accepted (Law 17).
    - At integration level, a missing blob and a foreign-tenant prefix are not proven (faults GREEN).
    - Typed input: a membership test runs before the type check, and a str subclass bypasses the tenant guard.
    - A malformed record raises untyped errors.
    - Whole-file mutation 297/299. The PR's 200/200 was diff-scoped.
    - Notes overclaim.
  - The first fault pass was discarded after the verifier's unbounded SeaweedFS fixture filled the disk. All rows come
    from a second, bounded pass with the module sha256-verified after each row.

## 2026-10-08T16:25Z — RETURNED: FTF-0903 (r4), FTF-0905 (r3), FTF-1703 (r3), FTF-1206 (r5), FTF-0501 (r2); SLICE: FTF-0501-UI
Independent verifiers, one per PR, each on the merge commit, CI on the PR head green (ci-verdict success) and gates
clean (product_truth 45/45, validate_spec --strict, data_exposure OK). No producer session matches the certifier.
- **FTF-0903: RETURNED** (round 3 → 4; tests only; #632, d37e6b18a43f87399d7ed7775dcfc35543439f2a). Round-3 fixes 2-3
  hold; mutation 99/99. Open: ContentRef echo (X5) stays GREEN with a raw traceback in str(exc); X4 and the
  out-of-range number/count/timestamp/bool echoes (X1-X3, X7) go RED only through guard text; the test leaves a mutant
  .pyc so a second run in one checkout fails.
- **FTF-0905: RETURNED** (round 2 → 3; tests only; #634, f6b99cea69406e523e15ed6edebca6cdb008cf68). All five round-2
  production fixes hold; 17 faults RED. GREEN: default-plan contents (F6, F7, F7b), sticky leak on the same key (F8,
  F9), canary_id independence (F11, F12).
- **FTF-1703: RETURNED** (round 2 → 3; production plus tests; #630, 6bc52597920aabb45130f9705a83ebc1120b57f7). Items
  1, 3, 4 hold. Reproduced: a TrainingTrace subclass with an overridden to_dict() and a nested dict subclass are
  admitted carrying a sealed final-suite prompt, a forged critical flag and an sk- secret. G4 (exact step type) GREEN.
- **FTF-1206: RETURNED** (round 4 → 5; production plus tests; #635, 77812afaa364acbd963af1a46375e8e493a56555). All
  round-3 items hold. Reproduced negative acceptance: a str subclass aliasing an allowed value widens a grant to a
  namespace the org never allowed and authorizes a non-approver. Seven identifier-pattern faults GREEN.
- **FTF-0501: RETURNED** (round 1 → 2; #610, 4154b96da3c02d05e3cd93006e803790693944b0). P1/P2 hold; 77 of 78
  real-route probes pass on PG16 + SeaweedFS; 18 faults RED. Open: XLSX check accepts any ZIP (E8b reproduced); a flaky
  unsigned-request check (400 vs 403); §67.5 24 h multipart abort claimed but not implemented; no route-level PG + S3
  CI job and blank proving_symbol; no resumable UI.
- **SLICE FTF-0501-UI:** the "resumable UI" clause becomes a Backlog child slice on the FTF-0501 routes. FTF-0501 is
  certified only when round 2 and FTF-0501-UI both pass.
- Pending: FTF-0616 (#636) verification still running.

## 2026-10-08T17:00Z — RETURNED: FTF-0616 (r5)
- **FTF-0616: RETURNED** (round 4 → 5; small production fix plus tests; #636, a9c2f3eef132). Independent verifier:
  CI on head 87b7f17 green (ci-verdict success); gates clean; whole-file mutation 302/302. Round-3 items 1, 2 and 4
  fixed; item 3 fixed for every gateway echo shape. Reproduced: an approved set holding None with no echo gives
  PASSED/VERIFIED (P-18); an echo of another approved but uncalibrated judge gives PASSED/VERIFIED (P-17), against the
  negative acceptance "an uncalibrated result cannot certify". Control-plane ruling: P-17 is blocking and overrides the
  round-3 acceptance of any approved model. Open #637 is superseded; its None clause is ported into round 5.

## 2026-10-08T17:52Z — RETURNED: FTF-0309-P1 (r2); SLICES: FTF-R004-P1..P4
- **FTF-0309-P1: RETURNED** (round 1 → 2; production plus tests; #642, ab71b1c0fca1e998a1ef4b40d1d740d85732282f).
  Independent verifier: CI on head f4f9f9a green (ci-verdict success, real-Postgres job ran); gates clean; mutation
  70/70; cross-tenant, role, revoke and single-discovery checks hold on PG16 as sovra_app. Reproduced: one GET makes a
  second gateway call through `enrich_visible_models` (3 requests, 2 secret reads), against "provider called only
  through the existing discovery client, at most once per request" (fault FA GREEN). Unpinned: exact alias matching
  (`.lower()` fault FB GREEN; a different unmapped alias reports another alias's artifact). DoD proving_symbol bullet
  not done.
- **SLICES FTF-R004-P1..P4** (Backlog): P1 generation-config fidelity and P2 provider-tool/MCP denial are ready now; P3
  (pinned Inspect worker + actual-runtime bridge) depends on P1 and P2; P4 (log normalization, complete accounting,
  sealed-data guard) depends on P3. Pinning `inspect_ai` in the evaluation worker is owner-approved (live answer,
  2026-10-08), matching REFERENCE_REPOSITORIES.md "Pinned evaluation worker package". Left with the parent: the
  engine-evidence read view (no EvaluationRun contract yet), the sealed final-suite worker (FTF-H020), the real
  gateway route (same key as the FTF-0616 owner action), the component version-lock row and the Q35 journey.
- Pending: FTF-1602 r3 (#638) verification still running.

## 2026-10-08T18:12Z — RETURNED: FTF-1602 (r4)
- **FTF-1602: RETURNED** (round 3 → 4; production plus tests; #638, 99fa4c05605be3b6cfe10f1034d864b9038b0224).
  Independent verifier: CI on head f8e776d green (unit and real-SeaweedFS integration logs read); real SeaweedFS
  13/13 locally on a bounded fixture; uncapped whole-file mutation 383/383. All round-3 findings fixed. Reproduced:
  the record pin reads the latest version after listing, so a shrunk, re-hashed, retained record PUT in between
  restores 3 of 4 objects as success (Law 17). Also: create_backup with an existing backup_id succeeds and loses the
  original backup; a naive `now` raises a raw TypeError; notes lack the mutation figure.

## 2026-10-08T19:59Z — RETURNED: FTF-0903 (r5), FTF-0905 (r4), FTF-0516-P1 (r2), FTF-0908 (r2)
Independent verifiers, one per PR, each on the merge commit; CI on each PR head green (ci-verdict success, proving
test log lines read through the GitHub MCP); gates clean (product_truth 45/45, validate_spec --strict, data_exposure
OK). No producer session matches the certifier.
- **FTF-0903: RETURNED** (round 4 → 5; tests only; #648, 3dd4c18b52c6f5c5236ae8865d47bc3cf9e97ab5). Round-4 items
  hold and the test re-runs cleanly. GREEN: a 40-char prefix echo of a rejected ContentRef id leaks traceback or path
  text (V5e, V5f, V4d), because the test marker sits only at the tail of the traceback.
- **FTF-0905: RETURNED** (round 3 → 4; tests only; #649, c19d8d6c58ffe0cd93fb3401e4fca01bf9fe12dd). Round-3 items
  hold. GREEN: clearing the recorded leak set after the first CanaryLeakedError lets a second completion call succeed
  (F27).
- **FTF-0516-P1: RETURNED** (round 1 → 2; #639, 90382a2149757d22ea369c1c092233811a841944). 99 of 102 probes pass on
  PG16 as sovra_app. Open: the detectors_reported CHECK is unpinned (V11 GREEN); detector reports are hand-built and
  contradict the real FTF-0508 validator on the same batch; NUL in three inputs gives an untyped 500.
- **FTF-0908: RETURNED** (round 1 → 2; #640, 5511b2469141a4de51cc34a6cd47840a24691298). Deny logic holds; mutation
  68/68. Reproduced on real Postgres: a propagated denial rolls back its own audit row (0 rows persist). GREEN: the
  effective job profile in the audit (F9) and the audit identity fields (F5, F6, F7, F12). Malformed AuditContext
  raises untyped errors. Control-plane ruling: round 2 also adds the §11.2 agreement, subprocessor and
  deletion-behaviour fields and account/region matching, which the original DoD left out.
- Pending: FTF-0616 r5 (#645) verification still running.

## 2026-10-08T21:01Z — RETURNED: FTF-0616 (r6)
- **FTF-0616: RETURNED** (round 5 → 6; tests only; #645, 6c22d586f4937f4e7823519b373baaeeafe8e969). Independent
  verifier: CI on head 44a68cb green (live HTTPS gateway path ran); gates clean; whole-file mutation 324/324. Round-5
  fixes P-17 (uncalibrated approved echo) and P-18 (None in the approved set) hold and are RED-proven; #637 closed
  unmerged. GREEN: dropping the candidate-id clause lets a candidate that is also a calibration alias judge itself
  (F9, R5f); case/whitespace-normalised alias matching lets an uncalibrated model certify (R5n).

## 2026-10-08T22:02Z — CERTIFIED: FTF-0905; RETURNED: FTF-0903 (r6), FTF-1703 (r4), FTF-H010-P1 (r2)
Independent verifiers, one per PR, each on the merge commit; CI on each PR head green (ci-verdict success; proving
test log lines read through the GitHub MCP); gates clean (product_truth 45/45, validate_spec --strict,
data_exposure OK).
- **FTF-0905: CERTIFIED** (round 4; tests only; PR #653, merge 0dfce99276ffa7b2dd5d69fe6790bdda997cb708, head
  ca5c595). Session comparison: producer session_01QYenfH8kYCjedbkhSrrirQ ≠ certifier session_01QVznorVMjnmcttUhyDnoGR;
  the certifier appears nowhere on the PR. CI: ci-verdict 113526706452; gate job 113523609561 logged
  "FTF-0905 Canary-secret generator tests: all checks passed". Local: 2 passing runs. Probes: 54, 0 violations
  (sticky leak across 35 pairs, 50 repeated completes, 16 threads, clean runs complete). Faults: F27 and variants b-f
  RED via FTF-0905-R4-1; all earlier faults still RED. Production module unchanged (sha256 3eb4992c). UNWIRED
  (proving_symbol blank, no live caller; FTF-0901/0902 precedent). Open optional items: P24, P25, draw-sequence pin.
- **FTF-0903: RETURNED** (round 5 → 6; tests only; #652, 8f131c59eb33136a5bd9e67baf7aa4ed40917ba3). Round-5 faults
  RED. GREEN: a refusal raised inside `except ValueError` carries the caller text in `__context__` (C3, fully green);
  explicit cause, notes and attribute echoes (C1, C4, C5) go RED only through meta checks. Fix the whole class in the
  echo helpers.
- **FTF-1703: RETURNED** (round 3 → 4; #654, a9c802c75d347dcd0d8406f5403b2cecf2b957b9). Round-3 items fixed. Reproduced:
  an instance-attribute `to_dict` override on an exact-type trace passes provenance and the secret scan (P4) and
  admits a trace whose group, title and answer hash are reserved (P5, held-out leakage); one generator index admitted
  three times (P6); review str/decision subclasses bypass the generator-reviewer refusal and give pending -1 (P8).
- **FTF-H010-P1: RETURNED** (round 1 → 2; #644, 1288391468e00ca67e56e2d932bf660238175159). Reproduced on PG16 as
  sovra_app: a job can be inserted at succeeded and moved queued→succeeded→queued. GREEN: the imported-adapter and
  external-endpoint CHECKs, the sha256 pattern and a permissive cross-tenant INSERT policy (bypass tests refused for
  the wrong reason; immutability check is a SQL syntax error). Four bad POSTs accepted; origin↔import_kind unenforced.
- Pending: FTF-0616 r6 (#657), FTF-1602 r4 (#655).

## 2026-10-08T22:12Z — RETURNED: FTF-1602 (r5)
- **FTF-1602: RETURNED** (round 4 → 5; production plus tests; #655, 947ee2f4e201d734bbd60cdd9c2fc517cfddd621).
  Independent verifier: CI on head 338a5ea green (unit and real-SeaweedFS logs read); real SeaweedFS 14/14 locally;
  uncapped whole-file mutation 405/405. The pinned record read, sequential backup_id refusal and naive-now refusal
  hold. Reproduced on real SeaweedFS: two concurrent creates with one backup_id leave one caller holding a success
  record for an unverifiable backup (6/6, Law 17). GREEN: G1 (get_latest read) in the integration suite, with an ABA
  partial restore under it; G7 (count-only re-list) in both suites. Ruling: create_backup generates the backup_id.

## 2026-10-08T23:05Z — RETURNED: FTF-0616 (r7)
- **FTF-0616: RETURNED** (round 6 → 7; tests only; #657, 7209ac7d4a36198056d63f58ba7071a062492882). Independent
  verifier: CI on head d8cc1a3 green (live TLS path ran); gates clean; whole-file mutation 324/324. Round-6 F9, R5f,
  R5n and optional F15 are RED. GREEN: NFKC-normalised alias membership (R6f) and judge-id comparison (R6m); under
  each, a fullwidth-character variant of an approved but uncalibrated id certifies. Round 7 closes the normalisation
  class through one shared variant list.

## 2026-10-09T00:07Z — RETURNED: FTF-0309-P1 (r3)
- **FTF-0309-P1: RETURNED** (round 2 → 3; spec-matrix plus tests; #661, 060cad27eb8caf50143df8bfd830182500936e99).
  Independent verifier: CI on head becc0f8 green before the GitHub Actions billing outage (ci-verdict 113572684356,
  real-Postgres job logged its pass line); gates clean; whole-file mutation 68/68; 61/61 probes on PG16 as sovra_app.
  FA (second gateway call) fixed; the listed alias faults RED. Open: the proving_symbol DoD bullet (ruling: FTF-0309's
  entry gets build_model_capability_router, verified 46/46 and RED when unmounted on a scratch matrix); GREEN:
  NFKC-normalised alias lookup (FB-nfkc) and a lower-cased classifier model_id (FB5, an unlisted alias shows granted).
  Producer session only in the PR body (no commit trailers); certifier absent.

## 2026-10-09T07:48Z — RETURNED: FTF-R004-P1 (r2)
- **FTF-R004-P1: RETURNED** (round 1 → 2; #673, e1035b6fe6ecc34149116f2a4105eba05d097a5c). Independent verifier: CI on
  head 3101908 green (gate log "all checks passed"; mutation 78/78); gates clean; UNWIRED disclosed. Reproduced: capture
  returns prompt text from instructions, prompt variables, prediction, conversation and extra_body.messages; a bridge
  rewrite (before 0.8 → after 0.7, approved 0.7) and a stale declaration pass as preserved. GREEN: CONTENT_KEYS
  removals (F5, F6), float tolerance (F8), plain == on declared values (F10). Reason codes and the tuple digest are
  missing; cyclic input raises a bare RecursionError; the declaration is mutable after validation.
- CI note: GitHub Actions refused all jobs for billing from ~23:05Z 2026-10-08 to ~07:20Z 2026-10-09.

## 2026-10-09T10:06Z — RETURNED: FTF-0903 (r7), FTF-1703 (r5), FTF-0516-P1 (r3)
Independent verifiers on the merge commits; CI on each PR head green (ci-verdict success, proving-test log lines read
through the GitHub MCP); gates clean (product_truth 47/47 locally, validate_spec --strict, data_exposure OK). No
producer session matches the certifier.
- **FTF-0903: RETURNED** (round 6 → 7; tests only; #663, 5589cc662481e52f1e791cde9a6bfd3df364a1d8). Round-6 C1/C3/C4/C5
  RED. GREEN: cause/note/attribute and KeyError-context faults on the event-kind, fields and field-name paths (the
  META-LEAK suffix is checked at one call site only) and a log line echoing the value (L1). Ruling: certifiable once
  every refusal site is covered by the shared META-LEAK check plus log capture.
- **FTF-1703: RETURNED** (round 4 → 5; #665, 3bf864aa3610e620239d15bfa60eec2f1329aa83). Reproduced held-out leakage:
  a dict-subclass `__dict__` flips the trace to a reserved generator index after the only reserved check (T1).
  Round 4 deleted the regenerated-object reserved check and secret scan the DoD required, while the notes still claim
  them (Law 2). GREEN: exact ReviewDecision type (F6).
- **FTF-0516-P1: RETURNED** (round 2 → 3; tests only; #671, 167ec9703f29af89dce421f648f0c5f8e7117847). Real detector
  reports, V10, V11 and NUL handling fixed. GREEN: the lone-surrogate guard (N2, N6; 500s return on real routes) and
  the detector_versions object-type clause (N8). Route-level NUL tests missing.
- Pending: FTF-0616 r7 (#669), FTF-0309-P1 r3 (#667), FTF-0908 r2 (#670); queued next: FTF-H010-P1 r2 (#668),
  FTF-R004-P1 r2 (#675), FTF-0501 r2 (#658).

## 2026-10-09T10:26Z — CERTIFIED: slice FTF-0309-P1; RETURNED: FTF-0908 (r3); QUEUED: FTF-0309 UI
- **FTF-0309-P1 (child slice of FTF-0309): CERTIFIED** (round 3; PR #667, merge
  8a829cc2b8d35f966303255c4d258cf1643b23cb, head b7819ac). Session comparison: producer session_01LG2dBH7dLGwKUP9AEoxK8X
  (main-sync commit session_01FzXZvoFHAhmXv43jugSSPS) ≠ certifier session_01QVznorVMjnmcttUhyDnoGR, which appears nowhere
  on the PR. CI: ci-verdict 113729777424; gate job 113718342112 "FTF-0309-P1 capability route tests: all checks passed";
  model-capabilities-integration job 113718480304 (PG16) "capability real proof: all checks passed". FTF-0309's
  proving_symbol is build_model_capability_router: product_truth 46/46 in CI, and RED (46/47, "no live callers") when the
  include_router block is removed. One shared Unicode variant set pins both lookups: FB-nfkc and FB5 RED in both suites;
  36 faults injected, every clause fault RED; whole-file mutation 68/68; 83/84 probes (the exception: a NUL in the
  model name gives a 500 after discovery, shared with the certified FTF-0308-P1 routes, no clause covers it - follow-up
  O1). Status count unchanged (child slice; FTF-0309 stays open for its UI).
- **FTF-0908: RETURNED** (round 2 → 3; #670, 9605bb9affd815aa07d977a9f697a198541796c0). All six round-2 items fixed;
  mutation 66/66. Reproduced on PG16 as sovra_app: the denial's own commit ends the transaction holding the tenant
  context, so a second denial raises a raw RLS error with no audit row and the caller's later reads return 0 tenant
  rows; a caller's pending row is committed by the denial.
- **Queued:** FTF-0309 UI (Backlog), gated on every dimension known, sufficient tested VRAM, trainable and access granted.

## 2026-10-09T11:38Z — RETURNED: FTF-0616 (r8)
- **FTF-0616: RETURNED** (round 7 → 8; tests only; #669, ecc0186581813e14cfd5213e083fbce93b818048). Independent
  verifier: CI on head 26d9a5e green (live TLS path ran); gates clean; whole-file mutation 324/324. Production correct:
  0 of 3377 generated Unicode echo variants accepted. R6f and R6m RED. GREEN: 44 of 114 normaliser faults (edge
  zero-width/BOM strip, CR/LF, NBSP, U+3000, U+2011, and configured-side-only normalisation), each letting an approved
  but uncalibrated id produce a verdict; the judge-id variant loop runs the pass verdict only. Ruling: round 8 replaces
  the fixed list with property-generated variants and is the last exactness round.

## 2026-10-09T12:10Z — RETURNED: FTF-0903 (r8), FTF-H010-P1 (r3), FTF-0516-P1 (r4), FTF-R004-P1 (r3)
Independent verifiers on the merge commits; CI on each PR head green (ci-verdict success, proving-test log lines read
through the GitHub MCP); gates clean (product_truth 47/47 locally, validate_spec --strict, data_exposure OK). No
producer session matches the certifier.
- **FTF-0903: RETURNED** (round 7 → 8; tests only; #680, 41d77de5f85564391b098c9e620b485b965c7840). Round-7 items met.
  GREEN: the whole fault class at the missing-required-field refusal site (cause, note, attribute, log, print, warn),
  the one site outside the shared META-LEAK check and log capture. Per the 0c ruling this is the last site.
- **FTF-H010-P1: RETURNED** (round 2 → 3; #668, 8dc1495ac0ed140ae41365b4233254a969e3e762). All round-2 production fixes
  hold on PG16 as sovra_app; no clause violated by the shipped code. GREEN: terminal-state block, a permissive
  cross-tenant UPDATE policy (reproduced an org2 row changed under org1), two origin/import-kind CHECK weakenings,
  partial sha256 patterns. Submission doc claims an allowlist and a .strip() check not in the diff (Law 2).
- **FTF-0516-P1: RETURNED** (round 3 → 4; tests only; #678, 167d32e35d077d1cdc1894708c0943a00e938766). Round-3 items
  delivered; 108/108 probes. GREEN: refusing non-ASCII or astral reasons (N9b, N9c) and the detector-path surrogate
  guard (N6c); a test comment claims a store-level test that does not exist.
- **FTF-R004-P1: RETURNED** (round 2 → 3; #675, da1a5b03a79f476a927dd353e33a76459a9ff1e3). Round-2 items fixed.
  Reproduced: the recursive content scrub erases JSON-schema properties named like content keys, so a schema type
  change passes as preserved (G1); int-key logit_bias entries dropped (G2); bare TypeError/KeyError (G3); inexact set
  and mapping compares (G4); further content keys captured verbatim (G5). Ruling: allow-list capture with digests.
- Pending: FTF-0501 r2 (#658), FTF-R005 (#674); queued next: FTF-R004-P2 (#677).

## 2026-10-09T12:21Z — RETURNED: FTF-0501 (r3, API half)
- **FTF-0501: RETURNED** (round 2 → 3; #658, 9149174209a0672193a37d8757b16b5c29e22a9d). Independent verifier on PG16 as
  sovra_app plus bounded real SeaweedFS: XLSX package check, the flaky refusal (12/12 green), the real-route CI job and
  proving_symbol hold. Reproduced: the §67.5 sweeper aborts nothing on SeaweedFS (no Initiated, exits 0, one unmarked
  page); an unbounded central-directory read (heap ~3x the object); a UTF-16 DTD/ENTITY bypass that let an entity-typed
  workbook become an accepted Dataset version on the real routes. GREEN: the three §67.3 bound values (F5, F18, F19).

## 2026-10-09T12:25Z — RETURNED: FTF-R005 (r6)
- **FTF-R005: RETURNED** (rounds 2-5 → 6; #674, a2e419fe80cec75a37af46edd4481fec7b852bd0; rounds 3-5 were submitted
  without Control-plane verification). Independent verifier on a real MLflow 3.1.1 server and PG16 as sovra_app: Law 17
  statuses, bounded retries, durable outbox reuse and paging hold. Reproduced: run-level drift is skipped when the
  mutable payload tag is deleted or the run is tampered with before completion, so a drifted run resolves SYNCED (D5);
  a newline/CRLF token leaks through an uncaught ValueError; record() rewrites resolved outbox rows. Notes overclaim and
  evidence_hash is stale (Law 2).

## 2026-10-09T14:06Z — CERTIFIED: FTF-0903; RETURNED: FTF-0309 (r2), FTF-R004-P2 (r2), FTF-1703 (r6), FTF-H010-P1 (r4), FTF-0516-P1 (r5)
Independent verifiers on the merge commits; CI on each PR head green (ci-verdict success, proving-test log lines read
through the GitHub MCP); gates clean (product_truth 47/47, validate_spec --strict, data_exposure OK).
- **FTF-0903: CERTIFIED** (round 8; tests only; PR #685, merge c003010bb0f70ea4270316c02142817734f617b5, head 1261b72).
  Session comparison: producer session_01DbtUsTabsqwV6ZUcmn153B ≠ certifier session_01QVznorVMjnmcttUhyDnoGR, which
  appears nowhere on the PR. CI: ci-verdict 113817955863; gate job 113814241100 "FTF-0903 Content-free telemetry tests:
  all checks passed". 4 local runs; 130 probes (every missing-required field with markers in all other fields,
  8x600 threaded refusals) clean; 96 faults, 94 RED (the whole missing-required class now RED); the 2 GREEN are
  hand-picked log-capture bypasses at covered sites, non-blocking under the 0c ruling. Production c83da8db unchanged
  since round 3. UNWIRED (proving_symbol blank). Optional: capture sys.__stderr__/fd 2; wrap a raising fields.items().
- **FTF-0309: RETURNED** (round 1 → 2; #683, e792efc836880e512e29f3dca379246d8d980fe7). Reproduced negative acceptance:
  Train stays enabled and onTrain fires for a model never checked (prop switch, late response, mismatched echo); a
  string license flag and a missing declared VRAM minimum enable Train; inferred per-row dates; a simulated "Training
  requested" with no handler (Law 11). The UI reads fields absent from the frozen contract.
- **FTF-R004-P2: RETURNED** (round 1 → 2; #677, 6dfdb1600e03256e1abbc47f5e59bb5048709f6b). Reproduced: remote media
  inside a function_call_output array, and unscreened top-level keys (mcp_servers, prompt, search_parameters, container,
  extra_body.tools) allowed. Ruling: top-level allow-list; the stricter data:-prefix check is accepted.
- **FTF-1703: RETURNED** (round 5 → 6; #682, f919b766d6b643cf496c61509083ac0e76d4af30). Reproduced: a str-subclass key
  in the instance dict re-binds index/trace_id after verification (an unreviewed trace approved) and swallows a
  conflicting rejection. Ruling: plain-JSON input only; the builder constructs its own objects.
- **FTF-H010-P1: RETURNED** (round 3 → 4; tests only; #686, 784a41623d01353cdeadb9359845a8d57322a622). Production correct.
  GREEN: an origin/import-kind widening, sha256 patterns with space/dash and a pattern rejecting every valid hash,
  validating→queued. Ruling: complete constraint and transition tables.
- **FTF-0516-P1: RETURNED** (round 4 → 5; tests only; #688, a32be8b37784c73999c095b8eca23ae0e49cd44a). Production
  correct. GREEN: reason length bounds (1999, 200, byte-counted, min 3). Ruling: one boundary table per text input.

## 2026-10-09T16:19Z — CERTIFIED: slice FTF-0516-P1; RETURNED: FTF-0309 (r3), FTF-R005 (r7), FTF-R004-P1 (r4); UN-PARKED: FTF-0516 UI
- **FTF-0516-P1 (child slice of FTF-0516): CERTIFIED** (round 5; tests only; PR #695, merge
  3ac6cea74b9cdf97ed73c735adf16451e693db58, head 71feff4). Session comparison: producer session_01HfSF1ZAfeerrggmNDmVvhz
  ≠ certifier session_01QVznorVMjnmcttUhyDnoGR, which appears nowhere on the PR. CI: ci-verdict 113867495738;
  dataset-quality-issues-integration job 113862797654 (postgres:16) "quality issue real proof: all checks passed".
  Local on PG16 as sovra_app: integration 328 checks, unit 590, 0 failed. The 0l boundary table (reason 1..2000,
  idempotency_key 1..200, detector 1..64; ASCII, Arabic, emoji; empty/whitespace/one-past refused with nothing
  written) is in place; 57 faults, every clause-level fault RED (N13, N13b, N13c, N14 now RED); 189/190 probes (the
  miss a verifier expectation). Production unchanged since round 4. UNWIRED (record_quality_report has no live
  producer). Count unchanged (child slice).
- **FTF-0309: RETURNED** (round 2 → 3; #696, a70296e5d4a04e308fa44032387f216e77f2bf72). Real P1 responses handled
  correctly; the UI still accepts an out-of-contract `state: "known"` that enables Train (K1, K3). GREEN: reload not
  cleared, org not in the identity key, no stale guard after json(), VRAM off-by-one. Ruling: only the frozen
  `unknown` state is valid.
- **FTF-R005: RETURNED** (round 6 → 7; #691, 4b62130294fb99acab0f912cabc02d682e3ae66e). Round-6 fixes hold. Regression
  reproduced on a real MLflow 3.1.1 server: strictness gated on the live run status lets a run flipped back to RUNNING
  resolve SYNCED (D7). No read-back before SYNCED (Q2). record() without a row lock rewrites a resolved row under
  concurrency (D8) and does not report the mismatch.
- **FTF-R004-P1: RETURNED** (round 3 → 4; tests only; #689, a2132159eea3ce3e6050b77b97678337e5569961). Production holds
  every clause. GREEN: digest sensitivity to nested values, order, length and duplicates (FG-FG6).
- **Un-parked:** FTF-0516 UI to the Backlog.
- Pending: FTF-0616 r8 (#690).

## 2026-10-09T18:18Z — CERTIFIED: slices FTF-H010-P1, FTF-R004-P2; RETURNED: FTF-0309 (r4), FTF-0904 (r2), FTF-1703 (r7)
- **FTF-H010-P1 (child slice of FTF-H010): CERTIFIED** (round 4; tests only; PR #699, merge
  f917395dd97dd33102128da10ee7424f3cb3bbe0, head d1db6b7). Session comparison: producer session_01Fh1ZUSqz3NqfJuhJukv7oS
  (earlier rounds session_01DbtUsTabsqwV6ZUcmn153B) ≠ certifier session_01QVznorVMjnmcttUhyDnoGR, absent from the PR.
  CI: ci-verdict 113900530075; model-import-integration job 113896077358 (postgres:16.15) "real model import proof: all
  checks passed". Complete tables per the 0d ruling: 96 origin/import-kind rows, per-column sha256 table, full 5x5 job
  transition matrix. 64 faults, 58 RED (N1-N3, N10, N11 now RED); GREEN only equivalent mutants and two hand-picked
  alphabet variants of the closed class (non-blocking). 768-row and 408-substitution probes: 0 violations. Production
  unchanged since round 2. FTF-H010's real endpoint/weights/GPU clauses stay BLOCKED_EXTERNAL.
- **FTF-R004-P2 (child slice of FTF-R004): CERTIFIED** (round 2; PR #702, merge 029dfa5218576b766cc4bec724d52ea43ab6529f,
  head 6ebd181). Session comparison: producer session_019UYvGwj2PggkDFprmJ9Nuz (round 1 session_01Jq9GyGEfDEoX8dJbyK3yvN)
  ≠ certifier, absent from the PR. CI: ci-verdict 113922024647; gate job 113918693513 "provider boundary: all 548 checks
  passed"; uncapped whole-file mutation 275/275. function_call_output screened; exact top-level allow-list; F18 pinned.
  116 probes, 0 failures; 21 of 23 faults RED (the 2 GREEN are one-key allow-list additions, non-blocking under the
  ruling). UNWIRED until P3.
- **FTF-0309: RETURNED** (round 3 → 4; tests only; #706, b09b05dbabbdadbcd12f369574c9055458123d2d). Known path removed;
  production holds. GREEN: the strict-parse faults the 0c ruling named (catalog_version, evidence, enums, trainable) and
  a regression on the availability blocker text (G4).
- **FTF-0904: RETURNED** (round 1 → 2; #703, 2c2faa5028a4a23878b44b56e29e8f412dc6d828). Negative acceptance holds.
  Reproduced acceptance leaks: a decoded percent layer re-exposes secrets the raw scan redacts; ASGI/WSGI header forms
  unredacted; extra= on non-root loggers unscrubbed. Quadratic regex under the handler lock (required fix).
- **FTF-1703: RETURNED** (round 6 → 7; #698, 2799c7837b054d061eb7ce73374ebc39a6ac8f54). Plain-JSON ruling in place;
  production correct; the ruling-named fault (FB2/FB3, read index/trace_id from the caller after parsing) stays GREEN.
- Pending: FTF-0616 r8 (#690).

## 2026-10-09T18:33Z — CERTIFIED: FTF-0616 (adapter scope)
- **FTF-0616: CERTIFIED** for the adapter scope (round 8; tests only; PR #690, merge
  4a0de86713e272749ba2ebe0d358e59a2957be2a, head bad5dfc). The real-judge clause stays BLOCKED_EXTERNAL (org LiteLLM
  gateway key, dated owner action); FTF-1810 precedent and the QUEUE 0b certification rule. Session comparison: producer
  session_01BQxQSRYYLDbqgvAcmKhSpR ≠ certifier session_01QVznorVMjnmcttUhyDnoGR, which appears nowhere on the PR. CI:
  ci-verdict 113837256048; gate job 113833563112 "FTF-0616 llm judge adapter tests: all checks passed" with the live
  TLS gateway path. Property-generated Unicode variants at both comparison points under pass and fail, with the
  configured-side mirror: all 44 round-7 GREEN faults now RED; 114/114 normaliser faults and 43/43 regression faults
  RED; 5258 new probes plus the 3377-variant sweep, 0 accepted on production, no over-refusal. Production
  d859f65a unchanged since round 5; uncapped whole-file mutation 324/324. In-class code points the CI sample skips (U+2029,
  LRM/RLM, U+00AD, sub/compat-only NFKC) go RED under the suite's full sweep; advisory to add them to the required list.
  UNWIRED (proving_symbol blank).

## 2026-10-09T20:10Z — CERTIFIED: FTF-0309 (138/302), slice FTF-R004-P1; RETURNED: FTF-0516 UI (r2)
- **FTF-0309: CERTIFIED** (round 4; tests only; PR #713, merge e2555b6c732dcd184c8fd270fb9301bc05c85dde, head ca1f85d8).
  Session comparison: producer session_01RW8pymEbYM5zLuunuZXqtf ≠ certifier session_01QVznorVMjnmcttUhyDnoGR, absent
  from the PR. CI: ci-verdict 113982177055; gate job 113978535363 (read via GitHub MCP get_job_logs), 304 vitest tests.
  The 0c strict-parse table (catalog_version, evidence, enums, trainable) and the G4 availability blocker text are pinned;
  96 faults, 69 RED, the GREEN rows equivalent or outside the ruling; 88 adversarial probes, 0 violations. Law 7: the
  UI component has no caller outside its spec, so FTF-0309 stays UNWIRED for its screen and is not counted product-true
  until FTF-H029 mounts it. status.py prints 46 product-true because FTF-0309's proving_symbol is its live,
  product_truth_check-proven P1 API route (build_model_capability_router); the reported figure stays 45.
- **FTF-R004-P1 (child slice of FTF-R004): CERTIFIED** (round 4; tests only; PR #707, merge
  f856b47e0be3c22a53b354860d97e8a8405f487c, head a92a341). Session comparison: producer session_01GXUZnGbQLHhSHJ3yjBU5yh ≠
  certifier, absent from the PR. CI: ci-verdict 113948036357; gate job 113943708703 (4203/4203 lines read); mutation gate
  113943709248 success (production unchanged, round 3 140/140 killed). Digest sensitivity pinned through capture on both
  sides: FG, FG2, FG3, FG4, FG5, FG6 all RED; 55 probes clean. UNWIRED until P3; FTF-R004-P3 is now dependency-ready.
- **FTF-0516 (UI): RETURNED** (round 1 → 2; PR #704, merge 8cc6970bf2dabe2e464a12028b08cf3fd710345e, head 351f6bc).
  Producer session_019UYvGwj2PggkDFprmJ9Nuz ≠ certifier. CI: ci-verdict 113930412003, gate job 113927092962 (288 vitest
  tests). Correct against the real P1 API on PG16 (exclude/correct/waive, 403, 409, 404, cross-tenant, identical-retry
  replay = one row). Faults: "Decision recorded" shown when the response's successor, positions, engine_decision,
  resolution_version or reason differ from what was sent (M1-M5c, F07); parseIssue accepts a nested resolution for
  another issue, a waived state with an exclude decision, blank actor_id or reason (P-n1..n4, F72); a submit during a
  reload leaves Loading stuck; 30 clause-level faults GREEN (78 mutants, 45 RED). Class-closing ruling: one
  response-binding check over every field sent.
- **Backlog (Control-plane design):** FTF-0616-F1 (judge-call deployment fingerprint + cache bypass; FTF-0616 stays
  CERTIFIED while F1 re-runs its full evidence, Law 15), FTF-0617 (zero-egress judge policy, UNWIRED), FTF-H010-P2..P4
  (endpoint import validation, weights/adapter validation, roles + continuation eligibility). Certified entries
  FTF-0309-P1, FTF-0309, FTF-H010-P1 and FTF-0516-P1 removed from the Backlog.
- Pending: FTF-0908 r3 (#693), FTF-R005 r7 (#708).

## 2026-10-09T20:35Z — RETURNED: FTF-0908 (r4)
- **FTF-0908: RETURNED** (round 3 → 4; PR #693, merge ca1b6dd1d4e2db83b977c7e2e519f03ae422aacf, head e3e1e3b). Producer
  session_01SN5XfLBY7kKmyy3Loa5Gjy ≠ certifier session_01QVznorVMjnmcttUhyDnoGR. CI: ci-verdict 113979085428;
  external-provider-denial-integration job 113964056545 and gate job 113963935230 "all checks passed"; mutation-gate
  113963934891 45/45 killed. Round-3 DB3/DB3b/DB4 fixed (27 real-PG probes on PG16 as sovra_app). Return reason: fault F2
  (post-commit tenant re-bind made session-level) stays GREEN in every suite and leaks the tenant to a fresh checkout on a
  pool without the reset guard (guc=<org1>, visible_rows=1); mitigated today only by the API engine's reset guard.
  Ruling: the raw InvalidRequestError inside `s.begin()` (P12) is a disclosure (fails closed, audit durable, no
  production caller).

## 2026-10-09T21:05Z — CERTIFIED: FTF-R005 (139/302)
- **FTF-R005: CERTIFIED** (round 7; PR #708, merge 03442f67294975313db37ec297dea0cc24a6ca9a, head 5cae2624; production
  b400e96a = evidence_hash at submission). Session comparison: producer session_01U1YRffpzqBJSJEesvRCUCK ≠ certifier
  session_01QVznorVMjnmcttUhyDnoGR, absent from the PR. CI: ci-verdict 113955681011; gate job 113951200403 "FTF-R005
  MLflow projection tests: all checks passed" and "round-7 tests: all checks passed"; mlflow-durable-outbox-integration
  113951311137 (PG16.15, mlflow 3.1.1) and mlflow-projection-integration 113951311240 passed; mutation-gate 113951200817
  85/85 killed. All 0e items class-closed: strictness from Factory's own completion record (R7-Q1/Q1b, D7 via the real
  outbox row), read-back before SYNCED (Q2 + 10 tamper probes FAILED with exact drift codes), `record()` row lock with
  reported disagreement (R7-D2/D3b/D8; 45 concurrent pairs, no stale write). Local real MLflow 3.1.1 + PG16 as sovra_app:
  876 suite checks pass; 69 probes, 0 violations. 29 faults, 27 RED; GREEN H7 (SYNCED-state term in the same-outcome
  check, errs safe, sibling variants RED) and C3 (token check narrowed to CR/LF, minor F8) are non-blocking under the
  ruling. Observations for the wiring task: concurrent sweepers on one new row can create a duplicate MLflow run (O1,
  serialize per row); apply_to keeps stale drift codes (O2); AMBIGUOUS over a failed row unreported (O3); C1 control
  characters accepted in tokens, 429 classed rejected, a raising ResultSource aborts the sweep (O4). UNWIRED
  (proving_symbol blank).
