"""Organization/Workspace tenant schema (FTF-0102).

Creates the two tenant-root tables (Section 6.1/6.2), the distinct
least-privileged application role Section 65.1 requires, and FORCEs
row-level security on both tables scoped to
`app.current_organization_id` (see
`app/auth/organization_workspace_schema.py::set_tenant_context`).

Revision ID: 0001_org_workspace_schema
Revises:
Create Date: 2026-09-18
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_org_workspace_schema"
down_revision = None
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME — kept as
# a literal here (not imported) so this migration stays self-contained and
# never silently changes behavior if that module is refactored later.
APP_ROLE_NAME = "sovra_app"
_STATUS_VALUES = ("active", "suspended", "archived")

# A dollar-quote tag Postgres treats as an opaque string delimiter with no
# escape processing inside it — the standard safe way to embed an
# arbitrary-content string (here, a password from the environment, never a
# request) directly in DDL, since CREATE ROLE's PASSWORD clause is a plain
# string-constant grammar position that does not accept a bind parameter.
_PASSWORD_DELIMITER = "$sovra_app_pwd$"


def _app_role_password() -> str:
    password = os.environ.get("SOVRA_APP_DB_PASSWORD")
    if not password:
        raise RuntimeError(
            "SOVRA_APP_DB_PASSWORD is required to create the restricted "
            f"'{APP_ROLE_NAME}' role — no development fallback credential "
            "exists (same rule as configuration_system.py)."
        )
    if _PASSWORD_DELIMITER in password:
        raise RuntimeError("SOVRA_APP_DB_PASSWORD may not contain the internal DDL delimiter")
    return password


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column(
            "default_security_profile", sa.String(64), nullable=False, server_default="standard"
        ),
        sa.Column("default_currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("default_timezone", sa.String(64), nullable=False, server_default="UTC"),
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
        sa.CheckConstraint(
            "status in ('active','suspended','archived')", name="ck_organizations_status"
        ),
    )

    op.create_table(
        "workspaces",
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
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("organization_id", "slug", name="uq_workspaces_organization_id_slug"),
        sa.CheckConstraint(
            "status in ('active','suspended','archived')", name="ck_workspaces_status"
        ),
    )
    op.create_index("ix_workspaces_organization_id", "workspaces", ["organization_id"])

    password = _app_role_password()
    op.execute(
        f'CREATE ROLE "{APP_ROLE_NAME}" WITH LOGIN '
        f"PASSWORD {_PASSWORD_DELIMITER}{password}{_PASSWORD_DELIMITER} "
        "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE INHERIT"
    )
    op.execute(f'GRANT USAGE ON SCHEMA public TO "{APP_ROLE_NAME}"')
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON organizations TO "{APP_ROLE_NAME}"')
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON workspaces TO "{APP_ROLE_NAME}"')

    # Section 65.1: "Enable and FORCE row-level security on tenant tables;
    # application runtime role is not the table owner, a superuser or
    # BYPASSRLS." FORCE (not just ENABLE) matters because the migration
    # role owns these tables — ENABLE alone is bypassed by the owner.
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspaces FORCE ROW LEVEL SECURITY")

    # `current_setting(..., true)` returns NULL rather than raising when the
    # session never called set_tenant_context — NULLIF(...,'')::uuid then
    # also yields NULL for an explicitly cleared ('') context. Either way
    # `id = NULL` / `organization_id = NULL` is never true, so a connection
    # with no tenant context sees and can write zero rows: "missing tenant
    # context" fails closed, not open.
    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON organizations FOR ALL "
        f"USING (id = {tenant_setting}) "
        f"WITH CHECK (id = {tenant_setting})"
    )
    op.execute(
        f"CREATE POLICY tenant_isolation ON workspaces FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON workspaces")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON organizations")
    op.execute(f'REVOKE ALL ON workspaces FROM "{APP_ROLE_NAME}"')
    op.execute(f'REVOKE ALL ON organizations FROM "{APP_ROLE_NAME}"')
    op.execute(f'REVOKE USAGE ON SCHEMA public FROM "{APP_ROLE_NAME}"')
    op.execute(f'DROP ROLE IF EXISTS "{APP_ROLE_NAME}"')
    op.drop_index("ix_workspaces_organization_id", table_name="workspaces")
    op.drop_table("workspaces")
    op.drop_table("organizations")
