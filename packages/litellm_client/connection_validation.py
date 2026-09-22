"""LiteLLM connection validation (FTF-0203, Section 2-3/38/65/77/81).

`apps/api/app/litellm/connection_schema.py` (FTF-0201) validated a
connection's `base_url`/`secret_ref` *shape* only and explicitly deferred
dialing the gateway to this task; `packages/litellm_client/
secret_manager_integration.py` (FTF-0202) resolves a `secret_ref` to a
real credential value but has no caller yet; `packages/security/
outbound_guard.py` (FTF-H006) is the only module allowed to open an
outbound socket on a caller-supplied host, but nothing called it yet
either. This module is the first real caller of both -- exactly the two
disclosed "no live caller yet" gaps those tasks' own submissions named
FTF-0203 as the one that would close.

Section 3.1's two capability dimensions are validated and reported
**separately**, never collapsed into one boolean: `GET /v1/models`
proves the least-privilege *inference* credential is accepted (Section
3.1: "Discovery uses a least-privilege inference key, not a master/
management key"), while `GET /model/info` is optional *management*
enrichment whose own denial "does not invalidate a successful standard
model list" (same section, quoted literally). A 403 on the inference
call is `invalid_token`; a 403 on the enrichment call is a distinct
`management_state=forbidden` next to an otherwise-`connected` inference
state -- the two can never be conflated into a single "not connected"
verdict (this task's own negative_acceptance: "Timeout, certificate
error, invalid token and management-403 produce distinct sanitized
states, not connected badge").

Every reported detail string is content-free (Section observability:
"no secrets/raw prompts in task reports") -- never the resolved
credential, the raw response body, or a raw exception message that might
echo request/response bytes.

No independent schema change (this task's own `database_changes`
clause): a validation result is computed fresh on every call and
returned directly in the API response, never persisted. Wiring a
`last_validated_at`-style column onto `LiteLLMConnection` is a separately
claimed migration prerequisite, not this task's.
"""

from __future__ import annotations

import json
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from packages.litellm_client.secret_manager_integration import (
    OpenBaoConfig,
    SecretAccessDeniedError,
    SecretBackendError,
    SecretExpiredError,
    SecretNotFoundError,
    SecretRefFormatError,
    resolve_secret_ref,
)
from packages.security.outbound_guard import OutboundGuardError, guarded_fetch
from packages.security.url_policy import ConnectionPolicy, OutboundPolicyError

__all__ = [
    "ConnectionValidationResult",
    "InferenceCheckState",
    "ManagementCheckState",
    "default_connection_validation_policy",
    "validate_connection",
]

_MODELS_PATH = "/v1/models"
_MODEL_INFO_PATH = "/model/info"
_DEFAULT_TIMEOUT_SECONDS = 10.0

# Only the OpenBao-backed reference shape FTF-0202 actually resolves.
# `connection_schema.py`'s own `SECRET_REF_SCHEMES` is wider
# (`vault`/`openbao`/`secret`) because it predates the owner's backend
# selection -- a `vault://`/`secret://` connection is a real, stored
# record with no backend able to resolve it yet, reported as
# `secret_backend_unsupported` below rather than crashing or silently
# treating it as unreachable.
_SUPPORTED_SECRET_REF_PREFIX = "openbao://"


class InferenceCheckState(str, Enum):
    """The `GET /v1/models` capability dimension (Section 3.1)."""

    CONNECTED = "connected"
    INVALID_TOKEN = "invalid_token"
    CERTIFICATE_ERROR = "certificate_error"
    TIMEOUT = "timeout"
    DNS_FAILED = "dns_failed"
    CONNECTION_REFUSED = "connection_refused"
    UNREACHABLE = "unreachable"
    DESTINATION_DENIED = "destination_denied"
    MALFORMED_RESPONSE = "malformed_response"
    SECRET_NOT_FOUND = "secret_not_found"
    SECRET_ACCESS_DENIED = "secret_access_denied"
    SECRET_EXPIRED = "secret_expired"
    SECRET_BACKEND_ERROR = "secret_backend_error"
    SECRET_BACKEND_UNSUPPORTED = "secret_backend_unsupported"


