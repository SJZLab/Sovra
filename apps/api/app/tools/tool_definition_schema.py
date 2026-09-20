"""Tool schema domain schema (FTF-0404, Section 6.9/7A/22/65/77/81).

A project-scoped `ToolDefinition` -- Section 6.9's own entity. Section 22
Screen D ("tools and schemas") shows these as part of the Agent Manifest
review; this task's own job is the storage/validation/versioning shape of
the record itself, not the Agent Manifest that will later reference it
(FTF-0408 and later, separately dependency-gated -- see docs/TASK_INDEX.md).

This task's own negative_acceptance ("Editing used tool schema creates new
version and drift, never changes historical manifest in place") is
enforced by construction: a `ToolDefinition` row is never updated in place
once it exists (`tool_definition_crud.py`'s own `apply_new_version` never
mutates a row's own content fields -- only `superseded_at`/`updated_at` on
the row it replaces). "Used" is read narrowly, matching this task's own
scope: no live AgentManifest/tool_contracts_json consumer exists anywhere
in this repository yet (that is FTF-0408's own, later job), so every
created tool schema is treated as potentially used from the moment it
exists -- there is no code path anywhere in this module or
`tool_definition_crud.py` that ever mutates a historical version's own
`input_schema_json`/`output_schema_json`/`description`/`side_effect_class`/
`requires_confirmation`/`is_critical` fields after creation, editable or
not.

Real forward Alembic migration
(`apps/api/alembic/versions/0006_tool_definitions.py`) FORCEs row-level
security on `tool_definitions` scoped to the same
`app.current_organization_id` tenant context FTF-0102/FTF-0104/FTF-0201/
FTF-0301/FTF-0401's migrations already established, and adds a real
composite foreign key `(organization_id, project_id)` ->
`projects(organization_id, id)` -- the same Section 65.1 "composite
(organization_id, id) keys where the parent is tenant-owned" precedent
FTF-0401's own migration already applied to `workspaces`, applied here to
the already-CERTIFIED `projects` table via a purely additive
`UNIQUE (organization_id, id)` constraint (trivially always true, since
`id` is already a globally-unique primary key).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.organization_workspace_schema import Base

__all__ = [
    "ALLOWED_SIDE_EFFECT_CLASSES",
    "DESCRIPTION_MAX_LENGTH",
    "NAME_MAX_LENGTH",
    "SCHEMA_MAX_SERIALIZED_LENGTH",
    "ToolDefinition",
    "ToolDefinitionValidationError",
    "compute_schema_hash",
    "validate_description",
    "validate_schema_object",
    "validate_side_effect_class",
    "validate_tool_name",
]

NAME_MAX_LENGTH = 200
DESCRIPTION_MAX_LENGTH = 4000
# Bare size ceiling, this task's own choice (no upstream section fixes
# one) -- the same "genuinely free-form but not an unbounded write
# vector" discipline FTF-0401's own `validate_ui_state` already applies
# to a different JSON blob field.
SCHEMA_MAX_SERIALIZED_LENGTH = 65536

# Section 6.9's own literal enumeration.
ALLOWED_SIDE_EFFECT_CLASSES = (
    "read_only",
    "reversible_write",
    "irreversible_write",
    "financial",
    "privileged",
)

_NAME_PATTERN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


class ToolDefinitionValidationError(ValueError):
    """A field value violates this domain schema's own shape/invariant."""


def validate_tool_name(value: object) -> str:
    """A tool's `name` is a callable identifier (Section 22's own worked
    example: `lookup_order`), not a free-form label like `Project.name` --
    it is what an agent's own tool-call payload and this task's own
    per-project uniqueness both key on, so it is validated as a real
    identifier shape: non-empty, bounded, and restricted to
    `[A-Za-z_][A-Za-z0-9_]*`, never starting with a digit."""
    if not isinstance(value, str) or not value:
        raise ToolDefinitionValidationError("name must be a non-empty string")
    if len(value) > NAME_MAX_LENGTH:
        raise ToolDefinitionValidationError(f"name must be at most {NAME_MAX_LENGTH} characters")
    if value[0].isdigit():
        raise ToolDefinitionValidationError("name must not start with a digit")
    if not set(value) <= _NAME_PATTERN_CHARS:
        raise ToolDefinitionValidationError(
            "name must contain only letters, digits and underscores"
        )
    return value


