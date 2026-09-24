"""TargetSourceSnapshot domain schema (FTF-04A07, Section 6.8B/7A.1-7A.3/
9/45/65/66/77/81).

A connection-scoped `TargetSourceSnapshot` -- Section 6.8B's own entity,
"Immutable source snapshot used for discovery/evaluation reproducibility."
This module owns exactly the storage/validation shape of that record: the
snapshot row itself, after a real commit/tree has already been resolved
(by a real connector). It deliberately does not implement the actual
GitHub App/token exchange or repository-tree enumeration (Section 7A.2 --
FTF-04A02), the local-repository root-allowlist/dirty-tree hashing walk
(Section 7A.3 -- FTF-04A06), or the deterministic source-analysis
pipeline that would ever advance `analysis_status` past its own single
Release-1 starting value (Section 7A.4 -- FTF-04A17, a separate,
dependency-gated later task that owns `DiscoveredAgentCandidate`).
Declaring any of that as a real, enforced capability here without a real
backing implementation would be exactly the "placeholder endpoint" Law 11
forbids.

Path refined from the task packet's own generic suggestion
(`packages/target-connectors/targetsourcesnapshot_schema_immutable_provenance.py`)
to `apps/api/app/target_connectors/target_source_snapshot_schema.py`,
matching FTF-04A01's own real `apps/api/app/<domain>/*_schema.py` layout
precedent exactly rather than the packet's initial owner-path placeholder.

Field list scoped to Section 6.8B's own literal fields plus exactly one
inherited addition: `organization_id`, denormalized directly onto this
table (never derived only through a join), the same Section 65.1 "All
organization-scoped entities carry organization_id NOT NULL" precedent
FTF-04A01/FTF-1001 already applied to a table one level removed from
`organizations` itself. Unlike FTF-04A01's own `TargetApplicationConnection`
(Section 6.8A explicitly lists `created_by`/`created_at`/`updated_at`),
Section 6.8B names no actor/audit-timestamp field at all -- a snapshot is
typically captured by an automated connector/sync workflow (Section 66.5:
"Poll approved remote/runtime config every 5 minutes by default"), not a
direct user action, so no `created_by` column is added here; real actor
attribution for *why* a snapshot was captured still flows through the
same `write_audit_event` (FTF-0104) call every mutation in this chain
already makes, decoupled from the domain row itself.

This task's own acceptance clause 1 ("Store immutable snapshot
provenance, source identity, status and superseded relationship") maps
directly onto Section 6.8B's own field list: `commit_sha`/`tree_hash`/
`capture_method` are the provenance, `target_application_connection_id`
is the source identity, `analysis_status` is the status, and
`supersedes_snapshot_id` is the superseded relationship -- read literally,
not narrowed further.

Every snapshot is immutable by construction, even more strongly than
FTF-1001's own `ModelVersion` (which at least shares its "no edit path
anywhere, SELECT/INSERT-only grant" discipline -- see this module's own
migration, `apps/api/alembic/versions/0012_target_source_snapshots.py`,
for the identical SELECT/INSERT-only grant): there is no edit path
anywhere in this module or `target_source_snapshot_crud.py` at all. A
resync always produces a new row; nothing about an existing row ever
changes after creation. This is this task's own real "Existing snapshot
cannot be mutated" negative_acceptance half, enforced at the database
privilege level, not only by omission of a route.

`capture_method` is a closed three-value set reflecting exactly the two
required connectors' own two real capture shapes (Section 7A.1: "Minimum
production connectors required by DoD: 1. GitHub read-only connector 2.
Local repository connector"): `github_resolved_commit` (Section 7A.2's
own "resolve branch/ref to immutable commit SHA"), `local_git_commit` (a
clean local Git checkout, Section 7A.3), and `local_dirty_snapshot` (a
dirty local working tree, content-hashed and "visibly marked
non_committed_source" per Section 7A.3 -- this task's own real reading of
that visible marking is the closed `capture_method` value itself, checked
both here and by a real database `CHECK` constraint requiring a dirty
snapshot to always carry a real content archive, since it has no other
durable reference: nothing else identifies its content).

`analysis_status` is deliberately a closed *one*-value set for Release 1
(`not_analyzed`) -- the same "single legal value now, expandable only
through a real schema change" precedent FTF-04A01's own
`ALLOWED_ACCESS_MODES = ('read_only',)` already established. No later
task may reach this row through an UPDATE (the grant forbids it
entirely); Section 7A.4's own real analysis pipeline (FTF-04A17) records
its own results on `DiscoveredAgentCandidate` rows that reference this
snapshot by ID, never by mutating this table.

`supersedes_snapshot_id` is this task's own real "superseded relationship"
-- set once, at INSERT time, on the *new* row, pointing at the *old* one
it replaces (never the reverse: the old row is never touched). This task's
own negative_acceptance ("Existing snapshot cannot be ... reassigned to
another repository/project") is the real, substantive half a database
`CHECK` constraint cannot express (a `CHECK` cannot compare against
another row's columns): `target_source_snapshot_crud.py`'s own
`create_target_source_snapshot` fetches the referenced snapshot through
the same connection-scoped `_fetch_or_raise` used for a real `get`, and
refuses (`TargetSourceSnapshotNotFoundError` -- the same "cross-parent-
within-the-same-organization is refused exactly like missing" precedent
`model_version_crud.py`'s own cross-project case already established)
unless it belongs to the *same* `target_application_connection_id` as the
new row -- the same "cross-row business invariant enforced in the CRUD
layer, tenant safety enforced by a composite foreign key" division of
responsibility `model_version_crud.py`'s own `ModelVersionArtifactStateError`
check already established for a structurally identical reason.

Real forward Alembic migration
(`apps/api/alembic/versions/0012_target_source_snapshots.py`) FORCEs
row-level security on `target_source_snapshots` scoped to the same
`app.current_organization_id` tenant context every prior migration in
this chain already established, and adds two real composite foreign keys
-- `(organization_id, target_application_connection_id)` ->
`target_application_connections(organization_id, id)` (via a purely
additive `UNIQUE (organization_id, id)` constraint on the already-
CERTIFIED `target_application_connections` table, the same FTF-1001
precedent) and a real *self-referencing* composite foreign key
`(organization_id, supersedes_snapshot_id)` ->
`target_source_snapshots(organization_id, id)`, which is what makes the
cross-tenant half of "reassigned to another repository/project" a real,
database-level guarantee (never merely an application-level check a
bypassing write could evade) -- the same-connection half is the CRUD-layer
check described above, since no `CHECK` constraint can express it.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.organization_workspace_schema import Base

__all__ = [
    "ALLOWED_ANALYSIS_STATUSES",
    "ALLOWED_CAPTURE_METHODS",
    "BRANCH_MAX_LENGTH",
    "SOURCE_ARCHIVE_OBJECT_KEY_MAX_LENGTH",
    "TargetSourceSnapshot",
    "TargetSourceSnapshotValidationError",
    "validate_analysis_status",
    "validate_capture_method",
    "validate_commit_sha",
    "validate_source_archive_object_key_or_none",
    "validate_source_archive_sha256_or_none",
    "validate_tree_hash",
]

BRANCH_MAX_LENGTH = 200
SOURCE_ARCHIVE_OBJECT_KEY_MAX_LENGTH = 1024

# Git object identifiers are either a SHA-1 (40 lowercase hex chars) or,
# under Git's newer SHA-256 object-format repositories, a SHA-256 (64
# lowercase hex chars). Never a shorter "abbreviated" hash -- Section
# 6.8B's own "immutable source snapshot ... reproducibility" requires a
# real, unambiguous, fully-qualified object ID.
_GIT_OBJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# Section 7A.1/7A.2/7A.3's own two required connectors and their two real
# capture shapes -- see this module's own docstring.
ALLOWED_CAPTURE_METHODS = ("github_resolved_commit", "local_git_commit", "local_dirty_snapshot")

# A closed *one*-value set for Release 1 -- see this module's own docstring.
ALLOWED_ANALYSIS_STATUSES = ("not_analyzed",)

_DIRTY_CAPTURE_METHOD = "local_dirty_snapshot"


class TargetSourceSnapshotValidationError(ValueError):
    """A field value violates this domain schema's own shape/invariant."""


