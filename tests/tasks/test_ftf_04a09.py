#!/usr/bin/env python3
"""FTF-04A09 -- Python deterministic agent/config/tool discovery.

Runnable without any database and without any external network egress:
`run_python_agent_config_tool_discovery` is a pure, synchronous, no-I/O-of-
its-own function over a caller-supplied `SourceFileRef` sequence (this
task's own `database_changes` clause -- "No independent schema change.
Consume frozen contract.").

This task's own acceptance clause ("Parse tested Python AST/config
patterns for agent entrypoints, tools and model bindings with evidence")
is covered by `test_entrypoint_detected_from_bare_litellm_import`,
`test_entrypoint_detected_from_aliased_module_import`,
`test_entrypoint_detected_from_from_import`,
`test_model_binding_resolved_from_literal_string`,
`test_model_binding_argument_not_present`,
`test_tool_schema_resolved_from_literal_openai_function_list`,
`test_entrypoint_function_name_captured_for_nested_function`,
`test_module_level_call_uses_module_scope_name`, and
`test_two_calls_in_two_functions_produce_two_candidates`.

This task's own negative_acceptance ("Dynamic/unresolved constructs remain
unknown; import side effects never run during detection") is covered by
`test_model_binding_unresolved_when_dynamic_expression`,
`test_tool_schema_unresolved_when_dynamic_expression`,
`test_tool_schema_unresolved_when_one_entry_is_not_openai_shaped`,
`test_unrelated_completion_function_is_not_guessed`,
`test_relative_litellm_import_is_not_matched`,
`test_module_never_imports_or_executes_scanned_content`,
`test_malformed_python_file_does_not_raise`,
`test_non_python_file_is_skipped`,
`test_oversized_file_is_skipped_not_read`,
`test_total_byte_budget_truncates_run`,
`test_max_files_budget_truncates_run`, and
`test_negative_size_is_rejected`.

Run directly: python3 tests/tasks/test_ftf_04a09.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.target_connectors.python_agent_config_tool_discovery import (  # noqa: E402
    PythonDiscoveryError,
    ResourceLimits,
    run_python_agent_config_tool_discovery,
)
from app.target_connectors.source_analysis_worker import SourceFileRef  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _ref(relative_path: str, content: bytes = b"") -> SourceFileRef:
    return SourceFileRef(
        relative_path=relative_path, size=len(content), read_content=lambda: content
    )


def test_entrypoint_detected_from_bare_litellm_import() -> None:
    content = b'import litellm\n\ndef handle_request():\n    litellm.completion(model="gpt-4o")\n'
    evidence = run_python_agent_config_tool_discovery([_ref("src/agent.py", content)])
    check(
        "FTF-04A09",
        "a real litellm.completion(...) call after 'import litellm' is detected as "
        "one entrypoint candidate",
        len(evidence.entrypoints) == 1
        and evidence.entrypoints[0].call_attribute == "litellm.completion",
    )


def test_entrypoint_detected_from_aliased_module_import() -> None:
    content = (
        b"import litellm as ll\n\ndef handle_request():\n" b'    ll.acompletion(model="gpt-4o")\n'
    )
    evidence = run_python_agent_config_tool_discovery([_ref("src/agent.py", content)])
    check(
        "FTF-04A09",
        "'import litellm as ll' followed by ll.acompletion(...) is detected via the "
        "real module alias, not merely the literal name 'litellm'",
        len(evidence.entrypoints) == 1
        and evidence.entrypoints[0].call_attribute == "ll.acompletion",
    )


def test_entrypoint_detected_from_from_import() -> None:
    content = (
        b"from litellm import completion\n\ndef handle_request():\n"
        b'    completion(model="gpt-4o")\n'
    )
    evidence = run_python_agent_config_tool_discovery([_ref("src/agent.py", content)])
    check(
        "FTF-04A09",
        "'from litellm import completion' followed by a bare completion(...) call "
        "is detected via the real function alias",
        len(evidence.entrypoints) == 1 and evidence.entrypoints[0].call_attribute == "completion",
    )


def test_model_binding_resolved_from_literal_string() -> None:
    content = b'import litellm\n\nlitellm.completion(model="support-agent-prod")\n'
    evidence = run_python_agent_config_tool_discovery([_ref("src/agent.py", content)])
    binding = evidence.entrypoints[0].model_binding
    check(
        "FTF-04A09",
        "a literal string model= argument resolves to the real model alias",
        binding.resolved is True
        and binding.model_alias == "support-agent-prod"
        and binding.reason == "literal_string",
    )


def test_model_binding_argument_not_present() -> None:
    content = b"import litellm\n\nlitellm.completion(messages=[])\n"
    evidence = run_python_agent_config_tool_discovery([_ref("src/agent.py", content)])
    binding = evidence.entrypoints[0].model_binding
    check(
        "FTF-04A09",
        "a call with no model= keyword is honestly 'argument_not_present', never "
        "guessed from another argument",
        binding.resolved is False and binding.reason == "argument_not_present",
    )


def test_model_binding_unresolved_when_dynamic_expression() -> None:
    content = (
        b"import litellm\n\ndef handle_request(selected_model):\n"
        b"    litellm.completion(model=selected_model)\n"
    )
    evidence = run_python_agent_config_tool_discovery([_ref("src/agent.py", content)])
    binding = evidence.entrypoints[0].model_binding
    check(
        "FTF-04A09",
        "Section 7A.4's own 'Dynamic/unresolved constructs remain unknown' -- a "
        "variable model= argument is never guessed",
        binding.resolved is False and binding.reason == "unresolved_dynamic_expression",
    )


def test_tool_schema_resolved_from_literal_openai_function_list() -> None:
    content = (
        b"import litellm\n\nlitellm.completion(\n"
        b'    model="gpt-4o",\n'
        b'    tools=[{"type": "function", "function": {"name": "lookup_order", '
        b'"parameters": {}}}],\n'
        b")\n"
    )
    evidence = run_python_agent_config_tool_discovery([_ref("src/agent.py", content)])
    tools = evidence.entrypoints[0].tool_schema
    check(
        "FTF-04A09",
        "a literal, OpenAI-function-shaped tools= list resolves the real tool name",
        tools.resolved is True
        and tools.tool_names == ("lookup_order",)
        and tools.reason == "literal_openai_function_list",
    )


def test_tool_schema_unresolved_when_dynamic_expression() -> None:
    content = (
        b"import litellm\n\ndef handle_request(registered_tools):\n"
        b'    litellm.completion(model="gpt-4o", tools=registered_tools)\n'
    )
    evidence = run_python_agent_config_tool_discovery([_ref("src/agent.py", content)])
    tools = evidence.entrypoints[0].tool_schema
    check(
        "FTF-04A09",
        "a variable tools= argument is never guessed -- honestly unresolved",
        tools.resolved is False and tools.reason == "unresolved_dynamic_expression",
    )


def test_tool_schema_unresolved_when_one_entry_is_not_openai_shaped() -> None:
    content = (
        b"import litellm\n\nlitellm.completion(\n"
        b'    model="gpt-4o",\n'
        b'    tools=[{"type": "function", "function": {"name": "a", "parameters": {}}},'
        b' {"custom": "shape"}],\n'
        b")\n"
    )
    evidence = run_python_agent_config_tool_discovery([_ref("src/agent.py", content)])
    tools = evidence.entrypoints[0].tool_schema
    check(
        "FTF-04A09",
        "one non-conforming list entry marks the entire extraction unresolved -- a "
        "partial extraction is never reported as if it were complete",
        tools.resolved is False and tools.tool_names == (),
    )


def test_unrelated_completion_function_is_not_guessed() -> None:
    content = b'def completion(model="whatever"):\n    return model\n\ncompletion(model="x")\n'
    evidence = run_python_agent_config_tool_discovery([_ref("src/utils.py", content)])
    check(
        "FTF-04A09",
        "a locally-defined function merely named 'completion', with no real "
        "litellm import in the file, is never matched -- unknown/custom is not "
        "guessed",
        evidence.entrypoints == (),
    )


def test_relative_litellm_import_is_not_matched() -> None:
    content = b'from . import litellm\n\nlitellm.completion(model="x")\n'
    evidence = run_python_agent_config_tool_discovery([_ref("src/agent.py", content)])
    check(
        "FTF-04A09",
        "a relative 'from . import litellm' is a genuinely different, unresolved "
        "import shape -- never matched as the real litellm package",
        evidence.entrypoints == (),
    )


def test_entrypoint_function_name_captured_for_nested_function() -> None:
    content = (
        b"import litellm\n\ndef outer():\n    def inner_handler():\n"
        b'        litellm.completion(model="gpt-4o")\n    return inner_handler\n'
    )
    evidence = run_python_agent_config_tool_discovery([_ref("src/agent.py", content)])
    check(
        "FTF-04A09",
        "the immediately-enclosing function, not the outer one, is recorded as "
        "the entrypoint's own function_name",
        evidence.entrypoints[0].function_name == "inner_handler",
    )


def test_module_level_call_uses_module_scope_name() -> None:
    content = b'import litellm\n\nlitellm.completion(model="gpt-4o")\n'
    evidence = run_python_agent_config_tool_discovery([_ref("src/agent.py", content)])
    check(
        "FTF-04A09",
        "a module-level (not-inside-any-function) call is disclosed as '<module>', "
        "never a fabricated function name",
        evidence.entrypoints[0].function_name == "<module>",
    )


def test_two_calls_in_two_functions_produce_two_candidates() -> None:
    content = (
        b"import litellm\n\n"
        b"def handle_support(selected_model):\n    litellm.completion(model=selected_model)\n\n"
        b'def handle_triage():\n    litellm.completion(model="triage-model")\n'
    )
    evidence = run_python_agent_config_tool_discovery([_ref("src/agent.py", content)])
    check(
        "FTF-04A09",
        "two qualifying calls in two different functions of the same file yield "
        "two distinct candidates, never coalesced into one",
        len(evidence.entrypoints) == 2
        and {c.function_name for c in evidence.entrypoints} == {"handle_support", "handle_triage"},
    )


def test_module_never_imports_or_executes_scanned_content() -> None:
    full_source_text = Path(
        API_ROOT / "app" / "target_connectors" / "python_agent_config_tool_discovery.py"
    ).read_text("utf-8")
    _, _, source_text = full_source_text.partition('"""')
    _, _, source_text = source_text.partition('"""')
    # `ast.literal_eval(` is a pure, non-executing literal evaluator -- the
    # opposite of the builtin `eval(...)` this check guards against -- and its
    # own name contains the substring "eval(" incidentally.
    source_text_without_safe_calls = source_text.replace("ast.literal_eval(", "")
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
        "FTF-04A09",
        "Section 7A.4's own 'without importing Python modules... running... "
        "build[s]' -- none of these code-execution primitives appear anywhere in "
        "this worker's own source",
        not any(token in source_text_without_safe_calls for token in forbidden_substrings),
    )

    # Behavioral half: this worker must read every scanned .py file's bytes to
    # parse it (unlike FTF-04A08's manifest-only reads) -- reading is safe;
    # the real claim under test is that the file's own statements are never
    # *executed*. A real filesystem side effect, if the parsed content were
    # ever run, is the only way to prove that from outside `ast.parse` itself.
    sentinel_path = Path(f"/tmp/ftf_04a09_execution_sentinel_{id(check)}")
    if sentinel_path.exists():
        sentinel_path.unlink()
    malicious_content = (
        f'import pathlib\npathlib.Path("{sentinel_path}").write_text("executed")\n'
    ).encode()
    malicious_py = _ref("src/malicious.py", malicious_content)
    try:
        run_python_agent_config_tool_discovery([malicious_py])
        check(
            "FTF-04A09",
            "a scanned .py file's own content is parsed (ast.parse), never "
            "imported or executed, so a real filesystem side effect never occurs",
            not sentinel_path.exists(),
        )
    finally:
        if sentinel_path.exists():
            sentinel_path.unlink()


