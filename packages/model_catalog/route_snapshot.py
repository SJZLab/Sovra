"""Pinned qualification route identity + drift detection (FTF-H005, Section
3.4-3.6/38/66.5/77/81).

`packages/litellm_client/route_attestation.py::attest_route` produces one
real, fresh identity reading; this module is Section 3.5's own "pin actual
model routes" half and this task's own acceptance clause: "Change alias
upstream or enable an external fallback during a private test; qualification
invalidates and sensitive request is denied rather than silently rerouted."

`pin_route_identity` freezes an attestation's real, resolved identity
(`deployment_fingerprint` + `resolved_model`) at the start of a private/
sealed qualification test. It refuses to pin -- raising
`RouteQualificationInvalidError(IDENTITY_UNVERIFIED)` -- whenever the
attestation's own `route_identity_state` is not `VERIFIED`. This is this
task's own negative_acceptance ("Unknown upstream identity cannot claim
reproducible/private qualification") enforced at the one place a caller
could otherwise start a qualification from an unresolved identity: there is
no partial or provisional pin this module will ever hand back for a caller
to use later.

`require_pinned_route_unchanged` is the gate every subsequent sensitive
qualification call in that same session must pass before being admitted. It
takes a *fresh* attestation (never a cached decision -- Section 3.3's own
"never retry access denial as a transient network issue" discipline extended
to identity: there is no stored "was verified last time" flag anywhere in
this module) and denies -- rather than silently rerouting through whatever
deployment now answers -- the instant the freshly observed
`deployment_fingerprint` or `resolved_model` differs from the pin, or the
fresh attestation itself failed to verify at all (the gateway/alias could
have been reconfigured to something this module cannot even identify,
which is itself already a drift, not a pass-through).

No independent schema change (this task's own `database_changes` clause):
this is a pure, synchronous, no-I/O function, same convention
`access_snapshot.py` (FTF-H004) already established -- it never calls the
gateway itself and never persists a pin; a caller holds `PinnedRouteIdentity`
in memory for the lifetime of one qualification session.

Disclosed, not hidden (same precedent every sibling FTF-H00x module already
established for "no live caller yet"): no live qualification/evaluation
workflow anywhere in this repository calls this module yet -- there is no
qualification engine in this repository at all (Inspect AI integration is
FTF-R004, still unclaimed). `proving_symbol` stays blank on this task's own
`docs/spec-matrix.yaml` entry and `product_truth_check.py`'s product-true
count is deliberately unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from packages.litellm_client.route_attestation import RouteAttestationResult, RouteIdentityState

__all__ = [
    "PinnedRouteIdentity",
    "RouteDriftReason",
    "RouteQualificationInvalidError",
    "pin_route_identity",
    "require_pinned_route_unchanged",
]


class RouteDriftReason(str, Enum):
    """Every way `require_pinned_route_unchanged`/`pin_route_identity` can
    refuse -- always exactly one of these, never inferred by a caller from
    exception text."""

    IDENTITY_UNVERIFIED = "identity_unverified"
    DEPLOYMENT_FINGERPRINT_CHANGED = "deployment_fingerprint_changed"
    RESOLVED_MODEL_CHANGED = "resolved_model_changed"
    REATTESTATION_FAILED = "reattestation_failed"


class RouteQualificationInvalidError(Exception):
    """Raised by both functions in this module for every denial. `reason`
    is a stable, machine-checkable identifier -- tests assert against it
    directly rather than parsing message prose. The message itself is
    content-free (no model/deployment identity), matching Section
    observability's "no secrets/raw prompts in task reports"."""

    def __init__(self, reason: RouteDriftReason) -> None:
        self.reason = reason
        super().__init__(f"route qualification invalid: {reason.value}")


@dataclass(frozen=True)
class PinnedRouteIdentity:
    model_id: str
    deployment_fingerprint: str
    resolved_model: str
    pinned_at: datetime


def pin_route_identity(model_id: str, attestation: RouteAttestationResult) -> PinnedRouteIdentity:
    """Freeze `attestation`'s own real, resolved identity for `model_id`.
    Raises `RouteQualificationInvalidError(IDENTITY_UNVERIFIED)` -- this
    task's own negative_acceptance -- unless the attestation is genuinely
    `VERIFIED` with both a real `deployment_fingerprint` and a real
    `resolved_model` present; there is no other way to obtain a
    `PinnedRouteIdentity` from this module."""
    if (
        attestation.route_identity_state is not RouteIdentityState.VERIFIED
        or attestation.deployment_fingerprint is None
        or attestation.resolved_model is None
    ):
        raise RouteQualificationInvalidError(RouteDriftReason.IDENTITY_UNVERIFIED)
    return PinnedRouteIdentity(
        model_id=model_id,
        deployment_fingerprint=attestation.deployment_fingerprint,
        resolved_model=attestation.resolved_model,
        pinned_at=attestation.attested_at,
    )


def require_pinned_route_unchanged(
    pinned: PinnedRouteIdentity, fresh_attestation: RouteAttestationResult
) -> None:
    """Raise `RouteQualificationInvalidError` unless `fresh_attestation`'s
    own real, current identity still matches `pinned` exactly. This is the
    one gate this task's own acceptance clause requires every sensitive
    qualification call to pass -- a denial here, not a silent reroute onto
    whatever now answers under the same alias."""
    if fresh_attestation.route_identity_state is not RouteIdentityState.VERIFIED:
        raise RouteQualificationInvalidError(RouteDriftReason.REATTESTATION_FAILED)
    if fresh_attestation.deployment_fingerprint != pinned.deployment_fingerprint:
        raise RouteQualificationInvalidError(RouteDriftReason.DEPLOYMENT_FINGERPRINT_CHANGED)
    if fresh_attestation.resolved_model != pinned.resolved_model:
        raise RouteQualificationInvalidError(RouteDriftReason.RESOLVED_MODEL_CHANGED)
