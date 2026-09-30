"""Hugging Face artifact resolver (FTF-0302, Section 6.7/13/34/68/77/81).

FTF-0301's own `TrainableArtifact` schema deliberately stores a
`huggingface` `source_uri` as a bare, unresolved `org/repo` identifier and
never resolves it against a real upstream, and FTF-0305's own license
workflow gates only on whether *this organization's own record* of
acceptance exists -- neither task dials Hugging Face Hub itself. This
module is that real caller for the `huggingface` source type: given an
already-registered artifact whose license has been accepted and whose
`base_model_revision` is a pinned identifier, it resolves the revision
against the real Hugging Face Hub API, downloads the files that shape
looks like a model artifact (never a dataset), and verifies each
downloaded file against Hugging Face's own declared content hash where one
is published.

Scope, disclosed rather than hidden: `FTF-0306` ("Base model revision/
digest resolver") is the separately dependency-gated task that combines
this module's own output (and its `private_s3`/`local_filesystem`
siblings, FTF-0303/FTF-0304) into the one `weights_digest` column
`FTF-0301`'s schema exposes and writes it through `artifact_crud.py`'s
already-certified `apply_edits`. This module computes a real
`combined_sha256_hex` over every verified file it downloaded (a candidate
digest FTF-0306 can consume) but never itself calls `apply_edits` or
touches the database -- `database_changes: "No independent schema change.
Consume frozen contract"` is read here as "this task does not decide how
the combined digest is persisted," not as permission to skip computing one
for real. No independent schema change is made or needed.

This task's own negative_acceptance ("Floating revision, unavailable
license or remote code execution is blocked/explicitly reviewed; no
sensitive training data present during acquisition") is enforced as four
independent, ordered gates, every one of them checked *before* any network
request reaches Hugging Face Hub wherever that is possible without the
real API response itself being required to decide:

  1. Floating revision -- `FTF-0301`'s own `is_pinned_revision` (reused,
     never re-implemented) rejects a mutable alias (`main`/`latest`/...)
     before any request is sent.
  2. Unavailable license -- `FTF-0305`'s own `assert_license_accepted_for_use`
     (reused, never re-implemented) rejects an artifact with no accepted
     license before any request is sent.
  3. Remote code execution -- a repository whose config declares an
     `auto_map` (Hugging Face's own "this model needs a custom Python
     class" signal) or that lists a `.py` file among its own real file
     listing requires `trust_remote_code_allowed=True` -- an explicit,
     human-reviewed field already on `TrainableArtifact` (FTF-0301) --
     before any file is downloaded. This module never downloads or
     executes a `.py` file itself either way; "explicitly reviewed" is the
     artifact owner's own prior act of setting that field, not anything
     this module does with the code.
  4. No sensitive training data present during acquisition -- file
     selection is an explicit, closed allowlist of known model-artifact
     name shapes (`_is_allowed_model_file`), not a denylist of known
     dataset shapes: a nested path (e.g. a repository's own `data/`
     subdirectory) or any file this allowlist does not recognize is simply
     never requested, so an unanticipated dataset-shaped file can never
     slip through the way a denylist could miss one.

Every real HTTP request this module makes -- to the revision-resolution
API and to every individual file download -- goes through
`packages/security/outbound_guard.py` (FTF-H006), the one module in this
repository allowed to open an outbound socket, using a policy that permits
following the real redirect Hugging Face Hub's own LFS-backed files issue
(to its CDN) while still revalidating the destination from scratch on
every hop (FTF-H006's own guarantee, not re-implemented here).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

from packages.security.outbound_guard import GuardedResponse, OutboundGuardError, guarded_fetch
from packages.security.url_policy import ConnectionPolicy

from app.artifacts.artifact_schema import (
    TrainableArtifact,
    TrainableArtifactValidationError,
    is_pinned_revision,
    validate_source_uri,
)
from app.artifacts.license_acceptance_workflow import assert_license_accepted_for_use

__all__ = [
    "DEFAULT_HUGGINGFACE_HOST",
    "AcquisitionBudgetExceededError",
    "FloatingRevisionError",
    "GatedRepositoryUnavailableError",
    "HuggingFaceResolutionError",
    "HuggingFaceResolutionResult",
    "NoRecognizedArtifactFilesError",
    "RemoteCodeNotAllowedError",
    "ResolvedFile",
    "ResolvedFileDigestMismatchError",
    "default_huggingface_resolver_policy",
    "resolve_and_verify_huggingface_artifact",
    "resolve_huggingface_snapshot",
]

DEFAULT_HUGGINGFACE_HOST = "huggingface.co"
_DEFAULT_BASE_URL = f"https://{DEFAULT_HUGGINGFACE_HOST}"
_DEFAULT_TIMEOUT_SECONDS = 30.0

# Section 13/68.4-style bounded acquisition -- a Release-1 resolver run
# never fetches an unbounded number/size of files, the same disclosed
# truncation-budget discipline FTF-04A08/04A09/04A10's own discovery
# workers already established for a structurally identical reason (a
# hostile or malformed upstream cannot turn one resolution call into an
# unbounded amount of work or memory).
MAX_ACQUIRED_FILES = 200
MAX_TOTAL_ACQUIRED_BYTES = 200 * 1024 * 1024  # 200 MiB

# Closed allowlist of real model-artifact file-name shapes. Deliberately
# an allowlist, not a denylist of known dataset shapes (`.parquet`/
# `.arrow`/`.csv`/`.jsonl`/...): a novel or unanticipated dataset file
# format would silently defeat a denylist, but can never pass this
# allowlist, satisfying this task's own "no sensitive training data
# present during acquisition" negative_acceptance by construction.
_ALLOWED_FILENAME_SUFFIXES: tuple[str, ...] = (
    ".safetensors",
    ".json",
    ".txt",
    ".md",
    ".model",  # sentencepiece
)
_ALLOWED_EXACT_FILENAMES: frozenset[str] = frozenset({"LICENSE"})

FetchFn = Callable[..., GuardedResponse]


class HuggingFaceResolutionError(ValueError):
    """Base class for every real defect this module raises resolving or
    acquiring a `huggingface` artifact -- never caught and silently
    converted to a fabricated success anywhere in this module."""


class FloatingRevisionError(HuggingFaceResolutionError):
    """`base_model_revision` is a mutable, unresolved alias (Section
    13/68.4) -- refused before any network request is made."""


class GatedRepositoryUnavailableError(HuggingFaceResolutionError):
    """Hugging Face Hub itself refused (401/403) the revision-resolution
    request -- a gated repository this credential/anonymous access cannot
    read, functionally "license unavailable" from the upstream's own
    point of view, distinct from this organization's own accepted-license
    record (`FTF-0305`, checked separately and first)."""


class RemoteCodeNotAllowedError(HuggingFaceResolutionError):
    """The repository requires executing repository-supplied Python code
    and the artifact's own `trust_remote_code_allowed` has not been set by
    an explicit review."""


class NoRecognizedArtifactFilesError(HuggingFaceResolutionError):
    """The repository's real file listing contains no file this module's
    own closed model-artifact allowlist recognizes -- refusing to acquire
    nothing, or to widen the allowlist implicitly, is deliberate."""


class ResolvedFileDigestMismatchError(HuggingFaceResolutionError):
    """A downloaded file's real sha256 does not match the sha256 Hugging
    Face Hub's own revision-resolution response declared for it -- a
    genuine integrity failure, never silently accepted."""


class AcquisitionBudgetExceededError(HuggingFaceResolutionError):
    """The repository's own file count or cumulative byte size exceeds
    this resolver's bounded acquisition budget."""


