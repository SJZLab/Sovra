"""Budget policy (FTF-0403).

Creates the `budget_policies` table (Section 6.8/7A/22/65-66/77/81's own
"initial ... budget policy" framing of Phase 4 DoD -- see
FINE_TUNING_FACTORY_RELEASE_1.md's own Phase 4 DoD list, "project records
project type, initial security/budget policy, and user-entered
objective"), FORCEs row-level security scoped to the same
`app.current_organization_id` tenant context FTF-0102/FTF-0104/FTF-0201/
FTF-0301/FTF-0401/FTF-0404's migrations already established, and grants
the restricted `sovra_app` role SELECT/INSERT/UPDATE only -- deliberately
never DELETE, matching FTF-0404's own precedent: a budget policy is a
financial-authorization record this task's own negative_acceptance
("Negative/unknown rates and concurrent allocation beyond available
reservation cannot be accepted") depends on never silently disappearing
out from under an already-reserved allocation.

`projects.budget_policy_id` is FTF-0401's own already-CERTIFIED, frozen
schema column -- its own module docstring explicitly disclosed it as
"a schema column this task's own routes never accept, for a later task to
write through" (FTF-0403). This migration is that later task: it adds the
real, DB-enforced composite foreign key `projects(organization_id,
budget_policy_id)` -> `budget_policies(organization_id, id)`, the same
Section 65.1 "composite (organization_id, id) keys where the parent is
tenant-owned" pattern FTF-0401/FTF-0404's own migrations already applied
to `workspaces`/`projects` respectively -- purely additive, and
NULL-safe (a project's own `budget_policy_id` stays nullable, so no
existing certified row is invalidated).

`UNIQUE (project_id)` -- a project has at most one budget policy for the
whole of this task's own scope (Release 1 draws no distinction between
"the project's budget policy" and "a budget policy," and Phase 4 DoD's
own "initial ... budget policy" wording names exactly one per project).
Revising it (`revise_budget_policy`) mutates the one row in place
(`row_version`-guarded optimistic concurrency, Section 6's own general
"mutable rows carry row_version for concurrency checks" rule) rather than
versioning a new row the way FTF-0404's own `ToolDefinition` does --
"approval history" (this task's own acceptance clause 1) is carried by
FTF-0104's own already-CERTIFIED, append-only `audit_events` table
instead (every mutation writes one in the same transaction, the same
discipline FTF-0201/FTF-0301/FTF-0401/FTF-0404 already established), so
this task needs no second, independent "approval" table of its own.

`CHECK (reserved_amount <= total_amount)` and `CHECK (reserved_amount >=
0)` are the real, DB-enforced backstop behind this task's own negative-
acceptance guard against concurrent over-allocation
(`app/budget/budget_policy_crud.py`'s own `reserve_budget_allocation`
issues a single conditional `UPDATE ... WHERE reserved_amount + :amount
<= total_amount` statement, never a read-then-write race) -- even a bug
in that application-level guard could not push `reserved_amount` past
`total_amount` in the database itself.

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
        sa.Column("total_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("scopes_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "reserved_amount", sa.Numeric(18, 4), nullable=False, server_default=sa.text("0")
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
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_budget_policies_organization_id_project_id",
        ),
        sa.UniqueConstraint("project_id", name="uq_budget_policies_project_id"),
        sa.CheckConstraint("total_amount > 0", name="ck_budget_policies_total_amount_positive"),
        sa.CheckConstraint(
            "reserved_amount >= 0", name="ck_budget_policies_reserved_amount_non_negative"
        ),
        sa.CheckConstraint(
            "reserved_amount <= total_amount", name="ck_budget_policies_reserved_amount_bounded"
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_budget_policies_row_version_positive"),
    )
    op.create_index("ix_budget_policies_organization_id", "budget_policies", ["organization_id"])
    op.create_unique_constraint(
        "uq_budget_policies_organization_id_id", "budget_policies", ["organization_id", "id"]
    )

    # Section 65.1: same FORCE ROW LEVEL SECURITY discipline as every other
    # tenant-scoped table in this repository.
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON budget_policies TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE budget_policies ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE budget_policies FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON budget_policies FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )

    # See module docstring: this is FTF-0401's own disclosed "for a later
    # task to write through" column, made real -- purely additive, NULL-safe.
    op.create_foreign_key(
        "fk_projects_organization_id_budget_policy_id",
        "projects",
        "budget_policies",
        ["organization_id", "budget_policy_id"],
        ["organization_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_projects_organization_id_budget_policy_id", "projects", type_="foreignkey"
    )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON budget_policies")
    op.execute(f'REVOKE ALL ON budget_policies FROM "{APP_ROLE_NAME}"')
    op.drop_constraint("uq_budget_policies_organization_id_id", "budget_policies", type_="unique")
    op.drop_index("ix_budget_policies_organization_id", table_name="budget_policies")
    op.drop_table("budget_policies")
