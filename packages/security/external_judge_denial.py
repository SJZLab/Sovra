"""FTF-0907 -- External judge denial (Section 11.1-11.3, 12, 65, 66, 70, 77, 81).

Non-bypassable denial, at the security layer, of an external or unknown-upstream
judge on a private project, both at *admission* and at *request time*. FTF-0617
owns judge selection and per-call identity; this module owns the boundary check
around it. It composes FTF-0901 `enforce_effect` / `effective_profile` (the
profile ordering and the no-relaxation rule are not re-implemented here).

Rules:

* A project is *private* when the effective profile is `zero_egress` or
  `air_gapped` (FTF-0901 boundary-only profiles). On a private project every
  upstream that is not `internal` is denied (`external_judge_upstream_external`)
  and an upstream that is not known is denied (`external_judge_upstream_unknown`).
  A name, alias or gateway address that *looks* local is never evidence.
* Fallback aliases are part of the route: every alias must name its resolved
  upstream and each is checked exactly like the primary
  (`external_judge_fallback_denied`); a route whose fallbacks are not fully
  enumerated, or one with an unknown fallback upstream, is denied.
* On `standard` an external judge still needs FTF-0901 `enforce_effect(surface=
  "judge")` to allow it with an explicit `RouteAuthorization`; an unknown
  upstream is denied on every profile. (The Section 11.2 managed-provider
  approval stays with FTF-0908.)
* Request time cannot be reached around admission: `check_judge_request` and
  `guarded_judge_call` refuse a missing, forged or `dataclasses.replace`d
  `JudgeAdmission` (`external_judge_admission_missing`), refuse a route that is
  not exactly the admitted route (`external_judge_route_changed`), and
  re-evaluate the boundary against the profile in force *now*. A caller that
  holds a raw client can only reach the judge through `guarded_judge_call`,
  which runs the check before `send` is invoked and passes `send` the admitted
  route snapshot (all inputs must be exact types, never subclasses).
* Unknown, str-subclass or unhashable values raise the module's own
  `InvalidJudgeRequestError`, never TypeError, never allow.

Denials carry a content-free `AuditReason` (profile, surface, code) -- never a
host, model name, alias or provider text.

Disclosure: pure decision library, no network, no DB, no live caller yet (the
evaluation worker's judge call path that will use `guarded_judge_call` is a
separate dependency-gated task). UNWIRED until that caller exists.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from packages.security.security_profile_enforcement import (
    Destination,
    EffectRequest,
    RouteAuthorization,
    RouteBlockedByProfileError,
    RouteNotAuthorizedError,
    SecurityProfileEnforcementError,
    effective_profile,
    enforce_effect,
)

__all__ = [
    "JUDGE_SURFACE",
    "ExternalJudgeError",
    "InvalidJudgeRequestError",
    "ExternalJudgeDeniedError",
    "AuditReason",
    "FallbackAlias",
    "JudgeRoute",
    "ProfilesInForce",
    "JudgeAdmission",
    "admit_judge_route",
    "check_judge_request",
    "guarded_judge_call",
]

JUDGE_SURFACE = "judge"
_STRING_MAX_LENGTH = 200
T = TypeVar("T")


class ExternalJudgeError(Exception):
    """Base; every subclass has a stable `code` for the API error body."""

    code = "external_judge_error"


class InvalidJudgeRequestError(ExternalJudgeError):
    code = "external_judge_invalid_request"


@dataclass(frozen=True)
class AuditReason:
    """Content-free: profile, surface and code only."""

    profile: str
    surface: str
    code: str


class ExternalJudgeDeniedError(ExternalJudgeError):
    """A denial; `code` is one of the stable `external_judge_*` codes."""

    def __init__(self, code: str, reason: AuditReason) -> None:
        super().__init__(code)
        self.code = code
        self.reason = reason


def _exact_text(value: object, what: str) -> str:
    if type(value) is not str or not value or value.isspace() or len(value) > _STRING_MAX_LENGTH:
        raise InvalidJudgeRequestError(f"{what} must be a non-empty exact string")
    return value


def _exact_destination(value: object, what: str) -> None:
    """None (unknown) or an exact Destination whose fields are exact str: a str subclass
    with a lying `__eq__` must never reach `enforce_effect`'s `kind == "internal"`."""
    if value is None:
        return
    if (
        type(value) is not Destination
        or type(value.kind) is not str
        or type(value.provider) not in (str, type(None))
    ):
        raise InvalidJudgeRequestError(f"{what} must be an exact Destination or None")


@dataclass(frozen=True)
class FallbackAlias:
    """A fallback alias and the upstream it resolves to (`None` = unknown)."""

    alias: str
    upstream: Destination | None


@dataclass(frozen=True)
class JudgeRoute:
    """Everything the gateway could send a judge request to.

    `upstream` is the primary's resolved destination; `fallbacks` is the
    complete list of fallback aliases. `fallbacks_enumerated` must be True: a
    route that cannot list its fallbacks cannot be proven closed.
    """

    judge_model_id: str
    upstream: Destination | None
    fallbacks: tuple[FallbackAlias, ...] = ()
    fallbacks_enumerated: bool = False

    def __post_init__(self) -> None:
        _exact_text(self.judge_model_id, "judge model id")
        _exact_destination(self.upstream, "upstream")
        if type(self.fallbacks) is not tuple:
            raise InvalidJudgeRequestError("fallbacks must be a tuple")
        for fallback in self.fallbacks:
            if type(fallback) is not FallbackAlias:
                raise InvalidJudgeRequestError("each fallback must be a FallbackAlias")
            _exact_text(fallback.alias, "fallback alias")
            _exact_destination(fallback.upstream, "fallback upstream")
        if type(self.fallbacks_enumerated) is not bool:
            raise InvalidJudgeRequestError("fallbacks_enumerated must be a bool")