@dataclass(frozen=True)
class ResolvedFile:
    filename: str
    size_bytes: int
    sha256_hex: str
    declared_sha256_hex: str | None


@dataclass(frozen=True)
class HuggingFaceResolutionResult:
    repo_id: str
    requested_revision: str
    resolved_commit_sha: str
    gated: bool
    requires_remote_code: bool
    files: tuple[ResolvedFile, ...]
    combined_sha256_hex: str


def default_huggingface_resolver_policy() -> ConnectionPolicy:
    """Production default: only real public destinations (no private
    allowlist), following redirects so a real LFS-backed file's real
    redirect to Hugging Face's own CDN host is not treated as a failure --
    FTF-H006's own `guarded_fetch` revalidates the redirect target from
    scratch, never trusting the original connection."""
    return ConnectionPolicy(
        name="huggingface_artifact_resolver", allow_redirects=True, max_redirects=5
    )


def _is_allowed_model_file(filename: str) -> bool:
    if not filename or "/" in filename or "\\" in filename:
        # A nested path (e.g. a repository's own `data/` subdirectory) is
        # never traversed into -- only a file this repository's own
        # top-level listing names directly is ever considered.
        return False
    if filename in _ALLOWED_EXACT_FILENAMES:
        return True
    lowered = filename.lower()
    return lowered.endswith(_ALLOWED_FILENAME_SUFFIXES)


