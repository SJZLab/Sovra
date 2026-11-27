#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,method-assign,misc,func-returns-value"
"""FTF-0701-P2 negative-test proof: `training_experiments` persistence, no database or network.

M  migration 0039, statically: revision chain, every constraint, the held-out consistency CHECK,
   the BEFORE INSERT guard, SELECT+INSERT-only grant, ENABLE + FORCE row-level security and the
   `tenant_isolation` policy; each protection removed from a copy of the source turns the
   findings non-empty (fault -> RED). The offline `alembic upgrade --sql` rendering is pinned too.
S  `app.training.training_experiment_store` over SQLite with the fact sources replaced by
   stand-ins (the real FTF-0307 classifier and the real pure module stay in): authorization,
   request values, idempotency, every fact mapping, version assignment, constraint mapping, the
   audit row, reads, paging and the read integrity check.

The real PostgreSQL 16 proof (RLS, grants, triggers, concurrency, certified fixtures) is
`tests/integration/test_ftf_0701_p2_training_experiments.py`.

Run: `uv run python3 tests/tasks/test_ftf_0701_p2.py`
"""

# ruff: noqa: E501
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from workers.training import training_config_schema as schema  # noqa: E402

from app.artifacts.artifact_crud import ArtifactNotFoundError  # noqa: E402
from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.budget.budget_policy_crud import BudgetPolicyNotFoundError  # noqa: E402
from app.datasets import dataset_split_manifest_store as split_store  # noqa: E402
from app.db.tenant_context import TenantContext  # noqa: E402
from app.litellm.model_catalog_mapping_store import (  # noqa: E402
    MappingChangedError,
    ModelCatalogEntryNotFoundError,
)
from app.training import training_experiment_store as store  # noqa: E402

CID = "FTF-0701-P2"
FAILURES: list[str] = []
CHECKS = [0]
MIGRATION = ROOT / "apps" / "api" / "alembic" / "versions" / "0039_training_experiments.py"
TABLE = "training_experiments"
CANARY = "CANARY0701P2"


def check(criterion_id: str, description: str, condition: bool) -> None:
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(f"FAIL [{CID} {criterion_id}] {description}")


