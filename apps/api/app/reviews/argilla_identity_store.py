"""Argilla review scope, reviewer identity and workspace-membership store
(FTF-R006-P1b, Section 8-10, 65, 67, 77.3-77.6, 78.4, 81).

Backed by `argilla_review_scopes` / `argilla_reviewer_identities` /
`argilla_workspace_members` (`apps/api/alembic/versions/0034_argilla_review_identity.py`).

This module is the one place that turns "a principal holds a
DATASET_SOURCE_ROLES membership" into real Argilla-server state, through
FTF-R006-P1a's `ArgillaClient` (`packages/integrations/argilla/client.py`,
untouched here) and FTF-0202's OpenBao integration
(`packages/litellm_client/secret_manager_integration.py`, also untouched).
Three operations, each independently idempotent and safe to retry:

- `ensure_review_scope` -- one Argilla workspace per (organization, project),
  named `sovra-` + sha256(org|project)[:24] so two projects can never collide
  and the name never carries anything readable about the project itself.
- `ensure_reviewer_identity` -- one Argilla user per (organization, Sovra
  principal), `annotator` only. The server's own plaintext `api_key`
  response is sent straight to OpenBao (`store_secret_ref`) and only the
  `openbao://` reference is ever written to this database or returned by
  this module -- Law 16/Section 65.4's "no secret value in this database."
  A revoked identity (`status='revoked'`) is a dead end: this function and
  every other one below refuse to touch it again
  (`ReviewerIdentityRevokedError`), never silently reactivating it.
- `sync_workspace_membership` -- the one function a caller re-runs whenever
  a principal's `DATASET_SOURCE_ROLES` membership changes (granted,
  widened, narrowed or revoked): adds the identity to the project's
  workspace when entitled, removes it the moment it is not. A removed
  `DATASET_SOURCE_ROLES` membership therefore always converges to a
  removed Argilla workspace membership, even if this function is only
  re-run later (eventual, not instantaneous, consistency -- there is no
  live webhook from `memberships` yet).
- `revoke_reviewer_identity` -- an explicit, final revoke: removes every
  workspace membership the identity still holds, then marks the identity
  itself `revoked`. The Argilla user account is left provisioned (not
  deleted) so prior annotations keep a real author -- only the Factory's
  own ability to add it to any further workspace is cut off.

Every real network outcome from `ArgillaClient` is surfaced through this
module's own `ArgillaIdentityError` subclasses or re-raised from
`packages.integrations.argilla.client`'s own `ArgillaClientError` family
unchanged (Law 17): a transient `ArgillaUnavailableError` is never
swallowed into "already done," and `ArgillaConflictError`/
`ArgillaNotFoundError` are read as the specific idempotent outcome each
call site expects (already a member / already removed), never as a
generic failure.

No API key, password or raw Argilla response body is ever logged, stored
outside OpenBao, or included in any exception message this module raises.
"""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from packages.integrations.argilla.client import (
    ArgillaClient,
    ArgillaConflictError,
    ArgillaNotFoundError,
)
from packages.litellm_client.secret_manager_integration import OpenBaoConfig, store_secret_ref
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.organization_workspace_schema import set_tenant_context
from app.auth.rbac_middleware import Role
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped, tenant_scoped_session

__all__ = [
    "ARGILLA_COMPONENT_KEY",
    "DATASET_SOURCE_ROLES",
    "ArgillaIdentityError",
    "ReviewScopeRecord",
    "ReviewerIdentityRecord",
    "ReviewerIdentityRevokedError",
    "ensure_review_scope",
    "ensure_reviewer_identity",
    "revoke_reviewer_identity",
    "sync_workspace_membership",
    "workspace_name_for",
]

# Mirrors apps/api/app/datasets/dataset_source_connection_routes.py::DATASET_SOURCE_ROLES
# exactly (not imported from there, to avoid a dataset-feature module depending on a
# reviews-feature one for a frozenset three lines long; both read Section 45's same role
# list, so a future change to one is a visible same-class-sweep prompt for the other).
DATASET_SOURCE_ROLES: frozenset[Role] = frozenset(
    {Role.ML_ENGINEER, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}
)

