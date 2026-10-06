# ADR 0003 — Release 1 task addendum: FTF-0103A (denominator 301 → 302)

- Status: Accepted, 2026-10-06
- Decided by: owner (live instruction, "Approve"), recorded by Control plane session
  `session_01QVznorVMjnmcttUhyDnoGR`

## Context
Every live `/api/v1` route in `apps/api/app/main.py` is built with the default fail-closed
`principal_resolver` and `role_lookup` from FTF-0103. Those defaults return `None`, so every request
is denied, and no success path is reachable from the product. FTF-0101 already issues and validates a
signed session cookie (`oidc_login.read_session_cookie`), but nothing reads it on API requests.
No Membership table exists (Section 6.4), so there is nothing to look roles up in. FTF-0101 and
FTF-0103 were certified with this gap disclosed as "a separately claimed prerequisite". No task in
the frozen 300-task index owns it. A 2026-10-06 wiring audit by the Control plane found it as the
reason most certified modules cannot become product-true.

## Decision
Add one task, **FTF-0103A — Live session principal and Membership persistence**, with its own
forward-migration contract (`spec/addenda/FTF-0103A.json`). The Release 1 denominator becomes 302.
`docs/spec-matrix.yaml` is the denominator's source of truth, and `scripts/status.py` reads it.

## Consequences
- `spec/tasks.json` and `tools/r1_handoff.py` stay frozen at 300 (ADR 0002). Addendum contracts live in
  `spec/addenda/<ID>.json`.
- FTF-0103A is top priority: it unblocks every success-path, user-visible assertion in the API.
- FTF-0101 and FTF-0103 keep their certification. Each certified exactly its own disclosed scope.
- Any further addendum needs the same owner decision and an ADR.