def _p1_valid_body() -> dict[str, Any]:
    path = ROOT / "tests" / "tasks" / "test_ftf_0701_p1.py"
    spec = importlib.util.spec_from_file_location("ftf_0701_p1_body_for_p2", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    saved = os.environ.get("FTF_0701_P1_NO_MAIN")
    os.environ["FTF_0701_P1_NO_MAIN"] = "1"
    try:
        spec.loader.exec_module(module)
    finally:
        if saved is None:
            os.environ.pop("FTF_0701_P1_NO_MAIN", None)
        else:
            os.environ["FTF_0701_P1_NO_MAIN"] = saved
    body: dict[str, Any] = module.valid_body()
    return body


BASE_BODY = _p1_valid_body()

# ---------------------------------------------------------------------------------------------
# M: the migration
# ---------------------------------------------------------------------------------------------

CONSTRAINTS = (
    "fk_training_experiments_organization_id",
    "fk_training_experiments_project",
    "fk_training_experiments_artifact",
    "fk_training_experiments_catalog",
    "fk_training_experiments_manifest",
    "fk_training_experiments_budget",
    "uq_training_experiments_org_id_id",
    "uq_training_experiments_project_name_version",
    "uq_training_experiments_idempotency",
    "ck_training_experiments_name",
    "ck_training_experiments_version",
    "ck_training_experiments_parent_version",
    "ck_training_experiments_status",
    "ck_training_experiments_method",
    "ck_training_experiments_schema_version",
    "ck_training_experiments_config_sha256",
    "ck_training_experiments_frozen_sha256",
    "ck_training_experiments_request_sha256",
    "ck_training_experiments_max_cost",
    "ck_training_experiments_currency",
    "ck_training_experiments_security_profile",
    "ck_training_experiments_requested_by",
    "ck_training_experiments_request_id",
    "ck_training_experiments_idempotency_key",
    "ck_training_experiments_config_consistency",
)
# exact source fragments the migration must contain (label -> needle)
MIGRATION_NEEDLES: dict[str, str] = {
    "revision": 'revision = "0039_training_experiments"',
    "down_revision": 'down_revision = "0038_dataset_mapping_requests"',
    "table literal": 'op.create_table(\n        "training_experiments",',
    "enable RLS": '"ALTER TABLE training_experiments ENABLE ROW LEVEL SECURITY"',
    "force RLS": '"ALTER TABLE training_experiments FORCE ROW LEVEL SECURITY"',
    "policy": '"CREATE POLICY tenant_isolation ON training_experiments FOR ALL "',
    "policy USING": 'f"USING (organization_id = {tenant_setting}) "',
    "policy WITH CHECK": 'f"WITH CHECK (organization_id = {tenant_setting})"',
    "tenant setting": (
        "tenant_setting = \"NULLIF(current_setting('app.current_organization_id', true), '')::uuid\""
    ),
    "grant": "op.execute(f'GRANT SELECT, INSERT ON training_experiments TO \"{APP_ROLE_NAME}\"')",
    "app role": 'APP_ROLE_NAME = "sovra_app"',
    "hex64": "_HEX64 = \"'^[0-9a-f]{64}$'\"",
    "restrict FK helper": 'ondelete="RESTRICT",\n        name=name,',
    "org FK restrict": 'ondelete="RESTRICT",\n                name="fk_training_experiments_organization_id",',
    "FK columns": '["organization_id", column],\n        [f"{target}.organization_id", f"{target}.id"],',
    "FK project": '_restrict_fk("project_id", "projects", "fk_training_experiments_project")',
    "FK artifact": '"trainable_artifact_id", "trainable_artifacts", "fk_training_experiments_artifact"',
    "FK catalog": '"model_catalog_entry_id", "model_catalog_entries", "fk_training_experiments_catalog"',
    "FK manifest": '"split_manifest_id", "dataset_split_manifests", "fk_training_experiments_manifest"',
    "FK budget": '_restrict_fk("budget_policy_id", "budget_policies", "fk_training_experiments_budget")',
    "UQ org id": 'sa.UniqueConstraint("organization_id", "id", name="uq_training_experiments_org_id_id")',
    "UQ name version": '"project_id", "name", "version", name="uq_training_experiments_project_name_version"',
    "UQ idempotency": (
        '"organization_id",\n            "requested_by",\n            "idempotency_key",\n'
        '            name="uq_training_experiments_idempotency",'
    ),
    "name check": "\"name ~ '^\\\\S(.*\\\\S)?$' AND char_length(name) <= 200\",",
    "version check": 'sa.CheckConstraint("version >= 1", name="ck_training_experiments_version")',
    "parent check v1": '"(version = 1 AND parent_version IS NULL) OR "',
    "parent check later": (
        '"(version > 1 AND parent_version IS NOT NULL "\n'
        '            "AND parent_version >= 1 AND parent_version < version)",'
    ),
    "status check": 'sa.CheckConstraint("status = \'draft\'", name="ck_training_experiments_status")',
    "method check": "\"method IN ('lora', 'qlora')\", name=\"ck_training_experiments_method\"",
    "schema version check": "\"config_schema_version = 'training_config.v1'\",",
    "config sha check": 'f"config_sha256 ~ {_HEX64}", name="ck_training_experiments_config_sha256"',
    "frozen sha check": (
        'f"frozen_manifest_sha256 ~ {_HEX64}", name="ck_training_experiments_frozen_sha256"'
    ),
    "request sha check": 'f"request_sha256 ~ {_HEX64}", name="ck_training_experiments_request_sha256"',
    "max cost check": 'sa.CheckConstraint("max_cost > 0", name="ck_training_experiments_max_cost")',
    "max cost type": 'sa.Column("max_cost", sa.Numeric(18, 4), nullable=False)',
    "currency check": '"currency ~ \'^[A-Z]{3}$\'", name="ck_training_experiments_currency"',
    "profile check": "\"security_profile IN ('standard', 'zero_egress', 'air_gapped')\",",
    "requested_by check": (
        "\"requested_by ~ '^\\\\S(.*\\\\S)?$' AND char_length(requested_by) <= 200\","
    ),
    "request id check": '"char_length(request_id) BETWEEN 1 AND 128"',
    "key check": "\"idempotency_key ~ '^[\\\\x20-\\\\x7E]{1,128}$'\",",
    "consistency object": "\"jsonb_typeof(config_json) = 'object' \"",
    "consistency schema version": (
        "\"AND (config_json->>'schema_version') IS NOT DISTINCT FROM config_schema_version \""
    ),
    "consistency method": "\"AND (config_json->>'method') IS NOT DISTINCT FROM method \"",
    "consistency train role": (
        "\"AND (config_json#>>'{data,train,role}') IS NOT DISTINCT FROM 'train' \""
    ),
    "consistency validation role": (
        "\"AND (config_json#>>'{data,validation,role}') IS NOT DISTINCT FROM 'validation' \""
    ),
    "consistency train manifest": (
        "\"AND (config_json#>>'{data,train,split_manifest_id}') \"\n"
        '            "IS NOT DISTINCT FROM split_manifest_id::text "'
    ),
    "consistency validation manifest": (
        "\"AND (config_json#>>'{data,validation,split_manifest_id}') \"\n"
        '            "IS NOT DISTINCT FROM split_manifest_id::text",'
    ),
    "guard manifest read": (
        '"WHERE id = NEW.split_manifest_id AND organization_id = NEW.organization_id; "'
    ),
    "guard manifest locked": "\"IF manifest_status IS DISTINCT FROM 'locked' \"",
    "guard manifest project": '"OR manifest_project IS DISTINCT FROM NEW.project_id THEN "',
    "guard manifest frozen": (
        '"IF manifest_frozen IS DISTINCT FROM NEW.frozen_manifest_sha256 THEN "'
    ),
    "guard artifact read": (
        '"WHERE id = NEW.trainable_artifact_id AND organization_id = NEW.organization_id; "'
    ),
    "guard artifact ready": (
        "\"IF artifact_status IS DISTINCT FROM 'ready' OR artifact_license IS NULL THEN \""
    ),
    "guard budget read": (
        '"WHERE id = NEW.budget_policy_id AND organization_id = NEW.organization_id; "'
    ),
    "guard budget project": '"IF policy_project IS DISTINCT FROM NEW.project_id THEN "',
    "guard constraint locked": '"tr_training_experiments_manifest_locked",',
    "guard constraint frozen": '"tr_training_experiments_manifest_frozen",',
    "guard constraint artifact": '"tr_training_experiments_artifact_ready",',
    "guard constraint budget": '"tr_training_experiments_budget_project",',
    "guard raise": "f\"RAISE EXCEPTION '{message}' USING ERRCODE = 'check_violation', \"",
    "guard raise constraint": "f\"CONSTRAINT = '{constraint}'; \"",
    "trigger": (
        '"CREATE TRIGGER tr_training_experiments_insert_guard BEFORE INSERT ON training_experiments "'
    ),
    "trigger row": 'f"FOR EACH ROW EXECUTE FUNCTION {_GUARD_FUNCTION}()"',
    "downgrade trigger": (
        '"DROP TRIGGER IF EXISTS tr_training_experiments_insert_guard ON training_experiments"'
    ),
    "downgrade function": 'f"DROP FUNCTION IF EXISTS {_GUARD_FUNCTION}()"',
    "downgrade policy": '"DROP POLICY IF EXISTS tenant_isolation ON training_experiments"',
    "downgrade revoke": "f'REVOKE ALL ON training_experiments FROM \"{APP_ROLE_NAME}\"'",
    "downgrade table": 'op.drop_table("training_experiments")',
}
FORBIDDEN_BEFORE_DOWNGRADE = (
    "USING (true)",
    "WITH CHECK (true)",
    "GRANT ALL",
    "GRANT UPDATE",
    "GRANT DELETE",
    "UPDATE ON",
    "DELETE ON",
    "TRUNCATE",
    "DATA-EXPOSURE-REVIEWED",
    "NO FORCE",
    "DISABLE ROW LEVEL SECURITY",
    "DISABLE TRIGGER",
    "SECURITY DEFINER",
    "BYPASSRLS",
    'ondelete="CASCADE"',
    'ondelete="SET NULL"',
)


def migration_findings(src: str) -> list[str]:
    """Every broken rule of the migration source, as text. Empty = compliant."""
    findings = [label for label, needle in MIGRATION_NEEDLES.items() if needle not in src]
    upgrade = src.split("def downgrade()", 1)[0]
    findings += [f"forbidden: {bad}" for bad in FORBIDDEN_BEFORE_DOWNGRADE if bad in upgrade]
    findings += [f"constraint: {name}" for name in CONSTRAINTS if f'"{name}"' not in src]
    if len(re.findall(r"\bGRANT\b", upgrade)) != 1:
        findings.append("exactly one GRANT")
    if len(re.findall(r"CREATE POLICY", upgrade)) != 1:
        findings.append("exactly one policy")
    if len(re.findall(r"sa\.CheckConstraint\(", upgrade)) != 16:
        findings.append("sixteen CHECK constraints")
    if len(re.findall(r"_restrict_fk\(\s*\"", upgrade)) != 5:
        findings.append("five composite RESTRICT foreign keys")
    if len(re.findall(r"nullable=True", upgrade)) != 2:
        findings.append("only parent_version and model_catalog_entry_id are nullable")
    if len('"0039_training_experiments"') - 2 > 32:
        findings.append("revision id fits alembic_version (32)")
    return findings


MIGRATION_FAULTS: dict[str, tuple[str, str]] = {
    "FORCE dropped": (
        '"ALTER TABLE training_experiments FORCE ROW LEVEL SECURITY"',
        '"SELECT 1"',
    ),
    "ENABLE dropped": (
        '"ALTER TABLE training_experiments ENABLE ROW LEVEL SECURITY"',
        '"SELECT 1"',
    ),
    "allow-all USING": ('f"USING (organization_id = {tenant_setting}) "', '"USING (true) "'),
    "allow-all WITH CHECK": (
        'f"WITH CHECK (organization_id = {tenant_setting})"',
        '"WITH CHECK (true)"',
    ),
    "policy on another column": (
        'f"USING (organization_id = {tenant_setting}) "',
        'f"USING (project_id = {tenant_setting}) "',
    ),
    "UPDATE granted": ("GRANT SELECT, INSERT ON", "GRANT SELECT, INSERT, UPDATE ON"),
    "DELETE granted": ("GRANT SELECT, INSERT ON", "GRANT SELECT, INSERT, DELETE ON"),
    "ALL granted": ("GRANT SELECT, INSERT ON", "GRANT ALL ON"),
    "second grant": (
        "    tenant_setting = ",
        "    op.execute('GRANT UPDATE (status) ON training_experiments TO \"sovra_app\"')\n"
        "    tenant_setting = ",
    ),
    "held-out CHECK: train role dropped": (
        "\"AND (config_json#>>'{data,train,role}') IS NOT DISTINCT FROM 'train' \"\n",
        "",
    ),
    "held-out CHECK: validation role dropped": (
        "\"AND (config_json#>>'{data,validation,role}') IS NOT DISTINCT FROM 'validation' \"\n",
        "",
    ),
    "held-out CHECK: validation accepts final": (
        "IS NOT DISTINCT FROM 'validation' \"",
        "IN ('validation', 'final_qualification') \"",
    ),
    "held-out CHECK: null-tolerant compare": (
        "\"AND (config_json#>>'{data,train,role}') IS NOT DISTINCT FROM 'train' \"",
        "\"AND (config_json#>>'{data,train,role}') = 'train' \"",
    ),
    "consistency: manifest id dropped": (
        "\"AND (config_json#>>'{data,train,split_manifest_id}') \"\n"
        '            "IS NOT DISTINCT FROM split_manifest_id::text "\n',
        "",
    ),
    "consistency CHECK replaced by true": (
        "\"jsonb_typeof(config_json) = 'object' \"",
        '"true OR "',
    ),
    "status widened": ("\"status = 'draft'\"", "\"status IN ('draft', 'submitted')\""),
    "method widened": (
        "\"method IN ('lora', 'qlora')\"",
        "\"method IN ('lora', 'qlora', 'full_sft')\"",
    ),
    "trigger dropped": (
        '"CREATE TRIGGER tr_training_experiments_insert_guard BEFORE INSERT ON training_experiments "',
        '"SELECT 1 -- "',
    ),
    "trigger after insert": (
        "BEFORE INSERT ON training_experiments",
        "AFTER INSERT ON training_experiments",
    ),
    "trigger: locked check bypassed": (
        "\"IF manifest_status IS DISTINCT FROM 'locked' \"",
        '"IF false "',
    ),
    "trigger: proposed accepted": (
        "\"IF manifest_status IS DISTINCT FROM 'locked' \"",
        "\"IF manifest_status NOT IN ('locked', 'proposed') \"",
    ),
    "trigger: project check dropped": (
        '        "OR manifest_project IS DISTINCT FROM NEW.project_id THEN "',
        '        "THEN "',
    ),
    "trigger: frozen check bypassed": (
        '"IF manifest_frozen IS DISTINCT FROM NEW.frozen_manifest_sha256 THEN "',
        '"IF false THEN "',
    ),
    "trigger: license check dropped": (
        "\"IF artifact_status IS DISTINCT FROM 'ready' OR artifact_license IS NULL THEN \"",
        "\"IF artifact_status IS DISTINCT FROM 'ready' THEN \"",
    ),
    "trigger: ready check dropped": (
        "\"IF artifact_status IS DISTINCT FROM 'ready' OR artifact_license IS NULL THEN \"",
        '"IF artifact_license IS NULL THEN "',
    ),
    "trigger: budget project dropped": (
        '"IF policy_project IS DISTINCT FROM NEW.project_id THEN "',
        '"IF false THEN "',
    ),
    "trigger: manifest read unscoped": (
        '"WHERE id = NEW.split_manifest_id AND organization_id = NEW.organization_id; "',
        '"WHERE id = NEW.split_manifest_id; "',
    ),
    "trigger: raise is a notice": ("f\"RAISE EXCEPTION '{message}'", "f\"RAISE NOTICE '{message}'"),
    "trigger: security definer": (
        "RETURNS trigger LANGUAGE plpgsql AS $$ ",
        "RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$ ",
    ),
    "FK cascade": (
        'ondelete="RESTRICT",\n        name=name,',
        'ondelete="CASCADE",\n        name=name,',
    ),
    "FK without the organization": (
        '["organization_id", column],\n        [f"{target}.organization_id", f"{target}.id"],',
        '[column],\n        [f"{target}.id"],',
    ),
    "idempotency unique per organization only": (
        '"organization_id",\n            "requested_by",\n            "idempotency_key",\n',
        '"organization_id",\n            "idempotency_key",\n',
    ),
    "name/version unique dropped": (
        '"project_id", "name", "version", name="uq_training_experiments_project_name_version"',
        '"project_id", "name", "version", "id", name="uq_training_experiments_project_name_version"',
    ),
    "max_cost zero allowed": ('"max_cost > 0"', '"max_cost >= 0"'),
    "hash check loosened": ("_HEX64 = \"'^[0-9a-f]{64}$'\"", "_HEX64 = \"'^[0-9a-f]{1,64}$'\""),
    "parent version may equal version": (
        "AND parent_version < version)",
        "AND parent_version <= version)",
    ),
    "wrong down_revision": (
        'down_revision = "0038_dataset_mapping_requests"',
        'down_revision = "0037_model_role_assignments"',
    ),
    "table name behind a variable": (
        'op.create_table(\n        "training_experiments",',
        "op.create_table(\n        TABLE_NAME,",
    ),
    "review marker added": (
        "def upgrade() -> None:",
        "# DATA-EXPOSURE-REVIEWED: training_experiments -- no\ndef upgrade() -> None:",
    ),
    "downgrade keeps the grant": (
        "f'REVOKE ALL ON training_experiments FROM",
        "f'GRANT SELECT ON training_experiments TO",
    ),
}


def part_m() -> None:
    src = MIGRATION.read_text(encoding="utf-8")
    findings = migration_findings(src)
    check("LAW18-MIGRATION", f"migration 0039 is compliant: {findings}", findings == [])
    for label, (old, new) in MIGRATION_FAULTS.items():
        present = src.count(old) >= 1
        check("LAW18-FAULT", f"fault text present: {label}", present)
        if present:
            red = migration_findings(src.replace(old, new, 1))
            check("LAW18-FAULT", f"fault {label} -> RED", red != [])


def offline_sql(action: str, revision_range: str) -> str:
    env = {**os.environ, "SOVRA_DATABASE_URL": "postgresql+psycopg://x:y@localhost:5432/z"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", action, revision_range, "--sql"],
        cwd=str(ROOT / "apps" / "api"),
        capture_output=True,
        text=True,
        env=env,
    )
    check(
        "LAW18-MIGRATION",
        f"alembic {action} {revision_range} --sql exits 0 ({result.stderr[-200:]})",
        result.returncode == 0,
    )
    return result.stdout


def part_m_sql() -> None:
    up = offline_sql("upgrade", "0038_dataset_mapping_requests:0039_training_experiments")
    flat = " ".join(up.split())
    for needle in (
        "CREATE TABLE training_experiments (",
        "id UUID DEFAULT gen_random_uuid() NOT NULL",
        "organization_id UUID NOT NULL",
        "project_id UUID NOT NULL",
        "name VARCHAR(200) NOT NULL",
        "version INTEGER NOT NULL",
        "parent_version INTEGER,",
        "status VARCHAR(16) DEFAULT 'draft' NOT NULL",
        "method VARCHAR(16) NOT NULL",
        "config_schema_version VARCHAR(32) NOT NULL",
        "config_json JSONB NOT NULL",
        "config_sha256 VARCHAR(64) NOT NULL",
        "trainable_artifact_id UUID NOT NULL",
        "model_catalog_entry_id UUID,",
        "split_manifest_id UUID NOT NULL",
        "frozen_manifest_sha256 VARCHAR(64) NOT NULL",
        "budget_policy_id UUID NOT NULL",
        "max_cost NUMERIC(18, 4) NOT NULL",
        "currency VARCHAR(3) NOT NULL",
        "security_profile VARCHAR(64) NOT NULL",
        "requested_by VARCHAR(200) NOT NULL",
        "request_id VARCHAR(128) NOT NULL",
        "idempotency_key VARCHAR(128) NOT NULL",
        "request_sha256 VARCHAR(64) NOT NULL",
        "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL",
        "CONSTRAINT fk_training_experiments_organization_id FOREIGN KEY(organization_id) "
        "REFERENCES organizations (id) ON DELETE RESTRICT",
        "CONSTRAINT fk_training_experiments_project FOREIGN KEY(organization_id, project_id) "
        "REFERENCES projects (organization_id, id) ON DELETE RESTRICT",
        "CONSTRAINT fk_training_experiments_artifact FOREIGN KEY(organization_id, "
        "trainable_artifact_id) REFERENCES trainable_artifacts (organization_id, id) "
        "ON DELETE RESTRICT",
        "CONSTRAINT fk_training_experiments_catalog FOREIGN KEY(organization_id, "
        "model_catalog_entry_id) REFERENCES model_catalog_entries (organization_id, id) "
        "ON DELETE RESTRICT",
        "CONSTRAINT fk_training_experiments_manifest FOREIGN KEY(organization_id, "
        "split_manifest_id) REFERENCES dataset_split_manifests (organization_id, id) "
        "ON DELETE RESTRICT",
        "CONSTRAINT fk_training_experiments_budget FOREIGN KEY(organization_id, budget_policy_id) "
        "REFERENCES budget_policies (organization_id, id) ON DELETE RESTRICT",
        "CONSTRAINT uq_training_experiments_org_id_id UNIQUE (organization_id, id)",
        "CONSTRAINT uq_training_experiments_project_name_version UNIQUE (project_id, name, version)",
        "CONSTRAINT uq_training_experiments_idempotency UNIQUE (organization_id, requested_by, "
        "idempotency_key)",
        "CONSTRAINT ck_training_experiments_status CHECK (status = 'draft')",
        "CONSTRAINT ck_training_experiments_method CHECK (method IN ('lora', 'qlora'))",
        "CONSTRAINT ck_training_experiments_schema_version CHECK (config_schema_version = "
        "'training_config.v1')",
        "CONSTRAINT ck_training_experiments_config_sha256 CHECK (config_sha256 ~ '^[0-9a-f]{64}$')",
        "CONSTRAINT ck_training_experiments_frozen_sha256 CHECK (frozen_manifest_sha256 ~ "
        "'^[0-9a-f]{64}$')",
        "CONSTRAINT ck_training_experiments_request_sha256 CHECK (request_sha256 ~ "
        "'^[0-9a-f]{64}$')",
        "CONSTRAINT ck_training_experiments_max_cost CHECK (max_cost > 0)",
        "CONSTRAINT ck_training_experiments_currency CHECK (currency ~ '^[A-Z]{3}$')",
        "CONSTRAINT ck_training_experiments_name CHECK (name ~ '^\\S(.*\\S)?$' AND "
        "char_length(name) <= 200)",
        "CONSTRAINT ck_training_experiments_idempotency_key CHECK (idempotency_key ~ "
        "'^[\\x20-\\x7E]{1,128}$')",
        "CONSTRAINT ck_training_experiments_config_consistency CHECK (jsonb_typeof(config_json) = "
        "'object' AND (config_json->>'schema_version') IS NOT DISTINCT FROM config_schema_version "
        "AND (config_json->>'method') IS NOT DISTINCT FROM method AND "
        "(config_json#>>'{data,train,role}') IS NOT DISTINCT FROM 'train' AND "
        "(config_json#>>'{data,validation,role}') IS NOT DISTINCT FROM 'validation' AND "
        "(config_json#>>'{data,train,split_manifest_id}') IS NOT DISTINCT FROM "
        "split_manifest_id::text AND (config_json#>>'{data,validation,split_manifest_id}') "
        "IS NOT DISTINCT FROM split_manifest_id::text)",
        "CREATE FUNCTION training_experiments_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$",
        "IF manifest_status IS DISTINCT FROM 'locked' OR manifest_project IS DISTINCT FROM "
        "NEW.project_id THEN RAISE EXCEPTION 'an experiment names a locked split manifest of its "
        "own project' USING ERRCODE = 'check_violation', CONSTRAINT = "
        "'tr_training_experiments_manifest_locked'; END IF;",
        "IF manifest_frozen IS DISTINCT FROM NEW.frozen_manifest_sha256 THEN RAISE EXCEPTION",
        "CONSTRAINT = 'tr_training_experiments_manifest_frozen'; END IF;",
        "IF artifact_status IS DISTINCT FROM 'ready' OR artifact_license IS NULL THEN RAISE "
        "EXCEPTION",
        "CONSTRAINT = 'tr_training_experiments_artifact_ready'; END IF;",
        "IF policy_project IS DISTINCT FROM NEW.project_id THEN RAISE EXCEPTION",
        "CONSTRAINT = 'tr_training_experiments_budget_project'; END IF; RETURN NEW; END $$",
        "CREATE TRIGGER tr_training_experiments_insert_guard BEFORE INSERT ON training_experiments "
        "FOR EACH ROW EXECUTE FUNCTION training_experiments_insert_guard()",
        'GRANT SELECT, INSERT ON training_experiments TO "sovra_app"',
        "ALTER TABLE training_experiments ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE training_experiments FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation ON training_experiments FOR ALL USING (organization_id = "
        "NULLIF(current_setting('app.current_organization_id', true), '')::uuid) WITH CHECK "
        "(organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid)",
        "UPDATE alembic_version SET version_num='0039_training_experiments'",
    ):
        check("LAW18-SQL", f"the upgrade SQL contains: {needle[:100]}", needle in flat)
    for bad in (
        "USING (true)",
        "GRANT ALL",
        "UPDATE ON training_experiments",
        "DELETE ON",
        "CASCADE",
    ):
        check("LAW18-SQL", f"the upgrade SQL never contains: {bad}", bad not in flat)
    check("LAW18-SQL", "exactly one GRANT in the upgrade SQL", flat.count("GRANT ") == 1)
    down = " ".join(
        offline_sql("downgrade", "0039_training_experiments:0038_dataset_mapping_requests").split()
    )
    for needle in (
        "DROP TRIGGER IF EXISTS tr_training_experiments_insert_guard ON training_experiments",
        "DROP FUNCTION IF EXISTS training_experiments_insert_guard()",
        "DROP POLICY IF EXISTS tenant_isolation ON training_experiments",
        'REVOKE ALL ON training_experiments FROM "sovra_app"',
        "DROP TABLE training_experiments",
        "UPDATE alembic_version SET version_num='0038_dataset_mapping_requests'",
    ):
        check("LAW18-SQL", f"the downgrade SQL contains: {needle}", needle in down)
    heads = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=str(ROOT / "apps" / "api"),
        capture_output=True,
        text=True,
        env={**os.environ, "SOVRA_DATABASE_URL": "postgresql+psycopg://x:y@localhost:5432/z"},
    )
    check(
        "LAW18-SQL",
        f"exactly one head, 0039: {heads.stdout!r}",
        heads.stdout.count("(head)") == 1 and "0039_training_experiments" in heads.stdout,
    )


