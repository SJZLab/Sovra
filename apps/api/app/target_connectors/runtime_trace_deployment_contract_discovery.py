"""Runtime trace/deployment contract discovery (FTF-04A16, Section
7A.4/66/77/81).

Section 7A.4's own "Analysis order" names step 10 ("Detect runtime trace/log
interfaces") and step 11 ("Detect deployment/inference configuration") as
two of the twelve baseline discovery steps this task owns, together, for
one reason: both live in the same small closed set of deployment-manifest
and runtime-binding shapes a real target repository actually uses to wire a
process to its own observability/serving surface, and both share this
task's own single negative_acceptance guardrail below.

This task's own acceptance clause ("Identify trace/deployment configuration
and actual runtime-binding inputs without contacting arbitrary URLs") is
met by two real, disclosed, non-executing scan classes over an
already-captured, read-only source snapshot (the same caller-supplied
`Sequence[SourceFileRef]` shape FTF-04A08/FTF-04A09/FTF-04A10/FTF-04A11 all
already established -- a caller already holding that sequence for a prior
discovery pass can hand this worker the identical sequence with no
adaptation):

1. **Deployment manifest evidence** -- a closed, named allowlist of
   deployment-manifest basenames (`Dockerfile`/`Dockerfile.<suffix>`,
   `docker-compose.yml`/`.yaml`, `compose.yml`/`.yaml`, `Procfile`,
   `railway.json`/`.toml`, `fly.toml`, `render.yaml`, `vercel.json`) --
   never a guessed extension or directory-name heuristic (Section 7A.4's
   own closing sentence, "Unknown/custom frameworks are not guessed",
   applied here to deployment shapes). A JSON/YAML/TOML-shaped manifest
   (`compose`/`railway`/`fly`/`render`/`vercel`) is parsed by reusing
   FTF-04A11's own already-CERTIFIED
   `run_json_yaml_toml_configuration_discovery` directly (Section 77.1
   reuse-first) rather than re-implementing a second JSON/YAML/TOML parser;
   `Dockerfile`/`Procfile` are line-oriented text formats neither JSON,
   YAML nor TOML, so this task owns their own narrow, disclosed,
   non-executing line parsers (`_parse_dockerfile_lines`/
   `_parse_procfile_lines`) instead.
2. **Runtime-binding/trace reference evidence** -- a closed, named set of
   key-name markers (`_TRACE_OBSERVABILITY_MARKERS`/
   `_RUNTIME_BINDING_MARKERS` below) matched, case- and separator-
   insensitively, against: a real `os.environ.get(...)`/`os.getenv(...)`/
   `os.environ[...]` call site statically traced via `ast.parse` in Python
   source (reusing FTF-04A09's own established "only `ast.parse`, never
   `exec`/`eval`/`compile`/`importlib`/a subprocess" discipline exactly);
   a `process.env.NAME`/`process.env["NAME"]` textual reference in
   JavaScript/TypeScript source, matched via a disclosed, narrow regex
   scan rather than a full AST parse (unlike FTF-04A10's own real
   TypeScript-compiler-backed helper, this task does not add a second
   `.cjs` AST subprocess helper -- a known, disclosed limitation: this
   regex cannot distinguish a real code reference from the same textual
   pattern appearing inside a JS/TS comment or string literal, the same
   class of disclosed narrow-heuristic precedent `_is_secret_like_key`
   already established in FTF-04A11's own module); and a flattened
   config-document key path or a declared `ENV`/`ARG`/Procfile `$VAR`
   reference from scan class 1 above.

This task's own negative_acceptance ("Detected logs are not automatically
harvested and a string in config is not proof a production endpoint is
reachable") is met by two independent, structural facts, both proven by
this module's own test suite with a disclosed fault injection each:

- This worker never calls `open`, `socket`, `requests`, `httpx`, or any
  other filesystem/network primitive against a path or URL *string* it
  discovers inside scanned content -- the only I/O this worker ever
  performs is `file_ref.read_content()` against the caller-supplied
  `SourceFileRef` entries themselves. A detected `LOG_FILE=/var/log/app.log`
  or `OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318` value is recorded
  as evidence text only; this worker never opens the referenced log path
  and never issues a network request to the referenced endpoint.
- Every `RuntimeBindingReferenceEvidence` whose `literal_value` is
  URL-shaped (`http://`/`https://`) carries a fixed, non-optional
  `endpoint_reachability` marker, the literal string
  `"unverified_static_reference"` -- never `"reachable"`, never inferred
  from the string's own shape, and never upgraded by any later step in
  this module. A code-reference (`os.environ.get(...)`/`process.env.X`)
  has no literal value at all (this is static analysis, not a runtime
  probe), so its `literal_value`/`endpoint_reachability` are always `None`
  -- an honestly unresolved runtime value, never guessed.

Resource limits mirror FTF-04A08/FTF-04A09/FTF-04A11's own three-bound
shape (`max_files`/`max_file_bytes`/`max_total_bytes`) for the files this
module's own top-level scan considers; a JSON/YAML/TOML-shaped manifest
handed to the reused FTF-04A11 worker is parsed with that worker's own
default `ResourceLimits`, disclosed here rather than newly exposed on this
module's own caller-facing surface, since a deployment manifest is
expected to be small relative to the arbitrary application configuration
FTF-04A11 itself already bounds independently.

Input shape: reuses FTF-04A08's own connector-agnostic `SourceFileRef`
(`apps/api/app/target_connectors/source_analysis_worker.py`), the same
sequence FTF-04A09/FTF-04A10/FTF-04A11 already consume, so a caller that
already assembled one sequence for those workers can hand the identical
sequence to this one with no adaptation. This module does not re-implement
FTF-04A08's own ignore-directory/secret-path filtering itself -- the same
reliance FTF-04A09/FTF-04A10/FTF-04A11 already disclosed.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.target_connectors.json_yaml_toml_configuration_discovery import (
    run_json_yaml_toml_configuration_discovery,
)
from app.target_connectors.source_analysis_worker import SourceFileRef

__all__ = [
    "DeploymentManifestEvidence",
    "ResourceLimits",
    "RuntimeBindingReferenceEvidence",
    "RuntimeTraceDeploymentDiscoveryEvidence",
    "RuntimeTraceDeploymentDiscoveryError",
    "run_runtime_trace_deployment_contract_discovery",
]

# Disclosed, non-exhaustive key-name marker sets -- see this module's own
# docstring. Matched against a key's own alphanumeric-only, upper-cased
# form (the same normalization FTF-04A11's own `_is_secret_like_key`
# established), so "otel_exporter"/"OTEL-Exporter"/"otelExporter" all
# normalize and match identically. Checked in this fixed order (trace
# before runtime-binding) so a name matching both (e.g. "OTEL_HOST") is
# reported once, as the more specific trace/observability category.
_TRACE_OBSERVABILITY_MARKERS: tuple[str, ...] = (
    "OTEL",
    "TRACE",
    "TRACING",
    "SENTRY",
    "DSN",
    "TELEMETRY",
    "LOGLEVEL",
    "LOGFILE",
    "LOGPATH",
)

_RUNTIME_BINDING_MARKERS: tuple[str, ...] = (
    "PORT",
    "HOST",
    "ENDPOINT",
    "DEPLOY",
    "DEPLOYMENT",
    "RAILWAY",
    "BASEURL",
    "APIURL",
)

_SECRET_LIKE_MARKERS: tuple[str, ...] = (
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "TOKEN",
    "APIKEY",
    "PRIVATEKEY",
    "ACCESSKEY",
    "CREDENTIAL",
    "CLIENTSECRET",
    "PASSPHRASE",
)

_NON_ALPHANUMERIC = re.compile(r"[^A-Za-z0-9]")

_ENV_DECLARATION_PATTERN = re.compile(
    r"^\s*(?P<directive>ENV|ARG)\s+(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*(?:[=\s](?P<value>.*))?$"
)
_EXPOSE_PATTERN = re.compile(r"^\s*EXPOSE\s+(?P<ports>.+)$", re.IGNORECASE)
_PROCFILE_LINE_PATTERN = re.compile(r"^(?P<process_type>[A-Za-z0-9_-]+):\s*(?P<command>.*)$")
_PROCFILE_VAR_REFERENCE_PATTERN = re.compile(r"\$\{?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\}?")
_JS_PROCESS_ENV_DOT_PATTERN = re.compile(r"process\.env\.(?P<key>[A-Za-z_][A-Za-z0-9_]*)")
_JS_PROCESS_ENV_BRACKET_PATTERN = re.compile(
    r"""process\.env\[\s*['"](?P<key>[A-Za-z_][A-Za-z0-9_]*)['"]\s*\]"""
)

_PYTHON_EXTENSIONS: tuple[str, ...] = (".py",)
_JS_TS_EXTENSIONS: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

_UNVERIFIED_MARKER = "unverified_static_reference"


class RuntimeTraceDeploymentDiscoveryError(Exception):
    """A caller-supplied `SourceFileRef` violates this worker's own input
    shape (negative size) -- rejected at the boundary rather than
    propagated downstream."""


@dataclass(frozen=True)
class ResourceLimits:
    """This task's own real "resource-limited" negative_acceptance half --
    see this module's own docstring for what each bound independently
    protects against."""

    max_files: int = 5_000
    max_file_bytes: int = 2_000_000
    max_total_bytes: int = 50_000_000


@dataclass(frozen=True)
class DeploymentManifestEvidence:
    """One real deployment-manifest file recognized from this task's own
    closed allowlist. `declared_ports` are literal port specs found in the
    manifest's own structure (never a claim the port is actually bound at
    runtime)."""

    relative_path: str
    manifest_kind: str
    resolved: bool
    reason: str
    declared_ports: tuple[str, ...]

    def to_evidence_json(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "manifest_kind": self.manifest_kind,
            "resolved": self.resolved,
            "reason": self.reason,
            "declared_ports": list(self.declared_ports),
        }


@dataclass(frozen=True)
class RuntimeBindingReferenceEvidence:
    """One real runtime-binding or trace/observability key reference. See
    this module's own docstring for why `literal_value`/
    `endpoint_reachability` are `None` for a code-level reference (no
    runtime value is knowable via static analysis) and why a URL-shaped
    `literal_value` always carries the fixed `"unverified_static_reference"`
    marker, never a claim of reachability."""

    relative_path: str
    source_kind: str
    access_pattern: str
    key_name: str
    category: str
    line: int | None
    literal_value: str | None
    endpoint_reachability: str | None

    def to_evidence_json(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "source_kind": self.source_kind,
            "access_pattern": self.access_pattern,
            "key_name": self.key_name,
            "category": self.category,
            "line": self.line,
            "literal_value": self.literal_value,
            "endpoint_reachability": self.endpoint_reachability,
        }


@dataclass(frozen=True)
class RuntimeTraceDeploymentDiscoveryEvidence:
    manifests: tuple[DeploymentManifestEvidence, ...]
    binding_references: tuple[RuntimeBindingReferenceEvidence, ...]
    files_scanned: int
    files_skipped_oversized: int
    parse_errors: int
    total_bytes_scanned: int
    truncated: bool = False


def _redact(value: str) -> str:
    """Same disclosed redaction shape as FTF-04A11's own `_redact` --
    length only, never the real value."""
    return "<empty>" if not value else f"<redacted:{len(value)} chars>"


def _normalized(key: str) -> str:
    return _NON_ALPHANUMERIC.sub("", key).upper()


def _is_secret_like(key: str) -> bool:
    compact = _normalized(key)
    return any(marker in compact for marker in _SECRET_LIKE_MARKERS)


def _classify_marker(key: str) -> str | None:
    compact = _normalized(key)
    if any(marker in compact for marker in _TRACE_OBSERVABILITY_MARKERS):
        return "trace_observability"
    if any(marker in compact for marker in _RUNTIME_BINDING_MARKERS):
        return "runtime_binding"
    return None


def _endpoint_evidence_for(value: str | None) -> tuple[str | None, str | None]:
    """Returns `(literal_value, endpoint_reachability)`. A URL-shaped value
    is disclosed, redacted-if-secret-shaped, and always carries the fixed
    unverified marker -- this worker never contacts it (this task's own
    negative_acceptance)."""
    if value is None:
        return None, None
    is_url = value.startswith("http://") or value.startswith("https://")
    return value, (_UNVERIFIED_MARKER if is_url else None)


def _detect_manifest_kind(relative_path: str) -> str | None:
    basename = relative_path.rsplit("/", 1)[-1]
    if basename == "Dockerfile" or basename.startswith("Dockerfile."):
        return "dockerfile"
    if basename in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        return "compose"
    if basename == "Procfile":
        return "procfile"
    if basename == "railway.json":
        return "railway"
    if basename == "railway.toml":
        return "railway"
    if basename == "fly.toml":
        return "fly"
    if basename == "render.yaml":
        return "render"
    if basename == "vercel.json":
        return "vercel"
    return None


def _detect_source_language(relative_path: str) -> str | None:
    lower = relative_path.lower()
    if lower.endswith(_PYTHON_EXTENSIONS):
        return "python"
    if lower.endswith(_JS_TS_EXTENSIONS):
        return "javascript_typescript"
    return None


def _parse_dockerfile_lines(
    relative_path: str, text: str
) -> tuple[DeploymentManifestEvidence, list[RuntimeBindingReferenceEvidence]]:
    """A pure, non-executing line-oriented scan. Line continuations
    (trailing `\\`) are not joined -- a disclosed, bounded limitation, the
    same class already accepted for FTF-04A11's own dotenv parser not
    performing shell expansion."""
    ports: list[str] = []
    references: list[RuntimeBindingReferenceEvidence] = []

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        expose_match = _EXPOSE_PATTERN.match(raw_line)
        if expose_match is not None:
            for token in expose_match.group("ports").split():
                ports.append(token.split("/", 1)[0])
            continue

        env_match = _ENV_DECLARATION_PATTERN.match(raw_line)
        if env_match is None:
            continue
        key = env_match.group("key")
        category = _classify_marker(key)
        if category is None:
            continue
        raw_value = env_match.group("value")
        value = raw_value.strip() if raw_value is not None else None
        if value is not None and _is_secret_like(key):
            value = _redact(value)
        literal_value, reachability = _endpoint_evidence_for(value)
        directive = env_match.group("directive")
        references.append(
            RuntimeBindingReferenceEvidence(
                relative_path=relative_path,
                source_kind="dockerfile",
                access_pattern="docker_env_declaration"
                if directive == "ENV"
                else "docker_arg_declaration",
                key_name=key,
                category=category,
                line=line_number,
                literal_value=literal_value,
                endpoint_reachability=reachability,
            )
        )

    manifest = DeploymentManifestEvidence(
        relative_path=relative_path,
        manifest_kind="dockerfile",
        resolved=True,
        reason="parsed",
        declared_ports=tuple(ports),
    )
    return manifest, references


def _parse_procfile_lines(
    relative_path: str, text: str
) -> tuple[DeploymentManifestEvidence, list[RuntimeBindingReferenceEvidence]]:
    """A pure, non-executing line-oriented scan -- a process command string
    is never itself executed, only pattern-matched for `$VAR`/`${VAR}`
    references."""
    process_types: list[str] = []
    references: list[RuntimeBindingReferenceEvidence] = []

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PROCFILE_LINE_PATTERN.match(line)
        if match is None:
            continue
        process_types.append(match.group("process_type"))
        for var_match in _PROCFILE_VAR_REFERENCE_PATTERN.finditer(match.group("command")):
            key = var_match.group("key")
            category = _classify_marker(key)
            if category is None:
                continue
            references.append(
                RuntimeBindingReferenceEvidence(
                    relative_path=relative_path,
                    source_kind="procfile",
                    access_pattern="procfile_variable_reference",
                    key_name=key,
                    category=category,
                    line=line_number,
                    literal_value=None,
                    endpoint_reachability=None,
                )
            )

    manifest = DeploymentManifestEvidence(
        relative_path=relative_path,
        manifest_kind="procfile",
        resolved=True,
        reason="parsed",
        declared_ports=(),
    )
    return manifest, references


def _leaf_key_and_parent(key_path: str) -> tuple[str, str | None]:
    segments = [segment.split("[", 1)[0] for segment in key_path.split(".") if segment]
    if not segments:
        return key_path, None
    leaf = segments[-1]
    parent = segments[-2] if len(segments) >= 2 else None
    return leaf, parent


def _parse_kv_string(value: str) -> tuple[str, str] | None:
    if "=" not in value:
        return None
    key, _, rest = value.partition("=")
    key = key.strip()
    if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        return None
    return key, rest.strip()


def _scan_config_manifest(
    file_ref: SourceFileRef, manifest_kind: str
) -> tuple[DeploymentManifestEvidence, list[RuntimeBindingReferenceEvidence]]:
    """Reuses FTF-04A11's own already-CERTIFIED JSON/YAML/TOML worker
    directly (Section 77.1 reuse-first) rather than re-implementing a
    second parser for a JSON/YAML/TOML-shaped deployment manifest."""
    config_evidence = run_json_yaml_toml_configuration_discovery([file_ref])
    if not config_evidence.documents:
        manifest = DeploymentManifestEvidence(
            relative_path=file_ref.relative_path,
            manifest_kind=manifest_kind,
            resolved=False,
            reason="not_a_recognized_config_format",
            declared_ports=(),
        )
        return manifest, []

    document = config_evidence.documents[0]
    if not document.resolved:
        manifest = DeploymentManifestEvidence(
            relative_path=file_ref.relative_path,
            manifest_kind=manifest_kind,
            resolved=False,
            reason=document.reason,
            declared_ports=(),
        )
        return manifest, []

    ports: list[str] = []
    references: list[RuntimeBindingReferenceEvidence] = []

    for key_evidence in document.keys:
        leaf, parent = _leaf_key_and_parent(key_evidence.key_path)
        candidate_key: str
        candidate_value: str | None

        if leaf in ("ports", "expose") and key_evidence.value is not None:
            ports.append(str(key_evidence.value))
            continue

        if leaf in ("environment", "env") and isinstance(key_evidence.value, str):
            # List-form `environment: ["KEY=VALUE", ...]` -- the flattened
            # leaf is the array's own key name ("environment"), so the real
            # variable name/value pair lives inside the string value itself.
            parsed_kv = _parse_kv_string(key_evidence.value)
            if parsed_kv is None:
                continue
            candidate_key, candidate_value = parsed_kv
        elif parent in ("environment", "env"):
            # Dict-form `environment: {KEY: VALUE}` -- the flattened leaf
            # already is the real variable name.
            candidate_key, candidate_value = (
                leaf,
                (str(key_evidence.value) if key_evidence.value is not None else None),
            )
        else:
            candidate_key, candidate_value = (
                leaf,
                (str(key_evidence.value) if isinstance(key_evidence.value, str) else None),
            )

        category = _classify_marker(candidate_key)
        if category is None:
            continue

        value = candidate_value
        if value is not None and not key_evidence.secret_like and _is_secret_like(candidate_key):
            value = _redact(value)
        literal_value, reachability = _endpoint_evidence_for(value)
        references.append(
            RuntimeBindingReferenceEvidence(
                relative_path=file_ref.relative_path,
                source_kind="config_document",
                access_pattern="config_key",
                key_name=candidate_key,
                category=category,
                line=None,
                literal_value=literal_value,
                endpoint_reachability=reachability,
            )
        )

    manifest = DeploymentManifestEvidence(
        relative_path=file_ref.relative_path,
        manifest_kind=manifest_kind,
        resolved=True,
        reason="parsed",
        declared_ports=tuple(ports),
    )
    return manifest, references


def _is_os_environ_get_call(func: ast.expr) -> bool:
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "get"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "environ"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "os"
    )


