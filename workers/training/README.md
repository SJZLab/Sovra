# workers/training — Training worker

Wraps Axolotl (pinned worker package) behind Factory-owned authorization,
config, masking, budget, recovery, and integrity controls. See
`docs/architecture/REFERENCE_REPOSITORIES.md` for the pinned upstream source
and `docs/architecture/ARCHITECTURE.md` section 2 for the reused-vs-owned
responsibility split.

No implementation yet — this directory is scaffolding from FTF-0001
(Initialize monorepo). Integration lands under FTF-R003 and related items.
