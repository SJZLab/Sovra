/bin/bash: line 51: warning: here-document at line 1 delimited by end-of-file (wanted `PY')
# Model Factory Release 1 Queue

## Canonical meaning

This is the complete Factory queue index. It contains 300 frozen required work-item records plus 1 owner-approved addendum (FTF-1206A, docs/adr/0002, contract in spec/addenda/; denominator 301): 246 retained feature tasks, 36 hardening tasks, and 18 reuse-architecture tasks. The complete task contracts define the acceptance, negative-acceptance, ownership, dependency, and verification rules for every row.

## Queue rules

A task is READY only when its contract, exact scope/files, positive and negative tests, prerequisites, and verification commands are known. DONE requires a merged change, independent review, and relevant evidence. The queue is dependency-driven; numeric task order is not a waterfall. A task title alone never authorizes implementation.

| Kind | Count | Meaning |
|---|---:|---|
| retained_feature | 246 | Product and retained implementation work |
| audit_hardening | 36 | Security, privacy, evidence, recovery, and release hardening |
| reuse_architecture | 18 | Binding integration of the seven selected components and runtime profiles |
| **Total** | **300** | **Release 1 required records** |

## Complete task index

| ID | Kind | Title | Dependencies | Requirements | Scenarios | Owner |
|---|---|---|---|---|---|---|

| FTF-0001 | retained_feature | Initialize monorepo |  | AF-31, AF-35 | Q01, Q26 | FTF-0001 |
| FTF-0002 | retained_feature | Add formatting/lint/type checks | FTF-0001 | AF-31, AF-35 | Q01, Q26 | FTF-0001 |
| FTF-0003 | retained_feature | Add test harnesses | FTF-0001, FTF-0002 | AF-20, AF-31, AF-35 | Q01, Q26 | FTF-0001 |
| FTF-0004 | retained_feature | Docker local stack | FTF-0001, FTF-0003, FTF-0005 | CAP-01 | Q01, Q26 | FTF-0001 |
| FTF-0005 | retained_feature | Configuration system | FTF-0001 | AF-01, AF-02, AF-10, AF-36 | Q01, Q26 | FTF-0001 |
| FTF-0006 | retained_feature | Observability foundation | FTF-0001, FTF-0004, FTF-0005 | CAP-01 | Q01, Q26 | FTF-0001 |
| FTF-0101 | retained_feature | OIDC login | FTF-0004, FTF-0005, FTF-0102 | AF-33 | Q01, Q24 | FTF-0102 |
| FTF-0102 | retained_feature | Organization/workspace schema | FTF-0004, FTF-0005, FTF-H001 | AF-08 | Q01, Q24 | FTF-0102 |
| FTF-0103 | retained_feature | RBAC middleware | FTF-0101, FTF-0102 | AF-08, AF-33 | Q01, Q24 | FTF-0102 |
| FTF-0104 | retained_feature | Audit event writer | FTF-0102 | AF-08, AF-33 | Q01, Q24 | FTF-0102 |
| FTF-0105 | retained_feature | Tenant-isolation query guards | FTF-0102, FTF-0103 | CAP-01 | Q01, Q24 | FTF-0102 |
| FTF-0106 | retained_feature | RBAC/IDOR security tests | FTF-0102, FTF-0103, FTF-0104, FTF-0105, FTF-H003 | CAP-01 | Q01, Q24 | FTF-0102 |
| FTF-0201 | retained_feature | LiteLLM connection CRUD | FTF-0103, FTF-0104, FTF-0005 | AF-10 | Q02, Q21 | FTF-0201 |
| FTF-0202 | retained_feature | Secret-manager integration | FTF-0005, FTF-0103, FTF-0201 | AF-10 | Q02, Q21 | FTF-0201 |
| FTF-0203 | retained_feature | Connection validation | FTF-0201, FTF-0202, FTF-H006 | AF-05 | Q02, Q21 | FTF-0201 |
| FTF-0204 | retained_feature | `/v1/models` discovery | FTF-0201, FTF-0203 | AF-03, AF-04 | Q02, Q21 | FTF-0201 |
| FTF-0205 | retained_feature | `/v1/model/info` enrichment | FTF-0201, FTF-0203, FTF-0204 | AF-03, AF-04 | Q02, Q21 | FTF-0201 |
| FTF-0206 | retained_feature | `/model_group/info` optional enrichment | FTF-0201, FTF-0203, FTF-0204 | CAP-02 | Q02, Q21 | FTF-0201 |
| FTF-0207 | retained_feature | Alias reconciliation | FTF-0201, FTF-0204, FTF-0205 | AF-03, AF-04 | Q02, Q21 | FTF-0201 |
| FTF-0208 | retained_feature | Last-known-good catalog | FTF-0201, FTF-0207 | AF-03, AF-04 | Q02, Q21 | FTF-0201 |
| FTF-0209 | retained_feature | Catalog anomaly detector | FTF-0201, FTF-0208 | AF-03, AF-04 | Q02, Q21 | FTF-0201 |
| FTF-0210 | retained_feature | Real chat probe | FTF-0201, FTF-0204, FTF-0207 | AF-05 | Q02, Q21 | FTF-0201 |
| FTF-0211 | retained_feature | Real streaming probe | FTF-0201, FTF-0210 | CAP-02 | Q02, Q21 | FTF-0201 |
| FTF-0212 | retained_feature | Real tool-call probe | FTF-0201, FTF-0210 | CAP-02 | Q02, Q21 | FTF-0201 |
| FTF-0213 | retained_feature | Real structured-output probe | FTF-0201, FTF-0210 | CAP-02 | Q02, Q21 | FTF-0201 |
| FTF-0214 | retained_feature | Model catalog UI | FTF-0201, FTF-0207, FTF-0208, FTF-0209, FTF-0210 | CAP-02 | Q02, Q21 | FTF-0201 |
| FTF-0215 | retained_feature | Periodic sync workflow | FTF-0004, FTF-0201, FTF-0207, FTF-0208, FTF-0209 | AF-05 | Q02, Q21 | FTF-0201 |
| FTF-0301 | retained_feature | TrainableArtifact schema/API | FTF-0103, FTF-0104 | CAP-06 | Q07, Q10 | FTF-0301 |
| FTF-0302 | retained_feature | Hugging Face artifact resolver | FTF-0202, FTF-0301, FTF-H006 | CAP-06 | Q07, Q10 | FTF-0301 |
| FTF-0303 | retained_feature | Private S3 artifact resolver | FTF-0202, FTF-0301, FTF-H006 | CAP-06 | Q07, Q10 | FTF-0301 |
| FTF-0304 | retained_feature | Local artifact resolver | FTF-0202, FTF-0301 | CAP-06 | Q07, Q10 | FTF-0301 |
| FTF-0305 | retained_feature | License acceptance workflow | FTF-0301 | CAP-06 | Q07, Q10 | FTF-0301 |
| FTF-0306 | retained_feature | Base model revision/digest resolver | FTF-0301, FTF-0302, FTF-0303, FTF-0304, FTF-0305 | AF-14 | Q07, Q10 | FTF-0301 |
| FTF-0307 | retained_feature | Training capability classifier | FTF-0207, FTF-0212, FTF-0301, FTF-0306 | AF-14 | Q07, Q10 | FTF-0301 |
| FTF-0308 | retained_feature | Model-to-artifact mapping UI | FTF-0214, FTF-0301, FTF-0307 | AF-14 | Q07, Q10 | FTF-0301 |
| FTF-0309 | retained_feature | Capability compatibility UI | FTF-0301, FTF-0307, FTF-0308 | CAP-06 | Q07, Q10 | FTF-0301 |
| FTF-0310 | retained_feature | Capability contract tests | FTF-0301, FTF-0307, FTF-0309 | CAP-06 | Q07, Q10 | FTF-0301 |
| FTF-0401 | retained_feature | Project schema/API/UI | FTF-0102, FTF-0103, FTF-0104 | CAP-05 | Q01, Q04, Q05 | FTF-0401 |
| FTF-0402 | retained_feature | Project security profile | FTF-0401 | CAP-05 | Q01, Q04, Q05 | FTF-0401 |
| FTF-0403 | retained_feature | Budget policy | FTF-0104, FTF-0401 | AF-22 | Q01, Q04, Q05 | FTF-0401 |
| FTF-0404 | retained_feature | Tool schema CRUD | FTF-0401 | CAP-05 | Q01, Q04, Q05 | FTF-0401 |
| FTF-0405 | retained_feature | JSON Schema tool validation | FTF-0401, FTF-0404 | CAP-05 | Q01, Q04, Q05 | FTF-0401 |
| FTF-0406 | retained_feature | Side-effect classification | FTF-0401, FTF-0404, FTF-0405 | CAP-05 | Q01, Q04, Q05 | FTF-0401 |
| FTF-0407 | retained_feature | Critical behavior requirements | FTF-0401, FTF-0406 | CAP-05 | Q01, Q04, Q05 | FTF-0401 |
| FTF-0408 | retained_feature | Requirements UI | FTF-0401, FTF-0402, FTF-0403, FTF-0407 | AF-30 | Q01, Q04, Q05 | FTF-0401 |
| FTF-04A01 | retained_feature | TargetApplicationConnection schema/API/RBAC | FTF-0401, FTF-0103, FTF-0202, FTF-H006 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A02 | retained_feature | GitHub App/token read-only secret integration | FTF-0202, FTF-04A01 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A03 | retained_feature | GitHub accessible-repository selector UI | FTF-04A01, FTF-04A02 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A04 | retained_feature | GitHub branch/ref selector + immutable SHA resolver | FTF-04A01, FTF-04A03 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A05 | retained_feature | GitHub source snapshot implementation | FTF-04A01, FTF-04A04, FTF-04A07 | AF-09, AF-11 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A06 | retained_feature | Local repository connector with root allowlist/path protections | FTF-04A01, FTF-04A07 | AF-09, AF-11 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A07 | retained_feature | TargetSourceSnapshot schema + immutable provenance | FTF-04A01 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A08 | retained_feature | Source-analysis worker foundation | FTF-04A01, FTF-04A07, FTF-04A17 | AF-09, AF-11 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A09 | retained_feature | Python deterministic agent/config/tool discovery | FTF-04A01, FTF-04A08 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A10 | retained_feature | TypeScript/JavaScript deterministic agent/config/tool discovery | FTF-04A01, FTF-04A08 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A11 | retained_feature | JSON/YAML/TOML configuration discovery | FTF-04A01, FTF-04A08 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A12 | retained_feature | LiteLLM/current-model binding discovery | FTF-0207, FTF-04A01, FTF-04A09, FTF-04A10, FTF-04A11 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A13 | retained_feature | Prompt-source discovery + evidence capture | FTF-04A01, FTF-04A09, FTF-04A10, FTF-04A11 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A14 | retained_feature | Tool/schema/permission discovery + evidence capture | FTF-0405, FTF-0406, FTF-04A01, FTF-04A09, FTF-04A10, FTF-04A11 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A15 | retained_feature | Agent test/evaluation fixture discovery | FTF-04A01, FTF-04A09, FTF-04A10, FTF-04A11 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A16 | retained_feature | Runtime trace/deployment contract discovery | FTF-04A01, FTF-04A09, FTF-04A10, FTF-04A11 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A17 | retained_feature | DiscoveredAgentCandidate schema/API | FTF-04A01, FTF-04A07 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A18 | retained_feature | Candidate-selection UI — no auto-selection | FTF-04A01, FTF-04A12, FTF-04A13, FTF-04A14, FTF-04A15, FTF-04A16, FTF-04A17 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A19 | retained_feature | AgentManifest schema/versioning/hash | FTF-0405, FTF-0407, FTF-04A01, FTF-04A07 | AF-09, AF-11 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A20 | retained_feature | ManifestEvidence schema/API | FTF-04A01, FTF-04A19 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A21 | retained_feature | Manifest review/edit UI with per-field evidence | FTF-04A01, FTF-04A18, FTF-04A19, FTF-04A20 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A22 | retained_feature | Mandate editor and conflict-resolution UI | FTF-0408, FTF-04A01, FTF-04A21 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A23 | retained_feature | Manifest validation engine | FTF-0307, FTF-04A01, FTF-04A21, FTF-04A22, FTF-04A25, FTF-H008 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A24 | retained_feature | Manifest lock/supersede workflow | FTF-04A01, FTF-04A19, FTF-04A20, FTF-04A23 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A25 | retained_feature | Production-faithful target-agent harness contract | FTF-0405, FTF-04A01, FTF-04A19 | AF-12 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A26 | retained_feature | Isolated real tool-backend adapter contract tests | FTF-04A01, FTF-04A25, FTF-1702 | AF-12 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A27 | retained_feature | Repository sync + manifest drift detector | FTF-04A01, FTF-04A05, FTF-04A06, FTF-04A24 | AF-13 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A28 | retained_feature | Requalification-required state machine | FTF-04A01, FTF-04A27 | AF-13 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A29 | retained_feature | Stale-evidence promotion blocker | FTF-04A01, FTF-04A28, FTF-1101 | AF-13 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A30 | retained_feature | Target Product setup E2E UI test | FTF-04A01, FTF-04A18, FTF-04A24, FTF-H008 | AF-30 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A31 | retained_feature | Real private GitHub repository integration test | FTF-04A01, FTF-04A05, FTF-04A18, FTF-H007 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A32 | retained_feature | Local repository integration test | FTF-04A01, FTF-04A06, FTF-H007 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A33 | retained_feature | Repository permission-revocation fail-closed test | FTF-04A01, FTF-04A02, FTF-04A31 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A34 | retained_feature | Monorepo multi-agent discovery test | FTF-04A01, FTF-04A18, FTF-H007 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-04A35 | retained_feature | Unknown/custom-framework manual-resolution test | FTF-04A01, FTF-04A24, FTF-H008 | CAP-05 | Q03, Q04, Q05, Q06 | FTF-04A01 |
| FTF-0500 | retained_feature | DatasetSourceConnection schema/API/RBAC | FTF-0401, FTF-0202, FTF-H003 | AF-15 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0500A | retained_feature | Dataset source selector + purpose-authorization UI | FTF-0500 | AF-15 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0500B | retained_feature | S3/MinIO source validation + metadata preview | FTF-0500, FTF-0500A, FTF-H006 | AF-02, AF-15 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0500C | retained_feature | Database/API/trace source contract and fail-closed validation | FTF-0500, FTF-0500A, FTF-H006 | AF-02, AF-15 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0500D | retained_feature | Dataset source security-boundary preview + audit evidence | FTF-0402, FTF-0500 | AF-16 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0501 | retained_feature | Direct object-storage upload | FTF-0500, FTF-0500D | AF-02, AF-15 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0502 | retained_feature | JSONL importer | FTF-0500, FTF-0501, FTF-0506 | CAP-08 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0503 | retained_feature | JSON importer | FTF-0500, FTF-0501, FTF-0506 | CAP-08 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0504 | retained_feature | CSV importer | FTF-0500, FTF-0501, FTF-0506 | CAP-08 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0505 | retained_feature | XLSX importer | FTF-0500, FTF-0501, FTF-0506 | CAP-08 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0506 | retained_feature | Canonical conversation normalizer | FTF-0405, FTF-0500 | AF-15 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0507 | retained_feature | Tool-trace normalizer | FTF-0500, FTF-0506 | AF-15 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0508 | retained_feature | Record schema validator | FTF-0500, FTF-0506, FTF-0507 | CAP-08 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0509 | retained_feature | Tool argument validator | FTF-0405, FTF-0500, FTF-0508 | CAP-08 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0510 | retained_feature | Tokenization analysis | FTF-0306, FTF-0500, FTF-0508 | CAP-08 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0511 | retained_feature | Exact duplicate detector | FTF-0500, FTF-0508 | CAP-08 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0512 | retained_feature | Normalized duplicate detector | FTF-0500, FTF-0511 | CAP-08 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0513 | retained_feature | Near-duplicate detector | FTF-0500, FTF-0512 | CAP-08 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0514 | retained_feature | PII/secrets detector | FTF-0402, FTF-0500, FTF-0508 | AF-16 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0515 | retained_feature | Quality issue engine | FTF-0500, FTF-0509, FTF-0510, FTF-0511, FTF-0512, FTF-0513, FTF-0514 | AF-16 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0516 | retained_feature | Dataset issue resolution UI | FTF-0500, FTF-0515 | AF-15 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0517 | retained_feature | Group-aware split engine | FTF-0500, FTF-0500D, FTF-0515 | AF-23 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0518 | retained_feature | Split leakage detector | FTF-0500, FTF-0513, FTF-0517 | AF-23 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0519 | retained_feature | Immutable split manifest | FTF-0500, FTF-0516, FTF-0517, FTF-0518 | AF-16, AF-23 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0520 | retained_feature | Dataset report and integrated Argilla review/export UI | FTF-0500, FTF-0515, FTF-0519, FTF-R006 | AF-30 | Q08, Q09, Q31 | FTF-0500 |
| FTF-0601 | retained_feature | Evaluation suite schema/API | FTF-0401, FTF-04A19, FTF-0405 | CAP-11 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0602 | retained_feature | Evaluation case editor/import | FTF-0601 | CAP-11 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0603 | retained_feature | Suite validation/locking | FTF-0519, FTF-0601, FTF-0602 | AF-23 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0604 | retained_feature | Exact-match grader | FTF-0601 | CAP-11 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0605 | retained_feature | JSON-schema grader | FTF-0405, FTF-0601 | CAP-11 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0606 | retained_feature | Tool-name grader | FTF-0601 | CAP-11 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0607 | retained_feature | Tool-argument grader | FTF-0405, FTF-0601 | CAP-11 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0608 | retained_feature | Tool-sequence grader | FTF-0601 | CAP-11 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0609 | retained_feature | Forbidden-action grader | FTF-0406, FTF-0601 | CAP-11 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0610 | retained_feature | Refusal grader | FTF-0407, FTF-0601 | CAP-11 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0611 | retained_feature | Numeric grader | FTF-0601 | CAP-11 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0612 | retained_feature | Latency grader | FTF-0006, FTF-0601 | CAP-11 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0613 | retained_feature | Cost grader | FTF-0403, FTF-0601, FTF-1304 | CAP-11 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0614 | retained_feature | Inspect integration with the actual agent runtime | FTF-0210, FTF-04A25, FTF-04A26, FTF-0601, FTF-R004 | AF-12 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0615 | retained_feature | Loop detector | FTF-0601, FTF-0614 | CAP-11 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0616 | retained_feature | LLM judge adapter through LiteLLM | FTF-0210, FTF-0601 | AF-25 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0617 | retained_feature | Zero-egress judge policy | FTF-0402, FTF-0601, FTF-0616 | AF-25 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0618 | retained_feature | Result aggregation | FTF-0601, FTF-0603, FTF-0604, FTF-0605, FTF-0606, FTF-0607, FTF-0608, FTF-0609, FTF-0610, FTF-0611, FTF-0612, FTF-0613, FTF-0614, FTF-0615, FTF-0617 | AF-24 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0619 | retained_feature | Comparison UI | FTF-0601, FTF-0618 | AF-30 | Q16, Q17, Q18 | FTF-0601 |
| FTF-0701 | retained_feature | Training config schema | FTF-0307, FTF-0403, FTF-0519 | CAP-09 | Q10, Q13 | FTF-0701 |
| FTF-0702 | retained_feature | AUTO config rule engine | FTF-0510, FTF-0701, FTF-0705 | CAP-09 | Q10, Q13 | FTF-0701 |
| FTF-0703 | retained_feature | Training estimate API | FTF-0701, FTF-0702, FTF-0806 | CAP-09 | Q10, Q13 | FTF-0701 |
| FTF-0704 | retained_feature | Training experiment UI | FTF-0701, FTF-0703, FTF-0801 | CAP-09 | Q10, Q13 | FTF-0701 |
| FTF-0705 | retained_feature | NVIDIA environment validation | FTF-0005, FTF-0202, FTF-0701 | CAP-09 | Q10, Q13 | FTF-0701 |
| FTF-0706 | retained_feature | Base artifact loading | FTF-0306, FTF-0701 | CAP-09 | Q10, Q13 | FTF-0701 |
| FTF-0707 | retained_feature | Chat template validation | FTF-0506, FTF-0507, FTF-0701, FTF-0706 | AF-17 | Q10, Q13 | FTF-0701 |
| FTF-0708 | retained_feature | LoRA integration through Axolotl | FTF-0701, FTF-0707, FTF-R003 | AF-17 | Q10, Q13 | FTF-0701 |
| FTF-0709 | retained_feature | QLoRA integration through Axolotl | FTF-0701, FTF-0705, FTF-0707, FTF-R003 | AF-17 | Q10, Q13 | FTF-0701 |
| FTF-0710 | retained_feature | Preflight train step | FTF-0701, FTF-0705, FTF-0708, FTF-0709 | CAP-09 | Q10, Q13 | FTF-0701 |
| FTF-0711 | retained_feature | Numeric metric reporting | FTF-0006, FTF-0701, FTF-0710 | CAP-09 | Q10, Q13 | FTF-0701 |
| FTF-0712 | retained_feature | Atomic checkpoints | FTF-0701, FTF-0710 | AF-09, AF-18 | Q10, Q13 | FTF-0701 |
| FTF-0713 | retained_feature | Checkpoint hash manifest | FTF-0701, FTF-0712 | AF-09, AF-18 | Q10, Q13 | FTF-0701 |
| FTF-0714 | retained_feature | Resume from checkpoint | FTF-0701, FTF-0713 | AF-09, AF-18 | Q10, Q13 | FTF-0701 |
| FTF-0715 | retained_feature | Final adapter packaging | FTF-0701, FTF-0706, FTF-0713 | AF-09, AF-18 | Q10, Q13 | FTF-0701 |
| FTF-0716 | retained_feature | Artifact reload inference test | FTF-0701, FTF-0715, FTF-1003, FTF-1006 | CAP-09 | Q10, Q13 | FTF-0701 |
| FTF-0717 | retained_feature | Live training UI | FTF-0701, FTF-0711, FTF-0801 | CAP-09 | Q10, Q13 | FTF-0701 |
| FTF-0718 | retained_feature | Cancellation | FTF-0701, FTF-0801, FTF-0808 | AF-19 | Q10, Q13 | FTF-0701 |
| FTF-0719 | retained_feature | Budget kill switch | FTF-0403, FTF-0701, FTF-0801, FTF-0807 | AF-22 | Q10, Q13 | FTF-0701 |
| FTF-0801 | retained_feature | Temporal TrainingWorkflow | FTF-0701, FTF-0710, FTF-0715, FTF-0803, FTF-0804, FTF-0805, FTF-0808 | AF-19 | Q11, Q13, Q14, Q15 | FTF-0803 |
| FTF-0802 | retained_feature | Worker heartbeat | FTF-0803, FTF-0804 | AF-19 | Q11, Q13, Q14, Q15 | FTF-0803 |
| FTF-0803 | retained_feature | LocalNvidiaProvider | FTF-0705, FTF-0004, FTF-0403 | AF-19 | Q11, Q13, Q14, Q15 | FTF-0803 |
| FTF-0804 | retained_feature | BYOCKubernetesProvider | FTF-0004, FTF-0403, FTF-0803, FTF-0805 | AF-19 | Q11, Q13, Q14, Q15 | FTF-0803 |
| FTF-0805 | retained_feature | Provisioning security policies | FTF-0005, FTF-0803, FTF-0902 | CAP-09 | Q11, Q13, Q14, Q15 | FTF-0803 |
| FTF-0806 | retained_feature | Compute estimation | FTF-0403, FTF-0705, FTF-0803 | CAP-09 | Q11, Q13, Q14, Q15 | FTF-0803 |
| FTF-0807 | retained_feature | Actual compute cost | FTF-0803, FTF-0804 | AF-22 | Q11, Q13, Q14, Q15 | FTF-0803 |
| FTF-0808 | retained_feature | Verified termination compensation and reconciliation | FTF-0803, FTF-0804 | AF-19 | Q11, Q13, Q14, Q15 | FTF-0803 |
| FTF-0809 | retained_feature | Orphan-resource sweeper | FTF-0803, FTF-0807, FTF-0808 | AF-19 | Q11, Q13, Q14, Q15 | FTF-0803 |
| FTF-0810 | retained_feature | Provision/kill/recover tests | FTF-0718, FTF-0801, FTF-0802, FTF-0803, FTF-0809 | CAP-09 | Q11, Q13, Q14, Q15 | FTF-0803 |
| FTF-0901 | retained_feature | Security profile enforcement | FTF-0103, FTF-0202, FTF-0402, FTF-0902 | AF-06, AF-07 | Q23, Q25 | FTF-0902 |
| FTF-0902 | retained_feature | Network-policy templates | FTF-0005, FTF-0402 | AF-06, AF-07 | Q23, Q25 | FTF-0902 |
| FTF-0903 | retained_feature | Content-free telemetry | FTF-0006, FTF-0901, FTF-0902 | AF-06, AF-07 | Q23, Q25 | FTF-0902 |
| FTF-0904 | retained_feature | Log redaction | FTF-0902, FTF-0903 | AF-06, AF-07 | Q23, Q25 | FTF-0902 |
| FTF-0905 | retained_feature | Canary-secret generator | FTF-0901, FTF-0902 | CAP-14 | Q23, Q25 | FTF-0902 |
| FTF-0906 | retained_feature | Egress capture/verification | FTF-0902, FTF-0904, FTF-0905 | AF-06, AF-07 | Q23, Q25 | FTF-0902 |
| FTF-0907 | retained_feature | External judge denial | FTF-0617, FTF-0901, FTF-0902 | CAP-14 | Q23, Q25 | FTF-0902 |
| FTF-0908 | retained_feature | External provider denial | FTF-0203, FTF-0901, FTF-0902 | CAP-14 | Q23, Q25 | FTF-0902 |
| FTF-0909 | retained_feature | Zero-egress qualification report | FTF-0902, FTF-0906, FTF-0907, FTF-0908 | CAP-14 | Q23, Q25 | FTF-0902 |
| FTF-1001 | retained_feature | ModelVersion schema/API | FTF-0301, FTF-0401, FTF-0103 | AF-14 | Q07, Q20 | FTF-1001 |
| FTF-1002 | retained_feature | Artifact lineage manifest | FTF-0306, FTF-1001 | AF-14 | Q07, Q20 | FTF-1001 |
| FTF-1003 | retained_feature | vLLM serving controller | FTF-0803, FTF-0804, FTF-1001, FTF-1002 | AF-27 | Q07, Q20 | FTF-1001 |
| FTF-1004 | retained_feature | LoRA loading | FTF-1001, FTF-1002, FTF-1003 | AF-27 | Q07, Q20 | FTF-1001 |
| FTF-1005 | retained_feature | Serving health checks | FTF-1001, FTF-1003 | AF-27 | Q07, Q20 | FTF-1001 |
| FTF-1006 | retained_feature | Tool-call compatibility validation | FTF-0707, FTF-1001, FTF-1004, FTF-1005 | AF-27 | Q07, Q20 | FTF-1001 |
| FTF-1007 | retained_feature | Serving benchmark | FTF-0006, FTF-1001, FTF-1006 | CAP-12 | Q07, Q20 | FTF-1001 |
| FTF-1008 | retained_feature | Staging deployment | FTF-1001, FTF-1005, FTF-1006 | CAP-12 | Q07, Q20 | FTF-1001 |
| FTF-1009 | retained_feature | Registry UI backed by controlled MLflow projection | FTF-1001, FTF-1002, FTF-1008, FTF-R005 | AF-30 | Q07, Q20 | FTF-1001 |
| FTF-1101 | retained_feature | PromotionPolicy engine | FTF-0601, FTF-0618, FTF-0407, FTF-1001 | AF-24, AF-29 | Q06, Q22 | FTF-1101 |
| FTF-1102 | retained_feature | Regression comparison | FTF-0618, FTF-1101 | AF-24 | Q06, Q22 | FTF-1101 |
| FTF-1103 | retained_feature | Approval RBAC | FTF-0103, FTF-1101 | AF-29 | Q06, Q22 | FTF-1101 |
| FTF-1104 | retained_feature | Canary traffic controller | FTF-1008, FTF-1101, FTF-1103 | AF-28 | Q06, Q22 | FTF-1101 |
| FTF-1105 | retained_feature | Runtime quality probes | FTF-0614, FTF-1101, FTF-1104 | AF-28 | Q06, Q22 | FTF-1101 |
| FTF-1106 | retained_feature | Automatic rollback | FTF-1101, FTF-1104, FTF-1105 | AF-28 | Q06, Q22 | FTF-1101 |
| FTF-1107 | retained_feature | Forced-failure rollback test | FTF-1101, FTF-1106 | CAP-12 | Q06, Q22 | FTF-1101 |
| FTF-1108 | retained_feature | Deployment UI | FTF-1101, FTF-1104, FTF-1106 | AF-30 | Q06, Q22 | FTF-1101 |
| FTF-1201 | retained_feature | LiteLLM registration capability detection | FTF-0203, FTF-0202, FTF-0103 | CAP-02 | Q21, Q22 | FTF-1201 |
| FTF-1202 | retained_feature | Add approved model/deployment | FTF-1008, FTF-1201 | AF-28 | Q21, Q22 | FTF-1201 |
| FTF-1203 | retained_feature | Verify `/v1/models` | FTF-0204, FTF-1201, FTF-1202 | CAP-02 | Q21, Q22 | FTF-1201 |
| FTF-1204 | retained_feature | Post-registration real inference | FTF-0614, FTF-1201, FTF-1203 | AF-28 | Q21, Q22 | FTF-1201 |
| FTF-1205 | retained_feature | Rollback/unregister procedure | FTF-1106, FTF-1201, FTF-1204 | CAP-02 | Q21, Q22 | FTF-1201 |
| FTF-1206 | retained_feature | Organization policy control | FTF-0402, FTF-1201 | CAP-02 | Q21, Q22 | FTF-1201 |
| FTF-1206A | retained_feature (addendum, ADR 0002) | Organization gateway policy persistence | FTF-0104, FTF-1201, FTF-H003 | CAP-02 | Q21, Q22 | FTF-1201 |
| FTF-1301 | retained_feature | CostProfile schema/UI | FTF-0401, FTF-0403, FTF-0103 | AF-22 | Q15, Q19 | FTF-1301 |
| FTF-1302 | retained_feature | Local compute costing | FTF-0006, FTF-1301 | AF-22 | Q15, Q19 | FTF-1301 |
| FTF-1303 | retained_feature | Provider token costing | FTF-0210, FTF-1301 | AF-22 | Q15, Q19 | FTF-1301 |
| FTF-1304 | retained_feature | Cost-per-success calculator | FTF-1301, FTF-1302, FTF-1303 | AF-22 | Q15, Q19 | FTF-1301 |
| FTF-1305 | retained_feature | Candidate ranking | FTF-0307, FTF-1301, FTF-1304 | AF-26 | Q15, Q19 | FTF-1301 |
| FTF-1306 | retained_feature | Development baseline evaluator | FTF-0614, FTF-1301, FTF-1305 | AF-26 | Q15, Q19 | FTF-1301 |
| FTF-1307 | retained_feature | Progressive search workflow | FTF-0618, FTF-0801, FTF-1301, FTF-1306 | AF-26 | Q15, Q19 | FTF-1301 |
| FTF-1308 | retained_feature | Stop condition | FTF-1301, FTF-1307 | AF-26 | Q15, Q19 | FTF-1301 |
| FTF-1309 | retained_feature | Comparison report | FTF-0619, FTF-1301, FTF-1308 | CAP-13 | Q15, Q19 | FTF-1301 |
| FTF-1310 | retained_feature | Winner recommendation UI | FTF-1301, FTF-1309 | CAP-13 | Q15, Q19 | FTF-1301 |
| FTF-1401 | retained_feature | API load test | FTF-0006, FTF-0401, FTF-0106 | AF-21 | Q28 | FTF-1401 |
| FTF-1402 | retained_feature | Dataset million-record import test | FTF-0520, FTF-1401, FTF-H012 | AF-21 | Q28 | FTF-1401 |
| FTF-1403 | retained_feature | Evaluation concurrency test | FTF-0618, FTF-1401, FTF-H008 | AF-21 | Q28 | FTF-1401 |
| FTF-1404 | retained_feature | Temporal queue saturation test | FTF-0801, FTF-1401, FTF-H016 | AF-21 | Q28 | FTF-1401 |
| FTF-1405 | retained_feature | vLLM concurrency benchmark | FTF-1007, FTF-1401, FTF-H024 | AF-21 | Q28 | FTF-1401 |
| FTF-1406 | retained_feature | GPU utilization benchmark | FTF-0711, FTF-1007, FTF-1401 | AF-21 | Q28 | FTF-1401 |
| FTF-1407 | retained_feature | DB index/query audit | FTF-0105, FTF-0519, FTF-0618, FTF-1401 | AF-21 | Q28 | FTF-1401 |
| FTF-1408 | retained_feature | Performance report | FTF-1401, FTF-1402, FTF-1403, FTF-1404, FTF-1405, FTF-1406, FTF-1407 | CAP-15 | Q28 | FTF-1401 |
| FTF-1501 | retained_feature | Tenant isolation suite | FTF-0106, FTF-H003 | CAP-14 | Q24, Q25, Q26 | FTF-1501 |
| FTF-1502 | retained_feature | RBAC suite | FTF-0103, FTF-1103, FTF-1501 | CAP-14 | Q24, Q25, Q26 | FTF-1501 |
| FTF-1503 | retained_feature | CSRF suite | FTF-0101, FTF-0401, FTF-1501 | CAP-14 | Q24, Q25, Q26 | FTF-1501 |
| FTF-1504 | retained_feature | IDOR suite | FTF-0106, FTF-0501, FTF-1501 | CAP-14 | Q24, Q25, Q26 | FTF-1501 |
| FTF-1505 | retained_feature | Secret scanning | FTF-0006, FTF-0904, FTF-1501 | CAP-14 | Q24, Q25, Q26 | FTF-1501 |
| FTF-1506 | retained_feature | Dependency scanning | FTF-0002, FTF-0004, FTF-1501 | CAP-14 | Q24, Q25, Q26 | FTF-1501 |
| FTF-1507 | retained_feature | Container scanning | FTF-0004, FTF-1003, FTF-1501 | CAP-14 | Q24, Q25, Q26 | FTF-1501 |
| FTF-1508 | retained_feature | SBOM | FTF-1501, FTF-1506, FTF-1507 | CAP-14 | Q24, Q25, Q26 | FTF-1501 |
| FTF-1509 | retained_feature | Supply-chain signing | FTF-1501, FTF-1508, FTF-H028 | CAP-14 | Q24, Q25, Q26 | FTF-1501 |
| FTF-1510 | retained_feature | Audit integrity test | FTF-0104, FTF-1501, FTF-1603 | CAP-14 | Q24, Q25, Q26 | FTF-1501 |
| FTF-1601 | retained_feature | Postgres backup | FTF-0004, FTF-0102 | AF-32 | Q29, Q30 | FTF-1601 |
| FTF-1602 | retained_feature | Object storage backup | FTF-0004, FTF-1002, FTF-1601 | AF-32 | Q29, Q30 | FTF-1601 |
| FTF-1603 | retained_feature | Restore automation | FTF-0809, FTF-1601, FTF-1602 | AF-32 | Q29, Q30 | FTF-1601 |
| FTF-1604 | retained_feature | Full restored-model inference | FTF-1006, FTF-1601, FTF-1603 | AF-32 | Q29, Q30 | FTF-1601 |
| FTF-1605 | retained_feature | DR runbook | FTF-1601, FTF-1603, FTF-1604 | AF-32 | Q29, Q30 | FTF-1601 |
| FTF-1606 | retained_feature | Qualification test | FTF-1601, FTF-1604, FTF-1605 | CAP-15 | Q29, Q30 | FTF-1601 |
| FTF-1701 | retained_feature | Reference agent specification | FTF-0407, FTF-0405 | AF-36 | Q05, Q10, Q17, Q32 | FTF-1701 |
| FTF-1702 | retained_feature | Tool environment | FTF-0004, FTF-1701 | AF-36 | Q05, Q10, Q17, Q32 | FTF-1701 |
| FTF-1703 | retained_feature | Training dataset | FTF-0506, FTF-1701, FTF-1702 | AF-36 | Q05, Q10, Q17, Q32 | FTF-1701 |
| FTF-1704 | retained_feature | Validation dataset | FTF-0517, FTF-1701, FTF-1703 | AF-36 | Q05, Q10, Q17, Q32 | FTF-1701 |
| FTF-1705 | retained_feature | Held-out suite | FTF-0601, FTF-1701, FTF-1702, FTF-H020 | AF-36 | Q05, Q10, Q17, Q32 | FTF-1701 |
| FTF-1706 | retained_feature | Candidate discovery from org LiteLLM | FTF-0215, FTF-0307, FTF-1701 | CAP-18 | Q05, Q10, Q17, Q32 | FTF-1701 |
| FTF-1707 | retained_feature | Baseline candidates | FTF-1306, FTF-1701, FTF-1704, FTF-1706 | CAP-18 | Q05, Q10, Q17, Q32 | FTF-1701 |
| FTF-1708 | retained_feature | Fine-tune cheapest candidate | FTF-0704, FTF-1701, FTF-1703, FTF-1707, FTF-H014 | AF-36 | Q05, Q10, Q17, Q32 | FTF-1701 |
| FTF-1709 | retained_feature | Evaluate | FTF-1701, FTF-1705, FTF-1708, FTF-H021, FTF-H022 | CAP-18 | Q05, Q10, Q17, Q32 | FTF-1701 |
| FTF-1710 | retained_feature | Escalate to next candidate if needed | FTF-1308, FTF-1701, FTF-1709 | CAP-18 | Q05, Q10, Q17, Q32 | FTF-1701 |
| FTF-1711 | retained_feature | Benchmark winner | FTF-1007, FTF-1701, FTF-1709, FTF-H019 | CAP-18 | Q05, Q10, Q17, Q32 | FTF-1701 |
| FTF-1712 | retained_feature | Deploy winner | FTF-1108, FTF-1701, FTF-1711, FTF-H025, FTF-H026 | CAP-18 | Q05, Q10, Q17, Q32 | FTF-1701 |
| FTF-1713 | retained_feature | Register winner in LiteLLM | FTF-1204, FTF-1701, FTF-1712 | CAP-18 | Q05, Q10, Q17, Q32 | FTF-1701 |
| FTF-1714 | retained_feature | Production-like E2E agent test | FTF-0614, FTF-1701, FTF-1713, FTF-H008 | AF-02, AF-36 | Q05, Q10, Q17, Q32 | FTF-1701 |
| FTF-1715 | retained_feature | Final cost/quality report | FTF-1309, FTF-1701, FTF-1714 | CAP-18 | Q05, Q10, Q17, Q32 | FTF-1701 |
| FTF-1801 | retained_feature | Operator guide | FTF-0004, FTF-1605, FTF-H001 | CAP-15 | Q30, Q34 | FTF-H001 |
| FTF-1802 | retained_feature | User guide | FTF-H001, FTF-H029, FTF-H030 | CAP-15 | Q30, Q34 | FTF-H001 |
| FTF-1803 | retained_feature | Security architecture | FTF-0909, FTF-1501, FTF-H001 | CAP-15 | Q30, Q34 | FTF-H001 |
| FTF-1804 | retained_feature | Data-flow diagrams | FTF-0909, FTF-H001, FTF-H027 | CAP-15 | Q30, Q34 | FTF-H001 |
| FTF-1805 | retained_feature | Incident runbooks | FTF-0006, FTF-0810, FTF-0909, FTF-H001 | AF-33 | Q30, Q34 | FTF-H001 |
| FTF-1806 | retained_feature | Training failure runbook | FTF-0714, FTF-0810, FTF-H001 | CAP-15 | Q30, Q34 | FTF-H001 |
| FTF-1807 | retained_feature | LiteLLM outage runbook | FTF-0215, FTF-H001, FTF-H004, FTF-H005 | CAP-15 | Q30, Q34 | FTF-H001 |
| FTF-1808 | retained_feature | GPU capacity runbook | FTF-0803, FTF-0804, FTF-0807, FTF-H001 | CAP-15 | Q30, Q34 | FTF-H001 |
| FTF-1809 | retained_feature | Backup/restore runbook | FTF-1605, FTF-H001, FTF-H032 | CAP-15 | Q30, Q34 | FTF-H001 |
| FTF-1810 | retained_feature | ReleaseQualificationWorkflow | FTF-H001, FTF-H028 | AF-02, AF-36 | Q30, Q34 | FTF-H001 |
| FTF-1811 | retained_feature | Signed qualification report | FTF-1810, FTF-H001, FTF-H036, FTF-R018 | CAP-15 | Q30, Q34 | FTF-H001 |
| FTF-1812 | retained_feature | Production release | FTF-0001, FTF-0002, FTF-0003, FTF-0004, FTF-0005, FTF-0006, FTF-0101, FTF-0102, FTF-0103, FTF-0104, FTF-0105, FTF-0106, FTF-0201, FTF-0202, FTF-0203, FTF-0204, FTF-0205, FTF-0206, FTF-0207, FTF-0208, FTF-0209, FTF-0210, FTF-0211, FTF-0212, FTF-0213, FTF-0214, FTF-0215, FTF-0301, FTF-0302, FTF-0303, FTF-0304, FTF-0305, FTF-0306, FTF-0307, FTF-0308, FTF-0309, FTF-0310, FTF-0401, FTF-0402, FTF-0403, FTF-0404, FTF-0405, FTF-0406, FTF-0407, FTF-0408, FTF-04A01, FTF-04A02, FTF-04A03, FTF-04A04, FTF-04A05, FTF-04A06, FTF-04A07, FTF-04A08, FTF-04A09, FTF-04A10, FTF-04A11, FTF-04A12, FTF-04A13, FTF-04A14, FTF-04A15, FTF-04A16, FTF-04A17, FTF-04A18, FTF-04A19, FTF-04A20, FTF-04A21, FTF-04A22, FTF-04A23, FTF-04A24, FTF-04A25, FTF-04A26, FTF-04A27, FTF-04A28, FTF-04A29, FTF-04A30, FTF-04A31, FTF-04A32, FTF-04A33, FTF-04A34, FTF-04A35, FTF-0500, FTF-0500A, FTF-0500B, FTF-0500C, FTF-0500D, FTF-0501, FTF-0502, FTF-0503, FTF-0504, FTF-0505, FTF-0506, FTF-0507, FTF-0508, FTF-0509, FTF-0510, FTF-0511, FTF-0512, FTF-0513, FTF-0514, FTF-0515, FTF-0516, FTF-0517, FTF-0518, FTF-0519, FTF-0520, FTF-0601, FTF-0602, FTF-0603, FTF-0604, FTF-0605, FTF-0606, FTF-0607, FTF-0608, FTF-0609, FTF-0610, FTF-0611, FTF-0612, FTF-0613, FTF-0614, FTF-0615, FTF-0616, FTF-0617, FTF-0618, FTF-0619, FTF-0701, FTF-0702, FTF-0703, FTF-0704, FTF-0705, FTF-0706, FTF-0707, FTF-0708, FTF-0709, FTF-0710, FTF-0711, FTF-0712, FTF-0713, FTF-0714, FTF-0715, FTF-0716, FTF-0717, FTF-0718, FTF-0719, FTF-0801, FTF-0802, FTF-0803, FTF-0804, FTF-0805, FTF-0806, FTF-0807, FTF-0808, FTF-0809, FTF-0810, FTF-0901, FTF-0902, FTF-0903, FTF-0904, FTF-0905, FTF-0906, FTF-0907, FTF-0908, FTF-0909, FTF-1001, FTF-1002, FTF-1003, FTF-1004, FTF-1005, FTF-1006, FTF-1007, FTF-1008, FTF-1009, FTF-1101, FTF-1102, FTF-1103, FTF-1104, FTF-1105, FTF-1106, FTF-1107, FTF-1108, FTF-1201, FTF-1202, FTF-1203, FTF-1204, FTF-1205, FTF-1206, FTF-1301, FTF-1302, FTF-1303, FTF-1304, FTF-1305, FTF-1306, FTF-1307, FTF-1308, FTF-1309, FTF-1310, FTF-1401, FTF-1402, FTF-1403, FTF-1404, FTF-1405, FTF-1406, FTF-1407, FTF-1408, FTF-1501, FTF-1502, FTF-1503, FTF-1504, FTF-1505, FTF-1506, FTF-1507, FTF-1508, FTF-1509, FTF-1510, FTF-1601, FTF-1602, FTF-1603, FTF-1604, FTF-1605, FTF-1606, FTF-1701, FTF-1702, FTF-1703, FTF-1704, FTF-1705, FTF-1706, FTF-1707, FTF-1708, FTF-1709, FTF-1710, FTF-1711, FTF-1712, FTF-1713, FTF-1714, FTF-1715, FTF-1801, FTF-1802, FTF-1803, FTF-1804, FTF-1805, FTF-1806, FTF-1807, FTF-1808, FTF-1809, FTF-1810, FTF-1811, FTF-H001, FTF-H002, FTF-H003, FTF-H004, FTF-H005, FTF-H006, FTF-H007, FTF-H008, FTF-H009, FTF-H010, FTF-H011, FTF-H012, FTF-H013, FTF-H014, FTF-H015, FTF-H016, FTF-H017, FTF-H018, FTF-H019, FTF-H020, FTF-H021, FTF-H022, FTF-H023, FTF-H024, FTF-H025, FTF-H026, FTF-H027, FTF-H028, FTF-H029, FTF-H030, FTF-H031, FTF-H032, FTF-H033, FTF-H034, FTF-H035, FTF-H036, FTF-R001, FTF-R002, FTF-R003, FTF-R004, FTF-R005, FTF-R006, FTF-R007, FTF-R008, FTF-R009, FTF-R010, FTF-R011, FTF-R012, FTF-R013, FTF-R014, FTF-R015, FTF-R016, FTF-R017, FTF-R018 | CAP-15 | Q30, Q34 | FTF-H001 |
| FTF-H001 | audit_hardening | Executable specification and gate bootstrap | FTF-0001, FTF-0002, FTF-0003 | AF-31, AF-35 | Q26, Q33, Q34 |  |
| FTF-H002 | audit_hardening | Freeze release scope, compatibility and measured execution inputs | FTF-H001, FTF-0005 | AF-01, AF-02, AF-36 | Q12, Q34 |  |
| FTF-H003 | audit_hardening | Cross-layer tenant isolation and relational identity | FTF-0102, FTF-0103, FTF-0104, FTF-H001 | AF-08 | Q01, Q24 |  |
| FTF-H004 | audit_hardening | Authorized LiteLLM catalog and revocation semantics | FTF-0204, FTF-0205, FTF-0207, FTF-0208, FTF-0209, FTF-H003 | AF-03, AF-04 | Q02 |  |
| FTF-H005 | audit_hardening | Route identity, fallback and cache provenance | FTF-0203, FTF-0210, FTF-0215, FTF-H004 | AF-05 | Q02, Q21 |  |
| FTF-H006 | audit_hardening | Shared outbound URL and credential guard | FTF-0005, FTF-0201, FTF-0202, FTF-H003 | AF-10 | Q25 |  |
| FTF-H007 | audit_hardening | Untrusted source acquisition and complete immutable analysis | FTF-04A05, FTF-04A06, FTF-04A08, FTF-04A19, FTF-H006 | AF-09, AF-11 | Q03, Q04, Q25 |  |
| FTF-H008 | audit_hardening | Actual product runtime adapter and parity gate | FTF-04A25, FTF-04A26, FTF-0614, FTF-H007 | AF-12 | Q05, Q16 |  |
| FTF-H009 | audit_hardening | Runtime compatibility tuple and drift invalidation | FTF-04A27, FTF-04A28, FTF-04A29, FTF-H005, FTF-H008 | AF-13 | Q06 |  |
| FTF-H010 | audit_hardening | Endpoint, weights and adapter import with honest continuation | FTF-1001, FTF-1002, FTF-0306, FTF-0307, FTF-0308, FTF-H005 | AF-14 | Q07 |  |
| FTF-H011 | audit_hardening | Dataset preview, field mapping and purpose approval UI | FTF-0500, FTF-0500A, FTF-0506, FTF-0507, FTF-0516, FTF-H003 | AF-15 | Q08 |  |
| FTF-H012 | audit_hardening | Concrete S3, PostgreSQL and REST/trace source ingestion | FTF-0500B, FTF-0500C, FTF-0501, FTF-H006, FTF-H011 | AF-02, AF-15 | Q09 |  |
| FTF-H013 | audit_hardening | Data rights, poisoning, revocation and retention lifecycle | FTF-0500D, FTF-0514, FTF-0515, FTF-0519, FTF-H012 | AF-16 | Q31 |  |
| FTF-H014 | audit_hardening | Supervised-token masks, packing and chat-template parity | FTF-0707, FTF-0708, FTF-0709, FTF-H001 | AF-17 | Q10 |  |
| FTF-H015 | audit_hardening | Trusted full-state checkpoint and atomic artifact publication | FTF-0712, FTF-0713, FTF-0714, FTF-0715, FTF-H014 | AF-09, AF-18 | Q13, Q25 |  |
| FTF-H016 | audit_hardening | Transactional outbox and ambiguous provider submission | FTF-0801, FTF-0803, FTF-0804, FTF-H003 | AF-19 | Q14 |  |
| FTF-H017 | audit_hardening | Worker generation fencing and cancellation races | FTF-0802, FTF-0808, FTF-0809, FTF-0718, FTF-H015, FTF-H016 | AF-19 | Q13, Q14 |  |
| FTF-H018 | audit_hardening | Atomic cost reservations and bounded stop-lag controls | FTF-0403, FTF-0719, FTF-0807, FTF-H016 | AF-22 | Q15 |  |
| FTF-H019 | audit_hardening | Fully allocated serving economics and auditable forecast | FTF-1301, FTF-1302, FTF-1303, FTF-1304, FTF-H018 | AF-22 | Q19 |  |
| FTF-H020 | audit_hardening | Four purpose datasets and sealed final campaign | FTF-0517, FTF-0518, FTF-0519, FTF-0603, FTF-H013 | AF-23 | Q17 |  |
| FTF-H021 | audit_hardening | Independent outcomes, full denominators and confidence gates | FTF-0618, FTF-1101, FTF-1102, FTF-H008, FTF-H020 | AF-24 | Q16, Q17 |  |
| FTF-H022 | audit_hardening | Judge calibration, manipulation resistance and audit | FTF-0616, FTF-0617, FTF-H020 | AF-25 | Q18 |  |
| FTF-H023 | audit_hardening | Development-only economic search including existing models | FTF-1305, FTF-1306, FTF-1307, FTF-1308, FTF-H010, FTF-H019, FTF-H020, FTF-H021 | AF-26 | Q19, Q32 |  |
| FTF-H024 | audit_hardening | Private vLLM health adapter and immutable serving security | FTF-1003, FTF-1004, FTF-1005, FTF-1006, FTF-H010, FTF-H014 | AF-27 | Q20 |  |
| FTF-H025 | audit_hardening | Target adoption and session-stable canary/rollback | FTF-1104, FTF-1105, FTF-1106, FTF-1202, FTF-1204, FTF-H005, FTF-H024 | AF-28 | Q21, Q22 |  |
| FTF-H026 | audit_hardening | Atomic approval and enforceable mandate | FTF-1101, FTF-1103, FTF-H003, FTF-H009, FTF-H025 | AF-29 | Q05, Q06, Q22 |  |
| FTF-H027 | audit_hardening | Full private data path and measured egress evidence | FTF-0901, FTF-0902, FTF-0903, FTF-0904, FTF-0906, FTF-H003, FTF-H006, FTF-H007 | AF-06, AF-07 | Q23 |  |
| FTF-H028 | audit_hardening | Trusted CI and exact-revision release evidence gate | FTF-0003, FTF-H001 | AF-20 | Q26 |  |
| FTF-H029 | audit_hardening | Complete backend-driven setup and model/data action branches | FTF-0408, FTF-04A30, FTF-0520, FTF-0619, FTF-1009, FTF-1108, FTF-H010, FTF-H011 | AF-30 | Q07, Q08, Q27 |  |
| FTF-H030 | audit_hardening | Shared design system and accessibility qualification | FTF-H029 | AF-30 | Q27 |  |
| FTF-H031 | audit_hardening | Reproducible load, capacity, overload and soak qualification | FTF-1401, FTF-1402, FTF-1403, FTF-1404, FTF-1405, FTF-1406, FTF-1407, FTF-H017, FTF-H024 | AF-21 | Q28 |  |
| FTF-H032 | audit_hardening | Consistent restore, workflow reconciliation and safe upgrades | FTF-1601, FTF-1602, FTF-1603, FTF-1604, FTF-1605, FTF-H017, FTF-H013 | AF-32 | Q29, Q30 |  |
| FTF-H033 | audit_hardening | Identity lifecycle, key rotation and real operational ownership | FTF-0101, FTF-0103, FTF-0104, FTF-1805, FTF-H003, FTF-H027 | AF-33 | Q01, Q30 |  |
| FTF-H034 | audit_hardening | Safe parallel scheduler and task/evidence ownership | FTF-H001, FTF-H002 | AF-31, AF-34, AF-35 | Q33, Q34 |  |
| FTF-H035 | audit_hardening | Reference data and full true fine-tuned model qualification | FTF-1701, FTF-1702, FTF-1703, FTF-1704, FTF-1705, FTF-1708, FTF-H008, FTF-H014, FTF-H020, FTF-H021, FTF-H024 | AF-36 | Q10, Q32 |  |
| FTF-H036 | audit_hardening | Managed adapter and full product adoption/release closure | FTF-1714, FTF-1810, FTF-H001, FTF-H002, FTF-H003, FTF-H004, FTF-H005, FTF-H006, FTF-H007, FTF-H008, FTF-H009, FTF-H010, FTF-H011, FTF-H012, FTF-H013, FTF-H014, FTF-H015, FTF-H016, FTF-H017, FTF-H018, FTF-H019, FTF-H020, FTF-H021, FTF-H022, FTF-H023, FTF-H024, FTF-H025, FTF-H026, FTF-H027, FTF-H028, FTF-H029, FTF-H030, FTF-H031, FTF-H032, FTF-H033, FTF-H034, FTF-H035 | AF-02, AF-36 | Q12, Q21, Q32, Q34 |  |
| FTF-R001 | reuse_architecture | Freeze seven-component architecture, ownership and version-lock schemas | FTF-0001, FTF-H001 | CAP-19, CAP-20, CAP-21 | Q35, Q43 | FTF-R001 |
| FTF-R002 | reuse_architecture | Implement authenticated internal component bindings and reconciled adapter foundation | FTF-R001, FTF-H003, FTF-H006 | CAP-19, CAP-21 | Q35, Q38, Q39 | FTF-R001 |
| FTF-R003 | reuse_architecture | Integrate Axolotl behind the real training worker | FTF-R002, FTF-0701, FTF-0707 | CAP-09, CAP-19 | Q10, Q13, Q35 | FTF-R001 |
| FTF-R004 | reuse_architecture | Integrate Inspect with actual target application and sealed evaluation | FTF-R002, FTF-0601, FTF-04A25, FTF-04A26, FTF-0210 | CAP-11, CAP-19 | Q16, Q17, Q18, Q35 | FTF-R001 |
| FTF-R005 | reuse_architecture | Integrate internal MLflow tracking and model registry projection | FTF-R002, FTF-1001, FTF-1002 | CAP-12, CAP-19, CAP-21 | Q35, Q38, Q40 | FTF-R001 |
| FTF-R006 | reuse_architecture | Integrate internal Argilla review, reviewer identity and immutable export | FTF-R002, FTF-0500, FTF-0506, FTF-0516 | CAP-07, CAP-08, CAP-19, CAP-21 | Q08, Q35, Q36, Q38 | FTF-R001 |
| FTF-R007 | reuse_architecture | Complete unified Factory UI across seven integrated components | FTF-R003, FTF-R004, FTF-R005, FTF-R006, FTF-H029 | CAP-16, CAP-21 | Q27, Q35, Q36 | FTF-R001 |
| FTF-R008 | reuse_architecture | Deploy internal compact and production placement with explicit dependencies | FTF-R002, FTF-R017, FTF-0804, FTF-1003 | CAP-15, CAP-19, CAP-20 | Q11, Q28, Q35, Q39 | FTF-R001 |
| FTF-R009 | reuse_architecture | Build and qualify complete offline install and lifecycle bundle | FTF-R008, FTF-H027 | CAP-14, CAP-20 | Q23, Q37, Q39 | FTF-R001 |
| FTF-R010 | reuse_architecture | Prove composed seven-component reference path and runtime independence | FTF-R007, FTF-R009, FTF-H014, FTF-H015, FTF-H024, FTF-H025, FTF-H035 | CAP-18, CAP-19, CAP-20 | Q32, Q35, Q36, Q39 | FTF-R001 |
| FTF-R011 | reuse_architecture | Define immutable connected and air-gap assertion-level release matrix | FTF-R001, FTF-H001, FTF-H002 | CAP-20, CAP-21 | Q12, Q26, Q43 | FTF-R001 |
| FTF-R012 | reuse_architecture | Test cross-component authorization, telemetry, tampering and outages | FTF-R002, FTF-R005, FTF-R006, FTF-H003, FTF-H027 | CAP-14, CAP-19, CAP-21 | Q24, Q25, Q38, Q39 | FTF-R001 |
| FTF-R013 | reuse_architecture | Qualify composed backup, export, restore and dependency upgrades | FTF-R008, FTF-R005, FTF-R006, FTF-H032, FTF-H031 | CAP-15, CAP-19, CAP-20 | Q29, Q30, Q31, Q40 | FTF-R001 |
| FTF-R014 | reuse_architecture | Bind selected coding executor to the embedded Release 1 execution controls | FTF-R001, FTF-H034 | CAP-17, CAP-21 | Q33, Q41 | FTF-R001 |
| FTF-R015 | reuse_architecture | Enforce isolated concurrent coding execution and independent integration | FTF-R014, FTF-H028 | CAP-17, CAP-21 | Q33, Q41, Q42 | FTF-R001 |
| FTF-R016 | reuse_architecture | Measure selected coding-model/executor pilot cost and enforce budget tree | FTF-R015, FTF-H002, FTF-0403 | CAP-17, CAP-21 | Q15, Q41, Q42 | FTF-R001 |
| FTF-R017 | reuse_architecture | Qualify maintained internal storage and OSS dependency lifecycle | FTF-R001, FTF-0004 | CAP-15, CAP-19, CAP-20 | Q09, Q29, Q37, Q40 | FTF-R001 |
| FTF-R018 | reuse_architecture | Integrate architecture evidence into protected full release gate | FTF-R010, FTF-R011, FTF-R012, FTF-R013, FTF-R014, FTF-R015, FTF-R016, FTF-R017, FTF-H028 | CAP-19, CAP-20, CAP-21 | Q26, Q34, Q43 | FTF-R001 |

## Execution interpretation

The complete task record for each row also contains sections, owner paths, API contract, shared locks, database-change rules, acceptance clauses, negative acceptance clauses, and verification commands. Read that record from Section 85 before claiming or implementing the task.

The first required sequence is: bootstrap and protected verifier; architecture/schema/threat/identity/release-scope lock; storage and durable submission contracts; reference target/tool domain and model binding; then the first real target-to-training-to-evaluation-to-serving vertical slice. Hardening and full release closeout follow with independent review.

