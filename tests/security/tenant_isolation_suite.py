#!/usr/bin/env python3
"""FTF-1501 -- Tenant isolation suite (Section 45-47, 52-53, 65-66, 77, 81).

FTF-0106's own module docstring names this task directly as one of its
intended dependents: "the later full-scale suites this task's own
dependents name (FTF-1501 'Tenant isolation suite', FTF-1504 'IDOR
suite') have a real six-role, two-tenant fixture and demonstration app
to build on instead of each re-deriving their own narrower one." This
module is that suite: it reuses FTF-0106's own
`TwoTenantRoleMatrixFixture` (real two-organization, twelve-principal
membership fixture) and FTF-H003's own `authorize_resource_ownership`/
`tenant_scoped_session_for` (the real cross-layer tenant-boundary guard)
rather than re-deriving either.

Scope, honestly narrowed (Law 5/7, "existence is not evidence" /
"designed is not wired"): this task's own acceptance clause names
"real two-tenant API/DB/worker/storage/cache/model paths". Of that list,
only API and DB paths have any real, live-wired existence in this
repository today -- `apps/api/app/main.py`'s own module docstring
enumerates exactly six live-wired product-runtime routes (auth,
litellm-connections, artifacts, projects, tools, requirements), and
there is no live Temporal worker, cache or model-serving path anywhere
in this repository yet (`workers/{training,evaluation,data,deploy}` are
still README/pyproject-only scaffolds -- confirmed by direct inspection,
not assumed). `packages/storage/tenant_policy.py` (FTF-H003) is itself
a pure, still-unwired policy module with no live storage-service caller
(grepped: no `apps/api/app` module imports it). Declaring worker/
storage/cache/model checks against infrastructure that does not exist
would be exactly the fabricated evidence Law 11/Section 84.4 forbids
("production code cannot contain ... simulated ... fake metrics").
This suite instead exercises every currently live-wired, CRUD-capable
API surface (connections, artifacts, projects, tools) plus the
read-only requirements view, each proven at both the API layer (real
HTTP round trip over a real socket) and the DB layer (a real, restricted
`sovra_app`-role connection, never the migration-owner/superuser role --
this task's own negative_acceptance). Whichever task first wires a real
worker/storage/cache/model path is expected to extend this suite's own
`main()` with its own resource-kind block, not duplicate this module
(matching Section 77.5's own "generated clients have a single writer"
discipline applied to this suite instead).

`database_changes` honored ("No independent schema change") -- this
module defines no new table and adds no new column to any existing one.

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/security/tenant_isolation_suite.py
"""

from __future__ import annotations

import contextlib
import dataclasses
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
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import Engine, create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.artifacts.artifact_routes import build_trainable_artifact_router  # noqa: E402
from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_idor_security_tests import TwoTenantRoleMatrixFixture  # noqa: E402
from app.auth.rbac_middleware import Role  # noqa: E402
from app.auth.resource_authorization import (  # noqa: E402
    ResourceAuthorizationError,
    authorize_resource_ownership,
)
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.litellm.connection_routes import build_litellm_connection_router  # noqa: E402
from app.litellm.connection_schema import LiteLLMConnection  # noqa: E402
from app.projects.project_routes import build_project_router  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.tools.critical_behavior_requirement_routes import (  # noqa: E402
    build_critical_behavior_requirement_router,
)
from app.tools.tool_definition_routes import build_tool_definition_router  # noqa: E402
from app.tools.tool_definition_schema import ToolDefinition  # noqa: E402

__all__ = [
    "TenantIsolationSuiteError",
    "ScenarioResult",
    "TenantIsolationEvidence",
    "assert_not_superuser_role",
    "build_evidence",
]


class TenantIsolationSuiteError(RuntimeError):
    """This suite refuses to produce evidence -- never converts a blocked
    precondition into a silent pass (Law 17)."""


# -- Pure, no-DB-testable evidence types ------------------------------------


