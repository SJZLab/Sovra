"""LiteLLM route identity attestation (FTF-H005, Section 3.4-3.6/38/66.5/77/81).

`packages/litellm_client/real_chat_probe.py` (FTF-0210, CERTIFIED) already
records a per-call `resolved_model` from the gateway's own OpenAI-compatible
response body and marks `RouteIdentityState.UNVERIFIED` when that field is
absent -- but an alias name alone cannot satisfy this task's own acceptance
clause ("Change alias upstream or enable an external fallback during a
private test; qualification invalidates..."), because a LiteLLM router can
silently fall back to a *different* deployment that still answers under the
identical alias/model string. Section 3.5 is explicit that this needs a
second, independent signal: "record the deployment fingerprint and resolved
model/artifact per call." This module is that second signal, not a
replacement for FTF-0210's own probe -- FTF-0210 is already CERTIFIED and is
deliberately left unmodified here (Law 15: a material change to certified
code invalidates its own evidence until requalification).

Deployment fingerprint source, disclosed rather than assumed silently: this
module reads the real `x-litellm-model-id` response header LiteLLM's proxy
documents at https://docs.litellm.ai/docs/proxy/response_headers as the
resolved deployment's own router-internal identity -- a genuine field of the
reused gateway (Section 77.1: "Reuse these engines... Existing... requirements
apply underneath"), not an invented header. This module never assumes the
header is present: `route_identity_state` is `VERIFIED` only when *both* a
real `deployment_fingerprint` and a real `resolved_model` are present in one
response; either one missing (an older gateway build, a misconfigured proxy,
a non-2xx/malformed outcome) leaves the identity `UNVERIFIED` and no
fingerprint is fabricated to fill the gap -- this task's own negative_
acceptance ("Unknown upstream identity cannot claim reproducible/private
qualification") starts exactly here, one layer below `route_snapshot.py`'s
own pin/drift gate.

Section 3.5's "ZERO_EGRESS validates every upstream, fallback... cache...
destination behind LiteLLM" and this task's own negative_acceptance ("cached
responses must not leak or replace required model probes") are met by
construction, not by detecting a cache hit after the fact from an unverified
response header: every request this module issues carries LiteLLM's own
real, documented request-level `"cache": {"no-cache": true}` field
(https://docs.litellm.ai/docs/proxy/caching), so an identity attestation can
never be silently answered from a cached response in the first place -- there
is no cache-hit header this module needs to parse or guess the name of.

Every other classification (guard/secret/gateway-rejection outcomes) reuses
the same real, already-established convention `real_chat_probe.py`/
`connection_validation.py` both already document -- duplicated here rather
than imported, matching every sibling FTF-020x/FTF-H00x module's own
self-contained-per-task convention in this package.

Every reported detail string stays content-free (Section observability: "no
secrets/raw prompts in task reports"). No independent schema change (this
task's own `database_changes` clause): an attestation is computed fresh on
every call and returned directly, never persisted.
"""

from __future__ import annotations

import json
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from time import monotonic

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
    "RouteAttestationError",
    "RouteAttestationNotAuthorizedError",
    "RouteAttestationResult",
    "RouteAttestationState",
    "RouteIdentityState",
    "attest_route",
]

_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
_DEFAULT_TIMEOUT_SECONDS = 10.0

# Same restriction every sibling FTF-020x/FTF-H00x module already documents:
# only the OpenBao-backed reference shape FTF-0202 actually resolves today.
_SUPPORTED_SECRET_REF_PREFIX = "openbao://"

# Section 3.4: "using synthetic non-sensitive content and an approved probe
# budget." Neither is caller-configurable -- an attestation is never a
# free-text or unbounded-budget call, same reasoning `real_chat_probe.py`
# already documents for its own identical constants.
_PROBE_PROMPT = "Reply with the single word: pong."
_PROBE_MAX_TOKENS = 16

# See module docstring: LiteLLM's own documented proxy response header
# carrying the resolved deployment's router-internal identity.
_DEPLOYMENT_FINGERPRINT_HEADER = "x-litellm-model-id"

_CONTENT_FILTER_CODES = frozenset({"content_filter", "content_policy_violation"})
_CONTENT_FILTER_TYPES = frozenset({"content_filter"})
_UNSUPPORTED_PARAMETER_CODES = frozenset({"unsupported_parameter", "unknown_parameter"})


class RouteAttestationState(str, Enum):
    """The real, cache-bypassed `POST /v1/chat/completions` attestation
    outcome -- the same state space `real_chat_probe.py::ChatProbeState`
    already establishes, duplicated here per this package's own
    self-contained-per-task convention."""

    ATTESTED = "attested"
    CONTENT_FILTERED = "content_filtered"
    UNSUPPORTED_PARAMETER = "unsupported_parameter"
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