ARGILLA_COMPONENT_KEY = "argilla"
# Matches tests/integration/test_ftf_0202_secret_manager.py / test_ftf_0500's own convention:
# the single default `secret` KV v2 mount, namespaced by a path prefix -- never a new mount
# (which would need its own infra-side `bao secrets enable` step this module cannot perform).
_OPENBAO_MOUNT = "secret"
_OPENBAO_PATH_PREFIX = "argilla-reviewers"
_PASSWORD_BYTES = 32  # secrets.token_urlsafe(32) -> 43 chars, inside ArgillaClient's 8..100 bound.
# A real Argilla username conflict on create_user can only mean a concurrent caller is
# provisioning the exact same (organization, issuer, subject) identity -- the username is a
# deterministic hash of exactly that triple (_username_for), never reused across identities.
# The race is bounded: the winner commits its own DB row immediately after its own successful
# create_user call, so a short, bounded poll always converges.
_CONFLICT_POLL_ATTEMPTS = 20
_CONFLICT_POLL_DELAY_SECONDS = 0.05


class ArgillaIdentityError(Exception):
    """Base for every failure this store raises itself (never for a real
    `ArgillaClientError`, which always propagates as-is -- Law 17)."""


class ReviewerIdentityRevokedError(ArgillaIdentityError):
    """The (organization, issuer, subject) identity exists but is
    `revoked` -- refused for any further use, never silently reactivated."""


@dataclass(frozen=True)
class ReviewScopeRecord:
    id: uuid.UUID
    organization_id: uuid.UUID
    project_id: uuid.UUID
    component_binding_id: uuid.UUID
    argilla_workspace_id: str
    argilla_workspace_name: str


@dataclass(frozen=True)
class ReviewerIdentityRecord:
    id: uuid.UUID
    organization_id: uuid.UUID
    issuer: str
    subject: str
    argilla_user_id: str
    credential_secret_ref: str
    status: str


def workspace_name_for(organization_id: uuid.UUID, project_id: uuid.UUID) -> str:
    """Deterministic, collision-free, content-free Argilla workspace name:
    `sovra-` + sha256(org|project)[:24]. Matches `ArgillaClient`'s own
    `_WORKSPACE_NAME` identifier shape (`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`)
    by construction -- a hex digest prefixed with a letter.

    Every real caller already validated both ids as real `uuid.UUID`
    values before reaching here (`tenant_scoped_session`'s own
    `TenantContextError` guard runs first in `ensure_review_scope`) -- the
    checks below are defense in depth, not the only guard, so this
    function never silently hashes a non-UUID into a deterministic-looking
    but meaningless name."""
    if not isinstance(organization_id, uuid.UUID) or not isinstance(project_id, uuid.UUID):
        raise TypeError("workspace_name_for requires real uuid.UUID values")
    digest = hashlib.sha256(f"{organization_id}|{project_id}".encode("ascii")).hexdigest()
    return f"sovra-{digest[:24]}"


def _username_for(organization_id: uuid.UUID, issuer: str, subject: str) -> str:
    """Deterministic, content-free Argilla username for one Sovra identity.
    Never the real issuer/subject text (which could be an email-shaped
    value) -- only a digest, matching `workspace_name_for`'s own rule."""
    if not isinstance(organization_id, uuid.UUID):
        raise TypeError("_username_for requires a real uuid.UUID organization_id")
    if not isinstance(issuer, str) or not issuer or not isinstance(subject, str) or not subject:
        raise TypeError("_username_for requires non-empty string issuer/subject")
    digest = hashlib.sha256(f"{organization_id}|{issuer}\x00{subject}".encode()).hexdigest()
    return f"sovra-{digest[:24]}"


def _scope_row(
    session: Session, organization_id: uuid.UUID, project_id: uuid.UUID
) -> ReviewScopeRecord | None:
    row = execute_tenant_scoped(
        session,
        organization_id,
        text(
            "SELECT id, component_binding_id, argilla_workspace_id, argilla_workspace_name "
            "FROM argilla_review_scopes WHERE organization_id = :org AND project_id = :project"
        ),
        {"org": organization_id, "project": project_id},
    ).first()
    if row is None:
        return None
    return ReviewScopeRecord(
        id=row[0],
        organization_id=organization_id,
        project_id=project_id,
        component_binding_id=row[1],
        argilla_workspace_id=row[2],
        argilla_workspace_name=row[3],
    )


