"""Source-analysis worker foundation (FTF-04A08, Section 7A.4/9/66/77/81).

Section 7A.4's own numbered "Analysis order" names twelve steps; this task
owns exactly the first two, the real foundation every later per-category
detector (FTF-04A09 onward, none built or claimed here) will run on top
of: "1. Detect languages/package managers/framework markers." and "2.
Parse repository tree and ignore generated/vendor/secret paths using
explicit rules." It deliberately does not detect entrypoints, model
configuration, prompts, tool schemas, structured-output schemas,
authorization boundaries, tests, trace hooks, or deployment configuration
(Section 7A.4 steps 3-11), and it does not produce a `DiscoveredAgentCandidate`
row (step 12, FTF-04A17's own already-CERTIFIED storage schema) -- claiming
any of that here without a real backing implementation would be exactly
the "placeholder endpoint" Law 11 forbids.

Path refined from the task packet's own generic suggestion
(`packages/target-connectors/source_analysis_worker_foundation.py`) to
`apps/api/app/target_connectors/source_analysis_worker.py`, matching
FTF-04A01's/FTF-04A06's/FTF-04A07's/FTF-04A17's own real
`apps/api/app/target_connectors/*.py` layout precedent exactly.

Connector-agnostic by design: Section 7A.1's own `TargetApplicationConnector`
Protocol has two very different real backends (a local filesystem walk,
FTF-04A06; a future GitHub API tree/content read, FTF-04A02 -- excluded
this shift, a keyed external credential/ask-first item). Rather than
coupling this worker to either one's own I/O shape, it accepts an already
-enumerated, already-bounded-by-size sequence of `SourceFileRef` entries
(a relative path, a real byte size known up front, and a lazy content
reader) -- the same "path/size known before any byte is read" shape
FTF-04A06's own `ManifestEntry` already established, generalized so a
future GitHub-backed caller can supply the identical shape without this
worker ever knowing which connector produced it. This task's own
`database_changes` clause ("No independent schema change. Consume frozen
contract.") and lack of any DB/network import here reflects that: this is
a pure, synchronous, no-I/O-of-its-own computation over caller-supplied
data, the same tractable shape already established by
`workers/data/pii_secrets_detector.py` and its dedup/validator siblings.

Section 7A.4's own literal sentence, quoted in full, is this task's own
real acceptance/negative_acceptance boundary: "Discovery parses data
without importing Python modules, executing JavaScript, running package
installers, Git hooks, builds or instructions inside README/AGENTS/
prompts. Source content is untrusted evidence, not authority over the
coding agent or Factory." This module never calls `exec`/`eval`/`compile`,
never imports a scanned file as a Python module (`importlib`), and never
spawns a subprocess/shell against scanned content -- the only parsing this
module performs on manifest file content is `json.loads` (a declarative,
non-executing parser) for `package.json` and `tomllib.loads` (likewise
declarative, stdlib, non-executing) for `pyproject.toml`, both wrapped so
a malformed/adversarial manifest cannot raise an unhandled exception out
of this worker (this task's own "malicious parser input cannot escape
worker" negative_acceptance half) -- see `_detect_framework_markers_in_manifest`.

Resource-limiting (this task's own real "resource-limited static
analyzer" acceptance half) is enforced at three independent points, all
disclosed here rather than only in the code: `max_total_entries` bounds
the raw iteration itself (an attacker cannot exhaust worker time merely by
supplying an unbounded number of *ignored* entries -- an ignored path is
still one loop iteration); `max_files` bounds how many *included* (i.e.
not ignored) files this run will actually record/classify; `max_file_bytes`
skips reading any single manifest file larger than that bound without
ever calling its own `read_content` (so a maliciously huge single file is
never loaded into memory at all); `max_total_bytes` stops reading further
manifest content once the cumulative budget for this run is spent. Every
limit that stops the run before it reached the end of the caller's own
sequence sets `truncated=True` on the returned evidence -- an honestly
incomplete analysis is disclosed, never silently reported as if it were
complete (the same "detection absence is not proof of X" honesty
discipline `pii_secrets_detector.py`'s own `RESIDUAL_RISK_DISCLOSURE`
already established for a structurally identical reason).

Ignore rules (Section 7A.4 step 2's own "ignore generated/vendor/secret
paths using explicit rules") are a closed, explicit set --
`IGNORED_DIRECTORY_SEGMENTS` (checked against every path segment, not a
substring match a crafted path like `mynode_modules_but_not_really/`
could defeat) and `IGNORED_SECRET_BASENAMES`/`IGNORED_SECRET_SUFFIXES`
(env files, private-key-shaped files, common credential-dump filenames) --
never a heuristic guess.

Framework-marker detection (Section 7A.4 step 1's own "framework markers")
is deliberately narrow: only a fixed, named set of dependency-name
literals (`_PY_FRAMEWORK_DEPENDENCY_NAMES`/`_JS_FRAMEWORK_DEPENDENCY_NAMES`)
found inside a real, successfully-parsed manifest file's own declared
dependency list -- Section 7A.4's own closing sentence, "Unknown/custom
frameworks are not guessed," is this task's own real negative reading:
this worker never infers a framework from file extensions, directory
names, or any signal other than a literal, named dependency string
appearing in a real manifest.

Path-shape validation on every `SourceFileRef.relative_path` (non-empty,
POSIX-relative, no `..`/absolute segment, no duplicate path within one
run) is real defense-in-depth, not merely trusting the caller: this
worker has no filesystem of its own to escape, but a malformed path is
exactly the kind of "malicious parser input" a future filesystem-writing
consumer of this worker's own evidence could be tricked by if it were
allowed through silently -- rejected outright here instead
(`SourceAnalysisWorkerError`), the same "reject at the boundary, do not
propagate an unsafe shape downstream" precedent `local_repository_connector.py`'s
own `resolve_allowed_root`/`_iter_repository_files` already established
for a structurally identical class of input.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

__all__ = [
    "IGNORED_DIRECTORY_SEGMENTS",
    "IGNORED_SECRET_BASENAMES",
    "IGNORED_SECRET_SUFFIXES",
    "LANGUAGE_EXTENSIONS",
    "PACKAGE_MANAGER_MARKER_BASENAMES",
    "IgnoredPathRecord",
    "ResourceLimits",
    "SourceAnalysisEvidence",
    "SourceAnalysisWorkerError",
    "SourceFileRef",
    "run_source_analysis",
]

# Section 7A.4 step 2's own "ignore generated/vendor... paths" -- checked
# against every real path segment (never a substring match).
IGNORED_DIRECTORY_SEGMENTS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".next",
        "target",
        "vendor",
        "site-packages",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "coverage",
        ".turbo",
        ".cache",
        ".tox",
    }
)

# Section 7A.4 step 2's own "ignore... secret paths" -- a closed, explicit
# filename/suffix set, never a heuristic guess.
IGNORED_SECRET_BASENAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        ".env.test",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "credentials.json",
        ".npmrc",
        ".netrc",
    }
)
IGNORED_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")

LANGUAGE_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}

PACKAGE_MANAGER_MARKER_BASENAMES: dict[str, str] = {
    "requirements.txt": "pip",
    "poetry.lock": "poetry",
    "uv.lock": "uv",
    "package-lock.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
}

_MANIFEST_BASENAMES_FOR_FRAMEWORK_DETECTION = frozenset(
    {"package.json", "pyproject.toml", "requirements.txt"}
)

# Section 7A.4 step 1's own "framework markers" -- a closed, named set.
# Never expanded by inference (Section 7A.4's own "Unknown/custom
# frameworks are not guessed").
_PY_FRAMEWORK_DEPENDENCY_NAMES: dict[str, str] = {
    "fastapi": "fastapi",
    "django": "django",
    "flask": "flask",
}
_JS_FRAMEWORK_DEPENDENCY_NAMES: dict[str, str] = {
    "express": "express",
    "next": "next",
    "nestjs": "@nestjs/core",
}

_DEPENDENCY_SPEC_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._@/-]+")

# json.loads/tomllib.loads on adversarial content can raise any of these
# on real, malformed/deeply-nested input -- every one is caught, none is
# allowed to escape this worker (this task's own "malicious parser input
# cannot escape worker" negative_acceptance half).
_MANIFEST_PARSE_EXCEPTIONS: tuple[type[Exception], ...] = (
    json.JSONDecodeError,
    tomllib.TOMLDecodeError,
    UnicodeDecodeError,
    RecursionError,
    ValueError,
)


class SourceAnalysisWorkerError(Exception):
    """A caller-supplied `SourceFileRef` violates this worker's own input
    shape (duplicate path, unsafe path, negative size) -- rejected at the
    boundary rather than propagated downstream."""


@dataclass(frozen=True)
class SourceFileRef:
    """One real file in an already-captured, read-only source snapshot --
    connector-agnostic (see this module's own docstring). `size` is known
    without reading `read_content`, so this worker can enforce
    `ResourceLimits.max_file_bytes` without ever loading an oversized
    file's bytes."""

    relative_path: str
    size: int
    read_content: Callable[[], bytes]


@dataclass(frozen=True)
class ResourceLimits:
    """This task's own real "resource-limited" acceptance half -- see this
    module's own docstring for what each bound independently protects
    against."""

    max_total_entries: int = 200_000
    max_files: int = 20_000
    max_file_bytes: int = 2_000_000
    max_total_bytes: int = 50_000_000


@dataclass(frozen=True)
class IgnoredPathRecord:
    relative_path: str
    reason: str


@dataclass(frozen=True)
class SourceAnalysisEvidence:
    """This task's own real "evidence output schema" acceptance half --
    the base, connector-agnostic evidence every later per-category
    detector (FTF-04A09 onward) will read alongside, never a stand-in for
    any of Section 7A.4 steps 3-11."""

    included_paths: tuple[str, ...]
    ignored_paths: tuple[IgnoredPathRecord, ...]
    detected_languages: tuple[str, ...]
    detected_package_managers: tuple[str, ...]
    detected_framework_markers: tuple[str, ...]
    total_entries_examined: int
    files_scanned: int
    files_skipped_oversized: int
    total_bytes_scanned: int
    manifest_parse_errors: int
    truncated: bool = field(default=False)


def _validate_relative_path(relative_path: object) -> str:
    if not isinstance(relative_path, str) or not relative_path:
        raise SourceAnalysisWorkerError(
            f"relative_path must be a real, non-empty string: {relative_path!r}"
        )
    if relative_path.startswith("/") or relative_path.startswith("\\"):
        raise SourceAnalysisWorkerError(f"relative_path must not be absolute: {relative_path!r}")
    segments = relative_path.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise SourceAnalysisWorkerError(
            f"relative_path must not contain an empty, '.', or '..' segment: {relative_path!r}"
        )
    return relative_path


def _ignore_reason(relative_path: str) -> str | None:
    segments = relative_path.split("/")
    for segment in segments[:-1]:
        if segment in IGNORED_DIRECTORY_SEGMENTS:
            return f"generated_or_vendor_directory:{segment}"

    basename = segments[-1]
    if basename in IGNORED_SECRET_BASENAMES or basename.endswith(IGNORED_SECRET_SUFFIXES):
        return "secret_path"
    return None


def _language_for_path(relative_path: str) -> str | None:
    for suffix, language in LANGUAGE_EXTENSIONS.items():
        if relative_path.endswith(suffix):
            return language
    return None


def _package_manager_for_path(relative_path: str) -> str | None:
    basename = relative_path.rsplit("/", 1)[-1]
    return PACKAGE_MANAGER_MARKER_BASENAMES.get(basename)


def _dependency_spec_name(spec: str) -> str:
    match = _DEPENDENCY_SPEC_NAME_PATTERN.match(spec.strip())
    return match.group(0).lower() if match else ""


def _detect_framework_markers_in_manifest(basename: str, text: str) -> frozenset[str]:
    """Real, declarative-only parsing (`json.loads`/`tomllib.loads`) --
    never `exec`/`eval`/an import of the manifest's own content. Every
    exception `_MANIFEST_PARSE_EXCEPTIONS` names is the caller's own
    responsibility to catch (see `run_source_analysis`); this function
    itself lets a genuine parse failure propagate so the caller can count
    it, matching this repo's own "never silently swallow inside the
    function that detects the failure" convention."""
    if basename == "package.json":
        data = json.loads(text)
        dependency_names: set[str] = set()
        if isinstance(data, dict):
            for section_key in ("dependencies", "devDependencies"):
                section = data.get(section_key)
                if isinstance(section, dict):
                    dependency_names.update(str(name) for name in section)
        return frozenset(
            marker
            for marker, dependency_name in _JS_FRAMEWORK_DEPENDENCY_NAMES.items()
            if dependency_name in dependency_names
        )

    if basename == "pyproject.toml":
        data = tomllib.loads(text)
        dependency_names = set()
        project_section = data.get("project") if isinstance(data, dict) else None
        project_deps = (
            project_section.get("dependencies") if isinstance(project_section, dict) else None
        )
        if isinstance(project_deps, list):
            dependency_names.update(
                _dependency_spec_name(dep) for dep in project_deps if isinstance(dep, str)
            )
        tool_section = data.get("tool") if isinstance(data, dict) else None
        poetry_section = tool_section.get("poetry") if isinstance(tool_section, dict) else None
        poetry_deps = (
            poetry_section.get("dependencies") if isinstance(poetry_section, dict) else None
        )
        if isinstance(poetry_deps, dict):
            dependency_names.update(str(name).lower() for name in poetry_deps)
        return frozenset(
            marker
            for marker, dependency_name in _PY_FRAMEWORK_DEPENDENCY_NAMES.items()
            if dependency_name in dependency_names
        )

    if basename == "requirements.txt":
        dependency_names = set()
        for line in text.splitlines():
            spec = line.strip()
            if not spec or spec.startswith("#") or spec.startswith("-"):
                continue
            dependency_names.add(_dependency_spec_name(spec))
        return frozenset(
            marker
            for marker, dependency_name in _PY_FRAMEWORK_DEPENDENCY_NAMES.items()
            if dependency_name in dependency_names
        )

    return frozenset()


def run_source_analysis(
    files: Sequence[SourceFileRef], *, resource_limits: ResourceLimits | None = None
) -> SourceAnalysisEvidence:
    """Section 7A.4 steps 1-2 only -- see this module's own docstring for
    the full scope boundary. Never raises on adversarial manifest content
    (caught and counted, see `manifest_parse_errors`); only raises
    `SourceAnalysisWorkerError` for a structurally unsafe caller-supplied
    `SourceFileRef` itself (empty/absolute/traversal path, duplicate path,
    negative size) -- a caller bug, not untrusted snapshot content."""
    limits = resource_limits or ResourceLimits()

    seen_paths: set[str] = set()
    included: list[str] = []
    ignored: list[IgnoredPathRecord] = []
    detected_languages: set[str] = set()
    detected_package_managers: set[str] = set()
    detected_framework_markers: set[str] = set()

    total_entries_examined = 0
    files_scanned = 0
    files_skipped_oversized = 0
    total_bytes_scanned = 0
    manifest_parse_errors = 0
    truncated = False

    for file_ref in files:
        if total_entries_examined >= limits.max_total_entries:
            truncated = True
            break
        total_entries_examined += 1

        relative_path = _validate_relative_path(file_ref.relative_path)
        if relative_path in seen_paths:
            raise SourceAnalysisWorkerError(
                f"duplicate relative_path in one run: {relative_path!r}"
            )
        seen_paths.add(relative_path)
        if not isinstance(file_ref.size, int) or file_ref.size < 0:
            raise SourceAnalysisWorkerError(
                f"size must be a real, non-negative integer for "
                f"{relative_path!r}: {file_ref.size!r}"
            )

        reason = _ignore_reason(relative_path)
        if reason is not None:
            ignored.append(IgnoredPathRecord(relative_path, reason))
            continue

        if files_scanned >= limits.max_files:
            truncated = True
            break
        files_scanned += 1
        included.append(relative_path)

        language = _language_for_path(relative_path)
        if language is not None:
            detected_languages.add(language)

        package_manager = _package_manager_for_path(relative_path)
        if package_manager is not None:
            detected_package_managers.add(package_manager)

        basename = relative_path.rsplit("/", 1)[-1]
        if basename not in _MANIFEST_BASENAMES_FOR_FRAMEWORK_DETECTION:
            continue

        if file_ref.size > limits.max_file_bytes:
            files_skipped_oversized += 1
            continue
        if total_bytes_scanned + file_ref.size > limits.max_total_bytes:
            truncated = True
            continue

        content = file_ref.read_content()
        total_bytes_scanned += len(content)
        text = content.decode("utf-8", errors="replace")

        try:
            markers = _detect_framework_markers_in_manifest(basename, text)
        except _MANIFEST_PARSE_EXCEPTIONS:
            manifest_parse_errors += 1
            continue
        detected_framework_markers.update(markers)

    return SourceAnalysisEvidence(
        included_paths=tuple(sorted(included)),
        ignored_paths=tuple(ignored),
        detected_languages=tuple(sorted(detected_languages)),
        detected_package_managers=tuple(sorted(detected_package_managers)),
        detected_framework_markers=tuple(sorted(detected_framework_markers)),
        total_entries_examined=total_entries_examined,
        files_scanned=files_scanned,
        files_skipped_oversized=files_skipped_oversized,
        total_bytes_scanned=total_bytes_scanned,
        manifest_parse_errors=manifest_parse_errors,
        truncated=truncated,
    )
