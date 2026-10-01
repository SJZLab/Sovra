"""EvaluationSuite schema/API (FTF-0601).

Creates `evaluation_suites` (Section 23-30/69/9/45/65-67/77/81), FORCEs
row-level security scoped to the same `app.current_organization_id`
tenant context every prior migration in this chain already established,
and grants the restricted `sovra_app` role `SELECT`/`INSERT`/`UPDATE`
(never `DELETE` -- a suite is a durable evaluation record, never removed;
see `app/evaluation/evaluation_suite_schema.py`'s own docstring).

Adds two real composite foreign keys -- `(organization_id, project_id)` ->
`projects(organization_id, id)` (reusing
`0006_tool_definitions.py`'s own already-added `uq_projects_organization_id_id`)
and `(organization_id, agent_manifest_id)` -> `agent_manifests(organization_id,
id)` (reusing `0016_manifest_evidence.py`'s own already-added
`uq_agent_manifests_organization_id_id`) -- no further additive
constraint needed on either parent table.

`uq_evaluation_suites_project_id_version` is this task's own real
versioning invariant made real at the database level: one version number
is never reused within a project.

`ck_evaluation_suites_grading_logic_version_positive`,
`ck_evaluation_suites_purpose`, `ck_evaluation_suites_status`,
`ck_evaluation_suites_locked_at_iff_locked`,
`ck_evaluation_suites_reviewer_present_when_locked` and
`ck_evaluation_suites_independent_final_reviewer` are this task's own
negative_acceptance/Section-69.1 invariants made real at the database
level, never only application-level checks a bypassing raw write could
evade -- see the schema module's own docstring for the full, disclosed
reading of this task's own acceptance/negative_acceptance clauses each one
enforces.

Revision ID: 0017_evaluation_suites
Revises: 0016_manifest_evidence
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0017_evaluation_suites"
down_revision = "0016_manifest_evidence"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as every prior migration in this chain.
APP_ROLE_NAME = "sovra_app"

_ALLOWED_STATUSES = ("draft", "locked")
_ALLOWED_PURPOSES = ("development", "final")


def upgrade() -> None:
    op.create_table(
        "evaluation_suites",
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
                name="fk_evaluation_suites_organization_id",
            ),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("grading_logic_version", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.String(200), nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_evaluation_suites_organization_id_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "agent_manifest_id"],
            ["agent_manifests.organization_id", "agent_manifests.id"],
            ondelete="RESTRICT",
            name="fk_evaluation_suites_organization_id_manifest_id",
        ),
        sa.UniqueConstraint(
            "project_id", "version", name="uq_evaluation_suites_project_id_version"
        ),
        sa.CheckConstraint(
            "status in (" + ", ".join(f"'{v}'" for v in _ALLOWED_STATUSES) + ")",
            name="ck_evaluation_suites_status",
        ),
        sa.CheckConstraint(
            "purpose in (" + ", ".join(f"'{v}'" for v in _ALLOWED_PURPOSES) + ")",
            name="ck_evaluation_suites_purpose",
        ),
        sa.CheckConstraint("version >= 1", name="ck_evaluation_suites_version_positive"),
        sa.CheckConstraint(
            "grading_logic_version >= 1",
            name="ck_evaluation_suites_grading_logic_version_positive",
        ),
        sa.CheckConstraint(
            "(status = 'locked') = (locked_at IS NOT NULL)",
            name="ck_evaluation_suites_locked_at_iff_locked",
        ),
        sa.CheckConstraint(
            "NOT (purpose = 'final' AND status = 'locked' AND reviewer_id = created_by)",
            name="ck_evaluation_suites_independent_final_reviewer",
        ),
        sa.CheckConstraint(
            "status <> 'locked' OR reviewer_id IS NOT NULL",
            name="ck_evaluation_suites_reviewer_present_when_locked",
        ),
    )
    op.create_index(
        "ix_evaluation_suites_organization_id", "evaluation_suites", ["organization_id"]
    )
    op.create_index("ix_evaluation_suites_project_id", "evaluation_suites", ["project_id"])
    op.create_index(
        "ix_evaluation_suites_agent_manifest_id", "evaluation_suites", ["agent_manifest_id"]
    )

    # Section 65.1: same FORCE ROW LEVEL SECURITY discipline as every other
    # tenant-scoped table in this repository. UPDATE is genuinely needed
    # here (draft content edits, the draft -> locked transition) -- the
    # same "needs real UPDATE" precedent `agent_manifests` already
    # established (see this module's own docstring).
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON evaluation_suites TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE evaluation_suites ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE evaluation_suites FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON evaluation_suites FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON evaluation_suites")
    op.execute(f'REVOKE ALL ON evaluation_suites FROM "{APP_ROLE_NAME}"')
    op.drop_index("ix_evaluation_suites_agent_manifest_id", table_name="evaluation_suites")
    op.drop_index("ix_evaluation_suites_project_id", table_name="evaluation_suites")
    op.drop_index("ix_evaluation_suites_organization_id", table_name="evaluation_suites")
    op.drop_table("evaluation_suites")
