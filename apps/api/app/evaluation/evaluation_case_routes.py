"""Live `.../projects/{project_id}/evaluation-suites/{suite_id}/cases` routes
(FTF-0602-P1, Section 6.14/23/24/26/27/39/65/69.1/69.2/77/78.2/81).

    POST   .../cases              all-or-nothing import of 1..500 cases -> 201 {items}
    GET    .../cases              ?limit=1..500&after=<case_key> -> {items, next_after}
    GET    .../cases/{case_id}    one case
    PATCH  .../cases/{case_id}    edit fields (case_key is immutable)

Roles are FTF-0601's own `EVALUATION_SUITE_READ_ROLES` /
`EVALUATION_SUITE_WRITE_ROLES`, unchanged. On top of them, the content of a
*final*-purpose suite is limited to evaluator / workspace_admin /
organization_admin (`final_suite_content_restricted`, 403, no content).

Errors are `{"detail": "<stable_code>"}`: 401, 403 (`final_suite_content_restricted`),
404 `not_found` (identical for a missing, foreign-tenant or wrong-project
suite and for a missing case), 409 `suite_locked` | `duplicate_case_key`. A 400
adds `errors: [{index, field, code}]` -- the row, the field and a stable code,
never the submitted value. A JSON body is capped at 32 MiB; each of the three
JSON payloads of a case at 2 MiB.

This module never computes `suite_sha256`, runs a grader, or parses an uploaded
file: the client sends JSON rows.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth.rbac_middleware import (
    AuthorizedContext,
    PrincipalResolver,
    RoleLookup,
    default_principal_resolver,
    default_role_lookup,
    require_role,
)
from app.auth.tenant_isolation_query_guards import TenantContextError
from app.db.tenant_context import TenantContext, tenant_scoped_session_for
from app.evaluation.evaluation_case_store import (
    CASE_KEY_MAX_LENGTH,
    MAX_BATCH_SIZE,
    CaseFieldError,
    CaseNotFoundError,
    DuplicateCaseKeyError,
    EvaluationCaseRecord,
    EvaluationCaseValidationError,
    FinalSuiteContentRestrictedError,
    SuiteLockedError,
    SuiteNotFoundError,
    ValidatedCase,
    get_case,
    import_cases,
    list_cases,
    update_case,
    validate_case_input,
    validate_case_patch,
)
from app.evaluation.evaluation_suite_routes import (
    EVALUATION_SUITE_READ_ROLES,
    EVALUATION_SUITE_WRITE_ROLES,
)

__all__ = ["MAX_BODY_BYTES", "build_evaluation_case_router"]

MAX_BODY_BYTES = 32 * 1024 * 1024
DEFAULT_PAGE_SIZE = 100

_COLLECTION_PATH = (
    "/api/v1/organizations/{organization_id}/projects/{project_id}"
    "/evaluation-suites/{suite_id}/cases"
)
_ITEM_PATH = _COLLECTION_PATH + "/{case_id}"


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not_found")


def _bad_request(errors: list[CaseFieldError], detail: str = "validation_failed") -> JSONResponse:
    return JSONResponse(
        {
            "detail": detail,
            "errors": [{"index": e.index, "field": e.field, "code": e.code} for e in errors],
        },
        status_code=400,
    )


def _serialize(case: EvaluationCaseRecord) -> dict[str, object]:
    return {
        "id": str(case.id),
        "organization_id": str(case.organization_id),
        "project_id": str(case.project_id),
        "suite_id": str(case.suite_id),
        "case_key": case.case_key,
        "category": case.category,
        "severity": case.severity,
        "critical": case.critical,
        "language": case.language,
        "group_key": case.group_key,
        "input_json": case.input_json,
        "expected_json": case.expected_json,
        "grader_type": case.grader_type,
        "grader_config_json": case.grader_config_json,
        "weight": case.weight,
        "created_by": case.created_by,
        "created_at": case.created_at.isoformat(),
        "updated_at": case.updated_at.isoformat(),
    }


class _BodyError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite JSON constant {name}")


async def _read_json(request: Request) -> object:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise _BodyError("body_too_large")
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks), parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        raise _BodyError("invalid_json") from exc


async def _reject_oversized_body(request: Request) -> None:
    """Runs before `require_role`, whose body-tenant cross-check reads a
    mutating request's whole body: a declared oversized body is refused before
    anything buffers it."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=400, detail="body_too_large")


def _body_failure(error: _BodyError) -> JSONResponse:
    return _bad_request([CaseFieldError(None, None, error.code)], detail="invalid_request")


