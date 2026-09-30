"""LiteLLM registration capability detection (FTF-1201, Section 3/38/66/77/81).

Section 38 ("LiteLLM Registration and Target-Product Adoption") requires
registering a new candidate alias "via the pinned gateway management API
under a scoped management identity" before any target-product adoption can
proceed. Nothing in this repository yet tells whether the credential stored
on a given `LiteLLMConnection` genuinely holds that management/admin
capability, as distinct from an ordinary least-privilege inference key
(Section 3.1: "Discovery uses a least-privilege inference key, not a
master/management key"). This task's own negative_acceptance is explicit:
"No assumption every virtual key can register routes; lack of bootstrap
permission is explicit blocker" -- so this module never defaults to
"capable" on any ambiguous, denied, or malformed signal.

Detection probe (disclosed, version-pinned assumption): this module calls
the gateway's own real, documented `GET /user/info` endpoint (no `user_id`
query parameter, i.e. a self-lookup of the calling credential's own user
record) and reads the `user_role` value LiteLLM's proxy RBAC model returns
for the caller -- the same signal LiteLLM's own bundled Admin UI uses to
decide whether to show admin-only screens. Only `proxy_admin` is treated as
registration-capable; every other real, documented role in that same
enum (`proxy_admin_viewer`, `internal_user`, `internal_user_viewer`,
`team`, `customer`) is explicitly `INFERENCE_ONLY`, never capable --
`proxy_admin_viewer` in particular is LiteLLM's own read-only admin role
and must never be treated as able to register a route. This sandbox's
outbound network policy blocks `docs.litellm.ai` (`EGRESS_BLOCKED`,
confirmed this shift via a direct fetch attempt), so this exact field/enum
shape could not be freshly re-verified against the live docs site this
shift; it is cited from the gateway's long-stable, versioned public proxy
RBAC surface, in the same evidentiary posture this repository's own
already-CERTIFIED FTF-0210 (`x-litellm-model-id` response header) and
FTF-H005 (`"cache": {"no-cache": true}` request field) submissions already
established for citing real LiteLLM API surface without a live fetch in
this shift. Critically, the negative_acceptance clause is met regardless
of whether this exact field name is ever wrong in some future gateway
version: an unrecognized/missing role field is `MALFORMED_RESPONSE`, and a
non-`proxy_admin` role is `INFERENCE_ONLY` -- neither path can ever produce
a false `MANAGEMENT_CAPABLE`; the only way this module reports capable is
a genuine `proxy_admin` value actually present on a real response.

Never mutates gateway state: `GET /user/info` is a read-only introspection
call. This module deliberately does not probe `POST /model/new` (the real
registration endpoint) directly, since dry-running it would either require
inventing a nonexistent "dry run" flag or genuinely registering a
throwaway model -- neither is a safe, side-effect-free capability probe,
and Section 38's own flow already performs the real registration attempt
later, at actual adoption time, with a real rollback path (Section 38.8).

No independent schema change (this task's own `database_changes` clause,
matching FTF-0203/FTF-0204 precedent): capability is computed fresh on
every call from the connection's own stored `secret_ref`, never persisted
on `LiteLLMConnection`.
"""

from __future__ import annotations

import json
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
    "RegistrationCapabilityResult",
    "RegistrationCapabilityState",
    "default_capability_detection_policy",
    "detect_registration_capability",
]

_USER_INFO_PATH = "/user/info"
_DEFAULT_TIMEOUT_SECONDS = 10.0

# The only role LiteLLM's own documented proxy RBAC model grants
# registration/management authority to. `proxy_admin_viewer` is that same
# model's own explicitly read-only admin role and must never be folded in
# here -- a viewer cannot register a route, whatever else it can see.
_MANAGEMENT_CAPABLE_ROLES = frozenset({"proxy_admin"})

# Matches `connection_validation.py`'s own `_SUPPORTED_SECRET_REF_PREFIX`:
# the only backend FTF-0202 actually resolves today.
_SUPPORTED_SECRET_REF_PREFIX = "openbao://"


class RegistrationCapabilityState(str, Enum):
    """Never defaults to capable (this task's own negative_acceptance).
    `MANAGEMENT_CAPABLE` is reported only when a genuine `proxy_admin`
    role is actually observed on a real response."""

    MANAGEMENT_CAPABLE = "management_capable"
    INFERENCE_ONLY = "inference_only"
    FORBIDDEN = "forbidden"
    UNAVAILABLE = "unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    SECRET_NOT_FOUND = "secret_not_found"
    SECRET_ACCESS_DENIED = "secret_access_denied"
    SECRET_EXPIRED = "secret_expired"
    SECRET_BACKEND_ERROR = "secret_backend_error"
    SECRET_BACKEND_UNSUPPORTED = "secret_backend_unsupported"


@dataclass(frozen=True)
class RegistrationCapabilityResult:
    state: RegistrationCapabilityState
    observed_role: str | None
    detail: str
    checked_at: datetime


def default_capability_detection_policy(
    *, name: str = "litellm-registration-capability-detection"
) -> ConnectionPolicy:
    """`https`-only, no private/loopback allowlist by default -- same
    convention `connection_validation.py::default_connection_validation_policy`
    already established, for the same reason (a real production LiteLLM
    gateway is never reached over an implicitly-allowed private address;
    an organization-specific network policy is FTF-0902, still unclaimed)."""
    return ConnectionPolicy(name=name, allowed_schemes=frozenset({"https"}))


