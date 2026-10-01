"""Live `/api/v1/organizations/{organization_id}/projects/{project_id}/
evaluation-suites` routes (FTF-0601, Section 23-30/69/45/65-66/77/81).

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry and the schema module's own
docstring): this router implements create/get/list/update/lock for the
`EvaluationSuite` record itself. It deliberately does not implement
`/cases` (Section 23-24's own evaluation-case authoring, FTF-0602
"Evaluation case editor/import," a separate, dependency-gated later task
that names this one as *its own* dependency), `/evaluations/results`
(Section 25's own real runtime-execution loop) or `/comparisons` (Section
28's own baseline-comparison surface) -- none of those entities/
capabilities exists anywhere in this repository yet. Declaring any of
them as a real, enforced endpoint here without a real backing
implementation would be exactly the "placeholder endpoint" Law 11
forbids.

Path shape: directly under `/projects/{project_id}` (a suite's real,
primary parent -- the same "resource's real owning parent, read literally
off its own field list" precedent FTF-1001's own `/model-versions` and
FTF-04A19's own `/manifests` both already applied.

Role gating: see `evaluation_suite_crud.py`'s own module docstring for the
full Section 45 reasoning -- reads are open to every role Section 45
names, writes (create/update/lock) are restricted to `{evaluator,
workspace_admin, organization_admin}`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

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
from app.evaluation.evaluation_suite_crud import (
    ActorInfo,
    EvaluationSuiteIncompleteError,
    EvaluationSuiteLockedError,
    EvaluationSuiteManifestMismatchError,
    EvaluationSuiteManifestNotFoundError,
    EvaluationSuiteManifestNotLockedError,
    EvaluationSuiteNotFoundError,
    ProjectNotFoundError,
    create_evaluation_suite,
    get_evaluation_suite,
    list_evaluation_suites,
    lock_evaluation_suite,
    update_evaluation_suite,
)
from app.evaluation.evaluation_suite_schema import EvaluationSuite, EvaluationSuiteValidationError

__all__ = [
    "EVALUATION_SUITE_READ_ROLES",
    "EVALUATION_SUITE_WRITE_ROLES",
    "build_evaluation_suite_router",
]

# Section 45: "evaluator: create/run evaluation, no deployment" is a real,
# specific sentence for exactly this class of resource -- see
# `evaluation_suite_crud.py`'s own module docstring.
EVALUATION_SUITE_WRITE_ROLES: frozenset[Role] = frozenset(
    {Role.EVALUATOR, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}
)
EVALUATION_SUITE_READ_ROLES: frozenset[Role] = frozenset(
    {
        Role.ORGANIZATION_ADMIN,
        Role.WORKSPACE_ADMIN,
        Role.ML_ENGINEER,
        Role.EVALUATOR,
        Role.APPROVER,
        Role.VIEWER,
    }
)

_COLLECTION_PATH = "/api/v1/organizations/{organization_id}/projects/{project_id}/evaluation-suites"
_ITEM_PATH = _COLLECTION_PATH + "/{suite_id}"
_LOCK_PATH = _ITEM_PATH + "/lock"


def _serialize(suite: EvaluationSuite) -> dict[str, object]:
    return {
        "id": str(suite.id),
        "organization_id": str(suite.organization_id),
        "project_id": str(suite.project_id),
        "agent_manifest_id": str(suite.agent_manifest_id),
        "version": suite.version,
        "status": suite.status,
        "purpose": suite.purpose,
        "name": suite.name,
        "grading_logic_version": suite.grading_logic_version,
        "reviewer_id": suite.reviewer_id,
        "created_by": suite.created_by,
        "locked_at": suite.locked_at.isoformat() if suite.locked_at else None,
        "created_at": suite.created_at.isoformat(),
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


async def _read_json_body_or_empty(request: Request) -> dict[str, Any]:
    body = await request.body()
    if not body:
        return {}
    return await _read_json_body(request)


def _require_uuid(payload: dict[str, Any], key: str) -> uuid.UUID:
    value = payload.get(key)
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} is required and must be a string")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{key} must be a valid UUID") from exc


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise HTTPException(
            status_code=400, detail=f"{key} is required and must be a non-empty string"
        )
    return value


def _require_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(status_code=400, detail=f"{key} is required and must be an integer")
    return value


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    if key not in payload:
        return None
    value = payload[key]
    if value is not None and not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} must be a string or null")
    return value


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    if key not in payload:
        return None
    value = payload[key]
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise HTTPException(status_code=400, detail=f"{key} must be an integer or null")
    return value


def build_evaluation_suite_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    """Real FastAPI router for FTF-0601's create/get/list/update/lock
    surface.

    `session_factory`/`principal_resolver`/`role_lookup` follow the exact
    same injection convention every prior router in this chain
    established -- fail-closed defaults until a real session-reader/
    DB-backed Membership lookup is wired in (same disclosed gap FTF-0103/
    FTF-0106/FTF-0201/FTF-0301/FTF-0401/FTF-0404/FTF-1001/FTF-04A01/
    FTF-04A07/FTF-04A19/FTF-04A20 already carry).
    """
    router = APIRouter()
    read_dependency = Depends(
        require_role(
            EVALUATION_SUITE_READ_ROLES,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
        )
    )
    write_dependency = Depends(
        require_role(
            EVALUATION_SUITE_WRITE_ROLES,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
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
                suite = create_evaluation_suite(
                    session,
                    tenant_context,
                    project_id=project_id,
                    agent_manifest_id=_require_uuid(payload, "agent_manifest_id"),
                    purpose=_require_str(payload, "purpose"),
                    name=_require_str(payload, "name"),
                    grading_logic_version=_require_int(payload, "grading_logic_version"),
                    reviewer_id=_optional_str(payload, "reviewer_id"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(suite)
        except (
            ProjectNotFoundError,
            EvaluationSuiteManifestNotFoundError,
            TenantContextError,
        ) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except (EvaluationSuiteManifestMismatchError, EvaluationSuiteManifestNotLockedError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, EvaluationSuiteValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=201)

    @router.get(_COLLECTION_PATH)
    async def list_all(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                suites = list_evaluation_suites(session, tenant_context, project_id)
                body = [_serialize(s) for s in suites]
        except (ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.get(_ITEM_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        suite_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                suite = get_evaluation_suite(session, tenant_context, project_id, suite_id)
                body = _serialize(suite)
        except (EvaluationSuiteNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.patch(_ITEM_PATH)
    async def update(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        suite_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        unknown = set(payload) - {"name", "grading_logic_version", "reviewer_id"}
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown field(s): {sorted(unknown)}")
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                suite = update_evaluation_suite(
                    session,
                    tenant_context,
                    project_id=project_id,
                    suite_id=suite_id,
                    name=_optional_str(payload, "name"),
                    grading_logic_version=_optional_int(payload, "grading_logic_version"),
                    reviewer_id=_optional_str(payload, "reviewer_id"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(suite)
        except (EvaluationSuiteNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except EvaluationSuiteLockedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, EvaluationSuiteValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    @router.post(_LOCK_PATH)
    async def lock_endpoint(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        suite_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        await _read_json_body_or_empty(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                suite = lock_evaluation_suite(
                    session,
                    tenant_context,
                    project_id=project_id,
                    suite_id=suite_id,
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(suite)
        except (EvaluationSuiteNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except (EvaluationSuiteLockedError, EvaluationSuiteIncompleteError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    return router
