"""EvaluationSuite domain schema (FTF-0601, Section 23-30/69/77/81/45/65-67).

This task's own literal acceptance is exactly one sentence: "Create suite
schema with development/final purpose, versioned mandate/runtime binding
and reviewer role." It does not implement case authoring/import (a
separate, dependency-gated later task, FTF-0602 "Evaluation case
editor/import," which names this task as *its own* dependency, not the
other way around), evaluation execution/results (Section 25's own runtime
loop -- no task in this dependency-ready frontier owns that yet), campaign
comparisons (Section 69.1's own `EvaluationCampaign`, a separate entity
this task's contract does not name), or promotion gating (Section 30 --
FTF-04A25/FTF-04A26 territory). Declaring any of that as a real, enforced
capability here without a real backing implementation would be exactly
the "placeholder endpoint" Law 11 forbids.

Path refined from the task packet's own generic suggestion
(`apps/web/components/evaluation-factory/evaluation_suite_schema_api.tsx`,
a frontend path with no backend counterpart at all) to
`apps/api/app/evaluation/evaluation_suite_schema.py`, matching
FTF-04A19/FTF-04A20's own real `apps/api/app/<domain>/*_schema.py` layout
precedent exactly -- this is schema/API work, not a UI component, and no
`EvaluationSuite` entity/route exists anywhere in this repository yet for
a frontend component to render.

## Reading this task's own three literal schema fields

"development/final purpose": Section 69.1/69.4's own real, closed
train/validation/development/final role vocabulary, narrowed to the two
values a suite itself can carry (`purpose`) -- a suite supports iterative
search (`development`) or is the sealed, frozen final measurement
(`final`); it is never both, and there is no third or "mixed" value (this
task's own negative_acceptance, "mixed train/final data grants
... rejected" -- read literally: there is no enum value expressing "both,"
so any attempt to request one is refused at the validation boundary, never
silently coerced into one purpose or the other).

"versioned mandate/runtime binding": Section 6.8D's own `AgentManifest` is
already the versioned, immutable-once-`locked` carrier of both the
mandate (`mandate_json`) and the runtime tuple (`runtime_dependencies_json`)
-- see `agent_manifest_schema.py`'s own docstring. This task's own real job
is not to duplicate those fields on a second table; it is to *bind* a
suite to one specific, already-`locked` `AgentManifest` row
(`agent_manifest_id`), which is what "versioned ... binding" means here: the
suite's own mandate/runtime context is fixed to that manifest's own frozen
content and version for the suite's entire lifetime, never a mutable
"current manifest for this project" pointer. `create_evaluation_suite`
(the CRUD layer) refuses a manifest that is not `status == 'locked'` --
see below, "negative_acceptance mapping," point 1.

"reviewer role": Section 69.1: "Final suite creator/reviewer is
independent of candidate implementer." `reviewer_id` is a real,
nullable-while-draft field distinct from `created_by`; `PURPOSE_FINAL`
suites additionally carry a real, database-level `CHECK` refusing
`reviewer_id = created_by` at the moment of lock (see this module's own
migration) -- the creator can never be their own final reviewer.
`development`-purpose suites carry no such restriction (Section 69.1 names
this independence rule for the *final* suite specifically).

## Status/versioning

`ALLOWED_STATUSES` is the two-value set `('draft', 'locked')` -- simpler
than `AgentManifest`'s own four-value chain: this task's own acceptance
names no intermediate review step distinct from locking itself (contrast
Section 6.8D's own explicit `review_required` state), and no supersession
workflow (a project may hold several independently-versioned suites of
either purpose at once; nothing here requires exactly one "current" suite
per project the way `AgentManifest`'s own locking does). `version` is a
real, per-project, monotonically increasing integer (mirrors
`AgentManifest`'s own `version` field/`uq_agent_manifests_project_id_version`
precedent) -- `uq_evaluation_suites_project_id_version` is the real,
database-level guarantee.

## Negative_acceptance mapping (disclosed, not hidden)

This task's own compressed sentence, "Wrong project/manifest, unversioned
grading logic and mixed train/final data grants rejected," splits into
three real, distinct, independently-enforced conditions:

1. "Wrong ... manifest": covers two real cases, both refused before a row
   is ever written -- (a) `agent_manifest_id` genuinely exists in the
   caller's own organization but belongs to a *different* project than the
   suite being created (`EvaluationSuiteManifestMismatchError`, the same
   cross-parent-check discipline `agent_manifest_crud.py`'s own
   `SnapshotProjectMismatchError` already established for a structurally
   identical reason -- no single `CHECK` constraint can express "same
   project" across a foreign table); (b) the manifest is not currently
   `status == 'locked'` (`EvaluationSuiteManifestNotLockedError`) -- a
   `draft`/`review_required` manifest has no real, frozen
   `manifest_sha256`/content yet, so binding a suite's own "versioned"
   context to it would not actually be versioned at all; binding to a
   `superseded` manifest is refused for the same reason (superseded means a
   *later* version now carries the real current mandate/runtime for this
   project).
2. "Wrong project": `project_id` itself must genuinely belong to the
   caller's own organization (`ProjectNotFoundError`, the same
   `authorize_resource_ownership` composition every prior task in this
   chain already established).
3. "unversioned grading logic": `grading_logic_version` is a real, `NOT
   NULL`, `>= 1` integer column with no default -- a caller must always
   supply an explicit positive version; there is no way to create or
   persist a suite with a null/absent/zero grading-logic version at all
   (enforced both at construction, `AgentManifest.__init__`-style, and by a
   real database `CHECK`).
4. "mixed train/final data grants": `purpose` is a real, closed two-value
   `CHECK`-enforced enum (`development`, `final`) -- there is no "mixed"
   value to request in the first place; see "Reading this task's own three
   literal schema fields" above for the full disclosed reasoning.

Real forward Alembic migration (`apps/api/alembic/versions/
0017_evaluation_suites.py`) FORCEs row-level security on `evaluation_suites`
scoped to the same `app.current_organization_id` tenant context every
prior migration in this chain already established, grants
`SELECT`/`INSERT`/`UPDATE` (never `DELETE` -- a suite is a durable
evaluation record, never removed, the same "no legitimate deletion path"
precedent `agent_manifests` already established), and adds two real
composite foreign keys -- `(organization_id, project_id)` ->
`projects(organization_id, id)` (reusing FTF-1001's own already-added
`uq_projects_organization_id_id`) and `(organization_id,
agent_manifest_id)` -> `agent_manifests(organization_id, id)` (reusing
FTF-04A20's own already-added `uq_agent_manifests_organization_id_id`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.organization_workspace_schema import Base

__all__ = [
    "ALLOWED_PURPOSES",
    "ALLOWED_STATUSES",
    "NAME_MAX_LENGTH",
    "PURPOSE_DEVELOPMENT",
    "PURPOSE_FINAL",
    "EvaluationSuite",
    "EvaluationSuiteValidationError",
    "validate_grading_logic_version",
    "validate_name",
    "validate_purpose",
    "validate_reviewer_id_or_none",
    "validate_status",
]

NAME_MAX_LENGTH = 200

# Section 69.1/69.4's own train/validation/development/final role
# vocabulary, narrowed to the two purposes a suite itself can carry -- see
# this module's own docstring, "Reading this task's own three literal
# schema fields."
PURPOSE_DEVELOPMENT = "development"
PURPOSE_FINAL = "final"
ALLOWED_PURPOSES = (PURPOSE_DEVELOPMENT, PURPOSE_FINAL)

# This task's own acceptance names no intermediate review state distinct
# from locking -- see this module's own docstring, "Status/versioning."
ALLOWED_STATUSES = ("draft", "locked")


class EvaluationSuiteValidationError(ValueError):
    """A field value violates this domain schema's own shape/invariant,
    including an illegal purpose/status value or a caller attempting to
    persist an unversioned grading-logic value."""


def validate_status(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_STATUSES:
        raise EvaluationSuiteValidationError(f"status must be one of {ALLOWED_STATUSES}: {value!r}")
    return value


def validate_purpose(value: object) -> str:
    """Refuses anything outside the closed two-value set -- this task's
    own negative_acceptance, "mixed train/final data grants ... rejected"
    (see this module's own docstring, point 4)."""
    if not isinstance(value, str) or value not in ALLOWED_PURPOSES:
        raise EvaluationSuiteValidationError(
            f"purpose must be one of {ALLOWED_PURPOSES}: {value!r}"
        )
    return value


def validate_name(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise EvaluationSuiteValidationError("name must be a non-empty string")
    if len(value) > NAME_MAX_LENGTH:
        raise EvaluationSuiteValidationError(f"name must be at most {NAME_MAX_LENGTH} characters")
    return value


def validate_grading_logic_version(value: object) -> int:
    """Refuses anything but a real positive integer -- this task's own
    negative_acceptance, "unversioned grading logic ... rejected" (see
    this module's own docstring, point 3). `bool` is deliberately excluded
    even though it is a `int` subclass in Python -- a caller passing
    `True`/`False` almost certainly made a mistake, not a real version."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EvaluationSuiteValidationError(
            f"grading_logic_version must be a positive integer: {value!r}"
        )
    return value


def validate_reviewer_id_or_none(value: object) -> str | None:
    """`None` means "no reviewer yet" (a real, legal draft state). Anything
    else must be a real identifier: an empty or whitespace-only string is
    refused here the same way `None` is accepted, so a caller cannot smuggle
    "no reviewer" past this check by sending `""` instead of omitting the
    field (FTF-0601's own negative_acceptance -- a `final` suite locking
    with no real reviewer)."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise EvaluationSuiteValidationError("reviewer_id must be a non-empty string or null")
    if len(value) > 200:
        raise EvaluationSuiteValidationError("reviewer_id must be at most 200 characters")
    return value


class EvaluationSuite(Base):
    """Section 23-30/69 -- a project's own EvaluationSuite, bound to one
    specific, already-`locked` `AgentManifest` version, versioned per
    project, with a real `draft` -> `locked` transition (see this module's
    own docstring)."""

    __tablename__ = "evaluation_suites"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_manifest_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    grading_logic_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "status in (" + ", ".join(f"'{v}'" for v in ALLOWED_STATUSES) + ")",
            name="ck_evaluation_suites_status",
        ),
        CheckConstraint(
            "purpose in (" + ", ".join(f"'{v}'" for v in ALLOWED_PURPOSES) + ")",
            name="ck_evaluation_suites_purpose",
        ),
        CheckConstraint("version >= 1", name="ck_evaluation_suites_version_positive"),
        CheckConstraint(
            "grading_logic_version >= 1", name="ck_evaluation_suites_grading_logic_version_positive"
        ),
        CheckConstraint(
            "(status = 'locked') = (locked_at IS NOT NULL)",
            name="ck_evaluation_suites_locked_at_iff_locked",
        ),
        # Section 69.1: "Final suite creator/reviewer is independent of
        # candidate implementer" -- a locked, final-purpose suite can
        # never carry the same identity for both fields (see this module's
        # own docstring, "reviewer role").
        CheckConstraint(
            "NOT (purpose = 'final' AND status = 'locked' AND reviewer_id = created_by)",
            name="ck_evaluation_suites_independent_final_reviewer",
        ),
        # A locked suite must have a real, non-empty reviewer assigned (any
        # purpose) -- `<> ''` closes the real gap `IS NOT NULL` alone left
        # open (an empty-string reviewer_id satisfies `IS NOT NULL`).
        CheckConstraint(
            "status <> 'locked' OR (reviewer_id IS NOT NULL AND reviewer_id <> '')",
            name="ck_evaluation_suites_reviewer_present_when_locked",
        ),
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_evaluation_suites_organization_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_evaluation_suites_organization_id_project_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "agent_manifest_id"],
            ["agent_manifests.organization_id", "agent_manifests.id"],
            ondelete="RESTRICT",
            name="fk_evaluation_suites_organization_id_manifest_id",
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        if "status" in kwargs:
            kwargs["status"] = validate_status(kwargs["status"])
        if "purpose" in kwargs:
            kwargs["purpose"] = validate_purpose(kwargs["purpose"])
        if "name" in kwargs:
            kwargs["name"] = validate_name(kwargs["name"])
        if "grading_logic_version" in kwargs:
            kwargs["grading_logic_version"] = validate_grading_logic_version(
                kwargs["grading_logic_version"]
            )
        if "reviewer_id" in kwargs:
            kwargs["reviewer_id"] = validate_reviewer_id_or_none(kwargs["reviewer_id"])

        status = kwargs.get("status", "draft")
        is_locked = status == "locked"
        has_locked_at = kwargs.get("locked_at") is not None
        if has_locked_at != is_locked:
            raise EvaluationSuiteValidationError(
                "locked_at must be set if and only if status is 'locked'"
            )
        if is_locked and kwargs.get("reviewer_id") is None:
            raise EvaluationSuiteValidationError(
                "reviewer_id must be set before a suite can be locked -- "
                "Section 69.1: 'Final suite creator/reviewer is independent "
                "of candidate implementer'"
            )
        if (
            is_locked
            and kwargs.get("purpose") == PURPOSE_FINAL
            and kwargs.get("reviewer_id") == kwargs.get("created_by")
        ):
            raise EvaluationSuiteValidationError(
                "a final-purpose suite's reviewer_id must differ from its "
                "own created_by -- Section 69.1's own independence rule"
            )

        super().__init__(**kwargs)
