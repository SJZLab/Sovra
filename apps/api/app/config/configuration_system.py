"""Typed, fail-fast configuration for the Factory API (FTF-0005).

`Settings.from_env()` reads every setting from the environment and validates
it eagerly, before the service can serve a request: a missing/invalid URL,
policy enum, budget or secret reference raises `ConfigurationError` at
construction time rather than surfacing later as a runtime failure or a
silently-accepted bad value. Nothing here supplies a development fallback
for a credential-shaped field, and no message this module raises embeds a
raw secret or a URL's embedded password (stdlib-only, no third-party
runtime dependency).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import TypeVar
from urllib.parse import SplitResult, urlsplit

E = TypeVar("E", bound=Enum)


class ConfigurationError(ValueError):
    """A required setting is missing, malformed, or violates policy."""


class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class PrivacyPolicy(str, Enum):
    STANDARD = "standard"
    ZERO_EGRESS = "zero_egress"
    AIR_GAPPED = "air_gapped"


_ALLOWED_DATABASE_SCHEMES = {"postgresql", "postgresql+psycopg", "postgresql+asyncpg"}
_PRODUCTION_GATEWAY_SCHEMES = {"https"}
_NON_PRODUCTION_GATEWAY_SCHEMES = _PRODUCTION_GATEWAY_SCHEMES | {"http"}
_SECRET_REF_SCHEMES = {"vault", "openbao", "secret"}
# Secret references are short path-like tokens naming where a value lives,
# never the value itself — this also rejects a raw credential pasted in by
# mistake, since real API keys/passwords don't take this shape.
_SECRET_REF_PATTERN = re.compile(r"^(?P<scheme>[a-z]+)://[A-Za-z0-9_\-./]+$")

REQUIRED_ENV_VARS = (
    "SOVRA_ENVIRONMENT",
    "SOVRA_DATABASE_URL",
    "SOVRA_LITELLM_GATEWAY_URL",
    "SOVRA_PRIVACY_POLICY",
    "SOVRA_TLS_VERIFY",
    "SOVRA_MAX_TRAINING_BUDGET_USD",
    "SOVRA_LITELLM_API_KEY_SECRET_REF",
    "SOVRA_DATABASE_PASSWORD_SECRET_REF",
)


def _redact(value: str) -> str:
    """Length-only fingerprint — never echoes the value itself."""
    return "<empty>" if not value else f"<redacted:{len(value)} chars>"


def _redact_url(raw: str) -> str:
    """Scheme+host only; strips path/query and any embedded userinfo password."""
    try:
        parsed: SplitResult = urlsplit(raw)
    except ValueError:
        return "<unparseable>"
    netloc = parsed.netloc
    if parsed.password:
        netloc = netloc.replace(f":{parsed.password}@", ":***@")
    return f"{parsed.scheme or '<no-scheme>'}://{netloc or '<no-host>'}"


def _get_required(source: Mapping[str, str], name: str) -> str:
    value = source.get(name)
    if value is None or value.strip() == "":
        raise ConfigurationError(f"{name} is required and was not set")
    return value


def _validate_url(name: str, raw: str, allowed_schemes: set[str]) -> str:
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} is not a valid URL ({_redact_url(raw)})") from exc
    if parsed.scheme not in allowed_schemes or not parsed.netloc:
        raise ConfigurationError(
            f"{name} must use one of {sorted(allowed_schemes)} with a host "
            f"(got {_redact_url(raw)})"
        )
    return raw


def _validate_enum(name: str, raw: str, enum_cls: type[E]) -> E:
    try:
        return enum_cls(raw)
    except ValueError as exc:
        allowed = [member.value for member in enum_cls]
        raise ConfigurationError(f"{name} must be one of {allowed} (got {raw!r})") from exc


def _validate_budget(name: str, raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ConfigurationError(f"{name} must be a decimal number (got {raw!r})") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be > 0 (got {value})")
    return value


def _validate_secret_ref(name: str, raw: str) -> str:
    match = _SECRET_REF_PATTERN.match(raw)
    if not match or match.group("scheme") not in _SECRET_REF_SCHEMES:
        raise ConfigurationError(
            f"{name} must be a secret reference URI with scheme in "
            f"{sorted(_SECRET_REF_SCHEMES)} (an inline value is not permitted; "
            f"got {_redact(raw)})"
        )
    return raw


def _validate_tls_verify(name: str, raw: str, environment: Environment) -> bool:
    normalized = raw.strip().lower()
    if normalized not in {"true", "false"}:
        raise ConfigurationError(f"{name} must be 'true' or 'false' (got {raw!r})")
    value = normalized == "true"
    if environment is Environment.PRODUCTION and not value:
        raise ConfigurationError(f"{name}=false is not permitted when SOVRA_ENVIRONMENT=production")
    return value


@dataclass(frozen=True)
class Settings:
    environment: Environment
    database_url: str
    litellm_gateway_url: str
    privacy_policy: PrivacyPolicy
    tls_verify: bool
    max_training_budget_usd: Decimal
    litellm_api_key_secret_ref: str
    database_password_secret_ref: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env

        environment = _validate_enum(
            "SOVRA_ENVIRONMENT", _get_required(source, "SOVRA_ENVIRONMENT"), Environment
        )
        gateway_schemes = (
            _PRODUCTION_GATEWAY_SCHEMES
            if environment is Environment.PRODUCTION
            else _NON_PRODUCTION_GATEWAY_SCHEMES
        )

        database_url = _validate_url(
            "SOVRA_DATABASE_URL",
            _get_required(source, "SOVRA_DATABASE_URL"),
            _ALLOWED_DATABASE_SCHEMES,
        )
        litellm_gateway_url = _validate_url(
            "SOVRA_LITELLM_GATEWAY_URL",
            _get_required(source, "SOVRA_LITELLM_GATEWAY_URL"),
            gateway_schemes,
        )
        privacy_policy = _validate_enum(
            "SOVRA_PRIVACY_POLICY", _get_required(source, "SOVRA_PRIVACY_POLICY"), PrivacyPolicy
        )
        tls_verify = _validate_tls_verify(
            "SOVRA_TLS_VERIFY", _get_required(source, "SOVRA_TLS_VERIFY"), environment
        )
        max_training_budget_usd = _validate_budget(
            "SOVRA_MAX_TRAINING_BUDGET_USD",
            _get_required(source, "SOVRA_MAX_TRAINING_BUDGET_USD"),
        )
        litellm_api_key_secret_ref = _validate_secret_ref(
            "SOVRA_LITELLM_API_KEY_SECRET_REF",
            _get_required(source, "SOVRA_LITELLM_API_KEY_SECRET_REF"),
        )
        database_password_secret_ref = _validate_secret_ref(
            "SOVRA_DATABASE_PASSWORD_SECRET_REF",
            _get_required(source, "SOVRA_DATABASE_PASSWORD_SECRET_REF"),
        )

        return cls(
            environment=environment,
            database_url=database_url,
            litellm_gateway_url=litellm_gateway_url,
            privacy_policy=privacy_policy,
            tls_verify=tls_verify,
            max_training_budget_usd=max_training_budget_usd,
            litellm_api_key_secret_ref=litellm_api_key_secret_ref,
            database_password_secret_ref=database_password_secret_ref,
        )

    def __repr__(self) -> str:
        # Secret-shaped fields and URL userinfo never appear in logs/tracebacks.
        return (
            "Settings("
            f"environment={self.environment.value!r}, "
            f"database_url={_redact_url(self.database_url)!r}, "
            f"litellm_gateway_url={self.litellm_gateway_url!r}, "
            f"privacy_policy={self.privacy_policy.value!r}, "
            f"tls_verify={self.tls_verify!r}, "
            f"max_training_budget_usd={self.max_training_budget_usd!r}, "
            f"litellm_api_key_secret_ref={_redact(self.litellm_api_key_secret_ref)}, "
            f"database_password_secret_ref={_redact(self.database_password_secret_ref)})"
        )
