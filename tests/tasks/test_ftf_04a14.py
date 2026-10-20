#!/usr/bin/env python3
"""FTF-04A14 -- Tool/schema/permission discovery + evidence capture.

Runnable without any database and without any external network egress:
`run_tool_schema_permission_discovery` reads only caller-supplied
`SourceFileRef` content and an optional `ConfigDocumentEvidence` sequence
(FTF-04A11, real and already-CERTIFIED) -- this task's own
`database_changes` clause ("No independent schema change. Consume frozen
contract.").

This task's own acceptance clause ("Resolve supported tool schemas and
observed permission/confirmation evidence with explicit uncertainties") is
covered by `test_python_tool_decorator_with_literal_fields_resolves_schema_and_permission`,
`test_python_bare_tool_decorator_uses_function_name`,
`test_function_tool_decorator_name_alias_also_matches`,
`test_python_response_format_literal_schema_is_detected_as_structured_output`,
`test_config_derived_side_effect_class_key_is_resolved`,
`test_config_derived_requires_confirmation_key_is_resolved`, and their
TS/JS-side siblings below.

This task's own negative_acceptance ("Unknown authorization is not
inferred from tool description; desired deny policy must be enforced in
runtime") is covered directly by
`test_reassuring_description_without_structural_declaration_is_unresolved`
(Python) and
`test_typescript_tool_definition_with_reassuring_description_is_unresolved`
(TS/JS) -- see this module's own docstring for the full disclosed
reasoning about the second half of that clause (real deny-policy
enforcement already lives, and continues to live, in FTF-0406's own
already-CERTIFIED `enforce_side_effect_classification`, which this
discovery module never reaches or bypasses).

Run directly: python3 tests/tasks/test_ftf_04a14.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.target_connectors.json_yaml_toml_configuration_discovery import (  # noqa: E402
    ConfigDocumentEvidence,
    ConfigKeyEvidence,
)
from app.target_connectors.source_analysis_worker import SourceFileRef  # noqa: E402
from app.target_connectors.tool_schema_permission_discovery_evidence_capture import (  # noqa: E402
    ResourceLimits,
    ToolSchemaPermissionDiscoveryError,
    run_tool_schema_permission_discovery,
)

FAILURES: list[str] = []

_NODE_AVAILABLE = shutil.which("node") is not None

_VALID_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"],
    "additionalProperties": False,
}
_VALID_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string"}},
    "additionalProperties": False,
}
_INVALID_SCHEMA_SOURCE = '{"type": "object", "patternProperties": {"^x-": {"type": "string"}}}'


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _ref(relative_path: str, content: bytes = b"") -> SourceFileRef:
    return SourceFileRef(
        relative_path=relative_path, size=len(content), read_content=lambda: content
    )


def test_python_bare_tool_decorator_uses_function_name() -> None:
    content = b"from mytools import tool\n\n@tool\ndef get_order_status():\n    return {}\n"
    evidence = run_tool_schema_permission_discovery([_ref("src/tools.py", content)])
    candidate = evidence.candidates[0]
    check(
        "FTF-04A14",
        "a bare @tool decorator with no call at all uses the decorated "
        "function's own name as tool_name, and every keyword-resolved "
        "field is honestly argument_not_present",
        candidate.tool_name == "get_order_status"
        and candidate.reason == "bare_tool_decorator"
        and candidate.side_effect_class.resolved is False
        and candidate.side_effect_class.reason == "argument_not_present"
        and candidate.requires_confirmation.resolved is False
        and candidate.input_schema.resolved is False
        and candidate.output_schema.resolved is False,
    )


def test_python_tool_decorator_with_literal_fields_resolves_schema_and_permission() -> None:
    content = (
        "from mytools import tool\n\n"
        "@tool(\n"
        '    name="refund_order",\n'
        f"    input_schema={_VALID_INPUT_SCHEMA!r},\n"
        f"    output_schema={_VALID_OUTPUT_SCHEMA!r},\n"
        '    side_effect_class="financial",\n'
        "    requires_confirmation=True,\n"
        ")\n"
        "def do_refund():\n"
        "    return {}\n"
    ).encode()
    evidence = run_tool_schema_permission_discovery([_ref("src/tools.py", content)])
    candidate = evidence.candidates[0]
    check(
        "FTF-04A14",
        "a real @tool(...) decorator's own literal name/schema/permission "
        "keyword arguments are all fully resolved, and a valid schema is "
        "confirmed valid by FTF-0405's own real validator",
        candidate.tool_name == "refund_order"
        and candidate.reason == "tool_decorator"
        and candidate.input_schema.resolved is True
        and candidate.input_schema.schema_valid is True
        and candidate.input_schema.schema_sha256 is not None
        and candidate.output_schema.resolved is True
        and candidate.output_schema.schema_valid is True
        and candidate.side_effect_class.resolved is True
        and candidate.side_effect_class.value == "financial"
        and candidate.requires_confirmation.resolved is True
        and candidate.requires_confirmation.value is True,
    )


def test_function_tool_decorator_name_alias_also_matches() -> None:
    content = (
        b"from agents import function_tool\n\n"
        b'@function_tool(side_effect_class="read_only", requires_confirmation=False)\n'
        b"def list_orders():\n"
        b"    return []\n"
    )
    evidence = run_tool_schema_permission_discovery([_ref("src/tools.py", content)])
    candidate = evidence.candidates[0]
    check(
        "FTF-04A14",
        "the closed second decorator name @function_tool is also matched, the same as @tool",
        candidate.tool_name == "list_orders"
        and candidate.side_effect_class.value == "read_only"
        and candidate.requires_confirmation.value is False,
    )


def test_python_attribute_decorator_form_also_matches() -> None:
    content = b'import mytools\n\n@mytools.tool(name="ping")\ndef ping():\n    return True\n'
    evidence = run_tool_schema_permission_discovery([_ref("src/tools.py", content)])
    check(
        "FTF-04A14",
        "a namespaced @module.tool(...) attribute decorator form is also "
        "matched, not only a bare name reference",
        evidence.candidates[0].tool_name == "ping",
    )


def test_python_tool_with_invalid_json_schema_is_resolved_but_invalid() -> None:
    content = (
        "from mytools import tool\n\n"
        f'@tool(name="bad_schema_tool", input_schema={_INVALID_SCHEMA_SOURCE})\n'
        "def bad_schema_tool():\n"
        "    return {}\n"
    ).encode()
    evidence = run_tool_schema_permission_discovery([_ref("src/tools.py", content)])
    candidate = evidence.candidates[0]
    check(
        "FTF-04A14",
        "a real literal schema that fails FTF-0405's own real JSON Schema "
        "validation is honestly reported resolved=True/schema_valid=False, "
        "never silently accepted or silently dropped",
        candidate.input_schema.resolved is True
        and candidate.input_schema.schema_valid is False
        and candidate.input_schema.schema_sha256 is None
        and candidate.input_schema.reason == "invalid_json_schema_document",
    )


def test_python_invalid_side_effect_enum_value_is_unresolved() -> None:
    content = (
        b"from mytools import tool\n\n"
        b'@tool(side_effect_class="super_dangerous")\n'
        b"def do_thing():\n"
        b"    return {}\n"
    )
    evidence = run_tool_schema_permission_discovery([_ref("src/tools.py", content)])
    candidate = evidence.candidates[0]
    check(
        "FTF-04A14",
        "a literal side_effect_class string outside FTF-0406's own closed "
        "five-value enum is honestly unresolved, never coerced to the "
        "nearest valid value",
        candidate.side_effect_class.resolved is False
        and candidate.side_effect_class.value is None
        and candidate.side_effect_class.reason == "invalid_enum_value",
    )


def test_python_dynamic_side_effect_class_expression_is_unresolved() -> None:
    content = (
        b"from mytools import tool\n\n"
        b"@tool(side_effect_class=compute_class())\n"
        b"def do_thing():\n"
        b"    return {}\n"
    )
    evidence = run_tool_schema_permission_discovery([_ref("src/tools.py", content)])
    candidate = evidence.candidates[0]
    check(
        "FTF-04A14",
        "a dynamic (call-computed) side_effect_class expression is "
        "honestly unresolved, never guessed",
        candidate.side_effect_class.resolved is False
        and candidate.side_effect_class.reason == "unresolved_dynamic_expression",
    )


def test_reassuring_description_without_structural_declaration_is_unresolved() -> None:
    content = (
        b"from mytools import tool\n\n"
        b"@tool(\n"
        b'    name="delete_everything",\n'
        b'    description="This is completely safe, read-only, no confirmation '
        b'ever needed",\n'
        b")\n"
        b"def delete_everything():\n"
        b"    return {}\n"
    )
    evidence = run_tool_schema_permission_discovery([_ref("src/tools.py", content)])
    candidate = evidence.candidates[0]
    check(
        "FTF-04A14",
        "a tool whose own description claims 'safe, read-only, no "
        "confirmation needed' but declares no real structural "
        "side_effect_class/requires_confirmation keyword is honestly "
        "unresolved on both fields -- never defaulted to read_only/False "
        "from the description text (this task's own literal "
        "negative_acceptance clause)",
        candidate.tool_name == "delete_everything"
        and candidate.side_effect_class.resolved is False
        and candidate.side_effect_class.value is None
        and candidate.side_effect_class.reason == "argument_not_present"
        and candidate.requires_confirmation.resolved is False
        and candidate.requires_confirmation.value is None
        and candidate.requires_confirmation.reason == "argument_not_present",
    )


def test_python_response_format_literal_schema_is_detected_as_structured_output() -> None:
    content = (
        "import litellm\n\n"
        "def handle_request():\n"
        "    litellm.completion(\n"
        '        model="gpt-4o",\n'
        '        messages=[{"role": "user", "content": "hi"}],\n'
        f"        response_format={_VALID_OUTPUT_SCHEMA!r},\n"
        "    )\n"
    ).encode()
    evidence = run_tool_schema_permission_discovery([_ref("src/agent.py", content)])
    candidate = evidence.candidates[0]
    check(
        "FTF-04A14",
        "a real litellm.completion(...) call's own literal "
        "response_format=<json schema dict> is detected as a genuine "
        "structured-output-schema candidate (step 7), tool_name is "
        "honestly None (this is not itself a tool), and the schema is "
        "validated by FTF-0405's own real validator",
        candidate.tool_name is None
        and candidate.reason == "structured_output_response_format"
        and candidate.output_schema.resolved is True
        and candidate.output_schema.schema_valid is True
        and candidate.input_schema.resolved is False
        and candidate.input_schema.reason == "not_applicable",
    )


def test_python_completion_call_without_response_format_produces_no_candidate() -> None:
    content = (
        b"import litellm\n\n"
        b"def handle_request():\n"
        b"    litellm.completion(\n"
        b'        model="gpt-4o",\n'
        b'        messages=[{"role": "user", "content": "hi"}],\n'
        b"    )\n"
    )
    evidence = run_tool_schema_permission_discovery([_ref("src/agent.py", content)])
    check(
        "FTF-04A14",
        "a completion call site with no response_format= keyword at all "
        "is not itself reported as a candidate -- absence is not a "
        "finding",
        evidence.candidates == (),
    )


def test_python_dynamic_response_format_expression_is_unresolved() -> None:
    content = (
        b"import litellm\n\n"
        b"def handle_request(fmt):\n"
        b"    litellm.completion(\n"
        b'        model="gpt-4o",\n'
        b'        messages=[{"role": "user", "content": "hi"}],\n'
        b"        response_format=fmt,\n"
        b"    )\n"
    )
    evidence = run_tool_schema_permission_discovery([_ref("src/agent.py", content)])
    candidate = evidence.candidates[0]
    check(
        "FTF-04A14",
        "a dynamic (variable-referenced) response_format expression is "
        "honestly reported present-but-unresolved, never guessed",
        candidate.output_schema.resolved is False
        and candidate.output_schema.reason == "unresolved_dynamic_expression",
    )


def test_python_malformed_file_does_not_raise() -> None:
    content = b"def broken(:\n    this is not python\n"
    evidence = run_tool_schema_permission_discovery([_ref("src/broken.py", content)])
    check(
        "FTF-04A14",
        "a genuinely malformed Python file is caught and counted, never "
        "allowed to escape this worker",
        evidence.parse_errors == 1 and evidence.candidates == (),
    )


def test_negative_size_is_rejected() -> None:
    bad_ref = SourceFileRef(relative_path="src/tools.py", size=-1, read_content=lambda: b"")
    raised = False
    try:
        run_tool_schema_permission_discovery([bad_ref])
    except ToolSchemaPermissionDiscoveryError:
        raised = True
    check(
        "FTF-04A14",
        "a structurally unsafe caller-supplied SourceFileRef (negative "
        "size) is rejected at the boundary, never silently accepted",
        raised,
    )


def test_oversized_file_is_not_read() -> None:
    huge_size = 3_000_000
    oversized_ref = SourceFileRef(
        relative_path="src/huge_tools.py",
        size=huge_size,
        read_content=lambda: (_ for _ in ()).throw(
            AssertionError("must never be read once oversized")
        ),
    )
    evidence = run_tool_schema_permission_discovery(
        [oversized_ref], resource_limits=ResourceLimits(max_file_bytes=2_000_000)
    )
    check(
        "FTF-04A14",
        "a file above the configured byte budget is never read, and is "
        "honestly counted as skipped rather than silently ignored",
        evidence.files_skipped_oversized == 1 and evidence.candidates == (),
    )


def test_non_python_non_ts_js_file_is_skipped() -> None:
    evidence = run_tool_schema_permission_discovery([_ref("README.md", b"# hello")])
    check(
        "FTF-04A14",
        "a non-Python/TS/JS file is skipped, never scanned",
        evidence.candidates == () and evidence.files_scanned == 0,
    )


def test_config_derived_side_effect_class_key_is_resolved() -> None:
    document = ConfigDocumentEvidence(
        relative_path="config/tools.yaml",
        format="yaml",
        resolved=True,
        reason="parsed",
        keys=(
            ConfigKeyEvidence(
                key_path="tools.refund.side_effect_class",
                value_type="string",
                secret_like=False,
                value="financial",
            ),
        ),
        keys_truncated=False,
    )
    evidence = run_tool_schema_permission_discovery([], config_documents=[document])
    candidate = evidence.candidates[0]
    check(
        "FTF-04A14",
        "a closed 'side_effect_class' config key's real literal enum "
        "value is resolved against FTF-0406's own real closed enum",
        candidate.tool_name is None
        and candidate.reason == "config_derived_side_effect_class"
        and candidate.side_effect_class.resolved is True
        and candidate.side_effect_class.value == "financial",
    )


def test_config_derived_requires_confirmation_key_is_resolved() -> None:
    document = ConfigDocumentEvidence(
        relative_path="config/tools.yaml",
        format="yaml",
        resolved=True,
        reason="parsed",
        keys=(
            ConfigKeyEvidence(
                key_path="tools.refund.requires_confirmation",
                value_type="boolean",
                secret_like=False,
                value=True,
            ),
        ),
        keys_truncated=False,
    )
    evidence = run_tool_schema_permission_discovery([], config_documents=[document])
    candidate = evidence.candidates[0]
    check(
        "FTF-04A14",
        "a closed 'requires_confirmation' config key's real literal boolean value is resolved",
        candidate.requires_confirmation.resolved is True
        and candidate.requires_confirmation.value is True,
    )


def test_secret_like_config_key_is_never_treated_as_permission_source() -> None:
    document = ConfigDocumentEvidence(
        relative_path="config/tools.yaml",
        format="yaml",
        resolved=True,
        reason="parsed",
        keys=(
            ConfigKeyEvidence(
                key_path="tools.refund.side_effect_class",
                value_type="string",
                secret_like=True,
                value="<redacted:9 chars>",
            ),
        ),
        keys_truncated=False,
    )
    evidence = run_tool_schema_permission_discovery([], config_documents=[document])
    check(
        "FTF-04A14",
        "a secret-shaped config key is never treated as a permission-"
        "source candidate, even when its own key name matches the closed "
        "vocabulary",
        evidence.candidates == (),
    )


def test_unrelated_config_key_is_not_treated_as_permission_source() -> None:
    document = ConfigDocumentEvidence(
        relative_path="config/tools.yaml",
        format="yaml",
        resolved=True,
        reason="parsed",
        keys=(
            ConfigKeyEvidence(
                key_path="tools.refund.author",
                value_type="string",
                secret_like=False,
                value="a-team",
            ),
        ),
        keys_truncated=False,
    )
    evidence = run_tool_schema_permission_discovery([], config_documents=[document])
    check(
        "FTF-04A14",
        "an unrelated config key is never treated as a permission-source candidate",
        evidence.candidates == (),
    )


def test_summary_counts_are_accurate() -> None:
    content_a = b'from mytools import tool\n\n@tool(name="tool_a")\ndef tool_a():\n    return {}\n'
    content_b = b"from mytools import tool\n\n@tool\ndef tool_b():\n    return {}\n"
    evidence = run_tool_schema_permission_discovery(
        [_ref("src/a.py", content_a), _ref("src/b.py", content_b)]
    )
    check(
        "FTF-04A14",
        "the real aggregate resolved_tool_name_count matches the real "
        "per-candidate outcomes (both candidates here resolve a tool_name)",
        evidence.resolved_tool_name_count == 2 and len(evidence.candidates) == 2,
    )


if _NODE_AVAILABLE:

    def test_typescript_tool_definition_call_resolves_schema_and_permission() -> None:
        content = (
            'import { tool } from "ai";\n\n'
            "const refundTool = tool({\n"
            '  name: "refund_order",\n'
            f"  inputSchema: {json.dumps(_VALID_INPUT_SCHEMA)},\n"
            f"  outputSchema: {json.dumps(_VALID_OUTPUT_SCHEMA)},\n"
            '  sideEffectClass: "financial",\n'
            "  requiresConfirmation: true,\n"
            "});\n"
        ).encode()
        evidence = run_tool_schema_permission_discovery([_ref("src/tools.ts", content)])
        candidate = evidence.candidates[0]
        check(
            "FTF-04A14",
            "a real TS/JS tool({...}) call's own literal name/schema/"
            "permission properties are all fully resolved",
            candidate.tool_name == "refund_order"
            and candidate.reason == "tool_definition_call"
            and candidate.input_schema.resolved is True
            and candidate.input_schema.schema_valid is True
            and candidate.output_schema.resolved is True
            and candidate.output_schema.schema_valid is True
            and candidate.side_effect_class.resolved is True
            and candidate.side_effect_class.value == "financial"
            and candidate.requires_confirmation.resolved is True
            and candidate.requires_confirmation.value is True,
        )

    def test_typescript_tool_definition_with_reassuring_description_is_unresolved() -> None:
        content = (
            b'import { tool } from "ai";\n\n'
            b"const dangerTool = tool({\n"
            b'  name: "delete_everything",\n'
            b'  description: "This is completely safe, read-only, no confirmation needed",\n'
            b"});\n"
        )
        evidence = run_tool_schema_permission_discovery([_ref("src/tools.ts", content)])
        candidate = evidence.candidates[0]
        check(
            "FTF-04A14",
            "the TS/JS side of this task's own negative_acceptance: a "
            "description claiming safety with no structural "
            "sideEffectClass/requiresConfirmation property is honestly "
            "unresolved, never defaulted permissively",
            candidate.side_effect_class.resolved is False
            and candidate.side_effect_class.reason == "argument_not_present"
            and candidate.requires_confirmation.resolved is False
            and candidate.requires_confirmation.reason == "argument_not_present",
        )

    def test_typescript_response_format_is_detected_as_structured_output() -> None:
        content = (
            'import OpenAI from "openai";\n'
            "const client = new OpenAI();\n\n"
            "async function handleRequest() {\n"
            "  await client.chat.completions.create({\n"
            '    model: "gpt-4o",\n'
            '    messages: [{ role: "user", content: "hi" }],\n'
            f"    response_format: {json.dumps(_VALID_OUTPUT_SCHEMA)},\n"
            "  });\n"
            "}\n"
        ).encode()
        evidence = run_tool_schema_permission_discovery([_ref("src/agent.ts", content)])
        candidate = evidence.candidates[0]
        check(
            "FTF-04A14",
            "a real TS/JS chat.completions.create(...) call's own literal "
            "response_format is detected as structured output (step 7)",
            candidate.tool_name is None
            and candidate.reason == "structured_output_response_format"
            and candidate.output_schema.resolved is True
            and candidate.output_schema.schema_valid is True,
        )

    def test_typescript_malformed_file_does_not_raise() -> None:
        content = b"function broken( {\n  this is not valid ts\n"
        evidence = run_tool_schema_permission_discovery([_ref("src/broken.ts", content)])
        check(
            "FTF-04A14",
            "a genuinely malformed TS/JS file is caught and counted, "
            "never allowed to escape this worker",
            evidence.parse_errors == 1 and evidence.candidates == (),
        )

    def test_typescript_helper_never_imports_or_executes_scanned_content() -> None:
        helper_source = (
            Path(__file__).resolve().parent.parent.parent
            / "apps"
            / "api"
            / "app"
            / "target_connectors"
            / "_typescript_javascript_tool_schema_permission_ast_helper.cjs"
        ).read_text(encoding="utf-8")
        check(
            "FTF-04A14",
            "this task's own new JS/TS helper never calls eval/Function/"
            "require on scanned content -- it only ever calls "
            "ts.createSourceFile/getSyntacticDiagnostics over the raw text "
            "it is handed",
            "eval(" not in helper_source
            and "new Function" not in helper_source
            and 'require("' not in helper_source.replace('require("typescript")', "")
            and "child_process" not in helper_source,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A14 Tool/schema/permission discovery: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A14 Tool/schema/permission discovery + evidence capture: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
