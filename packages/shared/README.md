# packages/shared — Shared schema/adapter contracts

Cross-language contracts (schemas, typed interfaces) shared between
`apps/web`, `apps/api`, and the workers — the stable Factory integration
entities listed in `docs/architecture/ARCHITECTURE.md` section 4
(ComponentBinding, DeploymentProfile, AgentManifest, EvaluationRun, and so
on). Kept as a JS/TS package so `apps/web` can import types directly; Python
consumers generate/validate against the same schemas rather than
maintaining a parallel definition.

No implementation yet — this directory is scaffolding from FTF-0001
(Initialize monorepo).
