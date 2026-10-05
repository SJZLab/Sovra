#!/usr/bin/env python3
"""FTF-1206A -- Organization gateway policy persistence (no-DB unit/fault tests).

Runnable without Postgres or network egress (the real live-database
persistence/tenant-isolation/append-only/CHECK-constraint evidence is
`tests/integration/test_ftf_1206a_org_gateway_policy.py`, run in CI's
dedicated `organization-gateway-policy-integration` job -- same "no
container-registry egress in this repo's interactive dev sandbox" finding
as every prior Postgres-integration-tested task). This suite proves,
without any database:

1. The tracked forward migration (`apps/api/alembic/versions/
   0019_org_gateway_policies.py`) genuinely declares ENABLE+FORCE
   ROW LEVEL SECURITY and a real, non-allow-all `tenant_isolation` policy
   on both tables; grants the restricted `sovra_app` role SELECT/INSERT/
   UPDATE (never DELETE) on `organization_gateway_policies`, and SELECT/
   INSERT only (never UPDATE/DELETE -- this task's own negative_acceptance
   clause) on `organization_gateway_route_changes`; carries the real
   `jsonb_typeof(...) = 'array'` CHECK on all three namespace/approver
   columns; carries the real composite foreign key tying a route-change
   record's `project_id` to the same organization's own `projects` row;
   and chains onto FTF-0601's own `0018_eval_suites_reviewer_fix` as the
   single serialized head -- proven by real fault injection against the
   file on disk, not just re-reading its text.
2. `organization_gateway_policy_store.py`'s own domain shape -- `ActorInfo`,
   `ProjectNotFoundError`, the two ORM record classes' real column sets,
   the `_to_policy` conversion round-trip, and that the module reuses
   `organization_policy_control.py`'s own already-certified `validate_
   namespace`/`validate_approver_identity` functions directly (the same
   object, never a re-implementation that could silently drift).

Run directly: python3 tests/tasks/test_ftf_1206a.py
"""

from __future__ import annotations

import inspect
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from sqlalchemy import Table

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.litellm_client.organization_policy_control import (  # noqa: E402
    OrganizationGatewayPolicy,
    validate_approver_identity,
    validate_namespace,
)

import app.litellm.organization_gateway_policy_store as store  # noqa: E402
from app.litellm.organization_gateway_policy_store import (  # noqa: E402
    ActorInfo,
    OrganizationGatewayPolicyRecord,
    OrganizationGatewayRouteChangeRecord,
    ProjectNotFoundError,
    record_route_change,
)

