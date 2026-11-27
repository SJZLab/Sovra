#!/usr/bin/env python3
# mypy: disable-error-code="dict-item,index,union-attr,arg-type"
# ruff: noqa: E501
"""FTF-0603-F1 -- real Postgres proof of the server-side suite lock gate.

Against a live PostgreSQL 16 (never SQLite: SQLite has no row-level security),
with the router's sessions bound to the least-privileged `sovra_app` role:

  G1  migration 0039 is the single head and round-trips downgrade -1 / upgrade;
      `evaluation_suite_sample_plans` has ENABLE + FORCE row-level security, a
      tenant policy on USING and WITH CHECK (never allow-all) and sovra_app holds
      exactly SELECT and INSERT on it.
  N1  the assigned reviewer POSTing /lock directly on an all-TBD final suite is
      refused 409 for each clause, the suite stays draft, no plan row exists.
  N2  each clause alone refuses on the live path (missing plan, below-floor
      plan, missing critical category, unresolved expected output, stale hash),
      and a fault injected into each clause's guard turns that refusal into a
      lock (fault -> RED), restored byte-identical afterwards.
  A1  a valid suite locks: the response's suite_sha256 equals the hash of the
      stored rows, one plan row records the plan and counts, the audit event
      carries the hash, GET returns it.
  X1  an unauthorized principal cannot read or write another scope's plan:
      sovra_app under org2's tenant context (or none) sees no org1 plan row,
      cannot insert one for org1, and cannot UPDATE or DELETE any; org2's
      principal gets 404 for org1's suite; raw writes that break the CHECKs,
      the unique key or the composite foreign key are refused.

Requires `SOVRA_DATABASE_URL` (migration owner) and `SOVRA_APP_DB_PASSWORD`,
like every integration test in this repository. Run:
    SOVRA_DATABASE_URL=postgresql+psycopg://<owner>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-password> \\
    python3 tests/integration/test_ftf_0603_f1_suite_lock_gate.py
"""

from __future__ import annotations

import contextlib
import http.client
import importlib
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

_GATE_PATH = API_ROOT / "app" / "evaluation" / "evaluation_suite_lock_gate.py"

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine, insert, select, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.evaluation import evaluation_suite_crud as _crud_module  # noqa: E402
from app.evaluation import evaluation_suite_lock_gate as _gate_module  # noqa: E402
from app.evaluation import evaluation_suite_routes as _routes_module  # noqa: E402
from app.evaluation.evaluation_case_store import EvaluationCaseRecord  # noqa: E402
from app.evaluation.evaluation_suite_schema import EvaluationSuite  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.target_connectors.agent_manifest_schema import AgentManifest  # noqa: E402
from app.target_connectors.target_application_connection_schema import (  # noqa: E402
    TargetApplicationConnection,
)
from app.target_connectors.target_source_snapshot_schema import TargetSourceSnapshot  # noqa: E402

FAILURES: list[str] = []
TABLE = "evaluation_suite_sample_plans"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this proof needs a live Postgres.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def _alembic(database_url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _app_role_url(migration_url: str, app_password: str) -> str:
    parts = urlsplit(migration_url)
    netloc = f"{APP_ROLE_NAME}:{app_password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def _running_app(app: FastAPI) -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(250):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)


def _request(
    base_url: str, method: str, path: str, *, principal: str | None, payload: Any = None
) -> _Response:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=60)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return _Response(response.status, response.read())
    finally:
        conn.close()


_ISSUER = "https://issuer.example"


