"""Cross-layer tenant isolation: RLS-safe workspace directory view (FTF-H003).

Section 65.1's row-level security is enforced on the *base* tables
(`organizations`/`workspaces`, FTF-0102) via `FORCE ROW LEVEL SECURITY`.
A SQL view built on top of a FORCE-RLS table is a distinct, easy-to-get-
wrong enforcement boundary: by default, Postgres evaluates a view with
the *view owner's* privileges (the migration role, which owns every table
and is therefore exempt from RLS as owner), not the querying role's --
so an ordinary `CREATE VIEW` here would silently hand `sovra_app` every
tenant's rows regardless of its own RLS context, even though the base
tables themselves are correctly protected. This is exactly this task's
own negative_acceptance clause ("test views... not API guards alone").

`security_invoker = true` (the real Postgres 15+ view option) fixes this:
it makes the view evaluate with the *querying* role's own privileges and
RLS policies, the same as if `sovra_app` had queried the base tables
directly. Reproduced directly against a real local Postgres 16 before
writing this migration: an otherwise-identical view without
`security_invoker = true` returns every organization's workspace rows to
`sovra_app` with zero tenant context set; the same view with it set
returns zero rows, matching the base tables' own RLS behavior exactly
(see tests/integration/test_cross_layer_tenant_isolation.py, which
reproduces this as an automated, fault-injectable proof).

Revision ID: 0003_cross_layer_isolation
Revises: 0002_audit_event_writer
Create Date: 2026-09-20

`0003_cross_layer_isolation`, not the fuller `..._tenant_isolation` name
this task's title suggests: `alembic_version.version_num` is
`VARCHAR(32)` (Alembic's own default column width), and the fuller name
is 33 characters -- reproduced directly against a real Postgres 16
(`StringDataRightTruncation`) before picking this shorter revision ID.
"""

from __future__ import annotations

from alembic import op

revision = "0003_cross_layer_isolation"
down_revision = "0002_audit_event_writer"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as
# a literal here (not imported) so this migration stays self-contained, same
# convention as 0001_org_workspace_schema.py/0002_audit_event_writer.py.
APP_ROLE_NAME = "sovra_app"


def upgrade() -> None:
    op.execute(
        "CREATE VIEW workspace_directory AS "
        "SELECT w.id, w.organization_id, w.name, w.slug, w.status, "
        "o.name AS organization_name "
        "FROM workspaces w JOIN organizations o ON o.id = w.organization_id"
    )
    # The one line that actually closes the RLS-bypass-through-views gap
    # this migration's own docstring reproduces above -- without it, this
    # view would silently leak every tenant's rows to sovra_app.
    op.execute("ALTER VIEW workspace_directory SET (security_invoker = true)")
    op.execute(f'GRANT SELECT ON workspace_directory TO "{APP_ROLE_NAME}"')


def downgrade() -> None:
    op.execute(f'REVOKE SELECT ON workspace_directory FROM "{APP_ROLE_NAME}"')
    op.execute("DROP VIEW IF EXISTS workspace_directory")
