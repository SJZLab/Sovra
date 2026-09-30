"""Agent test/evaluation fixture discovery (FTF-04A15, Section 7A.4/66/77/81).

Section 7A.4's own numbered "Analysis order" names step 9: "Detect agent
tests/evaluation fixtures." Steps 1-2 (FTF-04A08, CERTIFIED), 3/4/6 for
Python (FTF-04A09, CERTIFIED), 3/4/6 for TS/JS (FTF-04A10, CERTIFIED),
JSON/YAML/TOML/env-template configuration discovery (FTF-04A11, CERTIFIED),
model-binding (FTF-04A12, CERTIFIED), prompt sources (FTF-04A13, CERTIFIED),
and step 7/8 tool-schema/permission (FTF-04A14, CERTIFIED) are already
CERTIFIED; this task owns exactly step 9's disjoint remainder. It never
re-detects entrypoints/models/prompts/tools/config (those already-CERTIFIED
tasks' own jobs) and never assembles a `DiscoveredAgentCandidate` row
(FTF-04A17's own already-CERTIFIED storage schema, step 12) -- the same
"detection is not the same act as storage/selection" boundary every
04A-family task before this one has drawn.

Path refined from the task packet's own generic suggestion
(`packages/target-connectors/agent_test_evaluation_fixture_discovery.py`) to
`apps/api/app/target_connectors/agent_test_evaluation_fixture_discovery.py`,
matching every prior 04A-family task's own real
`apps/api/app/target_connectors/*.py` layout precedent exactly.

Scope -- a pure, synchronous, no-I/O-of-its-own function over a caller
-supplied `Sequence[SourceFileRef]` (FTF-04A08's own connector-agnostic
input shape, reused rather than reinvented), classifying each entry into at
most one of four disclosed, non-exhaustive categories, purely from its own
`relative_path` and `size` -- see "Never reads content" below for why this
is stronger than merely "read but redact":

  - `python_test`: filename matches pytest's own default discovery
    convention (`test_*.py`/`*_test.py`), the file is `conftest.py`, or the
    relative path contains a `tests`/`test` directory component (an exact
    path *segment*, never a substring match a path like
    `latest_release/foo.py` could accidentally trigger).
  - `js_ts_test`: filename matches `*.test.{ts,tsx,js,jsx}` or
    `*.spec.{ts,tsx,js,jsx}`, or the relative path contains a `__tests__`
    directory component (again an exact path segment).
  - `test_framework_config`: filename (by name only, never parsed here) is
    one of `pytest.ini`, `setup.cfg`, `tox.ini`, `pyproject.toml` (Python
    side) or `jest.config.*`, `vitest.config.*`, `playwright.config.*`
    (JS/TS side). This module never opens or parses any of these files --
    parsing `pyproject.toml`'s own `[tool.pytest.ini_options]` table (or any
    other config content) is FTF-04A11's own already-CERTIFIED job;
    duplicating it here would be exactly the kind of "scatter domain logic
    into one file per ticket simply because a ticket exists" Section 75.1
    already warns against. `pyproject.toml` is recognized as a possible
    marker purely by filename even though most repositories' own
    `pyproject.toml` is not pytest-related at all -- this is a disclosed,
    over-inclusive marker, not a claim that pytest is actually configured
    there.
  - `evaluation_fixture_data`: BOTH halves of this heuristic must hold --
    the relative path contains a `fixtures`/`fixture`/`evals`/`eval`/
    `golden` directory component AND the file extension is one of
    `.json`/`.yaml`/`.yml`/`.jsonl` (a closed, disclosed allowlist). An
    arbitrary extension under one of those directories is never treated as
    a fixture, and one of those extensions living elsewhere in the tree
    (for example `config/app.json`, already FTF-04A11's own territory) is
    never treated as a fixture either.

Everything else is not classified by this module at all -- not an error;
most files in a real repository are neither a test file, a framework
-config marker, nor evaluation fixture data.

Never reads content (the real proof mechanism for this task's own
negative_acceptance clause). Every one of the four categories above is
decidable from `relative_path`/`size` alone, so
`run_agent_test_evaluation_fixture_discovery` never calls a single
`SourceFileRef.read_content()` for any file, for any reason. This is
structurally stronger than FTF-04A11's own "read but redact" shape: content
that is never read cannot be graded, cannot be imported into a training
pipeline, and cannot certify a test's own correctness, because this module
never sees a single byte of it. `test_classification_never_reads_content`
proves this directly and behaviorally: a `SourceFileRef` whose own
`read_content` callable raises `AssertionError` if ever invoked is
classified correctly regardless, with that callable never firing.

This task's own acceptance clause ("Index relevant tests/fixtures and their
source version as possible evidence/data sources") is met by: this module's
own returned per-file evidence records ARE the "index." The clause's own
"source version" half is deliberately not fabricated here -- this pure
function, exactly like FTF-04A08/FTF-04A09/FTF-04A11 before it, only ever
sees a caller-supplied `SourceFileRef` sequence; no `TargetSourceSnapshot`
id, commit SHA, or other snapshot context ever reaches this function, so
there is nothing real to attach a `source_version` field to without
reaching into a DB/schema layer this task's own `database_changes` clause
("No independent schema change. Consume frozen contract.") forbids. The
"source version" of a given index entry is, honestly, whatever
`TargetSourceSnapshot` the caller captured `files` from in the first place
-- a future composition task (FTF-04A17, already-CERTIFIED storage schema)
is the one that has that context and attaches it when it builds a
`DiscoveredAgentCandidate.test_evidence_json` row from this module's own
output. No evidence record below carries a fabricated `source_version`
field; this boundary is disclosed here rather than papered over with a
fake value.

Resource limits: this module deliberately keeps only `max_files` from
sibling tasks' three-field `ResourceLimits` shape (FTF-04A08's/FTF-04A11's
own `max_files`/`max_file_bytes`/`max_total_bytes`). There is no content
read in this module at all, so `max_file_bytes`/`max_total_bytes` would
bound nothing real -- keeping them here would be exactly the kind of
"pretending to bound something you don't do" this task's own brief warns
against. `max_files` alone still protects the one real, unbounded resource
this module does consume: its own classification loop over the caller's
`files` sequence. Every run that stops before it reaches the end of that
sequence sets `truncated=True` -- an honestly incomplete scan is disclosed,
never silently reported as complete, the same discipline every sibling
04A-family task already established.

negative_acceptance ("Discovering tests never authorizes importing their
answers into training or certifies test correctness.") is proven as two
independent structural facts, each with its own dedicated test in
`tests/tasks/test_ftf_04a15.py` (mirroring FTF-04A14's own docstring framing
exactly):

  - **Never certifies test correctness.** This module contains no
    `subprocess`, `exec(`, `eval(`, or `importlib` usage anywhere -- it
    never executes, imports, or runs any classified file, including the
    ones it itself classifies as `python_test`/`js_ts_test`.
    `test_module_source_never_executes_imports_or_runs_a_subprocess` proves
    this via a static AST assertion over this module's own source (no
    `subprocess`/`importlib` import, no `exec`/`eval` call anywhere);
    `test_classification_never_reads_content` (above) proves the
    complementary, stronger behavioral fact that content is never even
    read, let alone executed.
  - **Never authorizes importing answers into training.** This module has
    zero imports from any dataset/database-schema module (it imports only
    `SourceFileRef` from FTF-04A08's own frozen contract, plus stdlib), and
    its own evidence dataclasses (`ClassifiedFixtureEvidence`,
    `AgentTestEvaluationFixtureDiscoveryEvidence`) declare no
    `approved`/`imported`/`dataset_id`-shaped field anywhere.
    `test_module_has_no_dataset_or_database_schema_imports` and
    `test_evidence_dataclasses_have_no_training_authorization_field` prove
    each half directly by asserting over this module's own AST imports and
    `__dataclass_fields__`.

Input shape: reuses FTF-04A08's own connector-agnostic `SourceFileRef`
(`apps/api/app/target_connectors/source_analysis_worker.py`) rather than
inventing a second one, the same "no second input shape invented"
precedent every sibling task in this family has already established.

Database changes: none (this task's own `database_changes` clause).
`run_agent_test_evaluation_fixture_discovery` is a synchronous, no-I/O-of
-its-own function over caller-supplied `SourceFileRef` entries. This module
does not itself assemble a `DiscoveredAgentCandidate` row or create a
`Dataset`/`DatasetRecord`/`DatasetSourceConnection` row -- Section 7A.10's
own "Dataset connection is a separate explicit step after target-product
connection" and "it must not import [discovered fixtures] until the user
selects the source and allowed purpose" apply directly here: discovery
recommends, it never itself imports. `proving_symbol` stays blank and
`product_truth_check.py`'s product-true count is deliberately unchanged,
the same disclosed "no live route yet" scope every prior 04A-family task
already established.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.target_connectors.source_analysis_worker import SourceFileRef

__all__ = [
    "AgentTestEvaluationFixtureDiscoveryEvidence",
    "AgentTestEvaluationFixtureDiscoveryError",
    "ClassifiedFixtureEvidence",
    "ResourceLimits",
    "run_agent_test_evaluation_fixture_discovery",
]

# Section 7A.4 step 9's own "agent tests" half, Python side -- pytest's own
# default discovery convention, a closed disclosed set.
_PYTHON_TEST_DIRECTORY_SEGMENTS = frozenset({"tests", "test"})
_PYTHON_TEST_CONFIG_BASENAMES = frozenset({"pytest.ini", "setup.cfg", "tox.ini", "pyproject.toml"})

# Section 7A.4 step 9's own "agent tests" half, JS/TS side -- a closed
# disclosed set of the real, common convention across today's popular JS/TS
# test runners (Jest, Vitest, Playwright).
_JS_TS_TEST_SUFFIXES = (
    ".test.ts",
    ".test.tsx",
    ".test.js",
    ".test.jsx",
    ".spec.ts",
    ".spec.tsx",
    ".spec.js",
    ".spec.jsx",
)
_JS_TS_TEST_DIRECTORY_SEGMENT = "__tests__"
_JS_TS_TEST_CONFIG_PREFIXES = ("jest.config.", "vitest.config.", "playwright.config.")

# Section 7A.4 step 9's own "evaluation fixtures" half -- BOTH halves of
# this heuristic must hold (see this module's own docstring): a directory
# component from this closed set AND an extension from the closed
# `_FIXTURE_DATA_EXTENSIONS` allowlist below.
_FIXTURE_DATA_DIRECTORY_SEGMENTS = frozenset({"fixtures", "fixture", "evals", "eval", "golden"})
_FIXTURE_DATA_EXTENSIONS = (".json", ".yaml", ".yml", ".jsonl")


class AgentTestEvaluationFixtureDiscoveryError(Exception):
    """A caller-supplied `SourceFileRef` violates this worker's own input
    shape (negative size) -- rejected at the boundary rather than
    propagated downstream."""


@dataclass(frozen=True)
class ResourceLimits:
    """This module's own real, disclosed resource-limit shape -- see this
    module's own docstring ("Resource limits") for why only `max_files` is
    kept here, unlike sibling tasks' own three-field shape."""

    max_files: int = 5_000


