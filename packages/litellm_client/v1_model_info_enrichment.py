"""LiteLLM `/model/info` metadata enrichment (FTF-0205, Section 2-3/38/65/77/81).

`packages/litellm_client/v1_models_discovery.py` (FTF-0204, already
CERTIFIED) already calls `GET /model/info` internally, but only to decide a
boolean `metadata_status` (`enriched`/`limited`) for its own already-
authorized model list -- it deliberately never surfaces the raw `model_info`
content itself. This task's own acceptance clause ("Enrich only visible
aliases with allowlisted fields, retaining limited metadata when
denied/absent") is the separate, later, dependency-gated step that actually
returns real per-model capability/limit/pricing fields -- not a widening of
FTF-0204's own already-CERTIFIED result shape.

Section 3.2's "no authorization union" is enforced the same way FTF-0204's
own `_fetch_enrichment_ids` already enforces it, but stricter: this module
never invents its own model-ID universe at all. The caller (a later task,
FTF-0207 "Alias reconciliation") supplies `visible_model_ids` -- the
already-authorized names a real `GET /v1/models` call produced -- and this
module's *only* job is to attach allowlisted `/model/info` fields onto that
exact set. An alias present only in `/model/info` is simply never iterated
into `entries`; it is not stored, not exposed, not even reported as
"denied" -- it does not exist from this module's point of view.

Section 65.4's "Metadata is not sent to the browser until secret-bearing/
provider-private fields have been allowlist-filtered" is enforced by
`_allowlisted_fields` being an allow list, not a denylist: a real LiteLLM
`/model/info` response's per-entry `litellm_params` sub-object (which
carries `api_key`/`api_base`) is never even read by `_parse_model_info`,
and a hypothetical misconfigured gateway that duplicates a credential-
shaped field *inside* `model_info` itself is still dropped, since only the
named fields in `_ALLOWLISTED_MODEL_INFO_FIELDS` ever pass through, and
only when their value is a plain JSON scalar (never a nested dict/list that
could smuggle arbitrary content under an allowlisted key).

This task's own negative_acceptance ("Raw credentials, admin-only provider
routes and metadata-only aliases are neither stored nor exposed") is
therefore satisfied by construction on all three counts: raw credentials by
the allowlist above; the admin-only `/model/info` route itself by never
letting a failure calling it (403/404/timeout/etc.) invalidate or replace
the caller's already-authorized `visible_model_ids` (Section 3.1: "403/404
on enrichment does not invalidate a successful standard model list") --
every visible id still gets a real entry, downgraded to `limited`, never
dropped or replaced by a gateway-shaped error; and metadata-only aliases by
the left-join construction above.

No independent schema change (this task's own `database_changes` clause):
an enrichment result is computed fresh on every call and returned directly
to its caller, never persisted.

Every reported `detail` string is content-free (Section observability: "no
secrets/raw prompts in task reports") -- never the resolved credential, the
raw response body, or a raw exception message.
"""

from __future__ import annotations

import json
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType

from packages.litellm_client.secret_manager_integration import (
    OpenBaoConfig,
    SecretAccessDeniedError,
    SecretBackendError,
    SecretExpiredError,
    SecretNotFoundError,
    SecretRefFormatError,
    resolve_secret_ref,
)
from packages.litellm_client.v1_models_discovery import ModelMetadataStatus
from packages.security.outbound_guard import OutboundGuardError, guarded_fetch
from packages.security.url_policy import ConnectionPolicy, OutboundPolicyError

__all__ = [
    "EnrichmentState",
    "ModelInfoEnrichmentResult",
    "ModelMetadataEntry",
    "enrich_visible_models",
]

_MODEL_INFO_PATH = "/model/info"
_DEFAULT_TIMEOUT_SECONDS = 10.0

# Same restriction `v1_models_discovery.py`/`connection_validation.py`
# already document: only the OpenBao-backed reference shape FTF-0202
# actually resolves today.
_SUPPORTED_SECRET_REF_PREFIX = "openbao://"

