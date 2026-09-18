"""Audit event writer (FTF-0104).

Creates the `audit_events` table (Section 6.24), FORCEs row-level
security scoped to the same `app.current_organization_id` tenant context
FTF-0102's migration established, and grants the restricted `sovra_app`
role SELECT and INSERT only -- deliberately never UPDATE or DELETE -- so
"audit events are append-only" (Section 6.24) and this task's own
negative_acceptance clause ("redacted audit cannot be edited by normal
app role") are enforced by the database itself, not only by application
code choosing not to call an UPDATE/DELETE.

Revision ID: 0002_audit_event_writer
Revises: 0001_org_workspace_schema
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_audit_event_writer"
down_revision = "0001_org_workspace_schema"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as 0001_org_workspace_schema.py.
APP_ROLE_NAME = "sovra_app"


def upgrade() -> None:
    op.create_table(
        "audit_events",
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
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource_type", sa.String(128), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("ip_hash", sa.String(128), nullable=True),
        sa.Column(
            "details_json",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "actor_type in ('user','system','worker')", name="ck_audit_events_actor_type"
        ),
    )
    op.create_index("ix_audit_events_organization_id", "audit_events", ["organization_id"])
    op.create_index(
        "ix_audit_events_organization_id_created_at",
        "audit_events",
        ["organization_id", "created_at"],
    )

    # Section 6.24 "Audit events are append-only" / this task's own
    # negative_acceptance: no UPDATE, no DELETE grant at all -- the
    # restricted role's DML surface on this table is physically limited to
    # writing new rows and reading them back.
    op.execute(f'GRANT SELECT, INSERT ON audit_events TO "{APP_ROLE_NAME}"')

    # Section 65.1: same FORCE ROW LEVEL SECURITY discipline as
    # organizations/workspaces -- the migration owner is not the runtime
    # role, so ENABLE alone would be bypassed by table ownership.
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON audit_events FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON audit_events")
    op.execute(f'REVOKE ALL ON audit_events FROM "{APP_ROLE_NAME}"')
    op.drop_index("ix_audit_events_organization_id_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_organization_id", table_name="audit_events")
    op.drop_table("audit_events")
