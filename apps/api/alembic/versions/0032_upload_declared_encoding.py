"""Upload sessions declare the text encoding finalize decodes with (FTF-0501-F2,
Section 8.1 steps 3-4; FTF-0503/0504 `encoding` in utf-8, utf-8-sig, utf-16-le, utf-16-be,
windows-1256).

`dataset_upload_sessions` gains `declared_encoding varchar(16) NOT NULL DEFAULT 'utf-8'`, held
to the five supported encodings by a CHECK. The column is set at insert and never updated:
`sovra_app` keeps its INSERT grant and the column-limited UPDATE grant of 0024 does not name
it, so the declaration cannot change after the session opens. No table, policy or grant change;
the Row Level Security setup of 0024 is untouched. Existing rows keep `utf-8`, which is what
finalize enforced before.
"""

import sqlalchemy as sa

from alembic import op

revision = "0032_upload_declared_encoding"
down_revision = "0031_model_import_validation"
branch_labels = None
depends_on = None

_CHECK = "ck_dataset_upload_sessions_declared_encoding"


def upgrade() -> None:
    op.add_column(
        "dataset_upload_sessions",
        sa.Column("declared_encoding", sa.String(16), nullable=False, server_default="utf-8"),
    )
    op.create_check_constraint(
        _CHECK,
        "dataset_upload_sessions",
        "declared_encoding IN ('utf-8', 'utf-8-sig', 'utf-16-le', 'utf-16-be', 'windows-1256')",
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK, "dataset_upload_sessions", type_="check")
    op.drop_column("dataset_upload_sessions", "declared_encoding")
