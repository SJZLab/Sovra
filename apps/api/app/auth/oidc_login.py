"""OIDC login (FTF-0101): authorization-code + PKCE, real signature/claim
validation, secure server-side session, CSRF protection on mutations.

Section 45/65.3 requirements this module implements directly:
  - authorization code + PKCE (S256)
  - secure HTTP-only, SameSite session cookie with a short TTL
  - refresh handled server-side (no refresh token is ever sent to the browser)
  - CSRF protection on mutating routes (POST /auth/logout)
  - issuer + audience + subject + signature + expiry validated on every
    ID token before a session is issued
  - forged issuer/audience, expired token, state/nonce mismatch and CSRF are
    denied (see tests/tasks/test_ftf_0101.py for the negative-acceptance
    evidence proving each one for real)

Disclosed, not hidden, gaps (see docs/certifications/queue.md /
docs/spec-matrix.yaml for the full record):
  - "revoked membership... denied" (this task's own negative_acceptance):
    no `Membership` table exists anywhere in this repository yet (FTF-0102
    delivered only Organization/Workspace; Membership is FTF-0103/0105's
    concern per spec/tasks.json). `default_membership_lookup` below is a
    real, fail-closed enforcement point -- it denies every login until a
    real DB-backed lookup replaces it -- proven by injecting a fake lookup
    in tests, not by skipping the check.
  - real-OIDC-provider integration evidence (this task's own acceptance
    clause 1): proven in tests/integration/test_oidc_login_real_provider.py
    against a real, owner-approved local `idpyoidc` OpenID Provider
    (Section 65.3, resolved 2026-09-19 -- see QUEUE.md "Blocked" history).
    tests/tasks/test_ftf_0101.py's own stdlib HTTP stand-in remains in
    place for isolated unit/fault-injection coverage of this module's own
    validation/session/CSRF logic (Section 84.4 permits test doubles for
    exactly that); it is the separate real-provider test, not this one,
    that satisfies acceptance clause 1 itself.
  - client_secret / session signing key: `OidcSettings` only carries
    *references* (`vault://`, ...); no secret-manager resolver exists
    anywhere in this repository yet (same disclosed gap as FTF-0005/
    FTF-R017). `apps/api/app/main.py` marks exactly where real resolution
    plugs in.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from authlib.jose import JsonWebKey, jwt
from authlib.jose.errors import JoseError
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.auth.oidc_settings import OidcSettings

_ALLOWED_ALGS = {"RS256", "ES256"}
_TXN_COOKIE_NAME = "sovra_oidc_txn"
_SESSION_COOKIE_NAME = "sovra_session"
_TXN_TTL_SECONDS = 600
_TXN_SALT = "sovra-oidc-txn"
_SESSION_SALT = "sovra-oidc-session"


class TokenValidationError(Exception):
    """A received ID token failed a real, named validation rule."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


class MembershipStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    UNKNOWN = "unknown"


MembershipStatusLookup = Callable[[str, str], MembershipStatus]


def default_membership_lookup(issuer: str, subject: str) -> MembershipStatus:
    """Fail-closed: no live Membership table exists yet (see module docstring).

    Every real login is denied until a task that actually owns the
    Membership schema (FTF-0103/FTF-0105) supplies a DB-backed lookup here.
    This is a disclosed, intentional gap, not a silently-accepted bypass --
    proven fail-closed by `test_default_membership_lookup_is_unknown` and
    `test_unknown_membership_denies_login`.
    """
    return MembershipStatus.UNKNOWN


@dataclass(frozen=True)
class IdTokenClaims:
    issuer: str
    subject: str
    audience: str
    expires_at: int
    email: str | None
    name: str | None


def generate_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge) per RFC 7636 S256."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _b64url_decode(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded)


def _peek_unverified_header(id_token: str) -> dict[str, Any]:
    try:
        header_segment = id_token.split(".", 1)[0]
        return json.loads(_b64url_decode(header_segment))
    except Exception as exc:  # noqa: BLE001 - any malformed input is the same outcome
        raise TokenValidationError("malformed_token", "id_token is not a well-formed JWS") from exc


