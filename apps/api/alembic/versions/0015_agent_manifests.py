"""AgentManifest schema/versioning/hash (FTF-04A19).

Creates `agent_manifests` (Section 6.8D/7A.6-7A.7/9/45/65-67/77/81), FORCEs
row-level security scoped to the same `app.current_organization_id` tenant
context every prior migration in this chain already established, and
grants the restricted `sovra_app` role `SELECT`/`INSERT`/`UPDATE` (never
`DELETE` -- a manifest is superseded, never removed; see
`app/target_connectors/agent_manifest_schema.py`'s own docstring for why
this table genuinely needs `UPDATE`, unlike FTF-04A07/FTF-1001's own
insert-only tables).

Adds two real composite foreign keys -- `(organization_id, project_id)` ->
`projects(organization_id, id)` (reusing the already-existing unique
constraint on `projects`, the same FTF-1001 precedent) and
`(organization_id, target_source_snapshot_id)` ->
`target_source_snapshots(organization_id, id)` (reusing FTF-04A07's own
already-added `UNIQUE (organization_id, id)` constraint -- no further
additive constraint needed on that table).

`uq_agent_manifests_project_id_version`/`ix_agent_manifests_project_id_locked`
are this task's own real versioning/supersession invariants made real at
the database level, never only an application-level check a bypassing
write could evade: one version number is never reused within a project,
and at most one `locked` manifest can ever exist per project at once (the
real basis for `agent_manifest_crud.py`'s own supersession mechanic -- see
the schema module's own docstring for why no `supersedes_manifest_id`
field is needed).

`ck_agent_manifests_hash_iff_locked`/`_approval_pair`/`_approval_iff_locked`
are real, database-level enforcement of this task's own structural
invariants ("locking computes manifest_sha256", "the user must approve") --
never only application-level checks a bypassing write could evade.

Revision ID: 0015_agent_manifests
Revises: 0014_discovered_agent_candidates
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0015_agent_manifests"
down_revision = "0014_discovered_agent_candidates"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as every prior migration in this chain.
APP_ROLE_NAME = "sovra_app"

_ALLOWED_STATUSES = ("draft", "review_required", "locked", "superseded")

_JSON_CONTENT_COLUMNS = (
    "agent_entrypoint_json",
    "current_model_binding_json",
    "system_prompt_refs_json",
    "tool_contracts_json",
    "input_contract_json",
    "output_contract_json",
    "mandate_json",
    "forbidden_actions_json",
    "permission_model_json",
    "required_context_json",
    "runtime_dependencies_json",
    "evaluation_harness_json",
    "trace_source_contract_json",
    "deployment_contract_json",
)


def upgrade() -> None:
    op.create_table(
        "agent_manifests",
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
                "organizations.id", ondelete="CASCADE", name="fk_agent_manifests_organization_id"
            ),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("agent_name", sa.String(200), nullable=True),
        *(sa.Column(name, postgresql.JSONB, nullable=True) for name in _JSON_CONTENT_COLUMNS),
        sa.Column("manifest_sha256", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("approved_by", sa.String(200), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_agent_manifests_organization_id_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "target_source_snapshot_id"],
            ["target_source_snapshots.organization_id", "target_source_snapshots.id"],
            ondelete="RESTRICT",
            name="fk_agent_manifests_organization_id_snapshot_id",
        ),
        sa.UniqueConstraint("project_id", "version", name="uq_agent_manifests_project_id_version"),
        sa.CheckConstraint(
            "status in (" + ", ".join(f"'{v}'" for v in _ALLOWED_STATUSES) + ")",
            name="ck_agent_manifests_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_manifests_version_positive"),
        sa.CheckConstraint(
            "(status in ('locked','superseded')) = (manifest_sha256 IS NOT NULL)",
            name="ck_agent_manifests_hash_iff_locked",
        ),
        sa.CheckConstraint(
            "(approved_by IS NULL) = (approved_at IS NULL)",
            name="ck_agent_manifests_approval_pair",
        ),
        sa.CheckConstraint(
            "(status in ('locked','superseded')) = (approved_by IS NOT NULL)",
            name="ck_agent_manifests_approval_iff_locked",
        ),
    )
    op.create_index("ix_agent_manifests_organization_id", "agent_manifests", ["organization_id"])
    op.create_index("ix_agent_manifests_project_id", "agent_manifests", ["project_id"])
    op.create_index(
        "ix_agent_manifests_snapshot_id", "agent_manifests", ["target_source_snapshot_id"]
    )
    # This task's own real "at most one locked manifest per project"
    # supersession invariant -- see this migration's own module docstring.
    op.create_index(
        "ix_agent_manifests_project_id_locked",
        "agent_manifests",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'locked'"),
    )

    # Section 65.1: same FORCE ROW LEVEL SECURITY discipline as every other
    # tenant-scoped table in this repository. UPDATE is genuinely needed
    # here (content edits, status transitions) -- see this module's own
    # docstring for why that differs from FTF-04A07/FTF-1001's own grants.
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON agent_manifests TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE agent_manifests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_manifests FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON agent_manifests FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON agent_manifests")
    op.execute(f'REVOKE ALL ON agent_manifests FROM "{APP_ROLE_NAME}"')
    op.drop_index("ix_agent_manifests_project_id_locked", table_name="agent_manifests")
    op.drop_index("ix_agent_manifests_snapshot_id", table_name="agent_manifests")
    op.drop_index("ix_agent_manifests_project_id", table_name="agent_manifests")
    op.drop_index("ix_agent_manifests_organization_id", table_name="agent_manifests")
    op.drop_table("agent_manifests")
