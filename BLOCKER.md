# BLOCKER.md

## OPEN 2026-09-25 — FTF-0214 (Model catalog UI): stated acceptance has no live backend surface to build against

Frontier check this shift (`docs/spec-matrix.yaml` `certification_state` joined against
`docs/TASK_INDEX.md` dependencies): FTF-0214's five listed dependencies (FTF-0201/0207/0208/
0209/0210) are all genuinely `CERTIFIED`. No open PR already claims it (open PRs are #290
doc-only, #231 FTF-0212, #194/#195 duplicate FTF-0304 — none overlap). Lowest-ID unclaimed,
dependency-ready item; picked per this repo's own established precedent (see PR #194's body).
Stopped in the **P** (read/plan) phase of PEV before any production-code change — no task marked
`IN_PROGRESS`, no file written under this task's own `files` write set.

**What was tried:** pulled the real DoD (`tools/r1_handoff.py packet FTF-0214`), then read the
actual current backend, not just the packet's claims — `apps/api/app/litellm/connection_routes.py`
in full, `packages/litellm_client/{alias_reconciliation,last_known_good_catalog,
catalog_anomaly_detector,real_chat_probe}.py`, `apps/api/app/main.py`'s router wiring, and
`packages/contracts/openapi/02-interface.yaml`.

**What failed — two independent gaps, both against this task's own stated acceptance
(`"Catalog shows actual access freshness/capability/probe evidence, safe filters and explicit
project selection"`), not a stylistic nice-to-have clause:**

