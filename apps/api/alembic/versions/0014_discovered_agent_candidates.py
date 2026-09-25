"""DiscoveredAgentCandidate schema (FTF-04A17).

Creates `discovered_agent_candidates` (Section 6.8C/7A.4/9/45/65-67/77/81),
FORCEs row-level security scoped to the same `app.current_organization_id`
tenant context every prior migration in this chain already established,
and grants the restricted `sovra_app` role SELECT/INSERT only -- never
UPDATE or DELETE, the same immutability discipline FTF-04A07's own
`0012_target_source_snapshots.py` already established for
`target_source_snapshots` (a discovered candidate is a fact about what a
real analysis run found; see `app/target_connectors/
discovered_agent_candidate_schema.py`'s own docstring for why there is no
legitimate mutation path for this table at all).

Adds a real composite foreign key
`(organization_id, target_source_snapshot_id)` ->
`target_source_snapshots(organization_id, id)`, reusing the `UNIQUE
(organization_id, id)` constraint FTF-04A07's own migration already added
to that table -- no further additive constraint is needed on the parent.

`uq_discovered_agent_candidates_snapshot_id_candidate_key` is this task's
own real deduplication identity (see the schema module's own docstring):
a real database-level guarantee that a second analysis run cannot create
a silent duplicate row for the same already-discovered entity, never only
an application-level check a bypassing write could evade.

`ck_discovered_agent_candidates_confidence_score_range`/
`_discovery_status` are real, database-level enforcement of this task's
own structural invariants -- never only application-level checks a
bypassing write could evade.

Revision ID: 0014_discovered_agent_candidates
Revises: 0013_component_bindings
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0014_discovered_agent_candidates"
down_revision = "0013_component_bindings"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as every prior migration in this chain.
APP_ROLE_NAME = "sovra_app"

_ALLOWED_DISCOVERY_STATUSES = ("candidate",)


def upgrade() -> None:
    op.create_table(
        "discovered_agent_candidates",
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
                name="fk_discovered_agent_candidates_organization_id",
            ),
            nullable=False,
        ),
        sa.Column("target_source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_key", sa.String(512), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("framework_or_runtime", sa.String(100), nullable=True),
        sa.Column("entrypoint_evidence_json", postgresql.JSONB, nullable=True),
        sa.Column("model_evidence_json", postgresql.JSONB, nullable=True),
        sa.Column("prompt_evidence_json", postgresql.JSONB, nullable=True),
        sa.Column("tool_evidence_json", postgresql.JSONB, nullable=True),
        sa.Column("test_evidence_json", postgresql.JSONB, nullable=True),
        sa.Column("trace_hook_evidence_json", postgresql.JSONB, nullable=True),
        sa.Column("deployment_evidence_json", postgresql.JSONB, nullable=True),
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=False),
        sa.Column("discovery_status", sa.String(32), nullable=False, server_default="candidate"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "target_source_snapshot_id",
            "candidate_key",
            name="uq_discovered_agent_candidates_snapshot_id_candidate_key",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "target_source_snapshot_id"],
            ["target_source_snapshots.organization_id", "target_source_snapshots.id"],
            ondelete="RESTRICT",
            name="fk_discovered_agent_candidates_organization_id_snapshot_id",
        ),
        sa.CheckConstraint(
            "confidence_score >= 0.0 AND confidence_score <= 1.0",
            name="ck_discovered_agent_candidates_confidence_score_range",
        ),
        sa.CheckConstraint(
            "discovery_status in ("
            + ", ".join(f"'{v}'" for v in _ALLOWED_DISCOVERY_STATUSES)
            + ")",
            name="ck_discovered_agent_candidates_discovery_status",
        ),
    )
    op.create_index(
        "ix_discovered_agent_candidates_organization_id",
        "discovered_agent_candidates",
        ["organization_id"],
    )
    op.create_index(
        "ix_discovered_agent_candidates_snapshot_id",
        "discovered_agent_candidates",
        ["target_source_snapshot_id"],
    )

    # Section 6.8C: a discovered candidate is a fact about what analysis
    # found -- SELECT/INSERT only, never UPDATE or DELETE, the same
    # discipline FTF-04A07's own migration already established for
    # target_source_snapshots.
    op.execute(f'GRANT SELECT, INSERT ON discovered_agent_candidates TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE discovered_agent_candidates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE discovered_agent_candidates FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON discovered_agent_candidates FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON discovered_agent_candidates")
    op.execute(f'REVOKE ALL ON discovered_agent_candidates FROM "{APP_ROLE_NAME}"')
    op.drop_index(
        "ix_discovered_agent_candidates_snapshot_id", table_name="discovered_agent_candidates"
    )
    op.drop_index(
        "ix_discovered_agent_candidates_organization_id", table_name="discovered_agent_candidates"
    )
    op.drop_table("discovered_agent_candidates")
