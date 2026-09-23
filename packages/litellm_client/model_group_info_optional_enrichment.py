"""LiteLLM `/model_group/info` optional metadata enrichment (FTF-0206,
Section 2-3/38/65/77/81).

`packages/litellm_client/v1_model_info_enrichment.py` (FTF-0205, already
CERTIFIED) enriches the caller's already-authorized model list from
`GET /model/info`, whose real LiteLLM response nests each entry's
capability/limit/pricing fields under a `model_info` sub-object keyed by
`model_name`. `GET /model_group/info` is a *separate* real LiteLLM route
with its own real response shape -- each entry's fields sit directly on
the top-level object, keyed by `model_group` (never `model_name`, never
nested under `model_info`), and it additionally reports true group-level
throughput limits (`tpm`/`rpm`) that `/model/info` does not carry. This
task's own `files` list (module + test only, no route file) is the same
disclosed producer/later-wiring scope decision FTF-0202/FTF-0205 already
established -- `FTF-0207` ("Alias reconciliation") is the later task that
wires enrichment results into a live route.

Section 3.1 names `/model_group/info` explicitly as one of the two
optional-enrichment routes ("Metadata from `/v1/model/info` or
`/model_group/info` is optional enrichment... 403/404 on enrichment does
not invalidate a successful standard model list"), so this module reuses
FTF-0205's own left-join/never-drop/allowlist-filter design wholesale.
This task's own acceptance clause is stricter on one specific point,
though: "Optional enrichment runs only with explicit supported capability
and safe permission." Unlike `/model/info` (attempted unconditionally by
FTF-0205, degrading to `limited` on any failure), `/model_group/info` is
not guaranteed to exist on every LiteLLM deployment this Factory may
connect to -- so this module never even attempts the network call unless
its caller passes `capability_supported=True`, an explicit, no-default,
keyword-only declaration that this specific connection is known to
support the route. A caller with no such declaration (the common case
until a later task records it on the connection) gets `limited` metadata
for every visible id without a single network round trip -- "runs only
with" is enforced by construction, not by a best-effort attempt that
happens to usually succeed.

"...and safe permission" is the same least-privilege-inference-key rule
Section 3.1 already states for `/model/info` and `/v1/models`
("Discovery uses a least-privilege inference key, not a master/
management key"): this module resolves and sends exactly one credential,
the caller's own already-configured inference secret ref, for every
call. There is no code path here that escalates to a management/admin
credential on any failure -- this task's own negative_acceptance
("403/not-supported leaves limited metadata without failing basic
discovery or gaining management rights") is therefore satisfied by
construction: a 403 (permission denied under the safe inference key) and
a 404 (route not supported by this gateway build) both degrade every
visible id to `limited` and return, with no retry under any other
credential and no effect on the caller's own already-authorized
discovery result.

Section 3.2's "no authorization union" is enforced exactly as FTF-0205
enforces it: the caller supplies `visible_model_ids` (the already-
authorized names a real `GET /v1/models` call produced), and this
module's only job is to attach allowlisted `/model_group/info` fields
onto that exact set. A group name present only in `/model_group/info` is
never iterated into `entries`.

Section 65.4's "Metadata is not sent to the browser until secret-bearing/
provider-private fields have been allowlist-filtered" is enforced by
`_allowlisted_fields` being an allow list, not a denylist, over the raw
entry's own top-level keys: `/model_group/info`'s real `providers` field
(a list of upstream provider names) and any `litellm_params`/deployment-
identity sub-object are dropped regardless of name, since only named
scalar fields in `_ALLOWLISTED_MODEL_GROUP_FIELDS` ever pass the filter,
and a non-scalar value under an allowlisted key is dropped too.

No independent schema change (this task's own `database_changes`
clause): a result is computed fresh on every call and returned directly
to its caller, never persisted.

Every reported `detail` string is content-free (Section observability:
"no secrets/raw prompts in task reports") -- never the resolved
credential, the raw response body, or a raw exception message.
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
    "GroupInfoState",
    "ModelGroupEnrichmentResult",
    "ModelGroupMetadataEntry",
    "enrich_visible_model_groups",
]

_MODEL_GROUP_INFO_PATH = "/model_group/info"
_DEFAULT_TIMEOUT_SECONDS = 10.0

# Same restriction `v1_model_info_enrichment.py`/`v1_models_discovery.py`
# already document: only the OpenBao-backed reference shape FTF-0202
# actually resolves today.
_SUPPORTED_SECRET_REF_PREFIX = "openbao://"

# Capability/limit/pricing/throughput fields only -- an allow list, not a
# denylist, so a real `/model_group/info` entry's own `providers` (a
# list) and any deployment/credential-shaped sibling are unreachable
# regardless of name, and a field name this set does not name is dropped
# even if the gateway response places it at the top level.
_ALLOWLISTED_MODEL_GROUP_FIELDS: frozenset[str] = frozenset(
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
        "tpm",
        "rpm",
    }
)

# JSON scalar types only -- an allowlisted key whose value is itself a
# dict or list is dropped rather than passed through, so a field name
# alone can never smuggle an arbitrary nested payload past the filter.
_ALLOWLISTED_VALUE_TYPES: tuple[type, ...] = (str, int, float, bool, type(None))


class GroupInfoState(str, Enum):
    """The `GET /model_group/info` call outcome. Mirrors
    `v1_model_info_enrichment.EnrichmentState`'s taxonomy, plus two states
    that enum has no equivalent for: `CAPABILITY_NOT_DECLARED` (the
    network call was never attempted at all, per this task's own "runs
    only with explicit supported capability" acceptance clause) and
    `NOT_SUPPORTED` (a real 404 -- this gateway build does not expose the
    route, distinct from a 401/403 permission denial under a valid
    connection)."""

    COMPLETED = "completed"
    CAPABILITY_NOT_DECLARED = "capability_not_declared"
    NOT_SUPPORTED = "not_supported"
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
class ModelGroupMetadataEntry:
    id: str
    metadata_status: ModelMetadataStatus
    fields: MappingProxyType[str, object]


@dataclass(frozen=True)
class ModelGroupEnrichmentResult:
    state: GroupInfoState
    entries: tuple[ModelGroupMetadataEntry, ...]
    detail: str
    checked_at: datetime


def _classify_guard_failure(exc: OutboundGuardError) -> GroupInfoState:
    """Same convention `v1_model_info_enrichment.py::_classify_guard_failure`
    already established: `guarded_fetch` wraps every connection-level
    failure as `OutboundGuardError(...) from <original exception>`, and the
    original cause (never the wrapper's own string message) is classified."""
    cause = exc.__cause__
    if isinstance(cause, socket.gaierror):
        return GroupInfoState.DNS_FAILED
    if isinstance(cause, ssl.SSLCertVerificationError):
        return GroupInfoState.CERTIFICATE_ERROR
    if isinstance(cause, ssl.SSLError):
        return GroupInfoState.CERTIFICATE_ERROR
    if isinstance(cause, TimeoutError):
        return GroupInfoState.TIMEOUT
    if isinstance(cause, ConnectionRefusedError):
        return GroupInfoState.CONNECTION_REFUSED
    return GroupInfoState.UNREACHABLE


def _resolve_inference_key(
    secret_ref: str, *, secret_config: OpenBaoConfig | None, now: datetime
) -> tuple[str, None, str] | tuple[None, GroupInfoState, str]:
    if not secret_ref.startswith(_SUPPORTED_SECRET_REF_PREFIX):
        return (
            None,
            GroupInfoState.SECRET_BACKEND_UNSUPPORTED,
            "no secret backend is configured for this reference's scheme",
        )
    if secret_config is None:
        return (
            None,
            GroupInfoState.SECRET_BACKEND_ERROR,
            "secret backend is not configured in this environment",
        )
    try:
        key = resolve_secret_ref(secret_ref, config=secret_config, now=now)
    except SecretNotFoundError:
        return None, GroupInfoState.SECRET_NOT_FOUND, "secret reference has no live data"
    except SecretAccessDeniedError:
        return (
            None,
            GroupInfoState.SECRET_ACCESS_DENIED,
            "secret backend denied access to this reference",
        )
    except SecretExpiredError:
        return None, GroupInfoState.SECRET_EXPIRED, "secret reference has expired"
    except SecretRefFormatError:
        return (
            None,
            GroupInfoState.SECRET_BACKEND_UNSUPPORTED,
            "secret reference is not a supported backend format",
        )
    except SecretBackendError:
        return None, GroupInfoState.SECRET_BACKEND_ERROR, "secret backend request failed"
    return key, None, ""


def _allowlisted_fields(raw_entry: object) -> MappingProxyType[str, object]:
    if not isinstance(raw_entry, dict):
        return MappingProxyType({})
    fields = {
        key: value
        for key, value in raw_entry.items()
        if key in _ALLOWLISTED_MODEL_GROUP_FIELDS and isinstance(value, _ALLOWLISTED_VALUE_TYPES)
    }
    return MappingProxyType(fields)


def _parse_model_group_info(body: bytes) -> dict[str, object] | None:
    """Maps group alias (`model_group`) -> its own raw top-level entry
    dict. Unlike `/model/info`'s `model_name`/`model_info` split, a real
    `/model_group/info` response places every field directly on the
    entry alongside `model_group` itself -- so the whole entry is kept
    here and the allowlist is applied later, per group, in
    `_allowlisted_fields` (which also drops the `model_group` key itself,
    since it is not in `_ALLOWLISTED_MODEL_GROUP_FIELDS`)."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    by_group: dict[str, object] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        alias = item.get("model_group")
        if isinstance(alias, str):
            by_group[alias] = item
    return by_group


def enrich_visible_model_groups(
    *,
    base_url: str,
    secret_ref: str,
    secret_config: OpenBaoConfig | None,
    policy: ConnectionPolicy,
    visible_model_ids: tuple[str, ...],
    capability_supported: bool,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> ModelGroupEnrichmentResult:
    """Enriches exactly `visible_model_ids` (the caller's own already-
    authorized `GET /v1/models` result) with allowlisted
    `GET /model_group/info` fields, on a strict left-join basis
    (Section 3.2 -- never a union). `capability_supported` must be
    explicitly passed by the caller (this task's own acceptance clause,
    "runs only with explicit supported capability"): when `False`, the
    network call is never attempted and every visible id is returned
    `limited` with state `capability_not_declared`. Never raises for an
    ordinary connectivity/authorization/secret/parse failure -- every
    such case still returns one entry per `visible_model_ids` member,
    each `metadata_status=limited` with empty `fields` (Section 3.1:
    "403/404 on enrichment does not invalidate a successful standard
    model list" -- applied here as "never drops or replaces an already-
    authorized id"). Uses exactly the caller's own inference credential
    for every call, never a management credential, on any outcome
    (this task's own negative_acceptance: "...without... gaining
    management rights")."""
    checked_at = now if now is not None else datetime.now(UTC)

    ids: list[str] = []
    seen: set[str] = set()
    for model_id in visible_model_ids:
        if model_id not in seen:
            seen.add(model_id)
            ids.append(model_id)

    def _all_limited(state: GroupInfoState, detail: str) -> ModelGroupEnrichmentResult:
        entries = tuple(
            ModelGroupMetadataEntry(
                id=model_id,
                metadata_status=ModelMetadataStatus.LIMITED,
                fields=MappingProxyType({}),
            )
            for model_id in ids
        )
        return ModelGroupEnrichmentResult(state, entries, detail, checked_at)

    if not capability_supported:
        return _all_limited(
            GroupInfoState.CAPABILITY_NOT_DECLARED,
            "model_group/info capability is not declared supported for this connection",
        )

    key, failure_state, detail = _resolve_inference_key(
        secret_ref, secret_config=secret_config, now=checked_at
    )
    if failure_state is not None:
        return _all_limited(failure_state, detail)

    headers = {"Authorization": f"Bearer {key}"}
    try:
        response = guarded_fetch(
            f"{base_url.rstrip('/')}{_MODEL_GROUP_INFO_PATH}",
            policy,
            headers=headers,
            timeout=timeout,
        )
    except OutboundGuardError as exc:
        return _all_limited(_classify_guard_failure(exc), "gateway connection failed")
    except OutboundPolicyError:
        return _all_limited(
            GroupInfoState.DESTINATION_DENIED, "gateway destination is not authorized by policy"
        )

    if response.status in (401, 403):
        return _all_limited(
            GroupInfoState.INVALID_TOKEN, "gateway rejected the inference credential"
        )
    if response.status == 404:
        return _all_limited(
            GroupInfoState.NOT_SUPPORTED, "gateway does not support model_group/info"
        )
    if response.status != 200:
        return _all_limited(
            GroupInfoState.UNREACHABLE, f"gateway returned unexpected status {response.status}"
        )

    by_group = _parse_model_group_info(response.body)
    if by_group is None:
        return _all_limited(
            GroupInfoState.MALFORMED_RESPONSE,
            "gateway response was not a valid model-group-info list",
        )

    entries = []
    for model_id in ids:
        raw = by_group.get(model_id) if model_id in by_group else None
        fields = _allowlisted_fields(raw)
        status = ModelMetadataStatus.ENRICHED if fields else ModelMetadataStatus.LIMITED
        entries.append(ModelGroupMetadataEntry(id=model_id, metadata_status=status, fields=fields))

    return ModelGroupEnrichmentResult(
        GroupInfoState.COMPLETED,
        tuple(entries),
        "gateway reachable and authorized",
        checked_at,
    )
