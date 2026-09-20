"""Project security profile enforcement (FTF-0402, Section 6.1/6.8/11.1/22/65-66/77/81).

Section 6.8's `Project.security_profile` and Section 6.1's
`Organization.default_security_profile` (both FTF-0102/FTF-0401's own
already-CERTIFIED columns) are typed as opaque strings -- neither upstream
section fixes a concrete enumeration or an ordering between them. This
task's own job is to make that real: a small, closed, ordered set of
recognized profile names, and the rule this task's own negative_acceptance
names explicitly ("User cannot relax inherited organization policy by
changing a project field or using direct API").

The ordering is grounded directly in this specification's own text, not
invented: Law 17/Section 11.1 names `ZERO_EGRESS` as a real enforced
posture, and the `AIR_GAPPED` profile is described as adding "a deployment
constraint to the existing ZERO_EGRESS policy" -- i.e. strictly more
restrictive than it. `standard` is this task's own least-restrictive
floor; no upstream section names one below it, matching
`Organization.default_security_profile`/`Project.security_profile`'s own
existing `"standard"` server default.

This module is deliberately pure and DB-free (no SQLAlchemy/session
import at all) -- the same "pure core" discipline
`apps/api/app/projects/project_crud.py`'s own `apply_edits` already
established -- so the ordering/relaxation/minimum-profile rules are
provable without a database. `project_crud.py`'s `create_project`/
`update_project` are the real, live-wired callers: they fetch the calling
organization's own `default_security_profile` and call
`enforce_security_profile_selection` before a project's `security_profile`
is ever persisted, at both creation and edit -- closing the exact gap
`project_routes.py`'s own module docstring already disclosed ("nor real
security-profile enforcement (FTF-0402)").

Scope disclosed, not hidden: this task's acceptance clause 1 ("Enforce
selected privacy profile across datasets, source, worker, models, judges
and telemetry") names six surfaces that do not exist as live modules yet
in this repository (FTF-0500s/0600s/0800s/FTF-0901 are later, separately
dependency-gated tasks -- see docs/TASK_INDEX.md). `require_profile_at_least`
below is the general-purpose primitive those future callers will use; it
has no live caller of its own yet, the same disclosed-gap precedent
FTF-0103/FTF-0104/FTF-0105/FTF-H003 already established before FTF-0201
gave each of them a first real caller. What *does* have a real live caller
today, proven against a real Postgres database in
`tests/integration/test_ftf_0402_project_security_profile.py`, is the
inheritance/no-relaxation rule at the one surface that already exists:
project creation and edit.
"""

from __future__ import annotations

__all__ = [
    "SECURITY_PROFILE_LEVELS",
    "SecurityProfileError",
    "UnknownSecurityProfileError",
    "SecurityProfileRelaxationError",
    "InsufficientSecurityProfileError",
    "validate_security_profile_name",
    "security_profile_rank",
    "resolve_effective_security_profile",
    "enforce_security_profile_selection",
    "require_profile_at_least",
]

# Ordered least- to most-restrictive. See module docstring for exactly
# where each name is grounded in this specification's own text.
SECURITY_PROFILE_LEVELS: tuple[str, ...] = ("standard", "zero_egress", "air_gapped")

_RANK: dict[str, int] = {name: index for index, name in enumerate(SECURITY_PROFILE_LEVELS)}


class SecurityProfileError(ValueError):
    """Base for every security-profile violation this module raises."""


class UnknownSecurityProfileError(SecurityProfileError):
    """A profile name outside `SECURITY_PROFILE_LEVELS` was supplied."""


class SecurityProfileRelaxationError(SecurityProfileError):
    """A requested profile is less restrictive than the inherited
    organization policy -- this task's own negative_acceptance made
    executable: no project field, and no direct API call, can relax an
    inherited organization security policy."""


class InsufficientSecurityProfileError(SecurityProfileError):
    """The effective profile in force for a project does not meet an
    operation's own stated minimum required profile."""


def validate_security_profile_name(value: object) -> str:
    if not isinstance(value, str) or value not in _RANK:
        raise UnknownSecurityProfileError(
            f"security_profile must be one of {SECURITY_PROFILE_LEVELS}: {value!r}"
        )
    return value


def security_profile_rank(value: str) -> int:
    """The validated profile's position in `SECURITY_PROFILE_LEVELS`
    (higher = more restrictive). Raises `UnknownSecurityProfileError` for
    any value outside the closed set -- there is no silent "unknown means
    least restrictive" fallback anywhere in this module."""
    return _RANK[validate_security_profile_name(value)]


def resolve_effective_security_profile(organization_default: str, project_profile: str) -> str:
    """The profile actually in force for a project is always at least as
    restrictive as its organization's own inherited default -- the more
    restrictive of the two, never a silent average or the project's own
    unchecked choice alone."""
    org_rank = security_profile_rank(organization_default)
    project_rank = security_profile_rank(project_profile)
    return project_profile if project_rank >= org_rank else organization_default


def enforce_security_profile_selection(organization_default: str, requested_profile: str) -> str:
    """Validates a project's requested `security_profile` against its own
    organization's inherited default at the moment of selection (project
    creation or edit). Raises `SecurityProfileRelaxationError` if the
    requested profile is strictly less restrictive than the organization
    default. Returns the validated `requested_profile` unchanged on
    success -- a project MAY choose a *stricter* profile than its
    organization's own floor; this never silently substitutes the
    organization default in that case."""
    org_rank = security_profile_rank(organization_default)
    requested_rank = security_profile_rank(requested_profile)
    if requested_rank < org_rank:
        raise SecurityProfileRelaxationError(
            f"security_profile {requested_profile!r} is less restrictive than "
            f"the inherited organization default {organization_default!r} -- a "
            "project field or direct API call cannot relax organization policy"
        )
    return requested_profile


def require_profile_at_least(effective_profile: str, minimum_required: str) -> None:
    """A future consuming operation's own primitive (datasets, source
    connectors, workers, models, judges, telemetry -- this task's own
    acceptance clause names all six): raises
    `InsufficientSecurityProfileError` unless `effective_profile` is at
    least as restrictive as `minimum_required`. See this module's own
    docstring for why no live caller exists yet for this specific
    function."""
    if security_profile_rank(effective_profile) < security_profile_rank(minimum_required):
        raise InsufficientSecurityProfileError(
            f"effective security profile {effective_profile!r} does not meet "
            f"the required minimum {minimum_required!r} for this operation"
        )