def ensure_review_scope(
    session_factory: Callable[[], Session],
    *,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    component_binding_id: uuid.UUID,
    client: ArgillaClient,
) -> ReviewScopeRecord:
    """Idempotent: returns the existing scope for (organization, project),
    or provisions the real Argilla workspace and persists it. A concurrent
    create-race (two callers provisioning the same project at once) is
    resolved by one re-select on the table's own UNIQUE (organization_id,
    project_id) constraint, never by retrying the Argilla create -- the
    same discipline `ArgillaClient.ensure_workspace` already uses one
    layer down."""
    with tenant_scoped_session(session_factory, organization_id) as session:
        existing = _scope_row(session, organization_id, project_id)
        if existing is not None:
            return existing

    name = workspace_name_for(organization_id, project_id)
    workspace = client.ensure_workspace(name)

    with tenant_scoped_session(session_factory, organization_id) as session:
        try:
            row = execute_tenant_scoped(
                session,
                organization_id,
                text(
                    "INSERT INTO argilla_review_scopes "
                    "(organization_id, project_id, component_binding_id, argilla_workspace_id, "
                    "argilla_workspace_name) "
                    "VALUES (:org, :project, :binding, :workspace_id, :workspace_name) "
                    "RETURNING id"
                ),
                {
                    "org": organization_id,
                    "project": project_id,
                    "binding": component_binding_id,
                    "workspace_id": workspace.id,
                    "workspace_name": workspace.name,
                },
            ).first()
            session.flush()
        except IntegrityError:
            session.rollback()
            set_tenant_context(session, organization_id)
            existing = _scope_row(session, organization_id, project_id)
            if existing is None:
                raise  # pragma: no cover -- the row that caused the conflict must exist
            return existing
        assert row is not None
        return ReviewScopeRecord(
            id=row[0],
            organization_id=organization_id,
            project_id=project_id,
            component_binding_id=component_binding_id,
            argilla_workspace_id=workspace.id,
            argilla_workspace_name=workspace.name,
        )


def _identity_row(
    session: Session, organization_id: uuid.UUID, issuer: str, subject: str
) -> ReviewerIdentityRecord | None:
    row = execute_tenant_scoped(
        session,
        organization_id,
        text(
            "SELECT id, argilla_user_id, credential_secret_ref, status "
            "FROM argilla_reviewer_identities "
            "WHERE organization_id = :org AND issuer = :issuer AND subject = :subject"
        ),
        {"org": organization_id, "issuer": issuer, "subject": subject},
    ).first()
    if row is None:
        return None
    return ReviewerIdentityRecord(
        id=row[0],
        organization_id=organization_id,
        issuer=issuer,
        subject=subject,
        argilla_user_id=row[1],
        credential_secret_ref=row[2],
        status=row[3],
    )


def _require_active(identity: ReviewerIdentityRecord) -> ReviewerIdentityRecord:
    if identity.status == "revoked":
        raise ReviewerIdentityRevokedError(
            f"reviewer identity {identity.id} is revoked and may not be reused"
        )
    return identity


