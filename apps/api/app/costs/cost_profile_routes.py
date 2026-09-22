"""Live `/api/v1/organizations/{organization_id}/cost-profiles` and
`/api/v1/organizations/{organization_id}/projects/{project_id}/costs/
cost-per-success/search` routes (FTF-1301, Section 28/31/32/45/64/69-70/
77/81) -- the eighth live-wired product-runtime path in this repository,
after FTF-0101's `/auth/*`, FTF-0201's `/connections/litellm`, FTF-0301's
`/artifacts`, FTF-0401's `/projects`, FTF-0404's `/tools`, FTF-0407's
`/requirements` and FTF-0403's `/budget-policy`.

Path shape: `cost-profiles` nested directly under `organizations` (an
organization-wide rate card, matching FTF-0201's own `/connections/
litellm` precedent for an org-scoped, non-project resource), while the
search endpoint is refined from the task packet's own abbreviated
`api_contract` (`"/api/v1/projects/{id}/costs/cost-per-success/search"`,
no `organization_id` segment) to include the real `{organization_id}`
path segment `require_role`'s own tenant resolution requires -- the same
documented path refinement FTF-0201's own `connection_routes.py` already
applied to its own packet's abbreviated `api_contract`.

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): this router implements
create/get/list/verify for a `CostProfile` rate-card entry, plus the real
search surface this task's own acceptance clause names. It does NOT
compute an actual cost-per-success figure against real usage data -- see
`cost_profile_schema.py`'s own module docstring for why that full
Section-32 pipeline belongs to FTF-1304, a later, separately
dependency-gated task.

Role gating: Section 45's own literal role table -- "organization_admin:
connections, security profiles, cost profiles" -- names cost profiles
explicitly. `COST_PROFILE_ROLES` is `{organization_admin}` alone, applied
to every route including reads and the search endpoint, the same
"admin-only, including reads" precedent FTF-0201's own `connection_
routes.py` already applied to "connections" (named in the identical
sentence).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
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
from app.costs.cost_profile_crud import (
    ActorInfo,
    CostProfileNotFoundError,
    ProjectNotFoundError,
    SearchResult,
    create_cost_profile,
    get_cost_profile,
    list_cost_profiles,
    search_cost_profiles,
    verify_cost_profile,
)
from app.costs.cost_profile_schema import (
    PROFILE_TYPES,
    RATE_UNITS,
    CostProfile,
    CostProfileValidationError,
)
from app.db.tenant_context import TenantContext, tenant_scoped_session_for

__all__ = ["COST_PROFILE_ROLES", "build_cost_profile_router"]

# Section 45: "organization_admin: connections, security profiles, cost
# profiles" is the only sentence naming a role for this resource -- every
# route here, including reads, is organization_admin-only.
COST_PROFILE_ROLES: frozenset[Role] = frozenset({Role.ORGANIZATION_ADMIN})

_COLLECTION_PATH = "/api/v1/organizations/{organization_id}/cost-profiles"
_ITEM_PATH = _COLLECTION_PATH + "/{cost_profile_id}"
_VERIFY_PATH = _ITEM_PATH + "/verify"
_SEARCH_PATH = (
    "/api/v1/organizations/{organization_id}/projects/{project_id}" "/costs/cost-per-success/search"
)

_DISPLAY_SCALE = Decimal("1").scaleb(-10)  # matches NUMERIC(24,10)


def _rate(value: Decimal) -> str:
    """Fixed-point, never scientific notation -- `str(Decimal)` switches to
    scientific notation for small magnitudes (this column's own real
    range, e.g. a $0.00000015-per-token rate), which `Decimal(str(...))`
    round-trips correctly but no API consumer should have to know that.
    `format(value, 'f')` always produces the same plain decimal shape
    `budget_policy_routes.py`'s own larger-magnitude `_money` already
    produces for its own column."""
    return format(value.quantize(_DISPLAY_SCALE), "f")


def _serialize(profile: CostProfile) -> dict[str, object]:
    return {
        "id": str(profile.id),
        "organization_id": str(profile.organization_id),
        "profile_type": profile.profile_type,
        "label": profile.label,
        "rate_unit": profile.rate_unit,
        "rate_amount": _rate(profile.rate_amount),
        "currency": profile.currency,
        "source": profile.source,
        "rate_recorded_at": profile.rate_recorded_at.isoformat(),
        "verified_at": profile.verified_at.isoformat() if profile.verified_at else None,
        "expires_at": profile.expires_at.isoformat() if profile.expires_at else None,
        "row_version": profile.row_version,
        "created_by": profile.created_by,
        "created_at": profile.created_at.isoformat(),
        "updated_at": profile.updated_at.isoformat(),
    }


def _serialize_search_result(result: SearchResult) -> dict[str, object]:
    body = _serialize(result.profile)
    body["certifiable_cheapest"] = result.certifiable_cheapest
    body["disqualified_reason"] = result.disqualified_reason
    return body


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


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} is required and must be a string")
    return value


def _require_decimal(payload: dict[str, Any], key: str) -> Decimal:
    """Money always arrives as a JSON string, never a bare number -- Section
    6's own "money uses Decimal + currency" rule, applied at the API
    boundary the same way `budget_policy_routes.py`'s own `_require_decimal`
    already established."""
    value = payload.get(key)
    if not isinstance(value, str):
        raise HTTPException(
            status_code=400, detail=f"{key} is required and must be a decimal string"
        )
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise HTTPException(status_code=400, detail=f"{key} is not a valid decimal") from exc


def _require_datetime(payload: dict[str, Any], key: str) -> datetime:
    value = payload.get(key)
    if not isinstance(value, str):
        raise HTTPException(
            status_code=400, detail=f"{key} is required and must be an ISO-8601 datetime string"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"{key} is not a valid ISO-8601 datetime"
        ) from exc
    if parsed.tzinfo is None:
        raise HTTPException(status_code=400, detail=f"{key} must carry an explicit timezone offset")
    return parsed


def _optional_datetime(payload: dict[str, Any], key: str) -> datetime | None:
    if key not in payload or payload[key] is None:
        return None
    return _require_datetime(payload, key)


def _optional_query_enum(request: Request, key: str, allowed: tuple[str, ...]) -> str | None:
    value = request.query_params.get(key)
    if value is None:
        return None
    if value not in allowed:
        raise HTTPException(status_code=400, detail=f"{key} must be one of {allowed}: {value!r}")
    return value


def build_cost_profile_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    """Real FastAPI router for FTF-1301's create/get/list/verify/search
    surface. `session_factory`/`principal_resolver`/`role_lookup` follow
    the exact same injection convention FTF-0201/FTF-0301/FTF-0401/
    FTF-0403/FTF-0404 already established -- fail-closed defaults until a
    real session-reader/DB-backed Membership lookup is wired in (same
    disclosed gap those tasks already carry)."""
    router = APIRouter()
    admin_dependency = Depends(
        require_role(
            COST_PROFILE_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    @router.post(_COLLECTION_PATH, status_code=201)
    async def create(
        request: Request,
        organization_id: uuid.UUID,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                profile = create_cost_profile(
                    session,
                    tenant_context,
                    profile_type=_require_str(payload, "profile_type"),
                    label=_require_str(payload, "label"),
                    rate_unit=_require_str(payload, "rate_unit"),
                    rate_amount=_require_decimal(payload, "rate_amount"),
                    currency=_require_str(payload, "currency"),
                    source=_require_str(payload, "source"),
                    rate_recorded_at=_require_datetime(payload, "rate_recorded_at"),
                    expires_at=_optional_datetime(payload, "expires_at"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(profile)
        except TenantContextError as exc:
            raise HTTPException(status_code=404, detail="organization_not_found") from exc
        except (ValueError, CostProfileValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=201)

    @router.get(_COLLECTION_PATH)
    async def list_one(
        request: Request,
        organization_id: uuid.UUID,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        del organization_id
        profile_type = _optional_query_enum(request, "profile_type", PROFILE_TYPES)
        rate_unit = _optional_query_enum(request, "rate_unit", RATE_UNITS)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                profiles = list_cost_profiles(
                    session, tenant_context, profile_type=profile_type, rate_unit=rate_unit
                )
                body = [_serialize(p) for p in profiles]
        except TenantContextError as exc:
            raise HTTPException(status_code=404, detail="organization_not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.get(_ITEM_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        cost_profile_id: uuid.UUID,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                profile = get_cost_profile(session, tenant_context, cost_profile_id)
                body = _serialize(profile)
        except (CostProfileNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.post(_VERIFY_PATH)
    async def verify(
        request: Request,
        organization_id: uuid.UUID,
        cost_profile_id: uuid.UUID,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                profile = verify_cost_profile(
                    session, tenant_context, cost_profile_id, actor=_actor_from(ctx, request)
                )
                body = _serialize(profile)
        except (CostProfileNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.get(_SEARCH_PATH)
    async def search(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        del organization_id
        profile_type = _optional_query_enum(request, "profile_type", PROFILE_TYPES)
        rate_unit = _optional_query_enum(request, "rate_unit", RATE_UNITS)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                results = search_cost_profiles(
                    session,
                    tenant_context,
                    project_id,
                    profile_type=profile_type,
                    rate_unit=rate_unit,
                )
                serialized = [_serialize_search_result(r) for r in results]
                # Computed inside the session block, never after -- once
                # `tenant_scoped_session_for` exits, every ORM instance it
                # loaded is detached and any lazy attribute access (e.g.
                # `.rate_amount` on a not-yet-loaded column) would raise
                # `DetachedInstanceError`.
                certified = [r for r in results if r.certifiable_cheapest]
                cheapest = (
                    min(certified, key=lambda r: r.profile.rate_amount) if certified else None
                )
                cheapest_body = _serialize(cheapest.profile) if cheapest else None
        except (ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc

        body = {
            "results": serialized,
            # "display unknowns honestly": null (never a fabricated
            # fallback number) when no rate in this organization's own
            # cost profile currently qualifies as certified-cheapest.
            "current_cheapest_certified": cheapest_body,
        }
        return JSONResponse(body, status_code=200)

    return router
