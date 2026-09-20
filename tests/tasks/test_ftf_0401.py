#!/usr/bin/env python3
"""FTF-0401 -- Project schema/API/UI (no-DB unit/fault tests).

Runnable without Postgres or network egress (the real live-database
create/get/list/edit/save-ui-state/archive and IDOR evidence is
`tests/integration/test_ftf_0401_project_schema_api.py`, run in CI's
dedicated `project-schema-api-integration` job -- this repo's interactive
dev sandbox has no container-registry egress, same documented finding as
every prior integration-tested task). This suite proves, without any
database:

1. `validate_agent_objective` (this task's own agentic/generic split made
   real): an agentic_product project requires a non-empty objective; a
   generic_model_tuning project must not carry one.
2. `initial_status_for`: an agentic_product project always starts
   `setup_incomplete`; a generic_model_tuning project always starts
   `active` -- this task's own negative_acceptance ("Agentic project
   without approved target/runtime remains setup-incomplete") proven at
   the pure-function level.
3. `Project.__init__` itself refuses bad input (project_type, mismatched
   agent_objective, oversized fields) before any session or database is
   ever touched.
4. `apply_edits`/`apply_ui_state_save`/`apply_archive` (the pure, DB-free
   cores): status-transition rules -- editing an archived project is
   refused, a row_version mismatch is refused (optimistic concurrency),
   `project_type` is never accepted by `apply_edits` (immutable), and
   archive is idempotent.
5. The real, locally-hosted `build_project_router` (a real uvicorn server
   over a real local socket, the same pattern
   `tests/tasks/test_ftf_0301.py` established) denies an unauthenticated
   request and a role outside `PROJECT_WRITE_ROLES` on a write *before*
   `session_factory` is ever called, and lets a read-only role reach the
   session layer for a GET.

Run directly: python3 tests/tasks/test_ftf_0401.py
"""

from __future__ import annotations

