#!/usr/bin/env python3
"""FTF-04A08 -- Source-analysis worker foundation.

Runnable without any database and without any external network egress:
`run_source_analysis` is a pure, synchronous, no-I/O-of-its-own function
over a caller-supplied `SourceFileRef` sequence (this task's own
`database_changes` clause -- "No independent schema change. Consume
frozen contract.").

This task's own acceptance clause ("Run resource-limited static analyzer
with read-only snapshot and evidence output schema") is covered by
`test_language_detected_from_extension`,
`test_package_manager_detected_from_marker_file`,
`test_framework_marker_detected_from_package_json_dependency`,
`test_framework_marker_detected_from_requirements_txt`,
`test_framework_marker_detected_from_pyproject_toml_project_dependencies`,
`test_unknown_custom_framework_is_not_guessed`,
`test_generated_vendor_directory_is_ignored`,
`test_nested_vendor_directory_is_ignored`,
`test_secret_path_is_ignored`,
`test_oversized_manifest_file_is_skipped_not_read`,
`test_total_byte_budget_truncates_run`,
`test_max_files_budget_truncates_run`, and
`test_max_total_entries_bounds_raw_iteration`.

This task's own negative_acceptance ("Do not import or execute target
code/hooks/build scripts; malicious parser input cannot escape worker")
is covered by `test_module_never_imports_or_executes_scanned_content`,
`test_malformed_package_json_does_not_raise`,
`test_deeply_nested_package_json_does_not_raise`,
`test_duplicate_relative_path_is_rejected`,
`test_path_traversal_relative_path_is_rejected`,
`test_absolute_relative_path_is_rejected`, and
`test_truncated_run_is_disclosed_not_silently_complete`.

Run directly: python3 tests/tasks/test_ftf_04a08.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.target_connectors.source_analysis_worker import (  # noqa: E402
    ResourceLimits,
    SourceAnalysisWorkerError,
    SourceFileRef,
    run_source_analysis,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _ref(relative_path: str, content: bytes = b"") -> SourceFileRef:
    return SourceFileRef(
        relative_path=relative_path, size=len(content), read_content=lambda: content
    )


def test_language_detected_from_extension() -> None:
    evidence = run_source_analysis(
        [_ref("src/agent.py"), _ref("src/index.ts"), _ref("src/widget.tsx"), _ref("src/legacy.js")]
    )
    check(
        "FTF-04A08",
        "python/typescript/javascript are detected from real file extensions",
        set(evidence.detected_languages) == {"python", "typescript", "javascript"},
    )


def test_package_manager_detected_from_marker_file() -> None:
    evidence = run_source_analysis(
        [_ref("requirements.txt", b"fastapi\n"), _ref("pnpm-lock.yaml", b"")]
    )
    check(
        "FTF-04A08",
        "pip/pnpm are detected from their own real marker files",
        set(evidence.detected_package_managers) == {"pip", "pnpm"},
    )


def test_framework_marker_detected_from_package_json_dependency() -> None:
    content = json.dumps({"dependencies": {"express": "^4.0.0"}}).encode("utf-8")
    evidence = run_source_analysis([_ref("package.json", content)])
    check(
        "FTF-04A08",
        "a named framework dependency in package.json is detected",
        "express" in evidence.detected_framework_markers,
    )


def test_framework_marker_detected_from_requirements_txt() -> None:
    evidence = run_source_analysis([_ref("requirements.txt", b"fastapi>=0.100,<1.0\nuvicorn\n")])
    check(
        "FTF-04A08",
        "a named framework dependency spec in requirements.txt is detected",
        "fastapi" in evidence.detected_framework_markers,
    )


def test_framework_marker_detected_from_pyproject_toml_project_dependencies() -> None:
    content = b'[project]\ndependencies = ["django>=5.0"]\n'
    evidence = run_source_analysis([_ref("pyproject.toml", content)])
    check(
        "FTF-04A08",
        "a named framework dependency in pyproject.toml [project.dependencies] is detected",
        "django" in evidence.detected_framework_markers,
    )


def test_unknown_custom_framework_is_not_guessed() -> None:
    evidence = run_source_analysis(
        [_ref("requirements.txt", b"totallycustominternalframework==1.0\n")]
    )
    check(
        "FTF-04A08",
        "Section 7A.4's own 'Unknown/custom frameworks are not guessed' -- an "
        "unnamed dependency never produces a framework marker",
        evidence.detected_framework_markers == (),
    )


def test_generated_vendor_directory_is_ignored() -> None:
    evidence = run_source_analysis(
        [_ref("node_modules/left-pad/index.js"), _ref(".git/HEAD"), _ref("src/real.py")]
    )
    check(
        "FTF-04A08",
        "node_modules/.git content is ignored, never scanned as real source",
        "node_modules/left-pad/index.js" not in evidence.included_paths
        and ".git/HEAD" not in evidence.included_paths
        and "src/real.py" in evidence.included_paths,
    )
    check(
        "FTF-04A08",
        "every ignored path is recorded with a real, disclosed reason",
        all(
            record.reason.startswith("generated_or_vendor_directory:")
            for record in evidence.ignored_paths
        ),
    )


def test_nested_vendor_directory_is_ignored() -> None:
    evidence = run_source_analysis([_ref("backend/services/node_modules/pkg/index.js")])
    check(
        "FTF-04A08",
        "a vendor directory nested arbitrarily deep is still ignored (segment "
        "check, not only a top-level check)",
        evidence.included_paths == () and len(evidence.ignored_paths) == 1,
    )


def test_secret_path_is_ignored() -> None:
    evidence = run_source_analysis(
        [_ref("config/.env"), _ref("deploy/server.pem"), _ref("src/normal.py")]
    )
    check(
        "FTF-04A08",
        "an explicit secret-shaped filename/suffix is ignored by explicit rule, " "never scanned",
        "config/.env" not in evidence.included_paths
        and "deploy/server.pem" not in evidence.included_paths,
    )
    check(
        "FTF-04A08",
        "the disclosed reason for a secret-path exclusion is 'secret_path'",
        {
            r.reason
            for r in evidence.ignored_paths
            if r.relative_path in ("config/.env", "deploy/server.pem")
        }
        == {"secret_path"},
    )


def test_oversized_manifest_file_is_skipped_not_read() -> None:
    read_calls: list[str] = []

    def _poison_read() -> bytes:
        read_calls.append("called")
        return b'{"dependencies": {"express": "1.0"}}'

    oversized = SourceFileRef(
        relative_path="package.json", size=10_000_000, read_content=_poison_read
    )
    evidence = run_source_analysis(
        [oversized], resource_limits=ResourceLimits(max_file_bytes=1_000)
    )
    check(
        "FTF-04A08",
        "a manifest file over max_file_bytes is never read at all (its own "
        "read_content is never invoked)",
        read_calls == [] and evidence.files_skipped_oversized == 1,
    )
    check(
        "FTF-04A08",
        "an unread oversized manifest contributes no framework marker",
        evidence.detected_framework_markers == (),
    )


def test_total_byte_budget_truncates_run() -> None:
    content = b'{"dependencies": {"express": "1.0"}}'
    refs = [_ref(f"pkg{i}/package.json", content) for i in range(5)]
    evidence = run_source_analysis(
        refs, resource_limits=ResourceLimits(max_total_bytes=len(content))
    )
    check(
        "FTF-04A08",
        "once the cumulative byte budget is spent, further manifest content is "
        "never read and the run is disclosed as truncated",
        evidence.truncated is True and evidence.total_bytes_scanned <= len(content),
    )


def test_max_files_budget_truncates_run() -> None:
    refs = [_ref(f"src/file_{i}.py") for i in range(10)]
    evidence = run_source_analysis(refs, resource_limits=ResourceLimits(max_files=3))
    check(
        "FTF-04A08",
        "the scanned-file count never exceeds max_files, and the run discloses "
        "truncated=True rather than silently reporting a complete scan",
        evidence.files_scanned == 3 and evidence.truncated is True,
    )


def test_max_total_entries_bounds_raw_iteration() -> None:
    # Every entry here is ignored (vendor dir) -- proves the entry-count bound
    # protects the raw iteration itself, not only the "included" count, so a
    # caller cannot exhaust worker time merely by supplying an unbounded
    # number of ignored entries.
    refs = [_ref(f"node_modules/pkg_{i}/index.js") for i in range(10)]
    evidence = run_source_analysis(refs, resource_limits=ResourceLimits(max_total_entries=4))
    check(
        "FTF-04A08",
        "max_total_entries bounds the raw iteration even when every entry is " "ignored",
        evidence.total_entries_examined == 4 and evidence.truncated is True,
    )


def test_module_never_imports_or_executes_scanned_content() -> None:
    full_source_text = Path(
        API_ROOT / "app" / "target_connectors" / "source_analysis_worker.py"
    ).read_text("utf-8")
    # Skip the module's own docstring, which discusses these primitives by
    # name in prose (to disclose exactly what is *not* used) -- the real
    # claim under test is that the executable code never calls them.
    _, _, source_text = full_source_text.partition('"""')
    _, _, source_text = source_text.partition('"""')
    # `re.compile(...)` (compiling a regex pattern) is not the builtin
    # code-compiling `compile(...)` this check is guarding against.
    source_text_without_regex_compile = source_text.replace("re.compile(", "")
    forbidden_substrings = (
        "exec(",
        "eval(",
        "compile(",
        "importlib",
        "subprocess",
        "os.system",
        "os.popen",
        "__import__",
    )
    check(
        "FTF-04A08",
        "Section 7A.4's own 'without importing Python modules, executing "
        "JavaScript... running... builds' -- none of these code-execution "
        "primitives appear anywhere in this worker's own source",
        not any(token in source_text_without_regex_compile for token in forbidden_substrings),
    )

    # Behavioral half: a .py file whose content would mutate a marker if
    # ever imported/executed is included only as an (inert) language-detection
    # signal -- its content is never read at all (.py is not a recognized
    # manifest basename), so the marker can never be touched by this worker.
    marker: list[str] = []

    def _poison_read() -> bytes:
        marker.append("executed")
        return b"marker.append('executed')"

    malicious_py = SourceFileRef(
        relative_path="src/malicious.py", size=64, read_content=_poison_read
    )
    run_source_analysis([malicious_py])
    check(
        "FTF-04A08",
        "a .py file's own content is never read by this worker (only "
        "package.json/pyproject.toml/requirements.txt content is ever read), "
        "so a malicious .py payload cannot execute",
        marker == [],
    )


