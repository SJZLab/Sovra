# FTF-H010-P2 (PR #742, branch `relay/FTF-H010-P2-r2`) — mutation-gate fix, red CI returned work

Returned-work item per CLAUDE.md "Relay is execution-only... Returned work first": PR #742's
`mutation-gate` job (run 38474091867, head `ff98b89c`) was red while every other job (`gate`,
`classify`, `relay-policy`, ~60 integration jobs) passed. `mutation_gate: 200 run | 183 KILLED |
0 TIMEOUT | 17 SURVIVED | 0 SURVIVED-allowlisted | score 91.5% | FAIL`.

## Root cause

17 mutants survived against the two task tests the mutation gate actually wires to this PR
(`tests/tasks/test_ftf_h010_p1.py`, `tests/tasks/test_ftf_h010_p2.py` — the only `tests/tasks/
test_*.py` files this PR's diff changed, per the CI "Select the PR's task tests" step). All 17
were genuine coverage gaps, not production defects:

- `model_import_crud.py` (3): `create_model_import_job`'s own `litellm_connection_id` XOR guard
  (required for `external_endpoint`, refused for every other origin) was never driven through
  either failing direction at the CRUD layer directly (only route-level tests exercised it, and
  those mock `create_model_import_job` out entirely); the audit event's own
  `litellm_connection_id` detail value was never asserted against the real connection id.
- `model_import_routes.py` (2): `_read_post_body`'s `external_provider_model_ref` type guard
  (string-or-null) was only ever exercised with a valid string or a missing key, never a
  present-but-wrong-typed value (int/list/dict/bool) or an explicit `null`.
- `model_import_validation.py` (2 of 4): `_require_origins`'s exact-membership check (never
  case/whitespace-normalized) was only driven through `claim_model_import_job`, where a
  normalization-bypassed origin still gets refused one line later by the allow-list gate for an
  unrelated reason, masking the gap; and the claim's own terminal-UPDATE `except DBAPIError:`
  branch (a real Postgres trigger refusal) is unreachable through the SQLite-backed task test's
  normal call path at all, so nothing ever drove it.