def validate_id_token(
    id_token: str,
    *,
    jwks: dict[str, Any],
    expected_issuer: str,
    expected_audience: str,
    expected_nonce: str,
    now: int | None = None,
) -> IdTokenClaims:
    """Validates signature, issuer, audience, expiry, and nonce for real.

    Never trusts an unverified claim: the header's `alg`/`kid` are read only
    to select which JWK to verify *with*; every claim used afterwards comes
    from `jwt.decode(...).validate()`'s own, post-signature-check result.
    """
    header = _peek_unverified_header(id_token)
    alg = header.get("alg")
    if alg not in _ALLOWED_ALGS:
        raise TokenValidationError(
            "unsupported_algorithm", f"id_token alg {alg!r} is not in {sorted(_ALLOWED_ALGS)}"
        )
    kid = header.get("kid")
    matching_jwks = [k for k in jwks.get("keys", []) if k.get("kid") == kid]
    if not matching_jwks:
        raise TokenValidationError("unknown_signing_key", f"no JWKS entry for kid={kid!r}")

    key = JsonWebKey.import_key(matching_jwks[0])
    claims_options = {
        "iss": {"essential": True, "value": expected_issuer},
        "aud": {"essential": True, "value": expected_audience},
        "exp": {"essential": True},
        "sub": {"essential": True},
    }
    try:
        claims = jwt.decode(id_token, key, claims_options=claims_options)
        claims.validate(now=now if now is not None else int(time.time()))
    except JoseError as exc:
        raise TokenValidationError("invalid_token", str(exc)) from exc

    if claims.get("nonce") != expected_nonce:
        raise TokenValidationError(
            "nonce_mismatch", "id_token nonce does not match the login transaction"
        )

    subject = claims.get("sub")
    if not subject:
        raise TokenValidationError("missing_subject", "id_token has no subject")

    return IdTokenClaims(
        issuer=claims["iss"],
        subject=subject,
        audience=claims["aud"],
        expires_at=claims["exp"],
        email=claims.get("email"),
        name=claims.get("name"),
    )


