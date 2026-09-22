"""Live `/api/v1/organizations/{organization_id}/projects/{project_id}/
budget-policy` routes (FTF-0403, Section 6.8/7A/22/32/65-66/70.3/77/81) --
the seventh live-wired product-runtime path in this repository, after
FTF-0101's `/auth/*`, FTF-0201's `/connections/litellm`, FTF-0301's
`/artifacts`, FTF-0401's `/projects`, FTF-0404's `/tools` and FTF-0407's
`/requirements`.

Path shape: nested under a specific project, matching FTF-0404's own
`/tools` precedent -- a `BudgetPolicy` genuinely belongs to one project
for its entire lifetime.

Scope (disclosed explicitly, not hidden): this router implements
create/get/revise plus the real `reserve`/`release` allocation primitive
and `history` (this task's own "approval history" acceptance clause) for
a project-scoped `BudgetPolicy`. It does not implement delete/archive --
`budget_policy_schema.py`'s own module docstring explains why a budget
policy is never removed, only revised in place.

Role gating: see `budget_policy_crud.py`'s own module docstring for the
full Section 45 reasoning -- reads (`get`/`history`) are open to every
role Section 45 names, writes (`create`/`revise`/`reserve`/`release`) are
restricted to `{workspace_admin, organization_admin}`. Reserve/release are
treated as writes, not a separate, wider-open role class: they mutate a
real financial-authorization row exactly as much as a revision does.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth.audit_event_writer import AuditEvent
from app.auth.rbac_middleware import (
    AuthorizedContext,
    PrincipalResolver,
    Role,
    RoleLookup,
    default_principal_resolver,
    default_role_lookup,
    require_role,
)
from app.auth.tenant_isolation_query_guards import TenantContextError
from app.budget.budget_policy_crud import (
    ActorInfo,
    BudgetPolicyInsufficientReservationError,
    BudgetPolicyNotFoundError,
    BudgetPolicyScopeError,
    BudgetPolicyStateError,
    ProjectNotFoundError,
    create_budget_policy,
    get_budget_policy,
    list_budget_policy_approval_history,
    release_budget_allocation,
    reserve_budget_allocation,
    revise_budget_policy,
)
from app.budget.budget_policy_crud import BudgetPolicyConcurrencyError as ConcurrencyError
from app.budget.budget_policy_schema import BudgetPolicy, BudgetPolicyValidationError
from app.db.tenant_context import TenantContext, tenant_scoped_session_for

__all__ = ["BUDGET_READ_ROLES", "BUDGET_WRITE_ROLES", "build_budget_policy_router"]

# Section 45's own literal role table -- every role it names, applied to
# this resource's reads (no dedicated restrictive sentence exists for
# budget authorization, matching FTF-0401/FTF-0404's own precedent).
BUDGET_READ_ROLES: frozenset[Role] = frozenset(
    {
        Role.ORGANIZATION_ADMIN,
        Role.WORKSPACE_ADMIN,
        Role.ML_ENGINEER,
        Role.EVALUATOR,
        Role.APPROVER,
        Role.VIEWER,
    }
)
# Section 45: "workspace_admin: project administration" is the only
# sentence naming a role for this class of per-project configuration
# work -- the same precedent FTF-0401/FTF-0404 applied.
BUDGET_WRITE_ROLES: frozenset[Role] = frozenset({Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN})

_COLLECTION_PATH = "/api/v1/organizations/{organization_id}/projects/{project_id}/budget-policy"
_RESERVE_PATH = _COLLECTION_PATH + "/reserve"
_RELEASE_PATH = _COLLECTION_PATH + "/release"
_HISTORY_PATH = _COLLECTION_PATH + "/history"


_DISPLAY_SCALE = Decimal("1").scaleb(-4)  # 0.0001 -- matches NUMERIC(18,4)


def _money(value: Decimal) -> str:
    """Quantizes to the real column's own scale before stringifying, so
    the serialized shape is identical whether `value` was just set in
    Python (e.g. `revise`, never re-fetched) or read back from a real
    `UPDATE ... RETURNING` (e.g. `reserve`/`release`) -- an API consumer
    must never be able to tell those two internal code paths apart from
    the response shape alone."""
    return str(value.quantize(_DISPLAY_SCALE))


def _serialize(policy: BudgetPolicy) -> dict[str, object]:
    return {
        "id": str(policy.id),
        "organization_id": str(policy.organization_id),
        "project_id": str(policy.project_id),
        "total_amount": _money(policy.total_amount),
        "currency": policy.currency,
        "scopes": list(policy.scopes_json),
        "reserved_amount": _money(policy.reserved_amount),
        "available_amount": _money(policy.total_amount - policy.reserved_amount),
        "row_version": policy.row_version,
        "created_by": policy.created_by,
        "created_at": policy.created_at.isoformat(),
        "updated_at": policy.updated_at.isoformat(),
    }


def _serialize_history_entry(event: AuditEvent) -> dict[str, object]:
    return {
        "id": str(event.id),
        "action": event.action,
        "actor_id": event.actor_id,
        "details": dict(event.details_json or {}),
        "created_at": event.created_at.isoformat(),
    }


def _actor_from(ctx: AuthorizedContext, request: Request) -> ActorInfo:
    del request
    return ActorInfo(
        actor_type="user",
        actor_id=ctx.principal.subject,
        request_id=uuid.uuid4().hex,
    )


async def _read_json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 - any unparsable body is the same outcome
        raise HTTPException(status_code=400, detail="request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    return payload


def _require_decimal(payload: dict[str, Any], key: str) -> Decimal:
    """Money always arrives as a JSON string, never a bare number -- a
    JSON float would already have lost precision before this module ever
    sees it (Section 6's own "money uses Decimal + currency" rule, read
    at the API boundary)."""
    value = payload.get(key)
    if not isinstance(value, str):
        raise HTTPException(
            status_code=400, detail=f"{key} is required and must be a decimal string"
        )
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise HTTPException(status_code=400, detail=f"{key} is not a valid decimal") from exc


def _optional_decimal(payload: dict[str, Any], key: str) -> Decimal | None:
    if key not in payload or payload[key] is None:
        return None
    if not isinstance(payload[key], str):
        raise HTTPException(status_code=400, detail=f"{key} must be a decimal string or null")
    try:
        return Decimal(payload[key])
    except InvalidOperation as exc:
        raise HTTPException(status_code=400, detail=f"{key} is not a valid decimal") from exc


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} is required and must be a string")
    return value


def _require_scopes(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail=f"{key} is required and must be a list")
    return value


def _optional_scopes(payload: dict[str, Any], key: str) -> list[str] | None:
    if key not in payload or payload[key] is None:
        return None
    if not isinstance(payload[key], list):
        raise HTTPException(status_code=400, detail=f"{key} must be a list or null")
    return payload[key]


def _require_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{key} is required and must be an integer")
    return value


def build_budget_policy_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    """Real FastAPI router for FTF-0403's create/get/revise/reserve/
    release/history surface. `session_factory`/`principal_resolver`/
    `role_lookup` follow the exact same injection convention FTF-0201/
    FTF-0301/FTF-0401/FTF-0404 already established -- fail-closed
    defaults until a real session-reader/DB-backed Membership lookup is
    wired in (same disclosed gap those tasks already carry)."""
    router = APIRouter()
    read_dependency = Depends(
        require_role(
            BUDGET_READ_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    write_dependency = Depends(
        require_role(
            BUDGET_WRITE_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    @router.post(_COLLECTION_PATH, status_code=201)
    async def create(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                policy = create_budget_policy(
                    session,
                    tenant_context,
                    project_id=project_id,
                    total_amount=_require_decimal(payload, "total_amount"),
                    currency=_require_str(payload, "currency"),
                    scopes=_require_scopes(payload, "scopes"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(policy)
        except (ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        except BudgetPolicyStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, BudgetPolicyValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=201)

    @router.get(_COLLECTION_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                policy = get_budget_policy(session, tenant_context, project_id)
                body = _serialize(policy)
        except (BudgetPolicyNotFoundError, ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.patch(_COLLECTION_PATH)
    async def revise(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                policy = revise_budget_policy(
                    session,
                    tenant_context,
                    project_id,
                    total_amount=_optional_decimal(payload, "total_amount"),
                    scopes=_optional_scopes(payload, "scopes"),
                    expected_row_version=_require_int(payload, "expected_row_version"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(policy)
        except (BudgetPolicyNotFoundError, ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except (BudgetPolicyStateError, ConcurrencyError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, BudgetPolicyValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    @router.post(_RESERVE_PATH)
    async def reserve(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                policy = reserve_budget_allocation(
                    session,
                    tenant_context,
                    project_id,
                    amount=_require_decimal(payload, "amount"),
                    scope=_require_str(payload, "scope"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(policy)
        except (BudgetPolicyNotFoundError, ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except BudgetPolicyInsufficientReservationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BudgetPolicyScopeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (ValueError, BudgetPolicyValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    @router.post(_RELEASE_PATH)
    async def release(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                policy = release_budget_allocation(
                    session,
                    tenant_context,
                    project_id,
                    amount=_require_decimal(payload, "amount"),
                    scope=_require_str(payload, "scope"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(policy)
        except (BudgetPolicyNotFoundError, ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except BudgetPolicyInsufficientReservationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, BudgetPolicyValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    @router.get(_HISTORY_PATH)
    async def history(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                events = list_budget_policy_approval_history(session, tenant_context, project_id)
                body = [_serialize_history_entry(e) for e in events]
        except (BudgetPolicyNotFoundError, ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    return router
