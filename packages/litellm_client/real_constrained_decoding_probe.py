"""LiteLLM real constrained-choice probe (FTF-0213-S1, Section 2-3/29/36/38/65/77/81).

Child slice of the certified FTF-0213 structured-output probe. FTF-0213
proves a route returns schema-valid JSON when the prompt *asks* for it. It
cannot tell a route that enforces `response_format` from one whose model
merely follows the prompt: a fixed `{"word": "pong"}` schema is satisfied by
a compliant model either way. This module owns the missing observation --
does the route *enforce* a constrained choice against the prompt -- and the
output-token count of that constrained answer (Section 29 "output
tokens/task", Section 3.4 "missing usage is unpriced, not zero").

How enforcement is observed: for each of `_TRIALS` trials the probe draws a
fresh enum of three nonsense tokens from a fixed synthetic vocabulary
(enums are disjoint across trials, so a cached or replayed answer from one
trial can never satisfy another) and sends the portable OpenAI
`response_format: {"type": "json_schema", ..., "strict": true}` request
only -- never a vendor extra such as `guided_choice`, which is
version-dependent and which a gateway may silently drop -- with an
adversarial prompt telling the model to answer with a word outside the
enum. Only an answer that is exactly `{"label": <member of this trial's
enum>}` counts. `CONSTRAINED_OBSERVED` is an observation, not proof of the
mechanism: a capable model could obey the schema against the prompt for all
three random enums; that is unlikely, not impossible. The real-engine run
belongs to FTF-1006.

States are never collapsed into success: `NOT_ENFORCED` (prose, the prompted
word, a non-member, extra keys, coercion), `UNSUPPORTED_PARAMETER` (400 on
the parameter; never retried without the constraint), `TRUNCATED`,
`USAGE_MISSING` (constraint observed but token count unknown), plus the
transport/secret states FTF-0210/0213 already established.

Probe-safety (Section 3.4) is enforced by construction, as in FTF-0210/0213:
`ChatProbeNotAuthorizedError` (reused) is raised before any secret read or
network call for an unapproved model; the prompt, token ceiling, temperature
and trial count are module constants, never caller-supplied. No content is
stored or reported -- only states, counts and token numbers.

No schema change, route, UI or dependency: a result is computed fresh and
returned, never persisted. UNWIRED (the `/models/{id}/probe` route is not
live), same FTF-0213 precedent.
"""

from __future__ import annotations

import json
import secrets
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from time import monotonic
from typing import Any, Protocol

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
    "ConstrainedDecodingProbeResult",
    "ConstrainedDecodingProbeState",
    "ConstrainedTrial",
    "probe_constrained_choice",
]

_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_SUPPORTED_SECRET_REF_PREFIX = "openbao://"

_TRIALS = 3
_ENUM_SIZE = 3
_MAX_TOKENS = 16
_TEMPERATURE = 0
_SCHEMA_NAME = "ftf_0213_s1_choice"
# Adversarial on purpose: it asks for a word outside every trial's enum, so
# only a route that enforces the schema can answer with an enum member.
_PROBE_PROMPT = "Reply with the single word: banana"
_PROMPTED_WORD = "banana"
_VOCABULARY: tuple[str, ...] = tuple(
    a + b
    for a in ("zor", "qua", "vel", "mix", "dap", "nul", "kre", "fyn")
    for b in ("blix", "tomp", "grav", "shen", "wuzz", "drel", "ptan", "yosk")
)
assert len(_VOCABULARY) == 64 and len(set(_VOCABULARY)) == 64
assert _PROMPTED_WORD not in _VOCABULARY

_CONTENT_FILTER_CODES = frozenset({"content_filter", "content_policy_violation"})
_CONTENT_FILTER_TYPES = frozenset({"content_filter"})
_UNSUPPORTED_PARAMETER_CODES = frozenset({"unsupported_parameter", "unknown_parameter"})


class _Sampler(Protocol):
    def sample(self, population: Any, k: int) -> list[Any]: ...