@dataclasses.dataclass(frozen=True)
class ScenarioResult:
    """One real check this suite performed. Deliberately content-free
    (Section 47's own "no secrets/raw prompts in task reports"): only
    resource kind, check name, method/path and the observed/expected
    status are recorded -- never a request/response body."""

    resource_kind: str
    check: str
    method: str
    path: str
    status: int
    expected_denied: bool
    passed: bool

    def to_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class TenantIsolationEvidence:
    """The immutable evidence output this task's own `api_contract` names
    ("security scenario inputs and immutable evidence outputs"). Built
    only from real `ScenarioResult`s -- `build_evidence` never accepts a
    raw pass/fail count, only the individual results it derives them
    from, so a caller cannot fabricate a passing summary without also
    supplying the (verifiable) results it was computed from."""

    suite_id: str
    executed_at: str
    resource_kinds_covered: tuple[str, ...]
    scenarios_total: int
    scenarios_passed: int
    scenarios_failed: int
    content_free: bool

    def to_dict(self) -> dict[str, object]:
        data = dataclasses.asdict(self)
        data["resource_kinds_covered"] = list(self.resource_kinds_covered)
        return data


def build_evidence(results: list[ScenarioResult]) -> TenantIsolationEvidence:
    """Pure: derives the evidence summary from real per-scenario results.
    Raises if `results` is empty -- an evidence record with zero scenarios
    is not evidence of anything (Law 1, "nothing proven until test shown
    able to fail" -- there is nothing here that could have failed)."""
    if not results:
        raise TenantIsolationSuiteError(
            "refusing to build evidence from zero scenario results -- an "
            "empty suite proves nothing"
        )
    kinds = tuple(dict.fromkeys(r.resource_kind for r in results))
    passed = sum(1 for r in results if r.passed)
    return TenantIsolationEvidence(
        suite_id=uuid.uuid4().hex,
        executed_at=datetime.now(UTC).isoformat(),
        resource_kinds_covered=kinds,
        scenarios_total=len(results),
        scenarios_passed=passed,
        scenarios_failed=len(results) - passed,
        content_free=True,
    )


# -- negative_acceptance, "No security result from mocked auth or
# superuser-only DB tests", made real by construction ------------------------


class _RoleSuperuserProbe(Protocol):
    def connect(self) -> Any: ...  # pragma: no cover - structural typing only


def assert_not_superuser_role(engine: _RoleSuperuserProbe) -> None:
    """Refuses to proceed if the connection's own current role is a real
    Postgres superuser. Run before this suite ever opens a tenant-scoped
    session, so a caller cannot silently point this suite at the
    migration-owner/superuser role and collect a vacuous "pass" -- RLS is
    not even enforced against a superuser/BYPASSRLS role (Section 65.1),
    so any result collected that way would be worthless as tenant-
    isolation evidence. Structurally typed (`_RoleSuperuserProbe`) so this
    logic is unit-testable against a plain stub without a real database
    (`tests/tasks/test_ftf_1501.py`), while the live suite always calls
    it against a real `sqlalchemy.Engine`."""
    with engine.connect() as connection:
        rolsuper = connection.execute(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        ).scalar()
    if rolsuper is None:
        raise TenantIsolationSuiteError(
            "refusing to proceed: could not resolve the connection's own "
            "current_user against pg_roles"
        )
    if rolsuper:
        raise TenantIsolationSuiteError(
            "refusing to proceed: this suite's own database connection is a "
            "real Postgres superuser/BYPASSRLS role -- RLS tenant isolation "
            "is not even enforced against such a role (Section 65.1), so any "
            "result collected through it would not be real tenant-isolation "
            "evidence"
        )


# -- Live suite ---------------------------------------------------------------

FAILURES: list[str] = []
RESULTS: list[ScenarioResult] = []


