"""Project schema/API (FTF-0401).

Creates the `projects` table (Section 6.8/7A/22/65-66/77/81), FORCEs
row-level security scoped to the same `app.current_organization_id`
tenant context FTF-0102/FTF-0104/FTF-0201/FTF-0301's migrations
established, and grants the restricted `sovra_app` role SELECT/INSERT/
UPDATE only -- deliberately never DELETE, since "archive" is a soft
status='archived' transition, not row deletion, the same discipline every
prior migration in this chain already applies.

Also adds a purely additive `UNIQUE (organization_id, id)` constraint on
the already-CERTIFIED `workspaces` table (FTF-0102): trivially always
true, since `id` is already a globally-unique primary key, so this cannot
invalidate FTF-0102's own certified evidence -- it only makes a real
composite foreign key `projects(organization_id, workspace_id)` ->
`workspaces(organization_id, id)` possible, Section 65.1's own "composite
(organization_id, id) keys where the parent is tenant-owned" enforced for
real at the database level.

Revision ID: 0005_projects
Revises: 0004_trainable_artifacts
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005_projects"
down_revision = "0004_trainable_artifacts"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as every prior migration in this chain.
APP_ROLE_NAME = "sovra_app"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_workspaces_organization_id_id", "workspaces", ["organization_id", "id"]
    )

    op.create_table(
        "projects",
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
                "organizations.id", ondelete="CASCADE", name="fk_projects_organization_id"
            ),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("project_type", sa.String(32), nullable=False),
        sa.Column("agent_objective", sa.Text(), nullable=True),
        sa.Column("security_profile", sa.String(64), nullable=False, server_default="standard"),
        sa.Column("budget_policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "ui_state_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
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
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_projects_organization_id_workspace_id",
        ),
        sa.UniqueConstraint("workspace_id", "name", name="uq_projects_workspace_id_name"),
        sa.CheckConstraint(
            "project_type in ('agentic_product','generic_model_tuning')",
            name="ck_projects_project_type",
        ),
        sa.CheckConstraint(
            "status in ('setup_incomplete','active','archived')", name="ck_projects_status"
        ),
    )
    op.create_index("ix_projects_organization_id", "projects", ["organization_id"])
    op.create_index("ix_projects_workspace_id", "projects", ["workspace_id"])

    # Section 65.1: same FORCE ROW LEVEL SECURITY discipline as every other
    # tenant-scoped table in this repository.
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON projects TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE projects ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE projects FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON projects FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON projects")
    op.execute(f'REVOKE ALL ON projects FROM "{APP_ROLE_NAME}"')
    op.drop_index("ix_projects_workspace_id", table_name="projects")
    op.drop_index("ix_projects_organization_id", table_name="projects")
    op.drop_table("projects")
    op.drop_constraint("uq_workspaces_organization_id_id", "workspaces", type_="unique")
