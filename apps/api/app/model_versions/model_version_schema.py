"""ModelVersion domain schema (FTF-1001, Section 34/35/36/68/77/81).

A project-scoped `ModelVersion` -- the immutable governance envelope
Section 35 describes ("Factory ModelVersion is an immutable governance
envelope referencing that record and content digest"). This task's own
job is the storage/validation shape of that envelope for exactly the
three import kinds its own acceptance clause names ("ModelVersion
supports endpoint-only/full-weight/adapter imports and Factory training
lineage") -- not the real MLflow tracking record Section 35 also names
(FTF-1009's own, later, separately dependency-gated "Registry UI backed
by controlled MLflow projection"), not real vLLM serving (FTF-1003), not
promotion/tag assignment (`candidate`/`eval-passed`/`approved`/`staging`/
`production`/`retired`/`revoked` -- FTF-1101 "PromotionPolicy engine",
which lists this task as its own dependency, not the other way around),
and not "one production version per deployment slot" (a deployment-slot
concept that does not exist anywhere in this repository yet -- FTF-1008
"Staging deployment"'s own, later job). Declaring any of that as a real,
enforced capability here without a real backing implementation would be
exactly the "placeholder endpoint" Law 11 forbids.

Every model version is immutable by construction (Section 35's own
literal rule, applied here even more strongly than FTF-0301/FTF-0404's
own "immutable once ready"/"immutable once superseded" rules): there is
no edit path anywhere in this module or `model_version_crud.py` at all,
and the migration's own database grant (see
`apps/api/alembic/versions/0007_model_versions.py`) is SELECT/INSERT
only -- never UPDATE, unlike every prior versioned resource in this
chain. A new import is always a new row; nothing about an existing row
ever changes after creation.

This task's own negative_acceptance ("Required nonexistent weights/hash
for endpoint import, mutable production artifact or cross-tenant parent
denied") resolves, against the real fields this module actually has, to
three distinct denial paths -- disclosed here, not hidden, the same way
FTF-0301/FTF-0404/FTF-0407 each disclosed their own reading of a
similarly compressed sentence:

1. "Required nonexistent weights/hash for endpoint import": an
   `endpoint` import has no local weight bytes to hash at all (Law 12's
   own `inference_only` capability -- the endpoint itself, wherever it is
   actually served, owns whatever revision it serves; the Factory never
   invents a weights/hash claim for it). `import_kind='endpoint'`
   therefore *structurally forbids* `weights_digest`/`trainable_artifact_id`/
   `base_model_revision` (enforced both here, in `__init__`, and by a
   real `CHECK` constraint in the migration -- defense in depth, not only
   an application-level check a bypassing write could evade) and
   *requires* `endpoint_reference` -- a claimed weights/hash on an
   endpoint import would be exactly a "required[-looking but actually]
   nonexistent" hash, since no real bytes back it.
2. "mutable production artifact ... denied": a `full_weight`/`adapter`
   import's own `trainable_artifact_id` must reference a parent
   `TrainableArtifact` that is already `ready` (FTF-0301's own immutable,
   license-accepted, digest-resolved state) -- never `registered` (still
   mutable: FTF-0301's own `apply_edits` refuses to touch anything but a
   `registered` row precisely because its identity can still change) or
   `archived` (a closed record). Enforced in `model_version_crud.py`'s
   own `create_model_version`, since it requires a real database fetch of
   the parent row, not a pure/DB-free check this module can make alone.
3. "cross-tenant parent ... denied": the referenced `TrainableArtifact`
   must belong to the *same* organization as the `ModelVersion` being
   created -- reusing FTF-0301's own already-certified
   `app.artifacts.artifact_crud.get_artifact` (built on FTF-H003's
   `authorize_resource_ownership`) rather than re-deriving that IDOR
   guard a second time, the same "no new authorization primitive, only
   composition" discipline FTF-0301/FTF-0404 already applied to a
   different parent relationship (`tool_definition_crud.py`'s own
   `_authorize_project_or_raise`).

`weights_digest` is never accepted as a disconnected client claim for a
`full_weight`/`adapter` import: `create_model_version` always copies it
(and `base_model_revision`) directly from the real, already-resolved
parent `TrainableArtifact` row, never from request input -- the same
"never trust an arbitrary client-supplied value where a real source of
truth already exists" discipline Section 65.1 established for license-
acceptance attribution (`artifact_crud.py`'s own `ActorInfo.actor_id`).

Real forward Alembic migration
(`apps/api/alembic/versions/0007_model_versions.py`) FORCEs row-level
security on `model_versions` scoped to the same
`app.current_organization_id` tenant context every prior migration in
this chain already established, and adds two real composite foreign keys
-- `(organization_id, project_id)` -> `projects(organization_id, id)`
(the same precedent FTF-0404's own migration already applied) and
`(organization_id, trainable_artifact_id)` ->
`trainable_artifacts(organization_id, id)` via a purely additive
`UNIQUE (organization_id, id)` constraint on the already-CERTIFIED
`trainable_artifacts` table (trivially always true, since `id` is already
a globally-unique primary key, so this cannot invalidate FTF-0301's own
certified evidence).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.artifacts.artifact_schema import (
    BASE_MODEL_REVISION_MAX_LENGTH,
    TrainableArtifactValidationError,
    validate_base_model_revision,
    validate_weights_digest,
)
from app.auth.organization_workspace_schema import Base

__all__ = [
    "ALLOWED_IMPORT_KINDS",
    "ENDPOINT_REFERENCE_MAX_LENGTH",
    "ModelVersion",
    "ModelVersionValidationError",
    "validate_endpoint_reference",
    "validate_import_kind",
]

ENDPOINT_REFERENCE_MAX_LENGTH = 2048

# Section 34/68's own three import shapes for this task's own acceptance
# clause. Law 12's `capability` dimension maps directly:
# endpoint -> inference_only, full_weight/adapter -> trainable_open_weight
# (that capability classification itself is a later, separate concern --
# this module owns only the import-kind shape, not capability derivation).
ALLOWED_IMPORT_KINDS = ("endpoint", "full_weight", "adapter")

_ARTIFACT_ONLY_KINDS = frozenset({"full_weight", "adapter"})


class ModelVersionValidationError(ValueError):
    """A field value violates this domain schema's own shape/invariant,
    including the structural endpoint-vs-artifact consistency rule this
    task's own negative_acceptance requires."""