def test_malformed_python_file_does_not_raise() -> None:
    evidence = run_python_agent_config_tool_discovery(
        [_ref("src/broken.py", b"def broken(:\n    pass\n")]
    )
    check(
        "FTF-04A09",
        "a real SyntaxError in a scanned file does not raise out of this worker -- "
        "caught and counted, never crashes the run",
        evidence.parse_errors == 1 and evidence.entrypoints == (),
    )


def test_non_python_file_is_skipped() -> None:
    content = b'import litellm\nlitellm.completion(model="x")\n'
    evidence = run_python_agent_config_tool_discovery([_ref("src/agent.ts", content)])
    check(
        "FTF-04A09",
        "a non-.py file is never parsed by this Python-only worker",
        evidence.entrypoints == () and evidence.files_scanned == 0,
    )


def test_oversized_file_is_skipped_not_read() -> None:
    read_calls: list[str] = []

    def _poison_read() -> bytes:
        read_calls.append("called")
        return b'import litellm\nlitellm.completion(model="x")\n'

    oversized = SourceFileRef(
        relative_path="src/agent.py", size=10_000_000, read_content=_poison_read
    )
    evidence = run_python_agent_config_tool_discovery(
        [oversized], resource_limits=ResourceLimits(max_file_bytes=1_000)
    )
    check(
        "FTF-04A09",
        "a file over max_file_bytes is never read at all (its own read_content is "
        "never invoked)",
        read_calls == [] and evidence.files_skipped_oversized == 1 and evidence.entrypoints == (),
    )


