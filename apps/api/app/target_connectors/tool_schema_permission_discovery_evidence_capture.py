"""Tool/schema/permission discovery + evidence capture (FTF-04A14, Section
7A.4/6.9/66/77/81).

Section 7A.4's own numbered "Analysis order" names step 6 ("Detect
tool/function schemas and adapters"), step 7 ("Detect structured output
schemas") and step 8 ("Detect authorization/confirmation boundaries
relevant to tools") as three distinct steps; this task owns exactly the
real slice of those three steps that FTF-04A09/FTF-04A10 do not already
own. Both of those modules' own docstrings already disclose, in their own
"deliberately does not detect" lists, that step 7 and step 8 are entirely
out of their scope, and that their own step-6 coverage is narrow: each
extracts only the `tools=`/`tools:` keyword argument's own literal
`{"type": "function", "function": {"name": ..., ...}}` list, on the exact
same LiteLLM/OpenAI completion call site their own module already
detects, and reports back only tool *names* -- never a tool's own
`input_schema_json`/`output_schema_json`/`side_effect_class`/
`requires_confirmation` (Section 6.9's own `ToolDefinition` fields). This
task does not re-extract that same `tools=` name list -- reaching into
FTF-04A09's/FTF-04A10's own already-CERTIFIED private functions, or
duplicating their own narrow extraction, would be exactly the kind of
"scatter domain logic into one file per ticket simply because a ticket
exists" Section 75.1 already warns against. Instead this task covers the
real, disjoint remainder those two modules never touch:

1. **Standalone tool/schema definitions (step 6's own remainder)** -- a
   real Python `@tool(...)`/`@function_tool(...)` decorator (or a bare
   `@tool`/`@function_tool` with no call at all) applied to a function
   definition, and the direct JS/TS analog, a bare `tool({...})`/
   `defineTool({...})` call -- a closed, disclosed, non-guessed vocabulary
   of decorator/function names chosen because they are the real, common
   convention across today's popular agent-tool-definition libraries
   (LangChain's `@tool`, the OpenAI Agents SDK's `@function_tool`, the
   Vercel AI SDK's `tool({...})`), the same "known, non-exhaustive
   heuristic" precedent FTF-04A09's own three `litellm` import shapes and
   FTF-04A11's own `_is_secret_like_key` already established, applied here
   to tool-definition call shapes instead. A resolved literal `name=`/
   `name:` argument becomes `tool_name`; absent it, the decorated
   function's own name is used (JS/TS has no decorator equivalent, so a
   bare `tool({...})` call with no `name` key is honestly `tool_name=None`
   there -- there is no enclosing "decorated function" to fall back to).

2. **Structured output schemas (step 7, entirely new)** -- the same
   `litellm.completion`/`acompletion` (Python) or
   `<client>.chat.completions.create` (TS/JS) call site FTF-04A09/
   FTF-04A10 already detect, but reading a field neither of those modules
   ever reads: `response_format=`/`response_format:`. Only produced as a
   candidate when that keyword/property is actually present at the call
   site (whether its own value resolves or not) -- a call site with no
   `response_format` at all is not itself evidence of anything and is
   never reported.

3. **Authorization/confirmation boundaries (step 8, entirely new)** -- a
   tool definition's own literal `side_effect_class=`/`sideEffectClass:`
   and `requires_confirmation=`/`requiresConfirmation:` fields, plus a
   disjoint, closed config-key vocabulary
   (`SIDEEFFECTCLASS`/`TOOLSIDEEFFECTCLASS`,
   `REQUIRESCONFIRMATION`/`TOOLREQUIRESCONFIRMATION`) over FTF-04A11's own
   `ConfigKeyEvidence`, the same three-source composition shape FTF-04A12/
   FTF-04A13 already established one/two tasks over.

This task's own negative_acceptance ("Unknown authorization is not
inferred from tool description; desired deny policy must be enforced in
runtime") is met by two independent, structural facts, each proven by a
dedicated test:

  - **Never inferred from description.** No code path in this module (or
    its own JS/TS subprocess helper) ever reads a tool's own `description`
    value for any purpose whatsoever -- neither this module's own
    `_extract_tool_decorator_candidate` (Python) nor its own helper's
    `extractToolDefinitionFields` (JS/TS) ever looks up a `description`
    keyword/property at all. `side_effect_class`/`requires_confirmation`
    are resolved *only* from their own same-named literal
    keyword-argument/object-property/config-key value -- never from
    keyword-matching, sentiment analysis, or any other inspection of free
    text anywhere in this module. `test_tool_with_reassuring_description_
    but_no_structural_declaration_is_unresolved_never_permissive` proves
    this directly: a `@tool(description="completely safe, read-only, no
    confirmation ever needed")`-decorated function with no
    `side_effect_class=`/`requires_confirmation=` keyword at all comes
    back with both fields `resolved=False`, `reason="argument_not_present"`
    -- never a fabricated `read_only`/`False` default.
  - **Deny-policy enforcement is not this module's own job, and this
    module never bypasses it.** The real deny-policy enforcement this
    negative_acceptance clause's own second half names is FTF-0406's
    already-CERTIFIED `enforce_side_effect_classification`
    (`apps/api/app/services/side_effect_classification.py`), already
    live-wired into `app.tools.tool_definition_schema.ToolDefinition.
    __init__` -- the real boundary every `create_tool_definition`/
    `edit_tool_definition` call already passes through today, independent
    of this task. This module produces *proposal* evidence only (Section
    7A.5: "Discovery is proposal, not governance") -- it does not itself
    construct a `ToolDefinition` row, call `create_tool_definition`, or
    otherwise reach that enforcement boundary, so there is no code path
    here that could weaken or bypass it. A later, separate composition
    task (not this one) is the one that would turn a
    `ToolSchemaPermissionCandidate` into a real `ToolDefinition` row, and
    it would do so through that same already-enforced boundary, the same
    "detection is not the same act as storage/selection" boundary every
    04A-family task before this one has drawn.

Reuse, not reimplementation (Section 77's own binding reuse-first rule,
applied at the service-layer boundary this task's own `shared_contracts_
read`/dependency list names): a discovered `input_schema`/`output_schema`
literal is validated by calling FTF-0405's own already-CERTIFIED
`validate_json_schema_document`
(`apps/api/app/services/json_schema_tool_validation.py`) -- this module
never reimplements JSON Schema validation. A discovered `side_effect_class`
literal is checked for enum membership against FTF-0406's own already-
CERTIFIED `RISK_RANK` mapping's own key set
(`apps/api/app/services/side_effect_classification.py`) -- the real,
already-declared closed five-value enum -- this module never re-declares
that enum locally. Neither service's own floor-enforcement/risk-signal
logic (`enforce_side_effect_classification`/`classify_side_effect_signals`)
is called here: this task's own acceptance clause is "resolve... with
explicit uncertainties", not "enforce a policy" -- enforcement already
happens, and continues to happen, at the real `ToolDefinition.__init__`
boundary named above, independent of anything this discovery module does.

AST use is read-only and non-executing throughout, the same discipline
FTF-04A09 already established: this module only calls `ast.parse` and
`ast.literal_eval`, never `exec`/`eval`/`compile`/`importlib`/a subprocess
for Python content. JS/TS-side detection lives in this task's own new,
separate subprocess helper,
`_typescript_javascript_tool_schema_permission_ast_helper.cjs` -- never an
edit to FTF-04A10's own already-CERTIFIED, frozen
`_typescript_javascript_ast_helper.cjs`, nor to FTF-04A13's own already-
CERTIFIED, frozen `_typescript_javascript_prompt_source_ast_helper.cjs`
(Section 75.1: "shared module changes require ownership transfer/
serialization"). Several small helpers (module/client-alias tracing, the
literal evaluator, the completion-call matcher) are intentionally
duplicated from those two files rather than imported, the same disclosed
per-task-duplication precedent FTF-04A10/FTF-04A13 already established.

Resource limits (`ResourceLimits`) mirror FTF-04A09's/FTF-04A10's own
shape exactly (`max_files`/`max_file_bytes`/`max_total_bytes`/
`subprocess_timeout_seconds`); every limit that stops a run before it
reaches the end of the caller's own sequence sets `truncated=True`.

Input shape: reuses FTF-04A08's own connector-agnostic `SourceFileRef`
and FTF-04A11's own `ConfigDocumentEvidence`, the same "no second input
shape invented" precedent every sibling task in this family has already
established.

Database changes: none (this task's own `database_changes` clause).
`run_tool_schema_permission_discovery` is a synchronous, no-I/O-of-its-own
function over already-computed evidence objects. This module does not
itself assemble a `DiscoveredAgentCandidate` row or write
`ToolDefinition`/`AgentManifest.tool_contracts_json` rows -- a later
composition task owns that, the same "detection is not the same act as
storage/selection" boundary every 04A-family task before this one has
drawn. `proving_symbol` stays blank and `product_truth_check.py`'s
product-true count is deliberately unchanged, the same disclosed "no live
route yet" scope FTF-04A09/FTF-04A10/FTF-04A11/FTF-04A12/FTF-04A13 already
established.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.services.json_schema_tool_validation import (
    JsonSchemaValidationError,
    validate_json_schema_document,
)
from app.services.side_effect_classification import RISK_RANK
from app.target_connectors.json_yaml_toml_configuration_discovery import ConfigDocumentEvidence
from app.target_connectors.source_analysis_worker import SourceFileRef

__all__ = [
    "RequiresConfirmationEvidence",
    "ResourceLimits",
    "SchemaFieldEvidence",
    "SideEffectClassEvidence",
    "ToolSchemaPermissionCandidate",
    "ToolSchemaPermissionDiscoveryError",
    "ToolSchemaPermissionDiscoveryEvidence",
    "run_tool_schema_permission_discovery",
]

_HELPER_SCRIPT_PATH = (
    Path(__file__).resolve().parent / "_typescript_javascript_tool_schema_permission_ast_helper.cjs"
)

# Closed, disclosed, non-guessed decorator/function-call vocabulary for a
# standalone tool definition -- see this module's own docstring, point 1,
# for why exactly these two names.
_TOOL_DEFINITION_DECORATOR_NAMES = frozenset({"tool", "function_tool"})

# Duplicated from FTF-04A09's/FTF-04A13's own closed vocabulary -- see this
# module's own docstring for why this is intentional, disclosed
# duplication rather than a shared-module edit.
_LITELLM_COMPLETION_FUNCTION_NAMES = frozenset({"completion", "acompletion"})

# FTF-0406's own already-declared closed five-value enum, reused by
# reference to its own key set -- never re-declared locally.
_VALID_SIDE_EFFECT_CLASSES = frozenset(RISK_RANK)

# Closed, disjoint config-key vocabularies for step 8's own "config" half --
# the same exact-match (never substring) heuristic shape FTF-04A11's own
# `_is_secret_like_key`/FTF-04A12's own `_MODEL_KEY_NAMES`/FTF-04A13's own
# prompt-key vocabularies already established.
_SIDE_EFFECT_CLASS_KEY_NAMES = frozenset({"SIDEEFFECTCLASS", "TOOLSIDEEFFECTCLASS"})
_REQUIRES_CONFIRMATION_KEY_NAMES = frozenset({"REQUIRESCONFIRMATION", "TOOLREQUIRESCONFIRMATION"})

_PARSE_EXCEPTIONS: tuple[type[Exception], ...] = (
    SyntaxError,
    ValueError,
    UnicodeDecodeError,
    RecursionError,
    MemoryError,
)
_LITERAL_EVAL_EXCEPTIONS: tuple[type[Exception], ...] = (
    ValueError,
    TypeError,
    SyntaxError,
    MemoryError,
    RecursionError,
)


class ToolSchemaPermissionDiscoveryError(Exception):
    """A caller-supplied `SourceFileRef` violates this worker's own input
    shape (negative size) -- rejected at the boundary rather than
    propagated downstream."""


@dataclass(frozen=True)
class ResourceLimits:
    """This task's own real "resource-limited" half, mirroring FTF-04A09's/
    FTF-04A10's own shape exactly."""

    max_files: int = 5_000
    max_file_bytes: int = 2_000_000
    max_total_bytes: int = 50_000_000
    subprocess_timeout_seconds: float = 5.0


