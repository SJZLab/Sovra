"""Model role assignments (FTF-H010-P4; Section 0.12 roles, Section 6.17, Section 17 paragraphs 3-4,
Section 27 judge != candidate, Section 58A "Allowed next operations", Section 68.1 paragraph 4).

A ModelVersion is immutable (0009), so a role -- current_production, baseline, candidate, teacher or
judge -- cannot live on it. One table, ENABLE + FORCE row-level security with the same
`tenant_isolation` policy every prior migration uses (Section 65.1), and no DELETE grant:

- `model_role_assignments` -- one row per (version, role) assignment. The version identity is held
  true by a composite foreign key `(organization_id, project_id, model_version_id)` to
  `model_versions` (backed by an additive `UNIQUE (organization_id, project_id, id)` on it).
- A role is revoked, never edited or deleted: `sovra_app` may UPDATE only `(revoked_at, revoked_by,
  revoke_reason_code)`, and a BEFORE UPDATE trigger, for every role, accepts nothing but the
  one-time NULL -> set revocation with every other column unchanged (no un-revoke).
- Partial unique indexes: one active row per (project, version, role), and one active
  `current_production` per project (the incumbent). `(organization_id, project_id,
  idempotency_key)` is unique so a retried POST replays instead of assigning twice.
- CHECKs: the role is one of the five (exact, case sensitive); `assigned_by` is non-blank; the
  idempotency key is 1..128 printable ASCII characters; `request_sha256` is 64 lowercase hex; the
  three revocation columns are all NULL or all set.
- A BEFORE INSERT trigger (serialized per version with an advisory transaction lock, so two
  concurrent transactions cannot both pass) refuses a row that is inserted already revoked, a role
  on a version that is not validated (no succeeded import job and not factory trained), and
  `judge` on a version that holds an active `candidate` role, and the reverse (Section 27).

Nothing here deploys or routes anything: `current_production` records the incumbent only.

Revision ID: 0037_model_role_assignments
Revises: 0036_argilla_identity_hardening
Create Date: 2026-10-23
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0037_model_role_assignments"
down_revision = "0036_argilla_identity_hardening"
branch_labels = None
depends_on = None

APP_ROLE_NAME = "sovra_app"

_ROLES = ("current_production", "baseline", "candidate", "teacher", "judge")
_UPDATABLE = ("revoked_at", "revoked_by", "revoke_reason_code")
_HEX64 = "'^[0-9a-f]{64}$'"


def _in(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


def _raise(message: str, constraint: str) -> str:
    return (
        f"RAISE EXCEPTION '{message}' USING ERRCODE = 'check_violation', "
        f"CONSTRAINT = '{constraint}'; "
    )


_INSERT_FUNCTION = (
    "CREATE FUNCTION model_role_assignments_insert_guard() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ "
    "DECLARE version_origin text; BEGIN "
    "IF NEW.revoked_at IS NOT NULL THEN "
    + _raise("a role is assigned active", "tr_model_role_assignments_insert_guard_active")
    + "END IF; "
    "PERFORM pg_advisory_xact_lock(hashtextextended(NEW.model_version_id::text, 36)); "
    "SELECT v.origin INTO version_origin FROM model_versions v "
    "WHERE v.id = NEW.model_version_id AND v.organization_id = NEW.organization_id "
    "AND v.project_id = NEW.project_id; "
    "IF version_origin IS DISTINCT FROM 'factory_trained' AND NOT EXISTS ("
    "SELECT 1 FROM model_import_jobs j WHERE j.organization_id = NEW.organization_id "
    "AND j.project_id = NEW.project_id AND j.result_model_version_id = NEW.model_version_id "
    "AND j.state = 'succeeded') THEN "
    + _raise(
        "a role needs a validated model version",
        "tr_model_role_assignments_insert_guard_validated",
    )
    + "END IF; "
    "IF (NEW.role = 'judge' AND EXISTS (SELECT 1 FROM model_role_assignments r "
    "WHERE r.organization_id = NEW.organization_id AND r.project_id = NEW.project_id "
    "AND r.model_version_id = NEW.model_version_id AND r.role = 'candidate' "
    "AND r.revoked_at IS NULL)) "
    "OR (NEW.role = 'candidate' AND EXISTS (SELECT 1 FROM model_role_assignments r "
    "WHERE r.organization_id = NEW.organization_id AND r.project_id = NEW.project_id "
    "AND r.model_version_id = NEW.model_version_id AND r.role = 'judge' "
    "AND r.revoked_at IS NULL)) THEN "
    + _raise(
        "a model version is never both judge and candidate",
        "tr_model_role_assignments_self_judging",
    )
    + "END IF; RETURN NEW; END $$"
)
_FROZEN = " - ".join(f"'{c}'" for c in _UPDATABLE)
_UPDATE_FUNCTION = (
    "CREATE FUNCTION model_role_assignments_update_guard() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF OLD.revoked_at IS NOT NULL THEN "
    + _raise("a revoked role never changes", "tr_model_role_assignments_update_guard_final")
    + "END IF; "
    "IF NEW.revoked_at IS NULL THEN "
    + _raise(
        "a role assignment can only be revoked",
        "tr_model_role_assignments_update_guard_transition",
    )
    + "END IF; "
    f"IF (to_jsonb(NEW) - {_FROZEN}) IS DISTINCT FROM (to_jsonb(OLD) - {_FROZEN}) THEN "
    + _raise(
        "a role assignment is immutable except its revocation",
        "tr_model_role_assignments_update_guard_frozen",
    )
    + "END IF; "
    "RETURN NEW; END $$"
)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_model_versions_organization_id_project_id_id",
        "model_versions",
        ["organization_id", "project_id", "id"],
    )

    op.create_table(
        "model_role_assignments",
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
                "organizations.id", ondelete="CASCADE", name="fk_model_role_assignments_org"
            ),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("assigned_by", sa.String(200), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(200), nullable=True),
        sa.Column("revoke_reason_code", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id", "model_version_id"],
            [
                "model_versions.organization_id",
                "model_versions.project_id",
                "model_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_model_role_assignments_model_version",
        ),
        sa.CheckConstraint(f"role IN {_in(_ROLES)}", name="ck_model_role_assignments_role"),
        sa.CheckConstraint(
            "assigned_by !~ '^\\s*$' AND request_id !~ '^\\s*$'",
            name="ck_model_role_assignments_text_not_blank",
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[ -~]{1,128}$'", name="ck_model_role_assignments_idempotency_key"
        ),
        sa.CheckConstraint(
            f"request_sha256 ~ {_HEX64}", name="ck_model_role_assignments_request_sha256"
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_by IS NULL) "
            "AND (revoked_at IS NULL) = (revoke_reason_code IS NULL)",
            name="ck_model_role_assignments_revocation_columns",
        ),
        sa.CheckConstraint(
            "(revoked_by IS NULL OR revoked_by !~ '^\\s*$') "
            "AND (revoke_reason_code IS NULL OR revoke_reason_code ~ '^[a-z][a-z0-9_]{0,63}$')",
            name="ck_model_role_assignments_revocation_text",
        ),
    )
    op.create_index(
        "ix_model_role_assignments_organization_id", "model_role_assignments", ["organization_id"]
    )
    op.create_index(
        "ix_model_role_assignments_model_version_id", "model_role_assignments", ["model_version_id"]
    )
    op.create_index(
        "uq_model_role_assignments_active",
        "model_role_assignments",
        ["project_id", "model_version_id", "role"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "uq_model_role_assignments_incumbent",
        "model_role_assignments",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("role = 'current_production' AND revoked_at IS NULL"),
    )
    op.create_index(
        "uq_model_role_assignments_idempotency",
        "model_role_assignments",
        ["organization_id", "project_id", "idempotency_key"],
        unique=True,
    )

    op.execute(f'GRANT SELECT, INSERT ON model_role_assignments TO "{APP_ROLE_NAME}"')
    op.execute(
        f'GRANT UPDATE ({", ".join(_UPDATABLE)}) ON model_role_assignments TO "{APP_ROLE_NAME}"'
    )

    op.execute(_INSERT_FUNCTION)
    op.execute(
        "CREATE TRIGGER tr_model_role_assignments_insert_guard BEFORE INSERT "
        "ON model_role_assignments FOR EACH ROW "
        "EXECUTE FUNCTION model_role_assignments_insert_guard()"
    )
    op.execute(_UPDATE_FUNCTION)
    op.execute(
        "CREATE TRIGGER tr_model_role_assignments_update_guard BEFORE UPDATE "
        "ON model_role_assignments FOR EACH ROW "
        "EXECUTE FUNCTION model_role_assignments_update_guard()"
    )

    tenant_setting = "NULLIF(current_setting('app.current_organization_id', true), '')::uuid"
    op.execute("ALTER TABLE model_role_assignments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE model_role_assignments FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON model_role_assignments FOR ALL "
        f"USING (organization_id = {tenant_setting}) "
        f"WITH CHECK (organization_id = {tenant_setting})"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS tr_model_role_assignments_update_guard ON model_role_assignments"
    )
    op.execute("DROP FUNCTION IF EXISTS model_role_assignments_update_guard()")
    op.execute(
        "DROP TRIGGER IF EXISTS tr_model_role_assignments_insert_guard ON model_role_assignments"
    )
    op.execute("DROP FUNCTION IF EXISTS model_role_assignments_insert_guard()")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON model_role_assignments")
    op.execute(f'REVOKE ALL ON model_role_assignments FROM "{APP_ROLE_NAME}"')
    op.drop_table("model_role_assignments")
    op.drop_constraint(
        "uq_model_versions_organization_id_project_id_id", "model_versions", type_="unique"
    )
