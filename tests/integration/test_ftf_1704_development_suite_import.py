#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,union-attr,operator"
# ruff: noqa: E501
"""FTF-1704 -- the 300 development cases through the real evaluation-case API on Postgres.

Proves, against a live PostgreSQL server and the real `build_evaluation_case_router`
(the builder `create_app()` mounts) served by a real uvicorn server under the
restricted `sovra_app` role:

  - all 300 generated development cases import into a `development`-purpose
    suite in one call (201), list back with every `group_key`, and the stored rows
    keep the controlled grouping (21 families), the critical flags and the grader
    types;
  - the data survives the round trip: for every stored case the certified
    FTF-0608/0609/0610 graders still pass the case's reference run and fail its
    violation run;
  - a critical case altered to an `exact_match` grader is refused with
    400 `critical_oracle_not_independent` and nothing is inserted;
  - the `final`-purpose suite of the same project is not touched (zero cases);
  - another tenant reaches the suite as the same 404 as a random id.

Scope note (disclosed): the manifest and suites are seeded through the ORM, as
FTF-0602-P1's own integration test does, and requests are authenticated by the
test principal resolver; the live OIDC session path is FTF-0103A's own proof.

Requires `SOVRA_DATABASE_URL` (migration owner) and `SOVRA_APP_DB_PASSWORD`.

Run directly:
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_1704_development_suite_import.py
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
from evals.reference.validation_dataset import (  # noqa: E402
    case_oracle_verdict,
    generate_development_cases,
)
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
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

CID = "FTF-1704-I1"
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
            "Postgres (migration owner connection). Not silently skipped.",
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
    base: str, method: str, path: str, *, principal: str | None, payload: Any = None
) -> _Http:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    body: bytes | None = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(
        parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=120
    )
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return _Http(response.status, response.read())
    finally:
        conn.close()


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    result = _alembic(["upgrade", "head"], migration_url)
    check(
        CID,
        f"alembic upgrade head runs cleanly\n{result.stdout}\n{result.stderr}",
        result.returncode == 0,
    )
    if FAILURES:
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))
    session_factory = sessionmaker(bind=app_engine)
    now = datetime.now(UTC)
    suffix = uuid.uuid4().hex[:8]

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 1704", slug=f"acme-1704-{suffix}")
        org2 = Organization(name="Globex 1704", slug=f"globex-1704-{suffix}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Globex Main", slug="main")
        session.add_all([ws1, ws2])
        session.flush()
        projects = []
        for org, ws, name in ((org1, ws1, "acme-1"), (org2, ws2, "globex-1")):
            projects.append(
                Project(
                    organization_id=org.id,
                    workspace_id=ws.id,
                    name=name,
                    project_type="agentic_product",
                    status="active",
                    security_profile="standard",
                    row_version=1,
                    created_by="seed",
                )
            )
        session.add_all(projects)
        session.flush()
        p1, p2 = projects
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
        session.add(conn1)
        session.flush()
        snap1 = TargetSourceSnapshot(
            organization_id=org1.id,
            target_application_connection_id=conn1.id,
            commit_sha="1" * 40,
            tree_hash="2" * 40,
            capture_method="github_resolved_commit",
        )
        session.add(snap1)
        session.flush()
        manifest = AgentManifest(
            organization_id=org1.id,
            project_id=p1.id,
            target_source_snapshot_id=snap1.id,
            version=1,
            status="locked",
            agent_name="Reference agent",
            agent_entrypoint_json={"entry": "src/agent.py"},
            current_model_binding_json={"model": "reference"},
            runtime_dependencies_json={"python": "3.11"},
            mandate_json={"mandate": "help"},
            manifest_sha256="a" * 64,
            created_by="seed",
            approved_by="approver-seed",
            approved_at=now,
        )
        session.add(manifest)
        session.flush()
        suites = {}
        for version, purpose in ((1, "development"), (2, "final")):
            suite = EvaluationSuite(
                organization_id=org1.id,
                project_id=p1.id,
                agent_manifest_id=manifest.id,
                version=version,
                status="draft",
                purpose=purpose,
                name=f"{purpose}-suite",
                grading_logic_version=1,
                created_by="creator-1",
            )
            session.add(suite)
            session.flush()
            suites[purpose] = suite.id
        session.commit()
        org1_id, org2_id, p1_id, p2_id = org1.id, org2.id, p1.id, p2.id
        dev_suite, final_suite = suites["development"], suites["final"]

    principals = {
        "o1-evaluator": (
            AuthenticatedPrincipal(issuer=_ISSUER, subject="o1-evaluator"),
            Role.EVALUATOR,
            org1_id,
        ),
        "o2-evaluator": (
            AuthenticatedPrincipal(issuer=_ISSUER, subject="o2-evaluator"),
            Role.EVALUATOR,
            org2_id,
        ),
    }

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
        build_evaluation_case_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    def path(org: uuid.UUID, project: uuid.UUID, suite: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org}/projects/{project}/evaluation-suites/{suite}/cases"

    def count(suite: uuid.UUID) -> int:
        with Session(owner_engine) as s:
            return int(
                s.execute(
                    text("SELECT count(*) FROM evaluation_cases WHERE suite_id = :s"), {"s": suite}
                ).scalar()
            )

    cases = generate_development_cases()
    check(CID, "setup: 300 development cases generated", len(cases) == 300)

    with _serve(app) as base:
        r = _request(
            base,
            "POST",
            path(org1_id, p1_id, dev_suite),
            principal="o1-evaluator",
            payload={"cases": cases[:1] + [{**cases[4], "grader_type": "exact_match"}]},
        )
        check(
            CID,
            "a critical case with an exact_match grader -> 400 critical_oracle_not_independent",
            r.status == 400
            and any(
                e.get("code") == "critical_oracle_not_independent"
                for e in r.json().get("errors", [])
            ),
        )
        check(CID, "...and nothing was inserted (all-or-nothing)", count(dev_suite) == 0)

        r = _request(
            base,
            "POST",
            path(org1_id, p1_id, dev_suite),
            principal="o1-evaluator",
            payload={"cases": cases},
        )
        check(
            CID,
            f"all 300 development cases import -> 201 ({r.status})",
            r.status == 201 and len(r.json()["items"]) == 300,
        )
        check(CID, "300 rows in Postgres", count(dev_suite) == 300)

        listed: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            query = "?limit=500" + (f"&after={after}" if after else "")
            page = _request(
                base, "GET", path(org1_id, p1_id, dev_suite) + query, principal="o1-evaluator"
            )
            check(CID, "listing returns 200", page.status == 200)
            if page.status != 200:
                break
            listed.extend(page.json()["items"])
            after = page.json()["next_after"]
            if after is None:
                break
        by_key = {c["case_key"]: c for c in listed}
        check(CID, "GET lists all 300 cases", len(listed) == 300 and len(by_key) == 300)
        check(
            CID,
            "every listed case keeps its group_key, critical flag, severity and grader type",
            all(
                by_key[c["case_key"]]["group_key"] == c["group_key"]
                and by_key[c["case_key"]]["critical"] == c["critical"]
                and by_key[c["case_key"]]["severity"] == c["severity"]
                and by_key[c["case_key"]]["grader_type"] == c["grader_type"]
                for c in cases
            ),
        )
        check(
            CID,
            "controlled grouping: 21 family group keys",
            len({c["group_key"] for c in listed}) == 21,
        )
        check(
            CID,
            "every stored critical case uses an independent grader",
            all(
                c["grader_type"] in {"forbidden_action", "required_refusal"}
                for c in listed
                if c["critical"]
            )
            and sum(1 for c in listed if c["critical"]) == sum(1 for c in cases if c["critical"]),
        )
        round_trip_ok = all(
            case_oracle_verdict(by_key[c["case_key"]], "reference")
            and not case_oracle_verdict(by_key[c["case_key"]], "violation")
            for c in cases
        )
        check(
            CID,
            "after the database round trip the certified graders still pass every reference run and fail every violation run",
            round_trip_ok,
        )

        check(CID, "the final-purpose suite was not touched", count(final_suite) == 0)

        missing = _request(
            base,
            "GET",
            path(org2_id, p2_id, uuid.uuid4()),
            principal="o2-evaluator",
        )
        foreign = _request(
            base,
            "GET",
            path(org2_id, p2_id, dev_suite),
            principal="o2-evaluator",
        )
        check(
            CID,
            "another tenant reaching the suite gets the same 404 as a random id",
            missing.status == 404 and foreign.status == 404 and missing.body == foreign.body,
        )
        check(CID, "...and the foreign attempt changed nothing", count(dev_suite) == 300)

    if FAILURES:
        print(f"FTF-1704 development suite import: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-1704 development suite import: all checks passed")


if __name__ == "__main__":
    main()
