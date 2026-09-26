"""TypeScript/JavaScript deterministic agent/config/tool discovery
(FTF-04A10, Section 7A.4/9/66/77/81).

The direct JS/TS sibling of FTF-04A09's own Python-only worker
(`python_agent_config_tool_discovery.py`) -- read that module's own
docstring first; this one repeats only what genuinely differs. Section
7A.4's own numbered "Analysis order" names twelve steps; this task owns
the same slice FTF-04A09 already claimed for Python -- steps 3 ("Detect
probable agent entrypoints"), 4 ("Detect model configuration and LiteLLM
aliases") and 6 ("Detect tool/function schemas and adapters") -- for
JS/TS source only. It deliberately does not detect prompt sources (step
5), structured output schemas (step 7), authorization/confirmation
boundaries (step 8), test/eval fixtures (step 9), trace/log interfaces
(step 10), deployment configuration (step 11), or assemble a
`DiscoveredAgentCandidate` row (step 12, FTF-04A17's own already-CERTIFIED
storage schema).

Path refined from the task packet's own generic suggestion
(`packages/target-connectors/typescript_javascript_deterministic_agent_config_tool_discovery.py`)
to `apps/api/app/target_connectors/...`, matching FTF-04A08's/FTF-04A09's
own real layout precedent exactly (that same packet-vs-real-layout
refinement is already disclosed in FTF-04A08's own module docstring).

**"Isolated approved parser" resolution (CLAUDE.md's own "new runtime
deps" ask-first gate, resolved without asking):** this repo's root
`package.json` already declares `typescript` (`^5.6.2`) as a real,
already-installed devDependency of the whole monorepo (used today for
`apps/web`'s own build/typecheck) -- not a new dependency this task
introduces. `_typescript_javascript_ast_helper.cjs`, a small, fixed,
repo-owned script (never derived from scanned content), is invoked as a
subprocess to call that already-approved compiler's own
`ts.createSourceFile`/`ts.Program.getSyntacticDiagnostics` -- both pure,
non-executing syntax operations, the direct JS/TS analog of `ast.parse`.
Section 7A.4's own literal sentence ("Discovery parses data without
importing Python modules, executing JavaScript, running package
installers...") is honored exactly: this module (and its own subprocess
helper) never calls `eval`/`new Function`/`vm.runInContext`/`require` on
scanned content, never runs `npm install` or any build/package-manager
command, and never imports a scanned file as a real module -- the helper
script's own source is read directly by `test_module_never_imports_or_executes_scanned_content`
to prove this statically, the same discipline FTF-04A09's own sibling test
already established for the Python worker's `ast.literal_eval`-only claim.

**Model-binding call shape, resolved without asking (same CLAUDE.md
default-resolution rule):** unlike Python, no native `litellm` npm package
exists for this repo to trace; Section 7A.4's own required-capability list
names "OpenAI-compatible/LiteLLM model references" and Section 7A.8/`api_contract`
across this plan's own JS-adjacent tasks anchor on the real `/v1/chat/completions`
REST shape this repo's LiteLLM gateway actually serves -- the JS/TS
ecosystem's own standard client for that exact shape is the `openai` npm
package's `OpenAI` class, whose `client.chat.completions.create(...)` call
is the literal, statically-traceable JS/TS analog of Python's
`litellm.completion(...)`. Exactly three closed, non-guessed import/
instantiation shapes are recognized -- `import OpenAI from "openai"`,
`import { OpenAI [as X] } from "openai"`, and `const X = require("openai")`,
each followed by `const <client> = new <alias>(...)` -- mirroring
FTF-04A09's own three Python import shapes in count and structure. A
namespace import (`import * as ns from "openai"`), a dynamically-computed
`require(expr)`, or any client reference that is not a plain identifier
traced back to one of these three shapes is a genuinely different,
unresolved shape this module does not guess through -- the same
"unknown/custom is not guessed" discipline FTF-04A09 already applied.

Model binding: the call's own single object-literal argument's `model`
property (only ever a literal string; a computed key, a shorthand
variable reference, a spread, or any non-string literal is honestly
`unresolved_dynamic_expression`, never guessed) is resolved by the helper
script's own non-executing literal evaluator -- the direct JS/TS analog of
`ast.literal_eval`. Tool schema: the same call's `tools` property,
resolved only when it is a literal array whose every element is a plain
object shaped exactly like an OpenAI-compatible function-calling tool
entry (`{"type": "function", "function": {"name": "<str>", ...}}`); one
non-conforming element marks the *entire* list unresolved, never silently
dropped -- both mirroring FTF-04A09's own extraction rules exactly.

Agent entrypoint: the *enclosing function* (a `function` declaration/
expression, an arrow function, or a class/object method; `"<module>"` for
a call at module scope) containing a qualifying call is the "probable
agent entrypoint". Two qualifying calls in two different functions of the
same file yield two distinct `EntrypointCandidate` entries, never
coalesced into one -- the same discipline FTF-04A09 already applied.

A file that fails to parse (a genuine TypeScript syntactic diagnostic, a
thrown exception from the helper's own compiler calls, a subprocess
timeout, a non-JSON/malformed helper response, or a non-zero helper exit)
is caught and counted, never allowed to escape this worker -- this task's
own "malicious parser input cannot escape worker" negative_acceptance
half. The Node.js runtime itself being unavailable is a genuinely
different, environment-level precondition failure (Law 17 -- "Provider
failure/outage/unknown state stays visible as blocked... never converted
to success") and is raised immediately as
`TypeScriptJavaScriptDiscoveryError` rather than silently laundered into a
per-file `parse_errors` count.

Resource limits (`ResourceLimits`) mirror FTF-04A09's own shape
(`max_files`/`max_file_bytes`/`max_total_bytes`) plus one genuinely new
bound this task's own subprocess-based design requires:
`subprocess_timeout_seconds` bounds how long any single file's own helper
invocation may run, so a pathological input cannot hang this worker
indefinitely -- a timeout is counted as `parse_errors`, the same
"malicious input cannot escape worker" half, not a budget-truncation.

Input shape: reuses FTF-04A08's own connector-agnostic `SourceFileRef`
(`apps/api/app/target_connectors/source_analysis_worker.py`), the same
"path/size known before any byte is read" contract FTF-04A09 already
reused. Only entries whose `relative_path` ends in `.ts`, `.tsx`, `.js`,
or `.jsx` are ever considered -- this module does not re-implement
FTF-04A08's own ignore-directory/secret-path rules itself, the same
relied-upon-not-rederived boundary FTF-04A09 already disclosed.

Output evidence shape: each `EntrypointCandidate`'s own
`to_entrypoint_evidence_json`/`to_model_evidence_json`/`to_tool_evidence_json`
produce a plain JSON object shaped for direct storage in FTF-04A17's own
independently-nullable `entrypoint_evidence_json`/`model_evidence_json`/
`tool_evidence_json` columns -- this module does not itself call
FTF-04A17's CRUD or assemble a `DiscoveredAgentCandidate` row.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.target_connectors.source_analysis_worker import SourceFileRef

__all__ = [
    "EntrypointCandidate",
    "ModelBindingEvidence",
    "ResourceLimits",
    "ToolSchemaEvidence",
    "TypeScriptJavaScriptDiscoveryError",
    "TypeScriptJavaScriptDiscoveryEvidence",
    "run_typescript_javascript_agent_config_tool_discovery",
]

_HELPER_SCRIPT_PATH = Path(__file__).resolve().parent / "_typescript_javascript_ast_helper.cjs"

# A closed, non-guessed extension set -- Section 7A.4's own "TypeScript/
# JavaScript AST/config search" minimum capability, never expanded by
# inference to e.g. `.mjs`/`.cjs`/`.vue`.
_SUPPORTED_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")


class TypeScriptJavaScriptDiscoveryError(Exception):
    """A caller-supplied `SourceFileRef` violates this worker's own input
    shape (negative size), or the Node.js runtime this worker depends on
    is genuinely unavailable -- either way, a precondition violated at the
    boundary rather than laundered into a per-file parse error."""


@dataclass(frozen=True)
class ResourceLimits:
    """This task's own real "resource-limited" half -- see this module's
    own docstring for what each bound independently protects against."""

    max_files: int = 5_000
    max_file_bytes: int = 2_000_000
    max_total_bytes: int = 50_000_000
    subprocess_timeout_seconds: float = 5.0


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
    """Section 7A.4 step 3 -- one real, statically-verified OpenAI-
    compatible completion call site, and the model/tool evidence tied to
    it."""

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
class TypeScriptJavaScriptDiscoveryEvidence:
    """This task's own real "evidence output schema" half -- see this
    module's own docstring for the full scope boundary."""

    entrypoints: tuple[EntrypointCandidate, ...]
    files_scanned: int
    files_skipped_oversized: int
    parse_errors: int
    total_bytes_scanned: int
    truncated: bool = False


