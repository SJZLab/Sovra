#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,union-attr,operator,no-untyped-def,no-untyped-call,var-annotated,misc"
# ruff: noqa: E501
"""FTF-R004-P3 -- real-service proof of the Inspect runtime bridge.

Real services: the pinned `inspect_ai` (model-less `none/none`), the FTF-04A26
`IsolatedToolBackendAdapter` over the real FTF-1702 reference tool backend, and a live PostgreSQL
server for the case read (never SQLite: it has no row-level security).

Proves, end to end:

  - a locked development suite is read through the tenant-scoped FTF-0602-P1 store under the
    restricted `sovra_app` role, its cases become Inspect Samples (id = case_key, metadata, frozen
    inputs), and the suite runs through the native adapter: completed cases carry a business
    outcome independently re-read from the real backend, a cross-tenant `get_record` is a real
    REFUSAL, an unseeded subject is a preflight refusal, and the claimed note side effect is
    observable in the backend;
  - a draft development suite -> `suite_not_locked`, a final suite (locked or draft) ->
    `final_suite_requires_freeze` with NO query ever touching `evaluation_cases`, another
    organization's suite id is the same not-found as a random id, and an unlocked suite never
    reaches Inspect;
  - the Inspect log is a real local file in a private directory, its sha256 matches, its plan is
    exactly the native solver, and re-reading the adapter's identity after the run is unchanged;
  - the whole run (database read excluded) executes with the socket guard installed and makes
    0 outbound attempts.

Requires `SOVRA_DATABASE_URL` (migration owner) and `SOVRA_APP_DB_PASSWORD`.

Run directly:
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_inspect_target_bridge.py
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from sqlalchemy import create_engine, event, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from workers.evaluation.adapters.inspect_adapter.case_adapter import (  # noqa: E402
    CaseAdapterError,
    load_locked_development_suite,
)
from workers.evaluation.adapters.inspect_adapter.provider_boundary import (  # noqa: E402
    ScreeningPolicy,
)
from workers.evaluation.adapters.inspect_adapter.runtime_bridge import (  # noqa: E402
    BridgeHook,
    run_suite_through_runtime,
)

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import Role  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.evaluation.evaluation_case_store import import_cases, validate_case_input  # noqa: E402
from app.evaluation.evaluation_suite_crud import EvaluationSuiteNotFoundError  # noqa: E402
from app.evaluation.evaluation_suite_schema import EvaluationSuite  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.target_connectors.agent_manifest_schema import AgentManifest  # noqa: E402
from app.target_connectors.isolated_real_tool_backend_adapter_contract_tests import (  # noqa: E402
    build_isolated_tool_backend_adapter,
)
from app.target_connectors.production_faithful_target_agent_harness_contract import (  # noqa: E402
    ModelBinding,
)
from app.target_connectors.target_application_connection_schema import (  # noqa: E402
    TargetApplicationConnection,
)
from app.target_connectors.target_source_snapshot_schema import TargetSourceSnapshot  # noqa: E402

CID = "FTF-R004-P3"
FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real evidence requires a live "
            "Postgres (migration owner connection). Not silently skipped: see this file's "
            "docstring for how to run it locally or in CI.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def _app_role_url(migration_url: str, app_password: str) -> str:
    parts = urlsplit(migration_url)
    netloc = f"{APP_ROLE_NAME}:{app_password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _alembic(args: list[str], database_url: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _raw(
    key: str, tool: str, subject: str, arguments: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    case: dict[str, Any] = {
        "case_key": key,
        "category": "retrieval",
        "severity": "high",
        "language": "en",
        "group_key": f"group-{key}",
        "input_json": {"tool_name": tool, "subject_id": subject, "arguments": arguments},
        "expected_json": {"expect": "anything"},
        "grader_type": "tool_name",
    }
    case.update(extra)
    return case


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    upgrade = _alembic(["upgrade", "head"], migration_url)
    check(
        CID,
        f"alembic upgrade head runs cleanly\n{upgrade.stdout}\n{upgrade.stderr}",
        upgrade.returncode == 0,
    )
    if FAILURES:
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))
    session_factory = sessionmaker(bind=app_engine)

    statements: list[str] = []

    @event.listens_for(app_engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    # -- seed two tenants, a locked manifest each ------------------------------------------------
    now = datetime.now(UTC)
    suffix = uuid.uuid4().hex[:8]
    with Session(owner_engine) as session:
        orgs = [
            Organization(name="Acme R004P3", slug=f"acme-r004p3-{suffix}"),
            Organization(name="Globex R004P3", slug=f"globex-r004p3-{suffix}"),
        ]
        session.add_all(orgs)
        session.flush()
        workspaces = [Workspace(organization_id=o.id, name="Main", slug="main") for o in orgs]
        session.add_all(workspaces)
        session.flush()
        projects = [
            Project(
                organization_id=o.id,
                workspace_id=w.id,
                name=f"proj-{i}",
                project_type="agentic_product",
                status="active",
                security_profile="standard",
                row_version=1,
                created_by="seed",
            )
            for i, (o, w) in enumerate(zip(orgs, workspaces, strict=True))
        ]
        session.add_all(projects)
        session.flush()
        connections = [
            TargetApplicationConnection(
                organization_id=o.id,
                project_id=p.id,
                connector_type="local_repository",
                display_name=f"repo-{i}",
                repository_identity=f"org{i}/primary",
                access_mode="read_only",
                connection_status="active",
                last_validated_at=now,
                created_by="seed",
            )
            for i, (o, p) in enumerate(zip(orgs, projects, strict=True))
        ]
        session.add_all(connections)
        session.flush()
        snapshots = [
            TargetSourceSnapshot(
                organization_id=o.id,
                target_application_connection_id=c.id,
                commit_sha=str(i + 1) * 40,
                tree_hash=str(i + 3) * 40,
                capture_method="github_resolved_commit",
            )
            for i, (o, c) in enumerate(zip(orgs, connections, strict=True))
        ]
        session.add_all(snapshots)
        session.flush()
        manifests = [
            AgentManifest(
                organization_id=o.id,
                project_id=p.id,
                target_source_snapshot_id=s.id,
                version=1,
                status="locked",
                agent_name="Agent",
                agent_entrypoint_json={"entry": "src/agent.py"},
                current_model_binding_json={"model": "gpt-x"},
                runtime_dependencies_json={"python": "3.11"},
                mandate_json={"mandate": "help"},
                manifest_sha256=("a" if i == 0 else "b") * 64,
                created_by="seed",
                approved_by="approver-seed",
                approved_at=now,
            )
            for i, (o, p, s) in enumerate(zip(orgs, projects, snapshots, strict=True))
        ]
        session.add_all(manifests)
        session.commit()
        org1_id, org2_id = orgs[0].id, orgs[1].id
        p1_id, p2_id = projects[0].id, projects[1].id
        m1_id, m2_id = manifests[0].id, manifests[1].id

    version_counter = [0]

    def seed_suite(
        org: uuid.UUID,
        project_id: uuid.UUID,
        manifest_id: uuid.UUID,
        *,
        purpose: str,
        locked: bool,
        cases: list[dict[str, Any]],
    ) -> uuid.UUID:
        version_counter[0] += 1
        suite = EvaluationSuite(
            organization_id=org,
            project_id=project_id,
            agent_manifest_id=manifest_id,
            version=version_counter[0],
            status="draft",
            purpose=purpose,
            name=f"suite-{version_counter[0]}",
            grading_logic_version=1,
            created_by="creator-1",
        )
        with Session(owner_engine) as s:
            s.add(suite)
            s.commit()
            suite_id = suite.id
        validated = []
        for raw in cases:
            case, errors = validate_case_input(raw, len(validated))
            assert case is not None and not errors, errors
            validated.append(case)
        context = TenantContext(org)
        with tenant_scoped_session_for(session_factory, context) as s:
            import_cases(
                s,
                context,
                project_id=project_id,
                suite_id=suite_id,
                role=Role.ORGANIZATION_ADMIN,
                cases=validated,
                actor_type="user",
                actor_id="seed",
                request_id="seed",
            )
        if locked:
            with Session(owner_engine) as s:
                s.execute(
                    text(
                        "UPDATE evaluation_suites SET status = 'locked', reviewer_id = 'reviewer-1', locked_at = :t WHERE id = :i"
                    ),
                    {"t": now, "i": suite_id},
                )
                s.commit()
        return suite_id

    # The adapter's backend is built first so cases can name a real record id.
    adapter = build_isolated_tool_backend_adapter()
    env = adapter.tool_environment
    a_record = env.record_id_for(
        tenant_id="tenant-a", owner_subject_id="subject-a1", title="Quarterly report"
    )
    b_record = env.record_id_for(
        tenant_id="tenant-b", owner_subject_id="subject-b1", title="Support escalation"
    )

    dev_cases = [
        _raw("c-01-search", "search_records", "subject-a1", {"query": "quarterly"}),
        _raw(
            "c-02-note",
            "create_note",
            "subject-a1",
            {"record_id": a_record, "text": "bridge note", "idempotency_key": "n-1"},
        ),
        _raw("c-03-cross-tenant", "get_record", "subject-a1", {"record_id": b_record}),
        _raw("c-04-unseeded", "search_records", "subject-never-seeded", {"query": "x"}),
    ]
    dev_suite = seed_suite(
        org1_id, p1_id, m1_id, purpose="development", locked=True, cases=dev_cases
    )
    draft_suite = seed_suite(
        org1_id, p1_id, m1_id, purpose="development", locked=False, cases=dev_cases[:1]
    )
    final_locked = seed_suite(
        org1_id, p1_id, m1_id, purpose="final", locked=True, cases=dev_cases[:1]
    )
    final_draft = seed_suite(
        org1_id, p1_id, m1_id, purpose="final", locked=False, cases=dev_cases[:1]
    )
    other_org_suite = seed_suite(
        org2_id, p2_id, m2_id, purpose="development", locked=True, cases=dev_cases[:1]
    )

    def load(
        org: uuid.UUID,
        project_id: uuid.UUID,
        suite_id: uuid.UUID,
        role: Role = Role.ORGANIZATION_ADMIN,
    ):
        context = TenantContext(org)
        with tenant_scoped_session_for(session_factory, context) as s:
            return load_locked_development_suite(
                s, context, project_id=project_id, suite_id=suite_id, role=role
            )

    def code_of(fn: Any, *args: Any) -> str:
        try:
            fn(*args)
        except CaseAdapterError as exc:
            return exc.code
        except EvaluationSuiteNotFoundError:
            return "not_found"
        except BaseException as exc:  # noqa: BLE001
            return f"OTHER:{type(exc).__name__}"
        return "NO_RAISE"

    # -- the loader's refusals, and that no case row is read for them --------------------------------
    for label, suite_id, expected in (
        ("draft development", draft_suite, "suite_not_locked"),
        ("locked final", final_locked, "final_suite_requires_freeze"),
        ("draft final", final_draft, "final_suite_requires_freeze"),
    ):
        statements.clear()
        got = code_of(load, org1_id, p1_id, suite_id)
        touched_cases = [s for s in statements if "evaluation_cases" in s]
        check(CID, f"{label} suite -> {expected} (got {got})", got == expected)
        check(
            CID,
            f"{label} suite: no query touched evaluation_cases ({len(touched_cases)})",
            touched_cases == [],
        )
    check(
        CID,
        "another organization's suite id is the same not-found as a random id",
        code_of(load, org1_id, p1_id, other_org_suite) == "not_found"
        and code_of(load, org1_id, p1_id, uuid.uuid4()) == "not_found"
        and code_of(load, org1_id, p2_id, dev_suite) == "not_found",
    )

    # -- the real read, as sovra_app, and the mapping ---------------------------------------------
    statements.clear()
    case_set = load(org1_id, p1_id, dev_suite)
    check(
        CID,
        "the locked development suite's four cases load in case_key order with the suite and manifest ids",
        [c.case_key for c in case_set.cases] == sorted(c["case_key"] for c in dev_cases)
        and case_set.suite_id == str(dev_suite)
        and case_set.agent_manifest_id == str(m1_id)
        and case_set.purpose == "development",
    )
    check(
        CID,
        "Sample ids and metadata come from the stored rows",
        [s.id for s in case_set.samples] == [c.case_key for c in case_set.cases]
        and all(s.metadata["suite_id"] == str(dev_suite) for s in case_set.samples)
        and all(uuid.UUID(s.metadata["case_id"]) for s in case_set.samples),
    )
    check(
        CID,
        "the read went through the database (queries ran)",
        any("evaluation_cases" in s for s in statements),
    )

    # -- the run -------------------------------------------------------------------------------------
    hook = BridgeHook(
        policy=ScreeningPolicy(
            "standard", "standard", None, frozenset({"search_records", "create_note", "get_record"})
        ),
        approved_settings={},
    )
    statements.clear()
    result = run_suite_through_runtime(
        adapter,
        case_set,
        hook=hook,
        entry_kind="signed_container",
        is_reconstructed_adapter=False,
        model_binding=ModelBinding("n/a", "n/a"),
        tenant_namespace="tenant-a",
        deadline_seconds=30.0,
        run_id=f"run-{suffix}",
    )
    by_key = {c.case_key: c for c in result.cases}
    check(CID, "the run issued no database statement of its own", statements == [])
    check(
        CID,
        "search and note cases completed with a business-outcome digest the independent collect matched",
        by_key["c-01-search"].status == "completed"
        and by_key["c-02-note"].status == "completed"
        and all(
            by_key[k].business_outcome_sha256 and by_key[k].collected_matches_invoke
            for k in ("c-01-search", "c-02-note")
        ),
    )
    check(
        CID,
        "the cross-tenant get_record is a real REFUSAL from the backend, with no outcome",
        by_key["c-03-cross-tenant"].status == "error"
        and by_key["c-03-cross-tenant"].error_classification == "refusal"
        and by_key["c-03-cross-tenant"].business_outcome_sha256 is None,
    )
    check(
        CID,
        "an unseeded subject is refused at preflight and never invoked",
        by_key["c-04-unseeded"].status == "preflight_refused",
    )
    reread = env.dispatch(
        "get_record",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={"record_id": a_record},
    )
    check(
        CID,
        "the claimed note side effect is independently observable in the real backend",
        any(note["text"] == "bridge note" for note in reread["notes"])
        and by_key["c-02-note"].side_effect_count == 1,
    )
    check(
        CID,
        "the Inspect log is a local file whose sha256 matches, with 0 outbound attempts",
        Path(result.log_path).is_file()
        and result.log_sha256 == hashlib.sha256(Path(result.log_path).read_bytes()).hexdigest()
        and result.network_attempts == 0
        and result.inspect_version == "0.3.269",
    )
    check(
        CID,
        "the result is content-free (no stored input text)",
        "bridge note" not in repr(result) and "quarterly" not in repr(result),
    )

    if FAILURES:
        print(f"FTF-R004-P3 inspect target bridge: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-R004-P3 inspect target bridge: all checks passed")


if __name__ == "__main__":
    main()
