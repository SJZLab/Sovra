"""Session principal resolver (FTF-0103A, Section 45-47/65.3).

The real `PrincipalResolver` FTF-0103's `require_role` takes: it reads the
signed session cookie FTF-0101's `/auth/callback` issues, verifies the
signature and age, and checks the session has not been revoked (logout or a
failed refresh), then returns the cookie's validated `(issuer, subject)`.

It reads the cookie and nothing else. A header, query parameter or request
body can never supply or change an identity (spec negative_acceptance): there
is no code path here that looks at any of them. Every missing, empty,
garbage, tampered, foreign-key-signed, expired or revoked cookie resolves to
`None`, which `require_role` turns into 401.

The revocation store is the SAME object `build_oidc_login_router` is given
(`revocations=`), so `/auth/logout` revokes a session for the API too.
Disclosed limitation (inherited from FTF-0101, unchanged here): the store is
in-memory per process.
"""

from __future__ import annotations

import logging

from fastapi import Request

from app.auth.oidc_login import SESSION_COOKIE_NAME, SessionRevocationStore, read_session_cookie
from app.auth.rbac_middleware import AuthenticatedPrincipal

__all__ = ["SessionPrincipalResolver"]

_decision_log = logging.getLogger("sovra.auth.decision")


class SessionPrincipalResolver:
    def __init__(
        self, *, signing_key: bytes, max_age_seconds: int, revocations: SessionRevocationStore
    ) -> None:
        self._signing_key = signing_key
        self._max_age_seconds = max_age_seconds
        self._revocations = revocations

    def __call__(self, request: Request) -> AuthenticatedPrincipal | None:
        request_id = request.headers.get("x-request-id", "")[:64]
        token = request.cookies.get(SESSION_COOKIE_NAME)
        if not token:
            return self._deny("no_session", request_id)
        data = read_session_cookie(self._signing_key, token, max_age=self._max_age_seconds)
        if data is None:
            return self._deny("invalid_or_expired_session", request_id)
        jti, issuer, subject = data.get("jti"), data.get("iss"), data.get("sub")
        if not (
            isinstance(jti, str)
            and isinstance(issuer, str)
            and isinstance(subject, str)
            and issuer
            and subject
        ):
            return self._deny("malformed_session", request_id)
        if self._revocations.is_revoked(jti):
            return self._deny("revoked_session", request_id)
        return AuthenticatedPrincipal(issuer=issuer, subject=subject)

    @staticmethod
    def _deny(reason: str, request_id: str) -> None:
        _decision_log.info(
            "auth_decision",
            extra={"outcome": "denied", "reason": reason, "request_id": request_id},
        )
        return None
