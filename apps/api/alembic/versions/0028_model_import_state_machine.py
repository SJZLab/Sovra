"""Model import job state machine + origin/import_kind consistency
(FTF-H010-P1 round 2, Section 6.17/58A/68.1).

Two real, database-level guards 0027's own review found missing:

1. `model_import_jobs` had no DB-enforced *transition* guard: the three
   CHECK constraints in 0027 each only look at one row's own column
   values, never the previous row, so `sovra_app` could INSERT a job
   directly at `state='succeeded'`, or UPDATE `queued -> succeeded`
   (skipping `validating` entirely), or change a row's state again after
   it already reached a terminal state (`failed`/`succeeded`/
   `blocked_external`) -- none of that violated any existing CHECK. A
   `BEFORE INSERT OR UPDATE` trigger enforces the module's own documented
   flow exactly: every INSERT must land at `state='queued'`; from
   `queued` the only allowed move is to `validating`; from `validating`
   the only allowed moves are to `failed`/`succeeded`/`blocked_external`;
   once in a terminal state, no further change to `state` (or any other
   column -- the grant is column-limited to `state, failure_code,
   updated_at`, and all three are locked once the row is terminal) is
   accepted. A same-state update (e.g. refreshing `updated_at` without
   changing `state`) is allowed at any non-terminal state.

2. `model_versions` had no CHECK tying its two origin columns together:
   `origin` (FTF-H010-P1, a finer classification) and `import_kind`
   (FTF-1001's original, coarser one) could disagree -- an `endpoint`
   row (no local weights, by `ck_model_versions_kind_consistency`) could
   still claim `origin='factory_trained'` (not one of §68.1's own three
   import forms at all, and not a shape an endpoint row can structurally
   be), or an `adapter` row (artifact + digest + base revision) could
   claim `origin='imported_weights'` while `adapter_only=false`,
   bypassing `ck_model_versions_imported_adapter_consistency` entirely
   because that CHECK only fires when `origin='imported_adapter'`.
   `factory_trained` is allowed for `full_weight` and `adapter` rows
   (this repository has no way to distinguish an organically
   Factory-trained full-weight/adapter row from an externally imported
   one -- 0027's own docstring already discloses this for `full_weight`;
   the same reasoning applies to an adapter Factory trained itself), but
   never for `endpoint` (an endpoint is, by definition in §68.1, an
   external reference -- there is no "Factory-trained endpoint" shape).

Revision ID: 0028_model_import_state_machine
Revises: 0027_model_import
Create Date: 2026-10-09
"""

from __future__ import annotations

from alembic import op

revision = "0028_model_import_state_machine"
down_revision = "0027_model_import"
branch_labels = None
depends_on = None

_TRIGGER_FUNCTION = "model_import_jobs_state_transition_guard"
_TRIGGER_NAME = "model_import_jobs_state_transition_guard"


def upgrade() -> None:
    op.create_check_constraint(
        "ck_model_versions_origin_import_kind_consistency",
        "model_versions",
        "(import_kind = 'endpoint' AND origin = 'external_endpoint') OR "
        "(import_kind = 'adapter' AND origin IN ('imported_adapter', 'factory_trained') "
        "AND adapter_only) OR "
        "(import_kind = 'full_weight' AND origin IN ('imported_weights', 'factory_trained'))",
    )
    # §68.1 names exactly three import forms (endpoint/full-weight/adapter);
    # `factory_trained` is not one of them -- that is FTF-1001's own direct
    # creation path (`create_model_version`), never something *imported*.
    # Database-level defense in depth, mirroring the CRUD-layer refusal in
    # `model_import_crud.py`.
    op.create_check_constraint(
        "ck_model_import_jobs_origin_not_factory_trained",
        "model_import_jobs",
        "requested_origin <> 'factory_trained'",
    )

    op.execute(
        f"""
        CREATE FUNCTION {_TRIGGER_FUNCTION}() RETURNS trigger
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
    )
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER_NAME}
        BEFORE INSERT OR UPDATE ON model_import_jobs
        FOR EACH ROW EXECUTE FUNCTION {_TRIGGER_FUNCTION}()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON model_import_jobs")
    op.execute(f"DROP FUNCTION IF EXISTS {_TRIGGER_FUNCTION}()")
    op.drop_constraint(
        "ck_model_import_jobs_origin_not_factory_trained", "model_import_jobs", type_="check"
    )
    op.drop_constraint(
        "ck_model_versions_origin_import_kind_consistency", "model_versions", type_="check"
    )
