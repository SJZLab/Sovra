"""LiteLLM connection CRUD (FTF-0201).

Creates the `litellm_connections` table (Section 2-3/38/65/77/81), FORCEs
row-level security scoped to the same `app.current_organization_id`
tenant context FTF-0102's migration established, and grants the
restricted `sovra_app` role SELECT/INSERT/UPDATE only -- deliberately
never DELETE, since "revoke" is a soft state transition
(`status='revoked'`), not row deletion, matching the same
grant-surface-matches-actual-DML-need discipline
`0002_audit_event_writer.py` already established for a different reason
(append-only there; soft-delete-only here).

Revision ID: 0003_litellm_connections
Revises: 0002_audit_event_writer
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_litellm_connections"
down_revision = "0002_audit_event_writer"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as 0001_org_workspace_schema.py/0002_audit_event_writer.py.
APP_ROLE_NAME = "sovra_app"


def upgrade() -> None:
    op.create_table(
        "litellm_connections",
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
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=False),
        sa.Column("secret_ref", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
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
        sa.UniqueConstraint(
            "organization_id", "name", name="uq_litellm_connections_organization_id_name"
        ),
        sa.CheckConstraint("status in ('active','revoked')", name="ck_litellm_connections_status"),
    )
    op.create_index(
        "ix_litellm_connections_organization_id", "litellm_connections", ["organization_id"]
    )

    # Section 65.1: same FORCE ROW LEVEL SECURITY discipline as
    # organizations/workspaces/audit_events -- the migration owner is not
    # the runtime role, so ENABLE alone would be bypassed by table
    # ownership.
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON litellm_connections TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE litellm_connections ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE litellm_connections FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON litellm_connections FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON litellm_connections")
    op.execute(f'REVOKE ALL ON litellm_connections FROM "{APP_ROLE_NAME}"')
    op.drop_index("ix_litellm_connections_organization_id", table_name="litellm_connections")
    op.drop_table("litellm_connections")