@dataclass(frozen=True)
class SchemaFieldEvidence:
    """One real `input_schema_json`/`output_schema_json` field (Section
    6.9). `resolved=True` means a real literal JSON-object schema was
    found; `schema_valid` then reports whether FTF-0405's own
    `validate_json_schema_document` actually accepted it (a resolved-but-
    invalid schema is disclosed as such, never silently dropped).
    `schema_sha256` mirrors Section 6.9's own `schema_hash` field."""

    resolved: bool
    schema_valid: bool | None
    schema_sha256: str | None
    reason: str

    def to_json(self) -> dict[str, object]:
        return {
            "resolved": self.resolved,
            "schema_valid": self.schema_valid,
            "schema_sha256": self.schema_sha256,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SideEffectClassEvidence:
    """One real `side_effect_class` field (Section 6.9). Resolved only
    from a literal string that is itself a member of FTF-0406's own
    closed five-value enum -- never from `description` text, and never
    defaulted when absent or invalid. See this module's own docstring,
    negative_acceptance point 1."""

    resolved: bool
    value: str | None
    reason: str

    def to_json(self) -> dict[str, object]:
        return {"resolved": self.resolved, "value": self.value, "reason": self.reason}


@dataclass(frozen=True)
class RequiresConfirmationEvidence:
    """One real `requires_confirmation` field (Section 6.9). Resolved only
    from a literal boolean -- never from `description` text, and never
    defaulted when absent."""

    resolved: bool
    value: bool | None
    reason: str

    def to_json(self) -> dict[str, object]:
        return {"resolved": self.resolved, "value": self.value, "reason": self.reason}


@dataclass(frozen=True)
class ToolSchemaPermissionCandidate:
    """One real tool-schema/structured-output/permission-boundary
    candidate. `tool_name` is `None` when this candidate is a structured-
    output-only call-site finding (step 7) or a config-derived, tool-
    unassociated policy key (step 8's own "config" half) -- never guessed
    from context."""

    relative_path: str
    location: str
    tool_name: str | None
    input_schema: SchemaFieldEvidence
    output_schema: SchemaFieldEvidence
    side_effect_class: SideEffectClassEvidence
    requires_confirmation: RequiresConfirmationEvidence
    reason: str

    def to_json(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "location": self.location,
            "tool_name": self.tool_name,
            "input_schema": self.input_schema.to_json(),
            "output_schema": self.output_schema.to_json(),
            "side_effect_class": self.side_effect_class.to_json(),
            "requires_confirmation": self.requires_confirmation.to_json(),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ToolSchemaPermissionDiscoveryEvidence:
    candidates: tuple[ToolSchemaPermissionCandidate, ...]
    resolved_tool_name_count: int
    files_scanned: int
    files_skipped_oversized: int
    parse_errors: int
    total_bytes_scanned: int
    truncated: bool = False


# --- Shared, source-agnostic field resolution -----------------------------


def _unresolved_schema(reason: str) -> SchemaFieldEvidence:
    return SchemaFieldEvidence(resolved=False, schema_valid=None, schema_sha256=None, reason=reason)


def _resolve_schema_literal(value: object) -> SchemaFieldEvidence:
    """`value` is an already-literal-evaluated candidate (a real Python
    object, never re-parsed from text). Only a real JSON-object (`dict`)
    is a candidate schema at all; anything else is honestly unresolved,
    never guessed. FTF-0405's own `validate_json_schema_document` is the
    only JSON Schema validation performed anywhere in this module -- see
    this module's own docstring, "Reuse, not reimplementation"."""
    if not isinstance(value, dict):
        return _unresolved_schema("unresolved_dynamic_expression")
    try:
        validate_json_schema_document(value, "schema")
    except JsonSchemaValidationError:
        return SchemaFieldEvidence(
            resolved=True,
            schema_valid=False,
            schema_sha256=None,
            reason="invalid_json_schema_document",
        )
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return SchemaFieldEvidence(
        resolved=True,
        schema_valid=True,
        schema_sha256=sha256(canonical).hexdigest(),
        reason="literal_json_schema_document",
    )


def _unresolved_side_effect_class(reason: str) -> SideEffectClassEvidence:
    return SideEffectClassEvidence(resolved=False, value=None, reason=reason)


def _resolve_side_effect_class_literal(value: object) -> SideEffectClassEvidence:
    """Only a literal string that is itself one of FTF-0406's own closed
    five enum values resolves. A literal string outside that enum is
    honestly `invalid_enum_value`, never coerced to the nearest valid
    value or silently accepted."""
    if not isinstance(value, str):
        return _unresolved_side_effect_class("unresolved_dynamic_expression")
    if value not in _VALID_SIDE_EFFECT_CLASSES:
        return _unresolved_side_effect_class("invalid_enum_value")
    return SideEffectClassEvidence(resolved=True, value=value, reason="literal_enum_value")


def _unresolved_confirmation(reason: str) -> RequiresConfirmationEvidence:
    return RequiresConfirmationEvidence(resolved=False, value=None, reason=reason)


def _resolve_requires_confirmation_literal(value: object) -> RequiresConfirmationEvidence:
    if not isinstance(value, bool):
        return _unresolved_confirmation("unresolved_dynamic_expression")
    return RequiresConfirmationEvidence(resolved=True, value=value, reason="literal_boolean")


# --- Python AST detection -----------------------------------------------


def _collect_litellm_aliases(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    """Duplicated from FTF-04A09's/FTF-04A13's own private helper of the
    same shape -- see this module's own docstring."""
    module_aliases: dict[str, str] = {}
    function_aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "litellm":
                    module_aliases[alias.asname or alias.name] = "litellm"
        elif isinstance(node, ast.ImportFrom):
            if node.module == "litellm" and node.level == 0:
                for alias in node.names:
                    if alias.name in _LITELLM_COMPLETION_FUNCTION_NAMES:
                        function_aliases[alias.asname or alias.name] = alias.name
    return module_aliases, function_aliases


def _match_litellm_completion_call(
    node: ast.Call, module_aliases: dict[str, str], function_aliases: dict[str, str]
) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in _LITELLM_COMPLETION_FUNCTION_NAMES:
        value = func.value
        if isinstance(value, ast.Name) and value.id in module_aliases:
            return f"{value.id}.{func.attr}"
        return None
    if isinstance(func, ast.Name) and func.id in function_aliases:
        return func.id
    return None


def _keyword_value(node: ast.Call, keyword: str) -> ast.expr | None:
    for kw in node.keywords:
        if kw.arg == keyword:
            return kw.value
    return None


def _decorator_matches_tool_definition(node: ast.expr) -> bool:
    """Matches a bare `@tool`/`@function_tool` name reference, a bare
    `@some_module.tool`/`@some_module.function_tool` attribute reference,
    or either shape called with arguments (`@tool(...)`). See this
    module's own docstring, point 1, for the closed name vocabulary."""
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id in _TOOL_DEFINITION_DECORATOR_NAMES
    if isinstance(target, ast.Attribute):
        return target.attr in _TOOL_DEFINITION_DECORATOR_NAMES
    return False


def _resolve_literal_keyword(node: ast.Call, keyword: str) -> tuple[bool, object]:
    """Returns `(present, value)`: `present=False` means the keyword is
    genuinely absent; `present=True` with `value=_UNRESOLVED` means it is
    present but not a literal this module can evaluate."""
    value_node = _keyword_value(node, keyword)
    if value_node is None:
        return False, None
    try:
        return True, ast.literal_eval(value_node)
    except _LITERAL_EVAL_EXCEPTIONS:
        return True, _UNRESOLVED_LITERAL


class _UnresolvedLiteral:
    """A private sentinel distinguishing "keyword present but its value is
    not a literal this module can evaluate" from a real resolved `None`
    literal (e.g. `name=None`)."""


_UNRESOLVED_LITERAL = _UnresolvedLiteral()


def _extract_tool_decorator_candidate(
    decorator: ast.expr, *, function_name: str, relative_path: str
) -> ToolSchemaPermissionCandidate:
    location = f"decorator:{function_name}"
    if not isinstance(decorator, ast.Call):
        # A bare `@tool`/`@function_tool` with no call at all -- every
        # field this module resolves from keyword arguments is genuinely
        # absent; only the decorated function's own name is known.
        return ToolSchemaPermissionCandidate(
            relative_path=relative_path,
            location=location,
            tool_name=function_name,
            input_schema=_unresolved_schema("argument_not_present"),
            output_schema=_unresolved_schema("argument_not_present"),
            side_effect_class=_unresolved_side_effect_class("argument_not_present"),
            requires_confirmation=_unresolved_confirmation("argument_not_present"),
            reason="bare_tool_decorator",
        )

    name_present, name_value = _resolve_literal_keyword(decorator, "name")
    tool_name = function_name
    if name_present and isinstance(name_value, str):
        tool_name = name_value

    def _schema_field(keyword: str) -> SchemaFieldEvidence:
        present, value = _resolve_literal_keyword(decorator, keyword)
        if not present:
            return _unresolved_schema("argument_not_present")
        if value is _UNRESOLVED_LITERAL:
            return _unresolved_schema("unresolved_dynamic_expression")
        return _resolve_schema_literal(value)

    def _side_effect_class_field() -> SideEffectClassEvidence:
        present, value = _resolve_literal_keyword(decorator, "side_effect_class")
        if not present:
            return _unresolved_side_effect_class("argument_not_present")
        if value is _UNRESOLVED_LITERAL:
            return _unresolved_side_effect_class("unresolved_dynamic_expression")
        return _resolve_side_effect_class_literal(value)

    def _requires_confirmation_field() -> RequiresConfirmationEvidence:
        present, value = _resolve_literal_keyword(decorator, "requires_confirmation")
        if not present:
            return _unresolved_confirmation("argument_not_present")
        if value is _UNRESOLVED_LITERAL:
            return _unresolved_confirmation("unresolved_dynamic_expression")
        return _resolve_requires_confirmation_literal(value)

    return ToolSchemaPermissionCandidate(
        relative_path=relative_path,
        location=location,
        tool_name=tool_name,
        input_schema=_schema_field("input_schema"),
        output_schema=_schema_field("output_schema"),
        side_effect_class=_side_effect_class_field(),
        requires_confirmation=_requires_confirmation_field(),
        reason="tool_decorator",
    )


def _extract_response_format_candidate(
    node: ast.Call, *, function_name: str, relative_path: str
) -> ToolSchemaPermissionCandidate | None:
    """Section 7A.4 step 7 -- only produced when `response_format=` is
    actually present at this real completion-call site; a call site with
    no such keyword at all is not itself a finding worth reporting."""
    value_node = _keyword_value(node, "response_format")
    if value_node is None:
        return None
    try:
        value = ast.literal_eval(value_node)
    except _LITERAL_EVAL_EXCEPTIONS:
        output_schema = _unresolved_schema("unresolved_dynamic_expression")
    else:
        output_schema = _resolve_schema_literal(value)
    return ToolSchemaPermissionCandidate(
        relative_path=relative_path,
        location=f"function:{function_name}",
        tool_name=None,
        input_schema=_unresolved_schema("not_applicable"),
        output_schema=output_schema,
        side_effect_class=_unresolved_side_effect_class("not_applicable"),
        requires_confirmation=_unresolved_confirmation("not_applicable"),
        reason="structured_output_response_format",
    )


class _ToolCallVisitor(ast.NodeVisitor):
    """Module-level (never a per-file closure -- see FTF-04A09's own
    `_EntrypointVisitor` precedent) visitor: walks one already-parsed
    file's own AST, tracking real function scope, and extracts both a
    `@tool`/`@function_tool`-decorated function's own candidate and any
    qualifying completion call's own `response_format=` candidate."""

    def __init__(
        self,
        *,
        relative_path: str,
        module_aliases: dict[str, str],
        function_aliases: dict[str, str],
    ) -> None:
        self._relative_path = relative_path
        self._module_aliases = module_aliases
        self._function_aliases = function_aliases
        self._function_stack: list[str] = ["<module>"]
        self.candidates: list[ToolSchemaPermissionCandidate] = []

    def _visit_function_scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            if _decorator_matches_tool_definition(decorator):
                self.candidates.append(
                    _extract_tool_decorator_candidate(
                        decorator, function_name=node.name, relative_path=self._relative_path
                    )
                )
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_function_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_function_scope(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if (
            _match_litellm_completion_call(node, self._module_aliases, self._function_aliases)
            is not None
        ):
            candidate = _extract_response_format_candidate(
                node, function_name=self._function_stack[-1], relative_path=self._relative_path
            )
            if candidate is not None:
                self.candidates.append(candidate)
        self.generic_visit(node)


def _run_python_discovery(
    files: Sequence[SourceFileRef],
    *,
    limits: ResourceLimits,
    counters: dict[str, int],
) -> list[ToolSchemaPermissionCandidate]:
    candidates: list[ToolSchemaPermissionCandidate] = []
    for file_ref in files:
        if not file_ref.relative_path.endswith(".py"):
            continue
        if not isinstance(file_ref.size, int) or file_ref.size < 0:
            raise ToolSchemaPermissionDiscoveryError(
                f"size must be a real, non-negative integer for "
                f"{file_ref.relative_path!r}: {file_ref.size!r}"
            )
        if counters["files_scanned"] >= limits.max_files:
            counters["truncated"] = 1
            break
        if file_ref.size > limits.max_file_bytes:
            counters["files_skipped_oversized"] += 1
            continue
        if counters["total_bytes_scanned"] + file_ref.size > limits.max_total_bytes:
            counters["truncated"] = 1
            continue

        content = file_ref.read_content()
        counters["files_scanned"] += 1
        counters["total_bytes_scanned"] += len(content)

        try:
            source_text = content.decode("utf-8")
            tree = ast.parse(source_text, filename=file_ref.relative_path)
        except _PARSE_EXCEPTIONS:
            counters["parse_errors"] += 1
            continue

        module_aliases, function_aliases = _collect_litellm_aliases(tree)
        visitor = _ToolCallVisitor(
            relative_path=file_ref.relative_path,
            module_aliases=module_aliases,
            function_aliases=function_aliases,
        )
        visitor.visit(tree)
        candidates.extend(visitor.candidates)

    return candidates


# --- TypeScript/JavaScript subprocess detection --------------------------


def _run_helper(relative_path: str, source_text: str, timeout_seconds: float) -> dict[str, object]:
    node_path = shutil.which("node")
    if node_path is None:
        return {"ok": False, "reason": "node_unavailable"}
    request = json.dumps({"relative_path": relative_path, "source_text": source_text})
    try:
        completed = subprocess.run(  # noqa: S603
            [node_path, str(_HELPER_SCRIPT_PATH)],
            input=request,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "parse_error"}
    if completed.returncode != 0:
        return {"ok": False, "reason": "parse_error"}
    try:
        response = json.loads(completed.stdout)
    except (json.JSONDecodeError, ValueError):
        return {"ok": False, "reason": "parse_error"}
    if not isinstance(response, dict):
        return {"ok": False, "reason": "parse_error"}
    return response


_SUPPORTED_TS_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")


def _schema_evidence_from_status(status: object) -> SchemaFieldEvidence:
    if not isinstance(status, dict):
        return _unresolved_schema("unresolved_dynamic_expression")
    present = status.get("present")
    if present == "no":
        return _unresolved_schema("argument_not_present")
    if present == "literal":
        return _resolve_schema_literal(status.get("value"))
    return _unresolved_schema("unresolved_dynamic_expression")


def _side_effect_class_evidence_from_status(status: object) -> SideEffectClassEvidence:
    if not isinstance(status, dict):
        return _unresolved_side_effect_class("unresolved_dynamic_expression")
    present = status.get("present")
    if present == "no":
        return _unresolved_side_effect_class("argument_not_present")
    if present == "literal":
        return _resolve_side_effect_class_literal(status.get("value"))
    return _unresolved_side_effect_class("unresolved_dynamic_expression")


def _requires_confirmation_evidence_from_status(status: object) -> RequiresConfirmationEvidence:
    if not isinstance(status, dict):
        return _unresolved_confirmation("unresolved_dynamic_expression")
    present = status.get("present")
    if present == "no":
        return _unresolved_confirmation("argument_not_present")
    if present == "literal":
        return _resolve_requires_confirmation_literal(status.get("value"))
    return _unresolved_confirmation("unresolved_dynamic_expression")


def _candidate_from_helper_payload(
    raw: dict[str, object], *, relative_path: str
) -> ToolSchemaPermissionCandidate:
    reason = str(raw.get("reason", "unresolved_dynamic_expression"))
    function_name = str(raw.get("function_name", "<anonymous>"))
    location = (
        f"function:{function_name}"
        if reason == "structured_output_response_format"
        else f"call:{function_name}"
    )
    tool_name_raw = raw.get("tool_name")
    tool_name = tool_name_raw if isinstance(tool_name_raw, str) else None
    return ToolSchemaPermissionCandidate(
        relative_path=relative_path,
        location=location,
        tool_name=tool_name,
        input_schema=_schema_evidence_from_status(raw.get("input_schema")),
        output_schema=_schema_evidence_from_status(raw.get("output_schema")),
        side_effect_class=_side_effect_class_evidence_from_status(raw.get("side_effect_class")),
        requires_confirmation=_requires_confirmation_evidence_from_status(
            raw.get("requires_confirmation")
        ),
        reason=reason,
    )


def _run_typescript_javascript_discovery(
    files: Sequence[SourceFileRef],
    *,
    limits: ResourceLimits,
    counters: dict[str, int],
) -> list[ToolSchemaPermissionCandidate]:
    candidates: list[ToolSchemaPermissionCandidate] = []
    for file_ref in files:
        if not file_ref.relative_path.endswith(_SUPPORTED_TS_JS_EXTENSIONS):
            continue
        if not isinstance(file_ref.size, int) or file_ref.size < 0:
            raise ToolSchemaPermissionDiscoveryError(
                f"size must be a real, non-negative integer for "
                f"{file_ref.relative_path!r}: {file_ref.size!r}"
            )
        if counters["files_scanned"] >= limits.max_files:
            counters["truncated"] = 1
            break
        if file_ref.size > limits.max_file_bytes:
            counters["files_skipped_oversized"] += 1
            continue
        if counters["total_bytes_scanned"] + file_ref.size > limits.max_total_bytes:
            counters["truncated"] = 1
            continue

        content = file_ref.read_content()
        counters["files_scanned"] += 1
        counters["total_bytes_scanned"] += len(content)

        try:
            source_text = content.decode("utf-8")
        except UnicodeDecodeError:
            counters["parse_errors"] += 1
            continue

        response = _run_helper(
            file_ref.relative_path, source_text, limits.subprocess_timeout_seconds
        )
        if not response.get("ok"):
            counters["parse_errors"] += 1
            continue

        raw_candidates = response.get("candidates")
        if not isinstance(raw_candidates, list):
            counters["parse_errors"] += 1
            continue

        for raw in raw_candidates:
            if not isinstance(raw, dict):
                continue
            candidates.append(
                _candidate_from_helper_payload(raw, relative_path=file_ref.relative_path)
            )

    return candidates


# --- Config-derived detection ---------------------------------------------


def _normalized_leaf_key(key_path: str) -> str:
    last_segment = key_path.rsplit(".", 1)[-1]
    last_segment = last_segment.split("[", 1)[0]
    return "".join(ch for ch in last_segment if ch.isalnum()).upper()


def _config_derived_candidates(
    document: ConfigDocumentEvidence,
) -> list[ToolSchemaPermissionCandidate]:
    candidates: list[ToolSchemaPermissionCandidate] = []
    if not document.resolved:
        return candidates
    for key in document.keys:
        if key.secret_like:
            continue
        normalized = _normalized_leaf_key(key.key_path)
        location = f"key:{key.key_path}"
        if normalized in _SIDE_EFFECT_CLASS_KEY_NAMES:
            if key.value_type == "string" and isinstance(key.value, str) and key.value != "":
                side_effect_class = _resolve_side_effect_class_literal(key.value)
            else:
                reason = (
                    "empty_string_value"
                    if key.value_type == "string"
                    else "unresolved_non_string_value"
                )
                side_effect_class = _unresolved_side_effect_class(reason)
            candidates.append(
                ToolSchemaPermissionCandidate(
                    relative_path=document.relative_path,
                    location=location,
                    tool_name=None,
                    input_schema=_unresolved_schema("not_applicable"),
                    output_schema=_unresolved_schema("not_applicable"),
                    side_effect_class=side_effect_class,
                    requires_confirmation=_unresolved_confirmation("not_applicable"),
                    reason="config_derived_side_effect_class",
                )
            )
        elif normalized in _REQUIRES_CONFIRMATION_KEY_NAMES:
            if key.value_type == "boolean" and isinstance(key.value, bool):
                requires_confirmation = _resolve_requires_confirmation_literal(key.value)
            else:
                requires_confirmation = _unresolved_confirmation("unresolved_non_boolean_value")
            candidates.append(
                ToolSchemaPermissionCandidate(
                    relative_path=document.relative_path,
                    location=location,
                    tool_name=None,
                    input_schema=_unresolved_schema("not_applicable"),
                    output_schema=_unresolved_schema("not_applicable"),
                    side_effect_class=_unresolved_side_effect_class("not_applicable"),
                    requires_confirmation=requires_confirmation,
                    reason="config_derived_requires_confirmation",
                )
            )
    return candidates


def run_tool_schema_permission_discovery(
    files: Sequence[SourceFileRef],
    *,
    config_documents: Sequence[ConfigDocumentEvidence] = (),
    resource_limits: ResourceLimits | None = None,
) -> ToolSchemaPermissionDiscoveryEvidence:
    """Section 7A.4 steps 6 (remainder)/7/8 -- see this module's own
    docstring for the full scope boundary. Never raises on adversarial
    file content (caught and counted, see `parse_errors`, or reported
    per-candidate via `resolved`/`reason`); only raises
    `ToolSchemaPermissionDiscoveryError` for a structurally unsafe
    caller-supplied `SourceFileRef` (negative size) -- a caller bug, not
    untrusted snapshot content."""
    limits = resource_limits or ResourceLimits()

    counters = {
        "files_scanned": 0,
        "files_skipped_oversized": 0,
        "parse_errors": 0,
        "total_bytes_scanned": 0,
        "truncated": 0,
    }

    candidates: list[ToolSchemaPermissionCandidate] = []
    candidates.extend(_run_python_discovery(files, limits=limits, counters=counters))
    candidates.extend(_run_typescript_javascript_discovery(files, limits=limits, counters=counters))
    for document in config_documents:
        candidates.extend(_config_derived_candidates(document))

    resolved_tool_name_count = sum(1 for candidate in candidates if candidate.tool_name is not None)

    return ToolSchemaPermissionDiscoveryEvidence(
        candidates=tuple(candidates),
        resolved_tool_name_count=resolved_tool_name_count,
        files_scanned=counters["files_scanned"],
        files_skipped_oversized=counters["files_skipped_oversized"],
        parse_errors=counters["parse_errors"],
        total_bytes_scanned=counters["total_bytes_scanned"],
        truncated=bool(counters["truncated"]),
    )