# ---------------------------------------------------------------------------------------------
# S: the store over SQLite
# ---------------------------------------------------------------------------------------------

# Letters in every id: SQLite gives a UUID column numeric affinity and would read an all-digit hex as an int.
ORG1 = uuid.UUID("aaaaaaaa-0000-4000-8000-00000000a001")
ORG2 = uuid.UUID("bbbbbbbb-0000-4000-8000-00000000b002")
SHA_TRAIN, SHA_VAL, SHA_FROZEN = "1" * 64, "2" * 64, "f" * 64
WEIGHTS = "ab" * 32
WRITER = store.ExperimentActor("user", "user-ml-1", "ml_engineer", "req-1")
WRITER_2 = store.ExperimentActor("user", "user-ml-2", "ml_engineer", "req-2")
ADMIN = store.ExperimentActor("service", "user-admin", "organization_admin", "req-3")


class Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.sessions: list[Any] = []

    def __call__(self, session: Session, **kwargs: Any) -> None:
        self.sessions.append(session)
        self.events.append(kwargs)


def passthrough(
    session: Session, organization_id: uuid.UUID, statement: Any, params: Any = None
) -> Any:
    PASSTHROUGH_ORGS.append(organization_id)
    return session.execute(statement) if params is None else session.execute(statement, params)


PASSTHROUGH_ORGS: list[uuid.UUID] = []


