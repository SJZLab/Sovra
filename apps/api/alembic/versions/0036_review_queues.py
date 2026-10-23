"""Argilla review queues, queue items and reviewer responses (FTF-R006-P2,
Section 8-10, 65, 67, 77.3-77.6, 78.4, 80.1-80.4, 81).

FTF-R006-P1b (migration 0034) made "one Argilla workspace per (org, project)" and "one Argilla
user per (org, Sovra principal)" durable. This migration adds the three tables a *queue of
records to review* needs, so that what was queued, what each reviewer answered and which version
of that answer is current are Factory facts, not something re-derived from the Argilla server:

- `review_queues` -- one queue per (organization, project, source dataset version). The composite
  foreign key to `uq_datasets_quality_lineage` (0026) pins the queue to the exact dataset id /
  name / purpose / version / verified sha256 it was built from, so a queue can never name a
  dataset the organization does not own or one whose bytes changed. It also records the quality
  report id + sha and the sha of the excluded positions the queue was built under, the
  `queue_snapshot_sha256` over every queued (record id, record sha) pair (what FTF-R006-P3's
  `expected_queue_snapshot_sha256` will bind to) and the Argilla dataset id. A CHECK refuses the
  sealed purposes (`held_out_evaluation`, `baseline_only`) at the database, in addition to the
  store module's `sealed_purpose_refused`: the final suite is never queued for ordinary review.
  `status` moves `building -> ready` only, `argilla_dataset_id` is set once, everything else is
  frozen (BEFORE UPDATE trigger, for every role).
- `review_items` -- one row per queued record (`record_id` = `{dataset_id}:{position}`), with its
  group id, record sha256 and the sha256 of the text pushed to the reviewer. `status` is
  `pending` or `drift`; `drift` is sticky (a trigger refuses any move out of it) because a drifted
  item blocks export until a Control-plane-reviewed requalification, never a silent reset.
- `review_responses` -- one row per (item, reviewer identity): decision, rationale, the sha256 of
  what was read back from Argilla, and `row_version` (optimistic concurrency: a BEFORE UPDATE
  trigger accepts nothing but "+1 on row_version, only decision / rationale / response_sha256
  changed").

All three: ENABLE + FORCE row-level security with the same `tenant_isolation` policy as every
prior tenant table, `sovra_app` granted SELECT/INSERT/UPDATE only (never DELETE; Section 65.1),
every hash pinned to 64 lowercase hex by CHECK, composite foreign keys that carry
`organization_id` so a row can never reference a parent in another organization even under a raw
INSERT.

Revision ID: 0036_review_queues
Revises: 0034_argilla_review_identity
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0036_review_queues"
down_revision = "0034_argilla_review_identity"
branch_labels = None
depends_on = None

APP_ROLE_NAME = "sovra_app"

_TENANT_SETTING = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
_SHA = "'^[0-9a-f]{64}$'"
_UUID_SHAPE = "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"
_QUEUE_STATUSES = ("building", "ready")
_ITEM_STATUSES = ("pending", "drift")
_DECISIONS = ("accept", "reject")
_SEALED_PURPOSES = ("held_out_evaluation", "baseline_only")
_TABLES = ("review_responses", "review_items", "review_queues")

_QUEUE_GUARD_FUNCTION = (
    "CREATE FUNCTION review_queues_update_guard() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF (to_jsonb(NEW) - 'status' - 'argilla_dataset_id' - 'updated_at') "
    "IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'argilla_dataset_id' - 'updated_at') THEN "
    "RAISE EXCEPTION 'a review queue is immutable except status and argilla_dataset_id' "
    "USING ERRCODE = 'check_violation', "
    "CONSTRAINT = 'tr_review_queues_update_guard_frozen'; END IF; "
    "IF NEW.status IS DISTINCT FROM OLD.status AND NOT (OLD.status = 'building' "
    "AND NEW.status = 'ready') THEN "
    "RAISE EXCEPTION 'a review queue may only move building -> ready' "
    "USING ERRCODE = 'check_violation', "
    "CONSTRAINT = 'tr_review_queues_update_guard_status'; END IF; "
    "IF OLD.argilla_dataset_id IS NOT NULL "
    "AND NEW.argilla_dataset_id IS DISTINCT FROM OLD.argilla_dataset_id THEN "
    "RAISE EXCEPTION 'argilla_dataset_id is set once' "
    "USING ERRCODE = 'check_violation', "
    "CONSTRAINT = 'tr_review_queues_update_guard_dataset_id'; END IF; "
    "RETURN NEW; END $$"
)
_ITEM_GUARD_FUNCTION = (
    "CREATE FUNCTION review_items_update_guard() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF (to_jsonb(NEW) - 'status' - 'drift_reason' - 'argilla_record_id' - 'updated_at') "
    "IS DISTINCT FROM "
    "(to_jsonb(OLD) - 'status' - 'drift_reason' - 'argilla_record_id' - 'updated_at') THEN "
    "RAISE EXCEPTION 'a review item is immutable except status, drift_reason, argilla_record_id' "
    "USING ERRCODE = 'check_violation', "
    "CONSTRAINT = 'tr_review_items_update_guard_frozen'; END IF; "
    "IF OLD.status = 'drift' AND (NEW.status IS DISTINCT FROM OLD.status "
    "OR NEW.drift_reason IS DISTINCT FROM OLD.drift_reason) THEN "
    "RAISE EXCEPTION 'drift is sticky' "
    "USING ERRCODE = 'check_violation', "
    "CONSTRAINT = 'tr_review_items_update_guard_drift_sticky'; END IF; "
    "IF OLD.argilla_record_id IS NOT NULL "
    "AND NEW.argilla_record_id IS DISTINCT FROM OLD.argilla_record_id THEN "
    "RAISE EXCEPTION 'argilla_record_id is set once' "
    "USING ERRCODE = 'check_violation', "
    "CONSTRAINT = 'tr_review_items_update_guard_record_id'; END IF; "
    "RETURN NEW; END $$"
)
_RESPONSE_GUARD_FUNCTION = (
    "CREATE FUNCTION review_responses_update_guard() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF NEW.row_version IS DISTINCT FROM OLD.row_version + 1 THEN "
    "RAISE EXCEPTION 'row_version must rise by exactly 1' "
    "USING ERRCODE = 'check_violation', "
    "CONSTRAINT = 'tr_review_responses_update_guard_step'; END IF; "
    "IF (to_jsonb(NEW) - 'decision' - 'rationale' - 'response_sha256' - 'row_version' "
    "- 'updated_at') IS DISTINCT FROM (to_jsonb(OLD) - 'decision' - 'rationale' "
    "- 'response_sha256' - 'row_version' - 'updated_at') THEN "
    "RAISE EXCEPTION 'a review response is immutable except decision, rationale, hash, version' "
    "USING ERRCODE = 'check_violation', "
    "CONSTRAINT = 'tr_review_responses_update_guard_frozen'; END IF; "
    "RETURN NEW; END $$"
)


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def _pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _org(table: str) -> sa.Column:
    return sa.Column(
        "organization_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE", name=f"fk_{table}_org"),
        nullable=False,
    )


def _uuid(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable)


def _now_col(name: str) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def _sha_check(table: str, column: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"{column} ~ {_SHA}", name=f"ck_{table}_{column}_shape")


def upgrade() -> None:
    op.create_table(
        "review_queues",
        _pk(),
        _org("review_queues"),
        _uuid("project_id"),
        _uuid("dataset_id"),
        sa.Column("dataset_name", sa.String(200), nullable=False),
        sa.Column("dataset_purpose", sa.String(32), nullable=False),
        sa.Column("dataset_version", sa.Integer, nullable=False),
        sa.Column("dataset_sha256", sa.String(64), nullable=False),
        _uuid("scope_id"),
        sa.Column("argilla_dataset_id", sa.String(36), nullable=True),
        sa.Column("argilla_dataset_name", sa.String(64), nullable=False),
        _uuid("quality_report_id"),
        sa.Column("quality_report_sha256", sa.String(64), nullable=False),
        sa.Column("excluded_positions_sha256", sa.String(64), nullable=False),
        sa.Column("queue_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("record_count", sa.Integer, nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="building"),
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
            name="fk_review_queues_dataset_lineage",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "scope_id"],
            ["argilla_review_scopes.organization_id", "argilla_review_scopes.id"],
            ondelete="RESTRICT",
            name="fk_review_queues_org_scope",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_review_queues_org_id"),
        sa.UniqueConstraint(
            "organization_id", "project_id", "dataset_id", name="uq_review_queues_org_dataset"
        ),
        sa.UniqueConstraint("argilla_dataset_id", name="uq_review_queues_argilla_dataset_id"),
        sa.CheckConstraint(f"status IN {_in(_QUEUE_STATUSES)}", name="ck_review_queues_status"),
        sa.CheckConstraint(
            f"dataset_purpose NOT IN {_in(_SEALED_PURPOSES)}",
            name="ck_review_queues_purpose_not_sealed",
        ),
        sa.CheckConstraint(
            "record_count BETWEEN 1 AND 100000", name="ck_review_queues_record_count"
        ),
        sa.CheckConstraint(
            f"argilla_dataset_id IS NULL OR argilla_dataset_id ~ {_UUID_SHAPE}",
            name="ck_review_queues_argilla_dataset_id_shape",
        ),
        sa.CheckConstraint(
            "status <> 'ready' OR argilla_dataset_id IS NOT NULL",
            name="ck_review_queues_ready_has_dataset",
        ),
        sa.CheckConstraint(
            "argilla_dataset_name ~ '^sovra-rq-[0-9a-f]{24}$'",
            name="ck_review_queues_argilla_dataset_name_shape",
        ),
        sa.CheckConstraint("created_by <> ''", name="ck_review_queues_created_by_not_blank"),
        _sha_check("review_queues", "dataset_sha256"),
        _sha_check("review_queues", "quality_report_sha256"),
        _sha_check("review_queues", "excluded_positions_sha256"),
        _sha_check("review_queues", "queue_snapshot_sha256"),
    )

    op.create_table(
        "review_items",
        _pk(),
        _org("review_items"),
        _uuid("queue_id"),
        sa.Column("record_id", sa.String(300), nullable=False),
        sa.Column("batch_position", sa.Integer, nullable=False),
        sa.Column("group_id", sa.String(64), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.Column("fields_sha256", sa.String(64), nullable=False),
        sa.Column("argilla_record_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("drift_reason", sa.String(64), nullable=True),
        _now_col("created_at"),
        _now_col("updated_at"),
        sa.ForeignKeyConstraint(
            ["organization_id", "queue_id"],
            ["review_queues.organization_id", "review_queues.id"],
            ondelete="RESTRICT",
            name="fk_review_items_org_queue",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_review_items_org_id"),
        sa.UniqueConstraint(
            "organization_id", "queue_id", "id", name="uq_review_items_org_queue_id"
        ),
        sa.UniqueConstraint("queue_id", "record_id", name="uq_review_items_queue_record"),
        sa.UniqueConstraint("queue_id", "batch_position", name="uq_review_items_queue_position"),
        sa.CheckConstraint(f"status IN {_in(_ITEM_STATUSES)}", name="ck_review_items_status"),
        sa.CheckConstraint("batch_position >= 0", name="ck_review_items_position"),
        sa.CheckConstraint(
            "(status = 'drift') = (drift_reason IS NOT NULL)",
            name="ck_review_items_drift_reason_consistent",
        ),
        sa.CheckConstraint(
            f"argilla_record_id IS NULL OR argilla_record_id ~ {_UUID_SHAPE}",
            name="ck_review_items_argilla_record_id_shape",
        ),
        _sha_check("review_items", "group_id"),
        _sha_check("review_items", "record_sha256"),
        _sha_check("review_items", "fields_sha256"),
    )

    op.create_table(
        "review_responses",
        _pk(),
        _org("review_responses"),
        _uuid("queue_id"),
        _uuid("item_id"),
        _uuid("identity_id"),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("rationale", sa.String(2000), nullable=False, server_default=""),
        sa.Column("response_sha256", sa.String(64), nullable=False),
        sa.Column("argilla_response_id", sa.String(36), nullable=False),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        _now_col("created_at"),
        _now_col("updated_at"),
        sa.ForeignKeyConstraint(
            ["organization_id", "queue_id", "item_id"],
            ["review_items.organization_id", "review_items.queue_id", "review_items.id"],
            ondelete="RESTRICT",
            name="fk_review_responses_org_queue_item",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "identity_id"],
            ["argilla_reviewer_identities.organization_id", "argilla_reviewer_identities.id"],
            ondelete="RESTRICT",
            name="fk_review_responses_org_identity",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_review_responses_org_id"),
        sa.UniqueConstraint("item_id", "identity_id", name="uq_review_responses_item_identity"),
        sa.UniqueConstraint("argilla_response_id", name="uq_review_responses_argilla_response_id"),
        sa.CheckConstraint(f"decision IN {_in(_DECISIONS)}", name="ck_review_responses_decision"),
        sa.CheckConstraint("row_version >= 1", name="ck_review_responses_row_version"),
        sa.CheckConstraint(
            f"argilla_response_id ~ {_UUID_SHAPE}",
            name="ck_review_responses_argilla_response_id_shape",
        ),
        _sha_check("review_responses", "response_sha256"),
    )

    # Written out literally per table so the static datastore-exposure scanner
    # (scripts/data_exposure_check.py), which greps this file for the literal table name, sees
    # each ENABLE / FORCE / CREATE POLICY directly -- the convention every prior migration uses.
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON review_queues TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE review_queues ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE review_queues FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON review_queues FOR ALL "
        f"USING (organization_id = {_TENANT_SETTING}) "
        f"WITH CHECK (organization_id = {_TENANT_SETTING})"
    )

    op.execute(f'GRANT SELECT, INSERT, UPDATE ON review_items TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE review_items ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE review_items FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON review_items FOR ALL "
        f"USING (organization_id = {_TENANT_SETTING}) "
        f"WITH CHECK (organization_id = {_TENANT_SETTING})"
    )

    op.execute(f'GRANT SELECT, INSERT, UPDATE ON review_responses TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE review_responses ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE review_responses FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON review_responses FOR ALL "
        f"USING (organization_id = {_TENANT_SETTING}) "
        f"WITH CHECK (organization_id = {_TENANT_SETTING})"
    )

    op.execute(_QUEUE_GUARD_FUNCTION)
    op.execute(
        "CREATE TRIGGER tr_review_queues_update_guard BEFORE UPDATE ON review_queues "
        "FOR EACH ROW EXECUTE FUNCTION review_queues_update_guard()"
    )
    op.execute(_ITEM_GUARD_FUNCTION)
    op.execute(
        "CREATE TRIGGER tr_review_items_update_guard BEFORE UPDATE ON review_items "
        "FOR EACH ROW EXECUTE FUNCTION review_items_update_guard()"
    )
    op.execute(_RESPONSE_GUARD_FUNCTION)
    op.execute(
        "CREATE TRIGGER tr_review_responses_update_guard BEFORE UPDATE ON review_responses "
        "FOR EACH ROW EXECUTE FUNCTION review_responses_update_guard()"
    )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS tr_{table}_update_guard ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_update_guard()")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f'REVOKE ALL ON {table} FROM "{APP_ROLE_NAME}"')
    op.drop_table("review_responses")
    op.drop_table("review_items")
    op.drop_table("review_queues")
