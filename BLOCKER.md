# BLOCKER.md

CLAUDE.md hash mismatch — control plane must re-verify and re-pin.

- Expected (per relay prompt / CLAUDE.md self-declared value):
  sha256:3dfc28137298f97d8851cc3351c89cb4471c5db1a8d2a3e7190a8334e875ea8f
- Actual (computed on current repo CLAUDE.md, this shift):
  sha256:b0ac948de4e3ecec953bab7bfeb068f098dd9af5d0f49d700e77e2fed788d5ea

Per relay instructions: "Mismatch between this hash and current CLAUDE.md =
stop this shift immediately." Shift stopped at Step 0 before reading
PROGRESS.md/QUEUE.md state or taking any queue item. No code changes, no
commits, no PR opened this shift.

Three route options for the control plane:
1. If CLAUDE.md's content is correct and unchanged since the hash was pinned,
   recompute sha256 on the current file and re-pin the corrected value in
   CLAUDE.md's "CLAUDE.md hash" line (and in the relay trigger's stored
   prompt, since it embeds the same value).
2. If CLAUDE.md was edited after the hash was pinned (intentional drift),
   review the diff for unauthorized changes to OPERATING MODE FLAGS, Laws,
   or "Ask owner first" sections before re-pinning — a hash mismatch is the
   designed tripwire for exactly this case (see CLAUDE.md Law 9,
   gate-weakening is watched).
3. If neither side can be trusted (e.g. the relay trigger prompt was stored
   from a stale CLAUDE.md draft before the file was finalized), regenerate
   both from a single source of truth and re-pin atomically so the relay
   prompt and CLAUDE.md never diverge again.

No owner action queued to QUEUE.md for this — hash re-pinning is a
control-plane responsibility per CLAUDE.md's own note ("Recompute and
re-pin here whenever this file's content changes"), not an owner ask.
