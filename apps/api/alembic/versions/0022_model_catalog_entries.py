"""Model catalog entry + training-artifact mapping persistence (FTF-0308-P1,
Section 2.2/3.3/6.6/6.24/39/65/77/81).

Migration prerequisite for FTF-0308's own UI half ("UI maps explicit
accessible alias to verified trainable artifact"): FTF-0308's packet says
"No independent schema change... any required change is a separately
claimed migration prerequisite", and nothing persisted the alias ->
trainable-artifact mapping before this slice. A gateway alias only becomes
a trainable artifact when a human maps it explicitly; nothing here infers a
weight identity from the alias name ("no guessed weight identity from
LiteLLM alias", FTF-0308 negative_acceptance).

Two tables:

- `model_catalog_entries` -- one row per (connection, public model name),
  created the first time that alias is mapped. `trainable_artifact_id` is
  NULL until a mapping exists (and again after an explicit unmap).
  `catalog_version` starts at 1 and is bumped by exactly one on every
  mapping change, so a qualification can bind to the version it observed and
  detect a later change (`require_current_mapping`). No metadata, cost or
  `supports_*` columns: nothing writes them (Law 11). `UPDATE` is granted
  (the mapping moves); `DELETE` never is.
- `model_catalog_mapping_changes` -- append-only: one row per real change
  with previous/new artifact and previous/new version (`new = previous + 1`,
  a CHECK). `sovra_app` gets SELECT/INSERT only.

Composite foreign keys keep every row inside its organization: entries ->
`litellm_connections(organization_id, id)` and -> `trainable_artifacts
(organization_id, id)` (both RESTRICT), changes -> `model_catalog_entries
(organization_id, id)`. One supporting unique constraint
(`organization_id, id`) is added to `litellm_connections` for that
reference; `trainable_artifacts` already carries it (0009_model_versions).

Both new tables ENABLE + FORCE row-level security with the same
`tenant_isolation` policy every prior migration uses (Section 65.1).

Revision ID: 0022_model_catalog_entries
Revises: 0021_memberships
Create Date: 2026-10-07
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0022_model_catalog_entries"
down_revision = "0021_memberships"
branch_labels = None
depends_on = None

APP_ROLE_NAME = "sovra_app"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_litellm_connections_organization_id_id",
        "litellm_connections",
        ["organization_id", "id"],
    )

    op.create_table(
        "model_catalog_entries",
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
                name="fk_model_catalog_entries_organization_id",
            ),
            nullable=False,
        ),
        sa.Column("litellm_connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_model_name", sa.String(256), nullable=False),
        sa.Column("trainable_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("catalog_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_verified_accessible_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("mapped_by", sa.String(200), nullable=True),
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
            ["organization_id", "litellm_connection_id"],
            ["litellm_connections.organization_id", "litellm_connections.id"],
            ondelete="RESTRICT",
            name="fk_model_catalog_entries_organization_id_connection_id",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "trainable_artifact_id"],
            ["trainable_artifacts.organization_id", "trainable_artifacts.id"],
            ondelete="RESTRICT",
            name="fk_model_catalog_entries_organization_id_artifact_id",
        ),
        sa.UniqueConstraint(
            "litellm_connection_id",
            "public_model_name",
            name="uq_model_catalog_entries_connection_id_public_model_name",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_model_catalog_entries_org_id_id"),
        sa.CheckConstraint(
            "public_model_name <> ''", name="ck_model_catalog_entries_public_model_name_not_blank"
        ),
        sa.CheckConstraint("catalog_version >= 1", name="ck_model_catalog_entries_version_min"),
    )
    op.create_index(
        "ix_model_catalog_entries_organization_id", "model_catalog_entries", ["organization_id"]
    )

    op.create_table(
        "model_catalog_mapping_changes",
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
                name="fk_model_catalog_mapping_changes_organization_id",
            ),
            nullable=False,
        ),
        sa.Column("model_catalog_entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_trainable_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("new_trainable_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("previous_catalog_version", sa.Integer(), nullable=False),
        sa.Column("new_catalog_version", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "model_catalog_entry_id"],
            ["model_catalog_entries.organization_id", "model_catalog_entries.id"],
            ondelete="RESTRICT",
            name="fk_model_catalog_mapping_changes_organization_id_entry_id",
        ),
        sa.CheckConstraint(
            "new_catalog_version = previous_catalog_version + 1",
            name="ck_model_catalog_mapping_changes_version_increments_by_one",
        ),
        sa.CheckConstraint("actor <> ''", name="ck_model_catalog_mapping_changes_actor_not_blank"),
    )
    op.create_index(
        "ix_model_catalog_mapping_changes_organization_id",
        "model_catalog_mapping_changes",
        ["organization_id"],
    )
    op.create_index(
        "ix_model_catalog_mapping_changes_entry_id",
        "model_catalog_mapping_changes",
        ["model_catalog_entry_id"],
    )

    # Entries move (UPDATE granted, never DELETE); the change log is
    # append-only, enforced by the database itself, not by caller discipline.
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON model_catalog_entries TO "{APP_ROLE_NAME}"')
    op.execute(f'GRANT SELECT, INSERT ON model_catalog_mapping_changes TO "{APP_ROLE_NAME}"')
    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    for table in ("model_catalog_entries", "model_catalog_mapping_changes"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            f"USING (organization_id = {tenant_setting}) "
            f"WITH CHECK (organization_id = {tenant_setting})"
        )


def downgrade() -> None:
    for table in ("model_catalog_mapping_changes", "model_catalog_entries"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f'REVOKE ALL ON {table} FROM "{APP_ROLE_NAME}"')
    op.drop_index(
        "ix_model_catalog_mapping_changes_entry_id", table_name="model_catalog_mapping_changes"
    )
    op.drop_index(
        "ix_model_catalog_mapping_changes_organization_id",
        table_name="model_catalog_mapping_changes",
    )
    op.drop_table("model_catalog_mapping_changes")
    op.drop_index("ix_model_catalog_entries_organization_id", table_name="model_catalog_entries")
    op.drop_table("model_catalog_entries")
    op.drop_constraint(
        "uq_litellm_connections_organization_id_id", "litellm_connections", type_="unique"
    )
