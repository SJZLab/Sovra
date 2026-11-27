"""Versioned training experiments (FTF-0701-P2; Sections 6.15, 14.1-14.2, 15, 16 steps 1-9, 65.1).

`create_training_experiment` does everything in the caller's one transaction:

1. Project write authorization: the actor's role, then the project row of the caller's own
   organization, locked `FOR UPDATE` (a missing and a foreign project are the same `not_found`).
2. The pure `parse_training_config_request` (`workers/training/training_config_schema.py`).
3. Idempotency per (organization, actor, key): the same key with the same request digest returns
   the stored row with no new row and no audit event; the same key with another digest is
   `idempotency_key_reused`.
4. The server-side facts, read only through certified public functions: the FTF-0301 artifact read
   with the FTF-0307 capability classification, FTF-0308 `require_current_mapping`, FTF-0519
   `get_split_manifest` + `load_training_split` (the live re-verification of the frozen hash, the
   quality reports and every frozen grant), and the FTF-0403 policy read. No budget is reserved.
   A source that refuses is recorded as the matching fact (`grants_current=False`, an artifact
   that is not `ready`, no policy), never raised here, so the pure module's fixed check order
   decides which refusal the caller sees.
5. The pure `resolve_training_config`.
6. `version = latest + 1` for (project, name), serialized by the project row lock of step 1; the
   unique constraint is the backstop (`version_conflict`).
7. INSERT plus one content-free `training_experiment.create` audit row.

Reads recompute `config_sha256` from the stored document and refuse `config_integrity_failed`
instead of serving a row whose document no longer matches its hash.

Every refusal is a `TrainingExperimentError` whose text is its stable code only. A database error
that is not one of the named constraints propagates as itself; nothing is reported as success
(Law 17).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from packages.litellm_client.alias_reconciliation import AliasReconciliationResult
from sqlalchemy import DateTime, Integer, Numeric, String, func, literal, select, tuple_
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.types import JSON
from workers.training.training_config_schema import (
    ERROR_CODES,
    ArtifactFacts,
    BudgetFacts,
    CatalogFacts,
    SplitFacts,
    TrainingConfigError,
    TrainingConfigFacts,
    TrainingConfigRequest,
    canonical_config_json,
    compute_config_sha256,
    parse_training_config_request,
    resolve_training_config,
)

from app.artifacts.artifact_crud import ArtifactNotFoundError, get_artifact
from app.artifacts.training_capability_classifier import classify_training_capability
from app.auth.audit_event_writer import write_audit_event
from app.auth.organization_workspace_schema import Base, Organization
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.budget.budget_policy_crud import BudgetPolicyNotFoundError, get_budget_policy
from app.datasets.dataset_split_manifest_store import (
    SplitManifestError,
    get_split_manifest,
    load_training_split,
)
from app.db.tenant_context import TenantContext
from app.litellm.model_catalog_mapping_store import (
    MappingChangedError,
    ModelCatalogEntryNotFoundError,
    require_current_mapping,
)
from app.projects.project_schema import Project
from app.services.project_security_profile import resolve_effective_security_profile

__all__ = [
    "IDEMPOTENCY_KEY_MAX_LENGTH",
    "MAX_PAGE_SIZE",
    "NAME_MAX_LENGTH",
    "STORE_ERROR_CODES",
    "WRITER_ROLES",
    "CreatedExperiment",
    "ExperimentActor",
    "TrainingExperiment",
    "TrainingExperimentError",
    "create_training_experiment",
    "get_training_experiment",
    "list_training_experiments",
    "request_sha256",
]

NAME_MAX_LENGTH: Final = 200
IDEMPOTENCY_KEY_MAX_LENGTH: Final = 128
MAX_PAGE_SIZE: Final = 200
# Who may save an experiment (QUEUE.md FTF-0701-P3: writers are the ML engineer and the
# organization admin). The role comes from the authenticated principal, never from a body.
WRITER_ROLES: Final = frozenset({"ml_engineer", "organization_admin"})
_RESOURCE: Final = "training_experiment"
_AUDIT_ACTION: Final = "training_experiment.create"
_SHA_PREFIX: Final = "sha256:"
_WEIGHTS_DIGEST: Final = re.compile(r"sha256:[0-9a-f]{64}")
# Shape-valid stand-ins for a fact that could not be read. They never reach a stored row: every
# path that uses one also sets the fact that makes `resolve_training_config` refuse.
_NO_DIGEST: Final = "0" * 64
_ABSENT: Final = "absent"
_NOT_CLEAR: Final = "not_clear"

# The store's own codes on top of the pure module's frozen table.
STORE_ERROR_CODES: Final[Mapping[str, int]] = {
    **ERROR_CODES,
    "not_found": 404,
    "actor_not_permitted": 403,
    "version_conflict": 409,
    "config_integrity_failed": 500,
}

_IDEMPOTENCY_CONSTRAINT: Final = "uq_training_experiments_idempotency"
# violated constraint / trigger CONSTRAINT -> the stable code (the database is the authority when
# a fact changes between the read and the INSERT)
_CONFLICT_BY_NAME: Final[Mapping[str, str]] = {
    "uq_training_experiments_project_name_version": "version_conflict",
    "tr_training_experiments_manifest_locked": "split_manifest_not_locked",
    "tr_training_experiments_manifest_frozen": "split_manifest_changed",
    "tr_training_experiments_artifact_ready": "artifact_not_trainable",
    "tr_training_experiments_budget_project": "budget_policy_missing",
    "ck_training_experiments_config_consistency": "invalid_source_role",
}
# FTF-0519 refusals of the live training read -> the fact they falsify.
_SPLIT_NOT_LOCKED: Final = frozenset(
    {"manifest_not_found", "manifest_not_locked", "leakage_not_clear", "quality_not_clear"}
)

_JSON = JSONB().with_variant(JSON(), "sqlite")


class TrainingExperimentError(Exception):
    """A refusal with a stable `code` and HTTP `status`; the text is the code and nothing else."""

    def __init__(self, code: str) -> None:
        if code not in STORE_ERROR_CODES:
            raise ValueError("unknown training experiment error code")
        super().__init__(code)
        self.code = code
        self.status = STORE_ERROR_CODES[code]


@dataclass(frozen=True)
class ExperimentActor:
    """`actor_id` and `role` come from the authenticated principal, never from a request body."""

    actor_type: str
    actor_id: str
    role: str
    request_id: str


class TrainingExperiment(Base):
    """One immutable version of a training configuration (`0039_training_experiments.py`)."""

    __tablename__ = "training_experiments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    config_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False)
    config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    trainable_artifact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model_catalog_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    split_manifest_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    frozen_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_policy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    max_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    security_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(200), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(IDEMPOTENCY_KEY_MAX_LENGTH), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True)
class CreatedExperiment:
    experiment: TrainingExperiment
    replayed: bool


# ------------------------------------------------------------------ request values


def _fail(code: str) -> TrainingExperimentError:
    return TrainingExperimentError(code)


def _valid_name(value: object) -> str:
    """A non-blank name of at most 200 characters with no leading or trailing whitespace and no
    control character. Never normalised: two spellings are two names."""
    if type(value) is not str or not 1 <= len(value) <= NAME_MAX_LENGTH:
        raise _fail("invalid_body")
    if value[0].isspace() or value[-1].isspace() or any(ord(ch) < 0x20 for ch in value):
        raise _fail("invalid_body")
    return value


def _valid_idempotency_key(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= IDEMPOTENCY_KEY_MAX_LENGTH:
        raise _fail("invalid_body")
    if any(not 0x20 <= ord(ch) <= 0x7E for ch in value):
        raise _fail("invalid_body")
    return value


def request_sha256(project_id: uuid.UUID, name: str, request: TrainingConfigRequest) -> str:
    """Digest of what a create request asks for: the project, the name and the parsed (normalised)
    configuration. The idempotency key is not part of it."""
    document = {
        "config": json.loads(canonical_config_json(request.doc)),
        "name": name,
        "project_id": str(project_id),
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


# ------------------------------------------------------------------ reads used by create


@dataclass(frozen=True)
class _ProjectRow:
    id: uuid.UUID
    security_profile: str


def _locked_project(session: Session, context: TenantContext, project_id: uuid.UUID) -> _ProjectRow:
    """The caller's own project row, locked until the transaction ends. A missing id and an id of
    another organization are the same `not_found`."""
    statement = (
        select(Project.id, Project.security_profile)
        .where(Project.organization_id == context.organization_id, Project.id == project_id)
        .with_for_update()
    )
    row = execute_tenant_scoped(session, context.organization_id, statement).first()
    if row is None:
        raise _fail("not_found")
    return _ProjectRow(id=row[0], security_profile=row[1])


def _project_exists(session: Session, context: TenantContext, project_id: uuid.UUID) -> None:
    statement = select(Project.id).where(
        Project.organization_id == context.organization_id, Project.id == project_id
    )
    if execute_tenant_scoped(session, context.organization_id, statement).first() is None:
        raise _fail("not_found")


def _find_by_idempotency_key(
    session: Session, context: TenantContext, actor_id: str, key: str
) -> TrainingExperiment | None:
    statement = select(TrainingExperiment).where(
        TrainingExperiment.organization_id == context.organization_id,
        TrainingExperiment.requested_by == actor_id,
        TrainingExperiment.idempotency_key == key,
    )
    return execute_tenant_scoped(session, context.organization_id, statement).scalars().first()


def _replay_or_reuse(existing: TrainingExperiment, digest: str) -> CreatedExperiment:
    if not hmac.compare_digest(existing.request_sha256.encode(), digest.encode()):
        raise _fail("idempotency_key_reused")
    return CreatedExperiment(experiment=_verified(existing), replayed=True)


def _artifact_facts(
    session: Session, context: TenantContext, artifact_id: uuid.UUID
) -> ArtifactFacts:
    try:
        artifact = get_artifact(session, context, artifact_id)
    except ArtifactNotFoundError:
        return ArtifactFacts(
            artifact_id=str(artifact_id),
            status=_ABSENT,
            capability=_ABSENT,
            weights_digest=_NO_DIGEST,
            base_revision="",
            license_accepted=False,
        )
    classification = classify_training_capability(
        model_id=str(artifact.id),
        organization_id=context.organization_id,
        reconciliation=AliasReconciliationResult(
            models=(), model_count=0, reconciled_at=datetime.now(UTC)
        ),
        artifact=artifact,
    )
    digest = artifact.weights_digest
    has_digest = type(digest) is str and _WEIGHTS_DIGEST.fullmatch(digest) is not None
    return ArtifactFacts(
        artifact_id=str(artifact.id),
        status=artifact.status,
        # Capability is the artifact dimension only (FTF-0307 orthogonality: access is not used).
        capability=classification.capability.value if has_digest else _ABSENT,
        weights_digest=str(digest)[len(_SHA_PREFIX) :] if has_digest else _NO_DIGEST,
        base_revision=artifact.base_model_revision,
        license_accepted=artifact.license_accepted_at is not None,
    )


def _catalog_facts(
    session: Session, context: TenantContext, entry_id: str | None, catalog_version: int | None
) -> CatalogFacts | None:
    if entry_id is None or catalog_version is None:
        return None
    try:
        record = require_current_mapping(session, context, uuid.UUID(entry_id), catalog_version)
    except (ModelCatalogEntryNotFoundError, MappingChangedError):
        return None
    mapped = record.trainable_artifact_id
    return CatalogFacts(
        entry_id=str(record.id),
        version=record.catalog_version,
        mapped_artifact_id="" if mapped is None else str(mapped),
    )


def _split_facts(
    session: Session, context: TenantContext, project_id: uuid.UUID, manifest_id: uuid.UUID
) -> SplitFacts:
    """The manifest's own summary (status, frozen hash, role counts and hashes), then the FTF-0519
    live training read. The read's refusal becomes the fact it falsifies."""
    try:
        summary = get_split_manifest(
            session, context, project_id=project_id, manifest_id=manifest_id
        )
    except SplitManifestError as exc:
        if exc.code != "manifest_not_found":
            raise
        return _unusable_split(manifest_id, _ABSENT)
    manifest = summary.manifest
    roles = {row.role: row for row in summary.roles}
    train, validation = roles.get("train"), roles.get("validation")
    status = str(manifest.status)
    hash_verified = quality_current = grants_current = True
    try:
        view = load_training_split(session, context, project_id=project_id, manifest_id=manifest_id)
    except SplitManifestError as exc:
        if exc.code in _SPLIT_NOT_LOCKED:
            status = status if status != "locked" else _NOT_CLEAR
        elif exc.code == "manifest_tampered":
            hash_verified = False
        elif exc.code == "quality_report_changed":
            quality_current = False
        elif exc.code == "grant_changed":
            grants_current = False
        else:
            raise
    else:
        served = {"train": 0, "validation": 0}
        for row in view.rows:
            served[row.role] = served.get(row.role, 0) + 1
        # The served rows must be the rows the summary counted, of the same frozen manifest.
        hash_verified = (
            view.frozen_manifest_sha256 == manifest.frozen_manifest_sha256
            and view.version == manifest.version
            and set(served) == {"train", "validation"}
            and served["train"] == (0 if train is None else train.record_count)
            and served["validation"] == (0 if validation is None else validation.record_count)
        )
    frozen = manifest.frozen_manifest_sha256
    return SplitFacts(
        manifest_id=str(manifest_id),
        manifest_version=manifest.version,
        status=status,
        frozen_sha256=frozen if type(frozen) is str else "",
        hash_verified=hash_verified,
        quality_report_current=quality_current,
        grants_current=grants_current,
        train_count=0 if train is None else train.record_count,
        validation_count=0 if validation is None else validation.record_count,
        train_role_sha256=_NO_DIGEST if train is None else train.role_sha256,
        validation_role_sha256=_NO_DIGEST if validation is None else validation.role_sha256,
    )


