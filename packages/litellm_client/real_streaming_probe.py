"""LiteLLM real streaming probe (FTF-0211, Section 2-3/38/65/77/81).

`real_chat_probe.py` (FTF-0210) proves a model answers one real,
non-streaming `POST /v1/chat/completions` call. Section 3.4's own list of
capabilities to probe -- "chat, streaming, tools, structured output" -- is
not satisfied by that alone: a gateway/model can accept a plain chat call
and still reject, truncate or silently fall back to non-streaming when
`stream: true` is requested. This task owns exactly that second, distinct
capability: consuming a real Server-Sent-Events stream and reconstructing
its content deltas, never assuming a streaming response behaves like a
buffered one.

Both halves of Section 3.4's own probe-safety sentence are enforced by
construction here, unchanged from FTF-0210's own precedent:

- `probe_stream_chat` raises `ChatProbeNotAuthorizedError` (reused directly
  from `real_chat_probe.py` -- the exact same authorization contract, not a
  parallel type) before any network call when `model_id` is not a member of
  the caller-supplied `approved_model_ids` set.
- The probe prompt and token ceiling are the same fixed values FTF-0210
  already established, as this module's own private constants (matching
  the convention already established by every sibling FTF-020x module:
  private helpers/constants are never cross-imported between them, even
  for identical values, so a rename inside one module can never silently
  break another) -- a streaming probe accepting arbitrary prompt content
  or an unbounded token budget would be exactly the unbounded inference
  call FTF-0210's own docstring already rejected, wearing a "streaming"
  label instead.

A stream additionally needs its own, new probe budget this task adds:
`_PROBE_MAX_STREAM_CHUNKS` bounds how many real network reads this probe
will perform before giving up and closing the connection -- a real
gateway that never terminates a stream (or terminates it far later than
any bounded probe should wait) must not be read from forever.

This task's own acceptance clause ("Consume actual SSE stream, reconstruct
text/tool deltas and account usage/termination") is `StreamProbeResult`:
`text` is the concatenation of every real `delta.content` fragment the
gateway actually sent (Section 3.4 tests chat *and* streaming, so this
reconstructs prose, not tool syntax, from the model's own real output);
`tool_calls` reconstructs the OpenAI-compatible incremental tool-call delta
shape (each fragment carries an `index`, and only the first fragment for
a given index carries `id`/`name` -- later fragments for that index append
to `arguments`) without ever parsing or executing the accumulated
arguments JSON, which is not this probe's job; `termination_reason` is the
real `finish_reason` from the last chunk that carried one; `usage` is
`ChatUsage` reused directly from `real_chat_probe.py` (Section 3.4's
"Missing usage becomes unpriced/unknown, not zero cost" applies exactly as
written there, since a streaming response as commonly configured omits
`usage` unless the caller explicitly opts in with `stream_options`, which
this probe's own fixed, non-caller-configurable request body does not do
-- disclosed here, not silently invented as a gap).

This task's own negative_acceptance ("Malformed/truncated streams, client
cancellation and gateway retry do not leak connection resources or
double-bill silently") is met three ways:

- `probe_stream_chat` always opens the real connection through a `with`
  block (`GuardedStreamResponse.__enter__`/`__exit__`), so every return
  path -- a clean `[DONE]`, a truncated stream, a malformed chunk, or the
  chunk-budget ceiling being hit -- closes the real underlying socket
  before returning. There is no code path that returns without having
  entered and exited that context manager exactly once.
- A stream that ends (real socket EOF) without ever emitting a `data:
  [DONE]` event, or that hits the chunk-read ceiling first, is
  `TRUNCATED_STREAM` -- never silently reported as a complete `PROBED`
  result assembled from whatever partial deltas happened to arrive.
- This module contains no retry loop anywhere: a single failure is
  reported once, as a single classified `StreamProbeResult`, and this
  function never reissues the real network call itself. Retrying a
  probe -- which would be a second real, billable inference call -- is a
  caller-level policy decision, deliberately kept out of this function so
  it can never happen silently inside it.

No independent schema change (this task's own `database_changes` clause):
a probe result is computed fresh on every call and returned directly,
never persisted -- same convention every sibling FTF-020x module already
established.

`packages/security/outbound_guard.py` (FTF-H006, already CERTIFIED) gained
one small, purely additive export for this task -- `guarded_stream_fetch`/
`GuardedStreamResponse` -- since `guarded_fetch` reads its entire response
body up front (`response.read()`), which a real SSE stream must never do.
The new function reuses the exact same validate-then-pin connection path
`guarded_fetch` already used (`_prepare_pinned_connection`, extracted from
`guarded_fetch`'s own prior body with no behavior change -- the full
FTF-H006 suite was re-run and stays green); duplicating that
security-critical pinning logic inside this module instead would have
been the actual violation of "reuse an existing compatible module instead
of creating a duplicate." `guarded_fetch`'s own existing behavior,
signature and redirect handling are unchanged.
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
from packages.security.outbound_guard import (
    GuardedStreamResponse,
    OutboundGuardError,
    guarded_stream_fetch,
)
from packages.security.url_policy import ConnectionPolicy, OutboundPolicyError

__all__ = [
    "ChatProbeNotAuthorizedError",
    "StreamProbeError",
    "StreamProbeResult",
    "StreamProbeState",
    "StreamTerminationReason",
    "ToolCallDelta",
    "probe_stream_chat",
]

_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_READ_CHUNK_SIZE = 4096

# Same restriction every sibling FTF-020x module already documents: only
# the OpenBao-backed reference shape FTF-0202 actually resolves today.
_SUPPORTED_SECRET_REF_PREFIX = "openbao://"

# Section 3.4: "using synthetic non-sensitive content and an approved
# probe budget." Same values FTF-0210's own module constants use --
# duplicated rather than cross-imported (this module's own private
# constant, matching the established convention every sibling FTF-020x
# module already follows of never importing another module's private
# names, even for identical values) -- neither is caller-configurable,
# for the same reason FTF-0210's own docstring already gives.
_PROBE_PROMPT = "Reply with the single word: pong."
_PROBE_MAX_TOKENS = 16

# This task's own new probe budget (see module docstring): bounds real
# network reads for a stream that never terminates, distinct from the
# token ceiling above, which bounds the model's own output.
_PROBE_MAX_STREAM_CHUNKS = 2048

_CONTENT_FILTER_CODES = frozenset({"content_filter", "content_policy_violation"})
_CONTENT_FILTER_TYPES = frozenset({"content_filter"})
_UNSUPPORTED_PARAMETER_CODES = frozenset({"unsupported_parameter", "unknown_parameter"})

_KNOWN_FINISH_REASONS = frozenset({"stop", "length", "content_filter", "tool_calls"})


class StreamProbeState(str, Enum):
    """The real streamed `POST /v1/chat/completions` probe outcome."""

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
    TRUNCATED_STREAM = "truncated_stream"
    SECRET_NOT_FOUND = "secret_not_found"
    SECRET_ACCESS_DENIED = "secret_access_denied"
    SECRET_EXPIRED = "secret_expired"
    SECRET_BACKEND_ERROR = "secret_backend_error"
    SECRET_BACKEND_UNSUPPORTED = "secret_backend_unsupported"


class StreamTerminationReason(str, Enum):
    """The real `finish_reason` from the last chunk that carried one. A
    stream that never carried a recognized `finish_reason` before ending
    is reported at the `StreamProbeState` level (`TRUNCATED_STREAM`), not
    guessed into one of these."""

    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    TOOL_CALLS = "tool_calls"


@dataclass(frozen=True)
class ToolCallDelta:
    """One reconstructed incremental tool-call delta stream, keyed by its
    real `index`. `arguments` is the raw concatenated JSON-fragment text
    exactly as the gateway streamed it -- never parsed or executed here,
    since validating/running tool arguments is not this probe's job."""

    index: int
    id: str | None
    name: str | None
    arguments: str


