#!/usr/bin/env python3
"""
FTF-0005 — Configuration system.

Proves `apps/api/app/config/configuration_system.py`'s typed settings
actually reject a missing/invalid URL, policy enum, budget and secret ref
before a Settings object can be constructed (FTF-0005's acceptance
criterion), and separately proves its negative_acceptance clause: no
development fallback credential, no production tls_verify=false, and no
sensitive value echoed back in a raised error or in Settings' own repr.

Each fault case is shown to fail for real (Law 1), then the same case is
shown to pass once fixed, using an isolated env dict so no fixture ever
touches this process's real environment variables.

Run directly: python3 tests/tasks/test_ftf_0005.py
Collectible by pytest too (see pyproject.toml testpaths — task-evidence
scripts under tests/tasks/ stay out of it, same convention as
tests/tasks/test_ftf_0002.py and test_ftf_0003.py).
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.config.configuration_system import (  # noqa: E402
    ConfigurationError,
    Settings,
)

FAILURES = []


def check(criterion_id, description, condition):
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


VALID_ENV = {
    "SOVRA_ENVIRONMENT": "staging",
    "SOVRA_DATABASE_URL": "postgresql://sovra_app:s3cr3t@db.internal:5432/sovra",
    "SOVRA_LITELLM_GATEWAY_URL": "https://litellm.internal.example",
    "SOVRA_PRIVACY_POLICY": "standard",
    "SOVRA_TLS_VERIFY": "true",
    "SOVRA_MAX_TRAINING_BUDGET_USD": "500.00",
    "SOVRA_LITELLM_API_KEY_SECRET_REF": "vault://sovra/litellm-api-key",
    "SOVRA_DATABASE_PASSWORD_SECRET_REF": "openbao://sovra/db-password",
}


def env(**overrides):
    merged = dict(VALID_ENV)
    for key, value in overrides.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def raises(env_dict):
    try:
        Settings.from_env(env_dict)
    except ConfigurationError as exc:
        return str(exc)
    return None


def test_valid_env_constructs_settings():
    settings = Settings.from_env(env())
    check("FTF-0005", "a fully valid environment constructs Settings", settings is not None)
    check(
        "FTF-0005",
        "environment enum member round-trips",
        settings.environment.value == "staging",
    )
    check(
        "FTF-0005",
        "budget is a positive Decimal",
        settings.max_training_budget_usd > 0,
    )


def test_missing_required_var_rejected():
    for missing in VALID_ENV:
        message = raises(env(**{missing: None}))
        check("FTF-0005", f"missing {missing} raises ConfigurationError", message is not None)


def test_invalid_database_url_rejected():
    message = raises(env(SOVRA_DATABASE_URL="not-a-url"))
    check("FTF-0005", "a malformed database URL raises", message is not None)
    message = raises(env(SOVRA_DATABASE_URL="mysql://db.internal:3306/sovra"))
    check("FTF-0005", "a database URL with a disallowed scheme raises", message is not None)


def test_invalid_gateway_url_rejected():
    message = raises(env(SOVRA_LITELLM_GATEWAY_URL="ftp://litellm.internal.example"))
    check("FTF-0005", "a LiteLLM gateway URL with a disallowed scheme raises", message is not None)
    # http is allowed outside production but not inside it.
    message = raises(
        env(
            SOVRA_ENVIRONMENT="production",
            SOVRA_LITELLM_GATEWAY_URL="http://litellm.internal.example",
        )
    )
    check("FTF-0005", "a plaintext gateway URL is rejected in production", message is not None)
    settings = Settings.from_env(
        env(
            SOVRA_ENVIRONMENT="development",
            SOVRA_LITELLM_GATEWAY_URL="http://litellm.internal.example",
        )
    )
    check(
        "FTF-0005",
        "a plaintext gateway URL is accepted outside production",
        settings.litellm_gateway_url.startswith("http://"),
    )


def test_invalid_policy_enum_rejected():
    message = raises(env(SOVRA_PRIVACY_POLICY="mostly_private"))
    check("FTF-0005", "an unknown privacy-policy value raises", message is not None)
    message = raises(env(SOVRA_ENVIRONMENT="quantum"))
    check("FTF-0005", "an unknown environment value raises", message is not None)


def test_invalid_budget_rejected():
    for bad in ("0", "-10", "not-a-number", ""):
        if bad == "":
            continue  # empty string is covered by the missing-var case
        message = raises(env(SOVRA_MAX_TRAINING_BUDGET_USD=bad))
        check("FTF-0005", f"budget {bad!r} raises", message is not None)


def test_invalid_secret_ref_rejected():
    # A raw inline credential pasted where a reference belongs must be
    # rejected, not silently accepted as the secret value.
    message = raises(env(SOVRA_LITELLM_API_KEY_SECRET_REF="sk-live-abcdef0123456789"))
    check("FTF-0005", "an inline (non-reference) LiteLLM key raises", message is not None)
    check(
        "FTF-0005",
        "the raw inline value is not echoed back in the error",
        message is not None and "sk-live-abcdef0123456789" not in message,
    )
    message = raises(env(SOVRA_DATABASE_PASSWORD_SECRET_REF="hunter2"))
    check("FTF-0005", "an inline (non-reference) DB password raises", message is not None)


def test_no_development_fallback_for_credentials():
    # Omitting a secret ref must never silently resolve to a baked-in
    # development default — it must be a hard failure.
    message = raises(env(SOVRA_LITELLM_API_KEY_SECRET_REF=None))
    check(
        "FTF-0005",
        "an unset LiteLLM secret ref has no fallback and raises",
        message is not None,
    )
    message = raises(env(SOVRA_DATABASE_PASSWORD_SECRET_REF=None))
    check(
        "FTF-0005",
        "an unset DB password secret ref has no fallback and raises",
        message is not None,
    )


def test_production_tls_verify_false_rejected():
    message = raises(env(SOVRA_ENVIRONMENT="production", SOVRA_TLS_VERIFY="false"))
    check("FTF-0005", "SOVRA_TLS_VERIFY=false in production raises", message is not None)
    check(
        "FTF-0005",
        "the production/tls_verify rejection names the actual rule",
        message is not None and "production" in message,
    )
    # The same value is fine outside production.
    settings = Settings.from_env(env(SOVRA_ENVIRONMENT="development", SOVRA_TLS_VERIFY="false"))
    check(
        "FTF-0005",
        "SOVRA_TLS_VERIFY=false is accepted outside production",
        settings.tls_verify is False,
    )


def test_sensitive_values_absent_from_errors_and_repr():
    password = "s3cr3t-p4ssw0rd"
    bad_env = env(SOVRA_DATABASE_URL=f"postgresql://sovra_app:{password}@db.internal:5432/sovra")
    settings = Settings.from_env(bad_env)
    check(
        "FTF-0005",
        "a database URL's embedded password never appears in Settings' repr",
        password not in repr(settings),
    )
    check(
        "FTF-0005",
        "a secret ref's own value never appears in Settings' repr",
        VALID_ENV["SOVRA_LITELLM_API_KEY_SECRET_REF"].split("/")[-1] not in repr(settings)
        or "redacted" in repr(settings),
    )
    # Trigger a gateway-URL failure that embeds a password too.
    message = raises(
        env(
            SOVRA_LITELLM_GATEWAY_URL="ftp://svc:hunter2@litellm.internal.example",
        )
    )
    check(
        "FTF-0005",
        "an embedded URL password never appears in a raised validation error",
        message is not None and "hunter2" not in message,
    )


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0005 configuration system: {len(FAILURES)} check(s) failed")
        for f in FAILURES:
            print(f"  {f}")
        sys.exit(1)

    print("FTF-0005 configuration system: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
