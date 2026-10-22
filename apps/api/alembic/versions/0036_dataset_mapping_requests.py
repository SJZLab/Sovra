"""Idempotent propose / approve / reject requests for dataset mappings (FTF-H011-P3).

The FTF-H011-P3 routes require an `Idempotency-Key` on `mappings`, `mappings/{id}/approve` and
`mappings/{id}/reject`, and a reviewer rationale (1..2000 characters) on approve and reject. Nothing
in `dataset_mappings` (0035) can hold either, and that table accepts one `proposed` -> `approved` |
`rejected` update of fixed columns only. This stores the key with a digest of the request, and the
rationale, in the same transaction as the proposal, approval or rejection it records
(`0032_split_manifest_requests` pattern):

- `dataset_mapping_requests` -- one row per request that took effect. The key is unique per
  (organization, project): the same key with the same digest replays the stored outcome, the same key
  with another digest is `idempotency_key_reused`. The mapping is held true by a composite foreign key
  to `dataset_mappings (organization_id, id)`. `rationale` is required for approve and reject and
  absent for a proposal; it is never copied into an audit row. ENABLE + FORCE row-level security
  with the `tenant_isolation` policy every prior migration uses (Section 65.1); `sovra_app` is
  granted SELECT and INSERT only (no UPDATE, no DELETE), so a stored digest or rationale cannot be
  rewritten.

Revision ID: 0036_dataset_mapping_requests
Revises: 0035_dataset_mappings
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0036_dataset_mapping_requests"
down_revision = "0035_dataset_mappings"
branch_labels = None
depends_on = None

APP_ROLE_NAME = "sovra_app"
_TABLE = "dataset_mapping_requests"
_HEX64 = "'^[0-9a-f]{64}$'"


def upgrade() -> None:
    op.create_table(
        _TABLE,
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
                "organizations.id", ondelete="CASCADE", name=f"fk_{_TABLE}_organization_id"
            ),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mapping_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "mapping_id"],
            ["dataset_mappings.organization_id", "dataset_mappings.id"],
            ondelete="RESTRICT",
            name=f"fk_{_TABLE}_mapping",
        ),
        sa.UniqueConstraint(
            "organization_id", "project_id", "idempotency_key", name=f"uq_{_TABLE}_key"
        ),
        sa.CheckConstraint(
            "action IN ('propose', 'approve', 'reject')", name=f"ck_{_TABLE}_action"
        ),
        sa.CheckConstraint(f"request_digest ~ {_HEX64}", name=f"ck_{_TABLE}_digest"),
        sa.CheckConstraint(
            "char_length(idempotency_key) BETWEEN 8 AND 200", name=f"ck_{_TABLE}_key_length"
        ),
        sa.CheckConstraint(
            "(action = 'propose' AND rationale IS NULL) OR "
            "(action IN ('approve', 'reject') AND rationale IS NOT NULL "
            "AND char_length(rationale) BETWEEN 1 AND 2000)",
            name=f"ck_{_TABLE}_rationale",
        ),
    )
    op.create_index(f"ix_{_TABLE}_organization_id", _TABLE, ["organization_id"])
    op.create_index(f"ix_{_TABLE}_mapping", _TABLE, ["mapping_id"])

    op.execute(f'GRANT SELECT, INSERT ON {_TABLE} TO "{APP_ROLE_NAME}"')

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {_TABLE} FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.execute(f'REVOKE ALL ON {_TABLE} FROM "{APP_ROLE_NAME}"')
    op.drop_index(f"ix_{_TABLE}_mapping", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_organization_id", table_name=_TABLE)
    op.drop_table(_TABLE)
