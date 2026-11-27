# How we work (v2, owner instruction 2026-11-27, live)

This page is the working procedure. It overrides docs/operations/routines.md and older QUEUE.md
rules where they differ. The Laws and hard stops in CLAUDE.md are unchanged.

1. **What to work on.** Run `python tools/next_tasks.py`. It lists the uncertified tasks whose
   dependencies are all certified, ordered by how many tasks wait on each. Take the first one with
   no open PR. Work outside that list (UX polish, items outside the 304) only when nothing on it
   is free.
2. **One task, one PR, one worker.** A worker stays on its task until the PR is ready. A PR with no
   push for 3 hours is continued on the same PR by the next worker; nobody opens a second PR for
   the same item. Split a task only when it cannot fit one PR, into at most three slices named in
   the first PR.
3. **Before every push.** Run lint, format and type checks, the proving tests the way CI runs
   them, and the mutation gate on the changed modules, locally. CI confirms; it is not the first
   run.
4. **One independent review.** When `ci-verdict` is green the worker marks the PR ready and stops.
   The Control plane starts ONE reviewer session that wrote none of the code, with
   docs/operations/l2-review-brief.md. CERTIFY: the Control plane merges and records it. RETURN:
   the worker fixes only the numbered blocking items and the re-review is bounded. There is no
   separate relay pre-review.
5. **Records.** The PR description is the submission: each clause, its test, and the fault shown
   RED. On certification the Control plane updates docs/spec-matrix.yaml, adds one ledger line and
   runs `sync_certified_status`. Nothing else is written: no shift logs, no progress notes, no
   PROGRESS.md narrative (one status line).
6. **Blocked.** The Control plane decides technical questions the same day under the
   default-resolution rule. Only a CLAUDE.md hard stop goes to the owner, as one dated line in
   QUEUE.md "Owner actions — current".
7. **Capacity.** Up to four workers at once, on tasks with different parents.
