"""Membership persistence store (FTF-0103A, Section 6.4/45-47/65/77/81).

The real, DB-backed `RoleLookup` (FTF-0103's `role_lookup` seam) and
`MembershipStatusLookup` (FTF-0101's login-time `membership_lookup` seam),
plus the write helpers the operator CLI (`membership_admin_cli.py`) uses.
Backed by `memberships` (`apps/api/alembic/versions/0021_memberships.py`).

Read path (runs as the restricted `sovra_app` role, SELECT-only under FORCEd
row-level security):

- `lookup_org_role` -- the role of a validated `(issuer, subject)` in ONE
  organization, read under that organization's own transaction-local tenant
  context. Only an ACTIVE, ORGANIZATION-level row (`workspace_id IS NULL`)
  counts: a revoked row, a workspace-level row, a foreign organization and
  an unknown identity all return `None`, so FTF-0103's `require_role` denies
  them identically (403). A workspace-level membership can therefore never
  grant or widen an organization-level role.
- `lookup_membership_status` -- the login gate: does this validated identity
  hold any membership at all, and is any of them active? Runs before any
  organization is known, so it binds the identity (not a tenant) to the
  transaction, which the `identity_self_lookup` policy admits and which
  exposes only that identity's own rows.

A database error is never converted into a grant or a "no membership"
answer (Law 17): it propagates, the request fails, nothing is allowed.

Write path (operator CLI only, migrate role, no public route): see
`grant_membership` / `revoke_membership`; both bind the organization's tenant
context first because the table is FORCEd.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.oidc_login import MembershipStatus
from app.auth.organization_workspace_schema import set_tenant_context
from app.auth.rbac_middleware import Role
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped, tenant_scoped_session

__all__ = [
    "MembershipError",
    "build_membership_status_lookup",
    "build_role_lookup",
    "grant_membership",
    "lookup_membership_status",
    "lookup_org_role",
    "revoke_membership",
]

# Content-free auth decision events (spec `observability`): outcome + reason
# code + organization id only -- never a subject, issuer, cookie or token.
_decision_log = logging.getLogger("sovra.auth.decision")

_VALID_ROLES = frozenset(role.value for role in Role)


class MembershipError(ValueError):
    """A membership write was refused (bad role, missing identity ...)."""


def lookup_org_role(
    session_factory: Callable[[], Session],
    issuer: str,
    subject: str,
    organization_id: uuid.UUID,
) -> Role | None:
    with tenant_scoped_session(session_factory, organization_id) as session:
        row = execute_tenant_scoped(
            session,
            organization_id,
            text(
                "SELECT role FROM memberships "
                "WHERE organization_id = :org AND workspace_id IS NULL "
                "AND issuer = :issuer AND subject = :subject AND status = 'active'"
            ),
            {"org": organization_id, "issuer": issuer, "subject": subject},
        ).first()
    if row is None:
        _decision_log.info(
            "auth_decision",
            extra={
                "outcome": "denied",
                "reason": "no_active_membership",
                "organization_id": str(organization_id),
            },
        )
        return None
    try:
        role = Role(row[0])
    except ValueError:
        # Cannot happen under ck_memberships_role; an unknown value still
        # fails closed rather than being guessed at.
        _decision_log.warning(
            "auth_decision",
            extra={
                "outcome": "denied",
                "reason": "unknown_role",
                "organization_id": str(organization_id),
            },
        )
        return None
    _decision_log.info(
        "auth_decision",
        extra={
            "outcome": "allowed",
            "reason": "active_membership",
            "organization_id": str(organization_id),
        },
    )
    return role


def lookup_membership_status(
    session_factory: Callable[[], Session], issuer: str, subject: str
) -> MembershipStatus:
    session = session_factory()
    try:
        session.execute(
            text(
                "SELECT set_config('app.current_issuer', :issuer, true), "
                "set_config('app.current_subject', :subject, true)"
            ),
            {"issuer": issuer, "subject": subject},
        )
        statuses = {
            row[0]
            for row in session.execute(
                text(
                    "SELECT status FROM memberships WHERE issuer = :issuer AND subject = :subject"
                ),
                {"issuer": issuer, "subject": subject},
            )
        }
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
    if "active" in statuses:
        return MembershipStatus.ACTIVE
    if statuses:
        return MembershipStatus.REVOKED
    return MembershipStatus.UNKNOWN


def build_role_lookup(
    session_factory: Callable[[], Session],
) -> Callable[[str, str, uuid.UUID], Role | None]:
    def role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        return lookup_org_role(session_factory, issuer, subject, organization_id)

    return role_lookup


def build_membership_status_lookup(
    session_factory: Callable[[], Session],
) -> Callable[[str, str], MembershipStatus]:
    def membership_lookup(issuer: str, subject: str) -> MembershipStatus:
        return lookup_membership_status(session_factory, issuer, subject)

    return membership_lookup


# -- operator write path (migrate role; no public route) -----------------


def grant_membership(
    session: Session,
    *,
    organization_id: uuid.UUID,
    issuer: str,
    subject: str,
    role: str,
    workspace_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Create the membership, or re-activate it with `role` if one already
    exists for the same (organization, workspace, issuer, subject)."""
    if role not in _VALID_ROLES:
        raise MembershipError(f"role must be one of {sorted(_VALID_ROLES)}: {role!r}")
    if not issuer or not subject:
        raise MembershipError("issuer and subject are required")
    set_tenant_context(session, organization_id)
    row = session.execute(
        text(
            "INSERT INTO memberships "
            "(organization_id, workspace_id, issuer, subject, role, status) "
            "VALUES (:org, :ws, :issuer, :subject, :role, 'active') "
            "ON CONFLICT (organization_id, workspace_id, issuer, subject) "
            "DO UPDATE SET role = EXCLUDED.role, status = 'active' RETURNING id"
        ),
        {
            "org": organization_id,
            "ws": workspace_id,
            "issuer": issuer,
            "subject": subject,
            "role": role,
        },
    ).one()
    membership_id: uuid.UUID = row[0]
    return membership_id


def revoke_membership(
    session: Session,
    *,
    organization_id: uuid.UUID,
    issuer: str,
    subject: str,
    workspace_id: uuid.UUID | None = None,
) -> int:
    """Mark the membership revoked (kept as history). Returns rows changed."""
    set_tenant_context(session, organization_id)
    result = session.execute(
        text(
            "UPDATE memberships SET status = 'revoked' "
            "WHERE organization_id = :org AND workspace_id IS NOT DISTINCT FROM :ws "
            "AND issuer = :issuer AND subject = :subject AND status = 'active'"
        ),
        {"org": organization_id, "ws": workspace_id, "issuer": issuer, "subject": subject},
    )
    return int(result.rowcount)  # type: ignore[attr-defined]