class ManagementCheckState(str, Enum):
    """The `GET /model/info` enrichment dimension (Section 3.1) -- always
    `not_attempted` when the inference dimension above never reached a
    real HTTP response (no credential to present, or the gateway was
    never actually reachable)."""

    AVAILABLE = "available"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    MALFORMED = "malformed"
    UNAVAILABLE = "unavailable"
    NOT_ATTEMPTED = "not_attempted"


@dataclass(frozen=True)
class ConnectionValidationResult:
    inference_state: InferenceCheckState
    management_state: ManagementCheckState
    model_count: int | None
    detail: str
    checked_at: datetime


def default_connection_validation_policy(
    *, name: str = "litellm-connection-validation"
) -> ConnectionPolicy:
    """`https`-only, no private/loopback allowlist, no redirects --
    matches `connection_schema.py::validate_base_url`'s own https-only
    storage-time requirement exactly, and Section 65.4's "default no
    redirects." An organization needing to reach an internal-network
    LiteLLM gateway needs an explicit `explicit_private_allowlist` entry;
    that allowlist is org-level network policy (FTF-0902, a separate,
    still-unclaimed task) this module does not yet source from anywhere,
    disclosed here rather than silently defaulted to "allow everything
    private."""
    return ConnectionPolicy(name=name, allowed_schemes=frozenset({"https"}))


def _classify_guard_failure(exc: OutboundGuardError) -> InferenceCheckState:
    """`guarded_fetch`/`resolve_and_validate` wrap every real connection-
    level failure as `OutboundGuardError(...) from <original exception>`
    -- the original is preserved on `__cause__` and is the only way to
    tell a DNS failure from a TLS certificate failure from a plain
    timeout from a refused connection; the wrapper's own string message
    is not parsed."""
    cause = exc.__cause__
    if isinstance(cause, socket.gaierror):
        return InferenceCheckState.DNS_FAILED
    if isinstance(cause, ssl.SSLCertVerificationError):
        return InferenceCheckState.CERTIFICATE_ERROR
    if isinstance(cause, ssl.SSLError):
        return InferenceCheckState.CERTIFICATE_ERROR
    if isinstance(cause, TimeoutError):
        return InferenceCheckState.TIMEOUT
    if isinstance(cause, ConnectionRefusedError):
        return InferenceCheckState.CONNECTION_REFUSED
    return InferenceCheckState.UNREACHABLE


def _resolve_inference_key(
    secret_ref: str, *, secret_config: OpenBaoConfig | None, now: datetime
) -> tuple[str, None, str] | tuple[None, InferenceCheckState, str]:
    if not secret_ref.startswith(_SUPPORTED_SECRET_REF_PREFIX):
        return (
            None,
            InferenceCheckState.SECRET_BACKEND_UNSUPPORTED,
            ("no secret backend is configured for this reference's scheme"),
        )
    if secret_config is None:
        return (
            None,
            InferenceCheckState.SECRET_BACKEND_ERROR,
            ("secret backend is not configured in this environment"),
        )
    try:
        key = resolve_secret_ref(secret_ref, config=secret_config, now=now)
    except SecretNotFoundError:
        return None, InferenceCheckState.SECRET_NOT_FOUND, "secret reference has no live data"
    except SecretAccessDeniedError:
        return (
            None,
            InferenceCheckState.SECRET_ACCESS_DENIED,
            ("secret backend denied access to this reference"),
        )
    except SecretExpiredError:
        return None, InferenceCheckState.SECRET_EXPIRED, "secret reference has expired"
    except SecretRefFormatError:
        return (
            None,
            InferenceCheckState.SECRET_BACKEND_UNSUPPORTED,
            ("secret reference is not a supported backend format"),
        )
    except SecretBackendError:
        return None, InferenceCheckState.SECRET_BACKEND_ERROR, "secret backend request failed"
    return key, None, ""


