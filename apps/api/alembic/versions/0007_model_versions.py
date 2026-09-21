"""ModelVersion schema/API (FTF-1001).

Creates the `model_versions` table (Section 34/35/36/45/65-66/68/77/81),
FORCEs row-level security scoped to the same `app.current_organization_id`
tenant context every prior migration in this chain already established,
and grants the restricted `sovra_app` role SELECT/INSERT only -- never
UPDATE or DELETE, since Section 35's own "every model version is
immutable" rule means there is no legitimate mutation path for this table
at all (a stronger real guarantee than FTF-0301/FTF-0404's own SELECT/
INSERT/UPDATE grant, since those two tables do have a real, narrow
in-place mutation each still needs).

Also adds a purely additive `UNIQUE (organization_id, id)` constraint on
the already-CERTIFIED `trainable_artifacts` table (FTF-0301): trivially
always true, since `id` is already a globally-unique primary key, so this
cannot invalidate FTF-0301's own certified evidence -- it only makes a
real composite foreign key `model_versions(organization_id,
trainable_artifact_id)` -> `trainable_artifacts(organization_id, id)`
possible, the same Section 65.1 "composite (organization_id, id) keys
where the parent is tenant-owned" precedent FTF-0401/FTF-0404's own
migrations already applied to `workspaces`/`projects`.

`ck_model_versions_kind_consistency` is a real, database-level
enforcement of this task's own negative_acceptance structural half (see
`app/model_versions/model_version_schema.py`'s own docstring) -- never
only an application-level check a bypassing write could evade.

Revision ID: 0007_model_versions
Revises: 0006_tool_definitions
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007_model_versions"
down_revision = "0006_tool_definitions"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as every prior migration in this chain.
APP_ROLE_NAME = "sovra_app"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_trainable_artifacts_organization_id_id",
        "trainable_artifacts",
        ["organization_id", "id"],
    )

    op.create_table(
        "model_versions",
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
                "organizations.id", ondelete="CASCADE", name="fk_model_versions_organization_id"
            ),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("import_kind", sa.String(32), nullable=False),
        sa.Column("trainable_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("weights_digest", sa.String(128), nullable=True),
        sa.Column("endpoint_reference", sa.String(2048), nullable=True),
        sa.Column("base_model_revision", sa.String(200), nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_model_versions_organization_id_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "trainable_artifact_id"],
            ["trainable_artifacts.organization_id", "trainable_artifacts.id"],
            ondelete="RESTRICT",
            name="fk_model_versions_organization_id_trainable_artifact_id",
        ),
        sa.UniqueConstraint(
            "project_id", "version_number", name="uq_model_versions_project_id_version_number"
        ),
        sa.CheckConstraint(
            "import_kind in ('endpoint','full_weight','adapter')",
            name="ck_model_versions_import_kind",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_model_versions_version_number_positive"),
        sa.CheckConstraint(
            "(import_kind = 'endpoint' AND trainable_artifact_id IS NULL "
            "AND weights_digest IS NULL AND base_model_revision IS NULL "
            "AND endpoint_reference IS NOT NULL) "
            "OR "
            "(import_kind in ('full_weight','adapter') AND trainable_artifact_id IS NOT NULL "
            "AND weights_digest IS NOT NULL AND base_model_revision IS NOT NULL "
            "AND endpoint_reference IS NULL)",
            name="ck_model_versions_kind_consistency",
        ),
    )
    op.create_index("ix_model_versions_organization_id", "model_versions", ["organization_id"])
    op.create_index("ix_model_versions_project_id", "model_versions", ["project_id"])
    op.create_index(
        "ix_model_versions_trainable_artifact_id", "model_versions", ["trainable_artifact_id"]
    )

    # Section 65.1: same FORCE ROW LEVEL SECURITY discipline as every other
    # tenant-scoped table in this repository.
    op.execute(f'GRANT SELECT, INSERT ON model_versions TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE model_versions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE model_versions FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON model_versions FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON model_versions")
    op.execute(f'REVOKE ALL ON model_versions FROM "{APP_ROLE_NAME}"')
    op.drop_index("ix_model_versions_trainable_artifact_id", table_name="model_versions")
    op.drop_index("ix_model_versions_project_id", table_name="model_versions")
    op.drop_index("ix_model_versions_organization_id", table_name="model_versions")
    op.drop_table("model_versions")
    op.drop_constraint(
        "uq_trainable_artifacts_organization_id_id", "trainable_artifacts", type_="unique"
    )
