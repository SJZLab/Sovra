# ADR 0001 — Seven-component reuse architecture freeze

Status: PROPOSED (pending FTF-R001 execution and owner attestation)

## Decision
Sovra (Model Factory) reuses seven components rather than building equivalents:
Axolotl (training), Inspect AI (evaluation), MLflow (tracking/registry), vLLM
(serving), Temporal (workflow), LiteLLM (existing gateway — not replaced), and
Argilla (review/annotation). Factory owns the adapter, policy, and evidence
layer around each. Full rationale and integration form: see
docs/architecture/ARCHITECTURE.md and docs/architecture/REFERENCE_REPOSITORIES.md.

## Consequences
- FTF-R001 through FTF-R018 (18 reuse_architecture tasks) implement and prove
  this decision; it is not re-litigated per task.
- Any proposal to swap, fork, or change a reused component's integration form
  is a hard stop (CLAUDE.md "Ask owner first").
- OpenHands is explicitly out of the runtime architecture (optional
  implementation-plane choice only) — the Factory must fully operate without it.

## SOURCE_ROOTS status
Not yet set — repo is fresh. To be filled once FTF-0001 lands (see
scripts/product_truth_check.py and CLAUDE.md "Setup gaps").
