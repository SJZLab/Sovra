"""Dataset source security-boundary preview + audit evidence (FTF-0500D,
Section 6.8F/7A.10/45/65-67/77/81).

Section 7A.10's own literal "before import, show" list -- "source
name/type, estimated records/bytes, security classification, requested
purposes, retention policy, whether data will leave its current security
boundary, expected transformations" -- needs one real, load-bearing place
where those facts are composed and the same security/purpose checks a
future importer (FTF-0501+) must enforce again at real import time are
run. This module is that place: `build_dataset_source_copy_preview`
renders Section 7A.10's own field list without ever raising (a preview
must always render, even when it shows a blocking warning), and
`authorize_dataset_source_copy` is the independent, real backend gate this
task's own negative_acceptance names literally ("UI warning alone cannot
allow forbidden egress; backend checks grant and destination again at
import.") -- it re-runs both checks from scratch and raises rather than
returning a soft "ok, but risky" pass.

This task's own `database_changes` clause ("No independent schema change.
Consume frozen contract.") fixes this module's shape, the same as
FTF-0506/FTF-0507/FTF-0508/FTF-0511/FTF-0512/FTF-0513: a pure,
synchronous, no-I/O, no-DB function. It does not itself connect to any
external source (no S3/Postgres/REST credential validation exists
anywhere in this repository yet -- FTF-0501-and-later's own job, see
FTF-0500's own module docstring) -- `estimated_bytes`/`estimated_records`/
`expected_transformations` are supplied by the caller as a
`SourceCopyManifest`, the same "caller supplies what this module cannot
itself measure" shape FTF-0508's own `validate_record_batch` already
established for its own already-normalized candidate input.

Cross-boundary-import precedent, same as FTF-0511/FTF-0512/FTF-0513's own
disclosed choice: `workers/data` cannot import `apps/api` (see FTF-0506's
own module docstring for the original disclosure). This module therefore
does not import `DatasetSourceConnection`
(`apps/api/app/datasets/dataset_source_connection_schema.py`) or
`project_security_profile.py`
(`apps/api/app/services/project_security_profile.py`) -- it re-derives
their same closed vocabularies and ordering rules locally
(`SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS` mirrors FTF-0500's own
`security_classification` enum; `SUPPORTED_PROJECT_SECURITY_PROFILES`
mirrors FTF-0402's own `SECURITY_PROFILE_LEVELS`; `SUPPORTED_PURPOSES`
mirrors FTF-0500's own `ALLOWED_PURPOSES`, the same value already
mirrored as `SUPPORTED_PURPOSES` in FTF-0511/FTF-0512/FTF-0513) rather
than importing the ORM/service modules that define them, and takes plain
values (`security_classification: str`, `connection_status: str`,
`allowed_purposes: Sequence[str]`) as arguments instead of the ORM
object itself. `resolve_effective_project_security_profile` below
re-implements FTF-0402's own `resolve_effective_security_profile` rule
("the more restrictive of the two, never a silent average or the
project's own unchecked choice alone") from that same disclosed
specification grounding (Law 17/Section 11.1), not a fresh invention.

Classification-to-minimum-profile mapping (`MINIMUM_REQUIRED_SECURITY_
PROFILE`): Section 7A.10 requires showing "whether data will leave its
current security boundary" before import, but no section of this
specification fixes a concrete mapping from `security_classification`
(FTF-0500's own disclosed four-tier scheme -- public/internal/
confidential/restricted) to the minimum `security_profile` (FTF-0402's
own disclosed three-tier scheme -- standard/zero_egress/air_gapped) a
project must have in force to receive it. This is a disclosed, narrow
choice (the same class of gap-filling precedent FTF-0402/FTF-0500's own
docstrings already establish for their own underspecified enums), grounded
in the ordinary reading of each tier's own name: `public`/`internal` data
carries no confidentiality obligation beyond the factory's own standard
posture; `confidential` data requires at least `zero_egress` (no external
network egress once inside the factory); `restricted` data requires
`air_gapped` (Section 11.1's own strictest tier). A later task may tighten
or replace this mapping if the architecture ever pins a specific one,
which would be a real `spec-matrix.yaml` note update at that time, not a
silent widening now.

Retention (`RETENTION_POLICY_STATUS`): Section 7A.10 names "retention
policy" as a field to display, but no real retention value exists
anywhere in this repository to display -- Section 67.4's own
`DataUseGrant` entity (approver identity, processing boundary, retention,
expiry) is explicitly the later, separately dependency-gated concept that
will carry a real retention policy (see FTF-0500's own module docstring:
"not one of Section 6's own listed entities, and no dependency task
certifies it yet"). This module surfaces that honestly -- a single closed
sentinel value naming the real, current state ("not yet configured") --
rather than fabricating a policy value or a day count Law 11 forbids.

Scope decision, same disclosed precedent as FTF-0506/FTF-0507/FTF-0508/
FTF-0511/FTF-0512/FTF-0513/FTF-1302/FTF-1303/FTF-1304 ("no live caller
yet"): this task's own packet `files` list names only this module and its
test, not an importer/route. FTF-0501 (Direct object-storage upload,
Section 8's own first real import surface) depends on this task and does
not exist yet -- it is the first real future caller of both functions
below. `proving_symbol` stays blank; `product_truth_check.py`'s
product-true count is deliberately unchanged.

Path refinement recorded before READY (Section 84.3), same precedent as
FTF-0506/FTF-0507/FTF-0508/FTF-0511/FTF-0512/FTF-0513: the packet names
`services/training-worker/data/dataset_source_security_boundary_preview_
audit_evidence.py`, but this repo's real `SOURCE_ROOTS` (FTF-0001, PR #10)
name `workers/data` as the real data/review worker root -- there is no
`services/` tree in this repository. Placed at
`workers/data/dataset_source_security_boundary_preview_audit_evidence.py`
alongside FTF-0506's/FTF-0507's/FTF-0508's/FTF-0511's/FTF-0512's/
FTF-0513's own modules instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "MINIMUM_REQUIRED_SECURITY_PROFILE",
    "RETENTION_POLICY_STATUS",
    "SUPPORTED_CONNECTION_STATUSES",
    "SUPPORTED_PROJECT_SECURITY_PROFILES",
    "SUPPORTED_PURPOSES",
    "SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS",
    "DatasetSourceCopyAuthorizationError",
    "DatasetSourceCopyPreview",
    "DatasetSourceSecurityBoundaryError",
    "SecurityBoundaryCrossingError",
    "SourceCopyManifest",
    "authorize_dataset_source_copy",
    "build_dataset_source_copy_preview",
    "required_minimum_security_profile_for_classification",
    "resolve_effective_project_security_profile",
    "security_profile_rank",
    "would_cross_security_boundary",
]

# Mirrors FTF-0500's own `DatasetSourceConnection.security_classification`
# enum exactly (`dataset_source_connection_schema.py::
# ALLOWED_SECURITY_CLASSIFICATIONS`) -- see module docstring for why this
# is a mirrored value set, not a cross-boundary import.
SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS: tuple[str, ...] = (
    "public",
    "internal",
    "confidential",
    "restricted",
)

# Mirrors FTF-0402's own `SECURITY_PROFILE_LEVELS` exactly
# (`project_security_profile.py`), ordered least- to most-restrictive.
SUPPORTED_PROJECT_SECURITY_PROFILES: tuple[str, ...] = (
    "standard",
    "zero_egress",
    "air_gapped",
)

# Mirrors FTF-0500's own `ALLOWED_PURPOSES` exactly (the same value already
# mirrored as `SUPPORTED_PURPOSES` in FTF-0511/FTF-0512/FTF-0513).
SUPPORTED_PURPOSES: tuple[str, ...] = (
    "training",
    "validation",
    "held_out_evaluation",
    "candidate_development",
    "baseline_only",
)

# Mirrors FTF-0500's own `ALLOWED_CONNECTION_STATUSES` exactly.
SUPPORTED_CONNECTION_STATUSES: tuple[str, ...] = ("active", "revoked")

# See module docstring for the full, disclosed reasoning behind this
# mapping -- no section of this specification fixes one.
MINIMUM_REQUIRED_SECURITY_PROFILE: dict[str, str] = {
    "public": "standard",
    "internal": "standard",
    "confidential": "zero_egress",
    "restricted": "air_gapped",
}

# See module docstring for why this is a single, honest sentinel rather
# than a fabricated policy value.
RETENTION_POLICY_STATUS = "not_yet_configured"

_PROFILE_RANK: dict[str, int] = {
    name: index for index, name in enumerate(SUPPORTED_PROJECT_SECURITY_PROFILES)
}


class DatasetSourceSecurityBoundaryError(ValueError):
    """A field value violates this module's own closed vocabulary or
    shape -- raised by every `validate_*` function below."""


class DatasetSourceCopyAuthorizationError(PermissionError):
    """Raised identically whether the connection is not `active` or the
    requested purpose is not in its `allowed_purposes` -- mirrors FTF-0500's
    own `DatasetSourceAuthorizationError` shape and reasoning, restated
    here rather than cross-boundary imported (see module docstring)."""


class SecurityBoundaryCrossingError(PermissionError):
    """Raised when copying this source's own classified data into the
    caller's own effective project security profile would leave that
    data's required security boundary -- Section 7A.10's own "whether
    data will leave its current security boundary" made a real, enforced
    backend gate, not merely a UI-displayed fact."""


def validate_security_classification(value: object) -> str:
    if not isinstance(value, str) or value not in SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS:
        raise DatasetSourceSecurityBoundaryError(
            f"security_classification must be one of "
            f"{SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS}: {value!r}"
        )
    return value


def validate_project_security_profile(value: object) -> str:
    if not isinstance(value, str) or value not in _PROFILE_RANK:
        raise DatasetSourceSecurityBoundaryError(
            f"security_profile must be one of {SUPPORTED_PROJECT_SECURITY_PROFILES}: {value!r}"
        )
    return value


def validate_purpose(value: object) -> str:
    if not isinstance(value, str) or value not in SUPPORTED_PURPOSES:
        raise DatasetSourceSecurityBoundaryError(
            f"purpose must be one of {SUPPORTED_PURPOSES}: {value!r}"
        )
    return value


def validate_connection_status(value: object) -> str:
    if not isinstance(value, str) or value not in SUPPORTED_CONNECTION_STATUSES:
        raise DatasetSourceSecurityBoundaryError(
            f"connection_status must be one of {SUPPORTED_CONNECTION_STATUSES}: {value!r}"
        )
    return value


def _validate_non_negative_int(value: object, *, field_name: str) -> int:
    # `bool` is a subclass of `int` in Python -- excluded explicitly so a
    # stray `True`/`False` is never silently accepted as `1`/`0`.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DatasetSourceSecurityBoundaryError(
            f"{field_name} must be a non-negative int: {value!r}"
        )
    return value


def _validate_transformations(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise DatasetSourceSecurityBoundaryError(
            f"expected_transformations must be a list/tuple of strings: {value!r}"
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise DatasetSourceSecurityBoundaryError(
                f"expected_transformations entries must each be a non-empty string: {item!r}"
            )
        result.append(item)
    return tuple(result)


@dataclass(frozen=True)
class SourceCopyManifest:
    """Caller-supplied candidate-copy facts this module cannot itself
    measure -- no real source-connector call exists anywhere in this
    repository yet (see module docstring). A future importer (FTF-0501+)
    supplies these from whatever real connector it adds. An empty
    `expected_transformations` is a legitimate, real state (no
    transformation expected), not an error."""

    estimated_bytes: int
    estimated_records: int
    expected_transformations: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "estimated_bytes",
            _validate_non_negative_int(self.estimated_bytes, field_name="estimated_bytes"),
        )
        object.__setattr__(
            self,
            "estimated_records",
            _validate_non_negative_int(self.estimated_records, field_name="estimated_records"),
        )
        object.__setattr__(
            self,
            "expected_transformations",
            _validate_transformations(self.expected_transformations),
        )


def security_profile_rank(value: str) -> int:
    """The validated profile's position in
    `SUPPORTED_PROJECT_SECURITY_PROFILES` (higher = more restrictive).
    Mirrors FTF-0402's own `security_profile_rank` exactly."""
    return _PROFILE_RANK[validate_project_security_profile(value)]


