# QUEUE — Sovra (Model Factory)
Updated: 2026-09-16 by relay
Relay: 2h · Report: 08:30 & 19:30 Asia/Riyadh · Certifier: Control plane (autonomous)

## Status
Certified: 4/300 · Provisional: 0 · Blocked: 0
Product-true (Law 16): 0 — must match certified (FTF-0001, FTF-0002,
FTF-0003 and FTF-0005 all have no proving_symbol; FTF-0001 is structural
layout, FTF-0002 is CI tooling, FTF-0003 is test-harness tooling, and
FTF-0005 is a config loader with no live caller yet — none a live-wired
product-runtime path, so all four are CERTIFIED without counting toward
product-true).

## Owner actions — current
- 2026-09-15: One setup gap remains — no *durable* trigger tooling
  available to create the 2h relay / watchdog triggers. See CLAUDE.md
  "Setup gaps". Owner must create them (or grant a control-plane session
  trigger tooling and re-run Groundwork S1.7/S1.8). Note: this shift's
  own session was itself invoked by some external schedule (its prompt
  says "started automatically by a schedule"), so a trigger mechanism
  exists for the relay cadence at minimum — not confirming this gap
  resolved without evidence of the watchdog trigger and the 08:30/19:30
  report cadence too, per the prior watchdog finding on unverified
  "gap resolved" claims. Owner: please confirm what's actually configured.
## Active (relay works this)
Nothing active — FTF-0005 (fix + re-certification) landed this shift.
Next candidate: FTF-0004 (docker local stack).

## Provisional (awaiting Control plane L2 certification)
None currently.

## Queue (ordered by dependency)
Full 300-record dependency graph: `docs/TASK_INDEX.md` (canonical) and
`docs/spec-matrix.yaml` (proof-state per record). Do not re-copy the table
here — two copies drift. Read order for early work:
1. FTF-0001 → FTF-0002 → FTF-0003 (bootstrap, lint/type, test harness)
2. FTF-0005 (config) → FTF-0004 (docker local stack) → FTF-0006 (observability)
3. FTF-0101…0106 (identity/tenancy/RBAC/audit — CAP-01)
4. FTF-R001/R002 (freeze seven-component architecture + authenticated bindings —
   do this early; every reuse_architecture and most audit_hardening tasks depend
   on it transitively)
5. FTF-H001/H002 (release-scope freeze) before anything claims DONE

Every task's real acceptance/negative-acceptance/verification-command contract
now lives in `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolvable on demand. Relay:
before claiming any task, run
`python tools/r1_handoff.py packet <task-id> --plan FINE_TUNING_FACTORY_RELEASE_1.md`
to get its real DoD — read the generated
`docs/implementation/task-packets/<task-id>.md`, not just the title in
`docs/TASK_INDEX.md`. Write BLOCKER.md only if that command itself fails
(unknown task ID, hash-mismatch on a tampered/edited plan file, or similar) —
not merely because the task looked title-only elsewhere.

## Blocked

## Done (certified + promoted via L4)
- [x] FTF-0001 — Initialize monorepo. CERTIFIED 2026-09-15 (control-plane
      L2). PR: https://github.com/SJZLab/Sovra/pull/3, merge commit
      7ab3f6ff1ac7bcc240e546b764af8d67ae0c8561. Ledger:
      docs/certifications/ledger.md. Not yet PROMOTED — L4 gate is
      release-only and hasn't run.
      Note: the original 2026-09-15 L2 certification was found
      self-certified (watchdog PR #13 — PR #3 and PR #5 share session
      identity, Law 4 violation), briefly reverted to PROVISIONAL, then
      independently re-verified and re-certified same day by a session
      with no session-ID overlap with either PR #3/#5 or PR #13. See
      docs/certifications/ledger.md for both entries. Count below was
      never inflated by this — it stayed 1/300 before and after, since
      the correction and re-certification landed together.
- [x] FTF-0002 — Add formatting/lint/type checks. CERTIFIED 2026-09-16
      (control-plane L2, session_01DY5FK4nK2VLrUMKHwNQQU2 — session-ID
      compared against both PR #23 commits' session_01NuAjwmK5rwuxefyo5L7zgy,
      no overlap). PR: https://github.com/SJZLab/Sovra/pull/23, merge
      commit 41f1493bca4f70aa74ab61516d1a22ee197daf79. Ledger:
      docs/certifications/ledger.md. Independently re-verified: fresh
      `uv sync`/`pnpm install`, fresh run of `tests/tasks/test_ftf_0002.py`
      (6/6 pass), a real fault injection (not just re-reading the
      producer's claim), the real GitHub Actions run on the actual merged
      PR head, and the delivered work checked against FTF-0002's real
      acceptance/negative_acceptance contract pulled via
      `tools/r1_handoff.py packet FTF-0002`. Not yet PROMOTED — L4 gate is
      release-only and hasn't run.
- [x] FTF-0003 — Add test harnesses. CERTIFIED 2026-09-16 (control-plane
      L2, session_018gZRsspGdmcTmtxkqRDE59 — session-ID compared against
      both PR #26 commits' session_01DY5FK4nK2VLrUMKHwNQQU2, no overlap).
      PR: https://github.com/SJZLab/Sovra/pull/26, merge commit
      e2c2dae96142c95a78005fd7528df6ac7e3e5dfc. Ledger:
      docs/certifications/ledger.md. Independently re-verified in a fresh
      git worktree at the actual merge commit: fresh `uv sync`/`pnpm
      install`, fresh runs of the real CI test commands (pytest + Vitest +
      `check_test_harness.py` + `tests/tasks/test_ftf_0003.py`), three
      independent fault injections (empty pytest dir, a `pytest.mark.skip`
      fixture, a Vitest `test.only` fixture — each shown to fail the gate
      for real), the real GitHub Actions run on the actual merged PR head,
      and the delivered work checked against FTF-0003's real
      acceptance/negative_acceptance contract pulled via
      `tools/r1_handoff.py packet FTF-0003`. Confirmed no regression to
      FTF-0001/0002's certified evidence (both files untouched by this
      PR's diff). Not yet PROMOTED — L4 gate is release-only and hasn't
      run.
- [x] FTF-0005 — Configuration system. CERTIFIED 2026-09-16 (control-plane
      L2, session_016Y23GYB47qB8ExZNhH16Wn — session-ID compared against
      both PR #30 commits' session_012mAtoqKYzA7PiXbkGkD7rY, no overlap).
      PR: https://github.com/SJZLab/Sovra/pull/30 (fix for the defect
      found reviewing PR #28, see PR #29 / docs/certifications/queue.md),
      merge commit 5f5a34d803a76e3261c8cc860bd5f21cd8290424. Ledger:
      docs/certifications/ledger.md. Independently re-verified: read the
      full one-line-fix diff, fetched the real GitHub Actions run tied to
      the actual PR head SHA (conclusion success), independently
      reproduced the original repr secret-redaction defect via fault
      injection (revert fix → new check fails; restore → 10/10 pass),
      confirmed the pre-existing lint/mypy findings on `main` are
      unrelated to this diff, `product_truth_check.py` 0/0 legitimate (no
      proving_symbol yet), and the fix checked against FTF-0005's real
      contract pulled via `tools/r1_handoff.py packet FTF-0005`. Not yet
      PROMOTED — L4 gate is release-only and hasn't run.
