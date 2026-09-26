"""Python deterministic agent/config/tool discovery (FTF-04A09, Section
7A.4/9/66/77/81).

Section 7A.4's own numbered "Analysis order" names twelve steps; this task
owns exactly the slice its own acceptance clause names -- "Parse tested
Python AST/config patterns for agent entrypoints, tools and model bindings
with evidence" -- read as the intersection of step 3 ("Detect probable
agent entrypoints"), step 4 ("Detect model configuration and LiteLLM
aliases") and step 6 ("Detect tool/function schemas and adapters") for
Python source only, tied together by one real, statically-verifiable AST
pattern: a call, anywhere in a real Python file, to a name traced back to a
genuine `litellm` import in that same file. It deliberately does not
detect prompt sources (step 5), structured output schemas (step 7),
authorization/confirmation boundaries (step 8), test/eval fixtures (step
9), trace/log interfaces (step 10), deployment configuration (step 11), or
assemble a `DiscoveredAgentCandidate` row (step 12, FTF-04A17's own
already-CERTIFIED storage schema -- `candidate_key`/`confidence_score`/
`discovery_status` assignment is a separate, later composition task this
one does not build). Declaring any of that here without a real backing
implementation would be exactly the "placeholder endpoint" Law 11 forbids.

Non-guessed heuristic, disclosed in full (Section 7A.4's own closing
sentence, "Unknown/custom frameworks are not guessed", read for this task
the same way FTF-04A08 already read it for package-manifest framework
detection): a call is only ever treated as a LiteLLM completion call when
its own callee is statically traced, in the same file, to one of exactly
three import shapes -- `import litellm` / `import litellm as <alias>`
(`litellm.completion(...)` / `<alias>.completion(...)` /
`<alias>.acompletion(...)`), or `from litellm import completion` /
`from litellm import acompletion` (optionally aliased, bare
`completion(...)` / `acompletion(...)`). No other completion-shaped call
name, and no relative or wildcard (`from . import litellm`, `from litellm
import *`) import, is ever matched -- each is a genuinely dynamic/
unresolved shape this module does not guess through.

Model binding: the call's own `model=` keyword argument (only ever a
keyword; a positional-argument call is honestly `argument_not_present` for
this field, never guessed from positional order) is resolved via
`ast.literal_eval` -- a pure, non-executing literal-AST evaluator, the
opposite of `eval`. A literal string resolves; any other shape (a variable,
an f-string, a call, string concatenation, a non-string literal) is
`unresolved_dynamic_expression` -- this task's own negative_acceptance,
"Dynamic/unresolved constructs remain unknown", applied literally.

Tool schema: the call's own `tools=` keyword argument is resolved the same
way -- only when `ast.literal_eval` yields a real Python `list` whose every
element is a `dict` shaped exactly like an OpenAI-compatible function-
calling tool entry (`{"type": "function", "function": {"name": "<str>",
...}}`, Section 7A.4's own "JSON Schema/OpenAPI/Pydantic/Zod-style tool
contracts when statically resolvable" bullet). One non-conforming element
marks the *entire* list `unresolved_dynamic_expression` rather than
silently dropping just that element -- a partial extraction reported as
complete would misrepresent what was actually found.

Agent entrypoint: the *enclosing function* (or `"<module>"`, if the call is
made at module scope) containing a qualifying call is the "probable agent
entrypoint" this task's own acceptance clause names -- Section 7A.4's own
UI mockup names a single function/handler as an entrypoint, not the file as
a whole. Two qualifying calls in two different functions of the same file
yield two distinct `EntrypointCandidate` entries, never coalesced into one.

AST use is read-only and non-executing throughout: this module only calls
`ast.parse` (a pure syntax-tree builder) and `ast.literal_eval` (a pure
literal-AST evaluator) -- never `exec`/`eval`/`compile`/`importlib`/a
subprocess, matching Section 7A.4's own "without importing Python
modules... running... build[s]" discipline and FTF-04A08's own module
docstring precedent one level up. A file that fails to parse (a real
`SyntaxError`, or any other genuine parse-time exception) is caught and
counted, never allowed to escape this worker -- this task's own "malicious
parser input cannot escape worker" negative_acceptance half.

Resource limits (`ResourceLimits`) mirror FTF-04A08's own shape:
`max_files` bounds how many `.py` files this run actually parses;
`max_file_bytes` skips reading any single file above that size without
ever calling its own `read_content`; `max_total_bytes` stops reading
further file content once the cumulative budget for this run is spent.
Every limit that stops the run before it reaches the end of the caller's
own sequence sets `truncated=True` -- an honestly incomplete analysis is
disclosed, never silently reported as if it were complete.

Input shape: reuses FTF-04A08's own connector-agnostic `SourceFileRef`
(`apps/api/app/target_connectors/source_analysis_worker.py`) rather than
inventing a second one, so a caller that already assembled a
`Sequence[SourceFileRef]` for FTF-04A08 can hand the identical sequence to
this module with no adaptation. Only entries whose `relative_path` ends in
`.py` are ever considered; this module does not re-implement FTF-04A08's
own ignore-directory/secret-path rules itself -- a caller is expected to
have already run (or separately runs) FTF-04A08's own `run_source_analysis`
over the same snapshot and pass its own `included_paths` (or an equivalent
already-filtered set) here. That filtering is disclosed as relied-upon, not
silently re-derived.

Output evidence shape: each `EntrypointCandidate`'s own
`to_entrypoint_evidence_json`/`to_model_evidence_json`/`to_tool_evidence_json`
produce a plain JSON object shaped for direct storage in FTF-04A17's own
independently-nullable `entrypoint_evidence_json`/`model_evidence_json`/
`tool_evidence_json` columns. This module does not itself call FTF-04A17's
CRUD or assemble a `DiscoveredAgentCandidate` row -- `candidate_key`/
`confidence_score`/`discovery_status` assignment (Section 7A.4 step 12) is
a separate, later composition task's own job, the same "detection is not
the same act as storage/selection" boundary FTF-04A08 already drew.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass

from app.target_connectors.source_analysis_worker import SourceFileRef

__all__ = [
    "EntrypointCandidate",
    "ModelBindingEvidence",
    "PythonDiscoveryError",
    "PythonDiscoveryEvidence",
    "ResourceLimits",
    "ToolSchemaEvidence",
    "run_python_agent_config_tool_discovery",
]

# Section 7A.4's own literal function names for the reused LiteLLM gateway.
# Never expanded by inference -- a closed, named set.
_LITELLM_COMPLETION_FUNCTION_NAMES = frozenset({"completion", "acompletion"})

# `ast.parse`/`ast.literal_eval` on real, adversarial/malformed Python
# source can raise any of these -- every one is caught and counted, never
# allowed to escape this worker (this task's own "malicious parser input
# cannot escape worker" negative_acceptance half).
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


class PythonDiscoveryError(Exception):
    """A caller-supplied `SourceFileRef` violates this worker's own input
    shape (negative size) -- rejected at the boundary rather than
    propagated downstream."""


@dataclass(frozen=True)
class ResourceLimits:
    """This task's own real "resource-limited" half -- see this module's
    own docstring for what each bound independently protects against."""

    max_files: int = 5_000
    max_file_bytes: int = 2_000_000
    max_total_bytes: int = 50_000_000


@dataclass(frozen=True)
class ModelBindingEvidence:
    """Section 7A.4 step 4 -- "Detect model configuration and LiteLLM
    aliases", resolved only from a literal string, never guessed."""

    resolved: bool
    model_alias: str | None
    reason: str

    def to_json(self) -> dict[str, object]:
        return {
            "resolved": self.resolved,
            "model_alias": self.model_alias,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ToolSchemaEvidence:
    """Section 7A.4 step 6 -- "Detect tool/function schemas and adapters",
    resolved only from a literal, OpenAI-function-shaped list, never
    guessed."""

    resolved: bool
    tool_names: tuple[str, ...]
    reason: str

    def to_json(self) -> dict[str, object]:
        return {
            "resolved": self.resolved,
            "tool_names": list(self.tool_names),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EntrypointCandidate:
    """Section 7A.4 step 3 -- one real, statically-verified LiteLLM
    completion call site, and the model/tool evidence tied to it."""

    relative_path: str
    line: int
    function_name: str
    call_attribute: str
    model_binding: ModelBindingEvidence
    tool_schema: ToolSchemaEvidence

    def to_entrypoint_evidence_json(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "line": self.line,
            "function_name": self.function_name,
            "call": self.call_attribute,
        }

    def to_model_evidence_json(self) -> dict[str, object]:
        return self.model_binding.to_json()

    def to_tool_evidence_json(self) -> dict[str, object]:
        return self.tool_schema.to_json()


@dataclass(frozen=True)
class PythonDiscoveryEvidence:
    """This task's own real "evidence output schema" half -- see this
    module's own docstring for the full scope boundary."""

    entrypoints: tuple[EntrypointCandidate, ...]
    files_scanned: int
    files_skipped_oversized: int
    parse_errors: int
    total_bytes_scanned: int
    truncated: bool = False


def _collect_litellm_aliases(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    """A real, file-local namespace scan for exactly the three import
    shapes this module's own docstring names -- never a relative or
    wildcard import, disclosed as genuinely unmatched rather than guessed."""
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


def _extract_model_binding(node: ast.Call) -> ModelBindingEvidence:
    value_node = _keyword_value(node, "model")
    if value_node is None:
        return ModelBindingEvidence(resolved=False, model_alias=None, reason="argument_not_present")
    try:
        value = ast.literal_eval(value_node)
    except _LITERAL_EVAL_EXCEPTIONS:
        return ModelBindingEvidence(
            resolved=False, model_alias=None, reason="unresolved_dynamic_expression"
        )
    if not isinstance(value, str):
        return ModelBindingEvidence(
            resolved=False, model_alias=None, reason="unresolved_dynamic_expression"
        )
    return ModelBindingEvidence(resolved=True, model_alias=value, reason="literal_string")


def _openai_function_tool_name(entry: object) -> str | None:
    if not isinstance(entry, dict) or entry.get("type") != "function":
        return None
    function_obj = entry.get("function")
    if not isinstance(function_obj, dict):
        return None
    name = function_obj.get("name")
    return name if isinstance(name, str) else None


def _extract_tool_schema(node: ast.Call) -> ToolSchemaEvidence:
    value_node = _keyword_value(node, "tools")
    if value_node is None:
        return ToolSchemaEvidence(resolved=False, tool_names=(), reason="argument_not_present")
    try:
        value = ast.literal_eval(value_node)
    except _LITERAL_EVAL_EXCEPTIONS:
        return ToolSchemaEvidence(
            resolved=False, tool_names=(), reason="unresolved_dynamic_expression"
        )
    if not isinstance(value, list):
        return ToolSchemaEvidence(
            resolved=False, tool_names=(), reason="unresolved_dynamic_expression"
        )
    tool_names: list[str] = []
    for entry in value:
        name = _openai_function_tool_name(entry)
        if name is None:
            return ToolSchemaEvidence(
                resolved=False, tool_names=(), reason="unresolved_dynamic_expression"
            )
        tool_names.append(name)
    return ToolSchemaEvidence(
        resolved=True, tool_names=tuple(tool_names), reason="literal_openai_function_list"
    )


class _EntrypointVisitor(ast.NodeVisitor):
    def __init__(
        self,
        relative_path: str,
        module_aliases: dict[str, str],
        function_aliases: dict[str, str],
    ) -> None:
        self._relative_path = relative_path
        self._module_aliases = module_aliases
        self._function_aliases = function_aliases
        self._function_stack: list[str] = ["<module>"]
        self.candidates: list[EntrypointCandidate] = []

    def _visit_function_scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_scope(node)

    def visit_Call(self, node: ast.Call) -> None:
        call_attribute = _match_litellm_completion_call(
            node, self._module_aliases, self._function_aliases
        )
        if call_attribute is not None:
            self.candidates.append(
                EntrypointCandidate(
                    relative_path=self._relative_path,
                    line=node.lineno,
                    function_name=self._function_stack[-1],
                    call_attribute=call_attribute,
                    model_binding=_extract_model_binding(node),
                    tool_schema=_extract_tool_schema(node),
                )
            )
        self.generic_visit(node)


def run_python_agent_config_tool_discovery(
    files: Sequence[SourceFileRef], *, resource_limits: ResourceLimits | None = None
) -> PythonDiscoveryEvidence:
    """Section 7A.4 steps 3/4/6, Python only -- see this module's own
    docstring for the full scope boundary. Never raises on adversarial
    Python source content (caught and counted, see `parse_errors`); only
    raises `PythonDiscoveryError` for a structurally unsafe caller-supplied
    `SourceFileRef` itself (negative size) -- a caller bug, not untrusted
    snapshot content."""
    limits = resource_limits or ResourceLimits()

    entrypoints: list[EntrypointCandidate] = []
    files_scanned = 0
    files_skipped_oversized = 0
    parse_errors = 0
    total_bytes_scanned = 0
    truncated = False

    for file_ref in files:
        if not file_ref.relative_path.endswith(".py"):
            continue
        if not isinstance(file_ref.size, int) or file_ref.size < 0:
            raise PythonDiscoveryError(
                f"size must be a real, non-negative integer for "
                f"{file_ref.relative_path!r}: {file_ref.size!r}"
            )

        if files_scanned >= limits.max_files:
            truncated = True
            break

        if file_ref.size > limits.max_file_bytes:
            files_skipped_oversized += 1
            continue
        if total_bytes_scanned + file_ref.size > limits.max_total_bytes:
            truncated = True
            continue

        content = file_ref.read_content()
        files_scanned += 1
        total_bytes_scanned += len(content)

        try:
            source_text = content.decode("utf-8")
            tree = ast.parse(source_text, filename=file_ref.relative_path)
        except _PARSE_EXCEPTIONS:
            parse_errors += 1
            continue

        module_aliases, function_aliases = _collect_litellm_aliases(tree)
        visitor = _EntrypointVisitor(file_ref.relative_path, module_aliases, function_aliases)
        visitor.visit(tree)
        entrypoints.extend(visitor.candidates)

    return PythonDiscoveryEvidence(
        entrypoints=tuple(entrypoints),
        files_scanned=files_scanned,
        files_skipped_oversized=files_skipped_oversized,
        parse_errors=parse_errors,
        total_bytes_scanned=total_bytes_scanned,
        truncated=truncated,
    )
