"""Dataset mapping persistence (FTF-H011-P2; Section 0 items 13/27, 7, 7A.10, 8-8.2, 9, 67.1-67.4).

FTF-H011-P1 previews a MappingSpec against a real dataset but nothing persisted a mapping or its
approval. AF-15's "explicit grant/purpose and server validation before immutable import" and the
acceptance "deny training for evaluation-only grants" need a stored, versioned, approved mapping
whose purpose the database itself pins. One table, ENABLE + FORCE row-level security with the same
`tenant_isolation` policy every prior migration uses (Section 65.1), and no DELETE grant:

- `dataset_mappings` -- one row per (dataset, mapping_version). The dataset identity (project, id,
  name, purpose, version, verified sha256) is held true by a composite foreign key to `datasets`
  (`uq_datasets_quality_lineage`) and the connection by a composite foreign key to
  `dataset_source_connections`. A change is a new row whose `parent_mapping_version` is the previous
  version (composite self foreign key, `parent_mapping_version = mapping_version - 1`), never an
  edit.
- The only transition is `proposed` -> `approved` | `rejected`: `sovra_app` may UPDATE only
  `(status, approved_purpose, approved_by, approver_role, approved_at, ignored_keys_sha256,
  updated_at)` and a BEFORE UPDATE trigger, for every role, accepts nothing but that one transition
  with every other column unchanged (`to_jsonb` diff, as 0026 / 0030). A superseded version (a later
  version exists) is never approved.
- CHECKs: approved <=> every approval column set; `approved_purpose = dataset_purpose`
  (Section 9: no reclassification) and `approved_purpose = ANY (allowed_purposes_snapshot)`, so
  raw SQL cannot record a training approval under an evaluation-only grant; every hash is 64
  lowercase hex; the
  approver is an admin role; `ignored_keys_sha256` is set exactly for an approved mapping that
  ignores unmapped message keys; `preview_counts` is content-free (only `seen`, `accepted`,
  `rejected` and `rejected:<code>` keys with non-negative integer values).
- A BEFORE INSERT trigger refuses a row that is not inserted as `proposed`.

Nothing here stores preview content: the mapping document, hashes and counts only.

Revision ID: 0034_dataset_mappings
Revises: 0033_upload_declared_encoding
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0034_dataset_mappings"
down_revision = "0033_upload_declared_encoding"
branch_labels = None
depends_on = None

APP_ROLE_NAME = "sovra_app"

_STATUSES = ("proposed", "approved", "rejected")
_IMPORTERS = ("csv", "json", "jsonl")
_PURPOSES = ("training", "validation", "candidate_development", "held_out_evaluation")
_ALL_PURPOSES = (*_PURPOSES, "baseline_only")
_ADMIN_ROLES = ("workspace_admin", "organization_admin")
_UPDATABLE = (
    "status",
    "approved_purpose",
    "approved_by",
    "approver_role",
    "approved_at",
    "ignored_keys_sha256",
    "updated_at",
)
_HEX64 = "'^[0-9a-f]{64}$'"


def _in(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


def _array(values: tuple[str, ...]) -> str:
    return "ARRAY[" + ",".join(f"'{v}'" for v in values) + "]::text[]"


def _raise(message: str, constraint: str) -> str:
    return (
        f"RAISE EXCEPTION '{message}' USING ERRCODE = 'check_violation', "
        f"CONSTRAINT = '{constraint}'; "
    )


_COUNTS_FUNCTION = (
    "CREATE FUNCTION dataset_mapping_counts_content_free(c jsonb) RETURNS boolean "
    "LANGUAGE sql IMMUTABLE AS $$ SELECT jsonb_typeof(c) = 'object' "
    "AND c ?& ARRAY['seen','accepted','rejected'] "
    "AND NOT EXISTS (SELECT 1 FROM jsonb_each(c) AS e(k, v) "
    "WHERE e.k !~ '^(seen|accepted|rejected|rejected:[a-z][a-z0-9_]*)$' "
    "OR jsonb_typeof(e.v) <> 'number' OR e.v::text !~ '^[0-9]{1,9}$') $$"
)
_INSERT_FUNCTION = (
    "CREATE FUNCTION dataset_mappings_insert_guard() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ "
    "DECLARE grant_connection uuid; grant_json jsonb; BEGIN "
    "IF NEW.status <> 'proposed' THEN "
    + _raise("a mapping is inserted as proposed", "tr_dataset_mappings_insert_guard_status")
    + "END IF; "
    "SELECT u.dataset_source_connection_id, c.allowed_purposes_json "
    "INTO grant_connection, grant_json FROM datasets d "
    "JOIN dataset_upload_sessions u ON u.id = d.upload_session_id "
    "AND u.organization_id = d.organization_id "
    "JOIN dataset_source_connections c ON c.id = u.dataset_source_connection_id "
    "AND c.organization_id = u.organization_id "
    "WHERE d.id = NEW.dataset_id AND d.organization_id = NEW.organization_id; "
    "IF grant_connection IS DISTINCT FROM NEW.dataset_source_connection_id THEN "
    + _raise(
        "a mapping names the upload connection of its dataset",
        "tr_dataset_mappings_insert_guard_connection",
    )
    + "END IF; "
    "IF (SELECT array_agg(g ORDER BY g) FROM jsonb_array_elements_text(grant_json) AS g) "
    "IS DISTINCT FROM (SELECT array_agg(p ORDER BY p) FROM unnest(NEW.allowed_purposes_snapshot) "
    "AS p) THEN "
    + _raise(
        "the grant snapshot is the grant of the connection",
        "tr_dataset_mappings_insert_guard_snapshot",
    )
    + "END IF; RETURN NEW; END $$"
)
_FROZEN = " - ".join(f"'{c}'" for c in _UPDATABLE)
_UPDATE_FUNCTION = (
    "CREATE FUNCTION dataset_mappings_update_guard() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF OLD.status <> 'proposed' THEN "
    + _raise(
        "an approved or rejected mapping never changes", "tr_dataset_mappings_update_guard_final"
    )
    + "END IF; "
    "IF NEW.status NOT IN ('approved', 'rejected') THEN "
    + _raise(
        "a proposed mapping can only be approved or rejected",
        "tr_dataset_mappings_update_guard_transition",
    )
    + "END IF; "
    f"IF (to_jsonb(NEW) - {_FROZEN}) IS DISTINCT FROM (to_jsonb(OLD) - {_FROZEN}) THEN "
    + _raise(
        "a mapping is immutable except its approval or rejection",
        "tr_dataset_mappings_update_guard_frozen",
    )
    + "END IF; "
    "IF NEW.status = 'rejected' AND NEW.ignored_keys_sha256 IS DISTINCT FROM "
    "OLD.ignored_keys_sha256 THEN "
    + _raise("a rejection records no acknowledgement", "tr_dataset_mappings_update_guard_frozen")
    + "END IF; "
    "IF NEW.status = 'approved' AND EXISTS (SELECT 1 FROM dataset_mappings m "
    "WHERE m.organization_id = NEW.organization_id AND m.project_id = NEW.project_id "
    "AND m.dataset_id = NEW.dataset_id AND m.mapping_version > NEW.mapping_version) THEN "
    + _raise(
        "a superseded mapping is never approved", "tr_dataset_mappings_update_guard_superseded"
    )
    + "END IF; "
    "RETURN NEW; END $$"
)


def _uuid(name: str) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=False)


def _now_col(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=None if nullable else sa.text("now()"),
    )


def _hash_col(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.String(64), nullable=nullable)


def upgrade() -> None:
    op.execute(_COUNTS_FUNCTION)

    op.create_table(
        "dataset_mappings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_dataset_mappings_org"),
            nullable=False,
        ),
        _uuid("project_id"),
        _uuid("dataset_id"),
        sa.Column("dataset_name", sa.String(200), nullable=False),
        sa.Column("dataset_purpose", sa.String(32), nullable=False),
        sa.Column("dataset_version", sa.Integer(), nullable=False),
        _hash_col("dataset_sha256"),
        _uuid("dataset_source_connection_id"),
        sa.Column("mapping_version", sa.Integer(), nullable=False),
        sa.Column("parent_mapping_version", sa.Integer(), nullable=True),
        sa.Column("mapping_document", postgresql.JSONB(), nullable=False),
        _hash_col("mapping_sha256"),
        sa.Column("importer", sa.String(16), nullable=False),
        sa.Column("importer_version", sa.String(16), nullable=False),
        sa.Column("transform_version", sa.String(16), nullable=False),
        _hash_col("source_schema_sha256", nullable=True),
        _hash_col("tool_definitions_sha256"),
        _hash_col("preview_sha256"),
        sa.Column("preview_counts", postgresql.JSONB(), nullable=False),
        _hash_col("ignored_keys_sha256", nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("allowed_purposes_snapshot", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("approved_purpose", sa.String(32), nullable=True),
        sa.Column("approved_by", sa.String(200), nullable=True),
        sa.Column("approver_role", sa.String(32), nullable=True),
        _now_col("approved_at", nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        _now_col("created_at"),
        _now_col("updated_at"),
        sa.ForeignKeyConstraint(
            [
                "organization_id",
                "project_id",
                "dataset_id",
                "dataset_name",
                "dataset_purpose",
                "dataset_version",
                "dataset_sha256",
            ],
            [
                "datasets.organization_id",
                "datasets.project_id",
                "datasets.id",
                "datasets.name",
                "datasets.purpose",
                "datasets.version",
                "datasets.verified_sha256",
            ],
            ondelete="RESTRICT",
            name="fk_dataset_mappings_dataset_lineage",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id", "dataset_source_connection_id"],
            [
                "dataset_source_connections.organization_id",
                "dataset_source_connections.project_id",
                "dataset_source_connections.id",
            ],
            ondelete="RESTRICT",
            name="fk_dataset_mappings_connection",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id", "dataset_id", "parent_mapping_version"],
            [
                "dataset_mappings.organization_id",
                "dataset_mappings.project_id",
                "dataset_mappings.dataset_id",
                "dataset_mappings.mapping_version",
            ],
            ondelete="RESTRICT",
            name="fk_dataset_mappings_parent",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "project_id",
            "dataset_id",
            "mapping_version",
            name="uq_dataset_mappings_dataset_version",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_dataset_mappings_org_id_id"),
        sa.CheckConstraint("mapping_version >= 1", name="ck_dataset_mappings_version_min"),
        sa.CheckConstraint(
            "(mapping_version = 1) = (parent_mapping_version IS NULL)",
            name="ck_dataset_mappings_first_has_no_parent",
        ),
        sa.CheckConstraint(
            "parent_mapping_version IS NULL OR parent_mapping_version = mapping_version - 1",
            name="ck_dataset_mappings_parent_is_previous",
        ),
        sa.CheckConstraint(f"status IN {_in(_STATUSES)}", name="ck_dataset_mappings_status"),
        sa.CheckConstraint(f"importer IN {_in(_IMPORTERS)}", name="ck_dataset_mappings_importer"),
        sa.CheckConstraint(
            "importer_version <> '' AND transform_version <> '' AND created_by <> ''",
            name="ck_dataset_mappings_text_not_blank",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(mapping_document) = 'object' "
            "AND octet_length(mapping_document::text) <= 65536",
            name="ck_dataset_mappings_document",
        ),
        sa.CheckConstraint(
            "dataset_mapping_counts_content_free(preview_counts)",
            name="ck_dataset_mappings_preview_counts",
        ),
        sa.CheckConstraint(
            f"dataset_sha256 ~ {_HEX64} AND mapping_sha256 ~ {_HEX64} "
            f"AND tool_definitions_sha256 ~ {_HEX64} AND preview_sha256 ~ {_HEX64} "
            f"AND (source_schema_sha256 IS NULL OR source_schema_sha256 ~ {_HEX64}) "
            f"AND (ignored_keys_sha256 IS NULL OR ignored_keys_sha256 ~ {_HEX64})",
            name="ck_dataset_mappings_hashes",
        ),
        sa.CheckConstraint(
            f"cardinality(allowed_purposes_snapshot) >= 1 "
            f"AND allowed_purposes_snapshot <@ {_array(_ALL_PURPOSES)}",
            name="ck_dataset_mappings_allowed_purposes_snapshot",
        ),
        sa.CheckConstraint(
            "(status = 'approved') = (approved_purpose IS NOT NULL AND approved_by IS NOT NULL "
            "AND approver_role IS NOT NULL AND approved_at IS NOT NULL) "
            "AND (status = 'approved' OR (approved_purpose IS NULL AND approved_by IS NULL "
            "AND approver_role IS NULL AND approved_at IS NULL))",
            name="ck_dataset_mappings_approval_columns",
        ),
        sa.CheckConstraint(
            "approved_purpose IS NULL OR approved_purpose = dataset_purpose",
            name="ck_dataset_mappings_approved_purpose_is_dataset_purpose",
        ),
        sa.CheckConstraint(
            "approved_purpose IS NULL OR approved_purpose = ANY (allowed_purposes_snapshot)",
            name="ck_dataset_mappings_approved_purpose_in_grant",
        ),
        sa.CheckConstraint(
            f"approver_role IS NULL OR approver_role IN {_in(_ADMIN_ROLES)}",
            name="ck_dataset_mappings_approver_role",
        ),
        sa.CheckConstraint(
            "approved_by IS NULL OR approved_by <> ''", name="ck_dataset_mappings_approver_named"
        ),
        sa.CheckConstraint(
            "(status = 'approved' AND COALESCE(mapping_document ->> "
            "'on_unmapped_message_keys', '') = 'ignore') = (ignored_keys_sha256 IS NOT NULL)",
            name="ck_dataset_mappings_ignored_keys_acknowledged",
        ),
    )
    op.create_index("ix_dataset_mappings_organization_id", "dataset_mappings", ["organization_id"])
    op.create_index("ix_dataset_mappings_dataset_id", "dataset_mappings", ["dataset_id"])

    op.execute(f'GRANT SELECT, INSERT ON dataset_mappings TO "{APP_ROLE_NAME}"')
    op.execute(f'GRANT UPDATE ({", ".join(_UPDATABLE)}) ON dataset_mappings TO "{APP_ROLE_NAME}"')

    op.execute(_INSERT_FUNCTION)
    op.execute(
        "CREATE TRIGGER tr_dataset_mappings_insert_guard BEFORE INSERT ON dataset_mappings "
        "FOR EACH ROW EXECUTE FUNCTION dataset_mappings_insert_guard()"
    )
    op.execute(_UPDATE_FUNCTION)
    op.execute(
        "CREATE TRIGGER tr_dataset_mappings_update_guard BEFORE UPDATE ON dataset_mappings "
        "FOR EACH ROW EXECUTE FUNCTION dataset_mappings_update_guard()"
    )

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute("ALTER TABLE dataset_mappings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE dataset_mappings FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON dataset_mappings FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tr_dataset_mappings_update_guard ON dataset_mappings")
    op.execute("DROP FUNCTION IF EXISTS dataset_mappings_update_guard()")
    op.execute("DROP TRIGGER IF EXISTS tr_dataset_mappings_insert_guard ON dataset_mappings")
    op.execute("DROP FUNCTION IF EXISTS dataset_mappings_insert_guard()")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON dataset_mappings")
    op.execute(f'REVOKE ALL ON dataset_mappings FROM "{APP_ROLE_NAME}"')
    op.drop_index("ix_dataset_mappings_dataset_id", table_name="dataset_mappings")
    op.drop_index("ix_dataset_mappings_organization_id", table_name="dataset_mappings")
    op.drop_table("dataset_mappings")
    op.execute("DROP FUNCTION IF EXISTS dataset_mapping_counts_content_free(jsonb)")
