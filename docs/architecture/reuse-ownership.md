# Reuse architecture ownership (FTF-R001)

Freezes ownership and authority for the seven reused components named in
`FINE_TUNING_FACTORY_RELEASE_1.md` Section 77. This document does not
restate the whole master spec; it records the binding decisions this task
actually freezes, with section references for the full text. The
machine-checked contracts are `packages/component-contracts/architecture.schema.json`
and `spec/component-lock.schema.json`; this document is their companion,
not a substitute for them.

## The seven components

Axolotl, Inspect AI, MLflow, vLLM, Temporal, the organization's existing
LiteLLM gateway, and Argilla (Section 77.1). Seven logical components, not a
requirement for seven servers, seven dashboards, or seven newly installed
services. Exactly these seven keys are frozen in `architecture.schema.json`'s
`componentKey` enum -- no eighth component may be added, and none of the
seven may be dropped or substituted, without a revision to that schema file
under contract lock (Section 84.3).

## Integration record ownership (Section 77.5)

- **ComponentBinding**: component key, organization/security-zone scope,
  optional project scope, deployment mode (`internal_service`,
  `worker_package`, or `existing_gateway`), approved endpoint or worker
  profile, secret reference, network-policy identity, health
  result/timestamp and compatibility-lock reference. Worker packages have no
  invented API URL -- `architecture.schema.json` enforces this structurally
  for Axolotl and Inspect AI, which are worker-executed software, not
  assumed always-on HTTP services (Section 77.1).
- **ComponentVersionLock**: component/package/image versions and digests,
  dependencies, configuration digest, supported API versions, source/license
  evidence and qualification status. Exact versions are installation inputs
  resolved by the qualification task, not `latest` tags or fictional example
  hashes (Section 77.5) -- `component-lock.schema.json` rejects any entry
  whose `package_version` is not an exact pinned version or whose
  `image_digest`/`configuration_digest` is not a real content digest.
- **ExternalRunBinding**: tenant/project, Factory run ID, component key,
  external run/version/record IDs, generation/fencing token, reconciliation
  state and last observed integrity hash (Section 77.5). Unique on component
  scope + Factory logical run + generation. Deferred to whichever task first
  wires a live run against one of the seven components -- no live run exists
  yet, so no schema for run instances is frozen in this task (same
  disclosed-scope pattern as FTF-0004's `web` exclusion).

Repository owner paths for these entities: `packages/component-contracts/`,
`packages/integrations/{mlflow,argilla,litellm}/`, `spec/component-lock.schema.json`,
`spec/deployment-profiles.yaml` (Section 77.5). The contract owner registers
these paths before fan-out; generated clients have a single writer.

## No competing authority (Section 77.4)

Release/promotion authority belongs solely to the Factory approval envelope.
A manual registry tag or model alias edit cannot authorize release; drift
invalidates the affected approval. `architecture.schema.json`'s
`release_approval_authorities` array is frozen to exactly one entry, whose
`authority_id` must equal `factory_approval_envelope` and whose
`component_key` must be `null` -- a vendor component (e.g. MLflow) can never
hold release approval authority, since user-editable vendor tags are not
acceptance evidence.

| State | Authority | Projection / integration rule |
|---|---|---|
| Mandate, source grants, role assignments, promotion policy | Factory PostgreSQL and signed policy snapshots | Vendor systems receive the minimum scoped references; cannot expand a grant |
| Training/evaluation metrics and model catalog metadata | Measured producer results with MLflow records | Store external run/version IDs and immutable evidence hashes; user-editable tags are not acceptance evidence |
| Production permission and actual route | Factory approval envelope plus observed target/LiteLLM/vLLM compatibility tuple | A manual registry tag or model alias edit cannot authorize release; drift invalidates the affected approval |

(Full seven-row table: Section 77.4.)

## UI calls the Factory API, never a vendor dashboard (Section 77.6)

The browser calls Factory routes only for ordinary work. Factory
authentication, authorization, CSRF protection, request validation, rate
limits and audit apply before any downstream call or worker job. No browser
holds LiteLLM management, MLflow administrator or Argilla service
credentials. Native vendor dashboards are optional, audited operator
surfaces -- never steps in the normal user journey and never a substitute
for the Factory UI. Long-running work starts from a durable Temporal
workflow, not directly from the browser (Section 77.2).

## Component responsibility split (Section 77.3)

| Component | Reuse | Factory remains responsible for |
|---|---|---|
| Axolotl | Supported model training, preprocessing and adapter/checkpoint facilities | Authorized data, approved method, token-mask validation, exact recovery qualification, budgets, integrity, lifecycle |
| Inspect AI | Evaluation tasks, scoring, agent integration and evaluation logs | Runtime parity, sealed-test permissions, deterministic business outcomes, complete accounting, statistics and production gates |
| MLflow | Experiment tracking and model-registry records | Policy, immutable evidence, purpose isolation, authoritative release approvals and reconciliation |
| vLLM | Open-weight inference | Artifact/template/parser identity, authentication, tenancy, capacity and release-controlled rollout |
| Temporal | Durable workflows and activity execution | Workflow definitions, outbox/idempotency/fencing, quotas, compensation and actual resource cleanup |
| Existing LiteLLM | Organizational inference access and routing | Current access checks, complete upstream privacy validation, route identity, candidate aliases and actual product adoption |
| Argilla | Review records, annotations and curation | Data grants, reviewer identity, immutable export, split authorization and final-test isolation |

## Deployment profiles

The four supported placement profiles (`INTERNAL_COMPACT`,
`INTERNAL_PRODUCTION`, `AIR_GAPPED`, `CONNECTED_MANAGED`) and their permitted
claims are frozen in `spec/deployment-profiles.yaml`, machine-readable per
Section 79.2. Seven components do not mean seven servers (Section 79.1) --
placement, deployable units, resource pools and physical machines are
tracked separately from logical component ownership.
