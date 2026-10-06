"""Normalize evaluation_suites reviewer CHECK constraints to strip+casefold
and trim-aware comparisons (FTF-0601 L2 round-2 finding).

0017's own `ck_evaluation_suites_independent_final_reviewer` compared
`reviewer_id = created_by` with a bare `=` -- a padded (`" creator-1"`) or
case-variant (`"CREATOR-1"`) `reviewer_id` did not match the real
`created_by` value under that comparison, so a final suite could be locked
with an effectively self-reviewed `reviewer_id` the application layer's own
`normalize_identity` (strip+casefold -- `app/evaluation/
evaluation_suite_schema.py`) now correctly refuses. 0018's own
`ck_evaluation_suites_reviewer_present_when_locked` fix closed the bare
empty-string gap (`''`) but not a non-empty, whitespace-only one (`'   '`)
written by a raw INSERT that bypasses `validate_reviewer_id_or_none`.

## Data-remediation step (not a silent schema change)

Before either CHECK is dropped and recreated with the new, stricter
condition, `upgrade()` queries for any existing row that would already
violate it and refuses to proceed (raises `RuntimeError` naming the exact
row IDs) rather than silently applying a stricter constraint against data
an operator has not reviewed, or silently mutating a durable evaluation
record (locking/reviewer assignment) on their behalf. This is deliberate:
unlocking a `final` suite or reassigning its `reviewer_id` are both
business decisions with real downstream consequences (Section 69.1
independence; any evaluation run already bound to this suite), not a
data-hygiene cleanup this migration can safely perform on its own.
Operator runbook if this migration ever refuses: for each named row,
either assign a genuinely independent `reviewer_id` (if the lock predates
this fix and the independence violation is real) or revert the row to
`status='draft'` via `UPDATE evaluation_suites SET status='draft',
locked_at=NULL WHERE id = ...` before re-running `alembic upgrade head`.

Revision ID: 0019_eval_suites_reviewer_norm
Revises: 0018_eval_suites_reviewer_fix
Create Date: 2026-10-05
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "0019_eval_suites_reviewer_norm"
down_revision = "0018_eval_suites_reviewer_fix"
branch_labels = None
depends_on = None

_OLD_INDEPENDENCE_CONDITION = (
    "NOT (purpose = 'final' AND status = 'locked' AND reviewer_id = created_by)"
)
_NEW_INDEPENDENCE_CONDITION = (
    "NOT (purpose = 'final' AND status = 'locked' "
    "AND lower(trim(reviewer_id)) = lower(trim(created_by)))"
)

_OLD_REVIEWER_PRESENT_CONDITION = (
    "status <> 'locked' OR (reviewer_id IS NOT NULL AND reviewer_id <> '')"
)
_NEW_REVIEWER_PRESENT_CONDITION = (
    "status <> 'locked' OR (reviewer_id IS NOT NULL AND trim(reviewer_id) <> '')"
)


def upgrade() -> None:
    bind = op.get_bind()

    independence_violations = bind.execute(
        text(
            """
            SELECT id FROM evaluation_suites
            WHERE status = 'locked'
              AND purpose = 'final'
              AND lower(trim(reviewer_id)) = lower(trim(created_by))
            """
        )
    ).fetchall()
    if independence_violations:
        ids = ", ".join(str(row[0]) for row in independence_violations)
        raise RuntimeError(
            "0019_eval_suites_reviewer_normalization: data remediation required before this "
            f"migration can proceed -- locked, purpose='final' evaluation_suites rows [{ids}] "
            "have a reviewer_id that normalizes (strip+casefold) to the same identity as "
            "created_by, violating Section 69.1's independence rule under the new, normalized "
            "CHECK. Assign a genuinely independent reviewer_id for each row, or revert it to "
            "status='draft', before re-running this migration -- this step deliberately does "
            "not auto-mutate production evaluation records (see this file's own docstring)."
        )

    empty_reviewer_violations = bind.execute(
        text(
            """
            SELECT id FROM evaluation_suites
            WHERE status = 'locked'
              AND (reviewer_id IS NULL OR trim(reviewer_id) = '')
            """
        )
    ).fetchall()
    if empty_reviewer_violations:
        ids = ", ".join(str(row[0]) for row in empty_reviewer_violations)
        raise RuntimeError(
            "0019_eval_suites_reviewer_normalization: data remediation required before this "
            f"migration can proceed -- locked evaluation_suites rows [{ids}] have a null or "
            "whitespace-only reviewer_id, violating Section 69.1's 'a real reviewer must be "
            "assigned before lock' requirement under the new, trim-aware CHECK. Assign a real "
            "reviewer_id for each row, or revert it to status='draft', before re-running this "
            "migration."
        )

    op.drop_constraint(
        "ck_evaluation_suites_independent_final_reviewer",
        "evaluation_suites",
        type_="check",
    )
    op.create_check_constraint(
        "ck_evaluation_suites_independent_final_reviewer",
        "evaluation_suites",
        _NEW_INDEPENDENCE_CONDITION,
    )

    op.drop_constraint(
        "ck_evaluation_suites_reviewer_present_when_locked",
        "evaluation_suites",
        type_="check",
    )
    op.create_check_constraint(
        "ck_evaluation_suites_reviewer_present_when_locked",
        "evaluation_suites",
        _NEW_REVIEWER_PRESENT_CONDITION,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_evaluation_suites_reviewer_present_when_locked",
        "evaluation_suites",
        type_="check",
    )
    op.create_check_constraint(
        "ck_evaluation_suites_reviewer_present_when_locked",
        "evaluation_suites",
        _OLD_REVIEWER_PRESENT_CONDITION,
    )

    op.drop_constraint(
        "ck_evaluation_suites_independent_final_reviewer",
        "evaluation_suites",
        type_="check",
    )
    op.create_check_constraint(
        "ck_evaluation_suites_independent_final_reviewer",
        "evaluation_suites",
        _OLD_INDEPENDENCE_CONDITION,
    )
