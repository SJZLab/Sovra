"""Real-effect LiteLLM model-access guard (FTF-H004, Section 3.2/3.3/6/65/77/81).

`packages/litellm_client/last_known_good_catalog.py::LastKnownGoodCatalog.
is_call_authorized` (FTF-0208, CERTIFIED) already answers "is this model
currently callable," but it only ever sees the in-memory catalog a caller
happens to hand it -- it has no idea whether that caller is even the
connection's own organization, or whether an organization administrator has
already revoked the underlying `LiteLLMConnection` row itself (a distinct,
Factory-level "missing grant" from the gateway's own 401/403). This task's
own goal ("fail closed on revoked/missing grants") is exactly that
composition: `require_model_access` is the one real gate a future job-
admission/execution call site is required to pass, in order, before ever
treating a model as callable:

1. **Ownership ("missing grant")** -- `authorize_resource_ownership`
   (FTF-H003, CERTIFIED) against `litellm_connections`, so a caller
   authenticated into the *wrong* organization (a validly authorized role
   there, holding or guessing another organization's real connection ID --
   exactly Q01/Q24's cross-tenant-guessed-ID scenario) is refused before
   this guard ever looks at the connection's own status or catalog.
2. **Connection-level revocation ("missing grant")** -- `litellm_
   connections.status`. FTF-0201's own schema (CERTIFIED) already models an
   administrator's explicit connection revocation as a soft `status=
   'revoked'` row, independent of anything the gateway itself reports; a
   caller whose in-memory catalog has not yet observed a fresh discovery
   cycle since that revocation (Section 3.3's own "disappearing catalog"
   risk) is still refused here, since this check reads the row fresh on
   every call rather than trusting the catalog's own possibly-stale
   `access_state`.
3. **Catalog-level authorization ("revoked" access / freshness / not
   callable)** -- `catalog.is_call_authorized`, unchanged, reusing FTF-0208's
   own already-CERTIFIED revocation/freshness/not-in-catalog/not-callable
   decision rather than re-implementing it.

This task's own negative_acceptance ("401/403 never invoke last-known-good
execution; retries of a disappearing catalog cannot silently restore
access") holds because every one of these three checks re-reads real,
current state on every call -- there is no cached "was authorized last
time" flag anywhere in this module for a retry to fall back on, and a
single `ModelAccessDeniedError` is raised (never a partial/optimistic
"probably fine") the instant any one of the three checks fails.

Checks run in the order above so a caller who does not even own the
connection never learns anything about that connection's revocation state
or its catalog contents from this guard's own behavior -- the same
"denied identically whether missing or foreign-tenant" discipline
`authorize_resource_ownership` itself already establishes, extended one
step further here (a caller who fails step 1 never reaches steps 2 or 3).

No independent schema change (this task's own `database_changes` clause):
this module issues ordinary `SELECT`s through FTF-0105/FTF-H003's own
already-CERTIFIED tenant-scoped guards against `litellm_connections`
(FTF-0201, CERTIFIED) -- it defines no new table and no new column.

Disclosed, not hidden (same precedent FTF-0202/FTF-0205/FTF-0207/FTF-0208/
FTF-0209/FTF-0215/FTF-H003 already established for "no live caller yet"):
no live job-admission, execution or route code anywhere in this repository
calls `require_model_access` yet -- there is no live job queue or execution
worker in this repository at all. `proving_symbol` stays blank and
`product_truth_check.py`'s product-true count is deliberately unchanged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum

from sqlalchemy import ColumnElement, FromClause
from sqlalchemy.orm import Session

from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from packages.litellm_client.last_known_good_catalog import (
    DEFAULT_FRESHNESS_TTL,
    AuthorizationReason,
    LastKnownGoodCatalog,
)

__all__ = ["ModelAccessDeniedError", "ModelAccessDenialReason", "require_model_access"]


class ModelAccessDenialReason(str, Enum):
    """Every way `require_model_access` can refuse -- always exactly one of
    these, never inferred by a caller from exception text."""

    CONNECTION_NOT_OWNED = "connection_not_owned"
    CONNECTION_REVOKED = "connection_revoked"
    ACCESS_REVOKED = "access_revoked"
    NO_SUCCESSFUL_DISCOVERY_YET = "no_successful_discovery_yet"
    FRESHNESS_EXPIRED = "freshness_expired"
    NOT_IN_CATALOG = "not_in_catalog"
    ENTRY_NOT_CALLABLE = "entry_not_callable"


_CATALOG_REASON_MAP: dict[AuthorizationReason, ModelAccessDenialReason] = {
    AuthorizationReason.ACCESS_REVOKED: ModelAccessDenialReason.ACCESS_REVOKED,
    AuthorizationReason.NO_SUCCESSFUL_DISCOVERY_YET: (
        ModelAccessDenialReason.NO_SUCCESSFUL_DISCOVERY_YET
    ),
    AuthorizationReason.FRESHNESS_EXPIRED: ModelAccessDenialReason.FRESHNESS_EXPIRED,
    AuthorizationReason.NOT_IN_CATALOG: ModelAccessDenialReason.NOT_IN_CATALOG,
    AuthorizationReason.ENTRY_NOT_CALLABLE: ModelAccessDenialReason.ENTRY_NOT_CALLABLE,
}


class ModelAccessDeniedError(PermissionError):
    """Raised by `require_model_access` for every denial. `reason` is a
    stable, machine-checkable identifier -- tests assert against it
    directly rather than parsing message prose. The message itself is
    content-free (no organization/connection/model identity), matching
    Section observability's "no secrets/raw prompts in task reports"."""

    def __init__(self, reason: ModelAccessDenialReason) -> None:
        self.reason = reason
        super().__init__(f"model access denied: {reason.value}")


def require_model_access(
    session: Session,
    context: TenantContext,
    connection_table: FromClause,
    connection_id_column: ColumnElement[uuid.UUID],
    connection_organization_id_column: ColumnElement[uuid.UUID],
    connection_status_column: ColumnElement[str],
    connection_id: uuid.UUID,
    catalog: LastKnownGoodCatalog,
    model_id: str,
    *,
    now: datetime | None = None,
    freshness_ttl: timedelta = DEFAULT_FRESHNESS_TTL,
) -> None:
    """Raise `ModelAccessDeniedError` unless `model_id` is genuinely
    callable, right now, for `connection_id` under `context`'s own
    authenticated tenant. Returns `None` (no value -- a guard, not a
    decision object) only when every one of the three checks in this
    module's own docstring passes."""
    try:
        authorize_resource_ownership(
            session,
            context,
            connection_table,
            connection_id_column,
            connection_organization_id_column,
            connection_id,
        )
    except ResourceAuthorizationError as exc:
        raise ModelAccessDeniedError(ModelAccessDenialReason.CONNECTION_NOT_OWNED) from exc

    status_statement = (
        connection_table.select()
        .with_only_columns(connection_status_column)
        .where(connection_id_column == connection_id)
    )
    status_row = execute_tenant_scoped(session, context.organization_id, status_statement).first()
    if status_row is None or status_row[0] != "active":
        raise ModelAccessDeniedError(ModelAccessDenialReason.CONNECTION_REVOKED)

    checked_at = now if now is not None else datetime.now(UTC)
    decision = catalog.is_call_authorized(model_id, now=checked_at, freshness_ttl=freshness_ttl)
    if not decision.authorized:
        raise ModelAccessDeniedError(_CATALOG_REASON_MAP[decision.reason])
