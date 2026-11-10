"""Idempotent approve / reject requests for dataset split manifests (FTF-0519-P2 round 2).

The FTF-0519-P2 routes require an `Idempotency-Key` on approve and reject. Round 1 shape-checked the
key and then discarded it, so a reused key with a different body was an accidental 409
`manifest_not_proposed` instead of the contract's `idempotency_key_reused`. This stores the key with
the request digest (FTF-0516-P1 `request_digest` pattern) in the same transaction as the lock or
rejection it records:

- `dataset_split_manifest_requests` -- one row per request that took effect. The key is unique per
  (organization, project); the same key with the same digest replays the stored outcome, the same
  key with another digest is `idempotency_key_reused`. ENABLE + FORCE row-level security with the
  `tenant_isolation` policy every prior migration uses (Section 65.1); the app role is granted
  SELECT and INSERT only (no UPDATE, no DELETE), so a stored digest cannot be rewritten.

Revision ID: 0032_split_manifest_requests
Revises: 0031_model_import_validation
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0032_split_manifest_requests"
down_revision = "0031_model_import_validation"
branch_labels = None
depends_on = None

APP_ROLE_NAME = "sovra_app"
_TABLE = "dataset_split_manifest_requests"
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
        sa.Column("manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "manifest_id"],
            ["dataset_split_manifests.organization_id", "dataset_split_manifests.id"],
            ondelete="RESTRICT",
            name=f"fk_{_TABLE}_manifest",
        ),
        sa.UniqueConstraint(
            "organization_id", "project_id", "idempotency_key", name=f"uq_{_TABLE}_key"
        ),
        sa.CheckConstraint("action IN ('approve', 'reject')", name=f"ck_{_TABLE}_action"),
        sa.CheckConstraint(f"request_digest ~ {_HEX64}", name=f"ck_{_TABLE}_digest"),
        sa.CheckConstraint(
            "char_length(idempotency_key) BETWEEN 8 AND 200", name=f"ck_{_TABLE}_key_length"
        ),
    )
    op.create_index(f"ix_{_TABLE}_organization_id", _TABLE, ["organization_id"])
    op.create_index(f"ix_{_TABLE}_manifest", _TABLE, ["manifest_id"])

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
    op.drop_index(f"ix_{_TABLE}_manifest", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_organization_id", table_name=_TABLE)
    op.drop_table(_TABLE)
