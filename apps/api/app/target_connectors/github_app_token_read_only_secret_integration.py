"""GitHub App/token read-only secret integration (FTF-04A02, Section 6.8A/
7A.1/7A.2/77/81).

Section 7A.1 names the GitHub read-only connector as one of exactly two
"Minimum production connectors required by DoD"; `local_repository_connector.py`
(FTF-04A06) is the other. `target_application_connection_schema.py`
(FTF-04A01) deliberately stops at storing/validating the *shape* of a
`connector_type='github'` `TargetApplicationConnection` row -- its own
docstring names "the concrete GitHub App/token exchange (Section 7A.2 --
FTF-04A02, a separate, dependency-gated later task)" as explicitly out of
its own scope. This module is that real caller.

Path refined from the task packet's own generic suggestion
(`packages/target-connectors/github_app_token_read_only_secret_integration.py`)
to `apps/api/app/target_connectors/github_app_token_read_only_secret_integration.py`,
matching every other CERTIFIED 04A-family task's own real
`apps/api/app/target_connectors/*.py` layout (FTF-04A01/FTF-04A06/FTF-04A19/
FTF-04A20/FTF-04A25/FTF-04A26) rather than the packet's initial owner-path
placeholder.

Scope, disclosed rather than hidden: this module accepts an
already-resolved bearer token string, not a `credential_secret_ref`
itself -- the same "caller resolves the secret, this module only ever
sees a real already-resolved credential" shape `private_s3_artifact_
resolver.py` (FTF-0303) and `hugging_face_artifact_resolver.py` (FTF-0302)
already established for `FTF-0202`'s own `resolve_secret_ref` (OpenBao,
CERTIFIED). A future connection-management task (out of this task's own
write set) resolves `TargetApplicationConnection.credential_secret_ref`
via `resolve_secret_ref` once and passes the real value in here. This
also means this module does not itself perform the GitHub App JWT/
private-key installation-token exchange (that would need a new asymmetric-
signing runtime dependency -- `cryptography`/`PyJWT` -- neither already
present in this repository and neither named by any architecture doc,
which CLAUDE.md's "Ask owner first" would require asking about); Section
7A.2's own "connect using GitHub App installation **or** organization-
approved token" is read as: either credential shape is accepted as
*already-minted* opaque bearer-token bytes at this module's boundary --
a real GitHub App installation access token and a real organization-
approved personal/fine-grained access token are indistinguishable to the
GitHub REST API's own `Authorization: Bearer <token>` contract, and both
are validated identically here. Minting a fresh installation token from
an App's private key is a separate, later, explicitly-scoped task, not
silently invented here.

Real, read-only GitHub REST API v3 validation only: `GET /repos/{owner}/
{repo}` is the one endpoint this module ever calls, confirming the
supplied credential's real installation/repository scope covers the
requested repository. This module never calls, and structurally cannot
call (no method parameter is ever exposed), any endpoint or HTTP verb
that would require write access -- Section 7A.2's own "never request
write permission for normal source discovery" / "never push, open PRs,
create branches, modify files, or alter repository settings" is enforced
by construction, not by a runtime flag a caller could disable.

This task's own negative_acceptance ("Private repo outside installation
and revoked token deny access; no write scopes requested for analysis")
is three independently-triggered real HTTP outcomes from GitHub's own
documented API contract, never guessed:

  1. Revoked/invalid token -- GitHub answers HTTP 401 to any request
     bearing a credential it no longer honors. Raises
     `GitHubCredentialRevokedError`.
  2. Private repository outside the installation/token's own grant --
     GitHub deliberately answers HTTP 404 (not 403) for a private
     repository the credential cannot see, to avoid confirming its
     existence to an unauthorized caller. Raises
     `GitHubRepositoryAccessDeniedError`.
  3. No write scopes requested for analysis -- structural: `_HTTP_METHOD`
     is a module-level constant, never a caller-supplied parameter, and
     every request carries no body. Proven by a disclosed fault injection
     (Law 1) that changes the hardcoded verb and shows the real local
     fixture server (which only implements `do_GET`) genuinely refuses
     the request.

An HTTP 403 with a real, exhausted `X-RateLimit-Remaining: 0` header is
treated as a distinct, transient `GitHubRateLimitExceededError` rather
than folded into the access-denied case -- GitHub's own documented
contract for rate limiting Section 7A.2's own "obey API rate limits with
bounded retry/backoff" refers to, and a real caller needs to tell "denied"
apart from "try again later."

This sandbox has no route to the public internet (`curl` to
`api.github.com` fails at the outbound proxy, the same disclosed
precedent FTF-0302/FTF-0303 already established for their own upstream
APIs), so the real request/response/verification logic is proven against
a real local HTTPS server implementing the exact GitHub REST API v3
subset this module calls, using this repository's own already-established
real-self-signed-CA-in-the-system-trust-store precedent
(`tests/tasks/test_ftf_0302.py`) rather than a mocked `guarded_fetch`.

Every real HTTP request this module makes goes through
`packages/security/outbound_guard.py` (FTF-H006) -- the one module in
this repository allowed to open an outbound socket.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from packages.security.outbound_guard import GuardedResponse, OutboundGuardError, guarded_fetch
from packages.security.url_policy import ConnectionPolicy

__all__ = [
    "DEFAULT_GITHUB_API_BASE_URL",
    "GitHubConnectionError",
    "GitHubCredentialRevokedError",
    "GitHubRateLimitExceededError",
    "GitHubRepositoryAccessDeniedError",
    "GitHubRepositoryValidation",
    "InvalidRepositoryIdentityError",
    "UnsupportedAccessModeError",
    "default_github_connector_policy",
    "validate_github_repository_access",
]

DEFAULT_GITHUB_API_BASE_URL = "https://api.github.com"
_DEFAULT_TIMEOUT_SECONDS = 30.0
_GITHUB_API_VERSION = "2022-11-28"

# Section 7A.2's own real repository-identity shape: GitHub's own
# `owner/repo` addressing. Refused before any network request is made --
# never sent to `fetch` as a free-form string that could be smuggled into
# a different path (e.g. a value containing `/../` or a second `/`).
_REPOSITORY_IDENTITY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,38})/[A-Za-z0-9._-]{1,100}$"
)

# Section 7A.2's own "never request write permission for normal source
# discovery" -- a module-level constant, never a parameter
# `validate_github_repository_access` exposes to a caller, so no caller
# can ever make this module issue a write-shaped request.
_HTTP_METHOD = "GET"

GuardedFetchFn = Callable[..., GuardedResponse]


class GitHubConnectionError(ValueError):
    """Base class for every real defect this module raises validating a
    GitHub repository connection -- never caught and silently converted
    to a fabricated success anywhere in this module."""


class InvalidRepositoryIdentityError(GitHubConnectionError):
    """`repository_identity` is not a real `owner/repo`-shaped identity --
    refused before any network request is made."""


class UnsupportedAccessModeError(GitHubConnectionError):
    """`access_mode` is not `read_only` -- Section 6.8A's own closed set;
    refused before any network request is made (defense-in-depth on top
    of `target_application_connection_schema.py`'s own already-enforced
    database check constraint)."""


class GitHubCredentialRevokedError(GitHubConnectionError):
    """GitHub answered HTTP 401 -- the credential is invalid or has been
    revoked. This task's own "revoked token deny access"
    negative_acceptance."""


class GitHubRepositoryAccessDeniedError(GitHubConnectionError):
    """GitHub answered HTTP 404 (a private repository outside the
    credential's own installation/token grant -- GitHub does not
    distinguish "does not exist" from "you cannot see it" for a private
    repository) or a real HTTP 403 that is not itself a rate-limit
    exhaustion. This task's own "private repo outside installation ...
    deny access" negative_acceptance."""


