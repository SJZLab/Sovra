"""GitHub branch/ref listing + immutable commit SHA resolution (FTF-04A04,
Section 6.8A/6.8B/7A.1/7A.2/77/81).

Section 7A.1's own `TargetApplicationConnector` Protocol names two
separate operations this task's own acceptance clause ("Resolve selected
branch/tag/ref to immutable commit SHA and persist both display ref and
resolved identity") composes: `resolve_revision` (branch/tag/ref ->
immutable commit SHA) and the real "list/select branch/ref" half of
Section 7A.2's own required capabilities. Neither exists anywhere in this
repository yet -- `github_app_token_read_only_secret_integration.py`
(FTF-04A02, CERTIFIED) validates access to one already-named repository
only, and `github_accessible_repository_listing.py` (FTF-04A03, CERTIFIED)
enumerates repositories, not branches/refs within one. This is a new,
additive sibling module for the two real GitHub REST API v3 endpoints
this task's own acceptance actually needs -- the same "new sibling module
rather than reopening a CERTIFIED one" precedent FTF-04A03's own module
docstring already established relative to FTF-04A02 (Law 15).

Two real, read-only, GET-only endpoints:

  - `GET /repos/{owner}/{repo}/branches` -- lists real branches, each with
    GitHub's own list-time `commit.sha`. This list-time SHA is shown to
    the user for orientation only; it is never persisted or treated as
    the resolved identity (see below).
  - `GET /repos/{owner}/{repo}/commits/{ref}` -- GitHub's own real,
    single-ref-resolution endpoint that accepts a branch name, tag name,
    or SHA and returns that ref's own current commit SHA. This is the
    real "immutable SHA resolver": `resolve_ref_to_commit` always issues
    a fresh call here, never reusing a list-time SHA, which is this
    task's own real "Branch advancing between list/read does not mix
    commits" negative_acceptance -- a branch that moved between the list
    call and the moment the user confirms their selection is resolved to
    its *current* tip, not a stale value silently carried over from the
    list response.

Same "caller resolves the secret" boundary FTF-04A02/FTF-04A03 already
established: this module accepts an already-resolved bearer token, never
a `credential_secret_ref`. Real, GET-only by construction (`_HTTP_METHOD`
is a module-level constant, never a caller parameter) -- Section 7A.2's
"never request write permission for normal source discovery" holds here
exactly as it does in FTF-04A02/FTF-04A03.

This task's own negative_acceptance's other half ("unknown/inaccessible
ref is blocked") is GitHub's own real, documented contract for this
endpoint: a ref that does not exist, or a private repository outside the
credential's grant, both answer HTTP 404 (GitHub does not distinguish the
two for a repository/ref a credential cannot see, the same ambiguity
`github_app_token_read_only_secret_integration.py`'s own module docstring
already documents for `GET /repos/{owner}/{repo}`) -- raised here as
`GitHubRepositoryAccessDeniedError`, never silently returned as an empty
or fabricated success.

Every real HTTP request this module makes goes through
`packages/security/outbound_guard.py` (FTF-H006), the one module in this
repository allowed to open an outbound socket -- identical to
FTF-04A02/FTF-04A03.
"""

from __future__ import annotations

import json
import re
import urllib.parse
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

__all__ = [
    "DEFAULT_PER_PAGE",
    "MAX_PER_PAGE",
    "MAX_REF_LENGTH",
    "GitHubBranchSummary",
    "GitHubBranchPage",
    "GitHubResolvedRef",
    "InvalidPaginationError",
    "InvalidRefError",
    "InvalidRepositoryIdentityError",
    "list_branches",
    "resolve_ref_to_commit",
]

_DEFAULT_TIMEOUT_SECONDS = 30.0
_GITHUB_API_VERSION = "2022-11-28"

# Section 7A.2's own "never request write permission for normal source
# discovery" -- a module-level constant, never a parameter either public
# function exposes to a caller.
_HTTP_METHOD = "GET"

DEFAULT_PER_PAGE = 30
# GitHub's own real documented ceiling for `GET /repos/{owner}/{repo}/branches`'s
# `per_page` query parameter -- refused before any network request is made,
# not merely passed through and silently clamped by the upstream API.
MAX_PER_PAGE = 100

# Real Git ref-name length is effectively unbounded by GitHub, but a ref
# this long can never be a real branch/tag/SHA a human selected from this
# task's own UI -- refused before any network request is made, the same
# "reject before any request" discipline every prior validator in this
# module family already established.
MAX_REF_LENGTH = 255

# Section 7A.2's own real repository-identity shape: GitHub's own
# `owner/repo` addressing -- the identical pattern
# `github_app_token_read_only_secret_integration.py` already validates,
# kept as its own copy here (not a cross-module private import) so this
# module's own fault-injection tests independently prove its own gate,
# not a shared one whose removal a different module's test could mask.
_REPOSITORY_IDENTITY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,38})/[A-Za-z0-9._-]{1,100}$"
)

GuardedFetchFn = Callable[..., GuardedResponse]