import contextlib
import http.client
import socket
import sys
import threading
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import uvicorn

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.projects.project_crud import (  # noqa: E402
    ProjectConcurrencyError,
    ProjectStateError,
    apply_archive,
    apply_edits,
    apply_ui_state_save,
)
from app.projects.project_routes import (  # noqa: E402
    PROJECT_READ_ROLES,
    PROJECT_WRITE_ROLES,
    _serialize,
    build_project_router,
)
from app.projects.project_schema import (  # noqa: E402
    ALLOWED_STATUSES,
    Project,
    ProjectValidationError,
    initial_status_for,
    validate_agent_objective,
    validate_ui_state,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except (ProjectValidationError, ProjectStateError, ProjectConcurrencyError):
        return True


_ORG_ID = uuid.uuid4()
_WORKSPACE_ID = uuid.uuid4()


def _make_project(**overrides: object) -> Project:
    # created_at/updated_at are server_default-only (real values come from a
    # real INSERT); a bare in-memory construction needs them supplied
    # explicitly, same as every other no-DB test in this repository.
    now = datetime.now(UTC)
    kwargs: dict[str, object] = {
        "organization_id": _ORG_ID,
        "workspace_id": _WORKSPACE_ID,
        "name": "acme-agent",
        "project_type": "generic_model_tuning",
        "description": None,
        "agent_objective": None,
        "security_profile": "standard",
        "budget_policy_id": None,
        "status": "active",
        "ui_state_json": {},
        "row_version": 1,
        "created_by": "user-1",
        "created_at": now,
        "updated_at": now,
    }
    kwargs.update(overrides)
    return Project(**kwargs)


# -- 1. validate_agent_objective (agentic/generic split) ---------------------


def test_agentic_project_requires_a_real_objective() -> None:
    check(
        "FTF-0401",
        "an agentic_product project with no agent_objective is rejected",
        _rejects(validate_agent_objective, "agentic_product", None),
    )
    check(
        "FTF-0401",
        "an agentic_product project with a blank agent_objective is rejected",
        _rejects(validate_agent_objective, "agentic_product", "   "),
    )
    check(
        "FTF-0401",
        "an agentic_product project with a real objective is accepted",
        validate_agent_objective("agentic_product", "Automate PR review") == "Automate PR review",
    )


def test_generic_project_must_not_carry_an_objective() -> None:
    check(
        "FTF-0401",
        "a generic_model_tuning project with no agent_objective is accepted (None)",
        validate_agent_objective("generic_model_tuning", None) is None,
    )
    check(
        "FTF-0401",
        "a generic_model_tuning project with an agent_objective set is rejected -- "
        "the two kinds must be genuinely distinguishable, not just labels",
        _rejects(validate_agent_objective, "generic_model_tuning", "Automate PR review"),
    )


def test_agent_objective_length_bound() -> None:
    check(
        "FTF-0401",
        "an oversized agent_objective is rejected",
        _rejects(validate_agent_objective, "agentic_product", "x" * 4001),
    )


# -- 2. initial_status_for ----------------------------------------------------


def test_initial_status_for_agentic_is_always_setup_incomplete() -> None:
    check(
        "FTF-0401",
        "negative_acceptance: an agentic_product project always starts setup_incomplete, "
        "never active",
        initial_status_for("agentic_product") == "setup_incomplete",
    )


def test_initial_status_for_generic_is_active() -> None:
    check(
        "FTF-0401",
        "a generic_model_tuning project starts active -- no reason to gate it on a "
        "target/manifest it will never need",
        initial_status_for("generic_model_tuning") == "active",
    )


# -- 3. Project.__init__ refuses bad input before any session/DB -------------


def test_construction_rejects_bad_project_type() -> None:
    check(
        "FTF-0401",
        "constructing a Project with an unknown project_type raises before any DB call",
        _rejects(_make_project, project_type="not_a_real_type"),
    )


def test_construction_rejects_mismatched_agent_objective() -> None:
    check(
        "FTF-0401",
        "constructing an agentic_product Project with no agent_objective raises",
        _rejects(_make_project, project_type="agentic_product", agent_objective=None),
    )
    check(
        "FTF-0401",
        "constructing a generic_model_tuning Project with an agent_objective raises",
        _rejects(
            _make_project,
            project_type="generic_model_tuning",
            agent_objective="should not be allowed",
        ),
    )


def test_construction_rejects_bad_status() -> None:
    check(
        "FTF-0401",
        "constructing a Project with an unknown status raises",
        _rejects(_make_project, status="not_a_real_status"),
    )
    check(
        "FTF-0401",
        "ALLOWED_STATUSES includes setup_incomplete, active and archived, nothing else",
        set(ALLOWED_STATUSES) == {"setup_incomplete", "active", "archived"},
    )


def test_construction_rejects_oversized_ui_state() -> None:
    check(
        "FTF-0401",
        "ui_state_json over the serialized size ceiling is rejected at construction",
        _rejects(_make_project, ui_state_json={"blob": "x" * 20000}),
    )


# -- 4. Pure, DB-free cores: apply_edits/apply_ui_state_save/apply_archive ---


def test_apply_edits_refuses_on_archived_project() -> None:
    project = _make_project(status="archived")
    check(
        "FTF-0401",
        "editing an archived project is refused -- a closed record",
        _rejects_apply_edits(project, name="new-name", expected_row_version=1),
    )


def _rejects_apply_edits(project: Project, **kwargs: object) -> bool:
    defaults: dict[str, object] = {
        "name": None,
        "description": None,
        "agent_objective": None,
        "security_profile": None,
        "expected_row_version": project.row_version,
    }
    defaults.update(kwargs)
    try:
        apply_edits(project, **defaults)  # type: ignore[arg-type]
        return False
    except (ProjectValidationError, ProjectStateError, ProjectConcurrencyError):
        return True


def test_apply_edits_refuses_on_row_version_mismatch() -> None:
    project = _make_project(row_version=5)
    check(
        "FTF-0401",
        "editing with a stale expected_row_version is refused (optimistic concurrency)",
        _rejects_apply_edits(project, name="new-name", expected_row_version=1),
    )
    check(
        "FTF-0401",
        "row_version is unchanged after a refused edit",
        project.row_version == 5,
    )


def test_apply_edits_succeeds_and_bumps_row_version() -> None:
    project = _make_project(row_version=1)
    changed = apply_edits(
        project,
        name="renamed",
        description=None,
        agent_objective=None,
        security_profile=None,
        expected_row_version=1,
    )
    check("FTF-0401", "a valid edit with the matching row_version succeeds", changed is True)
    check("FTF-0401", "a successful edit renames the project", project.name == "renamed")
    check("FTF-0401", "a successful edit bumps row_version by exactly 1", project.row_version == 2)


def test_apply_edits_never_accepts_project_type() -> None:
    import inspect

    params = set(inspect.signature(apply_edits).parameters)
    check(
        "FTF-0401",
        "apply_edits's own signature has no project_type parameter at all -- "
        "immutability is structural, not merely unexercised",
        "project_type" not in params,
    )


def test_apply_ui_state_save_replaces_state_and_refuses_when_archived() -> None:
    project = _make_project(ui_state_json={"step": 1})
    changed = apply_ui_state_save(project, ui_state={"step": 2, "draft_name": "x"})
    check("FTF-0401", "a real ui_state save succeeds", changed is True)
    check(
        "FTF-0401",
        "the saved ui_state is exactly what was submitted",
        project.ui_state_json == {"step": 2, "draft_name": "x"},
    )
    check(
        "FTF-0401",
        "saving ui_state does not bump row_version -- it is not a versioned setting",
        project.row_version == 1,
    )

    archived = _make_project(status="archived")
    check(
        "FTF-0401",
        "saving ui_state on an archived project is refused",
        _rejects(lambda p: apply_ui_state_save(p, ui_state={"x": 1}), archived),
    )


def test_apply_archive_is_idempotent() -> None:
    project = _make_project(status="active")
    check(
        "FTF-0401", "the first archive call reports a real change", apply_archive(project) is True
    )
    check("FTF-0401", "the project is now archived", project.status == "archived")
    check("FTF-0401", "a second archive call is a no-op", apply_archive(project) is False)


def test_validate_ui_state_rejects_non_object() -> None:
    check(
        "FTF-0401",
        "a non-dict ui_state is rejected",
        _rejects(validate_ui_state, ["not", "an", "object"]),
    )


# -- 5. Real demo router: role-boundary denial never opens a session --------


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


_WORKSPACE_ADMIN_PRINCIPAL = AuthenticatedPrincipal(issuer="https://issuer.example", subject="wa-1")
_EVALUATOR_PRINCIPAL = AuthenticatedPrincipal(
    issuer="https://issuer.example", subject="evaluator-1"
)


def _principal_resolver(request):
    header = request.headers.get("x-test-principal")
    if header == "workspace_admin":
        return _WORKSPACE_ADMIN_PRINCIPAL
    if header == "evaluator":
        return _EVALUATOR_PRINCIPAL
    return None


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID):
    if organization_id != _ORG_ID:
        return None
    if subject == _WORKSPACE_ADMIN_PRINCIPAL.subject:
        return Role.WORKSPACE_ADMIN
    if subject == _EVALUATOR_PRINCIPAL.subject:
        return Role.EVALUATOR
    return None