def test_malformed_package_json_does_not_raise() -> None:
    evidence = run_source_analysis([_ref("package.json", b"{not valid json at all")])
    check(
        "FTF-04A08",
        "a malformed manifest file does not raise out of this worker -- caught "
        "and counted, never crashes the run",
        evidence.manifest_parse_errors == 1 and evidence.detected_framework_markers == (),
    )


def test_deeply_nested_package_json_does_not_raise() -> None:
    # A fixed nesting depth is not portable: CPython interpreters differ in
    # how many C-stack frames one `json.loads` recursion level consumes (an
    # earlier version of this test used a fixed depth that reliably raised
    # `RecursionError` on Python 3.11 but not on Python 3.12's own CI
    # runner). Deterministically lower the recursion limit first, then nest
    # far deeper than that lowered limit -- guaranteed to overflow on any
    # interpreter, restored afterward so this test cannot affect any other.
    original_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(100)
    try:
        nested = ("[" * 5000) + ("]" * 5000)
        content = ('{"dependencies": {}, "x": ' + nested + "}").encode("utf-8")
        evidence = run_source_analysis([_ref("package.json", content)])
    finally:
        sys.setrecursionlimit(original_limit)
    check(
        "FTF-04A08",
        "adversarially deep JSON nesting (RecursionError-shaped input) does "
        "not raise out of this worker",
        evidence.manifest_parse_errors == 1,
    )


