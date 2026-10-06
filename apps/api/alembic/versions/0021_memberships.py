"""Membership persistence (FTF-0103A, Section 6.4/45-47/65/77/81).

Owner-approved addendum (ADR `docs/adr/0003-task-addendum-ftf-0103a.md`;
contract `spec/addenda/FTF-0103A.json`). Section 6.4's `Membership` entity
had no table: every /api/v1 route denied every request because
`default_role_lookup` has nothing to read. This migration adds it.

`memberships` -- one row per (organization, optional workspace, issuer,
subject). `(issuer, subject)` is FTF-0101's validated identity (Section
65.3; `user_id` is that pair, never an email). `workspace_id` is nullable:
NULL is an organization-level membership, the only kind that may grant an
organization-level role (the role lookup filters on it); a workspace-level
row can never widen an organization role. The composite FK
`(organization_id, workspace_id) -> workspaces(organization_id, id)` keeps a
workspace-level row inside its own organization (MATCH SIMPLE: it is simply
not enforced when `workspace_id` is NULL). Uniqueness is NULLS NOT DISTINCT,
so two organization-level rows for the same identity cannot coexist (a plain
UNIQUE would treat the NULL workspaces as distinct and allow duplicates).

`role` is CHECK-constrained to the six Section 6.4 roles; `status` to
`active`/`revoked` (a revoked membership is retained as history and denied).

Security (Law 18): ENABLE + FORCE row-level security. The app role
(`sovra_app`) is granted SELECT only -- membership writes go through the
operator CLI under the migrate role (`app/auth/membership_admin_cli.py`),
there is no public write route. Two permissive SELECT/ALL policies:

- `tenant_isolation` (FOR ALL): `organization_id` equals the transaction-local
  `app.current_organization_id` -- the same discipline as every tenant table.
  No tenant context, or a foreign one, sees zero rows.
- `identity_self_lookup` (FOR SELECT only): a row is visible to a
  transaction that has bound BOTH `app.current_issuer` and
  `app.current_subject` to that row's own identity. This is what lets the
  login-time lookup ("does this validated identity have any active
  membership?") run before any organization is known, without ever exposing
  another identity's rows. Neither setting bound -> NULL -> no rows.

Revision ID: 0021_memberships
Revises: 0020_org_gateway_policies
Create Date: 2026-10-06
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0021_memberships"
down_revision = "0020_org_gateway_policies"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as every prior migration in this chain.
APP_ROLE_NAME = "sovra_app"


def upgrade() -> None:
    op.create_table(
        "memberships",
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
                "organizations.id", ondelete="CASCADE", name="fk_memberships_organization_id"
            ),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_memberships_organization_id_workspace_id",
        ),
        sa.CheckConstraint(
            "role IN ('organization_admin','workspace_admin','ml_engineer',"
            "'evaluator','approver','viewer')",
            name="ck_memberships_role",
        ),
        sa.CheckConstraint("status IN ('active','revoked')", name="ck_memberships_status"),
        sa.CheckConstraint("issuer <> ''", name="ck_memberships_issuer_not_blank"),
        sa.CheckConstraint("subject <> ''", name="ck_memberships_subject_not_blank"),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_memberships_org_workspace_identity ON memberships "
        "(organization_id, workspace_id, issuer, subject) NULLS NOT DISTINCT"
    )
    op.create_index("ix_memberships_issuer_subject", "memberships", ["issuer", "subject"])

    # Section 65.1 / Law 18: app role is SELECT only; FORCE so even the
    # table owner is subject to the policies below.
    op.execute(f'GRANT SELECT ON memberships TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memberships FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        "CREATE POLICY tenant_isolation ON memberships FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )
    op.execute(
        "CREATE POLICY identity_self_lookup ON memberships FOR SELECT "
        "USING ("
        "issuer = NULLIF(current_setting('app.current_issuer', true), '') "
        "AND subject = NULLIF(current_setting('app.current_subject', true), '')"
        ")"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS identity_self_lookup ON memberships")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON memberships")
    op.execute(f'REVOKE ALL ON memberships FROM "{APP_ROLE_NAME}"')
    op.drop_index("ix_memberships_issuer_subject", table_name="memberships")
    op.execute("DROP INDEX IF EXISTS uq_memberships_org_workspace_identity")
    op.drop_table("memberships")
