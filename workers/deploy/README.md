# workers/deploy — Deployment controller

Owns artifact/template/parser identity, tenancy, capacity, and rollout on
top of private vLLM model-serving and the existing LiteLLM gateway's
registration surface. See `docs/architecture/REFERENCE_REPOSITORIES.md` for
pinned upstream sources and `docs/architecture/ARCHITECTURE.md` section 2
for the reused-vs-owned responsibility split. LiteLLM is the organization's
existing gateway and is never replaced automatically.

No implementation yet — this directory is scaffolding from FTF-0001
(Initialize monorepo).