def test_duplicate_relative_path_is_rejected() -> None:
    raised = False
    try:
        run_source_analysis([_ref("src/agent.py"), _ref("src/agent.py")])
    except SourceAnalysisWorkerError:
        raised = True
    check(
        "FTF-04A08",
        "a duplicate relative_path within one run is rejected, not silently "
        "merged or double-counted",
        raised,
    )


def test_path_traversal_relative_path_is_rejected() -> None:
    raised = False
    try:
        run_source_analysis([_ref("../../etc/passwd")])
    except SourceAnalysisWorkerError:
        raised = True
    check(
        "FTF-04A08",
        "a path-traversal-shaped relative_path is rejected outright",
        raised,
    )


def test_absolute_relative_path_is_rejected() -> None:
    raised = False
    try:
        run_source_analysis([_ref("/etc/passwd")])
    except SourceAnalysisWorkerError:
        raised = True
    check(
        "FTF-04A08",
        "an absolute relative_path is rejected outright",
        raised,
    )


def test_truncated_run_is_disclosed_not_silently_complete() -> None:
    refs = [_ref(f"src/file_{i}.py") for i in range(3)]
    complete_evidence = run_source_analysis(refs)
    check(
        "FTF-04A08",
        "a run that consumes its entire input is disclosed as not truncated",
        complete_evidence.truncated is False,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A08 source-analysis worker foundation: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A08 source-analysis worker foundation: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
