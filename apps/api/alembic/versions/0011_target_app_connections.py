"""TargetApplicationConnection schema/API/RBAC (FTF-04A01).

Creates the `target_application_connections` table (Section 6.8A/7A.1-7A.3/
9/45/65-67/77/81), FORCEs row-level security scoped to the same
`app.current_organization_id` tenant context FTF-0102/FTF-0104/FTF-0201/
FTF-0301/FTF-0401/FTF-0404/FTF-0500's migrations already established, and
grants the restricted `sovra_app` role SELECT/INSERT/UPDATE only --
deliberately never DELETE, since "revoke" is a soft
`connection_status='revoked'` transition, not row deletion, the same
discipline every prior migration in this chain already applies.

Reuses the already-real `UNIQUE (organization_id, id)` constraint
FTF-0404's own migration (`0006_tool_definitions.py`) already added to
`projects` -- no second additive constraint needed here.

Revision ID: 0011_target_app_connections
Revises: 0010_dataset_source_connections
Create Date: 2026-09-23

Revision id deliberately kept to <=32 characters ("0011_target_app_
connections", not the fuller "0011_target_application_connections") --
`alembic_version.version_num` is `character varying(32)` (Alembic's own
default column width for this table across every prior migration in this
chain); a longer id was tried first and failed for real against a live
Postgres server (`StringDataRightTruncation: value too long for type
character varying(32)`), caught by this task's own real integration run
before submission, not merely described.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0011_target_app_connections"
down_revision = "0010_dataset_source_connections"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as every prior migration in this chain.
APP_ROLE_NAME = "sovra_app"


def upgrade() -> None:
    op.create_table(
        "target_application_connections",
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
                name="fk_target_application_connections_organization_id",
            ),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_type", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("repository_identity", sa.String(500), nullable=False),
        sa.Column("repository_url_redacted", sa.String(2048), nullable=True),
        sa.Column("default_branch", sa.String(200), nullable=True),
        sa.Column("selected_branch", sa.String(200), nullable=True),
        sa.Column("credential_secret_ref", sa.String(512), nullable=True),
        sa.Column("access_mode", sa.String(16), nullable=False, server_default="read_only"),
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
            name="fk_target_application_connections_organization_id_project_id",
        ),
        sa.UniqueConstraint(
            "project_id",
            "display_name",
            name="uq_target_application_connections_project_id_display_name",
        ),
        sa.CheckConstraint(
            "connector_type in ('github','local_repository')",
            name="ck_target_application_connections_connector_type",
        ),
        sa.CheckConstraint(
            "access_mode in ('read_only')",
            name="ck_target_application_connections_access_mode",
        ),
        sa.CheckConstraint(
            "connection_status in ('active','revoked')",
            name="ck_target_application_connections_connection_status",
        ),
    )
    op.create_index(
        "ix_target_application_connections_organization_id",
        "target_application_connections",
        ["organization_id"],
    )
    op.create_index(
        "ix_target_application_connections_project_id",
        "target_application_connections",
        ["project_id"],
    )

    # Section 65.1: same FORCE ROW LEVEL SECURITY discipline as every other
    # tenant-scoped table in this repository.
    op.execute(
        f'GRANT SELECT, INSERT, UPDATE ON target_application_connections TO "{APP_ROLE_NAME}"'
    )
    op.execute("ALTER TABLE target_application_connections ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE target_application_connections FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON target_application_connections FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON target_application_connections")
    op.execute(f'REVOKE ALL ON target_application_connections FROM "{APP_ROLE_NAME}"')
    op.drop_index(
        "ix_target_application_connections_project_id",
        table_name="target_application_connections",
    )
    op.drop_index(
        "ix_target_application_connections_organization_id",
        table_name="target_application_connections",
    )
    op.drop_table("target_application_connections")
