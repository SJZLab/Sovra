"""TargetSourceSnapshot schema + immutable provenance (FTF-04A07).

Creates the `target_source_snapshots` table (Section 6.8B/7A.1-7A.3/9/45/
65-67/77/81), FORCEs row-level security scoped to the same
`app.current_organization_id` tenant context every prior migration in
this chain already established, and grants the restricted `sovra_app`
role SELECT/INSERT only -- never UPDATE or DELETE, since Section 6.8B's
own "Immutable source snapshot" rule means there is no legitimate
mutation path for this table at all (the same stronger SELECT/INSERT-only
discipline FTF-1001's own `0009_model_versions.py` already established
for `model_versions`).

Also adds a purely additive `UNIQUE (organization_id, id)` constraint on
the already-CERTIFIED `target_application_connections` table (FTF-04A01):
trivially always true, since `id` is already a globally-unique primary
key, so this cannot invalidate FTF-04A01's own certified evidence -- it
only makes a real composite foreign key
`target_source_snapshots(organization_id, target_application_connection_id)`
-> `target_application_connections(organization_id, id)` possible, the
same Section 65.1 "composite (organization_id, id) keys where the parent
is tenant-owned" precedent FTF-1001's own migration already applied to
`trainable_artifacts`.

`target_source_snapshots` also carries a real *self-referencing* composite
foreign key `(organization_id, supersedes_snapshot_id)` ->
`target_source_snapshots(organization_id, id)` -- the cross-tenant half of
this task's own "Existing snapshot cannot be ... reassigned to another
repository/project" negative_acceptance (the cross-connection half is a
real CRUD-layer check; see `app/target_connectors/
target_source_snapshot_crud.py`'s own module docstring for why no `CHECK`
constraint can express it). This requires its own `UNIQUE
(organization_id, id)` constraint on `target_source_snapshots` itself,
added in the same `CREATE TABLE` statement (Postgres allows a
self-referencing foreign key to a unique constraint defined in the same
statement).

`ck_target_source_snapshots_capture_method`/`_analysis_status`/
`_archive_pair`/`_dirty_requires_archive`/`_no_self_supersede` are real,
database-level enforcement of this task's own structural invariants (see
`app/target_connectors/target_source_snapshot_schema.py`'s own docstring)
-- never only application-level checks a bypassing write could evade.

Revision ID: 0012_target_source_snapshots
Revises: 0011_target_app_connections
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0012_target_source_snapshots"
down_revision = "0011_target_app_connections"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as every prior migration in this chain.
APP_ROLE_NAME = "sovra_app"

_ALLOWED_CAPTURE_METHODS = ("github_resolved_commit", "local_git_commit", "local_dirty_snapshot")
_ALLOWED_ANALYSIS_STATUSES = ("not_analyzed",)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_target_application_connections_organization_id_id",
        "target_application_connections",
        ["organization_id", "id"],
    )

    op.create_table(
        "target_source_snapshots",
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
                name="fk_target_source_snapshots_organization_id",
            ),
            nullable=False,
        ),
        sa.Column(
            "target_application_connection_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("commit_sha", sa.String(64), nullable=False),
        sa.Column("branch", sa.String(200), nullable=True),
        sa.Column("tree_hash", sa.String(64), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("capture_method", sa.String(32), nullable=False),
        sa.Column("source_archive_object_key", sa.String(1024), nullable=True),
        sa.Column("source_archive_sha256", sa.String(64), nullable=True),
        sa.Column("analysis_status", sa.String(32), nullable=False, server_default="not_analyzed"),
        sa.Column("supersedes_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_target_source_snapshots_organization_id_id"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "target_application_connection_id"],
            ["target_application_connections.organization_id", "target_application_connections.id"],
            ondelete="RESTRICT",
            name="fk_target_source_snapshots_organization_id_connection_id",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "supersedes_snapshot_id"],
            ["target_source_snapshots.organization_id", "target_source_snapshots.id"],
            ondelete="RESTRICT",
            name="fk_target_source_snapshots_organization_id_supersedes_id",
        ),
        sa.CheckConstraint(
            "capture_method in (" + ", ".join(f"'{v}'" for v in _ALLOWED_CAPTURE_METHODS) + ")",
            name="ck_target_source_snapshots_capture_method",
        ),
        sa.CheckConstraint(
            "analysis_status in (" + ", ".join(f"'{v}'" for v in _ALLOWED_ANALYSIS_STATUSES) + ")",
            name="ck_target_source_snapshots_analysis_status",
        ),
        sa.CheckConstraint(
            "(source_archive_object_key IS NULL) = (source_archive_sha256 IS NULL)",
            name="ck_target_source_snapshots_archive_pair",
        ),
        sa.CheckConstraint(
            "capture_method != 'local_dirty_snapshot' OR "
            "(source_archive_object_key IS NOT NULL AND source_archive_sha256 IS NOT NULL)",
            name="ck_target_source_snapshots_dirty_requires_archive",
        ),
        sa.CheckConstraint(
            "supersedes_snapshot_id IS NULL OR supersedes_snapshot_id != id",
            name="ck_target_source_snapshots_no_self_supersede",
        ),
    )
    op.create_index(
        "ix_target_source_snapshots_organization_id",
        "target_source_snapshots",
        ["organization_id"],
    )
    op.create_index(
        "ix_target_source_snapshots_connection_id",
        "target_source_snapshots",
        ["target_application_connection_id"],
    )
    op.create_index(
        "ix_target_source_snapshots_supersedes_snapshot_id",
        "target_source_snapshots",
        ["supersedes_snapshot_id"],
    )

    # Section 6.8B: "Immutable source snapshot" -- SELECT/INSERT only,
    # never UPDATE or DELETE, the same stronger discipline FTF-1001's own
    # migration already established for model_versions.
    op.execute(f'GRANT SELECT, INSERT ON target_source_snapshots TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE target_source_snapshots ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE target_source_snapshots FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON target_source_snapshots FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON target_source_snapshots")
    op.execute(f'REVOKE ALL ON target_source_snapshots FROM "{APP_ROLE_NAME}"')
    op.drop_index(
        "ix_target_source_snapshots_supersedes_snapshot_id",
        table_name="target_source_snapshots",
    )
    op.drop_index("ix_target_source_snapshots_connection_id", table_name="target_source_snapshots")
    op.drop_index(
        "ix_target_source_snapshots_organization_id", table_name="target_source_snapshots"
    )
    op.drop_table("target_source_snapshots")
    op.drop_constraint(
        "uq_target_application_connections_organization_id_id",
        "target_application_connections",
        type_="unique",
    )
