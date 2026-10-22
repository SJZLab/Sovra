"""Narrow the Argilla review-identity grants and make a revoked reviewer identity a real
terminal state at the database level (FTF-R006-P1b round 2; Control-plane L2 review of PR #953,
tracked as PR #968: "the table-wide UPDATE grant lets a revoked identity be restored").

0034 granted blanket `UPDATE` on all three tables to `sovra_app` -- wider than any column
`app/reviews/argilla_identity_store.py` ever writes, and wide enough that a raw `sovra_app`
UPDATE (not through that module at all) could flip a `revoked` identity back to `active`,
silently undoing `revoke_reviewer_identity`'s own one-way guarantee (Law 18: secure by default,
every access-control surface tested). This migration:

- Narrows `argilla_review_scopes` to `SELECT, INSERT` only -- nothing in the store module ever
  updates a scope row after it is created.
- Narrows `argilla_reviewer_identities` to `UPDATE (status, revoked_at, updated_at)` -- the only
  columns `revoke_reviewer_identity` ever writes; `issuer`/`subject`/`argilla_user_id`/
  `credential_secret_ref`/`argilla_role` can never be changed by a raw `sovra_app` UPDATE.
- Narrows `argilla_workspace_members` to `UPDATE (status, updated_at)` -- the only columns
  `sync_workspace_membership`/`revoke_reviewer_identity` ever write; `scope_id`/`identity_id` can
  never be repointed at a different scope or identity by a raw `sovra_app` UPDATE.
- Adds a `BEFORE UPDATE` trigger on `argilla_reviewer_identities` (the same "terminal state, no
  further change is allowed" convention `0028_model_import_state_machine.py`'s own job-state
  trigger already uses for `model_import_jobs`) that refuses ANY update to a row whose current
  `status` is already `'revoked'` -- even within the now-narrowed column set, a raw `sovra_app`
  UPDATE can never move a revoked identity back to `active`. The application's own
  `revoke_reviewer_identity` never hits this trigger: its own final UPDATE already carries
  `AND status <> 'revoked'`, so a legitimate idempotent re-revoke affects zero rows rather than
  reaching the trigger at all (see that function's own comment).

Revision ID: 0035_argilla_identity_hardening
Revises: 0034_argilla_review_identity
Create Date: 2026-10-22
"""

from __future__ import annotations

from alembic import op

revision = "0035_argilla_identity_hardening"
down_revision = "0034_argilla_review_identity"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as a literal here
# (not imported), the same convention every prior migration in this chain already uses.
APP_ROLE_NAME = "sovra_app"
_TRIGGER_FUNCTION = "argilla_reviewer_identities_revoked_is_terminal"
_TRIGGER_NAME = "trg_argilla_reviewer_identities_revoked_is_terminal"


def upgrade() -> None:
    op.execute(f'REVOKE UPDATE ON argilla_review_scopes FROM "{APP_ROLE_NAME}"')

    op.execute(f'REVOKE UPDATE ON argilla_reviewer_identities FROM "{APP_ROLE_NAME}"')
    op.execute(
        "GRANT UPDATE (status, revoked_at, updated_at) ON argilla_reviewer_identities "
        f'TO "{APP_ROLE_NAME}"'
    )

    op.execute(f'REVOKE UPDATE ON argilla_workspace_members FROM "{APP_ROLE_NAME}"')
    op.execute(
        f'GRANT UPDATE (status, updated_at) ON argilla_workspace_members TO "{APP_ROLE_NAME}"'
    )

    op.execute(
        f"""
        CREATE FUNCTION {_TRIGGER_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.status = 'revoked' THEN
                RAISE EXCEPTION
                    'argilla_reviewer_identities_revoked_is_terminal: identity % is revoked, '
                    'no further change is allowed', OLD.id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER_NAME}
        BEFORE UPDATE ON argilla_reviewer_identities
        FOR EACH ROW EXECUTE FUNCTION {_TRIGGER_FUNCTION}()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON argilla_reviewer_identities")
    op.execute(f"DROP FUNCTION IF EXISTS {_TRIGGER_FUNCTION}()")

    op.execute(f'REVOKE UPDATE ON argilla_workspace_members FROM "{APP_ROLE_NAME}"')
    op.execute(f'GRANT UPDATE ON argilla_workspace_members TO "{APP_ROLE_NAME}"')

    op.execute(f'REVOKE UPDATE ON argilla_reviewer_identities FROM "{APP_ROLE_NAME}"')
    op.execute(f'GRANT UPDATE ON argilla_reviewer_identities TO "{APP_ROLE_NAME}"')

    op.execute(f'GRANT UPDATE ON argilla_review_scopes TO "{APP_ROLE_NAME}"')
