"""Evaluation suite sample plans and lock hash (FTF-0603-F1, Section 23-30/26/69/77/81).

Migration prerequisite for FTF-0603 ("Suite validation/locking"): its packet
closes its own schema ("any required change is a separately claimed migration
prerequisite"), and its negative acceptance -- "Missing critical categories,
insufficient sample plan and unresolved expected output block lock" -- needs
the plan and the locked hash on the server, not only in the browser.

`evaluation_suite_sample_plans` -- at most one row per suite, written in the
same transaction that locks the suite (`evaluation_suite_lock_gate.py`). It
records the reviewer's plan (required categories and the three minimum
counts), the counts actually observed at lock, and `suite_sha256`, the
canonical hash of the suite identity and every case at the moment of lock.

The row is insert-only: `sovra_app` gets SELECT and INSERT, never UPDATE or
DELETE, so a recorded plan or hash can never be rewritten after the lock. The
database backs the application floors with CHECKs (at least one category, at
most 200, counts in range, a 64-character lowercase hex hash) and keeps every
row inside its organization, project and suite with composite foreign keys
(`(organization_id, project_id)` -> `projects`, `(organization_id,
project_id, suite_id)` -> `evaluation_suites`, both RESTRICT; the referenced
unique keys exist since 0006/0023). The table ENABLEs and FORCEs row-level
security with the same `tenant_isolation` policy every prior migration uses
(Section 65.1).

Revision ID: 0039_eval_suite_sample_plans
Revises: 0038_dataset_mapping_requests
Create Date: 2026-11-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0039_eval_suite_sample_plans"
down_revision = "0038_dataset_mapping_requests"
branch_labels = None
depends_on = None

APP_ROLE_NAME = "sovra_app"
_TABLE = "evaluation_suite_sample_plans"
_MAX_PLAN_CATEGORIES = 200
_MAX_PLAN_COUNT = 1_000_000


def upgrade() -> None:
    op.create_table(
        "evaluation_suite_sample_plans",
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
                name="fk_evaluation_suite_sample_plans_organization_id",
            ),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suite_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("required_categories", postgresql.JSONB(), nullable=False),
        sa.Column("min_independent_tasks", sa.Integer(), nullable=False),
        sa.Column("min_critical_scenarios", sa.Integer(), nullable=False),
        sa.Column("min_cases_per_category", sa.Integer(), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("independent_task_count", sa.Integer(), nullable=False),
        sa.Column("critical_scenario_count", sa.Integer(), nullable=False),
        sa.Column("suite_sha256", sa.String(64), nullable=False),
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
            ondelete="RESTRICT",
            name="fk_evaluation_suite_sample_plans_organization_id_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id", "suite_id"],
            [
                "evaluation_suites.organization_id",
                "evaluation_suites.project_id",
                "evaluation_suites.id",
            ],
            ondelete="RESTRICT",
            name="fk_evaluation_suite_sample_plans_org_project_suite",
        ),
        sa.UniqueConstraint("suite_id", name="uq_evaluation_suite_sample_plans_suite_id"),
        sa.CheckConstraint(
            "jsonb_typeof(required_categories) = 'array' "
            "AND jsonb_array_length(required_categories) >= 1 "
            f"AND jsonb_array_length(required_categories) <= {_MAX_PLAN_CATEGORIES}",
            name="ck_evaluation_suite_sample_plans_categories",
        ),
        sa.CheckConstraint(
            f"min_independent_tasks BETWEEN 1 AND {_MAX_PLAN_COUNT} "
            f"AND min_critical_scenarios BETWEEN 0 AND {_MAX_PLAN_COUNT} "
            f"AND min_cases_per_category BETWEEN 1 AND {_MAX_PLAN_COUNT}",
            name="ck_evaluation_suite_sample_plans_plan_counts",
        ),
        sa.CheckConstraint(
            "case_count >= independent_task_count "
            "AND independent_task_count >= min_independent_tasks "
            "AND critical_scenario_count >= min_critical_scenarios",
            name="ck_evaluation_suite_sample_plans_observed_counts",
        ),
        sa.CheckConstraint(
            "suite_sha256 ~ '^[0-9a-f]{64}$'", name="ck_evaluation_suite_sample_plans_sha256"
        ),
        sa.CheckConstraint(
            "trim(created_by) <> ''", name="ck_evaluation_suite_sample_plans_created_by"
        ),
    )
    op.create_index("ix_evaluation_suite_sample_plans_organization_id", _TABLE, ["organization_id"])

    op.execute(f'GRANT SELECT, INSERT ON evaluation_suite_sample_plans TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE evaluation_suite_sample_plans ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE evaluation_suite_sample_plans FORCE ROW LEVEL SECURITY")
    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON evaluation_suite_sample_plans FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON evaluation_suite_sample_plans")
    op.execute(f'REVOKE ALL ON evaluation_suite_sample_plans FROM "{APP_ROLE_NAME}"')
    op.drop_index("ix_evaluation_suite_sample_plans_organization_id", table_name=_TABLE)
    op.drop_table("evaluation_suite_sample_plans")
