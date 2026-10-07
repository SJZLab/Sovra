"""RBAC middleware (FTF-0103): server-side role/tenant enforcement for
`/api/v1` mutations (Section 45/65.1).

The real enforcement point any future `/api/v1` route wires through
`require_role(...)`. Every authorization decision is made from data the
server itself controls -- the authenticated session's own issuer+subject
(Section 65.3: "account identity is issuer+subject", never email) and a
role looked up by `(issuer, subject, organization_id)` -- never from a
client-supplied field. This task's own negative_acceptance clause names
two bypass vectors; both are denied by construction, not by convention:

  - a guessed/foreign organization UUID in the URL: `role_lookup` returns
    `None` for any `(issuer, subject, organization_id)` that is not a
    real, active Membership row, so no role is ever granted for an
    organization the caller does not actually belong to, however the
    UUID was obtained;
  - a body-selected tenant: the organization a request is authorized
    *against* always comes from the URL path, never the JSON body. If a
    mutating request's own body also carries an `organization_id` (or
    another configured tenant field) that disagrees with the path, the
    request is denied outright -- the body is never trusted to select or
    override the enforced tenant, even when the path's own tenant would
    otherwise have been permitted.

Disclosed, not hidden gaps (same class already flagged by FTF-0101's own
module docstring and its `default_membership_lookup`):
  - no `Membership` table exists anywhere in this repository yet
    (FTF-0102 delivered only Organization/Workspace; this task's own
    `database_changes` contract forbids an independent schema change --
    "No independent schema change. Consume frozen contract; any required
    change is a separately claimed migration prerequisite"). `default_role_lookup`
    below is fail-closed -- it denies every request until a real
    DB-backed lookup replaces it -- proven fail-closed by test, not
    hidden.
  - no live `/api/v1` product route exists anywhere in this repository
    yet (`apps/api/app/main.py` only wires FTF-0101's `/auth/*` router)
    -- the same disclosed "no live caller yet" class already accepted for
    FTF-0005/FTF-0006/FTF-0102/FTF-0104 before each one's own first live
    caller existed. This module is proven end to end against a real,
    locally-hosted demonstration FastAPI app wired with `require_role`
    (tests/tasks/test_ftf_0103.py, real uvicorn server over a real local
    socket -- the same pattern tests/tasks/test_ftf_0101.py established),
    not yet against a real product route.
  - session authentication itself is injected via `principal_resolver`,
    decoupled from FTF-0101's own concrete session-cookie implementation
    (`app.auth.oidc_login`) so that module's already-certified evidence is
    never put at risk by this task's own changes. Wiring a real resolver
    backed by `oidc_login.read_session_cookie` is the same class of
    "separately claimed" follow-up as the DB-backed role lookup above.
  - role/tenant scoping here is organization-level only. Section 6.4's
    `Membership.workspace_id` (nullable, for workspace-scoped roles) and
    the `Project` entity (Section 6.8) do not exist as live tables yet
    either; `RoleLookup`'s signature is deliberately narrow (issuer,
    subject, organization_id) rather than guessing a wider shape that
    would have to be revisited once those tables exist.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from fastapi import HTTPException, Request

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Largest body `require_role` will read to cross-check the body tenant: the largest existing
# route-level cap (32 MiB). Lower route-level caps still apply to the route's own read.
MAX_JSON_BODY_BYTES = 33554432


class Role(str, Enum):
    """Section 6.4 `Membership.role` -- the exact allowed role set."""

    ORGANIZATION_ADMIN = "organization_admin"
    WORKSPACE_ADMIN = "workspace_admin"
    ML_ENGINEER = "ml_engineer"
    EVALUATOR = "evaluator"
    APPROVER = "approver"
    VIEWER = "viewer"


ALL_ROLES: frozenset[Role] = frozenset(Role)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Who the request is genuinely authenticated as. Issuer+subject is
    account identity (Section 65.3) -- never email, which is not identity."""

    issuer: str
    subject: str


PrincipalResolver = Callable[[Request], AuthenticatedPrincipal | None]
RoleLookup = Callable[[str, str, uuid.UUID], Role | None]


def default_principal_resolver(request: Request) -> AuthenticatedPrincipal | None:
    """Fail-closed: no live session reader is wired in yet (see module
    docstring). Every request is denied as unauthenticated until a real
    resolver backed by a live session store replaces this one."""
    return None


def default_role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
    """Fail-closed: no live Membership table exists yet (see module
    docstring). Every request is denied until a real DB-backed lookup
    replaces this one."""
    return None


