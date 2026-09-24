"""LiteLLM real structured-output probe (FTF-0213, Section 2-3/38/65/77/81).

`real_chat_probe.py` (FTF-0210) proves a model answers one real, plain
`POST /v1/chat/completions` call; `real_streaming_probe.py` (FTF-0211)
proves the same for a real SSE stream. Section 3.4's own capability list
-- "chat, streaming, tools, structured output" -- names a fourth, distinct
capability neither of those owns: whether a model actually honors an
OpenAI-compatible `response_format: {"type": "json_schema", ...}` request
and returns content that *really* validates against the schema it was
given, as opposed to merely returning some JSON, or some JSON-looking
prose, or JSON that loosely resembles the schema through silent type
coercion. This task owns exactly that capability.

Both halves of Section 3.4's own probe-safety sentence are enforced by
construction here, unchanged from FTF-0210/FTF-0211's own precedent:

- `probe_structured_output` reuses `ChatProbeNotAuthorizedError` directly
  from `real_chat_probe.py` -- the exact same authorization contract, not
  a parallel type -- and raises it before any network call when `model_id`
  is not a member of the caller-supplied `approved_model_ids` set.
- The probe prompt, schema and token ceiling are fixed module constants,
  never caller-supplied free text or an unbounded budget -- same
  reasoning FTF-0210's own docstring already gives for why an
  unrestricted "probe" would just be an unbounded inference call wearing
  a probe label.

This task's own acceptance clause ("Exercise the model's advertised
structured-output protocol and schema-validate actual response") is met
by `_PROBE_SCHEMA`/`_PROBE_JSON_SCHEMA_REQUEST`: the probe always sends a
real `response_format: {"type": "json_schema", "json_schema": {...,
"strict": true}}` request (the OpenAI-compatible strict-structured-output
protocol), and the real response `choices[0].message.content` is always
run through `_validate_against_schema` -- a small, purpose-built JSON
Schema (draft 2020-12 subset: `type`, `properties`, `required`,
`additionalProperties`, `enum`) validator, since this repository has no
existing JSON Schema validation dependency and this probe's own fixed
schema does not need a general-purpose one. A response is only ever
`PROBED` when the real content both parses as JSON *and* validates
against `_PROBE_SCHEMA`.

This task's own negative_acceptance ("JSON-looking prose, unknown
parameter rejection or coercion is not silently treated as strict schema
support") is met by three distinct, real states, never collapsed into a
fabricated `PROBED`:

- `NOT_JSON`: the real response content does not even parse as JSON --
  including prose that merely looks JSON-ish (unquoted keys, trailing
  commentary, a code fence) -- covering the "JSON-looking prose" clause.
- `UNSUPPORTED_PARAMETER`: the real gateway rejects the `response_format`
  parameter itself with a 400 (reusing `real_chat_probe.py`'s own
  rejection taxonomy). This probe never silently retries without
  structured mode and reports success -- a caller that wants that
  fallback behavior is a distinct, undocumented capability this probe
  does not claim -- covering the "unknown parameter rejection" clause.
- `SCHEMA_VIOLATION`: the real content parses as JSON but fails
  `_validate_against_schema` -- including a value present under the
  right key but the wrong JSON type (e.g. `"count": "1"` where the schema
  requires an integer): `_validate_against_schema` never coerces a string
  that merely looks numeric into passing an `integer`/`number` check --
  covering the "coercion" clause.

Section 3.5's "record the deployment fingerprint and resolved
model/artifact per call... If identity cannot be resolved, mark
IDENTITY_UNVERIFIED" and Section 3.4's "Missing usage becomes
unpriced/unknown, not zero cost" are `RouteIdentityState`/`ChatUsage`,
reused directly from `real_chat_probe.py` -- the exact same contracts,
not parallel types, same convention `real_streaming_probe.py` already
established for reusing them.

Every reported detail string is content-free (Section observability: "no
secrets/raw prompts in task reports") -- never the resolved credential,
the probe prompt, or the model's real raw content.

No independent schema change (this task's own `database_changes`
clause): a probe result is computed fresh on every call and returned
directly, never persisted -- same convention every sibling FTF-020x
module in this package already established.
"""

from __future__ import annotations

import json
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from time import monotonic
from typing import Any

from packages.litellm_client.real_chat_probe import (
    ChatProbeNotAuthorizedError,
    ChatUsage,
    RouteIdentityState,
    _parse_usage,
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
    "StructuredOutputProbeError",
    "StructuredOutputProbeResult",
    "StructuredOutputProbeState",
    "probe_structured_output",
]

_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
_DEFAULT_TIMEOUT_SECONDS = 10.0

