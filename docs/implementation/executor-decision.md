# Executor decision — FTF-R014

Real record of the coding executor actually used to implement Sovra
(Model Factory), per Section 81/84's requirement to bind the selected
executor to the embedded Release 1 execution controls before broad
fan-out. This is a build/implementation-tooling decision record — it does
not describe a Factory production runtime dependency, and no Factory
service (`apps/api`, any `workers/*` package) reads this file.

## Selected executor

- **Executor:** Claude Code CLI (Anthropic), running in this repository's
  managed remote execution sessions (interactive and scheduled/autonomous
  relay shifts alike).
- **Model route:** the session's configured model identifier, currently
  `claude-sonnet-5`, with documented fallbacks (`claude-opus-5[1m]`,
  `claude-opus-4-8[1m]`) tried in order if the primary is unavailable. The
  serving model can differ from the configured identifier and can change
  mid-session; `model_route` on any individual receipt records the
  identifier configured for that session, not a guarantee of which model
  actually served every request in it.
- **Execution control profile: `R1_EMBEDDED`.** No separately maintained,
  frozen Agentic SDLC skill artifact (referenced in Section 84.9 as "prior
  project decisions as v20.1") is installed in or adopted by this
  repository — confirmed by inspecting the repository for any such
  artifact before writing this record, not assumed. A generic
  `anthropic-skills:agentic-sdlc` skill exists at the Claude Code account
  level and is available to any session using this account, but it has
  never been selected, installed, or recorded as this project's specific
  frozen external skill; using `R1_EMBEDDED_PLUS_EXISTING_SKILL` here
  without a real, recorded name/version/hash for an actually-adopted skill
  artifact would be exactly the fabricated-conformance case this task's
  own negative_acceptance clause forbids. `external_skill` is therefore
  `null` on every real receipt this repository produces today.
- **OpenHands:** not installed, not selected, not run, anywhere in this
  repository's history. `openhands_selected_and_run` is `False` on every
  real receipt; any future receipt claiming OpenHands qualification
  without genuine selection and execution is rejected by
  `tools/implementation/executor_contract.py`'s `validate_receipt`.

## Digest binding

`tools/implementation/executor_contract.py`'s `compute_expected_digests()`
computes both required digests live against the real, current
`spec/RELEASE_1.md` on every validation call — never a value pasted once
and left to go stale:

- `source_file_digest` — SHA-256 of the whole `spec/RELEASE_1.md` file.
- `embedded_section_digest` — SHA-256 of Section 84's real, current text,
  extracted by the same section-boundary logic `tools/r1_handoff.py`'s own
  `section_map()` uses (kept as an independent implementation rather than
  importing that file, since it is frozen byte-for-byte as the master
  spec's own embedded Section 86 helper).

A receipt whose digests do not match the real current file is rejected —
this is what "missing/mismatched embedded-control digest" (this task's own
negative_acceptance clause) means in practice: a stale or edited local
copy of Section 84 cannot pass.

## Task isolation, non-self-certification, protected gates (Q41)

Every real receipt records a `workspace_identity` (the actual branch this
session pushes to — never `main` itself) and distinct
`producer_identity`/`validator_identity` values; `validate_receipt` rejects
a receipt where they're equal (`SELF_CERTIFICATION_DENIED`, the same Law 4
rule `scripts/task_claim.py` already enforces for task claims). A receipt
whose `changed_files` touches any path `.github/CODEOWNERS` protects must
additionally be validated by that file's real, sole reviewer
(`owner:samerzakaria`, confirmed live via `get_me`/
`list_repository_collaborators` in the FTF-H028 shift) — an ordinary
relay/producer session validating its own protected-gate change is
rejected, matching Section 84.6's "a producer... cannot certify its own
modified gate."

## Parallel execution (Q33/Q42) — scope note, not re-claimed here

This task's own `qualification_scenarios` are `Q33` and `Q41` — not `Q42`.
Q33's real safe-parallel-wave evidence already exists: FTF-H034's
`scripts/task_claim.py`, proven with 8 genuinely racing OS threads against
a real `threading.Barrier`, with the same disclosed, honest limitation
carried forward here rather than re-asserted or silently dropped: this
sandbox has no live multi-agent dispatch harness able to hand a task to a
second, truly separate coding-executor process today. This task does not
re-claim that evidence or attempt to manufacture a second independent
executor process — it proves Q41's own, narrower scope (selected-executor
identity, digest binding, non-self-certification, protected-gate
ownership), which `tests/implementation/test_r1_execution_binding.py`
exercises directly against this repository's real files.
