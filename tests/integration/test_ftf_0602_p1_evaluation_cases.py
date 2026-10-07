#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,union-attr"
# ruff: noqa: E501
"""FTF-0602-P1 -- real Postgres proof of EvaluationCase persistence + API.

Proves, against a live PostgreSQL server and the real routes mounted on a real
uvicorn server (never SQLite: it has no row-level security, no jsonb and no
row locks):

  - migration 0023 upgrade / downgrade / upgrade; ENABLE + FORCE RLS and a
    non-allow-all tenant policy; the app role has SELECT/INSERT/UPDATE and no
    DELETE;
  - import / list / get / patch through the real routes under the restricted
    `sovra_app` role: rows, the `evaluation_case.import` / `.update` audit
    events (ids, count, field names only), all-or-nothing batches, duplicate
    keys, 501 rows, the critical-oracle rule, final-suite 403s;
  - writes take the suite row lock: an import that arrives while another
    transaction holds the suite row and locks it waits, then returns 409
    `suite_locked` and writes nothing; eight concurrent imports of one key
    produce exactly one 201;
  - cross-tenant: org2 reading/writing org1's suite or cases is the same 404 as
    a random id; a raw INSERT for org2 under org1's tenant context is rejected
    by RLS; with no tenant context no row is visible;
  - the database itself refuses what the API refuses: critical + exact_match /
    contains_fact / llm_judge, `expected_json = '{}'`, non-object payloads,
    payloads over 2 MiB, bad severity / grader, non-positive or NaN weight,
    blank keys, duplicate (suite, case_key), a suite of another project or
    organization, and DELETE;
  - an UPDATE is allowed to the app role (cases are edited in place).

Requires `SOVRA_DATABASE_URL` (migration owner) and `SOVRA_APP_DB_PASSWORD`.

Run directly:
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0602_p1_evaluation_cases.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.audit_event_writer import AuditEvent  # noqa: E402
from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
    set_tenant_context,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.evaluation.evaluation_case_routes import build_evaluation_case_router  # noqa: E402
from app.evaluation.evaluation_suite_schema import EvaluationSuite  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.target_connectors.agent_manifest_schema import AgentManifest  # noqa: E402
from app.target_connectors.target_application_connection_schema import (  # noqa: E402
    TargetApplicationConnection,
)
from app.target_connectors.target_source_snapshot_schema import TargetSourceSnapshot  # noqa: E402

CID = "FTF-0602-P1"
FAILURES: list[str] = []
_ISSUER = "https://issuer.example"


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


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def _serve(app: FastAPI) -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


class _Http:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)


def _request(
    base: str,
    method: str,
    path: str,
    *,
    principal: str | None,
    payload: Any = None,
) -> _Http:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    body: bytes | None = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=60)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return _Http(response.status, response.read())
    finally:
        conn.close()


def _case(key: str, **overrides: Any) -> dict[str, Any]:
    case: dict[str, Any] = {
        "case_key": key,
        "category": "critical side-effect prevention",
        "severity": "high",
        "language": "en",
        "group_key": f"group-{key}",
        "input_json": {"messages": [{"role": "user", "content": "Refund order 123"}]},
        "expected_json": {"must_not_call": ["refund_order"]},
        "grader_type": "tool_name",
    }
    case.update(overrides)
    return case


def _raw_insert_sql(**overrides: Any) -> tuple[str, dict[str, Any]]:
    values: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "organization_id": None,
        "project_id": None,
        "suite_id": None,
        "case_key": f"raw-{uuid.uuid4().hex[:8]}",
        "category": "c",
        "severity": "low",
        "critical": False,
        "language": "en",
        "group_key": "g",
        "input_json": "{}",
        "expected_json": '{"a": 1}',
        "grader_type": "exact_match",
        "grader_config_json": "{}",
        "weight": 1.0,
        "created_by": "raw",
    }
    values.update(overrides)
    sql = (
        "INSERT INTO evaluation_cases (id, organization_id, project_id, suite_id, case_key, "
        "category, severity, critical, language, group_key, input_json, expected_json, "
        "grader_type, grader_config_json, weight, created_by) VALUES "
        "(:id, :organization_id, :project_id, :suite_id, :case_key, :category, :severity, "
        ":critical, :language, :group_key, CAST(:input_json AS jsonb), CAST(:expected_json AS jsonb), "
        ":grader_type, CAST(:grader_config_json AS jsonb), :weight, :created_by)"
    )
    return sql, values


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    for label, args in (
        ("upgrade head", ["upgrade", "head"]),
        ("downgrade -1", ["downgrade", "-1"]),
        ("upgrade head again", ["upgrade", "head"]),
    ):
        result = _alembic(args, migration_url)
        check(
            CID,
            f"alembic {label} runs cleanly (exit {result.returncode})\n{result.stdout}\n{result.stderr}",
            result.returncode == 0,
        )
    if FAILURES:
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))
    session_factory = sessionmaker(bind=app_engine)

    with owner_engine.connect() as conn:
        head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        table_after_cycle = conn.execute(text("SELECT to_regclass('public.evaluation_cases')")).scalar()
    check(CID, "after upgrade/downgrade/upgrade the head is 0023 or later and the table exists", head is not None and table_after_cycle == "evaluation_cases")
    down = _alembic(["downgrade", "0022_model_catalog_entries"], migration_url)
    with owner_engine.connect() as conn:
        gone = conn.execute(text("SELECT to_regclass('public.evaluation_cases')")).scalar()
        uq_gone = conn.execute(
            text("SELECT count(*) FROM pg_constraint WHERE conname = 'uq_evaluation_suites_org_project_id_id'")
        ).scalar()
    check(CID, "downgrading to 0022 removes the table and the suite unique key", down.returncode == 0 and gone is None and uq_gone == 0)
    up = _alembic(["upgrade", "head"], migration_url)
    check(CID, "upgrading again restores it", up.returncode == 0)

    # -- Catalog: RLS, policy, grants ---------------------------------------------------
    with Session(owner_engine) as s:
        flags = s.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'evaluation_cases'")
        ).one()
        policies = s.execute(
            text("SELECT policyname, qual, with_check FROM pg_policies WHERE tablename = 'evaluation_cases'")
        ).all()
        privileges = {
            priv: s.execute(
                text("SELECT has_table_privilege(:role, 'evaluation_cases', :priv)"),
                {"role": APP_ROLE_NAME, "priv": priv},
            ).scalar()
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES")
        }
    check(CID, "evaluation_cases has ENABLE and FORCE row level security", tuple(flags) == (True, True))
    check(
        CID,
        "one tenant_isolation policy scoped to app.current_organization_id, not allow-all",
        len(policies) == 1
        and policies[0][0] == "tenant_isolation"
        and "app.current_organization_id" in (policies[0][1] or "")
        and "app.current_organization_id" in (policies[0][2] or "")
        and (policies[0][1] or "").strip().lower() != "true",
    )
    check(
        CID,
        "the app role has SELECT, INSERT, UPDATE and nothing else",
        privileges == {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": False, "TRUNCATE": False, "REFERENCES": False},
    )
    with owner_engine.connect() as conn:
        app_is_super = conn.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE_NAME}).scalar()
    check(CID, "the app role is neither superuser nor BYPASSRLS", app_is_super is False)

    # -- Seed two tenants -------------------------------------------------------------------
    now = datetime.now(UTC)
    suffix = uuid.uuid4().hex[:8]
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0602P1", slug=f"acme-0602p1-{suffix}")
        org2 = Organization(name="Globex 0602P1", slug=f"globex-0602p1-{suffix}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Globex Main", slug="main")
        session.add_all([ws1, ws2])
        session.flush()

        def project(org: Organization, ws: Workspace, name: str) -> Project:
            return Project(
                organization_id=org.id,
                workspace_id=ws.id,
                name=name,
                project_type="agentic_product",
                status="active",
                security_profile="standard",
                row_version=1,
                created_by="seed",
            )

        p1, p1b, p2 = project(org1, ws1, "acme-1"), project(org1, ws1, "acme-2"), project(org2, ws2, "globex-1")
        session.add_all([p1, p1b, p2])
        session.flush()
        conn1 = TargetApplicationConnection(
            organization_id=org1.id,
            project_id=p1.id,
            connector_type="local_repository",
            display_name="acme-repo",
            repository_identity="acme/primary",
            access_mode="read_only",
            connection_status="active",
            last_validated_at=now,
            created_by="seed",
        )
        conn2 = TargetApplicationConnection(
            organization_id=org2.id,
            project_id=p2.id,
            connector_type="local_repository",
            display_name="globex-repo",
            repository_identity="globex/primary",
            access_mode="read_only",
            connection_status="active",
            last_validated_at=now,
            created_by="seed",
        )
        session.add_all([conn1, conn2])
        session.flush()
        snap1 = TargetSourceSnapshot(
            organization_id=org1.id,
            target_application_connection_id=conn1.id,
            commit_sha="1" * 40,
            tree_hash="2" * 40,
            capture_method="github_resolved_commit",
        )
        snap2 = TargetSourceSnapshot(
            organization_id=org2.id,
            target_application_connection_id=conn2.id,
            commit_sha="3" * 40,
            tree_hash="4" * 40,
            capture_method="github_resolved_commit",
        )
        session.add_all([snap1, snap2])
        session.flush()

        def manifest(org: Organization, proj: Project, snap: TargetSourceSnapshot, sha: str) -> AgentManifest:
            return AgentManifest(
                organization_id=org.id,
                project_id=proj.id,
                target_source_snapshot_id=snap.id,
                version=1,
                status="locked",
                agent_name="Agent",
                agent_entrypoint_json={"entry": "src/agent.py"},
                current_model_binding_json={"model": "gpt-x"},
                runtime_dependencies_json={"python": "3.11"},
                mandate_json={"mandate": "help"},
                manifest_sha256=sha * 64,
                created_by="seed",
                approved_by="approver-seed",
                approved_at=now,
            )

        m1, m2 = manifest(org1, p1, snap1, "a"), manifest(org2, p2, snap2, "b")
        session.add_all([m1, m2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        p1_id, p1b_id, p2_id = p1.id, p1b.id, p2.id
        m1_id, m2_id = m1.id, m2.id

    version_counter = [0]

    def seed_suite(org: uuid.UUID, project_id: uuid.UUID, manifest_id: uuid.UUID, *, purpose: str = "development", status: str = "draft") -> uuid.UUID:
        version_counter[0] += 1
        extra: dict[str, Any] = {}
        if status == "locked":
            extra.update(reviewer_id="reviewer-1", locked_at=now)
        suite = EvaluationSuite(
            organization_id=org,
            project_id=project_id,
            agent_manifest_id=manifest_id,
            version=version_counter[0],
            status=status,
            purpose=purpose,
            name=f"suite-{version_counter[0]}",
            grading_logic_version=1,
            created_by="creator-1",
            **extra,
        )
        with Session(owner_engine) as s:
            s.add(suite)
            s.commit()
            return suite.id

    roles = {
        "evaluator": Role.EVALUATOR,
        "wsadmin": Role.WORKSPACE_ADMIN,
        "orgadmin": Role.ORGANIZATION_ADMIN,
        "ml": Role.ML_ENGINEER,
        "approver": Role.APPROVER,
        "viewer": Role.VIEWER,
    }
    principals: dict[str, tuple[AuthenticatedPrincipal, Role, uuid.UUID]] = {}
    for label, role in roles.items():
        principals[f"o1-{label}"] = (AuthenticatedPrincipal(issuer=_ISSUER, subject=f"o1-{label}"), role, org1_id)
    principals["o2-evaluator"] = (AuthenticatedPrincipal(issuer=_ISSUER, subject="o2-evaluator"), Role.EVALUATOR, org2_id)
    principals["o2-orgadmin"] = (AuthenticatedPrincipal(issuer=_ISSUER, subject="o2-orgadmin"), Role.ORGANIZATION_ADMIN, org2_id)

    def principal_resolver(request: Any) -> AuthenticatedPrincipal | None:
        entry = principals.get(request.headers.get("x-test-principal", ""))
        return None if entry is None else entry[0]

    def role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        for principal, role, org in principals.values():
            if principal.subject == subject and org == organization_id:
                return role
        return None

    app = FastAPI()
    app.include_router(
        build_evaluation_case_router(session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup)
    )

    def path(org: uuid.UUID, project_id: uuid.UUID, suite: uuid.UUID, suffix: str = "") -> str:
        return f"/api/v1/organizations/{org}/projects/{project_id}/evaluation-suites/{suite}/cases{suffix}"

    def case_rows(suite: uuid.UUID) -> list[Any]:
        with Session(owner_engine) as s:
            return list(
                s.execute(
                    text("SELECT id, case_key, category, critical, weight, created_by, expected_json FROM evaluation_cases WHERE suite_id = :s ORDER BY case_key"),
                    {"s": suite},
                ).all()
            )

    def audit_events(action: str, resource_id: str) -> list[AuditEvent]:
        with Session(owner_engine) as s:
            return list(
                s.scalars(
                    select(AuditEvent).where(AuditEvent.action == action, AuditEvent.resource_id == resource_id)
                ).all()
            )

    with _serve(app) as base:
        # -- Import through the real routes under the restricted app role ------------------
        suite_a = seed_suite(org1_id, p1_id, m1_id)
        r = _request(base, "POST", path(org1_id, p1_id, suite_a), principal="o1-evaluator", payload={"cases": [_case("c1"), _case("c2", critical=True, weight=2.5)]})
        check(CID, "import -> 201 with two items", r.status == 201 and [i["case_key"] for i in r.json()["items"]] == ["c1", "c2"])
        items = r.json()["items"] if r.status == 201 else []
        rows = case_rows(suite_a)
        check(CID, "two rows in Postgres with the right owner and weight", len(rows) == 2 and rows[0].created_by == "o1-evaluator" and rows[1].weight == 2.5 and rows[1].critical is True)
        events = audit_events("evaluation_case.import", str(suite_a))
        check(
            CID,
            "one evaluation_case.import audit event in the same transaction: ids and count, no content",
            len(events) == 1
            and events[0].details_json["count"] == 2
            and events[0].details_json["case_ids"] == [i["id"] for i in items]
            and events[0].actor_id == "o1-evaluator"
            and events[0].organization_id == org1_id
            and "refund" not in json.dumps(events[0].details_json).lower(),
        )

        # all-or-nothing, duplicates, size
        r = _request(base, "POST", path(org1_id, p1_id, suite_a), principal="o1-evaluator", payload={"cases": [_case("ok-1"), _case("c1")]})
        check(CID, "a batch repeating an existing key -> 409 duplicate_case_key", r.status == 409 and r.json() == {"detail": "duplicate_case_key"})
        check(CID, "the valid row of that batch was not inserted", [x.case_key for x in case_rows(suite_a)] == ["c1", "c2"])
        r = _request(base, "POST", path(org1_id, p1_id, suite_a), principal="o1-evaluator", payload={"cases": [_case("dup"), _case("dup")]})
        check(CID, "a batch repeating a key inside itself -> 409", r.status == 409 and len(case_rows(suite_a)) == 2)
        r = _request(base, "POST", path(org1_id, p1_id, suite_a), principal="o1-evaluator", payload={"cases": [_case("good"), _case("bad", severity="urgent")]})
        check(CID, "one bad row -> 400 naming it, and nothing inserted", r.status == 400 and r.json()["errors"] == [{"index": 1, "field": "severity", "code": "invalid_value"}] and len(case_rows(suite_a)) == 2)
        r = _request(base, "POST", path(org1_id, p1_id, suite_a), principal="o1-evaluator", payload={"cases": [_case(f"m{i}") for i in range(501)]})
        check(CID, "501 rows -> 400", r.status == 400 and len(case_rows(suite_a)) == 2)
        suite_big = seed_suite(org1_id, p1_id, m1_id)
        r = _request(base, "POST", path(org1_id, p1_id, suite_big), principal="o1-evaluator", payload={"cases": [_case(f"b{i:03d}") for i in range(500)]})
        check(CID, "500 rows -> 201 and all 500 stored", r.status == 201 and len(case_rows(suite_big)) == 500)
        r = _request(base, "GET", path(org1_id, p1_id, suite_big) + "?limit=200", principal="o1-viewer")
        page = r.json() if r.status == 200 else {}
        check(CID, "cursor listing on Postgres: first page of 200 and a cursor", r.status == 200 and len(page["items"]) == 200 and page["next_after"] == "b199")
        r2 = _request(base, "GET", path(org1_id, p1_id, suite_big) + "?limit=500&after=b199", principal="o1-viewer")
        check(CID, "second page holds the remaining 300 and no cursor", r2.status == 200 and len(r2.json()["items"]) == 300 and r2.json()["next_after"] is None)

        big_text = {"p": "a" * (2 * 1024 * 1024 - 9)}
        r = _request(base, "POST", path(org1_id, p1_id, suite_a), principal="o1-evaluator", payload={"cases": [_case("huge", input_json=big_text)]})
        check(CID, "a payload at the 2 MiB bound is accepted by the API and the database CHECK", r.status == 201)
        r = _request(base, "POST", path(org1_id, p1_id, suite_a), principal="o1-evaluator", payload={"cases": [_case("huge2", input_json={"p": "a" * (2 * 1024 * 1024)})]})
        check(CID, "a payload over 2 MiB -> 400 too_large", r.status == 400 and r.json()["errors"] == [{"index": 0, "field": "input_json", "code": "too_large"}])
        r = _request(base, "POST", path(org1_id, p1_id, suite_a), principal="o1-evaluator", payload={"cases": [_case("sp", input_json={"k": ["a"] * 480000})]})
        check(CID, "the API bound is at least as strict as the database CHECK on jsonb::text (no 500 from the CHECK)", r.status == 400)
        r = _request(base, "POST", path(org1_id, p1_id, suite_a), principal="o1-evaluator", payload={"cases": [_case("nul", input_json={"a": "x\u0000y"})]})
        check(CID, "a NUL character is a 400, not a database error", r.status == 400 and r.json()["errors"][0]["code"] == "invalid_value")

        # critical oracle
        for grader in ("exact_match", "contains_fact", "llm_judge"):
            r = _request(base, "POST", path(org1_id, p1_id, suite_a), principal="o1-evaluator", payload={"cases": [_case(f"crit-{grader}", critical=True, grader_type=grader)]})
            check(CID, f"critical + {grader} -> 400 critical_oracle_not_independent", r.status == 400 and r.json()["errors"] == [{"index": 0, "field": "grader_type", "code": "critical_oracle_not_independent"}])
        check(CID, "none of the refused critical cases was inserted", all(not x.case_key.startswith("crit-") for x in case_rows(suite_a)))

        # patch
        c1_id = next(i["id"] for i in items if i["case_key"] == "c1")
        r = _request(base, "PATCH", path(org1_id, p1_id, suite_a, f"/{c1_id}"), principal="o1-wsadmin", payload={"category": "recovery", "weight": 3, "expected_json": {"must_call": "SENSITIVE"}})
        check(CID, "PATCH -> 200 with the new values", r.status == 200 and r.json()["category"] == "recovery" and r.json()["weight"] == 3)
        upd = audit_events("evaluation_case.update", c1_id)
        check(CID, "one evaluation_case.update audit event with field names only", len(upd) == 1 and upd[0].details_json == {"suite_id": str(suite_a), "fields": ["category", "expected_json", "weight"]} and "SENSITIVE" not in json.dumps(upd[0].details_json))
        r = _request(base, "PATCH", path(org1_id, p1_id, suite_a, f"/{c1_id}"), principal="o1-wsadmin", payload={"case_key": "renamed"})
        check(CID, "case_key is immutable -> 400", r.status == 400 and r.json()["errors"] == [{"index": None, "field": "case_key", "code": "immutable"}])
        r = _request(base, "PATCH", path(org1_id, p1_id, suite_a, f"/{c1_id}"), principal="o1-wsadmin", payload={"critical": True, "grader_type": "llm_judge"})
        check(CID, "patching to critical + llm_judge -> 400", r.status == 400 and r.json()["errors"][0]["code"] == "critical_oracle_not_independent")
        check(CID, "the refused patch changed nothing", next(x for x in case_rows(suite_a) if x.case_key == "c1").category == "recovery")

        # final-suite restriction
        suite_final = seed_suite(org1_id, p1_id, m1_id, purpose="final")
        r = _request(base, "POST", path(org1_id, p1_id, suite_final), principal="o1-evaluator", payload={"cases": [_case("sealed", expected_json={"answer": "FINAL-ANSWER-42"})]})
        check(CID, "an evaluator writes to a final suite", r.status == 201)
        sealed_id = r.json()["items"][0]["id"] if r.status == 201 else str(uuid.uuid4())
        for who in ("o1-ml", "o1-approver", "o1-viewer"):
            lst = _request(base, "GET", path(org1_id, p1_id, suite_final), principal=who)
            one = _request(base, "GET", path(org1_id, p1_id, suite_final, f"/{sealed_id}"), principal=who)
            check(CID, f"{who}: list and get of a final suite -> 403 final_suite_content_restricted without content", lst.status == 403 and one.status == 403 and lst.json() == {"detail": "final_suite_content_restricted"} and b"FINAL-ANSWER" not in lst.body + one.body)
            check(CID, f"{who}: can read a development suite", _request(base, "GET", path(org1_id, p1_id, suite_a), principal=who).status == 200)
        for who in ("o1-evaluator", "o1-wsadmin", "o1-orgadmin"):
            check(CID, f"{who} reads the final suite", _request(base, "GET", path(org1_id, p1_id, suite_final), principal=who).status == 200)

        # locked suite
        locked = seed_suite(org1_id, p1_id, m1_id, status="locked")
        r = _request(base, "POST", path(org1_id, p1_id, locked), principal="o1-evaluator", payload={"cases": [_case("late")]})
        check(CID, "import into a locked suite -> 409 suite_locked", r.status == 409 and r.json() == {"detail": "suite_locked"} and not case_rows(locked))

        # -- Writes take the suite row lock ---------------------------------------------------
        racing = seed_suite(org1_id, p1_id, m1_id, purpose="final")
        outcome: dict[str, Any] = {}
        blocker = Session(owner_engine)
        blocker.execute(text("SELECT id FROM evaluation_suites WHERE id = :s FOR UPDATE"), {"s": racing})
        blocker.execute(
            text("UPDATE evaluation_suites SET status = 'locked', locked_at = now(), reviewer_id = 'someone-else' WHERE id = :s"),
            {"s": racing},
        )

        def do_import() -> None:
            outcome["result"] = _request(base, "POST", path(org1_id, p1_id, racing), principal="o1-evaluator", payload={"cases": [_case("raced")]})

        thread = threading.Thread(target=do_import)
        started = time.monotonic()
        thread.start()
        time.sleep(2.0)
        still_blocked = thread.is_alive()
        blocker.commit()
        blocker.close()
        thread.join(timeout=30)
        res = outcome.get("result")
        check(CID, "an import waits for another transaction's lock on the suite row (it takes FOR UPDATE)", still_blocked and time.monotonic() - started >= 2.0)
        check(CID, "after the lock is released the import re-reads the suite and returns 409 suite_locked", res is not None and res.status == 409 and res.json() == {"detail": "suite_locked"})
        check(CID, "...and wrote nothing", not case_rows(racing))

        concurrent = seed_suite(org1_id, p1_id, m1_id)
        results: list[_Http] = []
        lock = threading.Lock()

        def worker() -> None:
            res_ = _request(base, "POST", path(org1_id, p1_id, concurrent), principal="o1-evaluator", payload={"cases": [_case("same-key")]})
            with lock:
                results.append(res_)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        statuses = sorted(x.status for x in results)
        check(CID, "eight concurrent imports of one key: exactly one 201, seven 409, no 500", statuses == [201] + [409] * 7 and len(case_rows(concurrent)) == 1)

        # -- Cross-tenant -----------------------------------------------------------------------
        org2_suite = seed_suite(org2_id, p2_id, m2_id)
        r = _request(base, "POST", path(org2_id, p2_id, org2_suite), principal="o2-evaluator", payload={"cases": [_case("o2-case")]})
        check(CID, "org2 imports into its own suite", r.status == 201)
        o2_case = r.json()["items"][0]["id"] if r.status == 201 else str(uuid.uuid4())
        random_id = uuid.uuid4()
        for label, req in (
            ("org2 POSTing into org1's suite", ("POST", path(org2_id, p1_id, suite_a), "o2-evaluator", {"cases": [_case("x")]})),
            ("org2 listing org1's suite", ("GET", path(org2_id, p1_id, suite_a), "o2-orgadmin", None)),
            ("org2 getting org1's case", ("GET", path(org2_id, p1_id, suite_a, f"/{c1_id}"), "o2-orgadmin", None)),
            ("org2 patching org1's case", ("PATCH", path(org2_id, p1_id, suite_a, f"/{c1_id}"), "o2-evaluator", {"category": "x"})),
            ("org2 listing org1's final suite", ("GET", path(org2_id, p1_id, suite_final), "o2-orgadmin", None)),
        ):
            res = _request(base, req[0], req[1], principal=req[2], payload=req[3])
            ref = _request(base, req[0], req[1].replace(str(suite_a), str(random_id)).replace(str(suite_final), str(random_id)), principal=req[2], payload=req[3])
            check(CID, f"{label} -> 404 identical to a random id", res.status == 404 and res.json() == {"detail": "not_found"} and ref.status == 404 and ref.body == res.body)
        r = _request(base, "GET", path(org1_id, p1_id, suite_a), principal="o2-orgadmin")
        check(CID, "org1's path with an org2 principal is refused by the role gate (403)", r.status == 403)
        r = _request(base, "GET", path(org2_id, p1_id, suite_a), principal="o2-orgadmin")
        check(CID, "org1's project under org2's own path -> 404", r.status == 404)
        r = _request(base, "GET", path(org1_id, p1b_id, suite_a), principal="o1-orgadmin")
        check(CID, "org1's suite under another project of org1 -> 404", r.status == 404)
        check(CID, "org1's data is untouched by every cross-tenant attempt", [x.case_key for x in case_rows(suite_a)][:2] == ["c1", "c2"])
        r = _request(base, "GET", path(org2_id, p2_id, org2_suite, f"/{o2_case}"), principal="o2-orgadmin")
        check(CID, "org2 reads its own case", r.status == 200)

    # -- RLS on raw SQL under the restricted role ---------------------------------------------------
    with session_factory() as s:
        set_tenant_context(s, org1_id)
        visible = s.execute(text("SELECT count(*) FROM evaluation_cases")).scalar()
        foreign_visible = s.execute(text("SELECT count(*) FROM evaluation_cases WHERE organization_id = :o"), {"o": org2_id}).scalar()
    with Session(owner_engine) as s:
        org1_total = s.execute(text("SELECT count(*) FROM evaluation_cases WHERE organization_id = :o"), {"o": org1_id}).scalar()
    check(CID, "with org1 context only org1's rows are visible", visible == org1_total and foreign_visible == 0 and visible > 0)
    with session_factory() as s:
        none_visible = s.execute(text("SELECT count(*) FROM evaluation_cases")).scalar()
    check(CID, "with no tenant context no row is visible", none_visible == 0)
    with session_factory() as s:
        set_tenant_context(s, org1_id)
        sql, vals = _raw_insert_sql(organization_id=org2_id, project_id=p2_id, suite_id=org2_suite)
        try:
            s.execute(text(sql), vals)
            s.commit()
            rejected = False
        except DBAPIError as exc:
            s.rollback()
            rejected = "row-level security" in str(exc.orig)
    check(CID, "a raw INSERT for org2 under org1's tenant context is rejected by RLS", rejected)
    with session_factory() as s:
        set_tenant_context(s, org1_id)
        updated = s.execute(text("UPDATE evaluation_cases SET weight = 9 WHERE organization_id = :o AND case_key = 'c2'"), {"o": org1_id}).rowcount
        s.commit()
        cross_updated = 0
        s2 = session_factory()
        set_tenant_context(s2, org1_id)
        cross_updated = s2.execute(text("UPDATE evaluation_cases SET weight = 9 WHERE organization_id = :o"), {"o": org2_id}).rowcount
        s2.commit()
        s2.close()
    check(CID, "the app role may UPDATE its own tenant's cases but touches none of another's", updated == 1 and cross_updated == 0)
    with session_factory() as s:
        set_tenant_context(s, org1_id)
        try:
            s.execute(text("DELETE FROM evaluation_cases WHERE organization_id = :o"), {"o": org1_id})
            s.commit()
            denied = False
        except DBAPIError as exc:
            s.rollback()
            denied = "permission denied" in str(exc.orig)
    check(CID, "the app role cannot DELETE a case", denied)

    # -- The database itself refuses what the API refuses ---------------------------------------------------
    def raw_refused(constraint: str, **overrides: Any) -> bool:
        suite_id = overrides.pop("suite_id", suite_a)
        project_id = overrides.pop("project_id", p1_id)
        org_id = overrides.pop("organization_id", org1_id)
        with session_factory() as s:
            set_tenant_context(s, org_id)
            sql, vals = _raw_insert_sql(organization_id=org_id, project_id=project_id, suite_id=suite_id, **overrides)
            try:
                s.execute(text(sql), vals)
                s.commit()
                return False
            except DBAPIError as exc:
                s.rollback()
                return constraint in str(exc.orig)

    for grader in ("exact_match", "contains_fact", "llm_judge", "json_schema", "numeric_tolerance", "latency", "cost"):
        check(CID, f"raw INSERT critical + {grader} fails ck_evaluation_cases_critical_independent_grader", raw_refused("ck_evaluation_cases_critical_independent_grader", critical=True, grader_type=grader))
    for grader in ("tool_name", "tool_arguments", "tool_sequence", "state_transition", "forbidden_action", "required_refusal"):
        key = f"raw-crit-{grader}"
        sql, vals = _raw_insert_sql(organization_id=org1_id, project_id=p1_id, suite_id=suite_a, critical=True, grader_type=grader, case_key=key)
        with session_factory() as s:
            set_tenant_context(s, org1_id)
            s.execute(text(sql), vals)
            s.commit()
        check(CID, f"raw INSERT critical + {grader} is allowed", any(x.case_key == key for x in case_rows(suite_a)))
    check(CID, "raw INSERT expected_json {} fails ck_evaluation_cases_expected_not_empty", raw_refused("ck_evaluation_cases_expected_not_empty", expected_json="{}"))
    check(CID, "raw INSERT input_json array fails the object CHECK", raw_refused("ck_evaluation_cases_payloads_are_objects", input_json="[1]"))
    check(CID, "raw INSERT expected_json scalar fails the object CHECK", raw_refused("ck_evaluation_cases_payloads_are_objects", expected_json="5"))
    check(CID, "raw INSERT grader_config_json null fails the object CHECK", raw_refused("ck_evaluation_cases_payloads_are_objects", grader_config_json="null"))
    check(CID, "raw INSERT input_json over 2 MiB fails the size CHECK", raw_refused("ck_evaluation_cases_payload_size", input_json=json.dumps({"p": "a" * (2 * 1024 * 1024)})))
    check(CID, "raw INSERT expected_json over 2 MiB fails the size CHECK", raw_refused("ck_evaluation_cases_payload_size", expected_json=json.dumps({"p": "a" * (2 * 1024 * 1024)})))
    check(CID, "raw INSERT severity urgent fails ck_evaluation_cases_severity", raw_refused("ck_evaluation_cases_severity", severity="urgent"))
    check(CID, "raw INSERT grader nope fails ck_evaluation_cases_grader_type", raw_refused("ck_evaluation_cases_grader_type", grader_type="nope"))
    for weight in (0.0, -1.0, float("nan"), float("inf")):
        check(CID, f"raw INSERT weight {weight} fails ck_evaluation_cases_weight_positive", raw_refused("ck_evaluation_cases_weight_positive", weight=weight))
    check(CID, "raw INSERT blank case_key fails the not-blank CHECK", raw_refused("ck_evaluation_cases_case_key_not_blank", case_key="   "))
    check(CID, "raw INSERT blank category fails the not-blank CHECK", raw_refused("ck_evaluation_cases_category_not_blank", category=" "))
    check(CID, "raw INSERT blank language fails the not-blank CHECK", raw_refused("ck_evaluation_cases_language_not_blank", language=" "))
    check(CID, "raw INSERT blank group_key fails the not-blank CHECK", raw_refused("ck_evaluation_cases_group_key_not_blank", group_key=" "))
    check(CID, "raw INSERT duplicate (suite, case_key) fails the unique key", raw_refused("uq_evaluation_cases_suite_id_case_key", case_key="c1"))
    check(CID, "raw INSERT with a suite of another project fails the composite FK", raw_refused("fk_evaluation_cases_organization_id_project_id_suite_id", project_id=p1b_id))
    check(CID, "raw INSERT with a random suite fails the composite FK", raw_refused("fk_evaluation_cases_organization_id_project_id_suite_id", suite_id=uuid.uuid4()))
    with Session(owner_engine) as s:
        # the owner connection can reach across tenants (superuser); the composite FK still refuses a suite from another organization
        sql, vals = _raw_insert_sql(organization_id=org1_id, project_id=p1_id, suite_id=org2_suite)
        try:
            s.execute(text(sql), vals)
            s.commit()
            fk_refused = False
        except DBAPIError as exc:
            s.rollback()
            fk_refused = "fk_evaluation_cases_organization_id_project_id_suite_id" in str(exc.orig)
    check(CID, "a case in org1 pointing at org2's suite fails the composite FK", fk_refused)
    with Session(owner_engine) as s:
        try:
            s.execute(text("DELETE FROM evaluation_suites WHERE id = :s"), {"s": suite_a})
            s.commit()
            restrict = False
        except DBAPIError as exc:
            s.rollback()
            restrict = "foreign key" in str(exc.orig).lower()
    check(CID, "deleting a suite that has cases is refused (RESTRICT)", restrict)

    if FAILURES:
        print(f"FTF-0602-P1 evaluation case integration: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0602-P1 evaluation case integration: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
