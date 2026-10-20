#!/usr/bin/env python3
"""FTF-04A15 -- Agent test/evaluation fixture discovery.

Runnable without any database and without any external network egress:
`run_agent_test_evaluation_fixture_discovery` is a pure, synchronous,
no-I/O-of-its-own function over a caller-supplied `SourceFileRef` sequence
(this task's own `database_changes` clause -- "No independent schema
change. Consume frozen contract.").

This task's own acceptance clause ("Index relevant tests/fixtures and
their source version as possible evidence/data sources") is covered by
`test_pytest_default_convention_file_is_classified_python_test`,
`test_conftest_py_is_classified_python_test`,
`test_underscore_test_suffix_is_classified_python_test`,
`test_tests_directory_segment_is_classified_python_test`,
`test_python_test_directory_segment_never_matches_as_substring`,
`test_js_test_suffix_is_classified_js_ts_test`,
`test_spec_suffix_is_classified_js_ts_test`,
`test_dunder_tests_directory_segment_is_classified_js_ts_test`,
`test_pytest_ini_is_classified_test_framework_config`,
`test_pyproject_toml_is_classified_test_framework_config_by_filename_only`,
`test_jest_config_is_classified_test_framework_config`,
`test_fixture_json_under_fixtures_dir_is_classified_evaluation_fixture_data`,
`test_golden_yaml_under_golden_dir_is_classified_evaluation_fixture_data`,
`test_evidence_records_size_bytes_from_source_file_ref_size`, and
`test_unrelated_file_is_not_classified`.

The fixture-data heuristic's own "both halves must hold" boundary is
covered by `test_fixture_extension_outside_fixture_directory_is_not_classified`
and `test_non_allowlisted_extension_inside_fixture_directory_is_not_classified`.
The disclosed classification-precedence order (fixture data over the
generic tests-directory catch-all) is covered by
`test_fixture_data_heuristic_takes_precedence_over_generic_tests_directory_match`.

This task's own negative_acceptance ("Discovering tests never authorizes
importing their answers into training or certifies test correctness.") is
covered as two independent structural facts, each with its own dedicated
test:

  1. Never certifies test correctness --
     `test_module_source_never_executes_imports_or_runs_a_subprocess`
     (static source-text assertion: no subprocess/exec(/eval(/importlib
     usage anywhere in this module) plus `test_classification_never_reads_
     content` (a dedicated behavioral test: a `SourceFileRef` classified as
     `python_test` whose own `read_content` raises `AssertionError` if ever
     invoked is nonetheless classified correctly, proving content is
     genuinely never read).
  2. Never authorizes importing answers into training --
     `test_module_has_no_dataset_or_database_schema_imports` (a static
     assertion over this module's own AST imports) and
     `test_evidence_dataclasses_have_no_training_authorization_field` (a
     static assertion over `ClassifiedFixtureEvidence`'s and
     `AgentTestEvaluationFixtureDiscoveryEvidence`'s own
     `__dataclass_fields__` -- no `approved`/`imported`/`dataset_id`-shaped
     field exists anywhere).

Resource-limiting and input-validation are covered by
`test_max_files_budget_truncates_run` and `test_negative_size_is_rejected`.

Run directly: python3 tests/tasks/test_ftf_04a15.py
"""

from __future__ import annotations

import ast
import sys
from dataclasses import fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.target_connectors.agent_test_evaluation_fixture_discovery import (  # noqa: E402
    AgentTestEvaluationFixtureDiscoveryError,
    AgentTestEvaluationFixtureDiscoveryEvidence,
    ClassifiedFixtureEvidence,
    ResourceLimits,
    run_agent_test_evaluation_fixture_discovery,
)
from app.target_connectors.source_analysis_worker import SourceFileRef  # noqa: E402

MODULE_PATH = API_ROOT / "app" / "target_connectors" / "agent_test_evaluation_fixture_discovery.py"

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _ref(relative_path: str, size: int = 0) -> SourceFileRef:
    return SourceFileRef(
        relative_path=relative_path,
        size=size,
        read_content=lambda: (_ for _ in ()).throw(
            AssertionError(f"read_content must never be called for {relative_path!r}")
        ),
    )


