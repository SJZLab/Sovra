"""Live `.../model-versions/{model_version_id}/roles` and `/actions` routes (FTF-H010-P4;
Section 0.12 roles, Section 17 paragraphs 3-4, Section 27, Section 58A "Allowed next operations",
Section 68.1 paragraph 4 "Actions are capability driven ... Invalid actions return precise reason
and server-side denial, not just disabled buttons").

- `GET  .../roles` -- every assignment of the version (active and revoked).
- `POST .../roles` -- assign a role. The body is exactly `{role, idempotency_key}`.
- `POST .../roles/{role}/revoke` -- revoke the active assignment (no body fields).
- `GET  .../actions` -- the decision `workers/training/continuation.py` makes for every action,
  built from facts the server read itself.

Role gating (Section 45 / 0.12): `candidate`, `baseline` and `teacher` use
`MODEL_VERSION_WRITE_ROLES`; `current_production` and `judge` are `organization_admin` only. A judge
role does not make a qualified judge (FTF-0616 calibration and the FTF-0617 policy still decide) and
`current_production` records the incumbent only -- it deploys or routes nothing (FTF-10xx).

Error mapping: 400 `invalid_body` | `invalid_role`; 401; 403; 404 `not_found` (identical for a
foreign and a missing id); 409 `role_already_assigned` | `incumbent_exists` |
`self_judging_role_conflict` | `role_not_assigned` | `idempotency_key_reused` |
`import_not_validated`. Error bodies carry the code only.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from workers.training.continuation import (
    ACTIONS,
    ActionDecision,
    ContinuationFactsError,
    evaluate_model_actions,
)

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
from app.db.tenant_context import TenantContext, tenant_scoped_session_for
from app.model_versions.model_role_crud import (
    RoleConflictError,
    assign_role,
    build_action_facts,
    list_roles,
    revoke_role,
)
from app.model_versions.model_role_schema import (
    ADMIN_ONLY_ROLES,
    ModelRoleAssignment,
    ModelRoleValidationError,
    validate_idempotency_key,
    validate_role,
)
from app.model_versions.model_version_crud import (
    ActorInfo,
    ModelVersionNotFoundError,
    ProjectNotFoundError,
    get_model_version,
)
from app.model_versions.model_version_routes import (
    MODEL_VERSION_READ_ROLES,
    MODEL_VERSION_WRITE_ROLES,
)

__all__ = ["build_model_role_router"]

_VERSION_PATH = (
    "/api/v1/organizations/{organization_id}/projects/{project_id}"
    "/model-versions/{model_version_id}"
)
_ROLES_PATH = _VERSION_PATH + "/roles"
_REVOKE_PATH = _ROLES_PATH + "/{role}/revoke"
_ACTIONS_PATH = _VERSION_PATH + "/actions"

_POST_BODY_KEYS = frozenset({"role", "idempotency_key"})


def _serialize(assignment: ModelRoleAssignment) -> dict[str, object]:
    return {
        "id": str(assignment.id),
        "organization_id": str(assignment.organization_id),
        "project_id": str(assignment.project_id),
        "model_version_id": str(assignment.model_version_id),
        "role": assignment.role,
        "assigned_by": assignment.assigned_by,
        "assigned_at": assignment.assigned_at.isoformat(),
        "idempotency_key": assignment.idempotency_key,
        "revoked_at": None if assignment.revoked_at is None else assignment.revoked_at.isoformat(),
        "revoked_by": assignment.revoked_by,
        "revoke_reason_code": assignment.revoke_reason_code,
    }


def _serialize_decision(decision: ActionDecision) -> dict[str, object]:
    plan = decision.plan
    return {
        "allowed": decision.allowed,
        "code": decision.code,
        "plan": None if plan is None else dict(vars(plan)),
    }


def _actor_from(ctx: AuthorizedContext) -> ActorInfo:
    return ActorInfo(actor_type="user", actor_id=ctx.principal.subject, request_id=uuid.uuid4().hex)


async def _read_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 - any unparsable body is the same outcome
        raise HTTPException(status_code=400, detail="invalid_body") from exc
    if type(payload) is not dict:
        raise HTTPException(status_code=400, detail="invalid_body")
    return payload


def _require_role_allowed(ctx: AuthorizedContext, role: str) -> None:
    if role in ADMIN_ONLY_ROLES and ctx.role is not Role.ORGANIZATION_ADMIN:
        raise HTTPException(status_code=403, detail="forbidden")


def build_model_role_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    """Real FastAPI router for FTF-H010-P4's role and action surface. Injection follows every
    prior router in this chain: fail-closed defaults until a real session reader and Membership
    lookup are wired in."""
    router = APIRouter()
    read_dependency = Depends(
        require_role(
            MODEL_VERSION_READ_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    write_dependency = Depends(
        require_role(
            MODEL_VERSION_WRITE_ROLES,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
        )
    )

    @router.get(_ROLES_PATH)
    async def list_assignments(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        model_version_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                rows = list_roles(session, tenant_context, project_id, model_version_id)
                body = [_serialize(row) for row in rows]
        except (ModelVersionNotFoundError, ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.post(_ROLES_PATH)
    async def assign(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        model_version_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_object(request)
        if set(payload) != _POST_BODY_KEYS:
            raise HTTPException(status_code=400, detail="invalid_body")
        try:
            idempotency_key = validate_idempotency_key(payload["idempotency_key"])
            if type(payload["role"]) is not str:
                raise ModelRoleValidationError("invalid_body")
            role = validate_role(payload["role"])
        except ModelRoleValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.code) from None
        _require_role_allowed(ctx, role)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                result = assign_role(
                    session,
                    tenant_context,
                    project_id=project_id,
                    model_version_id=model_version_id,
                    role=role,
                    idempotency_key=idempotency_key,
                    actor=_actor_from(ctx),
                )
                body = _serialize(result.assignment)
        except (ModelVersionNotFoundError, ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except RoleConflictError as exc:
            raise HTTPException(status_code=409, detail=exc.code) from None
        return JSONResponse(body, status_code=200 if result.replayed else 201)

    @router.post(_REVOKE_PATH)
    async def revoke(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        model_version_id: uuid.UUID,
        role: str,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        raw = await request.body()
        if raw.strip() and await _read_object(request) != {}:
            raise HTTPException(status_code=400, detail="invalid_body")
        try:
            checked_role = validate_role(role)
        except ModelRoleValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.code) from None
        _require_role_allowed(ctx, checked_role)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                assignment = revoke_role(
                    session,
                    tenant_context,
                    project_id=project_id,
                    model_version_id=model_version_id,
                    role=checked_role,
                    actor=_actor_from(ctx),
                )
                body = _serialize(assignment)
        except (ModelVersionNotFoundError, ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except RoleConflictError as exc:
            raise HTTPException(status_code=409, detail=exc.code) from None
        return JSONResponse(body, status_code=200)

    @router.get(_ACTIONS_PATH)
    async def actions(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        model_version_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                version = get_model_version(session, tenant_context, project_id, model_version_id)
                facts = build_action_facts(session, tenant_context, version)
                decisions = evaluate_model_actions(facts)
                body: dict[str, object] = {
                    "model_version_id": str(version.id),
                    "origin": facts.origin,
                    "validated": facts.validated,
                    "roles": sorted(facts.roles),
                    "actions": {name: _serialize_decision(decisions[name]) for name in ACTIONS},
                }
        except (ModelVersionNotFoundError, ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except ContinuationFactsError as exc:
            raise HTTPException(status_code=409, detail="model_facts_invalid") from exc
        return JSONResponse(body, status_code=200)

    return router
