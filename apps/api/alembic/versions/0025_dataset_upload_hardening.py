"""Dataset upload-session lifecycle hardening (FTF-0501-P2, prerequisite for
FTF-0501; Section 8.1 steps 1-4, 67.3, 67.5).

Migration 0024 (FTF-0501-P1) lets the store do the right thing but leaves
three things to raw `sovra_app` SQL that the database should refuse itself:

- moving a `rejected` or `expired` session back to `open` (the 0024 freeze
  trigger only froze `verified` rows), which would re-arm a finished upload;
- inserting a session directly as `verified` (or any non-`open` status), which
  skips the finalize step every verified session is supposed to pass through;
- forward-dating `created_at`, which stretches `expires_at` (a 24 hour bound
  from `created_at`) past 24 real hours.

This migration closes all three with triggers, for every role (the owner
included), so the database refuses them no matter which code path asks:

- `dataset_upload_sessions_freeze_verified()` is replaced: a `verified` row is
  still immutable (unchanged message), and now so is every other closed row
  (`rejected`, `expired`). The only status change the database accepts is
  therefore `open` -> `verified | rejected | expired`.
- `dataset_upload_sessions_insert_guard()` and a BEFORE INSERT trigger: a new
  session must have `status = 'open'` and a `created_at` within 60 seconds of
  `now()` in either direction.

No table, column or grant changes; the Row Level Security setup of 0024 is
untouched.

Revision ID: 0025_dataset_upload_hardening
Revises: 0024_dataset_uploads
Create Date: 2026-10-08
"""

from __future__ import annotations

from alembic import op

revision = "0025_dataset_upload_hardening"
down_revision = "0024_dataset_uploads"
branch_labels = None
depends_on = None

_FREEZE_FUNCTION_0024 = (
    "CREATE OR REPLACE FUNCTION dataset_upload_sessions_freeze_verified() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF OLD.status = 'verified' THEN "
    "RAISE EXCEPTION 'a verified upload session is immutable' "
    "USING ERRCODE = 'check_violation', "
    "CONSTRAINT = 'tr_dataset_upload_sessions_freeze_verified'; "
    "END IF; RETURN NEW; END $$"
)

_FREEZE_FUNCTION_0025 = (
    "CREATE OR REPLACE FUNCTION dataset_upload_sessions_freeze_verified() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF OLD.status = 'verified' THEN "
    "RAISE EXCEPTION 'a verified upload session is immutable' "
    "USING ERRCODE = 'check_violation', "
    "CONSTRAINT = 'tr_dataset_upload_sessions_freeze_verified'; "
    "END IF; "
    "IF OLD.status <> 'open' THEN "
    "RAISE EXCEPTION 'a closed upload session cannot be changed or reopened' "
    "USING ERRCODE = 'check_violation', "
    "CONSTRAINT = 'tr_dataset_upload_sessions_closed_is_final'; "
    "END IF; "
    "RETURN NEW; END $$"
)

_INSERT_GUARD_FUNCTION = (
    "CREATE FUNCTION dataset_upload_sessions_insert_guard() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF NEW.status <> 'open' THEN "
    "RAISE EXCEPTION 'a new upload session must be open' "
    "USING ERRCODE = 'check_violation', "
    "CONSTRAINT = 'tr_dataset_upload_sessions_insert_guard_status'; "
    "END IF; "
    "IF NEW.created_at < now() - interval '60 seconds' "
    "OR NEW.created_at > now() + interval '60 seconds' THEN "
    "RAISE EXCEPTION 'created_at must be within 60 seconds of the database clock' "
    "USING ERRCODE = 'check_violation', "
    "CONSTRAINT = 'tr_dataset_upload_sessions_insert_guard_created_at'; "
    "END IF; "
    "RETURN NEW; END $$"
)


def upgrade() -> None:
    op.execute(_FREEZE_FUNCTION_0025)
    op.execute(_INSERT_GUARD_FUNCTION)
    op.execute(
        "CREATE TRIGGER tr_dataset_upload_sessions_insert_guard "
        "BEFORE INSERT ON dataset_upload_sessions "
        "FOR EACH ROW EXECUTE FUNCTION dataset_upload_sessions_insert_guard()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS tr_dataset_upload_sessions_insert_guard "
        "ON dataset_upload_sessions"
    )
    op.execute("DROP FUNCTION IF EXISTS dataset_upload_sessions_insert_guard()")
    op.execute(_FREEZE_FUNCTION_0024)
