"""Model import fields + import-validation jobs (FTF-H010-P1, Section
6.17/58A/68.1).

Migration prerequisite for FTF-H010's own "Endpoint, weights and adapter
import with honest continuation": `database_changes` says "Schema change
required under the single-writer migration lock; exact tenant-owned
fields from bound sections are frozen before fan-out." This slice owns
exactly that schema and a bounded enqueue/read API; it does not implement
FTF-H010's own real endpoint fine-tune, weights transfer, GPU dependency
or continuation logic, which stays `BLOCKED_EXTERNAL` and disclosed (see
`app/model_versions/model_import_routes.py`'s own module docstring).

This is a deliberate, disclosed evolution of FTF-1001's own scope choice:
`model_version_routes.py`'s own docstring states "'import' *is* creation
here (there is no separate asynchronous import job to track, since
resolving the referenced artifact/endpoint is already a prerequisite this
router enforces synchronously at create time)" -- true for the three
shapes FTF-1001 itself covers (`endpoint`/`full_weight`/`adapter`, each
resolved synchronously against an already-`ready` local artifact or a
well-formed HTTPS reference). FTF-H010's own real-endpoint/weights/GPU
validation genuinely cannot resolve synchronously inside one request, so
`model_import_jobs` adds the asynchronous enqueue/track surface FTF-1001
itself explicitly declined to build, without touching or widening
`model_versions`' own existing synchronous create path at all.

Two things, in one migration under the `migration:application` lock:

1. `model_versions` gains the §6.17 import-provenance fields it lacks,
   additive and backward compatible:

   - `origin` (`factory_trained`/`imported_weights`/`imported_adapter`/
     `external_endpoint`) -- a finer classification than the existing
     `import_kind` (`endpoint`/`full_weight`/`adapter`), backfilled from it
     (`endpoint` -> `external_endpoint`, `adapter` -> `imported_adapter`,
     `full_weight` -> `imported_weights`; disclosed reading: nothing in
     this repository's existing data distinguishes an organically
     Factory-trained `full_weight` row from an externally imported one,
     so every pre-existing `full_weight` row backfills to the more
     conservative `imported_weights` rather than an unproven
     `factory_trained` claim -- Law 1, never claim evidence that does not
     exist). `import_kind` is kept, unedited, per this slice's own scope
     note ("Do not drop import_kind in this slice").
   - `parent_model_version_id` -- a same-tenant, same-table lineage
     pointer (composite FK to `model_versions(organization_id, id)`,
     `RESTRICT`; a purely additive `UNIQUE (organization_id, id)` is added
     to `model_versions` itself for this self-reference, the same
     trivially-true-since-`id`-is-already-a-primary-key precedent
     `0009_model_versions.py`/`0022_model_catalog_entries.py` already
     applied to `trainable_artifacts`/`litellm_connections`).
   - `external_provider_model_ref`, `observed_deployment_fingerprint`,
     `tokenizer_revision`, `chat_template_sha256`, `serving_config_sha256`,
     `adapter_only`.

   Two new `CHECK` constraints enforce this slice's own negative_acceptance
   at the database level (never only in application code a bypassing
   write could evade):

   - `origin = 'external_endpoint'` requires `trainable_artifact_id` and
     `weights_digest` both `NULL` (no fictitious local weight bytes/hash
     for a row with no local weights -- the exact "No fictitious hash"
     negative_acceptance) and `external_provider_model_ref` `NOT NULL`.
   - `origin = 'imported_adapter'` requires an exact base revision
     (`base_model_revision IS NOT NULL`, already required by the existing
     `ck_model_versions_kind_consistency` for `import_kind = 'adapter'`)
     *and* `adapter_only = true`.

   Pre-existing `import_kind = 'endpoint'` rows backfill
   `external_provider_model_ref` from their own `endpoint_reference` (the
   closest existing proxy for "the external reference"), so the new
   `external_endpoint` CHECK holds for data that predates this migration
   too -- disclosed as a best-effort backfill, not a real provider model
   id resolution.

   Every `*_sha256` column is validated `^[0-9a-f]{64}$` when not `NULL`.

2. `model_import_jobs` -- one row per enqueued import request, `queued` ->
   `validating` -> (`failed` | `succeeded` | `blocked_external`), RLS
   `ENABLE` + `FORCE` with the same `tenant_isolation` policy every prior
   migration in this chain uses. Unlike `model_versions` (`SELECT, INSERT`
   only -- immutable by construction), a job's own state genuinely
   transitions after insert, so `sovra_app` gets a column-limited
   `UPDATE (state, failure_code, updated_at)` -- the same "the shape
   columns never change after insert, only the outcome columns do"
   discipline `0024_dataset_uploads.py`'s own `_SESSION_UPDATE_COLUMNS`
   already established -- and never `DELETE`. `CHECK
   (state IN ('failed','blocked_external')) = (failure_code IS NOT NULL)`
   mirrors `0024`'s own `(status='rejected') = (rejection_reason IS NOT
   NULL)` precedent. No task in this migration implements the worker that
   would ever move a row out of `queued` -- disclosed in
   `app/model_versions/model_import_routes.py`'s own module docstring,
   the same "wiring work, not yet done" disclosure FTF-0516-P1's own
   `record_quality_report` gap already carries.

Revision ID: 0027_model_import
Revises: 0025_dataset_upload_hardening
Create Date: 2026-10-08

Chained onto the current `main` head (`0025_dataset_upload_hardening`) at
claim time, per QUEUE.md's own migration-serialization rule ("take the
next free number at claim time ... renumber if another lands first"):
`0026_dataset_quality_issues` (FTF-0516-P1, PR #639) was still open, not
yet merged into `main`, when this migration was authored. If `0026` merges
first, this file's own `down_revision` is rebased onto it before merge
(two real Alembic heads otherwise) -- see this task's own submission entry
for which actually happened.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0027_model_import"
down_revision = "0025_dataset_upload_hardening"
branch_labels = None
depends_on = None

APP_ROLE_NAME = "sovra_app"

_ORIGINS = "('factory_trained','imported_weights','imported_adapter','external_endpoint')"
_LINEAGE_COMPLETENESS = "('complete','partial','unknown')"
_JOB_STATES = "('queued','validating','failed','succeeded','blocked_external')"
_SHA256_PATTERN = "^[0-9a-f]{64}$"
_JOB_UPDATE_COLUMNS = "state, failure_code, updated_at"


def upgrade() -> None:
    # -- 1. model_versions: additive import-provenance columns -----------
    op.add_column("model_versions", sa.Column("origin", sa.String(32), nullable=True))
    op.add_column(
        "model_versions",
        sa.Column(
            "lineage_completeness",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'unknown'"),
        ),
    )
    op.add_column(
        "model_versions",
        sa.Column(
            "adapter_only", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column(
        "model_versions",
        sa.Column("parent_model_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "model_versions", sa.Column("external_provider_model_ref", sa.String(2048), nullable=True)
    )
    op.add_column(
        "model_versions",
        sa.Column("observed_deployment_fingerprint", sa.String(256), nullable=True),
    )
    op.add_column(
        "model_versions", sa.Column("tokenizer_revision", sa.String(200), nullable=True)
    )
    op.add_column(
        "model_versions", sa.Column("chat_template_sha256", sa.String(64), nullable=True)
    )
    op.add_column(
        "model_versions", sa.Column("serving_config_sha256", sa.String(64), nullable=True)
    )

    # Backfill before the column and the new CHECKs become mandatory.
    op.execute(
        """
        UPDATE model_versions SET origin = CASE import_kind
            WHEN 'endpoint' THEN 'external_endpoint'
            WHEN 'adapter' THEN 'imported_adapter'
            WHEN 'full_weight' THEN 'imported_weights'
        END
        """
    )
    op.execute(
        "UPDATE model_versions SET external_provider_model_ref = endpoint_reference "
        "WHERE import_kind = 'endpoint' AND external_provider_model_ref IS NULL"
    )
    op.execute(
        "UPDATE model_versions SET adapter_only = true WHERE import_kind = 'adapter'"
    )

    op.alter_column("model_versions", "origin", nullable=False)

    op.create_unique_constraint(
        "uq_model_versions_organization_id_id", "model_versions", ["organization_id", "id"]
    )
    op.create_check_constraint(
        "ck_model_versions_origin_values", "model_versions", f"origin in {_ORIGINS}"
    )
    op.create_check_constraint(
        "ck_model_versions_lineage_completeness_values",
        "model_versions",
        f"lineage_completeness in {_LINEAGE_COMPLETENESS}",
    )
    op.create_check_constraint(
        "ck_model_versions_external_endpoint_consistency",
        "model_versions",
        "origin <> 'external_endpoint' OR ("
        "trainable_artifact_id IS NULL AND weights_digest IS NULL "
        "AND external_provider_model_ref IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_model_versions_imported_adapter_consistency",
        "model_versions",
        "origin <> 'imported_adapter' OR "
        "(base_model_revision IS NOT NULL AND adapter_only)",
    )
    op.create_check_constraint(
        "ck_model_versions_chat_template_sha256_format",
        "model_versions",
        f"chat_template_sha256 IS NULL OR chat_template_sha256 ~ '{_SHA256_PATTERN}'",
    )
    op.create_check_constraint(
        "ck_model_versions_serving_config_sha256_format",
        "model_versions",
        f"serving_config_sha256 IS NULL OR serving_config_sha256 ~ '{_SHA256_PATTERN}'",
    )
    op.create_foreign_key(
        "fk_model_versions_organization_id_parent_model_version_id",
        "model_versions",
        "model_versions",
        ["organization_id", "parent_model_version_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )

    # -- 2. model_import_jobs ---------------------------------------------
    op.create_table(
        "model_import_jobs",
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
                name="fk_model_import_jobs_organization_id",
            ),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_origin", sa.String(32), nullable=False),
        sa.Column(
            "parent_model_version_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("trainable_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("external_provider_model_ref", sa.String(2048), nullable=True),
        sa.Column(
            "state", sa.String(16), nullable=False, server_default=sa.text("'queued'")
        ),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
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
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_model_import_jobs_organization_id_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "parent_model_version_id"],
            ["model_versions.organization_id", "model_versions.id"],
            ondelete="RESTRICT",
            name="fk_model_import_jobs_organization_id_parent_model_version_id",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "trainable_artifact_id"],
            ["trainable_artifacts.organization_id", "trainable_artifacts.id"],
            ondelete="RESTRICT",
            name="fk_model_import_jobs_organization_id_trainable_artifact_id",
        ),
        sa.CheckConstraint(f"requested_origin in {_ORIGINS}", name="ck_model_import_jobs_origin"),
        sa.CheckConstraint(f"state in {_JOB_STATES}", name="ck_model_import_jobs_state"),
        sa.CheckConstraint(
            "(state in ('failed','blocked_external')) = (failure_code IS NOT NULL)",
            name="ck_model_import_jobs_failure_code_matches_state",
        ),
        sa.CheckConstraint("created_by <> ''", name="ck_model_import_jobs_created_by_not_blank"),
    )
    op.create_index(
        "ix_model_import_jobs_organization_id", "model_import_jobs", ["organization_id"]
    )
    op.create_index("ix_model_import_jobs_project_id", "model_import_jobs", ["project_id"])

    op.execute(f'GRANT SELECT, INSERT ON model_import_jobs TO "{APP_ROLE_NAME}"')
    op.execute(
        f'GRANT UPDATE ({_JOB_UPDATE_COLUMNS}) ON model_import_jobs TO "{APP_ROLE_NAME}"'
    )
    op.execute("ALTER TABLE model_import_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE model_import_jobs FORCE ROW LEVEL SECURITY")
    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON model_import_jobs FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON model_import_jobs")
    op.execute(f'REVOKE ALL ON model_import_jobs FROM "{APP_ROLE_NAME}"')
    op.drop_index("ix_model_import_jobs_project_id", table_name="model_import_jobs")
    op.drop_index("ix_model_import_jobs_organization_id", table_name="model_import_jobs")
    op.drop_table("model_import_jobs")

    op.drop_constraint(
        "fk_model_versions_organization_id_parent_model_version_id",
        "model_versions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_model_versions_serving_config_sha256_format", "model_versions", type_="check"
    )
    op.drop_constraint(
        "ck_model_versions_chat_template_sha256_format", "model_versions", type_="check"
    )
    op.drop_constraint(
        "ck_model_versions_imported_adapter_consistency", "model_versions", type_="check"
    )
    op.drop_constraint(
        "ck_model_versions_external_endpoint_consistency", "model_versions", type_="check"
    )
    op.drop_constraint(
        "ck_model_versions_lineage_completeness_values", "model_versions", type_="check"
    )
    op.drop_constraint("ck_model_versions_origin_values", "model_versions", type_="check")
    op.drop_constraint(
        "uq_model_versions_organization_id_id", "model_versions", type_="unique"
    )
    op.drop_column("model_versions", "serving_config_sha256")
    op.drop_column("model_versions", "chat_template_sha256")
    op.drop_column("model_versions", "tokenizer_revision")
    op.drop_column("model_versions", "observed_deployment_fingerprint")
    op.drop_column("model_versions", "external_provider_model_ref")
    op.drop_column("model_versions", "parent_model_version_id")
    op.drop_column("model_versions", "adapter_only")
    op.drop_column("model_versions", "lineage_completeness")
    op.drop_column("model_versions", "origin")
