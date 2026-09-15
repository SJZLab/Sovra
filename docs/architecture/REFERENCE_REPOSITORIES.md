# Model Factory Reference Repositories and Sources

## 1. How to read this document

This is the reference-source map for the selected implementation architecture. It is not a claim that the repositories are already installed, pinned, forked, deployed, or qualified.

Exact deployed versions, image digests, package locks, licenses, support owners, telemetry settings, and compatibility results must be recorded in the Factory deployment component lock and qualified against the actual environment. Never use a floating latest tag as the Factory contract.

## 2. Selected runtime components

| Component | Canonical upstream source | Factory integration form | Reference documentation |
|---|---|---|---|
| Axolotl | https://github.com/axolotl-ai-cloud/axolotl | Pinned worker package | https://docs.axolotl.ai/docs/cli.html and https://docs.axolotl.ai/docs/telemetry.html |
| Inspect AI | https://github.com/UKGovernmentBEIS/inspect_ai | Pinned evaluation worker package | https://inspect.aisi.org.uk/agent-bridge.html |
| MLflow | https://github.com/mlflow/mlflow | Self-hosted tracking/registry service | https://mlflow.org/docs/latest/self-hosting/ |
| vLLM | https://github.com/vllm-project/vllm | Private model-serving service | https://docs.vllm.ai/en/stable/usage/security/ and https://docs.vllm.ai/en/latest/features/lora/ |
| Temporal | https://github.com/temporalio/temporal | Self-hosted durable workflow service | https://docs.temporal.io/self-hosted-guide and https://docs.temporal.io/production-deployment/data-encryption |
| LiteLLM | https://github.com/BerriAI/litellm | Existing organizational gateway; do not replace automatically | https://docs.litellm.ai/docs/proxy/deploy, https://docs.litellm.ai/docs/proxy/virtual_keys, and https://docs.litellm.ai/docs/proxy/model_management |
| Argilla | https://github.com/argilla-io/argilla | Internal review/annotation service | https://docs.argilla.io/latest/getting_started/how-to-deploy-argilla-with-docker/ |

## 3. Supporting reference sources

| Area | Reference source | Factory rule |
|---|---|---|
| PostgreSQL tenancy/RLS | https://www.postgresql.org/docs/current/ddl-rowsecurity.html | Use database-enforced tenant isolation; application checks alone are insufficient |
| Network policy | https://kubernetes.io/docs/concepts/services-networking/network-policies/ | Verify enforcement in the installed CNI; a YAML file alone is not proof |
| Model templates | https://huggingface.co/docs/transformers/en/chat_templating | Training and serving must share the qualified serialization contract |
| SFT training | https://huggingface.co/docs/trl/en/sft_trainer | Use only within the approved Axolotl compatibility profile |
| PEFT checkpoints | https://huggingface.co/docs/peft/en/developer_guides/checkpoint | Adapter metadata is not automatically a complete resumable state |
| Managed customization | https://docs.aws.amazon.com/bedrock/latest/APIReference/API_CreateModelCustomizationJob.html | Validate actual account/model capability before admission |
| Model lifecycle | https://docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle.html | Availability and fine-tuning support can change; discover and verify |
| S3-compatible storage alternative | https://github.com/seaweedfs/seaweedfs | Qualify exact multipart, checksum, versioning, retention, encryption, backup, and restore behavior |
| Existing storage compatibility | https://github.com/minio/minio | Do not make a new archived-MinIO deployment the automatic default; qualify the organization's approved S3 service |
| CI security | https://docs.github.com/en/actions/reference/security/secure-use | Verify protected repository settings, immutable actions, isolated runners, and signing |
| File upload security | https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html | Parse and store untrusted uploads in restricted paths |
| SSRF prevention | https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html | Validate destinations and network policy, not just user-entered URLs |
| Statistical confidence | https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm | Report estimator, confidence level, assumptions, and denominator |
| Accessibility | https://www.w3.org/TR/WCAG22/ | Qualify applicable WCAG 2.2 AA behavior with automated and manual checks |

## 4. Reference repository policy

For every dependency, record:

- exact upstream source repository and commit/tag;
- package/image version and digest;
- license and source notice;
- security/vulnerability status;
- telemetry and outbound behavior;
- owner and support lifecycle;
- offline mirror or bundle location where required;
- upgrade and rollback evidence;
- Factory adapter compatibility result.

Documentation, a repository landing page, or a marketing claim is not integration evidence.
