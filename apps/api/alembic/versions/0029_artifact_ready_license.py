"""trainable_artifacts: `ready` requires an accepted license (FTF-0305-F1).

Defence in depth under the application-level row lock added to
`artifact_crud.py`: the database itself refuses a `ready` row whose
`license_accepted_at` is NULL, so no interleaving of `mark_ready` and
`revoke_license` (or a path that bypasses the CRUD module) can persist
that state.

The upgrade never deletes or rewrites data. If any existing row already
violates the new rule it raises `ArtifactReadyWithoutLicenseError`
naming the offending ids, and nothing is changed; an operator resolves
those rows deliberately before retrying. The downgrade drops only the
CHECK.

Revision ID: 0029_artifact_ready_license
Revises: 0028_model_import_state_machine
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0029_artifact_ready_license"
down_revision = "0028_model_import_state_machine"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_trainable_artifacts_ready_needs_license"


class ArtifactReadyWithoutLicenseError(RuntimeError):
    """Existing `ready` rows have no accepted license; refuse to migrate."""


def upgrade() -> None:
    bind = op.get_bind()
    offenders = (
        bind.execute(
            sa.text(
                "SELECT id FROM trainable_artifacts "
                "WHERE status = 'ready' AND license_accepted_at IS NULL "
                "ORDER BY id LIMIT 20"
            )
        )
        .scalars()
        .all()
    )
    if offenders:
        raise ArtifactReadyWithoutLicenseError(
            "refusing to add " + _CONSTRAINT + ": trainable_artifacts rows are 'ready' without an "
            "accepted license (first ids: " + ", ".join(str(i) for i in offenders) + "); "
            "resolve them deliberately -- this migration never rewrites or deletes data"
        )
    op.create_check_constraint(
        _CONSTRAINT,
        "trainable_artifacts",
        "status <> 'ready' OR license_accepted_at IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "trainable_artifacts", type_="check")
