"""Tool schema CRUD (FTF-0404).

Creates the `tool_definitions` table (Section 6.9/7A/22/45/65-66/77/81),
FORCEs row-level security scoped to the same `app.current_organization_id`
tenant context FTF-0102/FTF-0104/FTF-0201/FTF-0301/FTF-0401's migrations
established, and grants the restricted `sovra_app` role SELECT/INSERT/
UPDATE only -- deliberately never DELETE, since this task's own
negative_acceptance ("Editing used tool schema creates new version and
drift, never changes historical manifest in place") means a row is never
removed, only ever superseded by a new one -- the same
grant-surface-matches-actual-DML-need discipline every prior migration in
this chain already applies.

Also adds a purely additive `UNIQUE (organization_id, id)` constraint on
the already-CERTIFIED `projects` table (FTF-0401): trivially always true,
since `id` is already a globally-unique primary key, so this cannot
invalidate FTF-0401's own certified evidence -- it only makes a real
composite foreign key `tool_definitions(organization_id, project_id)` ->
`projects(organization_id, id)` possible, the same Section 65.1 "composite
(organization_id, id) keys where the parent is tenant-owned" precedent
FTF-0401's own migration already applied to `workspaces`.

`superseded_at IS NULL` marks the one current version for a given
`(project_id, name)` -- enforced by a real partial unique index, never
only in application code.

Revision ID: 0006_tool_definitions
Revises: 0005_projects
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_tool_definitions"
down_revision = "0005_projects"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as every prior migration in this chain.
APP_ROLE_NAME = "sovra_app"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_projects_organization_id_id", "projects", ["organization_id", "id"]
    )

    op.create_table(
        "tool_definitions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "organizations.id", ondelete="CASCADE", name="fk_tool_definitions_organization_id"
            ),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("input_schema_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_schema_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("side_effect_class", sa.String(32), nullable=False),
        sa.Column(
            "requires_confirmation", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("is_critical", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("schema_hash", sa.String(64), nullable=False),
        sa.Column("previous_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_tool_definitions_organization_id_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["previous_version_id"],
            ["tool_definitions.id"],
            ondelete="RESTRICT",
            name="fk_tool_definitions_previous_version_id",
        ),
        sa.CheckConstraint(
            "side_effect_class in "
            "('read_only','reversible_write','irreversible_write','financial','privileged')",
            name="ck_tool_definitions_side_effect_class",
        ),
        sa.CheckConstraint("version >= 1", name="ck_tool_definitions_version_positive"),
    )
    op.create_index("ix_tool_definitions_organization_id", "tool_definitions", ["organization_id"])
    op.create_index("ix_tool_definitions_project_id", "tool_definitions", ["project_id"])
    op.create_index(
        "uq_tool_definitions_project_id_name_current",
        "tool_definitions",
        ["project_id", "name"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )

    # Section 65.1: same FORCE ROW LEVEL SECURITY discipline as every other
    # tenant-scoped table in this repository.
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON tool_definitions TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE tool_definitions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tool_definitions FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON tool_definitions FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON tool_definitions")
    op.execute(f'REVOKE ALL ON tool_definitions FROM "{APP_ROLE_NAME}"')
    op.drop_index("uq_tool_definitions_project_id_name_current", table_name="tool_definitions")
    op.drop_index("ix_tool_definitions_project_id", table_name="tool_definitions")
    op.drop_index("ix_tool_definitions_organization_id", table_name="tool_definitions")
    op.drop_table("tool_definitions")
    op.drop_constraint("uq_projects_organization_id_id", "projects", type_="unique")
