# apps/api — Factory API + policy

Factory API and policy layer: identity/tenancy/RBAC, the transactional
outbox, and the typed job specifications handed to Temporal workflows and
workers. Owns the adapter/policy/evidence layer around the seven reused
components (see `docs/architecture/ARCHITECTURE.md` section 2); never
reimplements the reused engines themselves.

No implementation yet — this directory is scaffolding from FTF-0001
(Initialize monorepo).