def _only(evidence: AgentTestEvaluationFixtureDiscoveryEvidence, relative_path: str):
    matches = [f for f in evidence.classified_files if f.relative_path == relative_path]
    assert len(matches) == 1, f"expected exactly one match for {relative_path!r}, got {matches!r}"
    return matches[0]


def test_pytest_default_convention_file_is_classified_python_test() -> None:
    evidence = run_agent_test_evaluation_fixture_discovery([_ref("src/tests/test_widget.py")])
    record = _only(evidence, "src/tests/test_widget.py")
    check(
        "FTF-04A15",
        "a filename matching pytest's own default 'test_*.py' convention is classified python_test",
        record.category == "python_test" and evidence.python_test_count == 1,
    )


def test_conftest_py_is_classified_python_test() -> None:
    evidence = run_agent_test_evaluation_fixture_discovery([_ref("src/conftest.py")])
    record = _only(evidence, "src/conftest.py")
    check(
        "FTF-04A15",
        "conftest.py is classified python_test regardless of its own directory location",
        record.category == "python_test",
    )


def test_underscore_test_suffix_is_classified_python_test() -> None:
    evidence = run_agent_test_evaluation_fixture_discovery([_ref("src/widget_test.py")])
    record = _only(evidence, "src/widget_test.py")
    check(
        "FTF-04A15",
        "a filename matching pytest's own '*_test.py' convention is classified python_test",
        record.category == "python_test",
    )


def test_tests_directory_segment_is_classified_python_test() -> None:
    evidence = run_agent_test_evaluation_fixture_discovery([_ref("app/tests/helpers.py")])
    record = _only(evidence, "app/tests/helpers.py")
    check(
        "FTF-04A15",
        "a relative path containing a real 'tests' directory segment is "
        "classified python_test even when the filename itself does not "
        "match a test-file naming convention",
        record.category == "python_test",
    )


def test_python_test_directory_segment_never_matches_as_substring() -> None:
    evidence = run_agent_test_evaluation_fixture_discovery([_ref("latest_release/helpers.py")])
    check(
        "FTF-04A15",
        "a path segment like 'latest_release' (which merely contains the "
        "substring 'test') is never guessed as a real 'tests'/'test' "
        "directory segment -- exact segment match only",
        len(evidence.classified_files) == 0,
    )


def test_js_test_suffix_is_classified_js_ts_test() -> None:
    evidence = run_agent_test_evaluation_fixture_discovery([_ref("src/widget.test.ts")])
    record = _only(evidence, "src/widget.test.ts")
    check(
        "FTF-04A15",
        "a filename matching '*.test.ts' is classified js_ts_test",
        record.category == "js_ts_test" and evidence.js_ts_test_count == 1,
    )


def test_spec_suffix_is_classified_js_ts_test() -> None:
    evidence = run_agent_test_evaluation_fixture_discovery([_ref("src/widget.spec.tsx")])
    record = _only(evidence, "src/widget.spec.tsx")
    check(
        "FTF-04A15",
        "a filename matching '*.spec.tsx' is classified js_ts_test",
        record.category == "js_ts_test",
    )


def test_dunder_tests_directory_segment_is_classified_js_ts_test() -> None:
    evidence = run_agent_test_evaluation_fixture_discovery([_ref("src/__tests__/widget.ts")])
    record = _only(evidence, "src/__tests__/widget.ts")
    check(
        "FTF-04A15",
        "a relative path containing a '__tests__' directory segment is "
        "classified js_ts_test even when the filename itself does not "
        "match a test-file naming convention",
        record.category == "js_ts_test",
    )


def test_pytest_ini_is_classified_test_framework_config() -> None:
    evidence = run_agent_test_evaluation_fixture_discovery([_ref("pytest.ini")])
    record = _only(evidence, "pytest.ini")
    check(
        "FTF-04A15",
        "pytest.ini is recognized as a possible test-framework marker by filename only",
        record.category == "test_framework_config" and evidence.framework_config_count == 1,
    )


def test_pyproject_toml_is_classified_test_framework_config_by_filename_only() -> None:
    evidence = run_agent_test_evaluation_fixture_discovery([_ref("pyproject.toml")])
    record = _only(evidence, "pyproject.toml")
    check(
        "FTF-04A15",
        "pyproject.toml is recognized as a possible pytest-config marker by "
        "filename only -- this module never opens or parses its content "
        "(that is FTF-04A11's own already-CERTIFIED job)",
        record.category == "test_framework_config",
    )


