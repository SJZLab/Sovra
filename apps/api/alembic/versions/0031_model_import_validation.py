"""Model import validation activity: job fencing, leases, endpoint-only import
(FTF-H010-P2, Section 3.3-3.5, 6.17, 17 fencing paragraph, 41/77.6 activity rules,
58A row 1, 68.1 paragraphs 1-2).

`model_import_jobs` gains the columns a claim/validate activity needs:

- `litellm_connection_id`: same-tenant composite FK to `litellm_connections`
  (organization_id, id), RESTRICT. An `external_endpoint` job names the connection it is
  validated through; no other origin has one (`ck_..._connection_matches_origin`).
- `claim_epoch int NOT NULL DEFAULT 0` and `lease_expires_at timestamptz`: the fence. Every
  claim (and every re-claim after expiry) increments the epoch; only the current epoch may
  write a terminal state (the activity's conditional UPDATE), and the trigger below pins the
  transition rules in the database.
- `result_model_version_id`: the ModelVersion a succeeded job produced. The FK is
  (organization_id, project_id, result_model_version_id, requested_origin) ->
  `model_versions (organization_id, project_id, id, origin)`, so a result belongs to the same
  project and has the requested origin at database level (a new UQ on `model_versions`).

The column-limited UPDATE grant for `sovra_app` widens from (state, failure_code, updated_at) to
(state, failure_code, updated_at, result_model_version_id, claim_epoch, lease_expires_at) and
nothing else: `requested_origin`, `litellm_connection_id` and the identity columns stay
immutable after insert.

The 0028 transition trigger function is replaced (CREATE OR REPLACE, every 0028 rule kept):

- INSERT only at `queued` with epoch 0, no lease and no result;
- `queued -> validating` requires `claim_epoch = OLD + 1` (and a lease, by CHECK);
- `validating -> validating` is allowed only as (a) a lease renewal by the holder (same epoch,
  lease strictly later, nothing else but `updated_at` changed) or (b) a re-claim with
  `claim_epoch = OLD + 1`, and only when `OLD.lease_expires_at < now()`;
- `validating -> terminal` requires `NEW.claim_epoch = OLD.claim_epoch` (a late commit that
  carries another epoch is refused) and clears the lease (by CHECK);
- a terminal state stays final.

Control-plane ruling 2026-10-10T03:57Z (route 3): an alias is not a URL, so
`ck_model_versions_kind_consistency` is replaced and an `endpoint` row needs EITHER a non-NULL
`endpoint_reference` (the certified FTF-1001 form) OR `endpoint_reference IS NULL AND origin =
'external_endpoint' AND external_provider_model_ref IS NOT NULL`. The downgrade restores the
0009 definition byte-identically and refuses (never deletes data) while any NULL-reference row
exists.

Disclosed: `ck_model_import_jobs_connection_matches_origin` is added `NOT VALID`. A queued
`external_endpoint` job inserted by FTF-H010-P1 (before this column existed) cannot name a
connection and no migration can invent one, so the constraint is enforced on every new or
updated row and not re-checked against those legacy rows; the claim refuses such a row
(`job_invalid`) instead of validating it.

Revision ID: 0031_model_import_validation
Revises: 0030_dataset_split_manifests
Create Date: 2026-10-10

Renumbered from 0029 to 0030 (2026-10-13): this migration and 0029_artifact_ready_license
were each independently claimed off 0028_model_import_state_machine by different relay
sessions working concurrently, forking the Alembic chain into two heads once both merged
to main -- "Multiple head revisions are present for given argument 'head'", failing every
integration job that runs `alembic upgrade head`. Per QUEUE.md's own serialization rule
("take the next free number at claim time... renumber if another lands first"),
0029_artifact_ready_license landed on main first, so this migration is renumbered to the
next free slot and rechained onto it. No schema change: the two migrations touch disjoint
tables (trainable_artifacts vs. model_import_jobs).

Renumbered again, 0030 to 0031 (2026-10-20): the same fork recurred -- 0030_dataset_split_
manifests (FTF-0519-P1) was independently claimed off 0029_artifact_ready_license while
this migration sat as an open draft PR, forking the chain at 0030 once both existed on
their own branches. 0030_dataset_split_manifests landed on main first, so this migration
is renumbered again to the next free slot and rechained onto it. Still no schema change:
0030_dataset_split_manifests touches disjoint tables (dataset_split_manifests and its three
child tables, plus a new unique constraint on dataset_quality_reports) from this
migration's own model_import_jobs/model_versions. The real round trip from head now passes
through two unrelated migrations (0030_dataset_split_manifests, then 0029_artifact_ready_
license) before reaching 0028's state, so the task and integration tests' own full-rollback
assertions move from `downgrade -2` to `downgrade -3` -- see test_ftf_h010_p2.py and
test_ftf_h010_p2_import_validation.py.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0031_model_import_validation"
down_revision = "0030_dataset_split_manifests"
branch_labels = None
depends_on = None

APP_ROLE_NAME = "sovra_app"

_TRIGGER_FUNCTION = "model_import_jobs_state_transition_guard"
_OLD_JOB_UPDATE_COLUMNS = "state, failure_code, updated_at"
_NEW_JOB_UPDATE_COLUMNS = (
    "state, failure_code, updated_at, result_model_version_id, claim_epoch, lease_expires_at"
)

# The 0009 definition, restored byte-identically on downgrade.
_KIND_CONSISTENCY_0009 = (
    "(import_kind = 'endpoint' AND trainable_artifact_id IS NULL "
    "AND weights_digest IS NULL AND base_model_revision IS NULL "
    "AND endpoint_reference IS NOT NULL) "
    "OR "
    "(import_kind in ('full_weight','adapter') AND trainable_artifact_id IS NOT NULL "
    "AND weights_digest IS NOT NULL AND base_model_revision IS NOT NULL "
    "AND endpoint_reference IS NULL)"
)
_KIND_CONSISTENCY_0029 = (
    "(import_kind = 'endpoint' AND trainable_artifact_id IS NULL "
    "AND weights_digest IS NULL AND base_model_revision IS NULL "
    "AND (endpoint_reference IS NOT NULL "
    "OR (endpoint_reference IS NULL AND origin = 'external_endpoint' "
    "AND external_provider_model_ref IS NOT NULL))) "
    "OR "
    "(import_kind in ('full_weight','adapter') AND trainable_artifact_id IS NOT NULL "
    "AND weights_digest IS NOT NULL AND base_model_revision IS NOT NULL "
    "AND endpoint_reference IS NULL)"
)

# The 0028 function body, restored byte-identically on downgrade.
_FUNCTION_0028 = """
        CREATE OR REPLACE FUNCTION model_import_jobs_state_transition_guard() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.state <> 'queued' THEN
                    RAISE EXCEPTION
                        'model_import_jobs_state_transition: a new job must be inserted at '
                        'state=queued, not %', NEW.state
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.state IN ('failed', 'succeeded', 'blocked_external') THEN
                RAISE EXCEPTION
                    'model_import_jobs_state_transition: % is a terminal state, no further '
                    'change is allowed', OLD.state
                    USING ERRCODE = 'check_violation';
            END IF;

            IF NEW.state = OLD.state THEN
                RETURN NEW;
            END IF;

            IF OLD.state = 'queued' AND NEW.state = 'validating' THEN
                RETURN NEW;
            END IF;

            IF OLD.state = 'validating'
               AND NEW.state IN ('failed', 'succeeded', 'blocked_external') THEN
                RETURN NEW;
            END IF;

            RAISE EXCEPTION
                'model_import_jobs_state_transition: % -> % is not an allowed state '
                'transition', OLD.state, NEW.state
                USING ERRCODE = 'check_violation';
        END;
        $$;
        """

_FUNCTION_0029 = """
        CREATE OR REPLACE FUNCTION model_import_jobs_state_transition_guard() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.state <> 'queued' THEN
                    RAISE EXCEPTION
                        'model_import_jobs_state_transition: a new job must be inserted at '
                        'state=queued, not %', NEW.state
                        USING ERRCODE = 'check_violation';
                END IF;
                IF NEW.claim_epoch <> 0
                   OR NEW.lease_expires_at IS NOT NULL
                   OR NEW.result_model_version_id IS NOT NULL THEN
                    RAISE EXCEPTION
                        'model_import_jobs_state_transition: a new job must carry epoch 0, '
                        'no lease and no result'
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.state IN ('failed', 'succeeded', 'blocked_external') THEN
                RAISE EXCEPTION
                    'model_import_jobs_state_transition: % is a terminal state, no further '
                    'change is allowed', OLD.state
                    USING ERRCODE = 'check_violation';
            END IF;

            IF OLD.state = 'queued' AND NEW.state = 'queued' THEN
                RETURN NEW;
            END IF;

            IF OLD.state = 'queued' AND NEW.state = 'validating' THEN
                IF NEW.claim_epoch <> OLD.claim_epoch + 1 THEN
                    RAISE EXCEPTION
                        'model_import_jobs_state_transition: a claim must set claim_epoch to '
                        'the previous epoch + 1'
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.state = 'validating' AND NEW.state = 'validating' THEN
                IF NEW.claim_epoch = OLD.claim_epoch THEN
                    IF NEW.lease_expires_at > OLD.lease_expires_at
                       AND NEW.failure_code IS NOT DISTINCT FROM OLD.failure_code
                       AND NEW.result_model_version_id IS NOT DISTINCT FROM
                           OLD.result_model_version_id THEN
                        RETURN NEW;
                    END IF;
                    RAISE EXCEPTION
                        'model_import_jobs_state_transition: a lease renewal must extend the '
                        'lease and change nothing else'
                        USING ERRCODE = 'check_violation';
                END IF;
                IF NEW.claim_epoch = OLD.claim_epoch + 1 AND OLD.lease_expires_at < now() THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION
                    'model_import_jobs_state_transition: a re-claim needs epoch + 1 and an '
                    'expired lease'
                    USING ERRCODE = 'check_violation';
            END IF;

            IF OLD.state = 'validating'
               AND NEW.state IN ('failed', 'succeeded', 'blocked_external') THEN
                IF NEW.claim_epoch <> OLD.claim_epoch THEN
                    RAISE EXCEPTION
                        'model_import_jobs_state_transition: a terminal write must carry the '
                        'current claim_epoch'
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;

            RAISE EXCEPTION
                'model_import_jobs_state_transition: % -> % is not an allowed state '
                'transition', OLD.state, NEW.state
                USING ERRCODE = 'check_violation';
        END;
        $$;
        """


def upgrade() -> None:
    op.add_column(
        "model_import_jobs",
        sa.Column("litellm_connection_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "model_import_jobs",
        sa.Column("result_model_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "model_import_jobs",
        sa.Column("claim_epoch", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "model_import_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_unique_constraint(
        "uq_model_versions_organization_id_project_id_id_origin",
        "model_versions",
        ["organization_id", "project_id", "id", "origin"],
    )
    op.create_foreign_key(
        "fk_model_import_jobs_organization_id_litellm_connection_id",
        "model_import_jobs",
        "litellm_connections",
        ["organization_id", "litellm_connection_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_model_import_jobs_result_model_version",
        "model_import_jobs",
        "model_versions",
        ["organization_id", "project_id", "result_model_version_id", "requested_origin"],
        ["organization_id", "project_id", "id", "origin"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_model_import_jobs_claim_epoch_non_negative", "model_import_jobs", "claim_epoch >= 0"
    )
    op.create_check_constraint(
        "ck_model_import_jobs_succeeded_has_result",
        "model_import_jobs",
        "(state = 'succeeded') = (result_model_version_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_model_import_jobs_validating_has_lease",
        "model_import_jobs",
        "(state = 'validating') = (lease_expires_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_model_import_jobs_queued_epoch_zero",
        "model_import_jobs",
        "(state = 'queued') = (claim_epoch = 0)",
    )
    # NOT VALID: see the module docstring (legacy queued external_endpoint rows).
    op.execute(
        "ALTER TABLE model_import_jobs ADD CONSTRAINT "
        "ck_model_import_jobs_connection_matches_origin CHECK "
        "((requested_origin = 'external_endpoint') = (litellm_connection_id IS NOT NULL)) "
        "NOT VALID"
    )

    op.execute(f'REVOKE UPDATE ON model_import_jobs FROM "{APP_ROLE_NAME}"')
    op.execute(
        f'GRANT UPDATE ({_NEW_JOB_UPDATE_COLUMNS}) ON model_import_jobs TO "{APP_ROLE_NAME}"'
    )
    op.execute(_FUNCTION_0029)

    op.drop_constraint("ck_model_versions_kind_consistency", "model_versions", type_="check")
    op.create_check_constraint(
        "ck_model_versions_kind_consistency", "model_versions", _KIND_CONSISTENCY_0029
    )


def downgrade() -> None:
    bind = op.get_bind()
    null_reference_rows = bind.execute(
        sa.text(
            "SELECT count(*) FROM model_versions WHERE import_kind = 'endpoint' "
            "AND endpoint_reference IS NULL"
        )
    ).scalar_one()
    if null_reference_rows:
        raise RuntimeError(
            "cannot downgrade 0031: endpoint model_versions rows with a NULL endpoint_reference "
            "exist and the 0009 constraint cannot hold them (no data is deleted)"
        )
    op.drop_constraint("ck_model_versions_kind_consistency", "model_versions", type_="check")
    op.create_check_constraint(
        "ck_model_versions_kind_consistency", "model_versions", _KIND_CONSISTENCY_0009
    )

    op.execute(_FUNCTION_0028)
    op.execute(f'REVOKE UPDATE ON model_import_jobs FROM "{APP_ROLE_NAME}"')
    op.execute(
        f'GRANT UPDATE ({_OLD_JOB_UPDATE_COLUMNS}) ON model_import_jobs TO "{APP_ROLE_NAME}"'
    )

    op.drop_constraint(
        "ck_model_import_jobs_connection_matches_origin", "model_import_jobs", type_="check"
    )
    op.drop_constraint("ck_model_import_jobs_queued_epoch_zero", "model_import_jobs", type_="check")
    op.drop_constraint(
        "ck_model_import_jobs_validating_has_lease", "model_import_jobs", type_="check"
    )
    op.drop_constraint(
        "ck_model_import_jobs_succeeded_has_result", "model_import_jobs", type_="check"
    )
    op.drop_constraint(
        "ck_model_import_jobs_claim_epoch_non_negative", "model_import_jobs", type_="check"
    )
    op.drop_constraint(
        "fk_model_import_jobs_result_model_version", "model_import_jobs", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_model_import_jobs_organization_id_litellm_connection_id",
        "model_import_jobs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_model_versions_organization_id_project_id_id_origin", "model_versions", type_="unique"
    )
    op.drop_column("model_import_jobs", "lease_expires_at")
    op.drop_column("model_import_jobs", "claim_epoch")
    op.drop_column("model_import_jobs", "result_model_version_id")
    op.drop_column("model_import_jobs", "litellm_connection_id")
