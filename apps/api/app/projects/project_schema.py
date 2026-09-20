"""Project domain schema (FTF-0401, Section 6.8/7A/22/65-66/77/81).

A workspace-scoped `Project` -- Section 6.8's own entity. This module owns
exactly the storage/validation shape of that record: creating/editing an
agentic-product or generic-model-tuning project with versioned security/
budget-owner/UI-state settings. It deliberately does NOT implement the
deeper security-profile enforcement semantics (FTF-0402 "Project security
profile"), a real BudgetPolicy row to reference (FTF-0403 "Budget policy"),
tool-schema CRUD (FTF-0404), or the actual Target Application
connection/Agent Manifest an agentic project ultimately needs before
production qualification (FTF-04A01/FTF-0408 and later) -- those are
later, separately dependency-gated tasks (see docs/TASK_INDEX.md).
Declaring any of that as a real, enforced capability here without a real
backing implementation would be exactly the "placeholder endpoint" Law 11
forbids.

This task's own negative_acceptance ("Agentic project without approved
target/runtime remains setup-incomplete; no silent generic downgrade") is
enforced by construction, not by a later reachability check that could be
bypassed: `project_type` is immutable after creation (never accepted by
`apply_edits` in `project_crud.py`), and an `agentic_product` project's
`status` always starts (and, within this task's own surface, always
stays) `setup_incomplete` -- there is no code path anywhere in this module
or `project_crud.py` that ever transitions a project's `status` to
`active` for an agentic project. That transition is FTF-04A01/FTF-0408's
own, later job, once a real approved Target Application snapshot and
locked Agent Manifest exist; until then it is honestly `setup_incomplete`,
never silently treated as ready.

Real forward Alembic migration
(`apps/api/alembic/versions/0005_projects.py`) FORCEs row-level security on
`projects` scoped to the same `app.current_organization_id` tenant context
FTF-0102/FTF-0104/FTF-0201/FTF-0301 already established, and additionally
adds a real composite foreign key `(organization_id, workspace_id)` ->
`workspaces(organization_id, id)` -- Section 65.1's "composite
(organization_id, id) keys where the parent is tenant-owned" applied for
real at the database level, not only checked in application code -- via a
purely additive `UNIQUE (organization_id, id)` constraint on the
already-CERTIFIED `workspaces` table (trivially always true, since `id` is
already a globally-unique primary key; this adds a constraint the data
already satisfies, so it cannot invalidate FTF-0102's own certified
evidence).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.organization_workspace_schema import Base, validate_name

__all__ = [
    "ALLOWED_PROJECT_TYPES",
    "ALLOWED_STATUSES",
    "AGENT_OBJECTIVE_MAX_LENGTH",
    "DESCRIPTION_MAX_LENGTH",
    "SECURITY_PROFILE_MAX_LENGTH",
    "UI_STATE_MAX_SERIALIZED_LENGTH",
    "Project",
    "ProjectValidationError",
    "initial_status_for",
    "validate_agent_objective",
    "validate_description",
    "validate_project_type",
    "validate_security_profile",
    "validate_ui_state",
]

DESCRIPTION_MAX_LENGTH = 4000
AGENT_OBJECTIVE_MAX_LENGTH = 4000
SECURITY_PROFILE_MAX_LENGTH = 64
UI_STATE_MAX_SERIALIZED_LENGTH = 16384

# Section 6.8's own literal enumeration.
ALLOWED_PROJECT_TYPES = ("agentic_product", "generic_model_tuning")

# `setup_incomplete` is this task's own addition to the generic
# active/suspended/archived convention Organization/Workspace already use
# (Section 6.8's own "production qualification is blocked until an
# approved Target Application snapshot and locked Agent Manifest exist"
# made into a real, queryable status value rather than a derived fact
# computed elsewhere).
ALLOWED_STATUSES = ("setup_incomplete", "active", "archived")


class ProjectValidationError(ValueError):
    """A field value violates this domain schema's own shape/invariant."""


