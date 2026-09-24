"""LiteLLM real tool-call probe (FTF-0212, Section 2-3/38/65/77/81).

`real_chat_probe.py` (FTF-0210) proves a model answers one real, plain
chat call; `real_streaming_probe.py` (FTF-0211) proves a model answers a
real streamed call. Neither proves the gateway/model actually honors the
OpenAI-compatible `tools` request contract -- Section 3.4's own list of
capabilities to probe ("chat, streaming, tools, structured output")
names tool-calling as its own, distinct thing. This task owns exactly
that third capability: issuing one real, bounded, synthetic
`POST /v1/chat/completions` request that advertises a fixed tool schema,
and recording whether the gateway's real response actually invokes it --
never inferring tool-call support from a plain chat/streaming success.

Both halves of Section 3.4's own probe-safety sentence are enforced by
construction here, unchanged from FTF-0210's/FTF-0211's own precedent:

- `probe_tool_call` raises `ChatProbeNotAuthorizedError` (reused directly
  from `real_chat_probe.py` -- the exact same authorization contract, not
  a parallel type) before any network call when `model_id` is not a
  member of the caller-supplied `approved_model_ids` set.
- The probe prompt, tool schema and token ceiling are fixed module
  constants, never caller-supplied free text or an unbounded budget --
  duplicated rather than cross-imported from either sibling module,
  matching the convention `real_streaming_probe.py` already established:
  private helpers/constants are never cross-imported between sibling
  FTF-020x modules, even for identical values, so a rename inside one
  module can never silently break another.

This task's own acceptance clause 1 ("Probe tool schema/name/arguments
using non-sensitive case and record advertised versus observed result")
is `ToolCallProbeResult`: `advertised_tool_name` is the fixed constant
this module always requests (`_PROBE_TOOL_NAME`); `observed_tool_name`
and `observed_arguments` are the real `function.name`/`function.arguments`
strings the gateway's own response actually carried in
`choices[0].message.tool_calls[0]` -- never invented when that field is
absent, never read from ordinary message `content`.

This task's own negative_acceptance ("An ordinary textual JSON answer is
not proof of a tool-call capability; invalid tool result is failed
probe") is enforced by construction, not by a heuristic over text:

- Only the structured `message.tool_calls` field is ever inspected. A
  response whose `message.content` happens to contain JSON that *looks*
  like a tool invocation (e.g. an assistant that was asked to call a tool
  but instead typed out `{"name": "...", "arguments": {...}}` as plain
  prose) is `NO_TOOL_CALL_ISSUED` -- exactly the same real outcome as a
  response with no tool-shaped text at all, since this module never reads
  `content` to look for one.
- A real tool call naming a function other than the one this probe
  advertised is `TOOL_NAME_MISMATCH` -- never silently accepted as proof
  of the advertised tool's own support.
- A real tool call naming the advertised function but whose `arguments`
  string does not parse as a JSON object carrying the one required
  property this probe's own schema declares is `INVALID_TOOL_ARGUMENTS`
  -- a syntactically-present tool call is not itself proof the gateway
  honored the advertised parameter schema.

Every one of these is a real, distinct, sanitized `ToolCallProbeResult`
-- never an exception, never a fabricated `PROBED` outcome. Every
reported detail string is content-free (Section observability: "no
secrets/raw prompts in task reports") -- never the resolved credential,
the probe prompt, or the model's real raw answer text.

No independent schema change (this task's own `database_changes` clause):
a probe result is computed fresh on every call and returned directly,
never persisted -- same convention every sibling FTF-020x module already
established.

Path refined from the task packet's own literal suggestion
(`packages/litellm-client/real_tool_call_probe.py`) to
`packages/litellm_client/real_tool_call_probe.py`, matching every sibling
FTF-020x module's own already-established package directory (a hyphenated
directory name is not a valid Python package segment).
"""

from __future__ import annotations

import json
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from time import monotonic

from packages.litellm_client.real_chat_probe import (
    ChatProbeNotAuthorizedError,
    ChatUsage,
    RouteIdentityState,
)
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
    "ChatProbeNotAuthorizedError",
    "ToolCallProbeError",
    "ToolCallProbeResult",
    "ToolCallProbeState",
    "probe_tool_call",
]

_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
_DEFAULT_TIMEOUT_SECONDS = 10.0

# Same restriction every sibling FTF-020x module already documents: only
# the OpenBao-backed reference shape FTF-0202 actually resolves today.
_SUPPORTED_SECRET_REF_PREFIX = "openbao://"

