"""EvaluationCase persistence (FTF-0602-P1, Section 6.14/23/24/26/27/39/65/69.1/69.2/77/78.2/81).

Migration prerequisite for FTF-0602's own UI half ("Evaluation case editor/
import"): FTF-0602's packet closes its own schema ("any required change is a
separately claimed migration prerequisite"), and nothing persisted an
evaluation case before this slice.

`evaluation_cases` -- one row per (suite, case_key). The three JSONB payloads
(`input_json`, `expected_json`, `grader_config_json`) are database-checked to
be JSON objects of at most 2 MiB each, and `expected_json` can never be `{}`
(a case with no expectation grades nothing). `grader_type` is the closed
Section 23 vocabulary of 13 graders, `severity` a closed four-value set, and
`weight` a strictly positive number.

Section 27 / 69.2: a critical case must be checked by an oracle that is
independent of the model -- `ck_evaluation_cases_critical_independent_grader`
allows `critical = true` only for `tool_name`, `tool_arguments`,
`tool_sequence`, `state_transition`, `forbidden_action` and
`required_refusal`. `exact_match`, `contains_fact` and `llm_judge` (the last
being the "judge alone must not decide a critical safety call" rule) cannot
carry a critical case even through a raw write that bypasses the API.

Composite foreign keys keep every row inside its organization and its project:
`(organization_id, project_id)` -> `projects(organization_id, id)` and
`(organization_id, project_id, suite_id)` -> `evaluation_suites(organization_id,
project_id, id)` (both RESTRICT). The one supporting unique constraint
`(organization_id, project_id, id)` is added to `evaluation_suites` for that
reference; `projects` already carries `(organization_id, id)`
(0006_tool_definitions).

`sovra_app` gets SELECT/INSERT/UPDATE (a case is edited in place while its
suite is a draft) and never DELETE. The table ENABLEs and FORCEs row-level
security with the same `tenant_isolation` policy every prior migration uses
(Section 65.1).

Revision ID: 0023_evaluation_cases
Revises: 0022_model_catalog_entries
Create Date: 2026-10-07
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0023_evaluation_cases"
down_revision = "0022_model_catalog_entries"
branch_labels = None
depends_on = None

APP_ROLE_NAME = "sovra_app"

_SEVERITIES = ("low", "medium", "high", "critical")
_GRADER_TYPES = (
    "exact_match",
    "json_schema",
    "tool_name",
    "tool_arguments",
    "tool_sequence",
    "state_transition",
    "forbidden_action",
    "required_refusal",
    "contains_fact",
    "numeric_tolerance",
    "latency",
    "cost",
    "llm_judge",
)
_CRITICAL_GRADER_TYPES = (
    "tool_name",
    "tool_arguments",
    "tool_sequence",
    "state_transition",
    "forbidden_action",
    "required_refusal",
)
_MAX_JSON_BYTES = 2 * 1024 * 1024


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_evaluation_suites_org_project_id_id",
        "evaluation_suites",
        ["organization_id", "project_id", "id"],
    )

    op.create_table(
        "evaluation_cases",
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
                "organizations.id", ondelete="CASCADE", name="fk_evaluation_cases_organization_id"
            ),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suite_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_key", sa.String(200), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("critical", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("language", sa.String(35), nullable=False),
        sa.Column("group_key", sa.String(200), nullable=False),
        sa.Column("input_json", postgresql.JSONB(), nullable=False),
        sa.Column("expected_json", postgresql.JSONB(), nullable=False),
        sa.Column("grader_type", sa.String(32), nullable=False),
        sa.Column(
            "grader_config_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("weight", sa.Float(), nullable=False, server_default=sa.text("1")),
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
            ondelete="RESTRICT",
            name="fk_evaluation_cases_organization_id_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id", "suite_id"],
            [
                "evaluation_suites.organization_id",
                "evaluation_suites.project_id",
                "evaluation_suites.id",
            ],
            ondelete="RESTRICT",
            name="fk_evaluation_cases_organization_id_project_id_suite_id",
        ),
        sa.UniqueConstraint("suite_id", "case_key", name="uq_evaluation_cases_suite_id_case_key"),
        sa.UniqueConstraint("organization_id", "id", name="uq_evaluation_cases_org_id_id"),
        sa.CheckConstraint("trim(case_key) <> ''", name="ck_evaluation_cases_case_key_not_blank"),
        sa.CheckConstraint("trim(category) <> ''", name="ck_evaluation_cases_category_not_blank"),
        sa.CheckConstraint("trim(language) <> ''", name="ck_evaluation_cases_language_not_blank"),
        sa.CheckConstraint("trim(group_key) <> ''", name="ck_evaluation_cases_group_key_not_blank"),
        sa.CheckConstraint(
            f"severity IN ({_quoted(_SEVERITIES)})", name="ck_evaluation_cases_severity"
        ),
        sa.CheckConstraint(
            f"grader_type IN ({_quoted(_GRADER_TYPES)})", name="ck_evaluation_cases_grader_type"
        ),
        sa.CheckConstraint(
            f"NOT critical OR grader_type IN ({_quoted(_CRITICAL_GRADER_TYPES)})",
            name="ck_evaluation_cases_critical_independent_grader",
        ),
        sa.CheckConstraint(
            "weight > 0 AND weight < 'Infinity'::float8", name="ck_evaluation_cases_weight_positive"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_json) = 'object' "
            "AND jsonb_typeof(expected_json) = 'object' "
            "AND jsonb_typeof(grader_config_json) = 'object'",
            name="ck_evaluation_cases_payloads_are_objects",
        ),
        sa.CheckConstraint(
            "expected_json <> '{}'::jsonb", name="ck_evaluation_cases_expected_not_empty"
        ),
        sa.CheckConstraint(
            f"octet_length(input_json::text) <= {_MAX_JSON_BYTES} "
            f"AND octet_length(expected_json::text) <= {_MAX_JSON_BYTES} "
            f"AND octet_length(grader_config_json::text) <= {_MAX_JSON_BYTES}",
            name="ck_evaluation_cases_payload_size",
        ),
    )
    op.create_index("ix_evaluation_cases_organization_id", "evaluation_cases", ["organization_id"])
    op.create_index("ix_evaluation_cases_suite_id", "evaluation_cases", ["suite_id"])

    op.execute(f'GRANT SELECT, INSERT, UPDATE ON evaluation_cases TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE evaluation_cases ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE evaluation_cases FORCE ROW LEVEL SECURITY")
    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON evaluation_cases FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON evaluation_cases")
    op.execute(f'REVOKE ALL ON evaluation_cases FROM "{APP_ROLE_NAME}"')
    op.drop_index("ix_evaluation_cases_suite_id", table_name="evaluation_cases")
    op.drop_index("ix_evaluation_cases_organization_id", table_name="evaluation_cases")
    op.drop_table("evaluation_cases")
    op.drop_constraint(
        "uq_evaluation_suites_org_project_id_id", "evaluation_suites", type_="unique"
    )