class GitHubRateLimitExceededError(GitHubConnectionError):
    """GitHub answered HTTP 403 with a real, exhausted
    `X-RateLimit-Remaining: 0` header -- a transient condition, distinct
    from a genuine access denial (Section 7A.2's own "obey API rate
    limits with bounded retry/backoff")."""


@dataclass(frozen=True)
class GitHubRepositoryValidation:
    repository_identity: str
    resolved_full_name: str
    private: bool
    default_branch: str


def default_github_connector_policy() -> ConnectionPolicy:
    """Production default: no redirects. A real `GET /repos/{owner}/
    {repo}` call to GitHub's own fixed, already-known-good API host has no
    legitimate redirect to follow (unlike FTF-0302's own public Hugging
    Face CDN case)."""
    return ConnectionPolicy(name="github_app_token_connector", allow_redirects=False)


def _validate_repository_identity(repository_identity: object) -> str:
    if not isinstance(repository_identity, str) or not _REPOSITORY_IDENTITY_PATTERN.match(
        repository_identity
    ):
        raise InvalidRepositoryIdentityError(
            f"repository_identity must be a real GitHub 'owner/repo' identity: "
            f"{repository_identity!r}"
        )
    return repository_identity


def _validate_access_mode(access_mode: object) -> str:
    if not isinstance(access_mode, str) or access_mode != "read_only":
        raise UnsupportedAccessModeError(
            f"access_mode must be 'read_only' -- Section 6.8A names no other supported value "
            f"for Release 1: {access_mode!r}"
        )
    return access_mode


