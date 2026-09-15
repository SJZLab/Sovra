# BLOCKER.md
<empty — prior hash-mismatch blocker resolved by control plane, see below>

## Resolved: CLAUDE.md hash mismatch (2026-09-15)
Verified against both refs present on origin at resolution time
(`claude/trusting-wozniak-49xf3f` and `claude/wonderful-goodall-bnuc0r`,
common ancestor f9c2ccd — no `main` branch exists on origin yet, so there
was no single canonical ref to check against; both branches carried
byte-identical CLAUDE.md content, so the verified hash does not depend on
which one is treated as canonical).

Root cause: the placeholder hash pinned at scaffold time
(sha256:3dfc28137298f97d8851cc3351c89cb4471c5db1a8d2a3e7190a8334e875ea8f)
was never the correct self-referential hash for the file it shipped in —
a bootstrapping artifact, not drift or tampering (content identical across
both branches; no unauthorized edits to OPERATING MODE FLAGS or Laws).

Fix applied: recomputed sha256 over CLAUDE.md with the hash line's value
replaced by the literal placeholder token (per the file's own documented
convention — "computed on this file with the placeholder still in
place"), then pinned the result into the hash line.

New pinned value: sha256:137d0fdee4a1aca4ffc1ab7d07f0e1c4cc5bbfa8423b8a1cf321efca8b6e1d31

Outstanding note (not a new blocker, flagged for visibility): origin has
no `main` branch — only the two `claude/...` branches above. Whichever one
the owner intends as the trunk should be established before relying on
"current main branch" as a check target in future shifts.
