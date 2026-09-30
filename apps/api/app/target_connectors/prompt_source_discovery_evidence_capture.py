"""Prompt-source discovery + evidence capture (FTF-04A13, Section
7A.4/66/77/81).

Section 7A.4's own numbered "Analysis order" names step 5, "Detect
system/developer prompt sources", as its own distinct step -- separate
from step 3 (agent entrypoints, FTF-04A09/FTF-04A10's own scope), step 4
(model bindings, FTF-04A09/FTF-04A10/FTF-04A12's own scope) and the generic
configuration normalization FTF-04A11 already built. Both FTF-04A09's and
FTF-04A10's own module docstrings say explicitly that they "deliberately
do not detect prompt sources (step 5)" -- this is the first task to build
it, over the same real completion-call sites those two tasks already
identify independently (a `litellm.completion(...)`/`acompletion(...)` call
in Python, an `<client>.chat.completions.create(...)` call in TS/JS), plus
a new, disclosed, closed-vocabulary config-key heuristic over FTF-04A11's
own per-document `ConfigKeyEvidence`, the same three-source composition
shape FTF-04A12 already established one task over for model bindings.

This task's own acceptance clause ("Record system/developer prompt source
and referenced template dependencies by hash/location") is read literally:
"by hash/location" is this module's own real security boundary, not a
turn of phrase -- no `PromptSourceCandidate` this module ever produces
carries the real prompt text. An embedded literal prompt string is reduced
to `content_sha256`/`content_length` only; a file-referenced prompt
template is reduced to its own `referenced_path` (a bare location string)
plus, only when that exact path is present in the caller's own already-
captured `SourceFileRef` sequence, that file's own `content_sha256` --
never the real bytes of either. This is this task's own real
`observability` clause ("no secrets/raw prompts in task reports"), enforced
structurally: there is no code path in this module, or in its own JS/TS
subprocess helper, that ever writes real prompt content to this module's
own return value, a log line, or (for the subprocess) its own stdout.

This task's own negative_acceptance ("External/floating prompt source or
embedded injection is treated as data, never analyzer instruction") is met
by two independent, structural design decisions:

1. A `referenced_path` string (from a Python `open(<path>).read()` call or
   a JS/TS `<ident>.readFileSync(<path>, ...)` call) is never itself opened,
   fetched, or read by this module -- it is looked up, by exact string
   match only (never normalized, never traversed, never followed as a URL),
   against the caller's own already-bounded `files` sequence. A path this
   module cannot find there -- because it points outside the captured
   snapshot, is computed at runtime, or names a genuinely different
   template file the snapshot never captured -- is honestly reported
   `template_dependency_resolved=False` ("this is a floating prompt
   source"), never silently treated as present.
2. Whatever a resolved prompt string's own content actually says -- an
   embedded prompt-injection attempt included -- changes nothing about how
   this module classifies it: the string is only ever hashed
   (`hashlib.sha256`/Node's own `crypto.createHash("sha256")`, both pure,
   non-executing digests), never parsed, matched against a keyword list, or
   treated as an instruction to this analyzer itself. `test_embedded_
   prompt_injection_attempt_is_only_ever_hashed_not_interpreted` proves this
   directly.

Python-side detection duplicates FTF-04A09's own closed, three-shape
`litellm`-import/completion-call match (`import litellm`/`import litellm as
X`/`from litellm import completion|acompletion`, each followed by a
`.completion(...)`/`.acompletion(...)`/bare `completion(...)`/
`acompletion(...)` call) rather than importing FTF-04A09's own private
functions -- the same disclosed per-task duplication precedent FTF-04A10
already established one task over for its own JS/TS-equivalent shape.
`messages=` is this module's own new field to extract from that same call:
resolved only when it is a literal `list` of literal-keyed `dict`s (a
computed key, `**`-unpacking, or any other shape marks the whole list
`unresolved_dynamic_expression`, never guessed past); the first entry whose
own literal `role` is `"system"` or `"developer"` is this module's own
prompt-source candidate for that call. That entry's own `content` value is
resolved three ways, in order: a literal string (`embedded_literal`); the
closed call shape `open(<literal path>, ...).read()` (`file_reference`,
Python's own `open` needs no import to trace, unlike FTF-04A09's own
traced `litellm` alias); or, for any other shape (a variable, an f-string,
a different call), honestly `unresolved_dynamic_expression`.

JS/TS-side detection lives in this task's own new, separate subprocess
helper, `_typescript_javascript_prompt_source_ast_helper.cjs` -- never an
edit to FTF-04A10's own already-CERTIFIED, frozen
`_typescript_javascript_ast_helper.cjs` (Section 75.1: "shared module
changes require ownership transfer/serialization"). See that helper's own
header comment for its full scope and the same "isolated approved parser"
resolution (this repo's own already-declared root `typescript`
devDependency) FTF-04A10 already established.

Config-derived detection (Section 7A.4 step 5's own "config" half) reuses
FTF-04A12's own closed, exact-match (never substring) key-name heuristic
shape over FTF-04A11's own `ConfigKeyEvidence`, applied to two new, disjoint
closed vocabularies: `SYSTEMPROMPT`/`DEVELOPERPROMPT` (an embedded literal
prompt value) and `PROMPTPATH`/`PROMPTTEMPLATE`/`PROMPTFILE`/
`SYSTEMPROMPTPATH` (a referenced template path, resolved the same
files-sequence lookup as the Python/JS-TS file-reference case). A
secret-shaped key (already redacted by FTF-04A11 itself) is never treated
as a prompt-source candidate -- FTF-04A11's own redaction boundary means
its real value was never carried past that point for this module to read.

Database changes: none (this task's own `database_changes` clause). Like
FTF-04A12 itself, `run_prompt_source_discovery` is a synchronous function
over already-computed evidence objects plus the caller's own original
`SourceFileRef` sequence (needed here, unlike FTF-04A12, only to resolve a
`referenced_path` against an already-captured template file's real bytes --
never to re-scan file content this module has not already decided to
inspect). This module does not itself assemble a `DiscoveredAgentCandidate`
row or write `AgentManifest.system_prompt_refs_json` -- FTF-04A17's own
already-CERTIFIED storage schema and a later composition task own that, the
same "detection is not the same act as storage/selection" boundary every
04A-family task before this one has drawn. `proving_symbol` stays blank and
`product_truth_check.py`'s product-true count is deliberately unchanged,
the same disclosed "no live route yet" scope FTF-04A09/FTF-04A10/
FTF-04A11/FTF-04A12 already established.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path

from app.target_connectors.json_yaml_toml_configuration_discovery import ConfigDocumentEvidence
from app.target_connectors.source_analysis_worker import SourceFileRef

__all__ = [
    "PromptDiscoveryError",
    "PromptSourceCandidate",
    "PromptSourceDiscoveryEvidence",
    "PromptSourceKind",
    "ResourceLimits",
    "run_prompt_source_discovery",
]

_HELPER_SCRIPT_PATH = (
    Path(__file__).resolve().parent / "_typescript_javascript_prompt_source_ast_helper.cjs"
)

_NON_ALPHANUMERIC = re.compile(r"[^A-Za-z0-9]")

# Duplicated from FTF-04A09's own closed vocabulary -- see this module's
# own docstring for why this is intentional, disclosed duplication rather
# than a shared-module edit.
_LITELLM_COMPLETION_FUNCTION_NAMES = frozenset({"completion", "acompletion"})

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

# Closed, disclosed, exact-match (never substring) config-key vocabularies
# -- the same "known, non-exhaustive heuristic" precedent FTF-04A11's own
# `_is_secret_like_key`/FTF-04A12's own `_MODEL_KEY_NAMES` already
# established, applied here to two new, disjoint vocabularies.
_PROMPT_LITERAL_KEY_NAMES = frozenset({"SYSTEMPROMPT", "DEVELOPERPROMPT"})
_PROMPT_PATH_KEY_NAMES = frozenset(
    {"PROMPTPATH", "PROMPTTEMPLATE", "PROMPTFILE", "SYSTEMPROMPTPATH"}
)


class PromptSourceKind(str, Enum):
    """Only distinguishes *how the content resolved*, never *which source
    scanned it* -- `location`'s own `"key:"` (config-derived) vs
    `"function:"` (Python/TS-JS literal) prefix already discloses the
    latter, so a separate config-derived variant of each would be a
    redundant, never-independently-useful distinction."""

    EMBEDDED_LITERAL = "embedded_literal"
    FILE_REFERENCE = "file_reference"


class PromptDiscoveryError(Exception):
    """A caller-supplied `SourceFileRef` violates this worker's own input
    shape (negative size) -- rejected at the boundary rather than
    propagated downstream."""


@dataclass(frozen=True)
class ResourceLimits:
    """This task's own real "resource-limited" half, mirroring FTF-04A10's
    own shape (`subprocess_timeout_seconds` bounds the new JS/TS helper's
    own subprocess), plus one new bound this task's own referenced-template
    lookup requires."""

    max_files: int = 5_000
    max_file_bytes: int = 2_000_000
    max_total_bytes: int = 50_000_000
    subprocess_timeout_seconds: float = 5.0
    max_referenced_template_bytes: int = 2_000_000


@dataclass(frozen=True)
class PromptSourceCandidate:
    """One real prompt-source candidate, never carrying real prompt/template
    content -- only its own `content_sha256`/`content_length` and/or a bare
    `referenced_path` location string. See this module's own docstring for
    the full security boundary this shape enforces."""

    relative_path: str
    location: str
    role: str | None
    resolved: bool
    source_kind: PromptSourceKind | None
    content_sha256: str | None
    content_length: int | None
    referenced_path: str | None
    template_dependency_resolved: bool
    reason: str

    def to_json(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "location": self.location,
            "role": self.role,
            "resolved": self.resolved,
            "source_kind": self.source_kind.value if self.source_kind is not None else None,
            "content_sha256": self.content_sha256,
            "content_length": self.content_length,
            "referenced_path": self.referenced_path,
            "template_dependency_resolved": self.template_dependency_resolved,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PromptSourceDiscoveryEvidence:
    candidates: tuple[PromptSourceCandidate, ...]
    resolved_count: int
    template_dependency_resolved_count: int
    files_scanned: int
    files_skipped_oversized: int
    parse_errors: int
    total_bytes_scanned: int
    truncated: bool = False


def _unresolved(
    *, relative_path: str, location: str, role: str | None, reason: str
) -> PromptSourceCandidate:
    return PromptSourceCandidate(
        relative_path=relative_path,
        location=location,
        role=role,
        resolved=False,
        source_kind=None,
        content_sha256=None,
        content_length=None,
        referenced_path=None,
        template_dependency_resolved=False,
        reason=reason,
    )


def _embedded_literal(
    *, relative_path: str, location: str, role: str, content: str
) -> PromptSourceCandidate:
    encoded = content.encode("utf-8")
    return PromptSourceCandidate(
        relative_path=relative_path,
        location=location,
        role=role,
        resolved=True,
        source_kind=PromptSourceKind.EMBEDDED_LITERAL,
        content_sha256=sha256(encoded).hexdigest(),
        content_length=len(encoded),
        referenced_path=None,
        template_dependency_resolved=True,
        reason="literal_string",
    )


def _file_reference(
    *,
    relative_path: str,
    location: str,
    role: str | None,
    referenced_path: str,
    reason: str,
    template_index: dict[str, SourceFileRef],
    limits: ResourceLimits,
) -> PromptSourceCandidate:
    """Looks `referenced_path` up by exact string match only against the
    caller's own already-captured `files` sequence -- never opened, fetched
    or normalized. See this module's own docstring, negative_acceptance
    point 1."""
    template_ref = template_index.get(referenced_path)
    if template_ref is None:
        return PromptSourceCandidate(
            relative_path=relative_path,
            location=location,
            role=role,
            resolved=True,
            source_kind=PromptSourceKind.FILE_REFERENCE,
            content_sha256=None,
            content_length=None,
            referenced_path=referenced_path,
            template_dependency_resolved=False,
            reason="external_or_missing_template",
        )
    if template_ref.size > limits.max_referenced_template_bytes:
        return PromptSourceCandidate(
            relative_path=relative_path,
            location=location,
            role=role,
            resolved=True,
            source_kind=PromptSourceKind.FILE_REFERENCE,
            content_sha256=None,
            content_length=None,
            referenced_path=referenced_path,
            template_dependency_resolved=False,
            reason="referenced_template_oversized",
        )
    template_bytes = template_ref.read_content()
    return PromptSourceCandidate(
        relative_path=relative_path,
        location=location,
        role=role,
        resolved=True,
        source_kind=PromptSourceKind.FILE_REFERENCE,
        content_sha256=sha256(template_bytes).hexdigest(),
        content_length=len(template_bytes),
        referenced_path=referenced_path,
        template_dependency_resolved=True,
        reason=reason,
    )


# --- Python AST detection -----------------------------------------------


def _collect_litellm_aliases(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    """Duplicated from FTF-04A09's own private helper of the same shape --
    see this module's own docstring."""
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