def validate_import_kind(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_IMPORT_KINDS:
        raise ModelVersionValidationError(
            f"import_kind must be one of {ALLOWED_IMPORT_KINDS}: {value!r}"
        )
    return value


def validate_endpoint_reference(value: object) -> str:
    """Section 65.4-style shape safety for a real, reachable HTTPS
    endpoint reference -- this module never resolves it against a real
    serving backend (that is FTF-1003/FTF-1005's own, later job); it only
    stores/validates the reference's own shape, the same disclosed-scope
    precedent `artifact_schema.py`'s own `validate_source_uri` already
    established for a structurally identical reason."""
    if not isinstance(value, str) or not value:
        raise ModelVersionValidationError("endpoint_reference must be a non-empty string")
    if len(value) > ENDPOINT_REFERENCE_MAX_LENGTH:
        raise ModelVersionValidationError(
            f"endpoint_reference must be at most {ENDPOINT_REFERENCE_MAX_LENGTH} characters"
        )
    parsed = urlsplit(value)
    if parsed.scheme != "https":
        raise ModelVersionValidationError(
            f"endpoint_reference must use https://, got scheme {parsed.scheme!r}"
        )
    if not parsed.hostname:
        raise ModelVersionValidationError("endpoint_reference must have a host")
    if parsed.username or parsed.password:
        raise ModelVersionValidationError(
            "endpoint_reference may not carry userinfo (Section 65.4)"
        )
    if parsed.fragment:
        raise ModelVersionValidationError("endpoint_reference may not carry a fragment")
    return value


class ModelVersion(Base):
    """Section 35 -- a project's own immutable model-version envelope, one
    row per import, never edited after creation (see this module's own
    docstring for why no edit path exists at all)."""

    __tablename__ = "model_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    import_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    trainable_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    weights_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    endpoint_reference: Mapped[str | None] = mapped_column(
        String(ENDPOINT_REFERENCE_MAX_LENGTH), nullable=True
    )
    base_model_revision: Mapped[str | None] = mapped_column(
        String(BASE_MODEL_REVISION_MAX_LENGTH), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id", "version_number", name="uq_model_versions_project_id_version_number"
        ),
        CheckConstraint(
            "import_kind in ('endpoint','full_weight','adapter')",
            name="ck_model_versions_import_kind",
        ),
        CheckConstraint("version_number >= 1", name="ck_model_versions_version_number_positive"),
        # Real, database-level defense-in-depth for this task's own
        # negative_acceptance structural half (see module docstring point
        # 1) -- never only an application-level check a bypassing write
        # could evade.
        CheckConstraint(
            "(import_kind = 'endpoint' AND trainable_artifact_id IS NULL "
            "AND weights_digest IS NULL AND base_model_revision IS NULL "
            "AND endpoint_reference IS NOT NULL) "
            "OR "
            "(import_kind in ('full_weight','adapter') AND trainable_artifact_id IS NOT NULL "
            "AND weights_digest IS NOT NULL AND base_model_revision IS NOT NULL "
            "AND endpoint_reference IS NULL)",
            name="ck_model_versions_kind_consistency",
        ),
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_model_versions_organization_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_model_versions_organization_id_project_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "trainable_artifact_id"],
            ["trainable_artifacts.organization_id", "trainable_artifacts.id"],
            ondelete="RESTRICT",
            name="fk_model_versions_organization_id_trainable_artifact_id",
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        # `validate_weights_digest`/`validate_base_model_revision` are
        # reused directly from `artifact_schema.py` (never duplicated --
        # see this module's own docstring), so their own
        # `TrainableArtifactValidationError` is translated to this
        # module's own `ModelVersionValidationError` here, the same
        # "one domain, one exception type" discipline
        # `tool_definition_schema.py` already applies to its own reused
        # `SideEffectClassificationError`/`CriticalBehaviorRequirementError`.
        if "import_kind" in kwargs:
            kwargs["import_kind"] = validate_import_kind(kwargs["import_kind"])
        if kwargs.get("weights_digest") is not None:
            try:
                kwargs["weights_digest"] = validate_weights_digest(kwargs["weights_digest"])
            except TrainableArtifactValidationError as exc:
                raise ModelVersionValidationError(str(exc)) from exc
        if kwargs.get("base_model_revision") is not None:
            try:
                kwargs["base_model_revision"] = validate_base_model_revision(
                    kwargs["base_model_revision"]
                )
            except TrainableArtifactValidationError as exc:
                raise ModelVersionValidationError(str(exc)) from exc
        if kwargs.get("endpoint_reference") is not None:
            kwargs["endpoint_reference"] = validate_endpoint_reference(kwargs["endpoint_reference"])

        if "import_kind" in kwargs:
            kind = kwargs["import_kind"]
            has_artifact = kwargs.get("trainable_artifact_id") is not None
            has_digest = kwargs.get("weights_digest") is not None
            has_revision = kwargs.get("base_model_revision") is not None
            has_endpoint = kwargs.get("endpoint_reference") is not None
            if kind == "endpoint":
                if has_artifact or has_digest or has_revision:
                    raise ModelVersionValidationError(
                        "an 'endpoint' import must not carry trainable_artifact_id, "
                        "weights_digest or base_model_revision -- no local weight bytes "
                        "back an endpoint import (this task's own negative_acceptance)"
                    )
                if not has_endpoint:
                    raise ModelVersionValidationError(
                        "an 'endpoint' import requires a real endpoint_reference"
                    )
            elif kind in _ARTIFACT_ONLY_KINDS:
                if has_endpoint:
                    raise ModelVersionValidationError(
                        f"a {kind!r} import must not carry endpoint_reference"
                    )
                if not has_artifact:
                    raise ModelVersionValidationError(
                        f"a {kind!r} import requires trainable_artifact_id (Factory training "
                        "lineage)"
                    )
                if not has_digest:
                    raise ModelVersionValidationError(
                        f"a {kind!r} import requires a real, resolved weights_digest -- "
                        "this task's own negative_acceptance refuses a required but "
                        "nonexistent weights/hash"
                    )
                if not has_revision:
                    raise ModelVersionValidationError(
                        f"a {kind!r} import requires base_model_revision"
                    )

        super().__init__(**kwargs)