# Same restriction every sibling FTF-020x module already documents: only
# the OpenBao-backed reference shape FTF-0202 actually resolves today.
_SUPPORTED_SECRET_REF_PREFIX = "openbao://"

# Section 3.4: "using synthetic non-sensitive content and an approved
# probe budget." Neither the prompt, the schema nor the token ceiling is
# caller-configurable -- see the module docstring for why. This module's
# own private constants, not cross-imported from a sibling module, same
# convention every sibling FTF-020x module already follows.
_PROBE_SCHEMA_NAME = "ftf_0213_probe_reply"
_PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "word": {"type": "string", "enum": ["pong"]},
        "count": {"type": "integer"},
    },
    "required": ["word", "count"],
    "additionalProperties": False,
}
_PROBE_PROMPT = (
    "Reply with only a JSON object matching the provided schema exactly: "
    '{"word": "pong", "count": 1}.'
)
_PROBE_MAX_TOKENS = 64
_PROBE_JSON_SCHEMA_REQUEST: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": _PROBE_SCHEMA_NAME,
        "strict": True,
        "schema": _PROBE_SCHEMA,
    },
}

_CONTENT_FILTER_CODES = frozenset({"content_filter", "content_policy_violation"})
_CONTENT_FILTER_TYPES = frozenset({"content_filter"})
_UNSUPPORTED_PARAMETER_CODES = frozenset({"unsupported_parameter", "unknown_parameter"})

_JSON_SCHEMA_TYPE_CHECKS: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "boolean": (bool,),
    "null": (type(None),),
}


class StructuredOutputProbeState(str, Enum):
    """The real structured-output `POST /v1/chat/completions` probe
    outcome."""

    PROBED = "probed"
    SCHEMA_VIOLATION = "schema_violation"
    NOT_JSON = "not_json"
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
class StructuredOutputProbeResult:
    state: StructuredOutputProbeState
    route_identity_state: RouteIdentityState
    resolved_model: str | None
    parsed_content: dict[str, Any] | None
    usage: ChatUsage | None
    latency_ms: float | None
    detail: str
    probed_at: datetime


class StructuredOutputProbeError(Exception):
    """Raised only for a caller-side contract violation, never for a real
    gateway/network/content outcome -- those are always a
    `StructuredOutputProbeResult` (this task's own negative_acceptance:
    "...is not silently treated as strict schema support")."""


def _validate_against_schema(instance: Any, schema: dict[str, Any]) -> bool:
    """A small, purpose-built JSON Schema subset validator covering only
    what `_PROBE_SCHEMA` actually uses: `type`, `properties`, `required`,
    `additionalProperties` and `enum`. Never coerces a value into passing
    -- a `bool` is deliberately never accepted for `integer`/`number`
    (Python's `bool` is an `int` subclass) and a numeric-looking `str` is
    never accepted for `integer`/`number`/`boolean`, since accepting
    either would be exactly the silent coercion this task's own
    negative_acceptance forbids."""
    schema_type = schema.get("type")
    if schema_type == "integer":
        if isinstance(instance, bool) or not isinstance(instance, int):
            return False
    elif schema_type == "number":
        if isinstance(instance, bool) or not isinstance(instance, int | float):
            return False
    elif schema_type in _JSON_SCHEMA_TYPE_CHECKS:
        if not isinstance(instance, _JSON_SCHEMA_TYPE_CHECKS[schema_type]):
            return False

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and instance not in enum_values:
        return False

    if schema_type == "object" and isinstance(instance, dict):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        required = required if isinstance(required, list) else []
        for key in required:
            if key not in instance:
                return False
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in properties:
                    return False
        for key, sub_schema in properties.items():
            if key in instance and isinstance(sub_schema, dict):
                if not _validate_against_schema(instance[key], sub_schema):
                    return False

    return True