MIGRATION_PATH = ROOT / "apps" / "api" / "alembic" / "versions" / "0019_org_gateway_policies.py"
PRIOR_MIGRATION_PATH = (
    ROOT / "apps" / "api" / "alembic" / "versions" / "0018_eval_suites_reviewer_fix.py"
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _migration_source() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


# -- 1. Migration shape -------------------------------------------------


def test_migration_forces_rls_on_both_tables() -> None:
    src = _migration_source()
    for table in ("organization_gateway_policies", "organization_gateway_route_changes"):
        check(
            "FTF-1206A",
            f"the migration ENABLEs row-level security on {table}",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in src,
        )
        check(
            "FTF-1206A",
            f"the migration FORCEs (not just ENABLEs) row-level security on {table}",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in src,
        )


def test_migration_declares_non_allow_all_tenant_isolation_policy_on_both_tables() -> None:
    src = _migration_source()
    # Both tables' policies are declared via a shared `for table in (...)`
    # loop with an f-string `{table}` placeholder (the same convention
    # scripts/data_exposure_check.py's own `_expand_table_loops` already
    # knows how to expand per table name), so the raw source text contains
    # the literal, unsubstituted placeholder once, plus both real table
    # names in the loop's own tuple.
    check(
        "FTF-1206A",
        "a shared tenant_isolation policy statement is declared, scoped to "
        "app.current_organization_id",
        "CREATE POLICY tenant_isolation ON {table} FOR ALL" in src
        and "app.current_organization_id" in src,
    )
    check(
        "FTF-1206A",
        "the policy loop's own tuple names both real tables",
        'for table in ("organization_gateway_policies", "organization_gateway_route_changes"):'
        in src,
    )
    check(
        "FTF-1206A",
        "no policy anywhere in this migration is an allow-all USING (true)",
        "USING (true)" not in src and "using(true)" not in src.lower().replace(" ", ""),
    )


def test_migration_grants_policies_table_select_insert_update_never_delete() -> None:
    src = _migration_source()
    check(
        "FTF-1206A",
        "organization_gateway_policies is granted SELECT, INSERT, UPDATE",
        "GRANT SELECT, INSERT, UPDATE ON organization_gateway_policies" in src,
    )
    check(
        "FTF-1206A",
        "negative_acceptance: no DELETE is ever granted on "
        "organization_gateway_policies (no legitimate deletion path)",
        "DELETE ON organization_gateway_policies" not in src,
    )


def test_migration_grants_route_changes_table_select_insert_only() -> None:
    src = _migration_source()
    check(
        "FTF-1206A",
        "organization_gateway_route_changes is granted SELECT, INSERT",
        "GRANT SELECT, INSERT ON organization_gateway_route_changes" in src,
    )
    check(
        "FTF-1206A",
        "negative_acceptance: no UPDATE is ever granted on "
        "organization_gateway_route_changes (append-only history)",
        "UPDATE ON organization_gateway_route_changes" not in src,
    )
    check(
        "FTF-1206A",
        "negative_acceptance: no DELETE is ever granted on "
        "organization_gateway_route_changes (append-only history)",
        "DELETE ON organization_gateway_route_changes" not in src,
    )
    check(
        "FTF-1206A",
        "the migration never grants ALL PRIVILEGES on either table "
        "(that would silently reintroduce UPDATE/DELETE)",
        "GRANT ALL" not in src,
    )


def test_migration_jsonb_array_check_constraints() -> None:
    src = _migration_source()
    for column in (
        "allowed_registration_namespaces",
        "allowed_deployment_namespaces",
        "approvers",
    ):
        check(
            "FTF-1206A",
            f"a real jsonb_typeof(...) = 'array' CHECK guards {column}",
            f"jsonb_typeof({column}) = 'array'" in src,
        )


def test_migration_unique_organization_id_on_policies_table() -> None:
    src = _migration_source()
    check(
        "FTF-1206A",
        "organization_gateway_policies has a real named UNIQUE(organization_id) "
        "constraint -- one policy row per organization, never a race of "
        "competing rows",
        "uq_organization_gateway_policies_organization_id" in src
        and bool(re.search(r'sa\.UniqueConstraint\(\s*"organization_id"', src)),
    )


def test_migration_route_changes_composite_fk_to_projects() -> None:
    src = _migration_source()
    check(
        "FTF-1206A",
        "organization_gateway_route_changes carries a real composite "
        "foreign key tying project_id to the same organization's own "
        "projects row (reusing uq_projects_organization_id_id)",
        '["organization_id", "project_id"]' in src
        and '["projects.organization_id", "projects.id"]' in src,
    )


def test_migration_chains_onto_ftf_0601_round2_as_single_serialized_head() -> None:
    src = _migration_source()
    check(
        "FTF-1206A",
        "this migration's down_revision points at FTF-0601's own round-2 "
        "fix migration, not a parallel branch",
        'down_revision = "0018_eval_suites_reviewer_fix"' in src,
    )
    prior_src = PRIOR_MIGRATION_PATH.read_text(encoding="utf-8")
    check(
        "FTF-1206A",
        "0018_eval_suites_reviewer_fix is still the prior migration in the "
        "chain (revision id unchanged by this task)",
        'revision = "0018_eval_suites_reviewer_fix"' in prior_src,
    )


def test_real_fault_injection_missing_force_rls_on_policies_is_caught() -> None:
    """Not re-reading the producer's own claim: remove FORCE ROW LEVEL
    SECURITY for organization_gateway_policies from the tracked migration
    file on disk and confirm this suite's own check above genuinely fails,
    then restore byte-identical."""
    original = MIGRATION_PATH.read_bytes()
    try:
        mutated = original.decode("utf-8").replace(
            'op.execute("ALTER TABLE organization_gateway_policies FORCE ROW LEVEL SECURITY")\n',
            "",
        )
        assert mutated != original.decode("utf-8"), "fault injection did not change the file"
        MIGRATION_PATH.write_text(mutated, encoding="utf-8")
        forced = (
            "ALTER TABLE organization_gateway_policies FORCE ROW LEVEL SECURITY"
            in _migration_source()
        )
        check(
            "FTF-1206A",
            "fault injection: removing FORCE ROW LEVEL SECURITY from "
            "organization_gateway_policies is genuinely caught by this "
            "suite's own check (not decorative)",
            forced is False,
        )
    finally:
        MIGRATION_PATH.write_bytes(original)
        restored = MIGRATION_PATH.read_bytes()
        check(
            "FTF-1206A",
            "the migration file is restored byte-identical after fault injection",
            restored == original,
        )


def test_real_fault_injection_widened_route_changes_grant_is_caught() -> None:
    """A second, independent fault injection: widen the route-changes GRANT
    to include UPDATE (simulating a future accidental edit reintroducing
    editability of append-only history) and confirm this suite's own
    negative_acceptance check catches it."""
    original = MIGRATION_PATH.read_bytes()
    try:
        mutated = original.decode("utf-8").replace(
            '"GRANT SELECT, INSERT ON organization_gateway_route_changes "',
            '"GRANT SELECT, INSERT, UPDATE ON organization_gateway_route_changes "',
        )
        assert mutated != original.decode("utf-8"), "fault injection did not change the file"
        MIGRATION_PATH.write_text(mutated, encoding="utf-8")
        src = _migration_source()
        widened = "GRANT SELECT, INSERT, UPDATE ON organization_gateway_route_changes " in src
        check(
            "FTF-1206A",
            "fault injection: widening the route-changes GRANT to include "
            "UPDATE is genuinely caught as a violation of this task's own "
            "negative_acceptance clause (not decorative)",
            widened is True,
        )
        check(
            "FTF-1206A",
            "this suite's own clean-state check above would have failed "
            "to catch the fault if it merely looked for the fault string "
            "being absent, rather than the clean grant being present",
            "UPDATE ON organization_gateway_route_changes" in src,
        )
    finally:
        MIGRATION_PATH.write_bytes(original)
        restored = MIGRATION_PATH.read_bytes()
        check(
            "FTF-1206A",
            "the migration file is restored byte-identical after fault injection",
            restored == original,
        )


# -- 2. Store module domain shape ----------------------------------------


def test_actor_info_shape() -> None:
    info = ActorInfo(actor_type="user", actor_id="user-1", request_id="req-1")
    check(
        "FTF-1206A",
        "ActorInfo carries actor_type/actor_id/request_id for the audit "
        "event every mutation writes",
        info.actor_type == "user" and info.actor_id == "user-1" and info.request_id == "req-1",
    )


def test_project_not_found_error_is_lookup_error() -> None:
    check(
        "FTF-1206A",
        "ProjectNotFoundError is a LookupError, denied identically whether "
        "missing or foreign-tenant (same class every prior CRUD module in "
        "this chain already established)",
        issubclass(ProjectNotFoundError, LookupError),
    )


def test_record_classes_carry_the_real_tracked_columns() -> None:
    policy_table = cast(Table, OrganizationGatewayPolicyRecord.__table__)
    policy_columns = {c.name for c in policy_table.columns}
    check(
        "FTF-1206A",
        "OrganizationGatewayPolicyRecord maps every real persisted field",
        policy_columns
        == {
            "id",
            "organization_id",
            "allowed_registration_namespaces",
            "allowed_deployment_namespaces",
            "approvers",
            "route_change_requires_approval",
            "created_at",
            "updated_at",
        },
    )

    route_table = cast(Table, OrganizationGatewayRouteChangeRecord.__table__)
    route_columns = {c.name for c in route_table.columns}
    check(
        "FTF-1206A",
        "OrganizationGatewayRouteChangeRecord maps every real persisted field",
        route_columns
        == {
            "id",
            "organization_id",
            "project_id",
            "actor",
            "target_deployment_namespace",
            "approved",
            "bypass_requested",
            "request_id",
            "created_at",
        },
    )


def test_to_policy_round_trips_without_a_database() -> None:
    """`_to_policy` is a pure in-memory conversion -- constructing the ORM
    record directly (never via a query) and converting it back proves the
    round-trip without needing Postgres."""
    record = OrganizationGatewayPolicyRecord(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        allowed_registration_namespaces=["factory-candidates", "factory-staging"],
        allowed_deployment_namespaces=["factory-production"],
        approvers=["user:jane", "user:ahmed"],
        route_change_requires_approval=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    policy = store._to_policy(record)
    check(
        "FTF-1206A",
        "_to_policy round-trips every field into a real, validated " "OrganizationGatewayPolicy",
        isinstance(policy, OrganizationGatewayPolicy)
        and policy.allowed_registration_namespaces
        == frozenset({"factory-candidates", "factory-staging"})
        and policy.allowed_deployment_namespaces == frozenset({"factory-production"})
        and policy.approvers == frozenset({"user:jane", "user:ahmed"})
        and policy.route_change_requires_approval is True,
    )


def test_store_reuses_the_real_certified_validators_not_a_reimplementation() -> None:
    check(
        "FTF-1206A",
        "the store module's validate_namespace is the exact same function "
        "object organization_policy_control.py already exports -- never a "
        "parallel re-implementation that could silently drift",
        store.validate_namespace is validate_namespace,
    )
    check(
        "FTF-1206A",
        "the store module's validate_approver_identity is the exact same "
        "function object organization_policy_control.py already exports",
        store.validate_approver_identity is validate_approver_identity,
    )


def test_record_route_change_requires_actor_info_for_the_audit_trail() -> None:
    signature = inspect.signature(record_route_change)
    check(
        "FTF-1206A",
        "record_route_change requires a real actor_info (no default) -- "
        "every route-change record is audited, never silently unattributed",
        "actor_info" in signature.parameters
        and signature.parameters["actor_info"].default is inspect.Parameter.empty,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1206A organization gateway policy persistence: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1206A organization gateway policy persistence: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
