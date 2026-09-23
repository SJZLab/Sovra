"""LiteLLM real chat probe (FTF-0210, Section 2-3/38/65/77/81).

`packages/litellm_client/connection_validation.py` (FTF-0203) and
`v1_models_discovery.py` (FTF-0204) both only ever call `GET /v1/models`/
`GET /model/info` -- proving a credential is *accepted*, never that a
specific model actually answers a real chat request. Section 3.4
("Capability probes") is this task's own binding contract: "Probe only
selected or explicitly approved auto-probe models, using synthetic
non-sensitive content and an approved probe budget."

Both halves of that sentence are enforced here, not left to a caller's own
discipline (this task's own acceptance clause: "Explicitly authorized
synthetic probe..."):

- `probe_chat` raises `ChatProbeNotAuthorizedError` -- before any network
  call -- when `model_id` is not a member of the caller-supplied
  `approved_model_ids` set. There is no way to reach `guarded_fetch` for an
  unapproved model.
- The probe prompt (`_PROBE_PROMPT`) and token ceiling
  (`_PROBE_MAX_TOKENS`) are fixed module constants, never caller-supplied
  free text or an unbounded budget -- a capability probe that accepted
  arbitrary prompt content or an unbounded `max_tokens` would itself be an
  unbounded, arbitrary-content inference call wearing a "probe" label.

Scope decision (disclosed, same class as FTF-0301's own narrowing):
this task owns exactly the plain, non-streaming `POST /v1/chat/completions`
capability. Streaming (FTF-0211), tool calls (FTF-0212) and structured
output (FTF-0213) are each their own, later, separately dependency-gated
probe task built on top of this one; none of that machinery lives here.
Aggregating multiple probe kinds into a per-model "advertised vs probed"
capability registry (Section 3.4's "each has advertised, probed, timestamp
and result") is FTF-0214's ("Model catalog UI") job, not this module's --
this function returns one probe's own real outcome, nothing more.

This task's own negative_acceptance ("Sensitive content, unsupported
parameters and permission/timeout errors are normalized without
fabricated responses") is met by `ChatProbeState` never raising for an
ordinary gateway-classified outcome: a `400` whose `error.code`/`type`
names a content-policy rejection becomes `CONTENT_FILTERED`; a `400`
naming an unsupported/unknown request parameter becomes
`UNSUPPORTED_PARAMETER`; `401`/`403` becomes `INVALID_TOKEN` (same
convention `connection_validation.py`/`v1_models_discovery.py` already
established); and a connection-level timeout (`guarded_fetch` wrapping a
real `TimeoutError`) becomes `TIMEOUT`. Every one of these is a real,
distinct, sanitized result -- never an exception, never a fabricated
success.

Section 3.5's "record the deployment fingerprint and resolved
model/artifact per call... If identity cannot be resolved, mark
IDENTITY_UNVERIFIED" is `RouteIdentityState`: a successful probe whose
response body includes a real `model` field is `VERIFIED` with that value
recorded as `resolved_model`; a response with no such field (or a
non-2xx/malformed outcome, where there is nothing to pin) is
`UNVERIFIED`. Section 3.4's "Missing usage becomes unpriced/unknown, not
zero cost" is `ChatUsage`: it is only ever built from three real integer
fields actually present in the gateway's own response; any other shape
(missing usage object, non-integer field) leaves `ChatProbeResult.usage`
as `None`, never a zero-filled stand-in.

Every reported detail string is content-free (Section observability: "no
secrets/raw prompts in task reports") -- never the resolved credential,
the probe prompt, the model's real answer, or a raw exception message.

No independent schema change (this task's own `database_changes` clause):
a probe result is computed fresh on every call and returned directly,
never persisted -- same convention every sibling FTF-020x module in this
package already established.
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
    "ChatProbeError",
    "ChatProbeNotAuthorizedError",
    "ChatProbeResult",
    "ChatProbeState",
    "ChatUsage",
    "RouteIdentityState",
    "probe_chat",
]

_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
_DEFAULT_TIMEOUT_SECONDS = 10.0

# Same restriction every sibling FTF-020x module already documents: only
# the OpenBao-backed reference shape FTF-0202 actually resolves today.
_SUPPORTED_SECRET_REF_PREFIX = "openbao://"

# Section 3.4: "using synthetic non-sensitive content and an approved
# probe budget." Neither is caller-configurable -- see the module
# docstring for why.
_PROBE_PROMPT = "Reply with the single word: pong."
_PROBE_MAX_TOKENS = 16

_CONTENT_FILTER_CODES = frozenset({"content_filter", "content_policy_violation"})
_CONTENT_FILTER_TYPES = frozenset({"content_filter"})
_UNSUPPORTED_PARAMETER_CODES = frozenset({"unsupported_parameter", "unknown_parameter"})


class ChatProbeState(str, Enum):
    """The real `POST /v1/chat/completions` probe outcome."""

    PROBED = "probed"
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
    """Section 3.5's "record the deployment fingerprint and resolved
    model/artifact per call. If identity cannot be resolved, mark
    IDENTITY_UNVERIFIED" -- independent of whether the probe itself
    otherwise succeeded, since a real `200` response could still omit a
    `model` field."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class ChatUsage:
    """Only ever built from three real integer fields actually present in
    the gateway's own response body -- see the module docstring's
    "Missing usage becomes unpriced/unknown" note."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class ChatProbeResult:
    state: ChatProbeState
    route_identity_state: RouteIdentityState
    resolved_model: str | None
    usage: ChatUsage | None
    latency_ms: float | None
    detail: str
    probed_at: datetime


class ChatProbeError(Exception):
    """Raised only for a caller-side contract violation, never for a real
    gateway/network/content outcome -- those are always a
    `ChatProbeResult` (this task's own negative_acceptance: "...normalized
    without fabricated responses")."""


class ChatProbeNotAuthorizedError(ChatProbeError):
    """Section 3.4: "Probe only selected or explicitly approved auto-probe
    models." Raised before any network call when `model_id` is not a
    member of the caller-supplied `approved_model_ids` set."""


def _classify_guard_failure(exc: OutboundGuardError) -> ChatProbeState:
    """Same convention every sibling FTF-020x module already established:
    `guarded_fetch` wraps every connection-level failure as
    `OutboundGuardError(...) from <original exception>`, and the original
    cause (never the wrapper's own string message) is what is actually
    classified."""
    cause = exc.__cause__
    if isinstance(cause, socket.gaierror):
        return ChatProbeState.DNS_FAILED
    if isinstance(cause, ssl.SSLCertVerificationError):
        return ChatProbeState.CERTIFICATE_ERROR
    if isinstance(cause, ssl.SSLError):
        return ChatProbeState.CERTIFICATE_ERROR
    if isinstance(cause, TimeoutError):
        return ChatProbeState.TIMEOUT
    if isinstance(cause, ConnectionRefusedError):
        return ChatProbeState.CONNECTION_REFUSED
    return ChatProbeState.UNREACHABLE


def _resolve_inference_key(
    secret_ref: str, *, secret_config: OpenBaoConfig | None, now: datetime
) -> tuple[str, None, str] | tuple[None, ChatProbeState, str]:
    if not secret_ref.startswith(_SUPPORTED_SECRET_REF_PREFIX):
        return (
            None,
            ChatProbeState.SECRET_BACKEND_UNSUPPORTED,
            "no secret backend is configured for this reference's scheme",
        )
    if secret_config is None:
        return (
            None,
            ChatProbeState.SECRET_BACKEND_ERROR,
            "secret backend is not configured in this environment",
        )
    try:
        key = resolve_secret_ref(secret_ref, config=secret_config, now=now)
    except SecretNotFoundError:
        return None, ChatProbeState.SECRET_NOT_FOUND, "secret reference has no live data"
    except SecretAccessDeniedError:
        return (
            None,
            ChatProbeState.SECRET_ACCESS_DENIED,
            "secret backend denied access to this reference",
        )
    except SecretExpiredError:
        return None, ChatProbeState.SECRET_EXPIRED, "secret reference has expired"
    except SecretRefFormatError:
        return (
            None,
            ChatProbeState.SECRET_BACKEND_UNSUPPORTED,
            "secret reference is not a supported backend format",
        )
    except SecretBackendError:
        return None, ChatProbeState.SECRET_BACKEND_ERROR, "secret backend request failed"
    return key, None, ""


def _classify_rejection(body: bytes) -> tuple[ChatProbeState, str]:
    """Classifies a real `400` gateway response using the OpenAI-compatible
    `{"error": {"type": ..., "code": ..., "param": ...}}` envelope. An
    unparseable or unrecognized-shape rejection is `MALFORMED_RESPONSE`,
    never guessed into one of the two named categories."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ChatProbeState.MALFORMED_RESPONSE, "gateway rejection body was not valid JSON"
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return ChatProbeState.MALFORMED_RESPONSE, "gateway rejection body had no error object"
    code = error.get("code") if isinstance(error.get("code"), str) else None
    error_type = error.get("type") if isinstance(error.get("type"), str) else None
    param = error.get("param") if isinstance(error.get("param"), str) else None
    if code in _CONTENT_FILTER_CODES or error_type in _CONTENT_FILTER_TYPES:
        return ChatProbeState.CONTENT_FILTERED, "gateway rejected the probe content"
    if param is not None or code in _UNSUPPORTED_PARAMETER_CODES:
        return (
            ChatProbeState.UNSUPPORTED_PARAMETER,
            "gateway rejected an unsupported request parameter",
        )
    return (
        ChatProbeState.MALFORMED_RESPONSE,
        "gateway rejected the request for an unrecognized reason",
    )


def _parse_usage(raw: object) -> ChatUsage | None:
    if not isinstance(raw, dict):
        return None
    prompt = raw.get("prompt_tokens")
    completion = raw.get("completion_tokens")
    total = raw.get("total_tokens")
    fields = (prompt, completion, total)
    if any(isinstance(value, bool) for value in fields):
        return None
    if not all(isinstance(value, int) for value in fields):
        return None
    assert isinstance(prompt, int) and isinstance(completion, int) and isinstance(total, int)
    return ChatUsage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)


def _parse_success_body(body: bytes) -> tuple[str | None, ChatUsage | None] | None:
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
        resolved_model = None
    return resolved_model, _parse_usage(payload.get("usage"))


def probe_chat(
    *,
    base_url: str,
    model_id: str,
    approved_model_ids: frozenset[str],
    secret_ref: str,
    secret_config: OpenBaoConfig | None,
    policy: ConnectionPolicy,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> ChatProbeResult:
    """Issues one real, bounded, synthetic `POST /v1/chat/completions`
    call and records the actual outcome. Never raises for an ordinary
    gateway/connectivity/content outcome -- every such case is a real,
    distinct, sanitized `ChatProbeResult` (this task's own
    negative_acceptance). Raises `ChatProbeNotAuthorizedError` -- before
    any network call -- when `model_id` is not explicitly approved (this
    task's own acceptance clause)."""
    if model_id not in approved_model_ids:
        raise ChatProbeNotAuthorizedError(
            f"model {model_id!r} is not in the approved auto-probe set"
        )

    probed_at = now if now is not None else datetime.now(UTC)

    key, failure_state, detail = _resolve_inference_key(
        secret_ref, secret_config=secret_config, now=probed_at
    )
    if failure_state is not None:
        return ChatProbeResult(
            failure_state, RouteIdentityState.UNVERIFIED, None, None, None, detail, probed_at
        )

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    request_body = json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": _PROBE_PROMPT}],
            "max_tokens": _PROBE_MAX_TOKENS,
            "stream": False,
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
        return ChatProbeResult(
            state,
            RouteIdentityState.UNVERIFIED,
            None,
            None,
            None,
            "gateway connection failed",
            probed_at,
        )
    except OutboundPolicyError:
        return ChatProbeResult(
            ChatProbeState.DESTINATION_DENIED,
            RouteIdentityState.UNVERIFIED,
            None,
            None,
            None,
            "gateway destination is not authorized by policy",
            probed_at,
        )
    latency_ms = (monotonic() - started) * 1000.0

    if response.status in (401, 403):
        return ChatProbeResult(
            ChatProbeState.INVALID_TOKEN,
            RouteIdentityState.UNVERIFIED,
            None,
            None,
            latency_ms,
            "gateway rejected the inference credential",
            probed_at,
        )

    if response.status == 400:
        classification, error_detail = _classify_rejection(response.body)
        return ChatProbeResult(
            classification,
            RouteIdentityState.UNVERIFIED,
            None,
            None,
            latency_ms,
            error_detail,
            probed_at,
        )

    if response.status != 200:
        return ChatProbeResult(
            ChatProbeState.UNREACHABLE,
            RouteIdentityState.UNVERIFIED,
            None,
            None,
            latency_ms,
            f"gateway returned unexpected status {response.status}",
            probed_at,
        )

    parsed = _parse_success_body(response.body)
    if parsed is None:
        return ChatProbeResult(
            ChatProbeState.MALFORMED_RESPONSE,
            RouteIdentityState.UNVERIFIED,
            None,
            None,
            latency_ms,
            "gateway response was not a valid chat completion",
            probed_at,
        )

    resolved_model, usage = parsed
    route_identity_state = (
        RouteIdentityState.VERIFIED if resolved_model is not None else RouteIdentityState.UNVERIFIED
    )
    return ChatProbeResult(
        ChatProbeState.PROBED,
        route_identity_state,
        resolved_model,
        usage,
        latency_ms,
        "gateway probed successfully",
        probed_at,
    )