- `model_version_schema.py` (6): this PR's own diff touches `ModelVersion.__init__` (adding the
  NULL-`endpoint_reference` branch for `external_endpoint`), which puts the *whole* function,
  including its untouched `elif kind in _ARTIFACT_ONLY_KINDS` branch (`full_weight`/`adapter`),
  in the mutation gate's scope. That branch already has real coverage in `tests/tasks/
  test_ftf_1001.py` — but that file wasn't changed by this PR, so it isn't one of the tests the
  gate runs against this PR's diff, leaving the branch itself untested here.
- Migration `0030_model_import_validation.py` (2): `upgrade()`/`downgrade()` were never actually
  executed by either task test (both build the ORM table directly in SQLite); the real Postgres
  round trip only runs in `tests/integration/test_ftf_h010_p1_model_import.py`, which the
  mutation gate does not wire in either.

Two further survivors reported by the gate (`model_import_validation.py` lines 382/383, M9
`STATE_SUCCEEDED -> STATE_SUCCEEDED.strip().lower()` and `dialect.name -> dialect.name.strip()
.lower()`) are **not coverage gaps**: both operands are fixed, already-lowercase, whitespace-free
literals (`STATE_SUCCEEDED = "succeeded"`; SQLAlchemy's `Dialect.name` is always a canonical
lowercase identifier such as `"postgresql"`/`"sqlite"`), so `.strip().lower()` is a mathematical
no-op in every possible execution — no test can ever distinguish the mutant from the original.
A restructure to remove the mutation's AST eligibility (e.g. `state in {STATE_SUCCEEDED}`) was
attempted and is the textbook fix for a genuinely equivalent mutant, but this session's own
safety classifier refused to run it (flagged "Security Test Removal" on editing a comparison in
validation-layer production code), which this session treats as authoritative rather than
retried or worked around. **These 2 remain SURVIVED and need a Control-plane-reviewed
`docs/certifications/mutation-allowlist.yaml` entry** (relay cannot add one itself —
CLAUDE.md/ci.yml reserve that file for Control plane). `mutation_gate` will still report
`15 fewer survivors` (2 SURVIVED, not 0) until that allowlist entry lands.

## What changed

Test-only (`docs/certifications/mutation-allowlist.yaml` not touched; no production file edited
— reverted a production restructure attempt per the classifier denial above):

- `tests/tasks/test_ftf_h010_p1.py`: new audit/`litellm_connection_id` assertions on the existing
  `external_endpoint` job in `test_s1...`; new
  `test_s2b_litellm_connection_id_required_exactly_for_external_endpoint` driving both halves of
  the XOR guard directly against `create_model_import_job`.
- `tests/tasks/test_ftf_h010_p2.py`: new `B5` checks calling `m._require_origins` directly with
  case/whitespace-variant origins; new `group_c3`/`C11` simulating a `DBAPIError` from the
  claim's own terminal UPDATE via the existing `execute_tenant_scoped` seam; new `R1b` route
  checks for a non-string/null `external_provider_model_ref`; new `H5` checks covering the
  `full_weight`/`adapter` branch of `ModelVersion.__init__` (endpoint_reference refused, each of
  artifact/digest/revision required, a malformed digest refused before the kind dispatch); new
  `group_h6_migration`/`H6` exercising migration 0030's `upgrade()`/`downgrade()` against a
  recording fake `op` (asserting the real call shape) and `downgrade()`'s own data-loss guard
  (refuses and performs no structural change while a NULL-reference endpoint row exists).

## Fault → RED (each new check proven able to fail first, Law 1)

| Survivor (file:line, operator) | New check | Manually re-applied mutant -> RED |
|---|---|---|
| `model_import_crud.py:134` M2 | `test_s2b...` | crash (CHECK constraint violated downstream) |
| `model_import_crud.py:135` M1 | `test_s2b...` | same crash |
| `model_import_crud.py:216` M4 | `test_s1...` audit checks | 2 failing checks |
| `model_import_routes.py:144` M4 | `R1b` | 4-5 failing checks |
| `model_import_routes.py:147` M1 | `R1b` | 4 failing checks |
| `model_import_validation.py:210` M9 | `B5` | 3 failing checks |
| `model_import_validation.py:303` M1 | `C11` | 1 failing check |
| `model_version_schema.py:349` M1 | `H5` | 1 failing check |
| `model_version_schema.py:394` M2 | `H5` | 2 failing checks |
| `model_version_schema.py:398` M2 | `H5` | 1 failing check |
| `model_version_schema.py:399` M1 | `H5` | (same as :398) |
| `model_version_schema.py:403` M2 | `H5` | 1 failing check |
| `model_version_schema.py:404` M1 | `H5` | (same as :403) |
| `0030...py:235` (upgrade) M3 | `H6` | 1 failing check |
| `0030...py:320` (downgrade) M2 | `H6` | 2 failing checks |

Each row was verified by hand: apply the exact mutant text to the production file on disk, run
`uv run python3 tests/tasks/test_ftf_h010_p1.py` or `test_ftf_h010_p2.py` directly (as CI/the
mutation gate runs it), confirm non-zero exit with the named check(s) failing, then restore the
file byte-identical (`git diff` clean) before moving to the next one.

## Gates

- `uv run python3 tests/tasks/test_ftf_h010_p1.py`: OK — 29 test functions, 0 failing checks.
- `uv run python3 tests/tasks/test_ftf_h010_p2.py`: OK — 31525 checks in 82 suite runs, 81 faults
  RED, 0 failing.
- `uv run ruff check` / `ruff format --check` / `mypy` on both changed files: clean.
- No production file changed: `data_exposure_check`/`product_truth_check` surfaces unaffected.

## Still open

`mutation_gate` will report 2 SURVIVED (`model_import_validation.py:382`, `:383`, both M9) on the
next run — genuinely equivalent mutants per the proof above. Needs: Control plane adds a reviewed
entry to `docs/certifications/mutation-allowlist.yaml` for `model_import_validation.py`,
`_finish`, `M9` (both lines), with this reasoning, in a `[GOVERNANCE]` PR.
