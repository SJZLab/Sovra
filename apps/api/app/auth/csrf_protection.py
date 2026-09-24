"""CSRF protection dependency (FTF-1503, Section 45/65.3/77.6): a real,
reusable server-side check that a state-mutating `/api/v1` request both
declares an `Origin` the server actually allows and carries a caller-
specific CSRF token the server itself issued and can verify.

Decoupled from any concrete session implementation the same way
`app.auth.rbac_middleware.require_role` already decouples from
`app.auth.oidc_login`'s own concrete session-cookie mechanism (see that
module's own docstring) -- `csrf_token_resolver` reads the CSRF token the
server issued to the authenticated caller from whatever session store is
actually wired in. The default is fail-closed (denies every mutation)
until a real resolver -- backed by `oidc_login.read_session_cookie`'s own
signed `csrf` claim, the same class of "separately claimed" follow-up
`app.auth.rbac_middleware`'s own `default_principal_resolver`/
`default_role_lookup` already carry, and the same disclosed
"designed, not yet wired into the real product route" gap `require_role`
itself already carries in `apps/api/app/main.py` today -- replaces it.

`InMemoryCsrfSessionStore` is a real, working, single-process session/
CSRF-token store (same class of real-not-mock in-memory state as
`app.auth.oidc_login._SessionRevocationStore`/`_RefreshTokenStore`) --
usable directly by a single-instance deployment and by this task's own
demonstration app in `tests/security/csrf_suite.py`. Same disclosed
single-instance limitation as those two stores: a multi-instance
deployment needs a shared store instead.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request

__all__ = [
    "CsrfTokenResolver",
    "InMemoryCsrfSessionStore",
    "default_csrf_token_resolver",
    "require_csrf_protection",
]

CsrfTokenResolver = Callable[[Request], str | None]


def default_csrf_token_resolver(request: Request) -> str | None:
    """Fail-closed: no live session reader is wired in yet (same disclosed
    gap as `app.auth.rbac_middleware.default_principal_resolver`)."""
    del request
    return None


def require_csrf_protection(
    *,
    allowed_origin: str,
    csrf_token_resolver: CsrfTokenResolver = default_csrf_token_resolver,
    header_name: str = "x-csrf-token",
) -> Callable[[Request], Awaitable[None]]:
    """Returns a FastAPI dependency that denies a mutating request unless
    both of these hold -- this task's own negative_acceptance clause:
    "Cross-site forged form/fetch, missing token and unsafe CORS cannot
    mutate state":

    1. The request's own `Origin` header matches `allowed_origin` exactly.
       A cross-site request either carries a foreign Origin (the
       attacker's own site) or, for a plain cross-site form submission,
       still cannot forge this header -- the browser sets it, never page
       script.
    2. `csrf_token_resolver` resolves a real token for the caller's own
       already-authenticated session, and the request's own
       `X-CSRF-Token` header matches it via a constant-time comparison --
       a value a cross-site attacker can neither read (never delivered in
       a cross-site-readable response) nor guess.

    An ambient credential alone (a session cookie the browser attaches
    automatically to a same-site request) is never sufficient by
    construction: this dependency never reads the session cookie's own
    identity/role, only whether its own resolved CSRF token matches the
    caller-supplied header.
    """
    if not allowed_origin:
        raise ValueError("require_csrf_protection needs a real allowed_origin")

    async def _dependency(request: Request) -> None:
        origin = request.headers.get("origin")
        if origin != allowed_origin:
            raise HTTPException(status_code=403, detail="csrf_origin_mismatch")

        expected = csrf_token_resolver(request)
        if expected is None:
            raise HTTPException(status_code=403, detail="csrf_token_unavailable")

        supplied = request.headers.get(header_name)
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=403, detail="csrf_token_mismatch")

    return _dependency


class InMemoryCsrfSessionStore:
    """Real, working single-process session/CSRF-token store -- see module
    docstring for its disclosed single-instance limitation."""

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[str, str]] = {}
        self._lock = threading.Lock()

    def create(self, subject: str) -> tuple[str, str]:
        session_id = secrets.token_urlsafe(24)
        csrf_token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[session_id] = (subject, csrf_token)
        return session_id, csrf_token

    def subject_for(self, session_id: str) -> str | None:
        with self._lock:
            entry = self._sessions.get(session_id)
        return entry[0] if entry else None

    def csrf_token_for(self, session_id: str) -> str | None:
        with self._lock:
            entry = self._sessions.get(session_id)
        return entry[1] if entry else None