def ensure_reviewer_identity(
    session_factory: Callable[[], Session],
    *,
    organization_id: uuid.UUID,
    issuer: str,
    subject: str,
    client: ArgillaClient,
    openbao_config: OpenBaoConfig,
) -> ReviewerIdentityRecord:
    """Idempotent: returns the existing identity for (organization, issuer,
    subject), or provisions a real Argilla `annotator` user and persists
    it. Raises `ReviewerIdentityRevokedError` for an existing but revoked
    identity -- never silently reactivates one (a revoked identity must be
    re-provisioned under a fresh decision, not resurrected by a caller
    that merely calls this function again)."""
    with tenant_scoped_session(session_factory, organization_id) as session:
        existing = _identity_row(session, organization_id, issuer, subject)
        if existing is not None:
            return _require_active(existing)

    username = _username_for(organization_id, issuer, subject)
    password = secrets.token_urlsafe(_PASSWORD_BYTES)
    try:
        user = client.create_user(username=username, role="annotator", password=password)
    except ArgillaConflictError:
        # A concurrent caller already won the race for this exact identity (the username is
        # deterministic, so this can only be our own identity's winner) -- poll the database
        # for the row it is about to commit rather than treating this as a real failure.
        for attempt in range(_CONFLICT_POLL_ATTEMPTS):
            with tenant_scoped_session(session_factory, organization_id) as session:
                existing = _identity_row(session, organization_id, issuer, subject)
            if existing is not None:
                return _require_active(existing)
            if attempt + 1 < _CONFLICT_POLL_ATTEMPTS:
                time.sleep(_CONFLICT_POLL_DELAY_SECONDS)
        raise ArgillaIdentityError(
            f"Argilla rejected username {username!r} as already existing, but no matching "
            f"identity row ever appeared for (organization, issuer, subject) after "
            f"{_CONFLICT_POLL_ATTEMPTS} attempts"
        ) from None
    del password  # never keep the plaintext password alive past the create call.
    credential_secret_ref = store_secret_ref(
        config=openbao_config,
        mount=_OPENBAO_MOUNT,
        path=f"{_OPENBAO_PATH_PREFIX}/{organization_id}/{user.id}",
        field="api_key",
        value=user.api_key,
    )

    with tenant_scoped_session(session_factory, organization_id) as session:
        try:
            row = execute_tenant_scoped(
                session,
                organization_id,
                text(
                    "INSERT INTO argilla_reviewer_identities "
                    "(organization_id, issuer, subject, argilla_user_id, credential_secret_ref) "
                    "VALUES (:org, :issuer, :subject, :argilla_user_id, :secret_ref) "
                    "RETURNING id, status"
                ),
                {
                    "org": organization_id,
                    "issuer": issuer,
                    "subject": subject,
                    "argilla_user_id": user.id,
                    "secret_ref": credential_secret_ref,
                },
            ).first()
            session.flush()
        except IntegrityError:
            session.rollback()
            set_tenant_context(session, organization_id)
            existing = _identity_row(session, organization_id, issuer, subject)
            if existing is None:
                raise  # pragma: no cover -- the row that caused the conflict must exist
            return _require_active(existing)
        assert row is not None
        return ReviewerIdentityRecord(
            id=row[0],
            organization_id=organization_id,
            issuer=issuer,
            subject=subject,
            argilla_user_id=user.id,
            credential_secret_ref=credential_secret_ref,
            status=row[1],
        )


def _membership_row(
    session: Session, organization_id: uuid.UUID, scope_id: uuid.UUID, identity_id: uuid.UUID
) -> tuple[uuid.UUID, str] | None:
    row = execute_tenant_scoped(
        session,
        organization_id,
        text(
            "SELECT id, status FROM argilla_workspace_members "
            "WHERE organization_id = :org AND scope_id = :scope AND identity_id = :identity"
        ),
        {"org": organization_id, "scope": scope_id, "identity": identity_id},
    ).first()
    return (row[0], row[1]) if row is not None else None