def _parse_model_count(body: bytes) -> int | None:
    """OpenAI-compatible `{"data": [{"id": "...", ...}, ...]}` shape
    (Section 3.1: "`GET /v1/models` establishes the accessible model-name
    inventory"). A malformed *entry* is dropped, not treated as fatal --
    "authorized aliases retained" (Section 3.6, adversarial test 3) --
    but a response that is not even this envelope shape returns `None`
    (`malformed_response`), never an invented count."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    return sum(1 for item in data if isinstance(item, dict) and isinstance(item.get("id"), str))


def _check_management(
    base_url: str, headers: dict[str, str], policy: ConnectionPolicy, timeout: float
) -> ManagementCheckState:
    try:
        response = guarded_fetch(
            f"{base_url.rstrip('/')}{_MODEL_INFO_PATH}", policy, headers=headers, timeout=timeout
        )
    except (OutboundGuardError, OutboundPolicyError):
        return ManagementCheckState.UNAVAILABLE
    if response.status == 403:
        return ManagementCheckState.FORBIDDEN
    if response.status == 404:
        return ManagementCheckState.NOT_FOUND
    if response.status != 200:
        return ManagementCheckState.UNAVAILABLE
    try:
        payload = json.loads(response.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ManagementCheckState.MALFORMED
    return (
        ManagementCheckState.AVAILABLE
        if isinstance(payload, dict)
        else ManagementCheckState.MALFORMED
    )


def validate_connection(
    *,
    base_url: str,
    secret_ref: str,
    secret_config: OpenBaoConfig | None,
    policy: ConnectionPolicy,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> ConnectionValidationResult:
    """Validates real gateway TLS/auth/reachability (inference dimension)
    and safe management enrichment (management dimension) separately, as
    this task's own acceptance clause requires. Never raises for an
    ordinary connectivity/authorization/secret failure -- every such case
    is a real, distinct, sanitized `InferenceCheckState`/
    `ManagementCheckState` pair in the returned result, never a
    "connected" badge (this task's own negative_acceptance)."""
    checked_at = now if now is not None else datetime.now(UTC)

    key, failure_state, detail = _resolve_inference_key(
        secret_ref, secret_config=secret_config, now=checked_at
    )
    if failure_state is not None:
        return ConnectionValidationResult(
            failure_state, ManagementCheckState.NOT_ATTEMPTED, None, detail, checked_at
        )

    headers = {"Authorization": f"Bearer {key}"}
    try:
        response = guarded_fetch(
            f"{base_url.rstrip('/')}{_MODELS_PATH}", policy, headers=headers, timeout=timeout
        )
    except OutboundGuardError as exc:
        state = _classify_guard_failure(exc)
        return ConnectionValidationResult(
            state, ManagementCheckState.NOT_ATTEMPTED, None, "gateway connection failed", checked_at
        )
    except OutboundPolicyError:
        return ConnectionValidationResult(
            InferenceCheckState.DESTINATION_DENIED,
            ManagementCheckState.NOT_ATTEMPTED,
            None,
            "gateway destination is not authorized by policy",
            checked_at,
        )

    if response.status in (401, 403):
        return ConnectionValidationResult(
            InferenceCheckState.INVALID_TOKEN,
            ManagementCheckState.NOT_ATTEMPTED,
            None,
            "gateway rejected the inference credential",
            checked_at,
        )
    if response.status != 200:
        return ConnectionValidationResult(
            InferenceCheckState.UNREACHABLE,
            ManagementCheckState.NOT_ATTEMPTED,
            None,
            f"gateway returned unexpected status {response.status}",
            checked_at,
        )

    model_count = _parse_model_count(response.body)
    if model_count is None:
        return ConnectionValidationResult(
            InferenceCheckState.MALFORMED_RESPONSE,
            ManagementCheckState.NOT_ATTEMPTED,
            None,
            "gateway response was not a valid model list",
            checked_at,
        )

    management_state = _check_management(base_url, headers, policy, timeout)
    return ConnectionValidationResult(
        InferenceCheckState.CONNECTED,
        management_state,
        model_count,
        "gateway reachable and authorized",
        checked_at,
    )
