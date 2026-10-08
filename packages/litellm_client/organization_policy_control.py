"""Organization policy control (FTF-1206, Section 3/38/66/77/81).

Section 38 ("LiteLLM Registration and Target-Product Adoption") describes a
real gateway-management lifecycle that this repository's own already-CERTIFIED
FTF-1201 (`litellm_registration_capability_detection.py`) only *detects*
readiness for: (38.1) "Create an immutable candidate alias in a dedicated
Factory-owned namespace; never overwrite an unrelated organization alias",
(38.2) register under "a scoped management identity", and (38.5) "Freeze
deployment bundle and request approval" before (38.6) a real production
route/session-affinity change. Nothing in this repository yet says *which*
namespaces an organization actually permits a project to register or deploy
candidates into, *who* may approve a route change, or refuses a project that
tries to claim more of any of those three than its own organization allows.
This task's own negative_acceptance is explicit: "Project-level permission
cannot widen gateway management capability or data boundary" -- the same
inheritance/no-relaxation shape FTF-0402's own already-CERTIFIED
`enforce_security_profile_selection` established for security profiles,
applied here to registration namespaces, deployment namespaces and route-change
approval authority instead of a single ordered profile.

No independent schema change (this task's own `database_changes` clause, and
`shared_lock_required: []` in its own task packet -- unlike FTF-1201, this
task does not hold the phase-12 contract lock and cannot add a live route or
column to it). `OrganizationGatewayPolicy`/`ProjectGatewayPermissionRequest`
below are the real, provable *shape* of what this task's own acceptance
clause 1 names ("Persist allowed registration/deployment namespaces,
approvers and route changes under org policy") -- every field this task
requires an organization to be able to state, and the one real, pure
enforcement function every future live route/persistence layer must call
before granting a project any of that scope. Real backing storage for an
`OrganizationGatewayPolicy` row is a separately claimed migration
prerequisite, the identical disclosed-gap precedent FTF-0103/FTF-0104/
FTF-0105/FTF-H003/FTF-0402's own six not-yet-existing consuming surfaces
already established in this repository before each was later given a real
caller -- not a placeholder path (Law 11): every function here is a real,
total, side-effect-free decision procedure over its own inputs, never a
module that exists unused and unproven.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "OrganizationGatewayPolicy",
    "ProjectGatewayPermissionRequest",
    "ProjectGatewayGrant",
    "GatewayPolicyError",
    "InvalidNamespaceError",
    "ProjectPermissionWideningError",
    "RouteChangeNotAuthorizedError",
    "validate_namespace",
    "validate_approver_identity",
    "enforce_project_gateway_permission_request",
    "enforce_route_change_authority",
]

# A Factory-owned namespace segment (Section 38.1) or an approver identity is
# a plain, safe identifier -- never free text that could later be
# interpolated into a real gateway alias/route name unescaped. Mirrors the
# same conservative shape `organization_workspace_schema.py`'s own
# `SLUG_PATTERN_SOURCE` already established for a tenant-facing identifier,
# widened only to allow `.`/`_`/`:` so a namespace like `factory.candidates`
# or an approver identity like `user:jane` (this repository's own existing
# `ActorInfo.actor_id` shape in `connection_crud.py`) is not rejected.
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._:-]{0,126}[A-Za-z0-9])?$")


class GatewayPolicyError(ValueError):
    """Base for every organization-gateway-policy violation this module raises."""


class InvalidNamespaceError(GatewayPolicyError):
    """A namespace or approver-identity value is not a safe, non-empty identifier."""


class ProjectPermissionWideningError(GatewayPolicyError):
    """This task's own negative_acceptance made executable: a project's
    requested registration namespace, deployment namespace or approver is
    not already present in its own organization's policy. A project field
    or a direct API call can never widen gateway management capability or
    the data boundary beyond what its organization has itself authorized --
    it may only ever request a subset."""


class RouteChangeNotAuthorizedError(GatewayPolicyError):
    """Section 38.5/38.6: a real production route/session-affinity change
    was attempted by an actor who is not a recognized approver under the
    owning organization's own policy, or attempted to bypass an
    organization's own approval requirement outright."""


def validate_namespace(value: object) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise InvalidNamespaceError(
            "namespace must be a non-empty, safe identifier "
            f"(letters/digits/._:- , no leading/trailing separator): {value!r}"
        )
    return value


def validate_approver_identity(value: object) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise InvalidNamespaceError(
            "approver identity must be a non-empty, safe identifier "
            f"(letters/digits/._:- , no leading/trailing separator): {value!r}"
        )
    return value


