"""Make an unfinished Argilla revocation visible and re-drivable (FTF-R006-P2, the two
FTF-R006-P1b certifier advisories of 2026-11-28T04:00Z; Section 65, 78.4, 80.3; Law 17).

1. `argilla_workspace_members.status` gains `removal_pending`: a membership whose real
   Argilla-side removal has been started but has not been confirmed. `sync_workspace_membership`
   writes it BEFORE it calls `remove_workspace_user` in its revoke-race undo path, and only moves
   it to `revoked` after the real removal returned. An outage in between therefore leaves the row
   `removal_pending` (never a `revoked` row next to a real, live membership), and
   `revoke_reviewer_identity` re-drives every `active` or `removal_pending` row until it succeeds.
2. `argilla_reviewer_identities.argilla_user_deleted_at`: the moment the real Argilla user (and with
   it the user's API key) was deleted. Argilla v2.8.0 has no API-key rotation endpoint, so deleting
   the user is the only way to make the key stop working. A revoked identity with this column NULL
   is "revocation pending": its key may still be valid on the server, and a retry of
   `revoke_reviewer_identity` deletes it. The terminal-state trigger from 0036 is replaced by one
   that still refuses every change to a revoked row EXCEPT setting this column once, from NULL to a
   time; `sovra_app` gets UPDATE on this one extra column only.

Revision ID: 0041_argilla_revoke_completion
Revises: 0040_training_experiments
Create Date: 2026-11-28
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0041_argilla_revoke_completion"
down_revision = "0040_training_experiments"
branch_labels = None
depends_on = None

APP_ROLE_NAME = "sovra_app"
_TRIGGER_FUNCTION = "argilla_reviewer_identities_revoked_is_terminal"
_MEMBER_STATUS_CHECK = "ck_argilla_workspace_members_status"
_DELETED_CHECK = "ck_argilla_reviewer_identities_deleted_only_when_revoked"


def _terminal_function(allow_deletion_stamp: bool) -> str:
    if allow_deletion_stamp:
        body = (
            "IF OLD.status = 'revoked' THEN "
            "IF OLD.argilla_user_deleted_at IS NULL AND NEW.argilla_user_deleted_at IS NOT NULL "
            "AND (to_jsonb(NEW) - 'argilla_user_deleted_at' - 'updated_at') "
            "= (to_jsonb(OLD) - 'argilla_user_deleted_at' - 'updated_at') THEN RETURN NEW; END IF; "
            "RAISE EXCEPTION 'argilla_reviewer_identities_revoked_is_terminal: identity % is "
            "revoked, no further change is allowed', OLD.id USING ERRCODE = 'check_violation'; "
            "END IF; RETURN NEW;"
        )
    else:
        body = (
            "IF OLD.status = 'revoked' THEN "
            "RAISE EXCEPTION 'argilla_reviewer_identities_revoked_is_terminal: identity % is "
            "revoked, no further change is allowed', OLD.id USING ERRCODE = 'check_violation'; "
            "END IF; RETURN NEW;"
        )
    return (
        f"CREATE OR REPLACE FUNCTION {_TRIGGER_FUNCTION}() RETURNS trigger "
        f"LANGUAGE plpgsql AS $$ BEGIN {body} END; $$"
    )


def upgrade() -> None:
    op.drop_constraint(_MEMBER_STATUS_CHECK, "argilla_workspace_members", type_="check")
    op.create_check_constraint(
        _MEMBER_STATUS_CHECK,
        "argilla_workspace_members",
        "status IN ('active', 'removal_pending', 'revoked')",
    )
    op.add_column(
        "argilla_reviewer_identities",
        sa.Column("argilla_user_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        _DELETED_CHECK,
        "argilla_reviewer_identities",
        "argilla_user_deleted_at IS NULL OR status = 'revoked'",
    )
    op.execute(
        "GRANT UPDATE (argilla_user_deleted_at) ON argilla_reviewer_identities "
        f'TO "{APP_ROLE_NAME}"'
    )
    op.execute(_terminal_function(allow_deletion_stamp=True))


def downgrade() -> None:
    op.execute(_terminal_function(allow_deletion_stamp=False))
    op.execute(
        "REVOKE UPDATE (argilla_user_deleted_at) ON argilla_reviewer_identities "
        f'FROM "{APP_ROLE_NAME}"'
    )
    op.drop_constraint(_DELETED_CHECK, "argilla_reviewer_identities", type_="check")
    op.drop_column("argilla_reviewer_identities", "argilla_user_deleted_at")
    op.execute(
        "UPDATE argilla_workspace_members SET status = 'active' WHERE status = 'removal_pending'"
    )
    op.drop_constraint(_MEMBER_STATUS_CHECK, "argilla_workspace_members", type_="check")
    op.create_check_constraint(
        _MEMBER_STATUS_CHECK, "argilla_workspace_members", "status IN ('active', 'revoked')"
    )
