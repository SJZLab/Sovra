"""Fix ck_evaluation_suites_reviewer_present_when_locked to reject '' too (FTF-0601 L2 finding).

0017_evaluation_suites's own `ck_evaluation_suites_reviewer_present_when_locked`
CHECK only tested `reviewer_id IS NOT NULL` -- a real gap, since an
empty-string `reviewer_id` satisfies `IS NOT NULL` and so a `PATCH
{"reviewer_id": ""}` followed by `lock` genuinely locked a `final` suite
with no real reviewer identity, contrary to Section 69.1's own
"independent reviewer" requirement this same constraint pair exists to
enforce. Drops and recreates the constraint with the real fix
(`reviewer_id IS NOT NULL AND reviewer_id <> ''`); application-level
validation (`app/evaluation/evaluation_suite_schema.py::
validate_reviewer_id_or_none`, now called from `update_evaluation_suite`
too, not only at creation) already refuses an empty *or whitespace-only*
string before a write is ever attempted, so this database-level backstop
only needs to additionally catch the literal empty-string case no
application-level bypass (a raw write, a future caller that skips the
validator) could otherwise evade.

Revision ID: 0018_eval_suites_reviewer_fix
Revises: 0017_evaluation_suites
Create Date: 2026-10-01
"""

from __future__ import annotations

from alembic import op

revision = "0018_eval_suites_reviewer_fix"
down_revision = "0017_evaluation_suites"
branch_labels = None
depends_on = None

_OLD_CONDITION = "status <> 'locked' OR reviewer_id IS NOT NULL"
_NEW_CONDITION = "status <> 'locked' OR (reviewer_id IS NOT NULL AND reviewer_id <> '')"


def upgrade() -> None:
    op.drop_constraint(
        "ck_evaluation_suites_reviewer_present_when_locked",
        "evaluation_suites",
        type_="check",
    )
    op.create_check_constraint(
        "ck_evaluation_suites_reviewer_present_when_locked",
        "evaluation_suites",
        _NEW_CONDITION,
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
        _OLD_CONDITION,
    )