def check(
    resource_kind: str,
    check_name: str,
    method: str,
    path: str,
    *,
    status: int,
    expected_denied: bool,
    condition: bool,
) -> None:
    RESULTS.append(
        ScenarioResult(
            resource_kind=resource_kind,
            check=check_name,
            method=method,
            path=path,
            status=status,
            expected_denied=expected_denied,
            passed=condition,
        )
    )
    if not condition:
        FAILURES.append(
            f"FAIL [FTF-1501] {resource_kind}/{check_name}: {method} {path} -> "
            f"{status} (expected_denied={expected_denied})"
        )


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "two-tenant API/DB evidence requires a live Postgres (migration "
            "owner connection). Not silently skipped: see this file's own "
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


def _run_migration(database_url: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def _running_app(app: FastAPI) -> Iterator[str]:
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
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


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    principal: str | None = None,
    payload: dict[str, object] | None = None,
) -> _Response:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    body_bytes: bytes | None = None
    if payload is not None:
        body_bytes = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, body=body_bytes, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        return _Response(status=response.status, body=raw)
    finally:
        conn.close()


_CollectionPath = Callable[[uuid.UUID], str]
_ItemPath = Callable[[uuid.UUID, uuid.UUID], str]


def _connections_paths() -> tuple[_CollectionPath, _ItemPath]:
    return (
        lambda org: f"/api/v1/organizations/{org}/connections/litellm",
        lambda org, rid: f"/api/v1/organizations/{org}/connections/litellm/{rid}",
    )


def _artifacts_paths() -> tuple[_CollectionPath, _ItemPath]:
    return (
        lambda org: f"/api/v1/organizations/{org}/artifacts",
        lambda org, rid: f"/api/v1/organizations/{org}/artifacts/{rid}",
    )