class InvalidRepositoryIdentityError(GitHubConnectionError):
    """`repository_identity` is not a real `owner/repo`-shaped identity --
    refused before any network request is made."""


class InvalidPaginationError(GitHubConnectionError):
    """`page`/`per_page` is not a real, in-range pagination parameter --
    refused before any network request is made."""


class InvalidRefError(GitHubConnectionError):
    """`ref` is empty, too long, or otherwise cannot be a real Git ref/SHA
    -- refused before any network request is made."""


@dataclass(frozen=True)
class GitHubBranchSummary:
    name: str
    list_time_commit_sha: str


@dataclass(frozen=True)
class GitHubBranchPage:
    branches: tuple[GitHubBranchSummary, ...]
    page: int
    per_page: int
    has_next_page: bool


@dataclass(frozen=True)
class GitHubResolvedRef:
    ref: str
    resolved_commit_sha: str


def _validate_repository_identity(repository_identity: object) -> str:
    if not isinstance(repository_identity, str) or not _REPOSITORY_IDENTITY_PATTERN.match(
        repository_identity
    ):
        raise InvalidRepositoryIdentityError(
            f"repository_identity must be a real GitHub 'owner/repo' identity: "
            f"{repository_identity!r}"
        )
    return repository_identity


def _validate_page(page: object) -> int:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise InvalidPaginationError(f"page must be a positive integer: {page!r}")
    return page


def _validate_per_page(per_page: object) -> int:
    if (
        not isinstance(per_page, int)
        or isinstance(per_page, bool)
        or not (1 <= per_page <= MAX_PER_PAGE)
    ):
        raise InvalidPaginationError(
            f"per_page must be an integer between 1 and {MAX_PER_PAGE}: {per_page!r}"
        )
    return per_page


def _validate_token(token: object) -> str:
    if not isinstance(token, str) or not token.strip():
        raise GitHubCredentialRevokedError(
            "no credential was supplied -- refusing to attempt an unauthenticated request"
        )
    return token


def _validate_ref(ref: object) -> str:
    if not isinstance(ref, str) or not ref.strip():
        raise InvalidRefError("ref must be a non-empty string")
    if len(ref) > MAX_REF_LENGTH:
        raise InvalidRefError(f"ref must be at most {MAX_REF_LENGTH} characters: {ref!r}")
    # Git refs never contain whitespace or a literal ".." (Git's own
    # refname grammar) -- a value containing either is refused before any
    # network request is made, never smuggled into the request path.
    if any(ch.isspace() for ch in ref) or ".." in ref:
        raise InvalidRefError(f"ref is not a well-formed Git ref/SHA: {ref!r}")
    return ref


def _is_rate_limited(response: GuardedResponse) -> bool:
    headers_lower = {name.lower(): value for name, value in response.headers.items()}
    return headers_lower.get("x-ratelimit-remaining") == "0"


def _has_next_page(response: GuardedResponse) -> bool:
    """Same real `Link: ...rel="next"` pagination contract
    `github_accessible_repository_listing.py` already established for its
    own listing endpoint -- never inferred from the returned item count."""
    headers_lower = {name.lower(): value for name, value in response.headers.items()}
    link_header = headers_lower.get("link")
    if not link_header:
        return False
    return any('rel="next"' in segment for segment in link_header.split(","))


def _raise_for_non_200_status(response: GuardedResponse, *, context: str) -> None:
    """This module's single, comprehensive "deny access" checkpoint for
    both real endpoints below -- same discipline as
    FTF-04A02/FTF-04A03's own `_raise_for_non_200_status`, kept as its own
    copy so this module's own fault-injection test independently proves
    its own gate."""
    if response.status == 401:
        raise GitHubCredentialRevokedError(
            f"GitHub rejected the credential {context} (HTTP 401) -- the token is invalid or "
            "has been revoked"
        )
    if response.status == 403:
        if _is_rate_limited(response):
            raise GitHubRateLimitExceededError(
                f"GitHub's own rate limit is exhausted {context} (HTTP 403, "
                "X-RateLimit-Remaining: 0)"
            )
        raise GitHubRepositoryAccessDeniedError(f"GitHub denied access {context} (HTTP 403)")
    if response.status == 404:
        raise GitHubRepositoryAccessDeniedError(
            f"GitHub could not find the requested resource {context} (HTTP 404) -- the "
            "repository/ref is private and outside this credential's own grant, or does not "
            "exist; GitHub does not distinguish the two"
        )
    if response.status == 422:
        raise GitHubRepositoryAccessDeniedError(
            f"GitHub could not resolve the requested ref {context} (HTTP 422) -- the ref is "
            "not a real, unambiguous branch/tag/SHA in this repository"
        )
    raise GitHubConnectionError(f"unexpected HTTP {response.status} {context}")


def _headers(credential: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {credential}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        "User-Agent": "sovra-model-factory-target-connector",
    }