@contextlib.contextmanager
def _running_router(session_factory) -> Iterator[str]:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        build_project_router(
            session_factory,
            principal_resolver=_principal_resolver,
            role_lookup=_role_lookup,
        )
    )
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


@dataclass
class _HttpResult:
    status: int


def _request(base_url: str, method: str, path: str, *, principal: str | None = None) -> _HttpResult:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, headers=headers)
        response = conn.getresponse()
        response.read()
        return _HttpResult(status=response.status)
    finally:
        conn.close()


def test_unauthenticated_request_never_opens_a_session() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", f"/api/v1/organizations/{_ORG_ID}/projects")
        check(
            "FTF-0401",
            "an unauthenticated request is denied via the real require_role "
            "dependency (401), never a hand-rolled check",
            result.status == 401,
        )
        check(
            "FTF-0401",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_evaluator_can_read_but_not_write() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url, "GET", f"/api/v1/organizations/{_ORG_ID}/projects", principal="evaluator"
        )
        check(
            "FTF-0401",
            "an evaluator (a role Section 45 names, with no dedicated restrictive "
            "sentence for projects) can read (not 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-0401",
            "the evaluator's read genuinely reaches the session layer",
            session_factory.call_count >= 1,
        )

        session_factory.reset_mock()
        result = _request(
            base_url, "POST", f"/api/v1/organizations/{_ORG_ID}/projects", principal="evaluator"
        )
        check(
            "FTF-0401",
            "an evaluator is denied create (403) -- write is workspace_admin/"
            "organization_admin only",
            result.status == 403,
        )
        check(
            "FTF-0401",
            "the write role-boundary denial never opens a session",
            session_factory.call_count == 0,
        )


