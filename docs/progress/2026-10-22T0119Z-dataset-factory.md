# Shift log — FTF-UX02 round 2 (dataset-factory UI redesign)

2026-10-22, relay lane (dataset-factory). Branch `relay/FTF-UX02-r2`, base `origin/main`@`dfc0a57`.

## Picked

QUEUE.md "## Active" item **0k** (FTF-UX02 round 2). At claim time, 0b (`FTF-0904-F2-r3`) and 0g
(`FTF-UX03-r2`, PR #943) already had fresh in-flight claim branches with CI running; 0m is explicit
Control-plane-only; the FTF-0502/0503/0504/0505 importer-order items have long, ambiguous round
histories not safe to resume without deeper investigation this shift. 0k was unclaimed, fully
specified (concrete 7-finding return with a stopping rule) and clean to pick up.

## Done this shift

- FTF-0501-UI finding 1 (CONFIRM-FOCUS-LOST) and finding 5 (CONFIRM-PROMISES-DISCARD): the
  abandon-confirmation is now the new shared `apps/web/components/ui/Dialog.tsx` (Radix-based, real
  focus trap + return, Escape-safe), with honestly reworded copy. Found and fixed a real focus-return
  bug along the way (two wrong attempts, then a working `restoreFocusTo` mechanism) — only reproduced
  in a real-Chromium harness, not jsdom.
- FTF-0500A rebuilt on the UX01 shared components (Button/Field/EmptyState/PendingView), full
  bilingual chrome (lang+dir, every label/heading/button), a loading state and `role="alert"` on
  every previously-silent error (severity-2 required items).
- New real-Chromium §72.5 evidence harness: `apps/web/tests/browser/ux02_entry.tsx` +
  `apps/web/tests/ftf_ux02_browser.spec.ts` (32 tests, viewport/zoom/axe/keyboard + the two dedicated
  focus-trap/return tests), modelled on the sibling FTF-UX03 round-2 harness built this same shift.
- `@radix-ui/react-dialog@1.3.1` added for real this run (the 2026-10-20 sandbox denial recorded in
  QUEUE.md's "## Parked" did not reproduce) — flagged for Control plane/owner to reconcile that
  Parked entry and the matching "Owner actions — current" line (not edited directly; relay PRs don't
  touch QUEUE.md).
- Full repo: `pnpm exec vitest run` (figure as first measured this shift, see "Pre-review round 1"
  below for the corrected, reproducible count), `pnpm run typecheck` clean, `pnpm run lint` clean,
  `pnpm run format:check` clean, `scripts/product_truth_check.py --fail-on-unwired` 49/49.

## Pre-review round 1 (this update)

The coordinating session ran the required independent pre-review (a fresh subagent, strongest
model available, given `docs/operations/l2-review-brief.md` filled in for this item) against the
head above. **Verdict: RETURN**, 7 numbered findings — full verbatim report and this update's fix
log are in `docs/certifications/submissions/2026-10-22T0119Z-FTF-UX02-r2.md`. Summary of what the
review actually found and what changed in response:

- **Confirmed genuine** (no action needed): the Radix dependency really installed and pinned, the
  new browser-evidence harness wired into CI's `gate`/`ci-verdict`, full-suite/typecheck/lint/format
  clean, `product_truth_check.py` 49/49, the three parent suites + their real-API suites requalify,
  the Dialog's trap/Escape/overlay/Keep-draft focus behaviour, and finding 5's reword.
- **Real defects fixed this update** (all with fault→RED→GREEN, see the submission for detail):
  1. **CONFIRM-FOCUS-ON-CONFIRM**: confirming Abandon left focus on `<body>` instead of the fresh
     form's first control — a real bug, not just untested. Fixed with a two-effect sequencing change
     in `resumable_dataset_upload_ui.tsx`; also pinned initial open focus = the specific Keep-draft
     button.
  2. **UNPINNED-F4**: Keep/Abandon labels, the "cannot be undone" sentence, the Arabic confirm body,
     the Arabic empty-state title, `connection_status` in the 0500A option text, and
     `upload-resume-error`'s `role=alert` were all real but unasserted — now pinned.
  3. **NOT-BILINGUAL-0500A**: a few Arabic strings and the Dialog's `lang` attribute were unpinned;
     the connection option's display name was not wrapped in `<bdi>` (only the preview/authorization
     sections had it) — now wrapped and pinned.
  4. **ERRORS-NOT-ANNOUNCED-0500A** (severity 3, the big one): a rejected list/create/authorize
     fetch was silent (unhandled rejection, button stuck disabled); a non-array or `null` 200 list
     body crashed the component outright (`connections?.find is not a function`) or spun forever.
     Fixed for real in `dataset_source_selector_purpose_authorization_ui.tsx`: every fetch path now
     catches and shows a typed, bilingual, `role=alert` error with a Retry affordance; a malformed
     2xx body is a typed error, never a crash.
  5. **LABELS-ABOVE-FIELDS misclaimed**: this doc's own earlier "already true structurally" claim was
     wrong (DOM order ≠ visual layout — measured in real Chromium). Fixed with `display: block` on
     the labels (`Field.tsx` + two raw labels in 0500A) and a real geometry assertion at 1280/390px.
     Also fixed the duplicated "Credential (optional) (optional)".
  6. **STALE-IDENTITY-0500A**: an org/project change mid-flight left the old identity's data showing,
     with no loading state, and a late reply could still land. Fixed with the same key-based-remount
     pattern the sibling `ResumableDatasetUploadUI` already uses, plus an `aliveRef` guard.
  7. **UX01-REBUILD-UNPINNED / EVIDENCE**: the UX01 `Button` swap was unpinned; `aria-busy` was
     absent (now added to the shared `PendingView`, benefits every slice using it); the submission's
     own H1/H9 "Met" cells were false given finding 4's bugs (now corrected); the WCAG 2.2 AA
     manual-items deferral-to-FTF-H029 line was missing (now added). `ErrorSummary` stays
     deliberately unused for 0500A's top-level errors — disclosed as a scope decision, not silently
     skipped.
- **Regression after fixes**: full repo `pnpm exec vitest run` 1498→1527 passing (+29, zero
  regressions; same 2 pre-existing unrelated `ftf_ux01*` failures as before this round started).
  typecheck/lint/format clean. (A stray gitignored `dist/` build artifact, not part of this diff, was
  found inflating earlier counts in this item's history — removed before measuring; flagged in the
  submission so it isn't mistaken for a real discrepancy.)

PR #947 is still in **draft**, pushed with these fixes. A second independent pre-review round is the
coordinating session's next step before any merge decision — not performed by this fork (still
barred from spawning its own subagents).

## Not done / queued

- FTF-0516 (dataset issue resolution): untouched, same as round 1. Needs its own pass.
- FTF-0501-UI: only the abandon dialog was rebuilt; the rest of that 1458-line component (the
  multi-step upload form itself) is still native/English.
- The real-API integration suites were not re-run locally this update (no Docker daemon in this
  sandbox); this update's own diff doesn't touch the request/response logic those suites exercise.
  CI's own run is authoritative.

## Certified / product-true counts observed

`scripts/product_truth_check.py --fail-on-unwired`: 49/49 product-true (unchanged by this slice —
no `proving_symbol` entry touched). Certified count not independently recomputed this shift (no
QUEUE.md/certifications/queue.md edit made, per the "No shared-file edits" ruling); see those files'
own live state for the authoritative count.