def test_jest_config_is_classified_test_framework_config() -> None:
    evidence = run_agent_test_evaluation_fixture_discovery([_ref("jest.config.ts")])
    record = _only(evidence, "jest.config.ts")
    check(
        "FTF-04A15",
        "jest.config.ts is recognized as a possible test-framework marker by filename only",
        record.category == "test_framework_config",
    )


def test_fixture_json_under_fixtures_dir_is_classified_evaluation_fixture_data() -> None:
    evidence = run_agent_test_evaluation_fixture_discovery([_ref("tests/fixtures/customer.json")])
    record = _only(evidence, "tests/fixtures/customer.json")
    check(
        "FTF-04A15",
        "a .json file under a fixtures/ directory is classified evaluation_fixture_data",
        record.category == "evaluation_fixture_data" and evidence.fixture_data_count == 1,
    )


def test_golden_yaml_under_golden_dir_is_classified_evaluation_fixture_data() -> None:
    evidence = run_agent_test_evaluation_fixture_discovery(
        [_ref("evals/golden/expected_output.yaml")]
    )
    record = _only(evidence, "evals/golden/expected_output.yaml")
    check(
        "FTF-04A15",
        "a .yaml file under an evals/ (and golden/) directory is classified "
        "evaluation_fixture_data",
        record.category == "evaluation_fixture_data",
    )


def test_fixture_extension_outside_fixture_directory_is_not_classified() -> None:
    evidence = run_agent_test_evaluation_fixture_discovery([_ref("config/app.json")])
    check(
        "FTF-04A15",
        "an allowlisted fixture extension (.json) living outside any "
        "fixtures/evals/golden directory is never treated as fixture data "
        "-- both halves of the heuristic must hold",
        len(evidence.classified_files) == 0,
    )


def test_non_allowlisted_extension_inside_fixture_directory_is_not_classified() -> None:
    # Deliberately NOT nested under a tests/test directory segment, so this
    # exercises only the fixture-data heuristic's own "both halves must
    # hold" boundary, independent of the separate, generic
    # "under a tests/test directory" python_test catch-all.
    evidence = run_agent_test_evaluation_fixture_discovery([_ref("fixtures/readme.txt")])
    check(
        "FTF-04A15",
        "a non-allowlisted extension (.txt) living under a fixtures/ "
        "directory is never guessed as fixture data -- both halves of the "
        "heuristic must hold",
        len(evidence.classified_files) == 0,
    )


def test_fixture_data_heuristic_takes_precedence_over_generic_tests_directory_match() -> None:
    # tests/fixtures/*.json is a very common real convention: both the
    # generic "under a tests/ directory" python_test catch-all and the
    # more specific evaluation_fixture_data heuristic literally match this
    # same path. The more specific, more useful classification wins (see
    # this module's own `_classify` docstring for the disclosed precedence
    # order).
    evidence = run_agent_test_evaluation_fixture_discovery([_ref("tests/fixtures/customer.json")])
    record = _only(evidence, "tests/fixtures/customer.json")
    check(
        "FTF-04A15",
        "evaluation_fixture_data is reported (not the generic python_test "
        "catch-all) when a fixture-shaped path also happens to sit under a "
        "tests/ directory",
        record.category == "evaluation_fixture_data",
    )


def test_evidence_records_size_bytes_from_source_file_ref_size() -> None:
    evidence = run_agent_test_evaluation_fixture_discovery(
        [_ref("tests/test_widget.py", size=1234)]
    )
    record = _only(evidence, "tests/test_widget.py")
    check(
        "FTF-04A15",
        "size_bytes is read straight from SourceFileRef.size, never from reading content",
        record.size_bytes == 1234,
    )


def test_unrelated_file_is_not_classified() -> None:
    evidence = run_agent_test_evaluation_fixture_discovery([_ref("src/widget.py")])
    check(
        "FTF-04A15",
        "a file matching none of the four categories is not classified at "
        "all -- not an error, most files in a repository are neither",
        len(evidence.classified_files) == 0 and evidence.files_scanned == 1,
    )