class World:
    """One SQLite session wired like the Postgres one. The fact sources are dictionaries the test
    edits; each stand-in records how it was called."""

    def __init__(self, session: Session, audit: Audit) -> None:
        self.session = session
        self.audit = audit
        self.ctx = TenantContext(organization_id=ORG1)
        self.ctx2 = TenantContext(organization_id=ORG2)
        self.project_id = uuid.uuid4()
        self.other_project = uuid.uuid4()
        self.foreign_project = uuid.uuid4()
        self.calls: list[tuple[Any, ...]] = []
        for org, default in ((ORG1, "standard"), (ORG2, "standard")):
            session.execute(
                text("INSERT INTO organizations (id, default_security_profile) VALUES (:i, :d)"),
                {"i": org.hex, "d": default},
            )
        for project, org in (
            (self.project_id, ORG1),
            (self.other_project, ORG1),
            (self.foreign_project, ORG2),
        ):
            session.execute(
                text(
                    "INSERT INTO projects (id, organization_id, security_profile) "
                    "VALUES (:i, :o, 'standard')"
                ),
                {"i": project.hex, "o": org.hex},
            )
        self.manifest_id = uuid.uuid4()
        self.artifact = self.make_artifact()
        self.artifacts: dict[uuid.UUID, Any] = {self.artifact.id: self.artifact}
        self.policy: Any = SimpleNamespace(
            id=uuid.uuid4(),
            row_version=4,
            currency="USD",
            scopes_json=["training", "evaluation"],
            total_amount=Decimal("1000.0000"),
            reserved_amount=Decimal("0.0000"),
        )
        self.catalog: dict[uuid.UUID, Any] = {}
        self.manifest_values: dict[str, Any] = {
            "id": self.manifest_id,
            "status": "locked",
            "version": 3,
            "frozen_manifest_sha256": SHA_FROZEN,
        }
        self.roles: list[Any] = [
            SimpleNamespace(role="train", record_count=5, role_sha256=SHA_TRAIN),
            SimpleNamespace(role="validation", record_count=2, role_sha256=SHA_VAL),
            SimpleNamespace(role="development_eval", record_count=9, role_sha256="3" * 64),
            SimpleNamespace(role="final_qualification", record_count=9, role_sha256="4" * 64),
        ]
        self.summary_error: str | None = None
        self.load_error: str | None = None
        self.view_over: dict[str, Any] = {}
        self.view_rows: list[str] | None = None

    # ---- fixtures -------------------------------------------------------
    def make_artifact(self, **over: Any) -> Any:
        values: dict[str, Any] = {
            "id": uuid.uuid4(),
            "organization_id": ORG1,
            "display_name": "base",
            "source_type": "huggingface",
            "source_uri": "org/base",
            "model_family": "llama",
            "base_model_revision": "rev1",
            "weights_digest": "sha256:" + WEIGHTS,
            "license_id": "apache-2.0",
            "license_accepted_by": "user-admin",
            "license_accepted_at": datetime(2026, 11, 1, tzinfo=UTC),
            "trust_remote_code_allowed": False,
            "required_python_packages_json": [],
            "minimum_gpu_profile_json": {},
            "status": "ready",
        }
        values.update(over)
        return TrainableArtifact(**values)

    def set_default_profile(self, org: uuid.UUID, profile: str) -> None:
        self.session.execute(
            text("UPDATE organizations SET default_security_profile=:d WHERE id=:i"),
            {"d": profile, "i": org.hex},
        )

    def set_project_profile(self, profile: str) -> None:
        self.session.execute(
            text("UPDATE projects SET security_profile=:d WHERE id=:i"),
            {"d": profile, "i": self.project_id.hex},
        )

    # ---- stand-ins for the certified readers ------------------------------
    def get_artifact(self, session: Session, context: TenantContext, artifact_id: uuid.UUID) -> Any:
        self.calls.append(("get_artifact", session, context, artifact_id))
        artifact = self.artifacts.get(artifact_id)
        if artifact is None or artifact.organization_id != context.organization_id:
            raise ArtifactNotFoundError(f"{CANARY} artifact {artifact_id}")
        return artifact

    def require_current_mapping(
        self, session: Session, context: TenantContext, entry_id: uuid.UUID, version: int
    ) -> Any:
        self.calls.append(("require_current_mapping", session, context, entry_id, version))
        record = self.catalog.get(entry_id)
        if record is None:
            raise ModelCatalogEntryNotFoundError(f"{CANARY} entry {entry_id}")
        if record.catalog_version != version:
            raise MappingChangedError(entry_id, version, record.catalog_version)
        return record

    def get_split_manifest(
        self,
        session: Session,
        context: TenantContext,
        *,
        project_id: uuid.UUID,
        manifest_id: uuid.UUID,
    ) -> Any:
        self.calls.append(("get_split_manifest", session, context, project_id, manifest_id))
        if self.summary_error is not None:
            raise split_store.SplitManifestError(self.summary_error)
        if manifest_id != self.manifest_id or project_id != self.project_id:
            raise split_store.SplitManifestError("manifest_not_found")
        return split_store.ManifestSummary(
            split_store.ManifestView(values=dict(self.manifest_values), leakage_summary={}),
            tuple(self.roles),
            (),
        )

    def load_training_split(
        self,
        session: Session,
        context: TenantContext,
        *,
        project_id: uuid.UUID,
        manifest_id: uuid.UUID,
    ) -> Any:
        self.calls.append(("load_training_split", session, context, project_id, manifest_id))
        if self.load_error is not None:
            raise split_store.SplitManifestError(self.load_error)
        rows = self.view_rows
        if rows is None:
            rows = []
            for role in self.roles:
                if role.role in ("train", "validation"):
                    rows += [role.role] * role.record_count
        values = {
            "manifest_id": manifest_id,
            "version": self.manifest_values["version"],
            "frozen_manifest_sha256": self.manifest_values["frozen_manifest_sha256"],
            "rows": tuple(SimpleNamespace(role=r) for r in rows),
        }
        values.update(self.view_over)
        return split_store.TrainingSplitView(**values)

    def get_budget_policy(
        self, session: Session, context: TenantContext, project_id: uuid.UUID
    ) -> Any:
        self.calls.append(("get_budget_policy", session, context, project_id))
        if self.policy is None or project_id != self.project_id:
            raise BudgetPolicyNotFoundError(f"{CANARY} policy")
        return self.policy

    # ---- driving ----------------------------------------------------------
    def body(self, **over: Any) -> dict[str, Any]:
        body = copy.deepcopy(BASE_BODY)
        body["base"]["trainable_artifact_id"] = str(self.artifact.id)
        body["tokenizer"]["revision"] = "rev1"
        for slot in ("train", "validation"):
            body["data"][slot]["split_manifest_id"] = str(self.manifest_id)
        body["data"]["frozen_manifest_sha256"] = SHA_FROZEN
        body["budget"] = {"max_cost": "10.5", "currency": "USD", "max_wall_clock_seconds": 3600}
        for path, value in over.items():
            node = body
            parts = path.split("__")
            for part in parts[:-1]:
                node = node[part]
            node[parts[-1]] = value
        return body

    def create(
        self,
        name: Any = "exp-a",
        body: Any = None,
        key: Any = "key-1",
        *,
        actor: Any = WRITER,
        ctx: TenantContext | None = None,
        project_id: uuid.UUID | None = None,
    ) -> Any:
        return store.create_training_experiment(
            self.session,
            ctx or self.ctx,
            project_id or self.project_id,
            name=name,
            config_body=self.body() if body is None else body,
            idempotency_key=key,
            actor=actor,
        )

    def count(self) -> int:
        self.session.flush()
        return int(self.session.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar() or 0)

    def reader_calls(self) -> list[str]:
        return [c[0] for c in self.calls]


@contextlib.contextmanager
def sqlite_world() -> Any:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE organizations (id CHAR(32) PRIMARY KEY, "
                "default_security_profile VARCHAR(64) NOT NULL)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE projects (id CHAR(32) PRIMARY KEY, organization_id CHAR(32) NOT NULL, "
                "security_profile VARCHAR(64) NOT NULL)"
            )
        )
    store.TrainingExperiment.__table__.create(engine)
    audit = Audit()
    names = (
        "execute_tenant_scoped",
        "write_audit_event",
        "get_artifact",
        "require_current_mapping",
        "get_split_manifest",
        "load_training_split",
        "get_budget_policy",
    )
    saved = {name: getattr(store, name) for name in names}
    with Session(engine) as session:
        world = World(session, audit)
        store.execute_tenant_scoped = passthrough
        store.write_audit_event = audit
        store.get_artifact = world.get_artifact
        store.require_current_mapping = world.require_current_mapping
        store.get_split_manifest = world.get_split_manifest
        store.load_training_split = world.load_training_split
        store.get_budget_policy = world.get_budget_policy
        try:
            yield world
        finally:
            for name, value in saved.items():
                setattr(store, name, value)
    engine.dispose()


def code_of(fn: Any) -> str | None:
    """The stable code a store call refuses with; None if it did not refuse."""
    try:
        fn()
    except store.TrainingExperimentError as exc:
        return exc.code
    except BaseException as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    return None


def refuses(world: World, label: str, code: str, fn: Any, criterion: str = "REFUSE") -> None:
    rows, events = world.count(), len(world.audit.events)
    got = code_of(fn)
    check(criterion, f"{label}: refused with {code} (got {got})", got == code)
    check(criterion, f"{label}: wrote no row", world.count() == rows)
    check(criterion, f"{label}: wrote no audit event", len(world.audit.events) == events)


def expected_request_sha(project_id: uuid.UUID, name: str, doc: Any) -> str:
    document = {"config": doc, "name": name, "project_id": str(project_id)}
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
    ).hexdigest()


def part_s_constants() -> None:
    check(
        "CODES",
        "store codes include every pure code with its status",
        all(store.STORE_ERROR_CODES[c] == s for c, s in schema.ERROR_CODES.items()),
    )
    check(
        "CODES",
        "the store adds exactly its four codes",
        {c: s for c, s in store.STORE_ERROR_CODES.items() if c not in schema.ERROR_CODES}
        == {
            "not_found": 404,
            "actor_not_permitted": 403,
            "version_conflict": 409,
            "config_integrity_failed": 500,
        },
    )
    error = store.TrainingExperimentError("not_found")
    check(
        "CODES",
        "an error carries its code, status and code-only text",
        (error.code, error.status, str(error), error.args)
        == ("not_found", 404, "not_found", ("not_found",)),
    )
    check(
        "CODES",
        "a 403 keeps its status",
        store.TrainingExperimentError("heldout_training_source_denied").status == 403,
    )
    try:
        store.TrainingExperimentError("no_such_code")
        raised = False
    except ValueError as exc:
        raised = CANARY not in str(exc) and "no_such_code" not in str(exc)
    check("CODES", "an unknown code is a ValueError that does not echo it", raised)
    for spelling in ("NOT_FOUND", " not_found", "not_found ", "Not_Found", "\tinvalid_body\n"):
        try:
            store.TrainingExperimentError(spelling)
            exact = False
        except ValueError:
            exact = True
        check("CODES", f"a code is matched exactly, never folded or trimmed: {spelling!r}", exact)
    check(
        "CODES",
        "writers are exactly the ML engineer and the organization admin",
        (store.WRITER_ROLES == frozenset({"ml_engineer", "organization_admin"})),
    )
    check(
        "CODES",
        "limits",
        (store.NAME_MAX_LENGTH, store.IDEMPOTENCY_KEY_MAX_LENGTH, store.MAX_PAGE_SIZE)
        == (200, 128, 200),
    )
    check(
        "CODES",
        "the ORM table is training_experiments",
        (store.TrainingExperiment.__tablename__ == TABLE),
    )


