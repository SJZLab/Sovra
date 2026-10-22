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
- Full repo: `pnpm exec vitest run` 1539/1539, `pnpm run typecheck` clean, `pnpm run lint` clean,
  `pnpm run format:check` clean, `scripts/product_truth_check.py --fail-on-unwired` 49/49.

## Not done / queued

- FTF-0516 (dataset issue resolution): untouched, same as round 1. Needs its own pass.
- FTF-0501-UI: only the abandon dialog was rebuilt; the rest of that 1458-line component (the
  multi-step upload form itself) is still native/English.
- **No independent fresh-reviewer pre-review was run for this submission.** This session is a
  worker fork barred from spawning its own subagents (`Agent` tool disabled for forks), so the
  CLAUDE.md "Relay is execution-only" step 4 pre-review (hand `docs/operations/l2-review-brief.md`
  to a fresh reviewer) could not be performed here. **PR left in draft** rather than merged, pending
  that review — recommend the coordinating session or Control plane run it before merge.

## Certified / product-true counts observed

`scripts/product_truth_check.py --fail-on-unwired`: 49/49 product-true (unchanged by this slice —
no `proving_symbol` entry touched). Certified count not independently recomputed this shift (no
QUEUE.md/certifications/queue.md edit made, per the "No shared-file edits" ruling); see those files'
own live state for the authoritative count.
