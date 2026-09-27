"""ManifestEvidence schema/API (FTF-04A20).

Adds `uq_agent_manifests_organization_id_id` (a purely additive `UNIQUE
(organization_id, id)` on the already-existing `agent_manifests` table,
the same "add the composite-FK-support constraint a later migration
needs, without touching the parent table's own creation" precedent
`0006_tool_definitions.py`'s own `uq_projects_organization_id_id` already
established for `projects`) and creates `manifest_evidence` (Section
6.8E/7A.6-7A.7/9/45/65-67/77/81), FORCEs row-level security scoped to the
same `app.current_organization_id` tenant context every prior migration in
this chain already established, and grants the restricted `sovra_app`
role `SELECT`/`INSERT` only -- never `UPDATE`, never `DELETE` (a
`ManifestEvidence` row is a permanent record, never edited or removed; see
`app/target_connectors/manifest_evidence_schema.py`'s own docstring,
"Immutability").

Adds one real composite foreign key -- `(organization_id,
agent_manifest_id)` -> `agent_manifests(organization_id, id)`, via the new
unique constraint above.

The nine real `CHECK` constraints below are this task's own per-
`evidence_type` field-shape invariants made real at the database level,
never only an application-level check a bypassing raw `INSERT` could
evade -- see the schema module's own docstring for the full, disclosed
reading of this task's own acceptance/negative_acceptance clauses each one
enforces.

Revision ID: 0016_manifest_evidence
Revises: 0015_agent_manifests
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0016_manifest_evidence"
down_revision = "0015_agent_manifests"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as every prior migration in this chain.
APP_ROLE_NAME = "sovra_app"

_ALLOWED_EVIDENCE_TYPES = (
    "repo_file",
    "repo_symbol",
    "config",
    "test",
    "runtime_probe",
    "user_input",
)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_agent_manifests_organization_id_id", "agent_manifests", ["organization_id", "id"]
    )

    op.create_table(
        "manifest_evidence",
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
                "organizations.id", ondelete="CASCADE", name="fk_manifest_evidence_organization_id"
            ),
            nullable=False,
        ),
        sa.Column("agent_manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_path", sa.String(500), nullable=False),
        sa.Column("evidence_type", sa.String(32), nullable=False),
        sa.Column("repository_commit_sha", sa.String(64), nullable=True),
        sa.Column("file_path", sa.String(1000), nullable=True),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column("symbol", sa.String(500), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("evidence_json", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "agent_manifest_id"],
            ["agent_manifests.organization_id", "agent_manifests.id"],
            ondelete="CASCADE",
            name="fk_manifest_evidence_organization_id_manifest_id",
        ),
        sa.CheckConstraint(
            "evidence_type in (" + ", ".join(f"'{v}'" for v in _ALLOWED_EVIDENCE_TYPES) + ")",
            name="ck_manifest_evidence_evidence_type",
        ),
        sa.CheckConstraint(
            "(confidence_score IS NULL) OR (confidence_score >= 0.0 AND confidence_score <= 1.0)",
            name="ck_manifest_evidence_confidence_score_range",
        ),
        sa.CheckConstraint(
            "(evidence_type != 'user_input') = (repository_commit_sha IS NOT NULL)",
            name="ck_manifest_evidence_commit_sha_iff_detected",
        ),
        sa.CheckConstraint(
            "(evidence_type != 'user_input') = (confidence_score IS NOT NULL)",
            name="ck_manifest_evidence_confidence_iff_detected",
        ),
        sa.CheckConstraint(
            "(evidence_type in ('repo_file','repo_symbol','config','test')) "
            "= (file_path IS NOT NULL)",
            name="ck_manifest_evidence_file_path_iff_repo_cited",
        ),
        sa.CheckConstraint(
            "(evidence_type in ('repo_file','repo_symbol','config','test')) "
            "= (content_hash IS NOT NULL)",
            name="ck_manifest_evidence_content_hash_iff_repo_cited",
        ),
        sa.CheckConstraint(
            "(evidence_type = 'repo_symbol') = (symbol IS NOT NULL)",
            name="ck_manifest_evidence_symbol_iff_repo_symbol",
        ),
        sa.CheckConstraint(
            "(line_start IS NULL) = (line_end IS NULL)",
            name="ck_manifest_evidence_line_refs_paired",
        ),
        sa.CheckConstraint(
            "(line_start IS NULL) OR (line_start >= 1 AND line_end >= line_start)",
            name="ck_manifest_evidence_line_refs_valid_range",
        ),
        sa.CheckConstraint(
            "(evidence_type in ('repo_file','repo_symbol')) = (line_start IS NOT NULL)",
            name="ck_manifest_evidence_line_refs_iff_line_anchored",
        ),
    )
    op.create_index(
        "ix_manifest_evidence_organization_id", "manifest_evidence", ["organization_id"]
    )
    op.create_index(
        "ix_manifest_evidence_agent_manifest_id", "manifest_evidence", ["agent_manifest_id"]
    )

    # Section 65.1: same FORCE ROW LEVEL SECURITY discipline as every other
    # tenant-scoped table in this repository. SELECT/INSERT only -- see
    # this migration's own module docstring for why this table never needs
    # UPDATE/DELETE, unlike agent_manifests itself.
    op.execute(f'GRANT SELECT, INSERT ON manifest_evidence TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE manifest_evidence ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE manifest_evidence FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON manifest_evidence FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON manifest_evidence")
    op.execute(f'REVOKE ALL ON manifest_evidence FROM "{APP_ROLE_NAME}"')
    op.drop_index("ix_manifest_evidence_agent_manifest_id", table_name="manifest_evidence")
    op.drop_index("ix_manifest_evidence_organization_id", table_name="manifest_evidence")
    op.drop_table("manifest_evidence")
    op.drop_constraint("uq_agent_manifests_organization_id_id", "agent_manifests", type_="unique")
