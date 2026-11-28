# Certification Queue
Relay appends PROVISIONAL items here for the Control plane to certify.

Entries for certified items older than 14 days are in `docs/certifications/archive/certification-queue-history-2026-11-27.md` (verbatim, read-only).

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

## Next shift
This entry is the sole PROVISIONAL item added this shift — a certifier
shift should pick it up next. FTF-0207 reaching CERTIFIED would newly
unblock nothing directly by itself among currently dependency-ready
tasks (FTF-0208/FTF-0210/FTF-0214/FTF-0215 each still need at least one
other, separately-uncertified dependency first), but it clears the
highest-leverage single blocker in the current frontier (159 uncertified
transitive dependents). `FTF-0206` (PR #207) and the FTF-0205 gate-step
fix (PR #211) both remain open and unmerged from before this shift,
unrelated to this task's own branch — see this file's own prior entries.

## Next shift
This entry is the sole new PROVISIONAL item added this shift — a
certifier shift should pick it up next, alongside PR #213 (the real,
still-unmerged L2 certification of FTF-0207, disclosed above — left for
Control plane, not this relay's role). `FTF-0506` reaching CERTIFIED does
not by itself newly unblock any currently dependency-ready task, but it
clears the second-highest-leverage single blocker in the current
frontier (143 uncertified transitive dependents) after `FTF-0304`'s
family. `FTF-0302`/`FTF-0303` (Hugging Face / private S3 artifact
resolvers) are dependency-ready and unclaimed but need real external/
keyed-credential integration evidence this sandbox cannot reach directly
— better suited to a shift with CI Actions wiring (and, for FTF-0303, an
owner-approved new keyed AWS credential). `FTF-0206` (PR #207) and
`FTF-0304` (PRs #194/#195) remain open/unmerged from before this shift,
on branches this session does not own — left untouched.
## Next shift
`docs/spec-matrix.yaml` now has one PROVISIONAL entry (`FTF-0208`)
awaiting independent Control-plane L2 review -- a certifier shift should
pick it up next. Recomputing the dependency-ready frontier fresh (do not
trust this note alone) at this shift's end showed 16 other genuinely
ready, uncertified, unclaimed tasks besides FTF-0208 itself (e.g.
FTF-0210, FTF-0302, FTF-0303, FTF-04A02, FTF-04A07, FTF-0500A, FTF-0500D,
FTF-0507, FTF-0902, FTF-1201, FTF-1503, FTF-1506, FTF-1703, FTF-R002) --
a future relay shift should re-derive this list itself from
`docs/spec-matrix.yaml`'s own `notes`/`deps` rather than trusting this
enumeration, since a task certified after this shift ends could unlock
further items. `FTF-0304` still has two open, unmerged candidate PRs
(#194/#195) from other sessions -- leave those for their own producer
sessions or a certifier, do not redo the work.

## Next shift
No Owner actions, no Active items, no Provisional items as of this
shift's end. A relay shift should recompute the dependency-ready
frontier fresh from `docs/spec-matrix.yaml`'s own `notes`/`deps` rather
than trusting any prior enumeration, since this certification could
unlock further items. `FTF-0304` still has two open, unmerged candidate
PRs (#194/#195) from other sessions -- leave those for their own
producer sessions or a certifier, do not redo the work.

## Next shift
`docs/spec-matrix.yaml` now has one PROVISIONAL entry (`FTF-0209`)
awaiting independent Control-plane L2 review -- a certifier shift should
pick it up next. Recomputing the dependency-ready frontier fresh at this
shift's end (do not trust this note alone) showed 16 other genuinely
ready, uncertified, unclaimed tasks besides FTF-0209 itself (e.g.
FTF-0210, FTF-0302, FTF-0303, FTF-04A02, FTF-04A07, FTF-0500A, FTF-0500D,
FTF-0507, FTF-0902, FTF-1201, FTF-1503, FTF-1506, FTF-1703, FTF-R002) --
re-derive this list from `docs/spec-matrix.yaml` itself rather than
trusting this enumeration, since a certification landing after this
shift ends could unlock further items. `FTF-0304` still has two open,
unmerged candidate PRs (#194/#195) from other sessions -- leave those
for their own producer sessions or a certifier, do not redo the work.

## Next shift
`docs/spec-matrix.yaml` now has one PROVISIONAL entry (`FTF-0210`)
awaiting independent Control-plane L2 review -- a certifier shift should
pick it up next. Recomputing the dependency-ready frontier fresh at this
shift's end (do not trust this note alone) showed further genuinely
ready, uncertified, unclaimed tasks besides FTF-0210 itself (e.g.
FTF-0215 is now also dependency-ready once FTF-0210 itself is CERTIFIED,
not before) -- re-derive this list from `docs/spec-matrix.yaml` itself
rather than trusting this enumeration, since a certification landing
after this shift ends could unlock further items. `FTF-0304` still has
two open, unmerged candidate PRs (#194/#195) from other sessions -- leave
those for their own producer sessions or a certifier, do not redo the
work.

## Next shift
`docs/spec-matrix.yaml` has no PROVISIONAL entries at this shift's end --
next relay shift should recompute the dependency-ready frontier fresh
from `docs/spec-matrix.yaml` itself (do not trust any prior shift's
cached enumeration) rather than assuming FTF-0215 or any other item is
next without re-deriving it, since a certification landing after this
shift ends could unlock further items. `FTF-0304` still has two open,
unmerged candidate PRs (#194/#195) from other sessions -- leave those for
their own producer sessions or a certifier, do not redo the work.

## Next shift
FTF-0211 (PR #226) needs a relay shift to fix the real
`test_ftf_h006.py` IPv6-detection/gate-wiring defect above before this
can be re-reviewed -- either make `_detect_ipv6_support()` actually
predict the real GitHub Actions runner's own bind failure (e.g. probe
with the exact same `ThreadingHTTPServer`/`HTTPServer.server_bind`
construction the real server uses, not a bare `socket.bind`, since the
two calls provably disagree on the real runner), or wrap the real
`ThreadingHTTPServer(("::1", 0), _Handler)` construction itself in the
same `try/except OSError` fallback so a runner where the probe
under-predicts the failure still degrades to the disclosed skip path
instead of crashing the whole `gate` job. Push the fix to PR #226's own
branch (`claude/wizardly-fermat-pvnd0o`), watch a fresh CI run to
completion on the real new head before resubmitting for certification.
FTF-0211's own module logic (`real_streaming_probe.py`) and its own test
suite (`test_ftf_0211.py`) are otherwise sound -- confirmed independently
in this review -- so once the CI fix lands and genuinely goes green,
re-review should be a fast pass. `docs/spec-matrix.yaml` has no other
PROVISIONAL entries at this shift's end.

## Next shift
PR #228 is genuinely green (27/27, confirmed on its real final head) and
mergeable -- ready for an independent Control-plane L2 review (this
session cannot certify its own work -- Law 4). FTF-0211's own module
logic and test suite were already independently confirmed sound by the
2026-09-23 review above, so re-review should be a fast pass focused on:
re-confirming CI is still green on PR #228's actual current head at
review time (don't trust this note alone -- a later push could change
it), whether PR #226 should be closed in favor of #228 (left as a
Control-plane judgment call, disclosed above), and the branch-continuity
resolution itself. `docs/spec-matrix.yaml` has no other PROVISIONAL
entries at this shift's end. `FTF-0304` still has two open, unmerged
candidate PRs (#194/#195) from other sessions -- leave those for their
own producer sessions or a certifier, do not redo the work.

## Next shift
PR #232 is genuinely green (27/27, confirmed on its real final head) and
mergeable -- ready for an independent Control-plane L2 review (this
session cannot certify its own work -- Law 4). `docs/spec-matrix.yaml`
has no other PROVISIONAL entries at this shift's end. FTF-0212 (PR #231)
and FTF-0304 (PRs #194/#195) are other sessions' own open work -- leave
those alone.

Superseded by the entry below: PR #232 was independently reviewed and
**CERTIFIED 2026-09-24** (see `QUEUE.md`'s "## Done" list, merge commit
`1751211044041e15fc4708b4be552c3957509e80`) by an intervening
Control-plane shift before this note was acted on.

## Next shift
This PR needs a real CI run watched to completion on its actual pushed
head, then an independent Control-plane L2 review (this session cannot
certify its own work -- Law 4). Recompute the dependency-ready frontier
fresh from `docs/spec-matrix.yaml`/`spec/tasks.json` rather than trusting
this note alone: FTF-0902 ("Network-policy templates") is genuinely
dependency-ready but needs a real CNI-enforcing Kubernetes cluster this
sandbox does not have (disclosed above, not claimed); FTF-0212 (PR #231)
and FTF-0304 (PRs #194/#195) remain other sessions' own open work -- leave
those alone.

## Next shift
This PR needs a real CI run watched to completion on its actual pushed
head, then an independent Control-plane L2 review (this session cannot
certify its own work -- Law 4). A reviewer should specifically re-check
this task's own field-shape reconciliation against FTF-R001's frozen
`architecture.schema.json` line by line, since it was this session's own
mid-implementation correction, not the original submitted shape. Recompute
the dependency-ready frontier fresh from `docs/spec-matrix.yaml`/
`spec/tasks.json` rather than trusting this note alone: FTF-0902
("Network-policy templates") is genuinely dependency-ready but needs a
real CNI-enforcing Kubernetes cluster this sandbox does not have
(disclosed above, not claimed); FTF-0212 (PR #231) and FTF-0304 (PRs
#194/#195) remain other sessions' own open work -- leave those alone.

## Next shift
This PR needs a real CI run watched to completion and an independent
Control-plane L2 review (this session cannot certify its own work -- Law
4). Recompute the dependency-ready frontier fresh from
`docs/spec-matrix.yaml` rather than trusting this note alone: FTF-0212 (PR
#231) and FTF-0304 (PRs #194/#195) remain other sessions' own open work --
leave those alone. FTF-0902 ("Network-policy templates") is genuinely
dependency-ready but needs a real CNI-enforcing Kubernetes cluster this
sandbox does not have (disclosed by a prior shift, not claimed here).

### Control plane review — 2026-09-24 — FTF-04A06 (PR #238) — NOT CERTIFIED (real Law 1 gap: the module's own "never a string-prefix comparison" guarantee is asserted but never shown able to fail)
- **PR:** https://github.com/SJZLab/Sovra/pull/238 (`claude/wizardly-fermat-azo96v`
  → `main`), head at review time `4badf5af9f1d0056248be670c0ffbe84d27034fe`
  (three commits: `38ea4e4` implementation, `37e6b49` evidence-hash record,
  `4badf5a` tracking-doc record).
- **Session-ID check:** all three commits carry `Claude-Session:
  .../session_0159tZvUhDCyk3rdhW7fMscf`; this reviewing session's own ID is
  `session_01MM33SzxPJuU62Z4SY45XKg` -- no match, independent per Law 4.
- **Contract pulled directly**, not assumed: `python3 tools/r1_handoff.py
  packet FTF-04A06 --plan FINE_TUNING_FACTORY_RELEASE_1.md` ->
  `docs/implementation/task-packets/FTF-04A06.md`. `negative_acceptance`
  reads literally: "Path/symlink traversal and concurrent file drift fail;
  never edit the target working tree." -- **"path traversal" is named
  explicitly, distinct from "symlink traversal"**, and Section 7A.3's own
  binding line (packet line 313) reads "local repository paths are
  allowlisted explicitly and **canonicalized to prevent path traversal**."
- **Diff read directly**, no scope creep: `local_repository_connector.py`
  (431 lines) + `test_ftf_04a06.py` (434 lines) + the one-line CI gate step
  + the `docs/spec-matrix.yaml` entry, matching the PR's own description
  exactly.
- **Own re-run, this session's own checkout of the PR's real head**
  (`uv sync --all-packages` first): `uv run python3
  tests/tasks/test_ftf_04a06.py` -- "all checks passed", matching the
  producer's own claim. `uv run ruff check`/`ruff format --check`/`mypy`
  clean on both new files, matching the PR's own claim.
- **Own fault injection, independent of the producer's own two claimed
  vectors, targeting the module's own explicit design claim rather than
  its already-tested symlink/concurrency paths:** the module's own
  docstring (`local_repository_connector.py:174`) states as a design
  guarantee: *"Never a string-prefix comparison: `/approved-evil` is not
  treated as a descendant of `/approved` just because the text matches."*
  Patched `resolve_allowed_root`'s real comparison (line 190,
  `if real_root == real_allowed or real_allowed in real_root.parents:`) to
  a naive string-prefix check
  (`if str(real_root).startswith(str(real_allowed)):`) -- a realistic,
  common real-world path-allowlist bug, and exactly the class of bug the
  module's own docstring calls out by name. **Re-ran the full delivered
  suite (`tests/tasks/test_ftf_04a06.py`) against this broken version:
  `FTF-04A06 local repository connector tests: all checks passed` -- the
  suite did not detect the break at all**, because
  `test_resolve_allowed_root_accepts_inside_and_refuses_outside`'s own
  "refuses outside" case uses sibling directories `allowed`/`outside`
  (name text does not overlap at all), never a prefix-colliding pair like
  the docstring's own named example. Independently confirmed the concrete
  exploit directly (not merely inferred from the suite staying green):
  built `base/allowed` and `base/allowed-evil` as siblings, called
  `resolve_allowed_root(base/"allowed-evil", [base/"allowed"])` against
  the patched module -- **returned the evil root instead of raising
  `PathAllowlistError`** (`BUG NOT CAUGHT: accepted evil root ->
  /tmp/.../allowed-evil`). Reverted the patch byte-identical (`diff`
  confirmed against the original) and re-ran the same exploit against the
  real, unmodified shipped code: **correctly refused**
  (`PathAllowlistError: root .../allowed-evil is not inside any
  allowlisted root (...)`), confirming the shipped implementation itself
  is not vulnerable to this specific bug -- `resolve_allowed_root` already
  uses `real_allowed in real_root.parents`, a real structural check, not a
  string comparison. **This is a Law 1 gap in the delivered evidence, not
  a defect in the shipped code**: the module's own stated
  anti-prefix-collision guarantee, and the packet's own literal "path
  traversal" (not just "symlink traversal") negative_acceptance clause,
  have zero test coverage in `tests/tasks/test_ftf_04a06.py` -- nothing in
  the suite would have caught a regression to this exact bug class if one
  were introduced later. No literal `../`-style traversal string case
  exists in the suite either (every "refuses outside" case uses an
  already-resolved, non-overlapping sibling path).
- **Conclusion -- per CERTIFY step 3 ("confirm the test genuinely fails
  when the feature is broken"):** the delivered test suite does not fail
  when a realistic implementation regression is introduced into exactly
  the property the module's own docstring and this task's own
  negative_acceptance clause both name explicitly. Not merging. PR #238
  left open, unmerged. `certification_state` stays `PROVISIONAL` on `main`
  (Law 2 -- `docs/spec-matrix.yaml`'s FTF-04A06 entry does not yet exist
  on `main` at all, since this PR has not merged; no downgrade needed).
  **Fix needed:** add a dedicated case to
  `test_resolve_allowed_root_accepts_inside_and_refuses_outside` (or a new
  test function) using a sibling, prefix-colliding pair -- e.g.
  `allowed = base / "allowed"`, `evil = base / "allowed-evil"`, both real
  directories -- asserting `resolve_allowed_root(evil, [allowed])` raises
  `PathAllowlistError`; this exact case should be shown able to fail first
  against a naive `str(...).startswith(...)` implementation (matching this
  review's own fault injection) before being confirmed passing against the
  real one. No other part of this PR needs rework -- the symlink-escape
  and concurrent-drift guards are both genuinely proven (own independent
  re-run of the producer's two fault-injection vectors, same results).
  Re-submit for certification once the missing case is added.
- **CI status at review time:** run `35990930658` still `in_progress` on
  the real final head (`4badf5a`) -- not a factor in this NOT CERTIFIED
  verdict, which rests entirely on the Law 1 test-coverage gap above and
  would apply regardless of CI's outcome.
- **Validated-by:** session https://claude.ai/code/session_01MM33SzxPJuU62Z4SY45XKg
  (independent -- see session-ID check above), 2026-09-24.

## Next shift
PR #238's fix (commit `911ae78`) was independently re-verified this same
shift: CI re-ran green on the fixed head (`3fb3367`) after one re-run of a
genuine, unrelated flake in FTF-R002's own already-certified
`test_ftf_r002.py` (confirmed not caused by this PR's diff -- see this
PR's own comment thread). PR #238 now needs a fresh independent
Control-plane L2 review (Law 4 -- this session cannot certify its own
work). PR #239 (the certifier's own documentation-only NOT CERTIFIED
record, now merged into `main`) is a separate PR this relay did not merge
or edit.

## Next shift
`docs/spec-matrix.yaml` now has one PROVISIONAL entry (`FTF-0212`)
awaiting independent Control-plane L2 review -- a certifier shift should
pick it up next. `FTF-0304` still has two open, unmerged candidate PRs
(#194/#195) from other sessions -- leave those for their own producer
sessions or a certifier, do not redo the work.
## 2026-10-06T17:45Z — Control plane: FTF-04A18 CERTIFIED; FTF-1206A status flipped; FTF-0902 (round 4), FTF-1206A-F2 and FTF-H034-F1 returned

Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent verifiers:
top model for FTF-0902, FTF-1206A-F2 and FTF-H034-F1; mid-tier for FTF-04A18. All faults went into production code
and ran with the script runner; the mutation gate ran where the file is in scope.

### FTF-04A18 (#493, merge 6d5ff9501da3d86d335e398c978dc91ae4ec7f0d): CERTIFIED
- Producer session: `013LXTon`. CI on d80fdbe: `gate` 112383468247 ("FTF-04A18 candidate selection ...: all checks
  passed"), `candidate-selection-integration` 112383469113, `discovered-agent-candidate-integration` 112383468872.
- Every blocking clause in the 10-04 entry turns RED, including the server-side latest-snapshot staleness checks:
  delete, additive bypass, supersedes-only, captured_at-only, `>=`, inverted, and the same-connection filter.
- UI clauses also turn RED: no auto-select of a single candidate, the Enter flow, surfaced errors, and a 409 that
  is not treated as success.
- Mutation gate: 22/22 mutants killed.
- Follow-up FTF-04A18-F1: UI tests for a 500 on the list, a `{}` list body, and an ok select body without
  `candidate_id`.

### FTF-1206A: status flipped to CERTIFIED (verdict 2026-10-06T16:35Z; evidence a8a1936049376e0dabd3bba2536b169fb04afcd9)
- Since FTF-H034-F1 (#495) merged, `sync_certified_status` creates the TRACKER entry for the addendum and leaves
  `spec/tasks.json` byte-identical at 300. The verifier ran this check: the H034 test passes, and
  `r1_handoff` integrity holds.

### FTF-0902 (#491): NOT CERTIFIED, round 4 (1 blocking)
- Producer session: `011d3fmH`. CI: gate 112374705053, network-policy-enforcement-integration 112374705651
  (Calico metadata blocked).
- All 5 round-3 items hold:
  - 61 of 67 faults turn RED, including every fault that stayed GREEN in round 3;
  - the remaining GREENs are masked or equivalent;
  - probes P1–P9 are refused.
- Mutation gate on the module, run through a scratch SOURCE_ROOTS wrapper: 112/118 killed, and the 6 survivors are
  equivalent.
- **Blocking:** the DNS rule emits module-level mutable dicts by reference. The Control plane reproduced on
  unmodified code that clearing them in one returned manifest makes the next render emit
  `{"namespaceSelector":{"matchLabels":{}},"podSelector":{"matchLabels":{}}}` on port 53. The fix is in
  QUEUE 0b.
- Follow-up FTF-0902-F1 adds:
  - a lying-`str` protocol test;
  - a template_id length-cap boundary test;
  - packages/security in mutation-gate scope.

### FTF-1206A-F2 (#498): NOT CERTIFIED, round 2 (tests only)
- The "no tenant context" case reuses a pooled connection that already ran `set_config`, so it reads '' and
  duplicates the '' case.
- A WITH CHECK `current_setting(..., true) IS NULL OR …` fail-open stays GREEN for both tables. A fresh no-context
  `sovra_app` connection inserted a foreign-org policy row under that fault.
- With a fresh connection the fault turns RED. The fix is in QUEUE 0c.

### FTF-H034-F1 (#495): NOT CERTIFIED, round 2 (tests only)
- The production behavior is correct; the decisive check passed.
- But widening the exclusion by regex (`FTF-\d+A`, or `FTF-\d+[A-Z]\w*`) stays GREEN. The only non-addendum test
  id is `FTF-NOTREAL`, and 40+ real ids are addendum-shaped (04A*, 0500A–D). The fix is in QUEUE 0b2.

### Hygiene
- PR #493 committed `.mypy_cache`, and 2,308 cache files are tracked on main. This PR untracks them and adds the
  directory to `.gitignore`.

## 2026-10-06T20:10Z — Control plane: FTF-0500D, FTF-1002, FTF-1206A-F2, FTF-H034-F1 CERTIFIED; FTF-0103A (round 2) and FTF-1807 (round 4) returned

Validated-by: control-plane session https://claude.ai/code/session_01QVznorVMjnmcttUhyDnoGR. Independent verifiers:
top model for FTF-0103A, FTF-1206A-F2 and FTF-H034-F1; mid-tier for FTF-0500D, FTF-1002 and FTF-1807. Producer
sessions: `01Gy4xXk` (0103A), `01BGMHsj` (0500D, 1206A-F2, H034-F1, 1807), `012gb47Z` (1002). None is this session.

### FTF-0500D (#505, merge 2fb298eb57ee39910b654ac0620ce2af177dabd7): CERTIFIED
- CI: gate 112444119528 passed.
- The oracle stays independent.
- About 60 faults turn RED, including:
  - P1, P3 and P4 (display, classification and purpose hardcoded), caught by a 180-cell display-field test;
  - H7d and H7e (empty or first-only allowed_purposes), caught by a 60-cell shape test;
  - every round-3 core fault.
- Whole-file mutation gate: 72/92 killed. The survivors are redundant validators (equivalent), plus three small
  test gaps recorded as FTF-0500D-F1.
- Follow-up FTF-0500D-F1:
  - a test that an unknown connection status raises;
  - a zero bytes/records boundary;
  - refusing a non-sequence transformations value;
  - transformations order;
  - display fields in the shape test.

### FTF-1002 (#507, merge 84241a42d68c4a7f07a689deae23c9109d822143): CERTIFIED
- CI: gate 112451816141 passed.
- All round-4 DoD faults turn RED, behaviorally and in full CI mode:
  - (1) per-artifact licence: hardcoded value, dropped `accepted_by`/`accepted_at`, adapter `base.value`;
  - (2) `_canonical_fact` dropping detail, value or known; stripping the resolved base; the two-adapter
    digest-difference test;
  - (3) a16, plus None, blank, "abc" and whitespace base digests.
- Whole-file mutation gate: 114/129 killed. Eight survivors are equivalent M9 normalisations.
- Follow-up FTF-1002-F1:
  - isolated value/known/detail tamper for every fact, not only `base`;
  - distinct weights_digest per fixture;
  - order-sensitive licence detail assertions;
  - verify a fresh endpoint manifest;
  - unknown-fact detail text;
  - the earlier F1 items.

### FTF-1206A-F2 round 2 (#504, merge fb2f65c800043d3c73d2f8216ed9d937f12fa0e8): CERTIFIED
- CI: `organization-gateway-policy-integration` 112438988030 passed. Its Postgres log shows the no-context INSERTs
  refused on fresh backends.
- The no-context case runs on a NullPool connection, and its `IS NULL` precondition is proven not vacuous (test-side
  faults P1 and P2 turn RED).
- F4f and F4g turn RED. F4c–F4e, COALESCE, USING(true) and RLS-disabled show no regression.
- Follow-up: removing FORCE is caught only by data_exposure_check (expected, because sovra_app is not the owner).

### FTF-H034-F1 round 2 (#502, merge 9f6ce955c012a9b8850f63ea4ee6530114bebe70): CERTIFIED
- The addendum-shaped ids with no addenda file (FTF-9999A, 04A99, 0500E, 0103B) are reported missing.
- W2 and W3 turn RED. W1, W4, D1, D2, S1, S2, G1 and U1/U2 show no regression.
- New follow-up **FTF-H034-F2 (QUEUE 0b, tests only):** the whole-file mutation gate on `scripts/task_claim.py`
  leaves 7 survivors in the `sync_certified_status` update branch: re-certifying with a new hash keeps stale
  evidence, the sync is not idempotent, and an empty hash refresh is untested. The production code is correct. Now
  that scripts/ is in mutation scope, any future change to the function fails CI until these die.

### FTF-0103A (#499, merge 3a5d94a): NOT CERTIFIED, round 2 (1 blocking, tests only)
- CI on ca33612: `live-auth-path-integration` 112442806419 ("all checks passed"), gate 112442805670, and
  mutation-gate 112442805576 (51/51 killed).
- On unmodified code, all six contract items hold:
  - real OIDC login gives 200;
  - no, tampered, expired or logged-out cookies give 401;
  - a foreign org, the wrong role, or a revoked membership gives 403;
  - 83 /api/v1 routes each use exactly one real resolver and one real role lookup;
  - the memberships table has the §6.4 constraints, ENABLE+FORCE RLS and a SELECT-only app role;
  - no secrets are logged.
- No bypass probe succeeded: org case and format variants, forged keys, replay after logout, and header/body/query
  injection.
- **Blocking:** `require_role` honoring `X-Organization-Id` (C13b) or `?organization_id=` (C13f) stays GREEN in
  every suite. Under C13b, a viewer of org A gets 200 on org B with the header set to A. This clause is in the
  contract's negative_acceptance. The fix is in QUEUE 0-TOP.
- Follow-up FTF-0103A-F1 (recommended before any multi-replica deploy):
  - the session revocation store is in memory per process, so a logged-out cookie works again after a restart or
    on another replica (inherited from FTF-0101);
  - check positively that each route uses the real resolver;
  - sanitize the logged `x-request-id`;
  - a unit check that the role-lookup SQL keeps its org filter (today only RLS masks that fault).

### FTF-1807 (#506): NOT CERTIFIED, round 4 (tests only)
- 15 plain-language inversions stay GREEN. Examples: "Revoked keys keep working for up to 5 minutes", "Falling back
  to OpenAI is acceptable".
- Section bodies 1–6 are mostly unpinned. Emptying section 3 or 5, or changing "is dropped entirely" to "is
  applied", stays GREEN.
- Several checker-code faults are not killed.
- The fix is to pin the text exactly instead of extending the blacklist. See QUEUE 0c.
- Same-class follow-up: audit the other tests/documentation runbook tests for the same blacklist weakness.

**Mutation gate, first real use:** FTF-0103A's production change ran 51 mutants and killed all 51. Tests-only rounds
correctly show `targets=[]`; for those, verifiers run the gate in whole-file mode.

## 2026-10-06T21:00Z — Control plane: FTF-0103A and FTF-H034-F2 CERTIFIED; FTF-0510 returned (tests only)
- **FTF-0103A: CERTIFIED** (round 2, PR #509, merge 5e598e0). Every header and query org override is RED in the unit
  and live suites. Ten prior faults are RED. See the ledger.
- **FTF-H034-F2: CERTIFIED** (PR #510, merge 090de1c). Follow-up FTF-H034-F3: pin `scripts/task_claim.py:365` for an
  empty hash, using a tasks.json entry with non-CERTIFIED status and `evidence_hash=""`, and assert evidence is
  unchanged and never contains `""`.
- **FTF-0510: NOT CERTIFIED (round 5, tests only).** Production code is correct: the recorded oracle was
  re-derived from transformers 4.46.3 `apply_chat_template` and matches exactly, no dependency was added, and there
  are no stubs. Faults that stay GREEN, each needing a test:
  1. Distribution through the report: swapping p90 and p99, p50 off by one, or p99 off by one all stay GREEN. Fix:
     an `analyze_tokenization` test with at least 10 conversations, asserting count, min, max, mean, p50, p90 and p99
     exactly.
  2. Truncation policy keyed by `record_index` instead of `batch_position` stays GREEN. Fix: a multi-row batch where
     the indexes differ and the policy exists for one position only.
  3. Silently dropped rows. Fix: tests for a non-Mapping candidate (`invalid_candidate`), `content=None`, extra
     content keys, a string inside `messages`, and a single-message conversation. Each must produce its issue and
     certify=False, never a crash or a silent skip.
  4. `distribution.count` replaced by `len(records)` stays GREEN. Fix: a mixed batch of one good row and one issue
     row, asserting count == 1.
  5. Template inputs. Fix:
     - a multi-line template with block newlines (trim_blocks / lstrip_blocks), pinned against a recorded
       transformers value;
     - a template that references `tool_call_id`, `name` and `add_generation_prompt`;
     - `bos_token` and `eos_token` given as AddedToken dicts (`{"content": "<s>"}`);
     - a row that raises `jinja2.UndefinedError` at render.
  - Non-blocking (F1): certification should require verified non-None digests; assert the ApproximateAliasTokenizer
    counts; reject a bool `max_sequence_length`.
  - Show each fault above RED before submitting (STEP 5b).

## 2026-10-06T23:00Z — Control plane: FTF-H034-F3 CERTIFIED; FTF-0214 returned (round 2, 3 blocking)
- **FTF-H034-F3: CERTIFIED** (PR #517, merge 4e46906). New follow-up FTF-H034-F4 (Backlog, tests only): kill or
  justify the 12 claim-registry survivors in scripts/task_claim.py lines 80–270.
- **FTF-0214: NOT CERTIFIED (PR #516, head b396e93; the producer session is independent).**
  - What holds:
    - Every unit is live through `create_app()`: enrich_visible_models, enrich_visible_model_groups,
      reconcile_aliases, LastKnownGoodCatalog, CatalogAnomalyDetector, probe_chat, probe_stream_chat,
      probe_tool_call, probe_structured_output, require_model_access and build_access_snapshot.
    - product_truth_check on the merged tree is 45/45. Most unit faults turn RED in the task or integration suite.
    - Both relay fixes are confirmed: reverting the metadata_status validation turns RED, and the removed except was
      dead code.
  - Blocking:
    1. "No hidden stale access" is violated. After a connection is revoked, the catalog returns
       `connection_status: revoked` but `access_state: healthy`, with entries `callable`. The UI never renders
       connection_status, so the user sees "healthy" and an enabled probe button. Faults that skip the
       revoked-connection check, or return an empty revoked snapshot, stay GREEN everywhere because the revoked
       fixture was never discovered.
    2. "Shows capability/probe evidence": the UI does not render `fields`, `model_groups` or the probe's route
       identity, timestamp or type. These faults stay GREEN in vitest: access_state hardcoded to healthy, alert
       hidden, anomaly hidden, and the probe kind ignored.
    3. A model ID containing "/" gets a 404 on probe, because the route lacks `{model_id:path}` and the UI does not
       encode the ID.
  - Mutation gate: 88 run, 80 killed, 8 survived. All 8 are EQUIVALENT M9 mutants: `(str, Enum)` equality and values
    pre-validated at lines 306, 309-312 and 433. Control plane adds line-pinned allowlist entries once the round 2
    line numbers are final.
  - UI mount (Law 6): apps/web has no pages, so the component is unmounted. The contract does not require a page, so
    this does not block certification; it stays disclosed until FTF-H029.
  - Non-blocking (F1):
    - The integration "viewer cannot probe" check passes for the wrong reason (no discovery yet). Assert the role-gate
      detail instead.
    - Add a route-level test that admin metadata (api_key/api_base) never reaches the response.

## 2026-10-07T02:00Z — Control plane: FTF-0510-F1 and FTF-H034-F5 CERTIFIED; FTF-0606 returned (round 2)
- **FTF-0510-F1: CERTIFIED** (PR #521, merge f8f4c9c). The allowlist line is bumped from 220 to 221.
- **FTF-H034-F5: CERTIFIED** (PR #525, merge d5cfb39).
- **FTF-0606 (tool-name grader): NOT CERTIFIED** (PR #524, merge f85c67f; the producer is independent). Mutation is
  35/35, but hand faults expose gaps the gate's operators do not generate:
  1. **Fail-open on malformed calls.** `EmittedToolCall("1", 123)`, a list as the name, `tool_call_id=None`, and
     duplicate empty ids are all accepted (passed=True when must_call_one_of is empty). Fix: require a non-empty str
     for both name and tool_call_id, else raise ToolNameGraderError. Add tests for name=123, name=list,
     tool_call_id=None and tool_call_id="".
  2. **Exactness is not pinned.** Case-folding or stripping the called names, or the forbidden or required match, all
     stay GREEN. Fix: four tests showing that "Refund_Order" and "refund_order " neither trigger a forbidden call nor
     satisfy a required one.
  3. **Two more gaps.** No test that an empty must_not_call plus a satisfied call passes, and no test that duplicate
     calls are preserved in `called_names`.
  - Not product-true: no live caller exists. An Inspect AI scorer adapter plus a live caller is a separate task.
- **Gate gap (Control-plane follow-up):** mutation_gate M9 does not mutate `in` / membership comparisons on strings,
  which is why gaps 2 and 3 survived the gate. Extend M9 to `x in S` and `x not in S` (normalize the left operand).
- **Dispatcher replaced** (context reached about 400k): v2 is session_011i4PMFAPsFgHSySDQLTx7s. The old triggers are
  disabled, not deleted. See docs/operations/routines.md.

## 2026-10-07T05:50Z — Control plane: FTF-0608-F1 returned (round 2, tests only)
- **FTF-0608-F1: NOT CERTIFIED** (PR #536, merge 2c7707a). All six DoD behaviours are pinned, and mutation is 108/108.
  Two faults the gate does not generate stay GREEN. Tests only, in tests/tasks/test_ftf_0608.py:
  1. **Empty-string bare-str spec.** `refused(lambda: grade(trace("a"), allowed_orders="", must_not_call=["x"]))` and
     the same for `must_precede=""`. These pin the outer `isinstance(..., str | bytes)` guards; "ab" alone does not.
  2. **Mismatch order with a mismatched key before a missing key.**
     `grade(trace("a"), required_state={"k1": 1, "k2": 1}, final_state={"k1": 2}).state_mismatches == ("k1", "k2")`.
  - Optional: make the forbidden_calls expectation non-palindromic (`trace("a","b","c","c")`, `must_not_call=["a","c"]`
    gives ("a","c","c")), and add a vacuous pass for `trace("a")` with `must_precede=[("a","A")]`.

## 2026-10-07T11:00Z — Control plane: FTF-04A34 and FTF-04A21-F1 CERTIFIED; FTF-04A32 returned; Law 16 follow-up
- **FTF-04A34: CERTIFIED** (round 2, #552, merge 1af2d1b). 18/19 round-1 faults are RED; the remaining one (a
  select body) is disclosed as covered by the 04A18 DB integration test.
- **FTF-04A21-F1: CERTIFIED** (#554, merge 07fbb5c).
- **FTF-04A32: NOT CERTIFIED** (#544, merge c3a6cae; producer session_01PLmwxLdJyZhrxDMoF1W91q).
  - Production code is correct; 53 faults were run. Mutation (diff mode) is 66/68; the 2 survivors are
    root-only artefacts of OSError wrappers that are RED when the test runs unprivileged.
  - Non-equivalent GREEN faults inside N1/N2/A3: F09 prefix-sibling symlink; F19 same-length content blind (also
    after archive); F20 rename blind; F18 chmod blind (plus F15/F16 mode asserts). Exact tests: QUEUE item 0b.
- **Law 16 finding:** dirty snapshots include git-ignored files (`.env` was captured). The Control-plane decision
  is to use git's own file set (cached plus others, minus ignored). This is follow-up FTF-04A06-F1 in the Backlog.
- All three merges happened about 40 s after a green ci-verdict. Lanes FTF-0501 and FTF-0602 were parked as
  BLOCKED_DEPENDENCY by the relay (missing schema/contract prerequisites, the same pattern as FTF-0308). Backlog
  refilled with the graders FTF-0605, FTF-0607 and FTF-0609 (pure functions, no schema dependency).

## 2026-10-07T16:00Z — Control plane: five CERTIFIED; FTF-0609 and FTF-04A06-F1 returned
- **CERTIFIED:**
  - FTF-0515 (#569, merge 33a1226)
  - FTF-04A22 (#570, merge e6019bb)
  - FTF-0605 (#572, merge 6aebcf0)
  - FTF-0607 (#573, merge 9201640)
  - FTF-0610 (#575, merge ddddbd3)
- **Returned:** FTF-0609 (round 3, QUEUE 0b) and FTF-04A06-F1 (round 3, QUEUE 0c). Both need tests only.
- **Follow-ups (tests only, in the Backlog):** FTF-0515-F1 and FTF-0605-F1.
- **Merges:** all 7 relay merges (#569–#575) landed on a green ci-verdict, read from each run's jobs because the
  check-runs endpoint returned HTTP 500.
- **Still open:** #576 (FTF-0611 round 2) and #577 (FTF-0308-P1, draft claim).

## 2026-10-07T18:30Z — Control plane: FTF-0609 and FTF-0611 CERTIFIED (127/302); FTF-0308-P1 returned
- **CERTIFIED:** FTF-0609 (#579) and FTF-0611 (#576), plus follow-ups FTF-04A06-F1 (#580), FTF-0515-F1 (#583) and
  FTF-0605-F1 (#584).
- **Returned:** FTF-0308-P1, round 2, tests only (QUEUE 0b).
- **Backlog:** follow-ups FTF-0605-F2 and FTF-04A06-F2 added, and refilled with FTF-0612, FTF-0613, FTF-0517 and
  FTF-1305 (dependency-ready: every dependency is CERTIFIED).
- **ci-verdict:** all six relay merges (#576, #577, #579, #580, #583, #584) landed on a green ci-verdict.

## 2026-10-08T00:30Z — Control plane: FTF-0612 and FTF-0613 CERTIFIED (129/302); FTF-0501-P1 certified
- **CERTIFIED:** FTF-0612 (#594) and FTF-0613 (#595). Slice FTF-0501-P1 (#581) is certified on real Postgres.
- **Returned:** FTF-1305, round 3 (QUEUE 0b), and FTF-0103-F1, round 2 (QUEUE 0c). Both are tests only.
- **Backlog:** FTF-0501-P2 (hardening migration) is added, then FTF-0501 is re-queued on top of P1 and P2.
- **ci-verdict:** all 5 relay merges since 21:55Z landed on a green ci-verdict.

## 2026-10-08T12:20Z — Control plane: FTF-1706 CERTIFIED (134/302); 5 returned; FTF-0516-P1 slice
- **CERTIFIED:** FTF-1706 (#618, 879ea74), candidate discovery. It is UNWIRED.
- **Returned:**
  - FTF-0616 r3 (0b)
  - FTF-0903 r2 (0c)
  - FTF-1602 r2 (0d)
  - FTF-R005 r2 (0e)
  - FTF-1703 r2 (0g)
  - FTF-1206 r3 (0h), re-reviewed after FTF-1206A
  - FTF-0517 r2 is still 0f.
- **Backlog:** FTF-0516-P1, FTF-0908, FTF-0309-P1 and FTF-H010-P1. FTF-0501 (#610) and FTF-0905 (#625) are claimed.
- **CI fix:** a time bomb in the FTF-0501-P1 test (mixed fixed and real clocks) turned every gate red from 12:00Z. It is
  fixed in the test only.
- **ci-verdict:** all 7 relay merges since 06:02Z landed on a green ci-verdict.

## 2026-10-08T16:25Z — Control plane: 5 returned, FTF-0501-UI sliced (134/302 unchanged)
- **Returned:** FTF-0903 r4 (0c), FTF-0905 r3 (0d), FTF-1703 r3 (0g), FTF-1206 r5 (0h), FTF-0501 r2 (0j).
- **Sliced:** FTF-0501-UI (Backlog). **Pending:** FTF-0616 #636.
- **ci-verdict:** all 6 relay merges since 13:40Z (#610, #630, #632, #634, #635, #636) landed on a green ci-verdict.

## 2026-10-08T17:52Z — Control plane: FTF-0309-P1 returned, FTF-R004 sliced (134/302 unchanged)
- **Returned:** FTF-0309-P1 r2 (0k): single gateway call per GET, exact alias matching, proving_symbol.
- **Sliced:** FTF-R004-P1..P4 (P1 and P2 ready now). `inspect_ai` pin owner-approved 2026-10-08.
- **ci-verdict:** both relay merges since 15:40Z (#638, #642) landed on a green ci-verdict. **Pending:** FTF-1602 #638.

## 2026-10-09T00:07Z — Control plane: FTF-0309-P1 returned (135/302 unchanged)
- **Returned:** FTF-0309-P1 r3 (0k): proving_symbol on FTF-0309, shared exact-match helper for both lookups.
- **CI outage:** GitHub Actions refuses jobs for billing since ~23:05Z 2026-10-08; nothing merges until the owner fixes it.

## 2026-10-09T07:48Z — Control plane: FTF-R004-P1 returned (135/302 unchanged)
- **Returned:** FTF-R004-P1 r2 (0n): content-free capture, exact compares, bridge rewrite and stale declaration block, reason codes.
- **CI:** recovered ~07:20Z after the billing outage.

## 2026-10-09T10:26Z — Control plane: FTF-0309-P1 slice CERTIFIED; FTF-0908 returned (135/302)
- **Certified slice:** FTF-0309-P1 (#667). **Returned:** FTF-0908 r3 (0m). **Queued:** FTF-0309 UI.
- Follow-up O1 (non-blocking): refuse a NUL model name with 400 before discovery on the 0309-P1 and 0308-P1 routes.

## 2026-10-09T16:19Z — Control plane: FTF-0516-P1 slice CERTIFIED; 3 returned (136/302)
- **Certified slice:** FTF-0516-P1 (#695). **Returned:** FTF-0309 r3 (0c), FTF-R005 r7 (0e), FTF-R004-P1 r4 (0n).
- **Un-parked:** FTF-0516 UI (Backlog). **Pending:** FTF-0616 #690.

## 2026-10-09T18:18Z — Control plane: slices FTF-H010-P1 and FTF-R004-P2 CERTIFIED; 3 returned (136/302)
- **Certified slices:** FTF-H010-P1 (#699), FTF-R004-P2 (#702). **Returned:** FTF-0309 r4 (0c), FTF-0904 r2 (0d), FTF-1703 r7 (0g).
- **Pending:** FTF-0616 #690. FTF-R004-P3 becomes ready once FTF-R004-P1 certifies.

## 2026-10-09T22:27Z — Control plane: FTF-0501 API passed; FTF-0501-UI returned
- **Passed L2:** FTF-0501 API (#692). **Returned:** FTF-0501-UI r2 (0j). **Ruling:** UI CHECKLIST in QUEUE item 0.

## 2026-10-10T02:27Z — Control plane: FTF-0501-UI returned
- **Returned:** FTF-0501-UI r3 (0j): pin UF3 progress counts; terminal unsafe_archive with a drift-proof 422 vocabulary; pin B34.

## 2026-10-10T03:57Z — Control plane: FTF-H010-P2 ruling
- **Ruled:** FTF-H010-P2 park resolved (route 3: NULL endpoint_reference for gateway-alias imports; FTF-1001 requalified with P2). P2 re-queued.

## 2026-10-10T15:50Z — Control plane: FTF-H010-P3a and FTF-0616-F1 returned
- **Returned:** FTF-H010-P3a r2 (0e): bound the shape product and dimension range; pin negative dims, lora_A rank, PEFT prefix.
- **Returned:** FTF-0616-F1 r2 (0g), tests only; FTF-0616 requalification partial (no regression; uncapped mutation moves to F1 r2).

## 2026-10-13T20:07Z — Control plane: FTF-0502-P1 returned
- **Returned:** FTF-0502-P1 r2 (0j): catalog names; wrap raw HTTP errors; bounded-resource tests; integration via the real upload routes.

## 2026-10-13T23:59Z — Control plane: FTF-0502-P1 certified (slice); FTF-0504 returned
- **Returned:** FTF-0504 r2 (0c): rebuild on certified P1; locator, global field limit, wide-record cap, content-type gate, integration.
- **Certified slice:** FTF-0502-P1 (#770, merge 507ce05a422ad5addb94a1700172fa348f871662); importers may proceed (catalog rule updated).

## 2026-10-14T05:16Z — PROVISIONAL: FTF-0904-W1 (live log redaction wiring)
- **Submitted:** child slice of FTF-0904, built by the Control-plane session at the owner's live request (2026-10-14).
  Needs L2 verification by a different session (Law 4). Test: tests/tasks/test_ftf_0904_w1.py (gate job), faults F1-F6.

## 2026-10-14T08:18Z — Control plane: FTF-0616-F1 CERTIFIED (child slice)
- **Certified:** FTF-0616-F1 (#750, merge 02961dbe0a005967ee5d82e0a1ee10fd2c8bf9a5); FTF-0616 requalified (uncapped mutation 339/339); count unchanged.

## 2026-10-14T09:46Z — FTF-0904-W1 returned (r2 submitted)
- **Returned:** FTF-0904-W1 r1 (excepthook bypass; late install unpinned). **Round 2 submitted** in the same PR: redacting
  exception/warning hooks, W1-BOOT-FAIL and W1-HOOKS checks, faults F7-F12. Needs L2 from a separate session.

## 2026-10-14T11:30Z — FTF-0904-W1 returned (r3 submitted)
- **Returned:** FTF-0904-W1 r2 (multiprocess startup traceback). **Round 3 submitted**: redacting traceback printers,
  W1-BOOT-FAIL-MP (--workers 2, --reload), faults F13-F14. L2 from the separate verifier session.

## 2026-10-14T14:23Z — Control plane: FTF-0904-W1 CERTIFIED (child slice)
- **Certified:** FTF-0904-W1 (#792, merge b4d401fc1fb1222c65ad4778b5aee2e1461a99c1) by separate verifier session; FTF-0904 WIRED (product-true 46).

## 2026-10-14T16:30Z — Control plane: FTF-0904-F1 returned
- **Returned:** FTF-0904-F1 r2 (0g): idempotence guard first, serialized/form positions, generated name class, sweeps, FAULTS.

## 2026-10-14T20:19Z — Control plane: FTF-0502-P1-F1 CERTIFIED (child slice)
- **Certified:** FTF-0502-P1-F1 (#798, merge e57b4f98bdaa2e8e13298d5e743e3940cb1cfd56); FTF-0502-P1 requalified; count unchanged.

## 2026-10-14T21:57Z — Control plane: FTF-H010-P3a returned (r3)
- **Returned:** FTF-H010-P3a r3 (0e): boundary rows, dotted target suffixes, behavioural kills only; stopping rule.

## 2026-10-14T23:44Z — Control plane: FTF-0512 DECERTIFIED; FTF-0512-F1 returned
- **Decertified:** FTF-0512 (Law 15, regression merged by #812). **Returned:** FTF-0512-F1 r2 (0c).

## 2026-10-15T03:46Z — Control plane: FTF-0904-F1 returned (r3)
- **Returned:** FTF-0904-F1 r3 (0g): whole-name case match, glued compounds, guard tail, empty-value, value-class tables.

## 2026-10-15T08:34Z — Control plane: FTF-1304-S1 returned (r2)
- **Returned:** FTF-1304-S1 r2 (0b): currency on windows + mixed-currency refusal; declared default_step_kind.

## 2026-10-15T08:34Z — Control plane: FTF-0512-F1 returned (r3)
- **Returned:** FTF-0512-F1 r3 (0c): NFKD non-starter bound after Cf strip (3.11), FB2 pin, detector-version pin, role/json/id negatives, So/modifier folds, behavioural faults. FTF-0512 stays DECERTIFIED.

## 2026-10-15T08:53Z — Control plane: FTF-0213-S1 returned (r2)
- **Returned:** FTF-0213-S1 r2 (0f): impossible usage counts refused, V16/V18 pinned, fewer-than-K trials refused.

## 2026-10-15T09:14Z — Control plane: FTF-H010-P3a returned (r4)
- **Returned:** FTF-H010-P3a r4 (0e): behaviour-only kill signal, 4 casefold/depth rows, survivor ruling, stopping rule.

## 2026-10-15T17:21Z — Control plane: FTF-0904-W1-F1 certified (child slice)
- **Certified:** FTF-0904-W1-F1 (#831, merge f7646ecf). W1 requalified.

## 2026-10-15T19:59Z — Control plane: FTF-0512 recertified; FTF-0512-F1 certified
- **Certified:** FTF-0512-F1 r3 (#836, merge 2351f53e); FTF-0512 recertified (count +1). Follow-up FTF-0512-F2 queued.

## 2026-10-15T19:59Z — Control plane: FTF-1304-S1 returned (r3)
- **Returned:** FTF-1304-S1 r3 (0b, tests only): cross-attempt currency, alias step-kind, currency case/whitespace pins; stopping rule.

## 2026-10-15T20:22Z — Control plane: FTF-0213-S1 returned (r3)
- **Returned:** FTF-0213-S1 r3 (0f, tests only): real-wire negative/zero usage modes, W1/W2/W3 RED; stopping rule.

## 2026-10-16T01:58Z — Control plane: FTF-0904-F1 returned (r4)
- **Returned:** FTF-0904-F1 r4 (0g): segment lookup before camel split, deny-by-default credential-suffix rule, separator-strip pins, W1 docstring. FTF-0904 stays CERTIFIED.

## 2026-10-16T05:05Z — Control plane: FTF-1304-S1 certified (child slice)
- **Certified:** FTF-1304-S1 (#849, merge f490784c) under the stopping rule; UNWIRED.

## 2026-10-16T05:05Z — Control plane: FTF-0213-S1 certified (child slice)
- **Certified:** FTF-0213-S1 (#850, merge 7460dcbe) under the stopping rule; UNWIRED.

## 2026-10-16T05:05Z — Control plane: FTF-0519-P1 returned (r2)
- **Returned:** FTF-0519-P1 r2 (0k): re-authorize every frozen grant, full frozen hash, id-free manifest summary, saturation fixture.

## 2026-10-16T12:10Z — Control plane: FTF-H011-P1 returned (r2)
- **Returned:** FTF-H011-P1 r2 (0l): whole-result masking, tool validator, Law 17 changed-object and probe states, 8 pins, deep-copy observer + differential test; stopping rule.

## 2026-10-16T14:05Z — Control plane: FTF-0519-P1 certified (child slice)
- **Certified:** FTF-0519-P1 (#859, merge ced8203b), round 2; UNWIRED. FTF-0519-P2 ready.

## 2026-10-16T14:05Z — Control plane: FTF-0904-F1 returned (r5)
- **Returned:** FTF-0904-F1 r5 (0g): segment lookup before camel split, non-ASCII-prefix pin; stopping rule. FTF-0904 stays CERTIFIED.

## 2026-10-20T04:45Z — Control plane: FTF-0512-F2 certified (child slice)
- **Certified:** FTF-0512-F2 (#861, merge 244ea3af); FTF-0512 requalified. Follow-up FTF-0512-F3 queued.

## 2026-10-20T04:45Z — Control plane: FTF-R006-P1a returned (r2); FTF-R006-P1 split
- **Returned:** FTF-R006-P1a r2 (0m): annotator-only, HTTPException -> Unavailable, bounded stream + deadline, strict schema, 5 pins; P1 split into P1a/P1b; stopping rule.

## 2026-10-20T06:35Z — Control plane: FTF-H011-P1 returned (r3)
- **Returned:** FTF-H011-P1 r3 (0l): nested masking + whole-result canary, 7 pins, P4 fixtures, importer-level differential.

## 2026-10-20T06:35Z — Control plane: FTF-0904-F1 returned (r6, tests only)
- **Returned:** FTF-0904-F1 r6 (0g): generated benign-split table from _BENIGN_NAMES, MY11/MY12 RED, random-case logging rows. FTF-0904 stays CERTIFIED.

## 2026-10-20T21:15Z — Control plane: FTF-0904-F1 certified (child slice)
- **Certified:** FTF-0904-F1 (#890, merge 51f8753f), round 6; FTF-0904 requalified. Follow-up FTF-0904-F2 queued.

## 2026-10-20T21:15Z — Control plane: FTF-H011-P1 returned (r4, tests only); FTF-0501-F2 queued
- **Returned:** FTF-H011-P1 r4 (0l): socket/exec/META-LEAK hooks around the real service; cp1256 waived for P1, gap queued as FTF-0501-F2.

## 2026-10-20T21:15Z — Control plane: FTF-R006-P1a returned (r3)
- **Returned:** FTF-R006-P1a r3 (0m): header-phase deadline via an optional guard deadline (ruling), final-1xx pin, static role error; stopping rule.

## 2026-10-21T00:25Z — Control plane: FTF-UX01 returned (r2)
- **Returned:** FTF-UX01 r2 (0n): visible Pending progress, 7 pins incl. keyboard tests, ids/association, refocus, whitespace, §4 evidence, Arabic; Dialog waits on the owner's Radix approval; §72.5 viewport/keyboard/zoom now on a real-browser harness.

## 2026-10-21T00:25Z — Control plane: FTF-UX02 returned (r2)
- **Returned:** FTF-UX02 r2 (0k, after UX01 r2): Dialog focus, en/ar + lang, rebuild on UX01, 10 pins, truthful confirm, §4 evidence.

## 2026-10-21T00:25Z — Control plane: FTF-0512-F3 returned (r2)
- **Returned:** FTF-0512-F3 r2 (0c): keep the whole mark run touching a digit (ruling), full-NFKC oracle, spacing pins, version 6.

## 2026-10-21T00:50Z — Control plane: FTF-0519-P2 returned (r2)
- **Returned:** FTF-0519-P2 r2 (0d): PG route integration + CI job, gate step, held-out source hidden (ruling), Idempotency-Key bound to the request digest.

## 2026-10-21T00:50Z — Control plane: FTF-H010-P2 returned (r3)
- **Returned:** FTF-H010-P2 r3 (0f): contract 10, CI wiring, echo missing/mismatch fail, revoke race, 2 survivors, boundary pin, submission. FTF-1001 requalified.

## 2026-10-21T12:49Z — Control plane: FTF-H010-P2 certified (child slice); FTF-UX01 returned (r3)
- **Certified:** FTF-H010-P2 (#918, merge c3c8da60), round 3; FTF-1001 requalified.
- **Returned:** FTF-UX01 r2 -> r3 (0n): ErrorSummary focus steal, dark link contrast + color-scheme, §4 evidence, P6 and all-ten-state icon pins; Dialog still waits on the owner's Radix approval.
- **Returned:** FTF-1505 r8 -> r9 (0h, tests only; verdict by independent session_015VgaHaMgvjedrPji9hzGLf): end-to-end scan_line pin with MAX_SCAN_BYTES-sized runs. Control plane produces round 9.
- **Maintenance (no requalification):** #906 (FTF-1506, lock-only: certifi 2026.10.19 for GHSA-6q48-6c7w-9gw2, vite 8.2.4 for GHSA-587h-7wmj-h5pm); FTF-1506's dependency-scanning job green on its head; no gate or allowlist changed.
- **Returned:** FTF-R006-P1a r3 -> r4 (0m): late-TLS-handshake deadline (production), username/name key-echo pins.
- **Returned (urgent):** FTF-0904-F2 r1 -> r2 (0b, take first): generic quoted-value fail-open regression of FTF-0904 (live on main), escaped-quote forms, V6/V10/V11/V17 pins, over-redaction counts. FTF-0904 requalification pending.
- **Certified:** FTF-0519-P2 (#915, merge 43a381ec), round 2. Allowlist: pending_pr keys removed (#742/#869/#915 on main), _refusal re-pinned 304 -> 327, three stale M9 entries retired.
- **Certified:** FTF-0512-F3 (#912, merge a4dff1f8), round 2; FTF-0512 requalified.
- **Certified:** FTF-H011-P1 (#907, merge c0bd2b2f), round 4 (tests only).
- **Returned:** FTF-UX03 r1 -> r2 (0g, after FTF-UX01): rebuild on UX01 components, full en/ar + dir/lang, error handling and recovery, loading/empty states, 8 pins, severity-2 decisions, §4 evidence in CI.
- **Not certified:** FTF-0501-F2 (#914): two Alembic heads on main (with #915). Control plane rechained the migration in this PR; certification of the fix + FTF-0501-F2 + the FTF-0519 parent goes to a separate session.
- **Certified:** FTF-0519 parent (main b61a0acb; count 155 -> 156), and the Control-plane two-heads fix (#924) - both by the separate session session_01FxdpwdR6T7wbuY17C6gw3o.
- **Returned:** FTF-UX01 r3 -> r4 (0n): focus only on submitCount change; complete §4 evidence.
- **Returned:** FTF-0501-F2 r1 -> r2 (0c, tests only): pin the non-UTF-8 split-at-SNIFF_BYTES path (B-F9). FTF-0501 requalified on main.
- **Certified:** FTF-1505 (#931, merge 05554535), round 9; verdict by the separate session session_01FeAArcFJu2Z9bw7JQZTBSY. Follow-up FTF-1505-F1 queued.
- **Certified:** FTF-UX01 (#938, merge 8de3116d), round 4. UX02 r2 and UX03 r2 are claimable now.
- **Returned (urgent):** FTF-0904-F2 r2 -> r3 (0b): escaped-quote qsep, V6/V10/V11/V17 pins, over-redaction record. Generic odd-run leak fixed by the Control plane in #940 (separate-session certification pending).
- **Returned:** FTF-R006-P1a r4 -> r5 (0m, tests only, Control-plane produced in #941): response-side key-echo pins.
- **Certified:** FTF-0501-F2 (#936, merge 05545cfc), round 2; FTF-0501 requalified.
- **Certified:** FTF-H010-P3a (#839, merge 5fd14b86), round 4; S05/S07/S08 allowlisted as reviewed equivalents.
- **Certified:** FTF-R006-P1a (#941, merge 69d099f8), round 5; verdict by the separate session session_014KfghB3VrkPEodzJAk4oK3. P1b claimable.
- **Returned:** FTF-UX03 r2 -> r3 (0g): wrap long names (04A03/04A04/04A22) with RED harness fixtures; pin role=alert (04A18, 04A21) and failed-submit focus (04A03); canonical checklist and corrected submission.
- **Certified:** FTF-UX03 (#956, merge 369decc2), round 3; FTF-UX01 requalified.
- **Certified:** FTF-1505-F1 (#958, merge 24f1672f), round 3; FTF-1505 requalified.
- **Returned:** FTF-R006-P1b r1 -> r2 (0c): verify scope before any Argilla call (cross-tenant), serialize revoke/sync, pin Law 17 outage paths, converge concurrent sync, column-limited UPDATE + trigger, real-Argilla CI job + R002 + gate step.
- **Returned:** FTF-UX07 r1 -> r2 (0d): real-browser spec with F7/F20/F8/F9 RED, §4 evidence, blank-label fix or decision, severity-2 decisions.
- **Returned:** FTF-UX05 r1 -> r2 (0e): rebuild on UX01, catch + shape-check (no unmount, no fabricated empty), wrap long data, 0308 confirm focus, empty/loading states, phrase() residue, 7 pins, browser harness in CI + §4 evidence.
- **Returned:** FTF-UX06 r1 -> r2 (0f): browser harness in CI, wrap long names, ErrorSummary focus, full Arabic, recovery steps + empty action, Field/useId, 16 pins, §4 evidence.
- **Returned:** FTF-H011-P2 r1 -> r2 (0g): an explicit rejection blocks reuse; reuse requires the prior approval's connection to be live.
- **Returned:** FTF-UX07 r2 -> r3 (0d): evidence only - 39-item checklist item by item, real skill output, severity per H1-H10 cell, two claims corrected.
- **Returned:** FTF-UX05 r2 -> r3 (0e): 1301 catch + announce, wrap long names, empty/loading states, phrase() residue, 16 pins, browser spec in CI, §4 evidence, corrected claims.
- **Returned:** FTF-H010-P3 r1 -> r2 (0h): re-read the base artifact FOR SHARE at the terminal write; pin every-shard, share-lock and adapter-header binding.
- **Certified:** FTF-UX07 (#982, merge 2aa77912), round 3; FTF-0101 requalified.
- **Certified:** FTF-UX05 (#983, merge 5d793fb8), round 3; parents and FTF-UX01 requalified; follow-ups FTF-UX01-F1, FTF-1301-F1 queued.
- **Certified:** FTF-H010-P3 (#989, merge e5474526), round 2; P3a/P2 requalified.

## 2026-10-23T1420Z — PROVISIONAL: FTF-UX02 round 2 merged (#947); regression found and fixed in shared Field
- **Submitted:** closes out the 2026-10-23T10:52Z unparking note (owner approval of
  `@radix-ui/react-dialog@1.3.1`, PR #995). Round 3's own two remaining items closed: 4 exact-string
  assertions for the Arabic retry-family phrases (`retry`, `networkErrorRetryable`,
  `requestFailedRetryable`, `responseMalformedRetryable`), each confirmed RED on a mutated string and
  GREEN restored; and the submission's stale FTF-0500A H1/H9 heuristic cells corrected to record
  round 2's own fixes, not just round 1's. Merging current `main` (every UX00–UX07 area merged since
  this branch's original base) and re-running the full `apps/web` suite — not just this item's own
  three parent suites, per l2-review-brief item 6's Law 15 regression check — surfaced a real
  cross-area regression: round 1's `Field.tsx` fix (`display: "block"` on the shared `<label>`) left
  `Field`'s `<input>` without the `--target-min` (24px) floor every other raw input/select in this
  repo already applies by hand, so it fell under the WCAG 2.2 2.5.8 (AA) 24×24 CSS px minimum;
  `ftf_ux06_browser.spec.ts`'s real-Chromium axe check caught it on FTF-UX06's `#field-category`
  (6/52 failing, certified before this PR's `Field.tsx` change existed on `main`). Independently
  confirmed real (reproduces on the merged head, not on a clean, unmodified `main` checkout in the
  same sandbox, 3/3 runs) before fixing: `Field`'s `<input>` now carries
  `minBlockSize: "var(--target-min)"` (caller `style` wins), with two new regression tests in the
  shared-component suite `ftf_ux01_r4.spec.ts`, each confirmed RED on the pre-fix code and GREEN
  restored. `ftf_ux06_browser.spec.ts`: 52/52 again. Full repo `pnpm exec vitest run` on the merged
  head: 32 test files, 3069/3069 tests passing (the only 2 "failed suites",
  `ftf_ux01.spec.ts`/`ftf_ux01_r3.spec.ts`, 0 tests each, are the pre-existing `tokens.css?raw`
  CSS-parse issue, independently reproduced on a clean `main` checkout in this same sandbox, not
  introduced by this PR). Real CI on the final pushed head: `ci-verdict` green, all 83 jobs
  (`gate` 1510 steps, `mutation-gate`, `secret-scanning`, 76 integration jobs) passed; merged as
  `b264be1`. Not self-certified (Law 4) — awaiting Control-plane L2 review, which should also
  requalify the new UX01 Dialog (focus trap, focus return, Escape, aria-modal) per the unparking
  note. Full clause-to-test mapping, fault→RED tables, every round's verbatim reviewer report and
  this update's regression writeup: `docs/certifications/submissions/2026-10-22T0119Z-FTF-UX02-r2.md`.
  Shift log: `docs/progress/2026-10-23T1318Z-relay.md`.
- **Returned:** FTF-1301-F1 r1 -> r2 (0d): browser out-of-order test, clear the old result on switch, pin F3/F4/F5, evidence.
- **Returned:** FTF-UX02 r2 -> r3 (0k): Dialog RTL position + aria-modal + own tests FIRST, 0500A malformed replies, long names, rebuild 0516 and the rest of 0501-UI, full browser spec, A9 pin, evidence.

## 2026-10-23T1800Z — PROVISIONAL: FTF-UX01-F1 round 1 merged (#994)
- **Submitted:** follow-up on CERTIFIED FTF-UX01/FTF-UX05 (found by the FTF-UX05 r3 verifier, #983).
  `apps/web/components/ui/AlertMessage.tsx`'s recovery-sentence lookup is now `Object.hasOwn`-safe,
  so a `kind` outside the union (`"toString"`, `"__proto__"`, `"bogus"`, `""`) renders the retry
  step instead of throwing during render; `errors.ts`'s `networkError` no longer passes another
  `Error`'s English message through under `locale !== "en"`. New spec
  `apps/web/tests/ftf_ux01_f1.spec.ts` (16 tests, jsdom) plus direct coverage for `focus.ts`,
  `singleflight.ts` and `kindForStatus`, all RED on the pre-fix code and GREEN restored; fault→RED
  table for all 41 injected faults in the submission (38 RED, 3 disclosed equivalent mutants —
  `arm` recording `<body>`/an unmounted SVG target and the fallback-not-consumed case — none
  observable). Two pre-review rounds by a fresh reviewer subagent (strongest model, did not write
  the code; evidence only, never certification — Law 4): round 1 returned 3 findings (two stayed-
  GREEN faults, one stale same-class-sweep sentence), all fixed; round 2's own verdict is
  "FTF-UX01-F1 CERTIFY" (not a Control-plane action). Law 15: 13 jsdom spec files covering
  UX01/UX03/UX05/UX06/UX07/0214/0308/0309/1301, 997/997 passing; eslint/prettier/`tsc --build`
  clean; `validate_spec --strict`, `status.py` (157/304), `product_truth_check --fail-on-unwired`
  (49/49) and `data_exposure_check` all pass. No Python changed (mutation gate has nothing to
  mutate); no `ci.yml` change. Real CI on the final head (after one fresh-base merge, main had
  advanced past the PR's originally-tested base with zero file overlap): `ci-verdict` green;
  merged as `21e0b3a`. Not self-certified (Law 4) — `certification_state` stays PROVISIONAL,
  awaiting Control-plane L2 review. Full submission: `docs/certifications/submissions/
  2026-10-23T1040Z-FTF-UX01-F1.md`. Shift logs: `docs/progress/2026-10-23T1040Z-laneB.md` (lane B,
  authored the fix) and `docs/progress/2026-10-23T1652Z-relay.md` (this session's fresh-base merge
  and final push to green).
- **Returned:** FTF-R006-P1b r2 -> r3 (0c): revoke-first or row lock (R11b/R11c), pin F14/F16, real-Argilla CI that exercises the store, OpenSearch + Redis 7.2 per the licence rulings, gate step, [GOVERNANCE] title.
- **Returned:** FTF-UX06 r2 -> r3 (0f): wrap at the root, 403 save announced with focus, Arabic codes/catalogue/bdi, pin F1/F12/F13/F18/F23/N24, access recovery, useId, evidence.
- **Certified:** FTF-H011-P2 (#978, merge df2218ef), round 2; FTF-H011-P1 requalified; P3 rulings recorded.
- **Returned:** FTF-H010-P4 r1 -> r2 (0p): archived or digest-drifted weights artifact must stop continue_tuning (derive via the lineage manifest, fail closed, add rows to the decision table and integration suite), pin the advisory lock with a deterministic two-session interleaving test, pin the revocation all-NULL-or-all-set CHECK by name; PR #998 merge 43512af4; P5 and P6 remain.
- **Certified:** FTF-UX01-F1 (#994 merge 21e0b3a8 + #1008 merge bb940d33), round 2; FTF-UX01 and FTF-UX05 requalified.
- **Certified:** FTF-0617 (#1011, merge 816f0fb3), round 2; 158/304; UNWIRED.
- **Returned:** FTF-1301-F1 r2 -> r3 (0q): tests only - pin setNotice(null) and organization-in-identityKey in jsdom and Chromium, Chromium tests for stale 500 / network error / pending reply (F3/F4/F5), fix the stale docstring.
- **Ruled:** draft #1020 (FTF-1504 r2) is a dead claim; next lane claims relay/FTF-1504-r3 on top of relay/FTF-1504-r2.

## 2026-11-10T11:38Z — PROVISIONAL: FTF-1301-F1 round 2 merged (#1005)
- **Submitted:** round 2 on QUEUE.md item 0d (Control-plane return on #996, `dab64d2`). Four
  findings closed, each with its own fault → RED table (full detail in the submission doc):
  (1) MISSING-BROWSER-TEST — `ux05_entry.tsx` now reuses one `Root` per page and supports
  held/individually-released fetch responses, so a new real-Chromium test reproduces the
  out-of-order project-switch race, RED on `dab64d2^1` (pre-round-1), GREEN from round 1 onward;
  (2) STALE-WHILE-LOADING (severity 3) — a `seenIdentity` render-time reset (same pattern as the
  certified `capability_compatibility_ui.tsx`/FTF-0309) clears `search`/`error`/`notice` and sets
  `loading` synchronously on any identity change, so no committed frame shows the old project's
  rate under the new project's own loading indicator; new tests in both jsdom and Chromium;
  (3) UNPINNED-STALE-ERROR (F3/F4/F5) — three `stale()` guards round 1 already had but no test
  exercised, each now independently fault-injected; (4) EVIDENCE — the round-1 "in jsdom" wording
  is superseded by the new Chromium coverage. P8 advisory (severity 2) also fixed in both
  `handleCreate`/`handleVerify` (a write's own error no longer surfaces once the user has switched
  project/organization), including a second commit closing a Create-side gap the pre-review's own
  probe found. Independent pre-review (same reviewer who certified round 1, fresh continuation):
  ran the full fault matrix across pre-round-1/round-1-only/this-round code in both jsdom and
  Chromium, its own adversarial probe, full regression, and returned **`FTF-1301-F1-r2 CERTIFY`**
  (evidence only, never a self-certification — Law 4). Regression: full web suite (31 files, 3119
  jsdom tests) and the real-Chromium UX05 harness (253 tests) all pass; `tsc --build`/eslint/
  prettier clean; no new runtime dependency, migration, route, or datastore surface.
- **CI history note (for the record):** this PR's `ci-verdict` was red 2026-10-23T22:49Z–
  2026-11-10T09:48Z solely from a systemic GitHub Actions runner-provisioning outage affecting
  every open PR in the repo, not this item's own code (full evidence and resolution:
  `BLOCKER.md`'s `RESOLVED 2026-11-10T09:50Z` entry, `docs/progress/2026-10-27T1252Z-relay.md`).
  Once Actions recovered, a fresh-base merge and re-run went green on the real head.
- Merged fresh `main` repeatedly to stay on a tested base as other PRs landed concurrently (most
  recently #998 FTF-H010-P4); final real CI on the merged head: `ci-verdict` green, 93/93 check
  runs passing (including the `ftf0904-fault-matrix` shards and every `*-integration` job).
  Merged as `5e0d91b4`. Not self-certified (Law 4) — `certification_state` stays PROVISIONAL,
  awaiting Control-plane L2 review. Full submission:
  `docs/certifications/submissions/2026-10-23T1742Z-FTF-1301-F1-r2.md`. Shift logs:
  `docs/progress/2026-10-27T1252Z-relay.md` (outage discovery) and this session's own PR-activity
  follow-up (no separate dated shift-log file — recorded in `PROGRESS.md` directly).
