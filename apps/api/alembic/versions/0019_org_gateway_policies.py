"""Organization gateway policy persistence (FTF-1206A, Section 3/6/38/65/66/71/77/81).

Migration prerequisite for FTF-1206's own persistence half (ADR
`docs/adr/0002-task-addendum-ftf-1206a.md`; contract `spec/addenda/
FTF-1206A.json`). `packages/litellm_client/organization_policy_control.py`'s
own `OrganizationGatewayPolicy`/`ProjectGatewayPermissionRequest`/
`ProjectGatewayGrant` types and `enforce_project_gateway_permission_request`/
`enforce_route_change_authority` functions are the real, already-certified
*decision* procedures over this shape -- this migration adds the real
*storage* for it, which that task's own `database_changes` clause explicitly
deferred to "a separately claimed migration prerequisite".

Two tables:

- `organization_gateway_policies` -- one real row per organization (`UNIQUE
  (organization_id)`), the organization's own current, mutable statement of
  which Factory-owned namespaces a project may register/deploy into and who
  may approve a route change (Section 38.1/38.5-38.6). `UPDATE` is real and
  needed here (an organization's own policy legitimately changes over time);
  `DELETE` is never granted -- an organization's gateway policy is reset via
  `UPDATE`, never removed outright, the same "no legitimate deletion path"
  precedent `agent_manifests`/`evaluation_suites` already established for a
  durable governance record.
- `organization_gateway_policies_namespaces/approvers` are not separate
  tables -- `allowed_registration_namespaces`/`allowed_deployment_namespaces`/
  `approvers` are each a real `jsonb` array column (`CHECK
  (jsonb_typeof(...) = 'array')`), matching `OrganizationGatewayPolicy`'s own
  three `frozenset[str]` fields exactly: an organization's policy is a single
  atomically-updated row, not three independently-racing child tables.
- `organization_gateway_route_changes` -- an append-only record of every
  real production route/session-affinity change decision (Section 38.5-
  38.6), one row per attempt (`approved` real and `bypass_requested` both
  recorded, never only the successful ones) -- `sovra_app` is granted
  `SELECT`/`INSERT` only, never `UPDATE`/`DELETE` (this task's own
  negative_acceptance: "the app role cannot DELETE or UPDATE route-change
  history"), the identical append-only discipline `audit_events`
  (`0002_audit_event_writer.py`) already established at the database level,
  not only by application code choosing not to issue those statements.

Both tables FORCE row-level security scoped to the same
`app.current_organization_id` tenant context every prior migration in this
chain already established (Section 65.1) -- a foreign-tenant
`organization_id` is denied identically to a missing one, this task's own
negative_acceptance "Cross-tenant read or write is denied".
`organization_gateway_route_changes` additionally carries a real composite
foreign key `(organization_id, project_id)` -> `projects(organization_id,
id)` (reusing `0006_tool_definitions.py`'s own already-added
`uq_projects_organization_id_id`), so a route-change record can never be
written against a project that does not genuinely belong to the same
organization.

Revision ID: 0019_org_gateway_policies
Revises: 0018_eval_suites_reviewer_fix
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0019_org_gateway_policies"
down_revision = "0018_eval_suites_reviewer_fix"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as every prior migration in this chain.
APP_ROLE_NAME = "sovra_app"


def upgrade() -> None:
    op.create_table(
        "organization_gateway_policies",
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
                name="fk_organization_gateway_policies_organization_id",
            ),
            nullable=False,
        ),
        sa.Column(
            "allowed_registration_namespaces",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "allowed_deployment_namespaces",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "approvers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "route_change_requires_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
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
            "organization_id", name="uq_organization_gateway_policies_organization_id"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(allowed_registration_namespaces) = 'array'",
            name="ck_org_gateway_policies_registration_namespaces_is_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(allowed_deployment_namespaces) = 'array'",
            name="ck_org_gateway_policies_deployment_namespaces_is_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(approvers) = 'array'",
            name="ck_org_gateway_policies_approvers_is_array",
        ),
    )
    op.create_index(
        "ix_organization_gateway_policies_organization_id",
        "organization_gateway_policies",
        ["organization_id"],
    )

    op.create_table(
        "organization_gateway_route_changes",
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
                name="fk_organization_gateway_route_changes_organization_id",
            ),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("target_deployment_namespace", sa.String(128), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column(
            "bypass_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="RESTRICT",
            name="fk_org_gateway_route_changes_organization_id_project_id",
        ),
        sa.CheckConstraint("actor <> ''", name="ck_org_gateway_route_changes_actor_not_blank"),
        sa.CheckConstraint(
            "target_deployment_namespace <> ''",
            name="ck_org_gateway_route_changes_namespace_not_blank",
        ),
    )
    op.create_index(
        "ix_org_gateway_route_changes_organization_id",
        "organization_gateway_route_changes",
        ["organization_id"],
    )
    op.create_index(
        "ix_org_gateway_route_changes_project_id",
        "organization_gateway_route_changes",
        ["project_id"],
    )

    # Section 65.1: same FORCE ROW LEVEL SECURITY discipline as every other
    # tenant-scoped table in this repository. The policy table genuinely
    # needs UPDATE (an organization's own policy legitimately changes);
    # the route-change table never grants UPDATE/DELETE -- append-only,
    # enforced by the database itself (this task's own negative_acceptance).
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON organization_gateway_policies " f'TO "{APP_ROLE_NAME}"'
    )
    op.execute("ALTER TABLE organization_gateway_policies ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organization_gateway_policies FORCE ROW LEVEL SECURITY")

    op.execute(
        "GRANT SELECT, INSERT ON organization_gateway_route_changes " f'TO "{APP_ROLE_NAME}"'
    )
    op.execute("ALTER TABLE organization_gateway_route_changes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organization_gateway_route_changes FORCE ROW LEVEL SECURITY")

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    for table in ("organization_gateway_policies", "organization_gateway_route_changes"):
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            f"USING (organization_id = {tenant_setting}) "
            f"WITH CHECK (organization_id = {tenant_setting})"
        )


def downgrade() -> None:
    for table in ("organization_gateway_route_changes", "organization_gateway_policies"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f'REVOKE ALL ON {table} FROM "{APP_ROLE_NAME}"')

    op.drop_index(
        "ix_org_gateway_route_changes_project_id", table_name="organization_gateway_route_changes"
    )
    op.drop_index(
        "ix_org_gateway_route_changes_organization_id",
        table_name="organization_gateway_route_changes",
    )
    op.drop_table("organization_gateway_route_changes")

    op.drop_index(
        "ix_organization_gateway_policies_organization_id",
        table_name="organization_gateway_policies",
    )
    op.drop_table("organization_gateway_policies")