class RouteIdentityState(str, Enum):
    """`VERIFIED` only when *both* a real `deployment_fingerprint` header
    and a real `resolved_model` body field are present on the same
    response -- see module docstring for why a resolved model name alone
    is not sufficient here, unlike FTF-0210's own narrower probe."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class RouteAttestationResult:
    state: RouteAttestationState
    route_identity_state: RouteIdentityState
    resolved_model: str | None
    deployment_fingerprint: str | None
    latency_ms: float | None
    detail: str
    attested_at: datetime


class RouteAttestationError(Exception):
    """Raised only for a caller-side contract violation, never for a real
    gateway/network/content outcome -- those are always a
    `RouteAttestationResult` (this task's own negative_acceptance:
    responses are normalized, never fabricated)."""


class RouteAttestationNotAuthorizedError(RouteAttestationError):
    """Section 3.4: "Probe only selected or explicitly approved auto-probe
    models." Raised before any network call when `model_id` is not a
    member of the caller-supplied `approved_model_ids` set."""


def _classify_guard_failure(exc: OutboundGuardError) -> RouteAttestationState:
    cause = exc.__cause__
    if isinstance(cause, socket.gaierror):
        return RouteAttestationState.DNS_FAILED
    if isinstance(cause, ssl.SSLCertVerificationError):
        return RouteAttestationState.CERTIFICATE_ERROR
    if isinstance(cause, ssl.SSLError):
        return RouteAttestationState.CERTIFICATE_ERROR
    if isinstance(cause, TimeoutError):
        return RouteAttestationState.TIMEOUT
    if isinstance(cause, ConnectionRefusedError):
        return RouteAttestationState.CONNECTION_REFUSED
    return RouteAttestationState.UNREACHABLE


def _resolve_inference_key(
    secret_ref: str, *, secret_config: OpenBaoConfig | None, now: datetime
) -> tuple[str, None, str] | tuple[None, RouteAttestationState, str]:
    if not secret_ref.startswith(_SUPPORTED_SECRET_REF_PREFIX):
        return (
            None,
            RouteAttestationState.SECRET_BACKEND_UNSUPPORTED,
            "no secret backend is configured for this reference's scheme",
        )
    if secret_config is None:
        return (
            None,
            RouteAttestationState.SECRET_BACKEND_ERROR,
            "secret backend is not configured in this environment",
        )
    try:
        key = resolve_secret_ref(secret_ref, config=secret_config, now=now)
    except SecretNotFoundError:
        return None, RouteAttestationState.SECRET_NOT_FOUND, "secret reference has no live data"
    except SecretAccessDeniedError:
        return (
            None,
            RouteAttestationState.SECRET_ACCESS_DENIED,
            "secret backend denied access to this reference",
        )
    except SecretExpiredError:
        return None, RouteAttestationState.SECRET_EXPIRED, "secret reference has expired"
    except SecretRefFormatError:
        return (
            None,
            RouteAttestationState.SECRET_BACKEND_UNSUPPORTED,
            "secret reference is not a supported backend format",
        )
    except SecretBackendError:
        return None, RouteAttestationState.SECRET_BACKEND_ERROR, "secret backend request failed"
    return key, None, ""


def _classify_rejection(body: bytes) -> tuple[RouteAttestationState, str]:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return (
            RouteAttestationState.MALFORMED_RESPONSE,
            "gateway rejection body was not valid JSON",
        )
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return (
            RouteAttestationState.MALFORMED_RESPONSE,
            "gateway rejection body had no error object",
        )
    code = error.get("code") if isinstance(error.get("code"), str) else None
    error_type = error.get("type") if isinstance(error.get("type"), str) else None
    param = error.get("param") if isinstance(error.get("param"), str) else None
    if code in _CONTENT_FILTER_CODES or error_type in _CONTENT_FILTER_TYPES:
        return RouteAttestationState.CONTENT_FILTERED, "gateway rejected the probe content"
    if param is not None or code in _UNSUPPORTED_PARAMETER_CODES:
        return (
            RouteAttestationState.UNSUPPORTED_PARAMETER,
            "gateway rejected an unsupported request parameter",
        )
    return (
        RouteAttestationState.MALFORMED_RESPONSE,
        "gateway rejected the request for an unrecognized reason",
    )


def _resolved_model_from_body(body: bytes) -> str | None:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    resolved_model = payload.get("model")
    if not isinstance(resolved_model, str) or not resolved_model:
        return None
    return resolved_model


def _deployment_fingerprint_from_headers(headers: dict[str, str]) -> str | None:
    """Case-insensitive lookup: `GuardedResponse.headers` preserves the
    real casing the server actually sent (`dict(response.getheaders())`),
    which HTTP does not fix to any one case."""
    target = _DEPLOYMENT_FINGERPRINT_HEADER.lower()
    for name, value in headers.items():
        if name.lower() == target and value:
            return value
    return None


def attest_route(
    *,
    base_url: str,
    model_id: str,
    approved_model_ids: frozenset[str],
    secret_ref: str,
    secret_config: OpenBaoConfig | None,
    policy: ConnectionPolicy,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> RouteAttestationResult:
    """Issues one real, bounded, synthetic, cache-bypassed
    `POST /v1/chat/completions` call and records the actual resolved model
    *and* deployment fingerprint. Never raises for an ordinary gateway/
    connectivity/content outcome -- every such case is a real, distinct,
    sanitized `RouteAttestationResult`. Raises
    `RouteAttestationNotAuthorizedError` -- before any network call -- when
    `model_id` is not explicitly approved."""
    if model_id not in approved_model_ids:
        raise RouteAttestationNotAuthorizedError(
            f"model {model_id!r} is not in the approved auto-probe set"
        )

    attested_at = now if now is not None else datetime.now(UTC)

    key, failure_state, detail = _resolve_inference_key(
        secret_ref, secret_config=secret_config, now=attested_at
    )
    if failure_state is not None:
        return RouteAttestationResult(
            failure_state, RouteIdentityState.UNVERIFIED, None, None, None, detail, attested_at
        )

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    request_body = json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": _PROBE_PROMPT}],
            "max_tokens": _PROBE_MAX_TOKENS,
            "stream": False,
            # See module docstring: bypasses LiteLLM's own real
            # request-level cache so an attestation can never be answered
            # from a cached response.
            "cache": {"no-cache": True},
        }
    ).encode("utf-8")

    started = monotonic()
    try:
        response = guarded_fetch(
            f"{base_url.rstrip('/')}{_CHAT_COMPLETIONS_PATH}",
            policy,
            method="POST",
            headers=headers,
            body=request_body,
            timeout=timeout,
        )
    except OutboundGuardError as exc:
        state = _classify_guard_failure(exc)
        return RouteAttestationResult(
            state,
            RouteIdentityState.UNVERIFIED,
            None,
            None,
            None,
            "gateway connection failed",
            attested_at,
        )
    except OutboundPolicyError:
        return RouteAttestationResult(
            RouteAttestationState.DESTINATION_DENIED,
            RouteIdentityState.UNVERIFIED,
            None,
            None,
            None,
            "gateway destination is not authorized by policy",
            attested_at,
        )
    latency_ms = (monotonic() - started) * 1000.0

    if response.status in (401, 403):
        return RouteAttestationResult(
            RouteAttestationState.INVALID_TOKEN,
            RouteIdentityState.UNVERIFIED,
            None,
            None,
            latency_ms,
            "gateway rejected the inference credential",
            attested_at,
        )

    if response.status == 400:
        classification, error_detail = _classify_rejection(response.body)
        return RouteAttestationResult(
            classification,
            RouteIdentityState.UNVERIFIED,
            None,
            None,
            latency_ms,
            error_detail,
            attested_at,
        )

    if response.status != 200:
        return RouteAttestationResult(
            RouteAttestationState.UNREACHABLE,
            RouteIdentityState.UNVERIFIED,
            None,
            None,
            latency_ms,
            f"gateway returned unexpected status {response.status}",
            attested_at,
        )

    resolved_model = _resolved_model_from_body(response.body)
    if resolved_model is None:
        return RouteAttestationResult(
            RouteAttestationState.MALFORMED_RESPONSE,
            RouteIdentityState.UNVERIFIED,
            None,
            None,
            latency_ms,
            "gateway response was not a valid chat completion",
            attested_at,
        )

    deployment_fingerprint = _deployment_fingerprint_from_headers(response.headers)
    route_identity_state = (
        RouteIdentityState.VERIFIED
        if deployment_fingerprint is not None
        else RouteIdentityState.UNVERIFIED
    )
    return RouteAttestationResult(
        RouteAttestationState.ATTESTED,
        route_identity_state,
        resolved_model,
        deployment_fingerprint,
        latency_ms,
        "gateway attested successfully"
        if route_identity_state is RouteIdentityState.VERIFIED
        else "gateway responded but no deployment fingerprint was present",
        attested_at,
    )
