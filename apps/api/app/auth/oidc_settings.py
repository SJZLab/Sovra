"""Typed, fail-fast OIDC/session configuration for FTF-0101 (OIDC login).

Deliberately a separate module from `apps/api/app/config/configuration_system.py`
(FTF-0005, already `CERTIFIED`): adding OIDC-specific required environment
variables to that shared `Settings.from_env()` would make every existing,
independently-certified `test_ftf_0005.py` env fixture newly invalid,
regressing certified evidence for a task that does not need OIDC config
(Law 15). This module follows the same idioms deliberately (fail-fast at
construction, secret references never inline values, no value echoed by a
raised message) without touching the certified file.

`client_secret_ref` / `session_signing_key_secret_ref` are secret
*references* only, exactly like FTF-0005's own fields — this module never
resolves them to a real secret value. Resolving a `vault://`/`openbao://`/
`secret://` reference to bytes requires a real secret-manager integration
that does not exist anywhere in this repository yet (same disclosed gap
FTF-0005 and FTF-R017 already carry); `apps/api/app/main.py` documents
exactly where that wiring plugs in.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

REQUIRED_OIDC_ENV_VARS = (
    "SOVRA_OIDC_ISSUER",
    "SOVRA_OIDC_CLIENT_ID",
    "SOVRA_OIDC_CLIENT_SECRET_REF",
    "SOVRA_OIDC_REDIRECT_URI",
    "SOVRA_OIDC_AUTHORIZATION_ENDPOINT",
    "SOVRA_OIDC_TOKEN_ENDPOINT",
    "SOVRA_OIDC_JWKS_URI",
    "SOVRA_SESSION_SIGNING_KEY_SECRET_REF",
    "SOVRA_OIDC_ALLOWED_ORIGIN",
)

_SECRET_REF_SCHEMES = {"vault", "openbao", "secret"}
_SECRET_REF_PATTERN = re.compile(r"^(?P<scheme>[a-z]+)://[A-Za-z0-9_\-./]+$")
_PRODUCTION_SCHEMES = {"https"}
_NON_PRODUCTION_SCHEMES = _PRODUCTION_SCHEMES | {"http"}


class OidcConfigurationError(ValueError):
    """A required OIDC/session setting is missing, malformed, or violates policy."""


def _get_required(source: Mapping[str, str], name: str) -> str:
    value = source.get(name)
    if value is None or value.strip() == "":
        raise OidcConfigurationError(f"{name} is required and was not set")
    return value


def _redact_url(raw: str) -> str:
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return "<unparseable>"
    netloc = parsed.netloc
    if parsed.password:
        netloc = netloc.replace(f":{parsed.password}@", ":***@")
    return f"{parsed.scheme or '<no-scheme>'}://{netloc or '<no-host>'}"


def _validate_url(name: str, raw: str, *, allowed_schemes: set[str]) -> str:
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise OidcConfigurationError(f"{name} is not a valid URL ({_redact_url(raw)})") from exc
    if parsed.scheme not in allowed_schemes or not parsed.netloc:
        raise OidcConfigurationError(
            f"{name} must use one of {sorted(allowed_schemes)} with a host "
            f"(got {_redact_url(raw)})"
        )
    return raw


def _validate_secret_ref(name: str, raw: str) -> str:
    match = _SECRET_REF_PATTERN.match(raw)
    if not match or match.group("scheme") not in _SECRET_REF_SCHEMES:
        raise OidcConfigurationError(
            f"{name} must be a secret reference URI with scheme in "
            f"{sorted(_SECRET_REF_SCHEMES)} (an inline value is not permitted; "
            f"got <redacted:{len(raw)} chars>)"
        )
    return raw


def _validate_redirect_path(name: str, raw: str) -> str:
    # Only an internal, single-leading-slash path is permitted as a
    # post-login redirect target -- an absolute URL or "//host" would be an
    # open-redirect primitive (Section 65.4's outbound-guard intent applied
    # to the one browser-facing redirect this task itself introduces).
    if not raw.startswith("/") or raw.startswith("//"):
        raise OidcConfigurationError(
            f"{name} must be an internal path starting with '/' (got {raw!r})"
        )
    return raw


def _validate_ttl(name: str, raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise OidcConfigurationError(
            f"{name} must be an integer number of seconds (got {raw!r})"
        ) from exc
    if not (0 < value <= 86400):
        raise OidcConfigurationError(f"{name} must be > 0 and <= 86400 (got {value})")
    return value


@dataclass(frozen=True)
class OidcSettings:
    issuer: str
    client_id: str
    client_secret_ref: str
    redirect_uri: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    session_signing_key_secret_ref: str
    allowed_origin: str
    post_login_redirect_path: str
    session_ttl_seconds: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, production: bool) -> OidcSettings:
        import os

        source = env if env is not None else os.environ
        schemes = _PRODUCTION_SCHEMES if production else _NON_PRODUCTION_SCHEMES

        issuer = _validate_url(
            "SOVRA_OIDC_ISSUER", _get_required(source, "SOVRA_OIDC_ISSUER"), allowed_schemes=schemes
        )
        client_id = _get_required(source, "SOVRA_OIDC_CLIENT_ID")
        client_secret_ref = _validate_secret_ref(
            "SOVRA_OIDC_CLIENT_SECRET_REF", _get_required(source, "SOVRA_OIDC_CLIENT_SECRET_REF")
        )
        redirect_uri = _validate_url(
            "SOVRA_OIDC_REDIRECT_URI",
            _get_required(source, "SOVRA_OIDC_REDIRECT_URI"),
            allowed_schemes=schemes,
        )
        authorization_endpoint = _validate_url(
            "SOVRA_OIDC_AUTHORIZATION_ENDPOINT",
            _get_required(source, "SOVRA_OIDC_AUTHORIZATION_ENDPOINT"),
            allowed_schemes=schemes,
        )
        token_endpoint = _validate_url(
            "SOVRA_OIDC_TOKEN_ENDPOINT",
            _get_required(source, "SOVRA_OIDC_TOKEN_ENDPOINT"),
            allowed_schemes=schemes,
        )
        jwks_uri = _validate_url(
            "SOVRA_OIDC_JWKS_URI",
            _get_required(source, "SOVRA_OIDC_JWKS_URI"),
            allowed_schemes=schemes,
        )
        session_signing_key_secret_ref = _validate_secret_ref(
            "SOVRA_SESSION_SIGNING_KEY_SECRET_REF",
            _get_required(source, "SOVRA_SESSION_SIGNING_KEY_SECRET_REF"),
        )
        allowed_origin = _validate_url(
            "SOVRA_OIDC_ALLOWED_ORIGIN",
            _get_required(source, "SOVRA_OIDC_ALLOWED_ORIGIN"),
            allowed_schemes=schemes,
        )
        post_login_redirect_path = _validate_redirect_path(
            "SOVRA_OIDC_POST_LOGIN_REDIRECT_PATH",
            source.get("SOVRA_OIDC_POST_LOGIN_REDIRECT_PATH", "/"),
        )
        session_ttl_seconds = _validate_ttl(
            "SOVRA_SESSION_TTL_SECONDS", source.get("SOVRA_SESSION_TTL_SECONDS", "900")
        )

        return cls(
            issuer=issuer,
            client_id=client_id,
            client_secret_ref=client_secret_ref,
            redirect_uri=redirect_uri,
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            jwks_uri=jwks_uri,
            session_signing_key_secret_ref=session_signing_key_secret_ref,
            allowed_origin=allowed_origin,
            post_login_redirect_path=post_login_redirect_path,
            session_ttl_seconds=session_ttl_seconds,
        )

    def __repr__(self) -> str:
        return (
            "OidcSettings("
            f"issuer={_redact_url(self.issuer)!r}, "
            f"client_id={self.client_id!r}, "
            f"client_secret_ref=<redacted:{len(self.client_secret_ref)} chars>, "
            f"redirect_uri={_redact_url(self.redirect_uri)!r}, "
            f"authorization_endpoint={_redact_url(self.authorization_endpoint)!r}, "
            f"token_endpoint={_redact_url(self.token_endpoint)!r}, "
            f"jwks_uri={_redact_url(self.jwks_uri)!r}, "
            "session_signing_key_secret_ref="
            f"<redacted:{len(self.session_signing_key_secret_ref)} chars>, "
            f"allowed_origin={_redact_url(self.allowed_origin)!r}, "
            f"post_login_redirect_path={self.post_login_redirect_path!r}, "
            f"session_ttl_seconds={self.session_ttl_seconds!r})"
        )