@dataclass(frozen=True)
class ClassifiedFixtureEvidence:
    """One real, classified file -- evidence only, never content-graded.
    `size_bytes` is read straight from the caller's own `SourceFileRef.size`,
    never derived from reading content (this module never reads content at
    all, see this module's own docstring)."""

    relative_path: str
    category: str
    size_bytes: int

    def to_evidence_json(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "category": self.category,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class AgentTestEvaluationFixtureDiscoveryEvidence:
    """This task's own real run-level evidence -- see this module's own
    docstring for the full scope boundary."""

    classified_files: tuple[ClassifiedFixtureEvidence, ...]
    files_scanned: int
    python_test_count: int
    js_ts_test_count: int
    framework_config_count: int
    fixture_data_count: int
    truncated: bool = False


def _path_segments(relative_path: str) -> list[str]:
    return relative_path.split("/")


def _basename(relative_path: str) -> str:
    return relative_path.rsplit("/", 1)[-1]


def _is_python_test_framework_config(basename: str) -> bool:
    return basename in _PYTHON_TEST_CONFIG_BASENAMES


def _is_js_ts_test(relative_path: str, basename: str) -> bool:
    lower = basename.lower()
    if lower.endswith(_JS_TS_TEST_SUFFIXES):
        return True
    directory_segments = _path_segments(relative_path)[:-1]
    return _JS_TS_TEST_DIRECTORY_SEGMENT in directory_segments


def _is_js_ts_test_framework_config(basename: str) -> bool:
    lower = basename.lower()
    return any(lower.startswith(prefix) for prefix in _JS_TS_TEST_CONFIG_PREFIXES)


def _is_evaluation_fixture_data(relative_path: str, basename: str) -> bool:
    lower = basename.lower()
    if not lower.endswith(_FIXTURE_DATA_EXTENSIONS):
        return False
    directory_segments = _path_segments(relative_path)[:-1]
    return any(segment in _FIXTURE_DATA_DIRECTORY_SEGMENTS for segment in directory_segments)


def _is_python_test_filename(basename: str) -> bool:
    if basename == "conftest.py":
        return True
    if basename.startswith("test_") and basename.endswith(".py"):
        return True
    return basename.endswith("_test.py")


def _is_python_test_directory_only(relative_path: str) -> bool:
    directory_segments = _path_segments(relative_path)[:-1]
    return any(segment in _PYTHON_TEST_DIRECTORY_SEGMENTS for segment in directory_segments)


def _classify(relative_path: str) -> str | None:
    """Pure, content-free classification -- decidable from `relative_path`
    alone. Evaluated in a fixed, disclosed precedence order so a single
    file is never double-classified:

    1. A real Python test *filename* (`conftest.py`/`test_*.py`/
       `*_test.py`) or a real JS/TS test *filename*
       (`*.test.ts`/`*.spec.ts`/...) -- the strongest, most specific
       signal.
    2. A test-framework-config filename marker.
    3. The evaluation-fixture-data heuristic (both halves must hold) --
       evaluated *before* the generic "under a tests/test directory"
       catch-all below, so the very common real convention
       `tests/fixtures/*.json` is reported as the more specific and more
       useful `evaluation_fixture_data`, not swallowed by the generic
       tests-directory match.
    4. The generic, directory-only "any file under a tests/test (Python)
       or __tests__ (JS/TS) directory component" catch-all -- deliberately
       last, since it is the least specific of these signals.
    """
    basename = _basename(relative_path)

    if _is_python_test_filename(basename):
        return "python_test"
    if _is_js_ts_test(relative_path, basename):
        return "js_ts_test"
    if _is_python_test_framework_config(basename) or _is_js_ts_test_framework_config(basename):
        return "test_framework_config"
    if _is_evaluation_fixture_data(relative_path, basename):
        return "evaluation_fixture_data"
    if _is_python_test_directory_only(relative_path):
        return "python_test"
    return None


def run_agent_test_evaluation_fixture_discovery(
    files: Sequence[SourceFileRef], *, resource_limits: ResourceLimits | None = None
) -> AgentTestEvaluationFixtureDiscoveryEvidence:
    """Section 7A.4 step 9's own "Detect agent tests/evaluation fixtures" --
    see this module's own docstring for the full scope boundary. Never
    calls `SourceFileRef.read_content()` for any entry, for any reason
    (this task's own real negative_acceptance proof mechanism -- see this
    module's own docstring). Only raises
    `AgentTestEvaluationFixtureDiscoveryError` for a structurally unsafe
    caller-supplied `SourceFileRef` itself (negative size) -- a caller bug,
    not untrusted snapshot content."""
    limits = resource_limits or ResourceLimits()

    classified: list[ClassifiedFixtureEvidence] = []
    files_scanned = 0
    python_test_count = 0
    js_ts_test_count = 0
    framework_config_count = 0
    fixture_data_count = 0
    truncated = False

    for file_ref in files:
        if not isinstance(file_ref.size, int) or file_ref.size < 0:
            raise AgentTestEvaluationFixtureDiscoveryError(
                f"size must be a real, non-negative integer for "
                f"{file_ref.relative_path!r}: {file_ref.size!r}"
            )

        if files_scanned >= limits.max_files:
            truncated = True
            break
        files_scanned += 1

        category = _classify(file_ref.relative_path)
        if category is None:
            continue

        classified.append(
            ClassifiedFixtureEvidence(
                relative_path=file_ref.relative_path,
                category=category,
                size_bytes=file_ref.size,
            )
        )
        if category == "python_test":
            python_test_count += 1
        elif category == "js_ts_test":
            js_ts_test_count += 1
        elif category == "test_framework_config":
            framework_config_count += 1
        elif category == "evaluation_fixture_data":
            fixture_data_count += 1

    return AgentTestEvaluationFixtureDiscoveryEvidence(
        classified_files=tuple(classified),
        files_scanned=files_scanned,
        python_test_count=python_test_count,
        js_ts_test_count=js_ts_test_count,
        framework_config_count=framework_config_count,
        fixture_data_count=fixture_data_count,
        truncated=truncated,
    )