1. **No composed catalog/probe backend surface exists, anywhere, and the frozen shared contract
   deliberately excludes it.** `connection_routes.py`'s own module docstring states outright: it
   "deliberately does not declare `sync-models`/`syncs`/`/models`/`/models/{id}/probe` routes,
   even as a stub or a `501`" — declaring that surface without a real backing call "would be
   exactly the placeholder endpoint Law 11... forbid[s]." `packages/contracts/openapi/
   02-interface.yaml` repeats the identical exclusion verbatim. FTF-0207 (alias reconciliation),
   FTF-0208 (last-known-good catalog), FTF-0209 (anomaly detector) and FTF-0210 (real chat probe)
   are all genuinely `CERTIFIED`, but strictly as pure, no-I/O logic modules with zero HTTP
   surface and zero persisted state — confirmed by reading all four files directly, not trusting
   their own docstrings alone. The only live LiteLLM route beyond CRUD is FTF-0204's
   `GET .../connections/litellm/{connection_id}/models` (bare model IDs + `metadata_status`,
   nothing else) — and FTF-0204 is not even one of FTF-0214's five listed dependencies, which is
   itself a signal the spec author expected the catalog to be built from FTF-0207/0208/0209/0210,
   not from FTF-0204. There is no live data source anywhere in this repository for "capability"
   or "probe evidence," and no route a UI could call to display either for real. FTF-0214's own
   `files` list (`apps/web/components/litellm-gateway/model_catalog_ui.tsx` +
   `apps/web/tests/ftf_0214.spec.ts`) claims no backend write set — so building the missing
   composition route is not in this task's own declared scope to begin with.
2. **"Explicit project selection" has no backing data relationship.** Every certified task in
   this capability area (CAP-02, FTF-0201 through FTF-0215) scopes a LiteLLM connection to an
   *organization* only — `LiteLLMConnection` carries `organization_id`, never a project reference,
   and no route anywhere binds a `Project` (FTF-0401) to a `LiteLLMConnection`. Grepped
   `apps/api/app/projects/` directly: no `litellm`/`connection_id` reference exists beyond a
   docstring mention. The phrase is not spec boilerplate reused elsewhere (checked: it appears
   exactly once in `FINE_TUNING_FACTORY_RELEASE_1.md`, only in FTF-0214's own acceptance entry) —
   it is a specific, intentional requirement with no schema to satisfy it against.

Building the UI against only what is genuinely live today (FTF-0204's bare model-ID list, no
capability/probe data, connection-selection instead of project-selection) would not meet this
task's own stated acceptance in any but the most nominal sense — this is not the repo's usual
"no live caller yet, disclosed" reduction (where the built artifact is complete on its own terms
and simply unconsumed); here the *consumer* (this UI) has nothing real to consume for the
criterion's own central substance. Forcing a UI through anyway risks either an honest
near-total-non-acceptance submission or, worse, inventing display data not backed by a real
call — exactly Law 5/Law 11's target. Per CLAUDE.md "Relay is execution-only... write BLOCKER.md
and stop. Do not improvise the DoD," this is being escalated rather than improvised.

**Three route options for Control plane / owner:**

1. Slice a new, explicitly-scoped prerequisite task (e.g. `FTF-0214-pre`) that composes
   FTF-0207+FTF-0208+FTF-0209 into one authorized, tenant-scoped `GET` catalog route and adds a
   real, admin-gated probe-trigger route wrapping FTF-0210 (satisfying this task's own
   negative_acceptance, "viewer cannot run paid probe," by construction via role gating) — freezing
   both shapes in `packages/contracts/openapi/02-interface.yaml` and
   `packages/contracts/jsonschema/02-domain.schema.json` first. FTF-0214 becomes buildable, for
   real, once that lands and is independently certified.
2. Owner/Control plane formally narrows FTF-0214's acceptance in `docs/TASK_INDEX.md`/the master
   plan to match only what is genuinely live (bare model list + `metadata_status` +
   `checked_at`, connection selection, no probe control) — a recorded scope decision, not one this
   relay session makes unilaterally by quietly dropping the criterion's own central clause.
3. Confirm whether "project selection" is a spec-authoring artifact (no project ever binds to a
   LiteLLM connection anywhere in this repo's CAP-02 area) or reflects an intended, not-yet-built
   `Project`↔`LiteLLMConnection` binding — if the latter, that binding is its own prerequisite
   schema/migration task, not something to invent inline inside a UI-only task's declared write
   set.

No code, spec-matrix, or tracking-doc changes made for FTF-0214 this shift beyond this entry and
the matching `QUEUE.md`/`PROGRESS.md` notes. No other item claimed this shift, per this step's own
stop condition.

---

_The two entries below are resolved history, kept for the record per this repo's "reported, not
silently worked around" norm — not live issues._

## RESOLVED 2026-09-16 — Relay fully blocked past FTF-0001 (Section 85 gap)

Original entry: no queued item past FTF-0001 had a real acceptance/negative-acceptance/
verification contract; `docs/architecture/CRITERION_MAPPING.md` "Known gap" and
`docs/TASK_INDEX.md` had been unchanged since the initial scaffold commit (`f9c2ccd`) with no
Section 85 content ever supplied. Wrote this file and a QUEUE.md owner ask rather than improvise
a DoD, per CLAUDE.md "Relay is execution-only."

**Resolution:** the owner supplied `FINE_TUNING_FACTORY_RELEASE_1.md` (the single-file Release 1
master specification — root of the repo) and `tools/r1_handoff.py` (extracted from the master
doc's own Section 86). Verified before trusting either:
- Read `r1_handoff.py` in full: stdlib-only, no network/subprocess/eval/exec, path-traversal-
  and symlink-safe writes, refuses to overwrite existing content with different bytes.
- Copied the uploaded spec file byte-for-byte (`cp`, not regenerated) and confirmed
  `sha256sum` on disk matches the tool's own reported `source_sha256`
  (`5d3c183ff11b85718d5a3bbb08188868c455464202bb4898f6ddde66013eb664`) — no transfer corruption.
- `validate --self-test`: `status: PASS`, 300/300 tasks, 43 scenarios, 21 capabilities,
  dependency graph acyclic, 16/16 deliberately-broken structural fixtures correctly rejected.
- `bootstrap`: inspected every file it wrote (`spec/RELEASE_1.md`, `spec/tasks.json`,
  `spec/runtime-architecture.json`, `spec/qualification-inventory.json`,
  `spec/release-1-handoff.json`, `docs/implementation/TRACKER.json`) — all plain JSON/Markdown,
  every task `NOT_STARTED`/`ready=false`/no evidence, nothing executable, no claim of completion.
- `packet FTF-0002`: produced a genuine per-task DoD (real acceptance, negative_acceptance,
  verification_commands, files, dependencies, plus binding section text) — not a placeholder.

Updated `docs/architecture/CRITERION_MAPPING.md` ("Known gap — RESOLVED"), `CLAUDE.md` (Read-
first line now points to the master spec + tool ahead of the four excerpt docs it supersedes;
`CLAUDE.md.sha256` re-pinned via `sha256sum`, verified with `sha256sum -c`), and `QUEUE.md`
(relay now runs `tools/r1_handoff.py packet <task-id>` for a real DoD before claiming a task;
BLOCKER.md only if that command itself fails).

Per the sharing session's explicit instruction: this shift does not claim or work FTF-0002 or
any other task — this is a doc/tooling PR only. The relay picks up the now-unblocked queue on
its normal next fire.

## RESOLVED 2026-09-15 — CLAUDE.md hash mismatch

Original entry: a scheduled-relay prompt baked in a pinned CLAUDE.md hash that went stale after
an authorized edit re-pinned the file's own internal self-reference. Root-caused as expected
fallout from a legitimate change, not tampering; fixed at the source in commit `a010433` by
replacing the pinned-hash convention with a self-verifying one (compare `sha256sum CLAUDE.md`
against the committed `CLAUDE.md.sha256` directly, which can't go stale). Re-verified clean on
the next two shifts since (2026-09-15 and 2026-09-16, the latter after CLAUDE.md's own Read-first
edit above and hash re-pin).