def _parse_structured_content(body: bytes) -> tuple[str, str | None, ChatUsage | None] | None:
    """Same success-body shape `real_chat_probe.py._parse_success_body`
    already validates (a real 2xx with a non-empty `choices` list), plus
    this task's own `content` extraction -- kept as a private duplicate
    rather than a cross-import, same convention every sibling FTF-020x
    module already follows. Returns `(content, resolved_model, usage)`."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        return None
    resolved_model = payload.get("model")
    if not isinstance(resolved_model, str) or not resolved_model:
        resolved_model = None
    usage = _parse_usage(payload.get("usage"))
    return content, resolved_model, usage


def _classify_guard_failure(exc: OutboundGuardError) -> StructuredOutputProbeState:
    """Same convention every sibling FTF-020x module already established:
    `guarded_fetch` wraps every connection-level failure as
    `OutboundGuardError(...) from <original exception>`, and the original
    cause is what is actually classified."""
    cause = exc.__cause__
    if isinstance(cause, socket.gaierror):
        return StructuredOutputProbeState.DNS_FAILED
    if isinstance(cause, ssl.SSLCertVerificationError):
        return StructuredOutputProbeState.CERTIFICATE_ERROR
    if isinstance(cause, ssl.SSLError):
        return StructuredOutputProbeState.CERTIFICATE_ERROR
    if isinstance(cause, TimeoutError):
        return StructuredOutputProbeState.TIMEOUT
    if isinstance(cause, ConnectionRefusedError):
        return StructuredOutputProbeState.CONNECTION_REFUSED
    return StructuredOutputProbeState.UNREACHABLE


def _resolve_inference_key(
    secret_ref: str, *, secret_config: OpenBaoConfig | None, now: datetime
) -> tuple[str, None, str] | tuple[None, StructuredOutputProbeState, str]:
    if not secret_ref.startswith(_SUPPORTED_SECRET_REF_PREFIX):
        return (
            None,
            StructuredOutputProbeState.SECRET_BACKEND_UNSUPPORTED,
            "no secret backend is configured for this reference's scheme",
        )
    if secret_config is None:
        return (
            None,
            StructuredOutputProbeState.SECRET_BACKEND_ERROR,
            "secret backend is not configured in this environment",
        )
    try:
        key = resolve_secret_ref(secret_ref, config=secret_config, now=now)
    except SecretNotFoundError:
        return (
            None,
            StructuredOutputProbeState.SECRET_NOT_FOUND,
            "secret reference has no live data",
        )
    except SecretAccessDeniedError:
        return (
            None,
            StructuredOutputProbeState.SECRET_ACCESS_DENIED,
            "secret backend denied access to this reference",
        )
    except SecretExpiredError:
        return None, StructuredOutputProbeState.SECRET_EXPIRED, "secret reference has expired"
    except SecretRefFormatError:
        return (
            None,
            StructuredOutputProbeState.SECRET_BACKEND_UNSUPPORTED,
            "secret reference is not a supported backend format",
        )
    except SecretBackendError:
        return (
            None,
            StructuredOutputProbeState.SECRET_BACKEND_ERROR,
            "secret backend request failed",
        )
    return key, None, ""


def _classify_rejection(body: bytes) -> tuple[StructuredOutputProbeState, str]:
    """Classifies a real `400` gateway response using the OpenAI-compatible
    `{"error": {"type": ..., "code": ..., "param": ...}}` envelope -- same
    taxonomy `real_chat_probe.py` established. A rejection naming
    `response_format` (or a generic unsupported/unknown-parameter code) is
    `UNSUPPORTED_PARAMETER` -- this task's own "unknown parameter
    rejection" negative_acceptance clause. An unparseable or
    unrecognized-shape rejection is `MALFORMED_RESPONSE`, never guessed."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return (
            StructuredOutputProbeState.MALFORMED_RESPONSE,
            "gateway rejection body was not valid JSON",
        )
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return (
            StructuredOutputProbeState.MALFORMED_RESPONSE,
            "gateway rejection body had no error object",
        )
    code = error.get("code") if isinstance(error.get("code"), str) else None
    error_type = error.get("type") if isinstance(error.get("type"), str) else None
    param = error.get("param") if isinstance(error.get("param"), str) else None
    if code in _CONTENT_FILTER_CODES or error_type in _CONTENT_FILTER_TYPES:
        return StructuredOutputProbeState.CONTENT_FILTERED, "gateway rejected the probe content"
    if param is not None or code in _UNSUPPORTED_PARAMETER_CODES:
        return (
            StructuredOutputProbeState.UNSUPPORTED_PARAMETER,
            "gateway rejected an unsupported request parameter",
        )
    return (
        StructuredOutputProbeState.MALFORMED_RESPONSE,
        "gateway rejected the request for an unrecognized reason",
    )