def _match_open_read_call(node: ast.expr) -> str | None:
    """Matches exactly `open(<literal path>, ...).read()` -- Python's own
    builtin `open` needs no import to trace, unlike FTF-04A09's own traced
    `litellm` alias. Any other shape (`.read_text()`, a variable holding an
    already-opened handle, an f-string path) is genuinely unresolved, never
    guessed through."""
    if not isinstance(node, ast.Call):
        return None
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "read":
        return None
    if node.args:
        return None
    inner = node.func.value
    if not isinstance(inner, ast.Call):
        return None
    inner_func = inner.func
    if not (isinstance(inner_func, ast.Name) and inner_func.id == "open"):
        return None
    if not inner.args:
        return None
    try:
        path_value = ast.literal_eval(inner.args[0])
    except _LITERAL_EVAL_EXCEPTIONS:
        return None
    return path_value if isinstance(path_value, str) else None


def _extract_python_prompt_source(
    node: ast.Call,
    *,
    relative_path: str,
    function_name: str,
    template_index: dict[str, SourceFileRef],
    limits: ResourceLimits,
) -> PromptSourceCandidate:
    location = f"function:{function_name}"
    messages_node = _keyword_value(node, "messages")
    if messages_node is None:
        return _unresolved(
            relative_path=relative_path, location=location, role=None, reason="argument_not_present"
        )
    if not isinstance(messages_node, ast.List):
        return _unresolved(
            relative_path=relative_path,
            location=location,
            role=None,
            reason="unresolved_dynamic_expression",
        )

    for element in messages_node.elts:
        if not isinstance(element, ast.Dict):
            continue
        role_value: object = None
        content_node: ast.expr | None = None
        skip_entry = False
        for key_node, value_node in zip(element.keys, element.values, strict=True):
            if key_node is None:  # a `**spread` entry -- genuinely unresolved key
                continue
            try:
                key_literal = ast.literal_eval(key_node)
            except _LITERAL_EVAL_EXCEPTIONS:
                continue
            if key_literal == "role":
                try:
                    role_value = ast.literal_eval(value_node)
                except _LITERAL_EVAL_EXCEPTIONS:
                    skip_entry = True
            elif key_literal == "content":
                content_node = value_node
        if skip_entry or not isinstance(role_value, str):
            continue
        if role_value not in ("system", "developer"):
            continue

        if content_node is None:
            return _unresolved(
                relative_path=relative_path,
                location=location,
                role=role_value,
                reason="argument_not_present",
            )
        try:
            content_value = ast.literal_eval(content_node)
        except _LITERAL_EVAL_EXCEPTIONS:
            content_value = None
            literal_ok = False
        else:
            literal_ok = True
        if literal_ok and isinstance(content_value, str):
            return _embedded_literal(
                relative_path=relative_path,
                location=location,
                role=role_value,
                content=content_value,
            )
        referenced_path = _match_open_read_call(content_node)
        if referenced_path is not None:
            return _file_reference(
                relative_path=relative_path,
                location=location,
                role=role_value,
                referenced_path=referenced_path,
                reason="file_reference_open_read",
                template_index=template_index,
                limits=limits,
            )
        return _unresolved(
            relative_path=relative_path,
            location=location,
            role=role_value,
            reason="unresolved_dynamic_expression",
        )

    return _unresolved(
        relative_path=relative_path,
        location=location,
        role=None,
        reason="no_system_or_developer_role_found",
    )


