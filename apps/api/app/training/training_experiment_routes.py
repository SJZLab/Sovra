"""Training experiment routes (FTF-0701-P3; Sections 6.15, 14.1-14.2, 15, 16 steps 1-9, 65.1).

Exactly three operations, declared in `packages/contracts/openapi/07-interface.yaml`:

- `POST /api/v1/organizations/{organization_id}/projects/{project_id}/training/experiments`
  saves one version of a typed training configuration (201; 200 for an idempotent replay).
- `GET  .../training/experiments` lists the project's experiments (name, then version).
- `GET  .../training/experiments/{experiment_id}` returns one.

Every route sits behind `require_role` (FTF-0104): the organization comes from the path, the role
from the caller's membership of that organization. Writers are the ML engineer and the
organization admin; every role may read. All domain work is
`app.training.training_experiment_store`; a refusal is `{"detail": code}` with the store's own
status, and a missing id, an id of another project and an id of another organization are the same
404 `not_found`.

A response carries ids, hashes and the resolved configuration only: no dataset row, record id,
grant snapshot, idempotency key or request digest.

No other phase-07 route is declared or served here (`/training/estimate`, `/training/runs/...`
belong to their own tasks).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC
from decimal import Decimal
from typing import Any, Final

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth.rbac_middleware import (
    ALL_ROLES,
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
from app.training.training_experiment_store import (
    MAX_PAGE_SIZE,
    NAME_MAX_LENGTH,
    ExperimentActor,
    TrainingExperiment,
    TrainingExperimentError,
    create_training_experiment,
    get_training_experiment,
    list_training_experiments,
)

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "EXPERIMENTS_PATH",
    "EXPERIMENT_PATH",
    "MAX_BODY_BYTES",
    "READ_ROLES",
    "WRITE_ROLES",
    "build_training_experiment_router",
]

EXPERIMENTS_PATH: Final = (
    "/api/v1/organizations/{organization_id}/projects/{project_id}/training/experiments"
)
EXPERIMENT_PATH: Final = EXPERIMENTS_PATH + "/{experiment_id}"
MAX_BODY_BYTES: Final = 64 * 1024
DEFAULT_PAGE_SIZE: Final = 50
WRITE_ROLES: Final = frozenset({Role.ML_ENGINEER, Role.ORGANIZATION_ADMIN})
READ_ROLES: Final = ALL_ROLES
_BODY_KEYS: Final = frozenset({"name", "idempotency_key", "config"})
_QUERY_KEYS: Final = frozenset({"limit", "after_name", "after_version"})
_MAX_VERSION: Final = 2**31 - 1
_COST_QUANTUM: Final = Decimal("0.0001")


def _invalid_body() -> HTTPException:
    return HTTPException(status_code=400, detail="invalid_body")


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not_found")


def _actor(ctx: AuthorizedContext) -> ExperimentActor:
    return ExperimentActor(
        actor_type="user",
        actor_id=ctx.principal.subject,
        role=ctx.role.value,
        request_id=uuid.uuid4().hex,
    )


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


def _reject_constant(_name: str) -> Any:
    raise ValueError("non-finite number")


async def _read_body(request: Request) -> dict[str, Any]:
    """Exactly `{name, idempotency_key, config}` in at most 64 KiB of strict JSON (no duplicate
    key, no NaN/Infinity). Anything else is 400 `invalid_body`."""
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise _invalid_body()
    try:
        payload = json.loads(
            raw, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant
        )
    except (ValueError, RecursionError):
        raise _invalid_body() from None
    if type(payload) is not dict or set(payload) != _BODY_KEYS:
        raise _invalid_body()
    return payload


def _page(request: Request) -> tuple[int, tuple[str, int] | None]:
    """`limit` (1..200, default 50) and the `after_name` + `after_version` cursor, given together
    or not at all. A repeated, unknown or malformed query parameter is 400 `invalid_body`."""
    params = request.query_params
    keys = [key for key, _ in params.multi_items()]
    if len(keys) != len(set(keys)) or not set(keys) <= _QUERY_KEYS:
        raise _invalid_body()
    limit = DEFAULT_PAGE_SIZE
    raw_limit = params.get("limit")
    if raw_limit is not None:
        limit = _decimal(raw_limit, 1, MAX_PAGE_SIZE)
    name, raw_version = params.get("after_name"), params.get("after_version")
    if name is None and raw_version is None:
        return limit, None
    if name is None or raw_version is None or not 1 <= len(name) <= NAME_MAX_LENGTH:
        raise _invalid_body()
    return limit, (name, _decimal(raw_version, 1, _MAX_VERSION))


def _decimal(raw: str, low: int, high: int) -> int:
    # canonical decimal digits only: no sign, no leading zero (every lower bound here is >= 1)
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 10 or raw.startswith("0"):
        raise _invalid_body()
    value = int(raw)
    if not low <= value <= high:
        raise _invalid_body()
    return value


def _serialize(experiment: TrainingExperiment) -> dict[str, Any]:
    entry = experiment.model_catalog_entry_id
    return {
        "id": str(experiment.id),
        "organization_id": str(experiment.organization_id),
        "project_id": str(experiment.project_id),
        "name": experiment.name,
        "version": experiment.version,
        "parent_version": experiment.parent_version,
        "status": experiment.status,
        "method": experiment.method,
        "config_schema_version": experiment.config_schema_version,
        "config": experiment.config_json,
        "config_sha256": experiment.config_sha256,
        "trainable_artifact_id": str(experiment.trainable_artifact_id),
        "model_catalog_entry_id": None if entry is None else str(entry),
        "split_manifest_id": str(experiment.split_manifest_id),
        "frozen_manifest_sha256": experiment.frozen_manifest_sha256,
        "budget_policy_id": str(experiment.budget_policy_id),
        # NUMERIC(18,4): the same four-decimal text whether the row was just built or read back.
        "max_cost": format(experiment.max_cost.quantize(_COST_QUANTUM), "f"),
        "currency": experiment.currency,
        "security_profile": experiment.security_profile,
        "requested_by": experiment.requested_by,
        "created_at": experiment.created_at.astimezone(UTC).isoformat(),
    }


def build_training_experiment_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    router = APIRouter()
    read_dependency = Depends(
        require_role(READ_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup)
    )
    write_dependency = Depends(
        require_role(
            WRITE_ROLES,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
            oversize_error=(400, "invalid_body"),
        )
    )

    @router.post(EXPERIMENTS_PATH)
    async def create_experiment(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id  # the organization is the authorized context's, resolved from the path
        payload = await _read_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                created = create_training_experiment(
                    session,
                    tenant_context,
                    project_id,
                    name=payload["name"],
                    config_body=payload["config"],
                    idempotency_key=payload["idempotency_key"],
                    actor=_actor(ctx),
                )
                body = _serialize(created.experiment)
                replayed = created.replayed
        except TenantContextError:
            raise _not_found() from None
        except TrainingExperimentError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from None
        return JSONResponse(body, status_code=200 if replayed else 201)

    @router.get(EXPERIMENTS_PATH)
    async def list_experiments(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        limit, after = _page(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                rows = list_training_experiments(
                    session, tenant_context, project_id, limit=limit, after=after
                )
                body = {"experiments": [_serialize(row) for row in rows]}
        except TenantContextError:
            raise _not_found() from None
        except TrainingExperimentError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from None
        return JSONResponse(body)

    @router.get(EXPERIMENT_PATH)
    async def get_experiment(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        experiment_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                experiment = get_training_experiment(
                    session, tenant_context, project_id, experiment_id
                )
                body = _serialize(experiment)
        except TenantContextError:
            raise _not_found() from None
        except TrainingExperimentError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from None
        return JSONResponse(body)

    return router