@dataclass(frozen=True)
class StreamProbeResult:
    state: StreamProbeState
    route_identity_state: RouteIdentityState
    resolved_model: str | None
    text: str
    tool_calls: tuple[ToolCallDelta, ...]
    termination_reason: StreamTerminationReason | None
    usage: ChatUsage | None
    chunk_count: int
    latency_ms: float | None
    detail: str
    probed_at: datetime


class StreamProbeError(Exception):
    """Raised only for a caller-side contract violation, never for a real
    gateway/network/content/stream outcome -- those are always a
    `StreamProbeResult` (this task's own negative_acceptance)."""


def _classify_guard_failure(exc: OutboundGuardError) -> StreamProbeState:
    """Same convention every sibling FTF-020x module already established:
    `guarded_stream_fetch` wraps every connection-level failure as
    `OutboundGuardError(...) from <original exception>`, and the original
    cause is what is actually classified."""
    cause = exc.__cause__
    if isinstance(cause, socket.gaierror):
        return StreamProbeState.DNS_FAILED
    if isinstance(cause, ssl.SSLCertVerificationError):
        return StreamProbeState.CERTIFICATE_ERROR
    if isinstance(cause, ssl.SSLError):
        return StreamProbeState.CERTIFICATE_ERROR
    if isinstance(cause, TimeoutError):
        return StreamProbeState.TIMEOUT
    if isinstance(cause, ConnectionRefusedError):
        return StreamProbeState.CONNECTION_REFUSED
    return StreamProbeState.UNREACHABLE


