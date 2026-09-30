"""GitHub source snapshot capture: real commit tree/file-hash resolution
(FTF-04A05, Section 6.8B/7A.1/7A.2/77/81).

FTF-04A04's own module docstring (`github_branch_ref_resolver.py`) and
`docs/spec-matrix.yaml`'s own FTF-04A04 entry both name this task
explicitly: FTF-04A04 durably records a project's *branch selection*
(`TargetApplicationConnection.selected_branch`); this task is "the
downstream task that captures an immutable TargetSourceSnapshot
(tree_hash/capture_method/archive) and durably records the resolved
commit onto it." This module is the real connector that produces those
fields for a GitHub-sourced commit -- it never persists a row itself (the
same disclosed "connector returns a capture value, a separate composing
CRUD layer persists it" scope boundary `local_repository_connector.py`
(FTF-04A06) already established for its own sibling connector).

Reuses `github_branch_ref_resolver.resolve_ref_to_commit` (FTF-04A04,
CERTIFIED) for the actual branch/tag/ref -> immutable commit SHA step,
rather than re-implementing it -- this module picks up from an
already-resolved commit SHA and adds exactly the three further real
GitHub REST API v3 calls Section 7A.2's own "enumerate repository tree at
that SHA" / "capture repository metadata required for provenance"
capabilities need:

  - `GET /repos/{owner}/{repo}/git/commits/{sha}` -- GitHub's own real Git
    Commit object, whose `tree.sha` *is* the real, canonical Git tree
    object id for that commit (identical to what a local `git rev-parse
    {sha}^{tree}` would report) -- never independently recomputed, so
    `tree_hash` is always a real, externally-verifiable Git object id.
  - `GET /repos/{owner}/{repo}/git/trees/{tree_sha}?recursive=1` -- the
    real, full recursive tree listing. GitHub's own documented `truncated`
    response field is this task's own real "truncated listing completion"
    signal (Section 6.8B's own acceptance clause 1): a `truncated: true`
    response means GitHub itself could not return every object, so this
    module never claims a complete capture from it -- see
    `IncompleteSourceTreeError` below (negative_acceptance: "Missing
    critical objects ... block ... rather than proceed with partial
    evidence").
  - `GET /repos/{owner}/{repo}/git/blobs/{sha}` -- used exactly once per
    capture, only for a root-level `.gitattributes` blob when present, to
    detect real Git LFS filter declarations (Section 6.8B's own "LFS ...
    completion" recording). This module does not fetch every blob's
    content (a full recursive-tree capture can have tens of thousands of
    entries); LFS *tracking* is real evidence from the repository's own
    declared filter rules, not a guess.

Submodules (Section 6.8B's own "recording ... submodules ... completion")
are a real, structural signal already present in the tree listing itself:
a Git tree entry of `type: "commit"` *is* a submodule reference (a
"gitlink" pointing at another repository's own pinned commit) -- no
further network call is needed to detect one.

"Unsafe archive" (this task's own other negative_acceptance half) is read
here as: no tree entry may describe a path that could escape the
repository root if ever materialized onto a real filesystem (an absolute
path, a `..` path segment, or an embedded NUL byte) -- the same real,
zip-slip-shaped defense `local_repository_connector.py`'s own real-path
symlink check already established for its own (filesystem-sourced) walk,
adapted here to GitHub's own (API-sourced) tree entries. A real GitHub
repository's own tree can never legitimately contain such a path (Git's
own tree-object grammar forbids it); a response that claims one is either
a GitHub-side defect or a compromised/spoofed upstream, and this module
refuses to build a snapshot from it either way -- `UnsafeSourceTreeEntryError`.
This module does not download or extract a real tarball/zipball archive:
`default_github_connector_policy()` (FTF-04A02, reused unmodified) sets
`allow_redirects=False`, and GitHub's own real `.../tarball/{ref}` /
`.../zipball/{ref}` endpoints always 302-redirect to a separate
`codeload.github.com` host -- silently following that redirect would be
exactly the kind of un-pinned, policy-widening jump Section 65.4's own
outbound guard exists to prevent, and adding `codeload.github.com` to the
allowed destination set is a real, separate policy change this task's own
`api_contract`/`database_changes` scope does not authorize. The tree-entry
safety check above is this module's own real, fully-tested "unsafe
archive" gate instead.

Every real HTTP request goes through `packages/security/outbound_guard.py`
(FTF-H006), identical to every sibling GitHub connector in this chain.
"""

from __future__ import annotations

import base64
import fnmatch
import json
from collections.abc import Callable
from dataclasses import dataclass

from packages.security.outbound_guard import GuardedResponse, OutboundGuardError, guarded_fetch
from packages.security.url_policy import ConnectionPolicy

