"""LiteLLM `/v1/models` discovery (FTF-0204, Section 2-3/38/65/77/81).

`packages/litellm_client/connection_validation.py` (FTF-0203) already
dials `GET /v1/models` to prove the inference credential is accepted, but
only reports a *count* -- this task's own acceptance clause needs the
actual credential-visible model ID list itself, "through tested gateway
route and record timestamp/count/hash," so a separate call records the
real inventory rather than widening FTF-0203's own already-CERTIFIED
result shape.

Section 3.2's "no authorization union" rule is the load-bearing part of
this module's own negative_acceptance ("metadata-only... model is not
callable"): `GET /v1/models` is the *only* source of the returned model
ID list (`selectable_names`); `GET /model/info` enrichment is strictly a
**left join onto that already-authorized list** ("a name found only in
management metadata cannot be selected") -- `_fetch_enrichment_ids`'s
result is only ever consulted to set an already-authorized entry's
`metadata_status`, never to add a new entry.

Section 3.3's "stale is not authority" rule ("On a successful
authoritative list response, removed names immediately become
unselectable") is satisfied by construction, not by extra bookkeeping:
`discover_models` never reads or merges a previous result -- every call
is a fresh `GET /v1/models` round trip, so a name absent from the current
response is simply absent from the current `ModelDiscoveryResult`, with
nothing here to retain it. The same section's "On 401/403... disable new
calls" (this task's own negative_acceptance: "auth errors fail closed
instead of retaining permission") is why an inference-credential
rejection returns an empty `models` tuple and `model_count=None`
(unknown, never a stale or fabricated count) rather than any previously
observed list -- there is no cache here for a caller to fall back to.

No independent schema change (this task's own `database_changes`
clause): a discovery result is computed fresh on every call and returned
directly in the API response, same convention `connection_validation.py`
already established -- never persisted onto `LiteLLMConnection`.

Every reported detail string is content-free (Section observability: "no
secrets/raw prompts in task reports") -- never the resolved credential,
the raw response body, or a raw exception message.
"""

from __future__ import annotations

import hashlib
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
    "DiscoveredModel",
    "DiscoveryState",
    "ModelDiscoveryResult",
    "ModelMetadataStatus",
    "discover_models",
]

_MODELS_PATH = "/v1/models"
_MODEL_INFO_PATH = "/model/info"
_DEFAULT_TIMEOUT_SECONDS = 10.0

# Same restriction `connection_validation.py` already documents: only the
# OpenBao-backed reference shape FTF-0202 actually resolves today.
_SUPPORTED_SECRET_REF_PREFIX = "openbao://"


class DiscoveryState(str, Enum):
    """The `GET /v1/models` authoritative-list outcome (Section 3.1/3.3)."""

    DISCOVERED = "discovered"
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


class ModelMetadataStatus(str, Enum):
    """Section 3.2: "An authorized alias missing rich metadata is
    retained with `metadata_status=limited`" -- never the reverse; a name
    is never promoted into the result *by* having metadata."""

    ENRICHED = "enriched"
    LIMITED = "limited"


@dataclass(frozen=True)
class DiscoveredModel:
    id: str
    metadata_status: ModelMetadataStatus


@dataclass(frozen=True)
class ModelDiscoveryResult:
    state: DiscoveryState
    models: tuple[DiscoveredModel, ...]
    model_count: int | None
    content_hash: str | None
    detail: str
    checked_at: datetime


def _classify_guard_failure(exc: OutboundGuardError) -> DiscoveryState:
    """Same convention `connection_validation.py::_classify_guard_failure`
    already established: `guarded_fetch` wraps every connection-level
    failure as `OutboundGuardError(...) from <original exception>`, and
    the original cause (never the wrapper's own string message) is what
    is actually classified."""
    cause = exc.__cause__
    if isinstance(cause, socket.gaierror):
        return DiscoveryState.DNS_FAILED
    if isinstance(cause, ssl.SSLCertVerificationError):
        return DiscoveryState.CERTIFICATE_ERROR
    if isinstance(cause, ssl.SSLError):
        return DiscoveryState.CERTIFICATE_ERROR
    if isinstance(cause, TimeoutError):
        return DiscoveryState.TIMEOUT
    if isinstance(cause, ConnectionRefusedError):
        return DiscoveryState.CONNECTION_REFUSED
    return DiscoveryState.UNREACHABLE


def _resolve_inference_key(
    secret_ref: str, *, secret_config: OpenBaoConfig | None, now: datetime
) -> tuple[str, None, str] | tuple[None, DiscoveryState, str]:
    if not secret_ref.startswith(_SUPPORTED_SECRET_REF_PREFIX):
        return (
            None,
            DiscoveryState.SECRET_BACKEND_UNSUPPORTED,
            "no secret backend is configured for this reference's scheme",
        )
    if secret_config is None:
        return (
            None,
            DiscoveryState.SECRET_BACKEND_ERROR,
            "secret backend is not configured in this environment",
        )
    try:
        key = resolve_secret_ref(secret_ref, config=secret_config, now=now)
    except SecretNotFoundError:
        return None, DiscoveryState.SECRET_NOT_FOUND, "secret reference has no live data"
    except SecretAccessDeniedError:
        return (
            None,
            DiscoveryState.SECRET_ACCESS_DENIED,
            "secret backend denied access to this reference",
        )
    except SecretExpiredError:
        return None, DiscoveryState.SECRET_EXPIRED, "secret reference has expired"
    except SecretRefFormatError:
        return (
            None,
            DiscoveryState.SECRET_BACKEND_UNSUPPORTED,
            "secret reference is not a supported backend format",
        )
    except SecretBackendError:
        return None, DiscoveryState.SECRET_BACKEND_ERROR, "secret backend request failed"
    return key, None, ""


