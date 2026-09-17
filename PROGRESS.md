# PROGRESS — Sovra
Updated: 2026-09-17T17:10:00Z

## 2026-09-17 — FTF-R017 (PR #75): found and fixed why CI silently never ran (mergeable_state: dirty), CI now genuinely running
Continuation of this same relay shift, after pushing the debug-
instrumentation commit (`a43dff9`) below. Re-checked PR #75's CI status to
confirm the new head triggered a run — `pull_request_read
get_check_runs` still returned `total_count: 0`, and stayed at 0 across a
second check several minutes later. Rather than assume a webhook delay
and wait indefinitely, read `pull_request_read.get` directly:
`mergeable_state: "dirty"` — a real, GitHub-computed conflict against
`main`, caused by the Control-plane certifier session (documented below)
having pushed its review commits (`730d87d`, `b52f83c`) directly to
`main` while PR #75 was still open on an older base. This is the exact
same structural failure mode FTF-0004 hit during its own review cycle
(`docs/certifications/queue.md`'s "not in a certifiable state" entry): a
PR that goes `dirty` against a newer `main` stops GitHub Actions from
generating check runs for further pushes at all, silently — not a queue
delay, not a flake, and not something the debug commit itself broke.

Fixed the same way that precedent did: fetched current `main`, merged it
into `claude/wizardly-fermat-ci7leb` in the same worktree. All three
conflicts were pure tracking-doc conflicts (`PROGRESS.md`/`QUEUE.md`/
`docs/certifications/queue.md`) — no code conflict — resolved by keeping
both sides' substantive content in chronological order. While resolving,
caught two places where git's own 3-way merge had silently picked the
stale side *without* flagging a conflict at all: `QUEUE.md`'s `## Status`
line and its own `## Provisional` section had reverted to a stale
`Provisional: 1 (FTF-R017...)` entry that `main`'s own certifier review
had already correctly cleared when reclassifying this into `## Active` —
caught by diffing the merged file against `git show origin/main:QUEUE.md`
directly rather than trusting a clean `git merge` exit code, and fixed to
match `main`'s real state.

Re-ran the full regression suite on the merged state before pushing: `uv
run ruff check`/`format --check`/`mypy .` clean; `pnpm run lint`/
`format:check`/`typecheck` clean; all 16 task negative-test-proof scripts
(the existing 15 plus this task's own `test_ftf_r017.py`) pass; `uv run
pytest tests/unit` (3 passed), `pnpm exec vitest run` (2 passed),
`scripts/validate_spec.py --strict` clean. No code was touched by this
merge — tracking docs only.

Pushed the merge commit (`a2b2123`) to the same PR #75 branch. Confirmed
via `pull_request_read.get`: `mergeable_state` is now `clean`. Confirmed
via `get_check_runs`: a real CI run (35250917560) is now genuinely
`in_progress`/`queued` on all three jobs, including
`storage-semantics-integration` — the debug instrumentation from
`a43dff9` will run for the first time on this run. Recorded the
mergeable-state finding and fix in `docs/certifications/queue.md` as its
own entry (not mixed into either the producer's or the certifier's prior
entries).

STEP 4: still not a stop condition in the strict sense — waiting on the
now-unblocked CI run's actual result (subscribed to PR #75's activity)
before drawing or acting on any root-cause conclusion about the 500/501
failures. This is the first CI run that will actually exercise the debug
instrumentation, so there is nothing further to do until it completes.
`certification_state` for FTF-R017 remains `PROVISIONAL` (unchanged).

Certified: 15/300 (unchanged). Provisional: 0 (unchanged). Product-true: 0
(unchanged).

## 2026-09-17 — FTF-R017 (PR #75): per-request debug instrumentation added, root cause not yet confirmed, waiting on CI
Autonomous relay shift (scheduled firing, no live user present). STEP 0:
`sha256sum CLAUDE.md` matched committed `CLAUDE.md.sha256` — no drift.
STEP 0.1: read PROGRESS.md/QUEUE.md/docs/certifications/queue.md/
BLOCKER.md (all resolved history, no open blocker), cross-checked against
live repo state via `git fetch origin main` (local designated branch
matched `origin/main` exactly at `b52f83c`, no divergence) and
`list_pull_requests`.

Two open PRs found: #75 (FTF-R017, flagged in QUEUE.md "Active" — a real,
reproducible CI failure on `storage-semantics-integration`, not yet
root-caused) and #67 (the still-unconfirmed FTF-0102 "owner approved
option 1" claim). Re-checked #67's comments: still only this session's
own prior-shift finding comment, no owner-authored corroboration, still
`mergeable_state: dirty` — left exactly as-is, not re-litigated.

Picked up #75/FTF-R017 per QUEUE.md's own explicit instruction ("Next
relay shift on FTF-R017: root-cause the 500/501 responses... push to the
existing branch/PR #75"). Read the actual CI job log for the most recent
failing run directly (`get_job_logs`, job 105262713015, run 35239135374,
head `fb0c2ac`) rather than trusting the prior shift's own PROGRESS.md
inference about which specific S3 operation fails inside each test —
found that claim ("500 InternalError on object-write operations
PutObject/CreateMultipartUpload/UploadPart... 501 specifically on
PutBucketVersioning") was not actually verifiable from the log itself:
`tests/integration/test_storage_semantics.py`'s `main()` only catches
`S3Error` at the whole-test-function level, so the log shows 9 test-level
failures with status/code/message but never which real request inside
each test triggered it — the prior shift's operation attribution was a
plausible but unproven guess (matching test names to their presumed
first differentiating call), not evidence per Law 1.

Before writing any fix, reviewed `apps/api/app/services/
storage_semantics_client.py`'s `sign_request` line by line against the
published AWS SigV4 algorithm (canonical request, canonical headers/
query-string construction, signing-key derivation, payload-hash
computation and its reuse in both the signature and the transmitted
body) — found no structural defect, and the exact same code path is used
by every operation including `create_bucket` (presumed, not proven, to
succeed). Searched known SeaweedFS S3-gateway GitHub issues for this
task's exact error wording (`InternalError`/"please try again",
`NotImplemented`/"a header you provided implies functionality") — found
real issues with each message, but for several different underlying
causes apiece (volume-growth capacity races, a Content-MD5 digest
mismatch that a later SeaweedFS PR changed from a generic 500 to a
specific `BadDigest`, filer path-conflict cases, and PutBucketTagging's
own NotImplemented precedent) — none pinned down without knowing which
real request in this fixture is the one failing. Concluded that pushing
another single speculative fix (as the prior two commits on this PR each
did) risks a third wrong guess and a third CI round-trip; the actual gap
is observability, not a specific known bug to patch blind.

**Delivered (diagnosis-only, no behavior change to any existing path):**
`S3Client.__init__` gained an opt-in `debug: bool = False` keyword
(default unchanged for every other caller). `_request` now prints, only
when `debug=True`: the request shape (method, canonical URI, sorted query
key names, sorted header names, body length) before executing, and the
real response status on success or `(status, code, message)` on an
`S3Error` after. Deliberately never prints credentials, the
`Authorization` header, or any signature material — reviewed the diff
specifically for this before pushing. Wired `debug=True` only into
`tests/integration/test_storage_semantics.py`'s own client construction
(the `wrong_client` used solely for the identity-rejection probe is left
undebugged — not part of this mystery).

**Regression check (fresh, unshallowed worktree at the PR's real head):**
`uv sync`/`pnpm install --frozen-lockfile` (fresh container). `uv run
ruff check`/`format --check` clean on both changed files; `uv run mypy`
clean on the client module. This task's own non-Docker unit test
(`tests/tasks/test_ftf_r017.py`, SigV4 signing math + every
negative_acceptance case) — clean, unaffected (the debug flag defaults
False and that test never sets it). Full existing regression set — all
15 task scripts (`test_ftf_0001_monorepo_layout.py` through
`test_safe_parallel_executor.py`/`test_profile_scope.py`) pass; two
Python failures and one JS failure seen on the first pass
(`test_ftf_0002.py`/`test_ftf_0003.py`/`test_ftf_h001.py`) were confirmed
to be pre-existing environment artifacts of this fresh container/shallow
clone (missing `node_modules` before `pnpm install`; `git fetch
--unshallow` needed for `test_ftf_h001.py`'s real-commit-resolution
check, same documented artifact prior shifts hit) — not caused by this
change; re-ran clean after fixing the environment, confirming no
regression. `uv run pytest tests/unit` (3 passed), `pnpm exec vitest run`
(2 passed), `scripts/validate_spec.py --strict` clean.

Pushed as commit `a43dff9` directly to the existing PR #75 branch
(`claude/wizardly-fermat-ci7leb`) — not a new/duplicate PR, per QUEUE.md's
own explicit instruction. Subscribed to PR #75's activity. CI triggered
on the new head (confirmed a new workflow run started, superseding the
three prior runs on `fb0c2ac`/`42cae14`/`9d7bf82`).

STEP 4: not a stop condition in the usual sense (not N/N proven, no true
blocker, not stuck — this is the first debug-instrumented push, not a
third failed guess) but no further useful action is available until the
new CI run's actual per-request debug output is readable — continuing to
guess at the root cause without it would repeat exactly the mistake this
shift diagnosed in the prior two commits. Waiting on the subscribed PR
activity event for the new run's result before drawing or acting on any
conclusion. `certification_state` for FTF-R017 remains `PROVISIONAL`
(unchanged — still not certifiable, CI still not green). FTF-0102 remains
blocked on the standing owner ask; PR #67's claim remains unconfirmed and
unactioned (no new corroboration this shift).

Certified: 15/300 (unchanged). Provisional: 0 (unchanged — FTF-R017 was
never counted as Provisional in QUEUE.md's own tracker; it's tracked only
in "Active" pending a green CI run). Product-true: 0 (unchanged).

## 2026-09-17 — Control plane: FTF-R017 (PR #75) NOT CERTIFIED — real CI failure on the PR's own new job
Scheduled CERTIFY-mode session, triggered by PR #75's own
`pull_request.opened` webhook. Swept `docs/certifications/queue.md` for
anything PROVISIONAL: `main`'s own copy showed nothing pending (last
action was FTF-R011 CERTIFIED), but PR #75 (not yet merged) carried a
fresh FTF-R017 PROVISIONAL submission on its own branch
(`claude/wizardly-fermat-ci7leb`), same "lives on the branch until
merged" shape as the FTF-0004 precedent.

Session-ID check first: all 4 PR commits carry
`Claude-Session: .../session_016bZ7g5DPgSU8eYMTo8H5NY`; this session's ID
(`session_01EF89AyieNmjbMTcNSvVkNo`) does not match — independent.

Checked CI for real via `pull_request_read get_check_runs` +
`get_job_logs` rather than trusting the PR body's self-reported
checklist (which itself left CI as an open checkbox). The PR's new
`storage-semantics-integration` job failed on its first head (`9d7bf82`,
run 35238767213) with `RemoteDisconnected` on all 9 storage-semantics
tests. The PR advanced to a new head (`42cae14`, a QUEUE.md-only
bookkeeping commit — no test/implementation code changed) mid-review; the
same job failed again on the new run (35238865287), this time with
`ConnectionResetError(104, 'Connection reset by peer')` on all 9 tests —
confirming it's a real, reproducible race rather than a one-off flake.

Root cause read directly from the PR diff: `tests/integration/
test_storage_semantics.py`'s `main()` only waits for the S3 fixture's TCP
port to accept connections (`_wait_for_tcp`) before immediately issuing
real S3 API calls — SeaweedFS's TCP listener opens before its S3 HTTP
layer is actually ready to serve, so the tests race it and lose. This is
the exact bug class FTF-0004's own `/health`-based (not bare-TCP)
readiness polling already exists to avoid, including a prior CI-failure/
fix cycle on this exact class of bug during FTF-0004's own review.

Not evaluating the SigV4 client, component-maintenance ledger, or the
Docker-independent `tests/tasks/test_ftf_r017.py` suite's content further
— per CERTIFY step 3, CI hasn't actually passed on the PR's real head, so
certification would be premature regardless of the rest of the diff.
`certification_state` stays `PROVISIONAL` (not downgraded further — this
is a scoped fix, not a redo). Full finding: `docs/certifications/
queue.md`, "Control plane review — 2026-09-17 — NOT CERTIFIED (real CI
failure on this PR's own new job)". `QUEUE.md`'s "Active" section flags
the fix needed for the next relay shift (real HTTP-level readiness probe
before running the storage-semantics assertions, pushed to the same PR
#75 branch, not a new PR). Did not merge PR #75, did not touch anything
outside certification (no code fix attempted, no other task work, no
queue reprioritization).

## 2026-09-17 — Control plane: FTF-R011 CERTIFIED (PR #72 independently verified)
Continuation of this same scheduled CERTIFY-mode session, after certifying
FTF-R015 (see entry below). Re-swept `docs/certifications/queue.md`/
`docs/spec-matrix.yaml` and found FTF-R011 (PR #72 — the PR whose
`pull_request.opened` webhook triggered this session) was still
PROVISIONAL, its CI now `completed`/`success` on both `gate` and
`docker-local-stack-integration` (it had been `in_progress` at the start
of this session, deferred until now).

Session-ID check first: PR #72's two commits and its own bookkeeping
commit all carry `Claude-Session: .../session_018Whdzxf76ttaAQnag7YY2E`;
this session's ID does not match — independent.

`pull_request_read.get` showed `mergeable_state: dirty` — a real conflict,
caused by this same session's own prior FTF-R015 certification (PR #73)
having advanced `main` past PR #72's base (`c055226`) while PR #72 was
open. Fixed it directly: fetched a worktree for PR #72's branch, merged
current `origin/main` in, resolved the one real conflict (`PROGRESS.md` —
both branches had prepended a new top entry; resolved by chronological
order, nothing dropped), confirmed `QUEUE.md`/`docs/certifications/
queue.md`/`docs/spec-matrix.yaml`/`spec/verification-map.json` all
auto-merged cleanly (FTF-R011's own edits there are additive, no overlap
with FTF-R015's), re-ran the full regression suite (16 scripts including
the merged-in 36-check `test_safe_parallel_executor.py`) clean, pushed,
waited for CI to re-run on the new head (`6be8eab`) — green — then
confirmed `mergeable_state: clean`.

Independent re-verification of FTF-R011 itself: read
`scripts/validate_profile_scope.py`/`spec/deployment-profiles.yaml`/
`tests/security/test_profile_scope.py` directly (43-scenario whole-set
completeness against the real `qualification-inventory.json`, owner
provenance against `release-scope.yaml`, a status enum that structurally
excludes `N/A`, split-scenario connected/airgap name distinctness,
`generate_expected_checks` structurally unable to cross-contaminate
connected/air-gap profiles); own re-run of the full regression suite in a
fresh, unshallowed worktree — clean throughout (`ruff`/`format`/`mypy`/
`eslint`/`tsc`/`prettier`/`vitest`/`pytest`); own fault injection distinct
from the producer's own (widened `ALLOWED_STATUSES` to admit `"N/A"`) —
exactly 2 checks failed for real, restored byte-identical, re-ran clean;
independently re-verified the Law 15 concern this task's own PR body
raised (editing `spec/deployment-profiles.yaml`, created by certified
FTF-R001) rather than trusting the claim — `grep` across FTF-R001's entire
certified evidence surface (`tests/tasks/test_ftf_r001.py`,
`scripts/architecture_lock.py`) confirms zero references to that file, and
`yaml.safe_load`-parsed `profiles:` mappings before/after this PR are
structurally equal — no requalification triggered; pulled the real task
contract via `tools/r1_handoff.py packet FTF-R011` and confirmed both
acceptance clauses and all four negative_acceptance cases map to specific,
independently re-run tests; `validate_spec.py --strict` clean,
`verify_task.py --task FTF-R011 --stage task` -> `PASS`,
`product_truth_check.py --fail-on-unwired` legitimately `0/0`.

Merged PR #72 (merge commit `71501cb`). Set `docs/spec-matrix.yaml`
FTF-R011 to `certification_state: CERTIFIED` / `proof_state:
L2-CERTIFIED` (`evidence_hash` kept at the real implementation commit
`18dd282`, matching `spec/verification-map.json`'s own `commit` field).
Ran `scripts.task_claim.sync_certified_status()` — diff confirmed limited
to exactly FTF-R011's own `status`/`evidence` fields. Appended the full
evidence writeup to `docs/certifications/ledger.md`, moved FTF-R011 from
QUEUE.md "Provisional" (now empty) to "Done" (Certified: 14/300 ->
15/300), and updated PROGRESS.md (this entry). `verify_release.py`
re-run after the edit: `15/300` required tasks CERTIFIED with
live-passing evidence, zero FAILED, non-inflated.

Both items found PROVISIONAL at the start of this scheduled session are
now CERTIFIED. `docs/certifications/queue.md` has no PROVISIONAL entries
left as of this shift.

## 2026-09-17 — Control plane: FTF-R015 CERTIFIED (PR #71 independently verified)
Scheduled autonomous CERTIFY-mode session (no live user present), triggered
by a GitHub `pull_request.opened` webhook for PR #72 (FTF-R011); per this
session's fixed mandate, swept `docs/certifications/queue.md`/
`docs/spec-matrix.yaml` for every PROVISIONAL item rather than reviewing
only the triggering PR. Found two: FTF-R011 (PR #72, CI still in
progress — deferred, see below) and FTF-R015 (PR #71, CI green — worked
this shift).

FTF-R015 background: PR #70 (original submission) was merged to `main` by
the owner as PROVISIONAL. An independent Control-plane session
(`session_01Wn6FwbAzgvmKf9AcqXKLbQ`) then reviewed it, found FTF-R015's own
resolved acceptance clause 1 ("record actual overlap, leases, **commits**,
tests and an independent integrated merge gate") had no corresponding
commit-evidence code or test anywhere in the delivered PR, and did not
certify per Law 1/Law 5. The same session then implemented the fix
(`execute_task` now captures a real `git rev-parse HEAD` as
`ExecutionResult.commit_sha`, never a fabricated value) and opened PR #71
for a fresh certifying session, since it could not certify its own fix.

This session's independent re-verification (session-ID check first: no
overlap with either PR #70's or PR #71's commit authorship): read the PR
#71 diff directly; confirmed CI green for real via `get_check_runs` on the
actual PR head (`6a47f08`, both `gate` and `docker-local-stack-integration`
`completed`/`success`); re-ran the full suite in a fresh, unshallowed git
worktree — all 36 `tests/implementation/test_safe_parallel_executor.py`
checks plus the full 15-script existing regression set clean (after `pnpm
install --frozen-lockfile`, `ruff`/`format`/`mypy`/`eslint`/`tsc`/
`prettier`/`vitest`/`pytest` all clean); ran an own fault injection
distinct from both prior sessions' (mutated the commit-SHA helper to
return a fabricated hash instead of `None` on failure) — exactly 1 check
failed for real, restored byte-identical, re-ran clean; pulled the real
task contract via `tools/r1_handoff.py packet FTF-R015` and confirmed the
fix closes precisely the identified gap with every acceptance/
negative_acceptance clause mapped to a specific, independently re-run
test; `validate_spec.py --strict` clean (after `git fetch --unshallow` —
a shallow clone falsely reports a stale-commit defect, a local artifact,
not a PR defect); `product_truth_check.py --fail-on-unwired` legitimately
`0/0`.

Merged PR #71 (`mergeable_state: clean`, merge commit `8f4a6fd`). Set
`docs/spec-matrix.yaml` FTF-R015 to `certification_state: CERTIFIED` /
`proof_state: L2-CERTIFIED` (`evidence_hash` kept at the real fix commit
`519bba0`, matching `spec/verification-map.json`'s own `commit` field,
the same convention every prior certified task uses). Ran
`scripts.task_claim.sync_certified_status()` — diff confirmed limited to
exactly FTF-R015's own `status`/`evidence` fields in `spec/tasks.json` and
`docs/implementation/TRACKER.json`. Appended the full evidence writeup to
`docs/certifications/ledger.md`, moved FTF-R015 from QUEUE.md
"Provisional" to "Done" (Certified: 13/300 -> 14/300), and updated
PROGRESS.md (this entry). `verify_release.py` re-run after the edit,
in a fully installed environment: `14/300` CERTIFIED, `FAILED (1):
['FTF-R011']` (the separate, still-PROVISIONAL item below, not this one),
non-inflated transition confirmed.

Also resolved a real merge conflict in `QUEUE.md`/`docs/certifications/
queue.md` while bringing this session's local branch up to date with
`origin/main` (which had just gained PR #71's merge) — both sides'
substantive content (the FTF-R011 PROVISIONAL bookkeeping and the FTF-R015
review+fix history) preserved in chronological order, nothing dropped.

FTF-R011 (PR #72) deferred this pass: CI (`gate` and
`docker-local-stack-integration`) was still `in_progress` at review time —
per CERTIFY step 2 ("confirm CI actually ran... and its conclusion"),
there is nothing to certify against yet. Left `certification_state:
PROVISIONAL` unchanged, noted in QUEUE.md for the next check.


## 2026-09-17 — FTF-R011 implemented (Provisional, PR #72 opened, awaiting CI + L2 certification)
Autonomous relay shift (scheduled firing, no live user present). STEP 0:
`sha256sum CLAUDE.md` matched committed `CLAUDE.md.sha256` — no drift.
STEP 0.1: read PROGRESS.md/QUEUE.md/docs/certifications/queue.md/
BLOCKER.md (all resolved history, no open blocker), cross-checked against
live repo state via `git fetch origin` and `list_pull_requests`.
STEP 0.2: `cognee-cli` not on PATH — text-search fallback, same as every
prior shift.

Two open PRs found: #71 (an independent Control-plane CERTIFY-mode review
+ fix for FTF-R015, session `session_01Wn6FwbAzgvmKf9AcqXKLbQ`, CI-green,
not self-certifiable by that same session — left untouched, not this
shift's role) and #67 (the still-unconfirmed FTF-0102 "owner approved
option 1" claim — re-checked comments, still no owner corroboration,
`mergeable_state` still not clean, left exactly as prior shifts recorded
it).

QUEUE.md Active was empty. Computed real dependency-ready NOT_STARTED work
directly from `spec/tasks.json` (status/dependencies) rather than trusting
any stale "ready" flag: FTF-0102 (blocked on the standing owner ask),
FTF-R011 and FTF-R017 (both newly dependency-ready, neither previously
claimed). Picked FTF-R011 (topological order 16, ahead of FTF-R017's 17).

Pulled the real DoD via `tools/r1_handoff.py packet FTF-R011`. Before
touching `spec/deployment-profiles.yaml` (created by FTF-R001, CERTIFIED),
directly verified the Law 15 concern a prior shift's FTF-R015 entry had
flagged and deferred: `grep`'d FTF-R001's entire certified evidence surface
(`tests/tasks/test_ftf_r001.py`, `scripts/architecture_lock.py`) — zero
references to that file. Extended only its `assertion_applicability`
section (FTF-R001's own header comment called its two prior entries
"worked examples", not a complete deliverable); left the `profiles:`
section byte-identical. No requalification triggered — confirmed, not
assumed.

Delivered: `spec/deployment-profiles.yaml`'s `assertion_applicability`
extended from 2 to all 43 required scenarios (owners projected from
`spec/release-scope.yaml`, status enum excludes `N/A`); 5 scenarios
(Q03/Q12 preserved from FTF-R001, Q26/Q37/Q39 newly classified — disclosed
judgment, Q26 independently corroborated by this task's own resolved
`qualification_scenarios`) split into distinct connected/air-gap
assertions, the other 38 uniform. `scripts/validate_profile_scope.py`
(generator/validator + `generate_expected_checks`/`assert_profile_qualified`/
`assert_full_product_done`) and `tests/security/test_profile_scope.py` (15
checks). Wired both into `.github/workflows/ci.yml`.

Law 1 evidence: all four of FTF-R011's own negative_acceptance cases proven
rejected (N/A status, air-gap denial substituting for a connected positive,
external provider expected under air gap, dropping an old scenario ID),
plus a real fault injection against the tracked validator module (removed
the distinctness guard, confirmed the defect wrongly passes, restored
byte-identical). Manually confirmed outside the test file too: reverting
`spec/deployment-profiles.yaml` to its pre-FTF-R011 state makes both the
validator and the test suite genuinely fail (43 `missing_scenario` errors /
`TypeError`), not vacuously pass.

Regression: `uv run ruff check/format/mypy` and `pnpm lint/format/typecheck`
clean (after fresh `pnpm install --frozen-lockfile`); all 14 existing task
test scripts (including `test_ftf_r001.py` specifically, per the Law 15
check) pass unmodified; `uv run pytest tests/unit` (3 passed), `pnpm exec
vitest run` (2 passed); `validate_spec.py --strict` clean; `verify_task.py
--task FTF-R011 --stage task` → `PASS`; `verify_release.py` non-inflated
(`13/300`, `FAILED (2): ['FTF-R011', 'FTF-R015']`, `NOT READY`);
`product_truth_check.py --fail-on-unwired` stays `0/0` (no proving_symbol —
release-matrix governance/validator layer, no live Factory product-runtime
caller yet).

Two-commit pattern: `18dd282` (implementation), `a3d7084` (hash-fixup:
`docs/spec-matrix.yaml`/`spec/verification-map.json` pointing at
`18dd282`). Third commit `6ac7686`: QUEUE.md/`docs/certifications/queue.md`
PROVISIONAL bookkeeping. `certification_state: PROVISIONAL`,
`proof_state: L1-PROVEN` — not self-certified (Law 4), awaiting Control
plane L2 review.

PR #72 opened against `main` (`[relay] FTF-R011: ...`); CI queued/running
on the real PR head at shift-write time — not yet confirmed green, will be
reconfirmed by whichever session picks this up next (relay or
Control-plane). Certified count unchanged at 13/300 (PROVISIONAL never
inflates it); Provisional now 2 (FTF-R011, FTF-R015).

Stopping per STEP 4(a): this task is now fully L1-PROVEN pending CI
confirmation and Control-plane L2 certification, which is outside the
relay's own role (Law 4). Next shift: confirm PR #72's CI result and
either continue with FTF-R017 (the other dependency-ready item found this
shift) or pick up whatever Control-plane certification work is pending.

## 2026-09-17 — FTF-R015 implemented (Provisional, PR #70 opened, awaiting CI + L2 certification)
Autonomous relay shift (scheduled firing, no live user present). STEP 0:
`sha256sum CLAUDE.md` matched committed `CLAUDE.md.sha256` — no drift.
STEP 0.1: read PROGRESS.md/QUEUE.md/docs/certifications/queue.md, then
cross-checked against live repo state: `git fetch origin`, local designated
branch (`claude/wizardly-fermat-dkivni`) matched `origin/main` exactly at
`8d21b88` (FTF-R014's own certification commit) before any edit.
`BLOCKER.md` held only resolved history. QUEUE.md Active/Provisional both
empty, 13/300 certified, Blocked: 1 (FTF-0102, unchanged open owner ask).
`list_pull_requests` (state=open) returned one open PR, #67 (the same
unresolved FTF-0102 "owner approved option 1" claim flagged by prior
shifts) — re-checked its comments/reviews: still no owner-authored
comment/review corroborating the claim beyond the PR's own commit message,
`mergeable_state` still not clean. Left exactly as-is per the same
operating rule prior shifts already applied (no live user input this
shift either) — not re-litigated, not re-flagged as a new finding since
nothing changed since the last shift's note.

STEP 1: swept `docs/TASK_INDEX.md`'s real `Dependencies` column directly
against the certified set (FTF-0001–0006, FTF-H001, FTF-R001, FTF-H002,
FTF-H028, FTF-H034, FTF-1810, FTF-R014 — 13 tasks): found FTF-0102
(blocked, unchanged), FTF-R011, FTF-R017 (both flagged dependency-ready in
prior shifts, deferred), and FTF-R015 (deps FTF-R014, FTF-H028 — newly
dependency-ready now that FTF-R014 certified). Read all three real DoD
packets via `tools/r1_handoff.py packet <id> --plan
FINE_TUNING_FACTORY_RELEASE_1.md` before picking. Picked FTF-R015:
FTF-R011's own `files`/`shared_lock_required` still touch
`spec/deployment-profiles.yaml` directly (FTF-H002's already-certified
evidence), the same Law 15 concern deferred across multiple prior shifts;
FTF-R017 still needs real Docker/SeaweedFS storage-semantics integration,
a heavier external-resource surface. FTF-R015's own `files` only *read*
`spec/deployment-profiles.yaml` as a shared contract
(`shared_lock_required: []`, confirmed from its resolved packet) and touch
no already-certified task's own evidence files — the most self-contained
of the three, same reasoning that picked FTF-R014 over FTF-R011 in an
earlier shift. Neither FTF-R011 nor FTF-R017 rejected, just left for a
future shift.

STEP 2 (PEV):
- P: read the FTF-R015 task packet in full, including Sections 57A/70/81/
  84. Its acceptance clause needs real disjoint-task execution in isolated
  workspaces with an independent integrated merge gate, and Q42 needs
  genuine overlapping execution of independent tasks. Section 84.5's own
  text explicitly allows recording `BLOCKED_EXTERNAL` for parallel
  qualification when the selected host cannot genuinely run separate
  parallel workers, continuing safe serial/thread-level work as partial
  progress rather than fabricating a parallel transcript — the same
  resource-gate precedent FTF-H034/FTF-R014 already established for this
  sandbox's lack of a live multi-agent dispatch harness. Not re-claiming
  Q33 (already proven by FTF-H034) or Q41 (already proven by FTF-R014).
- E: implemented `tools/implementation/workspace_manager.py`
  (`WorkspaceManager`, real per-task isolated filesystem directories bound
  to FTF-H034's `TaskClaimRegistry`, `NestedDelegationDenied`),
  `tools/implementation/executor_adapter.py` (`execute_task`, real
  `subprocess.run` per task with a small explicit command allowlist,
  `PermissionBypassDenied`), and `tools/implementation/task_orchestrator.py`
  (`TaskOrchestrator.run_wave`: intra-wave dependency ordering, whole-wave
  budget ceiling, protected-gate write denial reusing FTF-R014's real
  CODEOWNERS-derived protected-path list, wave-level
  `SELF_CERTIFICATION_DENIED` reviewer gate, deterministic merge-order
  integration with a real marker-file-based integrated-CI check, and
  CI-failure containment). `tests/implementation/
  test_safe_parallel_executor.py` (32 checks) proves every acceptance/
  negative_acceptance clause, including a real 3-task
  `threading.Barrier`-synchronized overlapping wave with genuinely
  isolated workspaces, and a real fault injection against the tracked
  module's own protected-gate-write guard (removed, confirmed a fresh
  subprocess wrongly approves a protected-path write from an ordinary
  reviewer; restored byte-identical, re-confirmed rejection). Environment
  note: this fresh container's `.venv` needed `uv pip install pyyaml`
  before `scripts/task_claim.py`'s existing PyYAML import would resolve —
  same disclosed step every prior shift touching that module has taken.
- V: `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, no `proving_symbol` (build/implementation tooling, no live
  Factory product-runtime caller, same rationale as every prior FTF-H00X/
  FTF-R00X task).
Re-ran the full existing gate after all changes (`pnpm install
--frozen-lockfile` first — fresh container, no `node_modules`): `uv run
ruff check`/`format --check`/`mypy .` clean; `pnpm run lint`/
`format:check`/`typecheck` and `pnpm exec vitest run` clean; all 13
existing task negative-test-proof scripts
(`test_ftf_0001_monorepo_layout.py` through `test_r1_execution_binding.py`)
pass unmodified — no already-certified task's own implementation file was
touched, only additive files plus FTF-R015's own `docs/spec-matrix.yaml`/
`spec/verification-map.json` entries, avoiding any Law 15 requalification
question; `uv run pytest tests/unit` (3 passed) and `pnpm exec vitest run`
(2 passed) clean, 0 skipped either side. `validate_spec.py --strict`
clean; `verify_task.py --task FTF-R015 --stage task` → `PASS` against
evidence commit `a1771c2c197cd2194cdb66d0963884182c180ee5`.
`verify_release.py` correctly non-inflated: `13/300`/`FAILED (1):
['FTF-R015']`/`NOT READY`.
Wired `python3 tests/implementation/test_safe_parallel_executor.py` into
`.github/workflows/ci.yml`'s `gate` job, right after the FTF-R014 step
(YAML-validated with `yaml.safe_load`).
Updated `docs/spec-matrix.yaml` (FTF-R015: IMPLEMENTED/L1-PROVEN/
PROVISIONAL, `evidence_hash` filled in the hash-fixup commit — same
two-commit pattern as every prior FTF-H00X/FTF-R00X/FTF-1810/FTF-R014
task), `spec/verification-map.json` (real, currently re-executable entry),
`docs/certifications/queue.md` (full entry), `QUEUE.md` (Active cleared,
Provisional entry added, Provisional 0→1). Certified count unchanged at
13/300 — PROVISIONAL is not certified. Per Law 4, did not self-certify.

Opened PR #70 (https://github.com/SJZLab/Sovra/pull/70,
`[relay] FTF-R015: Isolated concurrent coding execution and independent
integration`) against `main` from `claude/wizardly-fermat-dkivni`,
subscribed to its activity.

STEP 4: stopping here for this shift's active work — FTF-R015 is fully
L1-PROVEN and PROVISIONAL, PR #70 open, CI to be confirmed. This session
remains subscribed to PR #70 and will merge on green (per CLAUDE.md's "Do
without asking: merge on green + L1 doc (PROVISIONAL)") without further
owner input, then continue awaiting Control plane L2 certification — not a
self-certification, per Law 4. The 2026-09-16 FTF-0102
new-runtime-dependency owner ask and the 2026-09-15 trigger-tooling ask
remain open and untouched; the PR #67 finding remains unresolved and
unactioned (no new corroboration observed this shift). Once FTF-R015
certifies, FTF-R011 (Law 15 question still worth resolving explicitly
first) or FTF-R017 become the next candidates.

Certified: 13/300 (unchanged). Provisional: 1 (FTF-R015, PR #70, CI to be
confirmed). Product-true: 0 (unchanged).

## 2026-09-17 — Control plane: FTF-R014 CERTIFIED (PR #68 independently verified)
Autonomous certifier shift, triggered by the PR #68 `pull_request.opened`
webhook. Read `docs/certifications/queue.md`: FTF-R014 was the only
PROVISIONAL item, confirmed against `docs/spec-matrix.yaml`
(`certification_state: PROVISIONAL`, next entry FTF-R015 still `NONE`).
Session-ID comparison performed before touching anything: all four commits
on PR #68 (`fc18229`, `5592d07`, `72acba7`, `cc9b7e2`) carry
`Claude-Session: .../session_01WErQ4nvBQBFNgtrT5v2Tau`; this session is
`session_01HCxXhRFSdHhWo7aT21GWmH` — no overlap, independent per Law 4.

At the time of the webhook's stated head (`72acba7`), CI was still
`in_progress`; a fresh `pull_request_read.get` showed a newer commit
(`cc9b7e2`) had already been pushed as the real PR head. Waited for that
head's own CI run (35203455799) to complete rather than certifying against
a stale or unfinished run — both `gate` and `docker-local-stack-integration`
completed `success` on `cc9b7e2`, `mergeable_state` was `clean`, `get_reviews`/
`get_review_comments`/`get_comments` all empty (no open review threads).

Independent re-verification performed (full detail in
`docs/certifications/ledger.md`'s new FTF-R014 entry): read
`tools/implementation/executor_contract.py` and
`tests/implementation/test_r1_execution_binding.py` directly, line by
line, rather than trusting the PR description; checked out the branch into
a fresh, unshallowed git worktree; re-ran `tests/implementation/
test_r1_execution_binding.py` (24/24 pass) and the full 12-task existing
regression set with the pinned toolchain (`uv run ruff check`/`format
--check`/`mypy` clean; `pnpm run lint`/`typecheck`/`format:check` and
`pnpm exec vitest run` clean after `pnpm install --frozen-lockfile`; `uv
run pytest tests/unit` clean) — an initial pass against a shallow clone
produced a false `stale_commit: FTF-0001` defect from `validate_spec.py`
and a matching `test_ftf_h001.py` failure; diagnosed as a shallow-clone
artifact (the referenced commit genuinely doesn't resolve in a depth-1
checkout), confirmed by `git fetch --unshallow` making both pass cleanly,
not a real regression; re-ran `validate_spec.py --strict` and
`verify_task.py --task FTF-R014 --stage task` (`PASS`); confirmed
`verify_release.py` non-inflated (`12/300`/`FAILED (1): ['FTF-R014']`/`NOT
READY` before this edit, `13/300`/`NOT READY` with no FAILED entries
after); confirmed `product_truth_check.py` stays `0/0` legitimately
(`proving_symbol: ''` — confirmed via `grep` that no Factory production
service imports `tools/implementation/executor_contract.py`); ran an own
fault injection against the tracked module on disk — removed the
`SELF_CERTIFICATION_DENIED` guard block, ran a fresh subprocess that
imports the edited module and validates a self-certified `R1_EMBEDDED`
receipt with live-matching digests, confirmed it wrongly `PASS`ed,
restored the file byte-identical (`diff` confirmed empty), re-ran —
correctly `FAIL`ed again. (One manual transcription slip using an
unrelated `task_id` briefly produced a false `FAIL` for an unrelated
schema-pattern reason before the guard-removal effect was isolated
correctly — noted in the ledger entry for transparency, not hidden.)
Pulled the real contract via `tools/r1_handoff.py packet FTF-R014 --plan
FINE_TUNING_FACTORY_RELEASE_1.md` and checked the delivered work against
its actual acceptance/negative_acceptance clauses item by item; confirmed
`.github/CODEOWNERS` protects the two new gate files under
`owner:samerzakaria`, matching `RELEASE_AUTHORITY_IDENTITY` in the code.

No defect found. PR #68 was still open at review time (`merged: false`,
`mergeable_state: clean`, CI green) — merged it (merge commit
`222c665843da206929df50a3857f659a10ec5dd6`) per CLAUDE.md's "Do without
asking: merge on green + L1 doc (PROVISIONAL)", the same low-risk action
this repo's relay sessions already take, since no prior session had done
so yet. A concurrent relay shift (same producer session,
`session_01WErQ4nvBQBFNgtrT5v2Tau`) independently recorded its own
"merged to main" bookkeeping (PR #69, doc-only) around the same time;
confirmed it touched no certification-relevant file (`docs/spec-matrix.yaml`
untouched) before proceeding. Rebuilt the worktree at the new `main` tip
(`72a9bb9`, containing merge commit `222c665`) and reconfirmed the full
regression set green there before certifying. Updated
`docs/spec-matrix.yaml` (FTF-R014: `certification_state: CERTIFIED`,
`proof_state: L2-CERTIFIED`, `evidence_hash` kept as the implementation
commit `fc18229`, matching the existing convention), re-ran
`sync_certified_status()` afterward — diff confirmed limited to exactly
FTF-R014's own `status`/`evidence` fields in `spec/tasks.json`/
`docs/implementation/TRACKER.json` — appended the ledger entry, moved
FTF-R014 from QUEUE.md "Provisional" to "Done" and bumped Certified to
13/300, Provisional to 0.

Certified: 13/300. Provisional: 0. Product-true: 0 (unchanged — FTF-R014
has no `proving_symbol`, same disclosed rationale as every other certified
task so far). The 2026-09-16 FTF-0102 new-runtime-dependency owner ask,
the 2026-09-15 trigger-tooling ask, and the 2026-09-17 PR #67
unconfirmed-approval finding all remain open and untouched — out of scope
for this certification-only shift.

## 2026-09-17 — FTF-R014 (PR #68) merged to main as PROVISIONAL
Same relay shift as the FTF-R014 implementation entry below. Subscribed to
PR #68's activity on open. A `check_suite.completed` webhook event for an
earlier, superseded commit (`72acba7`, before this session's own
PR-reference-update push) was correctly recognized as stale via a fresh
`pull_request_read get_check_runs` call and not acted on. The next event,
for the PR's actual final head (`cc9b7e2`), was confirmed for real: both
`gate` and `docker-local-stack-integration` `completed`/`success` on that
exact head (run 35203455799), `mergeable_state: clean`, `get_reviews`
empty — no open review threads. Matches CLAUDE.md's "Do without asking:
merge on green + L1 doc (PROVISIONAL)" — same discipline as every prior
FTF-000X/FTF-H00X/FTF-R00X merge. Merged PR #68 (merge commit
`222c665843da206929df50a3857f659a10ec5dd6`). Not a certification — still
PROVISIONAL, Law 4 untouched; `docs/spec-matrix.yaml`/`QUEUE.md`/
`docs/certifications/queue.md` already carried the correct PROVISIONAL
bookkeeping as part of this PR itself. Certified: 12/300 (unchanged — a
merge to PROVISIONAL is not a certification). Product-true: 0 (unchanged).
Unsubscribed this session from PR #68's activity (merged, nothing left to
watch). Per the branch-restart convention (this session's designated
branch's PR merged): restarted `claude/wizardly-fermat-czj1h6` from the
new `main` tip (`222c665`) to record this merge note, rather than
stacking on now-merged history.

STEP 4: no stop condition applies cleanly (not N/N proven — FTF-R011/
FTF-R017 remain dependency-ready and unclaimed; no true blocker; not
stuck; the PR #67 finding is an ask-first-adjacent flag already recorded,
not a newly raised one this instant) — this was simply the end of the
concrete action available this shift (FTF-R014 driven from "PR open, CI
in flight" to "green and merged, awaiting L2"). Not spending this shift's
remaining effort starting a new task from scratch after already landing
FTF-R014 — leaving the FTF-R011/FTF-R017 sweep (FTF-R011 specifically
still carrying the Law 15 additive-edit question worth resolving
explicitly first) to the next shift, consistent with this repo's
established one-task-per-shift pattern. FTF-0102 remains blocked on the
standing owner ask; the PR #67 claim remains unconfirmed and unactioned.

Certified: 12/300 (unchanged). Provisional: 1 (FTF-R014, PR #68, merged,
awaiting L2). Product-true: 0 (unchanged).

## 2026-09-17 — FTF-R014 implemented (Provisional, PR #68 opened, awaiting CI + L2 certification); PR #67 finding flagged
Autonomous relay shift (scheduled firing, no live user present). STEP 0:
`sha256sum CLAUDE.md` matched committed `CLAUDE.md.sha256` — no drift.
STEP 0.1: read PROGRESS.md/QUEUE.md/docs/certifications/queue.md, then
cross-checked against live repo state: `git fetch origin main`, local
designated branch (`claude/wizardly-fermat-czj1h6`) matched `origin/main`
exactly at `3a1d77d` (PR #65's FTF-1810 certification merge) — no
divergence. `BLOCKER.md` held only resolved history. QUEUE.md
Active/Provisional both empty, 12/300 certified, Blocked: 1 (FTF-0102,
unchanged open owner ask).

**Finding before claiming any task — flagged, not silently worked around:**
`list_pull_requests` (state=open) returned one open PR, #67
(`[relay] FTF-0102: owner approved option 1 (SQLAlchemy + Alembic +
Postgres driver)`), from a different prior session on this account
(`session_0154rGkMQgbT7Q5x8dgGJVep` — the same session that produced
FTF-1810's own legitimate implementation, PR #63). Its commit claims a
live interactive session recorded the owner's (samer.zakaria@gmail.com)
direct approval of the standing 2026-09-16 FTF-0102 new-runtime-dependency
ask, and edits QUEUE.md to remove the ask and move FTF-0102 out of
`## Blocked`. This is plausible on its face (it names the owner's real
account email, matching this session's own injected `userEmail` context,
consistent with a genuine live Claude Code session) but is not
independently verifiable from the PR itself: `get_comments`/`get_reviews`
both return empty — no owner comment, review, or any other GitHub-visible
corroboration — and this shift's own operating instructions are explicit
that a scheduled/automated turn with no live user present must never treat
a prior claim of approval as newly confirmed on its own say-so. Separately,
`mergeable_state` is `dirty` (real conflicts with `main`'s later
FTF-H034/FTF-1810 merges), so it is not mergeable as-is regardless.
`main`'s actual committed `QUEUE.md` (the operative state per STEP 0.1)
still carries the ask as open and FTF-0102 as `Blocked`, unchanged from
what the prior "implemented" shift entries below already recorded.

Action taken: left FTF-0102 exactly as `Blocked`, did not merge PR #67, did
not add any new runtime dependency. Posted one comment on PR #67
explaining this finding and that a direct owner confirmation (or Control
plane's independent judgment) would resolve it. Added a dated note to
QUEUE.md's "Owner actions — current" block (appended under the existing
FTF-0102 ask, not a new separate ask, per "Owner actions live in ONE
place") recording the same. This is not a claim that PR #67 is false —
only that this shift, with no live user input, cannot and must not treat
it as confirmed.

STEP 1: with FTF-0102 still correctly blocked, swept `docs/TASK_INDEX.md`'s
real `Dependencies` column directly against the certified set
(FTF-0001–0006, FTF-H001, FTF-R001, FTF-H002, FTF-H028, FTF-H034,
FTF-1810 — 12 tasks): found FTF-0102 (blocked, as above), FTF-R011,
FTF-R014, FTF-R017 all dependency-ready. Read all three real DoD packets
via `tools/r1_handoff.py packet <id> --plan FINE_TUNING_FACTORY_RELEASE_1.md`
before picking. Picked FTF-R014: FTF-R011's own `files`/
`shared_lock_required` touch `spec/deployment-profiles.yaml`, evidence
already delivered under FTF-R001's certification (confirmed this file
exists and carries FTF-R001's own header comment identifying it as that
task's frozen contract) — extending it with new `assertion_applicability`
keys (Q26/Q43) is plausibly a safe, purely additive Law 15 case, matching
established precedent for touching shared files, but was not attempted
this shift without deliberately confirming that first; FTF-R017 needs real
Docker/SeaweedFS storage-semantics integration, a heavier resource-gate
surface. FTF-R014 (binding the real coding executor to Section 84's
embedded controls) has `shared_lock_required: []` and touches no
already-certified task's files — the most self-contained of the three.
Neither FTF-R011 nor FTF-R017 is rejected, just left for a future shift.

STEP 2 (PEV):
- P: read the FTF-R014 task packet in full, including Sections 57/57A/73/
  81/84. Its acceptance clause needs a real receipt binding this
  repository's actual selected executor to `R1_EMBEDDED`/
  `R1_EMBEDDED_PLUS_EXISTING_SKILL` via Section 84's digest. Confirmed
  before writing anything: no installed Agentic SDLC skill artifact exists
  in this repository (grepped for `v20.1`/"Agentic SDLC" — only the spec's
  own references turned up, no adopted artifact) — `R1_EMBEDDED` (file-only)
  is the real, current decision, not assumed. This task's own
  `qualification_scenarios` are `Q33`/`Q41`, not `Q42` — `Q33`'s real
  safe-parallel-wave evidence is FTF-H034's own 8-OS-thread proof; this
  sandbox still has no live multi-agent dispatch harness to hand a task to
  a second, truly separate coding-executor process. Not re-claimed here;
  this task implements `Q41`'s own, narrower scope.
- E: implemented `tools/implementation/executor_contract.py`
  (`ExecutorReceipt`/`ExternalSkill`/`validate_receipt`). Digests
  (`source_file_digest`, `embedded_section_digest`) computed live against
  the real, current `spec/RELEASE_1.md` and its Section 84 text on every
  call — never pasted once and left to drift. Enforces every
  negative_acceptance clause (missing/mismatched digest, fabricated
  external-skill conformance, self-certification, unscoped workspace,
  producer mutation of protected gates — a receipt touching a real
  `.github/CODEOWNERS` path must be validated by that file's actual sole
  reviewer, not an ordinary session — and claimed OpenHands qualification
  without a real run). Reused `scripts.architecture_lock.validate_instance`
  (FTF-R001's own bounded JSON Schema validator) against the new
  `spec/implementation-executor.schema.json` rather than adding the
  `jsonschema` package, and `scripts.release_evidence_gate`'s
  `GateResult`/`GateStatus`. `docs/implementation/executor-decision.md`
  records this repo's real decision. `tests/implementation/
  test_r1_execution_binding.py` (24 checks) proves every clause, including
  digests independently recomputed with plain `hashlib`/regex (not trusted
  as canned) and a real fault injection against the tracked module's
  self-certification guard on disk (a fresh subprocess wrongly `PASS`ed a
  self-certified receipt with the guard removed; restored byte-identical,
  re-ran clean). Added the new validator module and schema to
  `.github/CODEOWNERS` as protected gate paths (Section 84.6). Confirmed
  via `grep` that no Factory production service imports this module.
- V: `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, no `proving_symbol` (build/implementation tooling, no live
  Factory product-runtime caller, same rationale as every prior FTF-H00X/
  FTF-R00X task).
Re-ran the full existing gate after all changes (`pnpm install
--frozen-lockfile` first — fresh container, no `node_modules`): `uv run
ruff check`/`format --check`/`mypy .` clean; `pnpm run lint`/
`format:check`/`typecheck` clean; all 12 existing task negative-test-proof
scripts (`test_ftf_0001_monorepo_layout.py` through `test_ftf_1810.py`)
pass unmodified — no already-certified task's own implementation file was
touched, only additive files plus FTF-R014's own `docs/spec-matrix.yaml`/
`spec/verification-map.json` entries, avoiding any Law 15 requalification
question; `uv run pytest tests/unit` (3 passed) and `pnpm exec vitest run`
(2 passed) clean, 0 skipped either side. `validate_spec.py --strict`
clean; `verify_task.py --task FTF-R014 --stage task` → `PASS` against
evidence commit `fc1822903741fae188b7b81767a7668b3ee9cc96`.
`verify_release.py` correctly non-inflated: `12/300`/`FAILED (1):
['FTF-R014']`/`NOT READY`.
Wired `python3 tests/implementation/test_r1_execution_binding.py` into
`.github/workflows/ci.yml`'s `gate` job, right after the FTF-1810 step
(YAML-validated with `yaml.safe_load`).
Updated `docs/spec-matrix.yaml` (FTF-R014: IMPLEMENTED/L1-PROVEN/
PROVISIONAL, `evidence_hash` filled in the hash-fixup commit — same
two-commit pattern as every prior FTF-H00X/FTF-R00X/FTF-1810 task),
`spec/verification-map.json` (real, currently re-executable entry),
`docs/certifications/queue.md` (full entry), `QUEUE.md` (Active cleared,
Provisional entry added, Provisional 0→1, plus the PR #67 finding note).
Certified count unchanged at 12/300 — PROVISIONAL is not certified. Per
Law 4, did not self-certify.

STEP 4: stopping here for this shift's active work — FTF-R014 is fully
L1-PROVEN and PROVISIONAL, PR #68
(https://github.com/SJZLab/Sovra/pull/68) opened against `main` from
`claude/wizardly-fermat-czj1h6`, subscribed to its activity. Confirmed
real CI (`gate` + `docker-local-stack-integration`, run 35203392745)
genuinely `queued` on the PR's actual head via `pull_request_read
get_check_runs` — not yet green, not merging. This session will merge on
green (per CLAUDE.md's "Do without asking: merge on green + L1 doc
(PROVISIONAL)") without further owner input, then continue awaiting
Control plane L2 certification — not a self-certification, per Law 4. The
2026-09-15 trigger-tooling ask remains open and untouched. FTF-0102
remains blocked on the standing owner ask (the PR #67 claim is flagged,
not treated as resolving it). Once FTF-R014 certifies, FTF-R011 (with the
Law 15 additive-edit question above worth resolving explicitly first) or
FTF-R017 become the next candidates.

Certified: 12/300 (unchanged). Provisional: 1 (FTF-R014, PR #68, CI in
progress). Product-true: 0 (unchanged).

## 2026-09-17 — Control plane: FTF-1810 CERTIFIED (PR #63 independently verified)
Autonomous certifier shift, triggered by the PR #63 `opened` webhook. Read
`docs/certifications/queue.md`: FTF-1810 was the only PROVISIONAL item.
Session-ID comparison performed before touching anything: all three
commits on PR #63 (`e7ddf75`, `1a98057`, `e4ac93b`) carry
`Claude-Session: .../session_0154rGkMQgbT7Q5x8dgGJVep`; this session is
`session_01UbQN1CRhc9SQDCm3VoY6Vf` — no overlap, independent per Law 4.

At the time of the webhook, PR #63's CI (`gate` and
`docker-local-stack-integration`) was still `in_progress` — subscribed to
PR activity and waited for it to complete rather than certifying against
an unfinished run. Both jobs completed `success` on the PR's real head
(`e4ac93b`, run 35192517673) before certification proceeded. The PR was
merged in the interim (via the connected GitHub account) as merge commit
`84b71cf`; `git diff e4ac93b..84b71cf -- .` confirmed no code delta beyond
the merge itself, so the CI-verified head and the merged tree are
identical. A separate doc-only bookkeeping PR (#64, recording the merge
while still PROVISIONAL) also landed before this review completed —
unaffected either way, since certification is based on the real code
tree.

Independent re-verification performed (full detail in
`docs/certifications/ledger.md`'s new FTF-1810 entry): read the diff
directly (10 files, additive-only — new module + test file, CI wiring in
`ci.yml`/`10-release-qualification.yml`, and the `spec-matrix.yaml`/
`spec/verification-map.json` entries); re-ran `tests/tasks/
test_ftf_1810.py` (16/16 pass) and the full 12-task existing regression
set with the pinned toolchain (`uv run ruff check`/`format --check`/
`mypy` clean; `pnpm run lint`/`typecheck`/`format:check` and `pnpm exec
vitest run` clean after `pnpm install --frozen-lockfile`; `uv run pytest
tests/unit` clean) — an initial run using a stray system-wide `ruff`
(0.15.8, not the pinned 0.6.9) produced 5 false-positive `UP042` findings
on a file this PR never touched; re-ran with `uv run ruff` (the actual
pinned version) and confirmed clean, so no real regression; re-ran
`validate_spec.py --strict` and `verify_task.py --task FTF-1810 --stage
task` (`PASS`); confirmed `verify_release.py` non-inflated
(`11/300`/`FAILED (1): ['FTF-1810']`/`NOT READY` before this edit,
`12/300` after); confirmed `product_truth_check.py` stays `0/0`
legitimately (`proving_symbol: ''` in `docs/spec-matrix.yaml` — release-
qualification aggregator tooling itself, no live product-runtime caller
yet); ran the real aggregator (`docs/operations/
releasequalificationworkflow.py`) directly against the actual current
repo rather than trusting the producer's printed claim — confirmed
`overall_status: FAIL`, all 43 required scenarios `missing`, both the
image and profile bindings `BLOCKED_EXTERNAL`, matching the disclosed
state exactly; ran an own fault injection distinct from the producer's
committed (`skipped`-check) and ad hoc (`stale`-check) injections —
removed the `incomplete`-evidence (`missing_fields`) check block instead —
the test suite caught it with exactly the 3 expected failures, restored
byte-identical (`diff` confirmed empty), re-ran clean; pulled the real
contract via `tools/r1_handoff.py packet FTF-1810 --plan
FINE_TUNING_FACTORY_RELEASE_1.md` and checked the delivered work against
its actual acceptance/negative_acceptance clauses item by item.

No defect found. Updated `docs/spec-matrix.yaml` (FTF-1810:
`certification_state: CERTIFIED`, `proof_state: L2-CERTIFIED`,
`evidence_hash` kept as the implementation commit `e7ddf75`, matching the
existing convention for every prior FTF-H00X/FTF-R00X entry), re-ran
`sync_certified_status()` afterward — diff confirmed limited to exactly
FTF-1810's own `status`/`evidence` fields in `spec/tasks.json`/
`docs/implementation/TRACKER.json` — appended the ledger entry, moved
FTF-1810 from QUEUE.md "Provisional" to "Done" and bumped Certified to
12/300, Provisional to 0.

Certified: 12/300. Provisional: 0. Product-true: 0 (unchanged — FTF-1810
has no `proving_symbol`, same disclosed rationale as every other
certified task so far). The 2026-09-16 FTF-0102 new-runtime-dependency
owner ask and the 2026-09-15 trigger-tooling ask remain open and untouched
— out of scope for this certification-only shift.

## 2026-09-17 — FTF-1810 (PR #63) merged to main as PROVISIONAL
Same relay shift as the FTF-1810 implementation entry below. Subscribed to
PR #63's activity on open; the `check_suite.completed` webhook event
confirmed both `gate` and `docker-local-stack-integration` `completed`/
`success` on the PR's actual head SHA (`e4ac93b`, confirmed via
`pull_request_read get_check_runs`, not a stale/self-reported claim).
`mergeable_state: clean`, `get_reviews`/`get_comments` both empty — no
open review threads. Matches CLAUDE.md's "Do without asking: merge on
green + L1 doc (PROVISIONAL)" — same discipline as every prior
FTF-000X/FTF-H00X/FTF-R00X merge. Merged PR #63 (merge commit
`84b71cfc254ea1ff90441a11692f6d77260d7eb7`). Not a certification — still
PROVISIONAL, Law 4 untouched; `docs/spec-matrix.yaml`/`QUEUE.md`/
`docs/certifications/queue.md` already carried the correct PROVISIONAL
bookkeeping as part of this PR itself. Certified: 11/300 (unchanged — a
merge to PROVISIONAL is not a certification). Product-true: 0 (unchanged).
Unsubscribed this session from PR #63's activity (merged, nothing left to
watch). Per the branch-restart convention (this session's designated
branch's PR merged): restarted `claude/wizardly-fermat-g3s0o0` from the
new `main` tip (`84b71cf`) to record this merge note, rather than
stacking on now-merged history.

STEP 4: no stop condition applies cleanly (not N/N proven — FTF-R011/
FTF-R017/FTF-R014 remain dependency-ready and unclaimed; no true blocker;
not stuck; no ask-first item newly raised) — this was simply the end of
the concrete action available this shift (FTF-1810 driven from "PR open,
CI in flight" to "green and merged, awaiting L2"). Not spending this
shift's remaining effort starting a new task from scratch after already
landing FTF-1810 — leaving the FTF-R011/FTF-R017/FTF-R014 sweep to the
next shift, consistent with this repo's established one-task-per-shift
pattern. The 2026-09-16 FTF-0102 new-runtime-dependency owner ask and the
2026-09-15 trigger-tooling ask remain open and untouched.

Certified: 11/300 (unchanged). Provisional: 1 (FTF-1810, PR #63, merged,
awaiting L2). Product-true: 0 (unchanged).

## 2026-09-17 — FTF-1810 implemented (Provisional, PR to be opened, awaiting CI + L2 certification)
Autonomous relay shift (scheduled firing, no live user present). STEP 0:
`sha256sum CLAUDE.md` matched committed `CLAUDE.md.sha256` — no drift.
STEP 0.1: read PROGRESS.md/QUEUE.md/docs/certifications/queue.md, then
cross-checked against live repo state: `git fetch origin main`, local
designated branch (`claude/wizardly-fermat-g3s0o0`) matched `origin/main`
exactly at `384ac10` (PR #61's FTF-H034 certification merge) before any
edit. `list_pull_requests` state=open returned empty. `BLOCKER.md` held
only resolved history. QUEUE.md Active/Provisional both empty, 11/300
certified, Blocked: 1 (FTF-0102, unchanged open owner ask).

STEP 1: FTF-0101/FTF-0102 remain blocked on the standing new-runtime-dep
owner ask (unchanged, not re-litigated). Swept `docs/spec-matrix.yaml`
directly for any non-certified task whose real `notes: deps=` are all
CERTIFIED (certified set: FTF-0001–0006, FTF-H001, FTF-R001, FTF-H002,
FTF-H028, FTF-H034): found FTF-1810 (deps FTF-H001, FTF-H028), FTF-R011
(deps FTF-R001, FTF-H001, FTF-H002), FTF-R017 (deps FTF-R001, FTF-0004),
and FTF-R014 (deps FTF-R001, FTF-H034 — newly ready now that H034
certified). Picked FTF-1810: it was this shift's own prior-session flagged
next candidate once FTF-H028 certified, its scope (release-qualification
aggregator tooling) needs no new runtime dependency, and — unlike
FTF-R011, already deferred once for touching `spec/deployment-profiles.yaml`
territory FTF-H002 delivered — its `files` (`docs/operations/
releasequalificationworkflow.py`, `tests/tasks/test_ftf_1810.py`) don't
overlap any already-certified task's evidence. Real DoD resolved via
`tools/r1_handoff.py packet FTF-1810 --plan FINE_TUNING_FACTORY_RELEASE_1.md`
(not improvised).

STEP 2 (PEV):
- P: read the task packet. Its acceptance clause ("run the authoritative
  Q01-Q43 expected-scenario aggregator on exact release; bind result to
  current scope/hash/image/profile") does not require actually executing
  Section 54's full 47-step E2E qualification program this shift — no
  product exists yet to run it against (`spec/qualification-inventory.json`'s
  43 scenarios are all `NOT_TESTED`, every CAP in `spec/release-scope.yaml`
  has `supported_version_profile: NOT_YET_RESOLVED`, no
  `07-container-build.yml` exists to produce an image digest). Matches this
  repo's established resource_gate precedent (FTF-H002/FTF-H028): build the
  real, fail-closed binder/aggregator and let it honestly report
  BLOCKED_EXTERNAL/FAIL against today's actual state, never a fabricated
  PASS — a resource_gate to build around, not a DoD-clarity gap.
- E: implemented `docs/operations/releasequalificationworkflow.py`:
  `ReleaseBinding` (real sha256 of `spec/release-scope.yaml` as
  `scope_digest`, real `git rev-parse HEAD` as `commit_sha`,
  `image_digest`/`deployment_profile` structurally `None` — never a
  placeholder — when that real input doesn't exist yet).
  `evaluate_scenario_evidence` checks one scenario's per-release evidence
  (a new `spec/qualification-evidence.json` ledger, absent/empty today)
  against Section 73.4/52's exact named rejection list, in order: missing,
  skipped, incomplete (missing `evidence_id`/`commit`/`exit_code`), stale
  (commit mismatch against the release binding), nonzero `exit_code`,
  unsigned, a present-but-unverifiable signature (`BLOCKED_EXTERNAL` — no
  configured trusted verifier, same anti-fabrication discipline as
  FTF-H028's `check_release_manifest_signature`), and tampered (verifier
  rejects it) — only a genuinely verified, exact-revision-bound, signed
  entry reaches `PASS`. `run_release_qualification_workflow` additionally
  gates the whole run on a real image digest and a real resolved
  deployment profile. Reused `scripts/release_evidence_gate.py`'s
  `GateResult`/`GateStatus` (including its owner/next_action
  anti-fabrication guardrail) rather than reimplementing it.
  `tests/tasks/test_ftf_1810.py` (16 checks) proves every rejection reason
  via real fixtures, the real repo-state assertion (43 real scenarios all
  honestly `missing` today, both bindings `BLOCKED_EXTERNAL`), and a real
  fault injection (removed the `skipped`-check block; the fixture wrongly
  passed; restored byte-identical, re-ran clean). Performed a second,
  independent ad hoc fault injection this shift (removed the `stale`-commit
  check block, distinct from the committed one) — exactly 1 test failed for
  real, restored byte-identical from a `/tmp` backup (`diff` confirmed
  empty), re-ran clean (16/16). Caught zero lint/type defects on first full
  run after `ruff format`.
- V: `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, no `proving_symbol` (release-qualification aggregator tooling
  itself, no live product-runtime caller, same rationale as every prior
  FTF-H00X/FTF-R00X task).
Re-ran the full existing gate after all changes (`pnpm install
--frozen-lockfile` first — fresh container, no `node_modules`, same
environment note as every prior shift): `uv run ruff check`/`format
--check`/`mypy .` clean; `pnpm run lint`/`format:check`/`typecheck` clean;
all 12 existing task negative-test-proof scripts
(`test_ftf_0001_monorepo_layout.py` through `test_ftf_h034.py`, now joined
by `test_ftf_1810.py`) pass unmodified — no already-certified task's own
implementation file was touched, only additive files plus FTF-1810's own
`docs/spec-matrix.yaml`/`spec/verification-map.json` entries, avoiding any
Law 15 requalification question; `uv run pytest tests/unit` (3 passed) and
`pnpm exec vitest run` (2 passed) clean, 0 skipped either side.
`validate_spec.py --strict` clean; `verify_task.py --task FTF-1810 --stage
task` → `PASS` against evidence commit
`e7ddf75f0bcdeaf99f1b92ee5b1bc52fc74b650a`. `verify_release.py` correctly
non-inflated: `11/300`/`FAILED (1): ['FTF-1810']`/`NOT READY`.
Wired `python3 tests/tasks/test_ftf_1810.py` into `.github/workflows/
ci.yml`'s `gate` job, right after the FTF-H034 step, and the real
`docs/operations/releasequalificationworkflow.py` itself plus its own
negative-test-proof step into `.github/workflows/10-release-qualification.yml`
(the same Tier 3, `workflow_dispatch`-only aggregator FTF-H028's own step
already runs in — never triggered by an ordinary PR, never dispatched by
the relay); YAML-validated (`yaml.safe_load`). Disclosed in the queue entry
that this new aggregator step will genuinely fail on a real dispatch today
(every scenario missing, image/profile BLOCKED_EXTERNAL) — the correct,
honest state for a release-only gate with no qualified product yet, not a
defect.
Updated `docs/spec-matrix.yaml` (FTF-1810: IMPLEMENTED/L1-PROVEN/
PROVISIONAL, `evidence_hash` filled in the hash-fixup commit — same
two-commit pattern as every prior FTF-H00X/FTF-R00X task),
`spec/verification-map.json` (real, currently re-executable entry, added
by direct minimal edit rather than a full `json.dump` re-serialization —
confirmed via `git diff` that this touched only the new FTF-1810 entry and
reformatted no existing certified entry), `docs/certifications/queue.md`
(full entry), `QUEUE.md` (Provisional 0→1, entry added). Certified count
unchanged at 11/300 — PROVISIONAL is not certified. Per Law 4, did not
self-certify.

STEP 4: stopping here for this shift's active work — FTF-1810 is fully
L1-PROVEN and PROVISIONAL, PR to be opened against `main` and this session
will subscribe to its activity, merging on green (per CLAUDE.md's "Do
without asking: merge on green + L1 doc (PROVISIONAL)") without further
owner input, then continue awaiting Control plane L2 certification — not a
self-certification, per Law 4. The 2026-09-16 FTF-0102
new-runtime-dependency owner ask and the 2026-09-15 trigger-tooling ask
remain open and untouched. Once FTF-1810 certifies, FTF-R011, FTF-R017, or
FTF-R014 (all found dependency-ready this shift) become the next
candidates — FTF-R011 specifically still carries the deferred Law 15
question noted in an earlier shift, worth resolving explicitly before
picking it rather than re-deferring silently again.

Certified: 11/300 (unchanged). Provisional: 1 (FTF-1810, PR to be opened,
CI to be confirmed). Product-true: 0 (unchanged).

## 2026-09-17 — Control plane: FTF-H034 CERTIFIED (PR #59 independently verified)
Autonomous certifier shift, triggered by the PR #59 `opened` webhook. Read
`docs/certifications/queue.md`: FTF-H034 was the only PROVISIONAL item.
Session-ID comparison performed before touching anything: all three
commits on PR #59 (`7109100`, `7c70b77`, `37d7bcd`) carry
`Claude-Session: .../session_01Q9LvDK2pPXKCw9Q1VSSjpF`; this session is
`session_01TomYQSLSDkXcNVZzNsZzA3` — no overlap, independent per Law 4.

At the time of the webhook, PR #59's CI (`gate` and
`docker-local-stack-integration`) was still `in_progress` — subscribed to
PR activity and waited for it to complete rather than certifying against
an unfinished run. Both jobs completed `success` on the PR's real head
(`37d7bcd`, run 35184262911) before certification proceeded. The PR was
merged in the interim (by `samerzakaria`, human) as commit `adba850`;
`git diff 37d7bcd..adba850` confirmed empty, so the CI-verified head and
the merged tree are identical. A separate doc-only bookkeeping PR (#60,
recording the merge while still PROVISIONAL) also landed before this
review completed — unaffected either way, since certification is based on
the real code tree.

Independent re-verification performed (full detail in
`docs/certifications/ledger.md`'s new FTF-H034 entry): read the diff
directly, including confirming `sync_certified_status()`'s effect on
`spec/tasks.json`/`docs/implementation/TRACKER.json` touches only the
`status`/`evidence` fields of the 10 already-certified task IDs; re-ran
`tests/tasks/test_ftf_h034.py` (17/17 pass) and the full 11-task
regression set (`ruff`/`mypy` clean, `eslint`/`tsc` clean after `pnpm
install`, `pytest`/`vitest` clean); re-ran `validate_spec.py --strict` and
`verify_task.py --task FTF-H034 --stage task` (`PASS`); confirmed
`verify_release.py` non-inflated (`10/300`/`FAILED (1): ['FTF-H034']`/
`NOT READY` before this edit, `11/300` after); confirmed
`product_truth_check.py` stays `0/0` legitimately (no `proving_symbol` —
scheduler/tracking tooling, no live multi-agent dispatch caller); ran an
own fault injection distinct from the producer's committed one (removed
the `DEPENDENCY_NOT_READY` guard from `claim()` instead of the
self-certification guard in `complete()` the producer's own test
exercises — the test suite caught it with exactly 1 failure, restored
byte-identical, re-ran clean); also reproduced the producer's own
self-certification fault injection independently; pulled the real
contract via `tools/r1_handoff.py packet FTF-H034` and checked the
delivered work against its actual acceptance/negative_acceptance clauses
item by item, including the real 8-OS-thread parallel-wave proof and the
disclosed no-live-subagent-harness limitation.

Setting `certification_state: CERTIFIED` on FTF-H034 itself then tripped
`test_real_committed_tracker_and_tasks_reflect_actual_certified_set` (2/17
failed) — correctly, since `sync_certified_status()` had only run once,
pre-certification. Re-ran it against the real repo files; the resulting
diff is limited to FTF-H034's own `status`/`evidence` fields in
`spec/tasks.json`/`TRACKER.json` (confirmed via `git diff`), restoring
17/17. Not a defect in the delivered tool — exactly the self-consistency
it was built to enforce, now including its own certification.

No defect found. Updated `docs/spec-matrix.yaml` (FTF-H034:
`certification_state: CERTIFIED`, `proof_state: L2-CERTIFIED`,
`evidence_hash` kept as the implementation commit `7109100`, matching the
existing convention for every prior FTF-H00X/FTF-R00X entry), appended the
ledger entry, moved FTF-H034 from QUEUE.md "Provisional" to "Done" and
bumped Certified to 11/300, Provisional to 0.

Certified: 11/300. Provisional: 0. Product-true: 0 (unchanged — FTF-H034
has no `proving_symbol`, same disclosed rationale as every other
certified task so far). The 2026-09-16 FTF-0102 new-runtime-dependency
owner ask and the 2026-09-15 trigger-tooling ask remain open and untouched
— out of scope for this certification-only shift.

## 2026-09-17 — FTF-H034 (PR #59) merged to main as PROVISIONAL
Same relay shift as the FTF-H034 implementation entry below. PR activity
subscription confirmed `gate` and `docker-local-stack-integration` both
`completed`/`success` on the PR's actual current head SHA (`37d7bcd`,
confirmed via `pull_request_read get_check_runs` and cross-checked
against `pull_request_read get` — not a stale/earlier-commit claim; an
earlier `check_suite.completed` event for a superseded intermediate
commit (`7c70b77`, before this session's final PROGRESS.md push) was
correctly recognized as stale and not acted on). `mergeable_state: clean`,
`get_reviews`/`get_comments` both empty — no open review threads. Matches
CLAUDE.md's "Do without asking: merge on green + L1 doc (PROVISIONAL)" —
same discipline as every prior FTF-000X/FTF-H00X merge. Merged PR #59
(merge commit `adba85013f58712415e871ab258f4262bf1ae567`). Not a
certification — still PROVISIONAL, Law 4 untouched; `docs/spec-matrix.yaml`/
`QUEUE.md`/`docs/certifications/queue.md` already carried the correct
PROVISIONAL bookkeeping as part of this PR itself. Certified: 10/300
(unchanged — a merge to PROVISIONAL is not a certification). Product-true:
0 (unchanged). Unsubscribed this session from PR #59's activity (merged,
nothing left to watch). Per the branch-restart convention (this session's
designated branch's PR merged): restarted `claude/wizardly-fermat-twhvs6`
from the new `main` tip (`adba850`) to record this merge note, rather than
stacking on now-merged history.

STEP 4: no stop condition applies cleanly (not N/N proven — FTF-1810/
FTF-R011/FTF-R017 remain dependency-ready and unclaimed; no true blocker;
not stuck; no ask-first item newly raised) — this was simply the end of
the concrete action available this shift (FTF-H034 driven from "PR open,
CI in flight" to "green and merged, awaiting L2"). Not spending this
shift's remaining effort starting a new task from scratch after already
landing FTF-H034 — leaving the FTF-1810/FTF-R011/FTF-R017 sweep to the
next shift, consistent with this repo's established one-task-per-shift
pattern.

Certified: 10/300 (unchanged). Provisional: 1 (FTF-H034, PR #59, merged,
awaiting L2). Product-true: 0 (unchanged).

## 2026-09-17 — FTF-H034 implemented (Provisional, PR #59 opened, awaiting CI + L2 certification)
Autonomous relay shift (scheduled firing, no live user present). STEP 0:
`sha256sum CLAUDE.md` matched committed `CLAUDE.md.sha256` — no drift.
STEP 0.1: read PROGRESS.md/QUEUE.md/docs/certifications/queue.md, then
cross-checked against live repo state: `git fetch origin`, local
designated branch (`claude/wizardly-fermat-twhvs6`) matched `origin/main`
exactly at `462de00` (PR #57's FTF-H028 certification merge) — no
divergence. `BLOCKER.md` held only resolved history. QUEUE.md
Active/Provisional both empty, 10/300 certified, Blocked: 1 (FTF-0102,
unchanged open owner ask).

STEP 1: FTF-0101/FTF-0102 remain blocked on the standing new-runtime-dep
owner ask (unchanged, not re-litigated). `docs/spec-matrix.yaml`'s own
`deps=` field is empty for every not-yet-started task — it tracks
proof-state only, not real dependencies (those live in
`docs/TASK_INDEX.md`'s table). Swept that table directly for any
non-certified task whose real `Dependencies` column is entirely
CERTIFIED (certified set: FTF-0001–0006, FTF-H001, FTF-H002, FTF-H028,
FTF-R001): found FTF-0102 (already blocked), FTF-1810, FTF-H034, FTF-R011,
FTF-R017. Picked FTF-H034 over FTF-R011 specifically because FTF-R011's
own `files` list includes `spec/deployment-profiles.yaml`, which
FTF-H002's already-certified evidence delivered — editing it would raise
a Law 15 requalification question FTF-H034 doesn't. Real DoD resolved via
`tools/r1_handoff.py packet FTF-H034 --plan FINE_TUNING_FACTORY_RELEASE_1.md`
(not improvised).

STEP 2 (PEV):
- P: read the task packet. Its acceptance clause ("execute actual safe
  parallel wave") needs genuine concurrent task execution; this session
  has no live multi-agent dispatch harness to hand a task to a second,
  truly separate worker process. Checked the task's own negative_acceptance
  first ("If runner lacks real subagent capability record orchestration
  blocker, never fabricate workers") — a resource_gate to build around
  honestly (same pattern FTF-H002 used for GPU/Bedrock), not a DoD-clarity
  gap.
- E: implemented `scripts/task_claim.py` (`TaskClaimRegistry`): DAG
  readiness (deny a claim unless every dependency is CERTIFIED in
  `docs/spec-matrix.yaml`), overlapping-claim denial, TTL-based expiring
  claims (stale completion denied), write-set/named-lock exclusivity
  (released on completion), bounded concurrency (constructor rejects a
  configured limit above the hard ceiling of 6; default 4 denies a 5th
  concurrent claim), a GPU quota independent of general concurrency,
  deterministic merge order (real claim-creation time, then task ID,
  independent of completion arrival order), stale-verification
  invalidation on an overlapping later completion, and an
  independent-review guard (`SELF_CERTIFICATION_DENIED` when certifier ==
  claimant — Law 4). The "safe parallel wave" clause is proven with 8 real
  OS threads racing the same claim via a `threading.Barrier` (genuine
  simultaneous contention, not a fabricated sequential loop) — exactly 1
  wins, the registry's state stays consistent. `sync_certified_status()`
  fixes a disclosed, pre-existing gap: `spec/tasks.json` and
  `docs/implementation/TRACKER.json` had carried every task as
  `NOT_STARTED`/empty evidence since bootstrap despite their own header
  comments promising real status; ran it once against the real repo files
  (using `ensure_ascii=False` to avoid a spurious repo-wide non-ASCII
  escaping diff) — both now genuinely show `CERTIFIED` + the real evidence
  hash for exactly the 10 already-independently-certified tasks, nothing
  else. `tests/tasks/test_ftf_h034.py` (17 checks) proves every
  acceptance/negative_acceptance clause via fixtures, the real 8-thread
  race, and a real fault injection against the self-certification guard
  (disabled it in the tracked file, confirmed a fresh subprocess can
  complete a self-certified claim without rejection, restored
  byte-identical, re-ran clean). Also performed an ad hoc second fault
  injection (neutered the write-set/lock overlap loop) confirming
  `test_lock_conflict_denied` fails for real; restored byte-identical.
  Caught and fixed real `ruff` E501 line-length violations before they
  reached CI.
- V: `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, no `proving_symbol` (scheduler/tracking tooling, no live
  product-runtime caller yet, same rationale as every prior FTF-H00X/
  FTF-R00X task).
Re-ran the full existing gate after all changes: `uv run ruff
check`/`format --check`/`mypy .` clean; `pnpm run lint`/`format:check`/
`typecheck` clean (after `pnpm install --frozen-lockfile` — fresh
container, no `node_modules`, same environment note as every prior
shift); all 11 existing task negative-test-proof scripts
(`test_ftf_0001_monorepo_layout.py` through `test_ftf_h028.py`, now
joined by `test_ftf_h034.py`) pass — no already-certified task's own
implementation file was touched beyond the disclosed, additive
`status`/`evidence` fields in `spec/tasks.json`/`TRACKER.json`, avoiding
any other Law 15 requalification question; `uv run pytest tests/unit` (3
passed) and `pnpm exec vitest run` (2 passed) clean, 0 skipped either
side. `validate_spec.py --strict` clean; `verify_task.py --task
FTF-H034 --stage task` → `PASS` against evidence commit `7109100`.
Wired `python3 tests/tasks/test_ftf_h034.py` into `.github/workflows/
ci.yml`'s `gate` job, right after the FTF-H028 step (YAML-validated with
`yaml.safe_load`).
Updated `docs/spec-matrix.yaml` (FTF-H034: IMPLEMENTED/L1-PROVEN/
PROVISIONAL, `evidence_hash` filled in the hash-fixup commit — same
two-commit pattern as FTF-0006/FTF-H001/FTF-R001/FTF-H002/FTF-H028),
`spec/verification-map.json` (real, currently re-executable entry),
`docs/certifications/queue.md` (full entry), `QUEUE.md` (Active cleared,
Provisional entry added, Provisional 0→1). Certified count unchanged at
10/300 — PROVISIONAL is not certified. Per Law 4, did not self-certify.

Opened PR #59 (https://github.com/SJZLab/Sovra/pull/59,
`[relay] FTF-H034: Safe parallel scheduler and task/evidence ownership`)
against `main` from `claude/wizardly-fermat-twhvs6`, subscribed to its
activity.

STEP 4: stopping here for this shift's active work — FTF-H034 is fully
L1-PROVEN and PROVISIONAL, PR #59 open, CI to be confirmed. This session
remains subscribed to PR #59 and will merge on green (per CLAUDE.md's "Do
without asking: merge on green + L1 doc (PROVISIONAL)") without further
owner input, then continue awaiting Control plane L2 certification — not
a self-certification, per Law 4. The 2026-09-16 FTF-0102
new-runtime-dependency owner ask and the 2026-09-15 trigger-tooling ask
remain open and untouched. Once FTF-H034 certifies, FTF-1810/FTF-R011/
FTF-R017 (also found dependency-ready this shift; FTF-R011 specifically
noted above as deferred, not rejected, pending the Law 15 question) or
another spec-matrix/TASK_INDEX sweep become the next candidates.

Certified: 10/300 (unchanged). Provisional: 1 (FTF-H034, PR #59, CI to be
confirmed). Product-true: 0 (unchanged).

## 2026-09-17 — Control plane: FTF-H028 CERTIFIED (PR #55 independently verified)
Autonomous certifier shift, triggered by the PR #55 `opened` webhook. Read
`docs/certifications/queue.md`: FTF-H028 was the only PROVISIONAL item.
Session-ID comparison performed before touching anything: all four commits
on PR #55 (`d502c89`, `183321c`, `813ede7`, `2040620`) carry
`Claude-Session: .../session_01D4UBNWAuJwWHKpfHwSuV7U`; this session is
`session_01W88XoZiR5AQjN9kiCQfCbB` — no overlap, independent per Law 4.

At the time of the webhook, PR #55's CI (`gate` and
`docker-local-stack-integration`) was still `in_progress` — waited for it
to complete rather than certifying against an unfinished run. Both jobs
completed `success` on the PR's real head (`2040620`, run 35176632687)
before certification proceeded. The PR was merged in the interim (by
`samerzakaria`, human) as commit `1de330d`; `git diff 2040620..1de330d`
confirmed empty, so the CI-verified head and the merged tree are identical.

Independent re-verification performed (full detail in
`docs/certifications/ledger.md`'s new FTF-H028 entry): read the diff
directly; re-ran `tests/tasks/test_ftf_h028.py` (33/33 pass) and the full
10-task regression set (`ruff`/`mypy`/`eslint`/`tsc` clean, `pytest`/
`vitest` clean); re-ran `validate_spec.py --strict` and `verify_task.py
--task FTF-H028 --stage task` (`PASS`); confirmed `verify_release.py`
non-inflated (`9/300`/`FAILED (1): ['FTF-H028']`/`NOT READY` before this
edit, `10/300` after); confirmed `product_truth_check.py` stays `0/0`
legitimately (no `proving_symbol` — CI/gate-tooling layer); independently
reproduced the live branch-protection 403 finding with this session's own
`GITHUB_TOKEN`; ran an own fault injection distinct from the producer's two
(flipped `check_branch_protection`'s 403/404 branch to `PASS` — the test
suite caught it with exactly 1 failure, restored byte-identical, re-ran
clean); confirmed the two open items from the prior FTF-H002 review cycle's
note (stale-SHA vs. missing discrimination; no `BLOCKED_EXTERNAL`-as-`PASS`
in the composition) directly against the delivered code and tests; pulled
the real contract via `tools/r1_handoff.py packet FTF-H028` and checked the
delivered work against its actual acceptance/negative_acceptance clauses.

No defect found. Updated `docs/spec-matrix.yaml` (FTF-H028:
`certification_state: CERTIFIED`, `proof_state: L2-CERTIFIED`,
`evidence_hash` kept as the implementation commit `d502c89`, matching the
existing convention for every prior FTF-H00X/FTF-R00X entry), appended the
ledger entry, moved FTF-H028 from QUEUE.md "Provisional" to "Done" and
bumped Certified to 10/300, Provisional to 0.

Certified: 10/300. Provisional: 0. Product-true: 0 (unchanged — FTF-H028
has no `proving_symbol`, same disclosed rationale as every other
certified task so far). The 2026-09-16 FTF-0102 new-runtime-dependency
owner ask and the 2026-09-15 trigger-tooling ask remain open and untouched
— out of scope for this certification-only shift.

## 2026-09-17 — FTF-H028 (PR #55) merged to main as PROVISIONAL
Same relay shift as the FTF-H028 implementation entry below. PR activity
subscription confirmed `gate` and `docker-local-stack-integration` both
`completed`/`success` on the PR's actual current head SHA (`2040620`,
confirmed via `pull_request_read get_check_runs` and cross-checked against
`pull_request_read get` — not a stale/earlier-commit claim; an earlier
`check_suite.completed` event for a superseded intermediate commit
(`813ede7`, before this session's final bookkeeping push) was correctly
recognized as stale and not acted on). `mergeable_state: clean`,
`get_reviews` empty — no open review threads. Matches CLAUDE.md's "Do
without asking: merge on green + L1 doc (PROVISIONAL)" — same discipline
as every prior FTF-000X/FTF-H00X merge. Merged PR #55 (merge commit
`1de330d7cbf71ba36ad8a65565eae1645fbd1929`). Not a certification — still
PROVISIONAL, Law 4 untouched; `docs/spec-matrix.yaml`/`QUEUE.md`/
`docs/certifications/queue.md` already carried the correct PROVISIONAL
bookkeeping as part of this PR itself. Certified: 9/300 (unchanged — a
merge to PROVISIONAL is not a certification). Product-true: 0 (unchanged).
Unsubscribed this session from PR #55's activity (merged, nothing left to
watch). Per the branch-restart convention (this session's designated
branch's PR merged): restarted `claude/wizardly-fermat-s54o5t` from the
new `main` tip (`1de330d`) to record this merge note, rather than stacking
on now-merged history.

STEP 4(a): FTF-H028 is now the queue's only non-certified item and is
fully L1-PROVEN/PROVISIONAL. Stopping — awaiting Control plane L2
certification of FTF-H028, then eventually the L4 gate. Not starting a
second item on top of an uncertified one, same discipline as every prior
FTF-000X/FTF-H00X handoff. The 2026-09-16 FTF-0102 new-runtime-dependency
owner ask and the 2026-09-15 trigger-tooling ask remain open and untouched.

Certified: 9/300 (unchanged). Provisional: 1 (FTF-H028, PR #55, merged,
awaiting L2). Product-true: 0 (unchanged).

## 2026-09-17 — FTF-H028 implemented (Provisional, PR to be opened, awaiting CI + L2 certification)
Autonomous relay shift (scheduled firing, no live user present). STEP 0:
`sha256sum CLAUDE.md` matched committed `CLAUDE.md.sha256` — no drift.
STEP 0.1: read PROGRESS.md/QUEUE.md/docs/certifications/queue.md, then
cross-checked against live repo state: `git fetch origin main` — local
designated branch had no remote counterpart yet (first push this session),
restarted from `origin/main` at `920d6ad` (PR #54's merge, FTF-H002
certification bookkeeping). `BLOCKER.md` held only resolved history.
QUEUE.md Active/Provisional both empty, 9/300 certified, Blocked: 1
(FTF-0102, unchanged open owner ask).

STEP 1: FTF-0101/FTF-0102 remain blocked on the standing new-runtime-dep
owner ask (unchanged, not re-litigated). The prior shift's own note flagged
FTF-H028 (deps FTF-0003, FTF-H001, both CERTIFIED) as the likely next
candidate once FTF-H002 certified — confirmed dependency-ready directly
against `docs/spec-matrix.yaml` before claiming it. Real DoD resolved via
`tools/r1_handoff.py packet FTF-H028 --plan FINE_TUNING_FACTORY_RELEASE_1.md`
(not improvised).

STEP 2 (PEV):
- P: read the task packet. Its acceptance clause ("verify actual branch
  protection through real repository settings") needs a real GitHub API
  read — tested live before assuming infeasibility (not assumed from
  CLAUDE.md's note alone): a direct GET against
  `https://api.github.com/repos/SJZLab/Sovra/branches/main/protection`
  returned a real HTTP 403 `"Resource not accessible by integration"`.
  Section 52 itself names the correct handling for this
  (`resource_gate`: "Missing GitHub/GPU/provider access is
  BLOCKED_EXTERNAL and blocks full release") — a resource_gate to build
  around honestly, not a DoD-clarity gap requiring BLOCKER.md.
- E: implemented `scripts/release_evidence_gate.py` (`GateResult`/
  `GateStatus` mirroring FTF-H002's `PreflightResult` anti-fabrication
  guardrail — a BLOCKED_EXTERNAL result structurally requires a non-empty
  owner/next_action): `check_release_evidence_report` (real check-run
  report validity against the exact release SHA — missing/stale/
  incomplete/non-success all rejected), `check_branch_protection` (a real
  GET, 403/404 honestly BLOCKED_EXTERNAL, a real 200 validated against
  required settings), `check_release_manifest_signature` (an injectable
  verifier; unsigned always fails, signed-with-no-verifier is
  BLOCKED_EXTERNAL — never a fabricated PASS from presence alone),
  `check_required_workflows_have_no_forbidden_patterns` (Section 52's
  named forbidden patterns: continue-on-error/||true/forced-exit-0/focus
  markers), `check_no_untrusted_pull_request_target_checkout` (handles
  PyYAML's `on:` -> boolean-`True` quirk, confirmed empirically first).
  Wired into `scripts/verify_release.py` behind an opt-in
  `--include-evidence-gate` flag so every existing caller's behavior is
  unchanged. `.github/workflows/10-release-qualification.yml`: the real
  protected aggregator (`workflow_dispatch`-only, Tier 3/release-only —
  the relay never dispatches it). `.github/CODEOWNERS`: the real
  repository owner (`samerzakaria`, confirmed live via `get_me`/
  `list_repository_collaborators`) assigned to every protected gate path.
  `tests/tasks/test_ftf_h028.py` (33 checks): every check's real
  positive/negative branches via fixtures, plus a genuine live network
  call confirming today's real branch-protection denial (not only a
  fixture), plus two independent real fault injections (one in-test
  against the live imported module, one external against the tracked
  file's stale-SHA branch, run separately and restored byte-identical
  with `diff` confirming empty) proving the checks are load-bearing.
  Caught zero lint/type defects this shift (clean on first full run after
  `ruff format`).
- V: `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, no proving_symbol (CI/gate tooling layer, no live
  product-runtime caller, same rationale as every prior FTF-H00X/FTF-R00X
  task).
Re-ran the full existing gate after all changes: `uv run ruff
check`/`format --check`/`mypy .` clean; `pnpm run lint`/`format:check`/
`typecheck` clean; all 10 existing task negative-test-proof scripts
(`test_ftf_0001_monorepo_layout.py` through `test_ftf_h002.py`, now joined
by `test_ftf_h028.py`) pass unmodified — no already-certified task's own
implementation file was touched, only additive modules/workflow/
CODEOWNERS, avoiding any Law 15 requalification question; `uv run pytest
tests/unit` (3 passed) and `pnpm exec vitest run` (2 passed) clean, 0
skipped either side. `validate_spec.py --strict` clean; `verify_task.py
--task FTF-H028 --stage task` → `PASS` against evidence commit `d502c89`.
Wired `python3 tests/tasks/test_ftf_h028.py` into `.github/workflows/
ci.yml`'s `gate` job, right after the FTF-H002 step (YAML-validated with
`yaml.safe_load`); the new `10-release-qualification.yml` file itself is
`workflow_dispatch`-only, deliberately never triggered by an ordinary PR
per CLAUDE.md's ship-gate rule.
Updated `docs/spec-matrix.yaml` (FTF-H028: IMPLEMENTED/L1-PROVEN/
PROVISIONAL, `evidence_hash` filled in the hash-fixup commit — same
two-commit pattern as FTF-0006/FTF-H001/FTF-R001/FTF-H002),
`spec/verification-map.json` (real, currently re-executable entry),
`docs/certifications/queue.md` (full entry), `QUEUE.md` (Active cleared,
Provisional entry added, Provisional 0→1). Certified count unchanged at
9/300 — PROVISIONAL is not certified. Per Law 4, did not self-certify.

Opened PR #55 (https://github.com/SJZLab/Sovra/pull/55,
`[relay] FTF-H028: Trusted CI and exact-revision release evidence gate`)
against `main` from `claude/wizardly-fermat-s54o5t`, subscribed to its
activity. Confirmed real CI (`gate` + `docker-local-stack-integration`,
run 35176601514) genuinely `in_progress` on the PR's actual head via
`pull_request_read get_check_runs` — not yet green, not merging.

STEP 4: stopping here for this shift's active work — FTF-H028 is fully
L1-PROVEN and PROVISIONAL, PR #55 open, CI in flight. This session
remains subscribed to PR #55 and will merge on green (per CLAUDE.md's "Do
without asking: merge on green + L1 doc (PROVISIONAL)") without further
owner input, then continue awaiting Control plane L2 certification — not
a second self-certification, per Law 4. The 2026-09-16 FTF-0102
new-runtime-dependency owner ask and the 2026-09-15 trigger-tooling ask
remain open and untouched. If real CI has not gone green by the next
scheduled relay fire, the next shift should check PR #55's real CI status
directly (`pull_request_read` get_check_runs) before doing anything else
with FTF-H028, rather than re-deriving this from scratch.

Certified: 9/300 (unchanged). Provisional: 1 (FTF-H028, PR #55, CI in
progress). Product-true: 0 (unchanged).

## 2026-09-17 — Control plane certification — FTF-H002 CERTIFIED (PR #51 verified)
Scheduled CERTIFY routine (triggered by the `pull_request.opened` webhook
for unrelated PR #52; per its own instructions, its whole job is checking
`docs/certifications/queue.md` for anything PROVISIONAL, so it worked
FTF-H002, not PR #52). Session-ID independence confirmed first: read every
commit on PR #51 (`40b61d2`/`3f5e618`/`af2b0fa`/`3d235b4`/`419f9cc` carry
`session_01KcK5jpotjXCpjrQj8qMPVp`; `1df4f83`/`55d3ba6`/`c6985e0` carry
`session_01BNQGXo9Q8t1PFdEmxQZXHF`; merge commit `b0f8184` is a human
GitHub merge, no session) — this session's ID
(`session_01ED5D84zpb2djpogKY9fktw`) matches neither, independent per Law 4.

Re-read the full diff directly (`environment_preflight.py`,
`release_scope_lock.py`/`release-scope.yaml`,
`generate_compatibility_lock.py`/`compatibility-lock.json`,
`budget-forecast.md`, `test_ftf_h002.py`). Confirmed CI real and green on
PR #51's actual head via `pull_request_read get_check_runs` (`gate` run
35168539940/job 105034980267, `docker-local-stack-integration` job
105034980058, both `success`, tied to head `c6985e0` per `pull_request_read
get`), and that the merge commit `b0f8184` carries that head's tree
unchanged. Installed the real toolchain (`uv sync`, `uv pip install
pyyaml` — matching CI's separate `pip install pyyaml` step, `pnpm install
--frozen-lockfile`) and re-ran both of the task's own
`verification_commands` for real: `validate_spec.py --strict` clean;
`verify_task.py --task FTF-H002 --stage task` → `PASS` against evidence
commit `40b61d2`. Full 9-task regression set clean (`ruff check`/`mypy`/
`pytest tests/unit` and all 9 `tests/tasks/test_ftf_*` + monorepo-layout
scripts). `product_truth_check.py --fail-on-unwired` → `0/0`, confirmed
legitimate (`proving_symbol: ''` in spec-matrix.yaml — no live
product-runtime caller yet, same disclosed rationale as every prior
foundation task). `verify_release.py` run before this certification's own
edits correctly reported `8/300`/`FAILED (1): ['FTF-H002']`/`NOT READY` —
non-inflated.

Own real fault injection, distinct from the producer's (which targeted
`PreflightResult`'s owner/next_action guard): removed
`check_managed_provider_bedrock`'s "unvalidated account/region/model
access" rejection so AWS-credential-env-var presence alone reports
`AVAILABLE` — `tests/tasks/test_ftf_h002.py` caught it for real (1
failure, exactly on
`test_bedrock_check_never_claims_available_from_credential_presence_alone`),
restored byte-identical (`diff` confirmed empty), re-ran clean (15/15).
Pulled the real task contract via `tools/r1_handoff.py packet FTF-H002
--plan FINE_TUNING_FACTORY_RELEASE_1.md` and checked delivered code
against its actual acceptance/negative_acceptance clauses directly (not
just "tests pass") — all hold, including confirming `check_litellm_gateway`
reads `SOVRA_LITELLM_GATEWAY_URL` independently of FTF-0005's
`Settings.from_env()` and that `budget-forecast.md` invents no dollar rate.

Certified: set `docs/spec-matrix.yaml` FTF-H002 to
`certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`; appended
the full re-verification entry to `docs/certifications/ledger.md`; moved
FTF-H002 from "Provisional" to "Done" in `QUEUE.md` (Certified: 9/300,
Provisional: 0); recorded here. Full entry:
`docs/certifications/ledger.md` ("FTF-H002 — Freeze release scope,
compatibility and measured execution inputs").

No other queue item pending certification this shift — FTF-0101/FTF-0102
remain blocked on the standing new-runtime-dependency owner ask
(unchanged, not re-litigated). No task-work started or advised; this
session's scope was certification only, per its own operating
instructions.

## 2026-09-17 — FTF-H002 PR #51 merged to main (PROVISIONAL) — shift closeout
PR #51's real `gate` + `docker-local-stack-integration` CI went green on
its actual final head (`c6985e0`, run 35168539940 — confirmed via
`pull_request_read get_check_runs`, not self-reported), with no open
reviews. Merged into `main` as commit `b0f8184` (`merge_pull_request`,
`expectedHeadSha` pinned to `c6985e0` to guard against a stale head).
Unsubscribed this session from PR #51's activity (merged, nothing left
to watch). Updated `QUEUE.md`'s "Provisional" entry and this file to
record the merge; `docs/certifications/queue.md`'s 2026-09-17
resubmission entry (already merged in via #51) stands as written and
needs no further edit — the certification decision itself is Control
plane's to make independently, not this session's.

Final state this shift: FTF-H002 is `proof_state=L1-PROVEN`,
`certification_state=PROVISIONAL`, genuinely merged to `main` with green
CI on its real final head. Not self-certified (Law 4) — the relay does
not certify its own work; this is queued for the next independent
Control plane L2 review. Certified: 8/300 (unchanged — merging does not
certify) · Provisional: 1 (FTF-H002) · Product-true: 0 (unchanged).

STEP 4: no stop condition applies cleanly (not N/N proven, no true
blocker, not stuck, no ask-first item newly raised) — this was simply
the end of the concrete action available this shift (FTF-H002 driven
from "CI red, do-not-merge" to "green and merged, awaiting L2"). Per
QUEUE.md, FTF-0101/FTF-0102 remain the only other queued item and both
stay blocked on the standing owner ask (unchanged, not re-litigated this
shift). Not spending this shift's remaining effort starting a new task
from scratch after already landing FTF-H002 — leaving that to the next
shift's own fresh `docs/spec-matrix.yaml` dependency sweep.

Next shift: an earlier shift's sweep (see the 2026-09-17 "implemented"
entry below) also found `FTF-H028` (deps `FTF-0003`, `FTF-H001`, both
CERTIFIED) dependency-ready alongside FTF-H002. Worth checking first via
`python tools/r1_handoff.py packet FTF-H028 --plan FINE_TUNING_FACTORY_RELEASE_1.md`
for its real DoD before claiming it — not verified or started by this
shift, just flagged as the likely next candidate given FTF-0101/0102
stay blocked on the owner ask.

## 2026-09-17 — FTF-H002 CI fix confirmed green, resubmitted via superseding PR
Autonomous relay shift (scheduled firing, no live user present).
STEP 0: `sha256sum CLAUDE.md` matched committed `CLAUDE.md.sha256` — no
drift.
STEP 0.1: read PROGRESS.md/QUEUE.md/docs/certifications/queue.md. QUEUE.md
Active named a single concrete next action on FTF-H002: rewrite
`test_kubernetes_cni_check_reflects_real_path_state`'s unisolated first
assertion to use a real empty-PATH temp directory (the fix Control
plane's 2026-09-16 NOT-CERTIFIED review had pinpointed), push to the
existing branch/PR #49, confirm `gate` goes green, then resubmit for
certification.

STEP 1/2 (P): fetched `origin/claude/wizardly-fermat-brui19` and found
the fix already present and pushed — commit `419f9cc`, authored by the
same producer session that opened PR #49 (`session_01KcK5jpotjXCpjrQj8qMPVp`),
applying exactly the isolation pattern the review named. No new code
needed from this shift; verified rather than re-did the work.

STEP 2 (V): queried the real GitHub Actions run on that exact head
(run 35160854110, not self-reported) — both `gate` and
`docker-local-stack-integration` completed `success`. Confirmed no
review requested changes and no other CI signal blocking.

Attempted to merge PR #49 directly (`merge_pull_request`) — GitHub's
merge API returned a genuine conflict (405, "Pull Request has merge
conflicts"). Reproduced locally with a real trial merge
(`git merge --no-commit --no-ff`), confirming it wasn't a stale-check
false positive: `main`'s tip had picked up PR #50 (the NOT-CERTIFIED
review commit `81ebb41`), which edited the same two bookkeeping files
(`QUEUE.md`, `docs/certifications/queue.md`) PR #49's own bookkeeping
commit (`3f5e618`) touched. Separately, this session's own git-branch
policy requires developing only on `claude/wizardly-fermat-w4h9ys` and
forbids pushing to any other branch — so pushing a resolution commit
onto `claude/wizardly-fermat-brui19` itself was never an option, apart
from the conflict.

Resolution: merged `origin/main` into a local copy of `419f9cc` on
`claude/wizardly-fermat-w4h9ys`. Every file besides the two bookkeeping
docs merged as a clean fast-forward (confirmed via `git diff` against
`419f9cc`'s own tree — zero difference outside `QUEUE.md` and
`docs/certifications/queue.md`). Resolved both conflicts by keeping
both sides' history (this file's chronological log entries; QUEUE.md's
current true state) rather than dropping either side's record. Re-ran
the full CI-equivalent regression against the merged tree before
opening anything: `uv sync`, `pnpm install --frozen-lockfile`,
`ruff check`/`format --check`/`mypy` clean, `pnpm run
lint`/`format:check`/`typecheck` clean, all 9 task
negative-test-proof scripts (`test_ftf_0001_monorepo_layout.py` through
`test_ftf_r001.py`, `test_ftf_h002.py` now genuinely passing with 15
checks including the fixed kubectl-isolation assertion), `pytest
tests/unit` (3 passed) and `vitest run` (2 passed) clean, `check_test_harness.py`
clean (0 skipped both sides), `validate_spec.py --strict` clean,
`verify_task.py --task FTF-H002 --stage task` → PASS against evidence
commit `40b61d2` (same two-commit pattern as FTF-0006/H001/R001 — the
evidence_hash intentionally still points at the implementation commit,
not the later hash-fixup/test-fix commits), `product_truth_check.py
--fail-on-unwired` → `0/0` (legitimate, unchanged), `status.py` →
`8/300 certified · 0 product-true` (unchanged — no inflation from this
shift's own bookkeeping).

Opened PR #51 from `claude/wizardly-fermat-w4h9ys` carrying
byte-identical code to `419f9cc` plus the conflict resolution, and
closed PR #49 as superseded (comment posted explaining why, same
commits, same CI-green evidence, same task — the new PR exists only
because of the merge conflict and this session's branch constraint).
Updated QUEUE.md (moved FTF-H002 from "Active" to "Provisional" with
the full resolution history) and appended a resubmission entry to
`docs/certifications/queue.md` dated 2026-09-17 documenting all of the
above, for the next independent Control plane L2 review per Law 4. Not
self-certified.

Subscribed this session to PR #51's activity (`subscribe_pr_activity`)
rather than blocking on a sleep/poll loop — GitHub Actions was still
`in_progress` on both jobs (run `35168462460`) at the time this entry
was written. Per "merge on green + L1 doc (PROVISIONAL)" (CLAUDE.md "Do
without asking") and this repo's established pattern (producer merges
its own PR once CI is genuinely green, independent Control plane
certifies later), this session will merge PR #51 into `main` as soon as
the real `gate` + `docker-local-stack-integration` check-suite event
confirms green — not before. If that event has not arrived by the next
scheduled relay fire, the next shift should check PR #51's real CI
status directly (`pull_request_read` get_check_runs) before doing
anything else with FTF-H002, rather than re-deriving this from scratch.

Certified: 8/300 · Provisional: 1 (FTF-H002, PR #51, CI in progress) ·
Product-true: 0 (unchanged this shift).

## 2026-09-17 — FTF-H002 implemented (Provisional, PR to be opened, awaiting CI + L2 certification)
Autonomous relay shift (scheduled firing, no live user present). STEP 0:
`sha256sum CLAUDE.md` matched committed `CLAUDE.md.sha256` — no drift.
`cognee-cli doctor` — command not found, text-search fallback active
(unchanged from every prior shift).
STEP 0.1: read PROGRESS.md/QUEUE.md/docs/certifications/queue.md, then
cross-checked against live repo state: `git fetch origin main` — local
designated branch (`claude/wizardly-fermat-brui19`) already matched
`origin/main` exactly at `9cbc1b3` (PR #48's FTF-R001 certification merge).
`list_pull_requests` state=open returned empty. QUEUE.md Active/Provisional
both empty, 8/300 certified, Blocked: 1 (FTF-0102, still-open new-runtime-dep
owner ask, unanswered — left untouched per QUEUE.md's "owner actions live in
one place" rule).

STEP 1: FTF-0101/FTF-0102 remain blocked (FTF-0102 on the open owner ask;
FTF-0101 behind FTF-0102). Rather than treating the queue as exhausted, swept
`docs/spec-matrix.yaml` directly for any task whose real `deps=` are all
CERTIFIED (same method the FTF-R001 shift used) — found two:
`FTF-H002` (deps FTF-H001, FTF-0005) and `FTF-H028` (deps FTF-0003, FTF-H001).
Picked FTF-H002: it matches QUEUE.md's own stated read order ("FTF-H001/H002
(release-scope freeze) before anything claims DONE") and Section 84.2's own
first-execution sequence ("Under FTF-H002, inspect available environment
bindings and report real external prerequisites... Under FTF-R001, freeze
the component/interface ownership"). Real DoD resolved via
`tools/r1_handoff.py packet FTF-H002 --plan FINE_TUNING_FACTORY_RELEASE_1.md`
(not improvised).

STEP 2 (PEV):
- P: read the task packet. Its `files` (`spec/release-scope.yaml`,
  `spec/compatibility-lock.json`, `apps/api/app/services/environment_preflight.py`,
  `docs/operations/budget-forecast.md`) and Section 64.2/64.3/64.4 ask for a
  real environment-discovery gate: GPU/driver, Kubernetes CNI, LiteLLM
  gateway, managed-provider (Bedrock) account, and coding-provider billing
  evidence. None of these real external resources exist in this sandbox.
  Checked Section 64.3 itself before treating this as a blocker: "Missing
  inputs use BLOCKED_EXTERNAL with owner and exact next action... The
  developer can build contracts and isolated unit code while blocked, but
  cannot mark real integration or full release PASS." This is the same
  documented precedent FTF-0004 (registry egress) and FTF-0005/0006
  (ask-first new runtime deps) already used in this repo — a resource_gate
  to work within, not a DoD-clarity gap requiring BLOCKER.md (the resolved
  packet's contract was exact, not unclear).
- E: implemented `apps/api/app/services/environment_preflight.py`
  (stdlib-only; `PreflightResult.__post_init__` structurally forbids a
  `BLOCKED_EXTERNAL` result with an empty `owner`/`next_action` — the
  anti-fabrication guardrail; real, non-canned checks for Python/git/GPU
  driver/Kubernetes CNI (kubectl-presence proxy, disclosed as narrower than
  full cluster CNI enforcement)/LiteLLM gateway (reuses FTF-0004's
  `check_tcp_reachable`, deliberately independent of FTF-0005's full
  `Settings.from_env()` per this task's own negative_acceptance)/managed
  Bedrock (real AWS credential env-var presence check that still never
  reports AVAILABLE from presence alone, since validated account/model
  access needs the unapproved AWS SDK)/coding-provider billing (always
  BLOCKED_EXTERNAL — no billing API reachable from this session)),
  `scripts/release_scope_lock.py` + generated `spec/release-scope.yaml`
  (CAP-01..CAP-21, generated from the frozen `spec/qualification-inventory.json`
  rather than hand-authored, with a whole-set validator rejecting a
  removed/downgraded CAP or a cut implementation/qualification scope),
  `scripts/generate_compatibility_lock.py` + generated
  `spec/compatibility-lock.json` (a live-regenerated preflight snapshot,
  `overall_status: BLOCKED_EXTERNAL` today, not a hand-typed table), and
  `docs/operations/budget-forecast.md` (real `git log`-derived measurements;
  discloses the coding-provider billing gap as BLOCKED_EXTERNAL and invents
  no per-token/hour/task dollar rate, per this task's own negative_acceptance).
  `tests/tasks/test_ftf_h002.py` (15 checks) proves the owner/next_action
  guard, every check's real positive/negative branch via genuine
  probes (a real empty-PATH temp dir, a real executable fake `kubectl` on a
  real temp PATH entry, a real bound-then-closed local socket for the
  LiteLLM check), that Bedrock never reports AVAILABLE from credential
  presence alone, that the release-scope validator rejects all three
  negative_acceptance-style mutations, that the committed compatibility-lock
  matches a live re-run (not stale), and that the budget-forecast doc
  discloses the gap without a fabricated rate.
  Law 1 evidence: temporarily removed the owner/next_action guard block from
  the tracked `environment_preflight.py`, then ran a genuinely fresh
  subprocess (new interpreter, so the edited file is actually re-imported)
  attempting the same fabricated construction — it succeeded for real
  (`CONSTRUCTED_WITHOUT_RAISING`) where the unmodified guard rejects it;
  restored byte-identical (`read_text() == original` asserted directly),
  re-ran clean.
  Caught and fixed real `ruff` E501 line-length violations (in both the new
  module and the test file) and real `mypy` `call-overload` errors (untyped
  `dict[str, object]` values in `release_scope_lock.py` tightened to
  `dict[str, Any]`, matching `scripts/architecture_lock.py`'s existing
  pattern) before they reached CI.
- V: `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, no proving_symbol (preflight/release-scope-freeze layer, no
  live product-runtime caller yet, same rationale as FTF-0002/0003/H001/R001).
Re-ran the full existing gate after all changes (first `pnpm install
--frozen-lockfile` — this fresh container had no `node_modules`, unrelated to
this change, same environment note as every prior shift): `uv run ruff
check`/`format --check`/`mypy` clean; `pnpm run lint`/`typecheck`/
`format:check` clean; all 9 existing task negative-test-proof scripts
(`test_ftf_0001_monorepo_layout.py` through `test_ftf_r001.py`, now joined by
`test_ftf_h002.py`) still pass unmodified — no already-certified task's own
implementation file was touched, only additive modules/scripts/docs, avoiding
any Law 15 requalification question; `uv run pytest tests/unit` (3 passed)
and `pnpm exec vitest run` (2 passed) both clean, 0 skipped either side.
Wired `python3 scripts/release_scope_lock.py` and `python3
tests/tasks/test_ftf_h002.py` into `.github/workflows/ci.yml`'s `gate` job,
right after FTF-R001's step (YAML-validated with `yaml.safe_load`).
Did **not** add an FTF-H002 entry to `spec/verification-map.json` in this
commit — following the same two-commit pattern as FTF-0006/FTF-H001/FTF-R001
(a commit can't embed its own hash), that entry is added in the hash-fixup
commit once this implementation commit's real hash is known.
Updated `docs/spec-matrix.yaml` (FTF-H002: IMPLEMENTED/L1-PROVEN/
PROVISIONAL, `evidence_hash` left blank pending the hash-fixup commit),
`docs/certifications/queue.md` (full entry), `QUEUE.md` (Active cleared,
Provisional entry added, Provisional 0→1). Certified count unchanged at
8/300 — PROVISIONAL is not certified. Per Law 4, did not self-certify.

STEP 4: stopping here for this shift's active work — FTF-H002 is fully
L1-PROVEN and PROVISIONAL, PR to be opened against `main` and this session
will subscribe to its activity, merging on green (per CLAUDE.md's "Do
without asking: merge on green + L1 doc (PROVISIONAL)") without further
owner input, then continue awaiting Control plane L2 certification — not a
second self-certification, per Law 4. The 2026-09-16 FTF-0102
new-runtime-dependency owner ask and the 2026-09-15 trigger-tooling ask
remain open and untouched. Once FTF-H002 certifies, FTF-H028 (deps
FTF-0003, FTF-H001, also already dependency-ready) or another spec-matrix
sweep become the next candidates.

Certified: 8/300 (unchanged). Product-true: 0 (unchanged).

## 2026-09-16 — FTF-R001 (PR #47) — Control plane certification CERTIFIED
Independent certifier session (session_01MrAN34weU7YzSCe6khm4H4 — does not
match PR #47's producer session_014i1TTcqPb3qynFFHsBfkvo, confirmed before
starting). Triggered by the `pull_request.opened` webhook for PR #47;
checked `docs/certifications/queue.md` and found FTF-R001 the only
PROVISIONAL item (FTF-0001–0006 and FTF-H001 all already `CERTIFIED` in
`docs/spec-matrix.yaml`). CI was still `queued` on first read — subscribed
to PR activity and waited for the real `check_suite.completed` webhook
event rather than assuming green. Once CI genuinely completed
(`gate`/`docker-local-stack-integration` both `success` on the PR's actual
head `924f6985`, `mergeable_state: clean`), merged PR #47 myself (permitted
under CLAUDE.md's "Do without asking: merge on green + L1 doc
(PROVISIONAL)" — no other session was doing it), then performed the full
independent re-verification against the real merged tip in a fresh
`git worktree`: real `uv sync`/`pnpm install`, both `verification_commands`
genuinely re-run, full 8-task regression clean, `product_truth_check.py`
0/0 confirmed legitimate (no `proving_symbol`), `verify_release.py`
confirmed non-inflated (`7/300`/`NOT READY` before this certification), two
own fault injections distinct from the producer's own two (widened the
mutable-tag `pattern`; loosened the release-authority `maxItems`/`type`
guard — each let the corresponding negative_acceptance case through with
zero errors until restored), and the real task-packet contract
(`tools/r1_handoff.py packet FTF-R001`) checked clause by clause, including
verifying the undisclosed-schema `ExternalRunBinding` gap matches existing
certified precedent (FTF-0004's `web` exclusion, FTF-H001's contract-stub
exclusion) rather than accepting the analogy on the producer's word alone.
Full evidence trail in `docs/certifications/ledger.md`. Set
`docs/spec-matrix.yaml` FTF-R001 to `certification_state: CERTIFIED` /
`proof_state: L2-CERTIFIED`. Moved FTF-R001 from Provisional to Done in
`QUEUE.md`; Certified count now 8/300, Provisional 0. Product-true stays 0
(FTF-R001 has no proving_symbol — architecture/schema freeze, no live
product-runtime caller yet). Next dependency-ready item per
`tools/r1_handoff.py packet`: none currently unblocked besides the
FTF-0101/FTF-0102 pair, both still blocked on the open new-runtime-
dependency owner ask (see QUEUE.md "Owner actions") — not claimed by this
certifier session, since certification is this session's entire job (per
CLAUDE.md "Relay is execution-only" / this session's own CERTIFY-only
mandate).

## 2026-09-16T21:02:23Z — FTF-R001 implemented (Provisional, PR #47 opened, awaiting CI + L2 certification)
Autonomous relay shift (scheduled firing, no live user present). STEP 0:
`sha256sum CLAUDE.md` matched committed `CLAUDE.md.sha256` — no drift.
STEP 0.1: read PROGRESS.md/QUEUE.md/docs/certifications/queue.md, then
cross-checked against live repo state: `git fetch origin main` (local
branch tip matched `origin/main` exactly at `5ffd2c4`), no open PRs, no
live BLOCKER.md entry. QUEUE.md Active/Provisional both empty, 7/300
certified, Blocked: 1 (FTF-0102).

STEP 1: QUEUE.md's own "Owner actions" block named FTF-0102 as the next
dependency-ready item, but it is blocked on the still-open 2026-09-16
new-runtime-dependency owner ask, and FTF-0101 is blocked behind FTF-0102
itself. Rather than treating the queue as exhausted, checked
`docs/spec-matrix.yaml` directly for any other item whose real
dependencies are already CERTIFIED: FTF-R001 (Freeze seven-component
architecture, ownership and version-lock schemas) has `deps=FTF-0001,
FTF-H001`, both CERTIFIED — a real, separate dependency-ready item,
unrelated to the blocked FTF-0101/FTF-0102 pair and not requiring any new
runtime dependency (schema/doc freeze, no Postgres/SQLAlchemy/FastAPI
involved). Pulled its real DoD via `tools/r1_handoff.py packet FTF-R001
--plan FINE_TUNING_FACTORY_RELEASE_1.md` (not improvised) before claiming.

STEP 2 (PEV):
- P: read the task packet in full. `files` lists
  `packages/component-contracts/architecture.schema.json`,
  `spec/component-lock.schema.json`, `spec/deployment-profiles.yaml`,
  `docs/architecture/reuse-ownership.md` — a schema/contract freeze, not a
  live service. Confirmed no `packages/component-contracts/` or
  `packages/integrations/` directories existed yet. Decided: freeze the
  two JSON Schema contracts for real (not decorative JSON sitting next to
  hand-written duplicate logic) by writing a bounded validator that
  actually reads and enforces them, rather than writing Python business
  logic with the schema files as unused documentation. Deliberately did
  not create `ExternalRunBinding` or
  `packages/integrations/{mlflow,argilla,litellm}/` — no live component
  run or adapter exists yet to back them; disclosed as a follow-up rather
  than inventing placeholder adapters (Law 11), same pattern as FTF-H001's
  undelivered contract-stub paths and FTF-0004's `web` exclusion.
- E: wrote `packages/component-contracts/architecture.schema.json`
  (`ComponentBinding` pinned to exactly the seven canonical component
  keys via its `componentKey` enum; an `allOf`/`if`/`then` rule
  structurally forces Axolotl/Inspect AI to `deployment_mode:
  worker_package` with `approved_endpoint: null` — Section 77.1: worker-
  executed software, not assumed always-on HTTP services; LiteLLM pinned
  to `existing_gateway`; `release_approval_authorities` capped to exactly
  one entry via `minItems`/`maxItems: 1`, whose `component_key` must be
  `null` — Section 77.4's "no competing authority", a vendor component
  can never hold release approval), `spec/component-lock.schema.json`
  (`ComponentVersionLock`; `package_version` pattern requires an exact
  pinned semantic version, `image_digest`/`configuration_digest` pattern
  requires a real `sha256:` content digest — neither matches a mutable
  `latest`/`main`/`master`/`HEAD` tag), `spec/deployment-profiles.yaml`
  (the four Section 79.2 placement profiles verbatim + the Section 79.5
  assertion-applicability worked example), `docs/architecture/
  reuse-ownership.md` (ComponentBinding/VersionLock/ExternalRunBinding
  ownership, the Section 77.3 responsibility table, the Section 77.6 "UI
  calls the Factory API, never a vendor dashboard" rule), and
  `scripts/architecture_lock.py` — a bounded JSON Schema validator
  (`type`/`enum`/`const`/`pattern`/`minLength`/`required`/`properties`/
  `additionalProperties`/`items`/`minItems`/`maxItems`/`allOf`-`if`-`then`/
  local `$ref`, matched to exactly what these two schemas use, not a
  general-purpose engine) plus the one whole-set business rule no
  per-record schema keyword can express: the seven bindings/locks must be
  exactly the seven canonical keys, each exactly once (`_check_exact_key_set`).
  `tests/tasks/test_ftf_r001.py` (13 checks, 16 individual `check()`
  assertions) proves: the two frozen schemas' `componentKey` enums stay in
  lockstep with the shared `CANONICAL_COMPONENT_KEYS` constant and each
  other; a genuinely correct binding/lock document passes with zero
  errors; and all four of the task's own `negative_acceptance` clauses are
  rejected — (1) missing component, (2) an invented component key
  substituted for a canonical one, (3) an invented always-on HTTP endpoint
  on Axolotl and separately on Inspect AI, (4) a second release approval
  authority record and separately a vendor component (`mlflow`) claiming
  authority, (5) a mutable `latest` `package_version` and separately a
  mutable `latest` `image_digest`.
  Law 1 evidence (two independent real fault injections, both reverted):
  (a) temporarily replaced the whole-set key-check calls in
  `scripts/architecture_lock.py` with `pass` — exactly the
  missing/duplicate-component checks failed for real (3 failures);
  restored byte-identical (`diff` confirmed empty against a pre-edit
  copy), re-ran clean. (b) temporarily removed the Axolotl/Inspect-AI
  worker-package `allOf` rule from the shipped `architecture.schema.json`
  itself — exactly the two invented-endpoint checks failed for real;
  restored byte-identical, re-ran clean. Additionally confirmed as a
  structural fact (not a fixture): `component_bindings` is schema-pinned
  to exactly 7 items, so a *missing* component always also trips item
  count, but a *duplicate* substituted for a missing key keeps the count
  at 7 with every individual binding still schema-valid — schema-only
  validation genuinely passes that fixture, while the real validator
  (schema + whole-set check) rejects it, confirming the business-rule
  check is load-bearing rather than a decorative re-assertion of what the
  schema already guarantees.
  Caught and fixed real `ruff` E501 line-length violations (9 across the
  new module and test file) before they reached CI.
- V: `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, no `proving_symbol` (architecture/schema freeze, no live
  product-runtime caller yet, same rationale as FTF-0002/0003/H001).
Re-ran the full existing gate after all changes (first installing
`pnpm install --frozen-lockfile` — this fresh container had no
`node_modules`, unrelated to this change, same environment note as prior
shifts, confirmed by re-running the TS/lint/typecheck-dependent tests
before and after install): `uv run ruff check`/`format --check`/`mypy`
clean; `pnpm run lint`/`typecheck`/`format:check` clean; all 8 existing
task negative-test-proof scripts (`test_ftf_0001_monorepo_layout.py`
through `test_ftf_h001.py`, now joined by `test_ftf_r001.py`) still pass
unmodified — no already-certified task's own implementation file was
touched, only additive schemas/docs/tooling, avoiding any Law 15
requalification question; `uv run pytest tests/unit` (3 passed) and
`pnpm exec vitest run` (2 passed) both clean, 0 skipped either side.
Wired `python3 tests/tasks/test_ftf_r001.py` into `.github/workflows/
ci.yml`'s `gate` job, right after FTF-H001's step (YAML-validated with
`yaml.safe_load`).
Committed as `f9c82e910cde344a08b910c5b059e85c25b59adf` (implementation)
and `42c1adf` (hash-fixup — same two-commit pattern as FTF-0006/FTF-H001,
since a commit can't embed its own hash) on branch
`claude/wizardly-fermat-1r7ubn`. Updated `docs/spec-matrix.yaml`
(FTF-R001: IMPLEMENTED/L1-PROVEN/PROVISIONAL, evidence_hash=f9c82e9...),
`spec/verification-map.json` (real, currently re-executable entry —
`verify_task.py --task FTF-R001 --stage task` confirmed `PASS` live
against it), `docs/certifications/queue.md` (full entry), `QUEUE.md`
(Active cleared, Provisional entry added, Provisional 0→1). Certified
count unchanged at 7/300 — PROVISIONAL is not certified. Per Law 4, did
not self-certify. Opened PR #47
(`[relay] FTF-R001: Freeze seven-component architecture, ownership and
version-lock schemas`) against `main`, subscribed to its PR activity —
not merging without confirming CI green first.

STEP 4: stopping here for this shift's active work — FTF-R001 is fully
L1-PROVEN and PROVISIONAL, PR #47 open, CI in flight. This session
remains subscribed to PR #47 and will merge on green (per CLAUDE.md's "Do
without asking: merge on green + L1 doc (PROVISIONAL)") without further
owner input, then continue awaiting Control plane L2 certification — not
a second self-certification, per Law 4. The 2026-09-16 FTF-0102
new-runtime-dependency owner ask remains open and untouched (still the
correct stopping point per the prior shift's note, until the owner
answers it). Once FTF-R001 certifies, FTF-R002 (deps FTF-R001, FTF-H003,
FTF-H006 — the latter two not yet started) or another spec-matrix sweep
for further dependency-ready items become the next candidates.

Certified: 7/300 (unchanged). Product-true: 0 (unchanged).

## 2026-09-16 — FTF-0102 blocked: new-runtime-dependency owner ask (not started)
Autonomous relay shift (scheduled firing, no live user present). STEP 0:
`sha256sum CLAUDE.md` matched committed `CLAUDE.md.sha256` — no drift.
`cognee-cli` still not on PATH — text-search fallback active (unchanged).
STEP 0.1: read PROGRESS.md/QUEUE.md/docs/certifications/queue.md, then
cross-checked against live repo state: `git fetch origin main` — local
branch tip matched `origin/main` exactly at `f886028` (includes PR #45's
FTF-H001 certification merge), `list_pull_requests` state=open returned
empty, `BLOCKER.md` held only resolved history. QUEUE.md Active/
Provisional both empty, 7/300 certified.

STEP 1: `docs/spec-matrix.yaml` shows FTF-0101's own `deps` include
FTF-0102 (not yet done) — so FTF-0101 is not actually dependency-ready
despite QUEUE.md's summary "Queue" ordering listing it first. FTF-0102's
own deps (FTF-0004, FTF-0005, FTF-H001) are all CERTIFIED, making it the
real frontier item. Pulled its real DoD via `python tools/r1_handoff.py
packet FTF-0102 --plan FINE_TUNING_FACTORY_RELEASE_1.md` (not improvised).

STEP 2 (P only — stopped before any E): the resolved contract's
`database_changes` field reads "Own forward Alembic migration for this
resource; tenant/composite constraints/RLS ... Serialize migration
chain," and its negative_acceptance requires cross-tenant parent IDs and
missing tenant context to fail real DB constraints/RLS. Checked this
repo's actual dependency surface before assuming feasibility:
`apps/api/pyproject.toml` has an empty `dependencies` list; root
`pyproject.toml`'s only deps are the ruff/mypy/pytest dev group — zero
Postgres client, ORM, or migration tooling exists anywhere in this repo
today. Confirmed the master architecture table
(`FINE_TUNING_FACTORY_RELEASE_1.md` line 291, the document's own labeled
"binding architecture") names "FastAPI + SQLAlchemy/Alembic +
PostgreSQL" for this exact layer.

Weighed whether that table entry is a standing pre-approval to install
these packages, against this repo's own established precedent: FTF-0004,
FTF-0005, and FTF-0006 each explicitly declined to add FastAPI/pydantic —
named in that *same* table row — specifically citing CLAUDE.md's "new
runtime deps" ask-first hard stop as governing regardless of prior
architecture naming, and disclosed the resulting scope limits as
follow-ups rather than improvising a framework into existence. Treating
FTF-0102 differently (silently installing SQLAlchemy/Alembic/a Postgres
driver because this table row happens to also name a migration tool)
would break that precedent inconsistently. Also considered and rejected
a stdlib/CLI-only workaround (e.g., raw SQL files executed via a `psql`
subprocess against FTF-0004's docker-local-stack Postgres instead of
Alembic): technically avoids a new *Python* package, but silently
substitutes for the contract's explicit, specific "Alembic" naming —
that is improvising the DoD, not executing it, which CLAUDE.md's "Relay
is execution-only" section forbids just as much as an unclear DoD would.

Decision: this is a genuine ask-first item (new runtime dependencies),
not a DoD-clarity gap and not something to route around. Recorded three
concrete options for the owner (approve the named stack as-is; approve a
disclosed stdlib/CLI substitute; or specify something else) in
`QUEUE.md`'s "Owner actions — current" block, dated 2026-09-16, alongside
the still-open 2026-09-15 trigger-tooling ask (owner asks live in one
place only, per CLAUDE.md — did not remove or touch that unresolved
entry). Added FTF-0102 to `QUEUE.md`'s `## Blocked` section and updated
the Status line (`Blocked: 1`). No code written, no dependency installed,
FTF-0102 not claimed as Active/Provisional. Per STEP 4(e), stopping this
shift here.

Certified: 7/300 (unchanged). Product-true: 0 (unchanged). Next shift:
do not re-litigate this decision from scratch — check whether the owner
answered the 2026-09-16 FTF-0102 ask in QUEUE.md first; if still
unanswered, this remains the correct stopping point.

## 2026-09-16 — FTF-H001 (PR #43) — Control plane certification CERTIFIED
Independent certifier session (session_01AmkjgSrjZzfKJXAYH7f8Hp — does not
match PR #43's producer session_01FJDW152aaaSm3uNPj9NCXH, confirmed before
starting). Re-checked `docs/certifications/queue.md` and found FTF-H001 the
only PROVISIONAL item; confirmed PR #44 on the same branch was a
`PROGRESS.md`-only tracking note (`git diff be6494c 37e68bb --stat`), no
code/spec-matrix/queue change, so it does not affect this review. Full
independent re-verification (fresh worktree at `main`'s merged tip, real
`uv sync`/`pnpm install`, both `verification_commands` genuinely re-run,
7-task regression clean, `product_truth_check.py` 0/0 confirmed
legitimate, `verify_release.py` confirmed non-inflated, an own fault
injection against `check_structural`'s cycle detection distinct from the
producer's two, and the real task-packet contract checked clause by
clause) recorded in `docs/certifications/ledger.md`. Set
`docs/spec-matrix.yaml` FTF-H001 to `certification_state: CERTIFIED` /
`proof_state: L2-CERTIFIED`. Moved FTF-H001 from Provisional to Done in
`QUEUE.md`; Certified count now 7/300, Provisional 0. Product-true stays
0 (FTF-H001 has no proving_symbol — CI/gate tooling, not a live
product-runtime path). Next dependency-ready item per
`tools/r1_handoff.py packet`: FTF-0101 or FTF-0102 (both now unblocked —
FTF-H001 was FTF-0102's last outstanding dependency) — not claimed by
this certifier session, since certification is this session's entire
job (per CLAUDE.md "Relay is execution-only" / this session's own
CERTIFY-only mandate).

## 2026-09-16 — FTF-H001 (PR #43) merged to main as PROVISIONAL
Same relay shift as the FTF-H001 implementation entries below. PR
activity subscription confirmed `gate` and `docker-local-stack-integration`
both `completed`/`success` on the PR's actual current head SHA (`ca53fe5`,
confirmed via `pull_request_read get_check_runs` and cross-checked against
`pull_request_read get` — not a stale/earlier-commit claim; the two prior
`check_run.completed` failure events for `d4301b4`/`de41d12`/`3bd23c9`
were correctly recognized as superseded-commit failures, already
root-caused and fixed in the shallow-clone entry above). `mergeable_state:
clean`, `get_reviews` empty — no open review threads. Matches CLAUDE.md's
"Do without asking: merge on green + L1 doc (PROVISIONAL)" — same
discipline as every prior FTF-000X merge. Merged PR #43 (merge commit
`be6494c3ec97615417f9f1e9fabad9dfa83feaf4`). Not a certification — still
PROVISIONAL, Law 4 untouched; `docs/spec-matrix.yaml`/`QUEUE.md`/
`docs/certifications/queue.md` already carried the correct PROVISIONAL
bookkeeping as part of this PR itself. Certified: 6/300 (unchanged — a
merge to PROVISIONAL is not a certification). Product-true: 0 (unchanged).
Per the branch-restart convention (this session's designated branch's PR
merged): restarted `claude/wizardly-fermat-43jlvy` from the new `main` tip
(`be6494c`) to record this merge note, rather than stacking on now-merged
history.
STEP 4(a): FTF-H001 is now the queue's only non-certified item and is
fully L1-PROVEN/PROVISIONAL. Stopping — awaiting Control plane L2
certification of FTF-H001, then eventually the L4 gate. Not starting a
second item (FTF-0102) on top of an uncertified one, same discipline as
every prior FTF-000X handoff.

## 2026-09-16 — FTF-H001 (PR #43): real CI failure found and fixed
PR activity subscription woke this session with a `check_run.completed`
failure on the `gate` job (run 35125855659, head `d4301b4`, since
superseded by later tracking-doc-only pushes). Confirmed against the PR's
actual current head (`3bd23c9` at the time) via `pull_request_read`
before acting, then `get_workflow_job` on the failed run showed the new
"FTF-H001 executable specification validator" step failing for real, not
a flake.
Root cause, reproduced locally before fixing (not assumed): GitHub's
`actions/checkout@v4` defaults to `fetch-depth: 1` (a shallow clone — only
the tip commit, no history). `scripts/spec_verification.py`'s
`git_commit_exists()` calls `git cat-file -e <hash>^{commit}`, which needs
the commit object to actually exist locally — in a shallow clone, none of
the historical evidence commits (FTF-0001 through FTF-0006, all real,
merged commits on `main`) are present as objects, so `validate_spec.py`
spuriously rejected all 7 entries as `stale_commit`. This sandbox's own
dev clone has full history, which is why the bug was invisible locally.
Reproduced with a real `git clone --depth 1` of this exact branch: same 7
false `stale_commit` failures. Fix: added `fetch-depth: 0` to the `gate`
job's `actions/checkout@v4` step. Verified the fix on the same shallow
clone via `git fetch --unshallow` + re-run — clean. Re-ran the full local
gate after the fix (ruff/mypy, `validate_spec.py --strict`,
`test_ftf_h001.py`) — clean.
Pushed as `86ed2bc`. This is a real defect in this PR's own new CI wiring,
not a base-branch or infra flake — fixed and pushed per the drive-to-green
discipline, not deferred or commented around.

## 2026-09-16 — FTF-H001 implemented (Provisional, awaiting L2 certification)
Autonomous relay shift (scheduled firing, no live user present). STEP 0:
`sha256sum CLAUDE.md` matched committed `CLAUDE.md.sha256` — no drift.
STEP 0.1: read PROGRESS.md/QUEUE.md/docs/certifications/queue.md, then
cross-checked against live repo state: `git fetch origin main` (local
branch tip matched `origin/main` exactly at `b453126`), `list_pull_requests`
state=open returned empty, `BLOCKER.md` held only resolved history.
STEP 0.2: `cognee-cli doctor` — command not found, text-search fallback
active (unchanged from every prior shift).
QUEUE.md Active was empty, 6/300 certified, 0 provisional. QUEUE.md's own
"Queue (ordered by dependency)" section names FTF-0101…0106 next.

PR: https://github.com/SJZLab/Sovra/pull/43 (`claude/wizardly-fermat-43jlvy`
→ `main`). Commits: `6a1abc093f2c8897e86d8e33bd33667d1c2ee0f3`
(implementation) and `d4301b4` (tracking-doc hash fixup — same two-commit
pattern as FTF-0006's `91eeff1`+`7d5834f`, since a commit can't embed its
own hash). Subscribed to PR #43's activity; not merging without confirming
CI green first.

STEP 1: pulled FTF-0101's real DoD via `tools/r1_handoff.py packet
FTF-0101 --plan FINE_TUNING_FACTORY_RELEASE_1.md` before claiming it — its
real `dependencies` are `FTF-0004, FTF-0005, FTF-0102` (not just FTF-0004/
0005 as QUEUE.md's summary ordering might suggest), and `contract_owner`
is `FTF-0102`. Pulled FTF-0102's packet next — its `dependencies` are
`FTF-0004, FTF-0005, FTF-H001`. Checked `docs/spec-matrix.yaml`: FTF-H001
was `certification_state: NONE` / `implementation_state: ABSENT`, with its
own deps (FTF-0001/0002/0003) all CERTIFIED — it, not FTF-0101, was the
real dependency-ready frontier item. Picked FTF-H001. Real DoD resolved
via `tools/r1_handoff.py packet FTF-H001 --plan
FINE_TUNING_FACTORY_RELEASE_1.md` (not improvised).

STEP 2 (PEV):
- P: read the task packet. This task's own `verification_commands`
  (`python scripts/validate_spec.py --strict`, `python scripts/verify_task.py
  --task FTF-H001 --stage task`) name scripts that don't exist yet —
  FTF-H001 is the task that creates them, matching Section 75.1's own
  bootstrap note. Its `files` list also names two `packages/contracts/`
  stub paths; neither is required by its actual acceptance/
  negative_acceptance clauses and no real API endpoint exists yet to back
  an OpenAPI contract — decided not to invent a homeless placeholder
  (Law 11), disclosed as a follow-up instead, same precedent as FTF-0004's
  `web` exclusion.
- E: implemented `scripts/spec_verification.py` (shared parser/verifier:
  `load_tasks()`/`check_structural()` validate spec/tasks.json's 300-task
  dependency/scenario graph against spec/qualification-inventory.json;
  `check_evidence()` validates spec/verification-map.json's recorded
  evidence — commit reachability via real `git cat-file -e`, zero
  tolerance for a recorded skipped test), `spec/verification-map.json`
  (real, currently re-executable commands + real merge-commit hashes for
  the 6 CERTIFIED tasks, pulled from docs/spec-matrix.yaml and verified
  against `git cat-file` directly, not copied blind), `scripts/
  validate_spec.py --strict`, `scripts/verify_task.py --task <ID> --stage
  task` (BLOCKED for any task absent from the map — never a fabricated
  PASS; rejects an unknown `--stage`), and `scripts/verify_release.py`
  (Tier-3/release-only whole-release gate, built but not dispatched by the
  relay per CLAUDE.md).
  Law 1 evidence (`tests/tasks/test_ftf_h001.py`, 13 checks): all six of
  the task's own named fixture kinds (missing_task, cyclic_dependency,
  duplicate_id, missing_scenario, skipped_test, stale_commit) proven
  rejected against constructed fixtures; a valid fixture proven to pass;
  negative_acceptance proven (`verify_task` on an unknown task ID returns
  BLOCKED, never PASS; an unknown stage returns FAIL); the real shipped
  FTF-0002 map entry proven not fabricated by re-running it live through
  the actual CLI against this checkout; two independent real fault
  injections against the live `spec/verification-map.json` file itself
  (corrupted commit hash, corrupted success marker) each shown to make the
  real CLI fail for the right reason, then restored byte-identical
  (`MAP_FILE.read_text() == original` asserted directly). Additionally,
  outside the formal fixture set: temporarily deleted the `stale_commit`
  check block from `scripts/spec_verification.py` itself — this cascaded
  into a genuine `ruff check .` failure, which in turn made FTF-0002's own
  live re-verification fail for real (since its mapped command *is*
  `ruff check .`'s own umbrella script) — confirming the gate catches a
  broken verifier end-to-end; restored via file copy, `diff` confirmed
  empty, re-ran clean.
  Caught and fixed two real lint/type findings before they reached CI: one
  `ruff` E501 line-length violation in `spec_verification.py`, and one
  `mypy` unused-`type: ignore` finding on `load_map()`'s return type
  (fixed with an explicit local annotation instead).
- V: `product_truth_check.py --fail-on-unwired` stays `0/0 product-true` —
  legitimate, no proving_symbol (this is CI/gate tooling, not a live
  product-runtime path, same rationale as FTF-0002/0003). `verify_release.py`
  itself run live against the real repo: correctly reports `6/300 required
  task(s) have live-passing, CERTIFIED evidence` / `NOT READY` — no
  inflation, matches the real known state exactly.
Re-ran the full existing gate after all changes: `uv run ruff
check`/`format --check`/`mypy` clean; `pnpm run lint`/`format:check`/
`typecheck` clean; all 7 existing task negative-test-proof scripts
(`test_ftf_0001_monorepo_layout.py` through `test_ftf_0006.py`, now joined
by `test_ftf_h001.py`) still pass unmodified — no already-certified task's
own implementation file was touched, only additive tooling, avoiding any
Law 15 requalification question; `uv run pytest tests/unit` (3 passed) and
`pnpm exec vitest run` (2 passed) both clean, 0 skipped either side
(`check_test_harness.py` confirms).
Wired `python3 scripts/validate_spec.py --strict` and `python3
tests/tasks/test_ftf_h001.py` into `.github/workflows/ci.yml`'s `gate`
job, right after FTF-0006's step (YAML-validated with `yaml.safe_load`).
Left the existing FTF-0002–0006 CI steps untouched — they still call
their own test scripts directly rather than being rerouted through the
new `verify_task.py`, avoiding a Law 15 requalification question for
their already-certified evidence; `spec/verification-map.json` is an
additive, parallel index for the new verifier, not a replacement for
their own CI steps.
Updated `docs/spec-matrix.yaml` (FTF-H001: IMPLEMENTED/L1-PROVEN/
PROVISIONAL, `evidence_hash` left blank pending a hash-fixup commit — same
two-commit pattern as FTF-0006's `91eeff1`+`7d5834f`, since a commit can't
embed its own hash), `docs/certifications/queue.md` (full entry),
`QUEUE.md` (Active cleared, Provisional entry added, bumped Provisional
0→1). Certified count unchanged at 6/300 — PROVISIONAL is not certified.
Per Law 4, did not self-certify. Opened PR #43 against `main`.
STEP 4: stopping here for this shift's active work — FTF-H001 is fully
L1-PROVEN and PROVISIONAL, PR #43 open, CI in flight. This session
remains subscribed to PR #43 and will merge on green (per CLAUDE.md's "Do
without asking: merge on green + L1 doc (PROVISIONAL)") without further
owner input, then continue awaiting Control plane L2 certification — not
a second self-certification, per Law 4. Once FTF-H001 certifies, FTF-0102
(Organization/workspace schema) becomes the next dependency-ready item —
it also requires a real local Postgres migration/RLS integration
(FTF-0004's docker-local-stack), which this sandbox can run locally per
FTF-0004's own precedent. FTF-0101 (OIDC login) remains blocked behind
FTF-0102.

## 2026-09-16 — FTF-0006 independently certified (Control plane, CERTIFY mode)
Scheduled control-plane certifier shift, triggered by the `pull_request.opened`
webhook for PR #39. Read `docs/certifications/queue.md`: one pending item,
FTF-0006 (Observability foundation), matching the just-opened PR. Session-ID
check: PR #39's commits (91eeff1, 7d5834f, and its final head 0970fa8) all
carry `Claude-Session: .../session_01GvzemvrJeMmGiZsAAPK5m1` — this session
(`session_01LPY7Wy6ASA6BiQnvu9eyvF`) does not match, independent certifier.

Re-read the full diff. Checked out the PR's actual final head (`0970fa8` —
one commit ahead of what the PR body originally reported, confirmed live via
`pull_request_read`) into an isolated git worktree and independently: ran
`tests/tasks/test_ftf_0006.py` (10/10 pass); performed my own fault
injection distinct from the producer's own (broke the `identity`
safe-identifier regex rather than the bounded-label check — the
content-exclusion check failed for real, restored byte-identical, re-ran
clean); ran the full regression suite fresh (`uv sync`, `pnpm install
--frozen-lockfile`: ruff/mypy/pytest/eslint/tsc/vitest all clean, no
regression to FTF-0001–0005's certified evidence); ran
`product_truth_check.py --fail-on-unwired` myself (`0/0`, legitimate —
`proving_symbol: ''`); verified `CLAUDE.md.sha256` matches (no drift);
fetched GitHub Actions run 35112030663 directly via the API (not the PR's
own claim) — both `gate` and `docker-local-stack-integration` jobs
`success` on the confirmed real final head SHA, and read the `gate` job's
log directly, showing the genuine test execution and a real `0/0
product-true` line. Pulled FTF-0006's real contract via
`python tools/r1_handoff.py packet FTF-0006 --plan
FINE_TUNING_FACTORY_RELEASE_1.md` and checked delivered work against its
actual acceptance/negative_acceptance clauses (see ledger entry for detail).

Found one disclosed (not hidden) residual gap: the `identity` field's
safe-identifier format boundary cannot distinguish a legitimate slug-style
ID from an opaque token of the same shape. Unlike the FTF-0005 PR #28
defect (an in-scope, accepted field silently violating the module's own
stated redaction contract), this module never claims to close that case —
its own docstring, validator error message, and test comment all disclose
the limitation honestly, and no live caller exists yet to exploit it. Not
a blocker: recorded as a required follow-up in `docs/spec-matrix.yaml`'s
notes for whichever task first wires a real caller into this seam.

The PR had already been merged on green by the relay (squash commit
`268186bd5e70032a6c563796910059ab2684b684` on `main`) per CLAUDE.md's
"Do without asking: merge on green + L1 doc (PROVISIONAL)" — this
session did not merge it, only certified the already-merged evidence.
Certified: updated `docs/spec-matrix.yaml` (FTF-0006:
certification_state=CERTIFIED, proof_state=L2-CERTIFIED,
evidence_hash=268186b, notes carry the disclosed residual-gap follow-up),
appended a signed `docs/certifications/ledger.md` entry, updated
`QUEUE.md` (Provisional -> Done, Certified bumped 5 -> 6, Provisional
count 1 -> 0) and this file. Per Law 4, this is independent certification,
not self-certification — this session authored none of PR #39's commits
and did not merge it.

Docs/certifications/queue.md's own tracking entries are historical
evidence for the PROVISIONAL submission and are left as-is (append-only
history), same convention as every prior certified item in that file.

Next candidate per dependency order (QUEUE.md "Queue" section):
FTF-0101+ (identity/tenancy/RBAC/audit, CAP-01) — not this session's job;
CERTIFY mode does not propose or start new work.

## 2026-09-16 — FTF-0006 (PR #39) merged to main as PROVISIONAL
Same relay shift as the FTF-0006 implementation entry below. Subscribed to
PR #39's activity after opening it; CI (`gate` and
`docker-local-stack-integration`) both completed `success` on the PR's
actual final head SHA (`0970fa8eb4b80635cd919f929d74f1ac49884325`,
confirmed via `get_check_runs`, not a stale/self-reported claim — an
earlier `check_suite.completed` event for a prior commit on this same
branch, `7d5834f`, was correctly recognized as stale and not acted on).
`mergeable_state: clean`, no open review threads, no other configured
checks (this repo has no Claude Approvals check). Matches CLAUDE.md's
"Do without asking: merge on green + L1 doc (PROVISIONAL)" — same
discipline as the prior FTF-0002/0003/0004/0005 merges. Merged PR #39
(merge commit `268186bd5e70032a6c563796910059ab2684b684`). Not a
certification — still PROVISIONAL, Law 4 untouched; `docs/spec-
matrix.yaml`/`QUEUE.md`/`docs/certifications/queue.md` already carried
the correct PROVISIONAL bookkeeping as part of PR #39 itself, verified
post-merge that all three landed on `main` as expected — no further doc
edits needed there. Certified: 5/300 (unchanged — a merge to PROVISIONAL
is not a certification). Product-true: 0 (unchanged).
Per the branch-restart convention (this session's designated branch's PR
merged): restarted `claude/wizardly-fermat-6dwdsp` from the new `main`
tip to record this merge note, rather than stacking on now-merged
history.
STEP 4(a): FTF-0006 is now the queue's only non-certified item and is
fully L1-PROVEN/PROVISIONAL. Stopping — awaiting Control plane L2
certification of FTF-0006, then eventually the L4 gate. Not starting a
second item (FTF-0101+) on top of an uncertified one, same discipline as
every prior FTF-000x handoff.

## 2026-09-16 — FTF-0006 implemented (Provisional, PR #39 opened, awaiting CI + L2 certification)
Autonomous relay shift (scheduled firing, no live user present). STEP 0:
`sha256sum CLAUDE.md` matched committed `CLAUDE.md.sha256` — no drift.
STEP 0.1: read PROGRESS.md/QUEUE.md/docs/certifications/queue.md, then
cross-checked against live repo state (`git fetch origin main`: local
branch tip matched `origin/main` exactly at `e5b3871`, no open PRs,
BLOCKER.md held only resolved history). QUEUE.md Active was empty with
FTF-0006 named as the next candidate (deps FTF-0001/0004/0005 all
CERTIFIED).
STEP 1: picked FTF-0006 — Observability foundation. Real DoD resolved via
`python tools/r1_handoff.py packet FTF-0006 --plan
FINE_TUNING_FACTORY_RELEASE_1.md` (not improvised).
STEP 2 (PEV):
- P: read the task packet. `files` lists only
  `apps/api/app/config/observability_foundation.py` and its test.
  Confirmed `apps/api` still has no `main.py` (no FastAPI app) and no
  `services/workflow-worker` exists (no Temporal workflow code) — no live
  API route or workflow to instrument yet. Building either now solely to
  host an observability demo would be a new-scope/new-runtime-dep
  ask-first item. Decided to follow FTF-0004's precedent: build the real
  seam stdlib-only, prove it against a genuine local integration (a real
  socket, a real HTTP request over the wire), and disclose the
  workflow-side gap as a documented follow-up rather than inventing a
  placeholder workflow.
- E: implemented `StructuredEvent` (frozen dataclass with a fixed field
  set — structurally no field can hold a raw prompt/source/token/signed
  URL; unexpected keywords raise `TypeError`), safe-identifier format
  validation on `event`/`identity` (blocks prose/newlines/URL
  querystrings/oversized blobs — documented honestly as a format
  boundary, not a secret scanner, since an opaque token shaped like a
  legitimate slug ID is not distinguishable by format alone),
  `MetricsRegistry` (rejects any label outside a predeclared finite set —
  bounded cardinality by construction), and `InstrumentedRequest` (context
  manager: correlation ID + timing + one emitted event per real
  request, never swallows an exception). `tests/tasks/test_ftf_0006.py`
  (10 checks) proves acceptance clause 1 via a real `ThreadingHTTPServer`
  on a real socket hit with a genuine `http.client` request (not an
  in-process call), proves "cardinality remains bounded" quantitatively
  (50 distinct correlation IDs → 1 observed metric label), and proves the
  negative_acceptance content-exclusion cases.
  Law 1 evidence: temporarily removed `MetricsRegistry.record`'s
  bounded-label check, re-ran the suite — exactly the cardinality-bound
  check failed for real; restored byte-identical (`diff` confirmed
  empty), re-ran clean (10/10).
  Caught and fixed one real design gap before finalizing: an initial
  negative-acceptance test case (a token-shaped alnum+hyphen string
  passed as `identity`) was expected to be rejected but the safe-
  identifier regex accepted it — that's a genuine, honest limitation
  (format validation cannot distinguish a legitimate short slug ID from
  an opaque token), not something to paper over. Rewrote the test to
  assert only what the design actually guarantees (structural field
  absence + rejection of prose/newlines/URL querystrings/oversized
  blobs) and documented the limitation in both the module docstring and
  the test's own comment.
  Also fixed two real lint/type findings before they reached CI: 4 `ruff`
  E501 line-length violations, and a `mypy` `exit-return` error on
  `InstrumentedRequest.__exit__` (changed its return type from `bool` to
  `None` so it can never be read as swallowing an exception).
- V: `product_truth_check.py --fail-on-unwired` stays `0/0 product-true`
  — legitimate, no proving_symbol yet (no live caller, same rationale as
  FTF-0002/0003/0004/0005).
Re-ran the full existing gate after all changes: `uv run ruff
check`/`format --check`/`mypy` clean; `uv run pytest tests/unit` (3
passed); `tests/tasks/test_ftf_0002.py`/`test_ftf_0003.py`/
`test_ftf_0004.py`/`test_ftf_0005.py` and
`tests/test_ftf_0001_monorepo_layout.py` all still pass unmodified (ran
`pnpm install --frozen-lockfile` first — this fresh container had no
`node_modules`, unrelated to this change, same environment note as prior
shifts); `pnpm run lint`/`typecheck`/`format:check` and `pnpm exec vitest
run` (2 passed) also clean.
Wired `python3 tests/tasks/test_ftf_0006.py` into `.github/workflows/
ci.yml`'s `gate` job (YAML-validated with `yaml.safe_load`).
Committed as `91eeff1` (implementation) and `7d5834f` (tracking-doc hash
fixup) on branch `claude/wizardly-fermat-6dwdsp`. Updated
`docs/spec-matrix.yaml` (FTF-0006: IMPLEMENTED/L1-PROVEN/PROVISIONAL,
evidence_hash=91eeff1), `docs/certifications/queue.md` (full entry),
`QUEUE.md` (Active cleared, Provisional entry added, bumped Provisional
to 1). Certified count unchanged at 5/300 — PROVISIONAL is not
certified. Per Law 4, did not self-certify. Opened PR #39
(`[relay] FTF-0006: Observability foundation`) against `main`, subscribed
to its PR activity — not merging without confirming CI green first.
STEP 4: stopping here for this shift's active work — FTF-0006 is fully
L1-PROVEN and PROVISIONAL, PR open, CI in flight. This session remains
subscribed to PR #39 and will merge on green (per CLAUDE.md's "Do without
asking: merge on green + L1 doc (PROVISIONAL)") without further owner
input, then continue awaiting Control plane L2 certification — not a
second self-certification, per Law 4. Next candidate once FTF-0006
certifies: FTF-0101+ (identity/tenancy/RBAC/audit, CAP-01) per QUEUE.md's
own dependency order.

## 2026-09-16 — FTF-0004 independently certified (Control plane, CERTIFY mode)
Scheduled control-plane certifier shift. Read docs/certifications/queue.md
— one pending item: FTF-0004 (PR #35, merged as
`a477e678b785d3248bdc985c91586b148411b4fe`), confirmed the sole
`PROVISIONAL` entry in `docs/spec-matrix.yaml` (FTF-0001/0002/0003/0005
all already `CERTIFIED`). Session-ID comparison first, per
docs/CONTROL_PLANE_MODES.md's explicit requirement: all seven commits on
PR #35 carry `Claude-Session: .../session_01NV9ZvW9ptNnx8RzFWUoZQi`; this
session is `session_0164zgaFH5z6hqEmpCyD1mLE` — no overlap, independent
per Law 4.
Verified for real rather than trusting the PR/queue description:
- Read the full delivered diff on the checked-out merge commit
  (`infra/docker/docker-compose.yml`, `apps/api/app/config/
  docker_local_stack.py`, `scripts/verify_docker_local_stack.py`,
  `tests/tasks/test_ftf_0004.py`, the `docker-local-stack-integration` CI
  job) — digest-pinned images, SeaweedFS confirmed as the master spec's
  own qualified S3-compatible fixture (§77.3/S32), stdlib-only health
  check, no `continue-on-error`, no canned-healthy fallback path.
- Ran `tests/tasks/test_ftf_0004.py` myself: 9/9 pass.
- Performed my own fault injection: replaced `check_tcp_reachable` with a
  hardcoded "canned healthy" `ReadinessResult` in the tracked module —
  4/9 checks failed for real with the expected messages; restored via
  `git checkout --`, `git diff` confirmed empty, re-ran clean (9/9).
- Fetched GitHub Actions run 35096166703 directly via the API: `gate` and
  `docker-local-stack-integration` both conclusion `success` on the
  actual PR head SHA (`63b13ce`, confirmed via `pull_request_read
  get_commits`, not a stale claim). Read the integration job's full log
  directly (`get_job_logs`) rather than trusting the green check: real
  stack boot, marker row written, `docker compose stop postgres`,
  readiness genuinely flipping to `{'ready': False, ...}` (real DNS
  failure once the container stopped), restart, recovery, and the marker
  row confirmed to survive — the exact negative_acceptance evidence
  FTF-0004 requires.
- `product_truth_check.py --fail-on-unwired`: `0/0 product-true`,
  legitimate since FTF-0004's `proving_symbol` is empty (no live caller
  yet, same rationale as FTF-0002/0003/0005). `CLAUDE.md.sha256` matches
  `sha256sum CLAUDE.md` — no drift.
- Pulled the real contract via `tools/r1_handoff.py packet FTF-0004
  --plan FINE_TUNING_FACTORY_RELEASE_1.md` and checked delivered work
  against its actual acceptance/negative_acceptance clauses: acceptance
  clause 1 (boot command + health probes) is met for
  API/Postgres/Temporal/S3; `web` is independently confirmed to have no
  server-side runtime on disk at all (only a trivial `src/index.ts`
  constant) — a disclosed, reasoned scope limitation (inventing a
  placeholder web server would violate Law 11; adding a real one now
  would be an out-of-scope new-runtime-dependency ask-first item), not a
  hidden gap, and is tracked as a follow-up. Acceptance clause 3
  (qualified SeaweedFS, not MinIO) and the negative_acceptance clause are
  both met per the fault injection and CI log evidence above.
Certified: `docs/spec-matrix.yaml` (FTF-0004: `certification_state=
CERTIFIED`, `proof_state=L2-CERTIFIED`, `evidence_hash=a477e678...` the
merge commit), `docs/certifications/ledger.md` (full entry with
session-ID comparison stated explicitly), `QUEUE.md` (FTF-0004 moved to
Done, Certified 4→5/300, Provisional cleared to 0). Not promoted — L4
gate is release-only and hasn't run. Certified: 5/300 · Product-true: 0
(unchanged — FTF-0004 has no proving_symbol, doesn't count either way).
Did not act on PR #37 (an unrelated, separately-opened docs-only
PROGRESS.md trim from a different relay shift) — out of scope for this
CERTIFY-mode pass; docs/certifications/queue.md had no pending item tied
to it.

## 2026-09-16 — FTF-0004 (PR #35) merged to main as PROVISIONAL
Autonomous relay shift (scheduled firing, no live user present). STEP 0:
`sha256sum CLAUDE.md` matched committed `CLAUDE.md.sha256` — no drift.
STEP 0.1: read PROGRESS.md/QUEUE.md/docs/certifications/queue.md, then
cross-checked against live repo state — local branch tip matched
`origin/main` (`fe954e0`) exactly.
STEP 1: QUEUE.md Active named PR #35 (FTF-0004) as the item to pick up —
but by the time this shift checked GitHub directly, that work was already
done: a prior producer session (`session_01NV9ZvW9ptNnx8RzFWUoZQi`) had
already merged current `main` into the branch, resolved the tracking-doc
conflicts, and fixed a real `docker-local-stack-integration` CI failure
(a `urllib.error.HTTPError`-swallows-503-body bug in
`verify_docker_local_stack.py`'s `poll_health`, unrelated to
`docker_local_stack.py`'s readiness logic) — all documented in
`docs/certifications/queue.md` and the PR body/branch tracking docs
before this shift ever looked at it.
Independently verified rather than trusting the PR body's claims: pulled
PR #35 via GitHub MCP — `mergeable_state: "clean"` (not the prior
`"dirty"`), `get_check_runs` on the actual head SHA
(`63b13cec9a58822290e678231069427370678855`) showed both `gate` and
`docker-local-stack-integration` `completed`/`success`, timestamps
consistent with that exact head. Read the diff's substantive new files
directly from GitHub (not just the PR description): `infra/docker/
docker-compose.yml` (all three real images digest-pinned; the local
Postgres password is clearly dev-only, not a real secret),
`apps/api/app/config/docker_local_stack.py` (stdlib-only TCP readiness
checks, no new runtime dependency), and the updated `.github/workflows/
ci.yml` — nothing scope-creeping past FTF-0004, no secrets, no unpinned
images.
This matches CLAUDE.md's "Do without asking" authorization ("merge on
green + L1 doc (PROVISIONAL)"): merged PR #35 (merge commit `a477e678`),
same discipline as prior relay shifts merging FTF-0002/0003/0005 once CI
was independently confirmed green on the real head. Not a certification —
still PROVISIONAL, Law 4 untouched; Control plane still needs to do the
L2 review docs/certifications/queue.md's most recent entry explicitly
asks for.
`main`'s spec-matrix.yaml/QUEUE.md/docs/certifications/queue.md already
carried the correct PROVISIONAL bookkeeping as part of PR #35 itself
(written by the producer session before this shift merged it) — verified
post-merge that all three landed as expected on `main`, no further doc
edits needed there.
STEP 4(a): FTF-0004 is now the queue's only non-certified item and is
fully L1-PROVEN/PROVISIONAL — per QUEUE.md's own stated discipline ("not
picking up a second item on top of an uncertified one, same discipline as
the FTF-0002→FTF-0003 handoff"), did not start FTF-0006 or any other
queue item this shift. Confirmed no other open PRs exist
(`list_pull_requests` state=open → empty) and no owner-ask items changed.
Stopping: awaiting Control plane L2 certification of FTF-0004, then
(eventually) the L4 gate. Certified: 4/300 (unchanged this shift — a
merge to PROVISIONAL is not a certification). Product-true: 0
(unchanged).

## 2026-09-16 — FTF-0004 (PR #35): CI green for real, awaiting L2 review
After the merge-base fix (below), CI ran on PR #35 for the first time.
`docker-local-stack-integration` **failed for real** on that first run
(35095359741): booted the real stack fine, but after `docker compose stop
postgres`, `poll_health(expect_ready=False)` timed out after 120s even
though the health endpoint was genuinely returning 503 with
`{"ready": false, ...}` throughout — confirmed by reading the actual job
log (`get_job_logs`), not inferred from the red X. Root cause:
`urllib.request.urlopen()` raises `HTTPError` for any non-2xx status
before the response body is read, so `poll_health`'s exception handler
was discarding the 503's real JSON body as an opaque connection failure
and looping until the deadline — a genuine bug in the verify script, not
a flake, not the containers' fault, and not the redaction/config code
FTF-0004 actually delivers.
Fixed (`c595897`): read the body from `HTTPError` the same way as a
normal response, then check the real `ready` field regardless of HTTP
status. Verified the exact failure mode locally first, without Docker —
pointed the stdlib health server at closed sockets (simulating a stopped
dependency) and confirmed the old code would spin for the full timeout
while the new code returns `ready=False` correctly in under a second;
also re-verified the healthy (200) path is unaffected. Re-ran the full
local gate (ruff/mypy, `tests/tasks/test_ftf_0004.py`, `docker compose
... config`) clean before pushing.
Pushed; CI re-ran on the real merge commit + fix
(35095834970): **both `gate` and `docker-local-stack-integration`
completed with conclusion `success`** — confirmed via `get_check_runs`,
not assumed from a lack of a wake event. This is the actual container
boot, restart, and persistence evidence FTF-0004's negative_acceptance
clause requires, produced on GitHub Actions' hosted runner exactly as
planned since this sandbox has no registry egress. Updated the PR body's
test-plan checklist to reflect the real green run and to record this
whole history (original NOT-CERTIFIED-for-structural-reasons finding →
merge fix → real CI failure → real fix → green) for the next
reviewer. `mergeable_state: clean`.
Still PROVISIONAL — not self-certified (Law 4). Awaiting Control plane
L2 re-review, which should now find a clean merge and a real green
`docker-local-stack-integration` run to check.

## 2026-09-16 — FTF-0004 (PR #35): merge-base fixed, resubmitted
Control plane reviewed PR #35 and did NOT certify it — not on the
delivered code, but on structural grounds (`docs/certifications/
queue.md`, "Control plane review — 2026-09-16 — NOT CERTIFIED (not in a
certifiable state)"): `mergeable_state: dirty`, because the branch's
prior tracking-doc "rebase" (commit `330be28`) only copied file *content*
from `main` at the time — it never ran a real `git merge`/`git rebase`,
so the branch's actual merge-base with `main` stayed at `9de083b`,
predating PR #30/#32 (the FTF-0005 fix + certification that had already
landed). Real conflict markers resulted in `PROGRESS.md`/`QUEUE.md`
against the true current `main`. Separately, 0 CI check runs had been
recorded on the PR at review time.
Fix: `git fetch origin main` (now `fe954e0`, which itself carries commit
`2dfdecb` — Control plane's own NOT-CERTIFIED finding, written directly
to `main`), then a real `git merge origin/main --no-edit`. This produced
the expected conflicts in exactly the four docs both branches had
touched (`PROGRESS.md`, `QUEUE.md`, `docs/certifications/queue.md`,
`docs/spec-matrix.yaml`) and none in code — confirming the reviewer's
diagnosis that this was a tracking-doc-only conflict, not a substantive
one. Resolved each by keeping both histories (this branch's FTF-0004
entries plus `main`'s real FTF-0005-certification and NOT-CERTIFIED-
review entries), verified the merge commit's parents include both the
old branch tip and the real `main` tip, then re-ran the full gate
(`uv run ruff`/`mypy`, `uv run pytest tests/unit`,
`tests/tasks/test_ftf_0002.py`/`0003`/`0004`/`0005`,
`tests/test_ftf_0001_monorepo_layout.py`, `pnpm run lint`/`typecheck`/
`format:check`, `pnpm exec vitest run`, `product_truth_check.py
--fail-on-unwired`) before pushing, to confirm the merge itself
introduced no regression. Pushed the merge commit to the same branch/PR
#35 — not opening a duplicate, per the reviewer's own instruction.
`docs/spec-matrix.yaml`'s FTF-0004 entry now correctly reflects
`certification_state: PROVISIONAL` on top of `main`'s real FTF-0005
CERTIFIED state (4/300), not a stale copy. Still not self-certified —
awaiting Control plane to actually see CI run to completion on the new
merge commit and re-review.

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