# Section 3.4: "using synthetic non-sensitive content and an approved
# probe budget." Neither the prompt, the advertised tool schema, nor the
# token ceiling is caller-configurable -- same reasoning FTF-0210's own
# docstring already gives for its own plain-chat probe constants.
_PROBE_PROMPT = "Call the get_probe_time tool with timezone set to UTC."
_PROBE_MAX_TOKENS = 64
_PROBE_TOOL_NAME = "get_probe_time"
_PROBE_TOOL_PARAMETER = "timezone"
_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": _PROBE_TOOL_NAME,
        "description": (
            "Returns a fixed synthetic timestamp string for capability probing only. "
            "Never a real clock and never called outside this probe."
        ),
        "parameters": {
            "type": "object",
            "properties": {_PROBE_TOOL_PARAMETER: {"type": "string"}},
            "required": [_PROBE_TOOL_PARAMETER],
        },
    },
}

_CONTENT_FILTER_CODES = frozenset({"content_filter", "content_policy_violation"})
_CONTENT_FILTER_TYPES = frozenset({"content_filter"})
_UNSUPPORTED_PARAMETER_CODES = frozenset({"unsupported_parameter", "unknown_parameter"})


class ToolCallProbeState(str, Enum):
    """The real tool-call probe outcome. `PROBED` is the only state in
    which the gateway's own response is confirmed to have invoked the
    advertised tool with structurally valid arguments."""

    PROBED = "probed"
    NO_TOOL_CALL_ISSUED = "no_tool_call_issued"
    TOOL_NAME_MISMATCH = "tool_name_mismatch"
    INVALID_TOOL_ARGUMENTS = "invalid_tool_arguments"
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


@dataclass(frozen=True)
class ToolCallProbeResult:
    state: ToolCallProbeState
    route_identity_state: RouteIdentityState
    resolved_model: str | None
    advertised_tool_name: str
    observed_tool_name: str | None
    observed_arguments: str | None
    usage: ChatUsage | None
    latency_ms: float | None
    detail: str
    probed_at: datetime


class ToolCallProbeError(Exception):
    """Raised only for a caller-side contract violation, never for a real
    gateway/network/content/tool outcome -- those are always a
    `ToolCallProbeResult` (this task's own negative_acceptance)."""


def _classify_guard_failure(exc: OutboundGuardError) -> ToolCallProbeState:
    """Same convention every sibling FTF-020x module already established:
    `guarded_fetch` wraps every connection-level failure as
    `OutboundGuardError(...) from <original exception>`, and the original
    cause (never the wrapper's own string message) is what is actually
    classified."""
    cause = exc.__cause__
    if isinstance(cause, socket.gaierror):
        return ToolCallProbeState.DNS_FAILED
    if isinstance(cause, ssl.SSLCertVerificationError):
        return ToolCallProbeState.CERTIFICATE_ERROR
    if isinstance(cause, ssl.SSLError):
        return ToolCallProbeState.CERTIFICATE_ERROR
    if isinstance(cause, TimeoutError):
        return ToolCallProbeState.TIMEOUT
    if isinstance(cause, ConnectionRefusedError):
        return ToolCallProbeState.CONNECTION_REFUSED
    return ToolCallProbeState.UNREACHABLE


def _resolve_inference_key(
    secret_ref: str, *, secret_config: OpenBaoConfig | None, now: datetime
) -> tuple[str, None, str] | tuple[None, ToolCallProbeState, str]:
    if not secret_ref.startswith(_SUPPORTED_SECRET_REF_PREFIX):
        return (
            None,
            ToolCallProbeState.SECRET_BACKEND_UNSUPPORTED,
            "no secret backend is configured for this reference's scheme",
        )
    if secret_config is None:
        return (
            None,
            ToolCallProbeState.SECRET_BACKEND_ERROR,
            "secret backend is not configured in this environment",
        )
    try:
        key = resolve_secret_ref(secret_ref, config=secret_config, now=now)
    except SecretNotFoundError:
        return None, ToolCallProbeState.SECRET_NOT_FOUND, "secret reference has no live data"
    except SecretAccessDeniedError:
        return (
            None,
            ToolCallProbeState.SECRET_ACCESS_DENIED,
            "secret backend denied access to this reference",
        )
    except SecretExpiredError:
        return None, ToolCallProbeState.SECRET_EXPIRED, "secret reference has expired"
    except SecretRefFormatError:
        return (
            None,
            ToolCallProbeState.SECRET_BACKEND_UNSUPPORTED,
            "secret reference is not a supported backend format",
        )
    except SecretBackendError:
        return None, ToolCallProbeState.SECRET_BACKEND_ERROR, "secret backend request failed"
    return key, None, ""