class ConstrainedDecodingProbeState(str, Enum):
    CONSTRAINED_OBSERVED = "constrained_observed"
    NOT_ENFORCED = "not_enforced"
    UNSUPPORTED_PARAMETER = "unsupported_parameter"
    TRUNCATED = "truncated"
    USAGE_MISSING = "usage_missing"
    CONTENT_FILTERED = "content_filtered"
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


_State = ConstrainedDecodingProbeState


@dataclass(frozen=True)
class ConstrainedTrial:
    """One trial's observation. No content: only state and counts."""

    finish_reason: str
    completion_tokens: int | None
    label_in_enum: bool
    route_identity_state: RouteIdentityState
    latency_ms: float


@dataclass(frozen=True)
class ConstrainedDecodingProbeResult:
    state: ConstrainedDecodingProbeState
    route_identity_state: RouteIdentityState
    resolved_model: str | None
    trials: tuple[ConstrainedTrial, ...]
    max_completion_tokens_observed: int | None
    mean_completion_tokens: Decimal | None
    detail: str
    probed_at: datetime


def _result(
    state: ConstrainedDecodingProbeState,
    detail: str,
    probed_at: datetime,
    *,
    trials: tuple[ConstrainedTrial, ...] = (),
    resolved_model: str | None = None,
    identity: RouteIdentityState = RouteIdentityState.UNVERIFIED,
    max_tokens_observed: int | None = None,
    mean_tokens: Decimal | None = None,
) -> ConstrainedDecodingProbeResult:
    return ConstrainedDecodingProbeResult(
        state=state,
        route_identity_state=identity,
        resolved_model=resolved_model,
        trials=trials,
        max_completion_tokens_observed=max_tokens_observed,
        mean_completion_tokens=mean_tokens,
        detail=detail,
        probed_at=probed_at,
    )


def _draw_enums(rng: _Sampler) -> list[tuple[str, ...]]:
    """Disjoint enums, one per trial, from one sample without replacement."""
    drawn = [str(token) for token in rng.sample(_VOCABULARY, _TRIALS * _ENUM_SIZE)]
    if len(drawn) != _TRIALS * _ENUM_SIZE or len(set(drawn)) != len(drawn):
        raise ValueError("rng did not return distinct vocabulary tokens")
    return [tuple(drawn[i * _ENUM_SIZE : (i + 1) * _ENUM_SIZE]) for i in range(_TRIALS)]


def _request_body(model_id: str, choices: tuple[str, ...]) -> bytes:
    schema = {
        "type": "object",
        "properties": {"label": {"type": "string", "enum": list(choices)}},
        "required": ["label"],
        "additionalProperties": False,
    }
    return json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": _PROBE_PROMPT}],
            "max_tokens": _MAX_TOKENS,
            "temperature": _TEMPERATURE,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": _SCHEMA_NAME, "strict": True, "schema": schema},
            },
        }
    ).encode("utf-8")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _label_is_member(content: str, choices: tuple[str, ...]) -> bool:
    """Exactly `{"label": <str member of this trial's enum>}`: no coercion
    (`{"label": 1}`), no extra keys, no duplicate keys, no prose."""
    try:
        decoded = json.loads(content, object_pairs_hook=_no_duplicate_keys)
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(decoded, dict) or set(decoded) != {"label"}:
        return False
    label = decoded["label"]
    return isinstance(label, str) and label in choices


def _parse_trial_body(body: bytes) -> tuple[str, str, str | None, ChatUsage | None] | None:
    """Returns `(content, finish_reason, resolved_model, usage)` for a real
    200 chat completion, else None. A missing/non-string content or
    finish_reason is malformed, never a guessed success."""
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    first = choices[0]
    message = first.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    finish_reason = first.get("finish_reason")
    if not isinstance(content, str) or not isinstance(finish_reason, str) or not finish_reason:
        return None
    model = payload.get("model")
    resolved = model if isinstance(model, str) and model else None
    return content, finish_reason, resolved, _parse_usage(payload.get("usage"))


