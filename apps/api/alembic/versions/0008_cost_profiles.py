"""Cost profiles (FTF-1301).

Creates the `cost_profiles` table (Section 28/31/32/45/64/69-70/77/81's
own "provider/local hardware TCO rates/currency/source" framing of this
task's acceptance clause 1), FORCEs row-level security scoped to the same
`app.current_organization_id` tenant context FTF-0102/FTF-0104/FTF-0201/
FTF-0301/FTF-0401/FTF-0403/FTF-0404's migrations already established, and
grants the restricted `sovra_app` role SELECT/INSERT/UPDATE only --
deliberately never DELETE, matching FTF-0403/FTF-0404's own precedent: a
dated rate observation is a real, sourced fact this task's own
negative_acceptance depends on never silently disappearing (an
organization cannot make a past "unverified"/"expired" disqualification
vanish by deleting the row it was computed from).

Every row is immutable once created except `verified_at`/`row_version`/
`updated_at` (see `cost_profile_schema.py`'s own module docstring) -- no
`ON UPDATE CASCADE` semantics or version-chaining columns are needed here,
unlike FTF-0404's own `ToolDefinition`, since a new dated observation is
always a brand new row, never a revision of an old one.

Revision ID: 0008_cost_profiles
Revises: 0007_budget_policies
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008_cost_profiles"
down_revision = "0007_budget_policies"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as every prior migration in this chain.
APP_ROLE_NAME = "sovra_app"

# Matches app/costs/cost_profile_schema.py::PROFILE_TYPES/RATE_UNITS --
# kept as literals here for the same self-contained-migration reason.
_PROFILE_TYPES = ("provider", "local_hardware")
_RATE_UNITS = (
    "per_input_token",
    "per_output_token",
    "per_gpu_hour",
    "per_cpu_hour",
    "per_ram_gb_hour",
    "per_hour",
    "per_request",
)


def upgrade() -> None:
    op.create_table(
        "cost_profiles",
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
                "organizations.id", ondelete="CASCADE", name="fk_cost_profiles_organization_id"
            ),
            nullable=False,
        ),
        sa.Column("profile_type", sa.String(32), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("rate_unit", sa.String(32), nullable=False),
        sa.Column("rate_amount", sa.Numeric(24, 10), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("source", sa.String(2048), nullable=False),
        sa.Column("rate_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
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
        sa.CheckConstraint(
            f"profile_type in {_PROFILE_TYPES!r}", name="ck_cost_profiles_profile_type"
        ),
        sa.CheckConstraint(f"rate_unit in {_RATE_UNITS!r}", name="ck_cost_profiles_rate_unit"),
        sa.CheckConstraint("rate_amount >= 0", name="ck_cost_profiles_rate_amount_non_negative"),
        sa.CheckConstraint("row_version >= 1", name="ck_cost_profiles_row_version_positive"),
    )
    op.create_index("ix_cost_profiles_organization_id", "cost_profiles", ["organization_id"])
    op.create_index(
        "ix_cost_profiles_organization_id_profile_type_rate_unit",
        "cost_profiles",
        ["organization_id", "profile_type", "rate_unit"],
    )
    op.create_unique_constraint(
        "uq_cost_profiles_organization_id_id", "cost_profiles", ["organization_id", "id"]
    )

    # Section 65.1: same FORCE ROW LEVEL SECURITY discipline as every other
    # tenant-scoped table in this repository.
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON cost_profiles TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE cost_profiles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE cost_profiles FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_isolation ON cost_profiles FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON cost_profiles")
    op.execute(f'REVOKE ALL ON cost_profiles FROM "{APP_ROLE_NAME}"')
    op.drop_constraint("uq_cost_profiles_organization_id_id", "cost_profiles", type_="unique")
    op.drop_index(
        "ix_cost_profiles_organization_id_profile_type_rate_unit", table_name="cost_profiles"
    )
    op.drop_index("ix_cost_profiles_organization_id", table_name="cost_profiles")
    op.drop_table("cost_profiles")