from app.target_connectors.github_app_token_read_only_secret_integration import (
    DEFAULT_GITHUB_API_BASE_URL,
    GitHubConnectionError,
    GitHubCredentialRevokedError,
    GitHubRateLimitExceededError,
    GitHubRepositoryAccessDeniedError,
    default_github_connector_policy,
)
from app.target_connectors.github_branch_ref_resolver import (
    InvalidRefError,
    InvalidRepositoryIdentityError,
    resolve_ref_to_commit,
)

__all__ = [
    "CAPTURE_METHOD",
    "GITATTRIBUTES_PATH",
    "GitHubSourceCapture",
    "GitHubTreeEntry",
    "IncompleteSourceTreeError",
    "InvalidRefError",
    "InvalidRepositoryIdentityError",
    "MissingCommitTreeError",
    "UnsafeSourceTreeEntryError",
    "capture_github_source_snapshot",
    "fetch_commit_tree_sha",
    "list_tree_recursive",
]

_DEFAULT_TIMEOUT_SECONDS = 30.0
_GITHUB_API_VERSION = "2022-11-28"
_HTTP_METHOD = "GET"

CAPTURE_METHOD = "github_resolved_commit"
GITATTRIBUTES_PATH = ".gitattributes"

GuardedFetchFn = Callable[..., GuardedResponse]


class MissingCommitTreeError(GitHubConnectionError):
    """GitHub's own real commit response did not carry a usable `tree.sha`
    -- never fabricated or left blank."""


class IncompleteSourceTreeError(GitHubConnectionError):
    """GitHub's own real recursive tree listing reported `truncated: true`
    -- this task's own real "missing critical objects ... block ...
    rather than proceed with partial evidence" negative_acceptance."""


class UnsafeSourceTreeEntryError(GitHubConnectionError):
    """A real tree entry's own path could escape the repository root if
    ever materialized (absolute path, `..` segment, or embedded NUL byte)
    -- this task's own real "unsafe archives block ... snapshot"
    negative_acceptance."""


@dataclass(frozen=True)
class GitHubTreeEntry:
    path: str
    entry_type: str  # "blob" | "tree" | "commit" (a submodule gitlink)
    mode: str
    sha: str
    size: int | None


@dataclass(frozen=True)
class GitHubSourceCapture:
    """The real fields a caller needs to persist a `TargetSourceSnapshot`
    (`target_source_snapshot_crud.create_target_source_snapshot`) -- this
    module never calls that function itself (see this module's own
    docstring for the disclosed scope boundary)."""

    capture_method: str
    commit_sha: str
    tree_hash: str
    branch: str | None
    tree_entries: tuple[GitHubTreeEntry, ...]
    submodule_paths: tuple[str, ...]
    lfs_tracked_paths: tuple[str, ...]
    tree_listing_complete: bool


def _headers(credential: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {credential}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        "User-Agent": "sovra-model-factory-target-connector",
    }


def _raise_for_non_200_status(response: GuardedResponse, *, context: str) -> None:
    """This module's own copy of the same "deny access" checkpoint every
    sibling GitHub connector module already established -- kept as its
    own copy so this module's own fault-injection test independently
    proves its own gate."""
    if response.status == 401:
        raise GitHubCredentialRevokedError(
            f"GitHub rejected the credential {context} (HTTP 401) -- the token is invalid or "
            "has been revoked"
        )
    if response.status == 403:
        headers_lower = {name.lower(): value for name, value in response.headers.items()}
        if headers_lower.get("x-ratelimit-remaining") == "0":
            raise GitHubRateLimitExceededError(
                f"GitHub's own rate limit is exhausted {context} (HTTP 403, "
                "X-RateLimit-Remaining: 0)"
            )
        raise GitHubRepositoryAccessDeniedError(f"GitHub denied access {context} (HTTP 403)")
    if response.status == 404:
        raise GitHubRepositoryAccessDeniedError(
            f"GitHub could not find the requested resource {context} (HTTP 404)"
        )
    raise GitHubConnectionError(f"unexpected HTTP {response.status} {context}")


def _get(
    url: str,
    *,
    credential: str,
    policy: ConnectionPolicy,
    fetch: GuardedFetchFn,
    timeout: float,
    context: str,
) -> GuardedResponse:
    try:
        response: GuardedResponse = fetch(
            url, policy, method=_HTTP_METHOD, headers=_headers(credential), timeout=timeout
        )
    except OutboundGuardError as exc:
        raise GitHubConnectionError(f"could not reach GitHub {context}: {exc}") from exc
    if response.status != 200:
        _raise_for_non_200_status(response, context=context)
    return response