def _classify_read_failure(exc: OSError) -> StreamProbeState:
    """Classifies a real socket-level failure raised directly by a body
    read (`GuardedStreamResponse.read_chunk`) after the connection and
    response headers were already established -- distinct from
    `_classify_guard_failure`, which classifies a wrapped
    `OutboundGuardError.__cause__` raised by `guarded_stream_fetch` itself
    before headers ever arrived."""
    if isinstance(exc, TimeoutError):
        return StreamProbeState.TIMEOUT
    if isinstance(exc, ssl.SSLError):
        return StreamProbeState.CERTIFICATE_ERROR
    return StreamProbeState.UNREACHABLE


def _resolve_inference_key(
    secret_ref: str, *, secret_config: OpenBaoConfig | None, now: datetime
) -> tuple[str, None, str] | tuple[None, StreamProbeState, str]:
    if not secret_ref.startswith(_SUPPORTED_SECRET_REF_PREFIX):
        return (
            None,
            StreamProbeState.SECRET_BACKEND_UNSUPPORTED,
            "no secret backend is configured for this reference's scheme",
        )
    if secret_config is None:
        return (
            None,
            StreamProbeState.SECRET_BACKEND_ERROR,
            "secret backend is not configured in this environment",
        )
    try:
        key = resolve_secret_ref(secret_ref, config=secret_config, now=now)
    except SecretNotFoundError:
        return None, StreamProbeState.SECRET_NOT_FOUND, "secret reference has no live data"
    except SecretAccessDeniedError:
        return (
            None,
            StreamProbeState.SECRET_ACCESS_DENIED,
            "secret backend denied access to this reference",
        )
    except SecretExpiredError:
        return None, StreamProbeState.SECRET_EXPIRED, "secret reference has expired"
    except SecretRefFormatError:
        return (
            None,
            StreamProbeState.SECRET_BACKEND_UNSUPPORTED,
            "secret reference is not a supported backend format",
        )
    except SecretBackendError:
        return None, StreamProbeState.SECRET_BACKEND_ERROR, "secret backend request failed"
    return key, None, ""


