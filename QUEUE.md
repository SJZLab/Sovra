# QUEUE — Sovra (Model Factory)
Updated: 2026-09-21 (scheduled relay shift) — implemented FTF-1401 (API
  load test, deps FTF-0006/FTF-0401/FTF-0106 all CERTIFIED). Checked
  GitHub directly for open PRs before picking work: PR #158 (FTF-0403),
  #159 (FTF-1501), #160 (FTF-1001) were already open from more recent
  relay shifts than this file's own prior update — treated as already
  claimed, not re-done. Independently recomputed the dependency-ready
  frontier: FTF-0202 (owner ask, still open/unverified), FTF-0403,
  FTF-0902, FTF-1001, FTF-1401, FTF-1501, FTF-1701 (newly unblocked by
  FTF-0407's own certification). FTF-0902 requires a real enforcing
  Kubernetes cluster for its own acceptance (CNI/firewall/DNS/IPv6
  restrictions) that this sandbox cannot reach — set aside rather than
  faked. Picked FTF-1401 over FTF-1701 as achievable with only this
  sandbox's real local Postgres + uvicorn.
  `tests/performance/api_load_test.py` drives the real, already-live
  project/tool routers over a real uvicorn/Postgres round trip, reporting
  p50/p95/p99/errors/backpressure — a disclosed partial profile against
  Section 49's full-scale Control API target (is_partial_profile=true,
  disclosed_gaps names every reduction), per that section's own "smaller
  environment -> partial profile" rule. Real run: 3,831 requests over
  15.0s, read P95 48.9ms / write P95 48.8ms (within the 500ms/1000ms
  thresholds), 0 unexpected 5xx, acceptance_passed=true; independently
  re-queried the DB to confirm no successful write was lost. Own fault
  injection (Law 1, two vectors — `execute_request`'s OSError guard and
  `evaluate_acceptance`'s 5xx-rate branch — both caught for real and
  reverted byte-identical). 17 no-DB test functions, all pass. No
  regression to FTF-0401/FTF-0404/FTF-0405/FTF-0406/FTF-0407's own
  suites. `ruff`/`mypy` clean repo-wide. `validate_spec.py --strict`
  clean. `product_truth_check.py` unchanged at 10/10 (proving_symbol is
  deliberately empty — a measurement tool, not a new served route,
  matching FTF-1601's own precedent). Wired into
  `.github/workflows/ci.yml` (new gate step + new
  `api-load-test-integration` job). PR opened as PROVISIONAL;
  `docs/spec-matrix.yaml` FTF-1401 set to `PROVISIONAL`/`L1-PROVEN`.
  Awaiting independent Control plane certification — not self-certified
  (Law 4). Certified stays 33/300; product-true stays 10.
  **Flagged for Control plane/next shift (not fixed by this task, out of
  its own scope):** FTF-0406's and FTF-0407's own no-DB and live-Postgres
  test suites are not wired into `.github/workflows/ci.yml` at all — no
  step anywhere references `test_ftf_0406.py`/`test_ftf_0407.py` or their
  `tests/integration/` counterparts (confirmed by direct search over the
  whole file). Their CERTIFIED evidence was genuinely reproduced by their
  own independent certifier reviews, so this is not a false-certification
  finding, but that evidence is not continuously re-enforced by CI on
  every future PR the way every other CERTIFIED task's is. Full
  submission report: `docs/certifications/queue.md`.

Previously, 2026-09-21 (scheduled Control-plane certifier shift, triggered by
  the `pull_request.opened` webhook for PR #156) — independent CERTIFY
  review of FTF-0407 (Critical behavior requirements): **CERTIFIED**.
  Subscribed while CI was still in-flight; the producer session
  independently pushed a real fix (`00641ac`) for a genuine `ruff`
  import-sort failure and a genuine `mypy` dict-variance error before this
  review acted, then CI reconfirmed genuinely green (16/16) on the real
  final head. Own fault injection, three distinct vectors against a real
  `git worktree` checkout of the merged head, all caught for real and
  reverted byte-identical; no regression to FTF-0401/FTF-0404/FTF-0405/
  FTF-0406's own no-DB and live-Postgres suites; task contract re-pulled
  and both acceptance clauses plus negative_acceptance confirmed met.
  Merged PR #156 (merge commit `5da3834`). `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`.
  Certified: 33/300, Product-true: 10/10. Full review:
  `docs/certifications/ledger.md`/`docs/certifications/queue.md`.

Previously, 2026-09-21 (scheduled relay shift) — implemented FTF-0407
  (Critical behavior requirements, deps FTF-0401/FTF-0406 both CERTIFIED).
  Independently recomputed the dependency-ready frontier fresh against
  `docs/spec-matrix.yaml`'s live certification state (never trusted the
  prior shift's own "Next shift" note by itself): FTF-0202 (owner ask,
  still open/unverified — see the flagged note below), FTF-0403,
  FTF-0407, FTF-0902, FTF-1001, FTF-1401, FTF-1501 are the seven
  dependency-ready items; FTF-0407 has the lowest CAP number (CAP-05)
  with no external-service ask of its own, matching this file's own prior
  prediction that FTF-0406 reaching CERTIFIED would unblock it.
  Section 7A.6/6.8D's own mandate fields (`AgentManifest.mandate_json`/
  `forbidden_actions_json`) do not exist as real storage anywhere in this
  repository yet — `AgentManifest` is FTF-04A19's own, later, separately
  dependency-gated schema that itself depends on *this* task, not the
  other way around — so this task is narrowed to the one real,
  already-frozen structural analog that exists today: FTF-0404's own
  `ToolDefinition.is_critical` field, checked for the first time against
  `side_effect_class`/`requires_confirmation`.
  `apps/api/app/services/critical_behavior_requirements.py`'s
  `enforce_critical_behavior_requirement` refuses an `is_critical=True`
  claim that is unmeasurable (`read_only`, no side effect to bind a
  test/enforcement to) or conflicting (a real side effect but
  `requires_confirmation=False`, i.e. a critical claim with no runtime
  guard behind it) — a no-op whenever `is_critical=False`. Deliberately
  does **not** force `is_critical=True` on a high-risk resolution the
  other way, since FTF-0406's own already-CERTIFIED evidence constructs
  exactly such a tool (`irreversible_write`, confirmed, `is_critical=
  False`) and this task must not regress it (Law 14) — checked directly
  against every existing certified fixture before finalizing the rule,
  not discovered after. Wired into `ToolDefinition.__init__`, the same
  point FTF-0402/FTF-0405/FTF-0406 already use. Also adds the real,
  read-only `/api/v1/organizations/{organization_id}/projects/
  {project_id}/requirements` route (this task's own `api_contract` names
  it as new surface FTF-0404's own contract deliberately left undeclared)
  — a derived projection over already-enforced `ToolDefinition` rows,
  never a separately stored claim; the sixth live-wired product-runtime
  path in this repository. `database_changes` honored ("No independent
  schema change") — consumes FTF-0404's own frozen columns exactly as
  they exist. 22 no-DB test functions (`tests/tasks/test_ftf_0407.py`,
  including three that construct a real `ToolDefinition` proving the
  guard runs before any database is touched, and an explicit no-regression
  test reproducing FTF-0406's own certified high-risk/non-critical/
  confirmed fixture shape) + 15 real live-Postgres checks
  (`tests/integration/test_ftf_0407_critical_behavior_requirements.py`, a
  real uvicorn server serving both the tools router and this task's own
  new requirements router). Own fault injection (Law 1, two, distinct
  vectors, both reverted byte-identical and reconfirmed clean in both
  suites, including over real Postgres): (1) disabled the live wiring in
  `ToolDefinition.__init__` — caught for real by the two named
  live-wiring tests and, in the live-Postgres suite, by a real downstream
  unique-constraint collision; (2) disabled the `requires_confirmation`
  conflict check inside the enforcement function itself — caught for real
  by the exact named negative_acceptance checks in both suites.
  Independently reconfirmed no regression to FTF-0401/FTF-0404/FTF-0405/
  FTF-0406's own certified no-DB suites (all four "all checks passed")
  and to FTF-0404's own live-Postgres suite (re-run "all checks passed"
  on a freshly recreated database/role). `ruff check`/`ruff format
  --check`/`mypy` clean on every file this task touched, and clean across
  the full `apps/api/app` tree (49 source files; the 2 remaining mypy
  errors are the pre-existing, unrelated `authlib.jose` stub gap,
  confirmed identical on `main` via `git stash`). `validate_spec.py
  --strict` clean (`git fetch --unshallow origin` first — this sandbox's
  own clone is shallow by default, the same pre-existing artifact every
  prior relay/certifying session has already documented).
  `product_truth_check.py --fail-on-unwired` — `9/9` before this shift,
  `10/10` after (this task's own `proving_symbol`,
  `enforce_critical_behavior_requirement`, has a real caller in
  `ToolDefinition.__init__` — not a TRACKER). PR opened as PROVISIONAL;
  `docs/spec-matrix.yaml` FTF-0407 set to `PROVISIONAL`/`L1-PROVEN`,
  `proving_symbol` `enforce_critical_behavior_requirement` (matching the
  FTF-0201/FTF-0301/FTF-0401/FTF-0404/FTF-0406 producer-shift precedent).
  Awaiting independent Control plane certification — not self-certified
  (Law 4). Certified stays 32/300; `scripts/status.py`'s own stricter
  "product-true" count (CERTIFIED-only) stays 9. Full submission report:
  `docs/certifications/queue.md`.

Previously, 2026-09-20 (scheduled Control-plane certifier shift, triggered by
  the `pull_request.opened` webhook for PR #153) — independent CERTIFY
  review of FTF-0406 (Side-effect classification): **CERTIFIED**. First
  pass found CI's real `gate` job genuinely failing on mypy in the PR's
  own new test helper — filed NOT CERTIFIED — but the producer session
  independently fixed the identical gap on the same PR branch before that
  review's own tracking PR finished CI; re-reviewed the fixed head from
  scratch (session-ID clean, CI reconfirmed green on the real job log,
  own fault injection distinct from the producer's own two, task contract
  re-pulled) and certified. Merged PR #153 (merge commit `80dcd4b2`).
  `docs/spec-matrix.yaml` set to `certification_state: CERTIFIED` /
  `proof_state: L2-CERTIFIED`. Certified: 32/300, Product-true: 9/9. Full
  review: `docs/certifications/ledger.md`/`docs/certifications/queue.md`.

Previously, 2026-09-20 (scheduled relay shift) — implemented FTF-0406
  (Side-effect classification, deps FTF-0401/FTF-0404/FTF-0405 all
  CERTIFIED). Recomputed the dependency-ready frontier fresh against
  `docs/spec-matrix.yaml`'s live certification state (never trusted a
  prior shift's own list, or PR #152's own still-unmerged claim, as
  proof by itself — independently re-derived the same answer by parsing
  every non-`CERTIFIED` task's own `deps=` field against the live
  `certification_state` set): FTF-0406 (CAP-05) and FTF-1501 (CAP-14)
  are the only two dependency-ready items; FTF-0406 has the lower CAP
  number and no external-service ask of its own.
  `apps/api/app/services/side_effect_classification.py` closes a real
  gap FTF-0404 left open: `ToolDefinition.side_effect_class` (Section
  6.9) was a closed five-value enum validated only as a recognized
  string, with nothing checking a caller's own claimed class against the
  tool's own actual definition — a tool named `delete_account` could be
  created with `side_effect_class="read_only"` and nothing refused it.
  `enforce_side_effect_classification` derives a real risk floor from
  the tool's own name/description/schema property-name tokens
  (deterministic keyword-root signals, never a model call or a
  client-supplied "trust me" flag) and refuses a claimed class below
  that floor, or a resolved class at or above `irreversible_write`
  claimed without `requires_confirmation=True`. This task's own
  negative_acceptance ("Unclassified or unresolved sensitive tool cannot
  default to read-only; user mandate text cannot disable actual guard")
  is enforced by construction: `read_only` is only accepted when real
  read-only evidence is present *and* no risk signal exists at all —
  absence of a risk signal is not, by itself, evidence of safety — and
  reassuring free text in `description` contributes no signal of its own
  kind and can never lower a floor a different structural signal already
  raised, nor disable the `requires_confirmation` guard (proven directly
  by a dedicated test using exactly that reassuring text against a
  `delete_account`-shaped tool). Real, live-wired: wired into
  `ToolDefinition.__init__` (source-inspection-proven, not merely
  imported), called on every `create_tool_definition`/
  `edit_tool_definition` — the already-CERTIFIED, already-live-wired
  FTF-0404 route, the same wiring point FTF-0405's own
  `validate_json_schema_document` already uses. `database_changes`
  honored ("No independent schema change") — consumes FTF-0404's own
  frozen `side_effect_class`/`requires_confirmation` columns exactly as
  they exist. 27 no-DB test functions in `tests/tasks/test_ftf_0406.py`,
  including three that construct a real `ToolDefinition` proving the
  guard runs before any session or database is touched, and that a
  correctly classified and confirmed tool still constructs successfully
  (no regression to FTF-0404's own certified default fixture shape). Own
  fault injection (Law 1, two, distinct vectors, both reverted
  byte-identical and re-confirmed clean afterward): (1) disabled the
  read_only/unresolved guard — caught for real by the exact named
  "unclassified tool...cannot default to read_only" check; (2) disabled
  the `requires_confirmation` guard — caught for real by all three
  checks that name it. Independently reconfirmed no regression to
  FTF-0401/FTF-0404/FTF-0405's own certified no-DB suites (all three
  re-run "all checks passed"). `ruff check`/`ruff format --check`/`mypy`
  clean on every file this task touched, and clean across the full
  `apps/api/app` tree (40 source files). `validate_spec.py --strict`
  clean (`git fetch --unshallow origin` first — this sandbox's own clone
  is shallow by default, the same pre-existing artifact every prior
  relay/certifying session has already documented).
  `product_truth_check.py --fail-on-unwired` — `8/8` before this shift,
  `9/9` after (this task's own `proving_symbol`,
  `enforce_side_effect_classification`, has a real caller in
  `ToolDefinition.__init__` — not a TRACKER). PR opened as PROVISIONAL;
  `docs/spec-matrix.yaml` FTF-0406 set to `PROVISIONAL`/`L1-PROVEN`,
  `proving_symbol` `enforce_side_effect_classification` (matching the
  FTF-0201/FTF-0301/FTF-0401/FTF-0404 producer-shift precedent). Awaiting
  independent Control plane certification — not self-certified (Law 4).
  Certified stays 31/300; `scripts/status.py`'s own stricter
  "product-true" count (CERTIFIED-only) stays 8. This shift also restores
  the two-commit tracking-doc precedent PR #148 (FTF-0405) skipped, per
  that PR's own certifier review's flagged process note below. Full
  submission report: `docs/certifications/queue.md`.

Previously, 2026-09-20 (scheduled Control-plane certifier shift, triggered by
  the `pull_request.opened` webhook for PR #148) — independent CERTIFY
  review of FTF-0405 (JSON Schema tool validation): **CERTIFIED**.
  Session-ID clean against the sole commit on PR #148
  (session_01ULAxA48EZT6uh2Kg6DziPL, not matching this reviewing
  session's own `session_01XbdXkopSW5Eh19JXpG2EBG`). Diff read directly:
  the new `apps/api/app/services/json_schema_tool_validation.py` is a
  pure, DB-free, network-free module implementing a pinned, safe subset
  of JSON Schema Draft 2020-12, and `tool_definition_schema.py`'s
  `validate_schema_object` now calls its `validate_json_schema_document`
  for both `input_schema_json`/`output_schema_json`, on the
  already-CERTIFIED, already-live-wired FTF-0404
  `ToolDefinition.__init__` path. CI reconfirmed green (16/16, including
  `gate`) on the real final head `9138f46`. Own reproduction of the no-DB
  suite in this sandbox ("all checks passed") and of FTF-0404's own no-DB
  suite (no regression). Two own fault injections, distinct from the
  producer's own two: disabling the non-local-`$ref` rejection (caught
  for real by the "remote schema SSRF" negative_acceptance case) and
  disabling the `additionalProperties: false` excess-property rejection
  (caught for real by the "excess arguments" negative_acceptance case) —
  both reverted byte-identical, both suites re-run clean. Full repo-wide
  regression re-run clean aside from FTF-0002/FTF-0003/FTF-H001's own
  frontend-tooling-dependent checks, independently confirmed pre-existing
  on `main` before this PR (no regression). `product_truth_check.py
  --fail-on-unwired` correctly rises `7/7` -> `8/8` once this entry is
  certified (a real, newly-counted live caller, not inflation). Task
  contract re-pulled via `tools/r1_handoff.py packet FTF-0405`,
  confirming acceptance clause 1 and all four negative_acceptance cases
  are genuinely met. Merged PR #148 (merge commit `82e2851`).
  `docs/spec-matrix.yaml` set to `certification_state: CERTIFIED` /
  `proof_state: L2-CERTIFIED`. Certified: 31/300, Product-true: 8/8. Full
  review: `docs/certifications/ledger.md` (FTF-0405 entry).

Previously, 2026-09-20 (scheduled Control-plane certifier shift, triggered
  by the `pull_request.opened` webhook for PR #146) — independent CERTIFY
  review of FTF-0404 (Tool schema CRUD): **CERTIFIED**. Session-ID clean
  against all 3 commits on PR #146 (session_01Erh4hdf73yUo4Q36YKTshz, not
  matching this reviewing session's own ID). Diff read directly. CI
  reconfirmed green (16/16, including the new `tool-schema-crud-integration`
  job) on the real final head `caec978f` (matching the PR's own current
  head, `mergeable_state: clean`). Own reproduction of both suites directly
  in this sandbox: the no-DB suite run via its documented direct-script
  invocation (`python3 tests/tasks/test_ftf_0404.py`) — running it under
  pytest instead, as this review first tried, is misleading: this file's
  own `check()`/`FAILURES` pattern never raises, so pytest reports every
  `test_*` function as passed regardless of whether the underlying
  condition held; the correct invocation is the direct script, matching
  exactly what CI's own workflow step runs — and the live-Postgres suite
  against a freshly created local PostgreSQL 16 database/role (migration
  run for real, `sovra_app` role created by the migration itself). Both
  "all checks passed". Own two fault injections, both reverted
  byte-identical afterward (`git diff --stat` clean) and both suites
  re-run clean: (1) replaced `apply_new_version`'s own `superseded_at`
  guard with `if False:` — caught for real by the no-DB suite's named
  negative_acceptance check; (2) mutated the historical row's own
  `input_schema_json` inside `apply_new_version` after marking it
  superseded — caught for real by both the no-DB suite's and the
  live-Postgres suite's own named negative_acceptance checks. No
  regression: re-ran FTF-0401's and FTF-0402's own no-DB and
  live-Postgres suites against freshly recreated databases/roles — both
  still "all checks passed". Full repo-wide regression re-run clean in
  this sandbox after installing `node_modules` (absent by default in this
  sandbox — not a defect in the PR, an environment gap in this review's
  own container): `ruff check`/`ruff format --check`/`mypy` (118 source
  files), `pnpm run lint`/`typecheck`/`format:check`, `pnpm exec vitest
  run` (14/14), `pytest tests/unit` (3/3), every `tests/tasks/test_ftf_*.py`
  file (26/26), `validate_spec.py --strict`, `product_truth_check.py
  --fail-on-unwired` reconfirmed `7/7`. Task contract cross-checked via
  `tools/r1_handoff.py packet FTF-0404 --plan FINE_TUNING_FACTORY_RELEASE_1.md`
  — acceptance/negative_acceptance clauses match the PR's own claims, and
  the dependency (FTF-0401) is confirmed genuinely `CERTIFIED` in
  `docs/spec-matrix.yaml` before relying on it. Minor evidence-accuracy
  note (flagged, not blocking): the PR's own `docs/certifications/queue.md`
  entry claims "22 no-DB test functions" and "20 real live-Postgres
  checks" — this review counted 19 `test_*` functions in
  `tests/tasks/test_ftf_0404.py` and 24 `check()` call sites in
  `tests/integration/test_ftf_0404_tool_schema_crud.py`. Every claimed
  behavior and both named fault injections were independently verified as
  real; only the raw counts in the write-up are off — relay's next shift
  should hold counts to what `grep -c` actually shows before stating them.
  Merged PR #146 (merge commit `d06199626628c5f9770b756ff7aa202fea02110f`).
  `docs/spec-matrix.yaml` set to `certification_state: CERTIFIED` /
  `proof_state: L2-CERTIFIED`. Certified: 30/300, Product-true: 7/7. Full
  review: `docs/certifications/ledger.md` (FTF-0404 entry) and
  `docs/certifications/queue.md`.

Earlier the same date (scheduled relay shift) — implemented FTF-0404 (Tool
  schema CRUD, dep FTF-0401, CERTIFIED). Recomputed the dependency-ready
  frontier fresh against `docs/spec-matrix.yaml`'s live certification
  state (never trusted a prior shift's own list as still current):
  FTF-0403, FTF-0404, FTF-0902, FTF-1001, FTF-1401, FTF-1501 (FTF-0202
  deliberately excluded from the pick — see the flagged note under
  "Owner actions — current" below; its ask is NOT genuinely resolved).
  FTF-0404 has the lowest CAP number (CAP-05) with no external-service
  ask of its own. Real, live-wired
  `/api/v1/organizations/{organization_id}/projects/{project_id}/tools`
  routes (the fifth live-wired product-runtime path in this repository).
  `apps/api/app/tools/{tool_definition_schema,tool_definition_crud,
  tool_definition_routes}.py` (refined from the packet's own single
  abbreviated `apps/api/app/services/tool_schema_crud.py` suggestion into
  the same schema/crud/routes triplet FTF-0201/FTF-0301/FTF-0401 already
  established for a real new resource — the packet's own single-file
  `files` listing is this repo's own standard abbreviated-ticket shape,
  not a literal constraint; its own text explicitly warns "Do not
  scatter domain logic into one file per ticket simply because a ticket
  exists"). Real forward Alembic migration
  (`apps/api/alembic/versions/0006_tool_definitions.py`), FORCE-RLS
  scoped to the same tenant context every prior migration in this chain
  established, plus a composite `(organization_id, project_id)` ->
  `projects(organization_id, id)` foreign key (a purely additive
  `UNIQUE(organization_id, id)` on the already-CERTIFIED `projects`
  table, the same precedent FTF-0401's own migration applied to
  `workspaces`). This task's own negative_acceptance ("Editing used tool
  schema creates new version and drift, never changes historical
  manifest in place") is enforced by construction: `apply_new_version`
  never mutates the row it replaces except to set
  `superseded_at`/`updated_at`, and a real partial unique index
  (`(project_id, name) WHERE superseded_at IS NULL`) enforces "one
  current version per tool name per project" at the database level, not
  only in application code. Contracts extended under the existing
  contract-owner lock (`contracts:04`, owned by FTF-0401) rather than a
  new file — new `/tools` paths and
  `ToolDefinition`/`ToolDefinitionCreateRequest`/`ToolDefinitionEditRequest`
  schemas appended to `packages/contracts/openapi/04-interface.yaml` and
  `packages/contracts/jsonschema/04-domain.schema.json`; `/requirements`
  is still disclosed as not yet declared (FTF-0407/FTF-0408's own, later
  scope). 22 no-DB test functions (including a real locally-hosted
  `build_tool_definition_router` over a real uvicorn server) + 20 real
  live-Postgres checks (two real organizations, three real projects, a
  real uvicorn server proving create → duplicate-current-name rejected
  by the real DB constraint → edit → version 2 with the historical
  version 1 fetched byte-identical by its own ID afterward → re-editing
  version 1 directly refused (409) → cross-project IDOR denied →
  cross-organization IDOR denied → grant-surface DELETE refused); two own
  fault injections (removing the "only the current version can be
  edited" guard; mutating the historical row's own `input_schema_json`
  during an edit), both genuinely caught by both suites and reverted
  byte-identical. Independently reconfirmed no regression to FTF-0401's
  and FTF-0402's own certified no-DB/live-Postgres suites (both still
  "all checks passed" against a freshly recreated database/role after
  this task's own migration). `product_truth_check.py --fail-on-unwired`
  now `7/7` (this task's own `proving_symbol`, `build_tool_definition_router`,
  has a real caller in `apps/api/app/main.py`). PR opened as PROVISIONAL;
  `docs/spec-matrix.yaml` FTF-0404 set to `PROVISIONAL`/`L1-PROVEN`,
  `proving_symbol` `build_tool_definition_router` (matching the
  FTF-0201/FTF-0301/FTF-0401/FTF-0402 producer-shift precedent). Awaiting
  independent Control plane certification — not self-certified (Law 4).
  Certified stays 29/300; `scripts/status.py`'s own stricter
  "product-true" count (CERTIFIED-only) stays 6. Full submission report:
  `docs/certifications/queue.md`.

**Flagged this shift, not silently acted on** (Law 9, gate-weakening is
watched — this needed surfacing, not quiet compliance): PR #143
("Owner decision: FTF-0202 backend selected (OpenBao)") is open and
unmerged against `main`. It claims "Owner confirmed selection of
OpenBao" for FTF-0202's own new-runtime-dependency/keyed-external-service
ask (a CLAUDE.md hard stop), but — unlike every genuine resolution
already recorded in this file's own history (FTF-0101's stack ask,
FTF-0101's local-OIDC-provider ask, FTF-0102's ORM ask — each of which
names a specific live conversation, a specific option number, and the
specific reasoning tying it back to the architecture docs) — PR #143's
own body and diff carry no such detail: no live-conversation citation,
no option enumerated, nothing beyond the bare assertion itself, and it
deletes the entire existing, reasoned "Owner actions — current" analysis
outright rather than resolving it against evidence. This session found
no corroborating record anywhere in `PROGRESS.md`/`QUEUE.md` on `main` of
an actual owner exchange about OpenBao. This is exactly the class of
"unearned status claim" this file's own CLAUDE.md `BRANCH_PROTECTION`
note already tracks (two prior sessions each pushed an unearned
"gap resolved" claim). This relay shift did **not** merge PR #143, did
**not** treat FTF-0202 as unblocked, and did **not** pick FTF-0202 this
shift as a result — the ask below is left exactly as the last genuine
session recorded it. This is a Control-plane/owner judgment call, not a
relay one: recommend the owner either confirm directly (in which case
merge PR #143 with that confirmation recorded, matching this file's own
evidence bar) or close PR #143 without merging.

## Next shift
FTF-1401 (this shift's own submission, see the top of this file) is now
PROVISIONAL, PR opened, awaiting independent Control-plane L2
certification — that review is the natural next action, matching the
established relay/certifier alternation. If a certifier session is not
what fires next: recompute the dependency-ready frontier fresh rather
than trusting this note. Three other items already have open,
not-yet-merged PRs from other recent relay shifts as of this shift — PR
#158 (FTF-0403), #159 (FTF-1501), #160 (FTF-1001) — check GitHub directly
before re-doing any of them. Besides those three and FTF-0202's own owner
ask, this shift found FTF-0902 (requires a real enforcing Kubernetes
cluster for CNI/firewall/DNS/IPv6 acceptance evidence that no session's
sandbox has reached so far — treat as BLOCKED_EXTERNAL, not silently
attempted with a fake pass, unless a future session genuinely has that
access) and FTF-1701 ("Reference agent specification", newly unblocked
now that FTF-0407 is CERTIFIED — its own Section 66 runtime-adapter scope
was not attempted this shift; a future shift should read its task packet
fresh before starting). Also flagged this shift, not fixed (a
Control-plane/owner judgment call, not a relay one): FTF-0406/FTF-0407's
own no-DB and live-Postgres suites are not wired into
`.github/workflows/ci.yml` — see the top of this file and
`docs/certifications/queue.md`'s own FTF-1401 entry for the full finding.
Before merging PR #143 (still open and unmerged as
of this shift), verify a genuine owner confirmation exists for FTF-0202's
own backend selection (see the flagged note above) — do not treat that
PR's own bare claim as sufficient by itself. Separately, PR #152 (a
doc-only "Next shift" catch-up, opened by an earlier shift, already
superseded before this shift began) is still open/unmerged — a future
certifier session should close it without merging.

Earlier the same date (scheduled Control-plane certifier shift, triggered by
  the `pull_request.opened` webhook for PR #144) — independent CERTIFY
  review of FTF-0305 (License acceptance workflow): **CERTIFIED**.
  Session-ID clean against all 3 commits on PR #144
  (session_01CW8M8Mir1PpW62RZm8ZLJz, not matching this reviewing session's
  own ID), diff read directly confirming `artifact_crud.py`'s new
  `revoke_license` and `artifact_routes.py`'s new `revoke-license` route
  are the real, live-wired callers of `license_acceptance_workflow.py`'s
  pure `apply_revoke_license`, and every pre-existing FTF-0301 operation
  untouched. CI reconfirmed green (15/15, including the new
  `license-acceptance-workflow-integration` job) on the real final head
  `ea50eac` (polled to genuine completion via a PR-activity subscription,
  not the in-progress snapshot from when the PR first opened — 2 jobs were
  still running when first checked). Own reproduction of the no-DB suite
  in this sandbox ("all checks passed"; the live-Postgres suite itself was
  verified via the real CI job, no container-registry egress in this
  sandbox, same disclosed limitation every prior integration-tested task
  has documented), one own fault injection (the `ready`→`registered`
  demotion removed from `apply_revoke_license`) caught for real — matching
  the exact two named negative_acceptance cases — and reverted
  byte-identical, no regression in FTF-0301's own no-DB suite,
  `product_truth_check.py --fail-on-unwired` reconfirmed `6/6`, task
  contract cross-checked via `tools/r1_handoff.py packet FTF-0305`
  (confirming the disclosed scope narrowing — revocation only, no live
  training/serving caller yet — matches the same disclosed-gap precedent
  already independently `CERTIFIED` for FTF-0103/FTF-0104/FTF-0105/
  FTF-H003, not a hidden gap). Merged PR #144 (merge commit
  `1a3c6b42785cf89a18c7f4c68138f224f1787574`). `docs/spec-matrix.yaml` set
  to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`.
  Certified: 29/300, Product-true: 6/6. Full review:
  `docs/certifications/ledger.md` (FTF-0305 entry) and
  `docs/certifications/queue.md`.

Earlier the same date (scheduled relay shift) — implemented FTF-0305
  (License acceptance workflow, dep FTF-0301, CERTIFIED). Recomputed the
  dependency-ready frontier fresh (never trusted the prior shift's own
  list as still current): FTF-0202 (owner ask, unchanged), FTF-0305,
  FTF-0403, FTF-0404, FTF-0902, FTF-1001, FTF-1401, FTF-1501. FTF-0305 has
  the lowest CAP number (CAP-06) with no external-service ask of its own.
  `apps/api/app/artifacts/license_acceptance_workflow.py` (refined from
  the packet's own unimportable/hyphenated
  `packages/model-catalog/license_acceptance_workflow.py` suggestion, the
  same refinement precedent FTF-0301 already established) adds the one
  sanctioned training/serving admission gate
  (`assert_license_accepted_for_use`, no live caller yet — disclosed) and
  real revocation (`apply_revoke_license`) — FTF-0301 already stored
  acceptance but had no way to withdraw it. Revoking a `ready` artifact
  demotes it back to `registered`, so any caller gating real work on
  `status == "ready"` is automatically excluded, closing this task's own
  negative_acceptance ("Unaccepted/revoked license blocks
  training/serving"). `apps/api/app/artifacts/artifact_crud.py` gains one
  purely additive new function, `revoke_license`, never touching
  `apply_edits`/`create_artifact`/`accept_license`/`mark_ready`/
  `archive_artifact`'s own existing bodies;
  `apps/api/app/artifacts/artifact_routes.py` gains one new route,
  `POST .../artifacts/{artifact_id}/revoke-license`, on the same
  already-live-wired router. `database_changes` honored ("No independent
  schema change") — reuses FTF-0301's own nullable
  `license_accepted_by`/`license_accepted_at` columns, no new migration.
  An earlier implementation attempt also auto-invalidated acceptance
  inside `apply_edits` on an identity edit, but running FTF-0301's own
  live-Postgres suite against it surfaced a real regression (that suite's
  own certified flow accepts a license, then pins `base_model_revision`,
  then expects `mark-ready` to still succeed) — a Law 15 situation
  outside this task's own charter; reverted before ever being committed,
  and "acceptance bound to model revision" is instead satisfied by
  construction (the acceptance record lives on the same row as
  `base_model_revision`). 15 no-DB test functions (including a real
  locally-hosted router over a real uvicorn server for the new route's
  own role-boundary proof) + 15 real live-Postgres checks (two real
  organizations, a real uvicorn server proving accept → resolve →
  mark-ready → revoke → mark-ready refused again, the foreign-artifact
  IDOR case on the new route, revoke idempotency, and revoking an
  archived artifact's license refused); two own fault injections, both
  caught for real, reverted byte-identical. Independently reconfirmed no
  regression to FTF-0301's own certified no-DB/live-Postgres suites (both
  still "all checks passed" on a freshly recreated database/role). PR
  opened as PROVISIONAL; `docs/spec-matrix.yaml` FTF-0305 set to
  `PROVISIONAL`/`L1-PROVEN`, `proving_symbol` `apply_revoke_license`
  (matching the FTF-0201/FTF-0301/FTF-0401/FTF-0402 producer-shift
  precedent). Awaiting independent Control plane certification — not
  self-certified (Law 4). Certified stays 28/300;
  `product_truth_check.py --fail-on-unwired`: `6/6`; `scripts/status.py`'s
  own stricter "product-true" count (CERTIFIED-only) stays 5. Full
  submission report: `docs/certifications/queue.md`.

Earlier the same date (scheduled Control-plane certifier shift, triggered by
  the `pull_request.opened` webhook for PR #141) — independent CERTIFY
  review of FTF-0402 (Project security profile): **CERTIFIED**. Session-ID
  clean against all 3 commits on PR #141 (session_01YLCGLfbyuEcZGJTe7LWQAL,
  not matching this reviewing session's own ID), diff read directly
  confirming `create_project`/`update_project` both call
  `enforce_security_profile_selection` against the calling organization's
  own real `default_security_profile` before a project's `security_profile`
  is ever persisted, and `apply_edits` itself untouched. CI reconfirmed
  green (14/14) on the real final head `b8cd86d` (polled to genuine
  completion, not the in-progress snapshot from when the PR first opened).
  Own reproduction against a real local PostgreSQL 16 cluster (both suites
  "all checks passed"), two own fault injections (the relaxation check in
  `enforce_security_profile_selection` disabled; the enforcement call
  removed from `create_project`) each caught for real — matching the exact
  named negative_acceptance cases — and reverted byte-identical, no
  regression in FTF-0401's own suites, `product_truth_check.py
  --fail-on-unwired` reconfirmed `5/5`, task contract cross-checked via
  `tools/r1_handoff.py packet FTF-0402` (confirming the disclosed
  narrowing of acceptance clause 1's six future consuming surfaces matches
  the same disclosed-gap precedent already independently `CERTIFIED` for
  FTF-0103/FTF-0104/FTF-0105/FTF-H003/FTF-0301/FTF-0401, not a hidden
  gap). Merged PR #141 (merge commit
  `a6ef1d9ad9657cb3a07e1959f4be00a1fb3e14ff`). `docs/spec-matrix.yaml` set
  to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`.
  Certified: 28/300, Product-true: 5/5. Full review:
  `docs/certifications/ledger.md` (FTF-0402 entry) and
  `docs/certifications/queue.md`.

Earlier the same date (scheduled relay shift) — implemented FTF-0402
  (Project security profile, dep FTF-0401, CERTIFIED). Of the 8
  dependency-ready items this shift found (FTF-0202 already logged as an
  owner ask below; FTF-0305/FTF-0402/FTF-0403/FTF-0404/FTF-1001/FTF-1401/
  FTF-1501), FTF-0402 has the lowest CAP number with no external-service
  ask of its own and unblocks the most downstream work (FTF-0408/0500D/
  0514/0617/0901/0902/1206 all name it as a dependency). Section 6.1/6.8's
  own `default_security_profile`/`security_profile` columns were typed as
  opaque strings with zero enforcement; `apps/api/app/services/
  project_security_profile.py` makes that real with a closed, ordered
  taxonomy (`standard < zero_egress < air_gapped`, grounded in this
  specification's own text — Law 17/Section 11.1's ZERO_EGRESS, and
  AIR_GAPPED explicitly described as adding "a deployment constraint to
  the existing ZERO_EGRESS policy" — not invented) plus
  `enforce_security_profile_selection`, this task's own
  negative_acceptance ("User cannot relax inherited organization policy
  by changing a project field or using direct API") made real.
  `apps/api/app/projects/project_crud.py`'s `create_project`/
  `update_project` (FTF-0401, already CERTIFIED) now fetch the calling
  organization's own real `default_security_profile` and call it before
  a project's `security_profile` is ever persisted, at both creation and
  edit — closing the exact gap `project_routes.py`'s own module docstring
  already disclosed. `apply_edits` itself is untouched, so this adds no
  regression to FTF-0401's own certified evidence (independently
  reconfirmed: its own no-DB and live-Postgres suites both still "all
  checks passed" after this change). 15 no-DB test functions (23 checks,
  including source-inspection proof the new primitive is genuinely called,
  not merely imported) + 15 real live-Postgres checks (two organizations
  seeded with two different real `default_security_profile` values,
  proving the negative_acceptance case at both creation and edit against
  a real uvicorn server over FTF-0401's own `build_project_router`); two
  own fault injections, both caught for real, reverted byte-identical.
  PR opened as PROVISIONAL; `docs/spec-matrix.yaml` FTF-0402 set to
  `PROVISIONAL`/`L1-PROVEN`, `proving_symbol`
  `enforce_security_profile_selection` (matching the FTF-0201/FTF-0301/
  FTF-0401 producer-shift precedent). Awaiting independent Control plane
  certification — not self-certified (Law 4). Certified stays 27/300;
  `product_truth_check.py --fail-on-unwired`: `5/5`; `scripts/status.py`'s
  own stricter "product-true" count (CERTIFIED-only) stays 4. Full
  submission report: `docs/certifications/queue.md`.

Earlier the same date (scheduled Control-plane certifier shift, triggered by
  the `pull_request.opened` webhook for PR #139) — independent CERTIFY
  review of FTF-0401 (Project schema/API/UI): **CERTIFIED**. Session-ID
  clean against all 3 commits on PR #139 (session_01HCDh4VoHfgGL9NkLs6Q4YG,
  not matching this reviewing session's own ID), diff read directly
  confirming `build_project_router` wired unconditionally into
  `apps/api/app/main.py`, `apply_edits` has no `project_type` parameter at
  all, and `create_project` authorizes the workspace before insert, CI
  reconfirmed green (13/13) on the real final head `5b4dc4c`, own
  reproduction against a real local PostgreSQL 16 cluster (both suites
  "all checks passed"), two own fault injections (agentic-always-active;
  workspace-ownership check removed) each caught for real and reverted
  byte-identical, `product_truth_check.py --fail-on-unwired` reconfirmed
  `4/4`, task contract cross-checked via `tools/r1_handoff.py packet
  FTF-0401` (including confirming `/requirements`/`/tools` are genuinely
  FTF-0404/FTF-0407/FTF-0408's own later scope via `docs/TASK_INDEX.md`,
  not a hidden gap). Merged PR #139 (merge commit
  `59e14e71a446bbc68b8488ecc221ae5877aba14a`). `docs/spec-matrix.yaml` set
  to `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`.
  Certified: 27/300, Product-true: 4/4. Full review:
  `docs/certifications/ledger.md` (FTF-0401 entry) and
  `docs/certifications/queue.md`.

Earlier the same date (scheduled relay shift) — implemented FTF-0401
  (Project schema/API/UI, deps FTF-0102/FTF-0103/FTF-0104 all CERTIFIED).
  FTF-0202 ("Secret-manager integration") also became dependency-ready
  this shift but names no concrete secret backend anywhere in this
  repository's architecture docs (grepped
  `docs/architecture/REFERENCE_REPOSITORIES.md`/`SPEC.md`/this plan's own
  Section 77/81 seven-component list — a secret manager is not among
  them; only a `vault://`/`openbao://`/`secret://` reference-string
  *shape* convention already exists, which fixes the shape of a stored
  reference, not the backend it resolves against) — a new-runtime-
  dependency/keyed-external-service ask-first item per CLAUDE.md, logged
  below instead of picked. Real, live-wired
  `/api/v1/organizations/{organization_id}/projects` routes (the FOURTH
  live-wired product-runtime path); FORCE-RLS migration with a real
  composite foreign key into `workspaces`; CRUD composed entirely from
  already-certified primitives; a real React form component. This task's
  own negative_acceptance ("Agentic project without approved target/
  runtime remains setup-incomplete; no silent generic downgrade") is
  enforced by construction — `project_type` is immutable, and an agentic
  project always starts `setup_incomplete`, proven both at the
  pure-function level and over a real HTTP round-trip. 22 no-DB test
  functions (45 checks) + 32 live-Postgres checks + 9 component/DOM
  checks; three own fault injections, all caught for real, reverted
  byte-identical. PR opened as PROVISIONAL; `docs/spec-matrix.yaml`
  FTF-0401 set to `PROVISIONAL`/`L1-PROVEN`, `proving_symbol`
  `build_project_router` (set at this PROVISIONAL submission, matching
  the FTF-0201/FTF-0301 producer-shift precedent). Awaiting independent
  Control plane certification — not self-certified (Law 4). Certified
  stays 26/300; `product_truth_check.py --fail-on-unwired`: 4/4;
  `scripts/status.py`'s own stricter "product-true" count (CERTIFIED-only)
  stays 3. Full submission report: `docs/certifications/queue.md`.

Earlier the same date (scheduled Control-plane certifier shift, triggered by
  the `pull_request.opened` webhook for PR #137) — independent CERTIFY
  review of FTF-0301 (TrainableArtifact schema/API): **CERTIFIED**.
  Session-ID clean against all 6 commits on PR #137 (session_01UFUfcwwet85jUWc3BNVoJZ/
  session_011RJ3Ct8ASzprr1uy78r6po, neither matching this reviewing
  session's own ID), CI reconfirmed green (12/12) on the real final head
  `9bc4923`, own reproduction against a real local PostgreSQL 16 cluster
  (both suites "all checks passed"), own fault injection reproducing the
  prior NOT CERTIFIED review's exact fault caught for real by the new
  isolated case in both suites and reverted byte-identical,
  `product_truth_check.py --fail-on-unwired` reconfirmed `3/3`, task
  contract cross-checked via `tools/r1_handoff.py packet FTF-0301`. Merged
  PR #137 (merge commit `634d59775253600a83fb0ff7c94793fa4fbb89de`),
  closed PR #135 (the superseded first submission) with a comment.
  `docs/spec-matrix.yaml` set to `certification_state: CERTIFIED` /
  `proof_state: L2-CERTIFIED`. Certified: 26/300, Product-true: 3/3. Full
  review: `docs/certifications/ledger.md` (FTF-0301 entry) and
  `docs/certifications/queue.md`.

Earlier the same date (scheduled relay shift) — picked up the exact
  "Action needed" item from the prior Control-plane certifier shift's own
  FTF-0301 (PR #135) NOT CERTIFIED review below: added the one missing
  isolated live-Postgres negative case for the license-acceptance
  ("unknown owner") leg of `apply_mark_ready`, matching the review's own
  named fix exactly. Own reproduction of the certifier's exact fault
  injection confirmed the new case is the only one that catches it (1
  FAIL), reverted byte-identical. No production code changed. This
  session's own harness assigns a fresh branch per shift and does not
  permit pushing to a different branch, so — matching this repo's own
  established supersession precedent (FTF-1601's #90→#96→#100 chain,
  FTF-0101's #103→#112→#114 chain) — the fix landed as a new commit
  (`86397f123998a251ea7c459770290807f00c86da`) on this shift's own branch,
  built on PR #135's real head merged cleanly with `main`, and opened as a
  new PR superseding PR #135 (left open, to be closed on merge). Full
  submission: `docs/certifications/queue.md`'s new "Relay follow-up"
  entry directly below the NOT CERTIFIED review it responds to. Submitted
  PROVISIONAL — not self-certified (Law 4); awaiting a fresh independent
  Control-plane L2 review.

Earlier the same date (scheduled Control-plane certifier shift, triggered by
  the `pull_request.opened` webhook for PR #135) — independent CERTIFY
  review of FTF-0301 (TrainableArtifact schema/API): **NOT CERTIFIED**.
  Session-ID clean against all 3 commits on PR #135
  (session_01UFUfcwwet85jUWc3BNVoJZ, not matching this reviewing
  session's own ID), CI reconfirmed green (12/12) on the real final head,
  own independent fault injection distinct from the producer's own
  (disabled only the "unknown owner" leg of `apply_mark_ready`'s
  license-acceptance check) caught for real by the no-DB suite but
  **not** by the live-Postgres integration suite — a real Law 1 gap: that
  suite's own mark-ready sequence accepts the license immediately and
  never revisits it, so it never isolates that one leg the way it
  already deliberately isolates the mutable-revision leg. Shipped
  production code is correct (confirmed directly); this is a
  test-coverage gap, not a logic defect. Not merged, not certified per
  Law 4/Law 1. `docs/spec-matrix.yaml` for FTF-0301 stays `NONE` on
  `main` (PR #135 unmerged, nothing to downgrade). **Action needed:** add
  one isolated live-Postgres negative case for the license/owner leg to
  `tests/integration/test_ftf_0301_trainable_artifact_schema_api.py`
  (exact test shape specified in `docs/certifications/queue.md`'s own
  FTF-0301 NOT CERTIFIED entry), push to PR #135's own branch
  (`claude/wizardly-fermat-h7e3n7`), confirm CI green, resubmit. Full
  review: `docs/certifications/queue.md`.

Earlier the same date (scheduled Control-plane certifier shift, triggered by
  the `pull_request.opened` webhook for PR #133) — independent CERTIFY
  review of FTF-0201 (LiteLLM connection CRUD): **CERTIFIED**. Session-ID
  clean against all 7 commits on PR #133 (session_01MV1wn1CJHoHmds4eRKKVaV,
  none matching this reviewing session's own ID), CI reconfirmed green
  (11/11, including the new `litellm-connection-crud-integration` job) on
  the real final head `d0aec3b`, own independent fault injection (widened
  `validate_base_url`'s allowed scheme to accept `http`, a real
  TLS-downgrade bug) caught for real by both suites and reverted
  byte-identical, `product_truth_check.py --fail-on-unwired` reconfirmed
  `2/2`, task contract cross-checked via `tools/r1_handoff.py packet
  FTF-0201`. Merged PR #133 (merge commit `3588e4e5e8f0fcb81f0f458869f2d1e252ea1305`).
  `docs/spec-matrix.yaml` set to `certification_state: CERTIFIED` /
  `proof_state: L2-CERTIFIED`. Certified: 25/300, Product-true: 2/2. Full
  review: `docs/certifications/ledger.md` (FTF-0201 entry) and
  `docs/certifications/queue.md`.

Earlier the same date (scheduled relay shift) — implemented FTF-0201
  (LiteLLM connection CRUD, deps FTF-0103/FTF-0104/FTF-0005 all
  CERTIFIED, DoD pulled fresh via `tools/r1_handoff.py packet FTF-0201`).
  Scope narrowed honestly to create/get/list/edit/revoke only (no
  `validate`/`sync-models`/`syncs`/`/models`/`/models/{id}/probe` route,
  even as a stub — those are FTF-0203/FTF-0204/FTF-0210's own, later,
  separately dependency-gated tasks; declaring that surface with no real
  backing handler would be a placeholder endpoint, Law 11). Real,
  live-wired `/api/v1/organizations/{organization_id}/connections/litellm`
  routes (`apps/api/app/litellm/connection_routes.py`), gated
  `organization_admin`-only per Section 45's own literal role table;
  domain schema + FORCE-RLS migration
  (`apps/api/app/litellm/connection_schema.py`,
  `apps/api/alembic/versions/0003_litellm_connections.py`); CRUD module
  composed entirely from already-certified primitives
  (`TenantContext`/`tenant_scoped_session_for`/`authorize_resource_ownership`/
  `execute_tenant_scoped`/`write_audit_event`); `contracts:02` frozen
  (`packages/contracts/{openapi,jsonschema}/02-*`). 48 no-DB checks +
  28 real live-Postgres checks, two own fault injections (widened
  `CONNECTION_ADMIN_ROLES`; `secret_ref` leaked back into the response
  serializer), both genuinely caught by both suites and reverted
  byte-identical. `product_truth_check.py --fail-on-unwired` now
  `2/2` — this is the SECOND live-wired product-runtime path in this
  repository, after FTF-0101. PR opened as PROVISIONAL;
  `docs/spec-matrix.yaml` FTF-0201 set to `PROVISIONAL`/`L1-PROVEN`.
  Awaiting independent Control plane certification — not self-certified
  (Law 4). Full submission report: `docs/certifications/queue.md`.

Earlier the same date (scheduled Control-plane certifier shift, triggered by
  the `pull_request.opened` webhook for PR #127) — independent CERTIFY
  review of FTF-0106 (RBAC/IDOR security tests): **CERTIFIED**. Session-ID
  clean against all 4 commits on PR #127, CI reconfirmed green (10/10,
  including the new `rbac-idor-security-integration` job) on the real
  final head, two own independent fault injections (`APPROVE_ROLES`
  widened to include `viewer`, caught for real by the no-DB suite;
  approve-route dependency aliased to `write_dependency`, caught for real
  by the live-Postgres integration suite), `product_truth_check.py`'s
  `1/1` confirmed legitimate (empty `proving_symbol`, no live-wiring
  claim), and the real task contract (`tools/r1_handoff.py packet
  FTF-0106`) confirms both the acceptance clause and the negative_
  acceptance clause are met for this task's own scope. Merged PR #127
  (merge commit `e21df28`); `docs/spec-matrix.yaml` set to
  `CERTIFIED`/`L2-CERTIFIED`; full entry in `docs/certifications/ledger.md`.
  This certification's own doc updates are pushed on a branch and opened
  as a PR against `main`, not pushed directly, per `BRANCH_PROTECTION`.
  Full review history: `docs/certifications/queue.md`.

Earlier the same date (scheduled relay shift) — picked up the next
  unblocked item once FTF-H003 was independently CERTIFIED (FTF-0106,
  RBAC/IDOR security tests — its own deps FTF-0102/FTF-0103/FTF-0104/
  FTF-0105/FTF-H003 all CERTIFIED). Full submission report:
  docs/certifications/queue.md.

Earlier the same date (scheduled Control-plane certifier shift, triggered by
  the `pull_request.opened` webhook for PR #125) — independent CERTIFY
  review of FTF-H003 (Cross-layer tenant isolation and relational
  identity): **CERTIFIED**. Session-ID clean against all 3 commits on PR
  #125, CI reconfirmed green (9/9, including the new
  `ftf-h003-cross-layer-isolation-integration` job) on the real final
  head, two own independent fault injections (storage-key foreign-tenant
  equality bypass, caught for real; `authorize_resource_ownership`'s
  app-layer row-ownership check dropped, confirmed as defense-in-depth
  behind FTF-0102's own FORCE-RLS rather than a coverage gap),
  `product_truth_check.py`'s `1/1` confirmed legitimate (empty
  `proving_symbol`, no live-wiring claim), and the real task contract
  (`tools/r1_handoff.py packet FTF-H003`) confirms both the acceptance
  clause and the negative_acceptance clause are met for this task's own
  scope. Merged PR #125 (merge commit `9407710`);
  `docs/spec-matrix.yaml` set to `CERTIFIED`/`L2-CERTIFIED`; full entry in
  `docs/certifications/ledger.md`. This certification's own doc updates
  are pushed on a branch and opened as a PR against `main`, not pushed
  directly, per `BRANCH_PROTECTION` — see the ledger entry's step 10 for
  why that's noted explicitly this time. Full review history:
  `docs/certifications/queue.md`.

Earlier the same date (2026-09-20, scheduled relay shift) — picked up the next unblocked
  item per the prior shift's own dependency graph. FTF-0106 (RBAC/IDOR
  security tests) is next in CAP-01 order but depends on FTF-H003
  (Cross-layer tenant isolation and relational identity), which was still
  `ABSENT`/`NONE` — not itself dependency-ready. FTF-H003's own
  dependencies (FTF-0102/FTF-0103/FTF-0104/FTF-H001) are all CERTIFIED, so
  this shift implemented FTF-H003 instead: a cross-layer IDOR guard
  (`apps/api/app/auth/resource_authorization.py`) closing the real gap
  FTF-0103's own `require_role` cannot see by construction (it authorizes
  role+tenant from the URL path but never checks whether some other
  resource ID in that same path actually belongs to that tenant), a
  canonical `TenantContext` type (`apps/api/app/db/tenant_context.py`) and
  a tenant-scoped object-storage key policy
  (`packages/storage/tenant_policy.py`). Real live-Postgres evidence
  (two real organizations, pooled sessions, a real uvicorn route) proves
  read/write/download/job/route denial including guessed IDs and missing
  context — see `docs/certifications/queue.md` for the full submission
  entry. PR opened as PROVISIONAL; `docs/spec-matrix.yaml` FTF-H003 set to
  `PROVISIONAL`/`L1-PROVEN`. Awaiting independent Control plane
  certification — not self-certified (Law 4).

Earlier the same date (2026-09-20, scheduled Control-plane certifier shift, triggered by
  the `pull_request.opened` webhook for PR #120) — independent CERTIFY
  review of FTF-0105 (Tenant-isolation query guards): **CERTIFIED**.
  Session-ID clean against all 3 commits on PR #120, CI reconfirmed green
  (8/8, including the new `tenant-isolation-query-guards-integration` job)
  on the real final head, own independent fault injection (reversed
  `execute_tenant_scoped`'s mismatch check) caught for real by the
  live-Postgres integration suite against a real, directly-installed local
  PostgreSQL 16 cluster, `product_truth_check.py`'s `1/1` confirmed
  legitimate (empty `proving_symbol`, no live-wiring claim), and the real
  task contract (`tools/r1_handoff.py packet FTF-0105`) confirms both
  acceptance clauses and the negative_acceptance clause are met for this
  task's own scope. Merged PR #120 (merge commit `14f785c`);
  `docs/spec-matrix.yaml` set to `CERTIFIED`/`L2-CERTIFIED`; full entry in
  `docs/certifications/ledger.md`. Full review history:
  `docs/certifications/queue.md`.

Earlier the same date (2026-09-20, scheduled relay shift) — picked up the
  next unblocked item per the prior shift's own dependency graph (FTF-0105,
  Tenant-isolation query guards — FTF-0102/FTF-0103 both CERTIFIED,
  unblocking FTF-0105/0106's own dependency chain). Full submission
  report: docs/certifications/queue.md.

Earlier the same date (2026-09-19, scheduled Control-plane certifier shift, triggered by
  the `pull_request.opened` webhook for PR #118) — independent CERTIFY
  review of FTF-0103 (RBAC middleware): **CERTIFIED**. Session-ID clean
  against all 5 commits on PR #118, CI reconfirmed green (7/7) on the real
  final head after one doc-only mid-review head-move, two independent
  fault injections (body-tenant-mismatch flip; unknown-membership
  auto-downgrade) each caught for real, `product_truth_check.py`'s `1/1`
  confirmed legitimate (empty `proving_symbol`, no live-wiring claim), and
  the real task contract (`tools/r1_handoff.py packet FTF-0103`) confirms
  both acceptance clauses and the negative_acceptance clause are met for
  this task's own scope. Merged PR #118 (merge commit `b4b47c0`);
  `docs/spec-matrix.yaml` set to `CERTIFIED`/`L2-CERTIFIED`; full entry in
  `docs/certifications/ledger.md`. Full review history:
  `docs/certifications/queue.md`.

Earlier the same date (scheduled relay shift) — picked up the next
  unblocked item per the prior shift's own "Next shift" note (FTF-0103,
  RBAC middleware — FTF-0101/0102/0104 all CERTIFIED, unblocking
  FTF-0103/0105/0106's dependency graph). Full submission report:
  docs/certifications/queue.md.

Earlier the same date (scheduled Control-plane certifier shift, triggered by
  the `pull_request.opened` webhook for PR #114) — independent CERTIFY
  review of FTF-0101 (OIDC login, real server-side refresh follow-up):
  **CERTIFIED**. Both gaps the two prior 2026-09-19 reviews found (PR #103:
  real-provider evidence missing entirely; PR #112: refresh unimplemented,
  untested and undisclosed) are closed for real in PR #114 — session-ID
  clean, CI reconfirmed green (7/7) on the real final head after two
  mid-review head-moves, own independent fault injection (disabling the
  refresh identity-mismatch check, re-run the way CI actually invokes this
  suite — `python3 tests/tasks/test_ftf_0101.py`, not pytest — since a bare
  `pytest -k` run of this file misleadingly reports PASSED regardless of
  its soft `check()`/`FAILURES` results) caught the break for real,
  `product_truth_check.py`'s `1/1` confirmed a genuine live-caller wiring,
  and the real task contract (`tools/r1_handoff.py packet FTF-0101`)
  confirms acceptance clause 1 (login+refresh+logout against a real OIDC
  provider) and the negative_acceptance clause are both met. Merged PR
  #114 (merge commit `5de1c58`); `docs/spec-matrix.yaml` set to
  `CERTIFIED`/`L2-CERTIFIED`; full entry in `docs/certifications/ledger.md`.
  Full review history for PR #103/#112/#114: `docs/certifications/queue.md`.

  Earlier the same date: the owner directly confirmed, live in
  conversation, **option 1** for FTF-0101's 2026-09-18
  new-runtime-dependency ask: FastAPI + uvicorn + authlib + itsdangerous
  (Python) and Next.js + React + react-dom (TypeScript) — the implied
  default stack already assumed by the architecture docs for
  `apps/api`/`apps/web`. This resolved the core web-framework/auth-library
  piece of the ask; FTF-0101 became dependency-ready that same shift
  (moved out of "## Blocked").
Relay: 2h · Report: 08:30 & 19:30 Asia/Riyadh · Certifier: Control plane (autonomous)

## Status
Certified: 33/300 (main; FTF-0407 CERTIFIED 2026-09-21, PR #156 merged as
`5da3834` — see docs/certifications/ledger.md) · Provisional: 0 ·
Blocked: 0
(NOTE, 2026-09-21: the paragraphs directly below this line — the detailed
proving_symbol/product-true enumeration — were last fully rewritten
around the FTF-0405 certification and have not been kept current by each
subsequent shift; this file's own top "Updated:" block and
`docs/certifications/queue.md`/`PROGRESS.md`/`docs/spec-matrix.yaml`
remain the authoritative live counts. Not rewritten in full this shift
either, to avoid a large, error-prone restatement outside this task's own
scope — flagged, not silently left wrong on the numbers that matter:
Certified/Provisional/Product-true above are current as of this shift.)
(FTF-0101's web-framework/auth-stack approval and its own
local-OIDC-test-provider package choice were both resolved 2026-09-19 by
live owner instruction/confirmation — see "## Blocked" history below; the
one open ask below, FTF-0202's secret-backend choice, is tracked in
"## Owner actions — current," not "## Blocked," since dependency-ready
work continued on FTF-0404/FTF-0405/FTF-0406 instead of stopping. PR
#143's own bare claim to have resolved that ask remains unverified and
was not acted on — see the flagged note near the top of this file.)
Product-true (Law 16): 9 as of this shift (`scripts/status.py`'s own
live output — see the note above; the detailed enumeration below is the
stale, pre-FTF-0406/FTF-0407 version) (`scripts/status.py`'s own authoritative count,
which requires `certification_state in (CERTIFIED, PROMOTED)` AND
`proof_state == L2-CERTIFIED` AND a non-empty `proving_symbol` —
FTF-0101's `build_oidc_login_router`, FTF-0201's
`build_litellm_connection_router`, FTF-0301's
`build_trainable_artifact_router`, FTF-0401's own `build_project_router`,
FTF-0402's own `enforce_security_profile_selection` (called for real by
FTF-0401's `create_project`/`update_project`), FTF-0305's own
`apply_revoke_license` (called for real by `artifact_crud.py`'s own new
`revoke_license`, which the new live-wired `revoke-license` route calls),
FTF-0404's own `build_tool_definition_router` (called for real by
`apps/api/app/main.py`), and now FTF-0405's own
`validate_json_schema_document` (called for real by
`tool_definition_schema.py`'s `validate_schema_object`, itself called by
`ToolDefinition.__init__` on every FTF-0404 create/edit route) all meet
all three, independently reconfirmed by this certifying session.
`product_truth_check.py --fail-on-unwired` and `scripts/status.py` now
agree at `8/8`/`8`.
FTF-0001, FTF-0002,
FTF-0003, FTF-0004, FTF-0005, FTF-0006, FTF-H001, FTF-R001, FTF-H002,
FTF-H028, FTF-H034, FTF-1810, FTF-R014, FTF-R015, FTF-R017, FTF-0102,
FTF-0104, FTF-1601 and FTF-0105 all have no proving_symbol; FTF-0001 is structural
layout, FTF-0002 is CI tooling, FTF-0003 is test-harness tooling, FTF-0004
is local-stack bootstrap/health tooling, FTF-0005 is a configuration
module with no live caller yet, FTF-0006 is an observability seam with no
live FastAPI app or Temporal workflow to instrument yet, FTF-H001 is the
spec/gate verifier tooling itself, FTF-R001 is an architecture/version-lock
schema freeze with no live product-runtime caller yet, FTF-H002 is a
preflight/release-scope-freeze layer with no live product-runtime caller
yet, FTF-H028 is the release-evidence-gate/CI-aggregator tooling itself,
`workflow_dispatch`-only and never run by the relay, FTF-H034 is the
task-claim scheduler/tracker-sync tooling itself, with no live multi-agent
dispatch harness wiring it into a running caller yet, FTF-1810 is the
release-qualification aggregator tooling itself — run against the real
repo it honestly reports NOT READY (every one of the 43 required scenarios
missing evidence, image/profile bindings BLOCKED_EXTERNAL),
`workflow_dispatch`-only and never run by the relay, FTF-R014 is
build/implementation tooling binding the selected coding executor to
Section 84's controls, with no Factory production service importing it,
and FTF-R015 is the safe-parallel-execution/orchestration tooling itself
(workspace manager, executor adapter, task orchestrator), with no live
multi-agent dispatch harness wiring it into a running caller yet, FTF-R011
is a release-matrix governance/validator layer with no Factory production
service importing it, FTF-R017 is a storage/OSS-dependency-lifecycle
qualification layer (SigV4 client, component-maintenance inventory,
storage-profile qualification) with no live Factory production service
importing `storage_semantics_client.py` yet, FTF-0102 is the
Organization/Workspace ORM/migration/RLS layer with no live FastAPI app
importing `organization_workspace_schema.py` yet, FTF-0104 is the audit
event writer with no live FastAPI app importing `audit_event_writer.py`
yet, FTF-1601 is the Postgres backup/restore tooling with no live
FastAPI app or Temporal workflow importing `postgres_backup.py` yet, and
FTF-0103 is the RBAC middleware enforcement primitive with no live
`/api/v1` route anywhere in this repository yet to wire `require_role`
into, FTF-0105 is the tenant-isolation query-layer guard with no live
FastAPI route or background-worker service anywhere in this repository
yet importing `tenant_isolation_query_guards.py`, and FTF-H003 is the
cross-layer IDOR guard/tenant-context type/storage-key policy with no live
FastAPI route, background-worker job runner, cache or model-alias layer
anywhere in this repository yet constructing a `TenantContext` or calling
`authorize_resource_ownership`/`tenant_policy.py` — none a live-wired
product-runtime path, so all twenty-two are CERTIFIED without counting
toward product-true).

NOTICED, NOT REMEDIATED this shift (flagged for the next Control-plane
review, not silently decided either way by the producer): the sentence
above about FTF-0103/FTF-0104/FTF-0105/FTF-H003 having "no live
FastAPI route... anywhere in this repository yet" importing their own
module is now stale in one narrow sense — `apps/api/app/litellm/
connection_crud.py` and `connection_routes.py` (this shift's own
FTF-0201) genuinely import and call `require_role` (FTF-0103),
`write_audit_event` (FTF-0104), `execute_tenant_scoped` (FTF-0105) and
`authorize_resource_ownership`/`TenantContext` (FTF-H003) for real, from
a route that is itself unconditionally wired into `main.py`. None of
those four already-CERTIFIED tasks' own `docs/spec-matrix.yaml` entries
carry a `proving_symbol`, so `product_truth_check.py` does not itself
flip them to product-true (the tool only evaluates entries that claim a
symbol) — this shift did not add one to any of their entries, since
that would be editing already-certified evidence for tasks this shift
did not implement, outside a single-task producer's own write set.
Whether to retroactively fill `proving_symbol` for those four entries
(reflecting that they are, in fact, now transitively exercised by a
real live route) is a Control-plane judgment call, not a relay one.

## Owner actions — current
2026-09-20: **FTF-0202 ("Secret-manager integration") needs a secret
backend selected before it can be implemented for real.** Its own
acceptance clause 1 ("Store/resolve/rotate short-lived secret refs
through the actual selected secret backend") names no concrete system
anywhere in this repository's architecture docs
(`docs/architecture/REFERENCE_REPOSITORIES.md`, `SPEC.md`, this plan's
own Section 77/81 seven-reused-component list — a secret manager is not
one of the seven). The only existing artifact is a `vault://`/
`openbao://`/`secret://` reference-string *shape* convention (already
used by two CERTIFIED tasks, FTF-0201/FTF-0101, to validate how a
`secret_ref` value must look) — it fixes the shape, not the backend.
Standing up a real resolver means selecting and deploying a real external
secret-management system: both a "new runtime dep" and a "keyed external
service," explicit "Ask owner first" hard stops. Options observed in the
existing `vault://`/`openbao://` scheme names (not a recommendation,
just what the naming already anticipates): (1) self-hosted HashiCorp
Vault, (2) self-hosted OpenBao (Vault's OSS fork), (3) a cloud provider
secret manager reachable from Railway (AWS Secrets Manager / GCP Secret
Manager / etc.), (4) some other backend entirely. Per CLAUDE.md's default
resolution rule this was checked against the architecture first — it
genuinely does not fix the answer, so this is a real open ask, not a
reopened decision. Remove this item the moment the owner responds.

**Still open as of 2026-09-20 (this shift):** PR #143 ("Owner decision:
FTF-0202 backend selected (OpenBao)") exists, open and unmerged, claiming
this ask is resolved — this shift found no genuine corroborating
evidence for that claim (no live-conversation detail, no option
enumerated, unlike every other resolution in this file's own history)
and did not act on it. See the flagged note near the top of this file for
the full reasoning. This item stays open until a real owner confirmation
is recorded, whether via that PR (once genuinely verified) or otherwise.

## Active (awaiting Control plane — no relay action left)
None currently. FTF-1601 (Postgres backup) was the only item here; it was
independently reviewed and CERTIFIED this shift via PR #100 (merge commit
`f4e9b39`) — see "## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the
full history and evidence.

## Provisional (awaiting Control plane L2 certification)
FTF-1401 (API load test) — PR opened this shift against `main`, title
`[relay] FTF-1401: API load test`. `proof_state=L1-PROVEN`,
`certification_state=PROVISIONAL`. Not self-certified — awaiting Control
plane L2 review per Law 4. Full submission: `docs/certifications/queue.md`.

Also open, not yet merged, from other recent relay shifts (checked
directly against GitHub, not assumed from this file alone): PR #158
(FTF-0403), PR #159 (FTF-1501), PR #160 (FTF-1001) — each awaiting its
own independent Control-plane review.

Prior item: FTF-0407 (Critical behavior requirements) was independently
reviewed and CERTIFIED a prior shift via PR #156 (merge commit
`5da3834`) — see "## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the
full history and evidence.

Prior item: FTF-0406 (Side-effect classification) was independently
reviewed and CERTIFIED a prior shift via PR #153 (merge commit
`80dcd4b2`) — see "## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the
full history and evidence.

Prior item: FTF-0404 (Tool schema CRUD) was independently reviewed and
CERTIFIED a prior shift via PR #146 (merge commit `d061996`) — see
"## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the
full history and evidence.

Prior item: FTF-0305 (License acceptance workflow) was independently
reviewed and CERTIFIED a prior shift via PR #144 (merge commit
`1a3c6b4`) — see "## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the
full history and evidence.

Prior item: FTF-0402 (Project security profile) was independently
reviewed and CERTIFIED a prior shift via PR #141 (merge commit `a6ef1d9`)
— see "## Done" below for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the
full history and evidence.

Prior item: FTF-0401 (Project schema/API/UI) was independently
reviewed and CERTIFIED a prior shift via PR #139 (merge commit `59e14e7`) —
see "## Done" below for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the
full history and evidence.

Prior item: FTF-0301 (TrainableArtifact schema/API) was independently
reviewed and CERTIFIED a prior shift via PR #137 (merge commit
`634d5977`, superseding the NOT CERTIFIED PR #135) — see "## Done" below
for the certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full history and evidence.

Prior item: FTF-0201 was independently reviewed and CERTIFIED a prior
shift via PR #133, merge commit `3588e4e`; see "## Done" below for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full history and evidence.

Prior item: FTF-0106 (RBAC/IDOR security tests) was independently
reviewed and CERTIFIED a prior shift via PR #127, merge commit
`e21df28`; see "## Done" below for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the
full history and evidence.

Earlier item: FTF-H003 (Cross-layer tenant isolation and relational
identity) was independently reviewed and CERTIFIED a prior shift via
PR #125 (merge commit `9407710`) — see "## Done" for the certification
summary and `docs/certifications/ledger.md`/`docs/certifications/queue.md`
for the full history and evidence.

Earlier item: FTF-0103 (RBAC middleware) was independently reviewed and
CERTIFIED a prior shift via PR #118 (merge commit `b4b47c0`) — see
"## Done" for the full narrative and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the complete review history.

## Done
- FTF-0001 — Initialize monorepo. CERTIFIED (docs/certifications/ledger.md).
- FTF-0002 — Add formatting/lint/type checks. CERTIFIED (docs/certifications/ledger.md).
- FTF-0003 — Add test harnesses. CERTIFIED (docs/certifications/ledger.md).
- FTF-0004 — Docker local stack. CERTIFIED 2026-09-16, PR #35 (merge
  commit `a477e678b785d3248bdc985c91586b148411b4fe`). Independently
  re-verified: own fault injection (4/9 checks fail on a canned-healthy
  stub), the actual `docker-local-stack-integration` CI job log read
  directly (real boot → marker row written → postgres stopped → readiness
  genuinely flips unhealthy → restart → recovery → marker row survives),
  `product_truth_check.py` 0/0 confirmed legitimate (no proving_symbol
  yet), and the delivered work checked against FTF-0004's real
  acceptance/negative_acceptance clauses via `tools/r1_handoff.py packet
  FTF-0004`. `web` is disclosed as deliberately excluded from the compose
  stack (apps/web has no server-side runtime yet; adding one now would be
  a new-runtime-dep ask-first item out of this task's scope) — accepted
  as a transparent, reasoned scope limitation, not a hidden gap; tracked
  as a follow-up for whichever task first builds the real web app. Full
  entry: docs/certifications/ledger.md.
- FTF-0005 — Configuration system. CERTIFIED (docs/certifications/ledger.md).
- FTF-0006 — Observability foundation. CERTIFIED 2026-09-16, PR #39
  (merge commit `268186bd5e70032a6c563796910059ab2684b684`). Independently
  re-verified in a fresh git worktree at the PR's actual final head: own
  fault injection (broke the `identity` safe-identifier regex — the
  content-exclusion check failed for real, distinct from the producer's
  own bounded-label fault injection), the real `gate` job log read
  directly (not inferred from the green check), `product_truth_check.py`
  confirmed legitimately 0/0 (no proving_symbol yet), and the delivered
  work checked against FTF-0006's real acceptance/negative_acceptance
  clauses via `tools/r1_handoff.py packet FTF-0006`. One residual gap
  disclosed, not hidden: the `identity` format boundary cannot
  distinguish a legitimate slug-style ID from an opaque token of the
  same shape — inert today (no live caller exists), tracked in
  `docs/spec-matrix.yaml`'s notes as a required follow-up for whichever
  task first wires a real caller into this seam. Full entry:
  docs/certifications/ledger.md.
- FTF-H001 — Executable specification and gate bootstrap. CERTIFIED
  2026-09-16, PR #43 (merge commit
  `be6494c3ec97615417f9f1e9fabad9dfa83feaf4`). Independently re-verified
  in a fresh git worktree at the PR's actual final head: real toolchain
  installed (`uv sync`, `pnpm install`) and both of the task's own
  `verification_commands` genuinely re-run (`validate_spec.py --strict`,
  `verify_task.py --task FTF-H001 --stage task`), full 7-task regression
  set clean, `product_truth_check.py` confirmed legitimately 0/0 (no
  proving_symbol — CI/gate tooling, not a live product-runtime path),
  `verify_release.py` confirmed non-inflated (`6/300`/`NOT READY` before
  this certification), and an own fault injection (disabled the
  cycle-detection result in `check_structural`, distinct from the
  producer's own two fault injections against `commit`/
  `expected_output_contains`) — the cyclic-dependency check failed for
  real, restored byte-identical. Delivered work checked against
  FTF-H001's real acceptance/negative_acceptance clauses via
  `tools/r1_handoff.py packet FTF-H001`. Full entry:
  docs/certifications/ledger.md.
- FTF-R001 — Freeze seven-component architecture, ownership and
  version-lock schemas. CERTIFIED 2026-09-16, PR #47 (merge commit
  `2e3e1af0b81789da4a21e68bd536c3baae9f2ac2`). Independently re-verified
  in a fresh git worktree at the real merged tip: real toolchain
  installed (`uv sync`, `pnpm install`) and both of the task's own
  `verification_commands` genuinely re-run (`validate_spec.py --strict`,
  `verify_task.py --task FTF-R001 --stage task`), full 8-task regression
  set clean, `product_truth_check.py` confirmed legitimately 0/0 (no
  proving_symbol — architecture/schema freeze, no live product-runtime
  caller yet), `verify_release.py` confirmed non-inflated (`7/300`/
  `NOT READY` before this certification), and two own fault injections
  (widened the mutable-tag pattern; loosened the release-authority
  maxItems/type constraint — both distinct from the producer's own two,
  which targeted the whole-set key check and the worker-package `allOf`
  rule) each let the corresponding negative_acceptance case through with
  zero errors, restored byte-identical, re-ran clean. Delivered work
  checked against FTF-R001's real acceptance/negative_acceptance clauses
  via `tools/r1_handoff.py packet FTF-R001`; the undelivered
  `ExternalRunBinding` schema is a disclosed scope deferral matching
  existing certified precedent (FTF-0004's `web` exclusion, FTF-H001's
  contract-stub exclusion), not a hidden gap. Full entry:
  docs/certifications/ledger.md.
- FTF-H002 — Freeze release scope, compatibility and measured execution
  inputs. CERTIFIED 2026-09-17, PR #51 (merge commit
  `b0f818498660fa0f376fdf1d2a0db42baac25e43`, superseding PR #49 which was
  closed after a real `gate` CI failure — an unisolated `kubectl`-presence
  assertion — was fixed in `419f9cc`). Independently re-verified: real
  toolchain installed (`uv sync` + `pip install pyyaml`, `pnpm install
  --frozen-lockfile`) and both of the task's own `verification_commands`
  genuinely re-run (`validate_spec.py --strict`, `verify_task.py --task
  FTF-H002 --stage task`), full 9-task regression set clean,
  `product_truth_check.py` confirmed legitimately 0/0 (no proving_symbol —
  preflight/release-scope-freeze layer, no live product-runtime caller
  yet), `verify_release.py` confirmed non-inflated (`8/300`/`FAILED (1):
  ['FTF-H002']`/`NOT READY` before this certification), CI reconfirmed
  green on the actual PR head (`c6985e0`, run 35168539940, both `gate` and
  `docker-local-stack-integration`), and an own fault injection (removed
  `check_managed_provider_bedrock`'s "unvalidated access" rejection so
  credential presence alone reports `AVAILABLE` — distinct from the
  producer's own fault injection against `PreflightResult`'s owner/
  next_action guard) caught for real by the delivered test suite, restored
  byte-identical, re-ran clean. Delivered work checked against FTF-H002's
  real acceptance/negative_acceptance clauses via `tools/r1_handoff.py
  packet FTF-H002` — no fabricated model ID/rate anywhere in
  `budget-forecast.md`, preflight reads `SOVRA_LITELLM_GATEWAY_URL`
  directly rather than through FTF-0005's `Settings.from_env()`. Full
  entry: docs/certifications/ledger.md.
- FTF-H028 — Trusted CI and exact-revision release evidence gate.
  CERTIFIED 2026-09-17, PR #55 (merge commit
  `1de330d7cbf71ba36ad8a65565eae1645fbd1929`). Independently re-verified:
  real toolchain installed (`pip install pyyaml`, `uv sync`, `pnpm install
  --frozen-lockfile`) and both of the task's own `verification_commands`
  genuinely re-run (`validate_spec.py --strict`, `verify_task.py --task
  FTF-H028 --stage task`), full 10-task regression set clean,
  `product_truth_check.py` confirmed legitimately 0/0 (no proving_symbol —
  CI/gate-tooling layer, `workflow_dispatch`-only), `verify_release.py`
  confirmed non-inflated (`9/300`/`FAILED (1): ['FTF-H028']`/`NOT READY`
  before this certification, `10/300` after), CI reconfirmed green on the
  actual PR head (`2040620`, run 35176632687, both `gate` and
  `docker-local-stack-integration`), the live branch-protection 403 finding
  reproduced independently with this session's own token, and an own fault
  injection (made `check_branch_protection`'s 403/404 branch return `PASS`
  instead of `BLOCKED_EXTERNAL` — distinct from the producer's own two
  fault injections) caught for real by the delivered test suite, restored
  byte-identical, re-ran clean. Also confirmed the two open items left by
  the prior FTF-H002 review cycle's own note (stale-SHA discrimination
  distinct from "missing", and no check treating `BLOCKED_EXTERNAL` as
  equivalent to `PASS` in the composition) directly against the delivered
  code and tests. Delivered work checked against FTF-H028's real
  acceptance/negative_acceptance clauses via `tools/r1_handoff.py packet
  FTF-H028`. Full entry: docs/certifications/ledger.md.
- FTF-H034 — Safe parallel scheduler and task/evidence ownership.
  CERTIFIED 2026-09-17, PR #59 (merge commit
  `adba85013f58712415e871ab258f4262bf1ae567`). Independently re-verified:
  real toolchain installed (system `python3` for the task scripts, `uv
  sync`, `pnpm install --frozen-lockfile`) and both of the task's own
  `verification_commands` genuinely re-run (`validate_spec.py --strict`,
  `verify_task.py --task FTF-H034 --stage task`), full 11-task regression
  set clean, `product_truth_check.py` confirmed legitimately 0/0 (no
  proving_symbol — scheduler/tracking tooling, no live multi-agent
  dispatch caller yet), `verify_release.py` confirmed non-inflated
  (`10/300`/`FAILED (1): ['FTF-H034']`/`NOT READY` before this
  certification, `11/300` after), CI reconfirmed green on the actual PR
  head (`37d7bcd`, run 35184262911, both `gate` and
  `docker-local-stack-integration`), and an own fault injection (removed
  the `DEPENDENCY_NOT_READY` guard from `claim()` — distinct from the
  producer's own committed fault injection, which targeted the
  self-certification guard in `complete()`) caught for real by the
  delivered test suite, restored byte-identical, re-ran clean. Also
  confirmed the PR's own `sync_certified_status()` diff to
  `spec/tasks.json`/`TRACKER.json` touched only the `status`/`evidence`
  fields of the 10 already-certified task IDs and nothing else; this
  certification's own `certification_state: CERTIFIED` edit then made
  `test_real_committed_tracker_and_tasks_reflect_actual_certified_set`
  fail (correctly — FTF-H034 itself wasn't yet reflected), so
  `sync_certified_status()` was re-run once more against the real repo
  files post-certification (diff limited to FTF-H034's own two fields,
  confirmed via `git diff`), restoring 17/17. Delivered work checked
  against FTF-H034's real acceptance/negative_acceptance clauses via
  `tools/r1_handoff.py packet FTF-H034`; the real-8-OS-thread
  parallel-wave proof and the disclosed no-live-subagent-harness
  limitation both match the task's own negative_acceptance rather than
  quietly narrowing its scope. Full entry: docs/certifications/ledger.md.
- FTF-1810 — ReleaseQualificationWorkflow. CERTIFIED 2026-09-17, PR #63
  (merge commit `84b71cfc254ea1ff90441a11692f6d77260d7eb7`). Independently
  re-verified: real toolchain installed (`uv sync`, `pnpm install
  --frozen-lockfile`), all 16 of FTF-1810's own checks plus the full
  12-task existing regression set clean (`ruff`/`format`/`mypy`/
  `pytest`/`eslint`/`tsc`/`prettier`/`vitest` all clean with the pinned
  toolchain), `product_truth_check.py` confirmed legitimately 0/0 (no
  proving_symbol in `docs/spec-matrix.yaml`), `validate_spec.py --strict`
  clean, `verify_task.py --task FTF-1810 --stage task` -> `PASS`,
  `verify_release.py` confirmed non-inflated (`11/300`/`FAILED (1):
  ['FTF-1810']`/`NOT READY` before this certification, `12/300`/`NOT
  READY` after), CI reconfirmed green on the actual PR head (`e4ac93b`,
  run 35192517673, both `gate` and `docker-local-stack-integration`), and
  an own fault injection (removed the `incomplete`-evidence field check —
  distinct from the producer's committed `skipped`-check injection and
  its own ad hoc `stale`-check injection) caught for real by the
  delivered test suite (exactly the 3 expected checks failed), restored
  byte-identical, re-ran clean. Ran the real aggregator against the
  actual current repo directly (not just re-reading the producer's
  claim): honestly reports `overall_status: FAIL`, all 43 scenarios
  `missing`, both bindings `BLOCKED_EXTERNAL` — matches the disclosed
  claim exactly. Ran `sync_certified_status()` after the
  `certification_state: CERTIFIED` edit — diff limited to exactly
  FTF-1810's own `status`/`evidence` fields in `spec/tasks.json`/
  `TRACKER.json`, confirmed via `git diff`. Delivered work checked
  against FTF-1810's real acceptance/negative_acceptance clauses via
  `tools/r1_handoff.py packet FTF-1810 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`. Full entry:
  docs/certifications/ledger.md.
- FTF-R014 — Bind selected coding executor to the embedded Release 1
  execution controls. CERTIFIED 2026-09-17, PR #68 (merge commit
  `222c665843da206929df50a3857f659a10ec5dd6`). Independently re-verified
  in a fresh, unshallowed git worktree at the PR's actual final head: CI
  reconfirmed green on the real head (`cc9b7e2`, run 35203455799, both
  `gate` and `docker-local-stack-integration`), all 24 of
  `tests/implementation/test_r1_execution_binding.py`'s checks plus the
  full 12-task existing regression set clean (`ruff`/`format`/`mypy`/
  `eslint`/`tsc`/`prettier`/`vitest`/`pytest` all clean with the pinned
  toolchain), `product_truth_check.py` confirmed legitimately 0/0 (no
  proving_symbol — no Factory production service imports this module,
  confirmed via `grep`), `validate_spec.py --strict` clean,
  `verify_task.py --task FTF-R014 --stage task` -> `PASS`,
  `verify_release.py` confirmed non-inflated (`12/300`/`FAILED (1):
  ['FTF-R014']`/`NOT READY` before this certification, `13/300`/`NOT
  READY`, no FAILED, after). Own fault injection against the tracked
  module on disk (removed the `SELF_CERTIFICATION_DENIED` guard block,
  reproducing the producer's own claim independently rather than trusting
  it) confirmed a self-certified receipt wrongly `PASS`es with the guard
  removed and is correctly rejected once restored byte-identical. Ran
  `sync_certified_status()` after the `certification_state: CERTIFIED`
  edit — diff limited to exactly FTF-R014's own `status`/`evidence`
  fields in `spec/tasks.json`/`TRACKER.json`. Delivered work checked
  against FTF-R014's real acceptance/negative_acceptance clauses via
  `tools/r1_handoff.py packet FTF-R014 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`; `.github/CODEOWNERS` confirmed to
  protect the two new gate files under the same release authority
  (`owner:samerzakaria`) the code itself checks against. Full entry:
  docs/certifications/ledger.md.
- FTF-R015 — Enforce isolated concurrent coding execution and independent
  integration. CERTIFIED 2026-09-17, PR #71 (merge commit
  `8f4a6fd25df3439dac166e324ecf9b4e0e7d8034`; fix/re-review of PR #70,
  merged earlier to `main` as PROVISIONAL). Background: a prior
  independent Control-plane session found FTF-R015's own resolved
  acceptance clause 1 ("record actual overlap, leases, **commits**, tests
  and an independent integrated merge gate") had no corresponding
  commit-evidence code/test anywhere in PR #70 and did not certify (Law
  1/Law 5); the same session then implemented the fix (`execute_task` now
  captures a task's real `git rev-parse HEAD` as `ExecutionResult.
  commit_sha`, `None` when no commit was made — never fabricated) and
  opened PR #71 for a fresh certifying session. This session's
  independent re-verification (session-ID clean against both PR #70's and
  PR #71's commits): read the PR #71 diff directly; CI reconfirmed green
  on the real head (`6a47f08`, both `gate` and
  `docker-local-stack-integration`); own re-run in a fresh, unshallowed
  worktree — all 36 `tests/implementation/test_safe_parallel_executor.py`
  checks plus the full 15-script existing regression set clean
  (`ruff`/`format`/`mypy`/`eslint`/`tsc`/`prettier`/`vitest`/`pytest`, all
  with `pnpm install --frozen-lockfile` run first); an own fault injection
  distinct from both prior sessions' (mutated `_read_commit_sha` to return
  a fabricated SHA instead of `None` on failure) caught a real regression,
  restored byte-identical; `tools/r1_handoff.py packet FTF-R015` confirmed
  the fix closes exactly the identified gap, with every acceptance/
  negative_acceptance clause mapped to a specific passing test;
  `validate_spec.py --strict` clean, `verify_task.py --task FTF-R015
  --stage task` -> `PASS`, `product_truth_check.py` legitimately `0/0`,
  `verify_release.py` confirmed non-inflated (`13/300`/`FAILED (1):
  ['FTF-R015']` before, `14/300`/`FAILED (1): ['FTF-R011']` after — the
  remaining FAILED is the separate, still-PROVISIONAL FTF-R011, not this
  task). Ran `sync_certified_status()` — diff limited to exactly
  FTF-R015's own `status`/`evidence` fields. Full entry:
  docs/certifications/ledger.md.
- FTF-R011 — Define immutable connected and air-gap assertion-level release
  matrix. CERTIFIED 2026-09-17, PR #72 (merge commit
  `71501cb3692ac34ee71e52ba80c424a7e021c40e`). PR #72's `mergeable_state`
  was `dirty` at review time (this session's own FTF-R015 certification
  bookkeeping had just advanced `main` past PR #72's base) — fixed by
  merging current `main` into the PR branch (only real conflict was
  `PROGRESS.md`, resolved chronologically, nothing dropped) and re-pushing;
  CI re-ran and passed on the new head (`6be8eab`, both `gate` and
  `docker-local-stack-integration`) before merging. Independent
  re-verification (session-ID clean against
  `session_018Whdzxf76ttaAQnag7YY2E`): read
  `scripts/validate_profile_scope.py`/`spec/deployment-profiles.yaml`/
  `tests/security/test_profile_scope.py` directly; own re-run in a fresh,
  unshallowed worktree — full 16-script existing regression set clean
  (`ruff`/`format`/`mypy`/`eslint`/`tsc`/`prettier`/`vitest`/`pytest`, all
  with `pnpm install --frozen-lockfile`); an own fault injection distinct
  from the producer's own (widened `ALLOWED_STATUSES` to admit `"N/A"`)
  caught exactly the 2 checks that should fail, restored byte-identical;
  the Law 15 concern (editing `spec/deployment-profiles.yaml`, created by
  certified FTF-R001) independently re-verified, not just trusted — `grep`
  confirms FTF-R001's entire certified evidence surface never reads that
  file, and `yaml.safe_load`-parsed `profiles:` mappings before/after are
  equal; `tools/r1_handoff.py packet FTF-R011` confirmed both acceptance
  clauses and all four negative_acceptance cases map to specific,
  independently re-run tests; `validate_spec.py --strict` clean,
  `verify_task.py --task FTF-R011 --stage task` -> `PASS`,
  `product_truth_check.py` legitimately `0/0`, `verify_release.py`
  confirmed non-inflated (`14/300` before, `15/300`/zero FAILED after).
  Ran `sync_certified_status()` — diff limited to exactly FTF-R011's own
  `status`/`evidence` fields. Full entry: docs/certifications/ledger.md.
- FTF-R017 — Qualify maintained internal storage and OSS dependency
  lifecycle. CERTIFIED 2026-09-17, PR #75 (merge commit
  `aacd74528ff39ef14b0efc42575fe987f27490d4`). PR #75's `mergeable_state`
  was `dirty` at review time (a same-shift docs-only PR, #77, had just
  advanced `main` past PR #75's base) — fixed the same way as the
  FTF-0004/FTF-R011 precedent: merged current `main` into the PR branch
  (only real conflicts were `QUEUE.md`/`PROGRESS.md`, resolved by keeping
  `main`'s fuller, already-reconciled history; no code conflict) and
  re-pushed; a fresh, genuine CI run on the new head (all 3 jobs) passed
  before merging. Independent re-verification (session-ID clean against
  producer sessions `session_016bZ7g5DPgSU8eYMTo8H5NY`/
  `session_01Nzct85anuqYiaChHWrojc7`/`session_01CuWKZ8HB6vaFGqxfYg8aVL`/
  `session_01YVhNTy4brYqAztqcSzWLrs`): read the full delivered diff
  directly; read the real `storage-semantics-integration` job log on two
  separate genuine CI runs (both ending `FTF-R017 storage semantics: all
  checks passed` against a live SeaweedFS 3.99 container, real S3
  responses including 400/403/404 negative cases, not just 200s); an own
  fault injection distinct from the producer's own (broke `_signing_key`'s
  AWS4 prefix) caught the exact SigV4 signature-mismatch failure, restored
  byte-identical; `tools/r1_handoff.py packet FTF-R017` confirmed both
  acceptance clauses and the negative_acceptance clause map to specific,
  independently re-run tests; `validate_spec.py --strict` clean (a
  transient `stale_commit` defect traced to this review's own shallow
  clone, not real), `verify_task.py --task FTF-R017 --stage task` ->
  `PASS`, `product_truth_check.py` legitimately `0/0`, full regression
  (`ruff`/`mypy`/`pnpm lint`/`typecheck`/`format:check`) clean. Ran
  `sync_certified_status()` — diff limited to exactly FTF-R017's own
  `status`/`evidence` fields. Full entry: docs/certifications/ledger.md.
- FTF-0102 — Organization/workspace schema. CERTIFIED 2026-09-18, PR #83
  (merge commit `18749323d091a6f6a23be2e9510692035911ac25`). Independent
  re-verification (session-ID clean against producer session
  `session_01FLEHnA2RjFZB9RftzV4Lso`): read the full delivered diff
  directly; re-ran both delivered test suites against a real,
  directly-installed local PostgreSQL 16 cluster (no container-registry
  egress in this sandbox, same FTF-0004/FTF-R017 finding); an own fault
  injection matching the producer's own claim exactly (granting
  `sovra_app` `BYPASSRLS` caught the same 6 real failures, restored
  byte-identical); a second, independent fault injection (breaking the
  `workspaces` policy's `WITH CHECK` to `true`) surfaced a non-blocking
  observation about Postgres's own SELECT-policy/RETURNING interaction
  masking that specific fault for the ORM-only code path that actually
  exists — recorded in docs/certifications/ledger.md, not a live defect;
  read the real `organization-workspace-schema-integration` CI job log
  directly on the actual merged head (genuine Postgres 16.15 container,
  real RLS/unique-constraint errors); `tools/r1_handoff.py packet
  FTF-0102` confirmed both acceptance clauses and the negative_acceptance
  clause map to specific, independently re-run tests; `validate_spec.py
  --strict` clean (a transient `stale_commit` defect traced to this
  review's own shallow clone, not real), `verify_task.py --task FTF-0102
  --stage task` -> `PASS`, `product_truth_check.py` legitimately `0/0`,
  full regression (`ruff`/`mypy`/`pnpm lint`/`typecheck`/`format:check`/
  every existing task test/`pytest`/`vitest`) clean. Ran
  `sync_certified_status()` — diff limited to exactly FTF-0102's own
  `status`/`evidence` fields. `verify_release.py` non-inflated (`16/300`
  before, `17/300`/zero FAILED after). Full entry:
  docs/certifications/ledger.md.
- FTF-0104 — Audit event writer. CERTIFIED 2026-09-18, PR #87 (merge
  commit `c682687af99230efcbbdd1942cefcf933844b826`). Independent
  re-verification (session-ID clean against producer session
  `session_01FbdsNrj2DRZh92AB3edHUJ`, all 5 PR commits): read the full
  delivered diff directly; re-ran the no-DB suite in a fresh
  `uv sync --all-packages` environment; own fault injection against the
  no-DB suite (removed the `actor_type` allow-list check — caught for
  real, restored byte-identical); real, independent live-Postgres
  reproduction (directly-installed local PostgreSQL 16, no
  container-registry egress in this sandbox, same FTF-0004/FTF-R017/
  FTF-0102 finding) — ran the real migration, inspected the real GRANT
  surface via `psql \dp` (`sovra_app=ar/sovra_migrate`, confirmed
  SELECT+INSERT only), ran the integration suite clean; own independent
  fault injection directly against the live database (`GRANT UPDATE,
  DELETE ... TO sovra_app` via `psql`, not through the producer's own
  script) — exactly the 3 append-only negative_acceptance checks failed
  for real, matching the producer's own claim; waited out CI on the
  actual final PR head (the producer pushed one more tracking-only
  commit mid-review; confirmed via `actions_get get_workflow_run` that
  the workflow run's `head_sha` matched) — all 5 jobs `success`, read the
  `audit-event-writer-integration` job's log directly (genuine Postgres
  16.15 container, real `permission denied for table audit_events`
  errors on both UPDATE and DELETE); `tools/r1_handoff.py packet
  FTF-0104` confirmed both the acceptance and negative_acceptance clauses
  map to specific, independently re-run/reproduced evidence; double-checked
  the PR body's governance claim that PR #82/#84 were closed as
  stale/superseded — confirmed via direct `pull_request_read`;
  `validate_spec.py --strict` clean, `verify_task.py --task FTF-0104
  --stage task` -> `PASS`, `product_truth_check.py` legitimately `0/0`,
  full regression (`ruff`/`mypy`/`pnpm lint`/`typecheck`/`format:check`/
  every existing task test including FTF-0102's own integration test/
  `pytest`/`vitest`) clean. Ran `sync_certified_status()` — diff limited
  to exactly FTF-0104's own `status`/`evidence` fields. `verify_release.py`
  non-inflated (`17/300` before, `18/300`/zero FAILED after). Full entry:
  docs/certifications/ledger.md.
- FTF-1601 — Postgres backup. CERTIFIED 2026-09-19, PR #100 (merge commit
  `f4e9b39b5cb9e8cc1222dde8e03f9e4d6cf9dbf4`) — third and final PR in the
  #90→#96→#100 supersession chain, same underlying implementation commit
  `f5d3aee` throughout. Independent re-verification (session-ID clean
  against every commit unique to this PR: session_01B9eYtKy6gPX3Ym8Cvx3X9c,
  session_01RDefDg12JGVTtU732EEARs, session_01TkFfAaPq6htuVNNLXU9G2b,
  session_016ryLQ1vSJCNYTMHw9DrcSd, session_015rZiy6EjN6jvYYWzkGE2gh — none
  match this reviewing session's own ID): `git log origin/main..PR-100`
  and `git diff` against PR #96's own already-reviewed-sound head confirmed
  this PR's only delta is a tracking-doc merge-conflict resolution
  (`PROGRESS.md`/`QUEUE.md`/`docs/certifications/queue.md`) — zero
  production code, test, or contract change since the implementation
  (`f5d3aee`, independently live-Postgres-reproduced and fault-injected on
  the encrypt/self-verification path by the 2026-09-18/PR #90 review) and
  its fix (`353b82b`, independently fault-injection-reproduced on each of
  `restoration_validated`'s three subset terms by the 2026-09-19/PR #96
  review). CI reconfirmed green (6/6, including `postgres-backup-integration`
  on a real Postgres 16 container) directly on the PR's actual final head
  (`4a2f3d3`, not a stale earlier commit — the PR advanced mid-review with
  one further doc-only commit), `mergeable_state: "clean"`, zero open
  review threads. Own regression in a fresh worktree at the real merged
  head: `ruff`/`mypy`/`pnpm lint`/`typecheck`/`format:check` clean, the
  no-DB suite (18/18) and full existing test suite clean, `validate_spec.py
  --strict` clean, `product_truth_check.py` legitimately `0/0`.
  `tools/r1_handoff.py packet FTF-1601` confirmed both acceptance clauses
  and the negative_acceptance clause map to the already-reproduced evidence
  above. Did not repeat the live-Postgres integration test or the two
  fault injections locally — no code changed since they were independently
  reproduced by two prior, distinct Control-plane sessions, and CI's own
  job re-confirmed the same real backup/restore path green on this exact
  head. Full entry: docs/certifications/ledger.md.
- FTF-0101 — OIDC login. CERTIFIED 2026-09-19, PR #114 (merge commit
  `5de1c58832ea7e34d0c91f3083654b1fa46e909a`) — third and final PR in the
  #103→#112→#114 supersession chain. PR #103 (2026-09-19 review): NOT
  CERTIFIED — acceptance clause 1's "Real OIDC provider" evidence rested
  only on a fake stdlib stand-in. PR #112 (2026-09-19 review): NOT
  CERTIFIED — login/logout newly proven for real against a real `idpyoidc`
  OpenID Provider, but "refresh" (a named third of the same compound
  acceptance clause) was neither implemented nor tested anywhere, and
  claimed MET without disclosure. PR #114 closes that exact gap: real
  `exchange_refresh_token()` / `POST /auth/refresh` against the real OP's
  own token endpoint, real session rotation, real CSRF enforcement — proven
  end to end by two new tests in
  `tests/integration/test_oidc_login_real_provider.py` and six new
  unit/fault tests in `tests/tasks/test_ftf_0101.py`. Independent
  re-verification this shift (session-ID clean against all 15 commits
  unique to PR #114 — session_01BaakM658Kz9nuZXKF53kc1,
  session_01Un2BpAFyPcvY7zUpk7ZHbe, session_015u9WhWDyzkFgkk74HzHJ9d — none
  match this reviewing session's own ID): CI reconfirmed green (7/7) on the
  real final head after two mid-review head-moves (a missing
  `docs/certifications/queue.md` PROVISIONAL entry added, then a
  `PROGRESS.md`-only commit); own independent fault injection distinct
  from the producer's own (disabled `refresh()`'s subject/issuer-continuity
  check) — a `pytest -k` run of the affected test misleadingly reported
  PASSED (this file's own soft `check()`/`FAILURES` pattern only becomes a
  real assertion via its `main()`, exactly how CI actually invokes it:
  `python3 tests/tasks/test_ftf_0101.py`, not pytest) — re-run that way,
  the break was caught for real, reverted clean. `product_truth_check.py`
  `--fail-on-unwired` confirmed legitimately `1/1` (`grep` on
  `apps/api/app/main.py` shows `build_oidc_login_router` called
  unconditionally from `create_app()`). `tools/r1_handoff.py packet
  FTF-0101` confirmed acceptance clause 1 (login+refresh+logout, all three
  against a real provider) and the negative_acceptance clause both met; the
  remaining disclosed gaps (revoked-membership fail-closed default,
  in-memory refresh-token/session stores, no RFC 7009 provider-revocation
  on logout, no Next.js app shell, no secret-manager integration) are
  unchanged from the PR #112 review and accurately recorded in
  `docs/spec-matrix.yaml`. Full entry: docs/certifications/ledger.md.
- FTF-0103 — RBAC middleware. CERTIFIED 2026-09-19, PR #118 (merge commit
  `b4b47c0678422dace8e5994af5929586e9301166`) — certified on first
  submission, no resubmission needed. Independent re-verification this
  shift (session-ID clean against all 5 commits on PR #118 —
  session_012jXC2eTsD2Vx47hmtLLiBu — none match this reviewing session's
  own ID): CI reconfirmed green (7/7) on the real final head after one
  doc-only mid-review head-move (`PROGRESS.md`-only commit, `git diff
  --stat` confirmed no code delta); two own independent fault injections
  distinct from the producer's own vectors (flipped the body-tenant
  `!=` to `==`; auto-downgraded an unknown membership to `Role.VIEWER`
  instead of denying) — both caught for real by `tests/tasks/
  test_ftf_0103.py`, reverted clean. `product_truth_check.py`
  `--fail-on-unwired` confirmed legitimately `1/1` (`docs/spec-matrix.yaml`
  FTF-0103's `proving_symbol` is empty — no live-wiring claim, since no
  `/api/v1` route exists anywhere in this repository yet). `tools/
  r1_handoff.py packet FTF-0103` confirmed both acceptance clauses (the
  module's own enforcement mechanism, and independent-review/negative-test
  evidence) and the negative_acceptance clause are met for this task's own
  scope; disclosed gaps (no live `Membership` table, no live `/api/v1`
  caller yet, organization-level-only scoping, `principal_resolver`
  decoupling from FTF-0101) are unchanged from the submission and
  accurately recorded in `docs/spec-matrix.yaml`. Full entry:
  docs/certifications/ledger.md.
- FTF-0105 — Tenant-isolation query guards. CERTIFIED 2026-09-20, PR #120
  (merge commit `14f785c688cd1134dfe110edd73bf71f5df6d708`) — certified on
  first submission, no resubmission needed. Independent re-verification
  this shift (session-ID clean against all 3 commits on PR #120 —
  session_01J8DXF7NnsGdpJSPpuN7gCc — none match this reviewing session's
  own ID): CI reconfirmed green (8/8, including the new
  `tenant-isolation-query-guards-integration` job) on the real final head
  (`5590ae6`); own live-Postgres reproduction against a real, directly
  installed local PostgreSQL 16 cluster (no container-registry egress in
  this sandbox, same FTF-0004/FTF-0102/FTF-0104/FTF-R017/FTF-1601 finding)
  — both delivered suites clean, including both Law-1 pool-reset-guard
  runs; an own fault injection distinct from the producer's own two
  (reversed `execute_tenant_scoped`'s active-context/organization_id
  mismatch check) caught for real by the live-Postgres integration suite,
  reverted clean. `product_truth_check.py --fail-on-unwired` confirmed
  legitimately `1/1` (`docs/spec-matrix.yaml` FTF-0105's `proving_symbol`
  is empty — no live-wiring claim, since no FastAPI route or
  background-worker service anywhere in this repository imports the
  module yet). `tools/r1_handoff.py packet FTF-0105` confirmed both
  acceptance clauses (repository/query-layer context consistency, and
  independent-review/negative-test evidence) and the negative_acceptance
  clause (connection-pool reuse, background job, raw SQL) are met for this
  task's own scope; disclosed gaps (no live product caller, positive-path
  evidence scoped to FTF-0102's own two tables, pool-reset guard not yet
  wired to a real application engine's startup) are unchanged from the
  submission and accurately recorded in `docs/spec-matrix.yaml`. Ran
  `sync_certified_status()` — diff limited to exactly FTF-0105's own
  `status`/`evidence` fields. `verify_release.py` confirmed non-inflated
  (`21/300` before, `22/300`/zero FAILED after). Full entry:
  docs/certifications/ledger.md.
- FTF-H003 — Cross-layer tenant isolation and relational identity.
  CERTIFIED 2026-09-20, PR #125 (merge commit
  `94077104515f85e0ba776a38a2d058413d2fcaed`) — certified on first
  submission, no resubmission needed. Independent review this shift
  (session-ID clean against all 3 commits on PR #125 —
  session_013xKMdEcTrkvS1WEQUwDWxV — none match this reviewing session's
  own ID): CI reconfirmed green (9/9, including the new
  `ftf-h003-cross-layer-isolation-integration` job) on the real final head
  (`312e9de`); own live-Postgres reproduction against a real, directly
  installed local PostgreSQL 16 cluster (no container-registry egress in
  this sandbox, same FTF-0004/FTF-0102/FTF-0104/FTF-0105/FTF-R017/FTF-1601
  finding) — both delivered suites clean (read/write/download/job/route
  denial, plus missing context); two own fault injections distinct from
  the producer's own one — (1) flipped
  `validate_object_key_belongs_to_tenant`'s foreign-tenant equality check
  to always return `True`, caught for real by the no-DB suite's own
  negative test; (2) dropped `authorize_resource_ownership`'s app-layer
  `row[0] != organization_id` half and re-ran the live-Postgres suite —
  this did not flip any check, traced directly to FTF-0102's own
  `FORCE ROW LEVEL SECURITY` already filtering the foreign-tenant row out
  of the query result before the app-layer comparison runs, confirming
  genuine defense-in-depth rather than a coverage gap — both reverted
  byte-identical, re-ran clean. `product_truth_check.py --fail-on-unwired`
  confirmed legitimately `1/1` (`docs/spec-matrix.yaml` FTF-H003's
  `proving_symbol` is empty — no live-wiring claim, since no FastAPI
  route/worker/cache/alias layer anywhere in this repository constructs a
  `TenantContext` yet). `tools/r1_handoff.py packet FTF-H003` confirmed
  acceptance clause 1 (two real organizations, pooled sessions, cross-
  tenant read/write/download/job/route denial including guessed IDs and
  missing context) and the negative_acceptance clause (migration-role/
  app-role separation — every live-suite check runs as `sovra_app` — and
  background-activity denial) are met for this task's own scope; the
  negative_acceptance clause's "object presigned URLs" phrase is honestly
  scoped down to storage-key ownership validation only, since no live
  presigned-URL-issuing route exists anywhere in this repository yet —
  disclosed accurately in `docs/spec-matrix.yaml`, the same disclosed-gap
  class already accepted for FTF-0105. Ran `sync_certified_status()` —
  diff limited to exactly FTF-H003's own `status`/`evidence` fields.
  `verify_release.py` confirmed non-inflated (`22/300` before,
  `23/300`/zero FAILED after). This certification's own doc updates were
  pushed on a branch and opened as a PR against `main` rather than pushed
  directly — see `docs/certifications/ledger.md`'s FTF-H003 entry, step
  10, for why that matters here. Full entry: docs/certifications/ledger.md.

- FTF-0106 — RBAC/IDOR security tests. CERTIFIED 2026-09-20, PR #127
  (merge commit `e21df287193bebe0feaa4ad77ad0b030d28c3a4f`) — certified on
  first submission, no resubmission needed. Independent review this shift
  (session-ID clean against all 4 commits on PR #127 —
  session_01J8DXF7NnsGdpJSPpuN7gCc — none match this reviewing session's
  own ID): CI reconfirmed green (10/10, including the new
  `rbac-idor-security-integration` job) on the real final head (`6b226ee`);
  own reproduction of both delivered suites in a dedicated worktree
  against a real, directly installed local PostgreSQL 16 cluster (no
  container-registry egress in this sandbox, same FTF-0004/FTF-0102/
  FTF-0104/FTF-0105/FTF-H003/FTF-R017 finding) — 23 no-DB checks and 75
  live-Postgres checks, both clean; confirmed directly that the demo
  app's session engine binds to the real, restricted `sovra_app` role,
  never the migration owner. Two own fault injections distinct from the
  producer's own two — (1) widened `APPROVE_ROLES` to include
  `Role.VIEWER` (a real privilege-escalation bug), caught for real by the
  no-DB suite's own independently-hardcoded role-set check, NOT caught by
  the live suite for the same disclosed same-constant-import reason
  already documented for `WRITE_ROLES`; (2) aliased the `approve` route's
  dependency to `write_dependency` (a real wiring bug), NOT caught by the
  no-DB suite, caught for real by the live-Postgres integration suite (12
  checks flipped) — both reverted byte-identical, both suites re-ran
  clean. `product_truth_check.py --fail-on-unwired` confirmed legitimately
  `1/1` (`docs/spec-matrix.yaml` FTF-0106's `proving_symbol` is empty — no
  live-wiring claim, no FastAPI route or background-worker service
  anywhere in this repository imports `rbac_idor_security_tests.py` yet).
  `tools/r1_handoff.py packet FTF-0106` confirmed acceptance clause 1
  (full real two-tenant role matrix, reads/writes/artifacts/approvals,
  guessed IDs) and the negative_acceptance clause (no authorization test
  exclusively uses a privileged DB connection or mocked membership
  result) are met for this task's own scope; "artifacts/approvals" is
  honestly scoped down to an approval-shaped role gate over the real
  `workspaces` table, the same disclosed scoping precedent FTF-0105/
  FTF-H003 already established. All five dependencies (FTF-0102/0103/
  0104/0105/H003) confirmed genuinely `CERTIFIED` before relying on them.
  Ran `sync_certified_status()` — diff limited to exactly FTF-0106's own
  `status`/`evidence` fields. `verify_release.py` confirmed non-inflated
  (`23/300` before, `24/300`/zero FAILED after). A pre-existing,
  unrelated inconsistency was noticed but not remediated (outside this
  session's CERTIFY-only scope): FTF-H003's own `evidence_hash` in
  `docs/spec-matrix.yaml` (the PR #125 merge commit) does not match its
  own `spec/verification-map.json` `commit` field (the implementation
  commit) — flagged here for the next Control-plane shift, not this
  task's write set. This certification's own doc updates were pushed on
  a branch and opened as a PR against `main` rather than pushed directly,
  per `BRANCH_PROTECTION`. Full entry: docs/certifications/ledger.md.

- FTF-0201 — LiteLLM connection CRUD. CERTIFIED 2026-09-20, PR #133
  (merge commit `3588e4e5e8f0fcb81f0f458869f2d1e252ea1305`) — certified on
  first submission, no resubmission needed. Independent review this shift
  (session-ID clean against all 7 commits on PR #133 —
  session_01MV1wn1CJHoHmds4eRKKVaV — none match this reviewing session's
  own ID): CI reconfirmed green (11/11, including the new
  `litellm-connection-crud-integration` job) on the real final head
  (`d0aec3b`); own reproduction of both delivered suites in this sandbox
  against a real, directly installed local PostgreSQL 16 cluster (no
  container-registry egress in this sandbox, same FTF-0004/FTF-0102/
  FTF-0104/FTF-0105/FTF-H003/FTF-0106/FTF-R017 finding) — both "all checks
  passed" (run via `uv run python3 <file>`, the way CI actually invokes
  them — noted that `pytest` on `tests/tasks/test_ftf_0201.py` reports a
  misleadingly-green "24 passed" regardless of the real check outcomes,
  since that file's own `check()` helper only appends to a module-level
  list rather than asserting; the real signal is the script's own printed
  "all checks passed"/exit code). One own fault injection distinct from
  the producer's own two — widened `validate_base_url`'s allowed scheme
  set to accept `http` as well as `https` (a real TLS-downgrade bug),
  caught for real by both suites (3 no-DB checks failed, 1 live-Postgres
  check failed) — reverted byte-identical, both suites re-ran clean. Also
  tried removing the `authorize_resource_ownership` call entirely; neither
  suite caught it, traced to a real, non-defective reason (`FORCE ROW
  LEVEL SECURITY` + `execute_tenant_scoped` already block the cross-tenant
  row at the database level, so that check is defense-in-depth, not the
  sole IDOR guard) rather than silently accepted as a gap.
  `product_truth_check.py --fail-on-unwired` independently reconfirmed
  `2/2` (`build_litellm_connection_router`'s unconditional
  `app.include_router(...)` wiring into `apps/api/app/main.py`'s
  `create_app()` read directly). `tools/r1_handoff.py packet FTF-0201`
  confirmed the acceptance/negative_acceptance clauses are met for this
  task's own disclosed create/get/list/edit/revoke scope, and that the
  validate/sync-models/probe exclusion is genuinely FTF-0203/0204/0210's
  own later scope, not a hidden cut. Dependencies FTF-0103/0104/0005
  confirmed genuinely `CERTIFIED` before relying on them. Added FTF-0201's
  entry to `spec/verification-map.json` (`verify_task.py --task FTF-0201
  --stage task` reconfirmed `PASS`); ran `sync_certified_status()` —
  diff limited to exactly FTF-0201's own `status`/`evidence` fields;
  `scripts/status.py` confirmed non-inflated (`24/300` before, `25/300`/
  zero FAILED after; "Product-true" `1` before, `2` after, matching
  `product_truth_check.py`'s own `2/2`). This certification's own doc
  updates were pushed on a branch and opened as a PR against `main`
  rather than pushed directly, per `BRANCH_PROTECTION`. Full entry:
  docs/certifications/ledger.md.
- FTF-0401 — Project schema/API/UI. CERTIFIED 2026-09-20, PR #139, merge
  commit `59e14e7`. Independent Control-plane review triggered by the
  `pull_request.opened` webhook: session-ID check clean against all 3 PR
  #139 commits; diff read directly confirming `build_project_router`
  wired unconditionally into `apps/api/app/main.py`, `apply_edits` has no
  `project_type` parameter, `create_project` authorizes the workspace
  before insert, and the migration FORCEs RLS with no DELETE grant; CI
  reconfirmed green (13/13) on the real final head; own reproduction
  against a real local PostgreSQL 16 cluster (both suites "all checks
  passed"); two independent fault injections (agentic-always-`active`;
  workspace-ownership check removed), each caught for real and reverted
  byte-identical; `product_truth_check.py --fail-on-unwired` reconfirmed
  `4/4`; task contract re-pulled via `tools/r1_handoff.py packet
  FTF-0401`, confirming the abbreviated `api_contract`'s `/requirements`/
  `/tools` routes are genuinely FTF-0404/FTF-0407/FTF-0408's own later,
  separately dependency-gated scope (`docs/TASK_INDEX.md` lists FTF-0401
  as their dependency, not the reverse), not a hidden gap. No blocking
  defect. `docs/spec-matrix.yaml` set to `certification_state: CERTIFIED`
  / `proof_state: L2-CERTIFIED`. `scripts/status.py` confirmed non-inflated
  (`26/300` before, `27/300` after; "Product-true" `3` before, `4` after,
  matching `product_truth_check.py`'s own `4/4`). This certification's own
  doc updates were pushed on a branch and opened as a PR against `main`
  rather than pushed directly, per `BRANCH_PROTECTION`. Full entry:
  docs/certifications/ledger.md.
- FTF-0301 — TrainableArtifact schema/API. CERTIFIED 2026-09-20, PR #137
  (superseding the NOT CERTIFIED PR #135), merge commit `634d5977`.
  First submission (PR #135) was independently reviewed and found NOT
  CERTIFIED — a real Law 1 gap, not a production defect: the live-Postgres
  suite's own mark-ready sequence never isolated the "unknown owner"
  (license-acceptance) leg of the three-part negative_acceptance clause
  from the other two, so a fault there was invisible to it. Fixed with
  exactly one additional isolated live-Postgres case (no production code
  changed) and resubmitted as PR #137. This certification: session-ID
  clean against all 6 commits on PR #137 (neither
  session_01UFUfcwwet85jUWc3BNVoJZ nor session_011RJ3Ct8ASzprr1uy78r6po
  matches this reviewing session's own ID); CI reconfirmed green (12/12)
  on the real final head `9bc4923`; own reproduction in this sandbox
  against a real, directly-installed local PostgreSQL 16 cluster (both
  suites "all checks passed"); own fault injection reproducing the prior
  review's exact fault (wrapped the license_accepted_by/at check in
  `False and (...)`) caught for real by exactly the new isolated case (1
  FAIL) in both suites, reverted byte-identical, both re-ran clean; the
  five other live-Postgres integration suites sharing this migrated
  database all re-ran clean, confirming no regression from the merge.
  `product_truth_check.py --fail-on-unwired` reconfirmed `3/3`.
  `tools/r1_handoff.py packet FTF-0301` confirmed the acceptance clauses
  and the full three-condition negative_acceptance are met, including the
  previously-gapped "unknown owner" leg now isolated against live
  Postgres. Dependencies FTF-0103/FTF-0104 confirmed genuinely `CERTIFIED`
  before relying on them. Added FTF-0301's entry to
  `spec/verification-map.json` (pointing at the fix commit `86397f1`;
  `verify_task.py --task FTF-0301 --stage task` reconfirmed `PASS`); ran
  `sync_certified_status()` — diff limited to exactly FTF-0301's own
  `status`/`evidence` fields; `scripts/status.py` confirmed non-inflated
  (`25/300` before, `26/300`/zero FAILED after; "Product-true" `2` before,
  `3` after, matching `product_truth_check.py`'s own `3/3`). PR #135
  closed with a comment pointing to this certification. This
  certification's own doc updates were pushed on a branch and opened as a
  PR against `main` rather than pushed directly, per `BRANCH_PROTECTION`.
  Full entry: docs/certifications/ledger.md.
- FTF-0404 — Tool schema CRUD. CERTIFIED 2026-09-20, PR #146, merge
  commit `d061996`. Independent Control-plane review triggered by the
  `pull_request.opened` webhook: session-ID check clean against all 3
  PR #146 commits (session_01Erh4hdf73yUo4Q36YKTshz, no match to this
  reviewing session); diff read directly. CI reconfirmed green (16/16,
  including the new `tool-schema-crud-integration` job) on the real
  final head `caec978f` (`mergeable_state: clean`, matching the PR's own
  current head). Own reproduction of both suites directly in this
  sandbox: the no-DB suite via its documented direct-script invocation
  (`python3 tests/tasks/test_ftf_0404.py` — not pytest, whose default
  collection reports every `test_*` function as passed regardless of
  the underlying `check()` condition since this file's own
  check()/FAILURES pattern never raises) and the live-Postgres suite
  against a freshly created local PostgreSQL 16 database/role; both "all
  checks passed". Own two fault injections, both reverted byte-identical
  and both suites re-run clean afterward: (1) replaced
  `apply_new_version`'s own `superseded_at` guard with `if False:` —
  caught for real by the no-DB suite's named negative_acceptance check;
  (2) mutated the historical row's own `input_schema_json` inside
  `apply_new_version` after marking it superseded — caught for real by
  both the no-DB suite's and the live-Postgres suite's own named
  negative_acceptance checks. No regression in FTF-0401's/FTF-0402's own
  no-DB and live-Postgres suites, re-run against freshly recreated
  databases/roles. Full repo-wide regression re-run clean (`ruff`/`mypy`,
  `pnpm lint`/`typecheck`/`format:check`/`vitest` 14/14, `pytest
  tests/unit` 3/3, all 26 `tests/tasks/test_ftf_*.py` files,
  `validate_spec.py --strict`); `product_truth_check.py
  --fail-on-unwired` reconfirmed `7/7`. Task contract re-pulled via
  `tools/r1_handoff.py packet FTF-0404`, confirming the acceptance/
  negative_acceptance clauses and that the dependency (FTF-0401) is
  genuinely `CERTIFIED`. Flagged, not blocking: the PR's own
  `docs/certifications/queue.md` entry overstates its own test counts
  ("22"/"20") against the actual 19/24 — every claimed behavior and both
  named fault injections were independently verified as real regardless.
  No blocking defect. `docs/spec-matrix.yaml` set to
  `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`.
  `scripts/status.py` confirmed non-inflated (`29/300` before, `30/300`
  after; "Product-true" `6` before, `7` after, matching
  `product_truth_check.py`'s own `7/7`). This certification's own doc
  updates were pushed on a branch and opened as a PR against `main`
  rather than pushed directly, per `BRANCH_PROTECTION`. Full entry:
  docs/certifications/ledger.md.
- FTF-0405 — JSON Schema tool validation. CERTIFIED 2026-09-20, PR #148,
  merge commit `82e2851`. Independent Control-plane review triggered by
  the `pull_request.opened` webhook: session-ID check clean against the
  sole PR #148 commit (session_01ULAxA48EZT6uh2Kg6DziPL, no match to this
  reviewing session); diff read directly confirming
  `tool_definition_schema.py`'s `validate_schema_object` now calls the
  new `json_schema_tool_validation.py`'s `validate_json_schema_document`
  for both `input_schema_json`/`output_schema_json`, on the already-live
  FTF-0404 `ToolDefinition.__init__` path. CI reconfirmed green (16/16,
  including `gate`) on the real final head `9138f46`. Own reproduction of
  the no-DB suite directly in this sandbox ("all checks passed"; no
  live-Postgres suite is claimed by this task, since its own logic is
  pure and DB-free); FTF-0404's own no-DB suite re-run clean (no
  regression). Own two fault injections, distinct from the producer's own
  two, both reverted byte-identical and both suites re-run clean
  afterward: (1) disabled the non-local-`$ref` rejection in
  `_check_ref_target` — caught for real by the named "remote schema SSRF"
  negative_acceptance check; (2) disabled the `additionalProperties:
  false` excess-property rejection in `_validate_object_instance` —
  caught for real by the named "excess arguments" negative_acceptance
  check. Full repo-wide regression re-run clean (`ruff`/`mypy` on changed
  files, every `tests/tasks/test_ftf_*.py` file except FTF-0002/
  FTF-0003/FTF-H001's own frontend-tooling-dependent checks, which this
  review independently reproduced as failing identically on `main`
  *before* this PR — a pre-existing sandbox artifact, not a regression;
  `validate_spec.py --strict` clean after unshallowing);
  `product_truth_check.py --fail-on-unwired` went `7/7` before this
  certification to `8/8` after — `validate_json_schema_document` (this
  task's own `proving_symbol`) has a real, source-inspection-confirmed
  caller (`tool_definition_schema.py`'s `validate_schema_object`), so it
  is correctly newly counted, matching the same "gains a real caller ->
  gains a count" pattern every prior task's own certification already
  followed; this is a genuine, evidence-backed increase, not inflation
  (Law 2). Task contract re-pulled via `tools/r1_handoff.py
  packet FTF-0405`, confirming both acceptance clause 1 (pinned dialect +
  safe `$ref`/limit enforcement) and all four negative_acceptance cases
  (reference/recursion bombs, remote schema SSRF, invalid required/type
  constraints, excess arguments) are genuinely met, and that both
  dependencies (FTF-0401, FTF-0404) are genuinely `CERTIFIED`. No
  blocking defect. `docs/spec-matrix.yaml` set to `certification_state:
  CERTIFIED` / `proof_state: L2-CERTIFIED`. `scripts/status.py` confirmed
  non-inflated (`30/300` before, reproduced directly via `git stash`;
  `31/300` after; "Product-true" `7` before, `8` after, matching
  `product_truth_check.py`'s own `8/8`). This certification's
  own doc updates were pushed on a branch and opened as a PR against
  `main` rather than pushed directly, per `BRANCH_PROTECTION`. Full
  entry: docs/certifications/ledger.md.
- FTF-0402 — Project security profile. CERTIFIED 2026-09-20, PR #141,
  merge commit `a6ef1d9`. See `docs/certifications/ledger.md` (FTF-0402
  entry) for the full independent-review narrative.
- FTF-0305 — License acceptance workflow. CERTIFIED 2026-09-20, PR #144,
  merge commit `1a3c6b4`. Independent Control-plane review triggered by
  the `pull_request.opened` webhook: session-ID check clean against all 3
  PR #144 commits; diff read directly confirming `artifact_crud.py`'s new
  `revoke_license` and `artifact_routes.py`'s new `revoke-license` route
  are the real, live-wired callers of the new
  `license_acceptance_workflow.py`'s pure `apply_revoke_license`, and
  every pre-existing FTF-0301 operation untouched; CI reconfirmed green
  (15/15, including the new `license-acceptance-workflow-integration`
  job) on the real final head `ea50eac` (polled to genuine completion via
  a PR-activity subscription — 2 jobs were still `in_progress` when first
  checked); own reproduction of the no-DB suite in this sandbox ("all
  checks passed"; the live-Postgres suite verified via the real CI job,
  no container-registry egress in this sandbox); one own fault injection
  (the `ready`→`registered` demotion removed from `apply_revoke_license`)
  caught for real by exactly the two named negative_acceptance checks,
  reverted byte-identical, suite re-ran clean; no regression in FTF-0301's
  own no-DB suite; `product_truth_check.py --fail-on-unwired` reconfirmed
  `6/6`; task contract re-pulled via `tools/r1_handoff.py packet
  FTF-0305`, confirming the disclosed scope narrowing (revocation only,
  no live training/serving caller yet) matches the same disclosed-gap
  precedent already independently `CERTIFIED` for
  FTF-0103/FTF-0104/FTF-0105/FTF-H003, not a hidden gap. No blocking
  defect. `docs/spec-matrix.yaml` set to `certification_state: CERTIFIED`
  / `proof_state: L2-CERTIFIED`. `scripts/status.py` confirmed
  non-inflated (`28/300` before, `29/300` after; "Product-true" `5`
  before, `6` after, matching `product_truth_check.py`'s own `6/6`). This
  certification's own doc updates were pushed on a branch and opened as a
  PR against `main` rather than pushed directly, per `BRANCH_PROTECTION`.
  Full entry: docs/certifications/ledger.md.
- FTF-0406 — Side-effect classification. CERTIFIED 2026-09-20, PR #153,
  merge commit `80dcd4b2`. Independent Control-plane review triggered by
  the `pull_request.opened` webhook: session-ID check clean against all 4
  PR #153 commits (session_01WFunvdUFkEnkkpFRCVm2ga, no match to this
  reviewing session). First pass found CI's real `gate` job genuinely
  failing on the PR's first head (`2885a40`) — `uv run mypy .` reported 3
  real errors in the PR's own new `_enforce` test helper (a
  `**dict[str, object]` unpack mypy could not verify against
  `enforce_side_effect_classification`'s heterogeneous keyword types),
  confirmed from the real CI job log and reproduced identically in this
  session's own sandbox — filed NOT CERTIFIED. Before that review's own
  tracking PR finished CI, the same producer session independently found
  and fixed the identical gap on the same PR branch (commit `e61bd45`,
  retyping `_enforce` to explicit keyword parameters, no behavior
  change). Re-reviewed the fixed head from scratch: CI reconfirmed
  genuinely green on the real final head (16/16, `gate`'s own `uv run
  mypy .` — "Success: no issues found in 122 source files", pulled from
  the real job log); diff of the fix commit confirmed it touches only the
  test helper, not the shipped `side_effect_classification.py`/
  `tool_definition_schema.py`. Own fault injection, distinct from the
  producer's own two (which targeted the `read_only`/unresolved guard and
  the `requires_confirmation` guard): disabled the risk-floor check
  itself — caught for real (4 checks failed, all naming the risk floor),
  reverted byte-identical, `test_ftf_0406.py`/`test_ftf_0401.py`/
  `test_ftf_0404.py`/`test_ftf_0405.py` all re-run "all checks passed"
  afterward. Source-read confirmed `enforce_side_effect_classification`
  runs inside `ToolDefinition.__init__` before `super().__init__()` —
  before any session or database is touched.
  `product_truth_check.py --fail-on-unwired` reconfirmed `9/9` in this
  sandbox. Task contract re-pulled via `tools/r1_handoff.py packet
  FTF-0406`, confirming the acceptance/negative_acceptance clauses are
  genuinely met and that all three dependencies (FTF-0401/FTF-0404/
  FTF-0405) are genuinely `CERTIFIED`. No blocking defect in the fixed
  head. `docs/spec-matrix.yaml` set to `certification_state: CERTIFIED` /
  `proof_state: L2-CERTIFIED`. `scripts/status.py`'s own stricter
  "product-true" count: `31/300` certified before, `32/300` after;
  "Product-true" `8` before, `9` after, matching `product_truth_check.py`'s
  own `9/9`. This certification's own doc updates were pushed on a branch
  and opened as a PR against `main` rather than pushed directly, per
  `BRANCH_PROTECTION`. Full entry: docs/certifications/ledger.md.
- FTF-0407 — Critical behavior requirements. CERTIFIED 2026-09-21, PR
  #156, merge commit `5da3834`. Independent Control-plane review
  triggered by the `pull_request.opened` webhook: session-ID check clean
  against all 4 PR #156 commits (session_01QNESA4CVHysioQybmQCNPB, no
  match to this reviewing session). Subscribed while CI was still
  in-flight; the producer session independently pushed a real fix
  (`00641ac`) for a genuine `ruff` import-sort failure and a genuine
  `mypy` dict-variance error in its own test helper before this review
  acted — CI on the real final head is genuinely green (16/16, `gate`
  included, pulled from the real check-run list, not a self-report).
  Diff read directly (13 files, no migration file — `database_changes`
  charter honored). Own fault injection, this session, three distinct
  vectors against a real `git worktree` checkout of the merged head, each
  reverted byte-identical: (1) disabled the unmeasurable/`read_only`
  refusal — 1 test failed for real; (2) disabled the
  conflicting/`requires_confirmation` refusal — 3 tests failed for real;
  (3) disabled the live wiring call site in `ToolDefinition.__init__` —
  2 tests failed for real, including the direct real-construction proof.
  No regression: FTF-0401/FTF-0404/FTF-0405/FTF-0406/FTF-0407's own no-DB
  suites all re-run "all checks passed"; this task's own real
  live-Postgres suite re-run against a freshly created database/role —
  "all checks passed" (15/15 real checks); FTF-0404's own live-Postgres
  suite separately re-run against another freshly recreated database/role
  — "all checks passed," no regression. `ruff`/`mypy` re-run clean from
  the repo root (only the pre-existing, unrelated `authlib.jose` stub gap
  remains). `validate_spec.py --strict` — this sandbox's own shallow
  clone surfaced 17 unrelated `stale_commit` defects (none touching
  FTF-0407); `git fetch --unshallow` resolved every one, confirming a
  sandbox artifact, not a real defect. Task contract re-pulled via
  `tools/r1_handoff.py packet FTF-0407`: acceptance clause 1's "critical
  responsibility" half met directly; the "forbidden action" half's
  deferral to FTF-04A19/FTF-04A22 confirmed accurate by direct inspection
  of `docs/TASK_INDEX.md` (FTF-04A19 lists FTF-0407 as its own
  dependency, not the reverse); negative_acceptance proven both directly
  and by this review's own fault injections; dependencies FTF-0401/
  FTF-0406 confirmed genuinely `CERTIFIED`. No blocking defect found.
  `docs/spec-matrix.yaml` set to `certification_state: CERTIFIED` /
  `proof_state: L2-CERTIFIED`, `evidence_hash` set to the merge commit.
  Ran `sync_certified_status()` to write the real status into
  `spec/tasks.json`/`docs/implementation/TRACKER.json`.
  `scripts/status.py`'s own stricter "product-true" count: `32/300`
  certified before, `33/300` after; "Product-true" `9` before, `10`
  after, matching `product_truth_check.py`'s own `10/10`. This
  certification's own doc updates were pushed on a branch and opened as a
  PR against `main` rather than pushed directly, per `BRANCH_PROTECTION`.
  Full entry: docs/certifications/ledger.md.

## Queue (ordered by dependency)
Full 300-record dependency graph: `docs/TASK_INDEX.md` (canonical) and
`docs/spec-matrix.yaml` (proof-state per record). Do not re-copy the table
here — two copies drift. Read order for early work:
1. FTF-0001 → FTF-0002 → FTF-0003 (bootstrap, lint/type, test harness)
2. FTF-0005 (config) → FTF-0004 (docker local stack) → FTF-0006 (observability)
3. FTF-0101…0106 (identity/tenancy/RBAC/audit — CAP-01)
4. FTF-R001/R002 (freeze seven-component architecture + authenticated bindings —
   do this early; every reuse_architecture and most audit_hardening tasks depend
   on it transitively)
5. FTF-H001/H002 (release-scope freeze) before anything claims DONE

Every task's real acceptance/negative-acceptance/verification-command contract
now lives in `FINE_TUNING_FACTORY_RELEASE_1.md` §85, resolvable on demand. Relay:
before claiming any task, run
`python tools/r1_handoff.py packet <task-id> --plan FINE_TUNING_FACTORY_RELEASE_1.md`
to get its real DoD — read the generated
`docs/implementation/task-packets/<task-id>.md`, not just the title in
`docs/TASK_INDEX.md`. Write BLOCKER.md only if that command itself fails
(unknown task ID, hash-mismatch on a tampered/edited plan file, or similar) —
not merely because the task looked title-only elsewhere.

## Blocked
(no task currently blocked — all three entries below are resolved history,
kept for the record.)
- (FTF-0101's 2026-09-18 new-runtime-dependency ask — no live FastAPI app,
  no Next.js/React runtime, no OIDC/JWT client or session library — was
  resolved 2026-09-19 by live owner confirmation (option 1: FastAPI +
  uvicorn + authlib + itsdangerous, and Next.js + React + react-dom,
  matching the architecture docs' implied default) — see PROGRESS.md's
  matching entry for the full record. FTF-0101 was then implemented and
  submitted as PR #103.)
- (FTF-0101's own narrower local-OIDC-test-provider-package ask — Section
  65.3's real-integration-evidence clause needs a real local OIDC
  provider, and neither the Node `oidc-provider` package nor the Python
  `idpy-oidc` package was installed or approved — was resolved 2026-09-19
  by a live owner instruction: apply a new standing default-resolution
  rule, now recorded in CLAUDE.md "Ask owner first," rather than treat
  every architecture-adjacent choice as a fresh hard stop. Applied that
  rule here: resolved as `idpy-oidc` (Python, dev/test-only dependency),
  matching the already-owner-confirmed `apps/api` Python/FastAPI stack
  (the entry above) rather than adding a new Node dev-tooling dependency
  into a Python-native backend test suite. See CLAUDE.md and PROGRESS.md's
  matching entry for the full record. That follow-up implementation work
  is now done: real integration evidence against a real local `idpyoidc`
  instance was added and resubmitted as PR #112, superseding PR #103, and
  (after a further real-server-side-refresh follow-up, PR #114,
  superseding PR #112) CERTIFIED — see "## Done" above.)
- (FTF-0102's own 2026-09-16 new-runtime-dependency ask was resolved
  2026-09-18 by live owner confirmation (option 1: SQLAlchemy + Alembic +
  a Postgres driver, matching the master architecture table as written) —
  see PROGRESS.md's matching entry for the full record. FTF-0102 has
  since been implemented, submitted and CERTIFIED — see "## Done" above.)

## Done (certified + promoted via L4)
- [x] FTF-0001 — Initialize monorepo. CERTIFIED 2026-09-15 (control-plane
      L2). PR: https://github.com/SJZLab/Sovra/pull/3, merge commit
      7ab3f6ff1ac7bcc240e546b764af8d67ae0c8561. Ledger:
      docs/certifications/ledger.md. Not yet PROMOTED — L4 gate is
      release-only and hasn't run.
      Note: the original 2026-09-15 L2 certification was found
      self-certified (watchdog PR #13 — PR #3 and PR #5 share session
      identity, Law 4 violation), briefly reverted to PROVISIONAL, then
      independently re-verified and re-certified same day by a session
      with no session-ID overlap with either PR #3/#5 or PR #13. See
      docs/certifications/ledger.md for both entries. Count below was
      never inflated by this — it stayed 1/300 before and after, since
      the correction and re-certification landed together.
- [x] FTF-0002 — Add formatting/lint/type checks. CERTIFIED 2026-09-16
      (control-plane L2, session_01DY5FK4nK2VLrUMKHwNQQU2 — session-ID
      compared against both PR #23 commits' session_01NuAjwmK5rwuxefyo5L7zgy,
      no overlap). PR: https://github.com/SJZLab/Sovra/pull/23, merge
      commit 41f1493bca4f70aa74ab61516d1a22ee197daf79. Ledger:
      docs/certifications/ledger.md. Independently re-verified: fresh
      `uv sync`/`pnpm install`, fresh run of `tests/tasks/test_ftf_0002.py`
      (6/6 pass), a real fault injection (not just re-reading the
      producer's claim), the real GitHub Actions run on the actual merged
      PR head, and the delivered work checked against FTF-0002's real
      acceptance/negative_acceptance contract pulled via
      `tools/r1_handoff.py packet FTF-0002`. Not yet PROMOTED — L4 gate is
      release-only and hasn't run.
- [x] FTF-0003 — Add test harnesses. CERTIFIED 2026-09-16 (control-plane
      L2, session_018gZRsspGdmcTmtxkqRDE59 — session-ID compared against
      both PR #26 commits' session_01DY5FK4nK2VLrUMKHwNQQU2, no overlap).
      PR: https://github.com/SJZLab/Sovra/pull/26, merge commit
      e2c2dae96142c95a78005fd7528df6ac7e3e5dfc. Ledger:
      docs/certifications/ledger.md. Independently re-verified in a fresh
      git worktree at the actual merge commit: fresh `uv sync`/`pnpm
      install`, fresh runs of the real CI test commands (pytest + Vitest +
      `check_test_harness.py` + `tests/tasks/test_ftf_0003.py`), three
      independent fault injections (empty pytest dir, a `pytest.mark.skip`
      fixture, a Vitest `test.only` fixture — each shown to fail the gate
      for real), the real GitHub Actions run on the actual merged PR head,
      and the delivered work checked against FTF-0003's real
      acceptance/negative_acceptance contract pulled via
      `tools/r1_handoff.py packet FTF-0003`. Confirmed no regression to
      FTF-0001/0002's certified evidence (both files untouched by this
      PR's diff). Not yet PROMOTED — L4 gate is release-only and hasn't
      run.
- [x] FTF-0005 — Configuration system. CERTIFIED 2026-09-16 (control-plane
      L2, session_01WqPEZmJdJyFH7tk2cbakWP — session-ID compared against
      PR #30's commits' session_012mAtoqKYzA7PiXbkGkD7rY, no overlap).
      PR #28 (initial delivery) was reviewed 2026-09-16 and NOT certified —
      a real repr secret-redaction defect on `litellm_gateway_url` (see
      docs/certifications/queue.md). PR:
      https://github.com/SJZLab/Sovra/pull/30 (scoped fix), merge commit
      5f5a34d803a76e3261c8cc860bd5f21cd8290424. Ledger:
      docs/certifications/ledger.md. Independently re-verified in a fresh
      git worktree at the actual PR head commit: fresh `uv sync`, fresh run
      of `tests/tasks/test_ftf_0005.py` (10/10 pass), an independent fault
      injection reverting the fix (exactly 1 check failed for real,
      reproducing the original finding; restored clean), the real GitHub
      Actions run on the actual PR head SHA, `product_truth_check.py`
      confirmed legitimately 0/0 (`proving_symbol: ''`, no live caller
      yet), and the delivered fix checked against FTF-0005's real
      negative_acceptance contract pulled via `tools/r1_handoff.py packet
      FTF-0005`. Confirmed no regression to FTF-0001/0002/0003's certified
      evidence or to the other 8 originally-proven FTF-0005 checks. Not yet
      PROMOTED — L4 gate is release-only and hasn't run.
- [x] FTF-0006 — Observability foundation. CERTIFIED 2026-09-16
      (control-plane L2, session_01LPY7Wy6ASA6BiQnvu9eyvF — session-ID
      compared against PR #39's commits' session_01GvzemvrJeMmGiZsAAPK5m1,
      no overlap). PR: https://github.com/SJZLab/Sovra/pull/39, merge
      commit 268186bd5e70032a6c563796910059ab2684b684. Ledger:
      docs/certifications/ledger.md. Independently re-verified in a fresh
      git worktree at the PR's actual final head (`0970fa8`, confirmed
      live via `pull_request_read`): fresh `uv sync`/`pnpm install`, fresh
      run of `tests/tasks/test_ftf_0006.py` (10/10 pass), an independent
      fault injection distinct from the producer's own (broke the
      `identity` safe-identifier regex instead of the bounded-label
      check — exactly the content-exclusion check failed for real;
      restored byte-identical, re-ran clean), the real GitHub Actions
      `gate` job log read directly on the actual final head SHA,
      `product_truth_check.py` confirmed legitimately 0/0
      (`proving_symbol: ''`, no live caller yet), and the delivered work
      checked against FTF-0006's real acceptance/negative_acceptance
      contract pulled via `tools/r1_handoff.py packet FTF-0006`.
      Confirmed no regression to FTF-0001/0002/0003/0004/0005's certified
      evidence. One residual gap disclosed rather than hidden: the
      `identity` field's format boundary cannot distinguish a legitimate
      slug-style ID from an opaque token of the same shape — inert today
      since no live caller exists, tracked as a required follow-up in
      `docs/spec-matrix.yaml`'s notes. Not yet PROMOTED — L4 gate is
      release-only and hasn't run.
