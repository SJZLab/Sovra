"""Live routes for immutable dataset split manifests (FTF-0519-P2,
Section 6.10-6.12/7/7A.10/8.1 steps 13/16-18/8.2/9/10/39/65/67.2/67.4/77/81):

    GET  .../dataset-splits                          manifests (limit, after)
    GET  .../dataset-splits/{manifest_id}            one manifest: roles, sources, hashes
    POST .../dataset-splits/{manifest_id}/approve    lock a proposed manifest
    POST .../dataset-splits/{manifest_id}/reject     reject a proposed manifest

Reads are open to `DATASET_SOURCE_ROLES` (ml_engineer, workspace_admin,
organization_admin). Approve and reject need workspace_admin or
organization_admin (an ml_engineer is a 403). Errors are `{"detail": "<code>"}`.

There is no create route: the records live in object storage and a proposal comes
from the data worker through `propose_split_manifest`.

A response is built from an explicit allow-list of fields. It carries counts,
role hashes, sources, the frozen grants, status and hashes, and a finding-count
`leakage_summary`; never an assignment, a record id, a group id, a finding, a
review text or a storage key. The store's ORM row (whose `leakage_report` holds
finding record ids, final-qualification ids included) is never serialized.

`Idempotency-Key` is required on both POST routes. The request fingerprint binds
the action, the manifest, the expected proposal hash and the reviews (order
insensitive). The key is stored with that digest in the transaction that locks or
rejects the manifest (`dataset_split_manifest_requests`): the same key with the
same digest returns the stored outcome (200, `replayed: true`) and writes nothing;
the same key with another digest is 409 `idempotency_key_reused`. A request that
already took effect under another key is still recognised from the manifest row
itself (approve: same proposal hash, same reviews, same approver; reject: the
rejected row and the expected proposal hash).

A caller that may not read held-out data (ml_engineer) sees no source whose
dataset purpose is `held_out_evaluation` (the certified dataset and quality routes
hide those datasets too) and the final role row carries only its record count and
role hash.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
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
from app.auth.tenant_isolation_query_guards import TenantContextError, execute_tenant_scoped
from app.datasets.dataset_source_connection_routes import DATASET_SOURCE_ROLES
from app.datasets.dataset_split_manifest_store import (
    DatasetSplitManifest,
    ManifestSummary,
    ManifestView,
    ReviewInput,
    SplitActor,
    SplitManifestError,
    approve_split_manifest,
    find_split_request,
    get_split_manifest,
    list_split_manifests,
    record_split_request,
    reject_split_manifest,
)
from app.db.tenant_context import TenantContext, tenant_scoped_session_for

__all__ = ["build_dataset_split_manifest_router"]

_BASE_PATH = "/api/v1/organizations/{organization_id}/projects/{project_id}/dataset-splits"
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
_MAX_BODY_BYTES = 1024 * 1024
_MAX_REVIEWS = 200
_MAX_RATIONALE = 2000
_MAX_FINDING_ID = 400
_KEY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{8,200}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_APPROVE_FIELDS = frozenset({"expected_proposal_sha256", "leakage_reviews"})
_REJECT_FIELDS = frozenset({"expected_proposal_sha256"})
_REVIEW_FIELDS = frozenset({"finding_id", "rationale"})
_WRITE_ROLES: frozenset[Role] = frozenset({Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN})
_HELD_OUT_PURPOSE = "held_out_evaluation"
_FINAL_ROLE = "final_qualification"

_VIEW_FIELDS = (
    "id",
    "organization_id",
    "project_id",
    "name",
    "version",
    "parent_version",
    "strategy",
    "seed",
    "ratios",
    "cutoff",
    "normalization_version",
    "tokenizer_identity",
    "split_manifest_sha256",
    "proposal_sha256",
    "leakage_report_sha256",
    "leakage_status",
    "reviewed_leakage_report_sha256",
    "migrated_from_legacy_sha256",
    "status",
    "frozen_manifest_sha256",
    "approved_by",
    "locked_at",
    "created_by",
    "created_at",
    "updated_at",
)
_UUID_FIELDS = frozenset({"id", "organization_id", "project_id"})


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not_found")


def _invalid_body() -> HTTPException:
    return HTTPException(status_code=400, detail="invalid_body")


def _scalar(name: str, value: Any) -> Any:
    if value is None:
        return None
    if name in _UUID_FIELDS:
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return list(value)
    return value


def _serialize_view(view: ManifestView) -> dict[str, Any]:
    body = {name: _scalar(name, view.values[name]) for name in _VIEW_FIELDS}
    body["leakage_summary"] = json.loads(json.dumps(dict(view.leakage_summary)))
    return body


def _may_see_held_out(ctx: AuthorizedContext) -> bool:
    # Section 9/69.1: among the roles allowed on this surface only ml_engineer may not read
    # held-out evaluation data (the certified dataset and quality routes use the same rule).
    return ctx.role is not Role.ML_ENGINEER


def _serialize_role(r: Any, include_held_out: bool) -> dict[str, Any]:
    row = {
        "role": r.role,
        "purpose": r.purpose,
        "record_count": r.record_count,
        "group_count": r.group_count,
        "role_sha256": r.role_sha256,
    }
    if not include_held_out and r.role == _FINAL_ROLE:
        del row["group_count"]
    return row


def _serialize_summary(summary: ManifestSummary, include_held_out: bool) -> dict[str, Any]:
    body = _serialize_view(summary.manifest)
    body["roles"] = [_serialize_role(r, include_held_out) for r in summary.roles]
    body["sources"] = [
        {
            "dataset_id": str(s.dataset_id),
            "dataset_name": s.dataset_name,
            "dataset_purpose": s.dataset_purpose,
            "dataset_version": s.dataset_version,
            "dataset_sha256": s.dataset_sha256,
            "dataset_source_connection_id": str(s.dataset_source_connection_id),
            "allowed_purposes_snapshot": list(s.allowed_purposes_snapshot),
            "quality_report_id": str(s.quality_report_id),
            "quality_report_sha256": s.quality_report_sha256,
            "excluded_positions_sha256": s.excluded_positions_sha256,
        }
        for s in summary.sources
        if include_held_out or s.dataset_purpose != _HELD_OUT_PURPOSE
    ]
    return body


def _parse_limit(request: Request) -> int:
    raw = request.query_params.get("limit")
    if raw is None:
        return _DEFAULT_LIMIT
    if not raw.isascii() or not raw.isdecimal():
        raise HTTPException(status_code=400, detail="invalid_limit")
    limit = int(raw)
    if not 1 <= limit <= _MAX_LIMIT:
        raise HTTPException(status_code=400, detail="invalid_limit")
    return limit


def _parse_after(request: Request) -> uuid.UUID | None:
    raw = request.query_params.get("after")
    if raw is None:
        return None
    try:
        parsed = uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_after") from exc
    if str(parsed) != raw:
        raise HTTPException(status_code=400, detail="invalid_after")
    return parsed


def _idempotency_key(request: Request) -> str:
    key = request.headers.get("idempotency-key")
    if key is None or _KEY_PATTERN.fullmatch(key) is None:
        raise HTTPException(status_code=400, detail="invalid_idempotency_key")
    return key


async def _read_body(request: Request, fields: frozenset[str]) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="request_too_large")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise _invalid_body() from exc
    if not isinstance(payload, dict) or set(payload) != fields:
        raise _invalid_body()
    expected = payload["expected_proposal_sha256"]
    if type(expected) is not str or _HEX64.fullmatch(expected) is None:
        raise _invalid_body()
    return payload


def _parse_reviews(raw: object) -> tuple[ReviewInput, ...]:
    if not isinstance(raw, list) or len(raw) > _MAX_REVIEWS:
        raise _invalid_body()
    reviews: list[ReviewInput] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != _REVIEW_FIELDS:
            raise _invalid_body()
        finding_id, rationale = item["finding_id"], item["rationale"]
        if (
            type(finding_id) is not str
            or not 1 <= len(finding_id) <= _MAX_FINDING_ID
            or type(rationale) is not str
            or not 1 <= len(rationale) <= _MAX_RATIONALE
        ):
            raise _invalid_body()
        reviews.append(ReviewInput(finding_id=finding_id, rationale=rationale))
    return tuple(reviews)


def _fingerprint(
    action: str,
    manifest_id: uuid.UUID,
    expected_proposal_sha256: str,
    reviews: list[tuple[str, str]],
) -> str:
    """Digest of the request (not the key); the reviews are order-insensitive."""
    body = json.dumps(
        {
            "action": action,
            "manifest_id": str(manifest_id),
            "expected_proposal_sha256": expected_proposal_sha256,
            "reviews": sorted(reviews),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _stored_reviews(
    session: Session, tenant: TenantContext, project_id: uuid.UUID, manifest_id: uuid.UUID
) -> list[tuple[str, str]] | None:
    """The reviews of a locked manifest as (finding_id, rationale). None when the stored shape is
    not the expected one (then the request is not treated as a replay)."""
    stored = (
        execute_tenant_scoped(
            session,
            tenant.organization_id,
            select(DatasetSplitManifest.leakage_reviews).where(
                DatasetSplitManifest.id == manifest_id,
                DatasetSplitManifest.project_id == project_id,
            ),
        )
        .scalars()
        .first()
    )
    if not isinstance(stored, list):
        return None
    out: list[tuple[str, str]] = []
    for review in stored:
        if (
            not isinstance(review, dict)
            or type(review.get("finding_id")) is not str
            or type(review.get("rationale")) is not str
        ):
            return None
        out.append((review["finding_id"], review["rationale"]))
    return out


def _actor(ctx: AuthorizedContext) -> SplitActor:
    return SplitActor(
        actor_type="user",
        actor_id=ctx.principal.subject,
        role=ctx.role.value,
        request_id=uuid.uuid4().hex,
    )


def _refusal(exc: SplitManifestError) -> HTTPException:
    if exc.code == "manifest_not_found":
        return _not_found()
    if exc.code == "actor_not_permitted":
        return HTTPException(status_code=403, detail="actor_not_permitted")
    if exc.code in ("invalid_review", "invalid_request"):
        return HTTPException(status_code=400, detail=exc.code)
    # Every other code is a state or hash conflict (leakage_not_clear, manifest_changed,
    # manifest_not_proposed, manifest_insufficient, manifest_tampered, ...).
    return HTTPException(status_code=409, detail=exc.code)


def build_dataset_split_manifest_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    router = APIRouter()
    read_dependency = Depends(
        require_role(
            DATASET_SOURCE_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    write_dependency = Depends(
        require_role(_WRITE_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup)
    )

    @router.get(_BASE_PATH)
    async def list_manifests(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        limit = _parse_limit(request)
        after = _parse_after(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                views = list_split_manifests(
                    session, tenant_context, project_id=project_id, limit=limit, after=after
                )
                items = [_serialize_view(view) for view in views]
        except TenantContextError as exc:
            raise _not_found() from exc
        next_after = items[-1]["id"] if len(items) == limit else None
        return JSONResponse({"items": items, "next_after": next_after})

    @router.get(_BASE_PATH + "/{manifest_id}")
    async def read_manifest(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        manifest_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                summary = get_split_manifest(
                    session, tenant_context, project_id=project_id, manifest_id=manifest_id
                )
                body = _serialize_summary(summary, _may_see_held_out(ctx))
        except (SplitManifestError, TenantContextError) as exc:
            raise _not_found() from exc
        return JSONResponse(body)

    def _stored_outcome(
        tenant_context: TenantContext,
        project_id: uuid.UUID,
        manifest_id: uuid.UUID,
        key: str,
        digest: str,
        include_held_out: bool,
    ) -> dict[str, Any] | None:
        """The outcome stored under this Idempotency-Key: the manifest as it is now when the key
        was used by this very request, None when the key is unused, and 409
        `idempotency_key_reused` when it was used by any other request. Read-only."""
        with tenant_scoped_session_for(session_factory, tenant_context) as session:
            stored = find_split_request(
                session, tenant_context, project_id=project_id, idempotency_key=key
            )
            if stored is None:
                return None
            # The digest binds the action, the manifest, the expected hash and the reviews.
            if not hmac.compare_digest(stored.request_digest, digest):
                raise SplitManifestError("idempotency_key_reused")
            return _serialize_summary(
                get_split_manifest(
                    session, tenant_context, project_id=project_id, manifest_id=manifest_id
                ),
                include_held_out,
            )

    def _replayed(
        tenant_context: TenantContext,
        project_id: uuid.UUID,
        manifest_id: uuid.UUID,
        action: str,
        expected: str,
        reviews: list[tuple[str, str]],
        subject: str,
        include_held_out: bool,
    ) -> dict[str, Any] | None:
        """The stored outcome when the manifest is already in the state this exact request
        produces (a request that took effect under another key); None otherwise. Read-only."""
        with tenant_scoped_session_for(session_factory, tenant_context) as session:
            summary = get_split_manifest(
                session, tenant_context, project_id=project_id, manifest_id=manifest_id
            )
            view = summary.manifest
            wanted = _fingerprint(action, manifest_id, expected, reviews)
            if action == "approve":
                if view.status != "locked" or view.approved_by != subject:
                    return None
                stored = _stored_reviews(session, tenant_context, project_id, manifest_id)
                if stored is None:
                    return None
                have = _fingerprint(action, manifest_id, view.proposal_sha256, stored)
            else:
                if view.status != "rejected":
                    return None
                have = _fingerprint(action, manifest_id, view.proposal_sha256, [])
            if not hmac.compare_digest(wanted, have):
                return None
            return _serialize_summary(summary, include_held_out)

    def _replay_or_none(
        tenant_context: TenantContext,
        project_id: uuid.UUID,
        manifest_id: uuid.UUID,
        action: str,
        expected: str,
        reviews: list[tuple[str, str]],
        key: str,
        digest: str,
        ctx: AuthorizedContext,
    ) -> dict[str, Any] | None:
        include_held_out = _may_see_held_out(ctx)
        outcome = _stored_outcome(
            tenant_context, project_id, manifest_id, key, digest, include_held_out
        )
        if outcome is not None:
            return outcome
        return _replayed(
            tenant_context,
            project_id,
            manifest_id,
            action,
            expected,
            reviews,
            ctx.principal.subject,
            include_held_out,
        )

    @router.post(_BASE_PATH + "/{manifest_id}/approve")
    async def approve(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        manifest_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        key = _idempotency_key(request)
        payload = await _read_body(request, _APPROVE_FIELDS)
        reviews = _parse_reviews(payload["leakage_reviews"])
        expected = payload["expected_proposal_sha256"]
        pairs = [(r.finding_id, r.rationale) for r in reviews]
        digest = _fingerprint("approve", manifest_id, expected, pairs)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            replay = _replay_or_none(
                tenant_context,
                project_id,
                manifest_id,
                "approve",
                expected,
                pairs,
                key,
                digest,
                ctx,
            )
            if replay is not None:
                return JSONResponse({**replay, "replayed": True})
            try:
                with tenant_scoped_session_for(session_factory, tenant_context) as session:
                    actor = _actor(ctx)
                    approve_split_manifest(
                        session,
                        tenant_context,
                        project_id=project_id,
                        manifest_id=manifest_id,
                        expected_proposal_sha256=expected,
                        reviews=reviews,
                        actor=actor,
                    )
                    record_split_request(
                        session,
                        tenant_context,
                        project_id=project_id,
                        manifest_id=manifest_id,
                        action="approve",
                        idempotency_key=key,
                        request_digest=digest,
                        actor=actor,
                    )
                    body = _serialize_summary(
                        get_split_manifest(
                            session, tenant_context, project_id=project_id, manifest_id=manifest_id
                        ),
                        _may_see_held_out(ctx),
                    )
            except SplitManifestError as exc:
                if exc.code not in ("manifest_not_proposed", "idempotency_key_reused"):
                    raise
                # A concurrent identical request may have locked it first.
                replay = _replay_or_none(
                    tenant_context,
                    project_id,
                    manifest_id,
                    "approve",
                    expected,
                    pairs,
                    key,
                    digest,
                    ctx,
                )
                if replay is None:
                    raise
                return JSONResponse({**replay, "replayed": True})
        except TenantContextError as exc:
            raise _not_found() from exc
        except SplitManifestError as exc:
            raise _refusal(exc) from exc
        return JSONResponse({**body, "replayed": False})

    @router.post(_BASE_PATH + "/{manifest_id}/reject")
    async def reject(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        manifest_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        key = _idempotency_key(request)
        payload = await _read_body(request, _REJECT_FIELDS)
        expected = payload["expected_proposal_sha256"]
        digest = _fingerprint("reject", manifest_id, expected, [])
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            replay = _replay_or_none(
                tenant_context, project_id, manifest_id, "reject", expected, [], key, digest, ctx
            )
            if replay is not None:
                return JSONResponse({**replay, "replayed": True})
            try:
                with tenant_scoped_session_for(session_factory, tenant_context) as session:
                    current = get_split_manifest(
                        session, tenant_context, project_id=project_id, manifest_id=manifest_id
                    )
                    if not hmac.compare_digest(current.manifest.proposal_sha256, expected):
                        raise SplitManifestError("manifest_changed")
                    actor = _actor(ctx)
                    reject_split_manifest(
                        session,
                        tenant_context,
                        project_id=project_id,
                        manifest_id=manifest_id,
                        actor=actor,
                    )
                    record_split_request(
                        session,
                        tenant_context,
                        project_id=project_id,
                        manifest_id=manifest_id,
                        action="reject",
                        idempotency_key=key,
                        request_digest=digest,
                        actor=actor,
                    )
                    body = _serialize_summary(
                        get_split_manifest(
                            session, tenant_context, project_id=project_id, manifest_id=manifest_id
                        ),
                        _may_see_held_out(ctx),
                    )
            except SplitManifestError as exc:
                if exc.code not in ("manifest_not_proposed", "idempotency_key_reused"):
                    raise
                replay = _replay_or_none(
                    tenant_context,
                    project_id,
                    manifest_id,
                    "reject",
                    expected,
                    [],
                    key,
                    digest,
                    ctx,
                )
                if replay is None:
                    raise
                return JSONResponse({**replay, "replayed": True})
        except TenantContextError as exc:
            raise _not_found() from exc
        except SplitManifestError as exc:
            raise _refusal(exc) from exc
        return JSONResponse({**body, "replayed": False})

    return router