@dataclass(frozen=True)
class ProfilesInForce:
    """The organization/project/job profiles read server-side by the caller."""

    organization_profile: str
    project_profile: str
    job_profile: str | None = None


_ISSUER = object()


@dataclass(frozen=True)
class JudgeAdmission:
    """Issued only by `admit_judge_route`; never constructed by a caller."""

    route: JudgeRoute
    profile: str
    # init=False: `dataclasses.replace` rebuilds through __init__, so a replaced or
    # hand-built admission cannot carry the issuer sentinel across.
    _issued: object = field(default=None, init=False, repr=False, compare=False)


def _deny(code: str, profile: str) -> ExternalJudgeDeniedError:
    return ExternalJudgeDeniedError(code, AuditReason(profile, JUDGE_SURFACE, code))


def _profile(profiles: object) -> str:
    if type(profiles) is not ProfilesInForce:
        raise InvalidJudgeRequestError("profiles must be ProfilesInForce")
    for value in (profiles.organization_profile, profiles.project_profile, profiles.job_profile):
        if value is not None and type(value) is not str:
            raise InvalidJudgeRequestError("profile must be an exact string")
    try:
        return effective_profile(
            profiles.organization_profile, profiles.project_profile, profiles.job_profile
        )
    except SecurityProfileEnforcementError:
        raise InvalidJudgeRequestError("invalid security profile") from None


def _check_destination(
    destination: Destination | None,
    profile: str,
    authorization: RouteAuthorization | None,
    external_code: str,
) -> None:
    if destination is None:
        raise _deny("external_judge_upstream_unknown", profile)
    try:
        enforce_effect(
            EffectRequest(
                JUDGE_SURFACE,
                destination,
                profile,
                profile,
            ),
            authorization,
        )
    except (RouteBlockedByProfileError, RouteNotAuthorizedError):
        raise _deny(external_code, profile) from None


def _exact_authorization(authorization: RouteAuthorization) -> None:
    """Exact-typed fields: a str/frozenset subclass with lying `__eq__`/`__contains__` must
    never reach `enforce_effect`'s provider and surface comparisons."""
    optional = (authorization.account, authorization.region, authorization.retention_terms)
    if (
        type(authorization.provider) is not str
        or type(authorization.surfaces) is not frozenset
        or any(type(surface) is not str for surface in authorization.surfaces)
        or any(value is not None and type(value) is not str for value in optional)
    ):
        raise InvalidJudgeRequestError("authorization fields must be exact types")


def _evaluate(
    route: object,
    profiles: object,
    authorization: RouteAuthorization | None,
) -> str:
    if type(route) is not JudgeRoute:
        raise InvalidJudgeRequestError("route must be a JudgeRoute")
    if authorization is not None and type(authorization) is not RouteAuthorization:
        raise InvalidJudgeRequestError("authorization must be a RouteAuthorization")
    if authorization is not None:
        _exact_authorization(authorization)
    profile = _profile(profiles)
    assert isinstance(profiles, ProfilesInForce)
    _check_destination(route.upstream, profile, authorization, "external_judge_upstream_external")
    if not route.fallbacks_enumerated:
        raise _deny("external_judge_fallback_denied", profile)
    for fallback in route.fallbacks:
        try:
            _check_destination(
                fallback.upstream,
                profile,
                authorization,
                "external_judge_fallback_denied",
            )
        except ExternalJudgeDeniedError as error:
            raise _deny("external_judge_fallback_denied", error.reason.profile) from None
    return profile


def admit_judge_route(
    route: JudgeRoute,
    profiles: ProfilesInForce,
    authorization: RouteAuthorization | None = None,
) -> JudgeAdmission:
    """Admission-time denial. Returns an issued admission or raises a coded error."""
    profile = _evaluate(route, profiles, authorization)
    admission = JudgeAdmission(route, profile)
    object.__setattr__(admission, "_issued", _ISSUER)
    return admission


def check_judge_request(
    admission: object,
    route: object,
    profiles: ProfilesInForce,
    authorization: RouteAuthorization | None = None,
) -> JudgeRoute:
    """Request-time denial against the profile in force *now* and the admitted route.
    Returns the admitted route snapshot."""
    profile = _profile(profiles)
    if type(admission) is not JudgeAdmission or admission._issued is not _ISSUER:
        raise _deny("external_judge_admission_missing", profile)
    if route != admission.route:
        raise _deny("external_judge_route_changed", profile)
    _evaluate(route, profiles, authorization)
    return admission.route


def guarded_judge_call(
    admission: object,
    route: object,
    profiles: ProfilesInForce,
    send: Callable[[JudgeRoute], T],
    authorization: RouteAuthorization | None = None,
) -> T:
    """The only supported way to reach a judge client: check first, then `send`."""
    # Hand over the admitted snapshot, never the caller-held object.
    return send(check_judge_request(admission, route, profiles, authorization))