def validate_description(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolDefinitionValidationError("description must be a string or null")
    if len(value) > DESCRIPTION_MAX_LENGTH:
        raise ToolDefinitionValidationError(
            f"description must be at most {DESCRIPTION_MAX_LENGTH} characters"
        )
    return value


def validate_side_effect_class(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_SIDE_EFFECT_CLASSES:
        raise ToolDefinitionValidationError(
            f"side_effect_class must be one of {ALLOWED_SIDE_EFFECT_CLASSES}: {value!r}"
        )
    return value


def validate_schema_object(value: object, field_name: str) -> dict[str, object]:
    """`input_schema_json`/`output_schema_json` are JSON Schema documents
    themselves (Section 6.9). This module does not validate them as a
    conforming JSON Schema meta-schema (a later task's own scope, matching
    this task's own `database_changes: "No independent schema change"`
    narrow charter) -- only that they are a real, bounded JSON object, the
    same "genuinely free-form but not unbounded" discipline
    `validate_ui_state` already applies."""
    if not isinstance(value, dict):
        raise ToolDefinitionValidationError(f"{field_name} must be a JSON object")
    serialized = json.dumps(value, sort_keys=True)
    if len(serialized) > SCHEMA_MAX_SERIALIZED_LENGTH:
        raise ToolDefinitionValidationError(
            f"{field_name} must serialize to at most {SCHEMA_MAX_SERIALIZED_LENGTH} characters"
        )
    return dict(value)


def compute_schema_hash(
    input_schema_json: dict[str, object], output_schema_json: dict[str, object]
) -> str:
    """Section 6.9's own `schema_hash` field, made real: a deterministic
    digest of the exact input+output schema shape this version carries --
    order-independent (canonical `sort_keys` serialization), so two
    semantically identical schemas hash identically regardless of key
    order, and any real content change produces a different hash."""
    canonical = json.dumps(
        {"input_schema_json": input_schema_json, "output_schema_json": output_schema_json},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ToolDefinition(Base):
    """Section 6.9 -- a project's own tool schema, one immutable version
    per row. `organization_id` is not one of Section 6.9's own literal
    fields but is required by Section 65.1's own "All organization-scoped
    entities carry organization_id NOT NULL" rule, the same refinement
    FTF-0201/FTF-0301/FTF-0401 already applied to their own new tables.

    `superseded_at IS NULL` marks the one *current* version for a given
    `(project_id, name)` -- enforced by a real partial unique index in the
    migration, never only in application code. `previous_version_id`
    threads the lineage back to the version this one replaced; it is only
    ever set at construction time, never edited afterward.
    """

    __tablename__ = "tool_definitions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    input_schema_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    output_schema_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    side_effect_class: Mapped[str] = mapped_column(String(32), nullable=False)
    requires_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "side_effect_class in "
            "('read_only','reversible_write','irreversible_write','financial','privileged')",
            name="ck_tool_definitions_side_effect_class",
        ),
        CheckConstraint("version >= 1", name="ck_tool_definitions_version_positive"),
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_tool_definitions_organization_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_tool_definitions_organization_id_project_id",
        ),
        ForeignKeyConstraint(
            ["previous_version_id"],
            ["tool_definitions.id"],
            ondelete="RESTRICT",
            name="fk_tool_definitions_previous_version_id",
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        if "name" in kwargs:
            kwargs["name"] = validate_tool_name(kwargs["name"])
        if "description" in kwargs:
            kwargs["description"] = validate_description(kwargs["description"])
        if "input_schema_json" in kwargs:
            kwargs["input_schema_json"] = validate_schema_object(
                kwargs["input_schema_json"], "input_schema_json"
            )
        if "output_schema_json" in kwargs:
            kwargs["output_schema_json"] = validate_schema_object(
                kwargs["output_schema_json"], "output_schema_json"
            )
        if "side_effect_class" in kwargs:
            kwargs["side_effect_class"] = validate_side_effect_class(kwargs["side_effect_class"])
        super().__init__(**kwargs)
