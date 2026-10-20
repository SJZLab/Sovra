#!/usr/bin/env python3
"""FTF-04A10 -- TypeScript/JavaScript deterministic agent/config/tool
discovery.

Runnable without any database and without any external network egress:
`run_typescript_javascript_agent_config_tool_discovery` is a pure,
synchronous function over a caller-supplied `SourceFileRef` sequence
(this task's own `database_changes` clause -- "No independent schema
change. Consume frozen contract."). It does spawn a local `node`
subprocess to run this repo's own already-installed `typescript`
devDependency -- no network egress of any kind.

This task's own acceptance clause ("Parse tested JS/TS AST patterns and
statically resolvable schemas using isolated approved parser") is covered
by `test_entrypoint_detected_from_default_import`,
`test_entrypoint_detected_from_named_import_with_alias`,
`test_entrypoint_detected_from_require`,
`test_model_binding_resolved_from_literal_string`,
`test_model_binding_argument_not_present`,
`test_tool_schema_resolved_from_literal_openai_function_list`,
`test_entrypoint_function_name_captured_for_nested_arrow_function`,
`test_module_level_call_uses_module_scope_name`,
`test_two_calls_in_two_functions_produce_two_candidates`, and
`test_tsx_file_is_parsed`.

This task's own negative_acceptance ("eval/dynamic imports/config
execution are not run; unresolved inferred contracts block auto
qualification") is covered by
`test_model_binding_unresolved_when_dynamic_expression`,
`test_tool_schema_unresolved_when_dynamic_expression`,
`test_tool_schema_unresolved_when_one_entry_is_not_openai_shaped`,
`test_unrelated_client_without_import_is_not_guessed`,
`test_namespace_import_is_not_matched`,
`test_module_never_imports_or_executes_scanned_content`,
`test_malformed_typescript_file_does_not_raise`,
`test_non_js_ts_file_is_skipped`,
`test_oversized_file_is_skipped_not_read`,
`test_total_byte_budget_truncates_run`,
`test_max_files_budget_truncates_run`, and
`test_negative_size_is_rejected`.

Run directly: python3 tests/tasks/test_ftf_04a10.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.target_connectors.source_analysis_worker import SourceFileRef  # noqa: E402
from app.target_connectors.typescript_javascript_deterministic_agent_config_tool_discovery import (  # noqa: E402
    ResourceLimits,
    TypeScriptJavaScriptDiscoveryError,
    run_typescript_javascript_agent_config_tool_discovery,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _ref(relative_path: str, content: bytes = b"") -> SourceFileRef:
    return SourceFileRef(
        relative_path=relative_path, size=len(content), read_content=lambda: content
    )


def test_entrypoint_detected_from_default_import() -> None:
    content = (
        b'import OpenAI from "openai";\n'
        b"const client = new OpenAI();\n"
        b"async function handleRequest() {\n"
        b'  await client.chat.completions.create({ model: "gpt-4o" });\n'
        b"}\n"
    )
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/agent.ts", content)]
    )
    check(
        "FTF-04A10",
        "a real client.chat.completions.create(...) call after a default "
        "'import OpenAI from \"openai\"' is detected as one entrypoint candidate",
        len(evidence.entrypoints) == 1
        and evidence.entrypoints[0].call_attribute == "client.chat.completions.create",
    )


def test_entrypoint_detected_from_named_import_with_alias() -> None:
    content = (
        b'import { OpenAI as LLM } from "openai";\n'
        b"const client = new LLM();\n"
        b'client.chat.completions.create({ model: "gpt-4o" });\n'
    )
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/agent.ts", content)]
    )
    check(
        "FTF-04A10",
        "'import { OpenAI as LLM } from \"openai\"' followed by 'new LLM()' is "
        "detected via the real named-import alias, not merely the literal name "
        "'OpenAI'",
        len(evidence.entrypoints) == 1,
    )


def test_entrypoint_detected_from_require() -> None:
    content = (
        b'const OpenAI = require("openai");\n'
        b"const client = new OpenAI();\n"
        b'client.chat.completions.create({ model: "gpt-4o" });\n'
    )
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/agent.js", content)]
    )
    check(
        "FTF-04A10",
        "a CommonJS 'const OpenAI = require(\"openai\")' followed by 'new "
        "OpenAI()' is detected via the real require alias",
        len(evidence.entrypoints) == 1,
    )


def test_model_binding_resolved_from_literal_string() -> None:
    content = (
        b'import OpenAI from "openai";\n'
        b"const client = new OpenAI();\n"
        b'client.chat.completions.create({ model: "support-agent-prod" });\n'
    )
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/agent.ts", content)]
    )
    binding = evidence.entrypoints[0].model_binding
    check(
        "FTF-04A10",
        "a literal string model property resolves to the real model alias",
        binding.resolved is True
        and binding.model_alias == "support-agent-prod"
        and binding.reason == "literal_string",
    )


def test_model_binding_argument_not_present() -> None:
    content = (
        b'import OpenAI from "openai";\n'
        b"const client = new OpenAI();\n"
        b"client.chat.completions.create({ messages: [] });\n"
    )
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/agent.ts", content)]
    )
    binding = evidence.entrypoints[0].model_binding
    check(
        "FTF-04A10",
        "a call with no model property is honestly 'argument_not_present', "
        "never guessed from another property",
        binding.resolved is False and binding.reason == "argument_not_present",
    )


def test_model_binding_unresolved_when_dynamic_expression() -> None:
    content = (
        b'import OpenAI from "openai";\n'
        b"const client = new OpenAI();\n"
        b"function handleRequest(selectedModel) {\n"
        b"  client.chat.completions.create({ model: selectedModel });\n"
        b"}\n"
    )
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/agent.ts", content)]
    )
    binding = evidence.entrypoints[0].model_binding
    check(
        "FTF-04A10",
        "Section 7A.4's own 'Dynamic/unresolved constructs remain unknown' -- a "
        "variable model property is never guessed",
        binding.resolved is False and binding.reason == "unresolved_dynamic_expression",
    )


def test_tool_schema_resolved_from_literal_openai_function_list() -> None:
    content = (
        b'import OpenAI from "openai";\n'
        b"const client = new OpenAI();\n"
        b"client.chat.completions.create({\n"
        b'  model: "gpt-4o",\n'
        b'  tools: [{ type: "function", function: { name: "lookup_order" } }],\n'
        b"});\n"
    )
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/agent.ts", content)]
    )
    tools = evidence.entrypoints[0].tool_schema
    check(
        "FTF-04A10",
        "a literal, OpenAI-function-shaped tools array resolves the real tool name",
        tools.resolved is True
        and tools.tool_names == ("lookup_order",)
        and tools.reason == "literal_openai_function_list",
    )


def test_tool_schema_unresolved_when_dynamic_expression() -> None:
    content = (
        b'import OpenAI from "openai";\n'
        b"const client = new OpenAI();\n"
        b"function handleRequest(registeredTools) {\n"
        b'  client.chat.completions.create({ model: "gpt-4o", tools: registeredTools });\n'
        b"}\n"
    )
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/agent.ts", content)]
    )
    tools = evidence.entrypoints[0].tool_schema
    check(
        "FTF-04A10",
        "a variable tools argument is never guessed -- honestly unresolved",
        tools.resolved is False and tools.reason == "unresolved_dynamic_expression",
    )


def test_tool_schema_unresolved_when_one_entry_is_not_openai_shaped() -> None:
    content = (
        b'import OpenAI from "openai";\n'
        b"const client = new OpenAI();\n"
        b"client.chat.completions.create({\n"
        b'  model: "gpt-4o",\n'
        b'  tools: [{ type: "function", function: { name: "a" } }, { custom: "shape" }],\n'
        b"});\n"
    )
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/agent.ts", content)]
    )
    tools = evidence.entrypoints[0].tool_schema
    check(
        "FTF-04A10",
        "one non-conforming array entry marks the entire extraction unresolved "
        "-- a partial extraction is never reported as if it were complete",
        tools.resolved is False and tools.tool_names == (),
    )


def test_unrelated_client_without_import_is_not_guessed() -> None:
    content = (
        b"class OpenAI {}\n"
        b"const client = new OpenAI();\n"
        b'client.chat.completions.create({ model: "x" });\n'
    )
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/agent.ts", content)]
    )
    check(
        "FTF-04A10",
        "a locally-defined class merely named 'OpenAI', with no real "
        "'openai' import in the file, is never matched -- unknown/custom is "
        "never guessed",
        evidence.entrypoints == (),
    )


def test_namespace_import_is_not_matched() -> None:
    content = (
        b'import * as openaiNs from "openai";\n'
        b"const client = new openaiNs.OpenAI();\n"
        b'client.chat.completions.create({ model: "x" });\n'
    )
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/agent.ts", content)]
    )
    check(
        "FTF-04A10",
        "a namespace import ('import * as ns from \"openai\"') is a genuinely "
        "different, unresolved import shape -- never matched as one of the "
        "three closed shapes this worker recognizes",
        evidence.entrypoints == (),
    )


def test_entrypoint_function_name_captured_for_nested_arrow_function() -> None:
    content = (
        b'import OpenAI from "openai";\n'
        b"const client = new OpenAI();\n"
        b"function outer() {\n"
        b"  const innerHandler = async () => {\n"
        b'    await client.chat.completions.create({ model: "gpt-4o" });\n'
        b"  };\n"
        b"  return innerHandler;\n"
        b"}\n"
    )
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/agent.ts", content)]
    )
    check(
        "FTF-04A10",
        "the immediately-enclosing arrow function, named via its own "
        "variable declarator, is recorded as the entrypoint's own "
        "function_name -- not the outer function",
        evidence.entrypoints[0].function_name == "innerHandler",
    )


def test_module_level_call_uses_module_scope_name() -> None:
    content = (
        b'import OpenAI from "openai";\n'
        b"const client = new OpenAI();\n"
        b'client.chat.completions.create({ model: "gpt-4o" });\n'
    )
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/agent.ts", content)]
    )
    check(
        "FTF-04A10",
        "a module-level (not-inside-any-function) call is disclosed as "
        "'<module>', never a fabricated function name",
        evidence.entrypoints[0].function_name == "<module>",
    )


def test_two_calls_in_two_functions_produce_two_candidates() -> None:
    content = (
        b'import OpenAI from "openai";\n'
        b"const client = new OpenAI();\n"
        b"function handleSupport(selectedModel) {\n"
        b"  client.chat.completions.create({ model: selectedModel });\n"
        b"}\n"
        b"function handleTriage() {\n"
        b'  client.chat.completions.create({ model: "triage-model" });\n'
        b"}\n"
    )
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/agent.ts", content)]
    )
    check(
        "FTF-04A10",
        "two qualifying calls in two different functions of the same file "
        "yield two distinct candidates, never coalesced into one",
        len(evidence.entrypoints) == 2
        and {c.function_name for c in evidence.entrypoints} == {"handleSupport", "handleTriage"},
    )


def test_tsx_file_is_parsed() -> None:
    content = (
        b'import OpenAI from "openai";\n'
        b"const client = new OpenAI();\n"
        b"export function Widget() {\n"
        b'  client.chat.completions.create({ model: "gpt-4o" });\n'
        b"  return <div>hi</div>;\n"
        b"}\n"
    )
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/widget.tsx", content)]
    )
    check(
        "FTF-04A10",
        "a real .tsx file (JSX syntax included) is parsed with the correct "
        "script kind, not skipped or misparsed",
        evidence.parse_errors == 0 and len(evidence.entrypoints) == 1,
    )


def test_module_never_imports_or_executes_scanned_content() -> None:
    helper_source = (
        Path(__file__).resolve().parent.parent.parent
        / "apps"
        / "api"
        / "app"
        / "target_connectors"
        / "_typescript_javascript_ast_helper.cjs"
    ).read_text("utf-8")
    forbidden_substrings = (
        "eval(",
        "new Function(",
        "vm.runInContext",
        "vm.runInNewContext",
        "child_process",
        "fs.writeFile",
        "fs.readFile(",
        "fs.readFileSync(",
    )
    check(
        "FTF-04A10",
        "Section 7A.4's own 'without importing Python modules... executing "
        "JavaScript... build[s]' -- none of these code-execution/filesystem "
        "primitives appear anywhere in this worker's own Node.js helper source",
        not any(token in helper_source for token in forbidden_substrings),
    )

    # Behavioral half: a scanned .js file's own content, if it were ever
    # imported/executed/`require`d by the helper, would run and could
    # produce a real filesystem side effect. Proving the negative from
    # outside the sandboxed subprocess directly: a real side-effect file
    # never appears after a "malicious" scanned file is analyzed.
    sentinel_path = Path(f"/tmp/ftf_04a10_execution_sentinel_{id(check)}")
    if sentinel_path.exists():
        sentinel_path.unlink()
    malicious_content = (
        f'require("fs").writeFileSync("{sentinel_path}", "executed");\n'
        f"module.exports.client = {{ chat: {{ completions: {{ create: () => {{}} }} }} }};\n"
    ).encode()
    malicious_js = _ref("src/malicious.js", malicious_content)
    try:
        run_typescript_javascript_agent_config_tool_discovery([malicious_js])
        check(
            "FTF-04A10",
            "a scanned .js file's own content is parsed (ts.createSourceFile), "
            "never imported/required/executed, so a real filesystem side "
            "effect never occurs",
            not sentinel_path.exists(),
        )
    finally:
        if sentinel_path.exists():
            sentinel_path.unlink()


def test_malformed_typescript_file_does_not_raise() -> None:
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/broken.ts", b"function broken( {\n")]
    )
    check(
        "FTF-04A10",
        "a real syntactic diagnostic in a scanned file does not raise out of "
        "this worker -- caught and counted, never crashes the run",
        evidence.parse_errors == 1 and evidence.entrypoints == (),
    )


def test_non_js_ts_file_is_skipped() -> None:
    content = b'import litellm\nlitellm.completion(model="x")\n'
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [_ref("src/agent.py", content)]
    )
    check(
        "FTF-04A10",
        "a non-JS/TS file is never parsed by this JS/TS-only worker",
        evidence.entrypoints == () and evidence.files_scanned == 0,
    )


def test_oversized_file_is_skipped_not_read() -> None:
    read_calls: list[str] = []

    def _poison_read() -> bytes:
        read_calls.append("called")
        return b'import OpenAI from "openai";\nnew OpenAI();\n'

    oversized = SourceFileRef(
        relative_path="src/agent.ts", size=10_000_000, read_content=_poison_read
    )
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        [oversized], resource_limits=ResourceLimits(max_file_bytes=1_000)
    )
    check(
        "FTF-04A10",
        "a file over max_file_bytes is never read at all (its own read_content is never invoked)",
        read_calls == [] and evidence.files_skipped_oversized == 1 and evidence.entrypoints == (),
    )


def test_total_byte_budget_truncates_run() -> None:
    content = b'import OpenAI from "openai";\nconst c = new OpenAI();\n'
    refs = [_ref(f"src/agent_{i}.ts", content) for i in range(5)]
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        refs, resource_limits=ResourceLimits(max_total_bytes=len(content))
    )
    check(
        "FTF-04A10",
        "once the cumulative byte budget is spent, further file content is "
        "never read and the run is disclosed as truncated",
        evidence.truncated is True and evidence.total_bytes_scanned <= len(content),
    )


def test_max_files_budget_truncates_run() -> None:
    content = b'import OpenAI from "openai";\nconst c = new OpenAI();\n'
    refs = [_ref(f"src/agent_{i}.ts", content) for i in range(10)]
    evidence = run_typescript_javascript_agent_config_tool_discovery(
        refs, resource_limits=ResourceLimits(max_files=3)
    )
    check(
        "FTF-04A10",
        "the scanned-file count never exceeds max_files, and the run "
        "discloses truncated=True rather than silently reporting a complete "
        "scan",
        evidence.files_scanned == 3 and evidence.truncated is True,
    )


def test_negative_size_is_rejected() -> None:
    bad_ref = SourceFileRef(relative_path="src/agent.ts", size=-1, read_content=lambda: b"")
    raised = False
    try:
        run_typescript_javascript_agent_config_tool_discovery([bad_ref])
    except TypeScriptJavaScriptDiscoveryError:
        raised = True
    check(
        "FTF-04A10",
        "a structurally invalid (negative-size) SourceFileRef is rejected "
        "outright, not silently accepted",
        raised,
    )


def test_node_runtime_is_genuinely_available_in_this_environment() -> None:
    # This is an environment precondition check, not a fault-injection test
    # -- confirms the "node not found" path in `_run_helper` is a real,
    # reachable branch rather than dead code, without mocking `shutil.which`.
    result = subprocess.run(["node", "--version"], capture_output=True, text=True, check=False)
    check(
        "FTF-04A10",
        "the Node.js runtime this worker depends on is genuinely present in this test environment",
        result.returncode == 0,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(
            f"FTF-04A10 TypeScript/JavaScript deterministic agent/config/tool "
            f"discovery: {len(FAILURES)} failed"
        )
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print(
        "FTF-04A10 TypeScript/JavaScript deterministic agent/config/tool "
        "discovery: all checks passed"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