def resolve_effective_project_security_profile(
    organization_default_profile: str, project_profile: str
) -> str:
    """Mirrors FTF-0402's own `resolve_effective_security_profile` exactly:
    the profile actually in force for a project is always at least as
    restrictive as its organization's own inherited default."""
    org_rank = security_profile_rank(organization_default_profile)
    project_rank = security_profile_rank(project_profile)
    return project_profile if project_rank >= org_rank else organization_default_profile


def required_minimum_security_profile_for_classification(security_classification: str) -> str:
    validate_security_classification(security_classification)
    return MINIMUM_REQUIRED_SECURITY_PROFILE[security_classification]


def would_cross_security_boundary(
    effective_project_security_profile: str, security_classification: str
) -> bool:
    """`True` when the effective project security profile is strictly
    less restrictive than this classification's own required minimum --
    i.e. copying this source's data here would leave its required
    security boundary (Section 7A.10)."""
    required = required_minimum_security_profile_for_classification(security_classification)
    return security_profile_rank(effective_project_security_profile) < security_profile_rank(
        required
    )


def check_purpose_authorized(
    connection_status: str, allowed_purposes: Sequence[str], requested_purpose: str
) -> None:
    """The pure, DB-free core purpose/status check -- mirrors FTF-0500's
    own `authorize_purpose_use` exactly (connection must be `active`, and
    `requested_purpose` must be one of `allowed_purposes`), restated here
    rather than cross-boundary imported (see module docstring). Raises
    `DatasetSourceCopyAuthorizationError` -- never returns a bare
    `False` -- so a caller cannot accidentally ignore a denial."""
    validate_connection_status(connection_status)
    validate_purpose(requested_purpose)
    for purpose in allowed_purposes:
        validate_purpose(purpose)
    if connection_status != "active":
        raise DatasetSourceCopyAuthorizationError(
            f"dataset source connection is not active (connection_status="
            f"{connection_status!r}) -- a revoked or otherwise inactive source "
            "authorizes no purpose"
        )
    if requested_purpose not in allowed_purposes:
        raise DatasetSourceCopyAuthorizationError(
            f"dataset source connection is not granted for purpose "
            f"{requested_purpose!r} (allowed: {tuple(allowed_purposes)})"
        )


