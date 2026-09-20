"""TrainableArtifact schema/API (FTF-0301).

Creates the `trainable_artifacts` table (Section 6.7/13/34/65/68/77/81),
FORCEs row-level security scoped to the same `app.current_organization_id`
tenant context FTF-0102/FTF-0104/FTF-0201's migrations established, and
grants the restricted `sovra_app` role SELECT/INSERT/UPDATE only --
deliberately never DELETE, since "archive" is a soft state transition
(`status='archived'`), not row deletion, the same grant-surface-matches-
actual-DML-need discipline `0003_litellm_connections.py` already
established for a different resource.

Revision ID: 0004_trainable_artifacts
Revises: 0003_litellm_connections
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_trainable_artifacts"
down_revision = "0003_litellm_connections"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as every prior migration in this chain.
APP_ROLE_NAME = "sovra_app"


def upgrade() -> None:
    op.create_table(
        "trainable_artifacts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_uri", sa.String(2048), nullable=False),
        sa.Column("model_family", sa.String(200), nullable=False),
        sa.Column("base_model_revision", sa.String(200), nullable=False),
        sa.Column("weights_digest", sa.String(128), nullable=True),
        sa.Column("license_id", sa.String(200), nullable=False),
        sa.Column("license_accepted_by", sa.String(200), nullable=True),
        sa.Column("license_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "trust_remote_code_allowed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "required_python_packages_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "minimum_gpu_profile_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="registered"),
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
        sa.UniqueConstraint(
            "organization_id",
            "display_name",
            name="uq_trainable_artifacts_organization_id_display_name",
        ),
        sa.CheckConstraint(
            "status in ('registered','ready','archived')", name="ck_trainable_artifacts_status"
        ),
        sa.CheckConstraint(
            "source_type in ('huggingface','private_s3','local_filesystem','private_registry')",
            name="ck_trainable_artifacts_source_type",
        ),
    )
    op.create_index(
        "ix_trainable_artifacts_organization_id", "trainable_artifacts", ["organization_id"]
    )

    # Section 65.1: same FORCE ROW LEVEL SECURITY discipline as every other
    # tenant-scoped table in this repository -- the migration owner is not
    # the runtime role, so ENABLE alone would be bypassed by table ownership.
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON trainable_artifacts TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE trainable_artifacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE trainable_artifacts FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON trainable_artifacts FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON trainable_artifacts")
    op.execute(f'REVOKE ALL ON trainable_artifacts FROM "{APP_ROLE_NAME}"')
    op.drop_index("ix_trainable_artifacts_organization_id", table_name="trainable_artifacts")
    op.drop_table("trainable_artifacts")