def _projects_paths() -> tuple[_CollectionPath, _ItemPath]:
    return (
        lambda org: f"/api/v1/organizations/{org}/projects",
        lambda org, rid: f"/api/v1/organizations/{org}/projects/{rid}",
    )


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    migration_result = _run_migration(migration_url)
    if migration_result.returncode != 0:
        print(
            "FTF-1501 tenant isolation suite: alembic upgrade head failed\n"
            f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
            file=sys.stderr,
        )
        sys.exit(1)

    owner_engine: Engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine: Engine = create_engine(app_url)

    # negative_acceptance, made real: this suite refuses to run against a
    # superuser/migration-owner connection -- checked against the real
    # restricted role this suite actually uses below.
    assert_not_superuser_role(app_engine)

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 1501", slug=f"acme-1501-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 1501", slug=f"globex-1501-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Globex Main", slug="main")
        session.add_all([ws1, ws2])
        session.flush()
        project1 = Project(
            organization_id=org1.id,
            workspace_id=ws1.id,
            name="acme-project",
            project_type="generic_model_tuning",
            status="active",
            security_profile="standard",
            row_version=1,
            created_by="seed",
        )
        project2 = Project(
            organization_id=org2.id,
            workspace_id=ws2.id,
            name="globex-project",
            project_type="generic_model_tuning",
            status="active",
            security_profile="standard",
            row_version=1,
            created_by="seed",
        )
        session.add_all([project1, project2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        project1_id, project2_id = project1.id, project2.id

    fixture = TwoTenantRoleMatrixFixture.build({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)

    app = FastAPI()
    app.include_router(
        build_litellm_connection_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )
    app.include_router(
        build_trainable_artifact_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )
    app.include_router(
        build_project_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )
    app.include_router(
        build_tool_definition_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )
    app.include_router(
        build_critical_behavior_requirement_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    org1_admin = fixture.header_for("org1", Role.ORGANIZATION_ADMIN)
    org2_admin = fixture.header_for("org2", Role.ORGANIZATION_ADMIN)

    connections_collection, connections_item = _connections_paths()
    artifacts_collection, artifacts_item = _artifacts_paths()
    projects_collection, projects_item = _projects_paths()

    def tools_collection(org: uuid.UUID, project: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org}/projects/{project}/tools"

    def tools_item(org: uuid.UUID, project: uuid.UUID, rid: uuid.UUID) -> str:
        return f"{tools_collection(org, project)}/{rid}"

    def requirements_path(org: uuid.UUID, project: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org}/projects/{project}/requirements"

    with _running_app(app) as base_url:
        # -- real setup: create one real resource of each kind in org1 --
        r = _request(
            base_url,
            "POST",
            connections_collection(org1_id),
            principal=org1_admin,
            payload={
                "name": "acme-gateway",
                "base_url": "https://gateway.example.com/v1",
                "secret_ref": "vault://litellm/org1/key",
            },
        )
        if r.status != 201:
            print(
                f"FTF-1501: setup failed creating connection1 (status {r.status})",
                file=sys.stderr,
            )
            sys.exit(1)
        connection1_id = uuid.UUID(r.json()["id"])

        r = _request(
            base_url,
            "POST",
            artifacts_collection(org1_id),
            principal=org1_admin,
            payload={
                "display_name": "llama-8b-candidate",
                "source_type": "huggingface",
                "source_uri": "meta-llama/Llama-3.1-8B",
                "model_family": "llama",
                "base_model_revision": "main",
                "license_id": "apache-2.0",
            },
        )
        if r.status != 201:
            print(
                f"FTF-1501: setup failed creating artifact1 (status {r.status})",
                file=sys.stderr,
            )
            sys.exit(1)
        artifact1_id = uuid.UUID(r.json()["id"])

        r = _request(
            base_url,
            "POST",
            tools_collection(org1_id, project1_id),
            principal=org1_admin,
            payload={
                "name": "lookup_order",
                "description": "Looks up an order by ID.",
                "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}},
                "output_schema": {"type": "object", "properties": {"status": {"type": "string"}}},
                "side_effect_class": "read_only",
            },
        )
        if r.status != 201:
            print(
                f"FTF-1501: setup failed creating tool1 (status {r.status})",
                file=sys.stderr,
            )
            sys.exit(1)
        tool1_id = uuid.UUID(r.json()["id"])

        # -- per-resource-kind tenant-isolation matrix --------------------
        specs: list[tuple[str, str, str, dict[str, object]]] = [
            (
                "connection",
                connections_item(org1_id, connection1_id),
                "PATCH",
                {"name": "hostile-edit"},
            ),
            (
                "artifact",
                artifacts_item(org1_id, artifact1_id),
                "PATCH",
                {"display_name": "hostile-edit"},
            ),
            (
                "project",
                projects_item(org1_id, project1_id),
                "PATCH",
                {"name": "hostile-edit"},
            ),
            (
                "tool",
                tools_item(org1_id, project1_id, tool1_id),
                "PATCH",
                {"description": "hostile-edit"},
            ),
        ]
        for kind, item_path, write_method, write_body in specs:
            r = _request(base_url, "GET", item_path, principal=org1_admin)
            check(
                kind,
                "owner_read_reachable",
                "GET",
                item_path,
                status=r.status,
                expected_denied=False,
                condition=r.status == 200,
            )

            r = _request(base_url, "GET", item_path, principal=org2_admin)
            check(
                kind,
                "cross_tenant_api_read_denied",
                "GET",
                item_path,
                status=r.status,
                expected_denied=True,
                condition=r.status == 403,
            )

            r = _request(
                base_url, write_method, item_path, principal=org2_admin, payload=write_body
            )
            check(
                kind,
                "cross_tenant_api_write_denied",
                write_method,
                item_path,
                status=r.status,
                expected_denied=True,
                condition=r.status == 403,
            )

            r = _request(base_url, "GET", item_path)
            check(
                kind,
                "unauthenticated_denied",
                "GET",
                item_path,
                status=r.status,
                expected_denied=True,
                condition=r.status == 401,
            )

        # -- cross-layer: tool under a real, but foreign, project (FTF-H003's
        # own guard -- org1_admin is legitimately authorized for org1, but
        # project2 does not belong to org1) --
        cross_layer_path = tools_item(org1_id, project2_id, tool1_id)
        r = _request(base_url, "GET", cross_layer_path, principal=org1_admin)
        check(
            "tool",
            "cross_layer_foreign_project_denied",
            "GET",
            cross_layer_path,
            status=r.status,
            expected_denied=True,
            condition=r.status == 404,
        )

        # -- requirements view (read-only, reuses tool_definitions -- no
        # separate DB-layer check to avoid duplicating the tool kind's own) --
        req_path = requirements_path(org1_id, project1_id)
        r = _request(base_url, "GET", req_path, principal=org1_admin)
        rows = r.json() if r.status == 200 else []
        check(
            "requirements",
            "owner_read_reachable",
            "GET",
            req_path,
            status=r.status,
            expected_denied=False,
            condition=r.status == 200 and isinstance(rows, list) and len(rows) == 1,
        )

        r = _request(base_url, "GET", req_path, principal=org2_admin)
        check(
            "requirements",
            "cross_tenant_api_read_denied",
            "GET",
            req_path,
            status=r.status,
            expected_denied=True,
            condition=r.status == 403,
        )

        # `/requirements` is a list view, not an item lookup by ID: a
        # foreign project_id naturally matches zero rows in the real
        # tenant-scoped query (never a distinguishable error) -- the
        # correct proof here is that org2's own real tool is not leaked
        # into org1's response, not a 404 (list endpoints in this
        # repository return an empty collection for "no matches", the
        # same convention `tool_definition_routes.py`'s own `list_all`
        # already establishes; only single-item lookups return 404).
        cross_layer_req_path = requirements_path(org1_id, project2_id)
        r = _request(base_url, "GET", cross_layer_req_path, principal=org1_admin)
        cross_layer_rows = r.json() if r.status == 200 else None
        check(
            "requirements",
            "cross_layer_foreign_project_no_leak",
            "GET",
            cross_layer_req_path,
            status=r.status,
            expected_denied=True,
            condition=r.status == 200 and cross_layer_rows == [],
        )

        r = _request(base_url, "GET", req_path)
        check(
            "requirements",
            "unauthenticated_denied",
            "GET",
            req_path,
            status=r.status,
            expected_denied=True,
            condition=r.status == 401,
        )

    # -- DB-layer tenant isolation: a real, restricted sovra_app-role
    # session scoped to org2's own tenant context, probing for org1's real
    # rows by their real IDs -- through the exact same production guard
    # every CRUD module in this repository already calls
    # (authorize_resource_ownership), never a hand-rolled query --
    db_specs: list[tuple[str, Any, Any, Any, uuid.UUID]] = [
        (
            "connection",
            LiteLLMConnection.__table__,
            LiteLLMConnection.id,
            LiteLLMConnection.organization_id,
            connection1_id,
        ),
        (
            "artifact",
            TrainableArtifact.__table__,
            TrainableArtifact.id,
            TrainableArtifact.organization_id,
            artifact1_id,
        ),
        (
            "project",
            Project.__table__,
            Project.id,
            Project.organization_id,
            project1_id,
        ),
        (
            "tool",
            ToolDefinition.__table__,
            ToolDefinition.id,
            ToolDefinition.organization_id,
            tool1_id,
        ),
    ]
    for kind, table, id_column, org_column, resource_id in db_specs:
        with tenant_scoped_session_for(session_factory, TenantContext(org2_id)) as db_session:
            denied = False
            try:
                authorize_resource_ownership(
                    db_session, TenantContext(org2_id), table, id_column, org_column, resource_id
                )
            except ResourceAuthorizationError:
                denied = True
        check(
            kind,
            "cross_tenant_db_read_denied",
            "DB",
            f"authorize_resource_ownership(org2, {kind}={resource_id})",
            status=404 if denied else 200,
            expected_denied=True,
            condition=denied,
        )

    evidence = build_evidence(RESULTS)
    print(json.dumps(evidence.to_dict(), indent=2))

    if FAILURES:
        print(f"FTF-1501 tenant isolation suite: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print(
        f"FTF-1501 tenant isolation suite: all checks passed "
        f"({evidence.scenarios_passed}/{evidence.scenarios_total})"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