def fetch_commit_tree_sha(
    *,
    token: str,
    repository_identity: str,
    commit_sha: str,
    base_url: str = DEFAULT_GITHUB_API_BASE_URL,
    policy: ConnectionPolicy | None = None,
    fetch: GuardedFetchFn = guarded_fetch,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Real `GET /repos/{owner}/{repo}/git/commits/{commit_sha}` ->
    `tree.sha`, GitHub's own canonical Git tree object id for that commit.
    """
    active_policy = policy or default_github_connector_policy()
    url = f"{base_url}/repos/{repository_identity}/git/commits/{commit_sha}"
    response = _get(
        url,
        credential=token,
        policy=active_policy,
        fetch=fetch,
        timeout=timeout,
        context=f"fetching the real commit object for {commit_sha!r} in {repository_identity!r}",
    )
    try:
        payload = json.loads(response.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GitHubConnectionError(
            "GitHub's own commit-object response was not valid JSON"
        ) from exc
    tree = payload.get("tree") if isinstance(payload, dict) else None
    tree_sha = tree.get("sha") if isinstance(tree, dict) else None
    if not isinstance(tree_sha, str) or not tree_sha:
        raise MissingCommitTreeError(
            f"GitHub's own commit object for {commit_sha!r} in {repository_identity!r} carries "
            "no usable tree.sha"
        )
    return tree_sha


def _parse_tree_entry(entry: object) -> GitHubTreeEntry:
    if not isinstance(entry, dict):
        raise GitHubConnectionError("GitHub's own tree entry was not a JSON object")
    path = entry.get("path")
    entry_type = entry.get("type")
    mode = entry.get("mode")
    sha = entry.get("sha")
    if not isinstance(path, str) or not path:
        raise GitHubConnectionError("GitHub's own tree entry is missing a real path")
    if entry_type not in ("blob", "tree", "commit"):
        raise GitHubConnectionError(
            f"GitHub's own tree entry has an unexpected type: {entry_type!r}"
        )
    if not isinstance(mode, str) or not mode:
        raise GitHubConnectionError("GitHub's own tree entry is missing a real mode")
    if not isinstance(sha, str) or not sha:
        raise GitHubConnectionError("GitHub's own tree entry is missing a real sha")
    size = entry.get("size")
    if size is not None and not isinstance(size, int):
        raise GitHubConnectionError("GitHub's own tree entry has a non-integer size")
    _validate_entry_path_safety(path)
    return GitHubTreeEntry(path=path, entry_type=entry_type, mode=mode, sha=sha, size=size)


def _validate_entry_path_safety(path: str) -> None:
    if path.startswith("/") or "\x00" in path:
        raise UnsafeSourceTreeEntryError(
            f"tree entry path is unsafe (absolute or contains a NUL byte): {path!r}"
        )
    if any(segment == ".." for segment in path.split("/")):
        raise UnsafeSourceTreeEntryError(
            f"tree entry path is unsafe (contains a '..' segment): {path!r}"
        )


def list_tree_recursive(
    *,
    token: str,
    repository_identity: str,
    tree_sha: str,
    base_url: str = DEFAULT_GITHUB_API_BASE_URL,
    policy: ConnectionPolicy | None = None,
    fetch: GuardedFetchFn = guarded_fetch,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> tuple[tuple[GitHubTreeEntry, ...], bool]:
    """Real `GET /repos/{owner}/{repo}/git/trees/{tree_sha}?recursive=1`.
    Returns `(entries, truncated)`; every entry's own path is validated
    for safety before this function returns (see
    `UnsafeSourceTreeEntryError`) -- a caller never sees an unsafe entry
    at all."""
    active_policy = policy or default_github_connector_policy()
    url = f"{base_url}/repos/{repository_identity}/git/trees/{tree_sha}?recursive=1"
    response = _get(
        url,
        credential=token,
        policy=active_policy,
        fetch=fetch,
        timeout=timeout,
        context=f"listing the real recursive tree {tree_sha!r} in {repository_identity!r}",
    )
    try:
        payload = json.loads(response.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GitHubConnectionError(
            "GitHub's own tree-listing response was not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise GitHubConnectionError("GitHub's own tree-listing response was not a JSON object")
    raw_entries = payload.get("tree")
    if not isinstance(raw_entries, list):
        raise GitHubConnectionError(
            "GitHub's own tree-listing response is missing a real tree array"
        )
    truncated = bool(payload.get("truncated", False))
    entries = tuple(_parse_tree_entry(entry) for entry in raw_entries)
    return entries, truncated


def _fetch_blob_text(
    *,
    token: str,
    repository_identity: str,
    blob_sha: str,
    base_url: str,
    policy: ConnectionPolicy,
    fetch: GuardedFetchFn,
    timeout: float,
) -> str:
    url = f"{base_url}/repos/{repository_identity}/git/blobs/{blob_sha}"
    response = _get(
        url,
        credential=token,
        policy=policy,
        fetch=fetch,
        timeout=timeout,
        context=(
            f"reading the real {GITATTRIBUTES_PATH} blob {blob_sha!r} in {repository_identity!r}"
        ),
    )
    try:
        payload = json.loads(response.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GitHubConnectionError("GitHub's own blob response was not valid JSON") from exc
    content = payload.get("content") if isinstance(payload, dict) else None
    encoding = payload.get("encoding") if isinstance(payload, dict) else None
    if not isinstance(content, str) or encoding != "base64":
        raise GitHubConnectionError(
            f"GitHub's own blob response for {blob_sha!r} is not real base64-encoded content"
        )
    try:
        return base64.b64decode(content, validate=False).decode("utf-8", errors="replace")
    except (ValueError, UnicodeDecodeError) as exc:
        raise GitHubConnectionError(
            f"GitHub's own blob content for {blob_sha!r} is not decodable"
        ) from exc


def _parse_lfs_patterns(gitattributes_text: str) -> tuple[str, ...]:
    """Real, minimal `.gitattributes` parsing: a line whose attribute list
    contains `filter=lfs` declares its own leading glob pattern as an
    LFS-tracked path pattern -- the real, documented Git LFS convention.
    """
    patterns: list[str] = []
    for line in gitattributes_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        pattern, attributes = parts[0], parts[1:]
        if any(attr == "filter=lfs" for attr in attributes):
            patterns.append(pattern)
    return tuple(patterns)


def capture_github_source_snapshot(
    *,
    token: str,
    repository_identity: str,
    ref: str,
    base_url: str = DEFAULT_GITHUB_API_BASE_URL,
    policy: ConnectionPolicy | None = None,
    fetch: GuardedFetchFn = guarded_fetch,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> GitHubSourceCapture:
    """Real end-to-end GitHub source capture: resolve `ref` to its real,
    current, immutable commit SHA (FTF-04A04's own `resolve_ref_to_commit`,
    reused unmodified), fetch that commit's own real, canonical Git tree
    hash, enumerate the real recursive tree, and detect submodules/LFS
    tracking. Raises `IncompleteSourceTreeError` if GitHub's own listing is
    truncated, or `UnsafeSourceTreeEntryError` if any entry's path is
    unsafe -- this function never returns a capture built from partial or
    unsafe tree data."""
    active_policy = policy or default_github_connector_policy()

    resolved = resolve_ref_to_commit(
        token=token,
        repository_identity=repository_identity,
        ref=ref,
        base_url=base_url,
        policy=active_policy,
        fetch=fetch,
        timeout=timeout,
    )

    tree_sha = fetch_commit_tree_sha(
        token=token,
        repository_identity=repository_identity,
        commit_sha=resolved.resolved_commit_sha,
        base_url=base_url,
        policy=active_policy,
        fetch=fetch,
        timeout=timeout,
    )

    entries, truncated = list_tree_recursive(
        token=token,
        repository_identity=repository_identity,
        tree_sha=tree_sha,
        base_url=base_url,
        policy=active_policy,
        fetch=fetch,
        timeout=timeout,
    )
    if truncated:
        raise IncompleteSourceTreeError(
            f"GitHub's own real recursive tree listing for {tree_sha!r} in "
            f"{repository_identity!r} is truncated -- refusing to build a snapshot from a "
            "partial tree"
        )

    submodule_paths = tuple(e.path for e in entries if e.entry_type == "commit")

    lfs_tracked_paths: tuple[str, ...] = ()
    gitattributes_entry = next(
        (e for e in entries if e.path == GITATTRIBUTES_PATH and e.entry_type == "blob"), None
    )
    if gitattributes_entry is not None:
        gitattributes_text = _fetch_blob_text(
            token=token,
            repository_identity=repository_identity,
            blob_sha=gitattributes_entry.sha,
            base_url=base_url,
            policy=active_policy,
            fetch=fetch,
            timeout=timeout,
        )
        patterns = _parse_lfs_patterns(gitattributes_text)
        if patterns:
            lfs_tracked_paths = tuple(
                e.path
                for e in entries
                if e.entry_type == "blob"
                and any(fnmatch.fnmatch(e.path, pattern) for pattern in patterns)
            )

    branch = None if ref.lower() == resolved.resolved_commit_sha.lower() else ref

    return GitHubSourceCapture(
        capture_method=CAPTURE_METHOD,
        commit_sha=resolved.resolved_commit_sha,
        tree_hash=tree_sha,
        branch=branch,
        tree_entries=entries,
        submodule_paths=submodule_paths,
        lfs_tracked_paths=lfs_tracked_paths,
        tree_listing_complete=True,
    )