def _is_os_getenv_call(func: ast.expr) -> bool:
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "getenv"
        and isinstance(func.value, ast.Name)
        and func.value.id == "os"
    )


def _python_env_call_key(node: ast.Call) -> str | None:
    """Statically traces `os.environ.get(...)`/`os.getenv(...)` -- only
    `ast.parse` is ever used, never `exec`/`eval`/`compile`/`importlib`."""
    if not (_is_os_environ_get_call(node.func) or _is_os_getenv_call(node.func)):
        return None
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _python_env_subscript_key(node: ast.Subscript) -> str | None:
    if not (
        isinstance(node.value, ast.Attribute)
        and node.value.attr == "environ"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "os"
    ):
        return None
    slice_node = node.slice
    if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
        return slice_node.value
    value_attr = getattr(slice_node, "value", None)
    if isinstance(value_attr, ast.Constant) and isinstance(value_attr.value, str):
        return value_attr.value
    return None


def _scan_python_source(relative_path: str, text: str) -> list[RuntimeBindingReferenceEvidence]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []

    references: list[RuntimeBindingReferenceEvidence] = []
    for node in ast.walk(tree):
        key: str | None
        access_pattern: str
        if isinstance(node, ast.Call):
            key = _python_env_call_key(node)
            if key is None:
                continue
            func = node.func
            access_pattern = (
                "os.getenv"
                if isinstance(func, ast.Attribute) and func.attr == "getenv"
                else "os.environ.get"
            )
        elif isinstance(node, ast.Subscript):
            key = _python_env_subscript_key(node)
            if key is None:
                continue
            access_pattern = "os.environ[]"
        else:
            continue

        category = _classify_marker(key)
        if category is None:
            continue
        references.append(
            RuntimeBindingReferenceEvidence(
                relative_path=relative_path,
                source_kind="python",
                access_pattern=access_pattern,
                key_name=key,
                category=category,
                line=getattr(node, "lineno", None),
                literal_value=None,
                endpoint_reachability=None,
            )
        )
    return references