def sync_workspace_membership(
    session_factory: Callable[[], Session],
    *,
    organization_id: uuid.UUID,
    issuer: str,
    subject: str,
    role: Role | None,
    scope: ReviewScopeRecord,
    identity: ReviewerIdentityRecord,
    client: ArgillaClient,
) -> None:
    """Converges this identity's real Argilla workspace membership to
    exactly what `role` entitles: a member while `role` is in
    `DATASET_SOURCE_ROLES`, not a member the moment it is not (membership
    narrowed, revoked, or never granted). Call this again whenever the
    principal's own `DATASET_SOURCE_ROLES` membership changes -- there is
    no live trigger from `memberships` yet, so convergence is only as
    fresh as the caller's own re-sync cadence (disclosed UNWIRED gap, same
    class as every other P1-P3 slice of this task).

    `identity`'s own `status` field is never trusted as-is: a caller may
    be holding a reference captured before a concurrent
    `revoke_reviewer_identity` call, so this function re-reads the
    identity's *current* status from the database on every call, not the
    possibly-stale value on the object the caller passed in."""
    entitled = role in DATASET_SOURCE_ROLES

    with tenant_scoped_session(session_factory, organization_id) as session:
        current = _identity_row(session, organization_id, issuer, subject)
        if current is None or current.id != identity.id:
            raise ArgillaIdentityError(
                f"reviewer identity {identity.id} no longer matches (organization, issuer, "
                "subject) -- refusing to sync a membership for a stale identity reference"
            )
        _require_active(current)
        membership = _membership_row(session, organization_id, scope.id, identity.id)

    if entitled:
        if membership is not None and membership[1] == "active":
            return
        try:
            client.add_workspace_user(scope.argilla_workspace_id, identity.argilla_user_id)
        except ArgillaConflictError:
            pass  # already a member on the real server -- idempotent outcome, not a failure.
        with tenant_scoped_session(session_factory, organization_id) as session:
            if membership is None:
                execute_tenant_scoped(
                    session,
                    organization_id,
                    text(
                        "INSERT INTO argilla_workspace_members "
                        "(organization_id, scope_id, identity_id, status) "
                        "VALUES (:org, :scope, :identity, 'active')"
                    ),
                    {"org": organization_id, "scope": scope.id, "identity": identity.id},
                )
            else:
                execute_tenant_scoped(
                    session,
                    organization_id,
                    text(
                        "UPDATE argilla_workspace_members SET status = 'active', "
                        "updated_at = now() WHERE id = :id"
                    ),
                    {"id": membership[0]},
                )
    else:
        if membership is None or membership[1] == "revoked":
            return
        try:
            client.remove_workspace_user(scope.argilla_workspace_id, identity.argilla_user_id)
        except ArgillaNotFoundError:
            pass  # already not a member on the real server -- idempotent outcome.
        with tenant_scoped_session(session_factory, organization_id) as session:
            execute_tenant_scoped(
                session,
                organization_id,
                text(
                    "UPDATE argilla_workspace_members SET status = 'revoked', "
                    "updated_at = now() WHERE id = :id"
                ),
                {"id": membership[0]},
            )


def revoke_reviewer_identity(
    session_factory: Callable[[], Session],
    *,
    organization_id: uuid.UUID,
    issuer: str,
    subject: str,
    client: ArgillaClient,
) -> None:
    """Final, explicit revoke: removes every active workspace membership
    this identity still holds on the real Argilla server, then marks the
    identity `revoked`. Idempotent -- revoking an already-revoked identity
    is a no-op, never an error (so a retried or duplicated revoke request
    can never fail)."""
    with tenant_scoped_session(session_factory, organization_id) as session:
        identity = _identity_row(session, organization_id, issuer, subject)
        if identity is None:
            raise ArgillaIdentityError(f"no reviewer identity for organization {organization_id}")
        if identity.status == "revoked":
            return
        memberships = execute_tenant_scoped(
            session,
            organization_id,
            text(
                "SELECT m.id, s.argilla_workspace_id FROM argilla_workspace_members m "
                "JOIN argilla_review_scopes s ON s.id = m.scope_id "
                "AND s.organization_id = m.organization_id "
                "WHERE m.organization_id = :org AND m.identity_id = :identity "
                "AND m.status = 'active'"
            ),
            {"org": organization_id, "identity": identity.id},
        ).all()

    for membership_id, argilla_workspace_id in memberships:
        try:
            client.remove_workspace_user(argilla_workspace_id, identity.argilla_user_id)
        except ArgillaNotFoundError:
            pass
        with tenant_scoped_session(session_factory, organization_id) as session:
            execute_tenant_scoped(
                session,
                organization_id,
                text(
                    "UPDATE argilla_workspace_members SET status = 'revoked', "
                    "updated_at = now() WHERE id = :id"
                ),
                {"id": membership_id},
            )

    with tenant_scoped_session(session_factory, organization_id) as session:
        execute_tenant_scoped(
            session,
            organization_id,
            text(
                "UPDATE argilla_reviewer_identities SET status = 'revoked', revoked_at = :now, "
                "updated_at = :now WHERE id = :id"
            ),
            {"id": identity.id, "now": datetime.now(UTC)},
        )