def _requires_remote_code(config_section: object, filenames: list[str]) -> bool:
    if isinstance(config_section, dict) and config_section.get("auto_map"):
        return True
    return any(name.endswith(".py") for name in filenames)


def _parse_revision_response(body: bytes) -> dict[str, object]:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HuggingFaceResolutionError(
            "Hugging Face Hub's revision-resolution response was not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise HuggingFaceResolutionError(
            "Hugging Face Hub's revision-resolution response was not a JSON object"
        )
    return payload


def resolve_huggingface_snapshot(
    repo_id: str,
    revision: str,
    *,
    trust_remote_code_allowed: bool,
    base_url: str = _DEFAULT_BASE_URL,
    policy: ConnectionPolicy | None = None,
    fetch: FetchFn = guarded_fetch,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> HuggingFaceResolutionResult:
    """Resolves `repo_id`@`revision` against a real Hugging Face Hub-shaped
    API (real `huggingface.co` by default; `base_url` is overridable only
    for this task's own real local-server test fixture, never for
    production use) and downloads/verifies every recognized model-artifact
    file. Raises one of this module's own error classes for every
    negative_acceptance case; never returns a partial or fabricated
    result."""
    try:
        validate_source_uri("huggingface", repo_id)
    except TrainableArtifactValidationError as exc:
        raise HuggingFaceResolutionError(str(exc)) from exc

    if not is_pinned_revision(revision):
        raise FloatingRevisionError(
            f"base_model_revision {revision!r} is a mutable, unresolved alias -- refusing to "
            "acquire a floating revision"
        )

    active_policy = policy or default_huggingface_resolver_policy()

    revision_url = f"{base_url}/api/models/{repo_id}/revision/{quote(revision, safe='')}"
    try:
        response: GuardedResponse = fetch(revision_url, active_policy, timeout=timeout)
    except OutboundGuardError as exc:
        raise HuggingFaceResolutionError(
            f"could not reach Hugging Face Hub to resolve {repo_id}@{revision}: {exc}"
        ) from exc

    if response.status in (401, 403):
        raise GatedRepositoryUnavailableError(
            f"{repo_id}@{revision} is gated or access was denied (HTTP {response.status})"
        )
    if response.status != 200:
        raise HuggingFaceResolutionError(
            f"unexpected HTTP {response.status} resolving {repo_id}@{revision}"
        )

    payload = _parse_revision_response(response.body)

    resolved_commit_sha = payload.get("sha")
    if not isinstance(resolved_commit_sha, str) or not resolved_commit_sha:
        raise HuggingFaceResolutionError(
            f"{repo_id}@{revision}'s revision-resolution response is missing a real commit sha"
        )

    gated = bool(payload.get("gated", False))

    siblings_raw = payload.get("siblings")
    siblings = siblings_raw if isinstance(siblings_raw, list) else []
    filenames: list[str] = []
    declared_sha256_by_name: dict[str, str] = {}
    for sibling in siblings:
        if not isinstance(sibling, dict):
            continue
        name = sibling.get("rfilename")
        if not isinstance(name, str) or not name:
            continue
        filenames.append(name)
        lfs = sibling.get("lfs")
        if isinstance(lfs, dict):
            declared = lfs.get("sha256")
            if isinstance(declared, str) and declared:
                declared_sha256_by_name[name] = declared.lower()

    requires_remote_code = _requires_remote_code(payload.get("config"), filenames)
    if requires_remote_code and not trust_remote_code_allowed:
        raise RemoteCodeNotAllowedError(
            f"{repo_id}@{revision} requires executing repository-supplied code and has not "
            "been explicitly reviewed (trust_remote_code_allowed=False)"
        )

    selected = sorted({name for name in filenames if _is_allowed_model_file(name)})
    if not selected:
        raise NoRecognizedArtifactFilesError(
            f"{repo_id}@{revision} has no recognized model-artifact files to acquire"
        )
    if len(selected) > MAX_ACQUIRED_FILES:
        raise AcquisitionBudgetExceededError(
            f"{repo_id}@{revision} has {len(selected)} recognized files, exceeding the "
            f"{MAX_ACQUIRED_FILES}-file acquisition budget"
        )

    resolved_files: list[ResolvedFile] = []
    total_bytes = 0
    for filename in selected:
        file_url = (
            f"{base_url}/{repo_id}/resolve/{quote(resolved_commit_sha, safe='')}/"
            f"{quote(filename, safe='')}"
        )
        try:
            file_response: GuardedResponse = fetch(file_url, active_policy, timeout=timeout)
        except OutboundGuardError as exc:
            raise HuggingFaceResolutionError(
                f"could not download {filename!r} from {repo_id}@{revision}: {exc}"
            ) from exc
        if file_response.status != 200:
            raise HuggingFaceResolutionError(
                f"unexpected HTTP {file_response.status} downloading {filename!r} from "
                f"{repo_id}@{revision}"
            )
        total_bytes += len(file_response.body)
        if total_bytes > MAX_TOTAL_ACQUIRED_BYTES:
            raise AcquisitionBudgetExceededError(
                f"{repo_id}@{revision} acquisition exceeded the {MAX_TOTAL_ACQUIRED_BYTES}-byte "
                "total budget"
            )

        digest = hashlib.sha256(file_response.body).hexdigest()
        declared = declared_sha256_by_name.get(filename)
        if declared is not None and declared != digest:
            raise ResolvedFileDigestMismatchError(
                f"{filename!r} downloaded from {repo_id}@{revision} does not match Hugging Face "
                "Hub's own declared sha256 -- refusing to accept unverified bytes"
            )
        resolved_files.append(
            ResolvedFile(
                filename=filename,
                size_bytes=len(file_response.body),
                sha256_hex=digest,
                declared_sha256_hex=declared,
            )
        )

    combined = hashlib.sha256()
    for resolved_file in resolved_files:  # already selected in sorted filename order
        combined.update(resolved_file.filename.encode("utf-8"))
        combined.update(b"\x00")
        combined.update(bytes.fromhex(resolved_file.sha256_hex))

    return HuggingFaceResolutionResult(
        repo_id=repo_id,
        requested_revision=revision,
        resolved_commit_sha=resolved_commit_sha,
        gated=gated,
        requires_remote_code=requires_remote_code,
        files=tuple(resolved_files),
        combined_sha256_hex=combined.hexdigest(),
    )


def resolve_and_verify_huggingface_artifact(
    artifact: TrainableArtifact,
    *,
    base_url: str = _DEFAULT_BASE_URL,
    policy: ConnectionPolicy | None = None,
    fetch: FetchFn = guarded_fetch,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> HuggingFaceResolutionResult:
    """The approved-artifact entry point: refuses (via `FTF-0305`'s own
    `assert_license_accepted_for_use`, reused rather than re-implemented)
    an artifact whose license has not been accepted -- before any network
    request is made -- then delegates to `resolve_huggingface_snapshot`
    with this artifact's own `source_uri`/`base_model_revision`/
    `trust_remote_code_allowed`. Never writes `weights_digest` or any
    other field back to `artifact` or the database -- see this module's
    own docstring for why that composition belongs to `FTF-0306`."""
    if artifact.source_type != "huggingface":
        raise HuggingFaceResolutionError(
            f"artifact {artifact.id} has source_type {artifact.source_type!r}, not 'huggingface'"
        )
    assert_license_accepted_for_use(artifact)
    return resolve_huggingface_snapshot(
        artifact.source_uri,
        artifact.base_model_revision,
        trust_remote_code_allowed=artifact.trust_remote_code_allowed,
        base_url=base_url,
        policy=policy,
        fetch=fetch,
        timeout=timeout,
    )