def _classify_guard_failure(exc: OutboundGuardError) -> ConstrainedDecodingProbeState:
    cause = exc.__cause__
    if isinstance(cause, socket.gaierror):
        return _State.DNS_FAILED
    if isinstance(cause, ssl.SSLError):
        return _State.CERTIFICATE_ERROR
    if isinstance(cause, TimeoutError):
        return _State.TIMEOUT
    if isinstance(cause, ConnectionRefusedError):
        return _State.CONNECTION_REFUSED
    return _State.UNREACHABLE


def _classify_rejection(body: bytes) -> tuple[ConstrainedDecodingProbeState, str]:
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return _State.MALFORMED_RESPONSE, "gateway rejection body was not valid JSON"
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return _State.MALFORMED_RESPONSE, "gateway rejection body had no error object"
    code = error.get("code") if isinstance(error.get("code"), str) else None
    error_type = error.get("type") if isinstance(error.get("type"), str) else None
    param = error.get("param") if isinstance(error.get("param"), str) else None
    if code in _CONTENT_FILTER_CODES or error_type in _CONTENT_FILTER_TYPES:
        return _State.CONTENT_FILTERED, "gateway rejected the probe content"
    if param is not None or code in _UNSUPPORTED_PARAMETER_CODES:
        return _State.UNSUPPORTED_PARAMETER, "gateway rejected an unsupported request parameter"
    return _State.MALFORMED_RESPONSE, "gateway rejected the request for an unrecognized reason"


def _resolve_inference_key(
    secret_ref: str, *, secret_config: OpenBaoConfig | None, now: datetime
) -> tuple[str, None, str] | tuple[None, ConstrainedDecodingProbeState, str]:
    if not secret_ref.startswith(_SUPPORTED_SECRET_REF_PREFIX):
        return (
            None,
            _State.SECRET_BACKEND_UNSUPPORTED,
            "no secret backend is configured for this reference's scheme",
        )
    if secret_config is None:
        return None, _State.SECRET_BACKEND_ERROR, "secret backend is not configured"
    try:
        key = resolve_secret_ref(secret_ref, config=secret_config, now=now)
    except SecretNotFoundError:
        return None, _State.SECRET_NOT_FOUND, "secret reference has no live data"
    except SecretAccessDeniedError:
        return None, _State.SECRET_ACCESS_DENIED, "secret backend denied access"
    except SecretExpiredError:
        return None, _State.SECRET_EXPIRED, "secret reference has expired"
    except SecretRefFormatError:
        return None, _State.SECRET_BACKEND_UNSUPPORTED, "secret reference format unsupported"
    except SecretBackendError:
        return None, _State.SECRET_BACKEND_ERROR, "secret backend request failed"
    return key, None, ""


