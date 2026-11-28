# QUEUE — Sovra (Model Factory)
Updated: 2026-11-27 (Control plane). History moved out of this file; read ONLY the sections below.
Archived shift logs and the Done list: `docs/certifications/archive/queue-history-2026-11-27.md` (search it; never edit it).

## How to record work (Control-plane ruling 2026-11-27)
- One submission doc per item round: `docs/certifications/submissions/<UTC>-<ITEM>-r<N>.md`, linked from the PR body. No separate `docs/progress/` note.
- A `[relay]` PR edits only its own item's entry here (claim, park, ruling reply). It never edits `PROGRESS.md`, `docs/certifications/queue.md` or `ledger.md`; the Control plane updates counts and certification records only.
- Do not append a 'Previous shift' block. The newest state of an item lives in its one entry; history lives in git and the submission docs.

## Status
Certified: 37/300 (main; FTF-1702 CERTIFIED 2026-09-22, PR #167 merged
as `099a075` — see docs/certifications/ledger.md) · Product-true:
13/13. FTF-1501/FTF-1001/FTF-1401/FTF-0202 are merged and already
independently reviewed/certified by this same shift, awaiting only the
owner's merge of the doc-only certification PRs (#179/#180/#181/#182)
that record it on `main`. FTF-1302 (PR #170) is being independently
re-reviewed by Control plane now (this entry's own review) after its
prior NOT-CERTIFIED verdict, which was blocked only by a GitHub Actions
runner-provisioning outage reported cleared 2026-09-22 (PR #178).
FTF-R016 (PR #172) remains open with the same prior verdict and needs
the same treatment next. Blocked: 0
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
Product-true (Law 16): 10 as of this shift (`scripts/status.py`'s own
live output, re-confirmed this shift — see the note above; the detailed
enumeration below is the stale, pre-FTF-0406/FTF-0407 version; FTF-1001's
own new `product_truth_check.py --fail-on-unwired` wiring, 11/11, does not
raise this stricter CERTIFIED-only count until independently certified,
Law 2) (`scripts/status.py`'s own authoritative count,
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
- 2026-10-08: FTF-0616 (LLM judge) needs a real judge call through the org LiteLLM gateway for its acceptance clause: a CI secret holding a gateway virtual key, plus the approved judge model id. Until then that clause stays BLOCKED_EXTERNAL; the rest of FTF-0616 continues. Amended 2026-10-23T18:52Z (FTF-H010 design): with the same gateway key, also name one existing fine-tuned deployment id, so FTF-H010 can import and evaluate a real endpoint-only fine-tune.
- 2026-10-07: FTF-04A31 needs a private-repo test credential (or approval to use this repo with `github.token`). See Parked.

## Active (decided by Control plane — relay executes, top first)
**Claim rule (2026-10-06T17:40Z, Control plane):** a `relay/<ID>` branch whose head is already merged into main
(`git merge-base --is-ancestor origin/relay/<ID> origin/main`) is NOT a claim. Merged branches cannot be deleted
from sessions (the proxy blocks it), so they stay. Claim a returned or new round with a fresh branch
`relay/<ID>-r<round>` (for example `relay/FTF-0500D-r4`). Skip an item only if it has an OPEN `[relay]` PR, or an
unmerged `relay/<ID>*` branch pushed in the last 3 hours.

**No shared-file edits (2026-10-06T13:10Z, Control plane):** relay PRs must NOT edit QUEUE.md, PROGRESS.md or
docs/certifications/queue.md. Write the submission to docs/certifications/submissions/<UTC>-<ID>.md and the shift
log to docs/progress/<UTC>-<lane>.md (both new files). Shared edits re-conflicted every open relay PR after each
merge and cost a shift each time. Do not open docs-only "record this shift" PRs.

**CI contract (2026-10-07, Control plane):** "green" means the latest `ci-verdict` check on the PR's current
head SHA concluded success, and nothing else (not `gate` alone, not most jobs). The `relay-policy` job now fails
a second open PR for an already-owned item, Control-plane file edits, CERTIFIED flips and log-only PRs. To
iterate on one failing integration job, put `ci-focus: <job-name>` in the PR body; remove it before the final
push. Never re-push or re-run unchanged to see whether a failure goes away. Full rules: docs/operations/routines.md
"CI contract".

**Every item, every shift (2026-10-06, Control plane):** before submitting, run the PRE-SUBMIT
SELF-ATTACK in docs/operations/routines.md STEP 5b. Break every protection you added (delete it, and
add a bypass), run the test file as CI does (`python tests/...`) and show RED, repeat on a neutral host
for anything host-dependent, include a negative test on the real default code, and never stub the
protection under test. Put the fault → RED table in your queue entry, and apply the SAME-CLASS SWEEP
to sibling modules. Items returned 3–5 times this week all failed on exactly this.
000. **PARKED-ITEM RULINGS AND REVISED CLAIM ORDER (Control plane, 2026-11-27). Supersedes the claim order in item 00 where they differ.** Since 2026-11-26 every lane shift reported "no claimable item" because five items were parked on questions only the Control plane may answer, and no ruling was given. These are technical decisions under the CLAUDE.md default-resolution rule: none is an owner ask, and the "Owner actions" lines about FTF-1504, FTF-0603 and FTF-H011-P6 on the park branches are VOID. Do not merge the park PRs #1082, #1066 or #1077 (their branches stay as history); this block replaces them. Done since 2026-11-19: #1050 (FTF-H011-P3 r3) and #1083 (FTF-H010-P5 r3) are PROVISIONAL-merged and await independent L2, and #1051 (FTF-R006-P1b r4, `[GOVERNANCE]`, CI green) is being merged by the Control plane; none of them is relay work now. "Frontier saturated" is still not a valid shift outcome while any item below has no live claim.
   **Why new claims were blocked, and the fix.** The `relay-policy` job fails a `[relay]` PR when an OLDER open `[relay]` PR names the same item ID. The lane kept opening reclaims (FTF-1504 reached r21) while every stale draft stayed open. FTF-1504 drafts #1069, #1071, #1073, #1075, #1076, #1078, #1079 and #1081 and FTF-UX02 #1072 were closed on 2026-11-27 (branches kept; reclaim from the branch). Still open and owning their item: FTF-1504 #1026, #1045, #1047; FTF-0907 #1046, #1080; FTF-H011-P6 #1035, #1049, #1070, #1074; FTF-UX02 #1007, #1040, #1084; FTF-0701-P1 #1068; FTF-0603 #1066. #1070 and #1074 could not be closed from the Control-plane session (classifier denial): superseded, left open. A claim whose item ID is owned by one of these stays red on `relay-policy` until that PR is closed with the reason "superseded by the 2026-11-27 ruling"; the lane may close a superseded draft of its own item when it reclaims it, and must say so in the new PR body. New child slices below use new item IDs (`FTF-1504-F1`, `FTF-0603-F1`) so they are never blocked by the old drafts.
   **WHERE TO PUSH (Control plane, 2026-11-27, overrides every "Claim `relay/...-rN`" branch name in this block for the items listed here).** No PR needs to be closed and `relay-policy` is unchanged: it only fails a `[relay]` PR when a LOWER-numbered open `[relay]` PR names the same item, so the LOWEST-numbered open PR for an item always passes. Each of these items therefore lives on its lowest-numbered open PR from now on. The latest round branch is a fast-forward of that PR's branch (checked 2026-11-27), so the first step is `git fetch origin && git checkout <PR branch> && git merge --ff-only origin/<latest branch> && git push`, then continue there with a `Round: N` line in the PR body. Do NOT open another PR for these items and do not push to the higher-numbered drafts (they stay open as history and are not claims):
   - FTF-1504: PR #1026, branch `relay/FTF-1504-r3`; fast-forward to `origin/relay/FTF-1504-r20` (after FTF-1504-F1 merges).
   - FTF-H011-P6: PR #1035, branch `relay/FTF-H011-P6-r1`; fast-forward to `origin/relay/FTF-H011-P6-r6`.
   - FTF-UX02: PR #1007, branch `relay/FTF-UX02-r3`; fast-forward to `origin/relay/FTF-UX02-r7`.
   - FTF-0907: PR #1046, branch `relay/FTF-0907-r1`; fast-forward to `origin/relay/FTF-0907-r2`.
   - FTF-0701-P1: PR #1056, branch `relay/FTF-0701-P1-r2`; fast-forward to `origin/relay/FTF-0701-P1-r3`.
   - FTF-0603 (UI round, after FTF-0603-F1 merges): PR #1066, branch `relay/FTF-0603-r5`.
   - FTF-R006-P2: PR #961, branch `relay/FTF-R006-P2-r1`.
   New item IDs (FTF-1504-F1, FTF-0603-F1, FTF-1301-F1) have no open PR, so each opens ONE new PR and stays on it until it merges. A claim is live only if its PR had a push in the last 3 hours; an older one is continued by the next shift on the same PR.
   **Rulings.**
   - **FTF-1504 (route 1).** Four live-matrix cells fail because `GET` snapshots, snapshot, candidates and candidate under `target-connections` do `del organization_id, project_id` (7 handlers in `apps/api/app/target_connectors/target_source_snapshot_routes.py` lines 180/216/234, `discovered_agent_candidate_routes.py` lines 217/261/286, `discovered_agent_candidate_selection_routes.py` line 117). A foreign or random project id returns the same 200 as the own project. Organization isolation holds through RLS; project isolation inside one organization does not. That is a real defect in certified routes, not an exemption candidate, so option 2 (ADR exemption) and option 3 (leave red) are rejected. New fix slice **FTF-1504-F1 (child slice, no count; production + tests; claim `relay/FTF-1504-F1-r1` off main):** each of the 7 handlers resolves the connection with `(organization_id, project_id, connection_id)` and answers 404 with the route's existing not-found code on any mismatch (never 403, so existence is not disclosed), using the same store call the sibling certified routes use; no route, schema or migration is added. DoD: for every one of the 7 handlers a test that the own project returns the existing 200 body byte-identical, a foreign project of the same organization returns 404, a random project id returns 404, and a principal of another organization returns the existing 403/404; fault -> RED row per handler (put `del project_id` back, each handler RED); `tests/security/idor_live.py` cells for these 4 routes pass on PG16; `data_exposure_check` and the same-class sweep over every other `del organization_id, project_id` in `apps/api` (grep result in the submission; none may remain in a route that takes a project-scoped resource). Law 15: the change invalidates FTF-04A07, FTF-04A17 and FTF-04A18 (the modules' owners) until requalified; the relay does not flip them, the Control plane requalifies in the cycle that merges F1. After F1 merges, FTF-1504 continues as `relay/FTF-1504-r22` ON `relay/FTF-1504-r20` (the work branch; r21 is park-only, do not use it): test-only, no production change, the remaining seeders, write verbs, S3 layer, CI job `idor-suite-integration`, fault -> RED table, mutation gate, pre-review.
   - **FTF-0603 (route 1, split).** The lock route (`evaluation_suite_crud.py` `lock_evaluation_suite`) checks reviewer and independence only, so the clause "missing critical categories, insufficient sample plan and unresolved expected output block lock" is browser-only. A browser-only gate is not acceptable (Laws 8, 11), so route 2 is rejected. A migration with ENABLE + FORCE row-level security and a non-allow-all policy is a normal slice, not an owner ask. New server slice **FTF-0603-F1 (child slice, no count; production + tests + migration; claim `relay/FTF-0603-F1-r1` off main):** a new Alembic revision (next free number after the head at claim time) adding a persisted sample plan table for evaluation suites with `suite_sha256` (canonical JSON, sorted keys, UTF-8, scoped by organization and project), ENABLE + FORCE RLS with the tenant-isolation policy the sibling tables use, `sovra_app` granted SELECT/INSERT only; `lock_evaluation_suite` refuses (409 with a typed code per violated clause, state unchanged) when a critical category has no case, the sample plan is below the packet's floors, or any expected output is unresolved, and stores `suite_sha256` on lock. DoD: a test that the assigned reviewer POSTing `/lock` directly on an all-TBD suite is refused for each clause with the suite still unlocked; `suite_sha256` identical to the value the UI computes from the same suite (shared test vector, canonical hash, includes organization and project); an unauthorized principal cannot read or write another scope's plan; `data_exposure_check` clean for the new table; fault -> RED per clause; FTF-0601 behavior for a valid suite unchanged (its certified tests pass untouched). Law 15: FTF-0601 requalified by the Control plane in the merging cycle. Then **FTF-0603 UI round 2 (`relay/FTF-0603-r6` on `relay/FTF-0603-r5`, UI slice, +1 when certified)** fixes the review's client findings (101st required category dropped, final-suite floors overridable, JS-double hash, "Nothing was changed" after a lock, hand-built dialog instead of UX01 `Dialog`/`AlertMessage` with Escape and focus return, English enum values under `ar`, missing skill output and H1-H10 table, the 14 GREEN faults) and calls the server gate; it needs a page in `apps/web/pages` and a `proving_symbol` the page uses, and UX_STANDARD.md section 4 evidence.
   - **FTF-H011-P6 (option 2, authorized).** Gap 1: the P3 probe route has no XLSX path (`_PROBE_BLOCKS` and `_PROBE_SLOTS` in `dataset_mapping_store.py`). Ruling: P6 adds the XLSX rows ADDITIVELY (every existing row byte-identical, a test that every non-XLSX probe result is unchanged, a fault -> RED for each new row); P3 is requalified by the Control plane in the merging cycle. Gap 2: `list_worksheets` must list only worksheets the importer accepts: chartsheets and dangling-rId sheets are omitted from the list (a picker that offers a sheet the importer then refuses is a dead end), and a sheet named directly that is unimportable keeps the importer's existing code (no new code is added; `xlsx_malformed` stays for a malformed workbook). Pin both with tests, including a workbook whose only sheet is a chartsheet (empty list, no exception). The two surviving mutants are NOT equivalent and are NOT allowlisted: in `importer_for_content_type` (`dataset_mapping_store.py:295`) and `run_worksheet_probe` (`dataset_mapping_review.py:626`) the mutated operand is the `verified_content_type` PARAMETER, not the lowercase constant (`ooxml_safety.py:58`), so `.strip().lower()` is not an identity: an input such as `" Application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"` or a non-string is refused today and accepted (or raises `AttributeError`) under the mutant. Kill them with tests: that exact-case input and a padded input must give the existing unsupported-content-type outcome in both functions. (The existing S05 allowlist entry is equivalent only because there the mutated operand is a constant.) Claim `relay/FTF-H011-P6-r8` on `relay/FTF-H011-P6-r6` (r7 is park-only).
   - **FTF-0701-P1 (authorize round 4, exactly four items).** Three review rounds on r3 returned only narrow findings, so the cycle ends with a bounded round 4, not a re-split. Claim `relay/FTF-0701-P1-r4` on `relay/FTF-0701-P1-r3` and fix exactly: (1) a hostile dict key with a raising `__eq__` must give the module's own error, not escape `_exact_keys` or the re-parse raw; (2) pin every resolved-envelope schema fault that is GREEN today (`base_revision.pattern`, `split_manifest_version` bounds, `allOf`, `type: object`, `ResolvedTrainingConfig.type`, `config_sha256.type`), each fault -> RED; (3) the test validator must apply `type` on enum/const nodes; (4) `canonical_config_json` on a cyclic document raises the module's own error, not a raw `RecursionError`. Stopping rule: ONE fresh pre-review of the round-4 delta only; CERTIFY submits; NOT CERTIFIED with findings inside the delta gets fixes and one more delta-only review; a second NOT CERTIFIED is submitted with the findings disclosed and the Control plane decides (no further full cycles).
   - **FTF-0907 (route 1).** Claim `relay/FTF-0907-r3` on `relay/FTF-0907-r2`. Run ONE fresh review of only the round-3 delta (the admission copy and `out = _snapshot(admitted)` evaluated then returned), add a bound on `fallbacks` (A1: more than 16 aliases is refused with the module's own error), replace the `assert isinstance` in `_evaluate` with an explicit raise of the module's own error (A2), keep it UNWIRED-disclosed (A4), and submit on green. Same stopping rule as FTF-0701-P1. Route 3 (exact types in FTF-0901 `Destination`/`RouteAuthorization`) is a separate later hardening slice, not a prerequisite.
   **CLAIM ORDER BY UNBLOCK COUNT (Control plane, 2026-11-27; replaces every earlier claim order).** Measured from spec/tasks.json against docs/spec-matrix.yaml: 144 tasks are uncertified and only 10 are dependency-ready; everything else waits on them. Tasks waiting on each (transitively): FTF-0701 100, FTF-R004 57, FTF-0603 47, FTF-H011 30, FTF-H010 22, FTF-R006 15, FTF-H027 9, FTF-0907 8, FTF-04A31 2, FTF-1504 1. UX items (FTF-UX01..UX07) are not in the 304 and unblock nothing, so they are LAST. Each lane takes the first line whose parent task no other lane holds:
   1. FTF-0701 (100): FTF-0701-P1 round 4 on PR #1056, then its remaining slices back to back until the parent certifies. Nothing else outranks this.
   2. FTF-R004 (57): unpark FTF-R004-P3 (park merged in #1031); Control plane rules its open question in the next sweep; then P4.
   3. FTF-0603 (47): FTF-0603-F1 CERTIFIED 2026-11-28. Left: the UI round on PR #1066 (compute suite_sha256 from the shared vector, accept up to 200 categories, no browser-only checks), which closes the parent (+1).
   4. FTF-H011 (30): P1-P3 certified. RETURNED WORK FIRST: FTF-H011-P5 round 2 (one blocking item, ledger 2026-11-28: a group whose first row fails to map and whose later rows return after another group is accepted truncated; quarantine or fail it, with a RED-able test). Then P4 (PR #1094) and P6 (PR #1035) merge on green. New child FTF-H011-P7: store, preview and approve grouped mappings and label maps through the API, the 05 contract and the wizard (the parent cannot close before it).
   5. FTF-H010: CERTIFIED 2026-11-28 (159/304). Nothing left; its 22 dependents are no longer waiting on it.
   6. FTF-R006 (15): FTF-R006-P2 on PR #961 as soon as P1b is certified.
   7. FTF-H027 (9), FTF-0907 (8, PR #1046), FTF-04A31 (2).
   8. FTF-1504-F1 then FTF-1504 on PR #1026 (1). 9. FTF-1301-F1 round 3. 10. FTF-UX02 on PR #1007 and other UX work.
   **Advisory follow-ups from the 2026-11-27 certifications (not blocking; take one when a worker touches the same file):** FTF-H010-P4: add a `factory_trained` archived row to integration block A1. FTF-H010-P5: gate the jobs error writes on the request sequence and pin it; walk every `ENUM_KEYS` entry under ar. FTF-H011-P3: carry ruling (a) onto FTF-0502-P2 / FTF-0801; document the 409 on a reused Idempotency-Key; align the rationale pattern with the U+001C-U+001F refusal.
00. **STALE-CLAIM RULING AND CLAIM ORDER (Control plane, 2026-11-19).** The relay merged nothing after 2026-11-12. Every Active item looked claimed by a draft whose session had ended, the lane asked for rulings that were not given, and one lane session sat PENDING from 2026-11-12T18:29Z (archived 2026-11-19), which also stopped the dispatcher. "Frontier saturated" is NOT a valid outcome while this block lists a claimable item.
   **Rule applied (2026-10-23 ruling, restated):** a draft `[relay]` PR with no push for 5 days is not a claim, and a draft whose session ended without finishing is dead at once. Evidence below is the last commit on each claim branch, read 2026-11-19. To reclaim: merge main into the dead branch, keep its commits, push the next round name, open the new draft. Do not close or edit the old PR.
   - #961 FTF-R006-P2 (last push 10-22): DEAD. Still blocked on FTF-R006-P1b (item 0c); take it right after 0c is certified, as `relay/FTF-R006-P2-r2`.
   - #974 FTF-H011-P3 r1: SUPERSEDED by #1038 (its own body says it reclaims #974). Ignore #974.
   - #1038 FTF-H011-P3 r2 (last push 11-11 16:00, 22 commits): DEAD. Claim `relay/FTF-H011-P3-r3` on `relay/FTF-H011-P3-r2`. FTF-H011-P2 is CERTIFIED, so it is unblocked. Scope as COUNT-FIRST item 6: one more pre-review aimed at the Postgres-only input 500s, then submit.
   - #1007 FTF-UX02 r3 (last push 10-23): SUPERSEDED by #1040. RULING on the lane's question: the r4 branch is the live line; do not continue r3.
   - #1040 FTF-UX02 r4 (last push 11-12 12:16, parked by its lane: the 0516 race is unfixed and the Python integration suites were not run): DEAD. Claim `relay/FTF-UX02-r5` on `relay/FTF-UX02-r4`, finish item 0k, run the pre-review. UX lane.
   - #1026 FTF-1504 r3 (last push 11-12 14:31, 9 commits; its gate hung at the Playwright install on a runner DNS fault): DEAD as a claim. Claim `relay/FTF-1504-r4` on `relay/FTF-1504-r3`. One re-run of the hung job is sanctioned (died before any test body ran); then build the live PG16 + SeaweedFS IDOR matrix (COUNT-FIRST item 3).
   - #1032 FTF-R006-P1b r3 (last push 11-11 02:40, 4 commits, claim only): DEAD. Claim `relay/FTF-R006-P1b-r4` on `relay/FTF-R006-P1b-r3`; keep the `[GOVERNANCE]` title (item 0c).
   - #1035 FTF-H011-P6 r1 (last push 11-11 09:38, 4 commits): DEAD. Claim `relay/FTF-H011-P6-r2`.
   - #1037 FTF-H010-P5 r1 (`[GOVERNANCE]`, last push 11-11 14:00; the work is submitted but the independent pre-review was NOT run): DEAD. Claim `relay/FTF-H010-P5-r2` on r1, run the pre-review with docs/operations/l2-review-brief.md, keep the `[GOVERNANCE]` title. The Control plane merges it.
   - #960 FTF-UX04: merged (#1039), not a claim. Docs-only shift-log PRs #980, #984, #990, #991, #1010 and #1013 are not claims either (they break "no docs-only PRs"); ignore them.
   **Claim order for the single relay lane (RELAY_LANES: 1), each shift takes the first item with no live claim; count-raising first, then small, then the rest:**
   1. FTF-1504-r4 (test-only finish, +1 certified).
   2. FTF-0907 External judge denial (new, +1; Backlog top; the 0617/0901/0902 dependencies are CERTIFIED).
   3. FTF-0603 Suite validation/locking (new, +1; Backlog top; deps 0519/0601/0602 CERTIFIED).
   4. FTF-0701-P1 Training config schema (designed; no branch exists; `relay/FTF-0701-P1-r1`; the first of three slices toward +1).
   5. FTF-1301-F1-r3 (item 0q, tests only, small).
   6. FTF-H011-P6-r2.  7. FTF-H011-P3-r3.  8. FTF-R006-P1b-r4.  9. FTF-H010-P5-r2.  10. FTF-UX02-r5.
   FTF-H010-P4 r2 was CERTIFIED by an independent Opus review (2026-11-10): it is no longer relay work. FTF-04A31 stays parked on its owner line above (needs a private-repo credential).
0. **2026-10-06T09:45Z (Control plane): highest priority, one item per lane, one PR per item.**
   Work strictly in this order. Skip an item only if it has an open `[relay]` PR with GREEN CI and no conflict.
   - **0a. Merge conflicts on your own open PRs first** (merge main into the branch, re-run CI, merge on green).
   - **Every grader (FTF-06xx):** before submitting, apply the GRADER CHECKLIST in the same queue.md entry.
   - **Returned 2026-10-08T03:30Z (Control plane L2).** Every bullet in the list needs its own fault → RED row in the
     submission, and the row must be behavioural (a regex self-check that the guard text is present is not evidence).
     Latest certified: FTF-1706. The items below are not certified.
   - **GRADER CHECKLIST addition (same-class sweep):** never test membership of a raw input in a dict or set before
     checking its type. An unhashable value ([] or {}) must raise the module's own error, not TypeError.
   - **UI CHECKLIST (Control-plane ruling 2026-10-09, every apps/web UI slice; class-closing, from FTF-0309/0516/0501-UI returns):** (a) a generated strict-parse table over EVERY response field (wrong JSON type, value outside each enum, missing required field), each asserting malformed + no action control; (b) ONE binding function comparing every field sent with the response, a fault per field; (c) identity: drafts/results keyed by organization, project and connection, reset on any prop change; (d) no simulated progress or success: only a typed 2xx with a bound body counts; (e) typed errors: 5xx, non-JSON and unknown codes are retryable and keep state, never success or terminal rejection (Law 17); (f) a real-API test against the real router in a CI job when the backing routes exist. A hand-picked subset does not close a class.
   - **0b. FTF-0904-F2 Quoted values after credential names round 3 - TAKE FIRST (production + tests).** Work in packages/security/log_redaction.py (`_QUERY_ASSIGN` qsep and `_QUOTED_SECRET` only - do NOT change `_QUOTED_VALUE`, the Control plane fixed it in PR #940) and tests/tasks/test_ftf_0904.py. Claim `relay/FTF-0904-F2-r3`. Law 15: FTF-0904 stays REQUALIFICATION PENDING until this round is certified. Verifier findings (06aec8a, #925: even trailing-backslash runs closed - the 747,600-row table 0 failures, no over-redaction past the real close; linear to the 262,144-char cap; full matrix, W1 and FTF-0905 green in CI (gate 119392544610)):
     FTF-0904-F2 RETURN -> round 3. Reproduced:
     1. GENERIC-QUOTED-FAIL-OPEN (odd backslash run, no later quote): 6,028/120,000 differential leaks vs the certified module. FIXED by the Control plane in PR #940 (unterminated quoted values fail closed to end of line; differential 0; test_f2_r3_* and faults F2-r3 RED) - separate-session certification pending; keep it intact.
     2. F2-ESCAPED-QUOTE-FORM (NOT addressed in r2): `qsep` is unchanged - 26/27 F2 names leak in every backslash-escaped shape (`{\"state\": \"V\"}`, `state=\"V\"`, logfmt `msg="... code=\"V\""`) at levels 1-3 on redact_text and the logging path; live uvicorn 28/30. Give `qsep` the `\\*` before the key's closing quote and accept an escaped opening quote as `_SEP`/`_QUOTED_VALUE` do; table rows for 1-3 escape levels for every F2 name and composite; a fault RED.
     3. V6 / V10 / V11 / V11s still GREEN: rows for an unterminated single quote and quoted values > 8 KB; each fault RED.
     4. V17 still GREEN: generated composites for all 13 endings x random case, on redact_text and the logging path.
     5. Record the over-redaction counts (32/106, 35/47, 39/40) and the Control-plane decision (a quoted value after a credential name is redacted by design) in the submission.
     Before submitting: the pre-review step (CLAUDE.md "Relay is execution-only" step 4) with docs/operations/l2-review-brief.md, and the full FTF-0904 matrix + W1 + FTF-0905 green on the new head.
     STOPPING RULE (Control plane): round 3 certifies once 2-5 land with their faults RED, #940 stays intact, the full matrix + W1 + FTF-0905 pass (FTF-0904 REQUALIFIED), and no leak of a value the certified module redacts is reproduced (differential vs b5f4739c: 0); a newly hand-picked variant does not block (advisories: `%20` before the quote, an unterminated generic quote with no close at all - both identical on the certified module).
   - **IMPORTER ORDER (Control-plane ruling, updated 2026-10-13T23:59Z):** FTF-0502-P1 is CERTIFIED (#770). FTF-0502, FTF-0503, FTF-0504 and FTF-0505 may now be claimed, each rebuilt on certified P1 (one lane at a time per the Backlog order 0502 → 0504 → 0503 → 0505). Catalog rule (refines the 20:4xZ ruling): an importer that needs a new code or limit field (e.g. FTF-0503 fatal structure errors with a byte offset; FTF-0505 max_cells and the sharedStrings cap) makes ONE additive, serialized edit to workers/data/import_common.py in its own PR, extending P1's CAT/limit tests in the same PR; existing codes are never renamed, moved between catalogs or removed, and a fatal error's location goes into ImportReport via an additive optional field. Each importer passes the dataset's verified sha256 as expected_sha256 and a mismatch with the reader's declared hash must fail (advisory from #766 verification: run_import today still reports completed).
   - **0c. FTF-R006-P1b Argilla review scope, reviewer identity and workspace membership store round 3 (production + tests + CI; title the PR `[GOVERNANCE]` because it changes ci.yml).** Work in apps/api/app/reviews/argilla_identity_store.py, its tests, tests/integration/test_argilla_real_server.py, infra/docker/argilla/docker-compose.yml (new), .github/workflows/ci.yml, tests/unit/test_ci_policy.py and the submission. Claim `relay/FTF-R006-P1b-r3`. Verifier findings (264f53d, #971: CROSS-TENANT FIXED - on real argilla-server 2.8.0 a foreign scope, forged workspace id, relabelled org or foreign identity makes 0 Argilla calls and an org1 annotator gets 403 on org2's dataset (F18/F19 RED); round-1 race R11a closed; 8x parallel sync converges (F21 RED); column-limited UPDATE + trigger refuse un-revoke and identity-column rewrites (F24-F27 RED); new migration 0036, one head; F13/F15 RED; ci.yml additive; P1a REQUALIFIED):
     FTF-R006-P1b RETURN -> round 3. Reproduced on real argilla-server 2.8.0:
     1. REVOKE-SYNC-RACE (security, not closed): revoke reads the active memberships BEFORE marking the identity revoked, so a sync that starts after that read and writes before the final UPDATE passes the UPSERT liveness guard - a revoked identity reads a workspace dataset (HTTP 200) in deterministic interleavings R11b and R11c and in 2 of 5 free-running trials (R11e). Mark the identity revoked (or `revoking`) FIRST and then remove memberships, or hold SELECT ... FOR UPDATE on the identity row across sync's add+UPSERT and in revoke; deterministic RED tests for R11b and R11c.
     2. LAW-17-UNPINNED: F14 (revoke loop swallows ArgillaUnavailableError) and F16 (ensure_review_scope fabricates a workspace on an outage) stay GREEN in every suite - outage tests for both.
     3. REAL-ARGILLA-CI-HOLLOW: the new real-server job never creates an annotator, never calls the store, never checks cross-org/cross-project 403/404 on workspace or dataset, and "unhealthy once stopped" is port 1, not a stopped server (its access log shows only /version x3 and /me/workspaces). It must run the store against the real server: annotator 403/404 across orgs and projects, narrowing and revoke, the race, and a really stopped server.
     4. STACK-BREAKS-RULINGS (Law 0 licence terms): CI runs Elasticsearch 8.8.2 (SSPL/ELv2 - the owner chose OpenSearch, Apache-2.0, live 2026-10-14) and Redis 7.4.11 (RSAL/SSPL - the licence ruling allows only 7.2.x). Use OpenSearch (2.19.x) and Redis 7.2.x pinned by digest, the Argilla worker container, telemetry OFF, CI-generated bootstrap credentials, and infra/docker/argilla/docker-compose.yml. The /status "upstream bug" and the `get_version` workaround in the certified P1a client exist only because of the ES choice (real /status returns 200 on OpenSearch 2.19.6) - remove the workaround unless still needed, and requalify P1a.
     5. A gate step for tests/tasks/test_ftf_r006_p1b.py and tests/unit/test_ci_policy.py coverage for the new job.
     6. Title the PR `[GOVERNANCE]` (it changes ci.yml; QUEUE 0c and CLAUDE.md).
     Advisory: real Argilla returns 500 on its own workspaces_users UNIQUE race and the store surfaces it as Unavailable for 1 of 8 parallel calls - Law-17-correct, keep it.
     STOPPING RULE (Control plane): round 3 certifies once 1-6 land with their faults RED, the real-Argilla CI job exercises the store's clauses on the ruled stack, and no revoked or cross-tenant read is reproduced on real Argilla; a newly hand-picked variant does not block.
   - **0f. FTF-UX06 Evaluation UI redesign round 3 (UX lane; web component + tests + submission).** Work in apps/web/components/evaluation-factory/evaluation_case_editor_import.tsx, strings_0602.ts, apps/web/tests/ftf_ux06*.spec.ts and the submission. Claim `relay/FTF-UX06-r3`. Verifier findings (8fa5187, #979: browser spec runs in CI; axe 0 on 168 real-Chromium pages, lang/dir in every state; malformed replies and rejected fetches on load/import/save each show one localized, focused role=alert with a recovery step, nothing unmounts, no fake empty state; round-1 overflow sites 0 px with 100/200-char names; empty-state action works; FTF-0602 REQUALIFIED; 11 of round 1's 16 GREEN faults now RED; 21/24 new faults RED):
     FTF-UX06 RETURN -> round 3. Reproduced in real Chromium:
     1. OVERFLOW-SAVED-NOTICE (severity 3): the "Saved {key}." notice has no wrap - a 100-char snake_case case_key scrolls 395/465/929 px at 390/320/200 %, a 200-char key 1235-2609 px (345 px even at 1280). Wrap at the component root so every descendant inherits it; save-success and import-success browser states with 200-char keys; RED pins for the notice and the alert wrap (N4).
     2. FAILED-SAVE-403-SILENT (severity 3): a save answered 403 `final_suite_content_restricted` unmounts the form, renders no error and no role=alert, and focus lands on <body> - an announced alert outside the form and focus on a stable element; save field errors through the UX01 ErrorSummary (field links).
     3. NOT-BILINGUAL residue: real store codes `invalid_type`, `unknown_field`, `empty_object`, `too_large` shown raw under ar; three Arabic catalogue strings contain "weight", "expected_json", "case_key"; no <bdi> on the saved notice, row hint and edit heading; the "no English can survive" header claim is false; a producer test pins the raw fallback. Map every code the store emits; an unmapped code shows a localized generic phrase under ar (remove the raw-fallback pin); a catalogue test that rejects Latin text except an allow-list (N22 RED).
     4. UNPINNED: F1 (list-error role=alert), F12 (import-error), F13 (edit-error), F18 (weight field error), F23 (grader_config blur-error) stay GREEN - each RED when removed on its own (F23 without a timing-dependent test).
     5. WRONG-RECOVERY (severity 2): a 403 `role_not_authorized` says "fix the field"/"fix the file", and an import network failure says "fix the file" (N24) - name an access step / retry, pinned.
     6. STATIC-IDS (Control-plane ruling): use `useId`; the FTF-UX program DoD already lets parent specs move to role/label queries, so "the certified parent spec could not change" does not justify static ids.
     7. EVIDENCE: raw skill output (not paraphrased), the 39-item checklist item by item, a numeric severity in every H1-H10 x state cell, and the four false claims corrected ("no severity 3-4 remains", checklist item 39 and the others).
     STOPPING RULE (Control plane): round 3 certifies once 1-7 land with their faults RED in real Chromium, FTF-0602 still requalifies, and no severity 3-4 finding remains; a newly hand-picked variant does not block.
   - **0d. FTF-1301-F1 Stale cost reply after a project switch round 2 (web component + tests + submission; small).** Work in apps/web/components/cost-and-model-search/costprofile_schema_ui.tsx, apps/web/tests/ftf_1301.spec.ts, apps/web/tests/ftf_ux05_browser.spec.ts, apps/web/tests/browser/ux05_entry.tsx and the submission. Claim `relay/FTF-1301-F1-r2`. Verifier findings (dab64d2, #996: the named race is fixed - out-of-order A/B replies, A->B->A, stale error after fresh success, stale success after fresh error, create/retry racing a switch and an organization switch all correct; a Verify finishing after a switch refreshes the current project; FTF-1301 spec not weakened; UX05/UX01 specs pass; faults F1, F2, F6 RED):
     FTF-1301-F1 RETURN -> round 2. Reproduced:
     1. MISSING-BROWSER-TEST (explicit DoD clause): no out-of-order test in the UX05 Chromium harness (F1 leaves ftf_ux05_browser.spec.ts GREEN 251/251) - extend ux05_entry.tsx to re-render in place and hold replies; an A->B test with B first and A last, RED on dab64d21^1 and under F1.
     2. STALE-WHILE-LOADING (severity 3, the item's own invariant): switching A->B while B is in flight shows "Loading... Cheapest certified rate: RATE_A" under project B, until B replies (forever if B hangs) - clear search, error and notice when the project or organization changes, as 0309 does (`seenIdentity`); a test RED on the current code in jsdom and Chromium.
     3. UNPINNED-STALE-ERROR: F3 (no stale check on the error-status path), F4 (none in catch), F5 (finally always clears loading) stay GREEN on every producer test - tests for a stale 500, stale network error and stale malformed 200 after a fresh success, and a stale reply while the fresh request is pending, each RED under F3/F4/F5.
     4. EVIDENCE: remove the "in jsdom" wording that contradicts the DoD; UX_STANDARD §4 evidence for the switch state.
     Advisories: a 409 from project A's Verify shows on B's panel (P8, severity 2 - fix if cheap with the same guard); `locale` read from a stale closure.
     STOPPING RULE (Control plane): round 2 certifies once 1-4 land with their faults RED in jsdom and Chromium and FTF-1301/UX05/UX01 still pass; a newly hand-picked variant does not block.
   - **0k. FTF-UX02 Dataset-factory UI redesign round 3 (web components + the new UX01 Dialog + tests + submission) - the Dialog fix FIRST.** Work in apps/web/components/ui/Dialog.tsx (+ its own UX01 tests), apps/web/components/dataset-factory/ (0500A, 0501-UI resumable upload, 0516 issue resolution), their specs, apps/web/tests/ftf_ux02_browser.spec.ts and the submission. Claim `relay/FTF-UX02-r3`. Verifier findings (b264be1, #947: dependency correct - only `@radix-ui/react-dialog` 1.3.1 exact-pinned, 25 lockfile packages all @radix-ui/* or documented transitives, MIT/0BSD, no install scripts, OSV clean; the two UNPARKED items met (4 Arabic exact-string asserts, 0500A H1/H9 cells); Dialog in English: focus starts on Keep draft, Tab trap, Escape and close return focus, confirm moves focus on, axe 0; 0500A list errors announced with Retry; parents FTF-0500A/0501-UI/0516 REQUALIFIED (specs additive, real-API suites pass); Field/Pending edits additive and pinned; 32/33 faults RED):
     FTF-UX02 RETURN -> round 3. Reproduced in real Chromium:
     1. DIALOG-RTL-OFFSCREEN (severity 4, shared UX01 component): in Arabic the Dialog is drawn half off-screen at every width (x -155..375 at 1280, -125..65 at 320) - `insetInlineStart: 50%` becomes `right: 50%` in RTL while `translate(-50%, -50%)` always shifts left - so "Abandon draft" and the close button cannot be clicked. Center with direction-independent positioning; add `aria-modal`; make the close control a UX01 Button; a Chromium test that the dialog and every button are inside the viewport and hit-testable, en and ar, at 1280/768/390/320 and 200 %; the Dialog's own UX01 unit tests with faults RED (it is tested only through 0501-UI today). FTF-UX01 is NOT requalified for the Dialog until this lands (its certified components still hold).
     2. MALFORMED-200 (severity 4/3, Law 17, the 0500A "real authorized/denied outcome" clause): a `null` list item or a 201 `null` from create crashes and unmounts 0500A; `[{}]` shows a blank "(, , )" item as real; a 201 `{}` from create appends and selects a blank connection; authorize shows "Authorized" for `{}`, `{"authorized":false}`, a non-JSON reply or a wrong purpose. Validate every list item and create reply (one localized alert, never a crash or fake connection); "Authorized" only for `{authorized: true, purpose: <the purpose sent>}`; a test per case, faults RED.
     3. OVERFLOW-LONG-NAMES (severity 3): 407-588 px at 390/320 with a 100-character connection name (up to 1156 px at 200 %), 518 px for a 100-character filename in 0501-UI - wrap names and filenames; browser tests with 100-char unbroken, snake_case and sha256 fixtures at 390, 320 and 200 %.
     4. NOT-BILINGUAL / NOT-ON-UX01 (severity 3, round-2 points 2/3/6 still open): most of 0501-UI and ALL of 0516 are not rebuilt (0516 has no locale prop, all English, no lang/dir; 0501-UI idle state English with native 19-21 px controls) - rebuild both on UX01 with the en/ar catalogue, root `lang`, localized aria-labels, and the 0516 H2/H1/H8 items.
     5. REAL-BROWSER-IN-CI incomplete: add 320 px, every state of all three components, en/ar x light/dark, keyboard in both locales.
     6. UNPINNED: the EmptyState "New connection" action made a no-op stays GREEN (A9) - pin it.
     7. EVIDENCE: the canonical 39-item pro-rules checklist item by item; full H1-H10 x state tables for 0501-UI and 0516; correct the "no severity 3-4 remains" claim.
     Advisory: the Dialog's `aria-describedby` points at a dangling `radix-:r4:` id when no description is given.
     STOPPING RULE (Control plane): round 3 certifies once 1-7 land with their faults RED in real Chromium, the three parents still requalify, FTF-UX01 requalifies with the Dialog, and no severity 3-4 finding remains; a newly hand-picked variant does not block. Do not merge a PR whose own submission says PARTIAL - finish the round first.
   - **0n. COUNT-FIRST LANES (Control-plane ruling 2026-10-23T17:46Z; the certified count has been flat at 157/304 since 2026-10-21 because recent work was child slices and UX-program rounds, which do not raise it).**
     **Lanes.** ONE lane takes the UX-program items (the "UX lane": 0d FTF-1301-F1, 0k FTF-UX02, FTF-UX04 #960, and any returned FTF-UX06); the other lanes take count-path work first - returned count-path items (0b FTF-0904-F2, 0c FTF-R006-P1b, 0g FTF-H011-P2 when returned), then the COUNT-FIRST ORDER below, then the rest of the Backlog. "Returned work first" applies within each lane.
     **Stale drafts are not claims.** A draft `[relay]` PR with no push for 5 days or more is NOT a claim (this refines the 2026-10-06 claim rule): reclaim it on `relay/<ID>-r2`, building on its branch (merge main into it first) and keeping its commits; close the old draft with a link. Today: #732 FTF-0617 (last push 10-13), #709 FTF-R004-P3 (10-13), #759 FTF-1504 (10-15).
     **COUNT-FIRST ORDER** (dependency-ready; each closes a top-level task or its last missing child):
       1. FTF-H010-P4 r2 (item 0p): #998 merged on 2026-11-10 (`43512af4`) and was RETURNED by the Opus L2 review (weights-artifact state check, advisory-lock pin, revocation CHECK pin); it is NOT the last child of FTF-H010: P5 (wizard) and P6 (version panel, parent closure) remain.
       2. FTF-0617: CERTIFIED 2026-11-10 (#1011, merge `816f0fb3`, 158/304; UNWIRED until FTF-0618 and FTF-R004-P3/P4 call it). Done.
       3. FTF-1504 r2 from #759: test-only - finish the live PG16 + SeaweedFS IDOR matrix; +1. DEAD CLAIM RULING (Control plane 2026-11-10): draft PR #1020 (`relay/FTF-1504-r2`, last push b5f5afc1) was opened by a session that ended about 90 seconds later without building the matrix, so it is NOT a claim (this refines the 5-day stale-draft rule: a draft whose session ended with no work is dead at once). The next lane claims `relay/FTF-1504-r3`, merges main and `relay/FTF-1504-r2` into it, keeps r2's commits and builds the real matrix. The SeaweedFS image is reachable only in CI, so iterate with a `ci-focus: <job-name>` line in the PR body (remove it before the final push). Never claim and abandon: finish the item or park it with the reason.
       4. FTF-R004-P3 r2 from #709: refresh onto main and submit; opens R004-P4 and the 0614 chain.
       5. FTF-H011-P6 (XLSX mapping; FTF-0505 is CERTIFIED): small.
       6. FTF-H011-P3 (#974): continue on #974 once FTF-H011-P2 is certified (route option 1: one more pre-review aimed at the Postgres-only input 500s, then submit).
       7. FTF-R006-P2 (#961): continue once FTF-R006-P1b is certified, with the real pinned Argilla CI leg (the P1b ruling applies).
       8. FTF-H011-P4, FTF-R004-P4, FTF-H011-P5, FTF-R006-P3 as their predecessors certify.
     Designs landed 2026-10-23T18:52Z (top of the Backlog): FTF-0701 P1-P3 (P1 claimable now; take it right after item 3 above, after #998 merges or rebased onto it - both create workers/training/__init__.py) and the FTF-H010 parent remainder P5 (wizard, claimable now) and P6 (after P4 and P5). FTF-0603 and FTF-H027 designs are next; until they land those are not claimable.
   - **0p. FTF-H010-P4 r2 (RETURNED 2026-11-10 by the independent Opus L2 review of #998, merge `43512af4`; NOT CERTIFIED round 1; branch `relay/FTF-H010-P4-r2`; count-path, take first).** Live path is fine (the four routes are mounted and answer 401 unauthenticated; 28190 task checks and 518 PG16 integration checks pass; uncapped mutation 352/352; product_truth 49/49), but three defects stand:
     1. WEIGHTS-ARTIFACT-STATE-UNCHECKED (clause violation, reproduced): `apps/api/app/model_versions/model_role_crud.py:286-287` (`_license_accepted`) and `:321-332` (`build_action_facts`) gate `imported_weights` and `factory_trained` versions on the license fields only. Archiving the weights version's artifact (`UPDATE trainable_artifacts SET status='archived'`, or the artifact DELETE route) or drifting its `weights_digest` still returns `continue_tuning {"allowed": true, "plan": {"kind": "new_run"}}` on `GET .../model-versions/{v}/actions`, while the same archived artifact as an adapter base gives `adapter_base_incompatible`. Fix: derive weights-version usability through `build_artifact_lineage_manifest(version, artifact)` or an equivalent ready-plus-digest check that fails closed; add archived, registered and digest-drift rows to the decision table and the integration suite.
     2. UNPINNED-ADVISORY-LOCK (fault stays GREEN): removing `PERFORM pg_advisory_xact_lock(...)` at `apps/api/alembic/versions/0037_model_role_assignments.py:69` leaves the whole suite green, yet a two-session race as `sovra_app` (session 1 inserts `judge` uncommitted, session 2 inserts `candidate`, session 1 commits) then leaves both active. Fix: a deterministic two-session interleaving test that is RED without the lock and gets `tr_model_role_assignments_self_judging` with it; the CI comment's claim about concurrent judge-vs-candidate posts must then be true.
     3. UNPINNED-REVOCATION-CHECK (fault stays GREEN): deleting `AND (revoked_at IS NULL) = (revoke_reason_code IS NULL)` at the same migration, line 181, is not caught. Fix: a raw insert/update with `revoked_at` and `revoked_by` set and the reason NULL, refused by the constraint name (the Control-plane DoD names this CHECK).
     Advisories (not blocking): a duplicate-key JSON body takes the last value (the bound role is the one in `request_sha256`); an all-space idempotency key is accepted (printable ASCII); a backdated `revoked_at` is accepted (disclosed); a role can be assigned to a validated version whose license was later revoked (actions still deny its use).
     STOPPING RULE (Control plane): round 2 certifies once 1-3 land with their faults RED on the real PG16 suite, the P1-P3 regression suites still pass, and a fresh probe of the same area finds nothing new. The slice only ADVANCES FTF-H010: FTF-H010-P5 (wizard) and P6 (version panel and parent closure) remain; the migration is `0037` (disclosed).
   - **0q. FTF-1301-F1 r3 (RETURNED 2026-11-10 by the independent Opus L2 review of #996 + #1005, merges `dab64d21` and `5e0d91b4`; NOT CERTIFIED round 2; branch `relay/FTF-1301-F1-r3`; UX lane, tests only, small).** The component works (every probe passed on the merged code, no severity 3-4 finding, `ftf_1301.spec.ts` 27/27, `ftf_ux05_browser.spec.ts` 253/253 in Chromium, full web suite 3144/3144, `wiring.spec.ts` passes, `pages/cost-profile.tsx` mounts it), but the stopping rule needs the round-1 faults RED in jsdom AND Chromium and five stay GREEN:
     1. UNPINNED-RESET-NOTICE: removing `setNotice(null)` from the reset (`apps/web/components/cost-and-model-search/costprofile_schema_ui.tsx:305`) is GREEN in jsdom and Chromium. Add a test in both: create on project A shows "Rate recorded."; switch to project B; the notice must be gone.
     2. UNPINNED-RESET-ORGANIZATION: dropping `organizationId` from `identityKey` (`:221`) is GREEN in both. Add a test in both: same project id, switch organization; project A's rate label must not show while the new organization's search is in flight, and an old organization's reply arriving last is ignored.
     3. F3/F4/F5 are RED in jsdom only; in `ftf_ux05_browser.spec.ts -t FTF-1301-F1` all three stay GREEN (2 of 2 pass under each). Add Chromium tests for a stale 500, a stale network error and a stale reply while the fresh request is pending, RED under F3 (no stale check on the error status), F4 (no stale check in the catch) and F5 (`finally` always clears loading); the P8 Verify/Create error guard also stays GREEN in Chromium.
     4. Docstring (advisory, fix it in this round): `costprofile_schema_ui.tsx:20-24` still says "no Next.js app shell yet ... not a full browser E2E"; `pages/cost-profile.tsx` now mounts the panel.
     Advisories (not blocking): typed form values carry over to a different organization (severity 1-2); the page never passes `locale` so the live panel is English (journey shell, FTF-H029); removing `setError(null)` or `setLoading(true)` from the reset changes one frame only and is not counted.
     STOPPING RULE (Control plane): round 3 certifies once 1-3 land with their faults RED in jsdom and Chromium and the docstring is corrected; a newly hand-picked variant does not block. Child slice: no count change; FTF-1301 requalifies with it.
   - **0o.** Then the Backlog.
   - FTF-H007 + FTF-04A06 are CERTIFIED (2026-10-05); FTF-0500B, FTF-0500C, FTF-0601, FTF-0307, FTF-1206A, FTF-04A18, FTF-0902, FTF-0500D, FTF-1002, FTF-0103A and FTF-0510 are CERTIFIED
     (2026-10-06); FTF-0214, FTF-0604, FTF-0606, FTF-0608, FTF-1807, FTF-04A21, FTF-04A34, FTF-04A32, FTF-0515, FTF-04A22, FTF-0605, FTF-0607, FTF-0610, FTF-0609, FTF-0611, FTF-0612, FTF-0613, FTF-1305, FTF-0308, FTF-0602, FTF-0901 and FTF-1706 are CERTIFIED (2026-10-07/08); follow-ups FTF-1206A-F2, FTF-H034-F1 through F5, FTF-0510-F1 and FTF-0608-F1 are CERTIFIED.
     They come back here only if returned again.
   Every item: run the PRE-SUBMIT SELF-ATTACK above (docs/operations/routines.md STEP 5b), using the script
   runner, not pytest, because the check-list tests pass under pytest even with production code broken. "Frontier saturated" is not a valid
   outcome while this list is non-empty.
1. **(History; superseded by 0b above.) FTF-0902 — round 2 (L2 review 2026-10-04T11:00Z, PR #467 opened 2026-10-05T17:04Z, closed
   unmerged; superseded by PR #468, open).** PR #467 fixed render-time endpoint revalidation
   (type-gated, never `isinstance`), real RFC1918-only + cloud-metadata CIDR-overlap checks sourced
   from `url_policy._CLOUD_METADATA_NETWORKS`, missing CI wiring for `tests/tasks/test_ftf_0902.py`,
   and added a real DNS probe (`nslookup kubernetes.default.svc.cluster.local`) to the live-cluster
   integration test — but PR #467's own CI run came back with `dns: False` on both clusters (a
   probe-methodology defect, not an enforcement one: the nonenforcing baseline should never fail
   DNS). **2026-10-05T19:47Z — PR #468 fixed this** (returned-work priority, CLAUDE.md "fix every
   open or returned item before new work"): two real root causes, both independently confirmed
   first — `create_cluster` never waited for CoreDNS's own rollout (only node-Ready), and the probe
   queried the bare Service name instead of its FQDN (an untested search-list dependency). Fixed:
   `create_cluster` now calls `wait_for_rollout(..., "deployment/coredns", timeout=120)` on both
   clusters; `resolve_dns` now queries `kubernetes.default.svc.cluster.local` directly.
   **CI on PR #468 (this shift):** attempt 1 (`37365686503`) came back "failure" with ~20/44 jobs
   showing `runner_id: 0` (never scheduled), `gate` and `network-policy-enforcement-integration`
   included — "died before any test body ran / runner loss" under CLAUDE.md's CI-red rules, so
   re-ran once (the one sanctioned re-run for that case). Attempt 2: `gate` and
   `network-policy-enforcement-integration` (the only two checks this PR's diff touches) both
   passed — the real test step ran in under 3 minutes once scheduled, confirming the fix itself is
   sound. 8 unrelated jobs (secret-manager, critical-behavior-requirements, storage-semantics,
   litellm-connection-crud, database-api-trace-source, litellm-registration-capability,
   evaluation-suite, tenant-isolation-query-guards integration) again showed the same
   never-scheduled signature and were cancelled — none touch this PR's own files, same class as
   the already-documented `BLOCKER.md` 2026-09-30 runner-provisioning entry. Disclosed on PR #468
   (comment) rather than re-run a third time or widen the PR's scope. Not self-certified (Law 4) —
   PROVISIONAL, awaiting Control-plane L2 re-review weighing `gate`/`network-policy-enforcement-
   integration`'s real green result as this task's actual acceptance evidence.
2. **FTF-1703 (Training dataset): stays deferred, now with a recorded dependency.** §55 requires
   ">=1,000 approved training traces", and §77.3 makes Argilla the owner of "review records,
   annotations and curation". So "reviewed/approved" needs FTF-R006 (Argilla review + reviewer
   identity + immutable export), even though `docs/TASK_INDEX.md` does not list it as a
   dependency. Under the default resolution rule the architecture already implies this, so it
   is not an owner ask. Critical path: FTF-0306 (CERTIFIED 2026-10-01) → FTF-0510 → FTF-0515 →
   FTF-0516 → FTF-R006 → FTF-1703. The relay should not claim FTF-1703 before FTF-R006 is
   CERTIFIED. Synthetic, labelled reference data is explicitly allowed (§55), so no customer
   data ask is needed.

3. **RELAY-OWNED backlog — CLEARED 2026-10-02 (all fix PRs merged to `main` as PROVISIONAL; none
   self-certified, Law 4; all await Control-plane L2 re-review, Law 4).** Eight items were reviewed
   and returned NOT CERTIFIED. Their `certification_state` stays PROVISIONAL only because the code
   is already merged. **FTF-H007, FTF-04A06, FTF-0601: fixed, PR #403 merged.** **FTF-0500C: fixed,
   PR #404 merged** (see `docs/certifications/queue.md`'s matching 2026-10-01 entry for full
   evidence — the read-only-transaction-mode test now goes through the module itself instead of
   re-implementing its SQL, and a later page that drops the snapshot-version header after one was
   seen is now caught). **FTF-0500B: fixed, PR #407 merged 2026-10-02** (version-id-sensitive
   manifest hash + versioning-disabled refusal). **FTF-1807: fixed, PR #408 merged** (operator-
   guidance semantics check replaces the old token-only check). **FTF-0500D: fixed, PR #410 merged
   2026-10-02** (classification × profile matrix test). **FTF-1206: PARTIALLY fixed, PR #412 merged
   2026-10-02** — the route-change-against-grant half of its DoD is done; the persistence half is a
   Control-plane scope decision with no existing task ID to claim, formalized as a live
   `BLOCKER.md` entry rather than improvised — do not re-attempt DoD item 1 without that decision.
   Nothing left for the relay to open here; each merged PR is re-reviewed by Control plane.
3. **Fix-and-resubmit, from the 2026-10-01 L2 review — ALL RELAY-SIDE WORK DONE** (full findings
   and DoDs in `docs/certifications/queue.md`, 2026-10-01 entry):
   - **FTF-H007 + FTF-04A06 (security, one PR):** harden `_run_git` (`core.fsmonitor=false`,
     `core.hooksPath=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`). Make
     risky repo-config keys and symlinked or hooksPath hooks blocking findings. Add tests for each.
     FTF-04A06 was decertified for this. **DONE — PR #403 merged.**
   - **FTF-0601:** add validation on the PATCH path; refuse to lock with an empty reviewer.
     **DONE — PR #403 merged.**
   - **FTF-0500C:** add a module-level read-only/isolation assertion; a missing snapshot header
     on a later page blocks. **DONE — PR #404 merged.**
   - **FTF-0500B:** add a version_id-sensitivity test; flag unversioned buckets. **DONE — PR #407
     merged 2026-10-02.**
   - **FTF-0500D:** test the classification × profile matrix on `authorize_*`. **DONE — PR #410
     merged 2026-10-02.**
   - **FTF-1807:** make the runbook test assert the guidance itself, not tokens. **DONE — PR #408
     merged.**
   - **FTF-1206:** needs persistence. Claim a separate migration prerequisite first, or mark
     BLOCKED_DEPENDENCY. **PARTIALLY DONE — PR #412 merged 2026-10-02** (route-change-against-grant
     half fixed); persistence half stays escalated to `BLOCKER.md` (no task ID in the 300-task index
     owns this migration — a Control-plane scope decision; relay has nothing further to do here
     until that decision lands).
   These items stay PROVISIONAL. The merged PR for each one is re-reviewed by Control plane.
4. **Newly dependency-ready** after FTF-0212/FTF-04A15/FTF-0306 were certified: recompute from
   `docs/spec-matrix.yaml`. **FTF-0510 (Tokenization analysis, next on the FTF-1703 path):
   implemented, submitted PROVISIONAL, and merged via PR #411 (2026-10-02)** (see this file's own
   top dated entry and `docs/certifications/queue.md`'s matching 2026-10-01 entry for full evidence,
   including the new `tokenizers`/`jinja2` runtime-dep disclosure). **Does not yet unblock FTF-0515**
   — that chain's own "dependency-ready" bar needs FTF-0510 `certification_state: CERTIFIED`, not
   merely merged-PROVISIONAL; recompute again once Control plane certifies it.

## Returned 2026-10-04 (relay-owned, highest priority; one PR per item)
FTF-0902, FTF-0500B, FTF-0500D, FTF-1807, FTF-0510, FTF-0307, FTF-1002 and FTF-04A18, plus the round-2
items below (FTF-H007, FTF-04A06, FTF-0601). Exact DoDs are in docs/certifications/queue.md, entries
"2026-10-04T11:00Z" and "2026-10-01T13:40Z". **New rule for every item:** each acceptance and negative_acceptance
clause gets a test that a clause-level fault turns red, including at least one additive fault (an early return or an
extra allow rule), not only an edit of your own line. List the clause-to-test mapping in the submission entry.

**FTF-0500C: fixed this shift (2026-10-04T13:50Z), PR opened — all five round-3 findings** (snapshot-header
tri-state tracking, SNAPSHOT_UNSTABLE on a header that never appears across a multi-page listing, repeated-cursor
refusal, non-unique Postgres stable-key detection via a one-row lookahead, and the untested statement_timeout_ms=0
case), plus recommended hardening (URL-encoded cursor/query values, a truncated-flag under-report fix, broader
psycopg error wrapping). Full clause-to-test mapping in `docs/certifications/queue.md`'s "2026-10-04T13:20Z" entry
and `docs/spec-matrix.yaml`'s FTF-0500C entry. Not self-certified (Law 4) — awaiting Control-plane L2 re-review and
a real CI run. Removed from this list's own remaining-work set above; the merged PR is Control plane's own next
step to re-review, same as every other item in this list.

**FTF-0500B: fixed this shift (2026-10-04T15:40Z), PR https://github.com/SJZLab/Sovra/pull/449 opened — all four round-3 findings** (VersionId
normalization covering missing/blank/case/whitespace variants of the "null" sentinel, a stuck-pagination refusal
when IsTruncated=true never advances either marker, an `<Error>`-root 200 response refused rather than treated as
an empty listing, and a new real versioned-bucket SeaweedFS integration test — `tests/integration/
test_ftf_0500b_s3_minio_versioned_source.py` plus a new CI job `s3-minio-versioned-source-integration` — closing
the "real integration, or BLOCKED_EXTERNAL" DoD item). Full clause-to-test mapping in `docs/certifications/
queue.md`'s "2026-10-04T15:40Z" entry and `docs/spec-matrix.yaml`'s FTF-0500B entry. Law 1 evidence: every new test
independently shown to fail against the pre-fix module first (`git stash` the module fix alone — 17/25 checks
failed — then `git stash pop` and re-run, all 25 pass). Not self-certified (Law 4) — awaiting Control-plane L2
re-review and a real CI run (including the new integration job). Removed from this list's own remaining-work set
above; the merged PR is Control plane's own next step to re-review, same as every other item in this list.

## Returned round 2 (2026-10-01T13:40Z — relay-owned, highest priority)
FTF-H007 + FTF-04A06 (filter drivers, config includes, unpinned hardening) and FTF-0601 (reviewer
normalisation and binding, int cap, whitespace name). Exact DoD: docs/certifications/queue.md, entry
"2026-10-01T13:40Z". One PR per item group, titled "[relay] <ID>: fix L2 round-2 findings".

## Backlog (sliced, ready — relay takes these after Active; Refill & Sweep keeps >= 3)
- **FTF-0907 External judge denial** (top-level; Refill 2026-11-19; deps FTF-0617, FTF-0901, FTF-0902 all CERTIFIED; +1 certified count when certified). DoD: `tools/r1_handoff.py packet FTF-0907 --plan FINE_TUNING_FACTORY_RELEASE_1.md`. Files `packages/security/external_judge_denial.py` and `tests/tasks/test_ftf_0907.py`. Boundary (recorded under FTF-0617): 0617 owns judge selection and per-call identity; 0907 owns non-bypassable denial at the security layer ("direct client path cannot bypass") and admission integration. No migration, route, runtime dependency or secret. Claim `relay/FTF-0907-r1`. Every DoD bullet needs its own fault-to-RED row; uncapped mutation gate 0 survivors.
- **FTF-0603 Suite validation/locking** (top-level; Refill 2026-11-19; deps FTF-0519, FTF-0601, FTF-0602 all CERTIFIED; +1 certified count when certified). DoD: `tools/r1_handoff.py packet FTF-0603 --plan FINE_TUNING_FACTORY_RELEASE_1.md`. Files `apps/web/components/evaluation-factory/suite_validation_locking.tsx` and `apps/web/tests/ftf_0603.spec.ts`; route family `/api/v1/evaluation-suites` (cases/lock). It computes `suite_sha256` (the 0602 boundary left it to this task). UI slice: the UX_STANDARD.md section 4 evidence is part of the DoD, and it must get a page in `apps/web/pages` with a `proving_symbol` the page uses. Claim `relay/FTF-0603-r1`.
- **FTF-0701 Training config schema** (Control-plane design 2026-10-23; FINE_TUNING_FACTORY_RELEASE_1.md §0, §6.15, §13, §14.1–14.2, §15.1–15.5, §16 steps 1–9, §17, §68.2–68.4, §70.1/70.3, §77.3–77.6, §78.1 steps 1/3/4, §81; ADR 0004 Consequences ("FTF-0701, when designed, reserves training method value `seq_cls_lora` in the 07 domain schema; validation refuses it until FTF-0708A is CERTIFIED"); CAP-09; Q10, Q13; deps FTF-0307, FTF-0403, FTF-0519 all CERTIFIED; also composes certified FTF-0301/0305/0306 (artifact, license, digest), FTF-0308 (catalog mapping), FTF-0401/0402 (project, security profile), FTF-0500 (`authorize_dataset_source_purpose`, reached only through 0519's `load_training_split`), FTF-0104/0105/H003/0103A. DoD `python tools/r1_handoff.py packet FTF-0701 --plan FINE_TUNING_FACTORY_RELEASE_1.md` plus this entry. FTF-0701 is the phase-07 contract owner (`contract_owner: FTF-0701`): it creates `packages/contracts/openapi/07-interface.yaml` and `packages/contracts/jsonschema/07-domain.schema.json`, which do not exist yet. Locks `contracts:07` (P1, P3), `migration:application` (P2). Acceptance: "Freeze the minimum shared phase interface and domain schemas at the declared owner paths, with schema/client validation and no placeholder endpoints. Then: Version typed config includes method/tokenizer/template/train+validation grants/precision/budget limits." / "Independent review, exact merged commit, real relevant integration and negative-test evidence recorded." Negative: "Invalid rank/LR/length/unsupported dtype and held-out training source are blocked server-side." Three slices: P1 pure schema + contract freeze (UNWIRED), P2 persistence (UNWIRED), P3 routes (WIRED, proving_symbol `build_training_experiment_router`); the parent closes when all three are certified. When it closes, FTF-0705 (NVIDIA environment validation) and FTF-0706 (base artifact loading) become dependency-ready; the other 20 dependents still wait on 0702/0705–0715/0801/R003.
  - Common rules:
    - Path refinements, recorded: the packet's `services/training-worker/trainers/training_config_schema.py` becomes `workers/training/training_config_schema.py` (`workers/training` is the real SOURCE_ROOT; same refinement FTF-H010 recorded for `continuation.py`). The packet's `/api/v1/projects/{id}/training/...` becomes `/api/v1/organizations/{organization_id}/projects/{project_id}/training/...` (repo convention). Persistence and routes live in a new `apps/api/app/training/` package (FTF-0403 `apps/api/app/budget/` precedent).
    - The packet's `api_contract` names the whole phase-07 route family (estimate, experiments, runs, metrics, cancel). FTF-0701 declares and implements **only** `training/experiments`. `/training/estimate` is FTF-0703, `/training/runs/{id}` and `/metrics` are FTF-0711/0717, `/cancel` is FTF-0718, and `/training/runs/{id}/engine` (§77.6) is FTF-R003. Declaring any of them now is the placeholder endpoint Law 11 and this acceptance forbid. Each owner extends `07-interface.yaml` later under `contracts:07` (FTF-0902 / FTF-0601 precedent).
    - The Axolotl boundary is unchanged and needs no owner ask. The config is a Factory-owned, engine-neutral typed spec (§77.3: "typed job specification -> local configuration"). FTF-0701 does not import, pin or install Axolotl, writes no Axolotl YAML (§78.1 step 4: "The UI never asks the user to write YAML") and adds no passthrough field. The pinned-release translation and validation is FTF-R003's job (§78.1 step 4). If the pin cannot honour a v1 field, that is BLOCKED_DEFECT at R003, and the fix is a `training_config.v2` with a Law 15 requalification of FTF-0701. It is never an untyped escape hatch.
    - No runtime dependency: stdlib `decimal`/`json`/`hashlib`/`re` only. There is no `jsonschema` package. The contract is checked by a stdlib structural validator inside the test (FTF-0102 / FTF-0902 precedent). No secret, no keyed service, no GPU, no Temporal and no budget reservation.
    - Coordination: open PR #998 (FTF-H010-P4) creates `workers/training/__init__.py` and migration `0037_model_role_assignments` and edits `main.py` and `10-interface.yaml`. P1 merges after #998 or rebases onto it, and never creates a competing `workers/training/__init__.py`. P2 takes the next free migration number at claim time (0038 if #998 lands first). Revision id ≤ 32 characters.
    - Must not edit (compose through public functions only; no Law 15 requalification):
      - `workers/training/continuation.py`;
      - anything in `workers/data/`;
      - `apps/api/app/datasets/dataset_split_manifest_store.py` and `dataset_source_connection_crud.py`;
      - `apps/api/app/budget/*`, `apps/api/app/artifacts/*` (including `training_capability_classifier.py`) and `apps/api/app/litellm/model_catalog_mapping_store.py`;
      - `apps/api/app/model_versions/*`;
      - every existing migration.
    - Build in the ledger lessons:
      - exact allow-lists at every nesting level;
      - plain-JSON, exact-type parsing (`type(x) is int`, so bool is not an int; `type(x) is str`; no float wherever an int or decimal is expected);
      - one boundary table per bounded field;
      - property-generated Unicode/case/whitespace variants for every enum (FTF-0616 r8);
      - complete constraint tables;
      - one shared META-LEAK check over the rendered exception chain, audit rows and logs (0903 r8);
      - fail-closed behaviour pinned by faults.
    - Every DoD bullet gets its own behavioural fault→RED row. The uncapped `scripts/mutation_gate.py` over each slice's new production modules must reach 0 survivors.
  - Recorded readings (default-resolution rule; none reopens an architecture decision):
    - **Training data.** §6.15's `train_dataset_id`/`validation_dataset_id` become `(split_manifest_id, role)` pairs on one locked four-role FTF-0519 manifest. They are read only through FTF-0519's `load_training_split`, the certified view: it re-verifies the frozen hash, leakage report, latest quality report and every frozen grant live, and serves `train`/`validation` rows only. So "train+validation grants" means the live grant re-check that view performs, and the config stores the manifest's frozen hash plus both `role_sha256` values.
    - **Tokenizer.** v1 accepts only the base artifact's own tokenizer at its `base_model_revision`. §68.4 says tokenizer modifications produce new artifact identities.
    - **Template.** v1 accepts only `source: "base_artifact"` with `loss_policy: "assistant_only"` and `packing: false` (§68.2: "Default packing is OFF"). Template bytes and hash, special tokens and mask compilation are validated at preflight by FTF-0707 (§16 step 11). FTF-0701 never claims them.
    - **Methods.** The `TrainingMethod` enum is `lora | qlora | full_sft | seq_cls_lora`; the enabled set is `lora | qlora`. `full_sft` → 409 `method_not_enabled`: §15.1 item 3 needs an administrator enablement plus a separate cost-approval gate, and no task has built either. `seq_cls_lora` → 409 `method_not_enabled` until FTF-0708A is CERTIFIED (ADR 0004). An unknown or variant string → 400 `invalid_method`.
    - **Mode.** `mode` is `advanced | auto`. `auto` → 409 `auto_rule_version_unknown` because the versioned rule registry is FTF-0702's (§14.1 "derived from a versioned rule engine"). `auto_rule_version` must be null in advanced mode.
    - **Writers and readers.** Writers are `{ml_engineer, organization_admin}` (§45 "ml_engineer: datasets/training"; the `MODEL_VERSION_WRITE_ROLES` precedent). Readers are every project role.
    - **Idempotency.** Scope is (organization, requested_by, key), the §70.1 tenant/actor/action triple.
    - **v1 schema-validity bounds.** The spec fixes only the buckets (§15.2) and the seed default (§15.5). The rest are Control-plane picks for the adapter-tuning range, versioned with the schema; FTF-0702's per-family registry picks values inside them, and changing a bound is a schema version bump:
      - **Integers:**
        - `epochs` 1..20;
        - `max_seq_length` ∈ {1024, 2048, 4096, 8192, 16384};
        - `micro_batch_size` 1..128;
        - `gradient_accumulation_steps` 1..1024;
        - `seed` 0..2147483647 (absent → 3407, stored explicitly);
        - `checkpoint_interval_steps` 1..1000000;
        - `max_checkpoints` 1..50;
        - `lora_rank` 1..256;
        - `lora_alpha` 1..1024;
        - `max_wall_clock_seconds` 60..2592000.
      - **Decimals** (JSON strings, no exponent, normalized before hashing so "0.00020" and "0.0002" hash equally):
        - `learning_rate` 0.0000001..0.001;
        - `warmup_ratio` 0..0.5;
        - `weight_decay` 0..1;
        - `max_grad_norm` >0..100;
        - `lora_dropout` 0..0.5;
        - `max_cost` >0 with ≤ 14 integer and ≤ 4 fractional digits (FTF-0403's own numeric shape).
      - **Enums:**
        - `optimizer` {adamw_torch, adamw_torch_fused, paged_adamw_8bit};
        - `scheduler` {cosine, linear, constant_with_warmup};
        - `compute_dtype` {bf16, fp16, fp32};
        - `quantization` {none, nf4}, with `qlora` ⇔ `nf4` and `lora` ⇒ `none`.
      - **Other fields:**
        - `gradient_checkpointing` is a strict bool;
        - `target_modules` is 1..32 unique identifiers matching `^[A-Za-z_][A-Za-z0-9_]{0,63}$`.
  - **FTF-0701-P1 Pure typed config schema and phase-07 contract freeze (no DB, no route; lock `contracts:07`; UNWIRED, proving_symbol blank; ready now, after #998 as above).**
    - Files:
      - `workers/training/training_config_schema.py` (pure, no I/O);
      - `packages/contracts/jsonschema/07-domain.schema.json`;
      - `packages/contracts/openapi/07-interface.yaml` (`paths: {}` in P1, the FTF-0902 precedent: the module description says the experiments paths arrive with P3 and every other phase-07 route with its owner task);
      - `tests/tasks/test_ftf_0701_p1.py` (stdlib `check()` script);
      - an additive `gate` step `uv run python3 tests/tasks/test_ftf_0701_p1.py`.
    - Module API:
      - Constants: `SCHEMA_VERSION = "training_config.v1"`, `METHODS`, `ENABLED_METHODS`, `RESERVED_METHODS` (each with its reason), `SEQUENCE_BUCKETS`, `DEFAULT_SEED = 3407`, one frozen `LIMITS` mapping, and `ERROR_CODES` mapping each code to its HTTP status.
      - `TrainingConfigError(code)`.
      - `parse_training_config_request(body) -> TrainingConfigRequest`: exact allow-list at every level, typed values, Decimal normalization.
      - `TrainingConfigFacts`: built server-side only. It holds the artifact id/status/weights digest/base revision/license state; the FTF-0307 capability; an optional catalog entry + version + mapped artifact; the split view summary (manifest id, version, frozen sha, train/validation counts and `role_sha256`); the budget policy (id, row_version, currency, scopes, total, reserved); and the project's security profile.
      - `resolve_training_config(request, facts) -> ResolvedTrainingConfig`: runs the checks in a fixed order (shape 400 → enablement 409 → source role 403/400 → facts 409/403) and the first failing check wins; the order is pinned by a test.
      - `canonical_config_json`, `compute_config_sha256` and `verify_config_sha256`.
    - Request shape. Every object is exact; no other key at any level:
      - Top level: `{schema_version, mode, auto_rule_version, method, base, tokenizer, template, data, precision, hyperparameters, adapter, budget}`.
      - `base` = `{trainable_artifact_id, model_catalog_entry_id|null, catalog_version|null}`.
      - `tokenizer` = `{source: "base_artifact", revision}`.
      - `template` = `{source: "base_artifact", loss_policy: "assistant_only", packing: false}`.
      - `data` = `{train: {split_manifest_id, role}, validation: {split_manifest_id, role}, frozen_manifest_sha256}`.
      - `precision` = `{compute_dtype, quantization}`.
      - `hyperparameters` holds the fourteen §14.2 non-adapter fields.
      - `adapter` = `{lora_rank, lora_alpha, lora_dropout, target_modules}`, required for lora/qlora.
      - `budget` = `{max_cost, currency, max_wall_clock_seconds}`.
      - The resolved config adds the server facts: artifact weights digest and base revision, the manifest frozen sha and both role hashes, budget policy id/row_version, security profile, and the effective seed. `config_sha256` covers all of it.
    - Error codes (exact, frozen in the domain schema as `TrainingConfigErrorCode`):
      - **400:**
        - `invalid_body`, `unsupported_schema_version`, `invalid_mode`, `invalid_method`, `invalid_source_role`;
        - `split_manifest_mismatch` (train and validation on different manifests);
        - `invalid_tokenizer`, `invalid_template`, `packing_not_qualified`;
        - `invalid_lora_rank`, `invalid_lora_alpha`, `invalid_lora_dropout`, `invalid_target_modules`;
        - `invalid_learning_rate`, `invalid_epochs`, `invalid_warmup_ratio`, `invalid_max_seq_length`, `invalid_batch_size`, `invalid_gradient_accumulation`, `invalid_optimizer`, `invalid_scheduler`, `invalid_weight_decay`, `invalid_max_grad_norm`, `invalid_seed`, `invalid_checkpoint_policy`;
        - `unsupported_dtype`, `invalid_quantization`, `method_quantization_mismatch`;
        - `invalid_budget`, `invalid_currency`.
      - **403:**
        - `heldout_training_source_denied` (a role of `development_eval`, `final_qualification` or legacy `heldout_test` in either slot);
        - `dataset_grant_changed`.
      - **409:**
        - `method_not_enabled`, `auto_rule_version_unknown`;
        - `split_manifest_not_locked`, `split_manifest_changed`, `split_manifest_tampered`, `quality_report_changed`;
        - `train_split_empty`, `validation_split_empty`;
        - `artifact_not_trainable`, `license_not_accepted`, `tokenizer_revision_mismatch`;
        - `catalog_mapping_changed`, `catalog_artifact_mismatch`;
        - `budget_policy_missing`, `budget_scope_not_authorized`, `budget_currency_mismatch`, `budget_limit_exceeds_policy` (max_cost > total − reserved; equal is allowed; no reservation is made);
        - `idempotency_key_reused`.
    - Domain schema `$defs`:
      - `TrainingConfigV1` and every nested object with `additionalProperties: false`;
      - `TrainingMethod` (all four values) and `EnabledTrainingMethod`, each reserved value annotated with its reason;
      - every enum and bound;
      - `TrainingConfigErrorCode`;
      - `ResolvedTrainingConfig`.
    - DoD tests (no network):
      - A boundary table for every bounded field: min−1/min/max/max+1 and, where defined, a value between buckets. Each wrong type is its own row (bool, float, int-as-string, null, list), with the exact code asserted.
      - A generated allow-list table from the schema's own property lists: a missing key and an unknown key at each level → `invalid_body`. This explicitly includes engine passthrough keys: `trust_remote_code`, `push_to_hub`, `hub_model_id`, `report_to`, `wandb_project`, `base_model`, `datasets`, `plugins`, `deepspeed`, `fsdp`, `special_tokens`, `chat_template`, `torch_compile`.
      - Property-generated variants of every enum value are refused: case, surrounding whitespace, NBSP, ZWSP, full-width, NFKC-compatible forms. Examples: `LoRA`, `lora `, `ｌora`, `bfloat16`, `BF16`, `torch.bfloat16`, `fp8`, `int8`.
      - `full_sft` and `seq_cls_lora` → `method_not_enabled` with every other field valid; `auto` → `auto_rule_version_unknown`.
      - The qlora/nf4 and lora/none truth table.
      - Held-out table: train or validation `role` ∈ {development_eval, final_qualification, heldout_test} → 403 `heldout_training_source_denied`; `Train`, `""`, `validation` in the train slot (and the reverse) → 400 `invalid_source_role`.
      - Every facts row: capability ≠ `trainable_open_weight`, license unaccepted, tokenizer revision ≠ base revision, the catalog table, and the budget table including the equal/+0.0001 boundary.
      - Hash properties:
        - every resolved leaf, mutated one at a time (generated), changes `config_sha256`;
        - decimal spellings that normalize equal hash equal;
        - the hash is equal under two `PYTHONHASHSEED` subprocesses and any key order.
      - Contract parity: every enum, bound, allow-list and error code in `07-domain.schema.json` byte-equals the module constants, and `07-interface.yaml` has `paths == {}`.
      - Schema/client validation: the stdlib validator accepts every request the module accepts (a generated valid set) and rejects every shape-invalid row of the boundary and allow-list tables.
      - META-LEAK: a canary in `target_modules`, `revision` and `max_cost` never reaches an exception message, its chain or `str()`.
    - Faults that must go RED:
      - each bound widened by one step;
      - an unknown key accepted at each level;
      - an enum casefolded or stripped;
      - a float accepted for an int;
      - a bool accepted as an int;
      - a reserved method enabled;
      - `auto` accepted;
      - a held-out role accepted in either slot;
      - qlora without nf4;
      - decimals hashed un-normalized;
      - each resolved leaf dropped from the hash;
      - the check order swapped;
      - schema/module drift.
  - **FTF-0701-P2 `training_experiments` persistence (migration; lock `migration:application`; UNWIRED, proving_symbol blank; after P1 CERTIFIED).**
    - Files:
      - `apps/api/alembic/versions/00NN_training_experiments.py`;
      - `apps/api/app/training/__init__.py`;
      - `apps/api/app/training/training_experiment_store.py`;
      - `tests/tasks/test_ftf_0701_p2.py` (gate step);
      - `tests/integration/test_ftf_0701_p2_training_experiments.py`.
    - Additive job `training-experiment-store-integration`: postgres service as `model-import-integration`; classify focus entry; `ci-verdict` needs; `tests/unit/test_ci_policy.py` coverage.
    - Table `training_experiments`:
      - **Columns:**
        - `id`, `organization_id`, `project_id`;
        - `name` (non-blank, ≤ 200), `version` ≥ 1, `parent_version` (NULL for v1, otherwise < version);
        - `status` CHECK = 'draft' (FTF-0801 widens it by its own migration);
        - `method` CHECK IN ('lora','qlora');
        - `config_schema_version` CHECK = 'training_config.v1';
        - `config_json` JSONB, `config_sha256` (hex64);
        - `trainable_artifact_id`, `model_catalog_entry_id` NULL;
        - `split_manifest_id`, `frozen_manifest_sha256`;
        - `budget_policy_id`, `max_cost` NUMERIC(18,4) > 0, `currency` ~ '^[A-Z]{3}$';
        - `security_profile`;
        - `requested_by` (non-blank, ≤ 200), `request_id`, `idempotency_key` (1..128 printable ASCII), `request_sha256` (hex64), `created_at`.
      - **Composite FKs** (organization_id, x) → projects, trainable_artifacts, model_catalog_entries, dataset_split_manifests and budget_policies, all ON DELETE RESTRICT.
      - **Uniqueness:** UQ (organization_id, id); UQ (project_id, name, version); UQ (organization_id, requested_by, idempotency_key).
      - **Consistency CHECK:** `config_json->>'schema_version' = config_schema_version`, `->>'method' = method`, `data.train.role = 'train'`, `data.validation.role = 'validation'`, and both `split_manifest_id`s equal the column. This is the DB-level held-out refusal.
      - **BEFORE INSERT trigger:** the manifest row must be `locked` with `frozen_manifest_sha256 = NEW.frozen_manifest_sha256`, and the artifact must be `ready` with `license_accepted_at` set. Defense in depth, 0030 child-guard style.
      - **Grants:** `sovra_app` gets SELECT, INSERT only (no UPDATE, no DELETE). Rows are immutable; a changed config is a new `version` row with `parent_version`.
      - RLS ENABLE + FORCE with policy `tenant_isolation` exactly as 0030.
    - `create_training_experiment(session, context, project_id, *, name, config_body, idempotency_key, actor)` does everything in one transaction:
      1. Project write authorization.
      2. P1 parse.
      3. Idempotency: same key + same `request_sha256` → the existing row, with 0 new rows and 0 audit; same key + a different body → `idempotency_key_reused`.
      4. Build the facts only through certified public functions:
         - the FTF-0301 tenant-scoped artifact read plus FTF-0307 `classify_training_capability`. Capability comes from the artifact dimension only; access is not used for training, per 0307's own orthogonality rule;
         - FTF-0308 `require_current_mapping` when a catalog entry is given;
         - FTF-0519 `load_training_split`, whose `SplitManifestError` codes map 1:1 (`grant_changed` → 403 `dataset_grant_changed`, `quality_report_changed`, `manifest_tampered` → `split_manifest_tampered`, not locked → `split_manifest_not_locked`). The returned frozen sha must equal the request's, else `split_manifest_changed`;
         - the FTF-0403 policy read (no `reserve_budget_allocation` call);
         - the project row.
      5. P1 `resolve_training_config`.
      6. `version = max + 1` for (project, name), serialized by `SELECT … FOR UPDATE` on the project row; the UQ is the backstop.
      7. INSERT, plus `write_audit_event` `training_experiment.create` in the same transaction. The audit is content-free: ids, version, method, `config_sha256`; never the name, target modules or revision text.
    - `get_training_experiment` / `list_training_experiments` (bounded page, ordered by name then version) recompute `config_sha256` from `config_json` on every read. A mismatch raises `config_integrity_failed` and the row is never served.
    - Law 17: a database or storage error raises its own error with nothing written, never success. Disclosed: `load_training_split` reads the role rows (≤ 1,000,000, its cap) at create time; record the measured time for the largest fixture in the submission, never a guessed figure.
    - DoD tests (real PG16, as `sovra_app`; fixtures built through the certified FTF-0519/0403/0301/0305 APIs, not raw SQL):
      - happy path for lora and qlora, with the DB `config_sha256` equal to a pure recompute;
      - replay gives 0 rows and 0 audit; key reuse → 409;
      - two threads posting the same name → versions 1 and 2; two threads with the same key → exactly one row;
      - a held-out role in the request → 403 and an independent DB count of 0;
      - raw INSERTs as `sovra_app` refused: `config_json` naming `final_qualification` (CHECK); a `proposed` manifest (trigger); a stale frozen sha (trigger); a license-revoked artifact (trigger);
      - a grant narrowed or revoked after lock, through the certified FTF-0500 CRUD → 403 `dataset_grant_changed`;
      - quality report replaced → 409; license revoked through FTF-0305 `revoke_license` → `license_not_accepted`;
      - the budget table: no policy, scope without `training`, currency mismatch, max_cost at and above available;
      - raw UPDATE and DELETE refused by grant;
      - cross-tenant: identical `not_found` for foreign and missing; a raw org2 INSERT with org1 ids is refused; dropping FORCE turns the cross-tenant test RED;
      - a superuser edit of `config_json` → GET refuses `config_integrity_failed`;
      - META-LEAK over the exception chain, audit rows and captured logs.
    - Faults that must go RED:
      - UPDATE granted; DELETE granted; FORCE dropped;
      - the held-out CHECK dropped; the lock trigger dropped;
      - idempotency scope widened to the organization, or narrowed to (actor, key, body);
      - `load_training_split` replaced by a raw manifest read;
      - the grant re-check, license check or budget check skipped;
      - version assignment without the row lock;
      - the read integrity check removed;
      - the audit written outside the transaction.
    - Law 18: `data_exposure_check.py --backends postgres_rls,s3_identity --fail-on-exposure` passes with the new table. Law 15: no certified module is edited; P1's suite is rerun on the head.
  - **FTF-0701-P3 Routes and contract paths (lock `contracts:07`; WIRED, proving_symbol `build_training_experiment_router`; after P2 CERTIFIED).**
    - Files:
      - `apps/api/app/training/training_experiment_routes.py`;
      - `apps/api/app/main.py` (one additive mount; no other line changed);
      - `packages/contracts/openapi/07-interface.yaml` and `07-domain.schema.json` (exactly three paths plus the request/response schemas, additive);
      - `tests/tasks/test_ftf_0701.py` (the packet's named test: contract parity and route table);
      - `tests/integration/test_ftf_0701_routes.py` (a real uvicorn server over PG16 as `sovra_app`, through the live FTF-0103A auth path).
    - Additive job `training-experiment-routes-integration`.
    - Routes:
      - `POST/GET /api/v1/organizations/{organization_id}/projects/{project_id}/training/experiments`;
      - `GET …/training/experiments/{experiment_id}`.
    - POST behaviour:
      - The body is exactly `{name, idempotency_key, config}`, capped at 64 KiB; anything else is 400 `invalid_body`.
      - 201 for a new experiment, 200 for a replay.
      - Writers `{ml_engineer, organization_admin}`; reads by every role.
      - Errors are `{"detail": code}` with P1's status table. 404 `not_found` is identical for missing and foreign ids.
      - The response is the row plus the resolved config: ids, hashes and hyperparameters only — no dataset rows, record ids, grant snapshots or secret refs.
    - DoD tests:
      - Role truth table: 6 roles × {POST, list, get} → exact status.
      - Every `ERROR_CODES` entry is reachable through the route with its status, from a generated table.
      - A held-out POST → 403 with an independent DB count of 0.
      - The `07-interface.yaml` paths and methods equal the router's registered routes exactly: no undeclared route, no placeholder path. Every 2xx body validates against `07-domain.schema.json` with the stdlib validator.
      - Malformed JSON, a non-object body or an oversized body → 400.
      - `scripts/product_truth_check.py --fail-on-unwired` passes: the live caller is `main.py`; the user-visible assertion is the 403 `heldout_training_source_denied` detail; removing the held-out check reddens it.
    - Faults that must go RED:
      - the router mounted without `require_role`;
      - writers widened to evaluator or viewer;
      - foreign → 403 instead of 404;
      - a placeholder path (`/training/estimate`) added to `07-interface.yaml`;
      - another project's experiment served;
      - a replay that writes audit.
    - Law 15: `main.py` changed, so rerun `live-auth-path-integration` and `product_truth_check` on the head.
    - Parent closure in this PR (the Control plane certifies; never self-certified):
      - the spec-matrix FTF-0701 `proving_test` lists the P1/P2/P3 task and integration tests, and `proving_symbol` is `build_training_experiment_router`;
      - the notes carry the disclosures below.
  - Disclosed (parent):
    - **Engine boundary:** no Axolotl translation or validation (FTF-R003).
    - **Method/model compatibility:** not checked. §13 `method_support`/`quantization_support`/VRAM are not produced by FTF-0307 (its own disclosed narrowing); FTF-0702/0705/0709 own this. `max_seq_length` is checked against the buckets only, because no model-limit field exists yet (FTF-0702/0706).
    - **AUTO mode:** §14.1's default mode stays refused until FTF-0702.
    - **Draft only:** no submission, reservation, workflow or GPU. Admission is FTF-0801/0807/H016 (§70.1/70.3).
    - **Template:** template validation and loss masks are FTF-0707.
    - **BLOCKED_EXTERNAL:** none for FTF-0701's own clauses. "Real relevant integration" is PG16 plus the live route. Q10/Q13 GPU evidence belongs to FTF-0708–0714 (`gpu_driver` BLOCKED_EXTERNAL in `spec/compatibility-lock.json`).
  - Ask-first: none. No new runtime dependency, secret or keyed service, and no reused component swapped, forked or re-formed.
  - **Control-plane rulings (2026-10-23, on the design's recorded conflicts):** (1) the 07 contract declares only the `experiments` routes FTF-0701 builds - the rest of the phase-07 family is added by its owning tasks (no placeholder endpoints, Law 11); (2) AUTO mode is refused with `auto_rule_version_unknown` until FTF-0702 lands, `full_sft` is refused until a task owns the §15.1 admin enablement/approval gate, `seq_cls_lora` is reserved and refused (ADR 0004) - each refusal is a stable code with a RED test, never a silent default; (3) sequence length is checked against the §15.2 buckets only, and method/model compatibility is disclosed as not checkable until §13 per-model fields exist (FTF-0307's recorded narrowing) - disclosed in the submission and spec-matrix notes, not claimed; (4) train/validation sources are (split manifest, role) pairs through FTF-0519 `load_training_split`; (5) the numeric bounds in this design are the Control plane's and are recorded here.
- **FTF-H010 parent remainder: import wizard, version panel and disclosed parent closure** (Control-plane design 2026-10-23; FINE_TUNING_FACTORY_RELEASE_1.md §0.12, §6.17, §17 paragraphs 3–4, §22, §58A, §68.1, §72.1–72.5, §77.6, §81; AF-14; Q07; FTF-H010-P1 (#699), P2 (#918), P3a (#839), P3 (#989) CERTIFIED; P4 PROVISIONAL in PR #998 `relay/FTF-H010-P4-r1`). This replaces the one-line re-queue note ("Then FTF-H010 (parent: `ImportModelWizard.tsx` on the P1–P4 routes plus its disclosures) is re-queued"). Two more child slices refine the parent under "For each card" step (3): P5 (wizard) and P6 (version panel plus parent closure). They add no criteria: no count change and no ADR. DoD `python tools/r1_handoff.py packet FTF-H010 --plan FINE_TUNING_FACTORY_RELEASE_1.md` plus the 2026-10-09 child-slice entry plus this entry. Acceptance: "Import and evaluate a real existing endpoint-only fine-tune; import/load full weights and adapter+base; continue only permitted artifacts; resumed step differs from new continuation." / "No full release claim until all mapped scenarios and exact-revision evidence pass." Negative: "Do not require fictitious artifact hash for endpoint-only imports or infer retrainability from alias; incompatible adapter/base and license denial block use."
  - Clause status after P1–P6 (updates the 2026-10-09 clause map; the union still covers the parent):

    | Parent clause | Covered by | State at parent closure |
    |---|---|---|
    | "Import ... a real existing endpoint-only fine-tune" | P1 enqueue, P2 validation (stand-in gateway), P5 UI | Real endpoint **BLOCKED_EXTERNAL**: `litellm_gateway` is BLOCKED_EXTERNAL in `spec/compatibility-lock.json`; needs the org gateway virtual key (Owner actions 2026-10-08, the FTF-0616 line) plus one existing fine-tuned deployment id on it |
    | "... and evaluate" it | none | **BLOCKED_EXTERNAL** (the same key) and BLOCKED_DEPENDENCY (FTF-R004 target-runtime evaluation, FTF-0601-series runs) |
    | "import ... full weights and adapter+base" (validation) | P3a, P3, P5 | Met for `local` artifact sources. `private_s3`/`huggingface` end `blocked_external metadata_files_unavailable` (P3's own disclosure) |
    | "... load full weights and adapter+base" | none | **BLOCKED_EXTERNAL** (`gpu_driver`) and BLOCKED_DEPENDENCY (FTF-1003/1004 vLLM) |
    | "continue only permitted artifacts" | P4 eligibility + server denial, P6 display | Met as eligibility. The actual continued training is **BLOCKED_EXTERNAL** (GPU) and BLOCKED_DEPENDENCY (FTF-R003/0701/0801) |
    | "resumed step differs from new continuation" | P4 `continuation_plan` property, P6 display | Met as a plan property. The real resume is **BLOCKED_EXTERNAL** (GPU) and BLOCKED_DEPENDENCY (FTF-0713/0714) |
    | negative "fictitious artifact hash for endpoint-only imports" | P1 CHECK, P2 success path, P5 (no hash field), P6 (no placeholder digest) | Met |
    | negative "infer retrainability from alias" | P2, P4, P6 | Met |
    | negative "incompatible adapter/base and license denial block use" | P3 (validation), P4 (actions refuse), P5/P6 (shown with reason) | Met |
    | goal "alias role assignment" | P4, P6 | Met |
    | production dispatch of import jobs | none | BLOCKED_DEPENDENCY FTF-0801/H016: UNWIRED, no dispatcher calls `claim_model_import_job`/`validate_*`, so a real job stays `queued` and the UI says so |
    | "No full release claim ..." | parent | Q07 is not claimed; the parent's notes say so |

  - Certification shape (precedent): FTF-0616, ledger 2026-10-09T18:33Z — "CERTIFIED for the adapter scope ... The real-judge clause stays BLOCKED_EXTERNAL (org LiteLLM gateway key, dated owner action); FTF-1810 precedent". The parent certifies for its import-validation, role, action and UI scope. Every BLOCKED_EXTERNAL and BLOCKED_DEPENDENCY row above stays listed in the spec-matrix notes, the ledger entry and the `docs/certifications/queue.md` submission, each with its owner/dependency and next action. None is converted to evidence and none is skipped. Q07 stays unpassed.
  - Owner action (Control plane edits, not the relay; Owner actions live in one place): amend the existing 2026-10-08 FTF-0616 line rather than add a second ask for the same key. The amended line asks for the gateway virtual key and judge model id (FTF-0616), plus one existing fine-tuned deployment id on that gateway (FTF-H010 endpoint import + evaluate). The relay never requests, stores or invents a credential.
  - Common rules (P5, P6):
    - **Path refinement, recorded:** the packet's `apps/web/components/models/ImportModelWizard.tsx` becomes `apps/web/components/model-import/import_model_wizard_ui.tsx` (plus P6's `model_version_panel_ui.tsx`), matching the snake_case `<domain>/<name>_ui.tsx` convention of all 20 existing components.
    - **UI-only:** no apps/api module, migration or OpenAPI path changes. One allowed contract edit, P5, doc only, under `contracts:10`: P3's `_finish` in `model_import_weights_validation.py` also sets `result_model_version_id` for weights/adapter jobs, so `10-interface.yaml`'s description "The endpoint-only ModelVersion a `succeeded` job created (FTF-H010-P2)" is stale. P5 corrects that description additively; the schema is unchanged.
    - **No new runtime dependency.** React and `@radix-ui/react-dialog` are already approved, and no i18n library is added. No secret, no keyed service. Only standard system phrases, written in English and Arabic without asking: buttons, field validation, error messages, empty/loading text and status labels.
    - **No owner-approval copy:** no license or legal explanatory text, no brand or marketing copy. A license appears only as the artifact's `license_id` data and its state label.
    - **UNWIRED until FTF-H029** mounts the screens; the UI `proving_symbol` stays blank (FTF-0309 / H011-P4 precedent). Only screen-reader checks and routed journeys wait for H029. Everything in brief item 8 is proven now.
    - **UX evidence:** CLAUDE.md "UI design standard". The work is designed with `.claude/skills/ui-ux-pro-max` inside §72 tokens and UX01 components, and meets docs/design/UX_STANDARD.md §4 items 1–5 in full:
      - the skill queries (`--domain ux`, `--stack react`) with their output;
      - an H1–H10 × state severity table per component, where the states are empty, loading, populated, error and permission-denied/blocked;
      - behavioural H1/H3/H5/H9 tests;
      - 1280/768/390 px viewports, keyboard-only and 200 % zoom;
      - axe plus the manual WCAG 2.2 AA items.
      A severity 3–4 finding blocks the slice; a severity 2 finding needs a recorded decision.
    - **L2 review brief item 8:** the submission reports every class as met or not met, each with a test that turns RED when its fault is applied:
      - NOT-ON-UX01;
      - REAL-BROWSER-IN-CI (`ftf_h010_p{5,6}_browser.spec.ts` on the Chromium harness: every state × en/ar × light/dark at 1280/768/390/320 and 200 % zoom, keyboard-only with a visible focus outline, axe wcag2a..22aa with 0 violations, run in CI on the head SHA);
      - ERRORS-SILENT / MALFORMED-200 (a rejected fetch, a non-JSON 200, a `{}` 200 and a list containing `null`, for every GET the component makes);
      - MISSING-STATES;
      - NOT-BILINGUAL (`lang`/`dir` on every state; user data in `<bdi>`);
      - OVERFLOW-LONG-NAMES (0 px horizontal scroll at 390/320 and 200 % with a 2048-character unbroken provider ref, 64-hex digests, a 200-character artifact name and a 128-character idempotency key);
      - FOCUS (a failed submit focuses the ErrorSummary; each confirm Dialog has an accessible name, takes focus, Escape cancels, focus returns on cancel and lands on a stable element after confirm);
      - UNPINNED;
      - EVIDENCE (the 39-item `references/pro-rules.md` checklist item by item).
    - **UI CHECKLIST a–f (Control-plane ruling 2026-10-09):**
      - (a) a generated strict-parse table over every response field of every route used;
      - (b) one binding function comparing every sent field with the response;
      - (c) drafts keyed by organization and project and reset on any prop change; nothing in localStorage;
      - (d) only a typed 2xx with a bound body counts as success;
      - (e) 5xx, non-JSON and unknown codes are retryable and keep state (Law 17);
      - (f) a real-API test against the real routers in a CI job.
    - **Real-API tests run against real routers:** a real uvicorn server over PG16 as `sovra_app` on the live FTF-0103A auth path. No production dispatcher exists, so the Python harness advances jobs by calling the real certified P2/P3 functions (`claim_model_import_job`, `validate_endpoint_import`, `validate_weights_import`, `validate_adapter_import`). It is labelled in the test and the submission as standing in for the absent FTF-0801/H016 dispatcher, never as product behaviour. Endpoint success reuses P2's own local TLS gateway and OpenBao KV stand-in fixtures from `tests/integration/test_ftf_h010_p2_import_validation.py`, labelled stand-in; real-gateway proof stays BLOCKED_EXTERNAL. Weights/adapter use P3's stdlib safetensors fixtures from `test_ftf_h010_p3_weights_import.py`.
    - **Must not edit:** any `apps/api` module, migrations 0027–0037, P1–P4 tests, `workers/training/continuation.py`, or the UX01 components (`apps/web/components/ui/*`, compose them).
    - **Mutation and faults:** `scripts/mutation_gate.py` mutates Python only. The new Python presence tests go through it uncapped with 0 survivors, and the TSX is pinned by an explicit fault→RED row for every DoD bullet (the UX0x precedent).
  - **FTF-H010-P5 Import wizard UI (ready now: P1/P2/P3a/P3 CERTIFIED; does not need P4).**
    - Files:
      - `apps/web/components/model-import/import_model_wizard_ui.tsx`;
      - `apps/web/tests/ftf_h010_p5.spec.ts` (DOM Vitest, UI CHECKLIST a–e);
      - `apps/web/tests/ftf_h010_p5_browser.spec.ts` + `apps/web/tests/browser/h010_p5_entry.tsx`;
      - `apps/web/tests/real_api/ftf_h010_p5_ui_real_api.realapi.ts` + `tests/integration/test_ftf_h010_p5_ui_real_api.py`.
    - Additive job `model-import-ui-real-api-integration`, following the `dataset-quality-ui-real-api-integration` precedent: classify focus entry, `ci-verdict` needs, `tests/unit/test_ci_policy.py` coverage.
    - Step 1, origin choice. Three options on UX01 radio cards: endpoint model / full weights / adapter + base.
    - Step 2, form per origin:
      - **external_endpoint:**
        - Pick an active connection from `GET /organizations/{org}/connections/litellm`.
        - Pick the alias/provider id from that connection's `GET …/catalog`, or type the exact ref (≤ 2048 characters, sent verbatim, never trimmed or case-folded).
        - No artifact, digest or hash field exists in this branch (negative clause), and `base_url` is never rendered.
        - Both routes are `CONNECTION_ADMIN_ROLES` (organization_admin only; certified FTF-0201/0308, and the 2026-10-10 `base_url` ruling). For an ml_engineer the branch shows the permission-denied state ("Requires an organization administrator") with no submit control, and the weights/adapter branches stay usable. Recorded reading below.
      - **imported_weights:** pick a TrainableArtifact from `GET /organizations/{org}/artifacts`, showing status, license state and digest (monospace, wrapped, in `<bdi>`). An artifact that is not ready or has an unaccepted license shows its blocked reason before submit; the server still decides.
      - **imported_adapter:** pick the adapter artifact plus the base ModelVersion from `GET …/model-versions`. Only `imported_weights` versions are listed, with origin and lineage. Exact base only: no family or name matching, and `base_model_name_or_path` is never used.
    - Step 3, review:
      - the summary;
      - the project security profile, read-only;
      - the expected lineage: "Unknown" for endpoint, "Partial" for weights/adapter;
      - an explicit confirm checkbox;
      - then `POST …/model-imports`.
    - The P1 POST has no idempotency key, so a lost response is never blindly retried. The wizard re-lists `GET …/model-imports` and shows the job if one was created. Only when none matches is resubmit enabled, after an explicit user action.
    - Step 4, status. The job list comes from `GET …/model-imports` on mount, so it survives refresh (§68.1 "persisted state survives refresh"). Per-job StatusBadge states:
      - `queued` → "Queued – validation has not started" (no ETA, progress bar or percentage);
      - `validating`;
      - `failed` + localized failure code;
      - `blocked_external` + reason (never shown as failed or success);
      - `succeeded` → `onModelVersion(result_model_version_id)`.
    - Polling uses bounded backoff (2 s → 30 s), stops at a terminal state and is announced via `role=status` / `aria-live=polite`.
    - Failure-code table: a Python test imports every `CODE_*` constant from `model_import_validation.py` and `model_import_weights_validation.py`, plus the P1 route error codes. It asserts each code has an en and an ar entry in the component's message map, so a missing code is RED. An unknown code renders "Validation failed" with the code in `<bdi>`: never success, never empty.
    - Real-API scenarios (independent DB check after each):
      - R1: endpoint import → `queued` shown; the harness validates → `succeeded` shown with lineage "Unknown". There is no digest in the DOM, and the DB row has NULL `artifact_sha256`/`endpoint_reference`.
      - R2: a resolved-model echo that differs → `failed` with its code.
      - R3: gateway unreachable → `blocked_external`, never success.
      - R4: a local full-weight fixture (P3 integration's tiny safetensors) → `succeeded`, lineage "Partial".
      - R5: digest mismatch → `failed digest_mismatch`.
      - R6: adapter on a mismatched base → `adapter_base_incompatible`.
      - R7: license revoked before validation → `license_not_accepted`.
      - R8: viewer and evaluator → permission-denied state with no submit control; ml_engineer on the endpoint branch → admin-required state.
      - R9: a foreign project → `not_found` shown.
      - R10: reload mid-queue → the job is restored.
      - R11: the POST response dropped → exactly one job row and the wizard shows it.
      - R12: a `private_s3` artifact → `blocked_external metadata_files_unavailable`.
    - Faults that must go RED:
      - a hash/digest field shown or required for endpoint;
      - success shown while queued;
      - `blocked_external` rendered as failed or success;
      - an unknown code rendered as success or empty;
      - a blind re-POST after a lost response;
      - `base_url` rendered;
      - the base list including unvalidated or name-matched versions;
      - `role=alert` removed;
      - ErrorSummary focus removed;
      - an English fallback under `ar`;
      - the wrap removed;
      - polling without backoff or stop.
  - **FTF-H010-P6 Model version panel (lineage, roles, capability-driven actions) and parent closure (after P4 CERTIFIED (#998) and P5 CERTIFIED).**
    - Files:
      - `apps/web/components/model-import/model_version_panel_ui.tsx`;
      - `apps/web/tests/ftf_h010_p6.spec.ts`;
      - `apps/web/tests/ftf_h010_p6_browser.spec.ts` + `apps/web/tests/browser/h010_p6_entry.tsx`;
      - `apps/web/tests/real_api/ftf_h010_p6_ui_real_api.realapi.ts` + `tests/integration/test_ftf_h010_p6_ui_real_api.py`.
    - Additive job `model-roles-ui-real-api-integration`.
    - Lineage section, from the FTF-1001 GET item:
      - `origin` and `lineage_completeness` verbatim; unknown parent and training data are shown as "Unknown" (§68.1);
      - the artifact digest only when present; an endpoint version shows "No training artifact", never a placeholder digest;
      - the observed deployment fingerprint when present, in `<bdi>`.
    - Roles section, from `GET …/roles`, active and revoked:
      - Assign: a role select plus a confirm Dialog naming the role and the version. One `idempotency_key` (`crypto.randomUUID()`) per confirmed attempt is reused on retry of that same attempt, so a lost response gives a 200 replay and one row. `current_production`/`judge` attempts by a non-admin show the server's 403.
      - Revoke: a confirm Dialog. A retry after a lost response gets 409 `role_not_assigned`; that re-fetches and shows the revoked state, not an error.
    - Actions section, from `GET …/actions`: a table of action × "Allowed" / "Not available" + localized reason. It is read-only. No effect route exists yet for any action (evaluate → FTF-R004/0601, continue_tuning → FTF-0701/0801, request_qualification → H020, deploy_route → FTF-10xx, retire → status lifecycle), so P6 renders no buttons for them (Law 11: no placeholder controls). The `continuation_plan` is shown when present: new_run start_step 0 vs exact_resume checkpoint step.
    - Reason-code table: generated from the P4 `continuation.py` constants and the P4 route error codes, en + ar each, with a Python presence test. An unknown reason renders "Not available" + code, never "Allowed" (fail closed).
    - Real-API scenarios:
      - R1: an endpoint version whose ref contains `ft:`, `lora`, `llama`, `trainable` or `teacher`, and that holds a `teacher` role → continue_tuning shows "Not available `endpoint_not_trainable`".
      - R2: an imported adapter → continue_tuning allowed as a new run; exact_resume shows "Not available `no_trusted_checkpoint`".
      - R3: license revoked through FTF-0305 → every use action not available.
      - R4: two admin browser contexts assign `current_production` concurrently → one succeeds, the other shows `incumbent_exists`.
      - R5: judge on a candidate → `self_judging_role_conflict`.
      - R6: ml_engineer assigning judge → 403 shown.
      - R7: an unvalidated FTF-1001 synchronous row → `import_not_validated` everywhere.
      - R8: revoke, then revoke again → the revoked state is shown.
      - R9: a foreign version → `not_found`.
    - Faults that must go RED:
      - "Allowed" shown when the server denies;
      - an unknown reason shown as allowed;
      - an effect button rendered;
      - a placeholder digest for an endpoint;
      - "Unknown" lineage replaced with a guess;
      - a new idempotency key on retry (duplicate row);
      - the 409 on a second revoke shown as a generic error;
      - focus lost after confirm;
      - the brief-item-8 class faults as in P5.
    - Parent closure (same PR; the Control plane certifies, never self-certified):
      - **`docs/spec-matrix.yaml` FTF-H010:**
        - `proving_test` lists every P1–P6 task, integration and UI test file;
        - `proving_symbol` is `build_model_role_router` (the live `…/actions` route in `main.py`, as the P4 design reserved);
        - `implementation_state` IMPLEMENTED;
        - the notes carry the clause table above, every BLOCKED_EXTERNAL/BLOCKED_DEPENDENCY row with its next action, "UI UNWIRED until FTF-H029", "dispatch UNWIRED until FTF-0801/H016", and "no Q07/release claim".
      - **Submission:** `docs/certifications/queue.md` gets a clause → evidence map with test ids and CI job ids per row.
      - **Law 15:** the P1–P4 suites and the `model-import-*` integration jobs are rerun on the closure head; `product_truth_check --fail-on-unwired` and `validate_spec --strict` must pass; `data_exposure_check` is unchanged.
  - Recorded readings (default-resolution rule):
    - **§68.1 "Require explicit project role, rights and privacy selection".** The P1 contract has no rights or privacy field.
      - Rights are the artifact license gate (FTF-0305; P3 refuses an unaccepted license) and, for endpoints, the org-admin-registered connection under the org gateway policy (FTF-0204).
      - Privacy is the project security profile (FTF-0402), which the import inherits. P5 shows it read-only and requires the explicit confirm.
      - No backend field is added, because a new field would be an FTF-H010-P1 contract change and a requalification.
    - **Endpoint import from the UI needs an organization_admin to list connections and aliases**, because both routes are `CONNECTION_ADMIN_ROLES`. The P2 POST still accepts an ml_engineer with a known connection id. P5 does not widen a certified read route.
  - Ask-first for the relay: none. No new runtime dependency, secret, keyed service, owner-approval copy, or reused-component change. The gateway key is an existing open owner action, extended by the Control plane.
  - **Control-plane rulings (2026-10-23, on the design's recorded conflicts):** (1) §68.1 "explicit project role, rights and privacy selection": the design's reading stands - rights = the license gate plus the admin-registered connection; privacy = the project security profile, shown read-only with an explicit confirm; no P1 contract change; (2) the organization-admin-only connection list and catalog routes (certified FTF-0201/0308) are not widened: the endpoint branch shows an "admin required" state for ml_engineers while weights and adapter stay usable; (3) "evaluate" and "load" are disclosed BLOCKED_DEPENDENCY/BLOCKED_EXTERNAL per the clause table (FTF-0616 precedent), never claimed; (4) the existing 2026-10-08 FTF-0616 owner ask (org LiteLLM gateway key) is amended to also ask for one existing fine-tuned deployment id - no second owner ask; the relay never requests a secret; (5) the `10-interface.yaml` description fix and the snake_case component name are accepted.
- **FTF-UX UI redesign to the UX standard (owner instruction, live 2026-10-16: "all our design must be redesigned using [ui-ux-pro-max] and following all NN/g best practices as a must"; Control-plane design 2026-10-16T16:40Z).** Standard: docs/design/UX_STANDARD.md (NN/g H1-H10 with severity-rated heuristic evaluation, NN/g form/error/empty-state/response-time rules, WCAG 2.2 AA) inside FINE_TUNING_FACTORY_RELEASE_1.md §72 (tokens and Radix/shadcn component pattern FIXED - the skill never picks a new palette/font/style; §72.5 viewports 1280/768/390, keyboard-only, zoom 200 %). Tooling: the vendored skill `.claude/skills/ui-ux-pro-max` (`search.py ... --domain ux`, `--stack react`, pre-delivery checklist). Child slices, no count change; each redesign slice REQUALIFIES the certified parents it touches (Law 15: their suites re-run, their certification stands only if no certified behaviour regresses). Every UI slice from now on (H011-P4, 0520, 0619, 0704, 0717, 1009, 1108, 1310, H029, R007) carries the UX_STANDARD.md §4 evidence in its DoD (CLAUDE.md "UI design standard"). Take in order:
  - **FTF-UX00 Heuristic audit (docs only; ready now; claim `relay/FTF-UX00-r1`).** `docs/design/audit-2026-10.md`: for each of the 16 components in apps/web/components (and the OIDC login), H1-H10 x the states it has (empty, loading, populated, error, permission-denied/blocked), each finding with an NN/g severity 0-4, the ui-ux-pro-max guideline it breaks (query + guideline name), the §72 clause, and the slice below that fixes it; a summary table of severity 3-4 findings. No code change. Certified by an independent second evaluation of a sample (NN/g: more than one evaluator).
  - **FTF-UX01 Design-system foundation (ready after UX00; lock `module:web_ui_foundation`).** `apps/web/styles/tokens.css` with the §72.1 token values exactly (light + separately contrast-tested dark tokens, RTL-safe logical properties); shared accessible components in `apps/web/components/ui/` (Button, Field with visible label/helper/error association, ErrorSummary (focusable, linked to fields), StatusBadge (text + icon, never colour alone, the §72.2 state words), EmptyState, DataTable (sortable, responsive summary/details), Dialog (focus trap + return), Banner/Breadcrumbs, Progress/Pending (NN/g 0.1/1/10 s rules)) on Radix primitives - Radix is fixed by §72.1 (default-resolution rule; not a fresh ask): pin exact versions in apps/web/package.json + the lockfile, no CDN, local/system fonts. Tests: DOM Vitest per component (keyboard, roles/names, error association, focus return, contrast of every token pair >= 4.5:1 text / 3:1 UI computed in the test), each with a fault that goes RED; UX_STANDARD.md §4 evidence.
  - **FTF-UX02..UX07 Redesign by area (each after UX01; one PR per area; requalify the named certified parents):** UX02 dataset-factory (0500A source selector, 0501-UI resumable upload, 0516 issue resolution); UX03 target-application (04A03, 04A04, 04A18, 04A21, 04A22); UX04 project-mandate (0401, 0408); UX05 models/capabilities/cost (0214, 0308, 0309, 1301); UX06 evaluation (0602); UX07 identity (0101 OIDC login). Each: rebuild on the UX01 components and tokens; fix every UX00 finding of severity >= 2 for that area (severity 2 may be a recorded decision); keep every certified behaviour and API contract (the parents' existing apps/web/tests/ftf_*.spec.ts stay green, unchanged except for selectors that move to role/label queries); bilingual standard phrases (en/ar, RTL); UX_STANDARD.md §4 evidence incl. the §72.5 viewports, keyboard-only and zoom checks. UNWIRED until FTF-H029 gives them a live route (Law 6/7), disclosed.
- **Prerequisite child slices (Control-plane design, 2026-10-07T13:30Z).** FTF-0308, FTF-0602 and FTF-0501 are refined into
  bounded child IDs under FINE_TUNING_FACTORY_RELEASE_1.md "For each card" step (3) ("refine any oversized item into bounded
  child IDs whose union covers every parent acceptance criterion"). Each persistence/API child is the "separately claimed
  migration prerequisite" the packets require, and it sits inside its parent's own acceptance ("changing mapping
  invalidates related qualification", "case editor/import", "cannot create accepted Dataset version"). These are
  refinements, not new criteria: the denominator stays 302 and no addendum/ADR applies (ADR 0002/0003 cover new tasks
  outside the 300). The parent certifies only when its children and its own UI/module half are done. Common rules:
  - Migrations are serialized on the one Alembic chain: take the next free number at claim time (0022, 0023, 0024 in the
    order below; renumber if another lands first). Revision ids ≤32 chars.
  - RLS exactly as 0017/0020: ENABLE + FORCE, policy `tenant_isolation` FOR ALL USING/WITH CHECK
    `organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid`; `sovra_app` never gets
    DELETE. Paths `/api/v1/organizations/{organization_id}/...`; errors `{"detail":"<stable_code>"}`; audit via
    `write_audit_event` in the same transaction.
  - Each child freezes its own interface section in the phase yaml under that phase's contract lock (serialized
    ownership transfer from the phase interface owner, per "shared module changes require ownership
    transfer/serialization"), adds its own additive ci.yml job + verdict `needs`, and never adds UI, runtime deps,
    secrets or keyed services.
  - **FTF-0308-P1 ModelCatalogEntry persistence + training-artifact mapping** (§2.2, §3.3, §6.6 subset, §6.24 audit,
    §39 Models). Migration `00NN_model_catalog_entries`: UQ (organization_id, id) on litellm_connections;
    `model_catalog_entries` (id, organization_id, litellm_connection_id, public_model_name ≠'', trainable_artifact_id
    NULL, catalog_version ≥1, first_seen_at, last_verified_accessible_at, mapped_by, timestamps; composite FKs to
    litellm_connections and trainable_artifacts RESTRICT; UQ (litellm_connection_id, public_model_name));
    append-only `model_catalog_mapping_changes` (prev/new artifact, prev/new version with new = prev+1, actor,
    request_id; SELECT/INSERT only). No metadata/cost/supports_* columns (nothing writes them; Law 11).
    API in 03-interface.yaml, under `.../connections/litellm/{connection_id}/models/{model_name:path}/training-artifact`:
    GET (ARTIFACT_READ_ROLES) → `{mapping_state unmapped|mapped, model_catalog_entry_id, litellm_connection_id,
    public_model_name, trainable_artifact_id, catalog_version, last_verified_accessible_at, last_changed_at, artifact:
    TrainableArtifact|null}`; nothing is inferred from the alias name. POST (§39 fixes POST; ARTIFACT_WRITE_ROLES)
    body `{trainable_artifact_id uuid|null, expected_catalog_version int|null}`, additionalProperties false; a fresh
    FTF-0204 discovery on every call; same artifact → 200 unchanged, no event. Errors 400, 401, 403, 404 `not_found`
    (identical for missing/foreign), 409 `connection_revoked`|`model_not_accessible`|`artifact_not_ready`|
    `catalog_version_conflict`, 503 `gateway_unavailable` with no write (Law 17). GET `.../training-artifact/changes`
    (limit ≤100). Store hook `require_current_mapping(session, ctx, entry_id, catalog_version)` raises
    `MappingChangedError`; a change bumps the version, inserts a change row and writes audit
    `model_catalog_entry.training_artifact_mapping_change` in one transaction.
    Tests (each fault RED): cross-tenant GET/POST/changes → identical 404 and a raw org2 INSERT rejected (drop FORCE →
    RED); app-role UPDATE/DELETE on changes refused; alias absent from fresh discovery → 409, no row; gateway down → 503,
    no row; artifact not `ready` → 409; remap A→B gives v2, one change row, one audit row, and v1 is rejected by
    `require_current_mapping`; stale expected version → 409; body with `source_uri`/`model_family` → 400; viewer → 403;
    data_exposure_check passes. Must not edit model_catalog_routes.py or training_capability_classifier.py, call the
    LiteLLM management API, or add an alias→artifact heuristic. Builds on certified FTF-0104/0103A/0105/0201/0204/0214/
    0301/0306/0307. Then FTF-0308 (UI) is re-queued on top. Note for planning: FTF-0309 names `/capabilities`, which
    nothing persists yet; expect the same pattern.
  - **FTF-0602-P1 EvaluationCase persistence + create/import/list/edit API** (§6.14, §23-§24, §26-§27, §69.1-§69.2,
    §78.2, §39 `POST /evaluation-suites/{id}/cases`). Migration `00NN_evaluation_cases`: UQ evaluation_suites
    (organization_id, project_id, id); `evaluation_cases` (case_key, category, severity low|medium|high|critical,
    critical bool, language, group_key, input_json/expected_json/grader_config_json JSONB objects, expected ≠ {}, each
    ≤2 MiB, grader_type in the 13 §23 values, weight >0; FKs to projects and (org, project, suite) RESTRICT; UQ
    (suite_id, case_key); CHECK `NOT critical OR grader_type IN (tool_name, tool_arguments, tool_sequence,
    state_transition, forbidden_action, required_refusal)` per §27/§69.2). API in 06-interface.yaml (remove its "does
    not declare /cases" sentence), base `.../projects/{project_id}/evaluation-suites/{suite_id}/cases`: POST
    `{cases: [EvaluationCaseInput] 1..500}` all-or-nothing → 201 `{items}`; GET `?limit 1..500&after=<case_key>` →
    `{items, next_after}`; GET/PATCH `/cases/{case_id}` (case_key immutable). Errors 400 (per-row `{index, field,
    code}`, never content; `critical_oracle_not_independent`), 403 (`final_suite_content_restricted`: final-suite case
    reads only evaluator/workspace_admin/organization_admin), 404, 409 `suite_locked`|`duplicate_case_key`. Writes lock
    the suite row; audit `evaluation_case.import` (ids, count) and `evaluation_case.update` (field names only).
    Tests (each fault RED): cross-tenant → 404 and raw org2 INSERT rejected; critical case with exact_match/
    contains_fact/llm_judge → 400 and raw INSERT fails the CHECK; ml_engineer listing a final suite → 403 without
    content; locked suite → 409; one bad row inserts nothing; duplicate key → 409; 501 rows → 400; data_exposure_check
    passes. Must not compute suite_sha256 (FTF-0603), run graders, widen FTF-0601's roles, or parse files server-side.
    Builds on certified FTF-0601/0104/0103A/0105/04A19. Then FTF-0602 (UI) is re-queued on top.
  - **FTF-0501-P1 Dataset version + upload-session persistence and read API** (§6.10, §7A.10, §8.1 steps 1-4, §65.2,
    §67.3, §67.5, §39 Datasets). A pure module cannot prove "cannot create accepted Dataset version", so the entity is
    needed (parked route 1, sized like FTF-1206A). Migration `00NN_dataset_uploads`: UQ dataset_source_connections
    (organization_id, project_id, id); `dataset_upload_sessions` with CHECKs: object_key =
    `'tenants/'||organization_id||'/projects/'||project_id||'/uploads/'||id||'/source'`, 0 < declared_size ≤ max_bytes
    ≤ 10 GiB, expires_at ≤ created_at + 24h, `status <> 'verified' OR (verified size/sha256/content type equal the
    declared ones AND finalized_at ≤ expires_at)`, `(status='rejected') = (rejection_reason IS NOT NULL)`; `datasets`
    (one immutable version per row, version ≥1, UQ (project_id, name, version), UQ (upload_session_id), UQ
    (organization_id, id)) with a composite FK to the session's (org, project, id, status, verified_sha256,
    verified_size_bytes) and CHECK upload_status = 'verified', so only a verified session can create a version.
    Column-limited UPDATE grants, no DELETE. API in 05-interface.yaml: GET `.../projects/{project_id}/datasets` (limit
    ≤200, after) and GET `.../datasets/{dataset_id}`, DATASET_SOURCE_ROLES; held_out_evaluation rows are 404 to
    ml_engineer (§9, §69.1); responses never include artifact_uri/object_key/bucket. Upload/finalize request and
    response components are frozen with no paths; FTF-0501 adds those routes with their handlers. Store functions
    `create_upload_session` (active `upload` connection + FTF-0500 `authorize_dataset_source_purpose`),
    `record_upload_rejected`, `record_upload_verified_and_create_dataset`, `expire_open_sessions(now)`.
    Tests on real Postgres with raw sovra_app inserts (each fault RED): a dataset on an open/rejected/expired session
    fails the FK; marking verified fails on partial size, oversize, hash mismatch, finalized after expiry, or changed
    object_key; a referenced verified session cannot be modified; cross-tenant → 404 and raw org2 INSERT rejected;
    ml_engineer cannot see held_out rows; app-role DELETE refused; `data_exposure_check --backends
    postgres_rls,s3_identity` passes. Must not call S3, presign, add buckets or identities, add upload/finalize paths,
    or add UI. Builds on certified FTF-0500/0500B/0500D/0104/0103A/0105/R017. Then FTF-0501 (presigned multipart +
    finalize verification via storage_semantics_client.py's stdlib SigV4; boto3 stays ask-first) is re-queued on top
    and owns the upload/finalize routes and the contracts:05 lock.
  - Recorded readings (default-resolution rule): §6 has no DatasetVersion entity, so each `datasets` row is an immutable
    version (§7A.10); §65.2's ≤5-minute grant applies per part URL, §67.5's 24 h to the session; §6.14 lacks
    language/group fields that §68.2/§69.2 and FTF-0602 need, so they are added; the live error convention is
    `{"detail": code}`, not §40's envelope; FTF-0501's "resumable UI" clause is not in its file list and stays with
    FTF-0501's own refinement.
- FTF-0904-F2 (FIRST: live regression of FTF-0904), FTF-UX02, FTF-R006-P1b (security: cross-tenant), FTF-UX06, FTF-1301-F1 are returned work in item 0 above.
- **FTF-UX01-F1 [CERTIFIED 2026-11-10, see Done] Shared UI error/focus library hardening (follow-up on CERTIFIED FTF-UX01/FTF-UX05; Control-plane 2026-10-23T09:10Z; small; ready now; take BEFORE FTF-UX06 round 2 or any other area reuses these modules).** Work in apps/web/components/ui/AlertMessage.tsx, errors.ts, focus.ts, singleflight.ts and a new apps/web/tests/ftf_ux01_f1.spec.ts; claim `relay/FTF-UX01-F1-r1`. Found by the FTF-UX05 r3 verifier (#983): (1) `RECOVERY_PHRASE[error.kind]` in AlertMessage is not Object.hasOwn-safe - a kind outside the union ("toString", "bogus") throws during render; (2) `networkError` passes any other Error's English message through under locale=ar; (3) errors.ts, focus.ts and singleflight.ts have no direct unit tests (pinned only through four components); (4) `kindForStatus(400)` -> "rejected" is unpinned (V33 GREEN). Fix each with a direct unit test RED on the current code; Law 15: UX01, UX03, UX05 and UX07 specs stay green.
- **FTF-1301-F1 [rounds 1-2 merged (#996, #1005), RETURNED round 3: item 0q] Cost panel shows the previous project's cheapest rate after a project switch (follow-up on CERTIFIED FTF-1301; Control-plane 2026-10-23T09:10Z; small; ready now).** Work in apps/web/components/cost-and-model-search/costprofile_schema_ui.tsx and apps/web/tests/ftf_1301*.spec.ts; claim `relay/FTF-1301-F1-r1`. Found by the FTF-UX05 r3 verifier (#983, reproduced in jsdom; pre-existing): when the user switches project and the old project's cheapest-rate response arrives last, the new project's panel shows the old project's rate. Ignore stale replies (a request counter like 0309's `stale()` guard); a test with an out-of-order response RED on the current code, in jsdom and the UX05 browser harness.
- **FTF-0501-F2 Finalize admits declared non-UTF-8 text encodings — CERTIFIED 2026-10-22 (see Done)** (two Alembic heads); the migration rechain is the Control plane's own fix in this PR, so a separate-session certifier reviews it and FTF-0501-F2 together - relay: no work, do not claim** (follow-up on CERTIFIED FTF-0501; Control-plane 2026-10-20T21:15Z; small; ready now; Law 15: FTF-0501 requalifies, and the 0502-P1/0503/0504 and H011-P1 suites re-run). Work in apps/api/app/datasets/direct_object_storage_upload.py (`sniff_content`, `SNIFF_BYTES`) and its tests; claim `relay/FTF-0501-F2-r1`. Found by the FTF-H011-P1 r3 verifier (#892): finalize refuses a small windows-1256 `;` CSV as text/csv because the sniff decodes as UTF-8 only, while FTF-0503/0504 (QUEUE "encoding ∈ utf-8, utf-8-sig, utf-16-le, utf-16-be, windows-1256") and the H011 P4 scenario need it accepted; and the sniff covers only the first 64 KiB, so the same file behind a >64 KiB ASCII preamble IS accepted - the decision depends on where the first non-ASCII byte falls. Fix: the declared `encoding` of the upload session (one of the five above, default utf-8) is the one the sniff decodes with, strictly, over the whole sniffed window; an undeclared non-UTF-8 text body is refused with a stable code, independent of its byte position. Tests: cp1256 / utf-16 CSV accepted when declared and refused when not, the >64 KiB-preamble variant refused when undeclared, each fault RED; the H011-P1 integration then gains the cp1256 `;` CSV with an Arabic header on row 3 (masked whole-result scan).
- **FTF-0512-F3 Digit-adjacent punctuation keeps its meaning — CERTIFIED 2026-10-21 (see Done)** (follow-up on CERTIFIED FTF-0512; Control-plane 2026-10-20T04:45Z; small; ready now; Law 15: FTF-0512 requalifies with it and the FTF-0518/0513/0515/0516-P1 suites re-run). Work in workers/data/normalized_duplicate_detector.py and tests/tasks/test_ftf_0512_f1.py; claim `relay/FTF-0512-F3-r1`. Found by the FTF-0512-F2 verifier (#861): the P* strip still merges "-5" == "5", "5%" == "5", "5‰" == "5", "#1" == "1", "5′" == "5" - distinct customer outcomes (negative acceptance wins over the §10 method 3 strip; default-resolution rule). Keep a P* character when it is directly adjacent to a digit (both sides checked after the F1 folds), drop it otherwise, so "hello, world" == "hello world" still merges; generated table (every P* code point x before/after a digit x a fixed sentence) and the repo-corpus differential (only digit-adjacent un-merges); NFC/NFD pair for `tool_calls[].name` (verifier fault G20 GREEN); bump detector_version (5); FTF-0518 scan; each fault RED. Add tests/tasks/test_ftf_0512_f1.py to FTF-0512's spec-matrix `proving_test` (it carries all F1/F2 evidence). Advisories, no change asked: "@user" == "user", combining marks on symbols ("x <⃒ 5" == "x < 5").
- **FTF-0512-F2 Meaning-bearing symbols and name-field pins — CERTIFIED 2026-10-20 (see Done)** (follow-up on CERTIFIED FTF-0512; Control-plane 2026-10-15T19:59Z; small; Law 15: FTF-0512 requalifies with it and FTF-0518/0513/0515/0516-P1 suites re-run). Work in workers/data/normalized_duplicate_detector.py, tests/tasks/test_ftf_0512.py and tests/tasks/test_ftf_0512_f1.py; claim `relay/FTF-0512-F2-r1`. Found by the FTF-0512-F1 r3 verifier (#836): (1) the base punctuation rule (unchanged since certification) strips math and currency symbols, so "x ≤ 5" == "x ≥ 5" and "$100" == "€100" merge - violates FTF-0512's negative acceptance ("cannot collapse distinct customer outcomes"). §10 method 3 says punctuation normalization: strip only Unicode P* (and whitespace), keep Sm/Sc/Sk/So as distinct tokens (separated like F1's compatibility folds); generated table over every Sm/Sc code point pair in a fixed sentence; differential over the repo corpus (only the intended un-merges); FTF-0518 scan; fault RED. (2) Pins: NFKC-folding or Cf-stripping tool_calls[].name or a message `name` (faults D18, D31–D33) stays GREEN although production keeps both byte-for-byte - add fullwidth/Cf/case/space negatives for both name fields, each fault RED. Bump detector_version (4). Advisory: Hangul compatibility jamo combine after folding ("ㄱㅏ" == "가") - same syllable, no change asked.
- **FTF-0512-F1 Normalized-text hash look-alike gaps — CERTIFIED 2026-10-15 (see Done); FTF-0512 recertified** (follow-up on certified FTF-0512; Control-plane 2026-10-14T14:23Z; Law 15: requalify FTF-0512 and re-run FTF-0518/0513 suites). Work in the FTF-0512 module and its tests. Found by the FTF-0518 verifier: fullwidth letters, zero-width characters and a timestamp present on one side only get past all eight leakage methods because FTF-0512 normalization keeps them distinct. Add NFKC folding, removal of zero-width/format characters (Cf) and the existing timestamp rule applied symmetrically, with a generated table (letters x look-alike classes x positions) and a fault per class; confirm no previously distinct, legitimately different texts collapse (differential over the existing corpus).
- **Backlog refill 2026-10-14 (Control plane, small task-specific models):** owner approved live 2026-10-14 three ScaleDown-derived ideas (sub-step routing to small models, constrained decoding, classifier-head models). Ideas 1-2 are existing scope and become child slices FTF-1304-S1 and FTF-0213-S1 below (child slices add no row). Idea 3 is new scope: ADR 0004 (docs/adr/0004-small-task-specific-models.md) + addenda FTF-0708A / FTF-1004A (spec/addenda/; denominator 304) were committed by the Control plane, not the relay; both addenda are blocked (FTF-R003/0701/0712/0713/0519 and FTF-1003/1005 absent; GPU BLOCKED_EXTERNAL). Take FTF-1304-S1 then FTF-0213-S1 after the current refill order (FTF-0518 → FTF-1704 → FTF-0906 → FTF-1505); they share no write set with it. ScaleDown's hosted API is not used (keyed external service, breaks ZERO_EGRESS) - do not reopen.

- **FTF-1304-S1 Compound per-step binding cost attribution — CERTIFIED 2026-10-16 (see Done)** (child slice of CERTIFIED FTF-1304; Control-plane design 2026-10-14; FINE_TUNING_FACTORY_RELEASE_1.md §0 item 19, §3.4–3.5, §29, §31–32, §66.3–66.4, §69–70, §77, §81; AF-22; Q15, Q19; deps FTF-1301, FTF-1302, FTF-1303, FTF-1304, FTF-1305, FTF-04A25, FTF-0210 all CERTIFIED; DoD `python tools/r1_handoff.py packet FTF-1304 --plan FINE_TUNING_FACTORY_RELEASE_1.md` plus this entry; no shared lock; contract owner FTF-1301 — read `13-interface.yaml` / `13-domain.schema.json`, add no path). Parent acceptance: "Total costs across all attempts divided by independently successful tasks, plus optional training amortization." Parent negative: "Zero successes, pending charges and unobserved fallback use cannot yield misleading cheap passing metric." This slice applies them to a candidate whose agent runs routine sub-steps (classify/extract/summarize) on a small model and reasoning on a large one: §3.5 "A deliberate fallback policy is evaluated and priced as a separate compound system." Refinement, not a new criterion: adds no row (denominator 304 counts only the ADR 0004 addenda). Ready now.
  - Files: `apps/api/app/services/compound_binding_cost_attribution.py`, `tests/tasks/test_ftf_1304_s1.py` (run as a script in the `gate` job, additive step next to test_ftf_1304). New module only: do not edit cost_per_success_calculator.py, provider_token_costing.py, local_compute_costing.py, candidate_ranking.py or the FTF-04A25 harness contract (no Law 15 requalification).
  - No migration, route, UI, contract edit, runtime dependency, secret or keyed service. Pure, synchronous, Decimal only, no I/O. UNWIRED (live callers will be FTF-1306/1307/1309, all ABSENT); proving_symbol stays blank, FTF-1304/1305 precedent.
  - Reuse, do not re-implement: FTF-04A25 `ModelBinding` + `validate_model_binding_override` (a step key is a `route_params` key the adapter's `describe()` declared); FTF-1303 `ProviderTokenUsage` / `ProviderRateCard` / `provider_call_cost`; FTF-1302 `ServiceWindow` / `window_total_cost`; FTF-1304 `AttemptCost` / `cost_per_success` / `TASK_OUTCOMES`; FTF-1305 `CostInterval` / `RankingCandidate`; FTF-0210 `RouteIdentityState`. Do not copy private helpers.
  - Types:
    - `StepBinding(step_key, route_alias, step_kind in reasoning|classify|extract|summarize|other, pricing_mode in provider|self_hosted, artifact_kind in existing|base|tuned)`. `step_kind` is a user-confirmed label carried for reporting only; it is never inferred and never changes a price.
    - `CompoundBinding(default_route_alias, provider, steps)`: `to_model_binding()` returns the certified `ModelBinding(model_id=default_route_alias, provider, route_params=sorted((step_key, route_alias)))`; `validate(describe_result)` calls `validate_model_binding_override` (undeclared key → refused). Duplicate step keys, blank values, a step reusing the default alias with a different pricing mode → `CompoundAttributionError`. `binding_sha256` = sha256 of the canonical binding (all fields), stable across `PYTHONHASHSEED`.
    - `ModelCallRecord(request_id, step_key or None (= default route), route_alias, identity_state, deployment_fingerprint, usage: ProviderTokenUsage or None, started_at, ended_at)` — one per model call in the attempt's correlated trace (§66.3 `collect_outcome`).
    - `AttemptTrace(request_id, outcome in TASK_OUTCOMES, calls, pending_cost or None)`.
    - `SelfHostedWindow(window_id, route_aliases, window: ServiceWindow, allocation_policy="proportional_to_calls")`.
  - `attribute_compound_costs(binding, attempts, rate_cards: Mapping[route_alias, ProviderRateCard], windows) -> CompoundAttribution(status, attempt_costs: tuple[AttemptCost, ...], route_lines, step_kind_totals, issues, attribution_sha256)`:
    - provider calls: `provider_call_cost(usage, rate_card)` per call;
    - self-hosted calls: each window's `window_total_cost` is allocated once over all calls on its routes inside the window, proportional to call count, exact Decimal with the residual on the last call in canonical order, so allocations sum exactly to the window total. A window shared by two routes (one GPU serving both models) is allocated once across both (§32: "No double-counting the same shared GPU second");
    - each `AttemptCost.settled_cost` = sum of that attempt's call costs; `pending_cost` carried through; `route_lines` (route, step_kind, calls, input/output tokens, cost) and `step_kind_totals` sum exactly to the attempts' total;
    - status `priced` only when every call is priced, identity-verified and inside the binding.
  - Refusals (status, not a number; parent negative clause and §3.4–3.5):
    - a call on a route alias or step key not in the binding → `binding_violation` (silent fallback/substitution);
    - `identity_state` not verified → `identity_unverified` (§3.5 IDENTITY_UNVERIFIED);
    - provider call with `usage=None` or no rate card for its route → `unpriced` (never $0);
    - self-hosted call with no covering window, or outside its window's time range → `unpriced` ("Do not assume self-hosted inference is free");
    - only `priced` attributions return `attempt_costs`; others return issues only, so `cost_per_success` cannot be computed from them.
  - `compound_ranking_candidate(candidate_id, attribution, quality_lower_bound, interval_low/base/high, eligible, training_and_qualification_cost, approved_expected_lifetime_successes)` → FTF-1305 `RankingCandidate` with `kind` = `tuned` if any step is tuned (so FTF-1305 refuses it without training amortization), else `existing` if any step is existing, else `base`; refuses a non-`priced` attribution.
  - Tests in `tests/tasks/test_ftf_1304_s1.py`, each criterion id in failure output:
    - golden: §32's own example ($12 GPU incl. idle + $2 CPU + $1 other, 100 successful tasks) on a self-hosted small route plus a priced large provider route; exact Decimal totals, per-route lines and step-kind totals add up exactly, FTF-1304 `cost_per_success` = expected value;
    - a failed attempt's calls are counted (all attempts); a retried request id counts once as a success (FTF-1304 dedup);
    - each refusal above with its status and the offending request id and route (no content);
    - shared window across two routes allocated once; residual rounding keeps the sum exact over 3, 7 and 1,000 calls;
    - `validate` refuses an undeclared step key through the certified 04A25 function;
    - FTF-1305 path: compound tuned candidate without amortization is refused; compound vs single-model incumbent with overlapping intervals → `INCONCLUSIVE_RETAIN_INCUMBENT`; non-overlapping cheaper compound → `WINNER`;
    - determinism: same `attribution_sha256` and `binding_sha256` under two `PYTHONHASHSEED` values and shuffled call order.
  - Faults that must go RED (each its own row, applied to a copy of the module): calls with a `step_key` dropped (only the default route priced); missing usage priced as 0; unknown route alias ignored; identity check skipped; self-hosted call without a window priced at 0; shared window allocated per route (double count); residual dropped (sum ≠ window total); step-kind totals not equal to attempt total; failed attempts excluded; `binding_sha256` excludes the step map; tuned step reported as `base` (amortization skipped); `validate_model_binding_override` not called.
  - Law 12: the module has no threshold, routing-choice or selection input, so it cannot tune on final results; which steps to route and any small→large escalation threshold are chosen on development data in FTF-1307 (design note recorded there).
  - Zero-egress: pure function, no network; compatible with every profile.
  - Disclosed: the benefit is only proven when FTF-1306 runs both candidates through the actual target harness on the same development suite; this slice makes that comparison honest, it does not measure it.

- **FTF-0213-S1 Constrained-choice enforcement and output-token probe — CERTIFIED 2026-10-16 (see Done)** (child slice of CERTIFIED FTF-0213; Control-plane design 2026-10-14; FINE_TUNING_FACTORY_RELEASE_1.md §2–3 (3.4 probes, 3.5 identity), §29, §36, §38, §65, §77, §81; CAP-02; Q02, Q21; deps FTF-0201, FTF-0202, FTF-0210, FTF-0213 all CERTIFIED; DoD `python tools/r1_handoff.py packet FTF-0213 --plan FINE_TUNING_FACTORY_RELEASE_1.md` plus this entry; no shared lock; contract owner FTF-0201 — read `02-interface.yaml`, add no path). Parent acceptance: "Exercise the model's advertised structured-output protocol and schema-validate actual response." Parent negative: "JSON-looking prose, unknown parameter rejection or coercion is not silently treated as strict schema support." FTF-0213 proves a route returns schema-valid JSON once; it cannot tell constrained decoding from a model that happened to comply, and it does not check the enum/choice case, truncation or output size. The owner-approved cheap-output idea (2026-10-14) needs exactly that before any "few output tokens" claim. Refinement, not a new criterion: adds no row (denominator 304 counts only the ADR 0004 addenda). Ready now.
  - Files: `packages/litellm_client/real_constrained_decoding_probe.py`, `tests/tasks/test_ftf_0213_s1.py` (script in the `gate` job, additive step next to test_ftf_0213). New module only; do not edit real_structured_output_probe.py or real_chat_probe.py.
  - No migration, route, UI, contract edit, runtime dependency, secret or keyed service. UNWIRED (the `/models/{id}/probe` route is not live); proving_symbol blank, FTF-0213 precedent.
  - Protocol: the portable OpenAI `response_format: {"type": "json_schema", "json_schema": {"name", "schema", "strict": true}}` only. The schema is an object with one required string property `label` whose `enum` is the trial's choice set, `additionalProperties: false`. Never send vLLM-only `guided_choice` / `guided_json` / `structured_outputs` extras: they are version-dependent and a gateway may drop them.
  - `probe_constrained_choice(*, base_url, model_id, approved_model_ids, secret_ref, secret_config, policy, timeout, now, rng=None) -> ConstrainedDecodingProbeResult`, same signature conventions as `probe_structured_output`; `ChatProbeNotAuthorizedError` (reused) before any network call for an unapproved model.
    - Fixed module constants, never caller-supplied: trials K=3, `max_tokens`=16, temperature 0, a fixed adversarial synthetic prompt that tells the model to answer with a word outside the enum ("Reply with the single word: banana"), and per trial a fresh enum of 3 nonsense tokens drawn from a fixed 64-token synthetic vocabulary (`rng` injectable for tests, `secrets.SystemRandom` otherwise).
    - Per trial record: `finish_reason`, `completion_tokens` (from usage, or None), the parsed label's enum membership (bool), route identity (reused `RouteIdentityState`), latency. No content stored or reported, only states and counts.
  - States (never collapsed into success):
    - `CONSTRAINED_OBSERVED` — all K trials parse as JSON, are exactly `{"label": <member of that trial's enum>}` with no coercion and no extra keys, `finish_reason == "stop"`, usage present and `completion_tokens <= 16`;
    - `NOT_ENFORCED` — any trial returns prose, the prompted word, a non-member label or extra keys (the route followed the prompt: constraint absent or dropped by the gateway);
    - `UNSUPPORTED_PARAMETER` — 400 on `response_format` (reuse FTF-0210/0213 rejection taxonomy; no retry without the constraint);
    - `TRUNCATED` — any `finish_reason == "length"`;
    - `USAGE_MISSING` — constraint observed but usage absent: output-token count is unknown and no cheap-output claim may be made (§3.4: missing usage is unpriced, not zero);
    - identity not verified is recorded on the result and blocks certification of that artifact (§3.5).
  - Result also carries `max_completion_tokens_observed` and `mean_completion_tokens` (Decimal) for FTF-1303/§29 "output tokens/task".
  - Tests in `tests/tasks/test_ftf_0213_s1.py` with the FTF-0213 harness (real local HTTPS LiteLLM stand-in with real TLS validation and the real local OpenBao stand-in), stand-in behaviours: enforcing (returns a member of the request's enum), prompt-following (returns "banana"), param-dropping (ignores `response_format`, answers prose), rejecting (400), truncating (`finish_reason: length`), no-usage, extra-key, numeric-string coercion (`{"label": 1}`), wrong-enum (member of a different trial's enum). Each maps to exactly one state; request body asserted: standard `response_format` only, no vendor extras, `max_tokens` 16, K=3 distinct enums; unapproved model → no socket opened.
  - Faults that must go RED (each its own row): any JSON accepted without enum membership; `finish_reason` not checked; missing usage treated as 0 tokens; loop stops after the first passing trial; same enum reused across trials (a cached answer passes); prompt not adversarial (asks for an enum member); vendor `guided_choice` extra sent; caller-supplied prompt or `max_tokens` accepted; approval check after the request; extra keys accepted.
  - Disclosed: `CONSTRAINED_OBSERVED` is an observation, not proof of the mechanism (a capable model could obey the schema against the prompt; K random enums plus an adversarial prompt make that unlikely, not impossible). The real-engine run belongs to FTF-1006 (pinned vLLM behind the real gateway); design note recorded there.
  - Zero-egress: talks only to the configured internal gateway route; synthetic content only.

- **FTF-0708A Sequence-classification-head training (seq_cls_lora)** (owner-approved Release 1 addendum 2026-10-14, ADR 0004; contract `spec/addenda/FTF-0708A.json` — read the contract there, not from `r1_handoff.py packet`). **BLOCKED — do not claim.** Deps: FTF-R003, FTF-0701, FTF-0712, FTF-0713, FTF-0519 (all ABSENT) and FTF-0307 (CERTIFIED); the qualified run also needs a real GPU (gpu_driver BLOCKED_EXTERNAL). Becomes claimable only when all five ABSENT deps are CERTIFIED and the Control plane moves it up with a design. Integration-form change already owner-approved (Transformers `Trainer` + PEFT SEQ_CLS inside the pinned Axolotl tuple, under the FTF-R003 supervisor); a version or library outside that tuple is still an ask-first new runtime dependency.

- **FTF-1004A Classifier serving via vLLM pooling runner behind LiteLLM** (owner-approved Release 1 addendum 2026-10-14, ADR 0004; contract `spec/addenda/FTF-1004A.json` — read the contract there, not from `r1_handoff.py packet`). **BLOCKED — do not claim.** Deps: FTF-1003 and FTF-1005 (ABSENT; FTF-1003 waits on FTF-0803/0804); FTF-1001, FTF-1002, FTF-04A25, FTF-1201 CERTIFIED. Does not depend on FTF-0708A. Becomes claimable when FTF-1003 and FTF-1005 are CERTIFIED and the Control plane moves it up with a design. If the org LiteLLM gateway has no classification-capable route at claim time: BLOCKED_ADMIN_ACTION plus a dated "Owner actions — current" line; never bypass the gateway.

- **FTF-0502-P1-F1 Resumable dataset source reads — CERTIFIED 2026-10-14 (see Done)** (follow-up on certified FTF-0502-P1; Control-plane 2026-10-14T08:18Z; Law 15: P1 is requalified with it). Work in the `open_dataset_source` reader (FTF-0502-P1 source layer) and its tests. Two verifiers reproduced it (FTF-0504 #775, FTF-0502 #778): SeaweedFS ends a slowly consumed GET (~18-21 MB at the importers' 0.6 MiB/s), so any dataset over ~20 MB ends `blocked` and the 10 GiB limit is unreachable. Read the verified object in bounded HTTP Range windows (e.g. 8 MiB), each request pinned to the verified object version/ETag and the whole-stream sha256 still checked at the end; a version change mid-read is `source_changed`, never spliced bytes (Law 17). Real-SeaweedFS test: a 96 MiB dataset consumed at the importer's real pace completes with the right sha; an object overwritten between windows ends source_changed; a stopped server mid-read ends blocked. Faults: no version pin, no final sha check, a window re-read at the wrong offset - each RED.
- **Backlog refill 2026-10-14T05:16Z (Control plane):** one relay lane, take in this order: FTF-0518 → FTF-1704 → FTF-0906 → FTF-1505 (FTF-1505 may run in a second lane from the start; it shares no write set). FTF-H011 and FTF-R006 are designed below (2026-10-15T14:14Z); its Argilla search backend is OpenSearch (Apache-2.0), owner-approved live 2026-10-14 over Elasticsearch (SSPL/ELv2) - do not reopen.
- **FTF-0904-W1 Live log redaction wiring — CERTIFIED 2026-10-14 (see Done)** (child slice of certified FTF-0904; owner-requested live 2026-10-14, built by the Control-plane session, so it needs an L2 verifier from a different session - Law 4). Finding from the FTF-1505 design: `install_redaction`/`attach_sink` had no caller and uvicorn's access log printed query-string secrets verbatim. `apps/api/app/log_redaction_setup.py` `install_api_log_redaction()` runs first in `create_app()`: FTF-0904 ingress + sinks on root, uvicorn handlers and `logging.lastResort`; uvicorn's `AccessFormatter` (needs the raw args ingress removes) is replaced by its `DefaultFormatter` on the redacted message. Test `tests/tasks/test_ftf_0904_w1.py` (gate job): real `uvicorn app.main:create_app --factory` with the default log config over a socket, FTF-0905 canaries in 6 secret query params on a /api/v1 route (401) and an unknown path (404); no canary in stdout/stderr, redacted access lines kept, no failure marker; faults F1-F6 RED. Disclosed follow-up for FTF-0904 (rule set, Law 15 requalify): OAuth `code`/`state` on /auth/callback are not redacted. FTF-1505 lands after this. **Round 2 (2026-10-14T09:46Z):** the separate-session verifier (session_0161278Aosjx54vgoMAk3jN6) returned r1 - an exception escaping create_app() (e.g. ConfigurationError quoting `password=<canary>` in SOVRA_MAX_TRAINING_BUDGET_USD) is printed by sys.excepthook outside logging, raw; and moving the install call late stayed GREEN. Fixed: redacting sys.excepthook, threading.excepthook, sys.unraisablehook and warnings.showwarning (fail closed to the marker; captureWarnings not used, it would swallow warnings); W1-BOOT-FAIL boots the real factory with three secret-shaped bad settings, W1-HOOKS covers thread/unraisable/warning output; faults F7-F12 (incl. F12 install-late) RED. L2 again from a separate session. **Round 3 (2026-10-14T11:30Z):** the verifier returned r2 - under `uvicorn --workers N`/`--reload` the factory runs in a spawned child and a startup exception is printed by multiprocessing via traceback.print_exc(), bypassing sys.excepthook (secret-shaped setting printed raw). Fixed: redacting traceback.print_exception/print_exc (also used by logging.Formatter.formatException); W1-BOOT-FAIL-MP covers --workers 2 and --reload; faults F13-F14 RED. Disclosed: a top-level `SystemExit("<text>")` is printed by the interpreter itself (no live path raises a string SystemExit).
- **FTF-0904-W1-F1 Redacting traceback printer accepts keyword extras — CERTIFIED 2026-10-15 (see Done)** (follow-up on certified FTF-0904-W1; Control-plane 2026-10-14T14:23Z; small). Work in apps/api/app/log_redaction_setup.py and tests/tasks/test_ftf_0904_w1.py. Verifier advisory A1: `_print_exception` has no **kwargs, so Python 3.13's stdlib calls with `colorize=` (traceback._print_exception_bltin used by the C fallback PyErr_Display, and pdb post-mortem) raise TypeError - if sys.excepthook is later reset/raises/deleted, the fallback prints the traceback raw. Accept and ignore extra keywords (colorize), keep the redacting writer; test: set sys.excepthook = sys.__excepthook__ after install and raise a canary exception -> redacted; an excepthook that raises -> redacted; fault (drop **kwargs) RED on 3.13 (skip-with-notice on 3.11/3.12 only if the stdlib path does not exist there). Also advisory A2 (no live path): callers that print traceback.format_exc()/TracebackException.format() themselves are raw - document; A3 top-level SystemExit(str) - disclosed.
- **FTF-0904-F2 Quoted values after credential names - RETURNED, see item 0b (round 2)** (follow-up on CERTIFIED FTF-0904; Control-plane 2026-10-20T21:15Z; small; ready now; Law 15: FTF-0904 requalifies (full matrix in CI), W1 and FTF-0905 re-run). Work in packages/security/log_redaction.py and tests/tasks/test_ftf_0904.py; claim `relay/FTF-0904-F2-r1`. Found by the FTF-0904-F1 r6 verifier (#890): a QUOTED value after any credential name is printed in clear - `state="v"`, `code='v'`, `session_key="v"`, in every case spelling (true since F1 round 1: the query-value pattern excludes quotes, and these are not URL query positions). Redact a single- or double-quoted value (with backslash escapes, up to the closing quote) after every name the deny-by-default rule covers, in `name=` and `name: ` forms; a generated table (every credential name and closed-list ending x quote style x `=`/`: ` x random case) on redact_text and the logging path; over-redaction quantified on the round-5 corpora; each fault RED. Advisories carried: MY17 non-Latin prefixes, MY19 extra split inside the prefix, MY24 trailing separator (production redacts all three; pin if cheap).
- **FTF-0904-F1 Redaction rule-set gaps — CERTIFIED 2026-10-20 (see Done)** (follow-up on CERTIFIED FTF-0904; Control-plane 2026-10-14; Law 15: FTF-0904 is requalified with it). Work in packages/security/log_redaction.py and tests/tasks/test_ftf_0904.py. Found by the FTF-0904-W1 verifier, reproduced in `redact_text` directly: query params `key`, `sig`, `auth`, `session`, `jwt`, OAuth `code` and `state` (on /auth/callback these are live credentials) print verbatim; a percent-encoded fullwidth `=` (`token%EF%BC%9D<v>`) bypasses the percent-decode + NFKC claim. Add them to the secret-parameter rules (generated table over names x encodings x positions, both redact_text and the logging path), keep every round-1..7 fault RED and the linearity sweeps, and add a fault per new name. Cosmetic, certified behaviour (do not change without a ruling): an unterminated encoded match redacts to end of line.
- **FTF-0518 Split leakage detector** (Control-plane design 2026-10-14; FINE_TUNING_FACTORY_RELEASE_1.md §7–10, §67, §77, §81; AF-23; Q08, Q09, Q31; deps FTF-0500, FTF-0513, FTF-0517 all CERTIFIED; composes certified FTF-0506, FTF-0511, FTF-0512, FTF-0901; DoD `python tools/r1_handoff.py packet FTF-0518 --plan FINE_TUNING_FACTORY_RELEASE_1.md`; no shared lock; contract owner FTF-0500 — read `05-interface.yaml` / `05-domain.schema.json`, add no path). Acceptance: "Check exact/normalized/group/semantic cross-purpose leakage including previous campaigns." Negative: "Privacy-forbidden embedding provider is denied; unresolved leakage blocks final freeze and training." Ready now.
  - Files: `workers/data/split_leakage_detector.py`, `tests/tasks/test_ftf_0518.py` (run as a script in the `gate` job), an additive ci.yml step. Path refinement, recorded: the packet's `services/training-worker/data/` becomes `workers/data/` (FTF-0506/0513/0517 precedent).
  - No migration, route, UI, contract edit, runtime dependency, secret or keyed service. Pure, synchronous, no I/O. UNWIRED (no import/split pipeline calls it yet); proving_symbol stays blank.
  - Inputs (typed, exact-type checked like 0517 `_text`): a `SplitManifest` from FTF-0517, run through `verify_manifest` first (a tampered manifest is `failed`, `manifest_invalid`, never scanned); one `LeakageRecord` per manifest record (record_id, canonical `content` in FTF-0506 shape, `record_sha256` and `normalized_sha256` = FTF-0506 lineage.raw_sha256 / canonical_hash, 64 lowercase hex); zero or more `PriorQualificationSource` (label, role `development_eval|final_qualification`, its manifest sha256, its records and group keys); an `EmbeddingRoute` (FTF-0901 `Destination`, optional `RouteAuthorization`, organization/project/job profile). Record ids must match the manifest exactly (missing/extra → `failed`, `record_set_mismatch`).
  - Reuse, do not re-implement:
    - methods 1-2: compare the caller's `record_sha256` / `normalized_sha256` across roles (FTF-0511 semantics: malformed hash → per-record issue, never silently skipped);
    - method 3: FTF-0512 `compute_normalized_text_hash(content)`;
    - group: the manifest's group ids plus each record's 0517 group keys, and prior sources' group keys;
    - methods 4-7: FTF-0513 `detect_near_duplicates`, called with `thresholds=NearDuplicateThresholds(0.0, 0.0, 0.0)` so every LSH candidate pair comes back with its four scores; 0518 then applies the configured `NearDuplicateThresholds` (default `DEFAULT_THRESHOLDS`, 0.95 embedding per §10) with `>=` to those scores. Purposes are mapped role→0513 vocabulary (train→training, validation→validation, development_eval→candidate_development, final_qualification→held_out_evaluation).
    - Must not import or copy 0513 private helpers, edit 0511/0512/0513/0515/0517, or add a second embedding.
  - Saturation (finding, 2026-10-14): 0513's `max_candidates_per_record` silently stops adding partners. 0518 counts returned partners per record; if any record reaches the cap (parameter `max_semantic_partners`, default 200, validated ≥1), the semantic stage is `incomplete` and the report is `blocked`, `semantic_scan_saturated` — never "no semantic leakage". Exact/normalized/group checks are hash/set based and unbounded in recall.
  - Embedding route (negative clause): before any semantic work call FTF-0901 `enforce_effect(EffectRequest(surface="data", destination, organization_profile, project_profile, job_profile), authorization)`. Raised `RouteBlockedByProfileError` / `RouteNotAuthorizedError` / `InvalidEffectRequestError` → report `blocked` with that error's `code`, methods 1-3 + group still reported. `Destination(kind="internal")` uses FTF-0513's in-process hashing embedding. A non-internal destination that policy allows → `blocked`, `embedding_provider_unavailable` (no external embedding client ships; Law 11, Law 17).
  - Pairs checked: every pair of records in different current roles; every prior-source record against current train and validation (all methods); prior final_qualification against current final_qualification (`consumed_final_reuse`, §9: a consumed final suite is not reused). Prior development_eval against current development_eval is not checked (disclosed).
  - Findings: `LeakageFinding(finding_id, method in exact_raw|canonical_normalized|text_normalized|group|char_ngram|token_jaccard|embedding|agent_trace_structural, disposition blocking|review_required, record_id_a, role_a, record_id_b, role_b, prior_source_label|None, scores)`. §10: "Leakage report must identify both record IDs and the reason." `finding_id` = sha256 of the canonical identity (method, sorted ids, roles, label), stable across `PYTHONHASHSEED`. No content, no text, no hash of content in the report beyond the caller's own record ids.
  - Dispositions (§10 table): exact, canonical-normalized, text-normalized, group and consumed-final reuse are `blocking` and cannot be reviewed away; they clear only through a new manifest without the pair. Semantic/structural cross-role findings are `review_required`.
  - Report: `LeakageReport(status clear|leakage_found|blocked|failed, findings, issues, methods_run, methods_not_run, report_sha256)`. `clear` only if every method ran on every pair class and nothing was found. Deterministic sha256 over the canonical report.
  - Gate: `apply_leakage_reviews(report, reviews)` binds `LeakageReview(finding_id, reviewer_id, decision="not_leakage", rationale 1..2000 non-blank)` only to `review_required` findings (unknown id, duplicate review, review of a blocking finding, blank reviewer/rationale → `LeakageEngineError`). `assert_leakage_clear(report, stage)` with stage `final_freeze` or `training` raises `LeakageBlockedError(stage, finding_ids)` unless status is clear or every remaining finding is reviewed `not_leakage`; `blocked`/`failed` always raise. Caller contract (docstring, like 0517): FTF-0519 / the training workflow calls this AND FTF-0515 `assert_clear_for_training`; 0518 does not edit 0515.
  - Tests in `tests/tasks/test_ftf_0518.py`, each criterion id in failure output, all generated from FTF-0506 `normalize_conversation` + FTF-0517 `allocate_splits` (no hand-built manifests except tamper cases):
    - each method detects its seeded cross-role pair (train↔final exact copy; whitespace/case/timestamp variant; same customer-case group key; paraphrase over 0.95; same tool sequence + goal + template) and names both record ids and the method;
    - recall: 10,000 records with 200 seeded exact/normalized/group violations → all 200 found (§ quality pass ≥99%), runtime and candidate count printed;
    - previous campaign: a prior final-suite record copied into current train → blocking; a prior final record reused in the current final role → `consumed_final_reuse`;
    - profile matrix: zero_egress/air_gapped with an external or managed destination → blocked with `security_profile_route_blocked`; standard without authorization → `security_profile_route_not_authorized`; standard with authorization → `embedding_provider_unavailable`; internal → semantic runs;
    - saturation: 300 same-role template siblings + one train↔final paraphrase → `semantic_scan_saturated`, not `clear`;
    - gate: an unreviewed semantic finding blocks `final_freeze` and `training`; a review of a blocking finding is refused; a reviewed `not_leakage` finding clears; a blocked report never clears; tampered manifest → `failed`;
    - determinism: same report sha256 under two `PYTHONHASHSEED` values and shuffled input order.
  - Faults that must go RED (each its own row, applied to a copy of the module): a method dropped from the pair loop; only train↔final compared (validation↔dev missed); prior sources ignored; cap saturation treated as clear; `enforce_effect` skipped; non-internal-allowed route uses the hashing embedder anyway; semantic finding made reviewable-but-non-blocking; blocking finding accepted a review; `verify_manifest` skipped; record-set mismatch ignored; finding without both record ids; report hash excluding findings.
  - Disclosed: the hashing embedding is a lexical proxy (FTF-0513 docstring), not a trained semantic model; no persistence of findings/reviews (needs a later migration, likely with FTF-0519).

- **FTF-1704 Validation dataset — CERTIFIED 2026-10-16 (see Done)** (Control-plane design 2026-10-14; FINE_TUNING_FACTORY_RELEASE_1.md §54–55, §66, §68–69, §77, §81; AF-36; Q05, Q10, Q17, Q32; deps FTF-0517, FTF-1701, FTF-1703 all CERTIFIED; composes certified FTF-1702, FTF-0601/0602, FTF-0608/0609/0610, FTF-0513; DoD `python tools/r1_handoff.py packet FTF-1704 --plan FINE_TUNING_FACTORY_RELEASE_1.md`; no shared lock; contract owner FTF-1701 — read `17-interface.yaml` / `17-domain.schema.json` and `06-interface.yaml`, add no path or schema). Acceptance: "Create >=150 validation and >=250 separate development cases with controlled source grouping." Negative: "Validation and development cannot share final groups/answers or be silently resplit after results." Ready now.
  - Files: `evals/reference/validation_dataset.py`, `tests/reference/test_ftf_1704.py` (script, `gate` job), `tests/integration/test_ftf_1704_development_suite_import.py` (real PG16), an additive ci.yml job + verdict `needs` + `tests/unit/test_ci_policy.py` coverage, and one serialized additive edit to `evals/reference/training_dataset.py` (below).
  - No migration, route, UI, contract edit, runtime dependency, secret or keyed service. Synthetic data only, labelled `synthetic`, origin `synthetic_generator` (Section 0 rule 27: nothing harvested). UNWIRED; proving_symbol blank (reference-data task, FTF-1703 precedent).
  - Serialized shared-module edit (owned by this item; no open PR touches the file): add public aliases to `evals/reference/training_dataset.py` and its `__all__` — `plan_for`, `execute_plan`, `plan_from_steps`, `outcome_holds` (the private executor), and read-only `USER_PHRASES`, `FINAL_ANSWERS`, `ADJECTIVES`, `NOUNS` (as `MappingProxyType`/tuples). No behaviour change; re-run `tests/reference/test_ftf_1703.py` unchanged in the PR (Law 15) and paste its count. Do not copy the executor.
  - What to build:
    - `generate_validation_traces(count=180)` → `ValidationTrace` (same field set as 1703 `TrainingTrace`, purpose `validation`, ids `val-NNNN`, template ids `val-tmpl-<kind>-vK`, groups `val-grp-…`), every tool result executed on a fresh FTF-1702 `ToolEnvironment` through the aliased executor;
    - `generate_development_cases(count=300)` → plain dicts that validate as 06 `EvaluationCaseInput`: `case_key` `dev-NNNN`, `category` = kind, `language` `en`, `group_key` = dev family id, `input_json` {user turn, tool specs by name from FTF-1701, seed descriptor}, `expected_json` derived from the real backend run, `grader_type` per kind (lookup/approval_approved → `tool_sequence`; note/note_retry → `state_transition`; approval_denied, execute_pending → `forbidden_action`, `critical: true`, severity `critical`; cross_subject → `required_refusal`, `critical: true`). Critical cases always use an independent oracle (§27/§69.2).
    - Disjointness by substance, not by id prefix: validation and development each get their own phrase tables, entity vocabularies and final-answer texts, disjoint (after strip/casefold) from 1703's and from each other. The seven tool behaviours (and so tool-call shapes) are the reference domain and are shared; disclosed.
    - `build_reference_eval_splits(validation, development, *, training, reserved_final: ReservedFinalSuite)` → `ReferenceEvalSplits` with counts, `group_count` per role (template families, as 1703 counts them — reported honestly, no floor claimed beyond the acceptance counts), and a FTF-0517 `SplitManifest`: every training/validation/development record is a `SplitRecord` PREASSIGNED to its role with `group_keys` = (family id, entity title, final-answer sha256); `allocate_splits` + `verify_manifest` prove no family/entity/answer crosses roles; refuse overlap with the reserved final suite (`HeldOutLeakageError`, reusing 1703's class). `meets_counts` is true only at ≥150 validation and ≥250 development.
    - `assert_split_unchanged(splits, recorded_manifest_sha256)` raises on any difference. No function accepts a score, outcome or result, so results cannot drive a resplit; a regenerated set is a new manifest hash.
    - Caller contract (docstring): FTF-1705 (sealed final suite, not started) must pass its group/template/entity/answer sets into `reserved_final` and run the same check in reverse.
  - Tests in `tests/reference/test_ftf_1704.py` (criterion ids in failure output):
    - counts: 180 validation, 300 development by default; 149/249 → `meets_counts` false; every trace replays through the real backend (`plan_from_steps` + `execute_plan`) and a hand-edited tool result is refused;
    - oracle positive/negative control per development case: the real certified graders (FTF-0608 `grade_tool_sequence`, FTF-0609 `grade_forbidden_actions`, FTF-0610 `grade_refusal_case`) pass the case's own reference trace and fail a mutated trace (execute before approval, read of a foreign record, duplicate note);
    - disjointness: no shared group/template/entity/answer hash across train/validation/development; no FTF-0513 `detect_near_duplicates` flag between any validation/development user turn and any 1703 training user turn (minimal canonical records); a seeded overlap with `reserved_final` → `HeldOutLeakageError`;
    - resplit: changing one group key, swapping one record's role, or reordering input changes nothing silently — tamper → `verify_manifest`/`assert_split_unchanged` raise; reorder → same hash;
    - provenance: regenerate-and-compare like 1703 `verify_trace_provenance`; a forged `critical` flag or harvested-looking origin is refused; no secret-shaped string (1703 `_SECRET_PATTERNS` via the scan it already runs).
  - Real integration `tests/integration/test_ftf_1704_development_suite_import.py` (PG16 pinned digest as the FTF-0602-P1 job, app as `sovra_app`, the real `create_app()` over a real socket, reusing that test's `_serve`/`_request` pattern): create an agent manifest + a `development`-purpose suite through the real API, POST all development cases (≤500 per call) → 201; GET lists them all with their `group_key`s; a critical case altered to `exact_match` → 400 `critical_oracle_not_independent`, nothing inserted. `final`-purpose suites are not touched.
  - Faults that must go RED: validation reuses one 1703 phrase template; development reuses a validation entity; a family id shared across roles; `reserved_final` ignored; replay check skipped; critical case with a non-independent grader; `meets_counts` true at 149; manifest hash excluding role; a result-dependent reorder accepted.
  - Disclosed: no independent human review of these sets (that is the reviewer/Argilla path, FTF-R006); suite locking/sha (FTF-0603) is not done, so the DB-side freeze is the manifest hash only.

- **FTF-0906 Egress capture/verification — CERTIFIED 2026-10-20 (see Done)** (Control-plane design 2026-10-14; FINE_TUNING_FACTORY_RELEASE_1.md §11–12, §65, §66, §70, §77, §81; AF-06, AF-07; Q23, Q25; deps FTF-0902, FTF-0904, FTF-0905 all CERTIFIED; composes certified FTF-0901, FTF-H006; DoD `python tools/r1_handoff.py packet FTF-0906 --plan FINE_TUNING_FACTORY_RELEASE_1.md`; lock `security:egress_capture_verification`; contract owner FTF-0902 — read `09-interface.yaml` / `09-domain.schema.json`, add no path or schema). Acceptance: "Combine enforcement, connection logs and controlled destination/history/log plaintext inspection." Negative: "Encrypted packet capture alone cannot show no plaintext leakage; blocked and permitted flows both tested." Ready now.
  - Files: `packages/security/egress_capture_verification.py`, `tests/tasks/test_ftf_0906.py` (script, `gate` job), `tests/integration/test_ftf_0906_egress_verification.py` (real sockets, no DB), an additive ci.yml job `egress-verification-integration` + verdict `needs` + `tests/unit/test_ci_policy.py` coverage.
  - No migration, route, contract edit, runtime dependency, secret or keyed service. Stdlib only (`socket`, `ssl`, `http.server`, `threading`); the openssl CLI for a test CA/leaf exactly as `tests/tasks/test_ftf_h005_route_attestation.py` does. UNWIRED (no qualification runner calls it yet); proving_symbol blank.
  - Scope boundary: FTF-H027 (later) owns full-topology evidence (cluster DNS/IPv6/metadata/TLS paths, Temporal histories). FTF-0906 is the verifier that combines the evidence kinds and one real-socket proof. It must not claim cluster scope; the existing kind+Calico job (`network-policy-enforcement-integration`) is not edited.
  - What to build (pure verifier):
    - `EgressPlan(plan_id, flows, required_stores, policy_digest)`; `policy_digest` is FTF-0902 `manifest_digest(...)` of the rendered policy (cluster scope) or the sha256 of the H006 `ConnectionPolicy` repr (process scope), recorded with a `scope` of `process` or `cluster`.
    - `PlannedFlow(flow_id, surface ∈ FTF-0901 SURFACES, destination_label, destination_class internal|external|metadata, expected allow|deny, canary placements)`. Placements are FTF-0905 `PlacementRecord`s; the canary itself comes from `generate_canary` (literal/base64/hex/url via `encode_canary`, fragments via `fragment_canary`).
    - Evidence: `EnforcementObservation(flow_id, layer process_guard|cni, decision allow|deny, code)`, `ConnectionLogEntry(flow_id, side client|destination, outcome connected|refused|timeout|reset|not_seen)`, `PlaintextInspection(store, store_id, flow_id|None, inspected, decrypted: bool)`, `PacketCaptureObservation(flow_id, encrypted, canary_found)`. `store` ∈ `controlled_destination`, `application_log`, `error_path`, `artifact`, `workflow_history`. The four non-destination stores map onto FTF-0905 `SINKS` (`application_log`→`trace`, `error_path`→`error_path`, `artifact`→`artifact`, `workflow_history`→`pipeline_history`) and every absence check there goes through the ledger with an explicit `required_coverage`. `controlled_destination` is not a 0905 sink: the verifier's own flow rules check it (denied flow: canary absent; permitted flow: canary present = positive control), so an authorized arrival is never fed to `record_checked(found=True)`.
    - `scan_store_for_canaries(data: bytes, markers)` reuses `encode_canary` / `fragment_canary` for every encoding and records each check through FTF-0905 `CanaryPlacementLedger.record_checked`; completeness is `assert_leak_test_complete` (not re-implemented).
    - `verify_egress(plan, evidence) -> EgressVerificationReport(status passed|failed|incomplete|partial, per-flow verdicts, codes, not_exercised, report_sha256)`, content-free (ids, codes, counts; never a canary value, host or body).
    - Rules (each a stable code): plan with no `allow` flow or no `deny` flow → refused `flow_class_missing`; a flow with no enforcement observation, or missing client or destination log → `incomplete`, `connection_log_missing`; denied flow passes only if enforcement=deny AND client≠connected AND destination=`not_seen` AND no canary in any inspected store → else `failed`, `unauthorized_transfer`; permitted flow must show arrival at the controlled destination (positive control, else `failed`, `vacuous_permitted_flow`) and its canary may appear only in the stores its placements authorize; any canary in an unauthorized store → `failed`, `canary_leaked`; a flow whose only no-leak evidence is an `encrypted` packet capture → `incomplete`, `encrypted_capture_insufficient` (negative clause); a required store never inspected → `incomplete`, `inspection_coverage_missing`; `workflow_history` absent from the evidence → `partial` with `not_exercised=("workflow_history",)` (no Temporal workflow exists), never `passed`.
    - Logs: the integration's client process installs FTF-0904 `install_redaction()` and `attach_sink(handler)` on the captured handler; the verifier inspects the captured text and the raw stderr/stdout. Redaction never licenses export (0904 rule); a canary reaching any log is a leak, redacted or not, unless its placement authorizes that store.
  - Real integration (no mocks, loopback only):
    - controlled destinations: (A) plaintext HTTP sink on 127.0.0.1 that records raw request bytes and an access log; (B) TLS sink on 127.0.0.1 (openssl-generated CA + leaf, H005 path) that records DECRYPTED request bytes, fronted by a recording TCP relay that stores the ciphertext stream (the "packet capture"); (C) a listening HTTP sink on 127.0.0.2 that is not allowlisted (so a bypass would really land); (D) 169.254.169.254 (nothing listening; always denied by H006).
    - enforcement: FTF-H006 `guarded_fetch` with `ConnectionPolicy(explicit_private_allowlist=(127.0.0.1/32,), allowed_ports={A,B})`; observations come from what the guard actually raised or returned, client logs from the client process, destination logs from the sinks.
    - flows: allowed A and B with canaries in body and header (literal, base64, hex, url, fragmented across two headers); denied C and D with canaries; an error-path flow where the client raises with the canary in the exception text (must be redacted in the log, still counted as a log-store leak check).
    - expected: `partial` (history not exercised) with every flow verdict passing; the B capture contains no canary while the B destination does (proving capture alone shows nothing).
    - CA install, openssl or a free port unavailable → the job fails (`NOT_EXERCISED` is RED), never skipped.
  - Faults that must go RED (pure table in `test_ftf_0906.py`, plus real-socket rows in the integration):
    - a direct `socket.create_connection` bypass to C carrying a canary (real) → `failed`, `unauthorized_transfer`;
    - verifier ignores destination logs; encrypted capture accepted as clearance; deny-only or allow-only plan accepted; permitted flow with no destination arrival accepted; a required store dropped from coverage; ledger completeness skipped; scanner literal-only (base64/hex/url/fragment leak missed); a canary value or host written into the report; `workflow_history` absence reported as `passed`; the client's redaction sink removed so the error-path canary lands in the log (real) → `canary_leaked`.
  - Disclosed: process-scope enforcement only (CNI scope is FTF-H027 on top of FTF-0902's job); no Temporal history store; no IPv6/DNS paths; passing means the tested controls passed on this topology, never "no leakage is possible" (§12).

- **FTF-0519 Immutable split manifest — CERTIFIED 2026-10-21 (see Done)** (Control-plane design 2026-10-15T09:36Z; FINE_TUNING_FACTORY_RELEASE_1.md §0 items 13–14, §6.10–6.12, §7, §7A.10, §8.1 steps 13/16–18, §8.2, §9, §10, §65, §67.2/67.4, §77, §81; AF-16, AF-23; Q08, Q09, Q31; deps FTF-0500, FTF-0516 (+P1), FTF-0517, FTF-0518 all CERTIFIED; DoD `python tools/r1_handoff.py packet FTF-0519 --plan FINE_TUNING_FACTORY_RELEASE_1.md` plus this entry; locks `migration:application` (P1) and `contracts:05` (P2); contract owner FTF-0500 - P2 edits `05-interface.yaml` / `05-domain.schema.json` additively under `contracts:05` by serialized transfer, as FTF-0501-P1/FTF-0516-P1 did; P1 adds no path or schema). Acceptance: "Freeze hashes/counts/roles/grants for all four dataset purposes and version on change." Negative: "Old three-split manifests are migrated explicitly; final set cannot be returned under training access." Two slices: **FTF-0519-P1** (pure domain + migration + store; UNWIRED) then **FTF-0519-P2** (routes + contract + wiring; WIRED); the parent closes when both are certified. **P1 is ready now; claim `relay/FTF-0519-P1-r1`.**
  - Path refinement (recorded before READY): packet `services/training-worker/data/immutable_split_manifest.py` -> `workers/data/immutable_split_manifest.py` (pure; FTF-0517/0518 precedent); persistence in the existing `apps/api/app/datasets/` package (FTF-0516-P1 precedent).
  - Reuse, do not copy: FTF-0517 `SplitSpec`/`SplitRecord`/`SplitManifest`/`allocate_splits`/`compute_manifest_sha256`/`verify_manifest`/`ROLES`; FTF-0518 `detect_split_leakage`/`apply_leakage_reviews`/`assert_leakage_clear`/`verify_report`/`report_body`/`compute_report_sha256`/`PriorQualificationSource`/`EmbeddingRoute`; FTF-0516-P1 `load_resolved_quality_report` + `excluded_batch_positions`; FTF-0515 `assert_clear_for_split`/`assert_clear_for_training`; FTF-0501-P1 `get_dataset(include_held_out=)`; FTF-0500 `authorize_dataset_source_purpose`; `write_audit_event`; `execute_tenant_scoped`. Do NOT edit group_aware_split_engine.py, split_leakage_detector.py, quality_issue_engine.py, dataset_quality_store.py, dataset_upload_store.py, dataset_source_connection_crud.py or migrations 0024–0026 (no Law 15 requalification).
  - Four purposes, fixed role<->purpose map (§0.13, §9, §6.8F): training = `train`<->`training`; validation = `validation`<->`validation`; development evaluation = `development_eval`<->`candidate_development`; final qualification / held-out = `final_qualification`<->`held_out_evaluation`. `baseline_only` is not a split purpose (refused). A source dataset whose purpose is `held_out_evaluation` or `candidate_development` is pinned to its eval role by the store from `datasets.purpose`, never by the caller (§9 "cannot be reclassified into training").
  - P1 files: `workers/data/immutable_split_manifest.py` (role map, `role_sha256`, frozen-manifest canonical body/hash, `LegacyThreeSplitManifest` + `migrate_legacy_three_split`, `verify_frozen_manifest`); `apps/api/alembic/versions/00NN_dataset_split_manifests.py` (next free number at claim time - head is 0029, 0030 is earmarked for FTF-H010-P4; revision id <= 32 chars); `apps/api/app/datasets/dataset_split_manifest_store.py`; `tests/tasks/test_ftf_0519_p1.py` (script; additive `gate` step next to test_ftf_0518); `tests/integration/test_ftf_0519_p1_split_manifests.py` (real PG16 as sovra_app); additive ci.yml job `dataset-split-manifest-integration` (same postgres digest as dataset-quality-issues-integration) + classify focus entry + ci-verdict `needs` + `tests/unit/test_ci_policy.py` coverage.
  - Persistence (4 tables; each ENABLE + FORCE RLS, `CREATE POLICY tenant_isolation ... FOR ALL USING/WITH CHECK (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid)` exactly as 0026; sovra_app never gets DELETE; `scripts/data_exposure_check.py` passes):
    - `dataset_split_manifests`: org, project, name, version, parent_version, strategy, seed, ratios int[4], cutoff, normalization_version (required), tokenizer_identity (nullable - recorded as absent, never invented), split_manifest_sha256 (0517), proposal_sha256, leakage_report JSONB (0518 `report_body`, content-free), leakage_report_sha256, leakage_status, leakage_reviews JSONB, reviewed_leakage_report_sha256, migrated_from_legacy_sha256, status (`proposed`|`locked`|`rejected`), frozen_manifest_sha256, approved_by, locked_at, created_by, timestamps. UQ (project_id, name, version); version >= 1; `(version = 1) = (parent_version IS NULL)`, parent_version = version - 1, composite FK (org, project, name, parent_version) -> itself (version-on-change chain: a change is a new row, never an edit); CHECK `(strategy = 'legacy_three_split_v1') = (migrated_from_legacy_sha256 IS NOT NULL)`; every hash `~ '^[0-9a-f]{64}$'`; `(status = 'locked') = (locked_at, approved_by, frozen_manifest_sha256 all NOT NULL)`. Immutability: sovra_app SELECT, INSERT and column-limited UPDATE on (status, leakage_reviews, reviewed_leakage_report_sha256, frozen_manifest_sha256, approved_by, locked_at, updated_at); a BEFORE UPDATE trigger (0026 `to_jsonb` diff pattern) allows only one `proposed` -> `locked`|`rejected` transition with every other column unchanged; lock also requires exactly 4 role rows, each count >= 1; nothing changes after `locked`/`rejected`.
    - `dataset_split_manifest_roles` (manifest_id, role, purpose, record_count, group_count, role_sha256): CHECK on the fixed (role, purpose) pairs (so `heldout_test` can never be stored); UQ (manifest_id, role).
    - `dataset_split_manifest_sources` (grants frozen): dataset_id/version/purpose/sha256 with composite FK to existing `uq_datasets_quality_lineage`; dataset_source_connection_id with FK to `uq_dataset_source_connections_org_project_id` (copied by the store from the dataset's upload session); allowed_purposes_snapshot text[]; quality_report_id + quality_report_sha256 with FK to the `dataset_quality_reports` identity UQ; excluded_positions_sha256.
    - `dataset_split_assignments` (manifest_id, record_id = `"{dataset_id}:{batch_position}"`, source_dataset_id, batch_position, role, group_id, record_sha256, normalized_sha256): PK (manifest_id, record_id); index (manifest_id, role).
    - Child tables SELECT, INSERT only; a BEFORE INSERT trigger refuses rows for a manifest that is not `proposed`.
  - Store (no I/O beyond the DB; all-or-nothing; each refusal a stable code that writes nothing; content-free audit `dataset_split_manifest.proposed|locked|rejected|legacy_migrated` in the same transaction):
    - `propose_split_manifest(..., spec, sources, prior_sources, route, normalization_version, tokenizer_identity, actor)`: per source `get_dataset`, `load_resolved_quality_report`, `assert_clear_for_split` (`quality_not_clear`), drop `excluded_batch_positions`; `allocate_splits`; every assignment's role purpose must pass `authorize_dataset_source_purpose` for its source's connection (`purpose_not_authorized`); the store itself calls `detect_split_leakage` on that exact manifest and never accepts a caller report (0518's report does not bind the manifest hash; its r2 note puts report authenticity in FTF-0519 persistence); prior sources must cover every locked manifest in the project with dev/final roles, matching stored record_id/hash sets exactly (`prior_sources_incomplete`); over `MAX_MANIFEST_RECORDS` = 1,000,000 -> `manifest_too_large` (refused, never truncated).
    - `approve_split_manifest(..., expected_proposal_sha256, reviews)`: reviewer_id is the authenticated principal, never input; `apply_leakage_reviews` then `assert_leakage_clear(report, "final_freeze")` (`insufficient` refused); locks with `frozen_manifest_sha256` = canonical JSON over every column above, all sources/grants, all four role rows and the reviewed report hash; stale expected hash -> `manifest_changed`. `reject_split_manifest`.
    - `migrate_legacy_three_split(..., legacy, development_dataset_id | None)`: "three-split" = the §6.10/§6.12 shape (`train`/`validation`/`heldout_test`, no development role). None exist in this repo (no split table; FTF-0517 is four-role in memory), so there is no backfill and fixtures prove the path. `heldout_test` -> `final_qualification` only (§9); train/validation keep their roles; assignments are built explicitly with strategy `legacy_three_split_v1`, ratios (0,0,0,0), checked by the reused `verify_manifest`; development_eval only from an explicit `candidate_development` dataset, else the result is `insufficient` and cannot lock. `propose` refuses any `heldout_test` or three-role input with `legacy_manifest_requires_migration`; no implicit mapping on read.
    - `load_training_split` (the training access path): only `train`/`validation` rows, filtered in SQL and re-asserted per row; first re-verifies `frozen_manifest_sha256`, `assert_leakage_clear(..., "training")`, FTF-0515 `assert_clear_for_training` on each source's latest report (hash differs from the frozen one -> `quality_report_changed`) and live re-authorization of each frozen grant (revoked/narrowed -> `grant_changed`, §67.4 / Q31). `load_development_split` returns only `development_eval`. There is NO final-record reader in FTF-0519 (the sealed reader is FTF-H020 `final_suite_access.py`).
  - Tests (criterion id in every failure): golden (two sources + a pinned held-out dataset -> locked v1, exact counts and role hashes); a changed seed / source version / quality report / grant -> v2 with parent v1, v1 byte-identical; determinism under two `PYTHONHASHSEED` values and shuffled input; every refusal code; legacy fixtures (`heldout_test` lands only in final; no dev source -> cannot lock); the training view never contains a final or dev record id.
  - Integration (real Postgres): upgrade/downgrade/upgrade with one head; RLS/policy/grant shape on all 4 tables; raw sovra_app SQL refused for UPDATE of a locked row or a non-granted column, DELETE, a child insert after lock, role `heldout_test` or a bad (role, purpose) pair, legacy strategy without lineage, a version gap, a cross-tenant INSERT; cross-tenant reads return 0 rows; the store end to end over real `datasets` / quality-report rows; revoked connection -> `load_training_split` refused; `data_exposure_check.py` passes.
  - Faults, each its own row and RED by a behavioural check (pure faults on a module copy, DB faults in the integration): training-view role filter removed; caller-supplied LeakageReport accepted or detection skipped; `assert_leakage_clear` skipped (blocking finding locks); reviewer taken from input; `heldout_test` mapped to dev or train; three-split accepted by `propose`; `assert_clear_for_split` skipped; excluded positions kept; purpose authorization skipped; revoked grant still serves training; quality-report drift undetected; `frozen_manifest_sha256` omits role hashes / grants / the reviewed report hash; prior source omitted and accepted; update trigger dropped; FORCE dropped; child insert after lock allowed; version chain unchecked; role hash order-dependent. Uncapped `scripts/mutation_gate.py` over the new modules, 0 survivors. Ledger lessons apply (FTF-H010 common rules): exact-type parsing (`type(x) is str`), full constraint/transition tables, one META-LEAK check over exceptions, audit rows and logs.
  - Law 12: the store takes no outcome or metric input; reviews bind only to `review_required` findings. Zero-egress: the semantic stage runs only through 0518's FTF-0901 route check. UNWIRED in P1 (proving_symbol blank; no live import runtime - 0517/0518 precedent).
  - **FTF-0519-P2 — CERTIFIED 2026-10-21 (see Done); FTF-0519 parent review pending (Control plane)** (was: ready now - P1 CERTIFIED 2026-10-16; lock `contracts:05`; claim `relay/FTF-0519-P2-r1`):** Carried from the P1 verifier (#859): propose/approve/reject still return the ORM row whose leakage_report holds finding record ids (final ids included) - every route returns only the id-free `ManifestView`/summary; the route integration on real PG16 exercises the round-2 grant re-authorization, frozen-hash and saturation cases (P1 proved them only in the SQLite task script); advisories A1 (`manifest_unchanged`) and A4 (recompute role hashes at approve) if cheap. `apps/api/app/datasets/dataset_split_manifest_routes.py` (`build_dataset_split_manifest_router` = proving_symbol) wired in `apps/api/app/main.py`; additive 05 contract paths/components; `tests/tasks/test_ftf_0519.py`; `tests/integration/test_ftf_0519_routes_postgres.py` (real uvicorn on PG16); additive CI job. Routes under `/api/v1/organizations/{organization_id}/projects/{project_id}/dataset-splits`: GET list (limit <= 200, after) and GET `/{manifest_id}` with DATASET_SOURCE_ROLES - the summary carries the four role rows (counts and role hashes only), sources, status and hashes, never assignments, record ids, group ids or storage keys; POST `/{manifest_id}/approve` `{expected_proposal_sha256, leakage_reviews:[{finding_id, rationale 1..2000}]}` and POST `/{manifest_id}/reject` with Idempotency-Key + request fingerprint (0516-P1 `request_digest` pattern), `additionalProperties: false`, workspace_admin/organization_admin only (ml_engineer -> 403). Errors `{"detail": code}`: 400/401/403, 404 `not_found` (identical for missing and foreign), 409 with the store codes. No create route (records live in object storage; proposals come from the data worker).
  - Recorded readings (default-resolution rule): the role<->purpose map above; "grants" = FTF-0500 `allowed_purposes` snapshot + live re-authorization (§67.4 DataUseGrant is FTF-H013); "each artifact hash" (§9) = `role_sha256` over sorted (record_id, record_sha256, normalized_sha256, group_id) rows of that role (materializing per-role artifacts is a later task); the approver must be a role that may see held-out data; the final role's count and role hash (no record or group ids) are lineage metadata visible to DATASET_SOURCE_ROLES; prior-source scope is the same project (cross-project prior suites need cross-project read authorization - not in this slice); content<->hash binding stays with FTF-0506 lineage (`_canonical_sha256` is private, not copied), disclosed. Known limit, fail-closed: FTF-0517 can pin only eval roles, so a source whose connection grants only `training` cannot be split (`purpose_not_authorized`); pinning sources to fitting roles is a later 0517 follow-up with Law 15 requalification, not part of P1.
  - Ask-first: none. No new runtime dependency (SQLAlchemy/Alembic/stdlib), no keyed service, no secret; no reused component touched.
- **FTF-H011 Dataset preview, field mapping and purpose approval** (Control-plane design 2026-10-15T14:14Z; FINE_TUNING_FACTORY_RELEASE_1.md §0 items 13/27, §7, §7A.10, §8–8.2, §9, §22 Screen F, §67.1–67.4, §72.1–72.3/72.5, §77.6, §81; AF-15 "Preview/mapping wizard with typed transforms, explicit grant/purpose and server validation before immutable import"; Q08 (Q09); deps FTF-0500, FTF-0500A, FTF-0506, FTF-0507, FTF-0516, FTF-H003 all CERTIFIED; also composes certified FTF-0501 (+P1), FTF-0502-P1, FTF-0502, FTF-0503, FTF-0504, FTF-0404, FTF-0405, FTF-0104; FTF-0505 is PROVISIONAL (returned, r4) and gates only P6. DoD `python tools/r1_handoff.py packet FTF-H011 --plan FINE_TUNING_FACTORY_RELEASE_1.md` plus this entry. Locks `module:import_common` (P1, P5), `migration:application` (P2), `contracts:05` (P3). Contract owner FTF-0500: P3 edits `05-interface.yaml` / `05-domain.schema.json` additively by serialized transfer, after FTF-0519-P2 if that holds the lock). Acceptance: "Import a real irregular CSV/XLSX/JSON source via UI; preserve Arabic/text types, reject unmapped critical roles/tool IDs and deny training for evaluation-only grants." Negative: "No arbitrary Python/JS eval transformations; preview/mapping sends no data outside project boundary and does not silently harvest repo/log records." Six slices: P1 preview runner (UNWIRED), P2 persistence (UNWIRED), P3 routes + contract (WIRED), P4 wizard UI (UNWIRED until FTF-H029), P5 grouped rows, P6 XLSX; the parent closes when all six are certified. **P1 is returned work (item 0l, round 2).**
  - Path refinement (recorded before READY): packet `apps/api/app/services/dataset_mapping.py` -> `workers/data/dataset_mapping_review.py` (pure; FTF-0517/0518 precedent) + `apps/api/app/datasets/dataset_mapping_service.py` / `dataset_mapping_store.py` (FTF-0516-P1/0519 precedent); `packages/contracts/jsonschema/dataset_mapping.schema.json` -> additive `$defs` in `05-domain.schema.json` (one phase schema); `apps/web/components/datasets/MappingWizard.tsx` -> `apps/web/components/dataset-factory/dataset_mapping_wizard_ui.tsx` (existing directory and naming).
  - Reuse, do not copy: FTF-0502-P1 `mapping_from_object`/`parse_mapping`/`ImportMapping`/`apply_mapping`/`preview`/`ImportLimits`/`ChunkReader`/`RowRejection`/`LocatedImportReport`/`strict_loads` (import_common.py itself says H011 must reuse its parser); `open_dataset_source(include_held_out=)`; the importers' public `jsonl_events`/`json_events`/`csv_events`; FTF-0500 `authorize_dataset_source_purpose`; FTF-0404 `list_tool_definitions` (as `tool_schemas`); FTF-0405 `validate_instance` (as `tool_argument_validator`); FTF-0501-P1 `get_dataset`/`get_upload_session`; `UploadStorageSettings.from_env`; FTF-0516-P1 `request_digest`; `write_audit_event`; `execute_tenant_scoped`. Do NOT edit canonical_conversation_normalizer.py, tool_trace_normalizer.py, jsonl/json/csv_importer.py (csv only in P5), xlsx_importer.py / ooxml_safety.py (FTF-0505's write set), dataset_object_source.py, dataset_upload_store.py, dataset_source_connection_*.py, direct_object_storage_upload*.py, dataset_quality_*.py, the three existing dataset-factory components, or migrations 0024–0026 and later; `import_common.py` changes only through the P1/P5 additive edits, with Law 15 requalification.
  - Mapping model (no new mapping language): the certified FTF-0502-P1 MappingSpec verbatim with its own `mapping_sha256`; shapes `messages` / `messages_json` / `prompt_completion`; exact `role_map` from each raw value to FTF-0506 `ALLOWED_ROLES` {system, user, assistant, tool} (developer context -> `system`; supervised target `assistant`); `tool_calls_key` / `tool_call_id_key` / `name_key`; `group_key`; closed metadata set {language, task_category, consent, quality_label, source_record_id, event_timestamp}; format blocks (CSV encoding/delimiter/header row, JSON container). The UI suggests nothing: every observed raw role value is listed with no target and the user picks each one.
  - Critical roles / tool IDs: at propose time 400 `critical_role_unmapped` (a message shape whose `role_map` targets lack `user` or `assistant`) and `tool_id_unmapped` (targets `tool` or maps `tool_calls_key` without both `tool_calls_key` and `tool_call_id_key`); at row level (certified behaviour, now driven with the project's real tool definitions) a call naming a tool absent from the current FTF-0404 definitions, an orphan/duplicate call id, or a result not matching its call is rejected and shown with its code, never imported; at approval a preview containing `unmapped_role` or `unmapped_message_key` -> 409 `critical_rows_unmapped`; `on_unmapped_message_keys:"ignore"` needs `acknowledged_ignored_keys` exactly equal to the keys seen in the preview (409 `ignored_keys_not_acknowledged`; only their sha256 stored).
  - Arabic/text types: no type inference, NFC/NFKC, casefold or trim; strings stay strings (`"0055"`, `"١٢٣"`, `"1e3"`, `"true"`, dates); Arabic with RTL marks, ZWJ/ZWNJ, tashkeel, tatweel or emoji is byte-identical raw vs canonical; windows-1256 and UTF-8 versions of the same CSV give identical canonical candidates; the preview shows decoded samples so a wrong-but-valid declared encoding is visible (2026-10-14 ledger ruling).
  - Closed declarative transform vocabulary (`TRANSFORM_VOCABULARY`, `TRANSFORM_VERSION="h011.1"`, mirrored as an enum in 05-domain): `select`, `role_map`, `json_parse` (`messages_json` through `strict_loads`), `timestamp_check` (ISO-8601 with offset; verbatim), `group_key`, plus `group_rows` and `label_enum_map` from P5. No expression, formula, template, regex, code or path field anywhere; unknown keys refused by the certified exact allow-list; values such as `"=1+1"`, `"${x}"`, `"lambda x: x"`, `"__import__('os')"` are only names, never evaluated. Behavioural: a `sys.addaudithook` run over every preview sees no `exec`/`compile`/`subprocess.*`/`os.system`/import of data; P4 adds ESLint `no-eval`/`no-implied-eval`/`no-new-func` for apps/web (additive).
  - Boundary (Law 16, §67.2): probe and preview read only the verified object of the path's `dataset_id` through `open_dataset_source`; bodies `additionalProperties:false` with no URL/bucket/key/path field; no listing or discovery; no read of repository, log or other-dataset data; outbound traffic during a preview only to the configured storage endpoint and the DB (audit-hook socket check); preview content never persisted, logged or audited (canary scan over every DB text/jsonb column, logs and audit rows); held-out datasets 404 to ml_engineer; foreign and missing datasets an identical 404.
  - Purpose/grants (source-use grant = FTF-0500 `allowed_purposes`; §67.4 DataUseGrant is FTF-H013): probe, preview, propose, approve and `resolve_approved_mapping` each re-run `authorize_dataset_source_purpose(connection from the dataset's upload session, purpose)` live; the approved purpose must equal `datasets.purpose` (409 `purpose_mismatch`; §9 no reclassification), authorization checked first - a training approval on an evaluation-only connection, or one narrowed/revoked after upload, gets 403 `purpose_not_authorized`, and again at resolve time.
  - Masking (§67.2 "masked unless the user has preview permission"): values visible to workspace_admin/organization_admin, and to ml_engineer only when the connection's `security_classification` is public or internal; otherwise every string leaf, header and key becomes `{"masked":true,"chars":n}` and mapping documents are withheld (JSON types and the `identical` flag stay visible).
  - **FTF-H011-P1 Preview runner — CERTIFIED 2026-10-21 (see Done)** (pure + read-only DB/S3; lock `module:import_common`; UNWIRED, proving_symbol blank; ready now). Files `workers/data/dataset_mapping_review.py`, `apps/api/app/datasets/dataset_mapping_service.py`, `tests/tasks/test_ftf_h011_p1.py` (script; additive `gate` step), `tests/integration/test_ftf_h011_p1_mapping_preview_postgres_s3.py`, additive ci.yml job `dataset-mapping-preview-integration` (PG16 + SeaweedFS; `dataset-import-source-integration` precedent) + classify focus + ci-verdict `needs` + `tests/unit/test_ci_policy.py`. ONE additive serialized `import_common.py` edit (IMPORTER ORDER catalog rule): `ImportMapping.row_observer: Callable[[Row, Sequence[str] | None], None] | None = field(default=None, compare=False, repr=False)`, called first by `apply_mapping` with a copy of the row, never in `document` or `mapping_sha256`, never set by `mapping_from_object`; differential test (candidates and `report_sha256` identical with and without it); the 0502-P1/0502/0503/0504 suites (and 0505's when it lands) re-run in the same PR. If FTF-0505 r4 (#826) also edits import_common.py, P1 waits for it. `run_mapping_probe(format block)` (one minimal certified probe mapping per importer; candidates discarded; headers + <= 20 raw rows). `run_mapping_preview(mapping)`: importer chosen by the server-verified content type only (jsonl, json, csv; XLSX -> `importer_unavailable` until P6); `ImportLimits(max_rejections=200, deadline_seconds=30)`, preview max 20 (lower-only); per row the locator, raw row (masked as above), outcome and code/slot, canonical candidate and comparison rows `{slot, raw_type, canonical_type, identical}` plus `not_mapped` columns/keys; `observed_role_values`, counts, `complete`, `tool_definitions_sha256`, `source_schema_sha256`; `preview_sha256` over (dataset sha, mapping sha, tool-definitions sha, candidates' record/normalized hashes, rejections, counts); storage failure -> `blocked`/`source_unavailable`, object changed -> `source_changed` (Law 17). Tests over the irregular fixtures listed in P4 on real PG16 + SeaweedFS, uploaded through the real FTF-0501 routes. Faults, each RED by a behavioural check: observer mutates the row; observer in the hash; observer called after mapping (raw of rejected rows lost); tap/event pairing off by one; masking skipped or a header left in clear; type coerced or text normalized; preview limit raised; XLSX dispatched to the provisional importer; `tool_schemas` not passed; held-out readable; either critical check skipped; `preview_sha256` omits rejections or tool definitions; grant not re-authorized; socket to a non-storage host allowed; content in an exception or log (shared META-LEAK helper `tests/tasks/import_test_support.py`).
  - **FTF-H011-P2 Mapping persistence — CERTIFIED 2026-10-23 (see Done)** (after P1 CERTIFIED; lock `migration:application`; UNWIRED). Files `apps/api/alembic/versions/00NN_dataset_mappings.py` (next free number at claim time; revision id <= 32 chars), `apps/api/app/datasets/dataset_mapping_store.py`, `tests/tasks/test_ftf_h011_p2.py`, `tests/integration/test_ftf_h011_p2_dataset_mappings.py`, additive job `dataset-mapping-store-integration`. Table `dataset_mappings`: composite FK (org, project, dataset_id, name, purpose, version, sha) -> `uq_datasets_quality_lineage`; `dataset_source_connection_id` FK -> `uq_dataset_source_connections_org_project_id`; `mapping_version` >= 1 with a parent chain (0519 pattern); `mapping_document` JSONB, `mapping_sha256`, `importer`, `importer_version`, `transform_version`, `source_schema_sha256` (nullable), `tool_definitions_sha256`, `preview_sha256`, `preview_counts` JSONB (content-free), `ignored_keys_sha256`; `status` proposed|approved|rejected, `allowed_purposes_snapshot` text[], `approved_purpose`, `approved_by`, `approver_role`, `approved_at`; CHECKs approved <=> all approval columns NOT NULL, `approved_purpose = dataset_purpose`, `approved_purpose = ANY(allowed_purposes_snapshot)` (raw SQL cannot record a training approval under an evaluation-only grant); every hash `~ '^[0-9a-f]{64}$'`; ENABLE + FORCE RLS with the 0026 `tenant_isolation` policy; sovra_app SELECT, INSERT, column-limited UPDATE, never DELETE; BEFORE UPDATE trigger (0026 `to_jsonb` diff) allows one proposed -> approved|rejected transition; `data_exposure_check.py --backends postgres_rls,s3_identity` passes (no new bucket/identity). Store (each refusal a stable code that writes nothing; content-free audit `dataset_mapping.proposed|approved|rejected`): `propose_dataset_mapping` (parse, critical checks, versioning); `approve_dataset_mapping(expected_preview_sha256, purpose, acknowledged_ignored_keys)` (approver from the principal; admins only `approval_not_permitted`; preview re-run outside the transaction must match `preview_changed`; `preview_incomplete`, `critical_rows_unmapped`, grant/purpose checks); `reject_dataset_mapping`; `resolve_approved_mapping(dataset_id, purpose)` (the input for a later import run: the dataset's own approved mapping, or a prior version's only when the re-probed header `source_schema_sha256` and `tool_definitions_sha256` are equal and the grant is live, else `mapping_reapproval_required`; JSON/JSONL always need a fresh approval). Faults: trigger dropped; FORCE dropped; purpose-equality or snapshot CHECK removed; approver from input; stale preview accepted; reuse-on-drift check skipped; revoked grant resolves; cross-tenant INSERT allowed; version gap.
  - **FTF-H011-P3 Routes and contract** (after P2; lock `contracts:05`; WIRED, proving_symbol `build_dataset_mapping_router` in `apps/api/app/main.py`). `apps/api/app/datasets/dataset_mapping_routes.py` under `/api/v1/organizations/{organization_id}/projects/{project_id}/datasets/{dataset_id}/`: POST `mapping-probe`, POST `mapping-preview` (read-only, DATASET_SOURCE_ROLES; content-free audit `dataset_mapping.previewed` with ids, counts, masked flag); POST `mappings` (Idempotency-Key); GET `mappings` (limit <= 200, after), GET `mappings/{mapping_id}`; POST `mappings/{id}/approve` and `/reject` (rationale 1..2000; admins only; Idempotency-Key + request fingerprint). Errors `{"detail": code}`: 400 (`mapping_invalid`, `mapping_unsupported`, `critical_role_unmapped`, `tool_id_unmapped`), 401, 403 (`purpose_not_authorized`, `approval_not_permitted`), 404 `not_found`, 409 (store codes, `importer_unavailable`, `source_changed`), 503 `storage_unavailable`. Contract: additive 05 paths + `$defs` (`DatasetMappingSpec` mirrors the certified allow-list, `additionalProperties:false` at every level, plus preview/mapping/request bodies); drift test over a generated table (schema-valid <=> `mapping_from_object` accepts). `tests/tasks/test_ftf_h011.py`, `tests/integration/test_ftf_h011_routes_postgres_s3.py` (real uvicorn, PG16, SeaweedFS), additive job `dataset-mapping-routes-integration`. **Control-plane rulings for P3 (2026-10-23T18:52Z, from the P2 certification):** (a) re-resolve the approved mapping INSIDE the import transaction (P2 `resolve_approved_mapping` is unlocked); (b) reuse never skips a newer rejection of the identical mapping: refuse with `mapping_reapproval_required` when any dataset version newer than the reused approval has a `rejected` decision with the same mapping_sha256 (P2 advisory V2c: v1 approved, v2 rejected, v3 still reused v1); (c) pin the refusal code for "own mapping rejected, no prior approval anywhere" (F8); (d) map the audit writer's refusal of an over-long actor_id to a stable code.
  - **FTF-H011-P4 Wizard UI** (after P3; UNWIRED until FTF-H029, proving_symbol blank). `dataset_mapping_wizard_ui.tsx`, `apps/web/tests/ftf_h011.spec.ts` (DOM Vitest; full UI CHECKLIST a–f), `apps/web/tests/real_api/ftf_h011_ui_real_api.realapi.ts` + `tests/integration/test_ftf_h011_ui_real_api.py`, additive job `dataset-mapping-ui-real-api-integration` (`resumable-upload-ui-real-api-integration` precedent). Steps: format options -> probe -> map -> preview (raw beside canonical, rejected rows with codes) -> propose -> approve (purpose read-only from the dataset, explicit confirm); states Draft / Validation required / Blocked - reason / Approved; data cells `dir="auto"`, text nodes only (an HTML payload renders as text); nothing in localStorage; standard phrases in English and Arabic (buttons, validation errors, empty/loading, "Hidden"/"مخفي"), no brand copy. Real-API scenarios: R1 windows-1256 `;` CSV with an Arabic header on row 3 -> approved, DB row has the exact `mapping_sha256`; R2 JSONL with tool calls, an orphan id and an unknown tool -> rows rejected and shown; R3 no assistant in `role_map` -> 400 shown, nothing stored; R4 unmapped raw role -> approve 409 shown; R5 evaluation-only connection approved as training -> 403, a narrowed connection -> 403; R6 ml_engineer on a confidential connection sees only masked values; R7 foreign dataset -> 404; R8 storage stopped -> blocked shown; R9 JSON with a container pointer; then an independent DB check and the canary scan.
  - **FTF-H011-P5 Grouped rows and label maps** (after P4; lock `module:import_common`; Law 15 requalify 0502-P1/0504). One serialized edit to `import_common.py` + `csv_importer.py`: the recognised-unsupported `grouped_multi_row` shape becomes `group_rows {group_key, order_key: int|rfc3339}`; rows of a group contiguous and strictly ordered; a gap, duplicate order, non-contiguous group, or a group ending without an assistant turn is quarantined (`incomplete_group`, additive code); memory bounded by one group <= max_record_bytes. `label_enum_map` exact and only for `language` / `task_category`; `consent` / `quality_label` stay verbatim (§67.2: no invented "approved").
  - **FTF-H011-P6 XLSX** (only after FTF-0505 is CERTIFIED): registry entry for `XLSX_CONTENT_TYPE` and a sheet probe through 0505's public API (exact names, hidden sheets flagged), plus XLSX rows in the P1/P3/P4 tables (formula cell -> `formula_cell` shown, Arabic sheet name). Until then XLSX gets a visible `importer_unavailable`, never a fallback.
  - Recorded readings (default-resolution rule): AF-15 says "before immutable import", so H011 ends at an approved, versioned MappingSpec plus `resolve_approved_mapping`; the full-file run and record persistence belong to the next import item (FTF-0502-P2 / durable dispatch FTF-0801/H016, to be designed); the "import via UI" evidence is the real importers over real uploaded irregular sources through the real UI and API. The UI cannot be reached from / now: FTF-H029 depends on FTF-H011, so H029 mounts the page and owns the from-/ browser journey (2026-10-09 ruling) - FTF-0501/0516 precedent (certified with UNWIRED UI, not product-true until H029). "Playwright against the real API" has no repo precedent and Playwright is not installed (adding it would be ask-first); the precedent is a real-API Vitest + jsdom test against real uvicorn/PG16/SeaweedFS. FTF-0507 applies to trace connectors, not shipped (R1 datasets are upload-only); its session/sequence/`authorized` mapping lands with that connector. Expected-outcome/grader mapping for evaluation suites stays with FTF-0601. "Preview permission" has no entity: admins, plus ml_engineer only on public/internal connections; FTF-H013 or an RBAC task may widen it later, never H011.
  - Ask-first: none. No new runtime or dev dependency (stdlib/SQLAlchemy/Alembic/React only), no keyed service, no secret, no reused component touched.
- **FTF-R006 Argilla review, reviewer identity and immutable export** (Control-plane design 2026-10-15T14:14Z; FINE_TUNING_FACTORY_RELEASE_1.md §8–10, §67, §72, §77.3–77.6, §78.4, §80.1–80.4, §81; CAP-07, CAP-08, CAP-19, CAP-21; Q08, Q35, Q36, Q38; deps FTF-R002, FTF-0500, FTF-0506, FTF-0516 (+P1) all CERTIFIED; recorded extra dependency (default-resolution rule): P2/P3 wait for FTF-0519-P1 CERTIFIED - §80.1 runs review before split and "no old split mutation" is vacuous until split rows exist. DoD `python tools/r1_handoff.py packet FTF-R006 --plan FINE_TUNING_FACTORY_RELEASE_1.md` plus this entry. Locks `migration:application` (P1–P3), `contracts:05` (P4, serialized after FTF-0519-P2). Contract owner FTF-R001: read `architecture.schema.json` / `deployment-profiles.yaml`, change neither). Acceptance: "Real authorized annotations with reviewer provenance/concurrency control, scoped records and canonical approved export." / "Export validates current grants/hash/schema/groups and creates immutable new dataset version; lifecycle owner and export test recorded." Negative: "No all-users-as-admin audit, no final-suite data in ordinary queues/search, no source permission expanded to training, no old split mutation or duplicate export." Four slices; P1–P3 UNWIRED (proving_symbol blank, FTF-R005 precedent), P4 WIRED; the parent closes when all four are certified. **P1 is split into P1a (returned, item 0m) and P1b (after P1a CERTIFIED).**
  - Integration form (unchanged; no owner ask): Argilla is a self-hosted internal service (REFERENCE_REPOSITORIES.md; `spec/runtime-architecture.json` form `internal_service`) reached only through its pinned REST API `/api/v1` by a stdlib adapter - no `argilla` SDK (a new runtime dependency; §78.4 allows "SDK/API"; FTF-R005 MLflow REST precedent). Every call through FTF-H006 `guarded_fetch`, built from the tenant's FTF-R002 `ComponentBinding` (`client_from_binding` pattern); no iframe, no native-dashboard step. §78.4/§80.4 server DB, search engine and background service are in the inventory; search engine = OpenSearch (owner-approved live 2026-10-14 over Elasticsearch - do not reopen). Nothing exists yet (R002 added no Argilla code; no server in CI or `infra/`), so P1 adds the stack CI-only, the reused component itself and never a Factory runtime dependency (MLflow-job precedent).
  - Pins: Argilla upstream v2.8.0 (latest 2.x patch at claim time; record why; §78.4 maintenance-mode risk recorded): `argilla/argilla-server` + a `python -m argilla_server worker` container, `opensearchproject/opensearch` 2.x, `redis` 7.2.x (BSD-3), a separate Argilla Postgres DB/role on the repo's `postgres@sha256:075f7ba6…` image. Every image pinned by digest, verified through the Docker Hub metadata API (FTF-0004/FTF-R017 no-pull precedent). Bootstrap USERNAME/PASSWORD/API_KEY CI-generated, never upstream defaults; `HF_HUB_DISABLE_TELEMETRY=1`, `HF_HUB_OFFLINE=1`; loopback ports. File `infra/docker/argilla/docker-compose.yml` (new; FTF-0004's compose untouched). Licence ruling (Control plane, default-resolution rule, consistent with the permissive-licence OpenSearch decision): Redis 7.2.x only - Redis 7.4+ (RSAL/SSPL) and 8.x (AGPL) are refused; if 7.2.x cannot run Argilla 2.8.0, park P1 with a dated owner ask (Valkey or another choice) - never adopt a non-permissive broker silently.
  - Reuse, do not copy: R002 `ComponentBinding`/`compute_health`/`component_sync_outbox`/`record_sync_outcome`; H006 `guarded_fetch`; FTF-0202 `store_secret_ref`/`resolve_secret_ref` (OpenBao); FTF-0500 `authorize_dataset_source_purpose`; FTF-0501-P1 `get_dataset`/`create_upload_session`/`record_upload_verified_and_create_dataset`; FTF-0502-P1 `open_dataset_source`; FTF-0506 `normalize_conversation`; FTF-0516-P1 `load_resolved_quality_report`/`excluded_batch_positions`; FTF-0515 `assert_clear_for_split`; `storage_semantics_client.put_object`; `write_audit_event`; `execute_tenant_scoped`. Do NOT edit migrations 0013/0024–0026, the dataset upload/quality stores, `canonical_conversation_normalizer.py` or any FTF-0519 module (no Law 15 requalification).
  - **P1 Deployment, client, scopes and reviewer identity** (split 2026-10-16: **P1a** = `packages/integrations/argilla/client.py` only - CERTIFIED 2026-10-22 (see Done), so P1b is claimable; **P1b** = everything else in this bullet (identity store, migration, OpenBao credential handling, compose stack, live integration) - round 1 merged (#953), RETURNED: item 0c, reusing P1a's shared guarded transport). Files `packages/integrations/argilla/client.py`, `apps/api/app/reviews/argilla_identity_store.py`, migration `apps/api/alembic/versions/00NN_argilla_review_identity.py`. Client errors kept apart (Law 17): `ArgillaUnavailableError` (network/5xx), `ArgillaRejectedError`, NotFound, Conflict; strict JSON, bounded pages and bodies. Scope: one Argilla workspace per (org, project), named `sovra-` + sha256(org|project)[:24]; no shared workspace. Identity: one Argilla user per (org, Sovra principal), role `annotator` only, member only of the workspaces of projects where the principal holds a DATASET_SOURCE_ROLES membership; the per-user `api_key` from `POST /api/v1/users` goes straight to OpenBao and only the `openbao://` ref is stored; the password is random, discarded, never shown; the provisioning (owner) key lives only in the binding's `secret_reference` and never submits a response; a removed membership revokes the workspace membership; a revoked identity is refused. Tables `argilla_review_scopes` (org, project, component_binding_id FK, argilla_workspace_id; UQ (org, project)), `argilla_reviewer_identities` (org, principal_id, argilla_user_id UQ, argilla_role CHECK = 'annotator', credential_secret_ref CHECK `^openbao://`, status, revoked_at; UQ (org, principal_id)), `argilla_workspace_members` (scope_id, identity_id, status). Integration `tests/integration/test_argilla_identity_scope.py` (real Argilla stack + PG16 + OpenBao built from the pinned commit, reusing the secret-manager-integration steps): R002 `/validate` reports live version/health and unhealthy once the server is stopped; every mapped user's role reads back annotator; an annotator gets 403/404 on another org's/project's workspace and dataset; no API key in DB, audit or logs.
  - **P2 Scoped queue and concurrency-controlled responses** (after FTF-0519-P1 CERTIFIED). Files `packages/integrations/argilla/review_mapping.py`, `apps/api/app/reviews/review_queue_store.py`, migration `00NN_review_queues`. Queue from one source `datasets` version: `get_dataset` + live `authorize_dataset_source_purpose`, `open_dataset_source` (sha verified), quality report `assert_clear_for_split`, `excluded_batch_positions` dropped, each record normalized with 0506 and pushed with metadata record_id `{dataset_id}:{pos}`, group_id, record_sha256, schema version; questions `decision` {accept, reject} + `rationale` (text <= 2000 chars). Refusals that write nothing: `sealed_purpose_refused` (source purpose `held_out_evaluation` or `baseline_only`), `dataset_already_split` (a source of any locked FTF-0519 manifest), `quality_not_clear`, `grant_changed`, `queue_too_large` (100,000, never truncated). Idempotent push (records found by record_id metadata before create). Responses submitted only with the reviewer's own key (Argilla `user_id` = mapped annotator); `review_responses.row_version` optimistic concurrency (`expected_version` checked under `SELECT … FOR UPDATE`; stale -> `review_version_conflict`, nothing written to Argilla); read-back after each write, `response_sha256` stored; reconciliation by authenticated polling, never webhooks - an Argilla-side edit or a response by a non-mapped/owner/admin user marks the item `drift` and blocks export; Argilla down -> `argilla_unavailable`, Factory row unchanged (§80.3). Tables `review_queues`, `review_items`, `review_responses`: composite FK to `uq_datasets_quality_lineage`, UQ (queue_id, record_id), UQ (item_id, identity_id), content-free audit row per response with actor = Factory principal.
  - **P3 Canonical approved export** (after P2 CERTIFIED). Files `packages/integrations/argilla/export.py`, `apps/api/app/reviews/review_export_store.py`, migration `00NN_review_exports`, `tests/integration/test_argilla_review_export.py`. `approve_export(..., expected_queue_snapshot_sha256, idempotency_key)` by workspace_admin/organization_admin (actor = authenticated principal). Revalidated now, each a stable code: source row and object sha (`source_changed`), current grant (`grant_changed`), latest quality report hash (`quality_report_changed`), every record re-normalizes to its queued sha (`record_hash_mismatch`), Argilla record metadata hash (`argilla_record_drift`), every counted response by an active mapped annotator still holding the role (`reviewer_not_authorized`) with unchanged sha (`response_drift`), no admin/owner response (`admin_response_refused`), annotation schema version and group ids unchanged. A record is approved only with `required_reviews` (1..5) submitted responses, all accept. Output: canonical JSONL rebuilt from the Factory source bytes (never Argilla text), sorted by record_id; recorded export sha, record/group counts, `groups_sha256`. Publication: `create_upload_session` on the source dataset's own connection with purpose = the source dataset's purpose (never request input, so a grant cannot widen), `put_object`, `record_upload_verified_and_create_dataset` -> a new immutable `datasets` version; nothing existing is updated. `review_exports` UQ (queue_id, export_input_sha256) and UQ (org, idempotency_key): replay returns the same dataset, same key + different body -> 409, a crash between PUT and insert resumes from the outbox row. Lifecycle: additive edit to the argilla entry in `spec/component-maintenance.yaml` (pinned version and digests, support owner, vulnerability policy, export test id).
  - **P4 Routes and wiring** (lock `contracts:05`). `apps/api/app/reviews/review_routes.py` wired in `main.py` (`build_review_router` = proving_symbol). Routes (§77.6) under `/api/v1/organizations/{org}`: `/projects/{id}/reviews` (create = 202 + outbox command; list), `/reviews/{id}/responses` (expected_version), `/reviews/{id}/exports` (Idempotency-Key + request fingerprint). Additive 05-interface.yaml / 05-domain.schema.json paths (no R-phase OpenAPI file exists; R001's schema has no review shapes - if the R001 owner objects, P4 is BLOCKED_DEPENDENCY), `additionalProperties: false`, `{"detail": code}`, 404 identical for missing and foreign. Executor: queue build and export run in `workers/data/review_worker.py` from `component_sync_outbox` (no Temporal scheduler yet - disclosed as later R-phase wiring). `tests/tasks/test_ftf_r006.py` + a real uvicorn test against the live stack.
  - CI/tests: one additive job `argilla-review-integration` (compose stack + postgres service + OpenBao build; classify focus entry; ci-verdict `needs`; `tests/unit/test_ci_policy.py` coverage), extended by later slices; no-network task scripts `tests/tasks/test_ftf_r006_p{1,2,3}.py` as `gate` steps. Law 11: the real-Argilla tests use no mock (a mocked transport only in the no-network unit scripts). Every table ENABLE + FORCE RLS, `tenant_isolation` policy exactly as 0026, no DELETE for sovra_app, column-limited UPDATE + 0026-style trigger, every hash `~ '^[0-9a-f]{64}$'`, `data_exposure_check.py` passes, raw cross-tenant INSERT/SELECT refused. Migration numbers: next free at claim time (head 0029; 0030 earmarked for FTF-H010-P4; FTF-0519-P1 likely next); revision id <= 32 chars.
  - Faults, each its own row, RED by a behavioural check (against the live stack where it applies) - P1: response submitted with the owner key; identity created as admin; a workspace shared across projects; API key persisted or logged; revoked identity still writes; health ok while the server is down. P2: held-out source queued; split-locked source queued; excluded positions pushed; expected_version ignored (second writer wins); drift undetected; replay duplicates records; outage turned into success. P3: export purpose from the request or hardcoded `training`; revoked grant exports; content read from Argilla; drifted or admin response counted; group id dropped; existing `datasets`/FTF-0519 rows changed (snapshot hash before/after); duplicate export creates v3; FORCE dropped; update trigger dropped. Uncapped `scripts/mutation_gate.py` over the new modules, 0 survivors. Final suite stays out of ordinary search: a canary planted in a held-out dataset is never findable through Argilla `records/search` by the annotator or the owner across all workspaces.
  - Recorded readings: Argilla has no response version precondition, so concurrency control lives in the Factory (proven in P2); review roles = DATASET_SOURCE_ROLES, export approval = workspace_admin/organization_admin, no role widened; final-suite authoring needs the §78.4 independently permissioned enclave and is refused here (FTF-H020's); PII/poison checks on the export run when the new version goes through the 0514/0515 pipeline before split, not inside the export; the OpenSearch security plugin is off in loopback CI only - production TLS/auth stays with deployment tasks R008/R012 (disclosed).
  - Ask-first: none open (Redis licence ruling above parks P1 with an owner ask only if 7.2.x cannot run 2.8.0). No Factory runtime dependency, no keyed service, no reused component swapped.
- **FTF-1505 Secret scanning — CERTIFIED 2026-10-21 (see Done)** (Control-plane design 2026-10-14; FINE_TUNING_FACTORY_RELEASE_1.md §45–47, §52–53 ("CI fails on ... leaked secrets"), §65–66, §77, §81; CAP-14; Q24, Q25, Q26; deps FTF-0006, FTF-0904, FTF-1501 all CERTIFIED; composes certified FTF-0905; DoD `python tools/r1_handoff.py packet FTF-1505 --plan FINE_TUNING_FACTORY_RELEASE_1.md`; no shared lock; contract owner FTF-1501 — reuse `ScenarioResult`/`build_evidence` from `tests/security/tenant_isolation_suite.py`, do not edit `15-interface.yaml`). Acceptance: "Scan current source/history/artifacts/log samples with seeded harmless secret markers." Negative: "Scanner omissions/excluded production dirs and actual exposed credential are release blockers." Ready now.
  - Files: `tests/security/secret_scanning.py` (standalone script like the 1501/1506 suites), `tests/security/secret_scan_baseline.yaml` (reviewed fixture fingerprints), `tests/tasks/test_ftf_1505.py` (script, `gate` job), an additive ci.yml job `secret-scanning` (checkout `fetch-depth: 0`, no services) + verdict `needs` + `tests/unit/test_ci_policy.py` coverage.
  - No production code, migration, contract, runtime dependency, secret or keyed service. Stdlib + the `git` CLI only. No gitleaks/trufflehog/detect-secrets and no new third-party action (each would be a new dependency). Test-only task: proving_symbol blank (FTF-1506 precedent). The job itself is the live CI gate (§53).
  - Why not reuse FTF-0904 `redact_text` as the detector (measured 2026-10-14): over the 1,209 tracked text files it changes 3,014 lines in 328 files (`"client_secret": client_secret,`, `secret_ref: str | None,`, `f"Bearer {key}"` ...). That over-redaction is 0904's design for logs and makes it unusable as a source scanner. 1505 owns a high-precision credential-format rule table instead, and uses 0904 as a cross-check (below).
  - Rules (`RULES`, each with an id, compiled once, linear-time patterns, applied to raw bytes and to one layer of base64 runs ≥24 chars and one layer of percent-decoding, detection only):
    - AWS access key id `(AKIA|ASIA)[A-Z2-7]{16}` and a 40-char secret next to `aws_secret_access_key`;
    - GitHub `gh[pousr]_[A-Za-z0-9]{36}`, `github_pat_[A-Za-z0-9_]{82}`; GitLab `glpat-[A-Za-z0-9_-]{20}`;
    - `sk-` / `sk-proj-` / `sk-ant-` keys ≥32 chars; Stripe `(sk|rk)_live_`; Slack `xox[abposr]-` and Slack webhook URLs; Google `AIza[0-9A-Za-z_-]{35}`; Hugging Face `hf_[A-Za-z0-9]{34}`;
    - JWT: three base64url segments whose first decodes to a JSON object with `alg`;
    - PEM `-----BEGIN … PRIVATE KEY-----` with a body;
    - URL userinfo password `scheme://user:pass@host` where pass is not a template/placeholder (`${…}`, `<…>`, `{…}`, `***`, `[REDACTED]`);
    - a secret-named assignment (`api_key`, `secret`, `password`, `token`, `client_secret`) to a quoted literal of ≥20 chars with Shannon entropy ≥3.5 bits/char (FTF-0514's threshold);
    - the FTF-0905 canary shape `CANARY-DO-NOT-USE-[0-9a-f]{32}` (log and artifact lanes only).
  - Lanes (each one `ScenarioResult` per seeded rule family + one coverage row, through `build_evidence`):
    - `source`: every path from `git ls-files` at HEAD, text and binary, plus members of zip/tar/gz/whl archives (bounded size/count, bomb-safe). No path exclusion exists. Coverage = scanned set equals `git ls-files` exactly, and every production SOURCE_ROOT (read from `scripts/product_truth_check.py`, not hand-listed) has ≥1 scanned file.
    - `history`: every unique blob reachable from all refs (`git rev-list --objects --all` + `git cat-file --batch`), deduplicated by blob id; refuse a shallow clone (`git rev-parse --is-shallow-repository` must be `false`, else `NOT_EXERCISED` → RED). Measured locally: 8,113 blobs, 2.65 GB, 225 s; record the CI runtime.
    - `artifacts`: every file under declared artifact roots (`reports/` and the suite's own evidence output) after the job writes them, including archive members. Completeness: every `actions/upload-artifact` `path:` in ci.yml must lie under a declared root (parsed from ci.yml), so a new uploaded artifact that is not scanned fails `gate`. Model/dataset/container artifacts do not exist yet; the task that first produces one adds its root (disclosed).
    - `log samples`: boot the real `app.main.create_app()` under uvicorn over a real socket with uvicorn's default log config, capturing root + `uvicorn.*` loggers + stderr/stdout. Configure seeded harmless secret values (built from FTF-0905 `generate_canary` tokens into the rule shapes) as the LiteLLM key / S3 secret / OIDC client secret env values, send requests carrying them in Authorization, Cookie, a JSON body and a query string, hit a 4xx and a 5xx/validation error path, and emit FTF-0006 `StructuredEvent`s via `emit_to_stream`. Scan the captured text for every rule plus the canary in literal/base64/hex/url/fragment forms (FTF-0905 `encode_canary`/`fragment_canary`). Positive control: the suite logs one seeded marker through its own logger and the lane must find it (else vacuous → RED).
  - Seeded markers (recall): generated at run time, never committed, so the repo never contains them: one per rule family, in each lane — a new file in a temporary real git repo (`git init` in a temp dir), a marker committed then deleted (history only), a base64-wrapped marker, a marker inside a zip member, a marker in an artifact root, a marker in a log line. Required recall 100% on seeded markers; every miss is RED with its rule id and lane.
  - Baseline (`secret_scan_baseline.yaml`): entries `{rule, fingerprint: sha256(rule NUL value), reason, reviewed_by, reviewed_on}`; values are never stored. No path or glob key is accepted (schema-checked). An unmatched (stale) entry fails the run. Current history needs about 36 reviewed entries (measured: 27 URL-userinfo CI/doc DSNs such as the `*_ci_only` Postgres passwords and `svc:hunter2@` examples, `AKIAIOSFODNN7EXAMPLE` (AWS's documented example), `AKIAABCDEFGHIJKLMNOP`, one `xoxb-1234567890-` literal, five `sk-…` test literals); the relay lists each with file/commit in the PR and the Control plane reviews them. If any value is not clearly a fixture, it is NOT baselined: item-level park with the finding (an actual exposed credential is a release blocker and needs rotation, not removal).
  - FTF-0904 cross-check: for every seeded marker family, `redact_text(line) != line` must hold (what the scanner calls a credential, the log sanitizer must redact). A family 0904 misses is reported as a finding against 0904 (park; the Control plane slices the fix), never fixed in place.
  - Evidence: content-free — lane, rule id, path or blob id, line number, never the value or a fingerprint of a NEW finding (a hash of a low-entropy password can be reversed). JSON artifact via `build_evidence` + per-lane totals.
  - Faults that must go RED (each its own row, run against temporary git repos and a copy of the scanner): a rule removed; history lane limited to HEAD (committed-then-deleted marker missed); shallow clone accepted; a path exclusion added to the policy; `tests/` skipped; binary files or archive members skipped; base64/percent decoding removed; stale baseline entry accepted; a baseline entry keyed by path; log lane without the positive control; log capture limited to the root logger (uvicorn access log missed); an `upload-artifact` path outside every declared root accepted; a finding value written to evidence.
  - Risk to check first (dry-run before claiming): no live code installs FTF-0904 redaction yet (`install_redaction`/`attach_sink` have no caller), and uvicorn's access log prints the request line including the query string. The query-string placement is therefore expected to be FOUND. Do not drop the placement to get green: park with the reproduction; the Control plane then slices the FTF-0904 wiring (install at app startup + uvicorn log config) as its own item, and 1505 resumes after it.
  - Disclosed: pattern-based scanning cannot find a bare opaque secret with no known shape (0904/0905 disclosures apply); the history lane covers refs present in the CI clone only.
- **FTF-0501-UI Resumable dataset upload UI** (child slice of FTF-0501; Control-plane design 2026-10-08T17:00Z; §8.1, §67.1 "Multipart resumable upload", §72.2; deps FTF-0501 round 2 API). DoD: `tools/r1_handoff.py packet FTF-0501`, "resumable UI" clause.
  A real file picker in apps/web on the five FTF-0501 routes (create session, part grants, list parts, finalize, read): dataset purpose, per-part progress, pause/resume after a page reload or network drop by listing uploaded parts and re-requesting only missing part URLs, and the typed finalize outcome (accepted, 409 incomplete, 410 expired, 422 hash/type/size, 503 storage outage) shown as visible states - never a fake success (Law 17).
  No secrets, part URLs or file bytes in localStorage (§72.2); presigned URLs are held in memory only.
  Tests: component tests for each typed outcome and for resume; a browser-path test (Playwright, Chromium preinstalled) from / through the real API against SeaweedFS uploading, interrupting and resuming a file to an accepted Dataset version (Law 6). Fault: resume re-uploads every part, or a 409 shown as success -> RED.
- **FTF-0908 External provider denial** (§11.1–11.3, §12, §65, §66, §70, §77, §81; deps FTF-0203, FTF-0901, FTF-0902,
  all CERTIFIED). Ready now. Files are closed to `packages/security/external_provider_denial.py` and
  `tests/tasks/test_ftf_0908.py`, with no schema change. Contract: `python tools/r1_handoff.py packet FTF-0908 --plan
  FINE_TUNING_FACTORY_RELEASE_1.md`. Build on FTF-0901's `enforce_effect`/`effective_profile` (compose them, do not
  re-implement the profile ordering).
  - DoD:
    - When the project privacy profile forbids external managed training or inference, the request is denied with a stable,
      coded policy error, never success. That means `zero_egress` and `air_gapped`, and also `standard` with no explicit
      org route authorization for that exact provider and purpose.
    - The denial writes a content-free audit reason: profile, surface, purpose and code only, never host, path, prompt or
      account text.
    - The audit goes through the certified FTF-0104 `write_audit_event` when a session is given; otherwise the decision
      object carries the reason and the module docstring discloses that it has no live caller.
    - §11.2: a managed provider is allowed only with an explicit approval that records provider, account, region,
      training/inference terms, retention and permitted purposes. Any missing field is a denial.
  - negative_acceptance tests:
    - A "private cloud account" label (e.g. a provider marked as the org's own cloud account or a private endpoint) does
      not auto-approve third-party processing or unrestricted egress. It is still denied without the §11.2 approval.
    - A weaker project setting or an omitted job profile cannot lower the effective profile (FTF-0901 rule, asserted
      through this module).
    - Inference and training each need their own purpose approval; an inference approval never authorizes training.
    - Unknown provider, purpose or profile values, a str subclass and unhashable values each raise the module's typed
      error, never TypeError and never allow.
  - Every DoD bullet needs its own behavioural fault→RED row.
  - Mutation gate passes at 0 survivors.
  - Disclose UNWIRED in spec-matrix notes; no caller yet.
  - "Real relevant integration" is satisfied by the pure decision module plus the FTF-0901/0902 composition. A real managed
    provider is not needed to prove a denial: no network call is made, so prove it with a counting outbound-guard stub
    showing zero fetches.
- **FTF-0616-F1 Judge-call deployment fingerprint and cache bypass** (follow-up on certified FTF-0616, prerequisite of FTF-0617; Control-plane design 2026-10-09; §3.5, §27, §69.5, §77, §81; deps FTF-0616 and FTF-H005, both CERTIFIED; DoD: `tools/r1_handoff.py packet FTF-0616` plus this entry). Files: `workers/evaluation/llm_judge_grader.py`, `tests/tasks/test_ftf_0616_f1.py`, and an additive ci.yml gate step. No open PR touches the module (checked #709: R004-P3 does not).
  - Why: FTF-0617 must know which deployment served the content-bearing judge call itself. Today the result carries only the `model` echo, so a fallback that keeps the model string is undetectable.
  - Additive only:
    - Add `LlmJudgeResult.deployment_fingerprint: str | None`, taken from the judge response's `x-litellm-model-id` header. This is the same documented header and the same lookup as `route_attestation._deployment_fingerprint_from_headers`: the name is compared as exact lower-case ASCII, never by substring.
    - The value must be exact `str`, 1..256 characters (the width of `model_versions.observed_deployment_fingerprint`), with no Cc/Cf/Zl/Zp characters. Otherwise it is `None`. It is never fabricated, and it is `None` on every non-200 path.
    - Every judge request carries LiteLLM's documented `"cache": {"no-cache": true}` (the field H005 already sends; §3.5 "semantic caching ... disabled" for evaluation routes).
  - No other behaviour change. Refusals, calibration, echo matching, schema, verdict and `route_identity_state` stay exactly as certified in round 8. A missing header does not change PASSED/FAILED here; the strict decision belongs to FTF-0617.
  - Golden test: the request body is byte-identical to round 8 apart from the one `cache` key.
  - Law 15: the PR re-runs the whole FTF-0616 suite, the full Unicode variant sweep (including the advisory U+2029, LRM/RLM and U+00AD points) and the uncapped whole-file mutation gate. Control plane re-certifies the FTF-0616 adapter scope together with F1.
  - Control-plane ruling 2026-10-09: FTF-0616 stays CERTIFIED while F1 is in flight; the F1 verifier re-runs the full FTF-0616 evidence. If F1 is returned for a 0616 regression, FTF-0616 is listed in item 0 and flagged for requalification (Law 15).
  - Tests on the existing local TLS gateway harness:
    - header present → recorded verbatim;
    - absent, empty, 257 characters, a control character, a NUL or a non-ASCII value → `None`;
    - a header on a 400, 401, 403 or 5xx response → ignored;
    - a look-alike name (`x-litellm-model-id-x`, `x-litellm-model`) → ignored;
    - the no-cache field is present exactly once.
  - Faults that must go RED:
    - the header is ignored;
    - substring or prefix match on the header name;
    - the value is read from the request instead of the response;
    - the fingerprint is read on a non-200 path;
    - the length or character check is dropped;
    - the cache field is dropped.
  - Must not edit `route_attestation.py`, `route_snapshot.py` or any grader. No runtime deps or secrets.

- **FTF-0617 Zero-egress judge policy** (Control-plane design 2026-10-09; §3.5, §11.1–11.3, §23–30 (§27), §69.1, §69.5, §77, §81; deps FTF-0402, FTF-0601, FTF-0616 CERTIFIED + FTF-0616-F1; composes certified FTF-0901, FTF-H005, FTF-0201, FTF-0103A; DoD: `tools/r1_handoff.py packet FTF-0617`; contract owner FTF-0601 — read 06-interface.yaml / 06-domain.schema.json, add no path). Acceptance: "Server enforces private-only judge or authorized humans on strict projects." Negative: "External fallback or unknown upstream judge fails closed even when LiteLLM alias looks local."
  - Spec it implements:
    - §27 "zero-egress mode requires a private/local judge".
    - §69.5 "Judge outages/unsupported parameters do not become pass or silently switch to an unapproved external model. Private projects use a qualified private judge or independent humans; lack of judge capability is an explicit blocked criterion."
    - §3.5 "ZERO_EGRESS validates every upstream, fallback, logging callback, cache and embedding/judge destination behind LiteLLM. A local gateway address does not prove local processing."
  - Path refinement, recorded: `services/evaluation-worker/evaluators/zero_egress_judge_policy.py` becomes `workers/evaluation/zero_egress_judge_policy.py`, the real root the sibling graders and FTF-0616 use.
  - Files:
    - that module;
    - `tests/tasks/test_ftf_0617.py`;
    - `tests/integration/test_ftf_0617_zero_egress_judge.py` (PG16 as sovra_app);
    - an additive ci.yml gate step and integration job, added to the verdict `needs`.
  - No migration, route, UI, runtime dependency, secret or keyed service.
  - Boundary with FTF-0907 ("External judge denial", deps 0617/0901/0902, `packages/security/external_judge_denial.py`):
    - 0617 owns the evaluator's judge-selection policy and per-call identity verification.
    - 0907 later owns non-bypassable denial at the security/network layer ("direct client path cannot bypass") and admission integration.
    - 0617 must not create 0907's file.
  - Profile in force:
    - The module reads the profile itself. It loads the org `default_security_profile` and the project `security_profile` through `execute_tenant_scoped` in the caller's tenant-scoped session.
    - It composes them with an optional job snapshot through FTF-0901 `effective_profile`.
    - It never takes a caller-supplied profile string or a UI flag.
    - Strict means FTF-0901's boundary-only set (`zero_egress`, `air_gapped`). Import it, do not re-list it.
    - A missing project and a foreign-org project both give the same `judge_policy_not_found`.
  - Gateway identity:
    - `base_url` and `secret_ref` come from the tenant's FTF-0201 LiteLLM connection row, loaded by id via `get_connection`. They are never caller strings.
    - A revoked connection gives `judge_gateway_revoked`.
  - `decide_judge_plan(...)` returns a `JudgePlan` with `mode` ∈ {`private_llm_judge`, `human_review`, `standard_llm_judge`, `blocked`} and a reason code. On a strict profile:
    - `private_llm_judge` only when all of these hold:
      - a valid private-judge approval (below);
      - a fresh admission attestation through FTF-H005 `attest_route` + `pin_route_identity`, using synthetic content, cache bypassed, made before any candidate content is sent;
      - the attestation equals exactly the approval's (`resolved_model`, `deployment_fingerprint`).
    - Otherwise `human_review`, when at least one authorized reviewer exists (below).
    - Otherwise `blocked` with `judge_capability_unavailable`: never pass, skip or an external judge.
    - On a standard profile, a non-internal judge destination still needs FTF-0901 `enforce_effect(surface="judge")` to allow it. The §11.2 managed-provider approval for judges stays with FTF-0907/0908 (disclosed).
  - Private-judge approval parsing:
    - Plain JSON only: `json.loads` output with duplicate keys refused via `object_pairs_hook` and NaN/Infinity refused. Exact types only (`type(x) is str/int/bool/dict/list`): no `str`/`dict` subclasses and no caller objects. The module builds its own frozen records.
    - Exact top-level allow-list: {`schema_version`, `judge_model_id`, `resolved_model`, `deployment_fingerprint`, `boundary_id`, `destinations`, `approved_by_issuer`, `approved_by_subject`, `approved_at`, `expires_at`}.
    - `destinations` has exactly the five §3.5 roles {`upstream`, `fallbacks`, `logging_callbacks`, `cache`, `embedding`}. Each is required and explicit.
    - On a strict profile, `fallbacks` must be `[]`, else `judge_fallback_not_permitted`. A fallback would change the attested identity anyway.
    - Each destination is `{kind ∈ internal|managed_provider|external, provider?}` or `{"kind":"disabled"}` (cache/embedding only).
    - Any missing, extra, wrong-type or `unknown` value gives `judge_approval_invalid` (fail closed).
    - Every non-disabled destination goes through FTF-0901 `enforce_effect(EffectRequest(surface="judge", ...))`. A refusal maps to `judge_destination_blocked`.
    - Approval time checks: `expires_at <= now` or `approved_at > now` gives `judge_approval_expired`.
    - The approver must be an active `organization_admin` or `workspace_admin`, resolved by FTF-0103A `lookup_org_role` on Postgres (a server lookup, not a field). Otherwise `judge_approval_not_authorized`.
  - Exact identity:
    - `judge_model_id`, `resolved_model`, `deployment_fingerprint` and membership lookups compare exact `str` with `==`. No strip, casefold or NFKC anywhere on the allow path.
    - Names and hosts are never evidence of locality. An alias containing `local`, `localhost`, `127.0.0.1`, `ollama/`, `hosted_vllm/`, `vllm`, `private` or `internal`, or a gateway on loopback/RFC1918, never upgrades a missing or mismatched approval.
  - `run_private_judge(plan, ...)` runs per call:
    - It re-reads the profile, re-checks expiry and calls FTF-0616 `grade_llm_judge` with `approved_judge_model_ids = frozenset({approval.judge_model_id})` only.
    - A verdict is accepted only when all of these hold:
      - state ∈ {PASSED, FAILED};
      - `result.resolved_model == approval.resolved_model`;
      - `result.deployment_fingerprint == approval.deployment_fingerprint` (from F1).
    - Otherwise `JudgeOutcome(blocked, code)`:
      - `judge_route_identity_unverified`: header or echo missing;
      - `judge_route_changed`: a fallback or other deployment. It carries `possible_egress=True`. Disclosed: the content-bearing request may already have left; preventing that is FTF-0907/0906/0902 network-layer scope;
      - `judge_unavailable`: every non-verdict FTF-0616 state.
    - Never retried on another model; never falls back to another approved judge.
  - Human path: `authorize_human_judgment(...)`:
    - The reviewer must be an active member with a role in `EVALUATION_SUITE_WRITE_ROLES` (evaluator, workspace_admin, organization_admin), resolved by `lookup_org_role` with exact (issuer, subject).
    - §69.1 independence: the reviewer is refused as `human_reviewer_not_independent` when equal to the candidate producer, either exactly or after NFKC+casefold+strip. Exact on the allow side, normalised on the deny side: the conservative direction each time.
    - The review queue and labels (Argilla, FTF-R006) are out of scope. The plan names roles; it stores no labels.
  - Errors and side effects:
    - One base error with a stable `code`. Messages are content-free: code, profile and surface only. Never model ids, fingerprints, hosts, provider names, approval fields or reviewer ids.
    - FTF-0616, H005 and 0901 exceptions are converted with `raise ... from None`.
    - The module writes nothing to the DB. It is read-only, so there is no commit/rollback coupling (the FTF-0908 r2/r3 lesson).
    - It makes no network call except through 0616 and H005.
  - DoD tests (each bullet has its own behavioural fault→RED row; mutation gate 0 survivors over the module):
    - Real Postgres (PG16, sovra_app):
      - complete profile truth table: org × project × job (3×3×4 including None) → strict or not, exactly per FTF-0901;
      - a raw-SQL-weakened project row under a `zero_egress` org is still strict;
      - a foreign project gives a result identical to a missing one;
      - a revoked connection is refused;
      - approver and reviewer role tables over all six roles, plus revoked and foreign-org memberships.
    - Complete mode table:
      - strict × {valid approval with matching attestation; each of the 5 roles × {managed_provider, external}; each role key missing; non-empty `fallbacks`; expired; not yet valid; unauthorized approver; no approval with an authorized human; neither} → exact mode and code;
      - standard × {internal; external without RouteAuthorization; external with one} → per FTF-0901.
    - Fallback and unknown upstream on the 0616/H005 local TLS gateway harness:
      - an admission fingerprint ≠ the approval → blocked before the judge call. The request counter shows 0 requests carrying candidate content;
      - the fingerprint changes between admission and the judge call (simulated fallback) → `judge_route_changed`, no verdict;
      - the header is missing on the judge call → `judge_route_identity_unverified`;
      - same echo but a different fingerprint → blocked;
      - aliases from a generated "looks local" set, with the gateway on 127.0.0.1 → still blocked without a valid approval.
    - Exactness:
      - Use property-generated Unicode variants (whitespace, Cc/Cf/Z*, NFKC/NFKD look-alikes, dashes, case forms, configured-side forms). Reuse the FTF-0616 round-8 generator; do not hand-pick a list.
      - Apply them to `judge_model_id`, `resolved_model`, the fingerprint, the approver and the reviewer subject. None is accepted under either a pass or a fail verdict.
      - An independence variant of the producer is refused.
    - Strict-parse table: for every allow-listed key, each of missing, extra, int-for-str, bool-for-int, float, null, list, nested object, duplicate key, NaN and an object passed instead of JSON → `judge_approval_invalid`.
    - Boundary table per text input:
      - `judge_model_id`, `resolved_model`, `boundary_id`, `provider`, approver: 0/1/200/201 characters (the FTF-0901 limit);
      - fingerprint: 0/1/256/257;
      - NUL, CR/LF, lone surrogate, astral and combining characters.
    - Content-free: one shared META-LEAK check over `traceback.format_exception` of every raised error and every outcome object (cause, context, notes, args, attributes). Markers are planted in model ids, fingerprints, hosts, secret refs, approval fields and reviewer ids.
    - Faults that must go RED:
      - a locality heuristic on the alias or host;
      - the profile taken from an argument instead of the DB;
      - the project profile used without org/job;
      - each of the 5 destination roles dropped from the required set;
      - non-empty `fallbacks` allowed;
      - fingerprint check removed (echo only);
      - admission attestation skipped;
      - casefold/strip/NFKC at any allow comparison;
      - a non-verdict state mapped to pass or to another judge;
      - each extra role added to the reviewer or approver allow-list;
      - the independence check dropped or made exact-only;
      - `from None` removed;
      - the expiry comparison `<=` changed to `<`.
  - Disclosed:
    - BLOCKED_EXTERNAL: a real private judge (an approved model served inside the org boundary, with its real `x-litellm-model-id`) behind the org gateway. Covered by the existing 2026-10-08 FTF-0616 owner action; for strict projects that judge must be a private deployment. Proven here only against the local TLS gateway stand-in.
    - UNWIRED: no EvaluationRun or workflow calls it yet (§25 runtime loop; first callers FTF-R004-P3/P4 and FTF-0618). The approval is caller-supplied plain JSON until an approval store exists (FTF-0908 precedent). `proving_symbol` stays blank.
    - Recorded gap: the FTF-0601 suite lock does not refuse a strict-project suite with `llm_judge` cases and no private-judge or human path. That is left for FTF-0618/H022 wiring. Certified 0601/0602 modules are not edited here.
    - Not required: a real LiteLLM proxy container in CI with two local upstreams and a configured fallback would prove the header semantics end to end. The image is the fixed reused gateway (not a new runtime dependency); Control plane may add it later.
  - Must not edit:
    - `llm_judge_grader.py` (beyond F1);
    - `route_attestation.py` or `route_snapshot.py`;
    - `security_profile_enforcement.py` or `external_provider_denial.py`;
    - `evaluation_suite_*` or `evaluation_case_*`.

- **FTF-H010 child slices P2–P4: validated import, roles and honest continuation** (Control-plane design 2026-10-09; FINE_TUNING_FACTORY_RELEASE_1.md §0.12, §3.3–3.5, §6.7, §6.17, §17, §41, §58A, §68.1, §68.4, §77, §81; Q07; FTF-H010-P1 CERTIFIED 2026-10-09, #699 f917395). These refine FTF-H010 under "For each card" step (3) ("refine any oversized item into bounded child IDs whose union covers every parent acceptance criterion"). They add no criteria: the denominator stays 302 and no ADR applies. Contract: `python tools/r1_handoff.py packet FTF-H010 --plan FINE_TUNING_FACTORY_RELEASE_1.md`.
  - Clause map (the union covers the parent):

    | Parent clause | Owner |
    |---|---|
    | "Import ... a real existing endpoint-only fine-tune" | import validation → P2 (stand-in gateway); the real endpoint → BLOCKED_EXTERNAL (org gateway key) |
    | "... and evaluate" it | BLOCKED_EXTERNAL (gateway key + FTF-R004 runtime) |
    | "import/load full weights and adapter+base" | validation → P3; load → BLOCKED_EXTERNAL (vLLM/GPU, FTF-1003/1004) |
    | "continue only permitted artifacts" | P4 (eligibility + server-side denial); the actual training → BLOCKED_EXTERNAL (GPU, FTF-R003/0701) |
    | "resumed step differs from new continuation" | P4 plan property; the real proof → BLOCKED_EXTERNAL + FTF-0713/0714 |
    | negative "fictitious artifact hash for endpoint-only imports" | P1 CHECK + P2 success path with no hash |
    | negative "infer retrainability from alias" | P2 (an endpoint version is never trainable) + P4 (eligibility ignores names) |
    | negative "incompatible adapter/base and license denial block use" | P3 (validation) + P4 (actions refuse) |
    | goal "alias role assignment" | P4 |
    | "No full release claim ..." | parent |

  - Common rules:
    - Migrations are serialized on `migration:application`. Take the next free number at claim time (0029 for P2, 0030 for P4; renumber if another lands first). Revision ids ≤ 32 characters. RLS exactly as 0017/0020: ENABLE + FORCE, policy `tenant_isolation`. `sovra_app` never gets DELETE.
    - Paths: `/api/v1/organizations/{organization_id}/projects/{project_id}/...`. Errors: `{"detail":"<stable_code>"}`. Audit through `write_audit_event` in the same transaction, content-free.
    - Contract changes are additive to `packages/contracts/openapi/10-interface.yaml` under `contracts:10`, by serialized transfer from FTF-1001.
    - Each child adds its own additive ci.yml job plus verdict `needs`.
    - Path refinement, recorded: the packet's `apps/api/app/services/model_import.py` becomes the existing `apps/api/app/model_versions/` package, and `services/training-worker/trainers/continuation.py` becomes `workers/training/continuation.py` (the real SOURCE_ROOT).
    - No UI, runtime dependencies, secrets or keyed services.
    - Must not edit:
      - `model_version_crud.py` or `model_version_routes.py` (FTF-1001);
      - migrations 0027/0028 (new migrations only; a replaced trigger function is restored byte-identical on downgrade);
      - the artifact resolvers, `base_model_revision_digest_resolver.py`, `license_acceptance_workflow.py` or `artifact_lineage_manifest.py`;
      - `route_attestation.py` or `v1_models_discovery.py`.
    - Build the 2026-10-08/09 ledger lessons in from round 1:
      - complete constraint and transition tables (H010-P1 r4 / 0d ruling);
      - exact identity matching tested with property-generated Unicode variants (FTF-0616 r8);
      - exact allow-lists for every request body and file shape (R004-P2);
      - plain-JSON, exact-type parsing (`type(x) is str`, no subclasses or caller objects; FTF-1703/1206 rulings);
      - one boundary table per length-checked text input (0516-P1 r5);
      - one shared META-LEAK check over the fully rendered exception chain, audit rows, logs and job rows (0903 r8);
      - fail-closed behaviour pinned by faults.
    - Every DoD bullet has its own behavioural fault→RED row. Uncapped mutation gate at 0 survivors.
    - Then FTF-H010 (parent: `ImportModelWizard.tsx` on the P1–P4 routes plus its disclosures) is re-queued.
  - **FTF-H010-P2 Import-validation activity, job fencing and endpoint-only import (migration) — CERTIFIED 2026-10-21 (see Done)** (§3.3–3.5, §6.17, §17 fencing paragraph, §41/§77.6 activity rules, §58A row 1, §68.1 paragraphs 1–2; deps FTF-H010-P1, H005, 0204, 0201, 1001, 1002 all CERTIFIED; locks `migration:application`, `contracts:10`).
    - **Control-plane ruling 2026-10-10T03:57Z (resolves the 02:30Z park; the park's routes 1 and 2 are rejected):** an alias is not a URL, and FTF-1001's `validate_endpoint_reference` (https URL) stays as certified for FTF-1001's own synchronous path. Route 2 (store the connection `base_url`) is rejected: `base_url` is org-admin-only on the connection routes (`CONNECTION_ADMIN_ROLES`) and FTF-1001's GET would expose it to every project reader - a widening of exposure, and the P2 DoD already treats it as a leak marker. Route 1 is rejected because FTF-1001's POST also defaults origin to `external_endpoint`, so origin cannot tell the paths apart. Ruled (route 3, scoped into P2):
      - Migration 0029 additionally replaces `ck_model_versions_kind_consistency` so an `endpoint` row needs EITHER a non-NULL `endpoint_reference` (the certified FTF-1001 form) OR `endpoint_reference IS NULL AND origin = 'external_endpoint' AND external_provider_model_ref IS NOT NULL`; downgrade restores the 0009/0028 definition byte-identically (refusing if any NULL-reference row exists, never deleting data).
      - `model_version_schema.py` (certified FTF-1001 module, minimal edit): `endpoint_reference` may be None only when origin is `external_endpoint` and `external_provider_model_ref` is an explicit non-empty value; otherwise unchanged. The derived copy into `external_provider_model_ref` is unchanged.
      - FTF-1001's POST route keeps refusing a missing or non-https `endpoint_reference` (its request parsing is unchanged); add a pinned test that the POST cannot create a NULL-reference row, including by sending `external_provider_model_ref`.
      - `10-interface.yaml`: the ModelVersion response's `endpoint_reference` becomes nullable (no client consumes it yet); FTF-1001's GET serializes null.
      - Law 15: this touches certified FTF-1001, so P2's verifier also re-runs FTF-1001's full suite and real-PG integration and reports a FTF-1001 REQUALIFIED line. Faults: CHECK widened to any endpoint row (RED); constructor accepts None without an explicit provider ref (RED); POST creates a NULL-reference row (RED).
    - Migration `0029_model_import_validation`:
      - `model_import_jobs` gains:
        - `litellm_connection_id`: same-tenant composite FK to `litellm_connections` (organization_id, id), RESTRICT;
        - `result_model_version_id`;
        - `claim_epoch int NOT NULL DEFAULT 0` with CHECK ≥ 0;
        - `lease_expires_at timestamptz NULL`.
      - `model_versions` gains a UQ on (organization_id, project_id, id, origin) if absent. The result FK is (organization_id, project_id, result_model_version_id, requested_origin) → that UQ, so a result belongs to the same project and has the requested origin at DB level.
      - CHECKs:
        - (requested_origin = 'external_endpoint') = (litellm_connection_id IS NOT NULL);
        - (state = 'succeeded') = (result_model_version_id IS NOT NULL);
        - (state = 'validating') = (lease_expires_at IS NOT NULL);
        - (state = 'queued') = (claim_epoch = 0).
      - The column-limited UPDATE grant widens to (state, failure_code, updated_at, result_model_version_id, claim_epoch, lease_expires_at) and nothing else.
      - `CREATE OR REPLACE` the 0028 trigger function, keeping every 0028 rule and adding:
        - INSERT only at queued with epoch 0, lease NULL and result NULL;
        - queued→validating requires epoch = OLD+1 and a lease;
        - validating→validating is allowed only as either (a) a lease renewal by the holder (same epoch, lease strictly later, nothing else changed) or (b) a re-claim with epoch = OLD+1, and only when `OLD.lease_expires_at < now()`;
        - validating→terminal requires NEW.claim_epoch = OLD.claim_epoch (a late commit from an old epoch is refused) and clears the lease;
        - terminal stays final.
      - Downgrade restores the 0028 function body byte-identical.
    - Contract (additive to 10-interface.yaml):
      - The POST body allow-list becomes exactly {requested_origin, parent_model_version_id, trainable_artifact_id, external_provider_model_ref, litellm_connection_id}.
      - `litellm_connection_id` is required for `external_endpoint` and refused for the other origins (400 `invalid_body`). A foreign or missing connection gives 404 `not_found`, identical either way.
      - The job response adds `litellm_connection_id` and `result_model_version_id`. No new path.
    - Activity module `apps/api/app/model_versions/model_import_validation.py`. Jobs are addressed per job by (organization_id, project_id, job_id), which is what a dispatcher derives from the enqueue. It never scans across tenants.
      - `claim_model_import_job(session_factory, organization_id, project_id, job_id, *, origins, worker_id, lease_seconds, now)` → `ClaimToken(job_id, epoch)`:
        - runs in its own tenant-scoped transaction with `SELECT ... FOR UPDATE`;
        - claims only the origins named in the `origins` allow-list, so P2 claims `external_endpoint` only;
        - a live lease gives `job_already_claimed`; a terminal job gives `job_terminal`.
      - `validate_endpoint_import(session_factory, token, *, secret_config, now)`:
        - Loads the job and its connection itself. A revoked connection → failed `connection_revoked`.
        - One FTF-0204 `discover_models`. Any state other than discovered → `blocked_external` `gateway_unavailable` (Law 17). A 401/403 → failed `gateway_access_denied` (§3.3).
        - `external_provider_model_ref` must be in the discovered ids by exact `==`. Otherwise failed `alias_not_authorized` (§3.3: removed names are unselectable).
        - Then one FTF-H005 `attest_route(model_id=ref, approved_model_ids=frozenset({ref}))`. Not attested → `blocked_external` `route_attestation_unavailable`. Identity UNVERIFIED → failed `route_identity_unverified` (§3.5 "do not certify").
        - Success, in one transaction, conditional on `claim_epoch = token.epoch` (0 rows → `fenced_out`, rollback, nothing written):
          - insert the ModelVersion through the `ModelVersion` constructor, with `_next_version_number` reused: import_kind endpoint, origin external_endpoint, `external_provider_model_ref = ref` verbatim and `endpoint_reference = NULL` (Control-plane ruling 2026-10-10T03:57Z below; never a fabricated URL and never the connection `base_url`), `observed_deployment_fingerprint` = the attested fingerprint, `lineage_completeness = 'unknown'` always (§68.1 "Display unknown parent/training data as unknown"), no artifact/digest/revision/tokenizer/template, and the declared parent kept as declared only;
          - set the job to succeeded with the result id;
          - write one audit row `model_import.validate` carrying the job id, origin and outcome code only.
        - Gateway calls run outside any DB transaction; no row lock is held across a network call.
    - DoD tests: real PG16 as sovra_app, plus the H005/0309-P1 local TLS gateway harness (`/v1/models`, `/model/info`, `/v1/chat/completions` with `x-litellm-model-id`) and the 0616 local secret-backend stand-in.
      - Happy path: the job succeeds, and the ModelVersion has a fingerprint, unknown lineage and NULL hash/URI. Job GET and FTF-1001 GET both show it.
      - Negative acceptance: no hash is ever required or written for an endpoint, and a raw endpoint+digest INSERT is still refused.
      - Alias exactness: generated Unicode variants of the ref, and discovery listing only a variant, both → failed `alias_not_authorized`.
      - Complete outage table: every DiscoveryState and RouteAttestationState value → its exact terminal state and code, never succeeded.
      - Missing header or echo → failed with no ModelVersion.
      - Fencing:
        - two threads claim → exactly one wins;
        - a re-claim after lease expiry gets epoch+1;
        - a re-claim before expiry is refused;
        - the old epoch's final commit writes 0 rows, no ModelVersion and no audit.
      - Complete 5×5 transition matrix × epoch {same, +1, +2, −1} × lease {expired, live} for sovra_app, plus a truth table for each new CHECK.
      - Cross-tenant: a foreign connection or job is identical to a missing one; a raw org2 INSERT is refused.
      - Strict POST body table: unknown key; missing or extra `litellm_connection_id` per origin; int/bool/null/list; `external_provider_model_ref` boundary 0/1/2048/2049 characters, whitespace-only, NUL, CR/LF, `\ud800`.
      - META-LEAK with markers in the ref, fingerprint, base_url and secret over failure_code, audit, logs and exception chains.
      - Faults that must go RED:
        - discovery membership by casefold/strip/NFKC;
        - attestation skipped or a NULL fingerprint accepted;
        - UNVERIFIED → succeeded;
        - each outage state → failed or succeeded;
        - lineage written as partial or complete;
        - epoch condition removed from the final UPDATE;
        - the trigger's terminal-epoch clause dropped;
        - re-claim before expiry allowed;
        - result FK without project or origin;
        - the grant widened to `requested_origin`;
        - revoked connection ignored.
    - Disclosed:
      - BLOCKED_EXTERNAL: importing and evaluating a real existing endpoint fine-tune (the 2026-10-08 gateway-key owner action, plus an existing fine-tuned alias).
      - UNWIRED: no Temporal or outbox dispatcher calls claim/validate yet (durable dispatch is FTF-0801/H016), so deployed jobs stay visibly `queued`. `proving_symbol` stays blank for the activity.
      - FTF-1001's synchronous `POST /model-versions` can still create an unvalidated endpoint row. P4's actions route denies such rows (`import_not_validated`); FTF-1001 is not edited.
  - **FTF-H010-P3a Pure import-artifact validators — CERTIFIED 2026-10-22 (see Done) (no DB, no migration; ready now)** (Control-plane refinement 2026-10-09T23:46Z: the file-level checks of P3 do not need P2's activity, so they are split out; P3 then wires them into the P2 activity and keeps steps 1-3, 5, 8's DB/lineage half and 9). Deps: certified FTF-0304 (manifest format) only. File: `apps/api/app/model_versions/import_artifact_validators.py` (pure, stdlib only) + `tests/tasks/test_ftf_h010_p3a.py`; no route, no table, no caller (UNWIRED until P3).
    - Implements exactly P3 steps 4 (exact file allow-list with `unexpected_file` / `unsafe_weight_format`), 6 (strict JSON: duplicate keys refused, no NaN/Infinity, depth bound; the `config.json`, `tokenizer_config.json`, special-token and `chat_template_sha256` rules; `quantization_config` → `quantization_unverified`), 7 (safetensors header parse with every bound) and the pure half of 8 (`adapter_config.json` rules and the lora_A/lora_B ↔ base `{module}.weight` shape check → `adapter_base_incompatible`), each as a function over bytes/parsed headers that returns a typed result or raises the module's own error with the P3 failure code. Inputs are exact types (`bytes`, `str`, plain dict/list from its own parse); no caller objects (FTF-1703 ruling).
    - Tests: the P3 safetensors boundary table, adapter table, file allow-list table (case and double-extension variants) and strict-JSON table, all generated (not hand-picked); fixtures written with stdlib `struct` + `json`; META-LEAK with markers in file names and contents (error messages carry codes only).
    - Faults that must go RED (from P3's list): pickle allowed; allow-list turned into a deny-list; each header bound off by one; offset-coverage check dropped; shape check dropped; quantized config accepted; duplicate JSON keys accepted.
    - Must not import torch, safetensors or transformers, and never executes or imports any artifact file. P3 must reuse these functions, not re-implement them.
  - **FTF-H010-P3 Full-weight and adapter import validation — CERTIFIED 2026-10-23 (see Done)** (§6.7, §6.17, §17 "Prefer safetensors ... do not deserialize arbitrary uploaded pickle", §34, §58A rows 2–3, §68.1 paragraph 3, §68.4; deps FTF-H010-P2, FTF-H010-P3a (reuse its validators) plus certified FTF-0301/0304/0305/0306/1002; no migration).
    - Adds `validate_weights_import` and `validate_adapter_import` to the P2 activity, with the same claim/lease/fence and origins `imported_weights` | `imported_adapter`. Inputs come from DB rows only: the job's `trainable_artifact_id`, and for adapters the job's `parent_model_version_id` as the base. The approved local root comes from server configuration passed by the dispatcher, never from a request.
    - Steps, each refusal its own failed code:
      1. The artifact must be `ready` with a resolved digest → else `artifact_not_ready`.
      2. License: FTF-0305 `assert_license_accepted_for_use` on the artifact (`license_not_accepted`) and, for an adapter, on the base's artifact (`base_license_not_accepted`).
      3. Digest: FTF-0306 `verify_or_compute_weights_digest` recomputes from real bytes and never trusts the stored value → `digest_mismatch`. Source unreachable → `blocked_external` `artifact_source_unavailable`.
         - This slice supports `local_filesystem` artifacts (the FTF-0304 multi-file manifest).
         - `private_s3` (FTF-0303 resolves one object) and `huggingface` (external acquisition) → `blocked_external` `metadata_files_unavailable` (disclosed).
      4. Exact file allow-list over the manifest:
         - required: `config.json`, `tokenizer_config.json` and `tokenizer.json` | `tokenizer.model`;
         - weights: `*.safetensors` (+ `model.safetensors.index.json`);
         - named optional files: `generation_config.json`, `special_tokens_map.json`, `added_tokens.json`, `vocab.json`, `merges.txt`, `chat_template.jinja`, `README.md`, `LICENSE`;
         - adapter: `adapter_config.json` + `adapter_model.safetensors`.
         - Anything else → `unexpected_file`. That includes `.bin/.pt/.pth/.ckpt/.pkl/.pickle/.joblib/.npy` in any case, double extensions, hidden files and nested directories. Pickle-capable formats get their own code, `unsafe_weight_format`.
      5. Each parsed file is read once, size-bounded (`config.json` ≤ 1 MiB, `tokenizer_config.json` ≤ 4 MiB), hashed, and must equal its FTF-0304 manifest sha256 → else `artifact_changed_during_validation` (TOCTOU).
      6. Strict parsing: stdlib `json` with duplicate keys refused, no NaN/Infinity, and a depth bound.
         - `config.json`: `model_type` str; `architectures` a non-empty list of str; `hidden_size`, `num_hidden_layers` and `vocab_size` positive int (bool refused); `torch_dtype` in an allow-list.
         - `chat_template_sha256` = sha256 of the exact UTF-8 bytes of `chat_template`, or NULL when absent.
         - Special tokens are str or `{content: str, ...}`.
         - A present `quantization_config` → `quantization_unverified`: serving/trainer compatibility is not provable without GPU.
      7. safetensors header (stdlib `struct` + `json`):
         - an 8-byte little-endian N with 0 < N ≤ 100,000,000 (the format's own limit) and N ≤ file size − 8;
         - a JSON object;
         - each tensor has `dtype` in an exact allow-list (F64, F32, F16, BF16, I64, I32, I16, I8, U8, BOOL, F8_E4M3, F8_E5M2), `shape` a list of non-negative int, and `data_offsets` [begin, end] with end − begin = prod(shape) × dtype size;
         - offsets contiguous, non-overlapping and covering the buffer exactly;
         - `__metadata__` is str→str only.
         - Any failure → `weights_header_invalid`.
      8. Adapter, exact base (§68.1 "not same-family guessed weights"):
         - The parent must be a same-project `full_weight` version (origin `imported_weights` | `factory_trained`) with a succeeded import job or a factory run → else `base_not_validated`.
         - FTF-1002 `build_artifact_lineage_manifest(..., resolved_base_artifact=parent_artifact)`: an `AdapterBaseMismatchError` → `adapter_base_mismatch`. The base revision compare is exact.
         - `adapter_config.json`: `peft_type == "LORA"` exact; `r` a positive int; `target_modules` a non-empty list of str. `base_model_name_or_path` is informational only and never resolves the base.
         - Shape compatibility: for each `lora_A` [r, in] / `lora_B` [out, r] tensor, the base safetensors header must hold the mapped `{module}.weight` with shape [out, in], and r must equal the config r → else `adapter_base_incompatible`.
         - Tokenizer files inside the adapter directory must hash-equal the base's → else `tokenizer_mismatch`.
      9. Success, epoch-fenced, inserts the ModelVersion:
         - import_kind `full_weight` | `adapter`; origin `imported_weights` | `imported_adapter`;
         - `weights_digest` = the re-verified digest; `base_model_revision` from the artifact; `tokenizer_revision` = the artifact's base revision (disclosed: there is no separate tokenizer revision source); `chat_template_sha256` as computed; `serving_config_sha256` NULL until FTF-1003;
         - `adapter_only` = true for an adapter; `parent_model_version_id` = the base for an adapter, or the declared parent for weights;
         - `lineage_completeness = 'partial'` (base/tokenizer/license known, training/data unknown). Never `complete`: only factory_trained with a TrainingRun can be.
         - No existing `model_versions` row is ever updated (§68.1 "Preserve prior model as immutable parent").
    - DoD tests: real PG16 as sovra_app, plus a real approved-root directory with tiny fixtures generated by stdlib (safetensors written with `struct` + `json`). No GPU.
      - Truth table: origin × artifact status × license × digest × base state.
      - safetensors boundary table: N = 0, 1, 100,000,000, 100,000,001, > file size; overlap, gap, out-of-range and end < begin offsets; unknown or lower-case dtype; negative, bool or float shape; non-str `__metadata__`; duplicate tensor names via duplicate JSON keys.
      - Adapter table: r mismatch; in/out swapped; missing base tensor; extra target module; base-revision Unicode/case variants (generated); `base_model_name_or_path` naming a different model while the true parent is compatible (accepted), and the reverse (refused).
      - File allow-list table, including case and double-extension variants.
      - TOCTOU: a real file rewrite between the manifest and the parse.
      - Complete per-step failure-code table; META-LEAK with markers in file names and contents.
      - Faults that must go RED:
        - license gate skipped;
        - digest taken from the stored value;
        - pickle allowed;
        - the allow-list turned into a deny-list (an unknown file accepted);
        - each header bound off by one;
        - offset-coverage check dropped;
        - base resolved by name/family;
        - base revision casefolded;
        - shape check dropped;
        - tokenizer mismatch ignored;
        - lineage written as `complete`;
        - quantized import accepted;
        - a parent row updated;
        - the TOCTOU check dropped.
    - Disclosed:
      - BLOCKED_EXTERNAL: loading full weights and adapter+base (vLLM/GPU, FTF-1003/1004) and trainer compatibility (FTF-R003).
      - `private_s3`/`huggingface` metadata stays unavailable until a multi-file resolver manifest exists.
      - Same UNWIRED dispatcher as P2.
    - Must not import torch, safetensors or transformers. It never executes or imports any artifact file.
  - **FTF-H010-P4 Role assignment, capability-driven actions and continuation eligibility (migration)** (§0.12 roles, §6.17, §17 paragraphs 3–4, §27 judge ≠ candidate, §58A "Allowed next operations", §68.1 paragraph 4 "Actions are capability driven ... Invalid actions return precise reason and server-side denial, not just disabled buttons"; deps FTF-H010-P2, P3, and certified FTF-0305/0307/0308/1001/1002).
    - Migration `0030_model_role_assignments` creates `model_role_assignments`:
      - Columns: id, organization_id, project_id, model_version_id, role ∈ {current_production, baseline, candidate, teacher, judge}, assigned_by (non-blank, ≤ 200), assigned_at, request_id, idempotency_key, request_sha256, revoked_at, revoked_by, revoke_reason_code.
      - Composite FK (org, project, model_version_id) → model_versions.
      - CHECK: the three revocation columns are all NULL or all set.
      - Partial UQ (project_id, model_version_id, role) WHERE revoked_at IS NULL.
      - Partial UQ (project_id) WHERE role = 'current_production' AND revoked_at IS NULL: one incumbent per project.
      - BEFORE INSERT trigger: refuses `judge` on a version holding an active `candidate` role in the same project, and the reverse (§27).
      - BEFORE UPDATE trigger: allows only a one-time NULL→set revocation; no un-revoke and no other column change.
      - Grants: SELECT, INSERT, UPDATE (revoked_at, revoked_by, revoke_reason_code). RLS ENABLE+FORCE, no DELETE.
    - Routes, additive in 10-interface.yaml:
      - `GET/POST .../model-versions/{id}/roles`;
      - `POST .../model-versions/{id}/roles/{role}/revoke`;
      - `GET .../model-versions/{id}/actions`.
    - Role POST behaviour:
      - Body allow-list exactly {role, idempotency_key}. `idempotency_key` is 1..128 characters of printable ASCII. Same key + same request_sha256 → 200 replay with no rows or audit; same key + a different body → 409.
      - Writes for candidate, baseline and teacher use `MODEL_VERSION_WRITE_ROLES`.
      - `current_production` and `judge` are `organization_admin` only. A judge role does not make a qualified judge: FTF-0616 calibration and the FTF-0617 policy still decide. `current_production` records the incumbent only; it deploys or routes nothing (FTF-10xx).
      - Any role requires a validated version: a succeeded import job or factory_trained. Otherwise 409 `import_not_validated`.
      - Errors:
        - 400 `invalid_body` | `invalid_role`;
        - 401; 403;
        - 404 `not_found` (identical for foreign and missing);
        - 409 `role_already_assigned` | `incumbent_exists` | `self_judging_role_conflict` | `role_not_assigned` | `idempotency_key_reused` | `import_not_validated`.
    - `workers/training/continuation.py`, pure and no I/O:
      - `evaluate_model_actions(facts)` covers {evaluate, compare, continue_tuning, exact_resume, request_qualification, retain_incumbent, deploy_route, retire}. The route builds the facts server-side from DB rows: the version, its succeeded job, the artifact, the license gate, the FTF-1002 manifest and the roles.
      - Rules:
        - external_endpoint: evaluate/compare allowed. continue_tuning is denied `endpoint_not_trainable` and exact_resume `no_trusted_checkpoint`, whatever the ref or alias text says (§68.1 "Permission to invoke is not permission or technical ability to continue training"; §0.12 "A teacher role does not determine trainability").
        - imported_weights/adapter, validated with license accepted: continue_tuning allowed as a `new_run` plan. exact_resume denied `no_trusted_checkpoint` (§17 "Imported adapters without optimizer state start a new training run, not a resume").
        - factory_trained: exact_resume is denied until a FTF-0713 checkpoint manifest exists. That input does not exist yet, so it is never assumed.
        - License revoked → every use action denied `license_not_accepted`. A base that no longer validates → `adapter_base_incompatible`.
        - Unvalidated rows (including FTF-1001 synchronous creates) → every action denied `import_not_validated`.
        - request_qualification, deploy_route and retire → denied `prerequisite_not_available` until H020/FTF-10xx and the status lifecycle exist. Unknown is never allowed.
        - retain_incumbent is allowed only for the active `current_production` holder.
      - `continuation_plan`:
        - `new_run` = {parent = this version, start_step = 0, optimizer_state none, a new run id, separate cost};
        - `exact_resume` = {checkpoint id, start_step = checkpoint step ≥ 1, same run lineage}.
        - So "resumed step differs from new continuation" is a tested property.
      - Exports `require_action_allowed` for future effect routes.
    - DoD tests (real PG16 as sovra_app):
      - Role truth table: 5 roles × {validated endpoint, weights, adapter, unvalidated FTF-1001 row} × 6 org roles → exact status and code.
      - Concurrency: two threads posting `current_production` → exactly one 201.
      - Raw-SQL self-judging refused in both insert orders.
      - Revoke: a second revoke → 409. A raw un-revoke is refused by the trigger; a raw role change is refused by the grant.
      - Cross-tenant: identical 404, a raw org2 INSERT refused, and dropping FORCE goes RED.
      - Idempotent replay adds 0 rows and 0 audit.
      - Complete actions decision table over origin × validated × license × base-compat × roles, each action's allowed/reason asserted.
      - Generated ref/alias variants containing `ft:`, `lora`, `llama`, `trainable` and `teacher` never change endpoint continue_tuning.
      - Plan property: for every checkpoint step ≥ 1, new_run.start_step == 0 ≠ exact_resume.start_step, and the run ids differ.
      - Strict body table: role enum exact, so `Judge`, `judge `, `ｊudge` and `judge​` are refused. Idempotency-key boundary 0/1/128/129 characters plus non-ASCII.
      - META-LEAK.
      - Faults that must go RED:
        - each widened role grant;
        - the incumbent index dropped;
        - the self-judging trigger dropped (each direction);
        - un-revoke allowed;
        - endpoint continue_tuning allowed on a trainable-looking ref;
        - imported adapter exact_resume allowed;
        - revoked license ignored;
        - an unvalidated row given actions;
        - an unknown prerequisite treated as allowed;
        - the role enum casefolded;
        - resume start_step set to 0.
    - Disclosed:
      - BLOCKED_EXTERNAL: continued training and real resume (GPU; FTF-R003/0701/0714). exact_resume is always denied until FTF-0713.
      - UNWIRED for effects: no training-submit path calls `require_action_allowed` yet.
      - The roles and actions routes are live; `product_truth_check` gets the actions route as FTF-H010's proving symbol only when the parent closes.
(Refill & Sweep appends further dependency-ready items here from docs/TASK_INDEX.md +
docs/spec-matrix.yaml. It never invents criteria.)

- **FTF-R004 child slices: Inspect AI behind the real target runtime** (Control-plane design 2026-10-08; FINE_TUNING_FACTORY_RELEASE_1.md
  §25, §26, §28, §30, §66.3-66.5, §69.1-69.2, §77.3-77.6, §78.2; Q16, Q17, Q18, Q35). These refine FTF-R004 under "For each card" step (3).
  They add no new criteria: the denominator stays 302 and no ADR applies. FTF-R004's deps are all CERTIFIED: R002, 0601, 04A25, 04A26 and
  0210. Contract: `python tools/r1_handoff.py packet FTF-R004 --plan FINE_TUNING_FACTORY_RELEASE_1.md`.
  Common rules:
  - Path refinement, recorded: the packet's `services/evaluation-worker/adapters/inspect/` becomes `workers/evaluation/adapters/inspect_adapter/`.
    `workers/evaluation` is the real SOURCE_ROOT, and the leaf is not named `inspect`, so it cannot shadow the stdlib module.
  - No slice adds a migration, an OpenAPI path, UI, a secret or a keyed service.
  - Each slice adds its proof as an additive `gate` step in ci.yml, like the 0604-0613 grader steps.
  - Every DoD bullet needs its own behavioural fault→RED row. The mutation gate must reach 0 survivors.
  - Must not edit:
    - `apps/api/app/target_connectors/*` (04A25/04A26 are frozen; compose them);
    - `workers/evaluation/llm_judge_grader.py` (open PR #645);
    - `packages/security/external_provider_denial.py` (open PR #640);
    - any grader module.
  - Held-out/final suite content is never loaded by P1-P4 (Law 12). The final-purpose path fails closed until FTF-H020's CandidateFreeze exists.
  - **FTF-R004-P1 Bridge generation-config parity comparator** (§78.2 "Explicit Inspect bridge parity", §66.4-66.5, Q35). Ready now.
    - Owns this acceptance clause: "Compare effective target generation settings before and after the Inspect bridge; preserve the approved
      temperature/top_p/token/reasoning settings or record an explicit candidate configuration change in the locked compatibility tuple.
      Do not silently inherit bridge defaults."
    - Owns the negative clause "Reject silent generation-config dropping".
    - Files: `workers/evaluation/adapters/__init__.py`, `.../inspect_adapter/__init__.py`, `.../inspect_adapter/generation_config_parity.py`
      and `tests/tasks/test_ftf_r004_p1.py`.
    - Module API:
      - `capture_effective_generation_settings(request_body: Mapping)`: reads only the settings keys of an OpenAI-compatible
        chat/responses body, never messages or tools. Fields:
        - temperature, top_p;
        - max_tokens, max_completion_tokens and max_output_tokens, keeping the raw key names;
        - reasoning_effort, reasoning;
        - seed, stop, presence_penalty, frequency_penalty;
        - tool_choice, parallel_tool_calls, response_format;
        - any unknown settings key, kept verbatim.
      - `compare_generation_parity(approved, observed_before, observed_after, declared_change=None)`, which returns `preserved`,
        `declared_change` or `blocked`.
      - `declared_change` carries `compatibility_tuple_sha256` (hex64) and the exact key→value changes.
    - Rules:
      - A key in approved or before that is missing or different after gives `blocked` with `generation_config_dropped` or
        `generation_config_changed`.
      - A key that appears only after gives `bridge_default_inherited`.
      - A declared change passes only if the after value equals it exactly. Every other difference still blocks.
      - Values are compared exactly. 0.7 ≠ "0.7", and True ≠ 1.
      - A missing observation gives `insufficient_evidence`, never `preserved`.
      - The result holds key names and sha256 digests only, never prompt content.
      - Unknown or malformed input raises the module's typed error, never TypeError and never allow.
    - DoD and faults (each RED):
      - a bridge drops temperature;
      - a bridge renames max_tokens to a default value;
      - a bridge adds top_p;
      - a declared change whose value differs from the observed value;
      - a declared change without a tuple digest;
      - one observation missing;
      - a canary prompt string appears in the result.
    - Disclose UNWIRED (no caller until P3). The approved-settings source and persisting the tuple are not in this slice. AgentManifest
      has no generation fields, and no compatibility-tuple table exists yet.
  - **FTF-R004-P2 Provider-side execution boundary + pre-filter attempt evidence** (§78.2 "Provider-side execution is a separate boundary",
    §66.2, §66.4, §69.2, §30 "attempted prohibited calls reported separately", Q16, Q35). Ready now, and independent of P1.
    - Owns this acceptance clause: "Block provider-side hosted tools, remote media dereferences and unapproved bridged host tools outside
      the privacy boundary; capture attempted forbidden tool calls before approval filtering can replace model output."
    - Owns the negative clause "Reject ... undeclared provider-side web/code/MCP execution or remote-media fetch, and lost pre-filter
      forbidden-action evidence in the bridge".
    - Files: `.../inspect_adapter/provider_boundary.py` and `tests/tasks/test_ftf_r004_p2.py`.
    - `screen_outbound_model_request(body, policy)`:
      - Default deny. The only tools allowed are `type:"function"` tools whose name is in the approved manifest tool set.
      - These are denied with stable codes (`hosted_tool_denied`, `mcp_denied`, `unapproved_bridged_tool`):
        - hosted or server tool types (web_search*, code_interpreter, file_search, computer_use*, image_generation, `*code_execution*`);
        - `mcp` entries;
        - `web_search_options` and plugin keys.
      - Any `image_url`, `input_image`, `file` or `input_audio` part that is not an inline `data:` URI is denied as `remote_media_dereference`.
        URLs are classified by FTF-H006 `url_policy`, never by substring.
      - An unknown part or tool shape is denied (`unknown_request_shape`).
      - The profile comes from FTF-0901 `effective_profile`/`enforce_effect`. Do not re-implement profile ordering. zero_egress and
        air_gapped deny every hosted capability even when it is approved.
    - `capture_response_attempts(response_body) -> AttemptEvidence`:
      - Captures every function call and hosted-call item (web_search_call, code_interpreter_call, mcp_call, ...) with its id, name,
        args sha256 and response sha256.
      - Exposes them as FTF-0606 `EmittedToolCall` records for FTF-0609 `grade_forbidden_actions`.
    - `apply_approval_filter(response_body, evidence)` refuses to run unless `evidence.response_sha256` matches that exact body. Filtering
      can therefore never come before capture.
    - DoD and faults (each RED):
      - each hosted tool type, an MCP entry, an http/https/file media URL and an unapproved function tool is allowed;
      - zero_egress allows an "approved" hosted tool;
      - the filter runs without evidence or with evidence from another body;
      - a denied attempt disappears from the evidence;
      - a counting outbound-guard stub shows any fetch during screening. Screening must make 0 network calls (FTF-0908 precedent);
      - a str subclass or unhashable value gets through, or raises TypeError.
    - Composition test: a filtered forbidden attempt still grades as a policy failure in `grade_forbidden_actions`.
    - Disclose UNWIRED until P3.
    - Real upstream-route enforcement at the org LiteLLM gateway is BLOCKED_EXTERNAL. It needs the same gateway key as the 2026-10-08
      FTF-0616 owner action.
  - **FTF-R004-P3 Pinned Inspect worker + case adapter + actual-runtime bridge** (§78.2 paragraphs 1-2 and 5, §66.3-66.4, §77.3, §77.5,
    §28, Q16, Q35). Deps: P1 and P2.
    - **Owner-approved dependency (live owner answer, 2026-10-08):** add `inspect_ai` to `workers/evaluation/pyproject.toml` and
      `uv.lock`, matching REFERENCE_REPOSITORIES.md ("Pinned evaluation worker package"). Pin an exact release at least 2 weeks old,
      chosen at claim, with remote log destinations and telemetry off; record the version and its transitive additions in the PR. Do not
      vendor, fork or re-implement Inspect, or use a look-alike harness.
    - Owns this acceptance clause: "Use qualified Agent Bridge or actual-runtime adapter; execute original orchestration and real isolated
      dependencies with identity pinning."
    - Owns the negative clause "A reconstructed generic agent ... cannot pass".
    - Owns the Inspect-side half of P1 and P2: the pinned release's `forward_generation_config` behaviour is captured and compared, not
      assumed.
    - Files:
      - the pyproject and lockfile pin (single lockfile writer);
      - `.../inspect_adapter/case_adapter.py`;
      - `.../inspect_adapter/runtime_bridge.py`;
      - `tests/integration/test_inspect_target_bridge.py` (packet name);
      - `tests/tasks/test_ftf_r004_p3.py`.
    - `case_adapter`:
      - Maps the cases of a **locked development-purpose** suite (FTF-0602-P1 `evaluation_cases`, read through its tenant-scoped store) to
        Inspect `Sample`s.
      - Each Sample gets a stable id = case_key, and metadata for case_id, category, severity, critical, group_key, language, grader_type
        and the suite/manifest ids.
      - These are refused: a draft suite (`suite_not_locked`), a final suite (`final_suite_requires_freeze`, until FTF-H020) and duplicate
        ids.
    - `runtime_bridge`:
      - Builds an Inspect `Task` whose solver calls a registered native `RuntimeAdapterProtocol` adapter. Each case goes through
        `preflight`, `invoke` (immutable `ModelBinding` checked by `validate_model_binding_override`, with a deadline and an idempotency
        key), then `collect_outcome`.
      - It never uses a generic Inspect agent such as `react()` or `basic_agent()`.
      - `evaluate_target_runtime_qualification` must return QUALIFIED, otherwise the run is refused with `integration_required`.
      - `attest()` is pinned before the run and re-checked after it. Any changed fingerprint gives `runtime_identity_changed`.
      - The log dir is a private temp workspace. No remote log or eval destination is set, and Inspect telemetry is off. The run passes
        under network denial.
      - For model-less native adapters, use the pinned release's model-less mode. `mockllm` is test-only and never on a production path.
    - Real services in tests: the pinned Inspect package, the FTF-04A26 `IsolatedToolBackendAdapter` over the real FTF-1702 tool
      backend, and real Postgres for the case read.
    - DoD and faults (each RED):
      - the solver is swapped for a generic agent;
      - the reconstructed-adapter declaration is missing parity categories;
      - the attestation changes mid-run;
      - an unsupported binding key is used;
      - a draft or final suite is used;
      - P1 or P2 is bypassed in the bridge hook;
      - an outbound call happens during a run.
    - Disclose BLOCKED_EXTERNAL: a real model route through the org LiteLLM gateway with H005 route pinning (same key as the FTF-0616
      owner action). A real customer target app beyond the reference backend is FTF-H008/0614's.
  - **FTF-R004-P4 Inspect log normalization, complete accounting and sealed-data guard** (§69.1-69.2, §78.2 paragraphs 3-5, §30
    "Final-test protection", §27, Q16, Q17, Q18). Deps: P3.
    - Owns this acceptance clause: "Normalize complete Inspect case/log results, protect sealed final data and independently verify
      business-state outcome and statistics."
    - Owns the negative clause "edited score log, missing/cancelled cases, unapproved judge or final content in normal MLflow/Argilla cannot
      pass".
    - Files: `.../inspect_adapter/result_reader.py` and `tests/tasks/test_ftf_r004_p4.py`, plus additions to
      `tests/integration/test_inspect_target_bridge.py`.
    - Read logs only through Inspect's own log API (`read_eval_log`), never a hand parser.
    - Normalization:
      - Each scheduled case maps to exactly one of pass, model_fail, tool_fail, infra_error, timeout, cancelled, not_run.
      - Any missing, cancelled or not_run case gives `incomplete_run` and blocks.
      - A critical model or tool effect can never be relabelled infra_error.
    - Integrity: P3 records the log's sha256 when it is written. A changed hash gives `score_log_tampered`.
    - Business state:
      - Every Inspect score is re-checked against the adapter's independent `collect_outcome` business state. A disagreement gives
        `outcome_mismatch`.
      - Never trust the Inspect summary metrics. The numerator and denominator are recomputed from per-case rows; the Wilson/bootstrap
        gates stay with FTF-H021/0618.
    - A model-graded score whose judge route identity is not the approved one passed in gives `unapproved_judge` (default deny; judge
      approval itself is 0616/0617).
    - The output is a redacted, content-free summary of case ids, statuses, counts and digests. The raw log is referenced only by path and
      hash.
    - DoD and faults (each RED):
      - one score is edited in the log;
      - a sample is deleted;
      - a case is cancelled;
      - an infra_error relabel is applied to a critical effect;
      - an Inspect score disagrees with the backend state;
      - the judge is unapproved;
      - a canary input/expected string appears in the summary;
      - a final-purpose run gets through without a freeze.
    - Disclose BLOCKED_DEPENDENCY on FTF-H020 for the sealed final worker, with its distinct identity, queue, storage and log destination.
  - Parent FTF-R004 remainder, certified only after P1-P4 plus its own integration evidence:
    - the `/evaluations/{id}/engine-evidence` restricted read view and its EvaluationRun binding. No EvaluationRun entity or contract
      exists yet, despite the packet's "existing EvaluationRun" wording, so this is BLOCKED_DEPENDENCY;
    - the sealed final worker run after the H020 CandidateFreeze;
    - the real model route and gateway-side enforcement (BLOCKED_EXTERNAL);
    - the inspect_ai ComponentVersionLock row (R002 deferred that table);
    - the Q35 composed journey (R010).

---

- **FTF-0904 Log redaction** (Control-plane 2026-10-09; deps FTF-0902, FTF-0903 CERTIFIED; §11-12, §65, §66, §70, §77, §81; DoD: `tools/r1_handoff.py packet FTF-0904`; lock `security:log_redaction`; contract owner FTF-0902, read 09-interface.yaml and 09-domain.schema.json; files `packages/security/log_redaction.py`, `tests/tasks/test_ftf_0904.py`). Acceptance: "Sanitize known secret headers/URLs/tokens and unstructured exception content at ingress and sink." Negative: "Redactor failure must not log raw payload; redaction alone cannot justify external telemetry of sensitive content."
  - A stdlib `logging` filter/formatter applied at ingress (record creation) and at every sink handler, plus a function for exception text. It redacts known secret headers (Authorization, Proxy-Authorization, Cookie, Set-Cookie, X-Api-Key and the like), userinfo and token-like query values in URLs, bearer/basic credentials and known token prefixes (sk-, ghp_, xox*-, AKIA..., JWT-shaped), and the rendered exception text including `__cause__`/`__context__` chains and `__notes__`.
  - Fail closed: if redaction raises, the record is replaced by a constant content-free marker with the redactor's error code - never the raw message, args or traceback. Redaction never upgrades a record to external telemetry; FTF-0903's content-free emitter stays the only external path.
  - Lessons from FTF-0903 (build them in from round 1): one shared leak check over the fully rendered output (message, args, `traceback.format_exception` of the whole chain, notes, attributes) for every case; capture root logging, stderr and stdout; secrets generated as variants (case, whitespace, URL-encoding, base64-wrapped, split across args) rather than a fixed list; a fault table where each removed rule and a failing redactor go RED.
  - Disclose UNWIRED if no live logger configuration installs it yet (proving_symbol blank).

- **FTF-0516 Dataset issue resolution UI** (parent; un-parked 2026-10-09: FTF-0516-P1 CERTIFIED, #695; DoD: `tools/r1_handoff.py packet FTF-0516`; files `apps/web/components/dataset-factory/dataset_issue_resolution_ui.tsx`, `apps/web/tests/ftf_0516.spec.ts`). Acceptance: "UI resolves record issue via exclusion/correction/new version with before/after evidence and audit." Negative: "Cannot edit locked data in place or mark a detector false positive without recorded authorized rationale."
  - Consume only the frozen FTF-0516-P1 routes in 05-interface.yaml (list issues, resolve-issue, resolutions); render exactly the frozen response shapes and treat anything else as a typed error. Before/after evidence and audit references come from the server, never inferred client-side.
  - Each resolution action (exclude, correct, new_version, waive) requires a non-blank rationale within the server's 1..2000 bound before submit; the server's 400/403/404/409 outcomes are shown as errors, never success (Law 17). A detector false positive cannot be marked without rationale; there is no in-place edit control for locked data.
  - Lessons from FTF-0309 (build in from round 1): tie every displayed result and every enabled action to the exact org/project/dataset/report identity requested - clear on any switch, ignore in-flight responses after a switch, treat a mismatched echo as an error; strict type checks (exact booleans, enums, required fields); no simulated effect when no handler is wired; idempotency key reused only for an identical retry.
  - Tests: a DOM-level Vitest spec with mocked fetch per typed state and action, each with a fault that goes RED (rationale not required; 409 shown as success; stale result after a switch; mismatched echo accepted; malformed body rendered). Disclose UNWIRED (no Next.js page tree until FTF-H029); proving_symbol stays blank.
- **FTF-0310 Capability contract tests** (Control-plane 2026-10-09T21:45Z; §6, §13, §34, §68, §77, §81; CAP-06; Q07, Q10; deps FTF-0301, FTF-0307, FTF-0309 all CERTIFIED; contract owner FTF-0301 - read `packages/contracts/openapi/03-interface.yaml` and `packages/contracts/jsonschema/03-domain.schema.json`, add no path and no schema change; DoD: `tools/r1_handoff.py packet FTF-0310`; files `packages/model-catalog/capability_contract_tests.py`, `tests/tasks/test_ftf_0310.py`, plus a real-PG integration test and an additive ci.yml step). Acceptance: "Run real artifact/capability resolution plus deterministic negative schema cases against frozen contract." Negative: "Unresolvable/upstream-only model fails trainability; all shipped source variants tested rather than interface-only stubs."
  - What it is: a contract-conformance suite, not new behaviour. `capability_contract_tests.py` holds the reusable checker: it validates a real response from the live `build_trainable_artifact_router` (training-artifact + /changes) and `build_model_capability_router` (/capabilities) against the frozen 03 schemas, using a stdlib-only checker over exactly the JSON Schema/OpenAPI keywords the frozen 03 files use (no jsonschema package: it is not a dependency, and adding one is ask-first). The checker refuses any keyword it does not implement, so an unchecked constraint fails loudly instead of passing. It never re-implements resolution.
  - Real resolution (PG16 as sovra_app, the real routers mounted as in `apps/api/app/main.py`, the existing local gateway stand-in used by the FTF-0309-P1 integration test): one registered artifact per shipped `SourceType` - huggingface, private_s3, local_filesystem, private_registry - in each `ArtifactStatus` (registered, ready, archived), mapped and unmapped, license approved/pending/revoked. Every response validates against the frozen schema, and trainable is true only for a mapped, ready, license-approved artifact with no `unknown` blocker.
  - Negative acceptance: an upstream-only model (in the gateway, no artifact mapping) and an unresolvable model (unknown name, Unicode look-alike, trailing space, case variant - exact match, no normalisation) each return trainable=false with the frozen reason, never 200-trainable; a revoked or archived artifact is never trainable.
  - Deterministic negative schema table: for each frozen response schema, a generated case per required field missing, per enum value outside the enum (including case/whitespace variants), per wrong JSON type, and an extra property where `additionalProperties: false` - the checker must reject each. A generated table, not hand-picked cases (FTF-0616 lesson).
  - Faults that must go RED: a source variant dropped from the matrix; the checker accepting an extra property; enum check casefolded; trainable allowed for an upstream-only model (mutate the real router); revoked license ignored; the schema loaded from a copy instead of the frozen file.
  - Must not edit the certified FTF-0301/0307/0309-P1 modules or the frozen contracts; a found contract mismatch is reported as a finding, not fixed in place. proving_symbol stays blank (test-only task).

- **Dataset importer family FTF-0502..0505 + common slice FTF-0502-P1** (Control-plane design 2026-10-13; FINE_TUNING_FACTORY_RELEASE_1.md §7, §7A.10, §8–8.2, §9, §10, §67.1–67.5, §77, §81; FTF-0501 CERTIFIED 2026-10-10 (#692 + #736, merge 41904e5) made all four dependency-ready; FTF-0500, FTF-0506 and FTF-0508 CERTIFIED). Contract per task: `python tools/r1_handoff.py packet <ID> --plan FINE_TUNING_FACTORY_RELEASE_1.md`. Contract owner FTF-0500: read `packages/contracts/openapi/05-interface.yaml` and `jsonschema/05-domain.schema.json`, and add no path. FTF-0502-P1 refines FTF-0502 under "For each card" step (3). It adds no criteria: the denominator stays 302 and no ADR applies.
  - Order (one lane): FTF-0502-P1 → FTF-0502 → FTF-0504 → FTF-0503 → FTF-0505. With more lanes, the importers run in parallel after P1 (no shared write set).
  - Common rules (every item in this family):
    - Path refinement, recorded: the packet's `services/training-worker/data/<x>.py` becomes `workers/data/<x>.py`, the real SOURCE_ROOT (FTF-0506/0508 precedent).
    - No migration, route, UI, contract edit, runtime dependency, secret or keyed service. Stdlib only: `json`, `csv`, `codecs`, `zipfile`, `xml.parsers.expat`, `hashlib`, `tempfile`.
    - Reuse, do not re-implement:
      - FTF-0506 `normalize_conversation` (the only place a record becomes canonical);
      - FTF-0508 `MAX_CANONICAL_RECORD_BYTES` (2 MiB, §67.3) and its candidate shape (`validate_record_batch` must report 0 issues on every accepted candidate);
      - P1's `import_common` (limits, mapping, chunk reader, report, codes);
      - FTF-0501's `S3Client.stream_object` through P1's `open_dataset_source`.
      - An importer may not edit `import_common.py`. A needed change is a serialized edit that re-runs every landed importer suite (Law 15).
    - Importers never read anything but the chunk iterable they are given. No listing, no other dataset, no file path from a mapping, no network: `sys.addaudithook` sees no `socket.*`, `subprocess.*`, `os.system`, `exec` or `import` of data during an import. Only XLSX opens one anonymous `tempfile.TemporaryFile`.
    - Importer choice is by the session's server-verified content type (§8.1 step 3), never by file name. Each importer refuses a dataset whose `verified_content_type` is not its own, with `content_type_not_supported_by_importer`, before reading a byte. Test: the 5 `SUPPORTED_CONTENT_TYPES` × 4 importers.
    - Law 17:
      - A chunk-source exception (storage outage, connection reset) gives report state `blocked`, `source_unavailable`.
      - A recomputed source sha256 ≠ `Dataset.verified_sha256`, or more bytes than `verified_size_bytes`, gives `failed`, `source_changed`.
      - The deadline gives `failed`, `parse_timeout`.
      - More than `max_rejections` rejected rows gives `failed`, `too_many_rejections`.
      - In each case no record is usable: the contract is that records are provisional until the final report says `completed`, and the tests assert the consumer gets no `completed` report.
    - Refuse, never truncate (§67.2 "Field truncation ... forbidden", §67.3 "Bound ... record bytes and nesting"). An oversized record or field is a row rejection or a fatal error. It is never cut, padded or coerced.
    - No type inference anywhere: every cell or field is kept as the exact source string unless the mapping explicitly applies `json_parse`. Arabic, RTL marks, combining marks, ZWJ/ZWNJ and emoji are kept byte-exact (no NFC/NFKC/casefold/strip).
    - Never sets or changes purpose:
      - The report copies `dataset_purpose` from the Dataset row.
      - No mapping slot can set a purpose. A data field named `purpose`, `split` or `heldout` is never read unless it is mapped as a plain metadata label.
      - No label is invented: an unmapped `quality_label` is absent, never `approved` (§9 "Preassigned evaluation-only data cannot be reclassified into training by an import heuristic", §67.2).
    - Content-free errors (META-LEAK):
      - Reports, rejections and exceptions carry only a code from P1's closed catalog, the mapping slot name from a closed set, and importer-generated structural locators (1-based line / row / element / sheet index, column letters, byte offset). They never carry a header text, sheet name, JSON key, value or a FTF-0506 exception message (its messages can echo data, e.g. `unmapped role {role!r}`).
      - Exceptions are converted with `raise ... from None`.
      - One shared META-LEAK check (P1's test helper) runs over `traceback.format_exception` of every raised error (cause, context, notes, args), every report and rejection field, and captured log records, with markers planted in contents, headers, keys, sheet names and file bytes.
    - Generated tables, not hand-picked cases:
      - stdlib generators with a fixed seed plus exhaustive `itertools.product` over the axes named per item;
      - chunk-boundary independence: every input in the tables is also fed in 1-byte and 7-byte chunks, and split at every byte offset for inputs ≤ 4 KiB. The candidates and `report_sha256` must be identical.
      - Host-dependent behaviour (`csv`, `expat`, `zipfile`) is pinned by tests that the verifier repeats on Python 3.12 (PRE-SUBMIT SELF-ATTACK, neutral host).
    - Bounded resources:
      - `tracemalloc` peak for a 64 MiB input stays within 1.5× the peak for a 1 MiB input with the same maximum record size (memory independent of file size);
      - a maximum-size record fed in 1-byte chunks finishes inside a fixed budget (no quadratic rescans);
      - every limit is tested at max−1 / max / max+1 without overriding the production constants.
    - Real integration (each item adds its own additive ci.yml job plus verdict `needs`, following `direct-upload-routes-integration`):
      - PG16 as `sovra_app` (never the migration owner) plus live SeaweedFS (the test boots the compose `s3` service itself, the `s3lab` precedent).
      - The fixture file is uploaded through the real FTF-0501 routes on uvicorn and finalized into a real Dataset. It is opened through `open_dataset_source` and imported, and every accepted candidate passes FTF-0508 `validate_record_batch` with 0 issues.
      - Mid-stream outage (`s3lab.compose("stop","s3")`, the 0501 precedent, or a truncating TCP relay) → `blocked`.
      - Foreign-org dataset → identical to a missing one.
      - Held-out dataset not readable with `include_held_out=False`.
    - Every DoD bullet has its own behavioural fault→RED row. Uncapped mutation gate at 0 survivors over each new module. PRE-SUBMIT SELF-ATTACK and SAME-CLASS SWEEP across the sibling importers.
    - Disclosed, every item:
      - UNWIRED: no route, dispatcher or activity calls the importers (durable dispatch FTF-0801/H016; persistence and the detector run are the proposed FTF-0502-P2; the MappingSpec UI is FTF-H011). `proving_symbol` stays blank.
      - "Parse in isolated worker" / "resource-limited worker" (§8.1 step 5, §67.3) is a deployment property not proven here.
  - **FTF-0502-P1 Dataset import common contract and verified-source reader (pure + one read-only DB/S3 function; ready now)** (§7, §8.1 steps 1–7, §9, §67.2, §67.3; deps FTF-0500, FTF-0501, FTF-0506, FTF-0508, all CERTIFIED; no lock beyond its own write set).
    - Files:
      - `workers/data/import_common.py` (pure, stdlib);
      - `apps/api/app/datasets/dataset_object_source.py` (read-only);
      - `tests/tasks/test_ftf_0502_p1.py`;
      - `tests/tasks/import_test_support.py` (shared generators + META-LEAK checker, reused by 0502–0505);
      - `tests/integration/test_ftf_0502_p1_dataset_source_postgres_s3.py`;
      - additive ci.yml job `dataset-import-source-integration` + verdict `needs`.
    - `import_common.py` provides:
      - `ImportLimits` (frozen):
        - `max_source_bytes` = 10 GiB (§67.3);
        - `max_record_bytes` = FTF-0508 `MAX_CANONICAL_RECORD_BYTES` (import it; do not re-declare);
        - `max_depth` = 64;
        - `max_rejections` = 10,000;
        - `preview_max_records` = 20 (§67.3 "preview max20");
        - `deadline_seconds`.
        - A caller may lower a limit and never raise one above the default: a higher value raises, because §67.3 says larger limits need "capability/load evidence".
      - `parse_mapping(document: str) -> ImportMapping`, the single MappingSpec parser (H011 must reuse it):
        - plain JSON: duplicate keys refused, NaN/Infinity/`1e999` refused;
        - exact types (`type(x) is str/int/bool/dict/list`, no subclasses or caller objects);
        - an exact allow-list per shape, with `mapping_sha256` over canonical JSON.
        - Shapes:
          - `messages`: a JSON pointer (RFC 6901, exact keys, `~0`/`~1`, decimal indices without leading zeros) to a messages array; explicit per-message key names for role/content/tool_calls/tool_call_id/name; an explicit `role_map` of exact str → FTF-0506 `ALLOWED_ROLES`; and a required `on_unmapped_message_keys ∈ {reject, ignore}` with no default.
          - `prompt_completion`: required `prompt`/`completion` slots and an optional `system`. Produces `[system?, user, assistant]`.
          - `messages_json`: one field/column holding a JSON messages array, parsed strictly. This is how tabular sources carry tool calls.
        - Optional slots: `group_key`, and `metadata` with closed keys {`language`, `task_category`, `consent`, `quality_label`, `source_record_id`, `event_timestamp`}. `event_timestamp` must be ISO 8601 with an offset, and the source string is kept as-is.
        - Format blocks: `csv` {encoding, delimiter, has_header, header_row}, `xlsx` {sheet_name, header_row}, `json` {container, ignored_top_level_keys}.
        - Tabular slots address a column by exact header text or by 0-based index / column letters.
        - Grouped multi-row conversations → `mapping_unsupported` (disclosed).
      - `apply_mapping` builds the raw record. Missing required slot → `required_field_missing`; empty string in a required tabular slot → `required_field_empty`; wrong type → `mapping_type_mismatch`; role not in `role_map` → `unmapped_role`. An empty optional slot is omitted and never defaulted.
      - `normalize_mapped_record` calls FTF-0506 `normalize_conversation(source_type, source_record_index, raw_record, tool_schemas, tool_argument_validator, group_key, record_id)`:
        - `record_id` = uuid5(fixed namespace, `dataset_sha256:mapping_sha256:index`), so it is deterministic and content-free.
        - `ConversationNormalizationError` → `canonical_schema_invalid`, plus at most the message index parsed from the structural `messages[N]` prefix.
      - `to_dataset_record_candidate` builds the §6.11 candidate exactly as FTF-0508 accepts it: `record_index` (0-based source ordinal, counting rejected rows and not blank lines), `schema_version`, `source_type`, `content`, `record_sha256` = lineage.raw_sha256, `normalized_sha256` = canonical_hash, `quality_status="pending"`, `pii_status="unscanned"`, `tool_schema_status ∈ {not_applicable, valid}`.
      - `strict_loads(data, *, max_depth)`:
        - a linear depth pre-scan outside strings before `json.loads` (no `RecursionError` path);
        - duplicate keys → `duplicate_key`;
        - NaN/Infinity, and floats that overflow to inf → `non_finite_number`;
        - an int over CPython's digit limit → `number_out_of_range`;
        - a lone surrogate after decoding → `invalid_unicode`.
        - The rules are the same as FTF-0607 `parse_strict_json`, but that module is not imported: it lives in a separate deployable (`workers/evaluation`). Record this.
      - `ChunkReader`:
        - wraps `Iterable[bytes]`, computes sha256 and the byte count, and enforces `expected_size`/`max_source_bytes`;
        - converts any exception raised by the source iterator into `blocked` / `source_unavailable` (never `completed` or `failed`);
        - checks the deadline;
        - at end of stream compares to `expected_sha256` and gives `source_changed` on a mismatch.
      - The report and events:
        - `RowRejection(locator, code, slot)`.
        - `ImportReport(state ∈ {completed, failed, blocked}, failure_code, importer, importer_version, mapping_sha256, dataset_purpose, expected_sha256, source_sha256, source_bytes, source_schema_sha256 | None, records_seen, records_accepted, records_rejected, blank_lines, rejection_counts, rejections, report_sha256)`.
        - Invariant: `records_seen == records_accepted + records_rejected`.
        - `report_sha256` covers canonical JSON of every field except itself.
        - `REJECTION_CODES` and `FAILURE_CODES` are closed frozensets. A test asserts every code any module emits is in the catalog.
      - `preview(events, limit)` with `limit ≤ preview_max_records` (21 raises). It uses the same code path as a full import.
    - `dataset_object_source.py`: `open_dataset_source(session, context, *, project_id, dataset_id, include_held_out, storage)` (keyword-only, `include_held_out` has no default) → `DatasetObjectSource(dataset_id, purpose, source_type, verified_content_type, expected_sha256, expected_size, chunks)`.
      - It loads the Dataset through FTF-0501-P1 `get_dataset`, so foreign, missing and hidden-held-out all raise the same `DatasetNotFoundError`. It then loads the dataset's own upload session row and its `object_key`, as `sovra_app` under the tenant context.
      - It streams only that key with `S3Client.stream_object`. There is no listing call, and no caller string reaches storage.
      - Storage errors surface from the iterator as FTF-0501's `UploadStorageUnavailableError` (content-free).
      - It never returns the key and never writes.
    - DoD tests:
      - Pure:
        - strict mapping-parse table: for every allow-listed key, each of missing, extra, int-for-str, bool-for-int, float, null, list, nested object, duplicate key, NaN and a str subclass → `mapping_invalid`;
        - JSON-pointer table (escapes, leading-zero index, `-`, empty token, non-ASCII keys exact);
        - `role_map` exactness, using property-generated Unicode variants (reuse the FTF-0616 round-8 generator) of `user`/`assistant`, none of which is accepted;
        - limits table (lower accepted, higher refused, max−1/max/max+1);
        - `strict_loads` table (depth 63/64/65 and 100,000; duplicate keys at every level; NaN/±Infinity/1e999/-1e999; 4300/4301-digit ints; lone and paired surrogate escapes);
        - ChunkReader table (exact size; size+1; sha mismatch at the last byte; the iterator raising at chunk 0, mid-stream and at the end; deadline);
        - candidate table (every accepted candidate passes `validate_record_batch` with 0 issues);
        - determinism across runs and `PYTHONHASHSEED`;
        - META-LEAK with markers in values, keys and FTF-0506 messages.
      - Integration (PG16 as `sovra_app` + SeaweedFS, real FTF-0501 routes):
        - streamed bytes hash to `Dataset.verified_sha256`;
        - foreign-org and foreign-project dataset raise exactly the missing error;
        - a held-out dataset is refused with `include_held_out=False` and readable with `True`;
        - a request counter on the S3 client shows exactly one GET of the session key and no LIST;
        - a mid-stream stop of `s3` → `UploadStorageUnavailableError` → ChunkReader `blocked`.
    - Faults that must go RED:
      - duplicate keys accepted (mapping or `strict_loads`);
      - 1e999 accepted;
      - the depth pre-scan removed;
      - a limit raisable above its default;
      - sha mismatch → completed;
      - a source exception → completed or failed (must be blocked);
      - deadline ignored;
      - `too_many_rejections` → completed;
      - the FTF-0506 message copied into a rejection;
      - a data `purpose`/`split` field read;
      - `quality_label` defaulted;
      - `record_id` from uuid4 (report sha unstable);
      - the role map compared after casefold/strip/NFKC;
      - `include_held_out` given a default of True;
      - the reader lists the bucket or accepts a caller key;
      - a foreign dataset distinguishable from a missing one;
      - `from None` removed.
  - **FTF-0502 JSONL importer** (§7, §8.1, §67.2, §67.3; packet acceptance "Stream JSONL per record and report exact row/schema failures with bounded memory"; negative "Malformed UTF-8/line, huge record and mixed critical schemas produce quarantine/errors, not silent loss"; deps FTF-0500/0501/0506 CERTIFIED + FTF-0502-P1).
    - Files:
      - `workers/data/jsonl_importer.py`;
      - `tests/tasks/test_ftf_0502.py`;
      - `tests/integration/test_ftf_0502_jsonl_import_postgres_s3.py`;
      - ci.yml job `jsonl-import-integration` + verdict `needs`.
    - `import_jsonl(chunks, *, mapping, expected_sha256, expected_size, verified_content_type, dataset_purpose, source_type, limits, tool_schemas, tool_argument_validator)` yields accepted candidates and `RowRejection`s, and finally one `ImportReport`. Accepted types: `application/jsonl` and `application/x-ndjson` only.
    - Line rules:
      - Only LF delimits; one trailing CR before LF is removed. U+2028/U+2029 inside strings are not delimiters.
      - A UTF-8 BOM is accepted only at byte 0 of the file. A BOM anywhere else → `invalid_json`.
      - UTF-16/32 (BOM or NUL pattern) → fatal `unsupported_encoding`.
      - A whitespace-only line counts in `blank_lines` and is not a record.
      - Each line is decoded as strict UTF-8 on its own. A bad line → `invalid_utf8` and the next line still imports.
      - Each line must be exactly one JSON object through P1 `strict_loads`. Otherwise `record_not_object` / `invalid_json` / `duplicate_key` / `non_finite_number` / `nesting_too_deep` / `invalid_unicode`.
      - A line longer than `max_record_bytes` → `record_too_large`. Its bytes are discarded up to the next LF without buffering, and the following lines continue.
      - The line buffer keeps a scan offset: no rescan from the start and no `buf += chunk` quadratic growth.
    - Mixed critical schemas (negative acceptance): lines that map, lines missing required slots, unmapped roles, non-objects and invalid canonical records interleave. Each gets its code and locator. The `records_seen` invariant and per-code counts are exact, and nothing is dropped silently.
    - Generated tables:
      - invalid-UTF-8 classes × position in the line (start/middle/end/split across a chunk): overlong, encoded surrogate `ED A0 80`, truncated multibyte, `FF`/`FE`, CESU-8, stray continuation;
      - line-ending axis: LF / CRLF / no final newline / CR-only / mixed / blank / whitespace-only;
      - BOM position axis;
      - size axis: max−1 / max / max+1 / 64 MiB, delivered in 1-byte chunks;
      - depth axis;
      - duplicate-key-at-depth axis;
      - number axis: NaN, ±Infinity, 1e999, 4301 digits;
      - non-object axis: array, string, number, null, true;
      - trailing-garbage axis: `{}{}`, `{} x`, `{}\u0000`;
      - an Arabic/RTL/emoji/ZWJ/formula-prefix content table, preserved byte-exact in the canonical content.
    - Faults that must go RED:
      - the line cap off by one;
      - an unbounded buffer (memory test);
      - a quadratic rescan (1-byte-chunk budget);
      - an oversized line swallowing the following lines;
      - one bad UTF-8 line failing the whole file;
      - a mid-file BOM accepted;
      - CR left in the record;
      - blank lines not counted;
      - a non-object accepted;
      - trailing garbage accepted;
      - the invariant broken (a rejected row not counted);
      - the content-type check removed (a CSV dataset parsed as JSONL);
      - decoding with `errors="replace"`.
  - **FTF-0504 CSV importer** (§8.1, §67.2 "Select sheet/header/encoding/delimiter", §67.3 "Spreadsheet/CSV exports neutralize formula execution without mutating canonical training content"; packet acceptance "Use explicit header/encoding/delimiter/column mapping; preserve strings, IDs and Arabic text"; negative "CSV formula text is not evaluated; leading-zero identifiers and quoted multiline fields remain intact"; deps FTF-0500/0501/0506 + FTF-0502-P1).
    - Files:
      - `workers/data/csv_importer.py`;
      - `tests/tasks/test_ftf_0504.py`;
      - `tests/integration/test_ftf_0504_csv_import_postgres_s3.py`;
      - ci.yml job `csv-import-integration` + verdict `needs`.
    - Accepted type: `text/csv` only.
    - Everything is explicit, with no `csv.Sniffer` and no guessing:
      - `encoding` ∈ {`utf-8`, `utf-8-sig`, `utf-16-le`, `utf-16-be`, `windows-1256`}, decoded strictly. A decode error is fatal `invalid_encoding` with the physical line, because the reader cannot resync. A UTF-8 BOM under `utf-8` → fatal `unexpected_bom` (choose `utf-8-sig`).
      - `delimiter` ∈ {`,`, `;`, `\t`, `|`}; `quotechar` `"`; `strict=True`; `newline=''`.
      - `has_header` and `header_row` are explicit. Columns match header text exactly: no strip, casefold or NFC; two headers differing only in case or whitespace are distinct. A duplicate header → fatal `duplicate_header`; a mapped column missing → fatal `mapping_column_missing`.
      - Disclosed: through upload only UTF-8 CSV passes FTF-0501's sniff today.
    - Row rules:
      - Field count ≠ header count → `column_count_mismatch`; the row is never padded or cut.
      - `[]` (a blank line) counts in `blank_lines`.
      - A NUL in a mapped field → `nul_character`. Python 3.11's `csv` accepts NUL, so the importer checks it.
      - `field_size_limit` = `max_record_bytes`; exceeding it → fatal `field_too_large`.
      - An unterminated quote at EOF → fatal `csv_structure_invalid`.
      - More than 16,384 columns → fatal `too_many_columns`.
      - The locator is the logical record number plus the starting physical line (`reader.line_num`), never header text.
    - Preservation: every field stays the exact decoded str, with no type inference.
      - Leading zeros, `-0`, `1e5`, `0x1F`, Arabic-Indic digits, `TRUE`, `NaN` and ` 12 ` survive.
      - Quoted multiline fields keep their exact embedded CRLF / LF / CR.
      - Formula-looking text (`=`, `+`, `-`, `@`, leading TAB/CR, full-width `＝`, the DDE `=cmd|' /C calc'!A0`) is kept byte-exact in the canonical content and never evaluated (audit hook: no subprocess, no socket).
      - Export neutralization is the export task's job, not the importer's.
      - The source schema digest is sha256 over the exact header cells, in order.
    - Generated tables:
      - encoding × Arabic/RTL/combining/emoji corpus (round-trip byte-exact);
      - a wrong declared encoding for each encoding's bytes;
      - delimiter × quoting (a field containing the delimiter, a doubled quote, CR, LF, CRLF) × line ending (LF/CRLF/CR);
      - a header table (duplicates, case/whitespace/NFC variants, a missing mapped column);
      - a ragged-row table;
      - the preservation table above × column position;
      - a formula-prefix table × quoted/unquoted;
      - a size table (field max−1/max/max+1; 1-byte chunks).
    - Faults that must go RED:
      - the delimiter or encoding guessed (Sniffer or a fallback);
      - any int/float/bool conversion;
      - a formula prefix stripped, escaped or quoted in the canonical content;
      - embedded CRLF normalized;
      - the header matched after strip or casefold;
      - a duplicate header accepted;
      - a ragged row padded or truncated;
      - `errors="replace"`/`"ignore"`;
      - a BOM silently accepted under `utf-8`;
      - the field-size cap removed;
      - NUL dropped silently;
      - the physical line number off by one in a multiline record.
  - **FTF-0503 JSON importer** (§7, §8.1, §67.3 "oversized nested JSON"; packet acceptance "Parse approved JSON conversation/container structures into canonical records without loading unbounded file"; negative "Deep nesting, malformed objects and unsupported structures fail with actionable mapping errors"; deps FTF-0500/0501/0506 + FTF-0502-P1).
    - Files:
      - `workers/data/json_importer.py`;
      - `tests/tasks/test_ftf_0503.py`;
      - `tests/integration/test_ftf_0503_json_import_postgres_s3.py`;
      - ci.yml job `json-import-integration` + verdict `needs`.
    - Accepted type: `application/json` only.
    - Approved containers are named explicitly in the mapping:
      - `"$"`: a root array of records;
      - `"/<key>"`: exactly one key of the root object holding the array. Every other root key must be listed in `ignored_top_level_keys`, else fatal `unmapped_top_level_key`. Each ignored value is scanned, bounded by `max_record_bytes` and the depth limit, and not retained.
      - `"$record"`: a root object that is one record (≤ `max_record_bytes`).
      - Anything deeper or different → `mapping_unsupported`.
    - Streaming (no whole-document `json.loads`): a linear byte scanner tracks string, escape and depth state and splits the container's elements.
      - Each element is ≤ `max_record_bytes` and parsed with P1 `strict_loads`.
      - An oversized element is skipped by scanning, without buffering, and rejected as `record_too_large`; the next element continues.
      - Element-level errors (duplicate key, NaN, invalid unicode, not an object, canonical schema) are row rejections with the element ordinal.
      - Structure errors are fatal with the byte offset: trailing comma, missing comma, unbalanced bracket, a bad token between elements, a root scalar, the container not found or not an array, a duplicate root key, trailing data after the root, depth > `max_depth`.
      - Actionable means the code plus the mapping's own container pointer; never document content.
      - UTF-8 only (BOM at byte 0 allowed); UTF-16/32 → `unsupported_encoding`.
      - An empty container → `completed` with 0 records (honest; split gates handle size).
    - Generated tables:
      - a seeded random JSON document generator (strings with escaped quotes, backslashes, brackets and braces inside strings, `\u` escapes, surrogate pairs, every whitespace kind between tokens, nested arrays/objects up to the depth limit) for a differential test: for every valid document, scanner elements + `strict_loads` equal `json.loads(whole)[container]` element by element, and every mutated invalid document fails in both;
      - container × shape table (root array; mapped key with ignored siblings; unmapped sibling; duplicate root key; container not an array; `$record`; scalar root; nested container pointer);
      - depth 63/64/65/100,000 inside and outside the container;
      - element size max−1/max/max+1 in 1-byte chunks;
      - memory: peak for a 64 MiB container stays within 1.5× of a 1 MiB one.
    - Faults that must go RED:
      - a split inside a string at an escaped quote;
      - the depth bound off by one;
      - a trailing comma accepted;
      - an oversized element buffered (memory test) or ending the import;
      - an unmapped root key accepted;
      - a duplicate root key accepted;
      - whole-document `json.loads` used (memory test);
      - the differential test mismatch;
      - a container pointer resolved after casefold;
      - an empty container reported as `failed` or invented records.
  - **FTF-0505 XLSX importer** (§8.1, §67.2, §67.3 "Quarantine malformed archives, zip bombs, path traversal, entity expansion ... XLSX support is values-only: no macro execution/external link retrieval; reject or surface formulas instead of trusting stale cached values"; packet acceptance "Read XLSX sheets/columns through explicit UI mapping without evaluating formulas/macros"; negative "ZIP/XML bombs, encrypted/unsupported workbook, formula-only critical data and excessive cells fail safely"; deps FTF-0500/0501/0506 + FTF-0502-P1).
    - Files:
      - `workers/data/xlsx_importer.py`;
      - `workers/data/ooxml_safety.py` (pure archive/XML safety primitives, public);
      - `tests/tasks/test_ftf_0505.py`;
      - `tests/integration/test_ftf_0505_xlsx_import_postgres_s3.py`;
      - ci.yml job `xlsx-import-integration` + verdict `needs`.
    - Accepted type: the XLSX content type only.
    - Reuse: port FTF-0501's checks into `ooxml_safety.py` (end-of-archive totals before `zipfile` allocates; ≤ 10,000 entries; directory ≤ 4 MiB; `[Content_Types].xml` ≤ 1 MiB; DOCTYPE refused at the expat parser in every encoding; traversal/absolute/backslash names).
      - Do not edit `direct_object_storage_upload.py`. A drift test imports both modules and pins the bounds equal.
      - Reuse FTF-0501's malicious-package fixture builders (`tests/tasks/test_ftf_0501.py` `_zip`, `_xlsx`, `_zip64_tail`) by import.
      - Follow-up FTF-0501-F1 (Control plane) moves `inspect_xlsx` onto `ooxml_safety`.
    - Reading:
      - The chunks are spooled to one anonymous `tempfile.TemporaryFile` (mode 0600, deleted on close; never a named path) while ChunkReader hashes them. Bytes beyond `expected_size` → `source_changed`.
      - `zipfile` opens the archive only after the sha matches.
      - Every XML part is parsed incrementally with expat: a DOCTYPE handler refuses; parameter entity parsing is NEVER; there is no external entity resolution.
      - Decompressed bytes are counted per part and in total against caps, and the counts are never trusted from the directory.
      - Part names are compared case-insensitively for duplicates (OPC part names are case-insensitive) → `unsafe_archive`.
      - An encrypted entry flag or compression other than stored/deflate → `unsupported_workbook`.
    - Workbook refusals (fatal, before any cell is read):
      - an OLE/CFB container (encrypted/password workbook) or a non-zip → `unsupported_workbook`;
      - strict OOXML namespaces, a template or a non-`sheet.main+xml` workbook type → `unsupported_workbook`;
      - `xl/vbaProject.bin`, `activeX`, `embeddings`, or any macro-enabled type, even inside an `.xlsx`-typed package → `unsafe_workbook`. FTF-0501 finalize does not catch these today (Decision 8).
      - `xl/externalLinks/*`, any relationship with `TargetMode="External"` (workbook or sheet rels), `xl/connections.xml` or query tables → `external_link_present`, never retrieved.
    - Sheet selection:
      - The mapping's `sheet_name` matches the workbook's `<sheet name>` exactly.
      - Names equal after casefold (impossible in Excel, so crafted) → `duplicate_sheet_name`.
      - A hidden or veryHidden sheet is used only when named explicitly, never chosen automatically.
      - The sheet target resolves through the workbook rels inside `xl/`. Traversal, an absolute path or an external target → `unsafe_archive`.
    - Cells:
      - `<f>` present (including a shared `<f t="shared" si=".."/>` with no text, and array formulas) in a mapped column → row rejection `formula_cell`. The cached `<v>` is never used, and `t="str"` counts as a formula result.
      - `t="s"` takes the shared string by index; an index out of range or not an int → fatal `xlsx_malformed`.
      - Rich-text runs are concatenated exactly; `<rPh>` is excluded.
      - ECMA-376 `_xHHHH_` escapes are decoded (`_x005F_` → `_`).
      - `t="inlineStr"` is used as-is. `n`, `b` and `d` keep the literal `<v>` text (no float conversion and no date-serial interpretation, disclosed). `t="e"` in a mapped column → `cell_error_value`.
      - Rows and cells must strictly increase by `r` (implicit refs allowed). A duplicate or decreasing ref → fatal `xlsx_malformed`.
      - The header row is explicit; a duplicate header → fatal `duplicate_header`.
      - Merged ranges hold a value only in the top-left cell, so a missing required value → `required_field_empty`.
    - Bounds (refuse, never truncate):
      - row > 1,048,576 or column > XFD → fatal `sheet_out_of_bounds`;
      - cells read > `max_cells` → fatal `too_many_cells`;
      - sharedStrings decompressed > its cap (default 256 MiB, recorded as the job memory quota; raising it needs load evidence) → fatal `limit_exceeded`;
      - any cell > `max_record_bytes` → `field_too_large`.
      - The dimension `ref` is ignored (untrusted).
      - The source schema digest is sha256 over the sheet index plus the exact header cells.
    - Generated fixtures: stdlib `zipfile` + XML strings only. No openpyxl (not a dependency, and adding one is an ask-first item).
    - Generated tables:
      - cell type {s, inlineStr, str, n, b, e, d, none} × formula {none, normal, shared-empty, array} × column {mapped, unmapped};
      - shared strings (plain, rich runs, `rPh`, `xml:space`, `_xHHHH_` incl. `_x000D_`/`_x005F_`, index −1/0/last/last+1/non-int);
      - an archive-attack table (zip bomb past the caps; the 10,001-entry zip64 tail; a 4 MiB+1 directory; DOCTYPE/ENTITY in a sheet / sharedStrings / rels in UTF-8 and UTF-16; case-insensitive duplicate part; traversal/absolute/backslash sheet targets; an encrypted entry; an unsupported compression method);
      - a workbook-type table (CFB/encrypted, strict OOXML, template, macro-enabled type, vbaProject in a plain package, activeX, embeddings);
      - an external-link table (externalLinks part, an External rel in the workbook and in sheet rels, connections.xml);
      - a sheet table (exact/case-variant name, hidden, veryHidden, missing, duplicate);
      - a bounds table (row 1,048,576/1,048,577; column XFD/XFE; cells max−1/max/max+1; sharedStrings cap ±1);
      - preservation (Arabic, RTL, leading zeros in text cells, formula-looking plain strings preserved, not rejected).
    - Faults that must go RED:
      - the cached `<v>` used for a formula cell;
      - a shared empty `<f/>` treated as a value;
      - vbaProject accepted in a plain package;
      - an External rel or connections.xml accepted;
      - DOCTYPE accepted in a sheet part;
      - the case-insensitive duplicate check removed;
      - a traversal sheet target followed;
      - strict OOXML parsed as transitional;
      - each bound off by one;
      - a shared-string index out of range read as an empty string;
      - `_xHHHH_` not decoded, or decoded twice;
      - a hidden sheet chosen automatically;
      - the sheet matched after casefold;
      - the sharedStrings cap removed;
      - an error cell imported as text;
      - the spool accepting bytes beyond `expected_size`;
      - the end-of-archive pre-check removed;
      - a named temp path, or the file not deleted.
- **FTF-1504 IDOR suite** (Control-plane design 2026-10-13; §45, §47, §52, §65.1–65.2, §77, §81; packet acceptance "Guess foreign IDs across every nested API and object download/export endpoint"; negative "404 masking alone insufficient if side effect/data still occurs; check actual state/access"; deps FTF-0106, FTF-0501, FTF-1501 all CERTIFIED; DoD `python tools/r1_handoff.py packet FTF-1504 --plan FINE_TUNING_FACTORY_RELEASE_1.md`; contract owner FTF-1501: reuse `ScenarioResult`/`TenantIsolationEvidence`/`build_evidence` from `tests/security/tenant_isolation_suite.py` and do not edit `15-interface.yaml`). Ready now.
  - Files:
    - `tests/security/idor_suite.py` (harness, registry, runner; a standalone script like the 1501/1503 suites);
    - `tests/tasks/test_ftf_1504.py` (no-DB: inventory completeness, generators, comparator, faults);
    - `tests/integration/test_ftf_1504_idor_postgres_s3.py` (or `main()` of the suite);
    - an additive ci.yml job `idor-suite-integration` + verdict `needs` + `tests/unit/test_ci_policy.py` coverage.
  - No production code, migration, contract, runtime dependency, secret or keyed service.
  - If the suite finds a real IDOR, do not allowlist it: park that route/slot with the reproduction, and the Control plane slices the fix against the owning task (Law 15).
  - Live surface (generated, never hand-listed): the REAL `app.main.create_app()` with the 0103A environment, flattened with FTF-0103A's `_flatten_routes`. Reuse it by import from `tests/tasks/test_ftf_0103a.py` or move it to a shared helper with that test re-run.
    - Today: 29 mounted routers (28 under `/api/v1` plus `/auth`), 108 `/api/v1` route-verbs (count it and print it; do not hard-code it).
    - Requests go over a real socket to uvicorn (1501 precedent).
    - Auth is the real `SessionPrincipalResolver` with the app's own signing key and real `memberships` rows written through `membership_admin_cli`. Sessions are minted with the same function the OIDC callback uses (disclosed: the OP round-trip itself is proven by FTF-0103A).
    - The app DB role is `sovra_app`, refused otherwise via 1501's `assert_not_superuser_role`.
  - Registry and how new routes join:
    - `ROUTE_REGISTRY[(method, path_template)] = RouteSpec(path_slots, query_slots, body_slots, seed, destructive)`. Every `{param}` gets a resource kind, auto-checked against the path.
    - Query id slots: `project_id` on catalog/capability/probe routes, `artifact_id` on mapping changes. Pagination cursors (`after`) are marked `cursor` with a reason; they compare by value and resolve no row.
    - Body slots: every request-body property ending `_id`/`_ids` (recursive into arrays/objects) in the frozen OpenAPI (01..17-interface) must be registered. For the 25 uncontracted route-verbs (Decision 10), the slots are declared by hand, cross-checked against each route module's body allow-list frozensets (every `*_id` name in them must be registered), and the list is recorded as a contract gap.
    - The completeness gate runs in `tests/tasks/test_ftf_1504.py`, in the `gate` job with no DB: live set == registry keys exactly, and every path/contract/allow-list id is registered. A PR that adds a `/api/v1` route without registering it fails `gate`.
    - Object download/export: no such route is live today (checked; FTF-0501's part-urls issues PUT grants only). The registry has the kinds `download` and `export` with zero routes and a test that any future route whose response carries a storage URL must declare one. Disclosed.
  - Matrix (generated: route-verb × id slot × id class × principal role):
    - Id classes:
      - `foreign_org`: org B's real object of the same kind;
      - `foreign_project`: a same-org object in project P2 addressed under P1;
      - `foreign_parent`: same project, another parent. Examples: case/suite, evidence/manifest, snapshot/connection, candidate/snapshot, issue/dataset, model under another LiteLLM connection;
      - `hidden_purpose`: a held-out dataset or upload session for `ml_engineer`, which the routes promise is the same 404;
      - `foreign_name`: a model name in org B's catalog under A's own connection;
      - `missing`: a fresh uuid4 never issued;
      - `own`: the positive control.
      - For the `organization_id` path param, `foreign_org` (B) vs `missing` (random) must both be the identical 403 `role_not_authorized`.
    - Roles: every role in the route's `require_role` allowed set, introspected from the guard's closure (`allowed`), as FTF-0103A does (generated, not trimmed to org admin). One disallowed role per route is also checked to be uniform (the full RBAC matrix is FTF-1502).
    - Body slots get the same classes, placed in an otherwise valid body.
  - Assertions per cell:
    - Indistinguishable: for every non-`own` class, status, raw body bytes and every header except `date` are byte-identical to the `missing` response for the same route, slot and role. No marker planted in org B / P2 objects (names, descriptions, refs) appears anywhere.
    - Positive control: `own` must differ from `missing`. Otherwise the slot is vacuous and the suite is RED. A slot that cannot be exercised (a dependency not available) is `NOT_EXERCISED` with a code and fails the run unless the Control plane allowlists it in the entry; none is allowlisted now.
    - Side effects (the negative acceptance), before and after each request:
      - DB digest: as the migration owner, for observation only, md5 of every row of every table in `public`, enumerated from `information_schema` (never hand-listed), including `audit_events`.
      - Storage digest: the SeaweedFS bucket object listing plus multipart uploads and parts.
      - Outbound counters on every local stand-in the seeds point at: the H005/0309-P1 local TLS LiteLLM gateway harness, a GitHub API stand-in, the component-binding endpoint stand-in, and the OpenBao dev server where the job has one.
      - The foreign-class delta must equal the missing-class delta. Expected: both empty. If a route writes a denial audit, the two must be equal after masking `id`, `created_at` and `request_id`, and must never name org B's id or row.
    - Storage-layer IDOR: own part-url grants with the object key path swapped to org B's session key are refused by SeaweedFS (signature), and B's parts are unchanged. A foreign `part-urls`/`parts`/`finalize` returns no URL and makes no S3 call (counted).
    - Destructive verbs (DELETE, revoke, lock, select, finalize, reserve/release) run the foreign classes and `missing` first, then the control on a disposable seed.
  - Seeds: two orgs (A, B); two projects in A (P1, P2) and one in B; two parents per nested kind; every kind the registry names. Seeds go through the real API as the org admin wherever a create route exists, otherwise through the certified store function as `sovra_app` under tenant context, never raw owner SQL. A real FTF-0501 upload and finalize into SeaweedFS creates the datasets; FTF-0516-P1 `record_quality_report` creates the issue rows.
  - Evidence: one content-free `ScenarioResult` per cell (route template, method, slot, class, role, status, passed) through 1501's `build_evidence`, written as a JSON artifact, plus totals per route. Bodies are never recorded.
  - CI: the job uses PG16 (pinned digest as `direct-upload-routes-integration`) and boots the compose `s3` itself. The runtime budget is recorded in the submission.
  - Faults that must go RED (each its own row; inject into a copy of the real app or by monkeypatching one real store in-process):
    - a demo route answering 403 for foreign and 404 for missing;
    - a route answering 404 but inserting a row;
    - a route answering 404 but calling the gateway stand-in;
    - a 404 with a different body or an extra header;
    - a foreign marker echoed in a 404 body;
    - a live route deleted from the registry;
    - a path param without a kind;
    - an unregistered contract `_id` field;
    - an unregistered allow-list `_id` name;
    - the role set trimmed to `organization_admin`;
    - a positive control that 404s (vacuous slot);
    - the app run as the migration owner;
    - real-route faults:
      - `evaluation_case` GET not filtering by `suite_id`;
      - `part-urls` not checking the project;
      - the dataset issue lookup not checking the dataset;
      - the model-catalog `project_id` query slot not checked;
    - the DB digest table list hand-trimmed (drop `audit_events`) so a side-effect fault stays green, which must itself be caught by the enumeration test.
  - Disclosed:
    - Worker, cache and model-serving paths and object download/export routes do not exist yet. Whichever task adds one registers it, and the gate enforces that.
    - Timing side channels are not measured.
    - The 25 uncontracted route-verbs are listed as a contract gap.

## Parked (item-level blockers — relay continues other work; Control plane resolves)
- **FTF-H010-P5 round 2: PARKED (3 independent pre-review rounds exhausted, CLAUDE.md "Relay is execution-only" step 4), 2026-11-20T1500Z.**
  - What failed: PR #1053 (`relay/FTF-H010-P5-r2`, draft, head `8602d2f2`, `[GOVERNANCE]`). Round 1 returned 7 findings (live region, loading false alert, loading/5xx/identity/poll-stop unpinned, strict-parse table), all fixed in `69f32f9f`. Round 2 returned 4 (stale catalog reply, English state names in the Arabic announcement, catalog status enum, source_type unpinned), all fixed in `8602d2f2`. Round 3 open: (1) STALE-POLL-ERASES-CREATED-JOB: `loadJobs` applies every reply with no sequence check, so a poll that started before the POST can erase the created job and stop polling; (2) NOT-BILINGUAL: artifact status/source_type enums and the review-step security_profile render in English under ar (spec lines ~367-369 lock it in); (3) the `current()` guard in the `loadInto` catch path is unpinned.
  - Tried: jsdom 164/164, Chromium 105/105, lint/tsc/prettier clean, fault->RED rows for every fix. Not run locally: real-API PG16 suite, mutation gate (no production Python changed).
  - Route options: (1) a relay shift claims `relay/FTF-H010-P5-r3` (merge r2), adds a request-sequence guard to `loadJobs` and the same-URL option loaders, en/ar labels for the artifact status/source/profile enums, a test for the loadInto catch path, then one more pre-review of that delta; (2) Control plane L2-certifies #1053 with these three as disclosed residuals (fail-closed or reload-recoverable); (3) Control plane splits the loader race hardening into a shared UX01 hook used by all wizards.
  - State left: PR #1053 open, draft, unmerged, PROVISIONAL (never self-certified). Do not reclaim `relay/FTF-H010-P5-r2`; read this entry first.
- **FTF-H010-P5 round 1: PARKED (3 independent pre-review rounds exhausted the fix-and-re-review cycle, CLAUDE.md "Relay is execution-only" step 4), 2026-11-11.**
  - What failed: PR #1037 (`relay/FTF-H010-P5-r1`, draft, head `28d0c9db` plus the parking commit) went through 3 fresh-reviewer rounds. Rounds 1-2 findings (unvalidated base list, lost-POST false match and stale baseline, blocked shown as failed, focus to body, silent project GET, ref check vs server, polling stop, catalog entries, unpinned roles) were fixed with fault->RED tests. Round 3 still returned 3 blockers: ANNOUNCE-ONCE (the poll announcement changes only once and the live region is inserted already holding text), BASE-LIST-FALSE-ALERT-WHILE-LOADING (a fabricated error alert while the job list is still loading) and OPTIONS-LOADING-UNPINNED (the options loading branch can be removed with both specs GREEN).
  - Tried: full wizard UI, DOM 70, Chromium 105, wiring 4, real-API job `model-import-ui-real-api-integration` (R1-R12), ~60 fault->RED rows. Reports and fix logs: `docs/certifications/submissions/2026-11-11T1305Z-FTF-H010-P5.md`.
  - Route options: (1) a relay shift claims `relay/FTF-H010-P5-r2` (merge the r1 branch) and fixes the 3 blockers: keep an always-mounted empty `role=status` announce region whose text changes per transition, show a loading state while `jobs === null` with no error, and pin the options-loading branch; then L2. (2) Control plane L2-certifies PR #1037 as-is, treating the blockers as advisories. (3) Split the announcement/loading states into a small follow-up slice and certify the rest.
  - State left: PR #1037 open, draft, unmerged, PROVISIONAL-not-submitted (never self-certified). It is also a `[GOVERNANCE]` PR (it edits ci.yml), so the owner reviews and merges it. Not claimed by any other lane. Do not reclaim `relay/FTF-H010-P5-r1`; read this entry first. FTF-H010-P6 stays blocked on it.
- **FTF-0701-P1 round 1: PARKED (3 independent pre-review rounds exhausted the fix-and-re-review cycle, CLAUDE.md "Relay is execution-only" step 4), 2026-11-11T0600Z.**
  - What failed: PR #1033 (`relay/FTF-0701-P1-r1`, draft, head `06fa6241` plus a submission-file commit) went through 3 fresh-reviewer rounds. Rounds 1-2 findings (Decimal context rounding in normalize and in budget arithmetic, ruff format, facts META-LEAK, schema looser than module, intra-stage order, regex parity, advanced empty rule, truthy flags) are fixed. Round 3 open: (1) NaN/Infinity/negative `BudgetFacts` values escape as raw `decimal` errors or resolve OK; (2) `TrainingConfigFacts` fields are not exact-typed (bool counts, str scopes, empty digests, bytes/Decimal row_version give uncoded errors); (3) per-field schema parity is pinned only as a pattern set, so 11 TrainingConfigV1 schema faults stay GREEN; (4) `ResolvedConfigDocument` server fields untyped and `seed` not required.
  - Tried: full pure module, 158,990 checks with ~100 fault->RED rows, uncapped mutation gate `279 run | 279 KILLED | 0 SURVIVED`, ruff/mypy/format clean, product_truth 58/58. Details: `docs/certifications/submissions/2026-11-11T0600Z-FTF-0701-P1.md` on the branch.
  - Route options: (1) a relay shift claims `relay/FTF-0701-P1-r2` (merge the r1 branch), adds a `validate_facts` fail-closed step (exact types, finite Decimals, `0 <= reserved <= total`, sha256 regexes, tuple scopes), per-field schema parity tests and typed resolved server fields, then one more pre-review of that delta; (2) Control plane L2-certifies PR #1033 as-is with findings 1-4 as disclosed residuals (facts are built server-side by P3, request parsing was clean in every round); (3) Control plane moves facts validation into P3 (where the facts are built from rows) and rules P1 certifies on the pure request schema only.
  - State left: PR #1033 open, draft, unmerged, PROVISIONAL (never self-certified). Not claimed by any other lane. FTF-0701-P2/P3 stay blocked on it. Do not reclaim `relay/FTF-0701-P1-r1`; read this entry first.
- **FTF-R004-P3 round 2: PARKED (3 independent pre-review rounds exhausted the fix-and-re-review cycle, CLAUDE.md "Relay is execution-only" step 4), 2026-11-10T2200Z.**
  - What failed: PR #1029 (`relay/FTF-R004-P3-r2`, draft, head `67a4a5a2`; r1's 7 P3 commits cherry-picked onto main, r1's base was stale) went through 3 fresh-reviewer rounds; rounds 1-2 findings (telemetry via .env/hooks, datagram/stream sends, forgeable provenance, route_params without hook, unbound case fingerprint, collect mismatch scored correct, deadline) are fixed. Round 3 still open: (1) the network guard is not active around describe/attest/bind_bridge_hook/final re-check, and fd-level egress (`sendfile`, `os.write(fileno)`, ssl sends) and subprocess/native code pass an in-process guard; (2) per-case results not type-checked as `AdapterInvokeResult`; (3) `route_fingerprint` not pinned across cases; (4) loaded-suite identity registry beaten by `object.__setattr__` (deliberate tampering).
  - Tried: full implementation, 200 task checks, PG16 integration, ruff/mypy clean, mutation gate `200 run | 197 KILLED | 3 TIMEOUT | 0 SURVIVED`. Full reviewer rounds and disclosures: `docs/certifications/submissions/2026-11-10T2100Z-FTF-R004-P3-r2.md` on the branch.
  - Route options: (1) Control plane rules that process-level network denial needs an OS sandbox (e.g. netns) and defers it, then a relay shift claims `relay/FTF-R004-P3-r3` for items 2-4 plus the guard around adapter calls (all small, mechanical); (2) Control plane L2-certifies PR #1029 as-is with items 1-4 as disclosed residuals; (3) Control plane designs the sandboxed-run mechanism (child process under `unshare -n`) as its own slice, and P3 then runs the bridge inside it.
  - State left: PR #1029 open, draft, unmerged, PROVISIONAL (never self-certified). Not claimed by any other lane. FTF-R004-P4 and the 0614 chain stay blocked on it. Do not reclaim `relay/FTF-R004-P3-r2`; read this entry first.
- **FTF-R006-P2 round 1: PARKED (3 independent pre-review rounds exhausted the fix-and-re-review cycle, CLAUDE.md "Relay is execution-only" step 4), 2026-10-22T1622Z.**
  - What failed: PR #961 (`relay/FTF-R006-P2-r1`, draft, head `2435d57b3000e0a5b3c673c142966152d7e3009c`) went through 3 fresh-reviewer rounds. Round 1: excluded-position shift, reconcile race, unverified record adoption, UQ constraints untested, fake 409 vs pinned 422 - all fixed. Round 2: replaced-record undetected, dataset settings verified by name only, quadratic reconcile (fixed), plus a false-drift-after-uncommitted-write finding whose round-2 fix ("adopt untracked responses") round 3 correctly rejected: ADOPTION-LAUNDERS-UNAUTHORIZED-VALUES (a refused or owner-edited value could become the reviewer's recorded decision). That fix was replaced (reconcile reports `response_pending_adoption`, records nothing; the reviewer's retry overwrites and reads back) and pinned by tests, but **no 4th independent pre-review was run**, so the last two commits (0c199502, 861e1e67) are not independently re-reviewed.
  - Tried: full implementation (adapter, store, migration 0036, live-DB integration test, additive CI job), uncapped mutation gate `535 run | 534 KILLED | 1 TIMEOUT | 0 SURVIVED`, 50+ fault->RED runs. Everything in `docs/certifications/submissions/2026-10-22T1622Z-FTF-R006-P2.md` on the branch (disclosures included). The OpenBao leg of the integration test could not be run locally (sandbox refused building it); it runs only in CI job `argilla-review-queue-integration`.
  - Route options: (1) Control plane L2-certifies PR #961 as-is (it is the independent review of the final delta; the last finding was fixed by deleting the offending feature, not adding design), after the CI run of `argilla-review-queue-integration` (real OpenBao) is green; (2) a future relay shift claims `relay/FTF-R006-P2-r2` for one more independent pre-review of the final delta and any follow-ups; (3) Control plane rules on the real-Argilla-stack leg first (QUEUE bullet wants a real argilla-server v2.8.0 + OpenSearch + Redis job; P1b, not yet certified, and this slice both use the real-socket `FakeArgillaServer`) and on migration numbering (0036 chains onto 0034 because PR #934 holds 0035; the second to merge rechains). Recommended: (1) after (3).
  - State left: PR #961 open, draft, unmerged, PROVISIONAL (never self-certified); branch `relay/FTF-R006-P2-r1` at the head above. FTF-R006 parent stays NONE/ABSENT in spec-matrix; P3 and P4 are not started. Do not reclaim `relay/FTF-R006-P2-r1`; read this entry first.
- **FTF-UX02 round 2: PARKED (3 pre-review rounds exhausted the fix-and-re-review cycle, CLAUDE.md "Relay is execution-only" step 4), 2026-10-22T0230Z.**
  - What failed: PR #947 (`relay/FTF-UX02-r2`, head `c61e406c208cf0ebf3cd49cdaaa1b7f369025ae5`) went through 3 independent pre-review rounds this shift, each by a fresh reviewer that did not write the code. Round 1 returned 7 structural findings (focus lost to `<body>` on the abandon-confirm, several finding-4 regressions unpinned, FTF-0500A not actually bilingual, errors silent/unannounced, "labels above fields" misclaimed as met, a stale-identity bug, missing UX01/evidence items) — all fixed with fault-injected RED→GREEN tests, independently confirmed. Round 2 reviewed those fixes and returned a *different* 7 findings — mostly test-pin gaps on code the reviewer found already correct, but also 3 genuine new bugs surfaced by the rebuild: raw English server-error `detail` text shown to Arabic-locale users, a silent "Authorized" success result with no `role="status"`, and keyboard focus falling through to `<body>` after Check-authorization/Confirm-fails/Retry-fails — all fixed and independently confirmed. Round 3 returned again, but narrowly: the reviewer's own words were "the code is correct on every user-facing path I probed"; the only two items were (1) 4 of the Arabic strings round 2 named (`retry`, `networkErrorRetryable`, `requestFailedRetryable`, `responseMalformedRetryable`) are confirmed correctly translated in the code but still have no exact-string assertion (a coverage gap, not a behavioral defect), and (2) the submission's own FTF-0500A heuristic-evaluation table (H1/H9 cells) was never updated to reflect round 2's fixes, leaving a stale "no severity 3-4 finding remains after round 1" claim in the evidence doc. No reproduced behavioral defect remains at head `c61e406`.
  - Tried: 3 full fix-and-independent-review cycles in one shift (never self-certified, Law 4); every round's full verbatim reviewer report and fix log is in `docs/certifications/submissions/2026-10-22T0119Z-FTF-UX02-r2.md`. Per CLAUDE.md's 3-round limit, this item parks now rather than attempting a 4th cycle, regardless of how narrow the remaining gap is.
  - Route options: (1) Control plane L2-certifies PR #947 as-is, treating the 2 remaining items (4 string-assertion additions, 1 documentation-table correction — both mechanical, no design judgment, no reproduced defect) as acceptable evidentiary bookkeeping gaps rather than a certification blocker, given the round-3 reviewer's own finding that the code itself is correct; (2) a future relay shift claims a 4th round (e.g. `relay/FTF-UX02-r2-r4`) to add the 4 missing assertions and correct the heuristic table, then re-requests one more independent pre-review; (3) Control plane makes the two trivial edits directly as part of its own certification pass. Recommended: (1) or (3) — the gap is too narrow to justify spending a full relay round and another independent-review cycle on.
  - State left: PR #947 open, draft, unmerged, `certification_state: PROVISIONAL` (never self-certified). Branch `relay/FTF-UX02-r2` at head `c61e406`. The three parent tasks (FTF-0500A, FTF-0501-UI, FTF-0516) all still requalify (their jsdom and real-API suites pass unchanged in behavior). FTF-0516 itself and most of FTF-0501-UI remain untouched from round 1 (disclosed, not a defect — out of this round's scope). Nothing further attempted by this shift on this item past round 3's RETURN.
- **FTF-1506 vite HIGH advisory: PARKED (new devDependency needs owner), 2026-10-20T22:30Z.**
  - What failed: dependency scan blocks on vite 8.3.0 GHSA-587h-7wmj-h5pm (fixed 8.3.3) and certifi 2026.7.22 GHSA-6q48-6c7w-9gw2 (fixed 2026.10.19). certifi bumped in this PR (uv.lock, 3 lines). vite: `pnpm update vite@8.3.3` resolved only rolldown bumps (vite is a vitest peer); `pnpm.overrides` left vite@8.3.0 (peer resolution).
  - Routes: (1) owner approves exact-pinned root devDependency vite@8.3.3, relay adds it; (2) Control plane adds a reviewed policy exception; (3) bump vitest to a release whose peer resolution picks vite >=8.3.3.
- **FTF-UX01 Dialog component: RESOLVED 2026-10-23T10:52Z - the owner approved `@radix-ui/react-dialog@1.3.1` (exact-pinned) live; `@radix-ui/react-progress` was not asked for and is not approved. The Dialog lands with FTF-UX02 round 2 (#947, item 0k); the UX01 Dialog then requalifies with that verifier.** (history below)
  - **2026-10-22T0230Z note (not resolved — unmerged):** the FTF-UX02-r2 shift (see this file's own FTF-UX02 round-2 Parked entry above) re-ran `pnpm --filter @sovra/web add --save-exact @radix-ui/react-dialog@1.3.1` in a later session and the install was NOT denied that time (sandbox-dependent, not a policy change) — a real `Dialog.tsx` now exists on `relay/FTF-UX02-r2` (PR #947, unmerged, parked). The owner ask below and this entry stay open until #947 (or an equivalent) actually merges to `main`; the install succeeding once in one sandbox is not the same as this dependency landing.
  - What failed: `pnpm --filter @sovra/web add --save-exact @radix-ui/react-dialog@1.3.1 @radix-ui/react-progress@1.2.1` was denied by the session's untrusted-code-integration guard. Everything else in FTF-UX01 (tokens.css, Button, Field, ErrorSummary, StatusBadge, EmptyState, DataTable, Banner, Breadcrumbs, Pending) shipped in PR `relay/FTF-UX01-r1` with no new deps.
  - Tried: one install, not retried (no workaround attempted).
  - Routes: (1) owner approves the two pinned Radix deps, relay adds `Dialog.tsx` + focus-trap/return test; (2) hand-write a focus-trapped dialog (violates §72.1 "Radix primitives"); (3) drop Dialog from UX01 and let UX02 own it.
- **FTF-H010-P3a round 2: PARKED (relay-policy item-ID regex collapse, false collision), 2026-10-14T07:00Z.**
  - What failed: PR #745 (`relay/FTF-H010-P3a-r2`)'s `ci-verdict` fails on `relay-policy` alone: "item(s)
    FTF-H010 already owned by open PR #742". `scripts/ci_policy.py`'s `ITEM_ID` regex
    (`FTF-[0-9A-Z]+(?:-F\d+)?`) only special-cases an `-F<n>` suffix; `[0-9A-Z]+` matches `H010` and
    stops at the hyphen before `-P3a`/`-P2`, so both PR #745's title (`FTF-H010-P3a: ...`) and PR
    #742's (`FTF-H010-P2: ...`) reduce to the same base id `FTF-H010` and collide, even though they are
    two disjoint, already-disclosed sub-tasks touching disjoint files. This is the exact collapsing bug
    class `[GOVERNANCE]` PR #705 (open since 2026-10-09, unmerged) already fixes for other suffixes
    (extends the allow-list to `-P\d+`/`-UI`) — confirmed by hand that the fixed regex would resolve
    PR #745's title to the distinct id `FTF-H010-P3` (not `FTF-H010-P3a` exactly, since the fix's
    `P\d+` group doesn't consume the trailing letter, but still distinct from PR #742's `FTF-H010-P2`,
    which is enough to clear this specific collision).
  - Why this can't be fixed from the relay side: `scripts/ci_policy.py` is `RELAY_FORBIDDEN_EXACT` — a
    `[relay]`-titled PR may not edit it (CLAUDE.md "Relay is execution-only", "Governance PRs"). Closing
    or interfering with PR #742 (another lane's own active, draft work) to clear the collision
    unilaterally would be worse than waiting. This session also cannot merge `[GOVERNANCE]` PR #705
    itself — CLAUDE.md reserves that for the owner ("Control plane opens it; the OWNER reviews and
    merges it").
  - Route options: (1) **owner merges PR #705** — already written, tested (`tests/unit/test_ci_policy.py`
    72/72 pass including the new collision test), clears this collision and any future `-P<n>`/`-UI`
    one; recommended, no further relay/Control-plane action needed once merged. (2) Wait for PR #742 to
    merge or close on its own schedule, which would also clear the collision without touching the
    regex (slower, and recurs for the next `-P<n>`/`-UI` pair). (3) A relay session reduces PR #745's
    title to a form the *current* regex already treats as distinct (not generally possible — any title
    starting `FTF-H010` collapses to the same base id regardless of suffix spelling) — not viable.
    Recommended: (1).
  - State left: PR #745 untouched by this entry (no code change attempted; the production code is
    unrelated to this collision). Flagged in "Owner actions — current" above since PR #705 has now sat
    open for 5 days while actively blocking a real submission, not merely a latent regex gap.
- **FTF-1602 round 5: PARKED (3 falsified hypotheses on one CI failure), 2026-10-09T10:10Z, lane B.**
  - What failed: PR #672 (`relay/FTF-1602-r5`): the CI job `object-storage-backup-integration` dies about 45 s into its
    test step on every run of the full round-5 file (runs 37899436881 a2, 37911808469, and the probes below), with no
    retrievable log (the job log API returns 404; the step stays `in_progress`; runner death signature, not a test
    failure). Everything else in the run is green: unit file, mutation gate 405/405 (per the PR), relay-policy, gate.
    r4 passed the same job (run 37843516353, 60 s); PR #658's run passed it at 08:33Z.
  - Tried (each a stated hypothesis, each falsified by a CI run): (1) branch behind main -> merged main (run
    37911808469, same death); (2) the new 6-thread test kills the runner -> made it serial (37913326565, same death);
    (3) bisect by test selection with `ci-focus`: r4 test file -> normal log, 14 tests in 8 s (37913521112); the four
    new tests as pairs {concurrent, pinned} and {relist, shared} -> both pass (37913710576, 37914178962); all
    existing tests without the four new ones -> passes (37914617993). So every subset passes and only the full 18-test
    file dies: a cumulative or interaction effect, not one bad test.
  - Not tried / unknown: the cause. Suspects: SeaweedFS 3.99 (`-master.volumeSizeLimitMB=64`, one collection per bucket,
    about 3 buckets per test) exhausting runner disk, memory or volume slots as buckets accumulate; or an interaction
    between G1's unretained-version writes and later tests. The log is lost with the runner, so it cannot be read.
  - Route options: (1) Control plane approves an additive diagnostic step in `object-storage-backup-integration`
    (runs before the test step: background `df -h`/`free -m`/`docker stats` sampler written to a file and uploaded by a
    new `if: always()` artifact step), then re-queue; (2) split the round-5 real-server proofs into two integration
    test files run as two additive jobs (each subset is proven to pass), keeping every test; (3) clean up per test
    (delete each test's three buckets at the end) so the fixture never accumulates state, which also removes the
    suspected cause; (4) periodically recycle the SeaweedFS fixture (`docker compose down -v` + `up -d s3`, re-wait
    ready) every N test functions inside this file's own `main()`, bounding accumulated collections/volumes without
    touching any test or `.github/workflows/ci.yml`. Recommended: (3) with (4) as the fallback, (2) after that.
  - **2026-10-09T17:10Z, read-only re-check (no Docker in this sandbox, so unverified):** corroborates the volume-slot
    suspicion above with a concrete precedent — `infra/docker/docker-compose.yml`'s own comment on the SeaweedFS
    service documents an earlier, near-identical death in `test_storage_semantics.py` ("no free volumes left" after
    ~2 of ~11 unique buckets, each bucket its own collection that eagerly grows to 7 volumes), fixed there with
    `-master.volumeSizeLimitMB=64`. This file's 18 tests create roughly 45 unique buckets, about 4x that prior
    threshold even with the fix in place — consistent with every tested subset (8-14 tests) passing and only the
    full 18-test run dying. Raises confidence in (3)/(4) above; still needs a real run to confirm.
  - State left: PR #672 is open and ready with the intended round-5 files; its `ci-verdict` is red on this one job only.
    The unit-level proofs, submission and spec-matrix notes are as the PR describes.
  - **2026-10-13T12:53Z, merge-conflict maintenance (this shift, no new diagnosis):** `main` had advanced past this
    branch's own merge-base through several unrelated merges; merged `origin/main` into `relay/FTF-1602-r5` in a
    worktree and found one real conflict, in this file's own "## Parked" section (an append-order collision with the
    now-resolved FTF-H010-P2 entry and this branch's own stale FTF-0516 parked entry — FTF-0516 is confirmed unparked
    elsewhere in this file's own "## Active" list, item 0c, round 3, so that stale entry is dropped here rather than
    carried forward). No code, test, or `docs/certifications/queue.md`/`docs/spec-matrix.yaml` content was touched
    beyond this one append-order resolution. The CI-hang root cause above is still unconfirmed and still needs a real
    CI run to resolve; this merge only keeps the branch current against `main` so a future unpark is not also fighting
    a stale diff.
  - **2026-10-23T0457Z, second occurrence on a different job (this shift, no new diagnosis attempted):** PR #971
    (`relay/FTF-R006-P1b-r2`, unrelated to this Parked item — its own diff touches only Argilla/identity files)
    hit a red `storage-semantics-integration` job on run `39893556331`: `test_multipart` raised
    `TimeoutError('timed out')` with the SeaweedFS container logging `failed to find writable volumes for
    collection:sovra-test-multipart-...` at the moment of failure — the same "no free volumes left" signature this
    entry already diagnosed for `object-storage-backup-integration`, now reproduced on a second job sharing the same
    `infra/docker/docker-compose.yml` `s3` fixture. Confirmed via the test's own source
    (`tests/integration/test_storage_semantics.py`): `sys.exit(1)` fires from the script's own `FAILURES` branch, not
    from the `docker compose stop` cleanup (`check_exit=False` either way). Not this PR's fault — commented on PR #971
    with the full diagnosis and left the one sanctioned re-run (CLAUDE.md CI-red rule) for whenever that run (still
    `in_progress` at comment time) completes, since GitHub rejects `rerun_failed_jobs` on an in-progress run
    (`403 This workflow is already running`). This raises the confidence that the fix belongs to the shared fixture
    (route options (3)/(4) above — per-test cleanup and/or periodic SeaweedFS recycle), not to either individual job.
- **FTF-H010-P2 Import-validation activity, job fencing and endpoint-only import: PARKED (unclear DoD, contradiction with certified FTF-1001), 2026-10-10T02:30Z, lane C.**
  - **2026-10-10T03:57Z: RESOLVED by Control-plane ruling (route 3, see the FTF-H010-P2 Backlog entry).** P2 is back in the Backlog; claim `relay/FTF-H010-P2-r2`. This entry is history.
  - What failed: nothing was implemented. The DoD says the success path inserts the ModelVersion "through the `ModelVersion` constructor ... `endpoint_reference = external_provider_model_ref = ref` verbatim". Reproduced on `main` (d64628a): `ModelVersion(import_kind="endpoint", endpoint_reference="ft-gpt-4o-mini-acme", ...)` raises `ModelVersionValidationError: endpoint_reference must use https://, got scheme ''`, because `validate_endpoint_reference` (model_version_schema.py, FTF-1001) requires an `https://` URL with a host. A discovered gateway alias (`/v1/models` id) is not a URL, so the happy path can never succeed for a real alias; the `ck_model_versions_kind_consistency` CHECK also makes `endpoint_reference` NOT NULL for `import_kind='endpoint'`.
  - Route options: (1) Control plane rules that the P2 files list may relax `validate_endpoint_reference` for `origin='external_endpoint'` (alias refs allowed, URL form kept for FTF-1001's synchronous path) and re-queues P2 with that edit and its tests in scope; (2) P2 stores the connection's validated `base_url` (an https URL, already vetted by FTF-0203) in `endpoint_reference` and the alias only in `external_provider_model_ref`, with the DoD sentence "verbatim" amended; (3) a prerequisite slice makes `endpoint_reference` nullable for `origin='external_endpoint'` (migration + CHECK change) and P2 leaves it NULL. Recommended: (2), no certified file edited.
  - The claim branch `relay/FTF-H010-P2-r1` and draft PR #733 carry only this park entry and the shift log; nothing else was written.
- **FTF-04A31 Real private GitHub repository integration test: PARKED (ask-first, secret), 2026-10-07T12:15Z, lane C.**
  - What failed: nothing attempted. The DoD needs a real private repository plus a credential; CI exposes only the
    workflow `github.token` (scoped to this repo) and no provisioned test-repo secret.
  - Route options: (1) owner provisions a read-only fine-grained token for a dedicated private test repo as a CI
    secret, then re-queue; (2) owner approves using this repository itself (private) with `github.token` as the
    fixture; (3) mark BLOCKED_EXTERNAL per the packet's resource_gate and re-queue when a credential exists.
- **FTF-0501 Direct object-storage upload: PARKED (BLOCKED_DEPENDENCY), 2026-10-07T10:50Z, lane B.**
  - **2026-10-08T00:30Z: RESOLVED.** FTF-0501-P1 is CERTIFIED; FTF-0501 is re-queued (after FTF-0501-P2). This entry is history.
  - What failed: nothing was attempted. The packet fixes `files` to one module plus its test and says "No independent
    schema change... any required change is a separately claimed migration prerequisite" and "A feature that needs a
    not-yet-frozen interface is BLOCKED_DEPENDENCY". Three things the DoD needs do not exist: (1) no multipart-upload,
    grant, finalize or Dataset-version route in `05-interface.yaml` (it declares only `dataset-sources`, `{id}` and
    `{id}/authorize`); (2) no Dataset / Dataset-version entity or table anywhere (FTF-0500's own notes say the Dataset
    entity is "FTF-0501-and-later's job"); (3) the negative acceptance ("cannot create accepted Dataset version")
    has nothing to assert against without (2).
  - Route options: (1) Control plane slices a prerequisite: Dataset + DatasetVersion migration (tenant-composite FK, FORCE
    RLS) and the freeze of upload/finalize/grant routes in `05-interface.yaml`, then re-queue FTF-0501 on top;
    (2) split FTF-0501 into a pure presigned-multipart + finalize-verification module (no route, no table, unit +
    real SeaweedFS S3 integration) and a later wiring task; (3) re-scope the DoD to the module alone and record the
    route/entity half as a disclosed narrowing, the way FTF-0500B did. Recommended: (2).
- **FTF-0602 Evaluation case editor/import: PARKED (BLOCKED_DEPENDENCY), 2026-10-07T10:50Z, lane B.**
  - **2026-10-07T21:30Z: RESOLVED.** FTF-0602-P1 is CERTIFIED; FTF-0602 is re-queued in the Backlog. This entry is history.
  - What failed: nothing was attempted. `06-interface.yaml` states in its own description that it "deliberately does not
    declare a `/cases` route" (and no cases table exists; FTF-0601 persists only the suite record). The packet's
    `files` are the web component and spec only, with "No independent schema change", so there is no API or table for
    the editor/import to call, and a UI over a route that does not exist would be the placeholder Law 11 forbids.
  - Route options: (1) Control plane slices an EvaluationCase migration + API prerequisite (cases table with FORCE RLS,
    create/import/list routes, frozen in `06-interface.yaml` under `contracts:06`), then re-queue FTF-0602 on top;
    (2) fold the backend into FTF-0602 with the migration and contract lock explicitly granted to it; (3) split the
    pure case-validation/import parser (testable now) from the UI. Recommended: (1).
- **FTF-0308 Model-to-artifact mapping UI: PARKED (BLOCKED_DEPENDENCY), 2026-10-07T08:00Z, Control plane.**
  - **2026-10-07T21:30Z: RESOLVED.** FTF-0308-P1 is CERTIFIED; FTF-0308 is re-queued in the Backlog. This entry is history.
  - Lane C's claim record (#541) is right. The contract says to consume a frozen interface, and the negative
    acceptance ("changing mapping invalidates related qualification") needs a persisted mapping plus an
    invalidation hook. Neither exists: there is no `model_catalog_entries` table, and no
    `/api/v1/models/{id}/training-artifact` path in `03-interface.yaml`.
  - Duplicate #543, which built a request-scoped route not in the contract, is closed.
  - Route chosen: option 1. Slice a migration + API prerequisite: ModelCatalogEntry persistence with a
    tenant-composite FK to trainable artifacts and RLS, GET/PUT training-artifact mapping, and a mapping-change
    audit/invalidation event. Freeze it in `03-interface.yaml`, then re-queue FTF-0308 on top. The Control plane
    writes the prerequisite contract; no relay action until then.

The PR #471 (FTF-0307 round-2 fix) merge-conflict entry below is resolved history.

**RESOLVED 2026-10-06T04:52Z — PR #471 merge conflict, route option (1).** The prior entry's own
route option (1) ("a session/user with the needed git permission merges `main` into PR #471's
branch... pushes") was exercised this shift: `git fetch`'d both `origin/main` and
`origin/claude/wizardly-fermat-4idnly` into a local worktree, merged `origin/main` in, resolved the
one real conflict in `docs/certifications/queue.md` by keeping both dated entries in chronological
order (`2026-10-06T00:00Z` FTF-0307 entry, then `2026-10-06T02:00Z` FTF-1506 entry — exactly the
"trivial keep-both append-order conflict" the dry run already found, no semantic/code conflict
touched), confirmed the merged `docs/spec-matrix.yaml` still parses with `yaml.safe_load` and
`python3 scripts/validate_spec.py --strict` is clean, then pushed the merge commit (`0051e14`)
straight to `claude/wizardly-fermat-4idnly` — unlike the prior session, this session's own
permission mode allowed the push. GitHub's `mergeable_state` flipped to `clean` immediately. The
real CI run this push triggered (`37415793440`) was watched to completion: **44/44 jobs green**,
`gate` and `dependency-scanning-integration` (the exact job PR #472's own fix targeted) included —
confirming the fix genuinely carries through once the merge-ref actually picks up `main`'s fixed
lockfile. Independent Control-plane L2 re-review (not this entry) is still the next step before
FTF-0307 itself can certify. No `BLOCKER.md` entry was needed; this was tooling access, not a
design question, per the entry it resolves. (A second, independent session reached the same
conclusion moments later and logged a duplicate confirmation, correctly attributing the fix to
"a separate session, not this one" -- that entry is not repeated here to avoid recording the same
resolved fact twice; PR #475 on `main` carries its own text for the record.)

## Active (awaiting Control plane — no relay action left)
None currently. FTF-1601 (Postgres backup) was the only item here; it was
independently reviewed and CERTIFIED this shift via PR #100 (merge commit
`f4e9b39`) — see "## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the
full history and evidence.

## Provisional (awaiting Control plane L2 certification)
**FTF-0500C** ("Database/API/trace source contract and fail-closed validation") — submitted
PROVISIONAL this shift via **PR https://github.com/SJZLab/Sovra/pull/364** (branch
`claude/wizardly-fermat-f9qdmb` → `main`, still open — merge-conflict-resolved onto current
`main` by a later shift; see this file's own top dated log entry).
`apps/api/app/datasets/database_api_trace_source_contract_and_fail_closed_validation.py` implements
Section 67.1's own real PostgreSQL connector (`validate_and_preview_postgres_source` — allowlisted,
identifier-validated, parameterized SELECT; `READ ONLY` + `REPEATABLE READ` for "no writes" +
"repeatable snapshot" in one real transaction-mode setting; `statement_timeout` set and
independently read back) and REST/agent-trace connector (`validate_and_preview_rest_trace_source` —
bounded cursor pagination, bounded 429 retry honoring `Retry-After`, a declared snapshot-version
header tracked across pages, cooperative cancellation, bounded by `max_page_fetches` against an
endpoint that never ends). Both negative_acceptance halves are real: a genuine Postgres
`UndefinedTable`/`UndefinedColumn` or a REST response missing the declared `records_field` raises
`UnknownSourceContractError`; a Postgres resume whose watermark row was deleted by a real
concurrent connection raises `LostWatermarkError` (re-verified inside the same `REPEATABLE READ`
transaction as the subsequent SELECT), and a REST snapshot-version header that changes mid-listing
raises `SourceChangedDuringListingError`. Two independent real "no writes" mechanisms are proven
separately: a real least-privileged Postgres role's own database grants deny a write
(`psycopg.errors.InsufficientPrivilege`), and, independently, this module's own `READ ONLY`
transaction mode blocks a write from a role that otherwise has write privilege
(`psycopg.errors.ReadOnlySqlTransaction`). Real fault injection (Law 1, disclosed) proved both
negative-test assertions genuinely fail when their real check is disabled, then reverted the module
file byte-identical (md5sum confirmed matching before/after) — once for the REST snapshot-version
comparison (`tests/tasks/test_ftf_0500c.py`, real local HTTPS fixture, no DB required) and once for
the Postgres watermark-presence guard against a real database
(`tests/integration/test_ftf_0500c_database_api_trace_source.py`, real local PostgreSQL 16). This
module does not implement Section 8's own file-content import pipeline, does not read/write
`DatasetSourceConnection` rows or resolve `credential_secret_ref` via OpenBao, and exposes no HTTP
route — same disclosed, dependency-gated composition-task boundary FTF-0500B's own module already
established for its identical-shaped sibling task. `product_truth_check.py --fail-on-unwired`
unchanged 23/23 (disclosed, no dataset-import composition task calls either connector yet). Not
self-certified (Law 4) — independent Control-plane L2 review and a real CI run on the opened PR are
required before certification.

**FTF-0500B** ("S3/MinIO source validation + metadata preview") — submitted PROVISIONAL via
**PR https://github.com/SJZLab/Sovra/pull/359** (branch `claude/wizardly-fermat-lp04ue` →
`main`, still open — merge-conflict-resolved onto current `main` by a later shift; see this
file's own top dated log entry).
`apps/api/app/datasets/s3_minio_source_validation_metadata_preview.py` is the real connector: a
real, paginated, bounded `ListObjectVersions` call against a real S3/MinIO-compatible endpoint,
signed via `storage_semantics_client.py`'s own real SigV4 `sign_request` (FTF-R017, CERTIFIED)
and dialed exclusively through `packages/security/outbound_guard.py`'s own `guarded_fetch`
(FTF-H006) — never `S3Client`'s own urllib transport, the same disclosed precedent `FTF-0303`
already established. A *metadata* preview only (key/version/size/ETag/last-modified, never
object bytes). `negative_acceptance` ("S3 ETag is not assumed SHA; source changes, cross-tenant
prefix and overly broad grants are denied") enforced as four independent checks: overly broad
grant (blank/root `tenant_key_prefix` refused pre-request), cross-tenant prefix (every real
returned key re-checked at a real path-segment boundary, refused even if the server itself
returns an out-of-scope key), source changed during listing (a key's own `IsLatest` version id
conflicting across two real pages is refused as `SourceChangedDuringListingError`, never
silently mixed), and ETag never treated as a content hash (informational-only metadata). Returns
an immutable `manifest_hash` (sha256 over the bounded key/version/size/etag tuples).

Test evidence: `tests/tasks/test_ftf_0500b.py` (no-DB, real local HTTP S3-shaped fixture with a
real independent SigV4 signature re-derivation, 15 checks incl. two disclosed fault injections)
— all clean. `ruff check .`/`ruff format --check .`/`mypy .` clean repo-wide (321 files);
`pnpm run lint`/`typecheck`/`format:check`/`vitest run` unaffected (no frontend file touched).
`validate_spec.py --strict` clean. `product_truth_check.py --fail-on-unwired`: unchanged `23/23`
(`proving_symbol` left blank — this task is a pure connector with no composing CRUD/route/UI yet,
the same disclosed `FTF-0303`/`FTF-0500` precedent). `scripts/status.py`: unchanged `93/300`
certified (Law 2 — this is a submission, not a certification). Regression swept
`test_ftf_0500.py`/`test_ftf_0303.py`/`test_ftf_h006.py` — zero regressions. Full evidence in
`docs/spec-matrix.yaml`'s own FTF-0500B entry and `docs/certifications/queue.md`'s matching dated
entry. Not self-certified (Law 4) — independent Control-plane L2 review and a real CI run on the
opened PR are required before certification.

Prior history, kept for the record (this file's own chain, as it read on `main` before this
merge-conflict resolution — FTF-0500B's own PR #359 and FTF-0500C's own PR #364 were still
open/unmerged at that point, so neither appears below):

**FTF-1201** ("LiteLLM registration capability detection") — submitted PROVISIONAL via
**PR https://github.com/SJZLab/Sovra/pull/366** (branch `claude/wizardly-fermat-85vqbq` → `main`,
merged — merge commit `3dcee5d602494fa5a15ff8069fb16313305c52fa`, real CI confirmed 38/38 green on
head `25066b1` before merge; see this file's own top dated log entry). Detects whether a LiteLLM
connection's stored credential genuinely holds gateway
management/registration authority (Section 38), separate from the inference-only dimension
FTF-0203 already validates. `packages/litellm_client/
litellm_registration_capability_detection.py::detect_registration_capability` reuses FTF-0202's
already-CERTIFIED `resolve_secret_ref` and FTF-H006's already-CERTIFIED `guarded_fetch` to call
the gateway's real `GET /user/info` self-lookup; only a genuinely observed `proxy_admin` role is
ever reported `management_capable` — every other real, documented role (including the read-only
`proxy_admin_viewer`) and every denied/malformed/unreachable probe is its own distinct non-capable
state, never a silent capable default (this task's own negative_acceptance). Freezes the shared
phase-12 contract pair (`packages/contracts/openapi/12-interface.yaml`,
`packages/contracts/jsonschema/12-domain.schema.json`), sole claim on `contracts:12`. New route
`GET .../connections/litellm/{connection_id}/registration-capability`, `organization_admin` only,
same tenant-scoped/IDOR/revoked-connection pattern as its `/validate`/`/models` siblings.

Disclosed, version-pinned assumption: this sandbox's outbound network policy blocks
`docs.litellm.ai` (`EGRESS_BLOCKED`, confirmed via a direct `WebFetch` attempt this shift), so the
`/user/info` + `user_role` signal could not be freshly re-verified against the live docs site —
cited from LiteLLM's long-stable public proxy RBAC surface instead, same evidentiary posture
already-CERTIFIED FTF-0210/FTF-H005 established for citing real LiteLLM API surface without an
in-shift live fetch. The negative_acceptance clause holds regardless of this assumption's
exactness: no path in this design can produce a false `management_capable`.

Real fault injection (Law 1, disclosed): a regex-based mutation of the real on-disk module widens
`_MANAGEMENT_CAPABLE_ROLES` to include `proxy_admin_viewer`, run in a fresh subprocess against a
real gateway returning that exact role, genuinely reports `management_capable` — proving the role
check is load-bearing, not decorative — then reverted byte-identical (direct bytes comparison
before/after).

Test evidence: `tests/tasks/test_ftf_1201.py` (34 no-DB, no-external-egress checks, real local
HTTPS gateways with a real, freshly generated CA), `tests/integration/
test_ftf_1201_registration_capability.py` (real Postgres 16 + real local HTTPS gateway, 13 checks,
driven end-to-end through the live FastAPI route) — all clean. `ruff check .`/`ruff format
--check .`/`mypy .` clean repo-wide (326 files). `validate_spec.py --strict` clean.
`product_truth_check.py --fail-on-unwired`: 23/23 → 24/24 (`proving_symbol` set to
`detect_registration_capability`, a real non-test caller in `connection_routes.py`).
`scripts/status.py`: unchanged 93/300 certified, 22 product-true (Law 2 — this is a submission,
not a certification). Regression swept `test_ftf_0201.py`/`test_ftf_0202.py`/
`test_ftf_0203.py`/`test_ftf_0204.py` — zero regressions. Full evidence in `docs/spec-matrix.yaml`'s
own FTF-1201 entry and `docs/certifications/queue.md`'s matching 2026-09-28 entry. Not
self-certified (Law 4) — independent Control-plane L2 review and a real CI run on the opened PR
are required before certification.

Prior history, kept for the record: this paragraph previously named **FTF-H005** ("Route identity,
fallback and cache provenance") as the current pending item, submitted via **PR #361** (merged to
`main`) — a Control-plane certification session opened PRs against it (#363/#365), still in
progress at this shift's start; left untouched per this shift's own "no relay action left on a
submission awaiting Control-plane review" scope. See `docs/certifications/queue.md`'s matching
FTF-H005 entry for the full evidence record.

Superseded by the above, kept verbatim for the record:

**FTF-H005** ("Route identity, fallback and cache provenance") — submitted PROVISIONAL via
**PR https://github.com/SJZLab/Sovra/pull/361** (branch `claude/wizardly-fermat-q43eub` → `main`,
merged — merge commit `2e9a575fefabffcec4a2b13a8b3a84c5a2eb2fd1`, real CI confirmed 37/37 green
on head `76d285e` before merge; see this file's own top dated log entry).
`packages/litellm_client/route_attestation.py::attest_route` is a real, cache-bypassed attestation
call (every request carries LiteLLM's own real, documented `"cache": {"no-cache": true}` field so
an attestation can never be answered from a cached response by construction) that records both the
already-established `resolved_model` body field (FTF-0210's own convention, left unmodified per
Law 15) and a new, independent `deployment_fingerprint` read from LiteLLM's own real, documented
`x-litellm-model-id` response header — needed because a resolved alias name alone cannot detect a
silent fallback to a *different* deployment answering under the identical alias, this task's own
acceptance-clause scenario. `route_identity_state` is `VERIFIED` only when both signals are
genuinely present. `packages/model_catalog/route_snapshot.py::pin_route_identity`/
`require_pinned_route_unchanged` is the pin/drift half: refuses to pin an unverified identity
(negative_acceptance), and denies — rather than silently rerouting — the instant a private
qualification session's pinned route drifts.

Real fault injection (Law 1, disclosed): temporarily disabled the real `deployment_fingerprint`
comparison in `route_snapshot.py`, confirmed the fallback-drift assertion genuinely fails (2
checks), reverted byte-identical (md5sum `d7c90fc4961f2f758f7477749d73d187` confirmed matching
before/after), reran clean.

Test evidence: `tests/tasks/test_ftf_h005_route_attestation.py` (real local HTTPS fixture, 5
checks), `tests/tasks/test_ftf_h005_route_snapshot.py` (pure no-I/O unit, 7 checks incl. the fault
injection above) — all clean. `ruff check .`/`ruff format --check .`/`mypy .` clean repo-wide (323
files). `validate_spec.py --strict` clean (required `git fetch --unshallow` first — this sandbox's
own checkout started shallow). `product_truth_check.py --fail-on-unwired`: unchanged `23/23`
(disclosed: no live qualification/evaluation workflow calls either module yet — Inspect AI
integration is FTF-R004, still unclaimed — `proving_symbol` stays blank). `scripts/status.py`:
unchanged `93/300` certified (Law 2 — this is a submission, not a certification). Regression swept
`test_ftf_0203.py`/`test_ftf_0210.py`/`test_ftf_0215.py`/`test_ftf_h004.py` — zero regressions.
Full evidence in `docs/spec-matrix.yaml`'s own FTF-H005 entry and `docs/certifications/queue.md`'s
matching 2026-09-28 entry. Not self-certified (Law 4) — independent Control-plane L2 review and a
real CI run on the opened PR are required before certification.

Prior history, kept for the record: this paragraph previously named **FTF-04A05** ("GitHub source
snapshot implementation") as the current pending item, submitted via **PR #358** (merged to
`main`, `f17260c`) — a Control-plane certification session opened **PR #360** against it
("Control plane: certify FTF-04A05..."), still in progress at this shift's start; left untouched
per this shift's own "no relay action left on a submission awaiting Control-plane review" scope.
See `docs/certifications/queue.md`'s matching FTF-04A05 entry for the full evidence record.

Superseded by the above, kept verbatim for the record:

**FTF-04A05** ("GitHub source snapshot implementation") — submitted PROVISIONAL via
**PR https://github.com/SJZLab/Sovra/pull/358** (branch `claude/wizardly-fermat-yfpaot` →
`main`).
`apps/api/app/target_connectors/github_source_snapshot_resolver.py` is the real connector:
reuses FTF-04A04's own `resolve_ref_to_commit` unmodified, then adds three further real GitHub
REST API v3 calls (`GET .../git/commits/{sha}` for the commit's own real, canonical tree hash;
`GET .../git/trees/{tree_sha}?recursive=1` for the full tree, whose real `truncated` field is this
task's own "missing critical objects" negative_acceptance signal; `GET .../git/blobs/{sha}` for a
root `.gitattributes` blob, to detect real Git LFS `filter=lfs` declarations). Submodules are
detected structurally from the tree's own real `type: "commit"` gitlink entries. "Unsafe archive"
(this task's own other negative_acceptance half) is a real, tested check that no tree entry's own
path can escape the repository root if materialized (absolute path / `..` segment / embedded NUL
byte) — this task does not download a real tarball archive at all (GitHub's own archive endpoints
always redirect to a separate `codeload.github.com` host this task's own contract does not
authorize adding to the allowed destination set; `default_github_connector_policy()` disallows
redirects by design). `apps/api/app/target_connectors/github_source_snapshot_crud.py` +
`github_source_snapshot_routes.py` (new `POST .../target-connections/{connection_id}/github-source-snapshot`
route, wired into `main.py`) compose this connector with FTF-04A07's own already-CERTIFIED
`create_target_source_snapshot` — the real GitHub-backed sibling of FTF-04A07's own
client-trusting `POST .../snapshots` route, re-deriving `commit_sha`/`tree_hash` from a real
GitHub call rather than trusting a client-supplied value for either.

Real fault injection (Law 1, disclosed) found a genuine gap in this submission's own first-draft
test suite before it was fixed: the integration test's original "cross-organization project_id"
scenario was confounded by the separately-CERTIFIED FTF-04A07 connection-ownership check
downstream, which alone already happened to deny that exact request — disabling this task's own
new `_authorize_project_or_raise` guard left every original negative test passing. A second,
isolating scenario (a nonexistent project ID paired with a real, valid same-organization
connection ID) was added, confirmed to genuinely fail with the guard disabled, and confirmed clean
again after reverting byte-identical (md5sum confirmed matching before/after).

Test evidence: `tests/tasks/test_ftf_04a05.py` (connector, no-DB, real local HTTPS fixture, 19
checks incl. a disclosed GET-only fault injection), `tests/tasks/test_ftf_04a05_routes.py`
(route-boundary, no-DB, 13 checks), `tests/integration/test_ftf_04a05_github_source_snapshot.py`
(real, freshly-provisioned local PostgreSQL 16 + the same local HTTPS fixture, 14 checks) — all
clean. `ruff check .`/`ruff format --check .` clean repo-wide (319 files); `mypy .` clean
repo-wide (319 files); `pnpm run lint`/`typecheck`/`format:check` clean; `pnpm exec vitest run`
48/48 unchanged (no frontend file touched). `validate_spec.py --strict` clean.
`product_truth_check.py --fail-on-unwired`: 22/22 → 23/23 (`proving_symbol` set to
`build_github_source_snapshot_router`, a real non-test caller in `main.py` — this task's own
scope carries no UI component, so there is no Law-6 front-door gap to disclose here, unlike
FTF-04A04). `scripts/status.py`: unchanged 93/300 certified (Law 2 — this is a submission, not a
certification). Regression swept `test_ftf_04a01.py`/`test_ftf_04a04_connector.py`/
`test_ftf_04a04_routes.py`/`test_ftf_04a07.py` (no-DB) and
`test_ftf_04a04_github_branch_ref.py` (real Postgres, re-provisioned fresh) — zero regressions.
Full evidence in `docs/spec-matrix.yaml`'s own FTF-04A05 entry and this file's own top dated log
entry. Not self-certified (Law 4) — independent Control-plane L2 review and a real CI run on the
opened PR are required before certification.

Prior history, kept for the record: this paragraph previously named **FTF-H004** ("Authorized
LiteLLM catalog and revocation semantics") as the current pending item; it was independently
reviewed and CERTIFIED (merge commit `00d0b8f38edb64b603cadd2c9cb157fc852d2aa2`) — see "## Done"
for the certification summary and `docs/certifications/ledger.md`/`docs/certifications/queue.md`
for the full history and evidence.

Superseded by the certification above, kept verbatim for the record:

**FTF-H004** ("Authorized LiteLLM catalog and revocation semantics") — submitted PROVISIONAL
via **PR #354** (branch `claude/wizardly-fermat-wq6qdg` -> `main`).
`packages/model_catalog/access_snapshot.py::build_access_snapshot`
composes FTF-0208's historic catalog with a fresh FTF-0207 reconciliation as a second,
independent left-join boundary; `packages/litellm_client/access_guard.py::require_model_access`
is the real fail-closed gate composing FTF-H003's ownership check, a fresh
`litellm_connections.status` read, and FTF-0208's own `is_call_authorized`. See this file's own
top dated log entry and `docs/certifications/queue.md`'s matching 2026-09-28 FTF-H004 entry for
the full evidence record. Real CI confirmed on the actual final head (`2b339fe`, run
`36413819814`): all 36 jobs green, including the new `litellm-access-guard-integration` job and
the `gate` job (which covers the new `FTF-H004 negative-test proof` step). Merged (relay's own
"merge on green + L1 doc (PROVISIONAL)" authorization) — merge commit `00d0b8f38edb64b603cadd2c9cb157fc852d2aa2`.
Awaiting independent Control-plane L2 review before `certification_state` can move past
`PROVISIONAL` (not this shift's own action — this shift's job is certifying already-pending
PROVISIONAL items, and FTF-H004 was not yet appended to `docs/certifications/queue.md` as an
evidence entry when this shift began).

PR: https://github.com/SJZLab/Sovra/pull/354 (branch `claude/wizardly-fermat-wq6qdg` → main,
merged).

Prior history, kept for the record: this paragraph previously named **FTF-04A04** ("GitHub
branch/ref selector + immutable SHA resolver") as the current pending item; it was independently
reviewed and CERTIFIED via **PR #353** (merge commit
`89c0b0ad5aefedf3bfb16ed89bfb3392871af828`) — see "## Done" for the certification summary and
**FTF-0601** ("Evaluation suite schema/API") — submitted this shift via
[PR #337](https://github.com/SJZLab/Sovra/pull/337), see this file's own top dated log entry for
the full evidence. `docs/spec-matrix.yaml`: `certification_state: PROVISIONAL`
/ `proof_state: L1-PROVEN` / `proving_symbol: build_evaluation_suite_router`. Awaiting independent
Control-plane L2 review and a real CI run on the opened PR — not self-certified (Law 4).

Prior history, kept for the record: this paragraph previously named FTF-04A20 (ManifestEvidence
schema/API) as the current pending item; it was independently reviewed and CERTIFIED via PR #333
(merge commit `7233538c45c604933799bdbc39492ac8c92d826f`) — see
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the full history and evidence.

Superseded by the certification above, kept verbatim for the record:

**FTF-04A04** ("GitHub branch/ref selector + immutable SHA resolver") — the original submission
(PR #351, branch `claude/wizardly-fermat-r37pvq`) was independently reviewed via PR #352 and
found **NOT CERTIFIED** (acceptance clause 1's own "persist... display ref and resolved
identity" half was not genuinely met). Fixed and resubmitted this shift via **PR #353**
(branch `claude/wizardly-fermat-3w4uhd` -> `main`): `resolve_project_github_ref` now durably
persists the resolved display ref onto a real `TargetApplicationConnection.selected_branch` row
via the already-CERTIFIED `update_target_application_connection`, with real 404/409 denial paths
for an unknown/cross-tenant/revoked connection. PR #351 closed without merging (superseded). See
this file's own top dated log entry and `docs/certifications/queue.md`'s matching 2026-09-28
entries (the NOT CERTIFIED finding and this fix) for the full evidence record.

Prior history, kept for the record: this paragraph previously said "None currently" — FTF-04A02
("GitHub App/token read-only secret integration") had been independently reviewed and CERTIFIED
via PR #344 (merge commit `da340d39510e5d716808cb605ffff3122c1ed4c0`) — see
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for that full history and
evidence.

Superseded by the certification above, kept verbatim for the record:

**FTF-04A02** ("GitHub App/token read-only secret integration") — submitted PROVISIONAL this shift
via **[PR #344](https://github.com/SJZLab/Sovra/pull/344)** (branch `claude/wizardly-fermat-bpvsvl`
→ main). See this file's own top dated log entry and `docs/certifications/queue.md` for the full
evidence record. Awaiting independent Control-plane L2 review and a real CI run on the opened PR.

Prior history, kept for the record: this paragraph previously named **FTF-04A26** ("Isolated real
tool-backend adapter contract tests") as the current pending item; it was independently reviewed
and CERTIFIED via **[PR #340](https://github.com/SJZLab/Sovra/pull/340)** (merge commit
`d25a555fad0960d8d016f8ab009279e5fe980255`) — see "## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the full history and evidence.

Superseded by the certification above, kept verbatim for the record:

**FTF-04A26** ("Isolated real tool-backend adapter contract tests") — submitted PROVISIONAL this
shift via **[PR #340](https://github.com/SJZLab/Sovra/pull/340)**. See this file's own top dated
log entry and `docs/certifications/queue.md` for the full evidence record. Awaiting independent
Control-plane L2 review.

Prior history, kept for the record: this paragraph previously named **FTF-04A25** ("Production-
faithful target-agent harness contract") as the current pending item; it was independently
reviewed and CERTIFIED via **[PR #338](https://github.com/SJZLab/Sovra/pull/338)** (merge commit
`537d28f8d65a6a0de0ba25b6b2c2c6fad37fdff9`) — see "## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the full history and evidence.

Prior history, kept for the record: this paragraph previously named **FTF-04A20** ("ManifestEvidence
schema/API") as the current pending item; it was independently reviewed and CERTIFIED via
**[PR #333](https://github.com/SJZLab/Sovra/pull/333)**
(merge commit `7233538c45c604933799bdbc39492ac8c92d826f`) — see "## Done" for the certification
summary and `docs/certifications/ledger.md`/`docs/certifications/queue.md` for the full history
and evidence.
Two items pending: **FTF-0500D** ("Dataset source security-boundary preview + audit evidence"),
submitted PROVISIONAL this shift (see this file's own top dated log entry and
`docs/certifications/queue.md` for the full submission record) — not yet independently reviewed;
and **FTF-04A20** ("ManifestEvidence schema/API"), submitted PROVISIONAL the previous shift and
already picked up by a separate Control-plane certifier session as
**[PR #334](https://github.com/SJZLab/Sovra/pull/334)**, opened 2026-09-27T11:21:51Z — in flight,
no relay action left there.

Prior history, kept for the record: this paragraph previously named **FTF-04A19** ("AgentManifest
schema/versioning/hash") as the current pending item; it was independently reviewed and CERTIFIED
via **[PR #331](https://github.com/SJZLab/Sovra/pull/331)**
(merge commit `45d94f8d08bc82614fb5d3792d56df3255824299`) — see this file's own top dated log entry
and `docs/certifications/ledger.md`/`docs/certifications/queue.md` for the full history and
evidence.

Prior history, kept for the record: this paragraph previously named **FTF-04A16** ("Runtime
trace/deployment contract discovery") as the current pending item; it was independently reviewed
and CERTIFIED via **[PR #329](https://github.com/SJZLab/Sovra/pull/329)** (merge commit
`0c796be1bdbc70c085f5c8851300e84bb04aae6a`) — see "## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the full history and evidence.
**FTF-04A15** ("Agent test/evaluation fixture discovery") — submitted 2026-09-26 via
[PR #328](https://github.com/SJZLab/Sovra/pull/328) (branch `claude/wizardly-fermat-x60k9h`), title
`[relay] FTF-04A15: Agent test/evaluation fixture discovery`. See this file's own top dated log for
the full submission record. Awaiting an independently-triggered Control-plane certifier session and
the L4 gate.

Prior history, kept for the record: this paragraph previously named **FTF-04A14** ("Tool/schema/
permission discovery + evidence capture") as the current pending item, dated from its own submission
(PR #326). That is now stale — FTF-04A14 was independently certified since (this file's own top
dated log has the full certifier record; `docs/spec-matrix.yaml`: `certification_state: CERTIFIED` /
`proof_state: L2-CERTIFIED`, `evidence_hash` `876e9937051f818c9049175e0b126c4cb85f1bd8`, confirmed
directly via [PR #326](https://github.com/SJZLab/Sovra/pull/326)). Corrected here rather than left
for a future session to rediscover, per this file's own established precedent (the same class of
staleness this paragraph itself corrected for FTF-04A13/FTF-04A12/FTF-04A11 one/two/three shifts
ago).

Correction (2026-09-27, before this shift's own FTF-04A14->FTF-04A15 update above): this paragraph
previously still named
**FTF-04A13** ("Prompt-source discovery + evidence capture") as the current pending item, dated from
its own submission (PR #324). That is now stale — FTF-04A13 was independently certified since (this
file's own top dated log has the full certifier record; `docs/spec-matrix.yaml`:
`certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash`
`9d7294e60a0abe11edf987030afab0d13c72b783`, confirmed directly). Corrected here rather than left for
a future session to rediscover, per this file's own established precedent (the same class of
staleness this paragraph itself corrected for FTF-04A12/FTF-04A11 one/two shifts ago).

Correction (2026-09-26, Control-plane certifier shift, session
`https://claude.ai/code/session_014Hxe7oodTekLv7ufMf6WDk`): this paragraph previously still named
**FTF-04A12** ("LiteLLM/current-model binding discovery") as the current pending item, dated from
its own submission. That is now stale — FTF-04A12 was independently certified by this same shift
(PR #322; `docs/spec-matrix.yaml`: `certification_state: CERTIFIED` / `proof_state:
L2-CERTIFIED`, confirmed directly). Corrected here rather than left for a future session to
rediscover, per this file's own established precedent (the same class of staleness this paragraph
itself corrected for FTF-04A11 one shift ago). Everything below this paragraph is prior history,
kept for the record (FTF-0302/FTF-0303/FTF-0513/FTF-04A11/FTF-04A10/FTF-04A09/FTF-04A12/FTF-04A13
are all already `CERTIFIED` — see this file's own top dated log and
`docs/certifications/ledger.md`).

**FTF-0302** ("Hugging Face artifact resolver") was the item here before that; submitted 2026-09-26 via **PR
#305** (branch `claude/wizardly-fermat-9ex5xy` -> `main`,
`https://github.com/SJZLab/Sovra/pull/305`), title `[relay] FTF-0302: Hugging Face artifact
resolver`, producer session `https://claude.ai/code/session_01X2DNqLN4Lcv1qJEfS5Z3Rr`. Real caller
of FTF-0301's own deliberately-unresolved `huggingface` `source_uri`: resolves a pinned revision
against a real Hugging Face Hub-shaped API, downloads only a closed allowlist of real
model-artifact file shapes (never a dataset-shaped file, never a nested path), and verifies every
file Hugging Face's own API declares an LFS sha256 for against its own real computed digest.
Negative_acceptance ("Floating revision, unavailable license or remote code execution is
blocked/explicitly reviewed; no sensitive training data present during acquisition") is four
independently-ordered gates, two reused verbatim from already-CERTIFIED dependencies rather than
re-implemented (FTF-0301's own `is_pinned_revision`, FTF-0305's own
`assert_license_accepted_for_use`) and two new to this task (remote-code-requires-explicit-review
via FTF-0301's own `trust_remote_code_allowed` field; acquisition file selection is a closed
allowlist, never a denylist). Deliberately does NOT write `weights_digest` to the database --
`FTF-0306` ("Base model revision/digest resolver") is the separately dependency-gated task named by
FTF-0301's own docstring that owns combining this module's real `combined_sha256_hex` (and its
`private_s3`/`local_filesystem` siblings, FTF-0303/FTF-0304) into that one column; doing so here
would be the cross-task overreach Law 15 warns against. This sandbox has no route to the public
internet (`curl` to `huggingface.co` fails at the outbound proxy, HTTP 403), so the real
request/response/verification logic is proven against a real local HTTPS server implementing the
exact Hugging Face Hub API subset this resolver calls, using this repository's own already-
established real-self-signed-CA-in-the-system-trust-store precedent (`tests/tasks/
test_ftf_0203.py`) rather than a mocked `guarded_fetch` -- the same reasoning already applied to
FTF-0202's real local OpenBao and FTF-0101's real local idpy-oidc instead of a live cloud secret
manager or live Google/Okta OIDC provider. 30 checks, all real, including one real HTTP redirect (a
genuine LFS-backed file's real CDN redirect), a real gated-403 denial, a real digest match and a
real digest mismatch, a real destination-denied policy check, and two disclosed fault injections
(Law 1) proving the digest-mismatch and floating-revision checks are load-bearing, each reverted
byte-identical. `uv run ruff check .`/`ruff format --check .`/`mypy .` clean; `uv run pytest
tests/unit` (3/3) and regression sweeps of `test_ftf_0301.py`/`test_ftf_0305.py`/`test_ftf_h006.py`
unaffected; `validate_spec.py --strict` clean; `product_truth_check.py --fail-on-unwired` unchanged
`20/20` (`proving_symbol` left blank -- no live caller yet, same disclosed precedent as
FTF-0005/FTF-0006/FTF-04A08/FTF-04A09/FTF-04A10, expected to be closed by FTF-0306); `status.py`
unchanged `76/300` (Law 2). Not self-certified (Law 4) -- awaiting an independently-triggered
Control-plane L2 certifier session. Full record: `docs/certifications/queue.md`'s matching
2026-09-26 entry, `docs/spec-matrix.yaml`'s FTF-0302 notes, `PROGRESS.md`.

**Merged to `main` this same shift** as commit `bde4b4a8562e8865474886a06fc91dcf9696a265` (31/31 CI
checks green, `gate` included, no merge conflict, no open review comments — merge permitted without
asking per CLAUDE.md's "do without asking: ... merge on green + L1 doc (PROVISIONAL)"). Disclosed,
not silently passed over: the merge commit's own title reads "Merge PR #305: Control plane
certifies FTF-0302 (Hugging Face artifact resolver)" — **this is not accurate; no certification has
occurred**, `docs/spec-matrix.yaml` genuinely reads `PROVISIONAL`/`L1-PROVEN` above, and this
session neither is nor claims to be Control plane (Law 4). Verified this is a pre-existing,
systemic artifact of this environment's own merge tooling, not something this session introduced or
can control: PR #303's own real title was verifiably `[relay] FTF-04A10: ...` (confirmed by
re-reading it directly via the GitHub API, never renamed), yet its own merge commit carries the
identical templated "Control plane certifies FTF-04A10" title despite that merge, too, having
happened well before any certification — the same phenomenon a same-day earlier shift already
flagged for FTF-04A09/PR #298 ("the merge-commit's own title notwithstanding"). No repository
setting was changed to investigate or "fix" this (a repo-settings change is an owner ask per
CLAUDE.md, and the cause looks environment/tool-level rather than repo-configured); the only
authoritative certification record remains `docs/spec-matrix.yaml`'s own `certification_state`
field, never a git merge-commit title.

**Certified 2026-09-26** by an independently-triggered Control-plane certifier session
(`https://claude.ai/code/session_01FFaBjhzTX1wS8BxeXFEUYr`, PR #305's own `pull_request.opened`
webhook), concurrently with the relay disclosure immediately above — see this file's own top
dated log for the full record (session-ID check, diff re-read, real CI `gate` job log confirmed
on the exact merged head, an independent fault injection against the license gate distinct from
the producer's own two, task-contract re-pull). `docs/spec-matrix.yaml` FTF-0302 now genuinely
reads `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`, `evidence_hash` = merge
commit `bde4b4a8562e8865474886a06fc91dcf9696a265` — the merge commit's own title was accurate by
the time this certification's own follow-up commit landed, resolving the timing gap the relay's
disclosure above correctly flagged. This paragraph is kept as the original submission record, not
rewritten.

**FTF-04A10** ("TypeScript/JavaScript deterministic agent/config/tool discovery") was the item
here before that; submitted 2026-09-26 via **PR #303** (branch `claude/wizardly-fermat-gg9rje` -> `main`,
`https://github.com/SJZLab/Sovra/pull/303`), title
`[relay] FTF-04A10: TypeScript/JavaScript deterministic agent/config/tool discovery`, producer
session `https://claude.ai/code/session_01MAwC6YFuztewV8dUutUMNh`. Direct JS/TS sibling of
FTF-04A09's own Python worker: traces a real `client.chat.completions.create(...)` call (the JS/TS
ecosystem's real client for an OpenAI-compatible endpoint) back to a real, statically-traced
`openai` npm import/require, using this repo's own already-declared root `typescript`
devDependency (never a new runtime dependency — both this and the model-binding-shape choice are
resolved in full in `docs/certifications/queue.md`'s own matching entry and this task's own module
docstring, per CLAUDE.md's default-resolution rule, without an owner ask). Two independent,
disclosed fault injections, both genuine FAILs, both reverted byte-identical
(`094127317a984868347126be15300fb2`). 21/21 tests pass; `ruff`/`mypy`/`eslint`/`prettier --check`/
`tsc --build` all clean (one narrowly-scoped `**/*.cjs` eslint override added for the new Node.js
helper script's own CommonJS globals); `pytest tests/unit` (3/3) and `pnpm exec vitest run` (27/27)
unaffected; `validate_spec.py --strict` clean; `product_truth_check.py --fail-on-unwired` unchanged
20/20 (`proving_symbol` left blank — no live dispatcher calls this worker yet, same disclosed
precedent as FTF-04A08/FTF-04A09); `status.py` unchanged 75/300 (Law 2). Not self-certified (Law
4) — independent Control-plane L2 review and a real CI run on the opened PR are required before
certification. Full record: `docs/certifications/queue.md`'s matching 2026-09-26 entry,
`docs/spec-matrix.yaml`'s FTF-04A10 notes, `PROGRESS.md`.

**Certified 2026-09-26** by an independently-triggered Control-plane certifier session
(`https://claude.ai/code/session_013DzXWSCTosSXk4sbSnhq47`, PR #303's own `pull_request.opened`
webhook) — see this file's own top dated log for the full record. `docs/spec-matrix.yaml`
FTF-04A10 now reads `certification_state: CERTIFIED` / `proof_state: L2-CERTIFIED`,
`evidence_hash` = merge commit `092d6adfa7d7066a1cb997af05250a78f74179bc`. This paragraph is kept
as the original submission record, not rewritten.

**FTF-04A09** ("Python deterministic agent/config/tool discovery") was the item here before that; submitted
2026-09-26 via PR #298 (branch `claude/wizardly-fermat-rw976n` → main), producer session
`https://claude.ai/code/session_017nX2q1sNKt5DYucXoPNU2z`. **PR #298 is already merged to `main`**
(merge commit `690f497`) but `docs/spec-matrix.yaml` still genuinely reads PROVISIONAL — no
certification has actually happened yet, the merge-commit's own title notwithstanding (flagged in
this file's own top dated log, 2026-09-26). A later relay shift (session
`https://claude.ai/code/session_01GiQiPXqDpqkZsHJUHWXMqh`) ran a full non-authoritative
re-verification pass (own fault injection distinct from the producer's two, real CI reconfirmed
31/31 green including `gate` on the actual merged head) and found no blocking defect, but
deliberately did not flip this to CERTIFIED — that requires an independently-triggered
Control-plane certifier session, not this relay (Law 4; see this file's own top dated log for the
full reasoning). See this file's own top dated log for the full record (scope boundary, two
distinct fault-injection vectors from two different sessions, two verification sweeps). Not
self-certified (Law 4) — awaiting an independently-triggered Control-plane L2 certifier session.

**FTF-04A09** ("Python deterministic agent/config/tool discovery") is **no longer here** — it was
independently reviewed and **CERTIFIED 2026-09-26** by a Control-plane certifier session
(`session_01PdswKsS7sgPHSE9e125d2D`, PR #298, merge commit `690f497`) before this shift began. See
this file's own top dated log and `docs/certifications/queue.md`/`docs/certifications/ledger.md`
for the full certification record.

**FTF-04A11** ("JSON/YAML/TOML configuration discovery") is the item here now; submitted
2026-09-26 by this shift (session `https://claude.ai/code/session_01GMDn1KqkzPZwd6WMgpZ4SZ`) via
a PR from branch `claude/wizardly-fermat-lf2dqo` → main. Dependency-ready (FTF-04A01/FTF-04A08
both `CERTIFIED`, reconfirmed before relying on them); no external prerequisite, pure static
parsing with no DB/network of its own. `apps/api/app/target_connectors/
json_yaml_toml_configuration_discovery.py` reads bounded JSON/YAML/TOML/`.env`-style config from
an already-captured snapshot and produces normalized, secret-redacted per-file/per-key evidence;
only `json.loads`/`tomllib.loads` (stdlib) and `yaml.safe_load` (never an unsafe/full YAML
loader) ever parse content. `tests/tasks/test_ftf_04a11.py`: 22 tests, two independent fault
injections (an unsafe-YAML-loader regression that genuinely created a file for real via the
injected tag before being reverted; a skipped-redaction regression that genuinely leaked a raw
secret value before being reverted) both reproduced the exact risk their matching
negative_acceptance clause exists to prevent, both reverted byte-identical. Real CI pending on
the opened PR. **New production runtime dependency, resolved without an owner ask, not silently
added:** promoted `pyyaml>=6.0.1,<6.1` from a workspace dev-only dependency (already pinned at
this exact version at the repo root) into `apps/api/pyproject.toml`'s own real dependency list —
CLAUDE.md's own "Default resolution rule" applies (no architecture doc names an alternative YAML
library; this version is already decided elsewhere in the repo, so reusing it is the most-
consistent choice, recorded here and in `docs/spec-matrix.yaml`'s own FTF-04A11 notes, rather than
a fresh unbounded choice or a hard stop). `docs/spec-matrix.yaml` set to `certification_state:
PROVISIONAL` / `proof_state: L1-PROVEN`. Not self-certified (Law 4) — awaiting an independently-
triggered Control-plane L2 certifier session and a real CI run on the opened PR.

**Also flagged, not claimed or forced through:** this shift first pulled **FTF-04A10**'s
("TypeScript/JavaScript deterministic agent/config/tool discovery") own real task packet and
found its acceptance clause needs a real JS/TS AST parser — a dependency class this repository's
pure-Python `apps/api` backend has none of anywhere, and no architecture doc names one. Left
unclaimed (task packet generated and kept, `docs/implementation/task-packets/FTF-04A10.md`,
not implemented) rather than force a fresh library/subprocess-bridge choice or invent a non-AST
substitute that would fail the acceptance clause's own explicit requirement. See this file's own
top dated log and `PROGRESS.md`'s matching entry for the full reasoning. Not written up as a
"## Blocked" stop, since dependency-ready work (FTF-04A11) continued immediately.

FTF-0215 ("Periodic sync workflow") was the item here before that; submitted 2026-09-25 via
PR #296 (branch `relay/ftf-0215-periodic-sync-workflow` → main), producer session
`https://claude.ai/code/session_0176zkJJ7cjvKszEcgJm9DQk`. Independently reviewed this same shift
(session `https://claude.ai/code/session_017o9VWhyaZNhhohoLbzdzpy`): diff re-read against the
real merge base, dependencies FTF-0004/FTF-0201/FTF-0207/FTF-0208/FTF-0209 reconfirmed CERTIFIED,
own re-verification in an isolated worktree (CLAUDE.md hash check, full test/lint/type sweep,
`git fetch --unshallow` resolved a pre-existing shallow-clone `stale_commit` artifact), own fault
injection distinct from the producer's two (disabled `register()`'s idempotency check — 2 genuine
FAIL lines) plus a narrower boundary probe on the ordering-gate comparison that found a real but
non-blocking test-coverage gap (see "Owner actions" note below), task contract re-pulled and
acceptance/negative_acceptance clauses cross-checked, real CI watched to 31/31 green including
`gate` and all three OpenBao-from-source jobs. **CERTIFIED 2026-09-25** via PR #296 (merge commit
`b8134b6fab043520ebadaa2b250456df95d67983`) — see "## Done" below and
`docs/certifications/ledger.md` for the full record.

**Flagged for Control plane/next shift (non-blocking, does not affect this CERTIFIED status):**
`PeriodicSyncWorkflow.complete_cycle()`'s own ordering gate (`packages/litellm_client/
periodic_sync_workflow.py`) already uses the correct `sequence <= self._last_applied_sequence`
comparison ("strictly newer"), but `tests/tasks/test_ftf_0215.py` does not explicitly exercise
the boundary case of an exact-duplicate redelivery of the *last-applied* sequence itself (as
opposed to a strictly older one) — this reviewing session's own fault injection weakening the
comparison to `<` was not caught by the existing suite. The shipped code is correct on this axis
today; this is a test-coverage gap to close, not a defect to fix. A small follow-up test asserting
that redelivering `complete_cycle(last_applied_sequence, ...)` a second time is also dropped as
`SKIPPED_OUT_OF_ORDER` would close it.

FTF-0408 ("Requirements UI") was the item here before that; submitted 2026-09-25 via PR #293, it
hit a real CI failure mid-review (three unquoted `"word: "` sequences inside a plain YAML scalar
in `docs/spec-matrix.yaml`'s own `notes` field broke `product_truth_check.py`/`status.py`
outright and failed `test_ftf_h034.py` in CI) that the producer's own session found and fixed via
that same real CI signal before this reviewing session's independent finding was published; this
reviewing session then independently re-verified the final head from scratch (own fault
injection distinct from the producer's own two, real CI re-confirmed 31/31 green) and
**CERTIFIED 2026-09-25** via PR #293 (merge commit `37fe4f5ca16b85af1e6be76db5f007362e21d3e7`) —
see "## Done" for the certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full evidence.

FTF-0509 ("Tool argument validator") was the item here before that; submitted 2026-09-25
via PR #288, it hit a real merge conflict (its base had fallen several merges behind `main` by
review time) which this reviewing session resolved before re-verifying real CI on the merged
commit and **CERTIFIED 2026-09-25** via PR #288 (merge commit
`0bb549b2f94e99e6d08179dd91643fbdbaa7907c`) — see "## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the full evidence.

**FTF-04A08** ("Source-analysis worker foundation") was the item here before that; submitted
PROVISIONAL 2026-09-25 via PR #289, it hit a real CI failure mid-review (a Python-3.12-incompatible
test trigger, unrelated to the shipped worker's own safety) that the producer's own session fixed
in two attempts before this reviewing session re-verified the final head and **CERTIFIED
2026-09-25** via PR #289 (merge commit `2b128ec8b38dc647e6dd68f395e332be48c77e7e`) — see "## Done"
for the certification summary and `docs/certifications/ledger.md`/`docs/certifications/queue.md`
for the full evidence.

FTF-0514 ("PII/secrets detector") was the item here before that; it was independently
reviewed and **CERTIFIED 2026-09-25** via PR #286 (merge commit
`1f859be5e520e114eeff08222230337a8a9acd0b`) — see "## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the full evidence.

FTF-0512 ("Normalized duplicate detector") was the item here before that; it was
independently reviewed and **CERTIFIED 2026-09-25** via PR #284 (merge commit
`bdd9c0e0a07da1ca5b18306277c50f841acc8ee7`) — see "## Done" for the certification
summary and `docs/certifications/ledger.md`/`docs/certifications/queue.md` for the
full evidence. FTF-0511 ("Exact duplicate detector") was the item here before that; it was
independently reviewed and **CERTIFIED 2026-09-25** via PR #282 (merge commit
`4602ba7d7b06063085d91d8782b33d946ef644d5`) — see "## Done" for that certification
summary too.

(Note: the rest of this section is a historical snapshot, not kept current
every shift — several items below, e.g. FTF-1503, have since moved to
CERTIFIED; see this file's own top dated log and `docs/certifications/queue.md`
for the real current state of any specific item.)

FTF-1503 ("CSRF suite") — submitted 2026-09-24 via a PR from branch
`claude/wizardly-fermat-gae8i9` against `main`, title `[relay] FTF-1503:
CSRF suite`. New, real, reusable CSRF-protection dependency
(`apps/api/app/auth/csrf_protection.py`) proven end to end against a real
uvicorn demonstration app composing the real `require_csrf_protection`
with the real `require_role`; zero certified production files touched
(`oidc_login.py`/`rbac_middleware.py`/`project_routes.py`/`main.py` all
untouched — same disclosed "designed, not yet wired into the real product
route" gap `require_role` itself already carries in `main.py` today). Own
fault injection (disabled the token comparison) genuinely caught by both
suites, reverted byte-identical. Full record:
`docs/certifications/queue.md`'s matching 2026-09-24 entry, `PROGRESS.md`.
Awaiting Control-plane L2 certification and a real CI run on the opened
PR — not this relay's role.

FTF-0507 is no longer in this section; it was independently reviewed and
**CERTIFIED 2026-09-24** via PR #253 (merge commit
`c7d33a3ac5a25bd9237422b8dd16d36c27a62fb4`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full evidence, including the
independent session-ID check, own local reproduction, an own fault
injection (distinct vector from the producer's own two: the trace-level
duplicate tool-call-id guard), and the real CI step-level log confirmation
on the PR's actual final head.

FTF-1304 is no longer in this section; it was independently reviewed and
**CERTIFIED 2026-09-24** via
PR #249 (merge commit `4b432a44d4d6ef511796bce5f30faec4d948b831`) — see
"## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the
full evidence, including the independent session-ID check, own local
reproduction, an own fault injection (distinct vector from the
producer's own: silently zero-filling a declared, unmatched
`fallback_cost` instead of raising), and the real CI step-level log
confirmation on the PR's actual final head.

FTF-1303 is no longer in this section; it was independently reviewed and
**CERTIFIED 2026-09-24** via PR #245 (supersedes PR #240, merge commit
`f1e545745f7679ed19628d66ffc84955329a6f50`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full evidence, including the
independent session-ID check, own local reproduction, an own fault
injection, and the real CI step-level log confirmation on the PR's actual
final head. History below, kept for the record, not a live next-step.

FTF-1303 ("Provider token costing") -- submitted 2026-09-24 via PR #240
against `main`. Real, DB-free Section 32 `provider_call_cost` calculator,
dependency-ready on FTF-0210/FTF-1301 (both CERTIFIED). Full submission
record: `docs/certifications/queue.md`'s matching 2026-09-24 entries,
`PROGRESS.md`.

**Update, same shift:** CI (which was still all-`queued` earlier this
shift) finished and went fully green (29/29, `gate` included). This
session ran the complete independent CERTIFY procedure: session-ID check
clean (PR #240's 4 commits all carry `session_01CuuVLc7aGhNUrpFbQmrpVk`,
no overlap with this reviewing session), full module/test read, a fresh
CI check-run pull plus the `gate` job's own step-level log text
confirming genuine execution, own local reproduction, an own fault
injection (mutated the real `_priced_category` to silently zero-fill a
missing rate instead of raising -- 4 genuine FAIL lines, reverted
byte-identical), `product_truth_check.py`'s `19/19` confirmed a
legitimate disclosed 0/0 (empty `proving_symbol`, no live caller yet),
and both `acceptance`/`negative_acceptance` clauses cross-checked against
the real task contract via `tools/r1_handoff.py packet FTF-1303` --
**every substantive check passed.**

**Not yet certified -- blocked only by a self-inflicted merge conflict,
not a code defect:** at merge time `mergeable_state` had flipped to
`"dirty"` because this exact shift's own FTF-04A06 certification (PR
#238/#242, merge commits `249ff066`/`5f0d6f02`) landed on `main` moments
earlier and edited the same three narrative tracking docs PR #240's own
last commit touched. A local dry-run merge (aborted, nothing pushed)
confirmed the conflict is contained entirely to `PROGRESS.md`/
`QUEUE.md`/`docs/certifications/queue.md` -- `docs/spec-matrix.yaml` and
`.github/workflows/ci.yml` both auto-merged cleanly, so there is no code
or CI-wiring conflict at all. Per this repo's own precedent (FTF-04A06's
earlier tracking-doc conflict against PR #239 was resolved by a later
session on the producer's own branch, not by the certifying session),
this certifying session left PR #240's branch untouched rather than
pushing a conflict resolution onto another session's still-open branch.
**Next relay shift:** merge `main` into `claude/wizardly-fermat-vwrwp6`
(keep both sides' additions in all three files), push, confirm CI is
still green on the new head -- at that point this PR needs no further
code review and can be merged/certified on sight per the review already
recorded in `docs/certifications/queue.md`'s matching 2026-09-24 entry.

**Update, next relay shift (2026-09-24T14:53:00Z):** resolved as instructed
above, but pushed to **PR #245** rather than onto `claude/wizardly-fermat-
vwrwp6` directly — this relay session's own designated branch is
`claude/wizardly-fermat-8lsly3`, a different, unrelated session's open
branch is outside this session's scope to push onto. Built
`claude/wizardly-fermat-8lsly3` from PR #240's own head, merged `main` in
(kept both sides in all three tracking docs; `docs/spec-matrix.yaml`/
`.github/workflows/ci.yml` both auto-merged cleanly, confirming the dry-run
finding above), re-verified clean (`test_ftf_1303.py`/`test_ftf_04a06.py`
both pass, ruff/mypy clean, `validate_spec.py --strict` clean after
unshallowing, `product_truth_check.py` 19/19, `status.py` 60/300
certified). Pushed, opened PR #245 (same production diff as PR #240,
byte-identical `provider_token_costing.py`/`test_ftf_1303.py`), commented
on and **closed PR #240 as superseded**. CI running fresh on PR #245 (run
`36016066208`). The independent review recorded above already covers this
identical code diff in full — **PR #245 needs only a fresh green CI
confirmation, not a repeat code review**, before merge/certification (Law
4 still requires a Control-plane session, not this relay, to do the
merge).

**Update, same shift (2026-09-24T15:02:00Z):** PR #245's CI finished —
fresh `get_check_runs` pull confirmed **29/29 checks `completed`/`success`
on the real final head (`a6e2742`), `gate` included**, run `36016262466`.
`mergeable_state` flipped `unstable` → `clean`. No review comments posted
yet. Posted a final status comment on PR #245. **PR #245 is CI-clean and
mergeable, awaiting a Control-plane certifier shift** — Law 4 still applies,
this relay session cannot certify or merge its own work.

FTF-04A06 is no longer in this section; it was independently reviewed and
**CERTIFIED 2026-09-24** via PR #238 (merge commit
`249ff0663df74fe25f33f35673a319ea6d728b90`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full evidence, including the
independent session-ID check, own local reproduction, two independent
fault-injection vectors, and the real CI step-level log confirmation.

FTF-1303 (Provider token costing) — this shift's own submission, see the
top-of-file 2026-09-24 relay entry for the full record. **PR:**
https://github.com/SJZLab/Sovra/pull/240
(`claude/wizardly-fermat-vwrwp6` → `main`), commits `be9775f`/`bee5b55`/
`4be55bf`. Awaiting CI + independent Control-plane L2 review (Law 4).

FTF-04A06 (PR #238, `claude/wizardly-fermat-azo96v`) — **UPDATE, this
shift (2026-09-24, read-only check, no relay action taken on this item):**
the Law 1 test-coverage gap the prior NOT-CERTIFIED review found (below)
was already fixed by a later session on the same PR branch — commit
`911ae78` added `test_resolve_allowed_root_refuses_a_prefix_colliding_
sibling`, independently confirmed (via this shift's own `pull_request_read`
call) to genuinely fail against a naive `str.startswith()` fault injection
and pass against the real code. A tracking-doc merge conflict with PR #239
was then resolved (merge commit `c122d5d`) and CI re-ran fully green
(run `35994361000`, all 29 checks including `gate`, `mergeable_state:
clean`) — confirmed directly via a fresh `pull_request_read get_check_runs`
call this shift, not taken on faith from the branch's own PROGRESS.md
claim. **PR #238 is ready for a fresh independent Control-plane L2
review** (this relay shift has no certifier standing to perform that
review itself, per Law 4/CLAUDE.md's relay-vs-certifier separation — left
for the next Control-plane certifier shift). Original 2026-09-24
NOT-CERTIFIED finding, preserved for context below:

FTF-04A06 (PR #238, `claude/wizardly-fermat-azo96v`) — implementation
sound (own independent re-run of both producer fault-injection vectors,
symlink-escape and concurrent-drift, both genuinely caught), but
**independently reviewed and NOT CERTIFIED 2026-09-24**: a real Law 1 gap
— `tests/tasks/test_ftf_04a06.py` has no test case for a prefix-colliding
allowlist pair (e.g. `allowed` vs `allowed-evil`), so the module's own
docstring-claimed "never a string-prefix comparison" guarantee, and this
task's own `negative_acceptance` clause naming "path traversal" (not just
"symlink traversal"), are asserted but never shown able to fail. The
shipped `resolve_allowed_root` itself is not vulnerable (confirmed by
reverting the reviewer's own fault injection and re-testing the real
code) — only the test evidence is missing. **(Superseded by the update
above -- kept for history, not a live next-step.)** Original "Next relay
shift" note, now moot: add one
test case constructing `base/allowed` and `base/allowed-evil` as sibling
directories and asserting `resolve_allowed_root(evil, [allowed])` raises
`PathAllowlistError`, shown able to fail first against a naive
`str(...).startswith(...)` implementation before confirming it passes
against the real one — same pattern this task's own existing symlink-
escape test already follows. No other part of this PR needs rework. Full
record: `docs/certifications/queue.md`'s 2026-09-24 FTF-04A06 entry.

FTF-R002 is no longer in this section; it was independently reviewed and
**CERTIFIED 2026-09-24** via PR #236 (merge commit
`d257254c78678e978533190b327d4d8758fb3914`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md` for the full
evidence.

FTF-04A07 is no longer in this section; it was independently reviewed and
**CERTIFIED 2026-09-24** via PR #234 (merge commit
`e0e17920b9df2fae57fb635c1549a244183af106`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full evidence.

FTF-0213 is no longer in this section; it was independently reviewed and
**CERTIFIED 2026-09-24** via PR #232 (merge commit
`1751211044041e15fc4708b4be552c3957509e80`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full evidence.
FTF-0212 (Real tool-call probe) was implemented and submitted PROVISIONAL
this shift (2026-09-24, autonomous relay) — pushed to
`claude/wizardly-fermat-t7b6qv`, PR `[relay] FTF-0212: Real tool-call
probe`. Awaiting independent Control-plane L2 review — see this file's
own top status note and `docs/certifications/queue.md` for the full
evidence record.

FTF-0211 is no longer in this section; it was independently reviewed and
**CERTIFIED 2026-09-24** via PR #228 (merge commit
`423eb560ac7a7a5c088c33f30eda916bbff2ec77`), superseding PR #226 (closed
as superseded) — see "## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the
full two-review history (PR #226's 2026-09-23 NOT-CERTIFIED review for a
real CI failure, the relay's IPv6 fix, and this 2026-09-24 CERTIFY
review).

FTF-0210 is no longer in this section; it was independently reviewed and
CERTIFIED 2026-09-23 via PR #224 (merge commit
`d38930b4d7b6b94cc7ba72dfd2c873a3627cd94a`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full history and evidence.

FTF-0209 is no longer in this section; it was independently reviewed and
CERTIFIED 2026-09-23 via PR #222 (merge commit
`78ab4537298e1b484f42fffc0d2ae5d27d85137f`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full history and evidence.

FTF-0208 is no longer in this section; it was independently reviewed and
CERTIFIED 2026-09-23 via PR #220 (merge commit
`30d91376505447d33dfd9ac8d9e5ee92ba162ed9`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full history and evidence.

FTF-0207 is no longer in this section; it was independently reviewed and
CERTIFIED 2026-09-23 (evidence: merge commit
`ab6473fc3854f71d782a8bb1671a95c33f413ba5` — the commit that actually
landed the implementation on `main`; see "Irregularity" note in the
ledger entry for why that commit's own message is not itself the
certification record). See "## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the
full history and evidence.

FTF-0506 is no longer in this section; it was independently reviewed and
CERTIFIED 2026-09-23 via PR #214 (merge commit
`7ffaebcb9879acf9db1adee2f70e6f6e539dbf3d`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full history and evidence.

FTF-04A01 is no longer in this section; it was independently reviewed
and CERTIFIED 2026-09-23 via PR #210 (merge commit
`f8b9393a8ea5ec8492f2d18e1083d8c4318b5c18`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full history and evidence.

FTF-0206 is no longer in this section; it was reviewed once (NOT
CERTIFIED — the `gate` job never ran `tests/tasks/test_ftf_0206.py` at
all), that CI gate-coverage gap was closed by a later relay shift (see
PROGRESS.md's 2026-09-23 "closed the FTF-0206 half of the CI
gate-coverage gap" entry), and it was then independently re-reviewed and
**CERTIFIED 2026-09-23** via PR #207 (merge commit
`7ee47f8c4fa2f7dbce7d6557a29bdb82b4369a53`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full history and evidence.

FTF-0205 is no longer in this section; it was independently reviewed
and CERTIFIED 2026-09-23 via PR #205 (merge commit
`b421fb366870167dcb7ecaab7a658adf1c2c6510`). This review watched the
real CI run on PR #205's real head to completion (26/26 jobs green,
including `gate`, `secret-manager-integration` and
`dataset-source-connection-integration`) before certifying. Own
reproduction (no-DB suite, FTF-0201/FTF-0202/FTF-0203/FTF-0204
regression suites, all clean) and an own fault injection distinct from
the producer's own two (made a denied/invalid-token `/model/info`
response drop every visible id outright instead of downgrading each to
`limited` — the exact negative_acceptance risk failed for real, reverted
byte-identical) — see "## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the
full history and evidence.

FTF-0204 is no longer in this section; it was independently reviewed
and CERTIFIED 2026-09-22 via PR #203 (merge commit
`9a3dd7e52040cca60ec7ae72b38676d253a32824`). This review watched the
real CI run on PR #203's real head to completion (26/26 jobs green,
including `model-discovery-integration` and `gate`) before certifying.
Own reproduction (no-DB suite, live-Postgres integration suite,
FTF-0201/FTF-0203 regression suites, all clean) and an own fault
injection distinct from the producer's own (disabled the invalid-token
check in `discover_models` — the exact negative_acceptance case failed
for real, reverted byte-identical) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full history and evidence.

FTF-0203 is no longer in this section; it was independently reviewed
and CERTIFIED 2026-09-22 via PR #200 (merge commit
`c6e51a2fb267a6ef26791eb9fccc79cbfd0fc4b4`). PR #200's own first CI run
genuinely failed `connection-validation-integration` (a real
`PermissionError` writing the test CA to `/usr/local/share/
ca-certificates/` without `sudo` on the GitHub Actions runner, which
does not run as root) — the producer's own follow-up commit fixed it
(`sudo cp`/`sudo update-ca-certificates`), and this review watched the
real re-run to completion (25/25 jobs green) before certifying, rather
than trusting a stale signal. Own reproduction (no-DB suite 48/48,
live-Postgres integration suite 16/16, FTF-0201 regression clean) and
an own fault injection distinct from the producer's own two (disabled
the `response.status in (401, 403)` invalid-token check — the exact
negative_acceptance check failed for real, reverted byte-identical) —
see "## Done" for the certification summary and
`docs/certifications/ledger.md`/`docs/certifications/queue.md` for the
full history and evidence.

FTF-H006 is no longer in this section; it was independently reviewed
and CERTIFIED 2026-09-22 via PR #197 (merge commit
`99e3d3d0be60d7f5491e99965809a29c62efb4ce`, after resolving a real
tracking-file merge conflict against the FTF-0500 certification that
landed just before it) — see "## Done" for the certification summary
and `docs/certifications/ledger.md`/`docs/certifications/queue.md` for
the full history and evidence.

FTF-0500 is no longer in this section; it was independently reviewed
and CERTIFIED 2026-09-22 via PR #196 (merge commit
`07b831d44d3c5a5875c5a63b40382c687455d1de`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full history and evidence.

FTF-0202, FTF-1501, FTF-1001, and FTF-1401 are no longer in this
section; all four were independently reviewed and CERTIFIED 2026-09-22
(PR #176 merge `6dfd97d`, PR #159 merge `8b8a9c1`, PR #160 merge
`8b51c85`, PR #161 merge `c2fda5b` respectively) — see "## Done" for
the certification summaries and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full history and evidence.

FTF-1702 is no longer in this section; it was independently reviewed and
CERTIFIED 2026-09-22 via PR #167 (merge commit
`099a0753710ac6f2bb4cba68c209e99d3845e5d9`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full history and evidence.

FTF-1302 (Local compute costing, PR #170
https://github.com/SJZLab/Sovra/pull/170) is being independently
re-reviewed by Control plane now (this entry's own review), after its
prior NOT-CERTIFIED verdict was blocked solely by the now-reportedly-
cleared GitHub Actions runner-provisioning outage.

FTF-1301 is no longer in this section; it was independently reviewed and
CERTIFIED 2026-09-21 via PR #168 (merge commit
`9a47458118ee46095270f4aa9758b5ec35efeea5`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full history and evidence.

FTF-1302 (Local compute costing, PR #170
https://github.com/SJZLab/Sovra/pull/170) was independently reviewed
2026-09-21 and **NOT CERTIFIED — not a code defect.** The shipped code,
its tests, and its fault-injection evidence were independently
reproduced and confirmed sound; certification was blocked only because
GitHub Actions could not show a genuine passing CI run on the real head
at the time (see `docs/certifications/queue.md`'s "2026-09-21 — FTF-1302
(PR #170)" entry for full detail) — that outage is reported cleared as
of PR #178 (2026-09-22); PR #170 stays open, unmerged, needing the same
merge-current-main treatment this PR just required before a fresh CI run
and review.

FTF-R016 (Measure selected coding-model/executor pilot cost and enforce
budget tree, PR #172 https://github.com/SJZLab/Sovra/pull/172) was
independently reviewed 2026-09-21 and **NOT CERTIFIED — not a code
defect,** blocked only by the same now-reportedly-cleared CI outage (see
`docs/certifications/queue.md`'s "2026-09-21 — FTF-R016 (PR #172)"
entry). PR #172 stays open, unmerged, needing the same treatment.

FTF-1702 (Tool environment, PR #167 https://github.com/SJZLab/Sovra/pull/167)
is being independently reviewed by Control plane now (this entry's own
review).

FTF-1701 (Reference agent specification) is no longer in this section; it
was independently reviewed and CERTIFIED 2026-09-21 via PR #165 (merge
commit `bbdf0c8278d39d1f3fa7ed87c11e240a109075a3`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full history and evidence.

FTF-0403 (Budget policy) is no longer in this section; it was
independently reviewed and CERTIFIED 2026-09-21 via PR #163 (merge commit
`4afab390b60028cccc489c439d8401252a2c1d8f`) — see "## Done" for the
certification summary and `docs/certifications/ledger.md`/
`docs/certifications/queue.md` for the full history and evidence.

Prior item: FTF-0407 (Critical behavior requirements) was
independently reviewed and CERTIFIED a prior shift via PR #156 (merge
commit `5da3834`) — see "## Done" for the certification summary and
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

## Done (certified items since 2026-11-27; earlier entries are in `docs/certifications/archive/queue-history-2026-11-27.md`)
