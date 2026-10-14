# ADR 0004 — Small task-specific models: classifier-head training and pooling-runner serving (denominator 302 → 304)

- Status: Accepted, 2026-10-14
- Decided by: owner (live instruction, 2026-10-14, approving three ScaleDown-derived ideas, including the
  Axolotl/vLLM integration-form change — "Add item 3 too" after being told it changes how two reused
  components are integrated), recorded by Control plane session `session_01QVznorVMjnmcttUhyDnoGR`

## Context
- The product objective is the cheapest agent that passes every gate (FINE_TUNING_FACTORY_RELEASE_1.md §1;
  §0 item 19: "Cost is measured as cost per successful agent task"). Routine agent sub-steps (classify,
  extract, summarize) often run on the same large model as reasoning.
- The owner reviewed ScaleDown (task-specific small models, encoder-only and constrained decoding) and
  approved three ideas: (1) route an agent's routine sub-steps through the existing LiteLLM gateway to a
  small fine-tuned model while large models keep reasoning, proven by cost per successful agent task;
  (2) vLLM guided/structured decoding so extract/classify outputs are a few tokens; (3) encoder /
  sequence-classification-head models trained by the Factory and served by vLLM's pooling/classification
  runner. ScaleDown's hosted API is not used (keyed external service, breaks ZERO_EGRESS). We import the
  ideas, not the vendor; its price is a vendor quote, never Factory evidence.
- Ideas 1 and 2 are existing scope (§3.4–3.5, §29, §31–32, §36–37, §66.3–66.4). They become child slices
  FTF-1304-S1 and FTF-0213-S1 of certified tasks. Child slices add no row to the denominator.
- Idea 3 has no owner in the frozen 300: §13 method support is lora|qlora, §34 packaging and the §68.2 loss
  contract are causal-LM shaped, §36 limits clients to chat unless "an explicit supported adapter" exists,
  and §78.1 says "A direct TRL trainer is not a silent fallback; a documented compatibility gap requires
  separately reviewed implementation". Axolotl has no documented N-label sequence-classification path today
  (its only seq-cls path is the 1-label `reward_model` head); this is verified again at pin time.
- Nothing is pinned yet: uv.lock has no vllm/axolotl/transformers/torch, spec/component-maintenance.yaml
  records `NOT_YET_RESOLVED`, and spec/compatibility-lock.json has gpu_driver and litellm_gateway
  `BLOCKED_EXTERNAL`. Every runner or parameter capability is therefore a probed, version-gated property
  that fails closed, never an assumption.

## Decision
Add two tasks, each with its own contract:
- **FTF-0708A — Sequence-classification-head training (`seq_cls_lora`)**, `spec/addenda/FTF-0708A.json`.
- **FTF-1004A — Classifier serving via vLLM pooling runner behind LiteLLM**, `spec/addenda/FTF-1004A.json`.

The Release 1 denominator becomes 304. `docs/spec-matrix.yaml` is the denominator's source of truth, and
`scripts/status.py` reads it.

Integration-form change (owner-approved; recorded in `docs/architecture/REFERENCE_REPOSITORIES.md` §2):

| Component | Change | Unchanged |
|---|---|---|
| Axolotl | For training method `seq_cls_lora` only, the training worker runs a Hugging Face Transformers `Trainer` + PEFT (`task_type=SEQ_CLS`, head in `modules_to_save`) on `AutoModelForSequenceClassification`, using the same pinned PyTorch/Transformers/PEFT/Accelerate tuple that Axolotl's lock resolves (§4, §77.1). If the pinned Axolotl release documents N-label sequence classification, Axolotl is used instead. | Same worker and FTF-R003 supervisor (process-group supervision, `AXOLOTL_DO_NOT_TRACK`/`DO_NOT_TRACK`/`HF_HUB_OFFLINE=1`, network denial, fencing, checkpoint hash manifest, MLflow sync). Chat LoRA/QLoRA stays Axolotl-only. No optimizer loop is written. |
| vLLM | A second runner mode: the pooling runner with the classify task (flag names depend on the pinned version) serves `*ForSequenceClassification` artifacts. Client protocol is vLLM `/classify` (and `/pooling`), declared as an explicit supported adapter per §36. The serving artifact is a merged full-weight classifier. | Private, identity-pinned, admin surface private, `VLLM_NO_USAGE_STATS=1`, readiness = intended artifact + uncached probe. |
| LiteLLM | None. A classification-capable route on the organization's gateway is needed; if the gateway has none, the Factory shows `BLOCKED_ADMIN_ACTION` (§38) and never bypasses the gateway. | Least-privilege inference key, route identity per call. |
| Target harness (FTF-04A25) | A binding key may declare protocol `classification`; an encoder artifact binds only to such a key. Rebinding a chat-protocol step to a classifier is a target-code change: `INTEGRATION_REQUIRED`, never an automatic swap (§38, §66.4). | Binding changes only model routes. |

## Consequences
- `spec/tasks.json` and `tools/r1_handoff.py` stay frozen at 300 (ADR 0002). Addendum contracts live in
  `spec/addenda/<ID>.json`.
- Both addenda are blocked at acceptance: FTF-0708A on FTF-R003, FTF-0701, FTF-0712, FTF-0713 and FTF-0519
  (and a real GPU for the qualified run); FTF-1004A on FTF-1003 and FTF-1005. FTF-1004A does not depend on
  FTF-0708A: an imported, approved full-weight classifier (§68.1) is servable, so they land in either order.
- No new runtime dependency, provided the classifier trainer uses exactly the transformers/peft/torch/
  accelerate/safetensors versions in the pinned Axolotl lock. Needing any version or library outside that
  tuple (for example `sentencepiece`/`protobuf` for a DeBERTa-v3 tokenizer) is an ask-first new runtime
  dependency. `datasets`, `evaluate` and `scikit-learn` are not added (metrics are Factory code). CI evidence
  may install a pinned vLLM CPU build inside one job only (FTF-R005 MLflow precedent).
- Guided-decoding backends ship inside the pinned vLLM image; they are not Factory dependencies, but they
  must be in the offline bundle (§79.3) and grammar compilation must pass the egress-denied test.
- FTF-0701, when designed, reserves training method value `seq_cls_lora` in the 07 domain schema; validation
  refuses it until FTF-0708A is CERTIFIED. `seq_cls_lora` is never a silent fallback for a chat LoRA job.
- Held-out rules (Law 12): per-step examples carry the parent agent-task id as their split group key;
  teacher labels use development/source data only; early stopping, label thresholds, cascade confidence and
  the choice of routed steps are tuned on validation/development data only.
- Until FTF-1004A and FTF-0708A land, the default route for classify/extract steps is a small decoder LoRA
  (FTF-0708/0709) plus constrained choice (idea 2) through the normal chat route.
- Any further addendum needs the same owner decision and an ADR.