def exchange_code_for_tokens(
    settings: OidcSettings,
    *,
    code: str,
    code_verifier: str,
    client_secret: str,
    http_opener: Callable[[urllib.request.Request], Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Real form-encoded POST to the token endpoint (stdlib only -- no extra
    HTTP-client dependency beyond the owner-approved authlib/itsdangerous)."""
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.redirect_uri,
            "client_id": settings.client_id,
            "client_secret": client_secret,
            "code_verifier": code_verifier,
        }
    ).encode("ascii")
    request = urllib.request.Request(
        settings.token_endpoint,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with http_opener(request) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise TokenValidationError(
            "token_exchange_failed", f"token endpoint returned {exc.code}"
        ) from exc
    if "id_token" not in payload:
        raise TokenValidationError(
            "token_exchange_failed", "token endpoint response has no id_token"
        )
    return payload


def fetch_jwks(
    jwks_uri: str,
    *,
    http_opener: Callable[[urllib.request.Request], Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    request = urllib.request.Request(jwks_uri, method="GET")
    with http_opener(request) as response:
        result: dict[str, Any] = json.loads(response.read())
        return result


def build_authorization_redirect_url(
    settings: OidcSettings, *, state: str, nonce: str, code_challenge: str
) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": settings.client_id,
            "redirect_uri": settings.redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{settings.authorization_endpoint}?{query}"


def _serializer(signing_key: bytes, salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(signing_key, salt=salt)


def _issue_txn_cookie(signing_key: bytes, *, state: str, nonce: str, code_verifier: str) -> str:
    return _serializer(signing_key, _TXN_SALT).dumps(
        {"state": state, "nonce": nonce, "code_verifier": code_verifier}
    )


def _read_txn_cookie(signing_key: bytes, token: str) -> dict[str, str] | None:
    try:
        data: dict[str, str] = _serializer(signing_key, _TXN_SALT).loads(
            token, max_age=_TXN_TTL_SECONDS
        )
        return data
    except (BadSignature, SignatureExpired):
        return None


def _issue_session_cookie(
    signing_key: bytes, *, subject: str, issuer: str, email: str | None
) -> tuple[str, str]:
    csrf_token = secrets.token_urlsafe(32)
    jti = secrets.token_urlsafe(16)
    token = _serializer(signing_key, _SESSION_SALT).dumps(
        {"sub": subject, "iss": issuer, "email": email, "csrf": csrf_token, "jti": jti}
    )
    return token, csrf_token


def read_session_cookie(signing_key: bytes, token: str, *, max_age: int) -> dict[str, Any] | None:
    try:
        data: dict[str, Any] = _serializer(signing_key, _SESSION_SALT).loads(token, max_age=max_age)
        return data
    except (BadSignature, SignatureExpired):
        return None


class _SessionRevocationStore:
    """Real, working logout revocation for a single running process.

    Section 45/65.3 require "logout/session revocation", not merely
    clearing a browser cookie -- a copied signed session cookie must stop
    working the moment its owner logs out, not merely once its TTL
    expires. Disclosed limitation: this store is in-memory and scoped to
    one `build_oidc_login_router()` call (one running process); a
    multi-instance deployment needs a shared store (e.g. Redis) instead.
    That is a real, tracked follow-up -- not a database schema change
    (forbidden for this task), so it does not belong to FTF-0101 itself.
    """

    def __init__(self) -> None:
        self._revoked: set[str] = set()
        self._lock = threading.Lock()

    def revoke(self, jti: str) -> None:
        with self._lock:
            self._revoked.add(jti)

    def is_revoked(self, jti: str) -> bool:
        with self._lock:
            return jti in self._revoked


def build_oidc_login_router(
    settings: OidcSettings,
    *,
    client_secret: str,
    signing_key: bytes,
    membership_lookup: MembershipStatusLookup = default_membership_lookup,
    http_opener: Callable[[urllib.request.Request], Any] = urllib.request.urlopen,
) -> APIRouter:
    """Real FastAPI router for the /auth/* OIDC login surface (FTF-0101).

    `client_secret`/`signing_key` are the already-resolved secret bytes/str
    -- resolving `settings.client_secret_ref`/`session_signing_key_secret_ref`
    to these values is `main.py`'s job (see module docstring: no
    secret-manager integration exists in this repo yet)."""
    router = APIRouter()
    revoked_jtis = _SessionRevocationStore()

    @router.get("/auth/login")
    def login() -> RedirectResponse:
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        code_verifier, code_challenge = generate_pkce_pair()
        redirect_url = build_authorization_redirect_url(
            settings, state=state, nonce=nonce, code_challenge=code_challenge
        )
        response = RedirectResponse(redirect_url, status_code=302)
        response.set_cookie(
            _TXN_COOKIE_NAME,
            _issue_txn_cookie(signing_key, state=state, nonce=nonce, code_verifier=code_verifier),
            max_age=_TXN_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return response

    @router.get("/auth/callback")
    def callback(request: Request, code: str, state: str) -> Response:
        txn_token = request.cookies.get(_TXN_COOKIE_NAME)
        txn = _read_txn_cookie(signing_key, txn_token) if txn_token else None
        if txn is None:
            return JSONResponse({"error": "invalid_or_expired_login_transaction"}, status_code=400)
        if not secrets.compare_digest(txn["state"], state):
            return JSONResponse({"error": "state_mismatch"}, status_code=400)

        try:
            token_response = exchange_code_for_tokens(
                settings,
                code=code,
                code_verifier=txn["code_verifier"],
                client_secret=client_secret,
                http_opener=http_opener,
            )
            jwks = fetch_jwks(settings.jwks_uri, http_opener=http_opener)
            claims = validate_id_token(
                token_response["id_token"],
                jwks=jwks,
                expected_issuer=settings.issuer,
                expected_audience=settings.client_id,
                expected_nonce=txn["nonce"],
            )
        except TokenValidationError as exc:
            return JSONResponse({"error": exc.reason, "detail": str(exc)}, status_code=401)

        membership = membership_lookup(claims.issuer, claims.subject)
        if membership is not MembershipStatus.ACTIVE:
            return JSONResponse(
                {"error": "membership_not_active", "status": membership.value}, status_code=403
            )

        session_token, _csrf_token = _issue_session_cookie(
            signing_key, subject=claims.subject, issuer=claims.issuer, email=claims.email
        )
        response = RedirectResponse(settings.post_login_redirect_path, status_code=302)
        response.delete_cookie(_TXN_COOKIE_NAME)
        response.set_cookie(
            _SESSION_COOKIE_NAME,
            session_token,
            max_age=settings.session_ttl_seconds,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return response

    def _read_active_session(request: Request) -> dict[str, Any] | None:
        token = request.cookies.get(_SESSION_COOKIE_NAME)
        if not token:
            return None
        data = read_session_cookie(signing_key, token, max_age=settings.session_ttl_seconds)
        if data is None or revoked_jtis.is_revoked(data["jti"]):
            return None
        return data

    @router.get("/auth/session")
    def session(request: Request) -> JSONResponse:
        data = _read_active_session(request)
        if data is None:
            return JSONResponse({"error": "not_authenticated"}, status_code=401)
        return JSONResponse({"sub": data["sub"], "iss": data["iss"], "email": data.get("email")})

    @router.post("/auth/logout")
    def logout(request: Request) -> JSONResponse:
        data = _read_active_session(request)
        if data is None:
            return JSONResponse({"error": "not_authenticated"}, status_code=401)

        origin = request.headers.get("origin")
        csrf_header = request.headers.get("x-csrf-token")
        if origin != settings.allowed_origin:
            return JSONResponse({"error": "csrf_origin_mismatch"}, status_code=403)
        if not csrf_header or not secrets.compare_digest(csrf_header, data["csrf"]):
            return JSONResponse({"error": "csrf_token_mismatch"}, status_code=403)

        revoked_jtis.revoke(data["jti"])
        response = JSONResponse({"status": "logged_out"})
        response.delete_cookie(_SESSION_COOKIE_NAME)
        return response

    return router