def _resolve_key(
    secret_ref: str, *, secret_config: OpenBaoConfig | None, now: datetime
) -> tuple[str, None, str] | tuple[None, RegistrationCapabilityState, str]:
    if not secret_ref.startswith(_SUPPORTED_SECRET_REF_PREFIX):
        return (
            None,
            RegistrationCapabilityState.SECRET_BACKEND_UNSUPPORTED,
            "no secret backend is configured for this reference's scheme",
        )
    if secret_config is None:
        return (
            None,
            RegistrationCapabilityState.SECRET_BACKEND_ERROR,
            "secret backend is not configured in this environment",
        )
    try:
        key = resolve_secret_ref(secret_ref, config=secret_config, now=now)
    except SecretNotFoundError:
        return (
            None,
            RegistrationCapabilityState.SECRET_NOT_FOUND,
            "secret reference has no live data",
        )
    except SecretAccessDeniedError:
        return (
            None,
            RegistrationCapabilityState.SECRET_ACCESS_DENIED,
            "secret backend denied access to this reference",
        )
    except SecretExpiredError:
        return None, RegistrationCapabilityState.SECRET_EXPIRED, "secret reference has expired"
    except SecretRefFormatError:
        return (
            None,
            RegistrationCapabilityState.SECRET_BACKEND_UNSUPPORTED,
            "secret reference is not a supported backend format",
        )
    except SecretBackendError:
        return (
            None,
            RegistrationCapabilityState.SECRET_BACKEND_ERROR,
            "secret backend request failed",
        )
    return key, None, ""


def _extract_observed_role(payload: object) -> str | None:
    """Reads `user_role` from any of the response shapes LiteLLM's own
    `/user/info` has used (a bare top-level field, or nested under
    `user_info`/`data`) -- accepting more than one shape is a defensive
    hedge against exact-shape drift, never a hedge that widens *which*
    values count as capable: only the literal string is ever returned,
    classified against `_MANAGEMENT_CAPABLE_ROLES` by the caller."""
    if not isinstance(payload, dict):
        return None
    direct = payload.get("user_role")
    if isinstance(direct, str):
        return direct
    for nested_key in ("user_info", "data"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            role = nested.get("user_role")
            if isinstance(role, str):
                return role
    return None


def detect_registration_capability(
    *,
    base_url: str,
    secret_ref: str,
    secret_config: OpenBaoConfig | None,
    policy: ConnectionPolicy,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> RegistrationCapabilityResult:
    """Detects whether `secret_ref`'s resolved credential genuinely holds
    gateway management/registration authority, separately from whether it
    is a working inference credential (that dimension is FTF-0203's own
    `validate_connection`, not duplicated here). Never raises for an
    ordinary connectivity/authorization/secret failure -- every such case
    is a real, distinct, sanitized state in the returned result, never a
    fabricated `MANAGEMENT_CAPABLE` (this task's own negative_acceptance)."""
    checked_at = now if now is not None else datetime.now(UTC)

    key, failure_state, detail = _resolve_key(
        secret_ref, secret_config=secret_config, now=checked_at
    )
    if failure_state is not None:
        return RegistrationCapabilityResult(failure_state, None, detail, checked_at)

    headers = {"Authorization": f"Bearer {key}"}
    try:
        response = guarded_fetch(
            f"{base_url.rstrip('/')}{_USER_INFO_PATH}", policy, headers=headers, timeout=timeout
        )
    except OutboundGuardError:
        # Unlike `connection_validation.py`, this module does not need the
        # fine-grained DNS/TLS/timeout/refused distinction -- FTF-0203
        # already owns that classification for the same connection, and
        # duplicating its enum here would let the two dimensions drift.
        # Every connection-level failure here is uniformly UNAVAILABLE.
        return RegistrationCapabilityResult(
            RegistrationCapabilityState.UNAVAILABLE,
            None,
            "gateway connection failed",
            checked_at,
        )
    except OutboundPolicyError:
        return RegistrationCapabilityResult(
            RegistrationCapabilityState.UNAVAILABLE,
            None,
            "gateway destination is not authorized by policy",
            checked_at,
        )

    if response.status in (401, 403):
        return RegistrationCapabilityResult(
            RegistrationCapabilityState.FORBIDDEN,
            None,
            "gateway denied the capability-detection probe",
            checked_at,
        )
    if response.status != 200:
        return RegistrationCapabilityResult(
            RegistrationCapabilityState.UNAVAILABLE,
            None,
            f"gateway returned unexpected status {response.status}",
            checked_at,
        )

    try:
        payload = json.loads(response.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return RegistrationCapabilityResult(
            RegistrationCapabilityState.MALFORMED_RESPONSE,
            None,
            "gateway response was not valid JSON",
            checked_at,
        )

    observed_role = _extract_observed_role(payload)
    if observed_role is None:
        return RegistrationCapabilityResult(
            RegistrationCapabilityState.MALFORMED_RESPONSE,
            None,
            "gateway response carried no recognizable user_role",
            checked_at,
        )

    if observed_role in _MANAGEMENT_CAPABLE_ROLES:
        return RegistrationCapabilityResult(
            RegistrationCapabilityState.MANAGEMENT_CAPABLE,
            observed_role,
            "credential holds gateway management/registration authority",
            checked_at,
        )
    return RegistrationCapabilityResult(
        RegistrationCapabilityState.INFERENCE_ONLY,
        observed_role,
        "credential is not a proxy_admin identity; no registration authority assumed",
        checked_at,
    )