def _parse_branches_response(body: bytes) -> list[dict[str, object]]:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GitHubConnectionError("GitHub's own branch-list response was not valid JSON") from exc
    if not isinstance(payload, list):
        raise GitHubConnectionError("GitHub's own branch-list response was not a JSON array")
    return payload


def _summarize_branch(entry: object) -> GitHubBranchSummary:
    if not isinstance(entry, dict):
        raise GitHubConnectionError("GitHub's own branch-list entry was not a JSON object")
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise GitHubConnectionError("GitHub's own branch-list entry is missing name")
    commit = entry.get("commit")
    sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(sha, str) or not sha:
        raise GitHubConnectionError(
            f"GitHub's own branch-list entry for {name!r} is missing commit.sha"
        )
    return GitHubBranchSummary(name=name, list_time_commit_sha=sha)


def list_branches(
    *,
    token: str,
    repository_identity: str,
    page: int = 1,
    per_page: int = DEFAULT_PER_PAGE,
    base_url: str = DEFAULT_GITHUB_API_BASE_URL,
    policy: ConnectionPolicy | None = None,
    fetch: GuardedFetchFn = guarded_fetch,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> GitHubBranchPage:
    """Lists the real branches of `repository_identity` that `token` (a
    real, already-resolved GitHub App installation token or
    organization-approved token) can see, one page at a time. Each
    branch's own `list_time_commit_sha` is display-only orientation, never
    the resolved identity this task persists -- call `resolve_ref_to_commit`
    at selection time for that. Raises one of this module's/FTF-04A02's
    own error classes for every negative_acceptance case; never returns a
    partial or fabricated page."""
    identity = _validate_repository_identity(repository_identity)
    validated_page = _validate_page(page)
    validated_per_page = _validate_per_page(per_page)
    credential = _validate_token(token)

    active_policy = policy or default_github_connector_policy()
    url = (
        f"{base_url}/repos/{identity}/branches"
        f"?page={validated_page}&per_page={validated_per_page}"
    )
    try:
        response: GuardedResponse = fetch(
            url, active_policy, method=_HTTP_METHOD, headers=_headers(credential), timeout=timeout
        )
    except OutboundGuardError as exc:
        raise GitHubConnectionError(
            f"could not reach GitHub to list branches for {identity!r}: {exc}"
        ) from exc

    if response.status != 200:
        _raise_for_non_200_status(response, context=f"listing branches for {identity!r}")

    entries = _parse_branches_response(response.body)
    branches = tuple(_summarize_branch(entry) for entry in entries)

    return GitHubBranchPage(
        branches=branches,
        page=validated_page,
        per_page=validated_per_page,
        has_next_page=_has_next_page(response),
    )


def _parse_commit_response(body: bytes) -> str:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GitHubConnectionError(
            "GitHub's own commit-resolution response was not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise GitHubConnectionError("GitHub's own commit-resolution response was not a JSON object")
    sha = payload.get("sha")
    if not isinstance(sha, str) or not sha:
        raise GitHubConnectionError("GitHub's own commit-resolution response is missing sha")
    return sha


def resolve_ref_to_commit(
    *,
    token: str,
    repository_identity: str,
    ref: str,
    base_url: str = DEFAULT_GITHUB_API_BASE_URL,
    policy: ConnectionPolicy | None = None,
    fetch: GuardedFetchFn = guarded_fetch,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> GitHubResolvedRef:
    """Resolves `ref` (a branch name, tag name, or SHA) to its real,
    current, immutable commit SHA in `repository_identity`, via GitHub's
    own real `GET /repos/{owner}/{repo}/commits/{ref}` endpoint. Always
    issues a fresh request -- this task's own real "immutable SHA
    resolver", never reusing a possibly-stale list-time SHA. Raises
    `GitHubRepositoryAccessDeniedError` for an unknown or inaccessible ref
    (this task's own negative_acceptance), or one of this module's/
    FTF-04A02's own other error classes; never returns a partial or
    fabricated result."""
    identity = _validate_repository_identity(repository_identity)
    validated_ref = _validate_ref(ref)
    credential = _validate_token(token)

    active_policy = policy or default_github_connector_policy()
    # Slashes in a branch name (e.g. "release/1.0") must be percent-encoded
    # for this endpoint to disambiguate the ref from a path segment --
    # GitHub's own documented requirement for this exact endpoint.
    encoded_ref = urllib.parse.quote(validated_ref, safe="")
    url = f"{base_url}/repos/{identity}/commits/{encoded_ref}"
    try:
        response: GuardedResponse = fetch(
            url, active_policy, method=_HTTP_METHOD, headers=_headers(credential), timeout=timeout
        )
    except OutboundGuardError as exc:
        raise GitHubConnectionError(
            f"could not reach GitHub to resolve ref {validated_ref!r} in {identity!r}: {exc}"
        ) from exc

    if response.status != 200:
        _raise_for_non_200_status(
            response, context=f"resolving ref {validated_ref!r} in {identity!r}"
        )

    sha = _parse_commit_response(response.body)
    return GitHubResolvedRef(ref=validated_ref, resolved_commit_sha=sha)
