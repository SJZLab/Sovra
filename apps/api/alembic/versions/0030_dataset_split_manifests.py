"""Immutable dataset split manifests (FTF-0519-P1; Section 0 items 13-14, 6.10-6.12, 7,
7A.10, 8.1 steps 13/16-18, 8.2, 9, 10, 65, 67.2/67.4, 77, 81).

FTF-0517 allocates a four-role split in memory and FTF-0518 reports its leakage, but nothing
persisted either. FTF-0519's acceptance ("Freeze hashes/counts/roles/grants for all four dataset
purposes and version on change") needs a frozen record, and its negative acceptance ("final set
cannot be returned under training access") needs the database itself to refuse the shapes that
would let it. Four tables, each ENABLE + FORCE row-level security with the same `tenant_isolation`
policy every prior migration uses (Section 65.1), none with a DELETE grant:

- `dataset_split_manifests` -- one row per (project, name, version). A change is a new row whose
  `parent_version` is the previous version (composite self foreign key, `parent_version =
  version - 1`), never an edit. The only transition is `proposed` -> `locked` | `rejected`:
  `sovra_app` may UPDATE only `(status, leakage_reviews, reviewed_leakage_report_sha256,
  frozen_manifest_sha256, approved_by, locked_at, updated_at)` and a BEFORE UPDATE trigger, for
  every role, accepts nothing but that one transition with every other column unchanged. A lock is
  refused unless the leakage status is `clear` or `leakage_found` (never `blocked` / `failed`),
  there are exactly four role rows with at least one record each, those counts match the stored
  assignments, no group crosses a role and there is at least one source. A BEFORE INSERT trigger
  refuses a manifest that is not inserted as `proposed`.
- `dataset_split_manifest_roles` -- the four role rows (counts and role hash only, never record
  ids), with the fixed (role, purpose) pairs as a CHECK so `heldout_test` can never be stored.
- `dataset_split_manifest_sources` -- the frozen grants: the source dataset identity is held true
  by a composite foreign key to `datasets`, the quality report by composite foreign keys to
  `dataset_quality_reports` (identity and hash), the connection by a composite foreign key to
  `dataset_source_connections`, with the `allowed_purposes_snapshot` taken at proposal.
- `dataset_split_assignments` -- one row per record. A BEFORE INSERT trigger refuses a record of a
  `held_out_evaluation` / `candidate_development` source outside its pinned evaluation role and any
  record placed in a role whose purpose the source's frozen grant does not contain.

Child tables are SELECT / INSERT only and a BEFORE INSERT trigger refuses a row for a manifest that
is not `proposed`, so nothing changes after a lock. The content-free leakage report body is stored
on the manifest (record ids, roles, methods and scores, never record content).

Adds one unique constraint to `dataset_quality_reports` (`organization_id, id, report_sha256`) as
the target of the sources' report-hash foreign key; no existing column or constraint changes.

Revision ID: 0030_dataset_split_manifests
Revises: 0029_artifact_ready_license
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0030_dataset_split_manifests"
down_revision = "0029_artifact_ready_license"
branch_labels = None
depends_on = None

APP_ROLE_NAME = "sovra_app"

_ROLES = ("train", "validation", "development_eval", "final_qualification")
_STRATEGIES = ("sha256_group_hash_v1", "legacy_three_split_v1")
_LEAKAGE_STATUSES = ("clear", "leakage_found", "blocked", "failed")
_SOURCE_PURPOSES = ("training", "validation", "candidate_development", "held_out_evaluation")
_ALL_PURPOSES = (*_SOURCE_PURPOSES, "baseline_only")
_MANIFEST_UPDATABLE = (
    "status",
    "leakage_reviews",
    "reviewed_leakage_report_sha256",
    "frozen_manifest_sha256",
    "approved_by",
    "locked_at",
    "updated_at",
)
_HEX64 = "'^[0-9a-f]{64}$'"
_TABLES = (
    "dataset_split_assignments",
    "dataset_split_manifest_sources",
    "dataset_split_manifest_roles",
    "dataset_split_manifests",
)


def _in(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


def _array(values: tuple[str, ...]) -> str:
    return "ARRAY[" + ",".join(f"'{v}'" for v in values) + "]::text[]"


def _raise(message: str, constraint: str) -> str:
    return (
        f"RAISE EXCEPTION '{message}' USING ERRCODE = 'check_violation', "
        f"CONSTRAINT = '{constraint}'; "
    )


_MANIFEST_INSERT_FUNCTION = (
    "CREATE FUNCTION dataset_split_manifests_insert_guard() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF NEW.status <> 'proposed' THEN "
    + _raise("a manifest is inserted as proposed", "tr_dataset_split_manifests_insert_guard_status")
    + "END IF; RETURN NEW; END $$"
)
_MANIFEST_UPDATE_FUNCTION = (
    "CREATE FUNCTION dataset_split_manifests_update_guard() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF OLD.status <> 'proposed' THEN "
    + _raise(
        "a locked or rejected manifest never changes",
        "tr_dataset_split_manifests_update_guard_final",
    )
    + "END IF; "
    "IF NEW.status NOT IN ('locked', 'rejected') THEN "
    + _raise(
        "a proposed manifest can only be locked or rejected",
        "tr_dataset_split_manifests_update_guard_transition",
    )
    + "END IF; "
    "IF (to_jsonb(NEW) - 'status' - 'leakage_reviews' - 'reviewed_leakage_report_sha256' "
    "- 'frozen_manifest_sha256' - 'approved_by' - 'locked_at' - 'updated_at') "
    "IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'leakage_reviews' "
    "- 'reviewed_leakage_report_sha256' - 'frozen_manifest_sha256' - 'approved_by' "
    "- 'locked_at' - 'updated_at') THEN "
    + _raise(
        "a manifest is immutable except its lock or rejection",
        "tr_dataset_split_manifests_update_guard_frozen",
    )
    + "END IF; "
    "IF NEW.status = 'rejected' AND NEW.leakage_reviews IS DISTINCT FROM OLD.leakage_reviews THEN "
    + _raise("a rejection records no review", "tr_dataset_split_manifests_update_guard_frozen")
    + "END IF; "
    "IF NEW.status = 'locked' THEN "
    "IF (SELECT count(*) FROM dataset_split_manifest_roles "
    "WHERE manifest_id = NEW.id AND record_count >= 1) <> 4 THEN "
    + _raise(
        "a lock needs four roles with at least one record each",
        "tr_dataset_split_manifests_update_guard_roles",
    )
    + "END IF; "
    "IF EXISTS (SELECT 1 FROM dataset_split_manifest_roles r WHERE r.manifest_id = NEW.id "
    "AND (r.record_count <> (SELECT count(*) FROM dataset_split_assignments a "
    "WHERE a.manifest_id = r.manifest_id AND a.role = r.role) "
    "OR r.group_count <> (SELECT count(DISTINCT a.group_id) FROM dataset_split_assignments a "
    "WHERE a.manifest_id = r.manifest_id AND a.role = r.role))) THEN "
    + _raise(
        "role counts do not match the stored assignments",
        "tr_dataset_split_manifests_update_guard_counts",
    )
    + "END IF; "
    "IF EXISTS (SELECT 1 FROM dataset_split_assignments a WHERE a.manifest_id = NEW.id "
    "GROUP BY a.group_id HAVING count(DISTINCT a.role) > 1) THEN "
    + _raise("a group crosses a split boundary", "tr_dataset_split_manifests_update_guard_groups")
    + "END IF; "
    "IF NOT EXISTS (SELECT 1 FROM dataset_split_manifest_sources WHERE manifest_id = NEW.id) "
    "THEN "
    + _raise("a lock needs at least one source", "tr_dataset_split_manifests_update_guard_sources")
    + "END IF; "
    "END IF; RETURN NEW; END $$"
)


def _child_guard(function: str, trigger_prefix: str, extra: str = "") -> str:
    return (
        f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE manifest_status text; source_purpose text; snapshot text[]; "
        "BEGIN "
        "SELECT status INTO manifest_status FROM dataset_split_manifests "
        "WHERE id = NEW.manifest_id AND organization_id = NEW.organization_id; "
        "IF manifest_status IS DISTINCT FROM 'proposed' THEN "
        + _raise("rows are added only to a proposed manifest", f"{trigger_prefix}_not_proposed")
        + "END IF; "
        + extra
        + "RETURN NEW; END $$"
    )


_ASSIGNMENT_EXTRA = (
    "SELECT dataset_purpose, allowed_purposes_snapshot INTO source_purpose, snapshot "
    "FROM dataset_split_manifest_sources "
    "WHERE manifest_id = NEW.manifest_id AND dataset_id = NEW.source_dataset_id; "
    "IF source_purpose IS NULL THEN "
    + _raise(
        "a record needs a source of its own manifest",
        "tr_dataset_split_assignments_insert_guard_source",
    )
    + "END IF; "
    "IF source_purpose = 'held_out_evaluation' AND NEW.role <> 'final_qualification' THEN "
    + _raise(
        "a held-out dataset is pinned to the final role",
        "tr_dataset_split_assignments_insert_guard_pinned",
    )
    + "END IF; "
    "IF source_purpose = 'candidate_development' AND NEW.role <> 'development_eval' THEN "
    + _raise(
        "a development dataset is pinned to the development role",
        "tr_dataset_split_assignments_insert_guard_pinned",
    )
    + "END IF; "
    "IF NOT (CASE NEW.role WHEN 'train' THEN 'training' WHEN 'validation' THEN 'validation' "
    "WHEN 'development_eval' THEN 'candidate_development' "
    "ELSE 'held_out_evaluation' END) = ANY (snapshot) THEN "
    + _raise(
        "the purpose of the role is not in the frozen grant of its source",
        "tr_dataset_split_assignments_insert_guard_grant",
    )
    + "END IF; "
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


def _uuid(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_dataset_quality_reports_hash_identity",
        "dataset_quality_reports",
        ["organization_id", "id", "report_sha256"],
    )

    op.create_table(
        "dataset_split_manifests",
        _uuid_pk(),
        _org_fk("dataset_split_manifests"),
        _uuid("project_id"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_version", sa.Integer(), nullable=True),
        sa.Column("strategy", sa.String(64), nullable=False),
        sa.Column("seed", sa.String(128), nullable=False),
        sa.Column("ratios", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("cutoff", sa.BigInteger(), nullable=True),
        sa.Column("normalization_version", sa.String(64), nullable=False),
        sa.Column("tokenizer_identity", sa.String(200), nullable=True),
        sa.Column("split_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("proposal_sha256", sa.String(64), nullable=False),
        sa.Column("leakage_report", postgresql.JSONB(), nullable=False),
        sa.Column("leakage_report_sha256", sa.String(64), nullable=False),
        sa.Column("leakage_status", sa.String(16), nullable=False),
        sa.Column(
            "leakage_reviews",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("reviewed_leakage_report_sha256", sa.String(64), nullable=True),
        sa.Column("migrated_from_legacy_sha256", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("frozen_manifest_sha256", sa.String(64), nullable=True),
        sa.Column("approved_by", sa.String(200), nullable=True),
        _now_col("locked_at", nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        _now_col("created_at"),
        _now_col("updated_at"),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_dataset_split_manifests_organization_id_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id", "name", "parent_version"],
            [
                "dataset_split_manifests.organization_id",
                "dataset_split_manifests.project_id",
                "dataset_split_manifests.name",
                "dataset_split_manifests.version",
            ],
            ondelete="RESTRICT",
            name="fk_dataset_split_manifests_parent",
        ),
        sa.UniqueConstraint(
            "project_id", "name", "version", name="uq_dataset_split_manifests_project_name_version"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "project_id",
            "name",
            "version",
            name="uq_dataset_split_manifests_lineage",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_dataset_split_manifests_org_id_id"),
        sa.CheckConstraint("name ~ '^\\S(.*\\S)?$'", name="ck_dataset_split_manifests_name_clean"),
        sa.CheckConstraint("version >= 1", name="ck_dataset_split_manifests_version_min"),
        sa.CheckConstraint(
            "(version = 1) = (parent_version IS NULL)",
            name="ck_dataset_split_manifests_first_has_no_parent",
        ),
        sa.CheckConstraint(
            "parent_version IS NULL OR parent_version = version - 1",
            name="ck_dataset_split_manifests_parent_is_previous",
        ),
        sa.CheckConstraint(
            f"strategy IN {_in(_STRATEGIES)}", name="ck_dataset_split_manifests_strategy"
        ),
        sa.CheckConstraint(
            "seed <> '' AND normalization_version <> '' AND created_by <> ''",
            name="ck_dataset_split_manifests_text_not_blank",
        ),
        sa.CheckConstraint(
            "cardinality(ratios) = 4 AND array_position(ratios, NULL::integer) IS NULL "
            "AND 0 <= ALL (ratios) AND 100 >= ALL (ratios) "
            "AND ((strategy = 'legacy_three_split_v1' AND ratios = ARRAY[0, 0, 0, 0]) "
            "OR (strategy <> 'legacy_three_split_v1' AND ratios[1] + ratios[2] + ratios[3] "
            "+ ratios[4] = 100 AND ratios[1] >= 1 AND ratios[4] >= 1))",
            name="ck_dataset_split_manifests_ratios",
        ),
        sa.CheckConstraint(
            "cutoff IS NULL OR cutoff >= 0", name="ck_dataset_split_manifests_cutoff"
        ),
        sa.CheckConstraint(
            f"split_manifest_sha256 ~ {_HEX64} AND proposal_sha256 ~ {_HEX64} "
            f"AND leakage_report_sha256 ~ {_HEX64} "
            "AND (reviewed_leakage_report_sha256 IS NULL "
            f"OR reviewed_leakage_report_sha256 ~ {_HEX64}) "
            "AND (migrated_from_legacy_sha256 IS NULL "
            f"OR migrated_from_legacy_sha256 ~ {_HEX64}) "
            f"AND (frozen_manifest_sha256 IS NULL OR frozen_manifest_sha256 ~ {_HEX64})",
            name="ck_dataset_split_manifests_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(leakage_report) = 'object' AND jsonb_typeof(leakage_reviews) = 'array'",
            name="ck_dataset_split_manifests_leakage_json",
        ),
        sa.CheckConstraint(
            f"leakage_status IN {_in(_LEAKAGE_STATUSES)}",
            name="ck_dataset_split_manifests_leakage_status",
        ),
        sa.CheckConstraint(
            "(strategy = 'legacy_three_split_v1') = (migrated_from_legacy_sha256 IS NOT NULL)",
            name="ck_dataset_split_manifests_legacy_lineage",
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'locked', 'rejected')",
            name="ck_dataset_split_manifests_status",
        ),
        sa.CheckConstraint(
            "(status = 'locked' AND locked_at IS NOT NULL AND approved_by IS NOT NULL "
            "AND approved_by <> '' AND frozen_manifest_sha256 IS NOT NULL "
            "AND reviewed_leakage_report_sha256 IS NOT NULL) "
            "OR (status <> 'locked' AND locked_at IS NULL AND approved_by IS NULL "
            "AND frozen_manifest_sha256 IS NULL AND reviewed_leakage_report_sha256 IS NULL)",
            name="ck_dataset_split_manifests_lock_columns",
        ),
        sa.CheckConstraint(
            "status <> 'locked' OR leakage_status IN ('clear', 'leakage_found')",
            name="ck_dataset_split_manifests_locked_leakage",
        ),
    )
    op.create_index(
        "ix_dataset_split_manifests_organization_id",
        "dataset_split_manifests",
        ["organization_id"],
    )
    op.create_index(
        "ix_dataset_split_manifests_project_name",
        "dataset_split_manifests",
        ["project_id", "name", "version"],
    )

    op.create_table(
        "dataset_split_manifest_roles",
        _uuid_pk(),
        _org_fk("dataset_split_manifest_roles"),
        _uuid("manifest_id"),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("group_count", sa.Integer(), nullable=False),
        sa.Column("role_sha256", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "manifest_id"],
            ["dataset_split_manifests.organization_id", "dataset_split_manifests.id"],
            ondelete="RESTRICT",
            name="fk_dataset_split_manifest_roles_manifest",
        ),
        sa.UniqueConstraint("manifest_id", "role", name="uq_dataset_split_manifest_roles_role"),
        sa.CheckConstraint(
            "(role = 'train' AND purpose = 'training') "
            "OR (role = 'validation' AND purpose = 'validation') "
            "OR (role = 'development_eval' AND purpose = 'candidate_development') "
            "OR (role = 'final_qualification' AND purpose = 'held_out_evaluation')",
            name="ck_dataset_split_manifest_roles_pair",
        ),
        sa.CheckConstraint(
            "record_count >= 0 AND group_count >= 0 AND group_count <= record_count "
            "AND ((record_count = 0) = (group_count = 0))",
            name="ck_dataset_split_manifest_roles_counts",
        ),
        sa.CheckConstraint(f"role_sha256 ~ {_HEX64}", name="ck_dataset_split_manifest_roles_hash"),
    )
    op.create_index(
        "ix_dataset_split_manifest_roles_organization_id",
        "dataset_split_manifest_roles",
        ["organization_id"],
    )

    op.create_table(
        "dataset_split_manifest_sources",
        _uuid_pk(),
        _org_fk("dataset_split_manifest_sources"),
        _uuid("manifest_id"),
        _uuid("project_id"),
        _uuid("dataset_id"),
        sa.Column("dataset_name", sa.String(200), nullable=False),
        sa.Column("dataset_purpose", sa.String(32), nullable=False),
        sa.Column("dataset_version", sa.Integer(), nullable=False),
        sa.Column("dataset_sha256", sa.String(64), nullable=False),
        _uuid("dataset_source_connection_id"),
        sa.Column("allowed_purposes_snapshot", postgresql.ARRAY(sa.Text()), nullable=False),
        _uuid("quality_report_id"),
        sa.Column("quality_report_sha256", sa.String(64), nullable=False),
        sa.Column("excluded_positions_sha256", sa.String(64), nullable=False),
        _now_col("created_at"),
        sa.ForeignKeyConstraint(
            ["organization_id", "manifest_id"],
            ["dataset_split_manifests.organization_id", "dataset_split_manifests.id"],
            ondelete="RESTRICT",
            name="fk_dataset_split_manifest_sources_manifest",
        ),
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
            name="fk_dataset_split_manifest_sources_dataset_lineage",
        ),
        sa.ForeignKeyConstraint(
            [
                "organization_id",
                "quality_report_id",
                "project_id",
                "dataset_id",
                "dataset_name",
                "dataset_purpose",
                "dataset_version",
                "dataset_sha256",
            ],
            [
                "dataset_quality_reports.organization_id",
                "dataset_quality_reports.id",
                "dataset_quality_reports.project_id",
                "dataset_quality_reports.dataset_id",
                "dataset_quality_reports.dataset_name",
                "dataset_quality_reports.dataset_purpose",
                "dataset_quality_reports.dataset_version",
                "dataset_quality_reports.dataset_sha256",
            ],
            ondelete="RESTRICT",
            name="fk_dataset_split_manifest_sources_report_identity",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "quality_report_id", "quality_report_sha256"],
            [
                "dataset_quality_reports.organization_id",
                "dataset_quality_reports.id",
                "dataset_quality_reports.report_sha256",
            ],
            ondelete="RESTRICT",
            name="fk_dataset_split_manifest_sources_report_hash",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id", "dataset_source_connection_id"],
            [
                "dataset_source_connections.organization_id",
                "dataset_source_connections.project_id",
                "dataset_source_connections.id",
            ],
            ondelete="RESTRICT",
            name="fk_dataset_split_manifest_sources_connection",
        ),
        sa.UniqueConstraint(
            "manifest_id", "dataset_id", name="uq_dataset_split_manifest_sources_dataset"
        ),
        sa.CheckConstraint(
            f"dataset_purpose IN {_in(_SOURCE_PURPOSES)}",
            name="ck_dataset_split_manifest_sources_purpose",
        ),
        sa.CheckConstraint(
            f"cardinality(allowed_purposes_snapshot) >= 1 "
            f"AND array_position(allowed_purposes_snapshot, NULL::text) IS NULL "
            f"AND allowed_purposes_snapshot <@ {_array(_ALL_PURPOSES)}",
            name="ck_dataset_split_manifest_sources_snapshot",
        ),
        sa.CheckConstraint(
            f"quality_report_sha256 ~ {_HEX64} AND excluded_positions_sha256 ~ {_HEX64} "
            f"AND dataset_sha256 ~ {_HEX64}",
            name="ck_dataset_split_manifest_sources_hashes",
        ),
    )
    op.create_index(
        "ix_dataset_split_manifest_sources_organization_id",
        "dataset_split_manifest_sources",
        ["organization_id"],
    )

    op.create_table(
        "dataset_split_assignments",
        _org_fk("dataset_split_assignments"),
        _uuid("manifest_id"),
        sa.Column("record_id", sa.String(400), nullable=False),
        _uuid("source_dataset_id"),
        sa.Column("batch_position", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("group_id", sa.String(400), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.Column("normalized_sha256", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("manifest_id", "record_id", name="pk_dataset_split_assignments"),
        sa.ForeignKeyConstraint(
            ["organization_id", "manifest_id"],
            ["dataset_split_manifests.organization_id", "dataset_split_manifests.id"],
            ondelete="RESTRICT",
            name="fk_dataset_split_assignments_manifest",
        ),
        sa.ForeignKeyConstraint(
            ["manifest_id", "source_dataset_id"],
            [
                "dataset_split_manifest_sources.manifest_id",
                "dataset_split_manifest_sources.dataset_id",
            ],
            ondelete="RESTRICT",
            name="fk_dataset_split_assignments_source",
        ),
        sa.CheckConstraint(f"role IN {_in(_ROLES)}", name="ck_dataset_split_assignments_role"),
        sa.CheckConstraint(
            "batch_position >= 0 AND group_id <> '' "
            "AND record_id = source_dataset_id::text || ':' || batch_position::text",
            name="ck_dataset_split_assignments_record_id",
        ),
        sa.CheckConstraint(
            f"record_sha256 ~ {_HEX64} AND normalized_sha256 ~ {_HEX64}",
            name="ck_dataset_split_assignments_hashes",
        ),
    )
    op.create_index(
        "ix_dataset_split_assignments_organization_id",
        "dataset_split_assignments",
        ["organization_id"],
    )
    op.create_index(
        "ix_dataset_split_assignments_manifest_role",
        "dataset_split_assignments",
        ["manifest_id", "role"],
    )

    # Manifests: SELECT/INSERT, and UPDATE of the lock columns only. Children: SELECT/INSERT.
    # DELETE is never granted.
    op.execute(f'GRANT SELECT, INSERT ON dataset_split_manifests TO "{APP_ROLE_NAME}"')
    op.execute(
        f"GRANT UPDATE ({', '.join(_MANIFEST_UPDATABLE)}) ON dataset_split_manifests "
        f'TO "{APP_ROLE_NAME}"'
    )
    for table in _TABLES[:3]:
        op.execute(f'GRANT SELECT, INSERT ON {table} TO "{APP_ROLE_NAME}"')

    op.execute(_MANIFEST_INSERT_FUNCTION)
    op.execute(
        "CREATE TRIGGER tr_dataset_split_manifests_insert_guard "
        "BEFORE INSERT ON dataset_split_manifests "
        "FOR EACH ROW EXECUTE FUNCTION dataset_split_manifests_insert_guard()"
    )
    op.execute(_MANIFEST_UPDATE_FUNCTION)
    op.execute(
        "CREATE TRIGGER tr_dataset_split_manifests_update_guard "
        "BEFORE UPDATE ON dataset_split_manifests "
        "FOR EACH ROW EXECUTE FUNCTION dataset_split_manifests_update_guard()"
    )
    for table, function in (
        ("dataset_split_manifest_roles", "dataset_split_manifest_roles_insert_guard"),
        ("dataset_split_manifest_sources", "dataset_split_manifest_sources_insert_guard"),
    ):
        op.execute(_child_guard(function, f"tr_{table}_insert_guard"))
        op.execute(
            f"CREATE TRIGGER tr_{table}_insert_guard BEFORE INSERT ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
        )
    op.execute(
        _child_guard(
            "dataset_split_assignments_insert_guard",
            "tr_dataset_split_assignments_insert_guard",
            _ASSIGNMENT_EXTRA,
        )
    )
    op.execute(
        "CREATE TRIGGER tr_dataset_split_assignments_insert_guard "
        "BEFORE INSERT ON dataset_split_assignments "
        "FOR EACH ROW EXECUTE FUNCTION dataset_split_assignments_insert_guard()"
    )

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    for table in (
        "dataset_split_manifests",
        "dataset_split_manifest_roles",
        "dataset_split_manifest_sources",
        "dataset_split_assignments",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            f"USING (organization_id = {tenant_setting}) "
            f"WITH CHECK (organization_id = {tenant_setting})"
        )


def downgrade() -> None:
    for table, function in (
        ("dataset_split_assignments", "dataset_split_assignments_insert_guard"),
        ("dataset_split_manifest_sources", "dataset_split_manifest_sources_insert_guard"),
        ("dataset_split_manifest_roles", "dataset_split_manifest_roles_insert_guard"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS tr_{table}_insert_guard ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {function}()")
    op.execute(
        "DROP TRIGGER IF EXISTS tr_dataset_split_manifests_update_guard "
        "ON dataset_split_manifests"
    )
    op.execute("DROP FUNCTION IF EXISTS dataset_split_manifests_update_guard()")
    op.execute(
        "DROP TRIGGER IF EXISTS tr_dataset_split_manifests_insert_guard "
        "ON dataset_split_manifests"
    )
    op.execute("DROP FUNCTION IF EXISTS dataset_split_manifests_insert_guard()")
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f'REVOKE ALL ON {table} FROM "{APP_ROLE_NAME}"')
    op.drop_index(
        "ix_dataset_split_assignments_manifest_role", table_name="dataset_split_assignments"
    )
    op.drop_index(
        "ix_dataset_split_assignments_organization_id", table_name="dataset_split_assignments"
    )
    op.drop_table("dataset_split_assignments")
    op.drop_index(
        "ix_dataset_split_manifest_sources_organization_id",
        table_name="dataset_split_manifest_sources",
    )
    op.drop_table("dataset_split_manifest_sources")
    op.drop_index(
        "ix_dataset_split_manifest_roles_organization_id",
        table_name="dataset_split_manifest_roles",
    )
    op.drop_table("dataset_split_manifest_roles")
    op.drop_index("ix_dataset_split_manifests_project_name", table_name="dataset_split_manifests")
    op.drop_index(
        "ix_dataset_split_manifests_organization_id", table_name="dataset_split_manifests"
    )
    op.drop_table("dataset_split_manifests")
    op.drop_constraint(
        "uq_dataset_quality_reports_hash_identity", "dataset_quality_reports", type_="unique"
    )