def _model_binding_from_json(payload: dict[str, object]) -> ModelBindingEvidence:
    resolved = payload.get("resolved")
    model_alias = payload.get("model_alias")
    reason = payload.get("reason")
    return ModelBindingEvidence(
        resolved=bool(resolved),
        model_alias=model_alias if isinstance(model_alias, str) else None,
        reason=str(reason),
    )


def _tool_schema_from_json(payload: dict[str, object]) -> ToolSchemaEvidence:
    resolved = payload.get("resolved")
    tool_names = payload.get("tool_names")
    reason = payload.get("reason")
    names = tuple(tool_names) if isinstance(tool_names, list) else ()
    return ToolSchemaEvidence(resolved=bool(resolved), tool_names=names, reason=str(reason))


def _run_helper(relative_path: str, source_text: str, timeout_seconds: float) -> dict[str, object]:
    """Invokes the fixed, repo-owned Node.js helper as a subprocess over
    exactly this one file's already-decoded source text, passed via stdin
    (never a shell-interpolated argument, never written to a temp file the
    scanned content itself could influence the path of). Every failure
    path -- a non-zero exit, a timeout, malformed stdout -- is reported
    back as a genuine parse error, never allowed to raise past this
    function except when the Node.js runtime itself cannot be found."""
    node_path = shutil.which("node")
    if node_path is None:
        raise TypeScriptJavaScriptDiscoveryError(
            "the 'node' runtime is not available on PATH -- this worker's own "
            "isolated approved parser cannot run"
        )
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


