"""FTF-0908 -- External provider denial (Section 11.1-11.3, 12, 65, 66, 70, 77, 81).

Decides whether a managed external provider may be used for *training* or
*inference* under the privacy profile in force, and denies with a stable,
coded error when it may not. It composes FTF-0901's `enforce_effect` and
`effective_profile` (the profile ordering and the no-relaxation rule are not
re-implemented here) and adds the purpose-specific approval Section 11.2
requires.

Rules:

* `zero_egress` and `air_gapped` (Section 11.1) forbid every managed provider
  route, approval or not (`external_provider_profile_blocked`).
* `standard` (Section 11.3) allows a managed provider only with an explicit
  `ProviderApproval` for that exact provider *and* that exact purpose.
  Section 11.2: the approval must record provider, account, region, the
  terms for the requested purpose, retention and permitted purposes; any
  missing field is a denial (`external_provider_approval_incomplete`).
  Training and inference are separate purposes: an inference approval never
  authorizes training (`external_provider_purpose_not_approved`).
* A label such as "private cloud account" (`ExternalProviderRequest.
  account_label`) is data only. It never approves third-party processing or
  unrestricted egress; the approval is the only way through.
* A weaker project setting or an omitted job profile cannot lower the
  effective profile (FTF-0901 rule, enforced through `effective_profile`).
* Unknown, str-subclass or unhashable values for provider, purpose or
  profile raise `InvalidProviderRequestError`, never TypeError, never allow.

The audit reason is content-free: profile, surface, purpose and code only --
never a host, path, prompt, provider or account text. When an `AuditContext`
is given the denial is written through the certified FTF-0104
`write_audit_event` in the caller's transaction; otherwise the raised error
still carries the same `AuditReason`.

Disclosure: this module is a pure decision library with no live caller yet
(the training and inference submit paths that will call it are separate,
dependency-gated tasks). It makes no network call.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.auth.audit_event_writer import write_audit_event
from packages.security.security_profile_enforcement import (
    Destination,
    EffectRequest,
    InvalidEffectRequestError,
    RouteAuthorization,
    RouteBlockedByProfileError,
    RouteNotAuthorizedError,
    SecurityProfileEnforcementError,
    effective_profile,
    enforce_effect,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = [
    "PURPOSES",
    "PURPOSE_SURFACE",
    "ExternalProviderDenialError",
    "InvalidProviderRequestError",
    "ExternalProviderDeniedError",
    "AuditReason",
    "AuditContext",
    "ProviderApproval",
    "ExternalProviderRequest",
    "ExternalProviderDecision",
    "authorize_external_provider",
]

PURPOSES: tuple[str, ...] = ("training", "inference")
# Section 12 surfaces: managed training acts on the model surface, managed
# inference on the serving surface.
PURPOSE_SURFACE: dict[str, str] = {"training": "model", "inference": "serving"}
_PROFILES: tuple[str, ...] = ("standard", "zero_egress", "air_gapped")
_STRING_MAX_LENGTH = 200

AUDIT_ACTION = "external_provider.denied"
AUDIT_RESOURCE_TYPE = "external_provider_request"


class ExternalProviderDenialError(Exception):
    """Base; every subclass has a stable `code` for the API error body."""

    code = "external_provider_error"


class InvalidProviderRequestError(ExternalProviderDenialError):
    code = "external_provider_invalid_request"


@dataclass(frozen=True)
class AuditReason:
    """Content-free denial reason: no host, path, prompt, provider or account."""

    profile: str
    surface: str
    purpose: str
    code: str


class ExternalProviderDeniedError(ExternalProviderDenialError):
    """The managed provider route is denied; `code` says why."""

    code = "external_provider_denied"

    def __init__(self, code: str, reason: AuditReason) -> None:
        super().__init__(code)
        self.code = code
        self.reason = reason


def _exact_text(value: object, what: str) -> str:
    # `type(...) is str`: a str subclass can lie about equality, hashing or
    # stripping, so it is refused rather than trusted.
    if type(value) is not str or not value or value.isspace() or len(value) > _STRING_MAX_LENGTH:
        raise InvalidProviderRequestError(f"{what} must be a non-empty string")
    return value


def _exact_member(value: object, allowed: tuple[str, ...], what: str) -> str:
    # Type check before membership so an unhashable value raises our own error.
    if type(value) is not str or value not in allowed:
        raise InvalidProviderRequestError(f"{what} must be one of {allowed}")
    return value


def _optional_text(value: object, what: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or len(value) > _STRING_MAX_LENGTH:
        raise InvalidProviderRequestError(f"{what} must be a string or None")
    return value


def _filled(value: str | None) -> bool:
    return value is not None and bool(value) and not value.isspace()


@dataclass(frozen=True)
class ProviderApproval:
    """Section 11.2 approval record for one provider.

    Missing fields are representable on purpose: an incomplete approval is a
    *denial*, not a construction error.
    """

    provider: str
    purposes: frozenset[str]
    approved_by: str | None = None
    account: str | None = None
    region: str | None = None
    retention_terms: str | None = None
    training_terms: str | None = None
    inference_terms: str | None = None

    def __post_init__(self) -> None:
        _exact_text(self.provider, "approval provider")
        if type(self.purposes) is not frozenset:
            raise InvalidProviderRequestError("approval purposes must be a frozenset")
        for purpose in self.purposes:
            _exact_member(purpose, PURPOSES, "approval purpose")
        for name in (
            "approved_by",
            "account",
            "region",
            "retention_terms",
            "training_terms",
            "inference_terms",
        ):
            _optional_text(getattr(self, name), f"approval {name}")


@dataclass(frozen=True)
class ExternalProviderRequest:
    provider: str
    purpose: str
    organization_profile: str
    project_profile: str
    job_profile: str | None = None
    # Descriptive label only (e.g. "private cloud account"); never an approval.
    account_label: str | None = None


@dataclass(frozen=True)
class ExternalProviderDecision:
    allowed: bool
    effective_profile: str
    purpose: str
    surface: str


@dataclass(frozen=True)
class AuditContext:
    """Where a denial is audited through the certified FTF-0104 writer."""

    session: Session
    organization_id: uuid.UUID
    actor_type: str
    actor_id: str
    request_id: str


def _approval_gap(
    approval: ProviderApproval | None, request: ExternalProviderRequest
) -> str | None:
    """Return the denial code for a missing/insufficient approval, else None."""
    if approval is None or approval.provider != request.provider:
        return "external_provider_not_approved"
    if request.purpose not in approval.purposes:
        return "external_provider_purpose_not_approved"
    terms = approval.training_terms if request.purpose == "training" else approval.inference_terms
    if not all(
        _filled(field)
        for field in (
            approval.approved_by,
            approval.account,
            approval.region,
            approval.retention_terms,
            terms,
        )
    ):
        return "external_provider_approval_incomplete"
    return None


def _validate(request: object) -> ExternalProviderRequest:
    if not isinstance(request, ExternalProviderRequest):
        raise InvalidProviderRequestError("request must be an ExternalProviderRequest")
    _exact_text(request.provider, "provider")
    _exact_member(request.purpose, PURPOSES, "purpose")
    _exact_member(request.organization_profile, _PROFILES, "organization profile")
    _exact_member(request.project_profile, _PROFILES, "project profile")
    if request.job_profile is not None:
        _exact_member(request.job_profile, _PROFILES, "job profile")
    _optional_text(request.account_label, "account label")
    return request


def _deny(
    code: str, reason: AuditReason, audit: AuditContext | None
) -> ExternalProviderDeniedError:
    if audit is not None:
        write_audit_event(
            audit.session,
            organization_id=audit.organization_id,
            actor_type=audit.actor_type,
            actor_id=audit.actor_id,
            action=AUDIT_ACTION,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=reason.purpose,
            request_id=audit.request_id,
            details={
                "profile": reason.profile,
                "surface": reason.surface,
                "purpose": reason.purpose,
                "code": reason.code,
            },
        )
    return ExternalProviderDeniedError(code, reason)


def authorize_external_provider(
    request: ExternalProviderRequest,
    approval: ProviderApproval | None = None,
    audit: AuditContext | None = None,
) -> ExternalProviderDecision:
    """Allow the managed-provider route or raise a coded denial. Never fails open."""
    request = _validate(request)
    if approval is not None and not isinstance(approval, ProviderApproval):
        raise InvalidProviderRequestError("approval must be a ProviderApproval")
    if audit is not None and not isinstance(audit, AuditContext):
        raise InvalidProviderRequestError("audit must be an AuditContext")
    surface = PURPOSE_SURFACE[request.purpose]
    try:
        profile = effective_profile(
            request.organization_profile, request.project_profile, request.job_profile
        )
    except InvalidEffectRequestError as exc:
        raise InvalidProviderRequestError(str(exc)) from exc

    gap = _approval_gap(approval, request)
    authorization = None
    if gap is None and approval is not None:
        authorization = RouteAuthorization(
            provider=approval.provider,
            surfaces=frozenset({surface}),
            approved_by=_exact_text(approval.approved_by, "approver"),
            account=approval.account,
            region=approval.region,
            retention_terms=approval.retention_terms,
        )
    effect = EffectRequest(
        surface,
        Destination("managed_provider", request.provider),
        request.organization_profile,
        request.project_profile,
        request.job_profile,
    )
    try:
        enforce_effect(effect, authorization)
    except RouteBlockedByProfileError:
        code = "external_provider_profile_blocked"
    except RouteNotAuthorizedError:
        code = gap or "external_provider_not_approved"
    except SecurityProfileEnforcementError as exc:
        raise InvalidProviderRequestError(str(exc)) from exc
    else:
        return ExternalProviderDecision(True, profile, request.purpose, surface)
    raise _deny(code, AuditReason(profile, surface, request.purpose, code), audit)