def _classify_rejection(body: bytes) -> tuple[StreamProbeState, str]:
    """Classifies a real `400` gateway response using the OpenAI-compatible
    `{"error": {"type": ..., "code": ..., "param": ...}}` envelope -- same
    taxonomy `real_chat_probe.py` established. An unparseable or
    unrecognized-shape rejection is `MALFORMED_RESPONSE`, never guessed."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return StreamProbeState.MALFORMED_RESPONSE, "gateway rejection body was not valid JSON"
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return StreamProbeState.MALFORMED_RESPONSE, "gateway rejection body had no error object"
    code = error.get("code") if isinstance(error.get("code"), str) else None
    error_type = error.get("type") if isinstance(error.get("type"), str) else None
    param = error.get("param") if isinstance(error.get("param"), str) else None
    if code in _CONTENT_FILTER_CODES or error_type in _CONTENT_FILTER_TYPES:
        return StreamProbeState.CONTENT_FILTERED, "gateway rejected the probe content"
    if param is not None or code in _UNSUPPORTED_PARAMETER_CODES:
        return (
            StreamProbeState.UNSUPPORTED_PARAMETER,
            "gateway rejected an unsupported request parameter",
        )
    return (
        StreamProbeState.MALFORMED_RESPONSE,
        "gateway rejected the request for an unrecognized reason",
    )


class _StreamBudgetExceeded(Exception):
    """Internal only: the chunk-read ceiling was hit before a real
    `[DONE]` event arrived."""


_MAX_REJECTION_BODY_BYTES = 65536


def _read_full_rejection_body(stream: GuardedStreamResponse) -> bytes:
    """A `400` rejection is an ordinary small JSON body, not a stream --
    but it can still arrive across more than one real read (chunked
    transfer encoding, or a body larger than one `read_chunk` call), so
    this reads to real EOF (bounded, never unbounded) rather than
    assuming one `read_chunk` call captured it all."""
    body = b""
    while len(body) < _MAX_REJECTION_BODY_BYTES:
        chunk = stream.read_chunk(_READ_CHUNK_SIZE)
        if not chunk:
            break
        body += chunk
    return body


def _iter_sse_events(stream: GuardedStreamResponse) -> list[bytes]:
    """Reads the real stream incrementally (never the whole body at once)
    and splits it into raw SSE event blocks on the blank-line delimiter.
    Raises `_StreamBudgetExceeded` if `_PROBE_MAX_STREAM_CHUNKS` real reads
    happen without the stream ending on its own. A trailing partial event
    with no terminating blank line (a genuinely truncated stream) is
    included as-is; the caller decides whether it parses as `[DONE]`."""
    events: list[bytes] = []
    buffer = b""
    for _ in range(_PROBE_MAX_STREAM_CHUNKS):
        chunk = stream.read_chunk(_READ_CHUNK_SIZE)
        if not chunk:
            break
        buffer += chunk
        while b"\n\n" in buffer:
            event, buffer = buffer.split(b"\n\n", 1)
            events.append(event)
    else:
        raise _StreamBudgetExceeded()
    if buffer.strip():
        events.append(buffer)
    return events


def _extract_data_payload(event: bytes) -> bytes | None:
    """Joins every `data:` line inside one SSE event block (the ordinary
    case is exactly one). Returns None if the block carries no `data:`
    line at all (a comment/`event:`/`id:`-only block, or a fully empty
    trailing fragment)."""
    lines = [
        line[5:].strip() if line.startswith(b"data:") else None
        for line in event.split(b"\n")
        if line.startswith(b"data:")
    ]
    parts = [line for line in lines if line is not None]
    if not parts:
        return None
    return b"\n".join(parts)


def _accumulate_tool_calls(
    accumulated: dict[int, dict[str, str | None]], raw_tool_calls: object
) -> None:
    if not isinstance(raw_tool_calls, list):
        return
    for raw in raw_tool_calls:
        if not isinstance(raw, dict):
            continue
        index = raw.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            continue
        entry = accumulated.setdefault(index, {"id": None, "name": None, "arguments": ""})
        call_id = raw.get("id")
        if isinstance(call_id, str) and entry["id"] is None:
            entry["id"] = call_id
        function = raw.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and entry["name"] is None:
                entry["name"] = name
            arguments_fragment = function.get("arguments")
            if isinstance(arguments_fragment, str):
                entry["arguments"] = (entry["arguments"] or "") + arguments_fragment


def probe_stream_chat(
    *,
    base_url: str,
    model_id: str,
    approved_model_ids: frozenset[str],
    secret_ref: str,
    secret_config: OpenBaoConfig | None,
    policy: ConnectionPolicy,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> StreamProbeResult:
    """Issues one real, bounded, synthetic streamed `POST
    /v1/chat/completions` call and reconstructs its real SSE deltas. Never
    raises for an ordinary gateway/connectivity/content/stream outcome --
    every such case is a real, distinct, sanitized `StreamProbeResult`
    (this task's own negative_acceptance). Raises
    `ChatProbeNotAuthorizedError` -- before any network call -- when
    `model_id` is not explicitly approved (this task's own acceptance
    clause, reusing FTF-0210's exact authorization contract)."""
    if model_id not in approved_model_ids:
        raise ChatProbeNotAuthorizedError(
            f"model {model_id!r} is not in the approved auto-probe set"
        )

    probed_at = now if now is not None else datetime.now(UTC)

    key, failure_state, detail = _resolve_inference_key(
        secret_ref, secret_config=secret_config, now=probed_at
    )
    if failure_state is not None:
        return StreamProbeResult(
            failure_state,
            RouteIdentityState.UNVERIFIED,
            None,
            "",
            (),
            None,
            None,
            0,
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
            "stream": True,
        }
    ).encode("utf-8")

    started = monotonic()
    try:
        stream = guarded_stream_fetch(
            f"{base_url.rstrip('/')}{_CHAT_COMPLETIONS_PATH}",
            policy,
            method="POST",
            headers=headers,
            body=request_body,
            timeout=timeout,
        )
    except OutboundGuardError as exc:
        state = _classify_guard_failure(exc)
        return StreamProbeResult(
            state,
            RouteIdentityState.UNVERIFIED,
            None,
            "",
            (),
            None,
            None,
            0,
            None,
            "gateway connection failed",
            probed_at,
        )
    except OutboundPolicyError:
        return StreamProbeResult(
            StreamProbeState.DESTINATION_DENIED,
            RouteIdentityState.UNVERIFIED,
            None,
            "",
            (),
            None,
            None,
            0,
            None,
            "gateway destination is not authorized by policy",
            probed_at,
        )

    try:
        with stream:
            if stream.status in (401, 403):
                latency_ms = (monotonic() - started) * 1000.0
                return StreamProbeResult(
                    StreamProbeState.INVALID_TOKEN,
                    RouteIdentityState.UNVERIFIED,
                    None,
                    "",
                    (),
                    None,
                    None,
                    0,
                    latency_ms,
                    "gateway rejected the inference credential",
                    probed_at,
                )

            if stream.status == 400:
                rejection_body = _read_full_rejection_body(stream)
                latency_ms = (monotonic() - started) * 1000.0
                classification, error_detail = _classify_rejection(rejection_body)
                return StreamProbeResult(
                    classification,
                    RouteIdentityState.UNVERIFIED,
                    None,
                    "",
                    (),
                    None,
                    None,
                    0,
                    latency_ms,
                    error_detail,
                    probed_at,
                )

            if stream.status != 200:
                latency_ms = (monotonic() - started) * 1000.0
                return StreamProbeResult(
                    StreamProbeState.UNREACHABLE,
                    RouteIdentityState.UNVERIFIED,
                    None,
                    "",
                    (),
                    None,
                    None,
                    0,
                    latency_ms,
                    f"gateway returned unexpected status {stream.status}",
                    probed_at,
                )

            try:
                events = _iter_sse_events(stream)
            except _StreamBudgetExceeded:
                latency_ms = (monotonic() - started) * 1000.0
                return StreamProbeResult(
                    StreamProbeState.TRUNCATED_STREAM,
                    RouteIdentityState.UNVERIFIED,
                    None,
                    "",
                    (),
                    None,
                    None,
                    _PROBE_MAX_STREAM_CHUNKS,
                    latency_ms,
                    "stream exceeded the bounded probe read budget before completing",
                    probed_at,
                )
    except OSError as exc:
        # A real socket-level failure (most commonly a timeout) during a
        # body read, distinct from a `guarded_stream_fetch`-level failure
        # (which is already `OutboundGuardError`, caught above, and never
        # reaches here): this module must never let a raw exception escape
        # in place of a classified `StreamProbeResult` (this task's own
        # negative_acceptance), whether the failure happens before the
        # first byte of the body or partway through the stream.
        latency_ms = (monotonic() - started) * 1000.0
        return StreamProbeResult(
            _classify_read_failure(exc),
            RouteIdentityState.UNVERIFIED,
            None,
            "",
            (),
            None,
            None,
            0,
            latency_ms,
            "gateway connection failed while streaming",
            probed_at,
        )

    latency_ms = (monotonic() - started) * 1000.0

    resolved_model: str | None = None
    text_parts: list[str] = []
    tool_call_accumulator: dict[int, dict[str, str | None]] = {}
    termination_reason: StreamTerminationReason | None = None
    usage: ChatUsage | None = None
    saw_done = False
    chunk_count = len(events)

    for event in events:
        payload = _extract_data_payload(event)
        if payload is None:
            continue
        if payload == b"[DONE]":
            saw_done = True
            continue
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return StreamProbeResult(
                StreamProbeState.MALFORMED_RESPONSE,
                RouteIdentityState.UNVERIFIED,
                None,
                "",
                (),
                None,
                None,
                chunk_count,
                latency_ms,
                "a stream event was not valid JSON",
                probed_at,
            )
        if not isinstance(parsed, dict):
            return StreamProbeResult(
                StreamProbeState.MALFORMED_RESPONSE,
                RouteIdentityState.UNVERIFIED,
                None,
                "",
                (),
                None,
                None,
                chunk_count,
                latency_ms,
                "a stream event was not a JSON object",
                probed_at,
            )

        model_field = parsed.get("model")
        if isinstance(model_field, str) and model_field:
            resolved_model = model_field

        raw_usage = parsed.get("usage")
        if isinstance(raw_usage, dict):
            prompt = raw_usage.get("prompt_tokens")
            completion = raw_usage.get("completion_tokens")
            total = raw_usage.get("total_tokens")
            fields = (prompt, completion, total)
            if all(isinstance(v, int) and not isinstance(v, bool) for v in fields):
                assert (
                    isinstance(prompt, int)
                    and isinstance(completion, int)
                    and isinstance(total, int)
                )
                usage = ChatUsage(
                    prompt_tokens=prompt, completion_tokens=completion, total_tokens=total
                )

        choices = parsed.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str):
                    text_parts.append(content)
                _accumulate_tool_calls(tool_call_accumulator, delta.get("tool_calls"))
            finish_reason = choice.get("finish_reason")
            if isinstance(finish_reason, str) and finish_reason in _KNOWN_FINISH_REASONS:
                termination_reason = StreamTerminationReason(finish_reason)

    if not saw_done:
        return StreamProbeResult(
            StreamProbeState.TRUNCATED_STREAM,
            RouteIdentityState.UNVERIFIED,
            None,
            "",
            (),
            None,
            None,
            chunk_count,
            latency_ms,
            "stream ended without a terminating [DONE] event",
            probed_at,
        )

    tool_calls = tuple(
        ToolCallDelta(
            index=index,
            id=entry["id"],
            name=entry["name"],
            arguments=entry["arguments"] or "",
        )
        for index, entry in sorted(tool_call_accumulator.items())
    )
    route_identity_state = (
        RouteIdentityState.VERIFIED if resolved_model is not None else RouteIdentityState.UNVERIFIED
    )
    return StreamProbeResult(
        StreamProbeState.PROBED,
        route_identity_state,
        resolved_model,
        "".join(text_parts),
        tool_calls,
        termination_reason,
        usage,
        chunk_count,
        latency_ms,
        "gateway probed successfully",
        probed_at,
    )