class _Fixture:
    """Subjects `<org>-<role>`; header `X-Test-Principal: <org>-<role>`."""

    def __init__(self, orgs: dict[str, uuid.UUID]) -> None:
        self.roles: dict[tuple[str, uuid.UUID], Role] = {}
        for label, org_id in orgs.items():
            for role in (Role.WORKSPACE_ADMIN, Role.EVALUATOR, Role.VIEWER):
                self.roles[(f"{label}-{role.value}", org_id)] = role

    def principal_resolver(self, request: Any) -> AuthenticatedPrincipal | None:
        subject = request.headers.get("x-test-principal")
        if subject is None or not any(s == subject for s, _ in self.roles):
            return None
        return AuthenticatedPrincipal(issuer=_ISSUER, subject=subject)

    def role_lookup(self, issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        return self.roles.get((subject, organization_id))


def _case_row(
    org_id: uuid.UUID, project_id: uuid.UUID, suite_id: uuid.UUID, i: int, **overrides: Any
) -> dict[str, Any]:
    now = datetime.now(UTC)
    critical = overrides.pop("critical", True)
    row: dict[str, Any] = {
        "id": uuid.uuid4(),
        "organization_id": org_id,
        "project_id": project_id,
        "suite_id": suite_id,
        "case_key": f"case-{i:04d}",
        "category": "prompt injection",
        "severity": "high",
        "critical": critical,
        "language": "en",
        "group_key": f"group-{i:04d}",
        "input_json": {"text": f"input {i}"},
        "expected_json": {"refused": True},
        "grader_type": "required_refusal" if critical else "exact_match",
        "grader_config_json": {},
        "weight": 1.0,
        "created_by": "fixture",
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def main() -> None:  # noqa: C901 - one linear live-database scenario
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    result = _alembic(migration_url, "upgrade", "head")
    check(
        "G1",
        f"alembic upgrade head runs cleanly ({result.returncode}) {result.stderr[-2000:]}",
        result.returncode == 0,
    )
    if FAILURES:
        _report()
    heads = _alembic(migration_url, "heads")
    check(
        "G1",
        f"exactly one head, 0039 ({heads.stdout.strip()})",
        heads.stdout.split() == ["0039_eval_suite_sample_plans", "(head)"],
    )

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))

    down = _alembic(migration_url, "downgrade", "-1")
    with owner_engine.connect() as conn:
        gone = conn.execute(
            text("SELECT to_regclass('public.evaluation_suite_sample_plans')")
        ).scalar()
    up = _alembic(migration_url, "upgrade", "head")
    with owner_engine.connect() as conn:
        back = conn.execute(
            text("SELECT to_regclass('public.evaluation_suite_sample_plans')")
        ).scalar()
    check(
        "G1",
        f"0039 round-trips: downgrade -1 drops the table, upgrade head restores it ({down.returncode}/{up.returncode})",
        down.returncode == 0 and up.returncode == 0 and gone is None and back is not None,
    )

    with owner_engine.connect() as conn:
        rls = conn.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": TABLE},
        ).one()
        policies = conn.execute(
            text("SELECT policyname, cmd, qual, with_check FROM pg_policies WHERE tablename = :t"),
            {"t": TABLE},
        ).all()
        grants = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = :t AND grantee = :g"
                ),
                {"t": TABLE, "g": APP_ROLE_NAME},
            )
        }
    check("G1", "ENABLE and FORCE row-level security are both on", tuple(rls) == (True, True))
    check(
        "G1",
        f"exactly one tenant policy on USING and WITH CHECK, never allow-all ({policies})",
        len(policies) == 1
        and policies[0][0] == "tenant_isolation"
        and policies[0][1] == "ALL"
        and "app.current_organization_id" in (policies[0][2] or "")
        and "app.current_organization_id" in (policies[0][3] or "")
        and (policies[0][2] or "").strip() not in ("true", "(true)"),
    )
    check(
        "G1",
        f"sovra_app holds exactly SELECT and INSERT ({sorted(grants)})",
        grants == {"SELECT", "INSERT"},
    )

    # -- seed two tenants (owner role) --
    now = datetime.now(UTC)
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0603", slug=f"acme-0603-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0603", slug=f"globex-0603-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ids: dict[str, dict[str, uuid.UUID]] = {}
        for label, org in (("org1", org1), ("org2", org2)):
            ws = Workspace(organization_id=org.id, name=f"{label} main", slug="main")
            session.add(ws)
            session.flush()
            project = Project(
                organization_id=org.id,
                workspace_id=ws.id,
                name=f"{label}-project",
                project_type="agentic_product",
                status="active",
                security_profile="standard",
                row_version=1,
                created_by="seed",
            )
            session.add(project)
            session.flush()
            connection = TargetApplicationConnection(
                organization_id=org.id,
                project_id=project.id,
                connector_type="local_repository",
                display_name=f"{label}-repo",
                repository_identity=f"{label}/repo",
                access_mode="read_only",
                connection_status="active",
                last_validated_at=now,
                created_by="seed",
            )
            session.add(connection)
            session.flush()
            snapshot = TargetSourceSnapshot(
                organization_id=org.id,
                target_application_connection_id=connection.id,
                commit_sha="1" * 40,
                tree_hash="2" * 40,
                capture_method="github_resolved_commit",
            )
            session.add(snapshot)
            session.flush()
            manifest = AgentManifest(
                organization_id=org.id,
                project_id=project.id,
                target_source_snapshot_id=snapshot.id,
                version=1,
                status="locked",
                agent_name="Agent",
                agent_entrypoint_json={"entry": "a.py"},
                current_model_binding_json={"model": "m"},
                runtime_dependencies_json={"python": "3.11"},
                mandate_json={"mandate": "m"},
                manifest_sha256="a" * 64,
                created_by="seed",
                approved_by="approver-seed",
                approved_at=now,
            )
            session.add(manifest)
            session.flush()
            ids[label] = {"org": org.id, "project": project.id, "manifest": manifest.id}
        session.commit()

    org1_id, project1_id = ids["org1"]["org"], ids["org1"]["project"]
    org2_id = ids["org2"]["org"]
    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    evaluator, reviewer = "org1-evaluator", "org1-workspace_admin"

    def build_app() -> FastAPI:
        app = FastAPI()
        app.include_router(
            _routes_module.build_evaluation_suite_router(
                session_factory,
                principal_resolver=fixture.principal_resolver,
                role_lookup=fixture.role_lookup,
            )
        )
        return app

    def suite_path(
        suite_id: object, org: uuid.UUID = org1_id, project: uuid.UUID = project1_id
    ) -> str:
        return f"/api/v1/organizations/{org}/projects/{project}/evaluation-suites/{suite_id}"

    def new_suite(base: str, purpose: str) -> uuid.UUID:
        r = _request(
            base,
            "POST",
            f"/api/v1/organizations/{org1_id}/projects/{project1_id}/evaluation-suites",
            principal=evaluator,
            payload={
                "agent_manifest_id": str(ids["org1"]["manifest"]),
                "purpose": purpose,
                "name": f"{purpose} suite",
                "grading_logic_version": 1,
            },
        )
        suite_id = uuid.UUID(r.json()["id"])
        r2 = _request(
            base,
            "PATCH",
            suite_path(suite_id),
            principal=evaluator,
            payload={"reviewer_id": reviewer},
        )
        check("A1", "suite created and reviewer assigned", r.status == 201 and r2.status == 200)
        return suite_id

    def seed(suite_id: uuid.UUID, rows: list[dict[str, Any]]) -> None:
        with tenant_scoped_session_for(
            session_factory, TenantContext(organization_id=org1_id)
        ) as session:
            session.execute(insert(EvaluationCaseRecord), rows)

    def current_sha(suite_id: uuid.UUID) -> str:
        with tenant_scoped_session_for(
            session_factory, TenantContext(organization_id=org1_id)
        ) as session:
            suite = session.get(EvaluationSuite, suite_id)
            cases = list(
                session.execute(
                    select(EvaluationCaseRecord).where(EvaluationCaseRecord.suite_id == suite_id)
                ).scalars()
            )
            return _gate_module.compute_suite_sha256(suite, cases)

    def plan(
        tasks: int = 1, critical: int = 1, categories: list[str] | None = None
    ) -> dict[str, Any]:
        return {
            "required_categories": categories or ["prompt injection"],
            "min_independent_tasks": tasks,
            "min_critical_scenarios": critical,
            "min_cases_per_category": 1,
        }

    def state(suite_id: uuid.UUID) -> tuple[str, int]:
        with owner_engine.connect() as conn:
            status = conn.execute(
                text("SELECT status FROM evaluation_suites WHERE id = :i"), {"i": suite_id}
            ).scalar()
            plans = conn.execute(
                text(f"SELECT count(*) FROM {TABLE} WHERE suite_id = :i"), {"i": suite_id}
            ).scalar()
        return str(status), int(plans or 0)

    def findings(r: _Response) -> list[str] | None:
        try:
            detail = r.json()["detail"]
            return (
                [f["code"] for f in detail["findings"]]
                if detail["code"] == "suite_lock_validation_failed"
                else None
            )
        except (ValueError, KeyError, TypeError):
            return None

    def dev_suite(base: str, **case_overrides: Any) -> uuid.UUID:
        suite_id = new_suite(base, "development")
        seed(suite_id, [_case_row(org1_id, project1_id, suite_id, 0, **case_overrides)])
        return suite_id

    # The clauses each fault disables: (name, exact text in the gate, replacement,
    # how to build a suite + body that only that clause refuses).
    def clause_cases(base: str) -> list[tuple[str, str, str, uuid.UUID, dict[str, Any], list[str]]]:
        out = []
        s = dev_suite(base)
        out.append(
            (
                "missing plan",
                'findings.append(Finding("sample_plan_missing"))',
                "pass",
                s,
                {"suite_sha256": current_sha(s)},
                ["sample_plan_missing"],
            )
        )
        s = dev_suite(base)
        out.append(
            (
                "below-floor plan",
                'findings.append(Finding("sample_plan_below_floor", name, floors[name], have))',
                "pass",
                s,
                {"sample_plan": plan(tasks=0), "suite_sha256": current_sha(s)},
                ["sample_plan_below_floor"],
            )
        )
        s = dev_suite(base)
        seed(
            s,
            [
                _case_row(
                    org1_id,
                    project1_id,
                    s,
                    1,
                    category="tool timeout",
                    critical=False,
                    expected_json={"answer": "retry"},
                )
            ],
        )
        out.append(
            (
                "missing critical category",
                'findings.append(Finding("missing_critical_category", category))',
                "pass",
                s,
                {
                    "sample_plan": plan(
                        critical=0, categories=["prompt injection", "tool timeout"]
                    ),
                    "suite_sha256": current_sha(s),
                },
                ["missing_critical_category"],
            )
        )
        s = dev_suite(base, expected_json={"refused": "TBD"})
        out.append(
            (
                "unresolved expected output",
                'findings.append(Finding("unresolved_expected_output", case.case_key))',
                "pass",
                s,
                {"sample_plan": plan(), "suite_sha256": current_sha(s)},
                ["unresolved_expected_output"],
            )
        )
        s = dev_suite(base)
        out.append(
            (
                "stale hash",
                'return [Finding("suite_sha256_mismatch")], current',
                "return [], current",
                s,
                {"sample_plan": plan(), "suite_sha256": "0" * 64},
                ["suite_sha256_mismatch"],
            )
        )
        return out

    with _running_app(build_app()) as base:
        # -- N1: all-TBD final suite, direct POST by the assigned reviewer --
        tbd = new_suite(base, "final")
        seed(
            tbd,
            [
                _case_row(
                    org1_id, project1_id, tbd, i, critical=False, expected_json={"answer": "TBD"}
                )
                for i in range(3)
            ],
        )
        r = _request(
            base,
            "POST",
            suite_path(tbd) + "/lock",
            principal=reviewer,
            payload={"sample_plan": plan(tasks=3, critical=0), "suite_sha256": current_sha(tbd)},
        )
        check(
            "N1",
            f"all-TBD final suite refused 409 for each clause ({r.status} {r.body[:600]!r})",
            r.status == 409
            and findings(r)
            == [
                "sample_plan_below_floor",
                "sample_plan_below_floor",
                "missing_critical_category",
                "unresolved_expected_output",
                "unresolved_expected_output",
                "unresolved_expected_output",
            ],
        )
        r = _request(base, "POST", suite_path(tbd) + "/lock", principal=reviewer)
        check(
            "N1",
            "the same suite with no body at all is refused (missing plan and hash)",
            r.status == 409
            and findings(r) is not None
            and findings(r)[0] == "sample_plan_missing"
            and findings(r)[-1] == "suite_sha256_required",
        )
        check(
            "N1", "the all-TBD suite is still draft and has no plan row", state(tbd) == ("draft", 0)
        )

        # -- N2: each clause alone, then fault -> RED, then restored --
        original = _GATE_PATH.read_bytes()
        for name, needle, replacement, suite_id, body, codes in clause_cases(base):
            r = _request(
                base, "POST", suite_path(suite_id) + "/lock", principal=reviewer, payload=body
            )
            check(
                "N2",
                f"{name}: refused 409 with {codes} ({r.status} {r.body[:300]!r})",
                r.status == 409 and findings(r) == codes,
            )
            check("N2", f"{name}: suite still draft, no plan row", state(suite_id) == ("draft", 0))
            source = original.decode("utf-8")
            check(
                "N2",
                f"{name}: fault target text exists exactly once in the gate",
                source.count(needle) == 1,
            )
            try:
                _GATE_PATH.write_text(source.replace(needle, replacement), encoding="utf-8")
                importlib.reload(_gate_module)
                importlib.reload(_crud_module)
                importlib.reload(_routes_module)
                with _running_app(build_app()) as faulty:
                    fr = _request(
                        faulty,
                        "POST",
                        suite_path(suite_id) + "/lock",
                        principal=reviewer,
                        payload=body,
                    )
                check(
                    "N2",
                    f"{name}: with its guard removed the clause no longer refuses (fault -> RED, got {fr.status})",
                    fr.status != 409 or findings(fr) != codes,
                )
            finally:
                _GATE_PATH.write_bytes(original)
                importlib.reload(_gate_module)
                importlib.reload(_crud_module)
                importlib.reload(_routes_module)
            check(
                "N2",
                f"{name}: gate source restored byte-identical",
                _GATE_PATH.read_bytes() == original,
            )

        with _running_app(build_app()) as base2:
            # -- A1: a valid development suite locks --
            ok = dev_suite(base2)
            sha = current_sha(ok)
            r = _request(
                base2,
                "POST",
                suite_path(ok) + "/lock",
                principal=reviewer,
                payload={"sample_plan": plan(), "suite_sha256": sha},
            )
            check(
                "A1",
                f"a valid suite locks 200 with its suite_sha256 ({r.status} {r.body[:300]!r})",
                r.status == 200
                and r.json()["status"] == "locked"
                and r.json().get("suite_sha256") == sha,
            )
            check("A1", "the locked suite has exactly one plan row", state(ok) == ("locked", 1))
            with owner_engine.connect() as conn:
                row = conn.execute(
                    text(
                        f"SELECT organization_id, project_id, required_categories, min_independent_tasks, "
                        f"min_critical_scenarios, min_cases_per_category, case_count, independent_task_count, "
                        f"critical_scenario_count, suite_sha256, created_by FROM {TABLE} WHERE suite_id = :i"
                    ),
                    {"i": ok},
                ).first()
                audit = conn.execute(
                    text(
                        "SELECT details_json FROM audit_events WHERE action = 'evaluation_suite.sample_plan_record' "
                        "AND resource_id = :r"
                    ),
                    {"r": str(ok)},
                ).all()
            check(
                "A1",
                f"the plan row records plan, counts and hash ({tuple(row) if row else None})",
                (tuple(row) if row else None)
                == (org1_id, project1_id, ["prompt injection"], 1, 1, 1, 1, 1, 1, sha, reviewer),
            )
            check(
                "A1",
                f"one lock audit event carries the hash ({audit})",
                len(audit) == 1 and audit[0][0] == {"suite_sha256": sha},
            )
            got = _request(base2, "GET", suite_path(ok), principal="org1-viewer")
            check(
                "A1",
                "GET returns the locked hash",
                got.status == 200 and got.json().get("suite_sha256") == sha,
            )

            # -- X1: cross-tenant over HTTP --
            r = _request(
                base2,
                "POST",
                suite_path(ok, org2_id, ids["org2"]["project"]) + "/lock",
                principal="org2-workspace_admin",
                payload={},
            )
            check(
                "X1",
                "org2's admin locking org1's suite under org2's path gets 404",
                r.status == 404,
            )
            r = _request(
                base2,
                "GET",
                suite_path(ok, org2_id, ids["org2"]["project"]),
                principal="org2-viewer",
            )
            check("X1", "org2's viewer reading org1's suite gets 404", r.status == 404)
            r = _request(
                base2,
                "POST",
                suite_path(ok) + "/lock",
                principal="org2-workspace_admin",
                payload={},
            )
            check(
                "X1",
                "org2's admin on org1's path is refused (403, no role in org1)",
                r.status == 403,
            )

    # -- X1: row-level security and grants on the raw table (sovra_app) --
    with tenant_scoped_session_for(
        session_factory, TenantContext(organization_id=org2_id)
    ) as session:
        seen = session.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar()
    check(
        "X1",
        f"sovra_app under org2's tenant context sees none of org1's plan rows ({seen})",
        seen == 0,
    )
    with app_engine.connect() as conn:
        seen = conn.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar()
    check("X1", f"sovra_app with no tenant context sees no plan row ({seen})", seen == 0)
    with owner_engine.connect() as conn:
        owned = conn.execute(
            text(f"SELECT count(*) FROM {TABLE} WHERE organization_id = :o"), {"o": org1_id}
        ).scalar()
    with tenant_scoped_session_for(
        session_factory, TenantContext(organization_id=org1_id)
    ) as session:
        mine = session.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar()
    check(
        "X1",
        f"sovra_app under org1's own context sees exactly org1's plan rows ({mine}/{owned})",
        owned is not None and owned >= 1 and mine == owned,
    )

    def refused(org: uuid.UUID, sql: str, params: dict[str, Any], what: str, needle: str) -> None:
        try:
            with tenant_scoped_session_for(
                session_factory, TenantContext(organization_id=org)
            ) as session:
                session.execute(text(sql), params)
        except DBAPIError as exc:
            check(
                "X1",
                f"{what} is refused by the database ({needle})",
                needle in str(exc.orig).lower(),
            )
            return
        check("X1", f"{what} is refused by the database", False)

    insert_sql = (
        f"INSERT INTO {TABLE} (organization_id, project_id, suite_id, required_categories, min_independent_tasks, "
        "min_critical_scenarios, min_cases_per_category, case_count, independent_task_count, critical_scenario_count, "
        "suite_sha256, created_by) VALUES (:o, :p, :s, CAST(:c AS jsonb), 1, 0, 1, 1, 1, 0, :h, 'x')"
    )
    target = tbd
    base_params = {"o": org1_id, "p": project1_id, "s": target, "c": '["a"]', "h": "a" * 64}
    refused(
        org2_id,
        insert_sql,
        base_params,
        "sovra_app under org2 inserting a plan for org1",
        "row-level security",
    )
    refused(
        org1_id,
        f"UPDATE {TABLE} SET suite_sha256 = :h",
        {"h": "b" * 64},
        "sovra_app UPDATE of a recorded plan",
        "permission denied",
    )
    refused(
        org1_id,
        f"DELETE FROM {TABLE}",
        {},
        "sovra_app DELETE of a recorded plan",
        "permission denied",
    )
    refused(
        org1_id,
        insert_sql,
        {**base_params, "h": "A" * 64},
        "an uppercase/non-hex hash",
        "ck_evaluation_suite_sample_plans_sha256",
    )
    refused(
        org1_id,
        insert_sql,
        {**base_params, "c": "[]"},
        "an empty category list",
        "ck_evaluation_suite_sample_plans_categories",
    )
    refused(
        org1_id,
        insert_sql,
        {**base_params, "s": ok},
        "a second plan for the same suite",
        "uq_evaluation_suite_sample_plans_suite_id",
    )
    refused(
        org1_id,
        insert_sql,
        {**base_params, "p": ids["org2"]["project"]},
        "a plan whose project is not the suite's",
        "fk_evaluation_suite_sample_plans",
    )
    _report()


def _report() -> None:
    if FAILURES:
        print(f"FTF-0603-F1 real suite lock gate proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0603-F1 real suite lock gate proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
