"""ComponentBinding + ComponentSyncOutbox schema (FTF-R002).

Creates `component_bindings` and `component_sync_outbox` (Section
39/65/70/77.1/77.3/77.5/77.6). `component_bindings`'s own column names and
enums match FTF-R001's already-CERTIFIED, frozen
`packages/component-contracts/architecture.schema.json` contract literally
(`component_key`, `security_zone_scope`, `project_scope`,
`deployment_mode`, `approved_endpoint`, `secret_reference`,
`network_policy_identity`, `health_result`, `compatibility_lock_reference`)
-- see `app/component_bindings/component_binding_schema.py`'s own
docstring for the full field-by-field mapping and for the real, additive
columns beyond that frozen abstract shape (`id`/`organization_id`/
`health_checked_at`/`health_detail`/`row_version`/`created_at`/
`updated_at`).

FORCEs row-level security on both tables scoped to the same
`app.current_organization_id` tenant context every prior migration in
this chain already established, and grants the restricted `sovra_app`
role SELECT/INSERT/UPDATE on both -- never DELETE. A component binding is
superseded by a new `row_version` (optimistic concurrency, the same
discipline `0005_projects.py`'s own `row_version` already established for
`projects`), never removed; a sync outbox row transitions `pending` ->
`succeeded`/`failed` in place and is never removed either (Section 70.1's
own durable reconciliation record).

`ck_component_bindings_worker_package_no_endpoint`/
`_service_requires_endpoint` are real, database-level enforcement of
Section 77.5's "Worker packages have no invented API URL" --
`_worker_components_are_worker_package`/`_litellm_is_existing_gateway` are
the frozen contract's own two `allOf`/`if`/`then` structural invariants
(Axolotl/Inspect AI must be `worker_package`; LiteLLM must be
`existing_gateway`), all real at the database level -- never only an
application-level check a bypassing raw INSERT could evade (see
`app/component_bindings/component_binding_schema.py`'s own docstring).

`component_sync_outbox.component_binding_id` carries a real composite
foreign key to `component_bindings(organization_id, id)` (via this
migration's own `UNIQUE (organization_id, id)` constraint on
`component_bindings`) -- the same Section 65.1 "composite
(organization_id, id) keys where the parent is tenant-owned" precedent
`target_source_snapshots`'s own migration already applied.

Revision ID: 0013_component_bindings
Revises: 0012_target_source_snapshots
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013_component_bindings"
down_revision = "0012_target_source_snapshots"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported), same convention as every prior migration.
APP_ROLE_NAME = "sovra_app"

_ALLOWED_COMPONENT_KEYS = (
    "axolotl",
    "inspect_ai",
    "mlflow",
    "vllm",
    "temporal",
    "litellm",
    "argilla",
)
_ALLOWED_DEPLOYMENT_MODES = ("internal_service", "worker_package", "existing_gateway")
_ALLOWED_HEALTH_RESULTS = ("healthy", "unhealthy", "unknown")
_ALLOWED_SYNC_OUTBOX_STATUSES = ("pending", "succeeded", "failed")


def _in_clause(values: tuple[str, ...]) -> str:
    return "in (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "component_bindings",
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
                "organizations.id",
                ondelete="CASCADE",
                name="fk_component_bindings_organization_id",
            ),
            nullable=False,
        ),
        sa.Column("component_key", sa.String(32), nullable=False),
        sa.Column("security_zone_scope", sa.String(100), nullable=False),
        sa.Column("project_scope", sa.String(200), nullable=True),
        sa.Column("deployment_mode", sa.String(32), nullable=False),
        sa.Column("approved_endpoint", sa.String(2048), nullable=True),
        sa.Column("secret_reference", sa.String(512), nullable=True),
        sa.Column("network_policy_identity", sa.String(200), nullable=False),
        sa.Column("health_result", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("health_detail", sa.String(200), nullable=True),
        sa.Column("compatibility_lock_reference", sa.String(200), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
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
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_component_bindings_organization_id_id"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "component_key",
            name="uq_component_bindings_organization_id_component_key",
        ),
        sa.CheckConstraint(
            "component_key " + _in_clause(_ALLOWED_COMPONENT_KEYS),
            name="ck_component_bindings_component_key",
        ),
        sa.CheckConstraint(
            "deployment_mode " + _in_clause(_ALLOWED_DEPLOYMENT_MODES),
            name="ck_component_bindings_deployment_mode",
        ),
        sa.CheckConstraint(
            "health_result " + _in_clause(_ALLOWED_HEALTH_RESULTS),
            name="ck_component_bindings_health_result",
        ),
        sa.CheckConstraint(
            "deployment_mode != 'worker_package' OR approved_endpoint IS NULL",
            name="ck_component_bindings_worker_package_no_endpoint",
        ),
        sa.CheckConstraint(
            "deployment_mode = 'worker_package' OR approved_endpoint IS NOT NULL",
            name="ck_component_bindings_service_requires_endpoint",
        ),
        sa.CheckConstraint(
            "component_key not in ('axolotl', 'inspect_ai') OR deployment_mode = 'worker_package'",
            name="ck_component_bindings_worker_components_are_worker_package",
        ),
        sa.CheckConstraint(
            "component_key != 'litellm' OR deployment_mode = 'existing_gateway'",
            name="ck_component_bindings_litellm_is_existing_gateway",
        ),
    )
    op.create_index(
        "ix_component_bindings_organization_id", "component_bindings", ["organization_id"]
    )

    op.create_table(
        "component_sync_outbox",
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
                "organizations.id",
                ondelete="CASCADE",
                name="fk_component_sync_outbox_organization_id",
            ),
            nullable=False,
        ),
        sa.Column("component_binding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("factory_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("external_id", sa.String(512), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(500), nullable=True),
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
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "organization_id",
            "component_binding_id",
            "factory_job_id",
            name="uq_component_sync_outbox_org_binding_job",
        ),
        sa.CheckConstraint(
            "status " + _in_clause(_ALLOWED_SYNC_OUTBOX_STATUSES),
            name="ck_component_sync_outbox_status",
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (resolved_at IS NULL)",
            name="ck_component_sync_outbox_resolved_pairs_with_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "component_binding_id"],
            ["component_bindings.organization_id", "component_bindings.id"],
            ondelete="RESTRICT",
            name="fk_component_sync_outbox_organization_id_binding_id",
        ),
    )
    op.create_index(
        "ix_component_sync_outbox_organization_id", "component_sync_outbox", ["organization_id"]
    )
    op.create_index(
        "ix_component_sync_outbox_binding_id", "component_sync_outbox", ["component_binding_id"]
    )

    for table in ("component_bindings", "component_sync_outbox"):
        op.execute(f'GRANT SELECT, INSERT, UPDATE ON {table} TO "{APP_ROLE_NAME}"')
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            f"USING (organization_id = {tenant_setting}) "
            f"WITH CHECK (organization_id = {tenant_setting})"
        )


def downgrade() -> None:
    for table in ("component_sync_outbox", "component_bindings"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f'REVOKE ALL ON {table} FROM "{APP_ROLE_NAME}"')

    op.drop_index("ix_component_sync_outbox_binding_id", table_name="component_sync_outbox")
    op.drop_index("ix_component_sync_outbox_organization_id", table_name="component_sync_outbox")
    op.drop_table("component_sync_outbox")

    op.drop_index("ix_component_bindings_organization_id", table_name="component_bindings")
    op.drop_table("component_bindings")
