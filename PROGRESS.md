# PROGRESS — Sovra
Updated: 2026-09-16T19:10:00Z

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