def probe_constrained_choice(
    *,
    base_url: str,
    model_id: str,
    approved_model_ids: frozenset[str],
    secret_ref: str,
    secret_config: OpenBaoConfig | None,
    policy: ConnectionPolicy,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    now: datetime | None = None,
    rng: _Sampler | None = None,
) -> ConstrainedDecodingProbeResult:
    """Runs `_TRIALS` bounded synthetic constrained-choice calls. Never
    raises for a gateway/network/content outcome; raises
    `ChatProbeNotAuthorizedError` before any secret read or network call for
    an unapproved model."""
    if model_id not in approved_model_ids:
        raise ChatProbeNotAuthorizedError(
            f"model {model_id!r} is not in the approved auto-probe set"
        )
    probed_at = now if now is not None else datetime.now(UTC)
    key, failure_state, detail = _resolve_inference_key(
        secret_ref, secret_config=secret_config, now=probed_at
    )
    if failure_state is not None:
        return _result(failure_state, detail, probed_at)

    enums = _draw_enums(rng if rng is not None else secrets.SystemRandom())
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    url = f"{base_url.rstrip('/')}{_CHAT_COMPLETIONS_PATH}"
    trials: list[ConstrainedTrial] = []
    resolved_models: list[str | None] = []

    for choices in enums:
        started = monotonic()
        try:
            response = guarded_fetch(
                url,
                policy,
                method="POST",
                headers=headers,
                body=_request_body(model_id, choices),
                timeout=timeout,
            )
        except OutboundGuardError as exc:
            return _result(
                _classify_guard_failure(exc),
                "gateway connection failed",
                probed_at,
                trials=tuple(trials),
            )
        except OutboundPolicyError:
            return _result(
                _State.DESTINATION_DENIED,
                "gateway destination is not authorized by policy",
                probed_at,
                trials=tuple(trials),
            )
        latency_ms = (monotonic() - started) * 1000.0

        if response.status in (401, 403):
            return _result(
                _State.INVALID_TOKEN,
                "gateway rejected the inference credential",
                probed_at,
                trials=tuple(trials),
            )
        if response.status == 400:
            state, error_detail = _classify_rejection(response.body)
            return _result(state, error_detail, probed_at, trials=tuple(trials))
        if response.status != 200:
            return _result(
                _State.UNREACHABLE,
                f"gateway returned unexpected status {response.status}",
                probed_at,
                trials=tuple(trials),
            )
        parsed = _parse_trial_body(response.body)
        if parsed is None:
            return _result(
                _State.MALFORMED_RESPONSE,
                "gateway response was not a valid chat completion",
                probed_at,
                trials=tuple(trials),
            )
        content, finish_reason, resolved_model, usage = parsed
        trials.append(
            ConstrainedTrial(
                finish_reason=finish_reason,
                completion_tokens=usage.completion_tokens if usage is not None else None,
                label_in_enum=_label_is_member(content, choices),
                route_identity_state=(
                    RouteIdentityState.VERIFIED
                    if resolved_model is not None
                    else RouteIdentityState.UNVERIFIED
                ),
                latency_ms=latency_ms,
            )
        )
        resolved_models.append(resolved_model)

    return _aggregate_trials(tuple(trials), resolved_models, probed_at)


def _aggregate_trials(
    trials: tuple[ConstrainedTrial, ...],
    resolved_models: list[str | None],
    probed_at: datetime,
) -> ConstrainedDecodingProbeResult:
    distinct_models = set(resolved_models)
    common_model = resolved_models[0] if len(distinct_models) == 1 else None
    identity = (
        RouteIdentityState.VERIFIED if common_model is not None else RouteIdentityState.UNVERIFIED
    )
    counts = [t.completion_tokens for t in trials]
    all_usage = all(count is not None for count in counts)
    max_observed = max(c for c in counts if c is not None) if all_usage else None
    mean_tokens = (
        Decimal(sum(c for c in counts if c is not None)) / Decimal(len(counts))
        if all_usage
        else None
    )

    def build(state: ConstrainedDecodingProbeState, detail: str) -> ConstrainedDecodingProbeResult:
        return _result(
            state,
            detail,
            probed_at,
            trials=trials,
            resolved_model=common_model,
            identity=identity,
            max_tokens_observed=max_observed,
            mean_tokens=mean_tokens,
        )

    if any(t.finish_reason == "length" for t in trials):
        return build(_State.TRUNCATED, "a trial hit the token ceiling before finishing")
    if any(t.finish_reason == "content_filter" for t in trials):
        return build(_State.CONTENT_FILTERED, "gateway filtered a probe response")
    if any(not t.label_in_enum or t.finish_reason != "stop" for t in trials):
        return build(
            _State.NOT_ENFORCED,
            "route did not hold the answer to the trial enum (constraint absent or dropped)",
        )
    if not all_usage:
        return build(
            _State.USAGE_MISSING,
            "constraint observed but usage is absent: output tokens are unknown, not zero",
        )
    if max_observed is not None and max_observed > _MAX_TOKENS:
        return build(
            _State.MALFORMED_RESPONSE,
            "reported completion tokens exceed the request's max_tokens ceiling",
        )
    return build(_State.CONSTRAINED_OBSERVED, "route held every trial to its enum")