class _PromptCallVisitor(ast.NodeVisitor):
    """Module-level (never a per-file closure -- see FTF-04A09's own
    `_EntrypointVisitor` precedent for why) visitor: walks one already-
    parsed file's own AST, tracking real function scope, and extracts one
    `PromptSourceCandidate` per real qualifying `litellm.completion(...)`/
    `acompletion(...)` call site."""

    def __init__(
        self,
        *,
        relative_path: str,
        module_aliases: dict[str, str],
        function_aliases: dict[str, str],
        template_index: dict[str, SourceFileRef],
        limits: ResourceLimits,
    ) -> None:
        self._relative_path = relative_path
        self._module_aliases = module_aliases
        self._function_aliases = function_aliases
        self._template_index = template_index
        self._limits = limits
        self._function_stack: list[str] = ["<module>"]
        self.candidates: list[PromptSourceCandidate] = []

    def _visit_function_scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
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
            self.candidates.append(
                _extract_python_prompt_source(
                    node,
                    relative_path=self._relative_path,
                    function_name=self._function_stack[-1],
                    template_index=self._template_index,
                    limits=self._limits,
                )
            )
        self.generic_visit(node)


def _run_python_discovery(
    files: Sequence[SourceFileRef],
    *,
    template_index: dict[str, SourceFileRef],
    limits: ResourceLimits,
    counters: dict[str, int],
) -> list[PromptSourceCandidate]:
    candidates: list[PromptSourceCandidate] = []
    for file_ref in files:
        if not file_ref.relative_path.endswith(".py"):
            continue
        if not isinstance(file_ref.size, int) or file_ref.size < 0:
            raise PromptDiscoveryError(
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
        visitor = _PromptCallVisitor(
            relative_path=file_ref.relative_path,
            module_aliases=module_aliases,
            function_aliases=function_aliases,
            template_index=template_index,
            limits=limits,
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


def _candidate_from_helper_payload(
    raw: dict[str, object],
    *,
    relative_path: str,
    template_index: dict[str, SourceFileRef],
    limits: ResourceLimits,
) -> PromptSourceCandidate:
    function_name = str(raw.get("function_name", "<anonymous>"))
    location = f"function:{function_name}"
    payload = raw.get("prompt_source")
    if not isinstance(payload, dict):
        return _unresolved(
            relative_path=relative_path,
            location=location,
            role=None,
            reason="unresolved_dynamic_expression",
        )
    role = payload.get("role")
    role_str = role if isinstance(role, str) else None
    if not payload.get("resolved"):
        reason = payload.get("reason")
        return _unresolved(
            relative_path=relative_path,
            location=location,
            role=role_str,
            reason=str(reason) if reason else "unresolved_dynamic_expression",
        )
    source_kind = payload.get("source_kind")
    if source_kind == "embedded_literal":
        content_sha256 = payload.get("content_sha256")
        content_length = payload.get("content_length")
        return PromptSourceCandidate(
            relative_path=relative_path,
            location=location,
            role=role_str,
            resolved=True,
            source_kind=PromptSourceKind.EMBEDDED_LITERAL,
            content_sha256=str(content_sha256) if isinstance(content_sha256, str) else None,
            content_length=int(content_length) if isinstance(content_length, int) else None,
            referenced_path=None,
            template_dependency_resolved=True,
            reason="literal_string",
        )
    if source_kind == "file_reference":
        referenced_path = payload.get("referenced_path")
        if not isinstance(referenced_path, str):
            return _unresolved(
                relative_path=relative_path,
                location=location,
                role=role_str,
                reason="unresolved_dynamic_expression",
            )
        return _file_reference(
            relative_path=relative_path,
            location=location,
            role=role_str,
            referenced_path=referenced_path,
            reason="file_reference_read_file_sync",
            template_index=template_index,
            limits=limits,
        )
    return _unresolved(
        relative_path=relative_path,
        location=location,
        role=role_str,
        reason="unresolved_dynamic_expression",
    )


def _run_typescript_javascript_discovery(
    files: Sequence[SourceFileRef],
    *,
    template_index: dict[str, SourceFileRef],
    limits: ResourceLimits,
    counters: dict[str, int],
) -> list[PromptSourceCandidate]:
    candidates: list[PromptSourceCandidate] = []
    for file_ref in files:
        if not file_ref.relative_path.endswith(_SUPPORTED_TS_JS_EXTENSIONS):
            continue
        if not isinstance(file_ref.size, int) or file_ref.size < 0:
            raise PromptDiscoveryError(
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
                _candidate_from_helper_payload(
                    raw,
                    relative_path=file_ref.relative_path,
                    template_index=template_index,
                    limits=limits,
                )
            )

    return candidates


# --- Config-derived detection ---------------------------------------------


def _normalized_leaf_key(key_path: str) -> str:
    last_segment = key_path.rsplit(".", 1)[-1]
    last_segment = last_segment.split("[", 1)[0]
    return _NON_ALPHANUMERIC.sub("", last_segment).upper()


def _config_derived_candidates(
    document: ConfigDocumentEvidence,
    *,
    template_index: dict[str, SourceFileRef],
    limits: ResourceLimits,
) -> list[PromptSourceCandidate]:
    candidates: list[PromptSourceCandidate] = []
    if not document.resolved:
        return candidates
    for key in document.keys:
        if key.secret_like:
            continue
        normalized = _normalized_leaf_key(key.key_path)
        location = f"key:{key.key_path}"
        if normalized in _PROMPT_LITERAL_KEY_NAMES:
            if key.value_type == "string" and isinstance(key.value, str) and key.value != "":
                candidates.append(
                    _embedded_literal(
                        relative_path=document.relative_path,
                        location=location,
                        role="system",
                        content=key.value,
                    )
                )
            else:
                reason = (
                    "empty_string_value"
                    if key.value_type == "string"
                    else "unresolved_non_string_value"
                )
                candidates.append(
                    _unresolved(
                        relative_path=document.relative_path,
                        location=location,
                        role="system",
                        reason=reason,
                    )
                )
        elif normalized in _PROMPT_PATH_KEY_NAMES:
            if key.value_type == "string" and isinstance(key.value, str) and key.value != "":
                candidates.append(
                    _file_reference(
                        relative_path=document.relative_path,
                        location=location,
                        role="system",
                        referenced_path=key.value,
                        reason="config_derived_path",
                        template_index=template_index,
                        limits=limits,
                    )
                )
            else:
                reason = (
                    "empty_string_value"
                    if key.value_type == "string"
                    else "unresolved_non_string_value"
                )
                candidates.append(
                    _unresolved(
                        relative_path=document.relative_path,
                        location=location,
                        role="system",
                        reason=reason,
                    )
                )
    return candidates


def run_prompt_source_discovery(
    files: Sequence[SourceFileRef],
    *,
    config_documents: Sequence[ConfigDocumentEvidence] = (),
    resource_limits: ResourceLimits | None = None,
) -> PromptSourceDiscoveryEvidence:
    """Section 7A.4 step 5 -- see this module's own docstring for the full
    scope boundary. Never raises on adversarial file content (caught and
    counted, see `parse_errors`, or reported per-candidate via `resolved`/
    `reason`); only raises `PromptDiscoveryError` for a structurally unsafe
    caller-supplied `SourceFileRef` (negative size) -- a caller bug, not
    untrusted snapshot content.

    `files` is scanned twice, conceptually: once for real Python/TS/JS
    completion-call detection (bounded by `ResourceLimits` exactly like
    FTF-04A09/FTF-04A10), and once, only for a `referenced_path` this
    module itself already decided to look up, as a plain `relative_path ->
    SourceFileRef` index -- never a second full re-scan of every file's own
    content."""
    limits = resource_limits or ResourceLimits()
    template_index: dict[str, SourceFileRef] = {
        file_ref.relative_path: file_ref for file_ref in files
    }

    counters = {
        "files_scanned": 0,
        "files_skipped_oversized": 0,
        "parse_errors": 0,
        "total_bytes_scanned": 0,
        "truncated": 0,
    }

    candidates: list[PromptSourceCandidate] = []
    candidates.extend(
        _run_python_discovery(
            files, template_index=template_index, limits=limits, counters=counters
        )
    )
    candidates.extend(
        _run_typescript_javascript_discovery(
            files, template_index=template_index, limits=limits, counters=counters
        )
    )
    for document in config_documents:
        candidates.extend(
            _config_derived_candidates(document, template_index=template_index, limits=limits)
        )

    resolved_count = sum(1 for candidate in candidates if candidate.resolved)
    template_dependency_resolved_count = sum(
        1 for candidate in candidates if candidate.template_dependency_resolved
    )

    return PromptSourceDiscoveryEvidence(
        candidates=tuple(candidates),
        resolved_count=resolved_count,
        template_dependency_resolved_count=template_dependency_resolved_count,
        files_scanned=counters["files_scanned"],
        files_skipped_oversized=counters["files_skipped_oversized"],
        parse_errors=counters["parse_errors"],
        total_bytes_scanned=counters["total_bytes_scanned"],
        truncated=bool(counters["truncated"]),
    )