def validate_project_type(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_PROJECT_TYPES:
        raise ProjectValidationError(
            f"project_type must be one of {ALLOWED_PROJECT_TYPES}: {value!r}"
        )
    return value


def validate_description(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProjectValidationError("description must be a string or null")
    if len(value) > DESCRIPTION_MAX_LENGTH:
        raise ProjectValidationError(
            f"description must be at most {DESCRIPTION_MAX_LENGTH} characters"
        )
    return value


def validate_agent_objective(project_type: str, value: object) -> str | None:
    """Section 6.8's own agentic/generic split, made real: an
    `agentic_product` project must name a real, non-empty objective; a
    `generic_model_tuning` project must NOT carry one -- this task's own
    negative_acceptance ("no silent generic downgrade") requires the two
    kinds to be genuinely distinguishable by more than a label, not just
    permissively accept either shape for either type."""
    if project_type == "agentic_product":
        if not isinstance(value, str) or not value.strip():
            raise ProjectValidationError(
                "agent_objective must be a non-empty string for an agentic_product project"
            )
        if len(value) > AGENT_OBJECTIVE_MAX_LENGTH:
            raise ProjectValidationError(
                f"agent_objective must be at most {AGENT_OBJECTIVE_MAX_LENGTH} characters"
            )
        return value
    if value is not None:
        raise ProjectValidationError(
            "agent_objective must not be set for a generic_model_tuning project"
        )
    return None


def validate_security_profile(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectValidationError("security_profile must be a non-empty string")
    if len(value) > SECURITY_PROFILE_MAX_LENGTH:
        raise ProjectValidationError(
            f"security_profile must be at most {SECURITY_PROFILE_MAX_LENGTH} characters"
        )
    return value


def validate_ui_state(value: object) -> dict[str, object]:
    """Bounded, JSON-object-shaped only -- Section 84.4's "no fabricated
    counters" read narrowly here as "no unbounded client-controlled blob
    either": a bare size ceiling (this task's own choice, no upstream
    section fixes one) keeps this genuinely free-form save-draft field
    from becoming an unbounded-write vector through an authenticated,
    otherwise-legitimate route."""
    import json

    if not isinstance(value, dict):
        raise ProjectValidationError("ui_state must be a JSON object")
    serialized = json.dumps(value)
    if len(serialized) > UI_STATE_MAX_SERIALIZED_LENGTH:
        raise ProjectValidationError(
            f"ui_state must serialize to at most {UI_STATE_MAX_SERIALIZED_LENGTH} characters"
        )
    return dict(value)


def initial_status_for(project_type: str) -> str:
    """This task's own negative_acceptance made executable: an
    `agentic_product` project can never start (or, within this task's own
    surface, ever become) `active` -- only `generic_model_tuning` does."""
    return "setup_incomplete" if project_type == "agentic_product" else "active"


class Project(Base):
    """Section 6.8 -- an organization's own workspace-scoped project.

    `organization_id` is not one of Section 6.8's own literal fields but
    is required by Section 65.1's own "All organization-scoped entities
    carry organization_id NOT NULL" rule -- the same refinement FTF-0201/
    FTF-0301 already applied to their own new tables, needed here so RLS
    can enforce isolation directly on this table rather than only via a
    join through `workspaces`.
    """

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    project_type: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_objective: Mapped[str | None] = mapped_column(Text(), nullable=True)
    security_profile: Mapped[str] = mapped_column(
        String(SECURITY_PROFILE_MAX_LENGTH), nullable=False, server_default="standard"
    )
    budget_policy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    ui_state_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_projects_workspace_id_name"),
        CheckConstraint(
            "project_type in ('agentic_product','generic_model_tuning')",
            name="ck_projects_project_type",
        ),
        CheckConstraint(
            "status in ('setup_incomplete','active','archived')", name="ck_projects_status"
        ),
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_projects_organization_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_projects_organization_id_workspace_id",
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        if "name" in kwargs:
            kwargs["name"] = validate_name(kwargs["name"])  # type: ignore[arg-type]
        if "project_type" in kwargs:
            kwargs["project_type"] = validate_project_type(kwargs["project_type"])
        if "description" in kwargs:
            kwargs["description"] = validate_description(kwargs["description"])
        if "agent_objective" in kwargs:
            kwargs["agent_objective"] = validate_agent_objective(
                kwargs.get("project_type"),  # type: ignore[arg-type]
                kwargs["agent_objective"],
            )
        if "security_profile" in kwargs:
            kwargs["security_profile"] = validate_security_profile(kwargs["security_profile"])
        if "ui_state_json" in kwargs:
            kwargs["ui_state_json"] = validate_ui_state(kwargs["ui_state_json"])
        if "status" in kwargs and kwargs["status"] not in ALLOWED_STATUSES:
            raise ProjectValidationError(
                f"status must be one of {ALLOWED_STATUSES}: {kwargs['status']!r}"
            )
        super().__init__(**kwargs)
