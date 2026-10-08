"""Dataset quality report, issue and resolution persistence (FTF-0516-P1,
Section 6.10/6.12/6.24/7A.10/8.1 steps 16-18/8.2/9/67.2/67.4/69.1/77/78.4).

FTF-0515's issue engine is a pure function set: it merges the FTF-0508..0514
detector reports into one ledger of issues and applies reviewer decisions, but
nothing persisted a ledger or a decision. FTF-0516's negative acceptance
("cannot edit locked data in place or mark a detector false positive without
recorded authorized rationale") needs both, so this migration is its
prerequisite slice.

Three tables, every one ENABLE + FORCE row-level security with the same
`tenant_isolation` policy every prior migration uses (Section 65.1), and none
with a DELETE grant:

- `dataset_quality_reports` -- one row per recorded detector run over one
  dataset version. The dataset identity (project, id, name, purpose, version,
  verified sha256) is copied and held true by a composite foreign key to
  `datasets` (a new unique constraint on `datasets` is the target). Immutable
  except `resolution_version`: `sovra_app` may UPDATE only
  `(resolution_version, updated_at)` and a BEFORE UPDATE trigger, for every
  role, accepts nothing but "+1 on resolution_version, no other column
  changed".
- `dataset_quality_issues` -- append-only ledger rows (SELECT/INSERT). The
  detector message is never stored (it can echo record values); only its
  SHA-256 is. `evidence_sha256` is pinned by CHECK to
  sha256(issue_id || ':' || dataset_sha256). Engine invariants are CHECKs: a
  detector failure is blocking with no positions, poisoning is blocking, and
  record indices pair one-to-one with batch positions.
- `dataset_issue_resolutions` -- append-only decisions (SELECT/INSERT), one
  per issue. A composite foreign key to the issue's
  `(organization, report, issue_id, severity, is_detector_failure,
  has_positions, evidence_sha256)` makes the copied flags truthful and makes
  `before_evidence_sha256` equal the issue's evidence; CHECKs refuse a
  resolution of a detector failure, a waiver of a blocking issue, a waiver
  by a role that may not waive, an exclusion without sorted positions, and
  bind `correct` / `new_version` to a successor dataset (same project, name
  and purpose, later version) through a second composite foreign key.

`has_positions` is a plain column held equal to `cardinality(batch_positions)
> 0` by CHECK rather than a GENERATED column: the database refuses a wrong
flag from any writer either way, and a plain column stays a portable ORM
attribute for the store's no-database tests.

Revision ID: 0026_dataset_quality_issues
Revises: 0025_dataset_upload_hardening
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0026_dataset_quality_issues"
down_revision = "0025_dataset_upload_hardening"
branch_labels = None
depends_on = None

APP_ROLE_NAME = "sovra_app"

_DETECTORS = (
    "record_schema",
    "tool_arguments",
    "tokenization",
    "exact_duplicate",
    "normalized_duplicate",
    "near_duplicate",
    "pii_secrets",
)
_CATEGORIES = (*_DETECTORS, "poisoning", "detector_failure")
_ACTIONS = ("exclude", "correct", "new_version", "waive")
_ROLES = ("ml_engineer", "workspace_admin", "organization_admin")
_ADMIN_ROLES = ("workspace_admin", "organization_admin")


def _array(values: tuple[str, ...]) -> str:
    return "ARRAY[" + ",".join(f"'{v}'" for v in values) + "]::text[]"


def _in(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


_HEX64 = "'^[0-9a-f]{64}$'"
_POSITIONS_FUNCTION = (
    "CREATE FUNCTION dataset_positions_strictly_increasing(p integer[]) RETURNS boolean "
    "LANGUAGE sql IMMUTABLE AS $$ SELECT p IS NOT NULL AND NOT EXISTS ("
    "SELECT 1 FROM generate_subscripts(p, 1) AS i "
    "WHERE p[i] IS NULL OR p[i] < 0 OR (i > 1 AND p[i] <= p[i - 1])) $$"
)
_REPORT_UPDATE_FUNCTION = (
    "CREATE FUNCTION dataset_quality_reports_resolution_guard() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF NEW.resolution_version IS DISTINCT FROM OLD.resolution_version + 1 THEN "
    "RAISE EXCEPTION 'resolution_version must rise by exactly 1' "
    "USING ERRCODE = 'check_violation', "
    "CONSTRAINT = 'tr_dataset_quality_reports_resolution_guard_step'; "
    "END IF; "
    "IF (to_jsonb(NEW) - 'resolution_version' - 'updated_at') "
    "IS DISTINCT FROM (to_jsonb(OLD) - 'resolution_version' - 'updated_at') THEN "
    "RAISE EXCEPTION 'a quality report is immutable except its resolution_version' "
    "USING ERRCODE = 'check_violation', "
    "CONSTRAINT = 'tr_dataset_quality_reports_resolution_guard_frozen'; "
    "END IF; "
    "RETURN NEW; END $$"
)
_RESOLUTION_INSERT_FUNCTION = (
    "CREATE FUNCTION dataset_issue_resolutions_insert_guard() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ DECLARE issue_positions integer[]; BEGIN "
    "SELECT batch_positions INTO issue_positions FROM dataset_quality_issues "
    "WHERE organization_id = NEW.organization_id "
    "AND report_id = NEW.quality_report_id AND issue_id = NEW.issue_id; "
    "IF NEW.excluded_batch_positions IS NOT NULL "
    "AND NOT (NEW.excluded_batch_positions <@ COALESCE(issue_positions, ARRAY[]::integer[])) "
    "THEN RAISE EXCEPTION 'excluded positions must all belong to the issue' "
    "USING ERRCODE = 'check_violation', "
    "CONSTRAINT = 'tr_dataset_issue_resolutions_insert_guard_positions'; "
    "END IF; "
    "RETURN NEW; END $$"
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


def _now_col(name: str) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def _uuid(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_datasets_quality_lineage",
        "datasets",
        [
            "organization_id",
            "project_id",
            "id",
            "name",
            "purpose",
            "version",
            "verified_sha256",
        ],
    )
    op.execute(_POSITIONS_FUNCTION)

    op.create_table(
        "dataset_quality_reports",
        _uuid_pk(),
        _org_fk("dataset_quality_reports"),
        _uuid("project_id"),
        _uuid("dataset_id"),
        sa.Column("dataset_name", sa.String(200), nullable=False),
        sa.Column("dataset_purpose", sa.String(32), nullable=False),
        sa.Column("dataset_version", sa.Integer(), nullable=False),
        sa.Column("dataset_sha256", sa.String(64), nullable=False),
        sa.Column("report_seq", sa.Integer(), nullable=False),
        sa.Column("report_sha256", sa.String(64), nullable=False),
        sa.Column("detector_versions", postgresql.JSONB(), nullable=False),
        sa.Column("detectors_reported", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("issue_count", sa.Integer(), nullable=False),
        sa.Column("blocking_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("resolution_version", sa.Integer(), nullable=False, server_default="0"),
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
            name="fk_dataset_quality_reports_dataset_lineage",
        ),
        sa.UniqueConstraint("dataset_id", "report_seq", name="uq_dataset_quality_reports_seq"),
        sa.UniqueConstraint(
            "dataset_id", "report_sha256", name="uq_dataset_quality_reports_report_sha256"
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_dataset_quality_reports_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "dataset_sha256",
            name="uq_dataset_quality_reports_sha_identity",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "project_id",
            "dataset_id",
            "dataset_name",
            "dataset_purpose",
            "dataset_version",
            "dataset_sha256",
            name="uq_dataset_quality_reports_dataset_identity",
        ),
        sa.CheckConstraint("report_seq >= 1", name="ck_dataset_quality_reports_seq_min"),
        sa.CheckConstraint(
            f"report_sha256 ~ {_HEX64} AND dataset_sha256 ~ {_HEX64}",
            name="ck_dataset_quality_reports_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(detector_versions) = 'object' "
            f"AND jsonb_exists_all(detector_versions, {_array(_DETECTORS)})",
            name="ck_dataset_quality_reports_detector_versions",
        ),
        sa.CheckConstraint(
            f"detectors_reported <@ {_array(_DETECTORS)}",
            name="ck_dataset_quality_reports_detectors_reported",
        ),
        sa.CheckConstraint(
            "issue_count >= 0 AND blocking_count >= 0 AND warning_count >= 0 "
            "AND issue_count = blocking_count + warning_count",
            name="ck_dataset_quality_reports_counts",
        ),
        sa.CheckConstraint(
            "resolution_version >= 0", name="ck_dataset_quality_reports_resolution_version_min"
        ),
    )
    op.create_index(
        "ix_dataset_quality_reports_organization_id",
        "dataset_quality_reports",
        ["organization_id"],
    )
    op.create_index(
        "ix_dataset_quality_reports_dataset_id", "dataset_quality_reports", ["dataset_id"]
    )

    op.create_table(
        "dataset_quality_issues",
        _uuid_pk(),
        _org_fk("dataset_quality_issues"),
        _uuid("report_id"),
        sa.Column("dataset_sha256", sa.String(64), nullable=False),
        sa.Column("issue_id", sa.String(64), nullable=False),
        sa.Column("detector", sa.String(64), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(100), nullable=False),
        sa.Column("message_sha256", sa.String(64), nullable=False),
        sa.Column("batch_positions", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("record_indices", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=True),
        sa.Column("is_detector_failure", sa.Boolean(), nullable=False),
        sa.Column("has_positions", sa.Boolean(), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        _now_col("created_at"),
        sa.ForeignKeyConstraint(
            ["organization_id", "report_id", "dataset_sha256"],
            [
                "dataset_quality_reports.organization_id",
                "dataset_quality_reports.id",
                "dataset_quality_reports.dataset_sha256",
            ],
            ondelete="RESTRICT",
            name="fk_dataset_quality_issues_report",
        ),
        sa.UniqueConstraint("report_id", "issue_id", name="uq_dataset_quality_issues_report_issue"),
        # The target of `dataset_issue_resolutions`' composite foreign key.
        sa.UniqueConstraint(
            "organization_id",
            "report_id",
            "issue_id",
            "severity",
            "is_detector_failure",
            "has_positions",
            "evidence_sha256",
            name="uq_dataset_quality_issues_resolution_identity",
        ),
        sa.CheckConstraint(
            f"issue_id ~ {_HEX64} AND message_sha256 ~ {_HEX64}",
            name="ck_dataset_quality_issues_hashes",
        ),
        sa.CheckConstraint("detector <> ''", name="ck_dataset_quality_issues_detector"),
        sa.CheckConstraint(
            f"category in {_in(_CATEGORIES)}", name="ck_dataset_quality_issues_category"
        ),
        sa.CheckConstraint(
            "severity in ('blocking','warning')", name="ck_dataset_quality_issues_severity"
        ),
        sa.CheckConstraint(
            "reason_code ~ '^[a-z][a-z0-9_]*$'", name="ck_dataset_quality_issues_reason_code"
        ),
        sa.CheckConstraint(
            f"evidence_digest IS NULL OR evidence_digest ~ {_HEX64}",
            name="ck_dataset_quality_issues_evidence_digest",
        ),
        sa.CheckConstraint(
            "array_position(batch_positions, NULL::integer) IS NULL "
            "AND 0 <= ALL (batch_positions)",
            name="ck_dataset_quality_issues_positions_valid",
        ),
        sa.CheckConstraint(
            "cardinality(record_indices) IN (0, cardinality(batch_positions))",
            name="ck_dataset_quality_issues_indices_pair_positions",
        ),
        sa.CheckConstraint(
            "has_positions = (cardinality(batch_positions) > 0)",
            name="ck_dataset_quality_issues_has_positions_truthful",
        ),
        sa.CheckConstraint(
            "evidence_sha256 = encode(sha256(convert_to(issue_id || ':' || dataset_sha256, "
            "'UTF8')), 'hex')",
            name="ck_dataset_quality_issues_evidence_sha256",
        ),
        sa.CheckConstraint(
            "(category = 'detector_failure') = is_detector_failure",
            name="ck_dataset_quality_issues_detector_failure_category",
        ),
        sa.CheckConstraint(
            "NOT is_detector_failure OR (severity = 'blocking' AND NOT has_positions)",
            name="ck_dataset_quality_issues_detector_failure_blocks",
        ),
        sa.CheckConstraint(
            "category <> 'poisoning' OR severity = 'blocking'",
            name="ck_dataset_quality_issues_poisoning_blocks",
        ),
    )
    op.create_index(
        "ix_dataset_quality_issues_organization_id", "dataset_quality_issues", ["organization_id"]
    )
    op.create_index("ix_dataset_quality_issues_report_id", "dataset_quality_issues", ["report_id"])

    op.create_table(
        "dataset_issue_resolutions",
        _uuid_pk(),
        _org_fk("dataset_issue_resolutions"),
        _uuid("quality_report_id"),
        sa.Column("issue_id", sa.String(64), nullable=False),
        _uuid("project_id"),
        _uuid("dataset_id"),
        sa.Column("dataset_name", sa.String(200), nullable=False),
        sa.Column("dataset_purpose", sa.String(32), nullable=False),
        sa.Column("dataset_version", sa.Integer(), nullable=False),
        sa.Column("dataset_sha256", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("is_detector_failure", sa.Boolean(), nullable=False),
        sa.Column("has_positions", sa.Boolean(), nullable=False),
        sa.Column("before_evidence_sha256", sa.String(64), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("engine_decision", sa.String(16), nullable=False),
        sa.Column(
            "excluded_batch_positions",
            postgresql.ARRAY(sa.Integer()),
            nullable=False,
            server_default=sa.text("'{}'::integer[]"),
        ),
        _uuid("successor_dataset_id", nullable=True),
        sa.Column("successor_version", sa.Integer(), nullable=True),
        sa.Column("successor_sha256", sa.String(64), nullable=True),
        sa.Column("after_evidence_sha256", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(200), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("request_id", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("resolution_version_after", sa.Integer(), nullable=False),
        _now_col("created_at"),
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
            name="fk_dataset_issue_resolutions_report_identity",
        ),
        sa.ForeignKeyConstraint(
            [
                "organization_id",
                "quality_report_id",
                "issue_id",
                "severity",
                "is_detector_failure",
                "has_positions",
                "before_evidence_sha256",
            ],
            [
                "dataset_quality_issues.organization_id",
                "dataset_quality_issues.report_id",
                "dataset_quality_issues.issue_id",
                "dataset_quality_issues.severity",
                "dataset_quality_issues.is_detector_failure",
                "dataset_quality_issues.has_positions",
                "dataset_quality_issues.evidence_sha256",
            ],
            ondelete="RESTRICT",
            name="fk_dataset_issue_resolutions_issue_evidence",
        ),
        # MATCH SIMPLE: skipped while successor_dataset_id is NULL.
        sa.ForeignKeyConstraint(
            [
                "organization_id",
                "project_id",
                "successor_dataset_id",
                "dataset_name",
                "dataset_purpose",
                "successor_version",
                "successor_sha256",
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
            name="fk_dataset_issue_resolutions_successor_lineage",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "quality_report_id",
            "issue_id",
            name="uq_dataset_issue_resolutions_one_decision",
        ),
        sa.CheckConstraint(
            f"action in {_in(_ACTIONS)}", name="ck_dataset_issue_resolutions_action"
        ),
        sa.CheckConstraint(
            "(action = 'waive') = (engine_decision = 'waived') "
            "AND engine_decision in ('resolved','waived')",
            name="ck_dataset_issue_resolutions_engine_decision",
        ),
        sa.CheckConstraint(
            "NOT is_detector_failure", name="ck_dataset_issue_resolutions_not_detector_failure"
        ),
        sa.CheckConstraint(
            "action <> 'waive' OR severity = 'warning'",
            name="ck_dataset_issue_resolutions_waive_warning_only",
        ),
        sa.CheckConstraint(
            f"action <> 'waive' OR actor_role in {_in(_ADMIN_ROLES)}",
            name="ck_dataset_issue_resolutions_waive_admin_only",
        ),
        sa.CheckConstraint(
            f"actor_role in {_in(_ROLES)}", name="ck_dataset_issue_resolutions_actor_role"
        ),
        sa.CheckConstraint(
            "action NOT IN ('exclude','correct') OR has_positions",
            name="ck_dataset_issue_resolutions_needs_positions",
        ),
        sa.CheckConstraint(
            "(action = 'exclude' AND dataset_positions_strictly_increasing("
            "excluded_batch_positions) AND cardinality(excluded_batch_positions) > 0) "
            "OR (action <> 'exclude' AND cardinality(excluded_batch_positions) = 0)",
            name="ck_dataset_issue_resolutions_excluded_positions",
        ),
        sa.CheckConstraint(
            "(successor_dataset_id IS NULL) = (successor_version IS NULL) "
            "AND (successor_dataset_id IS NULL) = (successor_sha256 IS NULL)",
            name="ck_dataset_issue_resolutions_successor_all_or_none",
        ),
        sa.CheckConstraint(
            "(action IN ('correct','new_version')) = (successor_dataset_id IS NOT NULL)",
            name="ck_dataset_issue_resolutions_successor_iff_new_data",
        ),
        sa.CheckConstraint(
            "successor_version IS NULL OR successor_version > dataset_version",
            name="ck_dataset_issue_resolutions_successor_later",
        ),
        sa.CheckConstraint(
            f"before_evidence_sha256 ~ {_HEX64} AND request_sha256 ~ {_HEX64} "
            f"AND (after_evidence_sha256 IS NULL OR after_evidence_sha256 ~ {_HEX64})",
            name="ck_dataset_issue_resolutions_hashes",
        ),
        sa.CheckConstraint(
            # COALESCE: a NULL comparison would otherwise make the CHECK pass.
            "COALESCE((action = 'waive' AND after_evidence_sha256 IS NULL) "
            "OR (action = 'exclude' AND after_evidence_sha256 = encode(sha256(convert_to("
            "dataset_sha256 || chr(58) || 'exclude' || chr(58) || "
            "array_to_string(excluded_batch_positions, ','), 'UTF8')), 'hex')) "
            "OR (action IN ('correct','new_version') "
            "AND after_evidence_sha256 = successor_sha256), false)",
            name="ck_dataset_issue_resolutions_after_evidence",
        ),
        sa.CheckConstraint(
            "reason ~ '^\\S(.*\\S)?$' AND char_length(reason) <= 2000",
            name="ck_dataset_issue_resolutions_reason",
        ),
        sa.CheckConstraint(
            "actor_id <> '' AND request_id <> '' AND idempotency_key <> ''",
            name="ck_dataset_issue_resolutions_text_not_blank",
        ),
        sa.CheckConstraint(
            "resolution_version_after >= 1", name="ck_dataset_issue_resolutions_version_min"
        ),
    )
    op.create_index(
        "ix_dataset_issue_resolutions_organization_id",
        "dataset_issue_resolutions",
        ["organization_id"],
    )
    op.create_index(
        "ix_dataset_issue_resolutions_idempotency",
        "dataset_issue_resolutions",
        ["organization_id", "idempotency_key"],
    )

    # Reports: SELECT/INSERT, and UPDATE of the resolution counter only.
    # Issues and resolutions: SELECT/INSERT only. DELETE is never granted.
    op.execute(f'GRANT SELECT, INSERT ON dataset_quality_reports TO "{APP_ROLE_NAME}"')
    op.execute(
        "GRANT UPDATE (resolution_version, updated_at) ON dataset_quality_reports "
        f'TO "{APP_ROLE_NAME}"'
    )
    op.execute(f'GRANT SELECT, INSERT ON dataset_quality_issues TO "{APP_ROLE_NAME}"')
    op.execute(f'GRANT SELECT, INSERT ON dataset_issue_resolutions TO "{APP_ROLE_NAME}"')

    op.execute(_REPORT_UPDATE_FUNCTION)
    op.execute(
        "CREATE TRIGGER tr_dataset_quality_reports_resolution_guard "
        "BEFORE UPDATE ON dataset_quality_reports "
        "FOR EACH ROW EXECUTE FUNCTION dataset_quality_reports_resolution_guard()"
    )
    op.execute(_RESOLUTION_INSERT_FUNCTION)
    op.execute(
        "CREATE TRIGGER tr_dataset_issue_resolutions_insert_guard "
        "BEFORE INSERT ON dataset_issue_resolutions "
        "FOR EACH ROW EXECUTE FUNCTION dataset_issue_resolutions_insert_guard()"
    )

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    for table in ("dataset_quality_reports", "dataset_quality_issues", "dataset_issue_resolutions"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            f"USING (organization_id = {tenant_setting}) "
            f"WITH CHECK (organization_id = {tenant_setting})"
        )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS tr_dataset_issue_resolutions_insert_guard "
        "ON dataset_issue_resolutions"
    )
    op.execute("DROP FUNCTION IF EXISTS dataset_issue_resolutions_insert_guard()")
    op.execute(
        "DROP TRIGGER IF EXISTS tr_dataset_quality_reports_resolution_guard "
        "ON dataset_quality_reports"
    )
    op.execute("DROP FUNCTION IF EXISTS dataset_quality_reports_resolution_guard()")
    for table in ("dataset_issue_resolutions", "dataset_quality_issues", "dataset_quality_reports"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f'REVOKE ALL ON {table} FROM "{APP_ROLE_NAME}"')
    op.drop_index(
        "ix_dataset_issue_resolutions_idempotency", table_name="dataset_issue_resolutions"
    )
    op.drop_index(
        "ix_dataset_issue_resolutions_organization_id", table_name="dataset_issue_resolutions"
    )
    op.drop_table("dataset_issue_resolutions")
    op.drop_index("ix_dataset_quality_issues_report_id", table_name="dataset_quality_issues")
    op.drop_index("ix_dataset_quality_issues_organization_id", table_name="dataset_quality_issues")
    op.drop_table("dataset_quality_issues")
    op.drop_index("ix_dataset_quality_reports_dataset_id", table_name="dataset_quality_reports")
    op.drop_index(
        "ix_dataset_quality_reports_organization_id", table_name="dataset_quality_reports"
    )
    op.drop_table("dataset_quality_reports")
    op.execute("DROP FUNCTION IF EXISTS dataset_positions_strictly_increasing(integer[])")
    op.drop_constraint("uq_datasets_quality_lineage", "datasets", type_="unique")