def _validate_git_object_id(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not _GIT_OBJECT_ID_PATTERN.fullmatch(value):
        raise TargetSourceSnapshotValidationError(
            f"{field_name} must be a real, fully-qualified lowercase-hex git "
            f"object id (40 or 64 hex characters), never a short/abbreviated "
            f"hash: {value!r}"
        )
    return value


def validate_commit_sha(value: object) -> str:
    return _validate_git_object_id(value, field_name="commit_sha")


def validate_tree_hash(value: object) -> str:
    return _validate_git_object_id(value, field_name="tree_hash")


def validate_capture_method(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_CAPTURE_METHODS:
        raise TargetSourceSnapshotValidationError(
            f"capture_method must be one of {ALLOWED_CAPTURE_METHODS}: {value!r}"
        )
    return value


def validate_analysis_status(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_ANALYSIS_STATUSES:
        raise TargetSourceSnapshotValidationError(
            f"analysis_status must be one of {ALLOWED_ANALYSIS_STATUSES}: {value!r}"
        )
    return value


def validate_source_archive_object_key_or_none(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise TargetSourceSnapshotValidationError(
            "source_archive_object_key must be a non-empty string or null"
        )
    if len(value) > SOURCE_ARCHIVE_OBJECT_KEY_MAX_LENGTH:
        raise TargetSourceSnapshotValidationError(
            f"source_archive_object_key must be at most "
            f"{SOURCE_ARCHIVE_OBJECT_KEY_MAX_LENGTH} characters"
        )
    return value


def validate_source_archive_sha256_or_none(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256_HEX_PATTERN.fullmatch(value):
        raise TargetSourceSnapshotValidationError(
            f"source_archive_sha256 must be 64 lowercase hex characters or null: {value!r}"
        )
    return value


def validate_branch_or_none(value: object) -> str | None:
    """Nullable -- Section 7A.3's own "support detached HEAD" requirement
    means a real local-repository capture can genuinely have no branch
    name at all."""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise TargetSourceSnapshotValidationError("branch must be a non-empty string or null")
    if len(value) > BRANCH_MAX_LENGTH:
        raise TargetSourceSnapshotValidationError(
            f"branch must be at most {BRANCH_MAX_LENGTH} characters"
        )
    return value


class TargetSourceSnapshot(Base):
    """Section 6.8B -- an immutable, connection-scoped source snapshot,
    never edited after creation (see this module's own docstring for why
    no edit path exists at all)."""

    __tablename__ = "target_source_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_application_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    branch: Mapped[str | None] = mapped_column(String(BRANCH_MAX_LENGTH), nullable=True)
    tree_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    capture_method: Mapped[str] = mapped_column(String(32), nullable=False)
    source_archive_object_key: Mapped[str | None] = mapped_column(
        String(SOURCE_ARCHIVE_OBJECT_KEY_MAX_LENGTH), nullable=True
    )
    source_archive_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    analysis_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="not_analyzed"
    )
    supersedes_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "capture_method in (" + ", ".join(f"'{v}'" for v in ALLOWED_CAPTURE_METHODS) + ")",
            name="ck_target_source_snapshots_capture_method",
        ),
        CheckConstraint(
            "analysis_status in (" + ", ".join(f"'{v}'" for v in ALLOWED_ANALYSIS_STATUSES) + ")",
            name="ck_target_source_snapshots_analysis_status",
        ),
        CheckConstraint(
            "(source_archive_object_key IS NULL) = (source_archive_sha256 IS NULL)",
            name="ck_target_source_snapshots_archive_pair",
        ),
        # Real, database-level defense-in-depth: a dirty local snapshot has
        # no other durable reference to its own content, so it must always
        # carry a real archive -- never only an application-level check a
        # bypassing write could evade.
        CheckConstraint(
            f"capture_method != '{_DIRTY_CAPTURE_METHOD}' OR "
            "(source_archive_object_key IS NOT NULL AND source_archive_sha256 IS NOT NULL)",
            name="ck_target_source_snapshots_dirty_requires_archive",
        ),
        CheckConstraint(
            "supersedes_snapshot_id IS NULL OR supersedes_snapshot_id != id",
            name="ck_target_source_snapshots_no_self_supersede",
        ),
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_target_source_snapshots_organization_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "target_application_connection_id"],
            ["target_application_connections.organization_id", "target_application_connections.id"],
            ondelete="RESTRICT",
            name="fk_target_source_snapshots_organization_id_connection_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "supersedes_snapshot_id"],
            ["target_source_snapshots.organization_id", "target_source_snapshots.id"],
            ondelete="RESTRICT",
            name="fk_target_source_snapshots_organization_id_supersedes_id",
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        if "commit_sha" in kwargs:
            kwargs["commit_sha"] = validate_commit_sha(kwargs["commit_sha"])
        if "tree_hash" in kwargs:
            kwargs["tree_hash"] = validate_tree_hash(kwargs["tree_hash"])
        if "capture_method" in kwargs:
            kwargs["capture_method"] = validate_capture_method(kwargs["capture_method"])
        if "analysis_status" in kwargs:
            kwargs["analysis_status"] = validate_analysis_status(kwargs["analysis_status"])
        if "branch" in kwargs:
            kwargs["branch"] = validate_branch_or_none(kwargs["branch"])
        if "source_archive_object_key" in kwargs:
            kwargs["source_archive_object_key"] = validate_source_archive_object_key_or_none(
                kwargs["source_archive_object_key"]
            )
        if "source_archive_sha256" in kwargs:
            kwargs["source_archive_sha256"] = validate_source_archive_sha256_or_none(
                kwargs["source_archive_sha256"]
            )

        has_key = kwargs.get("source_archive_object_key") is not None
        has_digest = kwargs.get("source_archive_sha256") is not None
        if has_key != has_digest:
            raise TargetSourceSnapshotValidationError(
                "source_archive_object_key and source_archive_sha256 must be "
                "both set or both null -- an archive reference without its own "
                "content hash (or vice versa) is not real provenance"
            )
        if kwargs.get("capture_method") == _DIRTY_CAPTURE_METHOD and not has_key:
            raise TargetSourceSnapshotValidationError(
                "capture_method='local_dirty_snapshot' requires a real "
                "source_archive_object_key/source_archive_sha256 -- a dirty "
                "working tree has no other durable reference to its own content"
            )
        if (
            "supersedes_snapshot_id" in kwargs
            and kwargs["supersedes_snapshot_id"] is not None
            and "id" in kwargs
            and kwargs["supersedes_snapshot_id"] == kwargs["id"]
        ):
            raise TargetSourceSnapshotValidationError(
                "supersedes_snapshot_id cannot reference this same row"
            )

        super().__init__(**kwargs)
