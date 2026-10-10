"""FTF-0617 -- Zero-egress judge policy (Section 3.5, 11.1-11.3, 23-30, 69.1, 69.5, 77, 81).

Decides, on the server, *which judge may grade* a project's candidate output and
verifies, per call, that the judge that answered is the one that was approved.
On a strict project (FTF-0901's boundary-only profiles, `zero_egress` and
`air_gapped`) the only judges are

* a **private LLM judge**: an organization admin's explicit approval of one
  exact (`judge_model_id`, `resolved_model`, `deployment_fingerprint`) with
  every upstream / fallback / logging-callback / cache / embedding destination
  named and inside the boundary, re-attested against the live gateway before
  any candidate content is sent (Section 3.5: "a local gateway address does not
  prove local processing"), or
* **authorized, independent humans** (Section 69.1), or
* nothing: an explicit `blocked` / `judge_capability_unavailable` (Section
  69.5). A judge outage, an unsupported parameter or an unknown upstream is
  never a pass, and never silently another model.

Names and hosts are never evidence of locality: an alias or gateway address
that *looks* local upgrades nothing. Identity comparisons on the allow path are
exact `str` equality (no strip, casefold or NFKC); normalisation is used only
to *refuse* (reviewer independence).

Rules this module enforces itself:

* The profile in force is read here, from the database, through
  `execute_tenant_scoped` in the caller's tenant-scoped session, and composed
  with an optional job snapshot by FTF-0901 `effective_profile`. No caller
  supplies a profile string or a UI flag. A missing and a foreign-org project
  both give `judge_policy_not_found`.
* The gateway's `base_url` / `secret_ref` come from the tenant's FTF-0201
  connection row, never from a caller string. A revoked connection is refused.
* Approval JSON is plain JSON, strictly parsed (duplicate keys, NaN/Infinity,
  wrong types, extra or missing keys, oversize input all refused as
  `judge_approval_invalid`).
* The approver is resolved server-side by FTF-0103A `lookup_org_role` and must
  be an active `organization_admin` or `workspace_admin`.
* `JudgePlan` can only be issued by `decide_judge_plan`; `run_private_judge`
  refuses a hand-built plan and re-reads the profile and re-checks expiry on
  every call.

This module is read-only: it writes nothing to the database, so there is no
commit/rollback coupling with the caller's session. It makes no network call
except through FTF-H005 `attest_route` and FTF-0616 `grade_llm_judge`.

Errors carry a stable `code` and a content-free message (code, profile and
surface only): never a model id, fingerprint, host, provider, approval field
or reviewer id. Exceptions from FTF-0616 / H005 / 0901 are re-raised
`from None` so their text cannot leak through a cause chain.

Disclosed (not hidden): no EvaluationRun or workflow calls this yet (UNWIRED;
first callers FTF-R004-P3/P4 and FTF-0618); the approval is caller-supplied
plain JSON until an approval store exists; a real private judge behind the org
gateway is BLOCKED_EXTERNAL and is proven here only against the local TLS
gateway stand-in; non-bypassable network-layer denial is FTF-0907/0906/0902.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from packages.litellm_client.real_chat_probe import RouteIdentityState
from packages.litellm_client.route_attestation import (
    RouteAttestationError,
    attest_route,
)
from packages.litellm_client.secret_manager_integration import OpenBaoConfig
from packages.model_catalog.route_snapshot import RouteQualificationInvalidError, pin_route_identity
from packages.security.outbound_guard import OutboundGuardError
from packages.security.security_profile_enforcement import (
    _BOUNDARY_ONLY_PROFILES as STRICT_PROFILES,
)
from packages.security.security_profile_enforcement import (
    Destination,
    EffectRequest,
    RouteAuthorization,
    SecurityProfileEnforcementError,
    effective_profile,
    enforce_effect,
)
from packages.security.url_policy import ConnectionPolicy, OutboundPolicyError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.membership_store import lookup_org_role
from app.auth.rbac_middleware import Role
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from app.evaluation.evaluation_suite_routes import EVALUATION_SUITE_WRITE_ROLES
from app.litellm.connection_crud import ConnectionNotFoundError, get_connection
from app.services.project_security_profile import security_profile_rank
from workers.evaluation.llm_judge_grader import (
    CalibrationRecord,
    JudgeRubric,
    LlmJudgeGraderError,
    LlmJudgeResult,
    LlmJudgeState,
    grade_llm_judge,
)

__all__ = [
    "APPROVAL_MAX_BYTES",
    "APPROVAL_SCHEMA_VERSION",
    "APPROVER_ROLES",
    "DESTINATION_ROLES",
    "REVIEWER_ROLES",
    "HumanJudgmentAuthorization",
    "JudgeApprovalExpiredError",
    "JudgeAttestationRefusedError",
    "JudgeCallRefusedError",
    "JudgeApprovalInvalidError",
    "JudgeApprovalNotAuthorizedError",
    "JudgeDestinationBlockedError",
    "JudgeFallbackNotPermittedError",
    "JudgeGatewayRevokedError",
    "JudgeMode",
    "JudgeOutcome",
    "JudgePlan",
    "JudgePlanError",
    "JudgePolicyInvalidInputError",
    "JudgePolicyNotFoundError",
    "HumanReviewerNotAuthorizedError",
    "HumanReviewerNotIndependentError",
    "PrivateJudgeApproval",
    "ZeroEgressJudgePolicyError",
    "authorize_human_judgment",
    "decide_judge_plan",
    "load_profiles_in_force",
    "parse_private_judge_approval",
    "run_private_judge",
]

_log = logging.getLogger("sovra.evaluation.zero_egress_judge")

APPROVAL_SCHEMA_VERSION = 1
APPROVAL_MAX_BYTES = 16384
_TEXT_MAX = 200  # FTF-0901's own string limit
_FINGERPRINT_MAX = 256  # FTF-0616's own fingerprint limit
_MAX_REVIEWER_CANDIDATES = 50

APPROVER_ROLES: frozenset[Role] = frozenset({Role.ORGANIZATION_ADMIN, Role.WORKSPACE_ADMIN})
REVIEWER_ROLES: frozenset[Role] = EVALUATION_SUITE_WRITE_ROLES

# Section 3.5: every one of these destinations behind the gateway must be named.
DESTINATION_ROLES: tuple[str, ...] = (
    "upstream",
    "fallbacks",
    "logging_callbacks",
    "cache",
    "embedding",
)
# role -> may this destination be explicitly disabled (cache and embedding only)
_ROLE_DISABLEABLE: dict[str, bool] = {
    "upstream": False,
    "logging_callbacks": False,
    "cache": True,
    "embedding": True,
}
# kind -> (exact key set, is "disabled"); every other kind names a provider or is internal
_KIND_RULES: dict[str, tuple[frozenset[str], bool]] = {
    "disabled": (frozenset({"kind"}), True),
    "internal": (frozenset({"kind"}), False),
    "managed_provider": (frozenset({"kind", "provider"}), False),
    "external": (frozenset({"kind", "provider"}), False),
}
# Strictness is derived from FTF-0901's boundary-only set (not re-listed) as a rank threshold.
_STRICT_MIN_RANK = min(security_profile_rank(name) for name in STRICT_PROFILES)
_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "judge_model_id",
        "resolved_model",
        "deployment_fingerprint",
        "boundary_id",
        "destinations",
        "approved_by_issuer",
        "approved_by_subject",
        "approved_at",
        "expires_at",
    }
)
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})",
    re.ASCII,
)
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


class JudgeMode(str, Enum):
    PRIVATE_LLM_JUDGE = "private_llm_judge"
    HUMAN_REVIEW = "human_review"
    STANDARD_LLM_JUDGE = "standard_llm_judge"
    BLOCKED = "blocked"


class ZeroEgressJudgePolicyError(Exception):
    """Base; every subclass has a stable `code`. Messages are content-free."""

    code = "zero_egress_judge_policy_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message if message is not None else self.code)


class JudgePolicyInvalidInputError(ZeroEgressJudgePolicyError):
    code = "judge_policy_invalid_input"


class JudgePolicyNotFoundError(ZeroEgressJudgePolicyError):
    code = "judge_policy_not_found"


class JudgeGatewayRevokedError(ZeroEgressJudgePolicyError):
    code = "judge_gateway_revoked"


class JudgeApprovalInvalidError(ZeroEgressJudgePolicyError):
    code = "judge_approval_invalid"


class JudgeApprovalExpiredError(ZeroEgressJudgePolicyError):
    code = "judge_approval_expired"


class JudgeApprovalNotAuthorizedError(ZeroEgressJudgePolicyError):
    code = "judge_approval_not_authorized"


class JudgeFallbackNotPermittedError(ZeroEgressJudgePolicyError):
    code = "judge_fallback_not_permitted"


class JudgeDestinationBlockedError(ZeroEgressJudgePolicyError):
    code = "judge_destination_blocked"


class HumanReviewerNotAuthorizedError(ZeroEgressJudgePolicyError):
    code = "human_reviewer_not_authorized"


class HumanReviewerNotIndependentError(ZeroEgressJudgePolicyError):
    code = "human_reviewer_not_independent"


class JudgeCallRefusedError(ZeroEgressJudgePolicyError):
    """FTF-0616 refused the call before any network I/O (self-judging, uncalibrated ...)."""

    code = "judge_call_refused"


class JudgeAttestationRefusedError(ZeroEgressJudgePolicyError):
    """FTF-H005 refused the admission attestation before any network I/O."""

    code = "judge_attestation_refused"


class JudgePlanError(ZeroEgressJudgePolicyError):
    """A plan that was not issued by `decide_judge_plan`, or is stale / not private."""

    code = "judge_plan_invalid"


# -- Exact-type input helpers ---------------------------------------------------------


def _approval_text(value: object, maximum: int = _TEXT_MAX) -> str:
    """An exact `str` of 1..maximum characters with no control, format, surrogate,
    separator or whitespace character. Nothing is stripped or normalised."""
    if type(value) is not str or not 1 <= len(value) <= maximum:
        raise JudgeApprovalInvalidError()
    for char in value:
        if char.isspace() or unicodedata.category(char) in _UNSAFE_CATEGORIES:
            raise JudgeApprovalInvalidError()
    return value


def _input_text(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= _TEXT_MAX:
        raise JudgePolicyInvalidInputError()
    for char in value:
        if unicodedata.category(char) in _UNSAFE_CATEGORIES:
            raise JudgePolicyInvalidInputError()
    return value


def _input_uuid(value: object) -> uuid.UUID:
    if type(value) is not uuid.UUID:
        raise JudgePolicyInvalidInputError()
    return value


def _input_now(value: object) -> datetime:
    if type(value) is not datetime or value.utcoffset() is None:
        raise JudgePolicyInvalidInputError()
    return value


def _input_context(context: object, session: object) -> TenantContext:
    if type(context) is not TenantContext or not isinstance(session, Session):
        raise JudgePolicyInvalidInputError()
    return context


def _identity(value: object) -> tuple[str, str]:
    if type(value) is not tuple or len(value) != 2:
        raise JudgePolicyInvalidInputError()
    return (_input_text(value[0]), _input_text(value[1]))


# -- Profile in force (read from the database, never from a caller) -------------------


def load_profiles_in_force(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    job_profile: str | None = None,
) -> tuple[str, str, str]:
    """`(organization_profile, project_profile, effective)` read from the database in the
    caller's tenant-scoped session. `effective` is FTF-0901's most-restrictive composition
    with the optional job snapshot. A missing and a foreign-org project are identical."""
    context = _input_context(context, session)
    project_id = _input_uuid(project_id)
    if job_profile is not None:
        job_profile = _input_text(job_profile)
    organization_id = context.organization_id
    org_row = execute_tenant_scoped(
        session,
        organization_id,
        text("SELECT default_security_profile FROM organizations WHERE id = :org"),
        {"org": organization_id},
    ).first()
    project_row = execute_tenant_scoped(
        session,
        organization_id,
        text(
            "SELECT security_profile FROM projects "
            "WHERE organization_id = :org AND id = :project"
        ),
        {"org": organization_id, "project": project_id},
    ).first()
    if org_row is None or project_row is None:
        raise JudgePolicyNotFoundError()
    try:
        effective = effective_profile(org_row[0], project_row[0], job_profile)
    except SecurityProfileEnforcementError:
        # An unknown stored profile is not "standard"; refuse rather than guess.
        raise JudgePolicyInvalidInputError() from None
    return (org_row[0], project_row[0], effective)


def _is_strict(profile: str) -> bool:
    return security_profile_rank(profile) >= _STRICT_MIN_RANK


def _has_role(role: Role | None, allowed: frozenset[Role]) -> bool:
    """Identity, not equality: only the exact enum members count."""
    return any(role is member for member in allowed)


def _load_connection(
    session: Session, context: TenantContext, connection_id: uuid.UUID
) -> tuple[str, str]:
    connection_id = _input_uuid(connection_id)
    try:
        connection = get_connection(session, context, connection_id)
    except ConnectionNotFoundError:
        raise JudgePolicyNotFoundError() from None
    if connection.status != "active":
        raise JudgeGatewayRevokedError()
    return (connection.base_url, connection.secret_ref)


# -- Private-judge approval -----------------------------------------------------------


@dataclass(frozen=True)
class JudgeDestinationRecord:
    kind: str
    provider: str | None = None


@dataclass(frozen=True)
class PrivateJudgeApproval:
    schema_version: int
    judge_model_id: str
    resolved_model: str
    deployment_fingerprint: str
    boundary_id: str
    upstream: JudgeDestinationRecord | None
    logging_callbacks: JudgeDestinationRecord | None
    cache: JudgeDestinationRecord | None
    embedding: JudgeDestinationRecord | None
    approved_by_issuer: str
    approved_by_subject: str
    approved_at: datetime
    expires_at: datetime


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate key")
    return result


def _reject_constant(name: str) -> Any:
    raise ValueError("non-finite number")


def _timestamp(value: object) -> datetime:
    if type(value) is not str or _TIMESTAMP.fullmatch(value) is None:
        raise JudgeApprovalInvalidError()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise JudgeApprovalInvalidError() from None
    return parsed


def _destination_record(value: object, disableable: bool) -> JudgeDestinationRecord | None:
    """`None` is an explicitly disabled destination."""
    if type(value) is not dict:
        raise JudgeApprovalInvalidError()
    kind = value.get("kind")
    if type(kind) is not str:
        raise JudgeApprovalInvalidError()
    rule = _KIND_RULES.get(kind)
    if rule is None:
        raise JudgeApprovalInvalidError()
    keys, is_disabled = rule
    if set(value) != keys:
        raise JudgeApprovalInvalidError()
    if is_disabled:
        if not disableable:
            raise JudgeApprovalInvalidError()
        return None
    if "provider" in keys:
        return JudgeDestinationRecord(kind, _approval_text(value["provider"]))
    return JudgeDestinationRecord(kind)


def parse_private_judge_approval(
    raw: object,
    *,
    organization_profile: str,
    project_profile: str,
    job_profile: str | None,
    now: datetime,
) -> PrivateJudgeApproval:
    """Strictly parse and validate approval JSON. Raises one of
    `JudgeApprovalInvalidError`, `JudgeFallbackNotPermittedError`,
    `JudgeDestinationBlockedError`, `JudgeApprovalExpiredError`. The approver's role is
    NOT checked here (that is a database lookup, done by `decide_judge_plan`)."""
    now = _input_now(now)
    if type(raw) is str:
        try:
            data = raw.encode("utf-8")
        except UnicodeEncodeError:
            raise JudgeApprovalInvalidError() from None
    elif type(raw) is bytes:
        data = raw
    else:
        raise JudgeApprovalInvalidError()
    if len(data) > APPROVAL_MAX_BYTES:
        raise JudgeApprovalInvalidError()
    try:
        document = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError):
        raise JudgeApprovalInvalidError() from None
    if type(document) is not dict or set(document) != _TOP_LEVEL_KEYS:
        raise JudgeApprovalInvalidError()
    version = document["schema_version"]
    if type(version) is not int or version != APPROVAL_SCHEMA_VERSION:
        raise JudgeApprovalInvalidError()
    judge_model_id = _approval_text(document["judge_model_id"])
    resolved_model = _approval_text(document["resolved_model"])
    fingerprint = _approval_text(document["deployment_fingerprint"], _FINGERPRINT_MAX)
    boundary_id = _approval_text(document["boundary_id"])
    issuer = _approval_text(document["approved_by_issuer"])
    subject = _approval_text(document["approved_by_subject"])
    approved_at = _timestamp(document["approved_at"])
    expires_at = _timestamp(document["expires_at"])
    destinations = document["destinations"]
    if type(destinations) is not dict or set(destinations) != set(DESTINATION_ROLES):
        raise JudgeApprovalInvalidError()
    fallbacks = destinations["fallbacks"]
    if type(fallbacks) is not list:
        raise JudgeApprovalInvalidError()
    records = {
        role: _destination_record(destinations[role], disableable)
        for role, disableable in _ROLE_DISABLEABLE.items()
    }
    if expires_at <= approved_at:
        raise JudgeApprovalInvalidError()
    # A fallback would change the attested identity anyway.
    if fallbacks:
        raise JudgeFallbackNotPermittedError()
    for record in records.values():
        if record is None:
            continue
        try:
            enforce_effect(
                EffectRequest(
                    "judge",
                    Destination(record.kind, record.provider),
                    organization_profile,
                    project_profile,
                    job_profile,
                )
            )
        except SecurityProfileEnforcementError:
            raise JudgeDestinationBlockedError() from None
    if expires_at <= now or approved_at > now:
        raise JudgeApprovalExpiredError()
    return PrivateJudgeApproval(
        schema_version=version,
        judge_model_id=judge_model_id,
        resolved_model=resolved_model,
        deployment_fingerprint=fingerprint,
        boundary_id=boundary_id,
        upstream=records["upstream"],
        logging_callbacks=records["logging_callbacks"],
        cache=records["cache"],
        embedding=records["embedding"],
        approved_by_issuer=issuer,
        approved_by_subject=subject,
        approved_at=approved_at,
        expires_at=expires_at,
    )


# -- Humans ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HumanJudgmentAuthorization:
    role: Role


def _normalised(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


def authorize_human_judgment(
    *,
    session_factory: Callable[[], Session],
    organization_id: uuid.UUID,
    reviewer: tuple[str, str],
    candidate_producer: tuple[str, str],
) -> HumanJudgmentAuthorization:
    """The reviewer must be an active member with a role in `REVIEWER_ROLES`, resolved with
    the exact (issuer, subject), and must not be the candidate's producer (Section 69.1) --
    refused when equal exactly or after NFKC+casefold+strip: exact on the allow side,
    normalised on the deny side."""
    organization_id = _input_uuid(organization_id)
    reviewer_issuer, reviewer_subject = _identity(reviewer)
    producer_issuer, producer_subject = _identity(candidate_producer)
    role = lookup_org_role(session_factory, reviewer_issuer, reviewer_subject, organization_id)
    if not _has_role(role, REVIEWER_ROLES):
        raise HumanReviewerNotAuthorizedError()
    if (reviewer_issuer, reviewer_subject) == (producer_issuer, producer_subject) or (
        _normalised(reviewer_issuer),
        _normalised(reviewer_subject),
    ) == (_normalised(producer_issuer), _normalised(producer_subject)):
        raise HumanReviewerNotIndependentError()
    return HumanJudgmentAuthorization(role)


# -- Plan -----------------------------------------------------------------------------

_ISSUER = object()


@dataclass(frozen=True)
class JudgePlan:
    """What may judge. Only `decide_judge_plan` can issue one."""

    mode: JudgeMode
    reason: str
    profile: str
    organization_id: uuid.UUID
    project_id: uuid.UUID
    connection_id: uuid.UUID
    approval_defect: str | None = None
    human_reviewer_roles: frozenset[Role] = frozenset()
    approval: PrivateJudgeApproval | None = None
    _issued: object = field(default=None, repr=False, compare=False)


def _plan(
    mode: JudgeMode,
    reason: str,
    profile: str,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    approval_defect: str | None = None,
    human_reviewer_roles: frozenset[Role] = frozenset(),
    approval: PrivateJudgeApproval | None = None,
) -> JudgePlan:
    _log.info(
        "judge_plan",
        extra={
            "mode": mode.value,
            "reason": reason,
            "profile": profile,
            "organization_id": str(organization_id),
        },
    )
    return JudgePlan(
        mode,
        reason,
        profile,
        organization_id,
        project_id,
        connection_id,
        approval_defect,
        human_reviewer_roles,
        approval,
        _ISSUER,
    )


def _attestation_defect(
    approval: PrivateJudgeApproval,
    *,
    base_url: str,
    secret_ref: str,
    secret_config: OpenBaoConfig | None,
    policy: ConnectionPolicy,
    now: datetime,
) -> str | None:
    """Admission attestation: synthetic content, cache bypassed, made before any candidate
    content is sent. `None` when the live gateway reports exactly the approved identity."""
    try:
        attestation = attest_route(
            base_url=base_url,
            model_id=approval.judge_model_id,
            approved_model_ids=frozenset({approval.judge_model_id}),
            secret_ref=secret_ref,
            secret_config=secret_config,
            policy=policy,
            now=now,
        )
    except RouteAttestationError:
        raise JudgeAttestationRefusedError() from None
    try:
        pinned = pin_route_identity(approval.judge_model_id, attestation)
    except RouteQualificationInvalidError:
        return "judge_route_identity_unverified"
    if (
        pinned.resolved_model != approval.resolved_model
        or pinned.deployment_fingerprint != approval.deployment_fingerprint
    ):
        return "judge_admission_mismatch"
    return None


def decide_judge_plan(
    *,
    session: Session,
    context: TenantContext,
    session_factory: Callable[[], Session],
    project_id: uuid.UUID,
    job_profile: str | None,
    connection_id: uuid.UUID,
    approval: object,
    human_reviewers: tuple[tuple[str, str], ...],
    candidate_producer: tuple[str, str],
    judge_destination: Destination | None,
    authorization: RouteAuthorization | None,
    secret_config: OpenBaoConfig | None,
    policy: ConnectionPolicy,
    now: datetime,
) -> JudgePlan:
    """Server-side judge selection. See the module docstring for the strict-project rules.
    Standard projects: a non-internal judge destination needs FTF-0901 `enforce_effect(
    surface="judge")` to allow it (the Section 11.2 managed-provider approval stays with
    FTF-0907/0908)."""
    context = _input_context(context, session)
    project_id = _input_uuid(project_id)
    connection_id = _input_uuid(connection_id)
    now = _input_now(now)
    if type(human_reviewers) is not tuple or len(human_reviewers) > _MAX_REVIEWER_CANDIDATES:
        raise JudgePolicyInvalidInputError()
    candidate_producer = _identity(candidate_producer)
    reviewers = tuple(_identity(reviewer) for reviewer in human_reviewers)
    organization_id = context.organization_id
    organization_profile, project_profile, profile = load_profiles_in_force(
        session, context, project_id, job_profile
    )
    base_url, secret_ref = _load_connection(session, context, connection_id)

    def issue(mode: JudgeMode, reason: str, **kwargs: Any) -> JudgePlan:
        return _plan(mode, reason, profile, organization_id, project_id, connection_id, **kwargs)

    if not _is_strict(profile):
        if type(judge_destination) is not Destination:
            raise JudgePolicyInvalidInputError()
        try:
            enforce_effect(
                EffectRequest(
                    "judge", judge_destination, organization_profile, project_profile, job_profile
                ),
                authorization,
            )
        except SecurityProfileEnforcementError:
            return issue(JudgeMode.BLOCKED, "judge_destination_blocked")
        return issue(JudgeMode.STANDARD_LLM_JUDGE, "standard_judge_allowed")

    defect: str | None = None
    if approval is not None:
        try:
            parsed = parse_private_judge_approval(
                approval,
                organization_profile=organization_profile,
                project_profile=project_profile,
                job_profile=job_profile,
                now=now,
            )
            role = lookup_org_role(
                session_factory,
                parsed.approved_by_issuer,
                parsed.approved_by_subject,
                organization_id,
            )
            if not _has_role(role, APPROVER_ROLES):
                raise JudgeApprovalNotAuthorizedError()
        except ZeroEgressJudgePolicyError as exc:
            defect = exc.code
        else:
            try:
                defect = _attestation_defect(
                    parsed,
                    base_url=base_url,
                    secret_ref=secret_ref,
                    secret_config=secret_config,
                    policy=policy,
                    now=now,
                )
            except (OutboundGuardError, OutboundPolicyError):
                defect = "judge_route_identity_unverified"
            if defect is None:
                return issue(JudgeMode.PRIVATE_LLM_JUDGE, "private_judge_attested", approval=parsed)

    authorized_roles: set[Role] = set()
    for reviewer in reviewers:
        try:
            authorized_roles.add(
                authorize_human_judgment(
                    session_factory=session_factory,
                    organization_id=organization_id,
                    reviewer=reviewer,
                    candidate_producer=candidate_producer,
                ).role
            )
        except ZeroEgressJudgePolicyError:
            continue
    if authorized_roles:
        return issue(
            JudgeMode.HUMAN_REVIEW,
            "authorized_human_review",
            approval_defect=defect,
            human_reviewer_roles=REVIEWER_ROLES,
        )
    return issue(JudgeMode.BLOCKED, "judge_capability_unavailable", approval_defect=defect)


# -- Per-call execution ---------------------------------------------------------------


@dataclass(frozen=True)
class JudgeOutcome:
    """Either a real verdict (`blocked` False, `result` set) or a blocked outcome with a
    stable `code`. A blocked outcome carries no model identity and no judge text."""

    blocked: bool
    code: str | None
    possible_egress: bool = False
    result: LlmJudgeResult | None = None

    @property
    def passed(self) -> bool | None:
        return None if self.result is None else self.result.passed


def _blocked(code: str, *, possible_egress: bool = False) -> JudgeOutcome:
    _log.info("judge_outcome", extra={"code": code, "possible_egress": possible_egress})
    return JudgeOutcome(True, code, possible_egress)


def run_private_judge(
    plan: JudgePlan,
    *,
    session: Session,
    context: TenantContext,
    job_profile: str | None,
    rubric: JudgeRubric,
    calibration: CalibrationRecord,
    candidate_model_id: str,
    candidate_response: str,
    secret_config: OpenBaoConfig | None,
    policy: ConnectionPolicy,
    now: datetime,
) -> JudgeOutcome:
    """One judge call through the approved private judge only. Never retried on another model
    and never falls back to another approved judge. Re-reads the profile, re-checks expiry and
    verifies the verdict came from exactly the approved (resolved model, deployment)."""
    context = _input_context(context, session)
    now = _input_now(now)
    if type(plan) is not JudgePlan or plan._issued is not _ISSUER:
        raise JudgePlanError()
    if plan.mode is not JudgeMode.PRIVATE_LLM_JUDGE:
        raise JudgePlanError()
    approval = plan.approval
    if approval is None:
        raise JudgePlanError()
    if plan.organization_id != context.organization_id:
        raise JudgePlanError()
    _organization_profile, _project_profile, profile = load_profiles_in_force(
        session, context, plan.project_id, job_profile
    )
    if security_profile_rank(profile) != security_profile_rank(plan.profile) or not _is_strict(
        profile
    ):
        raise JudgePlanError()
    if approval.expires_at <= now or approval.approved_at > now:
        return _blocked("judge_approval_expired")
    base_url, secret_ref = _load_connection(session, context, plan.connection_id)
    try:
        result = grade_llm_judge(
            base_url=base_url,
            judge_model_id=approval.judge_model_id,
            candidate_model_id=candidate_model_id,
            approved_judge_model_ids=frozenset({approval.judge_model_id, approval.resolved_model}),
            rubric=rubric,
            candidate_response=candidate_response,
            calibration=calibration,
            critical=False,
            secret_ref=secret_ref,
            secret_config=secret_config,
            policy=policy,
            now=now,
        )
    except LlmJudgeGraderError:
        raise JudgeCallRefusedError() from None
    if result.state is LlmJudgeState.PASSED or result.state is LlmJudgeState.FAILED:
        if (
            result.route_identity_state is not RouteIdentityState.VERIFIED
            or result.resolved_model is None
            or result.deployment_fingerprint is None
        ):
            return _blocked("judge_route_identity_unverified")
        if (
            result.resolved_model != approval.resolved_model
            or result.deployment_fingerprint != approval.deployment_fingerprint
        ):
            # A content-bearing request was answered by another deployment: it may already
            # have left the boundary (preventing that is network-layer scope).
            return _blocked("judge_route_changed", possible_egress=True)
        return JudgeOutcome(False, None, False, result)
    if result.state is LlmJudgeState.MODEL_MISMATCH:
        return _blocked("judge_route_changed", possible_egress=True)
    return _blocked("judge_unavailable")