def _scan_js_ts_source(relative_path: str, text: str) -> list[RuntimeBindingReferenceEvidence]:
    """A disclosed, narrow regex scan -- see this module's own docstring
    for why this cannot distinguish a real code reference from the same
    textual pattern inside a comment or string literal."""
    references: list[RuntimeBindingReferenceEvidence] = []
    for pattern, access_pattern in (
        (_JS_PROCESS_ENV_DOT_PATTERN, "process.env.NAME"),
        (_JS_PROCESS_ENV_BRACKET_PATTERN, "process.env[NAME]"),
    ):
        for match in pattern.finditer(text):
            key = match.group("key")
            category = _classify_marker(key)
            if category is None:
                continue
            line = text.count("\n", 0, match.start()) + 1
            references.append(
                RuntimeBindingReferenceEvidence(
                    relative_path=relative_path,
                    source_kind="javascript_typescript",
                    access_pattern=access_pattern,
                    key_name=key,
                    category=category,
                    line=line,
                    literal_value=None,
                    endpoint_reachability=None,
                )
            )
    return references


def run_runtime_trace_deployment_contract_discovery(
    files: Sequence[SourceFileRef], *, resource_limits: ResourceLimits | None = None
) -> RuntimeTraceDeploymentDiscoveryEvidence:
    """Section 7A.4's own "Detect runtime trace/log interfaces" (step 10)
    and "Detect deployment/inference configuration" (step 11) baseline
    steps -- see this module's own docstring for the full scope boundary.
    Never raises on adversarial scanned content (a malformed Python/JS
    file or config document is caught and disclosed per-file, never
    propagated); only raises `RuntimeTraceDeploymentDiscoveryError` for a
    structurally unsafe caller-supplied `SourceFileRef` itself (negative
    size) -- a caller bug, not untrusted snapshot content."""
    limits = resource_limits or ResourceLimits()

    manifests: list[DeploymentManifestEvidence] = []
    references: list[RuntimeBindingReferenceEvidence] = []
    files_scanned = 0
    files_skipped_oversized = 0
    parse_errors = 0
    total_bytes_scanned = 0
    truncated = False

    for file_ref in files:
        manifest_kind = _detect_manifest_kind(file_ref.relative_path)
        language = _detect_source_language(file_ref.relative_path)
        if manifest_kind is None and language is None:
            continue

        if not isinstance(file_ref.size, int) or file_ref.size < 0:
            raise RuntimeTraceDeploymentDiscoveryError(
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

        if manifest_kind in ("compose", "railway", "fly", "render", "vercel"):
            content = file_ref.read_content()
            files_scanned += 1
            total_bytes_scanned += len(content)
            manifest, refs = _scan_config_manifest(file_ref, manifest_kind)
            manifests.append(manifest)
            references.extend(refs)
            if not manifest.resolved:
                parse_errors += 1
            continue

        content = file_ref.read_content()
        files_scanned += 1
        total_bytes_scanned += len(content)

        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            parse_errors += 1
            if manifest_kind is not None:
                manifests.append(
                    DeploymentManifestEvidence(
                        relative_path=file_ref.relative_path,
                        manifest_kind=manifest_kind,
                        resolved=False,
                        reason="not_utf8_text",
                        declared_ports=(),
                    )
                )
            continue

        if manifest_kind == "dockerfile":
            manifest, refs = _parse_dockerfile_lines(file_ref.relative_path, text)
            manifests.append(manifest)
            references.extend(refs)
        elif manifest_kind == "procfile":
            manifest, refs = _parse_procfile_lines(file_ref.relative_path, text)
            manifests.append(manifest)
            references.extend(refs)
        elif language == "python":
            references.extend(_scan_python_source(file_ref.relative_path, text))
        elif language == "javascript_typescript":
            references.extend(_scan_js_ts_source(file_ref.relative_path, text))

    return RuntimeTraceDeploymentDiscoveryEvidence(
        manifests=tuple(manifests),
        binding_references=tuple(references),
        files_scanned=files_scanned,
        files_skipped_oversized=files_skipped_oversized,
        parse_errors=parse_errors,
        total_bytes_scanned=total_bytes_scanned,
        truncated=truncated,
    )