def part_s_create() -> None:
    with sqlite_world() as w:
        created = w.create()
        exp = created.experiment
        w.session.flush()
        check("CREATE", "a first create is not a replay", created.replayed is False)
        resolved = schema.resolve_training_config(
            schema.parse_training_config_request(w.body()),
            schema.TrainingConfigFacts(
                artifact=schema.ArtifactFacts(
                    artifact_id=str(w.artifact.id),
                    status="ready",
                    capability="trainable_open_weight",
                    weights_digest=WEIGHTS,
                    base_revision="rev1",
                    license_accepted=True,
                ),
                split=schema.SplitFacts(
                    manifest_id=str(w.manifest_id),
                    manifest_version=3,
                    status="locked",
                    frozen_sha256=SHA_FROZEN,
                    hash_verified=True,
                    quality_report_current=True,
                    grants_current=True,
                    train_count=5,
                    validation_count=2,
                    train_role_sha256=SHA_TRAIN,
                    validation_role_sha256=SHA_VAL,
                ),
                security_profile="standard",
                budget=schema.BudgetFacts(
                    policy_id=str(w.policy.id),
                    row_version=4,
                    currency="USD",
                    scopes=("training", "evaluation"),
                    total=Decimal("1000.0000"),
                    reserved=Decimal("0.0000"),
                ),
            ),
        )
        expected_doc = json.loads(schema.canonical_config_json(resolved.doc))
        check(
            "CREATE",
            "the stored document is the pure resolution of the facts",
            (exp.config_json == expected_doc),
        )
        check(
            "CREATE",
            "the stored hash is the pure hash",
            exp.config_sha256 == resolved.config_sha256,
        )
        check(
            "CREATE",
            "the stored hash is an independent sha256 of the canonical document",
            (
                exp.config_sha256
                == hashlib.sha256(
                    json.dumps(exp.config_json, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
            ),
        )
        check(
            "CREATE", "the document is a plain dict (JSON-storable)", type(exp.config_json) is dict
        )
        check(
            "CREATE",
            "row columns",
            (
                exp.organization_id,
                exp.project_id,
                exp.name,
                exp.version,
                exp.parent_version,
                exp.status,
                exp.method,
                exp.config_schema_version,
                exp.trainable_artifact_id,
                exp.model_catalog_entry_id,
                exp.split_manifest_id,
                exp.frozen_manifest_sha256,
                exp.budget_policy_id,
                exp.max_cost,
                exp.currency,
                exp.security_profile,
                exp.requested_by,
                exp.request_id,
                exp.idempotency_key,
            )
            == (
                ORG1,
                w.project_id,
                "exp-a",
                1,
                None,
                "draft",
                "lora",
                "training_config.v1",
                w.artifact.id,
                None,
                w.manifest_id,
                SHA_FROZEN,
                w.policy.id,
                Decimal("10.5"),
                "USD",
                "standard",
                "user-ml-1",
                "req-1",
                "key-1",
            ),
        )
        check(
            "CREATE",
            "the id is a uuid and created_at is an aware instant",
            (
                isinstance(exp.id, uuid.UUID)
                and isinstance(exp.created_at, datetime)
                and exp.created_at.tzinfo is not None
            ),
        )
        parsed_doc = json.loads(
            schema.canonical_config_json(schema.parse_training_config_request(w.body()).doc)
        )
        check(
            "CREATE",
            "request_sha256 is the digest of project, name and parsed config",
            (exp.request_sha256 == expected_request_sha(w.project_id, "exp-a", parsed_doc)),
        )
        check(
            "CREATE",
            "exactly one audit event, content-free, in the caller's session",
            w.audit.events
            == [
                {
                    "organization_id": ORG1,
                    "actor_type": "user",
                    "actor_id": "user-ml-1",
                    "action": "training_experiment.create",
                    "resource_type": "training_experiment",
                    "resource_id": str(exp.id),
                    "request_id": "req-1",
                    "details": {
                        "project_id": str(w.project_id),
                        "version": 1,
                        "method": "lora",
                        "config_sha256": exp.config_sha256,
                    },
                }
            ]
            and w.audit.sessions == [w.session],
        )
        check(
            "CREATE",
            "every reader was asked, with the caller's session and context",
            (
                sorted(set(w.reader_calls()))
                == [
                    "get_artifact",
                    "get_budget_policy",
                    "get_split_manifest",
                    "load_training_split",
                ]
                and all(c[1] is w.session and c[2] is w.ctx for c in w.calls)
            ),
        )
        check(
            "CREATE",
            "the readers were asked for this project, manifest and artifact",
            (
                ("get_artifact", w.session, w.ctx, w.artifact.id) in w.calls
                and ("get_split_manifest", w.session, w.ctx, w.project_id, w.manifest_id) in w.calls
                and ("load_training_split", w.session, w.ctx, w.project_id, w.manifest_id)
                in w.calls
                and ("get_budget_policy", w.session, w.ctx, w.project_id) in w.calls
            ),
        )
        check(
            "CREATE",
            "every query ran under the caller's organization",
            (PASSTHROUGH_ORGS != [] and set(PASSTHROUGH_ORGS) == {ORG1}),
        )

        # versions
        v2 = w.create(key="key-2", body=w.body(hyperparameters__epochs=4))
        other = w.create(name="exp-b", key="key-3")
        v3 = w.create(key="key-4", actor=ADMIN)
        check(
            "VERSION",
            "the same name is version 2 with parent 1",
            (v2.experiment.version, v2.experiment.parent_version) == (2, 1),
        )
        check(
            "VERSION",
            "another name starts at version 1",
            (other.experiment.version, other.experiment.parent_version) == (1, None),
        )
        check(
            "VERSION",
            "the third is version 3 with parent 2, by the admin service actor",
            (
                v3.experiment.version,
                v3.experiment.parent_version,
                v3.experiment.requested_by,
                v3.experiment.request_id,
            )
            == (3, 2, "user-admin", "req-3"),
        )
        check(
            "VERSION",
            "the admin's audit event carries its actor type",
            (
                w.audit.events[-1]["actor_type"] == "service"
                and w.audit.events[-1]["details"]["version"] == 3
            ),
        )

    # the same name in another project of the same organization starts at 1
    with sqlite_world() as w:
        w.create()
        saved_project = w.project_id
        w.project_id = w.other_project
        try:
            second = w.create(key="key-other-project")
        finally:
            w.project_id = saved_project
        check(
            "VERSION",
            "versions are per project",
            (second.experiment.version, second.experiment.project_id) == (1, w.other_project),
        )

    # qlora
    with sqlite_world() as w:
        q = w.create(body=w.body(method="qlora", precision__quantization="nf4"))
        check(
            "CREATE",
            "qlora is stored as qlora",
            (q.experiment.method == "qlora" and q.experiment.config_json["method"] == "qlora"),
        )
        check(
            "CREATE",
            "the audit names the method",
            w.audit.events[-1]["details"]["method"] == "qlora",
        )


def part_s_authorization() -> None:
    with sqlite_world() as w:
        for role in (
            "viewer",
            "evaluator",
            "workspace_admin",
            "reviewer",
            "ML_ENGINEER",
            " ml_engineer",
            "",
        ):
            actor = store.ExperimentActor("user", "user-x", role, "req-x")
            refuses(w, f"role {role!r}", "actor_not_permitted", lambda a=actor: w.create(actor=a))
        check("AUTHZ", "a refused role reads nothing", w.calls == [])
        # the role is judged before the project is looked up and before the body is parsed
        viewer = store.ExperimentActor("user", "user-x", "viewer", "req-x")
        refuses(
            w,
            "a non-writer on a missing project with a bad body",
            "actor_not_permitted",
            lambda: w.create(actor=viewer, project_id=uuid.uuid4(), body={"x": 1}),
        )
        refuses(w, "a missing project", "not_found", lambda: w.create(project_id=uuid.uuid4()))
        refuses(
            w,
            "a project of another organization",
            "not_found",
            lambda: w.create(project_id=w.foreign_project),
        )
        refuses(
            w,
            "the caller's project under another organization's context",
            "not_found",
            lambda: w.create(ctx=w.ctx2),
        )
        refuses(
            w,
            "a missing project with a bad body is still not_found",
            "not_found",
            lambda: w.create(project_id=uuid.uuid4(), body={"x": 1}),
        )
        check("AUTHZ", "a refused project reads no fact", w.calls == [])
        for actor in (WRITER, ADMIN):
            check(
                "AUTHZ",
                f"{actor.role} may create",
                (code_of(lambda a=actor: w.create(key=f"k-{a.role}", actor=a)) is None),
            )


def part_s_request_values() -> None:
    with sqlite_world() as w:
        for label, name in (
            ("empty", ""),
            ("one space", " "),
            ("leading space", " a"),
            ("trailing space", "a "),
            ("leading tab", "\ta"),
            ("trailing newline", "a\n"),
            ("inner newline", "a\nb"),
            ("NUL", "a\x00b"),
            ("unit separator", "a\x1fb"),
            ("201 characters", "n" * 201),
            ("an int", 5),
            ("None", None),
            ("bytes", b"exp"),
            ("a list", ["exp"]),
        ):
            refuses(w, f"name {label}", "invalid_body", lambda n=name: w.create(name=n))
        for label, key in (
            ("empty", ""),
            ("129 characters", "k" * 129),
            ("DEL", "a\x7fb"),
            ("unit separator", "a\x1fb"),
            ("newline", "a\nb"),
            ("non-ASCII", "ké"),
            ("an int", 5),
            ("None", None),
        ):
            refuses(w, f"idempotency key {label}", "invalid_body", lambda k=key: w.create(key=k))
        check("VALUES", "an invalid name or key reads no fact", w.calls == [])
        accepted = (
            ("one character", "a", "k"),
            ("200 characters", "n" * 200, "k" * 128),
            ("inner spaces and unicode", "exp é 1", " ~"),
            ("space-bounded key", "exp-space", " key "),
        )
        for label, name, key in accepted:
            check(
                "VALUES",
                f"accepted: {label}",
                code_of(lambda n=name, k=key: w.create(n, None, k)) is None,
            )
        stored = w.session.execute(text(f"SELECT name FROM {TABLE} ORDER BY name")).scalars().all()
        check(
            "VALUES",
            "names are stored exactly as given (no normalisation)",
            (sorted(stored) == sorted(n for _, n, _ in accepted)),
        )

        # pure-module refusals keep their code and status
        for label, over, code, status in (
            ("rank 0", {"adapter__lora_rank": 0}, "invalid_lora_rank", 400),
            (
                "learning rate too high",
                {"hyperparameters__learning_rate": "1"},
                "invalid_learning_rate",
                400,
            ),
            (
                "length between buckets",
                {"hyperparameters__max_seq_length": 3000},
                "invalid_max_seq_length",
                400,
            ),
            ("unsupported dtype", {"precision__compute_dtype": "int8"}, "unsupported_dtype", 400),
            ("unknown key", {"push_to_hub": True}, "invalid_body", 400),
            ("full_sft", {"method": "full_sft"}, "method_not_enabled", 409),
            ("auto", {"mode": "auto", "auto_rule_version": "v1"}, "auto_rule_version_unknown", 409),
            (
                "held-out train",
                {"data__train__role": "final_qualification"},
                "heldout_training_source_denied",
                403,
            ),
            (
                "held-out validation",
                {"data__validation__role": "development_eval"},
                "heldout_training_source_denied",
                403,
            ),
            (
                "legacy held-out",
                {"data__train__role": "heldout_test"},
                "heldout_training_source_denied",
                403,
            ),
            ("swapped role", {"data__train__role": "validation"}, "invalid_source_role", 400),
            ("qlora without nf4", {"method": "qlora"}, "method_quantization_mismatch", 400),
        ):
            refuses(w, f"pure refusal {label}", code, lambda o=over: w.create(body=w.body(**o)))
            try:
                w.create(body=w.body(**over))
                got_status: Any = None
            except store.TrainingExperimentError as exc:
                got_status = (exc.status, exc.__cause__, exc.__suppress_context__)
            check(
                "VALUES",
                f"{label}: status {status}, no chained cause",
                got_status == (status, None, True),
            )
        for label, body in (("None", None), ("a list", []), ("a string", "x"), ("an int", 1)):
            refuses(
                w,
                f"config body {label}",
                "invalid_body",
                lambda b=body: store.create_training_experiment(
                    w.session,
                    w.ctx,
                    w.project_id,
                    name="exp",
                    config_body=b,
                    idempotency_key="k9",
                    actor=WRITER,
                ),
            )
        # a shape refusal is decided before any fact is read
        w.calls.clear()
        refuses(
            w,
            "shape before facts",
            "invalid_lora_rank",
            lambda: w.create(body=w.body(adapter__lora_rank=0)),
        )
        check("VALUES", "a shape refusal reads no fact", w.calls == [])


def part_s_idempotency() -> None:
    with sqlite_world() as w:
        first = w.create()
        rows, events = w.count(), len(w.audit.events)
        w.calls.clear()
        again = w.create()
        check(
            "IDEMPOTENT",
            "a replay returns the stored row",
            (again.replayed is True and again.experiment.id == first.experiment.id),
        )
        check(
            "IDEMPOTENT",
            "a replay writes no row and no audit event",
            (w.count() == rows and len(w.audit.events) == events),
        )
        check("IDEMPOTENT", "a replay reads no fact", w.calls == [])
        spelled = w.create(
            body=w.body(hyperparameters__learning_rate="0.000200", budget__max_cost="10.50")
        )
        check(
            "IDEMPOTENT",
            "an equal decimal spelling replays",
            (spelled.replayed is True and w.count() == rows),
        )
        reordered = json.loads(json.dumps(w.body(), sort_keys=True))
        check("IDEMPOTENT", "another key order replays", w.create(body=reordered).replayed is True)
        refuses(
            w,
            "same key, another body",
            "idempotency_key_reused",
            lambda: w.create(body=w.body(hyperparameters__epochs=4)),
        )
        refuses(
            w, "same key, another name", "idempotency_key_reused", lambda: w.create(name="exp-b")
        )
        refuses(
            w,
            "same key, another project",
            "idempotency_key_reused",
            lambda: w.create(project_id=w.other_project),
        )
        # a replay is decided before the facts: it still answers after the facts changed
        w.load_error = "grant_changed"
        check("IDEMPOTENT", "a replay does not re-judge changed facts", w.create().replayed is True)
        w.load_error = None
        # an invalid body with a used key is the shape refusal, not a replay
        refuses(
            w,
            "a used key with an invalid body",
            "invalid_lora_rank",
            lambda: w.create(body=w.body(adapter__lora_rank=0)),
        )
        # scope: (organization, actor, key)
        other_actor = w.create(actor=WRITER_2)
        check(
            "IDEMPOTENT",
            "another actor with the same key gets its own row",
            (other_actor.replayed is False and other_actor.experiment.version == 2),
        )
        other_key = w.create(key="key-2")
        check(
            "IDEMPOTENT",
            "another key is a new version",
            (other_key.replayed is False and other_key.experiment.version == 3),
        )
        # same actor id and key in another organization never finds this organization's row
        foreign = store.TrainingExperiment(
            **{
                c.name: getattr(first.experiment, c.name)
                for c in store.TrainingExperiment.__table__.columns
                if c.name not in ("id", "organization_id")
            },
            id=uuid.uuid4(),
            organization_id=ORG2,
        )
        w.session.add(foreign)
        w.session.flush()
        found = store._find_by_idempotency_key(w.session, w.ctx2, "user-ml-1", "key-1")
        check(
            "IDEMPOTENT",
            "the lookup is scoped to the organization",
            (found is not None and found.id == foreign.id),
        )
        mine = store._find_by_idempotency_key(w.session, w.ctx, "user-ml-1", "key-1")
        check(
            "IDEMPOTENT",
            "and finds this organization's own row",
            (mine is not None and mine.id == first.experiment.id),
        )
        check(
            "IDEMPOTENT",
            "the lookup is scoped to the actor",
            (store._find_by_idempotency_key(w.session, w.ctx, "user-nobody", "key-1") is None),
        )
        check(
            "IDEMPOTENT",
            "the lookup is scoped to the key",
            (store._find_by_idempotency_key(w.session, w.ctx, "user-ml-1", "key-none") is None),
        )
        for label, actor_id, key in (
            ("upper-case actor", "USER-ML-1", "key-1"),
            ("padded actor", " user-ml-1 ", "key-1"),
            ("upper-case key", "user-ml-1", "KEY-1"),
            ("padded key", "user-ml-1", " key-1 "),
        ):
            check(
                "IDEMPOTENT",
                f"the lookup matches the actor and the key exactly: {label}",
                store._find_by_idempotency_key(w.session, w.ctx, actor_id, key) is None,
            )
        shouting = store.ExperimentActor("user", "USER-ML-1", "ml_engineer", "req-9")
        loud = w.create(actor=shouting)
        check(
            "IDEMPOTENT",
            "an actor id differing only in case is another actor",
            loud.replayed is False and loud.experiment.requested_by == "USER-ML-1",
        )
        cased = w.create(key="KEY-1")
        check(
            "IDEMPOTENT",
            "a key differing only in case is another key",
            cased.replayed is False and cased.experiment.idempotency_key == "KEY-1",
        )
        # a replay of a row whose document was edited is refused
        first.experiment.config_json = {**first.experiment.config_json, "method": "qlora"}
        w.session.flush()
        refuses(w, "a replay of an edited row", "config_integrity_failed", lambda: w.create())

    # request_sha256
    with sqlite_world() as w:
        request = schema.parse_training_config_request(w.body())
        doc = json.loads(schema.canonical_config_json(request.doc))
        base = store.request_sha256(w.project_id, "exp-a", request)
        check(
            "DIGEST",
            "the request digest is the documented canonical sha256",
            (base == expected_request_sha(w.project_id, "exp-a", doc)),
        )
        check(
            "DIGEST",
            "it covers the project",
            (store.request_sha256(w.other_project, "exp-a", request) != base),
        )
        check(
            "DIGEST",
            "it covers the name",
            store.request_sha256(w.project_id, "exp-b", request) != base,
        )
        other = schema.parse_training_config_request(w.body(hyperparameters__epochs=4))
        check(
            "DIGEST",
            "it covers the config",
            store.request_sha256(w.project_id, "exp-a", other) != base,
        )
        check(
            "DIGEST",
            "a non-ASCII name is hashed, not refused",
            (
                store.request_sha256(w.project_id, "é", request)
                == expected_request_sha(w.project_id, "é", doc)
            ),
        )


def part_s_facts() -> None:
    def fresh(label: str, code: str, setup: Any, body_over: dict[str, Any] | None = None) -> None:
        with sqlite_world() as w:
            setup(w)
            refuses(w, label, code, lambda: w.create(body=w.body(**(body_over or {}))), "FACTS")

    def creates(label: str, setup: Any, body_over: dict[str, Any] | None = None) -> Any:
        with sqlite_world() as w:
            setup(w)
            try:
                created = w.create(body=w.body(**(body_over or {})))
            except BaseException as exc:  # noqa: BLE001
                check("FACTS", f"{label}: creates ({type(exc).__name__}: {exc})", False)
                return None
            check("FACTS", f"{label}: creates", True)
            return SimpleNamespace(
                doc=created.experiment.config_json,
                experiment=created.experiment,
                world=w,
                calls=list(w.calls),
            )

    def swap_artifact(w: World, **over: Any) -> None:
        raw_digest = over.pop("raw_weights_digest", None)
        artifact = w.make_artifact(id=w.artifact.id, **over)
        if raw_digest is not None:
            # a stored value the constructor would refuse (a row written before the rule existed)
            artifact.weights_digest = raw_digest
        w.artifact = artifact
        w.artifacts = {artifact.id: artifact}

    # --- artifact
    fresh(
        "an artifact that does not exist", "artifact_not_trainable", lambda w: w.artifacts.clear()
    )
    fresh(
        "an artifact of another organization",
        "artifact_not_trainable",
        lambda w: swap_artifact(w, organization_id=ORG2),
    )
    fresh(
        "a registered artifact without a license",
        "license_not_accepted",
        lambda w: swap_artifact(
            w, status="registered", license_accepted_at=None, license_accepted_by=None
        ),
    )
    fresh(
        "a registered artifact with a license",
        "artifact_not_trainable",
        lambda w: swap_artifact(w, status="registered"),
    )
    fresh(
        "an archived artifact",
        "artifact_not_trainable",
        lambda w: swap_artifact(w, status="archived"),
    )
    fresh(
        "an artifact with no weights digest",
        "artifact_not_trainable",
        lambda w: swap_artifact(w, weights_digest=None),
    )
    fresh(
        "an artifact whose digest has no sha256 prefix",
        "artifact_not_trainable",
        lambda w: swap_artifact(w, raw_weights_digest=WEIGHTS),
    )
    fresh(
        "an artifact whose digest is malformed",
        "artifact_not_trainable",
        lambda w: swap_artifact(w, raw_weights_digest="sha256:xyz"),
    )
    fresh(
        "a tokenizer revision that is not the artifact's",
        "tokenizer_revision_mismatch",
        lambda w: swap_artifact(w, base_model_revision="rev2"),
    )
    ok = creates(
        "an artifact with another revision that the request names",
        lambda w: swap_artifact(w, base_model_revision="main"),
        {"tokenizer__revision": "main"},
    )
    if ok is not None:
        check(
            "FACTS",
            "the server block carries the artifact's digest without its prefix and its revision",
            (
                ok.doc["server"]["weights_digest"] == WEIGHTS
                and ok.doc["server"]["base_revision"] == "main"
            ),
        )

    # --- catalog
    def with_entry(w: World, version: int = 3, artifact: Any = "same") -> uuid.UUID:
        entry_id = uuid.uuid4()
        mapped = w.artifact.id if artifact == "same" else artifact
        w.catalog[entry_id] = SimpleNamespace(
            id=entry_id, catalog_version=version, trainable_artifact_id=mapped
        )
        w.entry_id = entry_id
        return entry_id

    with sqlite_world() as w:
        entry = with_entry(w)
        created = w.create(
            body=w.body(base__model_catalog_entry_id=str(entry), base__catalog_version=3)
        )
        check(
            "FACTS",
            "a current mapping to the same artifact stores the entry id",
            (created.experiment.model_catalog_entry_id == entry),
        )
        check(
            "FACTS",
            "the mapping was read with the entry id and the request's version",
            (("require_current_mapping", w.session, w.ctx, entry, 3) in w.calls),
        )
        refuses(
            w,
            "a stale catalog version",
            "catalog_mapping_changed",
            lambda: w.create(
                key="k2",
                body=w.body(base__model_catalog_entry_id=str(entry), base__catalog_version=2),
            ),
            "FACTS",
        )
        refuses(
            w,
            "a catalog entry that does not exist",
            "catalog_mapping_changed",
            lambda: w.create(
                key="k3",
                body=w.body(
                    base__model_catalog_entry_id=str(uuid.uuid4()), base__catalog_version=3
                ),
            ),
            "FACTS",
        )
        other = with_entry(w, artifact=uuid.uuid4())
        refuses(
            w,
            "an entry mapped to another artifact",
            "catalog_artifact_mismatch",
            lambda: w.create(
                key="k4",
                body=w.body(base__model_catalog_entry_id=str(other), base__catalog_version=3),
            ),
            "FACTS",
        )
        unmapped = with_entry(w, artifact=None)
        refuses(
            w,
            "an entry mapped to no artifact",
            "catalog_artifact_mismatch",
            lambda: w.create(
                key="k5",
                body=w.body(base__model_catalog_entry_id=str(unmapped), base__catalog_version=3),
            ),
            "FACTS",
        )
    with sqlite_world() as w:
        w.create()
        check(
            "FACTS",
            "no catalog entry in the request reads no mapping",
            ("require_current_mapping" not in w.reader_calls()),
        )

    # --- split manifest
    fresh(
        "a manifest that does not exist",
        "split_manifest_not_locked",
        lambda w: setattr(w, "summary_error", "manifest_not_found"),
    )
    for status in ("proposed", "rejected"):
        fresh(
            f"a {status} manifest",
            "split_manifest_not_locked",
            lambda w, s=status: (
                w.manifest_values.update(status=s, frozen_manifest_sha256=None),
                setattr(w, "load_error", "manifest_not_locked"),
            ),
        )
    for code in (
        "manifest_not_locked",
        "leakage_not_clear",
        "quality_not_clear",
        "manifest_not_found",
    ):
        fresh(
            f"a locked manifest whose live read says {code}",
            "split_manifest_not_locked",
            lambda w, c=code: setattr(w, "load_error", c),
        )
    fresh(
        "a tampered manifest",
        "split_manifest_tampered",
        lambda w: setattr(w, "load_error", "manifest_tampered"),
    )
    fresh(
        "a changed quality report",
        "quality_report_changed",
        lambda w: setattr(w, "load_error", "quality_report_changed"),
    )
    fresh(
        "a changed grant",
        "dataset_grant_changed",
        lambda w: setattr(w, "load_error", "grant_changed"),
    )
    fresh(
        "a frozen hash that is not the request's",
        "split_manifest_changed",
        lambda w: w.manifest_values.update(frozen_manifest_sha256="e" * 64),
    )
    fresh(
        "a locked manifest with no frozen hash",
        "split_manifest_changed",
        lambda w: w.manifest_values.update(frozen_manifest_sha256=None),
    )
    fresh(
        "a live read of another frozen hash",
        "split_manifest_tampered",
        lambda w: w.view_over.update(frozen_manifest_sha256="e" * 64),
    )
    fresh(
        "a live read of another version",
        "split_manifest_tampered",
        lambda w: w.view_over.update(version=4),
    )
    fresh(
        "a live read serving fewer train rows than counted",
        "split_manifest_tampered",
        lambda w: setattr(w, "view_rows", ["train"] * 4 + ["validation"] * 2),
    )
    fresh(
        "a live read serving more validation rows than counted",
        "split_manifest_tampered",
        lambda w: setattr(w, "view_rows", ["train"] * 5 + ["validation"] * 3),
    )
    fresh(
        "a live read serving a final-qualification row",
        "split_manifest_tampered",
        lambda w: setattr(
            w, "view_rows", ["train"] * 5 + ["validation"] * 2 + ["final_qualification"]
        ),
    )
    fresh(
        "a manifest with no train role",
        "train_split_empty",
        lambda w: setattr(w, "roles", [r for r in w.roles if r.role != "train"]),
    )
    fresh(
        "a manifest with no validation role",
        "validation_split_empty",
        lambda w: setattr(w, "roles", [r for r in w.roles if r.role != "validation"]),
    )
    fresh(
        "an empty train role",
        "train_split_empty",
        lambda w: setattr(w.roles[0], "record_count", 0),
    )
    fresh(
        "an empty validation role",
        "validation_split_empty",
        lambda w: setattr(w.roles[1], "record_count", 0),
    )
    # order: the manifest state wins over a grant change, as in the pure module
    fresh(
        "a stale frozen hash on a tampered manifest",
        "split_manifest_changed",
        lambda w: (
            w.manifest_values.update(frozen_manifest_sha256="e" * 64),
            setattr(w, "load_error", "manifest_tampered"),
        ),
    )
    fresh(
        "held-out role on an unlocked manifest: the role refusal wins",
        "heldout_training_source_denied",
        lambda w: setattr(w, "load_error", "manifest_not_locked"),
        {"data__train__role": "final_qualification"},
    )
    # a refusal the store does not own propagates as itself, with nothing written
    for where, code in (("summary_error", "invalid_request"), ("load_error", "manifest_too_large")):
        with sqlite_world() as w:
            setattr(w, where, code)
            rows = w.count()
            got = code_of(lambda: w.create())
            check(
                "FACTS",
                f"an unowned split refusal ({code}) propagates as SplitManifestError",
                (
                    got == f"SplitManifestError: {code}"
                    and w.count() == rows
                    and w.audit.events == []
                ),
            )
    ok = creates("the default manifest", lambda w: None)
    if ok is not None:
        check(
            "FACTS",
            "the server block carries the manifest's own version, hash, counts and role hashes",
            {
                k: ok.doc["server"][k]
                for k in (
                    "split_manifest_version",
                    "frozen_manifest_sha256",
                    "train_count",
                    "validation_count",
                    "train_role_sha256",
                    "validation_role_sha256",
                )
            }
            == {
                "split_manifest_version": 3,
                "frozen_manifest_sha256": SHA_FROZEN,
                "train_count": 5,
                "validation_count": 2,
                "train_role_sha256": SHA_TRAIN,
                "validation_role_sha256": SHA_VAL,
            },
        )
        check(
            "FACTS",
            "the summary is read before the live training read",
            (
                [
                    c
                    for c in (x[0] for x in ok.calls)
                    if c in ("get_split_manifest", "load_training_split")
                ]
                == ["get_split_manifest", "load_training_split"]
            ),
        )
    with sqlite_world() as w:
        w.summary_error = "manifest_not_found"
        code_of(lambda: w.create())
        check(
            "FACTS",
            "a missing manifest is not read live",
            "load_training_split" not in w.reader_calls(),
        )

    # --- budget
    fresh("no budget policy", "budget_policy_missing", lambda w: setattr(w, "policy", None))
    fresh(
        "a policy without the training scope",
        "budget_scope_not_authorized",
        lambda w: setattr(w.policy, "scopes_json", ["evaluation", "serving"]),
    )
    fresh(
        "a policy in another currency",
        "budget_currency_mismatch",
        lambda w: setattr(w.policy, "currency", "EUR"),
    )
    fresh(
        "max_cost above total - reserved",
        "budget_limit_exceeds_policy",
        lambda w: setattr(w.policy, "reserved_amount", Decimal("989.5001")),
    )
    ok = creates(
        "max_cost equal to total - reserved",
        lambda w: setattr(w.policy, "reserved_amount", Decimal("989.5000")),
    )
    if ok is not None:
        check(
            "FACTS",
            "the server block carries the policy id and row version",
            (
                ok.doc["server"]["budget_policy_id"] == str(ok.world.policy.id)
                and ok.doc["server"]["budget_policy_row_version"] == 4
                and ok.experiment.budget_policy_id == ok.world.policy.id
            ),
        )
    fresh(
        "max_cost above the total",
        "budget_limit_exceeds_policy",
        lambda w: None,
        {"budget__max_cost": "1000.0001"},
    )
    creates("max_cost equal to the total", lambda w: None, {"budget__max_cost": "1000"})

    # --- security profile: the more restrictive of the organization default and the project's
    for org_default, project, expected in (
        ("standard", "standard", "standard"),
        ("zero_egress", "standard", "zero_egress"),
        ("standard", "zero_egress", "zero_egress"),
        ("air_gapped", "zero_egress", "air_gapped"),
        ("zero_egress", "air_gapped", "air_gapped"),
    ):
        ok = creates(
            f"profiles {org_default}/{project}",
            lambda w, o=org_default, p=project: (
                w.set_default_profile(ORG1, o),
                w.set_project_profile(p),
            ),
        )
        if ok is not None:
            check(
                "FACTS",
                f"effective profile for {org_default}/{project} is {expected}",
                (
                    ok.doc["server"]["security_profile"] == expected
                    and ok.experiment.security_profile == expected
                ),
            )
    ok = creates(
        "another organization's default does not leak in",
        lambda w: w.set_default_profile(ORG2, "air_gapped"),
    )
    if ok is not None:
        check(
            "FACTS",
            "the default read is the caller's organization's",
            (ok.experiment.security_profile == "standard"),
        )


class _Diag:
    def __init__(self, name: Any) -> None:
        self.constraint_name = name


class _Orig(Exception):
    def __init__(self, name: Any, *, diag: bool = True) -> None:
        super().__init__(f"{CANARY} database detail")
        if diag:
            self.diag = _Diag(name)


def part_s_conflicts() -> None:
    def failing_flush(w: World, orig: Any) -> Any:
        real = w.session.flush
        state = {"armed": True}

        def flush(*args: Any, **kwargs: Any) -> Any:
            if state["armed"] and any(
                isinstance(o, store.TrainingExperiment) for o in w.session.new
            ):
                state["armed"] = False
                raise IntegrityError("INSERT", {}, orig)
            return real(*args, **kwargs)

        return flush

    for constraint, code in (
        ("uq_training_experiments_project_name_version", "version_conflict"),
        ("tr_training_experiments_manifest_locked", "split_manifest_not_locked"),
        ("tr_training_experiments_manifest_frozen", "split_manifest_changed"),
        ("tr_training_experiments_artifact_ready", "artifact_not_trainable"),
        ("tr_training_experiments_budget_project", "budget_policy_missing"),
        ("ck_training_experiments_config_consistency", "invalid_source_role"),
    ):
        with sqlite_world() as w:
            w.session.flush = failing_flush(w, _Orig(constraint))
            try:
                w.create()
                outcome: Any = None
            except store.TrainingExperimentError as exc:
                outcome = (exc.code, exc.__cause__, exc.__suppress_context__, str(exc))
            except BaseException as exc:  # noqa: BLE001
                outcome = repr(exc)
            del w.session.flush
            check(
                "CONFLICT",
                f"{constraint} -> {code}, code-only, no chained database text",
                (outcome == (code, None, True, code)),
            )
            check(
                "CONFLICT",
                f"{constraint}: no audit event and no row",
                (w.audit.events == [] and w.count() == 0),
            )
    for label, orig in (
        ("an unknown constraint", _Orig("ck_training_experiments_max_cost")),
        ("no constraint name", _Orig(None)),
        ("a non-string constraint name", _Orig(7)),
        ("no diagnostics", _Orig("x", diag=False)),
    ):
        with sqlite_world() as w:
            w.session.flush = failing_flush(w, orig)
            got = code_of(lambda: w.create())
            del w.session.flush
            check(
                "CONFLICT",
                f"{label} propagates as the database error itself ({got})",
                (
                    got is not None
                    and got.startswith("IntegrityError")
                    and w.audit.events == []
                    and w.count() == 0
                ),
            )
    check(
        "CONFLICT",
        "_violated_name reads the diagnostics",
        (
            store._violated_name(IntegrityError("x", {}, _Orig("abc"))) == "abc"
            and store._violated_name(IntegrityError("x", {}, _Orig(None))) is None
            and store._violated_name(IntegrityError("x", {}, _Orig(7))) is None
            and store._violated_name(IntegrityError("x", {}, _Orig("abc", diag=False))) is None
        ),
    )

    # the idempotency index: a racing request with the same key
    def race(other_body: bool) -> tuple[Any, World]:
        with sqlite_world() as w:
            winner = w.create()
            events = len(w.audit.events)
            real_find = store._find_by_idempotency_key
            seen = {"calls": 0}

            def late_find(session: Session, context: TenantContext, actor_id: str, key: str) -> Any:
                seen["calls"] += 1
                if seen["calls"] == 1:
                    return None  # the racing request had not committed yet
                return real_find(session, context, actor_id, key)

            store._find_by_idempotency_key = late_find
            w.session.flush = failing_flush(w, _Orig("uq_training_experiments_idempotency"))
            try:
                body = w.body(hyperparameters__epochs=4) if other_body else w.body()
                try:
                    result: Any = w.create(body=body)
                except store.TrainingExperimentError as exc:
                    result = exc.code
            finally:
                store._find_by_idempotency_key = real_find
                del w.session.flush
            return (result, winner, events, len(w.audit.events), w.count(), seen["calls"]), w

    (result, winner, before, after, rows, calls), _ = race(other_body=False)
    check(
        "CONFLICT",
        "a lost idempotency race with the same request replays the winner",
        (
            not isinstance(result, str)
            and result.replayed is True
            and result.experiment.id == winner.experiment.id
            and (before, rows, calls) == (after, 1, 2)
        ),
    )
    (result, winner, before, after, rows, calls), _ = race(other_body=True)
    check(
        "CONFLICT",
        "a lost idempotency race with another body is idempotency_key_reused",
        (result == "idempotency_key_reused" and (before, rows) == (after, 1)),
    )
    with sqlite_world() as w:
        w.session.flush = failing_flush(w, _Orig("uq_training_experiments_idempotency"))
        got = code_of(lambda: w.create())
        del w.session.flush
        check(
            "CONFLICT",
            "an idempotency violation with no stored row propagates as itself",
            (got is not None and got.startswith("IntegrityError") and w.audit.events == []),
        )


def part_s_reads() -> None:
    with sqlite_world() as w:
        made = [
            w.create(name=name, key=f"k-{index}", body=w.body(hyperparameters__epochs=epochs))
            for index, (name, epochs) in enumerate(
                (("b", 3), ("a", 3), ("b", 4), ("c", 3), ("a", 4), ("B", 3), ("a", 5))
            )
        ]
        w.session.flush()
        first = made[0].experiment
        got = store.get_training_experiment(w.session, w.ctx, w.project_id, first.id)
        check(
            "READ",
            "get returns the row",
            got.id == first.id and got.name == "b" and got.version == 1,
        )
        for label, call in (
            (
                "a missing id",
                lambda: store.get_training_experiment(w.session, w.ctx, w.project_id, uuid.uuid4()),
            ),
            (
                "another project",
                lambda: store.get_training_experiment(w.session, w.ctx, w.other_project, first.id),
            ),
            (
                "another organization",
                lambda: store.get_training_experiment(w.session, w.ctx2, w.project_id, first.id),
            ),
            (
                "another organization's project",
                lambda: store.get_training_experiment(
                    w.session, w.ctx2, w.foreign_project, first.id
                ),
            ),
        ):
            check("READ", f"get {label}: not_found", code_of(call) == "not_found")

        def listing(**kwargs: Any) -> list[tuple[str, int]]:
            kwargs.setdefault("limit", 200)
            rows = store.list_training_experiments(w.session, w.ctx, w.project_id, **kwargs)
            return [(row.name, row.version) for row in rows]

        everything = [("B", 1), ("a", 1), ("a", 2), ("a", 3), ("b", 1), ("b", 2), ("c", 1)]
        check("READ", f"list is ordered by name then version: {listing()}", listing() == everything)
        check("READ", "limit 1 returns the first", listing(limit=1) == everything[:1])
        check("READ", "limit 3 returns the first three", listing(limit=3) == everything[:3])
        check(
            "READ",
            "after continues strictly past the cursor",
            (listing(after=("a", 2)) == everything[3:]),
        )
        check(
            "READ",
            "after the same name's last version moves to the next name",
            (listing(after=("a", 3)) == everything[4:]),
        )
        check("READ", "after a name between rows", listing(after=("aa", 0)) == everything[4:])
        check("READ", "after the last row is empty", listing(after=("c", 1)) == [])
        check("READ", "after with a limit", listing(after=("B", 1), limit=2) == everything[1:3])
        pages: list[tuple[str, int]] = []
        cursor: Any = None
        for _ in range(10):
            page = listing(limit=2, after=cursor)
            if not page:
                break
            pages += page
            cursor = page[-1]
        check("READ", "paging by two visits every row exactly once", pages == everything)
        for label, kwargs in (
            ("limit 0", {"limit": 0}),
            ("limit -1", {"limit": -1}),
            ("limit 201", {"limit": 201}),
            ("limit True", {"limit": True}),
            ("limit 1.0", {"limit": 1.0}),
            ("limit '5'", {"limit": "5"}),
            ("after a string", {"after": "a"}),
            ("after a list", {"after": ["a", 1]}),
            ("after a 1-tuple", {"after": ("a",)}),
            ("after a 3-tuple", {"after": ("a", 1, 2)}),
            ("after (int, int)", {"after": (1, 1)}),
            ("after (str, str)", {"after": ("a", "1")}),
            ("after (str, bool)", {"after": ("a", True)}),
        ):
            got_error = code_of(lambda k=kwargs: listing(**k))
            check(
                "READ",
                f"list {label} is a ValueError ({got_error})",
                (got_error is not None and got_error.startswith("ValueError: ")),
            )
        check("READ", "limit 200 is accepted", listing(limit=200) == everything)
        check(
            "READ",
            "list of another project of the organization is empty",
            (store.list_training_experiments(w.session, w.ctx, w.other_project, limit=5) == []),
        )
        for label, call in (
            (
                "a missing project",
                lambda: store.list_training_experiments(w.session, w.ctx, uuid.uuid4(), limit=5),
            ),
            (
                "another organization's project",
                lambda: store.list_training_experiments(
                    w.session, w.ctx, w.foreign_project, limit=5
                ),
            ),
            (
                "this project under another organization",
                lambda: store.list_training_experiments(w.session, w.ctx2, w.project_id, limit=5),
            ),
        ):
            check("READ", f"list {label}: not_found", code_of(call) == "not_found")
        # a row of another organization inside this project id never appears
        stray = store.TrainingExperiment(
            **{
                c.name: getattr(first, c.name)
                for c in store.TrainingExperiment.__table__.columns
                if c.name not in ("id", "organization_id", "name")
            },
            id=uuid.uuid4(),
            organization_id=ORG2,
            name="stray",
        )
        w.session.add(stray)
        w.session.flush()
        check("READ", "list filters by organization", listing() == everything)
        check(
            "READ",
            "get filters by organization",
            (
                code_of(
                    lambda: store.get_training_experiment(w.session, w.ctx, w.project_id, stray.id)
                )
                == "not_found"
            ),
        )

        # integrity
        target = made[1].experiment  # ("a", 1)
        good_doc, good_sha = target.config_json, target.config_sha256
        for label, doc, sha in (
            ("an edited value", {**good_doc, "method": "qlora"}, good_sha),
            ("a dropped key", {k: v for k, v in good_doc.items() if k != "server"}, good_sha),
            ("an added key", {**good_doc, "extra": 1}, good_sha),
            ("another hash", good_doc, "0" * 64),
            ("an upper-case hash", good_doc, good_sha.upper()),
            ("a hash prefix", good_doc, good_sha[:63]),
            ("an empty document", {}, good_sha),
        ):
            target.config_json, target.config_sha256 = doc, sha
            w.session.flush()
            check(
                "INTEGRITY",
                f"get refuses {label}",
                (
                    code_of(
                        lambda: store.get_training_experiment(
                            w.session, w.ctx, w.project_id, target.id
                        )
                    )
                    == "config_integrity_failed"
                ),
            )
            check(
                "INTEGRITY",
                f"list refuses a page holding {label}",
                (code_of(lambda: listing()) == "config_integrity_failed"),
            )
            check(
                "INTEGRITY",
                f"a page before the row with {label} is still served",
                (listing(limit=1) == everything[:1]),
            )
        target.config_json, target.config_sha256 = good_doc, good_sha
        w.session.flush()
        check(
            "INTEGRITY",
            "the restored row is served",
            (
                store.get_training_experiment(w.session, w.ctx, w.project_id, target.id).id
                == target.id
                and listing() == everything
            ),
        )
    # _verified on values the column would never hold
    for label, doc, sha in (
        ("a non-string hash", {"a": 1}, None),
        ("an unserializable document", {"a": object()}, "0" * 64),
        ("a cyclic document", None, "0" * 64),
    ):
        if doc is None:
            doc = {}
            doc["self"] = doc
        row = SimpleNamespace(config_json=doc, config_sha256=sha)
        check(
            "INTEGRITY",
            f"_verified refuses {label}",
            (code_of(lambda r=row: store._verified(r)) == "config_integrity_failed"),
        )
    honest = SimpleNamespace(
        config_json={"a": 1}, config_sha256=schema.compute_config_sha256({"a": 1})
    )
    check(
        "INTEGRITY",
        "_verified returns the same row when the hash matches",
        store._verified(honest) is honest,
    )


def part_s_leak() -> None:
    with sqlite_world() as w:
        chains: list[str] = []
        for label, call in (
            (
                "target module",
                lambda: w.create(body=w.body(adapter__target_modules=[f"{CANARY} x"])),
            ),
            ("revision", lambda: w.create(body=w.body(tokenizer__revision=f"{CANARY}"))),
            ("max_cost", lambda: w.create(body=w.body(budget__max_cost=CANARY))),
            ("unknown key", lambda: w.create(body={**w.body(), CANARY: 1})),
            ("name", lambda: w.create(name=f" {CANARY}")),
            ("key", lambda: w.create(key=f"{CANARY}\n")),
            ("missing artifact", lambda: (w.artifacts.clear(), w.create())),
        ):
            try:
                call()
                check("LEAK", f"{label} was refused", False)
            except BaseException as exc:  # noqa: BLE001
                cursor: BaseException | None = exc
                while cursor is not None:
                    chains.append(f"{cursor!s}|{cursor!r}|{getattr(cursor, 'args', ())!r}")
                    cursor = cursor.__cause__ or (
                        None if cursor.__suppress_context__ else cursor.__context__
                    )
        check("LEAK", "no canary in any refusal or its chain", not any(CANARY in c for c in chains))
    with sqlite_world() as w:
        w.create(
            name=f"exp {CANARY}",
            body=w.body(adapter__target_modules=[f"{CANARY}_proj"], tokenizer__revision="rev1"),
        )
        check("LEAK", "no canary in the audit event", CANARY not in repr(w.audit.events))


def main() -> None:
    part_m()
    part_m_sql()
    part_s_constants()
    part_s_create()
    part_s_authorization()
    part_s_request_values()
    part_s_idempotency()
    part_s_facts()
    part_s_conflicts()
    part_s_reads()
    part_s_leak()
    if FAILURES:
        for failure in FAILURES[:80]:
            print(failure, file=sys.stderr)
        print(f"{len(FAILURES)} failure(s) of {CHECKS[0]} checks", file=sys.stderr)
        sys.exit(1)
    print(f"OK {CID}: {CHECKS[0]} checks")


if __name__ == "__main__":
    main()