def test_classification_never_reads_content() -> None:
    """Dedicated behavioral proof for negative_acceptance half 1: a
    SourceFileRef whose read_content callable raises if ever invoked is
    nonetheless classified correctly, across all four categories, proving
    content is genuinely never read for classification."""
    refs = [
        _ref("tests/test_widget.py"),
        _ref("src/widget.test.ts"),
        _ref("pytest.ini"),
        _ref("tests/fixtures/customer.json"),
    ]
    # _ref's own read_content raises AssertionError if ever called -- a run
    # that completes without raising proves none of these four were read.
    evidence = run_agent_test_evaluation_fixture_discovery(refs)
    check(
        "FTF-04A15",
        "all four category shapes are classified correctly without their "
        "own read_content ever being invoked (no AssertionError escaped)",
        evidence.python_test_count == 1
        and evidence.js_ts_test_count == 1
        and evidence.framework_config_count == 1
        and evidence.fixture_data_count == 1,
    )


def test_module_source_never_executes_imports_or_runs_a_subprocess() -> None:
    """A static AST assertion over this module's own real code -- not a
    naive source-text substring search, which would false-positive on this
    module's own docstring prose that names these forbidden APIs while
    explaining why they are absent."""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
    forbidden_import_roots = {"subprocess", "importlib"}
    forbidden_call_names = {"exec", "eval"}

    has_forbidden_import = False
    has_forbidden_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] in forbidden_import_roots for alias in node.names):
                has_forbidden_import = True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in forbidden_import_roots:
                has_forbidden_import = True
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in forbidden_call_names
        ):
            has_forbidden_call = True

    check(
        "FTF-04A15",
        "this module never executes, imports, or runs any classified "
        "file's content -- no real subprocess/importlib import and no "
        "real exec(/eval( call anywhere in this module's own AST",
        not has_forbidden_import and not has_forbidden_call,
    )


def test_module_has_no_dataset_or_database_schema_imports() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
    forbidden_markers = ("dataset", "database", "models.db", "db.models", "sqlalchemy")
    offending = [
        name
        for name in imported_modules
        if any(marker in name.lower() for marker in forbidden_markers)
    ]
    check(
        "FTF-04A15",
        "this module has zero imports from any dataset/database-schema "
        "module -- it imports only SourceFileRef from FTF-04A08's own "
        "frozen contract, plus stdlib",
        offending == [],
    )


def test_evidence_dataclasses_have_no_training_authorization_field() -> None:
    forbidden_field_markers = ("approved", "imported", "dataset_id", "dataset", "authorized")
    offending: list[str] = []
    for dataclass_type in (ClassifiedFixtureEvidence, AgentTestEvaluationFixtureDiscoveryEvidence):
        for field in fields(dataclass_type):
            if any(marker in field.name.lower() for marker in forbidden_field_markers):
                offending.append(f"{dataclass_type.__name__}.{field.name}")
    check(
        "FTF-04A15",
        "neither evidence dataclass declares an approved/imported/"
        "dataset_id-shaped field anywhere -- the absence of such a field "
        "is itself the proof that discovery never authorizes training "
        "import",
        offending == [],
    )


def test_max_files_budget_truncates_run() -> None:
    refs = [_ref(f"tests/test_widget_{i}.py") for i in range(10)]
    evidence = run_agent_test_evaluation_fixture_discovery(
        refs, resource_limits=ResourceLimits(max_files=3)
    )
    check(
        "FTF-04A15",
        "the scanned-file count never exceeds max_files, and the run "
        "discloses truncated=True rather than silently reporting a "
        "complete scan",
        evidence.files_scanned == 3 and evidence.truncated is True,
    )


def test_negative_size_is_rejected() -> None:
    bad_ref = SourceFileRef(relative_path="tests/test_widget.py", size=-1, read_content=lambda: b"")
    raised = False
    try:
        run_agent_test_evaluation_fixture_discovery([bad_ref])
    except AgentTestEvaluationFixtureDiscoveryError:
        raised = True
    check(
        "FTF-04A15",
        "a structurally invalid (negative-size) SourceFileRef is rejected "
        "outright, not silently accepted",
        raised,
    )


def main() -> None:
    test_fns = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    for _name, fn in test_fns:
        fn()

    if FAILURES:
        print(f"FTF-04A15 Agent test/evaluation fixture discovery: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print(
        f"FTF-04A15 Agent test/evaluation fixture discovery: all checks passed "
        f"({len(test_fns)} test functions run)"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
