"""Dataset version + upload-session persistence (FTF-0501-P1,
Section 6.10/7A.10/8.1/9/65.2/67.3/67.5/69.1/39).

Migration prerequisite for FTF-0501's own presigned-multipart and finalize
half: its acceptance says "cannot create accepted Dataset version" without
a verified upload, which a pure module cannot prove. Nothing persisted a
Dataset or an upload session before this slice. Each `datasets` row is one
immutable dataset version (Section 7A.10); there is no separate
DatasetVersion entity.

Two tables:

- `dataset_upload_sessions` -- one row per upload attempt on an active
  `upload` source connection, with a purpose, a declared size / sha256 /
  content type and a bounded lifetime. The database itself enforces:
  the object key is derived from the row's own ids
  (`tenants/<org>/projects/<project>/uploads/<id>/source`);
  `0 < declared_size_bytes <= max_bytes <= 10 GiB`; `expires_at` is at most
  24 hours after `created_at` (Section 67.5); `verified` requires the
  verified size, sha256 and content type to equal the declared ones and
  `finalized_at <= expires_at`; `rejected` and only `rejected` carries a
  rejection reason. `sovra_app` may only UPDATE the finalize-outcome columns
  (`status`, `verified_*`, `finalized_at`, `rejection_reason`,
  `updated_at`); the declared values, object key, purpose and expiry never
  change after insert, and DELETE is never granted.
- `datasets` -- one immutable version per row (`UNIQUE(project_id, name,
  version)`, `UNIQUE(upload_session_id)`). A composite foreign key to the
  session's `(organization_id, project_id, id, purpose, status,
  verified_sha256, verified_size_bytes)` plus `CHECK (upload_status =
  'verified')` means a dataset can only reference a verified session whose
  verified values match, and a referenced session's status or verified
  values cannot be changed afterwards. `sovra_app` gets SELECT/INSERT only.

One supporting unique constraint (`organization_id, project_id, id`) is added
to `dataset_source_connections` for the session's composite foreign key;
`projects` already carries `(organization_id, id)` (0008).

Both new tables ENABLE + FORCE row-level security with the same
`tenant_isolation` policy every prior migration uses (Section 65.1).

Revision ID: 0024_dataset_uploads
Revises: 0023_evaluation_cases
Create Date: 2026-10-07
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0024_dataset_uploads"
down_revision = "0023_evaluation_cases"
branch_labels = None
depends_on = None

APP_ROLE_NAME = "sovra_app"

_MAX_UPLOAD_BYTES = 10 * 1024**3  # 10 GiB, Section 8.1.
_PURPOSES = (
    "('training','validation','held_out_evaluation','candidate_development','baseline_only')"
)
_SESSION_UPDATE_COLUMNS = (
    "status, verified_size_bytes, verified_sha256, verified_content_type, "
    "finalized_at, rejection_reason, updated_at"
)


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _org_fk(table: str) -> sa.Column:
    return sa.Column(
        "organization_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE", name=f"fk_{table}_organization_id"),
        nullable=False,
    )


def _now_col(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=None if nullable else sa.text("now()"),
    )


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_dataset_source_connections_org_project_id",
        "dataset_source_connections",
        ["organization_id", "project_id", "id"],
    )

    op.create_table(
        "dataset_upload_sessions",
        _uuid_pk(),
        _org_fk("dataset_upload_sessions"),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_source_connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("declared_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("max_bytes", sa.BigInteger(), nullable=False),
        sa.Column("declared_sha256", sa.String(64), nullable=False),
        sa.Column("declared_content_type", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("verified_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("verified_sha256", sa.String(64), nullable=True),
        sa.Column("verified_content_type", sa.String(255), nullable=True),
        sa.Column("rejection_reason", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        _now_col("created_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _now_col("finalized_at", nullable=True),
        _now_col("updated_at"),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_dataset_upload_sessions_organization_id_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id", "dataset_source_connection_id"],
            [
                "dataset_source_connections.organization_id",
                "dataset_source_connections.project_id",
                "dataset_source_connections.id",
            ],
            ondelete="RESTRICT",
            name="fk_dataset_upload_sessions_connection",
        ),
        # The target of `datasets`' composite foreign key.
        sa.UniqueConstraint(
            "organization_id",
            "project_id",
            "id",
            "purpose",
            "status",
            "verified_sha256",
            "verified_size_bytes",
            name="uq_dataset_upload_sessions_verified_identity",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_dataset_upload_sessions_org_id_id"),
        sa.CheckConstraint(f"purpose in {_PURPOSES}", name="ck_dataset_upload_sessions_purpose"),
        sa.CheckConstraint(
            "status in ('open','verified','rejected','expired')",
            name="ck_dataset_upload_sessions_status",
        ),
        sa.CheckConstraint(
            "object_key = 'tenants/' || organization_id::text || '/projects/' || "
            "project_id::text || '/uploads/' || id::text || '/source'",
            name="ck_dataset_upload_sessions_object_key_shape",
        ),
        sa.CheckConstraint(
            f"declared_size_bytes > 0 AND declared_size_bytes <= max_bytes "
            f"AND max_bytes <= {_MAX_UPLOAD_BYTES}",
            name="ck_dataset_upload_sessions_size_bounds",
        ),
        sa.CheckConstraint(
            "expires_at <= created_at + interval '24 hours'",
            name="ck_dataset_upload_sessions_expiry_within_24h",
        ),
        sa.CheckConstraint(
            "declared_sha256 ~ '^[0-9a-f]{64}$'", name="ck_dataset_upload_sessions_declared_sha256"
        ),
        sa.CheckConstraint(
            "declared_content_type <> ''", name="ck_dataset_upload_sessions_content_type_not_blank"
        ),
        sa.CheckConstraint(
            "status <> 'verified' OR ("
            "verified_size_bytes = declared_size_bytes "
            "AND verified_sha256 = declared_sha256 "
            "AND verified_content_type = declared_content_type "
            "AND finalized_at IS NOT NULL AND finalized_at <= expires_at)",
            name="ck_dataset_upload_sessions_verified_matches_declared",
        ),
        sa.CheckConstraint(
            "(status = 'rejected') = (rejection_reason IS NOT NULL)",
            name="ck_dataset_upload_sessions_rejection_reason_iff_rejected",
        ),
        sa.CheckConstraint(
            "rejection_reason IS NULL OR rejection_reason <> ''",
            name="ck_dataset_upload_sessions_rejection_reason_not_blank",
        ),
        sa.CheckConstraint(
            "status = 'open' OR finalized_at IS NOT NULL",
            name="ck_dataset_upload_sessions_closed_has_finalized_at",
        ),
    )
    op.create_index(
        "ix_dataset_upload_sessions_organization_id", "dataset_upload_sessions", ["organization_id"]
    )
    op.create_index(
        "ix_dataset_upload_sessions_project_id", "dataset_upload_sessions", ["project_id"]
    )

    op.create_table(
        "datasets",
        _uuid_pk(),
        _org_fk("datasets"),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("upload_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("upload_status", sa.String(16), nullable=False, server_default="verified"),
        sa.Column("verified_sha256", sa.String(64), nullable=False),
        sa.Column("verified_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        _now_col("created_at"),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_datasets_organization_id_project_id",
        ),
        sa.ForeignKeyConstraint(
            [
                "organization_id",
                "project_id",
                "upload_session_id",
                "purpose",
                "upload_status",
                "verified_sha256",
                "verified_size_bytes",
            ],
            [
                "dataset_upload_sessions.organization_id",
                "dataset_upload_sessions.project_id",
                "dataset_upload_sessions.id",
                "dataset_upload_sessions.purpose",
                "dataset_upload_sessions.status",
                "dataset_upload_sessions.verified_sha256",
                "dataset_upload_sessions.verified_size_bytes",
            ],
            ondelete="RESTRICT",
            name="fk_datasets_verified_upload_session",
        ),
        sa.UniqueConstraint(
            "project_id", "name", "version", name="uq_datasets_project_name_version"
        ),
        sa.UniqueConstraint("upload_session_id", name="uq_datasets_upload_session_id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_datasets_org_id_id"),
        sa.CheckConstraint("name <> ''", name="ck_datasets_name_not_blank"),
        sa.CheckConstraint("version >= 1", name="ck_datasets_version_min"),
        sa.CheckConstraint(f"purpose in {_PURPOSES}", name="ck_datasets_purpose"),
        sa.CheckConstraint("upload_status = 'verified'", name="ck_datasets_upload_verified"),
    )
    op.create_index("ix_datasets_organization_id", "datasets", ["organization_id"])
    op.create_index("ix_datasets_project_id", "datasets", ["project_id"])

    # Sessions: INSERT, and UPDATE of the finalize-outcome columns only, so the
    # declared values, object key, purpose and expiry are immutable for the
    # application role. Datasets are immutable versions: SELECT/INSERT only.
    # DELETE is never granted on either table.
    op.execute(f'GRANT SELECT, INSERT ON dataset_upload_sessions TO "{APP_ROLE_NAME}"')
    op.execute(
        f'GRANT UPDATE ({_SESSION_UPDATE_COLUMNS}) ON dataset_upload_sessions TO "{APP_ROLE_NAME}"'
    )
    op.execute(f'GRANT SELECT, INSERT ON datasets TO "{APP_ROLE_NAME}"')
    # A verified session is the evidence a dataset version rests on, so no
    # column of it may change afterwards. The composite foreign key already
    # pins status / sha256 / size; this trigger also freezes finalized_at,
    # the verified content type and everything else, for every role.
    op.execute(
        "CREATE FUNCTION dataset_upload_sessions_freeze_verified() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        "IF OLD.status = 'verified' THEN "
        "RAISE EXCEPTION 'a verified upload session is immutable' "
        "USING ERRCODE = 'check_violation', "
        "CONSTRAINT = 'tr_dataset_upload_sessions_freeze_verified'; "
        "END IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER tr_dataset_upload_sessions_freeze_verified "
        "BEFORE UPDATE ON dataset_upload_sessions "
        "FOR EACH ROW EXECUTE FUNCTION dataset_upload_sessions_freeze_verified()"
    )
    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    for table in ("dataset_upload_sessions", "datasets"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            f"USING (organization_id = {tenant_setting}) "
            f"WITH CHECK (organization_id = {tenant_setting})"
        )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS tr_dataset_upload_sessions_freeze_verified "
        "ON dataset_upload_sessions"
    )
    op.execute("DROP FUNCTION IF EXISTS dataset_upload_sessions_freeze_verified()")
    for table in ("datasets", "dataset_upload_sessions"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f'REVOKE ALL ON {table} FROM "{APP_ROLE_NAME}"')
    op.drop_index("ix_datasets_project_id", table_name="datasets")
    op.drop_index("ix_datasets_organization_id", table_name="datasets")
    op.drop_table("datasets")
    op.drop_index("ix_dataset_upload_sessions_project_id", table_name="dataset_upload_sessions")
    op.drop_index(
        "ix_dataset_upload_sessions_organization_id", table_name="dataset_upload_sessions"
    )
    op.drop_table("dataset_upload_sessions")
    op.drop_constraint(
        "uq_dataset_source_connections_org_project_id",
        "dataset_source_connections",
        type_="unique",
    )
