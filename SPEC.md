# Model Factory Product Specification

## 1. Product definition

The Fine-Tuning + Evaluation Factory is a standalone reusable web platform that finds the lowest-cost model that satisfies a target agent product's locked quality, safety, privacy, latency, reliability, and operational requirements.

The product supports:

- existing models available through the organization's LiteLLM gateway;
- local, private-cloud, and BYOC open-weight models;
- on-demand GPU training;
- managed customization where an approved provider actually supports it;
- closed models for baseline, teacher, judge, and production comparison roles where policy permits;
- zero-data-egress/private profiles;
- normal UI-only operation after administrator onboarding.

## 2. Independent input classes

An agentic project has three independent inputs:

| Input | Meaning | Factory control |
|---|---|---|
| Target product/repository | The real agent and its runtime behavior | Read-only connector, immutable source snapshot, discovery evidence, runtime harness |
| Models | Current, baseline, trainable, teacher, judge, or candidate routes | Capability/availability/role separation, route identity, compatibility tuple |
| Datasets | Approved examples, traces, cases, and held-out tests | Explicit source connection, purpose grants, immutable versions and splits |

The Factory must never collapse these into one dataset or one model name.

## 3. Required product capabilities

| ID | Capability |
|---|---|
| CAP-01 | Private UI/API, enterprise identity, tenancy, and audit |
| CAP-02 | LiteLLM discovery, inference, and scoped registration |
| CAP-03 | Read-only private GitHub and local-repository connectors |
| CAP-04 | Python and JS/TS discovery with explicit manual correction |
| CAP-05 | Actual target runtime, mandate, and Agent Manifest |
| CAP-06 | Existing endpoint, weights, adapter, and continuation paths |
| CAP-07 | JSONL/JSON/CSV/XLSX, S3-compatible, PostgreSQL, REST, and trace data sources |
| CAP-08 | Four-way data-purpose isolation and source lifecycle |
| CAP-09 | Real local NVIDIA LoRA/QLoRA and private Kubernetes workers |
| CAP-10 | One real managed customization provider |
| CAP-11 | Deterministic, agent, and judge evaluation with sealed final qualification |
| CAP-12 | Registry, private vLLM, and target/LiteLLM adoption |
| CAP-13 | Cheapest tested passing selection and billing reconciliation |
| CAP-14 | Strict private security boundary |
| CAP-15 | Availability, scale, upgrades, recovery, backups, and operator readiness |
| CAP-16 | Complete UI journeys and accessibility |
| CAP-17 | Safe parallel implementation and protected CI |
| CAP-18 | At least one independently qualified fine-tuned output |
| CAP-19 | Seven selected components behind versioned Factory adapters |
| CAP-20 | Internal runtime and explicit disconnected deployment profile |
| CAP-21 | Unified Factory UX and governance despite reused component engines |

## 4. Non-negotiable rules

1. No production mocks, fake integrations, placeholder routes, simulated progress, fake metrics, or dummy model artifacts.
2. Required tests cannot be skipped or xfailed.
3. Every shipped provider or compute adapter needs a real integration test.
4. The browser never receives vendor administration credentials.
5. Training, validation, development evaluation, and final qualification data are separate, versioned, purpose-restricted, and hashed.
6. Held-out evaluation data cannot be used for training, tuning, synthetic generation, or early stopping.
7. Training completion does not authorize promotion.
8. A critical regression blocks promotion.
9. Production evaluation must run against the actual target-agent harness for an agentic project.
10. A material source or manifest change invalidates affected evidence until requalification.
11. Dataset permissions are explicit; the Factory never silently harvests repository, production, database, storage, or service data.
12. Inference availability does not imply training capability.
13. A provider failure, outage, or unknown state must remain visible as blocked, pending, or failed—not converted to success.
14. An air-gap profile must cover the complete offline lifecycle and must separately test denial of connected-only capabilities.
15. The reference Factory qualification needs at least one real fine-tuned artifact that passes final qualification and is usable through the actual target and LiteLLM route.

## 5. Model capability model

Capability is independent from availability and project role:

| Dimension | Values |
|---|---|
| Capability | trainable_open_weight, managed_fine_tunable, inference_only, unknown |
| Availability | authorized_and_healthy, stale, revoked, unavailable |
| Project role | current_production, baseline, candidate, teacher, judge |

The selectable model set is the intersection of the credential-authorized list, project allowlist, and privacy allowlist. Metadata cannot expand authorization.

## 6. Project modes

| Mode | Repository requirement | Result label |
|---|---|---|
| agentic_product | Required before production qualification | Agent-product qualified only after actual runtime evidence |
| generic_model_tuning | Optional | Must be labeled not agent-product qualified |

## 7. Reference qualification floor

The reference domain is a real runnable agent product with stateful isolated tools. The source specification calls for genuine training and evaluation data, including at least 1,000 approved training traces, 150 validation traces, 250 development cases, and a final suite of at least 1,000 independent tasks with critical/adversarial coverage. These are qualification requirements, not evidence that they already exist.

## 8. Supported profiles

- INTERNAL_COMPACT
- INTERNAL_PRODUCTION
- AIR_GAPPED
- CONNECTED_MANAGED

Profiles change placement and permitted connected capabilities; they do not silently remove required product scope.

## 9. Explicit non-shipped extensions

The Release 1 baseline does not silently claim additional cloud provisioners, GitLab, unrestricted arbitrary databases, generic code generation for arbitrary frameworks, SGLang serving, preference/RL training, or cross-boundary metadata control planes. Adding one requires an explicit compatibility policy and real tests.