def probe_structured_output(
    *,
    base_url: str,
    model_id: str,
    approved_model_ids: frozenset[str],
    secret_ref: str,
    secret_config: OpenBaoConfig | None,
    policy: ConnectionPolicy,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> StructuredOutputProbeResult:
    """Issues one real, bounded, synthetic structured-output
    `POST /v1/chat/completions` call and schema-validates the actual
    response. Never raises for an ordinary gateway/connectivity/content
    outcome -- every such case is a real, distinct, sanitized
    `StructuredOutputProbeResult` (this task's own negative_acceptance).
    Raises `ChatProbeNotAuthorizedError` -- before any network call --
    when `model_id` is not explicitly approved (reused from
    `real_chat_probe.py`, this task's own acceptance clause)."""
    if model_id not in approved_model_ids:
        raise ChatProbeNotAuthorizedError(
            f"model {model_id!r} is not in the approved auto-probe set"
        )

    probed_at = now if now is not None else datetime.now(UTC)

    key, failure_state, detail = _resolve_inference_key(
        secret_ref, secret_config=secret_config, now=probed_at
    )
    if failure_state is not None:
        return StructuredOutputProbeResult(
            state=failure_state,
            route_identity_state=RouteIdentityState.UNVERIFIED,
            resolved_model=None,
            parsed_content=None,
            usage=None,
            latency_ms=None,
            detail=detail,
            probed_at=probed_at,
        )

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    request_body = json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": _PROBE_PROMPT}],
            "max_tokens": _PROBE_MAX_TOKENS,
            "stream": False,
            "response_format": _PROBE_JSON_SCHEMA_REQUEST,
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
        return StructuredOutputProbeResult(
            state=state,
            route_identity_state=RouteIdentityState.UNVERIFIED,
            resolved_model=None,
            parsed_content=None,
            usage=None,
            latency_ms=None,
            detail="gateway connection failed",
            probed_at=probed_at,
        )
    except OutboundPolicyError:
        return StructuredOutputProbeResult(
            state=StructuredOutputProbeState.DESTINATION_DENIED,
            route_identity_state=RouteIdentityState.UNVERIFIED,
            resolved_model=None,
            parsed_content=None,
            usage=None,
            latency_ms=None,
            detail="gateway destination is not authorized by policy",
            probed_at=probed_at,
        )
    latency_ms = (monotonic() - started) * 1000.0

    if response.status in (401, 403):
        return StructuredOutputProbeResult(
            state=StructuredOutputProbeState.INVALID_TOKEN,
            route_identity_state=RouteIdentityState.UNVERIFIED,
            resolved_model=None,
            parsed_content=None,
            usage=None,
            latency_ms=latency_ms,
            detail="gateway rejected the inference credential",
            probed_at=probed_at,
        )

    if response.status == 400:
        classification, error_detail = _classify_rejection(response.body)
        return StructuredOutputProbeResult(
            state=classification,
            route_identity_state=RouteIdentityState.UNVERIFIED,
            resolved_model=None,
            parsed_content=None,
            usage=None,
            latency_ms=latency_ms,
            detail=error_detail,
            probed_at=probed_at,
        )

    if response.status != 200:
        return StructuredOutputProbeResult(
            state=StructuredOutputProbeState.UNREACHABLE,
            route_identity_state=RouteIdentityState.UNVERIFIED,
            resolved_model=None,
            parsed_content=None,
            usage=None,
            latency_ms=latency_ms,
            detail=f"gateway returned unexpected status {response.status}",
            probed_at=probed_at,
        )

    parsed = _parse_structured_content(response.body)
    if parsed is None:
        return StructuredOutputProbeResult(
            state=StructuredOutputProbeState.MALFORMED_RESPONSE,
            route_identity_state=RouteIdentityState.UNVERIFIED,
            resolved_model=None,
            parsed_content=None,
            usage=None,
            latency_ms=latency_ms,
            detail="gateway response was not a valid chat completion",
            probed_at=probed_at,
        )

    content, resolved_model, usage = parsed
    route_identity_state = (
        RouteIdentityState.VERIFIED if resolved_model is not None else RouteIdentityState.UNVERIFIED
    )

    try:
        decoded_content = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return StructuredOutputProbeResult(
            state=StructuredOutputProbeState.NOT_JSON,
            route_identity_state=route_identity_state,
            resolved_model=resolved_model,
            parsed_content=None,
            usage=usage,
            latency_ms=latency_ms,
            detail="gateway response content did not parse as JSON",
            probed_at=probed_at,
        )

    if not isinstance(decoded_content, dict) or not _validate_against_schema(
        decoded_content, _PROBE_SCHEMA
    ):
        return StructuredOutputProbeResult(
            state=StructuredOutputProbeState.SCHEMA_VIOLATION,
            route_identity_state=route_identity_state,
            resolved_model=resolved_model,
            parsed_content=None,
            usage=usage,
            latency_ms=latency_ms,
            detail="gateway response content did not validate against the probe schema",
            probed_at=probed_at,
        )

    return StructuredOutputProbeResult(
        state=StructuredOutputProbeState.PROBED,
        route_identity_state=route_identity_state,
        resolved_model=resolved_model,
        parsed_content=decoded_content,
        usage=usage,
        latency_ms=latency_ms,
        detail="gateway probed successfully",
        probed_at=probed_at,
    )