def run_typescript_javascript_agent_config_tool_discovery(
    files: Sequence[SourceFileRef], *, resource_limits: ResourceLimits | None = None
) -> TypeScriptJavaScriptDiscoveryEvidence:
    """Section 7A.4 steps 3/4/6, JS/TS only -- see this module's own
    docstring for the full scope boundary. Never raises on adversarial
    JS/TS source content (caught and counted, see `parse_errors`); raises
    `TypeScriptJavaScriptDiscoveryError` only for a structurally unsafe
    caller-supplied `SourceFileRef` (negative size) or a genuinely
    unavailable Node.js runtime -- a caller/environment precondition, not
    untrusted snapshot content."""
    limits = resource_limits or ResourceLimits()

    entrypoints: list[EntrypointCandidate] = []
    files_scanned = 0
    files_skipped_oversized = 0
    parse_errors = 0
    total_bytes_scanned = 0
    truncated = False

    for file_ref in files:
        if not file_ref.relative_path.endswith(_SUPPORTED_EXTENSIONS):
            continue
        if not isinstance(file_ref.size, int) or file_ref.size < 0:
            raise TypeScriptJavaScriptDiscoveryError(
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
        except UnicodeDecodeError:
            parse_errors += 1
            continue

        response = _run_helper(
            file_ref.relative_path, source_text, limits.subprocess_timeout_seconds
        )
        if not response.get("ok"):
            parse_errors += 1
            continue

        raw_entrypoints = response.get("entrypoints")
        if not isinstance(raw_entrypoints, list):
            parse_errors += 1
            continue

        for raw in raw_entrypoints:
            if not isinstance(raw, dict):
                continue
            model_binding_raw = raw.get("model_binding")
            tool_schema_raw = raw.get("tool_schema")
            entrypoints.append(
                EntrypointCandidate(
                    relative_path=str(raw.get("relative_path")),
                    line=int(raw.get("line", 0)),
                    function_name=str(raw.get("function_name")),
                    call_attribute=str(raw.get("call_attribute")),
                    model_binding=_model_binding_from_json(
                        model_binding_raw if isinstance(model_binding_raw, dict) else {}
                    ),
                    tool_schema=_tool_schema_from_json(
                        tool_schema_raw if isinstance(tool_schema_raw, dict) else {}
                    ),
                )
            )

    return TypeScriptJavaScriptDiscoveryEvidence(
        entrypoints=tuple(entrypoints),
        files_scanned=files_scanned,
        files_skipped_oversized=files_skipped_oversized,
        parse_errors=parse_errors,
        total_bytes_scanned=total_bytes_scanned,
        truncated=truncated,
    )