def _validated_set(values: object, *, validator: object) -> frozenset[str]:
    # Type check before iteration: a bare `str` is itself iterable (its own
    # characters), and `None`/an `int` would raise a bare `TypeError` rather
    # than this module's own typed error. Mirrors the same
    # isinstance-before-membership discipline `security_profile_enforcement.
    # RouteAuthorization.__post_init__` already established for its own
    # `surfaces: frozenset[str]` field (FTF-0402, this task's own cited
    # precedent) -- confirmed by reading that module before writing this
    # fix, not assumed.
    if not isinstance(values, (frozenset, set)):
        raise InvalidNamespaceError(
            "expected a set or frozenset of identifiers, got "
            f"{type(values).__name__}: {values!r}"
        )
    return frozenset(validator(value) for value in values)  # type: ignore[operator]


@dataclass(frozen=True)
class OrganizationGatewayPolicy:
    """The real, closed statement of what an organization's own gateway
    policy actually authorizes (this task's own acceptance clause 1, made a
    real typed shape rather than an opaque blob): which Factory-owned
    namespaces a project under this organization may register a candidate
    alias into (Section 38.1), which namespaces may be rolled into a real
    production route (Section 38.5-38.6), which identities may approve a
    route change, and whether a route change requires approval at all.
    Every field is validated at construction -- there is no path to an
    `OrganizationGatewayPolicy` instance holding an unsafe or empty
    identifier."""

    allowed_registration_namespaces: frozenset[str]
    allowed_deployment_namespaces: frozenset[str]
    approvers: frozenset[str]
    route_change_requires_approval: bool = True

    def __post_init__(self) -> None:
        # Exact type, never truthiness: `None`/`0`/`[]` are all falsy and
        # would otherwise silently switch approval off (`not None` is
        # `True`, the opposite of what a missing/malformed flag should mean
        # for a fail-closed approval gate).
        if type(self.route_change_requires_approval) is not bool:
            raise InvalidNamespaceError(
                "route_change_requires_approval must be a bool, got "
                f"{type(self.route_change_requires_approval).__name__}: "
                f"{self.route_change_requires_approval!r}"
            )
        object.__setattr__(
            self,
            "allowed_registration_namespaces",
            _validated_set(self.allowed_registration_namespaces, validator=validate_namespace),
        )
        object.__setattr__(
            self,
            "allowed_deployment_namespaces",
            _validated_set(self.allowed_deployment_namespaces, validator=validate_namespace),
        )
        object.__setattr__(
            self, "approvers", _validated_set(self.approvers, validator=validate_approver_identity)
        )


@dataclass(frozen=True)
class ProjectGatewayPermissionRequest:
    """What a project under an organization asks to be granted. Structurally
    identical to `OrganizationGatewayPolicy`'s own three set-valued fields --
    deliberately, since the only real question this task's own negative_
    acceptance asks is whether the request is a subset of the organization's
    own policy, never whether the shapes differ."""

    registration_namespaces: frozenset[str]
    deployment_namespaces: frozenset[str]
    approvers: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "registration_namespaces",
            _validated_set(self.registration_namespaces, validator=validate_namespace),
        )
        object.__setattr__(
            self,
            "deployment_namespaces",
            _validated_set(self.deployment_namespaces, validator=validate_namespace),
        )
        object.__setattr__(
            self, "approvers", _validated_set(self.approvers, validator=validate_approver_identity)
        )


@dataclass(frozen=True)
class ProjectGatewayGrant:
    """The real, effective grant a project actually holds -- always equal to
    what it requested (never silently widened or narrowed to something the
    caller didn't ask for), and only ever returned once every dimension has
    been proven to already be inside the owning organization's own policy."""

    registration_namespaces: frozenset[str]
    deployment_namespaces: frozenset[str]
    approvers: frozenset[str]