def test_total_byte_budget_truncates_run() -> None:
    content = b'import litellm\nlitellm.completion(model="x")\n'
    refs = [_ref(f"src/agent_{i}.py", content) for i in range(5)]
    evidence = run_python_agent_config_tool_discovery(
        refs, resource_limits=ResourceLimits(max_total_bytes=len(content))
    )
    check(
        "FTF-04A09",
        "once the cumulative byte budget is spent, further file content is never "
        "read and the run is disclosed as truncated",
        evidence.truncated is True and evidence.total_bytes_scanned <= len(content),
    )


def test_max_files_budget_truncates_run() -> None:
    content = b'import litellm\nlitellm.completion(model="x")\n'
    refs = [_ref(f"src/agent_{i}.py", content) for i in range(10)]
    evidence = run_python_agent_config_tool_discovery(
        refs, resource_limits=ResourceLimits(max_files=3)
    )
    check(
        "FTF-04A09",
        "the scanned-file count never exceeds max_files, and the run discloses "
        "truncated=True rather than silently reporting a complete scan",
        evidence.files_scanned == 3 and evidence.truncated is True,
    )


def test_negative_size_is_rejected() -> None:
    bad_ref = SourceFileRef(relative_path="src/agent.py", size=-1, read_content=lambda: b"")
    raised = False
    try:
        run_python_agent_config_tool_discovery([bad_ref])
    except PythonDiscoveryError:
        raised = True
    check(
        "FTF-04A09",
        "a structurally invalid (negative-size) SourceFileRef is rejected outright, "
        "not silently accepted",
        raised,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A09 Python deterministic agent/config/tool discovery: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A09 Python deterministic agent/config/tool discovery: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
