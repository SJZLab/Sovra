"""Argilla review scope, reviewer identity and workspace membership (FTF-R006-P1b,
Section 8-10, 65, 67, 77.3-77.6, 78.4, 81).

FTF-R006-P1a (`packages/integrations/argilla/client.py`) is a REST adapter with nothing of its
own to persist. This migration adds the three tables P1b's identity store
(`app/reviews/argilla_identity_store.py`) needs to make "one Argilla workspace per (org, project)"
and "one Argilla user per (org, Sovra principal), member only of the workspaces of projects where
the principal holds a DATASET_SOURCE_ROLES membership" real, durable facts rather than something
re-derived from the live Argilla server on every call:

- `argilla_review_scopes` -- one row per (organization, project) naming the real Argilla
  workspace id `ensure_workspace` returned. `component_binding_id` ties the scope to the
  `component_bindings` row (`component_key = 'argilla'`) whose `secret_reference` holds the
  provisioning ("owner") API key this task's own client calls are authenticated with.
- `argilla_reviewer_identities` -- one row per (organization, issuer, subject): the real identity
  pair every other identity table in this repository already uses (Section 65.3), not an
  invented `principal_id` -- `memberships`/`audit_events` have no such column either, and adding
  one here would be a new, narrower identity representation nothing else in the schema shares.
  `argilla_user_id` is UNIQUE (one Sovra identity maps to exactly one Argilla user);
  `argilla_role` is CHECKed to `'annotator'` only (Section 77.3: Factory-provisioned reviewers are
  never `owner`/`admin`); `credential_secret_ref` is CHECKed to the `openbao://` scheme
  `packages/litellm_client/secret_manager_integration.py` (FTF-0202) already resolves -- the
  per-user Argilla `api_key` the server returns at creation goes straight there and only the ref
  is ever stored in this table (Law 16/Section 65.4: no secret value in this database, ever).
  `status`/`revoked_at` mirror `memberships`' own `active`/`revoked` convention: a revoked
  identity is retained as history, never deleted, and is refused for any further use by the
  store module (never a silent no-op).
- `argilla_workspace_members` -- the join table recording that one reviewer identity is (or was)
  a member of one review scope's workspace, mirroring the real Argilla-side membership the store
  module's `add_workspace_user`/`remove_workspace_user` calls create/remove. Composite foreign
  keys to both parents additionally pin `organization_id` so a membership can never name a scope
  or identity belonging to a different organization even under a raw `sovra_app` INSERT.

All three: ENABLE + FORCE row-level security, the same `tenant_isolation` policy (keyed on
`app.current_organization_id`) every prior tenant table in this chain already uses, and
`sovra_app` granted SELECT/INSERT/UPDATE only -- never DELETE (Section 65.1; a revoke is a status
flip, same as `memberships`/`component_bindings`).

Revision ID: 0034_argilla_review_identity
Revises: 0033_upload_declared_encoding
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0034_argilla_review_identity"
down_revision = "0033_upload_declared_encoding"
branch_labels = None
depends_on = None

# Matches app/auth/organization_workspace_schema.py::APP_ROLE_NAME -- kept as a literal here
# (not imported), the same convention every prior migration in this chain already uses.
APP_ROLE_NAME = "sovra_app"

_ARGILLA_ROLES = ("annotator",)
_STATUSES = ("active", "revoked")
_UUID_SHAPE = "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"
_TABLES = ("argilla_workspace_members", "argilla_reviewer_identities", "argilla_review_scopes")


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def _now_col(name: str, *, nullable: bool = False) -> sa.Column:
    if nullable:
        return sa.Column(name, sa.DateTime(timezone=True), nullable=True)
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


_TENANT_SETTING = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "argilla_review_scopes",
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
                "organizations.id", ondelete="CASCADE", name="fk_argilla_review_scopes_org"
            ),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component_binding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("argilla_workspace_id", sa.String(36), nullable=False),
        sa.Column("argilla_workspace_name", sa.String(64), nullable=False),
        _now_col("created_at"),
        _now_col("updated_at"),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_argilla_review_scopes_org_project",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "component_binding_id"],
            ["component_bindings.organization_id", "component_bindings.id"],
            ondelete="RESTRICT",
            name="fk_argilla_review_scopes_org_binding",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_argilla_review_scopes_org_id"),
        sa.UniqueConstraint(
            "organization_id", "project_id", name="uq_argilla_review_scopes_org_project"
        ),
        sa.UniqueConstraint("argilla_workspace_id", name="uq_argilla_review_scopes_workspace_id"),
        sa.CheckConstraint(
            f"argilla_workspace_id ~* {_UUID_SHAPE}",
            name="ck_argilla_review_scopes_workspace_id_shape",
        ),
    )

    op.create_table(
        "argilla_reviewer_identities",
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
                "organizations.id", ondelete="CASCADE", name="fk_argilla_reviewer_identities_org"
            ),
            nullable=False,
        ),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("argilla_user_id", sa.String(36), nullable=False),
        sa.Column("argilla_role", sa.String(16), nullable=False, server_default="annotator"),
        sa.Column("credential_secret_ref", sa.String(512), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        _now_col("revoked_at", nullable=True),
        _now_col("created_at"),
        _now_col("updated_at"),
        sa.UniqueConstraint("organization_id", "id", name="uq_argilla_reviewer_identities_org_id"),
        sa.UniqueConstraint(
            "organization_id",
            "issuer",
            "subject",
            name="uq_argilla_reviewer_identities_org_identity",
        ),
        sa.UniqueConstraint(
            "argilla_user_id", name="uq_argilla_reviewer_identities_argilla_user_id"
        ),
        sa.CheckConstraint(
            f"argilla_role IN {_in(_ARGILLA_ROLES)}",
            name="ck_argilla_reviewer_identities_role",
        ),
        sa.CheckConstraint(
            f"status IN {_in(_STATUSES)}", name="ck_argilla_reviewer_identities_status"
        ),
        sa.CheckConstraint(
            "credential_secret_ref ~ '^openbao://'",
            name="ck_argilla_reviewer_identities_secret_ref_scheme",
        ),
        sa.CheckConstraint(
            f"argilla_user_id ~* {_UUID_SHAPE}",
            name="ck_argilla_reviewer_identities_user_id_shape",
        ),
        sa.CheckConstraint("issuer <> ''", name="ck_argilla_reviewer_identities_issuer_not_blank"),
        sa.CheckConstraint(
            "subject <> ''", name="ck_argilla_reviewer_identities_subject_not_blank"
        ),
        sa.CheckConstraint(
            "(status = 'revoked') = (revoked_at IS NOT NULL)",
            name="ck_argilla_reviewer_identities_revoked_at_consistent",
        ),
    )

    op.create_table(
        "argilla_workspace_members",
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
                "organizations.id", ondelete="CASCADE", name="fk_argilla_workspace_members_org"
            ),
            nullable=False,
        ),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        _now_col("created_at"),
        _now_col("updated_at"),
        sa.ForeignKeyConstraint(
            ["organization_id", "scope_id"],
            ["argilla_review_scopes.organization_id", "argilla_review_scopes.id"],
            ondelete="CASCADE",
            name="fk_argilla_workspace_members_org_scope",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "identity_id"],
            ["argilla_reviewer_identities.organization_id", "argilla_reviewer_identities.id"],
            ondelete="CASCADE",
            name="fk_argilla_workspace_members_org_identity",
        ),
        sa.UniqueConstraint(
            "scope_id", "identity_id", name="uq_argilla_workspace_members_scope_identity"
        ),
        sa.CheckConstraint(
            f"status IN {_in(_STATUSES)}", name="ck_argilla_workspace_members_status"
        ),
    )

    # Written out literally per table (not via a loop/f-string helper) so the static
    # datastore-exposure scanner (scripts/data_exposure_check.py), which greps this file's own
    # source text for "ALTER TABLE <name> ENABLE/FORCE ROW LEVEL SECURITY" /
    # "CREATE POLICY ... ON <name>" with the literal table name, can see each one directly --
    # the same convention every prior migration in this chain already follows.
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON argilla_review_scopes TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE argilla_review_scopes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE argilla_review_scopes FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON argilla_review_scopes FOR ALL "
        f"USING (organization_id = {_TENANT_SETTING}) "
        f"WITH CHECK (organization_id = {_TENANT_SETTING})"
    )

    op.execute(f'GRANT SELECT, INSERT, UPDATE ON argilla_reviewer_identities TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE argilla_reviewer_identities ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE argilla_reviewer_identities FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON argilla_reviewer_identities FOR ALL "
        f"USING (organization_id = {_TENANT_SETTING}) "
        f"WITH CHECK (organization_id = {_TENANT_SETTING})"
    )

    op.execute(f'GRANT SELECT, INSERT, UPDATE ON argilla_workspace_members TO "{APP_ROLE_NAME}"')
    op.execute("ALTER TABLE argilla_workspace_members ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE argilla_workspace_members FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON argilla_workspace_members FOR ALL "
        f"USING (organization_id = {_TENANT_SETTING}) "
        f"WITH CHECK (organization_id = {_TENANT_SETTING})"
    )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f'REVOKE ALL ON {table} FROM "{APP_ROLE_NAME}"')
    op.drop_table("argilla_workspace_members")
    op.drop_table("argilla_reviewer_identities")
    op.drop_table("argilla_review_scopes")