# Capability/limit/pricing fields only. This is an allow list, not a
# denylist, so a real `model_info` sub-object's own `litellm_params`
# sibling (which carries `api_key`/`api_base`) is unreachable regardless of
# its shape, and a field name this set does not name is dropped even if the
# gateway response places it directly under `model_info`.
_ALLOWLISTED_MODEL_INFO_FIELDS: frozenset[str] = frozenset(
    {
        "mode",
        "max_tokens",
        "max_input_tokens",
        "max_output_tokens",
        "input_cost_per_token",
        "output_cost_per_token",
        "supports_function_calling",
        "supports_parallel_function_calling",
        "supports_vision",
        "supports_response_schema",
        "supports_system_messages",
        "supports_prompt_caching",
    }
)

# JSON scalar types only -- an allowlisted key whose value is itself a dict
# or list is dropped rather than passed through, so a field name alone can
# never smuggle an arbitrary nested payload past the filter.
_ALLOWLISTED_VALUE_TYPES: tuple[type, ...] = (str, int, float, bool, type(None))


class EnrichmentState(str, Enum):
    """The `GET /model/info` call outcome. Mirrors the connectivity/secret
    taxonomy `v1_models_discovery.DiscoveryState`/`connection_validation.
    InferenceCheckState` already establish (same repeated-per-module
    pattern: each module owns its own private classifier for its own
    result enum, since the enums themselves are not interchangeable)."""

    COMPLETED = "completed"
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
class ModelMetadataEntry:
    id: str
    metadata_status: ModelMetadataStatus
    fields: MappingProxyType[str, object]


@dataclass(frozen=True)
class ModelInfoEnrichmentResult:
    state: EnrichmentState
    entries: tuple[ModelMetadataEntry, ...]
    detail: str
    checked_at: datetime


def _classify_guard_failure(exc: OutboundGuardError) -> EnrichmentState:
    """Same convention `v1_models_discovery.py::_classify_guard_failure`
    already established: `guarded_fetch` wraps every connection-level
    failure as `OutboundGuardError(...) from <original exception>`, and the
    original cause (never the wrapper's own string message) is classified."""
    cause = exc.__cause__
    if isinstance(cause, socket.gaierror):
        return EnrichmentState.DNS_FAILED
    if isinstance(cause, ssl.SSLCertVerificationError):
        return EnrichmentState.CERTIFICATE_ERROR
    if isinstance(cause, ssl.SSLError):
        return EnrichmentState.CERTIFICATE_ERROR
    if isinstance(cause, TimeoutError):
        return EnrichmentState.TIMEOUT
    if isinstance(cause, ConnectionRefusedError):
        return EnrichmentState.CONNECTION_REFUSED
    return EnrichmentState.UNREACHABLE


def _resolve_inference_key(
    secret_ref: str, *, secret_config: OpenBaoConfig | None, now: datetime
) -> tuple[str, None, str] | tuple[None, EnrichmentState, str]:
    if not secret_ref.startswith(_SUPPORTED_SECRET_REF_PREFIX):
        return (
            None,
            EnrichmentState.SECRET_BACKEND_UNSUPPORTED,
            "no secret backend is configured for this reference's scheme",
        )
    if secret_config is None:
        return (
            None,
            EnrichmentState.SECRET_BACKEND_ERROR,
            "secret backend is not configured in this environment",
        )
    try:
        key = resolve_secret_ref(secret_ref, config=secret_config, now=now)
    except SecretNotFoundError:
        return None, EnrichmentState.SECRET_NOT_FOUND, "secret reference has no live data"
    except SecretAccessDeniedError:
        return (
            None,
            EnrichmentState.SECRET_ACCESS_DENIED,
            "secret backend denied access to this reference",
        )
    except SecretExpiredError:
        return None, EnrichmentState.SECRET_EXPIRED, "secret reference has expired"
    except SecretRefFormatError:
        return (
            None,
            EnrichmentState.SECRET_BACKEND_UNSUPPORTED,
            "secret reference is not a supported backend format",
        )
    except SecretBackendError:
        return None, EnrichmentState.SECRET_BACKEND_ERROR, "secret backend request failed"
    return key, None, ""


def _allowlisted_fields(raw_model_info: object) -> MappingProxyType[str, object]:
    if not isinstance(raw_model_info, dict):
        return MappingProxyType({})
    fields = {
        key: value
        for key, value in raw_model_info.items()
        if key in _ALLOWLISTED_MODEL_INFO_FIELDS and isinstance(value, _ALLOWLISTED_VALUE_TYPES)
    }
    return MappingProxyType(fields)