@dataclass(frozen=True)
class DatasetSourceCopyPreview:
    """Section 7A.10's own literal "before import, show" field list, made
    real and machine-checkable. `blocked`/`blocking_reasons` name exactly
    what `authorize_dataset_source_copy` would independently refuse --
    this object is a preview only; it authorizes nothing by itself."""

    source_display_name: str
    source_type: str
    security_classification: str
    requested_purpose: str
    estimated_bytes: int
    estimated_records: int
    expected_transformations: tuple[str, ...]
    retention_policy_status: str
    effective_project_security_profile: str
    required_minimum_security_profile: str
    would_cross_security_boundary: bool
    purpose_authorized: bool
    blocked: bool
    blocking_reasons: tuple[str, ...]


def build_dataset_source_copy_preview(
    *,
    source_display_name: str,
    source_type: str,
    security_classification: str,
    connection_status: str,
    allowed_purposes: Sequence[str],
    requested_purpose: str,
    organization_default_security_profile: str,
    project_security_profile: str,
    manifest: SourceCopyManifest,
) -> DatasetSourceCopyPreview:
    """Section 7A.10's own field list, composed and never raised on a
    blocking condition -- a preview must always render, even when it
    shows a blocking warning. Runs the exact same two checks
    `authorize_dataset_source_copy` below independently re-runs at real
    import time; this task's own negative_acceptance ("UI warning alone
    cannot allow forbidden egress; backend checks grant and destination
    again at import.") is precisely why that second, independent call
    exists rather than this preview's own `blocked` flag being trusted at
    import time."""
    validate_security_classification(security_classification)
    validate_connection_status(connection_status)
    validate_purpose(requested_purpose)
    for purpose in allowed_purposes:
        validate_purpose(purpose)
    organization_default_security_profile = validate_project_security_profile(
        organization_default_security_profile
    )
    project_security_profile = validate_project_security_profile(project_security_profile)

    effective_profile = resolve_effective_project_security_profile(
        organization_default_security_profile, project_security_profile
    )
    required_profile = required_minimum_security_profile_for_classification(security_classification)
    crosses_boundary = would_cross_security_boundary(effective_profile, security_classification)

    blocking_reasons: list[str] = []
    purpose_authorized = True
    try:
        check_purpose_authorized(connection_status, allowed_purposes, requested_purpose)
    except DatasetSourceCopyAuthorizationError as exc:
        purpose_authorized = False
        blocking_reasons.append(str(exc))
    if crosses_boundary:
        blocking_reasons.append(
            f"security_classification {security_classification!r} requires at least "
            f"{required_profile!r}, but the effective project security profile is "
            f"{effective_profile!r} -- copying would leave this data's own required "
            "security boundary"
        )

    return DatasetSourceCopyPreview(
        source_display_name=source_display_name,
        source_type=source_type,
        security_classification=security_classification,
        requested_purpose=requested_purpose,
        estimated_bytes=manifest.estimated_bytes,
        estimated_records=manifest.estimated_records,
        expected_transformations=manifest.expected_transformations,
        retention_policy_status=RETENTION_POLICY_STATUS,
        effective_project_security_profile=effective_profile,
        required_minimum_security_profile=required_profile,
        would_cross_security_boundary=crosses_boundary,
        purpose_authorized=purpose_authorized,
        blocked=bool(blocking_reasons),
        blocking_reasons=tuple(blocking_reasons),
    )