def _parse_models(body: bytes) -> list[str] | None:
    """OpenAI-compatible `{"data": [{"id": "...", ...}, ...]}` shape,
    same envelope `connection_validation.py::_parse_model_count` already
    parses. A malformed *entry* is dropped, not fatal; a response that is
    not even this envelope shape returns `None` (`malformed_response`),
    never an invented list. Order is preserved and duplicates collapsed
    to their first occurrence."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    seen: set[str] = set()
    ids: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if isinstance(model_id, str) and model_id not in seen:
            seen.add(model_id)
            ids.append(model_id)
    return ids


def _fetch_enrichment_ids(
    base_url: str, headers: dict[str, str], policy: ConnectionPolicy, timeout: float
) -> frozenset[str]:
    """Best-effort `GET /model/info` enrichment. Section 3.1: "403/404 on
    enrichment does not invalidate a successful standard model list" --
    every failure here (guard/policy error, non-200 status, malformed
    body) returns an empty set rather than raising, so every authorized
    name from `_parse_models` simply stays `metadata_status=limited`
    (Section 3.2) instead of failing the whole discovery."""
    try:
        response = guarded_fetch(
            f"{base_url.rstrip('/')}{_MODEL_INFO_PATH}", policy, headers=headers, timeout=timeout
        )
    except (OutboundGuardError, OutboundPolicyError):
        return frozenset()
    if response.status != 200:
        return frozenset()
    try:
        payload = json.loads(response.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return frozenset()
    if not isinstance(payload, dict):
        return frozenset()
    data = payload.get("data")
    if not isinstance(data, list):
        return frozenset()
    ids: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        for key in ("model_name", "id"):
            value = item.get(key)
            if isinstance(value, str):
                ids.add(value)
    return frozenset(ids)


def _hash_models(model_ids: list[str]) -> str:
    """A stable, content-free (no secrets) fingerprint of the current
    authorized set -- this task's own acceptance clause "record
    timestamp/count/hash." Sorted so the hash reflects membership, not
    gateway-response ordering; a legitimate zero-model credential still
    gets a real, deterministic hash (`sha256("")`), never `None`."""
    canonical = "\n".join(sorted(set(model_ids)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def discover_models(
    *,
    base_url: str,
    secret_ref: str,
    secret_config: OpenBaoConfig | None,
    policy: ConnectionPolicy,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> ModelDiscoveryResult:
    """Lists the connection's real credential-visible model IDs through
    `GET /v1/models` (Section 3.1), enriches each with `GET /model/info`
    on a strict left-join basis (Section 3.2 -- never a union), and
    records a timestamp/count/hash triple. Never raises for an ordinary
    connectivity/authorization/secret failure -- every such case is a
    real, distinct, sanitized `DiscoveryState` with an empty model list,
    never a fabricated or retained-from-elsewhere inventory (this task's
    own negative_acceptance)."""
    checked_at = now if now is not None else datetime.now(UTC)

    key, failure_state, detail = _resolve_inference_key(
        secret_ref, secret_config=secret_config, now=checked_at
    )
    if failure_state is not None:
        return ModelDiscoveryResult(failure_state, (), None, None, detail, checked_at)

    headers = {"Authorization": f"Bearer {key}"}
    try:
        response = guarded_fetch(
            f"{base_url.rstrip('/')}{_MODELS_PATH}", policy, headers=headers, timeout=timeout
        )
    except OutboundGuardError as exc:
        state = _classify_guard_failure(exc)
        return ModelDiscoveryResult(state, (), None, None, "gateway connection failed", checked_at)
    except OutboundPolicyError:
        return ModelDiscoveryResult(
            DiscoveryState.DESTINATION_DENIED,
            (),
            None,
            None,
            "gateway destination is not authorized by policy",
            checked_at,
        )

    if response.status in (401, 403):
        return ModelDiscoveryResult(
            DiscoveryState.INVALID_TOKEN,
            (),
            None,
            None,
            "gateway rejected the inference credential",
            checked_at,
        )
    if response.status != 200:
        return ModelDiscoveryResult(
            DiscoveryState.UNREACHABLE,
            (),
            None,
            None,
            f"gateway returned unexpected status {response.status}",
            checked_at,
        )

    model_ids = _parse_models(response.body)
    if model_ids is None:
        return ModelDiscoveryResult(
            DiscoveryState.MALFORMED_RESPONSE,
            (),
            None,
            None,
            "gateway response was not a valid model list",
            checked_at,
        )

    enrichment_ids = _fetch_enrichment_ids(base_url, headers, policy, timeout)
    models = tuple(
        DiscoveredModel(
            id=model_id,
            metadata_status=(
                ModelMetadataStatus.ENRICHED
                if model_id in enrichment_ids
                else ModelMetadataStatus.LIMITED
            ),
        )
        for model_id in model_ids
    )
    return ModelDiscoveryResult(
        DiscoveryState.DISCOVERED,
        models,
        len(models),
        _hash_models(model_ids),
        "gateway reachable and authorized",
        checked_at,
    )
