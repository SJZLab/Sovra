"""Versioned, immutable training experiments (FTF-0701-P2; Sections 6.15, 14.1-14.2, 15, 16
steps 1-9, 65.1, 68.2).

`training_experiments` stores one row per saved version of a typed training configuration: the
resolved `training_config.v1` document the pure module `workers/training/training_config_schema.py`
produced, its `config_sha256`, and the ids it was resolved against. A changed configuration is a
new `version` row with `parent_version`; a row is never edited or deleted.

- Composite foreign keys `(organization_id, x)` to `projects`, `trainable_artifacts`,
  `model_catalog_entries`, `dataset_split_manifests` and `budget_policies`, all ON DELETE RESTRICT,
  so an experiment can only name rows of its own organization.
- The consistency CHECK is the database-level held-out refusal: the stored document's
  `data.train.role` is exactly `train`, `data.validation.role` is exactly `validation`, both name
  the row's own `split_manifest_id`, and its schema version and method equal the columns.
- A BEFORE INSERT trigger (0030 child-guard style) is defense in depth: the split manifest must be
  `locked`, in the same project, with the row's own `frozen_manifest_sha256`; the artifact must be
  `ready` with an accepted license; the budget policy must be the project's own.
- ENABLE + FORCE row-level security with the `tenant_isolation` policy every prior migration uses
  (Section 65.1). `sovra_app` is granted SELECT and INSERT only (no UPDATE, no DELETE).

`status` accepts only `draft`: submission and admission belong to FTF-0801, which widens it by its
own migration.

Revision ID: 0040_training_experiments
Revises: 0039_eval_suite_sample_plans
Create Date: 2026-11-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0040_training_experiments"
down_revision = "0039_eval_suite_sample_plans"
branch_labels = None
depends_on = None

APP_ROLE_NAME = "sovra_app"
_HEX64 = "'^[0-9a-f]{64}$'"
_GUARD_FUNCTION = "training_experiments_insert_guard"


def _raise(message: str, constraint: str) -> str:
    return (
        f"RAISE EXCEPTION '{message}' USING ERRCODE = 'check_violation', "
        f"CONSTRAINT = '{constraint}'; "
    )


def _restrict_fk(column: str, target: str, name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id", column],
        [f"{target}.organization_id", f"{target}.id"],
        ondelete="RESTRICT",
        name=name,
    )


def upgrade() -> None:
    op.create_table(
        "training_experiments",
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
                ondelete="RESTRICT",
                name="fk_training_experiments_organization_id",
            ),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("config_schema_version", sa.String(32), nullable=False),
        sa.Column("config_json", postgresql.JSONB(), nullable=False),
        sa.Column("config_sha256", sa.String(64), nullable=False),
        sa.Column("trainable_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_catalog_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("split_manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("frozen_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("budget_policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("max_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("security_profile", sa.String(64), nullable=False),
        sa.Column("requested_by", sa.String(200), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        _restrict_fk("project_id", "projects", "fk_training_experiments_project"),
        _restrict_fk(
            "trainable_artifact_id", "trainable_artifacts", "fk_training_experiments_artifact"
        ),
        _restrict_fk(
            "model_catalog_entry_id", "model_catalog_entries", "fk_training_experiments_catalog"
        ),
        _restrict_fk(
            "split_manifest_id", "dataset_split_manifests", "fk_training_experiments_manifest"
        ),
        _restrict_fk("budget_policy_id", "budget_policies", "fk_training_experiments_budget"),
        sa.UniqueConstraint("organization_id", "id", name="uq_training_experiments_org_id_id"),
        sa.UniqueConstraint(
            "project_id", "name", "version", name="uq_training_experiments_project_name_version"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "requested_by",
            "idempotency_key",
            name="uq_training_experiments_idempotency",
        ),
        sa.CheckConstraint(
            "name ~ '^\\S(.*\\S)?$' AND char_length(name) <= 200",
            name="ck_training_experiments_name",
        ),
        sa.CheckConstraint("version >= 1", name="ck_training_experiments_version"),
        sa.CheckConstraint(
            "(version = 1 AND parent_version IS NULL) OR "
            "(version > 1 AND parent_version IS NOT NULL "
            "AND parent_version >= 1 AND parent_version < version)",
            name="ck_training_experiments_parent_version",
        ),
        sa.CheckConstraint("status = 'draft'", name="ck_training_experiments_status"),
        sa.CheckConstraint("method IN ('lora', 'qlora')", name="ck_training_experiments_method"),
        sa.CheckConstraint(
            "config_schema_version = 'training_config.v1'",
            name="ck_training_experiments_schema_version",
        ),
        sa.CheckConstraint(
            f"config_sha256 ~ {_HEX64}", name="ck_training_experiments_config_sha256"
        ),
        sa.CheckConstraint(
            f"frozen_manifest_sha256 ~ {_HEX64}", name="ck_training_experiments_frozen_sha256"
        ),
        sa.CheckConstraint(
            f"request_sha256 ~ {_HEX64}", name="ck_training_experiments_request_sha256"
        ),
        sa.CheckConstraint("max_cost > 0", name="ck_training_experiments_max_cost"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_training_experiments_currency"),
        sa.CheckConstraint(
            "security_profile IN ('standard', 'zero_egress', 'air_gapped')",
            name="ck_training_experiments_security_profile",
        ),
        sa.CheckConstraint(
            "requested_by ~ '^\\S(.*\\S)?$' AND char_length(requested_by) <= 200",
            name="ck_training_experiments_requested_by",
        ),
        sa.CheckConstraint(
            "char_length(request_id) BETWEEN 1 AND 128", name="ck_training_experiments_request_id"
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[\\x20-\\x7E]{1,128}$'",
            name="ck_training_experiments_idempotency_key",
        ),
        # The database-level held-out refusal: the stored document trains on `train` and validates
        # on `validation` of this row's own manifest, and agrees with the row's own columns.
        sa.CheckConstraint(
            "jsonb_typeof(config_json) = 'object' "
            "AND (config_json->>'schema_version') IS NOT DISTINCT FROM config_schema_version "
            "AND (config_json->>'method') IS NOT DISTINCT FROM method "
            "AND (config_json#>>'{data,train,role}') IS NOT DISTINCT FROM 'train' "
            "AND (config_json#>>'{data,validation,role}') IS NOT DISTINCT FROM 'validation' "
            "AND (config_json#>>'{data,train,split_manifest_id}') "
            "IS NOT DISTINCT FROM split_manifest_id::text "
            "AND (config_json#>>'{data,validation,split_manifest_id}') "
            "IS NOT DISTINCT FROM split_manifest_id::text",
            name="ck_training_experiments_config_consistency",
        ),
    )
    op.create_index(
        "ix_training_experiments_organization_id", "training_experiments", ["organization_id"]
    )
    op.create_index("ix_training_experiments_project_id", "training_experiments", ["project_id"])

    # Defense in depth (0030 child-guard style). It runs as the inserting role under row-level
    # security, so a row of another organization reads as absent and is refused the same way.
    op.execute(
        f"CREATE FUNCTION {_GUARD_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE manifest_status text; manifest_frozen text; manifest_project uuid; "
        "artifact_status text; artifact_license timestamptz; policy_project uuid; BEGIN "
        "SELECT status, frozen_manifest_sha256, project_id "
        "INTO manifest_status, manifest_frozen, manifest_project FROM dataset_split_manifests "
        "WHERE id = NEW.split_manifest_id AND organization_id = NEW.organization_id; "
        "IF manifest_status IS DISTINCT FROM 'locked' "
        "OR manifest_project IS DISTINCT FROM NEW.project_id THEN "
        + _raise(
            "an experiment names a locked split manifest of its own project",
            "tr_training_experiments_manifest_locked",
        )
        + "END IF; "
        "IF manifest_frozen IS DISTINCT FROM NEW.frozen_manifest_sha256 THEN "
        + _raise(
            "the frozen manifest hash differs from the locked manifest",
            "tr_training_experiments_manifest_frozen",
        )
        + "END IF; "
        "SELECT status, license_accepted_at INTO artifact_status, artifact_license "
        "FROM trainable_artifacts "
        "WHERE id = NEW.trainable_artifact_id AND organization_id = NEW.organization_id; "
        "IF artifact_status IS DISTINCT FROM 'ready' OR artifact_license IS NULL THEN "
        + _raise(
            "an experiment names a ready artifact with an accepted license",
            "tr_training_experiments_artifact_ready",
        )
        + "END IF; "
        "SELECT project_id INTO policy_project FROM budget_policies "
        "WHERE id = NEW.budget_policy_id AND organization_id = NEW.organization_id; "
        "IF policy_project IS DISTINCT FROM NEW.project_id THEN "
        + _raise(
            "an experiment names the budget policy of its own project",
            "tr_training_experiments_budget_project",
        )
        + "END IF; "
        "RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER tr_training_experiments_insert_guard BEFORE INSERT ON training_experiments "
        f"FOR EACH ROW EXECUTE FUNCTION {_GUARD_FUNCTION}()"
    )

    op.execute(f'GRANT SELECT, INSERT ON training_experiments TO "{APP_ROLE_NAME}"')

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute("ALTER TABLE training_experiments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE training_experiments FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON training_experiments FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS tr_training_experiments_insert_guard ON training_experiments"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {_GUARD_FUNCTION}()")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON training_experiments")
    op.execute(f'REVOKE ALL ON training_experiments FROM "{APP_ROLE_NAME}"')
    op.drop_index("ix_training_experiments_project_id", table_name="training_experiments")
    op.drop_index("ix_training_experiments_organization_id", table_name="training_experiments")
    op.drop_table("training_experiments")
