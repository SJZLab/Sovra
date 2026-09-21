"""Budget policy (FTF-0403).

Creates `budget_policies` (Section 6.8's own `Project.budget_policy_id`
target -- FTF-0401's own migration already added that nullable UUID
column, but no table existed for it to reference) and
`budget_policy_events` (this task's own acceptance clause 1's "approval
history" half, plus every real reservation this policy ever grants, as
one append-only ledger -- the same "audit events are append-only"
discipline Section 6.24 already establishes, applied here as a
directly-queryable budget-specific projection). FORCEs row-level security
on both tables scoped to the same `app.current_organization_id` tenant
context every prior migration in this chain already establishes.

Grants `sovra_app` SELECT/INSERT/UPDATE on `budget_policies` -- UPDATE is
required: `apps/api/app/services/budget_policy.py`'s `reserve_budget`
decrements `remaining_amount` via a single atomic conditional `UPDATE ...
WHERE remaining_amount >= :amount`, never a read-then-write race -- but
only SELECT/INSERT on `budget_policy_events` (append-only, never UPDATE
or DELETE, the same grant-surface-matches-actual-DML-need discipline
FTF-0104's own `audit_events` migration already applies).

Also adds a purely additive `UNIQUE (organization_id, id)` constraint on
`budget_policies` itself, making a real composite foreign key
`projects(organization_id, budget_policy_id)` ->
`budget_policies(organization_id, id)` possible -- the same Section 65.1
"composite (organization_id, id) keys where the parent is tenant-owned"
precedent FTF-0401/FTF-0404's own migrations already applied, here
completing the reference FTF-0401's own migration left as a bare,
unconstrained nullable UUID column. `budget_policy_id` stays nullable (a
project is not required to have a budget policy attached), so Postgres's
own default MATCH SIMPLE only checks this constraint when the column is
actually non-null.

Revision ID: 0007_budget_policies
Revises: 0006_tool_definitions
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007_budget_policies"
down_revision = "0006_tool_definitions"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as every prior migration in this chain.
APP_ROLE_NAME = "sovra_app"

_TENANT_SETTING = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "budget_policies",
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
                "organizations.id", ondelete="CASCADE", name="fk_budget_policies_organization_id"
            ),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("authorized_amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("remaining_amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("scopes_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("approved_by", sa.String(200), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_budget_policies_organization_id_project_id",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_budget_policies_organization_id_id"),
        sa.CheckConstraint(
            "authorized_amount >= 0", name="ck_budget_policies_authorized_amount_nonneg"
        ),
        sa.CheckConstraint(
            "remaining_amount >= 0", name="ck_budget_policies_remaining_amount_nonneg"
        ),
        sa.CheckConstraint(
            "remaining_amount <= authorized_amount",
            name="ck_budget_policies_remaining_le_authorized",
        ),
        sa.CheckConstraint(
            "status in ('active','exhausted','revoked')", name="ck_budget_policies_status"
        ),
    )
    op.create_index("ix_budget_policies_organization_id", "budget_policies", ["organization_id"])
    op.create_index("ix_budget_policies_project_id", "budget_policies", ["project_id"])

    op.create_table(
        "budget_policy_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "budget_policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "budget_policies.id",
                ondelete="RESTRICT",
                name="fk_budget_policy_events_budget_policy_id",
            ),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("scope", sa.String(32), nullable=True),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "event_type in ('authorized','reserved','revoked')",
            name="ck_budget_policy_events_event_type",
        ),
        sa.CheckConstraint("amount >= 0", name="ck_budget_policy_events_amount_nonneg"),
    )
    op.create_index(
        "ix_budget_policy_events_budget_policy_id", "budget_policy_events", ["budget_policy_id"]
    )
    op.create_index(
        "ix_budget_policy_events_organization_id", "budget_policy_events", ["organization_id"]
    )

    op.create_foreign_key(
        "fk_projects_organization_id_budget_policy_id",
        "projects",
        "budget_policies",
        ["organization_id", "budget_policy_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )

    # Section 65.1: same FORCE ROW LEVEL SECURITY discipline as every other
    # tenant-scoped table in this repository.
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON budget_policies TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE budget_policies ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE budget_policies FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON budget_policies FOR ALL "
        f"USING (organization_id = {_TENANT_SETTING}) "
        f"WITH CHECK (organization_id = {_TENANT_SETTING})"
    )

    op.execute(f'GRANT SELECT, INSERT ON budget_policy_events TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE budget_policy_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE budget_policy_events FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON budget_policy_events FOR ALL "
        f"USING (organization_id = {_TENANT_SETTING}) "
        f"WITH CHECK (organization_id = {_TENANT_SETTING})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON budget_policy_events")
    op.execute(f'REVOKE ALL ON budget_policy_events FROM "{APP_ROLE_NAME}"')
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON budget_policies")
    op.execute(f'REVOKE ALL ON budget_policies FROM "{APP_ROLE_NAME}"')

    op.drop_constraint(
        "fk_projects_organization_id_budget_policy_id", "projects", type_="foreignkey"
    )

    op.drop_index("ix_budget_policy_events_organization_id", table_name="budget_policy_events")
    op.drop_index("ix_budget_policy_events_budget_policy_id", table_name="budget_policy_events")
    op.drop_table("budget_policy_events")

    op.drop_index("ix_budget_policies_project_id", table_name="budget_policies")
    op.drop_index("ix_budget_policies_organization_id", table_name="budget_policies")
    op.drop_table("budget_policies")
