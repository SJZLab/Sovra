"""GitHub accessible-repository listing (FTF-04A03, Section 6.8A/7A.1/7A.2/
77/81).

Section 7A.2 lists "list only repositories visible to that credential" as
one of the GitHub connector's own required capabilities, and this task's
own acceptance clause ("Render only credential-accessible repositories
with pagination and explicit selection") needs a real backend call to
produce that list. `github_app_token_read_only_secret_integration.py`
(FTF-04A02, CERTIFIED) deliberately implements only `GET /repos/{owner}/
{repo}` -- validating a single, already-named repository -- and its own
module docstring never claims to enumerate every repository a credential
can see. Rather than reopen that CERTIFIED module (Law 15: a material
change to certified evidence invalidates it until requalification) or
invent a placeholder list client-side (Law 11), this is a new, additive
sibling module for the one real GitHub REST API v3 endpoint that actually
enumerates a credential's accessible repositories: `GET
/installation/repositories`, GitHub's own real endpoint for a GitHub App
installation access token, and this task's own "either credential shape
is an opaque bearer token" reading (the same one FTF-04A02's module
docstring already established) applies identically here.

Same "caller resolves the secret" boundary FTF-04A02 established: this
module accepts an already-resolved bearer token, never a
`credential_secret_ref`. Real, read-only, GET-only by construction
(`_HTTP_METHOD` is a module-level constant, never a caller parameter) --
Section 7A.2's "never request write permission for normal source
discovery" holds here exactly as it does in FTF-04A02.

Every real HTTP request goes through `packages/security/outbound_guard.py`
(FTF-H006), the one module in this repository allowed to open an outbound
socket -- identical to FTF-04A02.
"""

from __future__ import annotations

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
    default_github_connector_policy,
)

__all__ = [
    "DEFAULT_PER_PAGE",
    "MAX_PER_PAGE",
    "GitHubRepositoryPage",
    "GitHubRepositorySummary",
    "InvalidPaginationError",
    "list_accessible_repositories",
]

_DEFAULT_TIMEOUT_SECONDS = 30.0
_GITHUB_API_VERSION = "2022-11-28"

# Section 7A.2's own "never request write permission for normal source
# discovery" -- a module-level constant, never a parameter
# `list_accessible_repositories` exposes to a caller.
_HTTP_METHOD = "GET"

DEFAULT_PER_PAGE = 30
# GitHub's own real documented ceiling for this endpoint's `per_page` query
# parameter -- refused before any network request is made, not merely
# passed through and silently clamped by the upstream API.
MAX_PER_PAGE = 100

GuardedFetchFn = Callable[..., GuardedResponse]


class InvalidPaginationError(GitHubConnectionError):
    """`page`/`per_page` is not a real, in-range pagination parameter --
    refused before any network request is made."""


@dataclass(frozen=True)
class GitHubRepositorySummary:
    repository_identity: str
    private: bool
    default_branch: str


@dataclass(frozen=True)
class GitHubRepositoryPage:
    repositories: tuple[GitHubRepositorySummary, ...]
    page: int
    per_page: int
    has_next_page: bool


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


def _is_rate_limited(response: GuardedResponse) -> bool:
    headers_lower = {name.lower(): value for name, value in response.headers.items()}
    return headers_lower.get("x-ratelimit-remaining") == "0"


def _has_next_page(response: GuardedResponse) -> bool:
    """GitHub's own real pagination contract for this endpoint: a `Link`
    response header whose value contains a segment with `rel="next"` when
    a further page exists -- never inferred from the returned item count
    alone (a full page and a genuinely final page are indistinguishable by
    count when `total_count` is not itself trusted)."""
    headers_lower = {name.lower(): value for name, value in response.headers.items()}
    link_header = headers_lower.get("link")
    if not link_header:
        return False
    return any(
        segment.strip().endswith('rel="next"') or 'rel="next"' in segment
        for segment in link_header.split(",")
    )


def _raise_for_non_200_status(response: GuardedResponse) -> None:
    """This module's single, comprehensive "deny access" checkpoint --
    same discipline as FTF-04A02's own `_raise_for_non_200_status`, kept as
    a separate copy rather than a cross-module private import so each
    module's own negative-test fault injection independently proves its
    own gate, not a shared one that could mask the other's removal."""
    if response.status == 401:
        raise GitHubCredentialRevokedError(
            "GitHub rejected the credential (HTTP 401) -- the token is invalid or has been "
            "revoked"
        )
    if response.status == 403:
        if _is_rate_limited(response):
            raise GitHubRateLimitExceededError(
                "GitHub's own rate limit is exhausted listing accessible repositories (HTTP "
                "403, X-RateLimit-Remaining: 0)"
            )
        raise GitHubConnectionError(
            "GitHub denied access to the installation repository list (HTTP 403)"
        )
    raise GitHubConnectionError(f"unexpected HTTP {response.status} listing repositories")


def _parse_repositories_response(body: bytes) -> list[dict[str, object]]:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GitHubConnectionError(
            "GitHub's own repository-list response was not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise GitHubConnectionError("GitHub's own repository-list response was not a JSON object")
    repositories = payload.get("repositories")
    if not isinstance(repositories, list):
        raise GitHubConnectionError(
            "GitHub's own repository-list response is missing a real 'repositories' array"
        )
    return repositories


def _summarize(entry: object) -> GitHubRepositorySummary:
    if not isinstance(entry, dict):
        raise GitHubConnectionError("GitHub's own repository-list entry was not a JSON object")
    full_name = entry.get("full_name")
    if not isinstance(full_name, str) or not full_name:
        raise GitHubConnectionError("GitHub's own repository-list entry is missing full_name")
    default_branch = entry.get("default_branch")
    if not isinstance(default_branch, str) or not default_branch:
        raise GitHubConnectionError(
            f"GitHub's own repository-list entry for {full_name!r} is missing default_branch"
        )
    return GitHubRepositorySummary(
        repository_identity=full_name,
        private=bool(entry.get("private", False)),
        default_branch=default_branch,
    )


def list_accessible_repositories(
    *,
    token: str,
    page: int = 1,
    per_page: int = DEFAULT_PER_PAGE,
    base_url: str = DEFAULT_GITHUB_API_BASE_URL,
    policy: ConnectionPolicy | None = None,
    fetch: GuardedFetchFn = guarded_fetch,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> GitHubRepositoryPage:
    """Lists the real repositories `token` (a real, already-resolved
    GitHub App installation token or organization-approved token) can see,
    one page at a time. Raises one of this module's/FTF-04A02's own error
    classes for every negative_acceptance case; never returns a partial or
    fabricated page."""
    validated_page = _validate_page(page)
    validated_per_page = _validate_per_page(per_page)
    credential = _validate_token(token)

    active_policy = policy or default_github_connector_policy()
    url = (
        f"{base_url}/installation/repositories"
        f"?page={validated_page}&per_page={validated_per_page}"
    )
    headers = {
        "Authorization": f"Bearer {credential}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        "User-Agent": "sovra-model-factory-target-connector",
    }
    try:
        response: GuardedResponse = fetch(
            url, active_policy, method=_HTTP_METHOD, headers=headers, timeout=timeout
        )
    except OutboundGuardError as exc:
        raise GitHubConnectionError(f"could not reach GitHub to list repositories: {exc}") from exc

    if response.status != 200:
        _raise_for_non_200_status(response)

    entries = _parse_repositories_response(response.body)
    repositories = tuple(_summarize(entry) for entry in entries)

    return GitHubRepositoryPage(
        repositories=repositories,
        page=validated_page,
        per_page=validated_per_page,
        has_next_page=_has_next_page(response),
    )