def _unusable_split(manifest_id: uuid.UUID, status: str) -> SplitFacts:
    return SplitFacts(
        manifest_id=str(manifest_id),
        manifest_version=1,
        status=status,
        frozen_sha256="",
        hash_verified=False,
        quality_report_current=False,
        grants_current=False,
        train_count=0,
        validation_count=0,
        train_role_sha256=_NO_DIGEST,
        validation_role_sha256=_NO_DIGEST,
    )


def _budget_facts(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> BudgetFacts | None:
    try:
        policy = get_budget_policy(session, context, project_id)
    except BudgetPolicyNotFoundError:
        return None
    return BudgetFacts(
        policy_id=str(policy.id),
        row_version=policy.row_version,
        currency=policy.currency,
        scopes=tuple(policy.scopes_json),
        total=policy.total_amount,
        reserved=policy.reserved_amount,
    )


def _effective_security_profile(
    session: Session, context: TenantContext, project: _ProjectRow
) -> str:
    statement = select(Organization.default_security_profile).where(
        Organization.id == context.organization_id
    )
    default = execute_tenant_scoped(session, context.organization_id, statement).scalar_one()
    return resolve_effective_security_profile(default, project.security_profile)


def _build_facts(
    session: Session,
    context: TenantContext,
    project: _ProjectRow,
    doc: Mapping[str, Any],
) -> TrainingConfigFacts:
    base = doc["base"]
    return TrainingConfigFacts(
        artifact=_artifact_facts(session, context, uuid.UUID(base["trainable_artifact_id"])),
        split=_split_facts(
            session, context, project.id, uuid.UUID(doc["data"]["train"]["split_manifest_id"])
        ),
        security_profile=_effective_security_profile(session, context, project),
        catalog=_catalog_facts(
            session, context, base["model_catalog_entry_id"], base["catalog_version"]
        ),
        budget=_budget_facts(session, context, project.id),
    )


def _refined(code: str, facts: TrainingConfigFacts) -> str:
    """An artifact whose license is not accepted is never `ready` (FTF-0305 revocation demotes it;
    0029 CHECK), so the pure module reports it as `artifact_not_trainable`. For an artifact that
    exists, the store names the cause: `license_not_accepted`."""
    artifact = facts.artifact
    if (
        code == "artifact_not_trainable"
        and artifact.status != _ABSENT
        and artifact.license_accepted is not True
    ):
        return "license_not_accepted"
    return code


def _violated_name(exc: DBAPIError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    if diag is None:
        return None
    name = diag.constraint_name
    return name if isinstance(name, str) else None


# ------------------------------------------------------------------ create


def create_training_experiment(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    *,
    name: object,
    config_body: object,
    idempotency_key: object,
    actor: ExperimentActor,
) -> CreatedExperiment:
    # 1. Project write authorization.
    if actor.role not in WRITER_ROLES:
        raise _fail("actor_not_permitted")
    project = _locked_project(session, context, project_id)
    # 2. Shape (the pure module; the first problem wins).
    checked_name = _valid_name(name)
    key = _valid_idempotency_key(idempotency_key)
    try:
        request = parse_training_config_request(config_body)
    except TrainingConfigError as exc:
        raise _fail(exc.code) from None
    # 3. Idempotency.
    digest = request_sha256(project.id, checked_name, request)
    existing = _find_by_idempotency_key(session, context, actor.actor_id, key)
    if existing is not None:
        return _replay_or_reuse(existing, digest)
    # 4-5. Facts, then the pure resolution in its fixed order.
    facts = _build_facts(session, context, project, request.doc)
    try:
        resolved = resolve_training_config(request, facts)
    except TrainingConfigError as exc:
        raise _fail(_refined(exc.code, facts)) from None
    doc = json.loads(canonical_config_json(resolved.doc))
    # 6. The next version of this name, under the project row lock.
    latest = execute_tenant_scoped(
        session,
        context.organization_id,
        select(func.max(TrainingExperiment.version)).where(
            TrainingExperiment.organization_id == context.organization_id,
            TrainingExperiment.project_id == project.id,
            TrainingExperiment.name == checked_name,
        ),
    ).scalar()
    entry_id = doc["base"]["model_catalog_entry_id"]
    experiment = TrainingExperiment(
        organization_id=context.organization_id,
        project_id=project.id,
        name=checked_name,
        version=1 if latest is None else latest + 1,
        parent_version=latest,
        status="draft",
        method=doc["method"],
        config_schema_version=doc["schema_version"],
        config_json=doc,
        config_sha256=resolved.config_sha256,
        trainable_artifact_id=uuid.UUID(doc["base"]["trainable_artifact_id"]),
        model_catalog_entry_id=None if entry_id is None else uuid.UUID(entry_id),
        split_manifest_id=uuid.UUID(doc["data"]["train"]["split_manifest_id"]),
        frozen_manifest_sha256=doc["server"]["frozen_manifest_sha256"],
        budget_policy_id=uuid.UUID(doc["server"]["budget_policy_id"]),
        max_cost=Decimal(doc["budget"]["max_cost"]),
        currency=doc["budget"]["currency"],
        security_profile=doc["server"]["security_profile"],
        requested_by=actor.actor_id,
        request_id=actor.request_id,
        idempotency_key=key,
        request_sha256=digest,
        created_at=datetime.now(UTC),
    )
    # 7. INSERT and the audit row, in the caller's transaction.
    try:
        with session.begin_nested():
            session.add(experiment)
            session.flush()
    except (IntegrityError, DBAPIError) as exc:
        violated = _violated_name(exc)
        if violated == _IDEMPOTENCY_CONSTRAINT:
            raced = _find_by_idempotency_key(session, context, actor.actor_id, key)
            if raced is None:
                raise
            return _replay_or_reuse(raced, digest)
        code = _CONFLICT_BY_NAME.get(violated) if violated is not None else None
        if code is None:
            raise
        raise _fail(code) from None
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action=_AUDIT_ACTION,
        resource_type=_RESOURCE,
        resource_id=str(experiment.id),
        request_id=actor.request_id,
        # Content-free: ids, version, method and the hash. Never the name, a module or a revision.
        details={
            "project_id": str(project.id),
            "version": experiment.version,
            "method": experiment.method,
            "config_sha256": experiment.config_sha256,
        },
    )
    return CreatedExperiment(experiment=experiment, replayed=False)


# ------------------------------------------------------------------ reads


def _verified(experiment: TrainingExperiment) -> TrainingExperiment:
    """The row, only if its stored document still hashes to its stored `config_sha256`."""
    try:
        recomputed = compute_config_sha256(experiment.config_json)
    except TrainingConfigError:
        raise _fail("config_integrity_failed") from None
    stored = experiment.config_sha256
    if type(stored) is not str or not hmac.compare_digest(recomputed.encode(), stored.encode()):
        raise _fail("config_integrity_failed")
    return experiment


def get_training_experiment(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    experiment_id: uuid.UUID,
) -> TrainingExperiment:
    """One experiment of the caller's own organization and project. A missing id, an id of another
    project and an id of another organization are the same `not_found`."""
    statement = select(TrainingExperiment).where(
        TrainingExperiment.organization_id == context.organization_id,
        TrainingExperiment.project_id == project_id,
        TrainingExperiment.id == experiment_id,
    )
    experiment = (
        execute_tenant_scoped(session, context.organization_id, statement).scalars().first()
    )
    if experiment is None:
        raise _fail("not_found")
    return _verified(experiment)


def list_training_experiments(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    *,
    limit: int,
    after: tuple[str, int] | None = None,
) -> list[TrainingExperiment]:
    """A bounded page ordered by name, then version; `after` is the last (name, version) seen."""
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("limit must be an integer between 1 and 200")
    if after is not None and not (
        type(after) is tuple and len(after) == 2 and type(after[0]) is str and type(after[1]) is int
    ):
        raise ValueError("after must be a (name, version) pair or None")
    _project_exists(session, context, project_id)
    statement = select(TrainingExperiment).where(
        TrainingExperiment.organization_id == context.organization_id,
        TrainingExperiment.project_id == project_id,
    )
    if after is not None:
        statement = statement.where(
            tuple_(TrainingExperiment.name, TrainingExperiment.version)
            > tuple_(literal(after[0]), literal(after[1]))
        )
    statement = statement.order_by(TrainingExperiment.name, TrainingExperiment.version).limit(limit)
    rows = execute_tenant_scoped(session, context.organization_id, statement).scalars().all()
    return [_verified(row) for row in rows]