def authorize_dataset_source_copy(
    *,
    security_classification: str,
    connection_status: str,
    allowed_purposes: Sequence[str],
    requested_purpose: str,
    organization_default_security_profile: str,
    project_security_profile: str,
) -> None:
    """The real backend gate a future importer (FTF-0501+) calls again at
    real import time -- independent of, and never trusting, any preview a
    UI may have already rendered or any warning a user may have dismissed.
    Raises `DatasetSourceCopyAuthorizationError` or
    `SecurityBoundaryCrossingError`; never returns a soft "ok, but risky"
    pass. This task's own negative_acceptance in full: "UI warning alone
    cannot allow forbidden egress; backend checks grant and destination
    again at import." Both checks are re-derived from scratch from this
    call's own arguments -- nothing here reads or trusts a previously
    built `DatasetSourceCopyPreview`."""
    validate_security_classification(security_classification)
    validate_connection_status(connection_status)
    validate_purpose(requested_purpose)
    for purpose in allowed_purposes:
        validate_purpose(purpose)
    organization_default_security_profile = validate_project_security_profile(
        organization_default_security_profile
    )
    project_security_profile = validate_project_security_profile(project_security_profile)

    check_purpose_authorized(connection_status, allowed_purposes, requested_purpose)

    effective_profile = resolve_effective_project_security_profile(
        organization_default_security_profile, project_security_profile
    )
    if would_cross_security_boundary(effective_profile, security_classification):
        required_profile = required_minimum_security_profile_for_classification(
            security_classification
        )
        raise SecurityBoundaryCrossingError(
            f"security_classification {security_classification!r} requires at least "
            f"{required_profile!r}, but the effective project security profile is "
            f"{effective_profile!r} -- refusing to copy across this security boundary"
        )
