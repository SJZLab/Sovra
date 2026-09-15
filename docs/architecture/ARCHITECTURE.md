# Model Factory Architecture

## 1. Logical architecture

The normal data/control path is:

Authorized user → Unified Factory UI → Factory API and policy → Factory PostgreSQL and transactional outbox → self-hosted Temporal → controlled workers and deployment controller.

Worker and integration paths:

- Data/review workers → Argilla and private immutable artifact storage.
- Training worker → Axolotl → verified checkpoint/model artifacts.
- Evaluation worker → Inspect AI → actual target application and isolated real tools.
- Target application → existing LiteLLM gateway → private vLLM or approved internal endpoints.
- Tracking/registry adapter → self-hosted MLflow plus controlled artifact storage.
- Deployment controller → private vLLM management and the existing LiteLLM registration surface.
- Approval service → Factory approval envelope bound to the observed compatibility tuple.

The browser is not a workflow engine. Long-running work begins from durable admission and Temporal workflows, not from an open browser tab.

## 2. Seven logical components

| Component | Form | Reused responsibility | Factory-owned responsibility |
|---|---|---|---|
| Axolotl | Worker package | Training, preprocessing, adapters, checkpoints | Authorized data, configs, masks, budgets, recovery, integrity, lifecycle |
| Inspect AI | Worker package | Evaluation tasks, scoring, agent bridge, logs | Runtime parity, sealed permissions, business outcomes, statistics, gates |
| MLflow | Internal service | Tracking and registry records | Immutable evidence, policy, tenant isolation, approvals, reconciliation |
| vLLM | Internal service | Open-weight inference | Artifact/template/parser identity, tenancy, capacity, rollout |
| Temporal | Internal service | Durable workflow history and activities | Workflow contracts, outbox, idempotency, fencing, quotas, cleanup |
| Existing LiteLLM | Existing gateway | Model access and inference routing | Discovery, route identity, privacy, adoption, registration authority |
| Argilla | Internal service | Review records, annotation, curation | Reviewer identity, grants, immutable export, split isolation |

Seven components do not mean seven servers. Axolotl and Inspect run as worker packages/jobs; Temporal, MLflow, and Argilla have services and backing dependencies; vLLM runs on model-serving workers; LiteLLM remains the organization's existing gateway.

## 3. Sources of truth

| State | Authoritative owner |
|---|---|
| Mandate, grants, roles, promotion policy | Factory PostgreSQL and signed policy snapshots |
| Paid intent, admission, reservations | Factory ledger and transactional outbox |
| Workflow progress | Temporal execution history |
| Measured metrics and catalog mappings | Producer results plus MLflow records |
| Dataset/model/checkpoint/evaluation bytes | Integrity-checked private artifact storage |
| In-progress annotation | Argilla plus Factory reviewer/audit mapping |
| Final qualification | Restricted Factory qualification enclave and signed evidence |
| Production permission and route | Factory approval envelope plus observed target/LiteLLM/vLLM compatibility tuple |

MLflow tags, Argilla edits, mutable aliases, model judgments, user-provided scripts, and agent self-reports cannot become a competing authority.

## 4. Stable Factory integration entities

- ComponentBinding
- ComponentVersionLock
- ExternalRunBinding
- ComponentSyncOutbox
- DeploymentProfile
- ImplementationExecutorDecision
- TargetApplicationConnection
- TargetSourceSnapshot
- AgentManifest and ManifestEvidence
- DatasetSourceConnection
- DatasetSplitManifest
- EvaluationSuite and EvaluationCase
- TrainingExperiment and TrainingRun
- ModelVersion
- EvaluationRun and EvaluationCaseResult
- PromotionPolicy and PromotionDecision
- Deployment
- CostProfile
- AuditEvent

All tenant-scoped state uses explicit organization identity, row-level security, composite constraints, versioning, and append-only audit where required.

## 5. Adapter rules

Every adapter must:

1. Receive a typed, signed, policy-bound Factory job specification.
2. Use a pinned component/version/compatibility tuple.
3. Resolve only approved local or private artifacts.
4. Reject shell injection, unrestricted URLs, unapproved remote code, external callbacks, and undeclared packages.
5. Record real external IDs, hashes, versions, and measured outcomes.
6. Preserve pending, failed, cancelled, not-run, and incomplete states.
7. Recover through idempotency, fencing, reconciliation, and compensation.
8. Keep final-test content outside ordinary implementation, Argilla, or MLflow paths unless explicitly permitted.

## 6. Deployment profiles and boundaries

| Profile | Meaning |
|---|---|
| INTERNAL_COMPACT | Qualified compact internal placement |
| INTERNAL_PRODUCTION | Highly available internal placement and serving protection |
| AIR_GAPPED | Complete disconnected lifecycle from approved mirrors/bundles |
| CONNECTED_MANAGED | Internal Factory with explicitly approved connected model/training/repository services |

Sensitive processing belongs inside the declared boundary: UI/API session handling, repository analysis, dataset parsing, training, evaluation, Temporal histories, database, artifacts, audit, and observability.

The deployment inventory must include supporting dependencies: PostgreSQL, artifact storage, identity, secrets, DNS/time/CA, observability, mirrors, CI, and Argilla search/background dependencies. They are not optional merely because they are not user-facing.

## 7. Runtime independence

OpenHands is an optional implementation-plane choice. It is absent from the Factory runtime architecture. The completed Factory must operate, train, evaluate, recover, serve, adopt, roll back, and restore when OpenHands is absent or shut down.

## 8. Vertical slice

The first real end-to-end slice is:

Target repository → immutable source snapshot → Agent Manifest → explicitly mapped data → real small training → actual target-runtime evaluation → private serving → LiteLLM call → evidence-bound adoption.

An interface-only demo, borrowed training result, fake evaluation, or MLflow-only approval does not satisfy this slice.

