"""FTF-0901 -- Security profile enforcement at each effect (Section 11-12, 65-66).

FTF-0402 owns the closed, ordered set of profile names and the
no-relaxation rule at selection time (`standard` < `zero_egress` <
`air_gapped`). This module is the *effect-time* check Section 11 requires:
before any effect on one of the eight surfaces the acceptance clause names
(source, data, model, judge, storage, browser, history, serving) leaves the
organization boundary, `enforce_effect` re-derives the profile in force and
decides, fail closed, whether the destination is permitted.

Rules, each grounded in the specification text:

* The profile in force is the most restrictive of the organization default,
  the project selection and the profile frozen on the job when it was
  created (`effective_profile`). A weaker project setting, a project edited
  after the job started, or a request that simply omits the job profile can
  never lower it -- this is the negative_acceptance ("Private job cannot
  switch to managed route by bypassing UI or applying weaker project
  setting") made executable at the effect, not at a UI control.
* `zero_egress` and `air_gapped` (Section 11.1) allow only destinations
  inside the organization boundary, on every surface, including the
  browser (no external scripts, fonts or analytics). A managed provider
  (Section 11.2) is an external destination and is refused too.
* `standard` (Section 11.3) allows external processing only through an
  explicit, surface-scoped `RouteAuthorization` naming the exact provider;
  no authorization, a different provider, or a different surface is
  refused -- no silent provider or fallback change. A managed provider
  additionally needs its account, region and retention terms recorded
  (Section 11.2).
* Unknown profile, surface, destination kind or malformed input is a typed
  `InvalidEffectRequestError`, never "treated as internal".

Every error carries a stable `code` (API contract: a denied effect returns a
stable policy error, never success). Decisions are content-free: they carry
only the profile, surface and destination kind, never hosts, paths or data.

This module is a pure library with no live caller yet: the surfaces it
polices (datasets, workers, judges, serving) are separate, dependency-gated
tasks that will call `enforce_effect`. That gap is disclosed in
docs/spec-matrix.yaml, the same blank-`proving_symbol` precedent FTF-0902
carries.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.project_security_profile import (
    SECURITY_PROFILE_LEVELS,
    resolve_effective_security_profile,
    security_profile_rank,
)

__all__ = [
    "SURFACES",
    "DESTINATION_KINDS",
    "SecurityProfileEnforcementError",
    "InvalidEffectRequestError",
    "RouteBlockedByProfileError",
    "RouteNotAuthorizedError",
    "Destination",
    "RouteAuthorization",
    "EffectRequest",
    "EffectDecision",
    "effective_profile",
    "enforce_effect",
]

SURFACES: tuple[str, ...] = (
    "source",
    "data",
    "model",
    "judge",
    "storage",
    "browser",
    "history",
    "serving",
)
DESTINATION_KINDS: tuple[str, ...] = ("internal", "managed_provider", "external")

_BOUNDARY_ONLY_PROFILES: frozenset[str] = frozenset({"zero_egress", "air_gapped"})
_STRING_MAX_LENGTH = 200


class SecurityProfileEnforcementError(Exception):
    """Base; every subclass has a stable `code` for the API error body."""

    code = "security_profile_enforcement_error"


class InvalidEffectRequestError(SecurityProfileEnforcementError):
    code = "security_profile_invalid_request"


class RouteBlockedByProfileError(SecurityProfileEnforcementError):
    """The profile in force forbids any route outside the boundary."""

    code = "security_profile_route_blocked"


class RouteNotAuthorizedError(SecurityProfileEnforcementError):
    """`standard` profile, but no explicit authorization covers this route."""

    code = "security_profile_route_not_authorized"


def _present(value: object) -> bool:
    return isinstance(value, str) and bool(value) and not value.isspace()


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not _present(value) or len(value) > _STRING_MAX_LENGTH:
        raise InvalidEffectRequestError(f"{what} must be a non-empty string")
    return value


def _member(value: object, allowed: tuple[str, ...], what: str) -> str:
    # Type check before membership: an unhashable value must raise our own
    # error rather than TypeError.
    if not isinstance(value, str) or value not in allowed:
        raise InvalidEffectRequestError(f"{what} must be one of {allowed}")
    return value


def _profile(value: object, what: str) -> str:
    return _member(value, SECURITY_PROFILE_LEVELS, what)


@dataclass(frozen=True)
class Destination:
    kind: str
    provider: str | None = None

    def __post_init__(self) -> None:
        _member(self.kind, DESTINATION_KINDS, "destination kind")
        if self.kind == "internal":
            if self.provider is not None:
                raise InvalidEffectRequestError("an internal destination has no provider")
        else:
            _text(self.provider, "destination provider")


@dataclass(frozen=True)
class RouteAuthorization:
    """An organization policy grant for one provider on named surfaces."""

    provider: str
    surfaces: frozenset[str]
    approved_by: str
    account: str | None = None
    region: str | None = None
    retention_terms: str | None = None

    def __post_init__(self) -> None:
        _text(self.provider, "authorization provider")
        _text(self.approved_by, "authorization approver")
        if not isinstance(self.surfaces, frozenset) or not self.surfaces:
            raise InvalidEffectRequestError("authorization surfaces must be a non-empty frozenset")
        for surface in self.surfaces:
            _member(surface, SURFACES, "authorization surface")


@dataclass(frozen=True)
class EffectRequest:
    surface: str
    destination: Destination
    organization_profile: str
    project_profile: str
    job_profile: str | None = None


@dataclass(frozen=True)
class EffectDecision:
    allowed: bool
    effective_profile: str
    surface: str
    destination_kind: str


def effective_profile(
    organization_profile: object, project_profile: object, job_profile: object = None
) -> str:
    """Most restrictive of org default, project selection and job snapshot."""
    org = _profile(organization_profile, "organization profile")
    current = resolve_effective_security_profile(org, _profile(project_profile, "project profile"))
    if job_profile is not None:
        current = resolve_effective_security_profile(current, _profile(job_profile, "job profile"))
    return current


def _covers(authorization: RouteAuthorization, request: EffectRequest) -> bool:
    destination = request.destination
    if authorization.provider != destination.provider:
        return False
    if request.surface not in authorization.surfaces:
        return False
    if destination.kind == "managed_provider":
        return all(
            _present(field)
            for field in (
                authorization.account,
                authorization.region,
                authorization.retention_terms,
            )
        )
    return True


def enforce_effect(
    request: EffectRequest, authorization: RouteAuthorization | None = None
) -> EffectDecision:
    """Allow the effect or raise a typed, coded error. Never fails open."""
    if not isinstance(request, EffectRequest):
        raise InvalidEffectRequestError("request must be an EffectRequest")
    surface = _member(request.surface, SURFACES, "surface")
    if not isinstance(request.destination, Destination):
        raise InvalidEffectRequestError("destination must be a Destination")
    if authorization is not None and not isinstance(authorization, RouteAuthorization):
        raise InvalidEffectRequestError("authorization must be a RouteAuthorization")
    profile = effective_profile(
        request.organization_profile, request.project_profile, request.job_profile
    )
    kind = request.destination.kind
    decision = EffectDecision(True, profile, surface, kind)
    if kind == "internal":
        return decision
    if profile in _BOUNDARY_ONLY_PROFILES:
        raise RouteBlockedByProfileError(
            f"profile {profile!r} forbids a {kind} route on the {surface} surface "
            f"(rank {security_profile_rank(profile)})"
        )
    if authorization is None or not _covers(authorization, request):
        raise RouteNotAuthorizedError(
            f"no explicit organization authorization covers a {kind} route on the {surface} surface"
        )
    return decision
