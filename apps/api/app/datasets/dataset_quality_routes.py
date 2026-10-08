"""Live routes for dataset quality reports, issues and resolutions (FTF-0516-P1,
Section 6.10/6.12/8.1 steps 16-18/8.2/9/39/67.4/69.1/77/78.4):

    GET  .../datasets/{dataset_id}/quality-report            latest report
    GET  .../datasets/{dataset_id}/issues                    issues (limit, after, filters)
    GET  .../datasets/{dataset_id}/issues/{issue_id}         one issue
    POST .../datasets/{dataset_id}/resolve-issue             decide 1..100 issues
    GET  .../datasets/{dataset_id}/issue-resolutions         decisions (limit)

Role gating is `DATASET_SOURCE_ROLES` (ml_engineer, workspace_admin,
organization_admin). A `held_out_evaluation` dataset is the same 404 for
`ml_engineer` on every route (Section 9/69.1). `waive` needs workspace_admin or
organization_admin (403 `waive_not_permitted`); `exclude`, `correct` and
`new_version` are open to ml_engineer. Errors are `{"detail": "<stable_code>"}`.

Responses carry digests, positions, indices and codes only: never record
content, a detector message or an object key. The write path accepts no
client-built issue or report; issues come from FTF-0515's engine through the
store's `record_quality_report`, which has no route.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from workers.data.quality_issue_engine import RESIDUAL_RISK_DISCLOSURE

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
from app.datasets.dataset_quality_store import (
    MAX_PAGE_SIZE,
    DatasetIssueResolution,
    DatasetNotFoundError,
    DatasetQualityReport,
    IssueView,
    QualityActor,
    QualityStoreError,
    ResolutionRequest,
    get_issue,
    get_latest_report,
    list_issues,
    list_resolutions,
    load_resolved_quality_report,
    submit_resolutions,
)
from app.datasets.dataset_source_connection_routes import DATASET_SOURCE_ROLES
from app.db.tenant_context import TenantContext, tenant_scoped_session_for

__all__ = ["build_dataset_quality_router"]

_BASE_PATH = "/api/v1/organizations/{organization_id}/projects/{project_id}/datasets/{dataset_id}"
_DEFAULT_LIMIT = 50
_MAX_BODY_BYTES = 1024 * 1024
_BODY_FIELDS = frozenset(
    {"quality_report_id", "expected_resolution_version", "idempotency_key", "resolutions"}
)
_REQUIRED_RESOLUTION_FIELDS = frozenset(
    {"issue_id", "action", "reason", "expected_before_evidence_sha256"}
)
_RESOLUTION_FIELDS = _REQUIRED_RESOLUTION_FIELDS | {
    "excluded_batch_positions",
    "successor_dataset_id",
}


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not_found")


def _stamp(value: Any) -> str:
    return value.isoformat()


def _serialize_resolution(row: DatasetIssueResolution) -> dict[str, object]:
    return {
        "id": str(row.id),
        "quality_report_id": str(row.quality_report_id),
        "issue_id": row.issue_id,
        "action": row.action,
        "engine_decision": row.engine_decision,
        "excluded_batch_positions": list(row.excluded_batch_positions),
        "successor_dataset_id": (
            None if row.successor_dataset_id is None else str(row.successor_dataset_id)
        ),
        "before_evidence_sha256": row.before_evidence_sha256,
        "after_evidence_sha256": row.after_evidence_sha256,
        "reason": row.reason,
        "actor_id": row.actor_id,
        "actor_role": row.actor_role,
        "resolution_version_after": row.resolution_version_after,
        "created_at": _stamp(row.created_at),
    }


def _serialize_issue(view: IssueView) -> dict[str, object]:
    issue = view.issue
    return {
        "issue_id": issue.issue_id,
        "report_id": str(issue.report_id),
        "detector": issue.detector,
        "category": issue.category,
        "severity": issue.severity,
        "reason_code": issue.reason_code,
        "message_sha256": issue.message_sha256,
        "batch_positions": list(issue.batch_positions),
        "record_indices": list(issue.record_indices),
        "evidence_digest": issue.evidence_digest,
        "is_detector_failure": issue.is_detector_failure,
        "evidence_sha256": issue.evidence_sha256,
        "state": view.state,
        "resolution": (None if view.resolution is None else _serialize_resolution(view.resolution)),
    }


def _serialize_report(report: DatasetQualityReport, blocks: bool, clean: bool) -> dict[str, object]:
    return {
        "id": str(report.id),
        "dataset_id": str(report.dataset_id),
        "dataset_version": report.dataset_version,
        "dataset_sha256": report.dataset_sha256,
        "report_seq": report.report_seq,
        "report_sha256": report.report_sha256,
        "detector_versions": dict(report.detector_versions),
        "detectors_reported": sorted(report.detectors_reported),
        "issue_count": report.issue_count,
        "blocking_count": report.blocking_count,
        "warning_count": report.warning_count,
        "resolution_version": report.resolution_version,
        "is_clean": clean,
        "blocks_split": blocks,
        "blocks_training": blocks,
        "residual_risk_disclosure": RESIDUAL_RISK_DISCLOSURE,
        "created_at": _stamp(report.created_at),
        "updated_at": _stamp(report.updated_at),
    }


def _may_see_held_out(ctx: AuthorizedContext) -> bool:
    # Section 9/69.1: only ml_engineer, among the roles allowed here, may not
    # read held-out evaluation data.
    return ctx.role is not Role.ML_ENGINEER


def _parse_limit(request: Request) -> int:
    raw = request.query_params.get("limit")
    if raw is None:
        return _DEFAULT_LIMIT
    try:
        limit = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_limit") from exc
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise HTTPException(status_code=400, detail="invalid_limit")
    return limit


def _hex64(raw: object) -> bool:
    return isinstance(raw, str) and len(raw) == 64 and all(c in "0123456789abcdef" for c in raw)


def _actor(ctx: AuthorizedContext) -> QualityActor:
    return QualityActor(
        actor_type="user",
        actor_id=ctx.principal.subject,
        role=ctx.role.value,
        request_id=uuid.uuid4().hex,
    )


def _invalid_body() -> HTTPException:
    return HTTPException(status_code=400, detail="invalid_body")


async def _read_body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="request_too_large")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise _invalid_body() from exc
    if not isinstance(payload, dict) or set(payload) != _BODY_FIELDS:
        raise _invalid_body()
    return payload


def _parse_uuid(raw: object) -> uuid.UUID:
    if not isinstance(raw, str):
        raise _invalid_body()
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise _invalid_body() from exc


def _parse_resolution(item: object) -> ResolutionRequest:
    if (
        not isinstance(item, dict)
        or not _REQUIRED_RESOLUTION_FIELDS <= set(item)
        or not set(item) <= _RESOLUTION_FIELDS
    ):
        raise _invalid_body()
    positions = item.get("excluded_batch_positions", [])
    if not isinstance(positions, list):
        raise _invalid_body()
    successor = item.get("successor_dataset_id")
    for field in ("issue_id", "action", "reason", "expected_before_evidence_sha256"):
        if not isinstance(item[field], str):
            raise _invalid_body()
    return ResolutionRequest(
        issue_id=item["issue_id"],
        action=item["action"],
        reason=item["reason"],
        expected_before_evidence_sha256=item["expected_before_evidence_sha256"],
        excluded_batch_positions=tuple(positions),
        successor_dataset_id=None if successor is None else _parse_uuid(successor),
    )


def build_dataset_quality_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    router = APIRouter()
    dependency = Depends(
        require_role(
            DATASET_SOURCE_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    @router.get(_BASE_PATH + "/quality-report")
    async def read_report(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                report = get_latest_report(
                    session,
                    tenant_context,
                    project_id=project_id,
                    dataset_id=dataset_id,
                    include_held_out=_may_see_held_out(ctx),
                )
                engine_report = load_resolved_quality_report(
                    session,
                    tenant_context,
                    project_id=project_id,
                    dataset_id=dataset_id,
                    include_held_out=_may_see_held_out(ctx),
                )
                body = _serialize_report(report, engine_report.blocks_split, engine_report.is_clean)
        except (DatasetNotFoundError, TenantContextError) as exc:
            raise _not_found() from exc
        except QualityStoreError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from exc
        return JSONResponse(body)

    @router.get(_BASE_PATH + "/issues")
    async def read_issues(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        limit = _parse_limit(request)
        after = request.query_params.get("after")
        if after is not None and not _hex64(after):
            raise HTTPException(status_code=400, detail="invalid_after")
        filters: dict[str, str | None] = {
            name: request.query_params.get(name)
            for name in ("severity", "state", "detector", "category")
        }
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                views = list_issues(
                    session,
                    tenant_context,
                    project_id=project_id,
                    dataset_id=dataset_id,
                    include_held_out=_may_see_held_out(ctx),
                    limit=limit,
                    after=after,
                    **filters,
                )
                items = [_serialize_issue(view) for view in views]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid_filter") from exc
        except (DatasetNotFoundError, TenantContextError) as exc:
            raise _not_found() from exc
        except QualityStoreError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from exc
        next_after = items[-1]["issue_id"] if len(items) == limit else None
        return JSONResponse({"items": items, "next_after": next_after})

    @router.get(_BASE_PATH + "/issues/{issue_id}")
    async def read_issue(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        issue_id: str,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                view = get_issue(
                    session,
                    tenant_context,
                    project_id=project_id,
                    dataset_id=dataset_id,
                    issue_id=issue_id,
                    include_held_out=_may_see_held_out(ctx),
                )
                body = _serialize_issue(view)
        except (DatasetNotFoundError, TenantContextError) as exc:
            raise _not_found() from exc
        except QualityStoreError as exc:
            # get_issue refuses only with a 404 (no report / no such issue).
            raise _not_found() from exc
        return JSONResponse(body)

    @router.post(_BASE_PATH + "/resolve-issue")
    async def resolve(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_body(request)
        report_id = _parse_uuid(payload["quality_report_id"])
        raw_items = payload["resolutions"]
        if not isinstance(raw_items, list):
            raise _invalid_body()
        requests = [_parse_resolution(item) for item in raw_items]
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                outcome = submit_resolutions(
                    session,
                    tenant_context,
                    project_id=project_id,
                    dataset_id=dataset_id,
                    quality_report_id=report_id,
                    expected_resolution_version=payload["expected_resolution_version"],
                    idempotency_key=payload["idempotency_key"],
                    resolutions=requests,
                    include_held_out=_may_see_held_out(ctx),
                    actor=_actor(ctx),
                )
                body = {
                    "quality_report_id": str(outcome.report.id),
                    "resolution_version": outcome.report.resolution_version,
                    "replayed": outcome.replayed,
                    "items": [_serialize_resolution(row) for row in outcome.resolutions],
                }
        except (DatasetNotFoundError, TenantContextError) as exc:
            raise _not_found() from exc
        except QualityStoreError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from exc
        return JSONResponse(body, status_code=200 if outcome.replayed else 201)

    @router.get(_BASE_PATH + "/issue-resolutions")
    async def read_resolutions(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        limit = _parse_limit(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                rows = list_resolutions(
                    session,
                    tenant_context,
                    project_id=project_id,
                    dataset_id=dataset_id,
                    include_held_out=_may_see_held_out(ctx),
                    limit=limit,
                )
                items = [_serialize_resolution(row) for row in rows]
        except (DatasetNotFoundError, TenantContextError) as exc:
            raise _not_found() from exc
        return JSONResponse({"items": items})

    return router