def _parse_model_info(body: bytes) -> dict[str, object] | None:
    """Maps alias (`model_name`) -> its raw `model_info` sub-value, same
    OpenAI/LiteLLM-compatible `{"data": [...]}` envelope `v1_models_
    discovery.py::_fetch_enrichment_ids` already parses. Deliberately never
    reads the sibling `litellm_params` key at all -- the allowlist in
    `_allowlisted_fields` is applied later, per model, not here; this only
    locates the raw sub-value so it can be filtered at the last point
    before it is ever returned to a caller."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    by_alias: dict[str, object] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        alias = item.get("model_name")
        if isinstance(alias, str):
            by_alias[alias] = item.get("model_info")
    return by_alias


def enrich_visible_models(
    *,
    base_url: str,
    secret_ref: str,
    secret_config: OpenBaoConfig | None,
    policy: ConnectionPolicy,
    visible_model_ids: tuple[str, ...],
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> ModelInfoEnrichmentResult:
    """Enriches exactly `visible_model_ids` (the caller's own already-
    authorized `GET /v1/models` result) with allowlisted `GET /model/info`
    fields, on a strict left-join basis (Section 3.2 -- never a union: an
    id not in `visible_model_ids` is never iterated into `entries`, however
    prominently `/model/info` may advertise it). Never raises for an
    ordinary connectivity/authorization/secret/parse failure -- every such
    case still returns one entry per `visible_model_ids` member, each
    `metadata_status=limited` with empty `fields` (Section 3.1: "403/404 on
    enrichment does not invalidate a successful standard model list" --
    applied here as "never drops or replaces an already-authorized id")."""
    checked_at = now if now is not None else datetime.now(UTC)

    ids: list[str] = []
    seen: set[str] = set()
    for model_id in visible_model_ids:
        if model_id not in seen:
            seen.add(model_id)
            ids.append(model_id)

    def _all_limited(state: EnrichmentState, detail: str) -> ModelInfoEnrichmentResult:
        entries = tuple(
            ModelMetadataEntry(
                id=model_id,
                metadata_status=ModelMetadataStatus.LIMITED,
                fields=MappingProxyType({}),
            )
            for model_id in ids
        )
        return ModelInfoEnrichmentResult(state, entries, detail, checked_at)

    key, failure_state, detail = _resolve_inference_key(
        secret_ref, secret_config=secret_config, now=checked_at
    )
    if failure_state is not None:
        return _all_limited(failure_state, detail)

    headers = {"Authorization": f"Bearer {key}"}
    try:
        response = guarded_fetch(
            f"{base_url.rstrip('/')}{_MODEL_INFO_PATH}", policy, headers=headers, timeout=timeout
        )
    except OutboundGuardError as exc:
        return _all_limited(_classify_guard_failure(exc), "gateway connection failed")
    except OutboundPolicyError:
        return _all_limited(
            EnrichmentState.DESTINATION_DENIED, "gateway destination is not authorized by policy"
        )

    if response.status in (401, 403):
        return _all_limited(
            EnrichmentState.INVALID_TOKEN, "gateway rejected the inference credential"
        )
    if response.status != 200:
        return _all_limited(
            EnrichmentState.UNREACHABLE, f"gateway returned unexpected status {response.status}"
        )

    by_alias = _parse_model_info(response.body)
    if by_alias is None:
        return _all_limited(
            EnrichmentState.MALFORMED_RESPONSE, "gateway response was not a valid model-info list"
        )

    entries = []
    for model_id in ids:
        raw = by_alias.get(model_id) if model_id in by_alias else None
        fields = _allowlisted_fields(raw)
        status = ModelMetadataStatus.ENRICHED if fields else ModelMetadataStatus.LIMITED
        entries.append(ModelMetadataEntry(id=model_id, metadata_status=status, fields=fields))

    return ModelInfoEnrichmentResult(
        EnrichmentState.COMPLETED,
        tuple(entries),
        "gateway reachable and authorized",
        checked_at,
    )