def _validate_token(token: object) -> str:
    if not isinstance(token, str) or not token.strip():
        raise GitHubCredentialRevokedError(
            "no credential was supplied -- refusing to attempt an unauthenticated request"
        )
    return token


def _is_rate_limited(response: GuardedResponse) -> bool:
    headers_lower = {name.lower(): value for name, value in response.headers.items()}
    return headers_lower.get("x-ratelimit-remaining") == "0"


def _raise_for_non_200_status(response: GuardedResponse, identity: str) -> None:
    """The one place a non-200 response is classified and denied. Called
    from exactly one gate (`validate_github_repository_access`'s own
    `if response.status != 200:`), so that gate is this module's single,
    comprehensive "deny access" checkpoint for every negative_acceptance
    status this module recognizes -- proven load-bearing as a whole by
    this task's own disclosed fault injection, rather than by several
    independent, redundant per-status checks that would each mask the
    others' removal."""
    if response.status == 401:
        raise GitHubCredentialRevokedError(
            f"GitHub rejected the credential for {identity!r} (HTTP 401) -- the token is "
            "invalid or has been revoked"
        )
    if response.status == 403:
        if _is_rate_limited(response):
            raise GitHubRateLimitExceededError(
                f"GitHub's own rate limit is exhausted validating {identity!r} (HTTP 403, "
                "X-RateLimit-Remaining: 0)"
            )
        raise GitHubRepositoryAccessDeniedError(
            f"GitHub denied access to {identity!r} (HTTP 403) -- the credential's own "
            "installation/token scope does not cover this repository"
        )
    if response.status == 404:
        raise GitHubRepositoryAccessDeniedError(
            f"{identity!r} is private and outside this credential's own installation/token "
            "grant, or does not exist (HTTP 404) -- GitHub does not distinguish the two for a "
            "repository this credential cannot see"
        )
    raise GitHubConnectionError(f"unexpected HTTP {response.status} validating {identity!r}")


def _parse_repository_response(body: bytes) -> dict[str, object]:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GitHubConnectionError("GitHub's own repository response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise GitHubConnectionError("GitHub's own repository response was not a JSON object")
    return payload


def validate_github_repository_access(
    repository_identity: str,
    *,
    access_mode: str,
    token: str,
    base_url: str = DEFAULT_GITHUB_API_BASE_URL,
    policy: ConnectionPolicy | None = None,
    fetch: GuardedFetchFn = guarded_fetch,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> GitHubRepositoryValidation:
    """Validates that `token` (a real, already-resolved GitHub App
    installation token or organization-approved token) genuinely covers
    `repository_identity` with real, read-only GitHub REST API v3 access.
    Raises one of this module's own error classes for every
    negative_acceptance case; never returns a partial or fabricated
    result."""
    identity = _validate_repository_identity(repository_identity)
    _validate_access_mode(access_mode)
    credential = _validate_token(token)

    active_policy = policy or default_github_connector_policy()
    url = f"{base_url}/repos/{identity}"
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
        raise GitHubConnectionError(
            f"could not reach GitHub to validate {identity!r}: {exc}"
        ) from exc

    if response.status != 200:
        _raise_for_non_200_status(response, identity)

    payload = _parse_repository_response(response.body)
    full_name = payload.get("full_name")
    if not isinstance(full_name, str) or full_name.lower() != identity.lower():
        raise GitHubConnectionError(
            f"GitHub's own response full_name {full_name!r} does not match the requested "
            f"repository identity {identity!r}"
        )
    default_branch = payload.get("default_branch")
    if not isinstance(default_branch, str) or not default_branch:
        raise GitHubConnectionError(
            f"GitHub's own response for {identity!r} is missing a real default_branch"
        )

    return GitHubRepositoryValidation(
        repository_identity=identity,
        resolved_full_name=full_name,
        private=bool(payload.get("private", False)),
        default_branch=default_branch,
    )