def enforce_project_gateway_permission_request(
    org_policy: OrganizationGatewayPolicy,
    requested: ProjectGatewayPermissionRequest,
) -> ProjectGatewayGrant:
    """Validates every dimension of a project's requested gateway-management
    scope against its own organization's real policy. Raises
    `ProjectPermissionWideningError` naming exactly which values are not
    already authorized -- this task's own negative_acceptance ("Project-level
    permission cannot widen gateway management capability or data boundary")
    made real: a project MAY request any subset of its organization's own
    policy, but a single unauthorized namespace or approver denies the whole
    request outright, the same all-or-nothing shape
    `enforce_security_profile_selection` already established for a relaxation
    attempt (FTF-0402) -- never a partial grant that silently drops just the
    offending value."""
    unauthorized_registration = (
        requested.registration_namespaces - org_policy.allowed_registration_namespaces
    )
    unauthorized_deployment = (
        requested.deployment_namespaces - org_policy.allowed_deployment_namespaces
    )
    unauthorized_approvers = requested.approvers - org_policy.approvers

    if unauthorized_registration or unauthorized_deployment or unauthorized_approvers:
        raise ProjectPermissionWideningError(
            "requested gateway permission widens organization policy -- "
            f"unauthorized registration namespaces: {sorted(unauthorized_registration)}, "
            f"unauthorized deployment namespaces: {sorted(unauthorized_deployment)}, "
            f"unauthorized approvers: {sorted(unauthorized_approvers)}"
        )

    return ProjectGatewayGrant(
        registration_namespaces=requested.registration_namespaces,
        deployment_namespaces=requested.deployment_namespaces,
        approvers=requested.approvers,
    )


def enforce_route_change_authority(
    org_policy: OrganizationGatewayPolicy,
    grant: ProjectGatewayGrant,
    *,
    actor: str,
    target_deployment_namespace: str,
    bypass_requested: bool = False,
) -> None:
    """Section 38.5/38.6: a real production route/session-affinity change may
    proceed only when the organization's own policy is actually satisfied
    AND the change targets a namespace this specific project was actually
    granted. Raises `RouteChangeNotAuthorizedError` if:
    (1) `target_deployment_namespace` is not already a member of `grant`'s
    own `deployment_namespaces` -- checked first, and unconditionally (even
    when the organization does not require approval at all), since this is
    a scope check on *this project's own grant*, never a question the
    organization's approval policy answers. A recognized approver, or an
    organization that waives approval entirely, authorizes a route change
    *within the project's own already-granted namespaces* -- neither widens
    what namespace it may be rolled into. This is what makes `grant` load-
    bearing rather than a type-presence-only parameter;
    (2) the organization requires approval and either (a) the caller asks to
    bypass that requirement outright, or (b) `actor` is not one of the
    organization's own recognized approvers -- checked against the
    organization's own real `approvers` set, never merely against the
    project's own (already-narrower-or-equal, per
    `enforce_project_gateway_permission_request`) grant, since a project
    cannot be trusted to enumerate its own valid approvers.

    `actor` and `target_deployment_namespace` are each re-validated here,
    defensively, even though a grant's and an `OrganizationGatewayPolicy`'s
    own members are already validated at construction: this function is the
    one real enforcement point every future live route must call, so it
    must never trust a caller-supplied value's shape, and never silently
    tolerate a case/whitespace variant of an already-granted or
    already-approved identifier as if it were the same value (exact-string
    match only -- Section 38's own namespace/approver identifiers are never
    case- or whitespace-insensitive).

    (3) `target_deployment_namespace` is also checked against the
    organization's own CURRENT `allowed_deployment_namespaces`, not only
    against `grant`: a grant issued before the organization narrowed its own
    policy must never still authorize a route change into a namespace the
    organization no longer allows at all -- this task's own
    negative_acceptance applies to the organization's policy at the moment
    of the route change, not only at the moment the grant was issued."""
    validate_approver_identity(actor)
    validate_namespace(target_deployment_namespace)
    if target_deployment_namespace not in grant.deployment_namespaces:
        raise RouteChangeNotAuthorizedError(
            f"{target_deployment_namespace!r} is not a deployment namespace this "
            "project's own grant includes -- a route change may never target a "
            "namespace outside what this project was actually granted, "
            "regardless of organization approval policy"
        )
    if target_deployment_namespace not in org_policy.allowed_deployment_namespaces:
        raise RouteChangeNotAuthorizedError(
            f"{target_deployment_namespace!r} is not in this organization's own "
            "CURRENT allowed_deployment_namespaces -- a grant issued before the "
            "organization narrowed its policy can never still authorize a route "
            "change the organization no longer allows"
        )
    if not org_policy.route_change_requires_approval:
        return
    if bypass_requested:
        raise RouteChangeNotAuthorizedError(
            "a project cannot bypass its organization's route-change approval requirement"
        )
    if actor not in org_policy.approvers:
        raise RouteChangeNotAuthorizedError(
            f"{actor!r} is not a recognized approver under this organization's policy"
        )
