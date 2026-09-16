# Budget forecast (FTF-H002)

Section 64.4: "Measure one actual vertical slice before approving a full-
program forecast... These are workflow controls, not promises to deliver
the entire system for a particular dollar amount." This document records
what this repository can actually measure about its own delivered slice so
far, and discloses — rather than invents — the parts it cannot measure from
inside this execution environment.

## Real vertical-slice measurement (from this repository's own history)

The following is derived directly from `git log` against this exact
checkout, not estimated or asserted from memory:

- Merged, certified tasks to date: 8 (`FTF-0001`–`FTF-0006`, `FTF-H001`,
  `FTF-R001` — see `docs/spec-matrix.yaml` for the authoritative current
  count).
- Total commits in this repository's history: re-derive with
  `git log --oneline | wc -l` against the current checkout (the count
  changes on every future task, so it is not pinned here as a stale
  number).
- Elapsed wall-clock span of delivered work: re-derive with
  `git log --format=%ad --date=short | sort | sed -n '1p;$p'` against the
  current checkout, for the same reason.

These are real, reproducible measurements of *implementation activity*
(commits, merged tasks, elapsed time) — they are not a cost figure, because
none of them are denominated in billed provider units.

## What this document cannot measure: BLOCKED_EXTERNAL

Per Section 64.4, cost must be "input/output/cache/reasoning units as billed
by the chosen coding provider, reviewer/parallel overhead, failed attempts,
GPU/storage/platform/CI hours, managed serving minimums and expected
recurring operating costs." None of that billing/usage data is reachable
from inside this relay's execution environment — there is no coding-provider
billing/usage API call available to this session (see
`app.services.environment_preflight.check_coding_provider_billing`, which
reports this same fact as a structured `BLOCKED_EXTERNAL` result with a
required `owner`/`next_action`, not a silent gap).

Per this task's own `negative_acceptance` ("Do not invent a model ID/rate or
cut required scope to fit the earlier conversational budget"), this document
does not estimate, guess, or back-fill a per-token, per-hour, or per-task
dollar figure. **BLOCKED_EXTERNAL — no billed-usage forecast is recorded
here.**

- **Owner:** owner (budget authorizer).
- **Next action:** supply a real billed-usage export (coding-provider
  invoice/usage report) covering a specific, named task or date range, so a
  real $/task or $/hour rate can be computed from actual billed units
  instead of estimated. Once supplied, reforecast per Section 64.4 (warn at
  70% of authorized spend, require review at 85%, deny new reservations
  beyond authorized available spend).

## Separate budget categories (Section 64.4)

`development implementation`, `qualification`, and `ongoing service` budgets
must be tracked separately once real billed data exists. None of the three
has a real measured baseline yet — each starts `BLOCKED_EXTERNAL` for the
same reason as above, not `$0` (a `$0` local-token price does not imply
`$0` TCO, and this document does not claim one).