def build_evaluation_case_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    """Injection conventions match `build_evaluation_suite_router`: fail-closed
    defaults until a real principal resolver / role lookup is wired in."""
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
            oversize_error=(400, "body_too_large"),
        )
    )

    @router.post(_COLLECTION_PATH, status_code=201)
    async def import_batch(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        suite_id: uuid.UUID,
        _size_guard: None = Depends(_reject_oversized_body),
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        try:
            payload = await _read_json(request)
        except _BodyError as exc:
            return _body_failure(exc)
        if not isinstance(payload, dict):
            return _bad_request([CaseFieldError(None, None, "invalid_type")], "invalid_request")
        if set(payload) != {"cases"}:
            return _bad_request([CaseFieldError(None, None, "unknown_field")], "invalid_request")
        rows = payload["cases"]
        if not isinstance(rows, list):
            return _bad_request([CaseFieldError(None, "cases", "invalid_type")], "invalid_request")
        if not 1 <= len(rows) <= MAX_BATCH_SIZE:
            return _bad_request(
                [CaseFieldError(None, "cases", "count_out_of_range")], "invalid_request"
            )
        cases: list[ValidatedCase] = []
        errors: list[CaseFieldError] = []
        for index, row in enumerate(rows):
            case, row_errors = validate_case_input(row, index)
            errors.extend(row_errors)
            if case is not None:
                cases.append(case)
        if errors:
            return _bad_request(errors)

        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                records = import_cases(
                    session,
                    tenant_context,
                    project_id=project_id,
                    suite_id=suite_id,
                    role=ctx.role,
                    cases=cases,
                    actor_type="user",
                    actor_id=ctx.principal.subject,
                    request_id=uuid.uuid4().hex,
                )
                body = {"items": [_serialize(record) for record in records]}
        except (SuiteNotFoundError, TenantContextError) as exc:
            raise _not_found() from exc
        except FinalSuiteContentRestrictedError as exc:
            raise HTTPException(status_code=403, detail="final_suite_content_restricted") from exc
        except SuiteLockedError as exc:
            raise HTTPException(status_code=409, detail="suite_locked") from exc
        except DuplicateCaseKeyError as exc:
            raise HTTPException(status_code=409, detail="duplicate_case_key") from exc
        return JSONResponse(body, status_code=201)

    @router.get(_COLLECTION_PATH)
    async def list_page(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        suite_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        limit = DEFAULT_PAGE_SIZE
        errors: list[CaseFieldError] = []
        raw_limit = request.query_params.get("limit")
        if raw_limit is not None:
            if (
                raw_limit.isascii()
                and raw_limit.isdigit()
                and 1 <= int(raw_limit) <= MAX_BATCH_SIZE
            ):
                limit = int(raw_limit)
            else:
                errors.append(CaseFieldError(None, "limit", "invalid_value"))
        after = request.query_params.get("after")
        if after is not None and (not after or len(after) > CASE_KEY_MAX_LENGTH or "\x00" in after):
            errors.append(CaseFieldError(None, "after", "invalid_value"))
        if errors:
            return _bad_request(errors, "invalid_query")

        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                records, next_after = list_cases(
                    session,
                    tenant_context,
                    project_id=project_id,
                    suite_id=suite_id,
                    role=ctx.role,
                    limit=limit,
                    after=after,
                )
                body = {
                    "items": [_serialize(record) for record in records],
                    "next_after": next_after,
                }
        except (SuiteNotFoundError, TenantContextError) as exc:
            raise _not_found() from exc
        except FinalSuiteContentRestrictedError as exc:
            raise HTTPException(status_code=403, detail="final_suite_content_restricted") from exc
        return JSONResponse(body)

    @router.get(_ITEM_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        suite_id: uuid.UUID,
        case_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                record = get_case(
                    session,
                    tenant_context,
                    project_id=project_id,
                    suite_id=suite_id,
                    case_id=case_id,
                    role=ctx.role,
                )
                body = _serialize(record)
        except (SuiteNotFoundError, CaseNotFoundError, TenantContextError) as exc:
            raise _not_found() from exc
        except FinalSuiteContentRestrictedError as exc:
            raise HTTPException(status_code=403, detail="final_suite_content_restricted") from exc
        return JSONResponse(body)

    @router.patch(_ITEM_PATH)
    async def patch_one(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        suite_id: uuid.UUID,
        case_id: uuid.UUID,
        _size_guard: None = Depends(_reject_oversized_body),
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        try:
            payload = await _read_json(request)
        except _BodyError as exc:
            return _body_failure(exc)
        patch, errors = validate_case_patch(payload)
        if errors:
            return _bad_request(errors)

        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                record = update_case(
                    session,
                    tenant_context,
                    project_id=project_id,
                    suite_id=suite_id,
                    case_id=case_id,
                    role=ctx.role,
                    patch=patch,
                    actor_type="user",
                    actor_id=ctx.principal.subject,
                    request_id=uuid.uuid4().hex,
                )
                body = _serialize(record)
        except (SuiteNotFoundError, CaseNotFoundError, TenantContextError) as exc:
            raise _not_found() from exc
        except FinalSuiteContentRestrictedError as exc:
            raise HTTPException(status_code=403, detail="final_suite_content_restricted") from exc
        except SuiteLockedError as exc:
            raise HTTPException(status_code=409, detail="suite_locked") from exc
        except EvaluationCaseValidationError as exc:
            return _bad_request(exc.errors)
        return JSONResponse(body)

    return router
