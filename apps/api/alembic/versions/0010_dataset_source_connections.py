"""DatasetSourceConnection schema/API/RBAC (FTF-0500).

Creates the `dataset_source_connections` table (Section 6.8F/7A.10/9/45/
65-67/77/81), FORCEs row-level security scoped to the same
`app.current_organization_id` tenant context FTF-0102/FTF-0104/FTF-0201/
FTF-0301/FTF-0401/FTF-0404's migrations already established, and grants
the restricted `sovra_app` role SELECT/INSERT/UPDATE only -- deliberately
never DELETE, since "revoke" is a soft `connection_status='revoked'`
transition, not row deletion, the same discipline every prior migration in
this chain already applies.

Reuses the already-real `UNIQUE (organization_id, id)` constraint
FTF-0404's own migration (`0006_tool_definitions.py`) already added to
`projects` -- no second additive constraint needed here, unlike every
prior task that was itself the first to add a composite FK against its
own parent table.

Revision ID: 0010_dataset_source_connections
Revises: 0009_model_versions
Create Date: 2026-09-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010_dataset_source_connections"
down_revision = "0009_model_versions"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as every prior migration in this chain.
APP_ROLE_NAME = "sovra_app"


def upgrade() -> None:
    op.create_table(
        "dataset_source_connections",
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
                "organizations.id",
                ondelete="CASCADE",
                name="fk_dataset_source_connections_organization_id",
            ),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("credential_secret_ref", sa.String(512), nullable=True),
        sa.Column("connection_config_encrypted_ref", sa.String(512), nullable=True),
        sa.Column("allowed_purposes_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("security_classification", sa.String(32), nullable=False),
        sa.Column("connection_status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_dataset_source_connections_organization_id_project_id",
        ),
        sa.UniqueConstraint(
            "project_id",
            "display_name",
            name="uq_dataset_source_connections_project_id_display_name",
        ),
        sa.CheckConstraint(
            "source_type in ('upload','s3','minio','database','agent_traces',"
            "'production_conversations','api')",
            name="ck_dataset_source_connections_source_type",
        ),
        sa.CheckConstraint(
            "security_classification in ('public','internal','confidential','restricted')",
            name="ck_dataset_source_connections_security_classification",
        ),
        sa.CheckConstraint(
            "connection_status in ('active','revoked')",
            name="ck_dataset_source_connections_connection_status",
        ),
    )
    op.create_index(
        "ix_dataset_source_connections_organization_id",
        "dataset_source_connections",
        ["organization_id"],
    )
    op.create_index(
        "ix_dataset_source_connections_project_id", "dataset_source_connections", ["project_id"]
    )

    # Section 65.1: same FORCE ROW LEVEL SECURITY discipline as every other
    # tenant-scoped table in this repository.
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON dataset_source_connections TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE dataset_source_connections ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE dataset_source_connections FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON dataset_source_connections FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON dataset_source_connections")
    op.execute(f'REVOKE ALL ON dataset_source_connections FROM "{APP_ROLE_NAME}"')
    op.drop_index(
        "ix_dataset_source_connections_project_id", table_name="dataset_source_connections"
    )
    op.drop_index(
        "ix_dataset_source_connections_organization_id", table_name="dataset_source_connections"
    )
    op.drop_table("dataset_source_connections")