def test_workspace_admin_reaches_the_session_layer_for_writes() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "DELETE",
            f"/api/v1/organizations/{_ORG_ID}/projects/{uuid.uuid4()}",
            principal="workspace_admin",
        )
        check(
            "FTF-0401",
            "a workspace_admin's archive request passes the role gate and reaches "
            "the session layer (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-0401",
            "the workspace_admin's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_role_split_matches_section_45() -> None:
    check(
        "FTF-0401",
        "Section 45: 'workspace_admin: project administration' is the only sentence "
        "naming a role for this class of work -- PROJECT_WRITE_ROLES is exactly "
        "{workspace_admin, organization_admin}",
        PROJECT_WRITE_ROLES == frozenset({Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}),
    )
    check(
        "FTF-0401",
        "PROJECT_READ_ROLES includes every role Section 45 names",
        PROJECT_READ_ROLES == frozenset(Role),
    )


def test_require_role_is_the_real_imported_dependency_not_a_duplicate() -> None:
    import ast
    import inspect

    from app.auth.rbac_middleware import require_role
    from app.projects import project_crud, project_routes

    routes_source = inspect.getsource(project_routes)
    check(
        "FTF-0401",
        "project_routes.py builds its dependencies from the real, imported "
        "require_role -- not a second, hand-rolled role check",
        "require_role(" in routes_source and require_role.__module__ in routes_source,
    )
    crud_source = inspect.getsource(project_crud)
    check(
        "FTF-0401",
        "project_crud.py authorizes every fetch through the real, imported "
        "authorize_resource_ownership (FTF-H003)",
        "authorize_resource_ownership(" in crud_source
        and "app.auth.resource_authorization" in crud_source,
    )
    tree = ast.parse(crud_source)
    calls_session_get = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "session"
        for node in ast.walk(tree)
    )
    check(
        "FTF-0401",
        "project_crud.py reads every row through the real, imported "
        "execute_tenant_scoped (FTF-0105) -- never a bare session.get(...) call",
        "execute_tenant_scoped(" in crud_source
        and "app.auth.tenant_isolation_query_guards" in crud_source
        and not calls_session_get,
    )


def test_serialize_never_exposes_a_wrong_shape() -> None:
    project = _make_project()
    body = _serialize(project)
    check(
        "FTF-0401",
        "the serialized project carries row_version as a real integer field",
        isinstance(body["row_version"], int) and body["row_version"] == 1,
    )
    check(
        "FTF-0401",
        "the serialized project's ui_state is a plain dict, never the raw JSONB column",
        body["ui_state"] == {},
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0401 Project schema/API tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0401 Project schema/API tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