class TenantResolutionError(ValueError):
    """The request's own path does not carry a well-formed organization ID."""


def organization_id_from_path(
    param_name: str = "organization_id",
) -> Callable[[Request], uuid.UUID]:
    """Returns a callable that reads the resource's organization ID
    strictly from the URL path -- never the JSON body -- so a body field
    can never select which tenant a request is authorized against (this
    task's own negative_acceptance clause)."""

    def _resolve(request: Request) -> uuid.UUID:
        raw = request.path_params.get(param_name)
        if raw is None:
            raise TenantResolutionError(f"path has no {param_name!r} parameter")
        try:
            return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
        except (ValueError, AttributeError, TypeError) as exc:
            raise TenantResolutionError(f"{param_name} is not a well-formed UUID: {raw!r}") from exc

    return _resolve


@dataclass(frozen=True)
class AuthorizedContext:
    """What a protected route handler actually receives once authorized --
    never re-derived from the request body downstream."""

    principal: AuthenticatedPrincipal
    organization_id: uuid.UUID
    role: Role


class _NoBodyTenant:
    """Sentinel: the body carries no tenant field at all, so there is
    nothing to cross-check against the path (distinct from a present but
    malformed/mismatching value, which is an active mismatch, not a
    pass-through)."""


_NO_BODY_TENANT = _NoBodyTenant()


def _extract_body_tenant_id(payload: object, field_name: str) -> uuid.UUID | _NoBodyTenant | None:
    if not isinstance(payload, dict) or field_name not in payload:
        return _NO_BODY_TENANT
    raw = payload[field_name]
    try:
        return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        # Present but unparsable: still an active mismatch, never ignored.
        return None


async def _read_capped_body(request: Request, oversize: tuple[int, str]) -> None:
    """Reads the request body into Starlette's own body cache, refusing more than
    `MAX_JSON_BODY_BYTES`. A declared Content-Length over the cap is refused
    without reading; a chunked body (no Content-Length) is stopped at the cap
    while streaming. The cache is filled so a route handler can still read it."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isascii() and declared.isdigit():
        if int(declared) > MAX_JSON_BODY_BYTES:
            raise HTTPException(status_code=oversize[0], detail=oversize[1])
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_JSON_BODY_BYTES:
            raise HTTPException(status_code=oversize[0], detail=oversize[1])
        chunks.append(chunk)
    request._body = b"".join(chunks)  # noqa: SLF001 - Starlette's documented body cache


async def _read_json_body(request: Request, oversize: tuple[int, str]) -> object:
    await _read_capped_body(request, oversize)
    try:
        return await request.json()
    except Exception:  # noqa: BLE001 - any unparsable/empty body means "no body tenant"
        return None


def require_role(
    allowed_roles: frozenset[Role] | set[Role],
    *,
    organization_id_source: Callable[[Request], uuid.UUID] | None = None,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
    body_tenant_field: str | None = "organization_id",
    oversize_error: tuple[int, str] = (413, "request_too_large"),
) -> Callable[[Request], Awaitable[AuthorizedContext]]:
    """Returns a FastAPI dependency enforcing `allowed_roles`, scoped to the
    organization named in the URL path, for one route/operation -- this
    task's own acceptance clause 1 ("every route/operation maps to
    explicit role and scoped project policy on server"). `oversize_error` is the
    (status, detail) returned when the body exceeds `MAX_JSON_BODY_BYTES`; a route
    with its own published error contract for that case passes it here."""
    allowed = frozenset(allowed_roles)
    if not allowed:
        raise ValueError("require_role needs at least one allowed role")
    resolve_organization_id = organization_id_source or organization_id_from_path()

    async def _dependency(request: Request) -> AuthorizedContext:
        principal = principal_resolver(request)
        if principal is None:
            raise HTTPException(status_code=401, detail="not_authenticated")

        try:
            organization_id = resolve_organization_id(request)
        except TenantResolutionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if body_tenant_field is not None and request.method in _MUTATING_METHODS:
            body = await _read_json_body(request, oversize_error)
            body_tenant = _extract_body_tenant_id(body, body_tenant_field)
            if body_tenant is not _NO_BODY_TENANT and body_tenant != organization_id:
                raise HTTPException(status_code=403, detail="tenant_mismatch")

        role = role_lookup(principal.issuer, principal.subject, organization_id)
        if role is None or role not in allowed:
            raise HTTPException(status_code=403, detail="role_not_authorized")

        return AuthorizedContext(principal=principal, organization_id=organization_id, role=role)

    return _dependency