def _classify_rejection(body: bytes) -> tuple[ToolCallProbeState, str]:
    """Classifies a real `400` gateway response using the OpenAI-compatible
    `{"error": {"type": ..., "code": ..., "param": ...}}` envelope -- same
    taxonomy `real_chat_probe.py`/`real_streaming_probe.py` established. An
    unparseable or unrecognized-shape rejection is `MALFORMED_RESPONSE`,
    never guessed into one of the two named categories."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return (
            ToolCallProbeState.MALFORMED_RESPONSE,
            "gateway rejection body was not valid JSON",
        )
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return (
            ToolCallProbeState.MALFORMED_RESPONSE,
            "gateway rejection body had no error object",
        )
    code = error.get("code") if isinstance(error.get("code"), str) else None
    error_type = error.get("type") if isinstance(error.get("type"), str) else None
    param = error.get("param") if isinstance(error.get("param"), str) else None
    if code in _CONTENT_FILTER_CODES or error_type in _CONTENT_FILTER_TYPES:
        return ToolCallProbeState.CONTENT_FILTERED, "gateway rejected the probe content"
    if param is not None or code in _UNSUPPORTED_PARAMETER_CODES:
        return (
            ToolCallProbeState.UNSUPPORTED_PARAMETER,
            "gateway rejected an unsupported request parameter",
        )
    return (
        ToolCallProbeState.MALFORMED_RESPONSE,
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


def _extract_first_tool_call(message: dict[str, object]) -> tuple[str | None, str | None] | None:
    """Returns `(name, arguments)` for the real first entry of
    `message["tool_calls"]`, or `None` if that field is absent, empty or
    not a list of well-shaped entries. Deliberately never reads
    `message["content"]` -- the negative_acceptance clause this module
    exists to enforce ("An ordinary textual JSON answer is not proof of a
    tool-call capability") is met by construction: text is not a source
    of truth for tool invocation here at all."""
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        return None
    first = tool_calls[0]
    if not isinstance(first, dict):
        return None
    function = first.get("function")
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    arguments = function.get("arguments")
    name = name if isinstance(name, str) else None
    arguments = arguments if isinstance(arguments, str) else None
    return name, arguments


def _arguments_are_structurally_valid(arguments: str) -> bool:
    """This task's own negative_acceptance ("invalid tool result is failed
    probe"): the advertised schema's one required property must actually
    be present as a string in the parsed arguments object."""
    try:
        parsed = json.loads(arguments)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(parsed, dict):
        return False
    value = parsed.get(_PROBE_TOOL_PARAMETER)
    return isinstance(value, str) and bool(value)


def _parse_success_body(
    body: bytes,
) -> tuple[str | None, str | None, str | None, ChatUsage | None] | None:
    """Returns `(resolved_model, observed_tool_name, observed_arguments,
    usage)` for a real 200 response with at least one real choice, or
    `None` if the body is not a valid completion shape at all."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return None
    message = first_choice.get("message")
    if not isinstance(message, dict):
        return None
    resolved_model = payload.get("model")
    resolved_model = resolved_model if isinstance(resolved_model, str) and resolved_model else None
    tool_call = _extract_first_tool_call(message)
    observed_tool_name, observed_arguments = tool_call if tool_call is not None else (None, None)
    usage = _parse_usage(payload.get("usage"))
    return resolved_model, observed_tool_name, observed_arguments, usage


def probe_tool_call(
    *,
    base_url: str,
    model_id: str,
    approved_model_ids: frozenset[str],
    secret_ref: str,
    secret_config: OpenBaoConfig | None,
    policy: ConnectionPolicy,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> ToolCallProbeResult:
    """Issues one real, bounded, synthetic `POST /v1/chat/completions`
    call advertising a fixed tool schema, and records the real advertised-
    versus-observed outcome (this task's own acceptance clause 1). Never
    raises for an ordinary gateway/connectivity/content/tool outcome --
    every such case is a real, distinct, sanitized `ToolCallProbeResult`
    (this task's own negative_acceptance). Raises
    `ChatProbeNotAuthorizedError` -- before any network call -- when
    `model_id` is not explicitly approved (reusing FTF-0210's exact
    authorization contract)."""
    if model_id not in approved_model_ids:
        raise ChatProbeNotAuthorizedError(
            f"model {model_id!r} is not in the approved auto-probe set"
        )

    probed_at = now if now is not None else datetime.now(UTC)

    key, failure_state, detail = _resolve_inference_key(
        secret_ref, secret_config=secret_config, now=probed_at
    )
    if failure_state is not None:
        return ToolCallProbeResult(
            failure_state,
            RouteIdentityState.UNVERIFIED,
            None,
            _PROBE_TOOL_NAME,
            None,
            None,
            None,
            None,
            detail,
            probed_at,
        )

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    request_body = json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": _PROBE_PROMPT}],
            "max_tokens": _PROBE_MAX_TOKENS,
            "stream": False,
            "tools": [_PROBE_TOOL],
            "tool_choice": {"type": "function", "function": {"name": _PROBE_TOOL_NAME}},
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
        return ToolCallProbeResult(
            state,
            RouteIdentityState.UNVERIFIED,
            None,
            _PROBE_TOOL_NAME,
            None,
            None,
            None,
            None,
            "gateway connection failed",
            probed_at,
        )
    except OutboundPolicyError:
        return ToolCallProbeResult(
            ToolCallProbeState.DESTINATION_DENIED,
            RouteIdentityState.UNVERIFIED,
            None,
            _PROBE_TOOL_NAME,
            None,
            None,
            None,
            None,
            "gateway destination is not authorized by policy",
            probed_at,
        )
    latency_ms = (monotonic() - started) * 1000.0

    if response.status in (401, 403):
        return ToolCallProbeResult(
            ToolCallProbeState.INVALID_TOKEN,
            RouteIdentityState.UNVERIFIED,
            None,
            _PROBE_TOOL_NAME,
            None,
            None,
            None,
            latency_ms,
            "gateway rejected the inference credential",
            probed_at,
        )

    if response.status == 400:
        classification, error_detail = _classify_rejection(response.body)
        return ToolCallProbeResult(
            classification,
            RouteIdentityState.UNVERIFIED,
            None,
            _PROBE_TOOL_NAME,
            None,
            None,
            None,
            latency_ms,
            error_detail,
            probed_at,
        )

    if response.status != 200:
        return ToolCallProbeResult(
            ToolCallProbeState.UNREACHABLE,
            RouteIdentityState.UNVERIFIED,
            None,
            _PROBE_TOOL_NAME,
            None,
            None,
            None,
            latency_ms,
            f"gateway returned unexpected status {response.status}",
            probed_at,
        )

    parsed = _parse_success_body(response.body)
    if parsed is None:
        return ToolCallProbeResult(
            ToolCallProbeState.MALFORMED_RESPONSE,
            RouteIdentityState.UNVERIFIED,
            None,
            _PROBE_TOOL_NAME,
            None,
            None,
            None,
            latency_ms,
            "gateway response was not a valid chat completion",
            probed_at,
        )

    resolved_model, observed_tool_name, observed_arguments, usage = parsed
    route_identity_state = (
        RouteIdentityState.VERIFIED if resolved_model is not None else RouteIdentityState.UNVERIFIED
    )

    if observed_tool_name is None:
        return ToolCallProbeResult(
            ToolCallProbeState.NO_TOOL_CALL_ISSUED,
            route_identity_state,
            resolved_model,
            _PROBE_TOOL_NAME,
            None,
            observed_arguments,
            usage,
            latency_ms,
            "gateway answered without invoking the advertised tool",
            probed_at,
        )

    if observed_tool_name != _PROBE_TOOL_NAME:
        return ToolCallProbeResult(
            ToolCallProbeState.TOOL_NAME_MISMATCH,
            route_identity_state,
            resolved_model,
            _PROBE_TOOL_NAME,
            observed_tool_name,
            observed_arguments,
            usage,
            latency_ms,
            "gateway invoked a different tool than the one advertised",
            probed_at,
        )

    if observed_arguments is None or not _arguments_are_structurally_valid(observed_arguments):
        return ToolCallProbeResult(
            ToolCallProbeState.INVALID_TOOL_ARGUMENTS,
            route_identity_state,
            resolved_model,
            _PROBE_TOOL_NAME,
            observed_tool_name,
            observed_arguments,
            usage,
            latency_ms,
            "gateway invoked the advertised tool with invalid arguments",
            probed_at,
        )

    return ToolCallProbeResult(
        ToolCallProbeState.PROBED,
        route_identity_state,
        resolved_model,
        _PROBE_TOOL_NAME,
        observed_tool_name,
        observed_arguments,
        usage,
        latency_ms,
        "gateway probed successfully",
        probed_at,
    )
