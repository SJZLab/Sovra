#!/usr/bin/env python3
"""FTF-0404 -- Tool schema CRUD (no-DB unit/fault tests).

Runnable without Postgres or network egress (the real live-database
create/get/list/edit and IDOR/versioning evidence is
`tests/integration/test_ftf_0404_tool_schema_crud.py`, run in CI's
dedicated `tool-schema-crud-integration` job -- this repo's interactive
dev sandbox has no container-registry egress, same documented finding as
every prior integration-tested task). This suite proves, without any
database:

1. `validate_tool_name`/`validate_side_effect_class`/`validate_schema_object`:
   a tool's own name/side-effect-class/schema shape is a real, bounded,
   closed-set validation, never silently accepted.
2. `compute_schema_hash`: deterministic and order-independent; a real
   content change to either schema produces a different hash.
3. `ToolDefinition.__init__` itself refuses bad input (name, side-effect
   class, oversized schema) before any session or database is ever
   touched.
4. `apply_new_version` (the pure, DB-free core) -- this task's own
   negative_acceptance ("Editing used tool schema creates new version and
   drift, never changes historical manifest in place") proven at the
   pure-function level: editing produces a genuinely new, separate row
   (version+1, previous_version_id set); the row it replaces is left with
   its own content fields byte-identical, only superseded_at/updated_at
   change; editing an already-superseded row is refused.
5. The real, locally-hosted `build_tool_definition_router` (a real
   uvicorn server over a real local socket, the same pattern
   `tests/tasks/test_ftf_0401.py` established) denies an unauthenticated
   request and a role outside `TOOL_WRITE_ROLES` on a write *before*
   `session_factory` is ever called, and lets a read-only role reach the
   session layer for a GET.

Run directly: python3 tests/tasks/test_ftf_0404.py
"""

from __future__ import annotations

import contextlib
import copy
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
from app.tools.tool_definition_crud import (  # noqa: E402
    ToolDefinitionStateError,
    apply_new_version,
)
from app.tools.tool_definition_routes import (  # noqa: E402
    TOOL_READ_ROLES,
    TOOL_WRITE_ROLES,
    _serialize,
    build_tool_definition_router,
)
from app.tools.tool_definition_schema import (  # noqa: E402
    ALLOWED_SIDE_EFFECT_CLASSES,
    ToolDefinition,
    ToolDefinitionValidationError,
    compute_schema_hash,
    validate_schema_object,
    validate_side_effect_class,
    validate_tool_name,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except (ToolDefinitionValidationError, ToolDefinitionStateError):
        return True


_ORG_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()


def _make_tool(**overrides: object) -> ToolDefinition:
    now = datetime.now(UTC)
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
    }
    output_schema: dict[str, object] = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
    }
    kwargs: dict[str, object] = {
        "organization_id": _ORG_ID,
        "project_id": _PROJECT_ID,
        "name": "lookup_order",
        "description": "Looks up an order by ID.",
        "input_schema_json": input_schema,
        "output_schema_json": output_schema,
        "side_effect_class": "read_only",
        "requires_confirmation": False,
        "is_critical": False,
        "version": 1,
        "schema_hash": compute_schema_hash(input_schema, output_schema),
        "previous_version_id": None,
        "superseded_at": None,
        "created_by": "user-1",
        "created_at": now,
        "updated_at": now,
    }
    kwargs.update(overrides)
    return ToolDefinition(**kwargs)


# -- 1. validate_tool_name / validate_side_effect_class / validate_schema_object -


def test_validate_tool_name_accepts_real_identifiers() -> None:
    check(
        "FTF-0404",
        "a real snake_case identifier is accepted",
        validate_tool_name("lookup_order") == "lookup_order",
    )
    check(
        "FTF-0404",
        "a leading underscore is accepted",
        validate_tool_name("_private_tool") == "_private_tool",
    )


def test_validate_tool_name_rejects_bad_shapes() -> None:
    check("FTF-0404", "an empty name is rejected", _rejects(validate_tool_name, ""))
    check("FTF-0404", "a non-string name is rejected", _rejects(validate_tool_name, None))
    check(
        "FTF-0404",
        "a name starting with a digit is rejected",
        _rejects(validate_tool_name, "1_bad_name"),
    )
    check(
        "FTF-0404",
        "a name with a hyphen (not a valid identifier character) is rejected",
        _rejects(validate_tool_name, "bad-name"),
    )
    check(
        "FTF-0404",
        "a name with a space is rejected",
        _rejects(validate_tool_name, "bad name"),
    )
    check(
        "FTF-0404",
        "an oversized name is rejected",
        _rejects(validate_tool_name, "x" * 201),
    )


def test_validate_side_effect_class_is_the_closed_section_6_9_set() -> None:
    check(
        "FTF-0404",
        "ALLOWED_SIDE_EFFECT_CLASSES is exactly Section 6.9's own five classes",
        set(ALLOWED_SIDE_EFFECT_CLASSES)
        == {"read_only", "reversible_write", "irreversible_write", "financial", "privileged"},
    )
    for cls in ALLOWED_SIDE_EFFECT_CLASSES:
        check(
            "FTF-0404",
            f"validate_side_effect_class accepts the real class {cls!r}",
            validate_side_effect_class(cls) == cls,
        )
    check(
        "FTF-0404",
        "an unrecognized side_effect_class is refused, not silently accepted",
        _rejects(validate_side_effect_class, "dangerous"),
    )


def test_validate_schema_object_rejects_non_object_and_oversized() -> None:
    check(
        "FTF-0404",
        "a non-dict schema is rejected",
        _rejects(validate_schema_object, ["not", "an", "object"], "input_schema_json"),
    )
    check(
        "FTF-0404",
        "an oversized schema is rejected",
        _rejects(validate_schema_object, {"blob": "x" * 70000}, "input_schema_json"),
    )
    check(
        "FTF-0404",
        "a real, bounded schema object round-trips unchanged",
        validate_schema_object({"type": "object"}, "input_schema_json") == {"type": "object"},
    )


# -- 2. compute_schema_hash --------------------------------------------------


def test_compute_schema_hash_is_deterministic_and_order_independent() -> None:
    a = compute_schema_hash({"type": "object", "x": 1}, {"type": "object", "y": 2})
    b = compute_schema_hash({"x": 1, "type": "object"}, {"y": 2, "type": "object"})
    check(
        "FTF-0404",
        "schema_hash is order-independent -- key order never changes the digest",
        a == b,
    )


def test_compute_schema_hash_changes_with_real_content() -> None:
    a = compute_schema_hash({"type": "object"}, {"type": "object"})
    b = compute_schema_hash({"type": "string"}, {"type": "object"})
    check(
        "FTF-0404",
        "a real content change to either schema produces a different hash",
        a != b,
    )


# -- 3. ToolDefinition.__init__ refuses bad input before any session/DB -----


def test_construction_rejects_bad_name() -> None:
    check(
        "FTF-0404",
        "constructing a ToolDefinition with an invalid name raises before any DB call",
        _rejects(_make_tool, name="bad name!"),
    )


def test_construction_rejects_bad_side_effect_class() -> None:
    check(
        "FTF-0404",
        "constructing a ToolDefinition with an unknown side_effect_class raises",
        _rejects(_make_tool, side_effect_class="dangerous"),
    )


def test_construction_rejects_oversized_schema() -> None:
    check(
        "FTF-0404",
        "constructing a ToolDefinition with an oversized input_schema_json raises",
        _rejects(_make_tool, input_schema_json={"blob": "x" * 70000}),
    )


# -- 4. apply_new_version: the negative_acceptance core ----------------------


def test_apply_new_version_produces_a_genuinely_new_row() -> None:
    current = _make_tool(version=1)
    new_version = apply_new_version(
        current,
        description="Looks up an order by ID (v2).",
        input_schema_json=None,
        output_schema_json=None,
        side_effect_class=None,
        requires_confirmation=None,
        is_critical=None,
        created_by="user-2",
    )
    check(
        "FTF-0404",
        "the new version is a genuinely different object, not the same row re-tagged",
        new_version is not current,
    )
    check("FTF-0404", "the new version's version is exactly current+1", new_version.version == 2)
    check(
        "FTF-0404",
        "the new version's previous_version_id threads back to the row it replaced",
        new_version.previous_version_id == current.id,
    )
    check(
        "FTF-0404",
        "the new version is itself the new current version (superseded_at is None)",
        new_version.superseded_at is None,
    )
    check(
        "FTF-0404",
        "an omitted field on edit inherits the current version's own value",
        new_version.input_schema_json == current.input_schema_json
        and new_version.name == current.name,
    )
    check(
        "FTF-0404",
        "a real edited field is applied to the new version",
        new_version.description == "Looks up an order by ID (v2).",
    )


def test_apply_new_version_never_mutates_the_historical_row_content() -> None:
    # negative_acceptance, made real at the pure-function level: editing a
    # tool schema must never change the historical row's own manifest
    # content in place -- only superseded_at/updated_at may change on it.
    current = _make_tool(version=1)
    original_input_schema = copy.deepcopy(current.input_schema_json)
    original_output_schema = copy.deepcopy(current.output_schema_json)
    original_description = current.description
    original_side_effect_class = current.side_effect_class
    original_name = current.name

    apply_new_version(
        current,
        description="a completely different description",
        input_schema_json={"type": "object", "properties": {"totally": {"type": "boolean"}}},
        output_schema_json={"type": "object", "properties": {"also": {"type": "boolean"}}},
        side_effect_class="irreversible_write",
        requires_confirmation=True,
        is_critical=True,
        created_by="attacker-attempting-drift",
    )

    check(
        "FTF-0404",
        "negative_acceptance: the historical row's own input_schema_json is "
        "untouched by an edit that changed it on the NEW version",
        current.input_schema_json == original_input_schema,
    )
    check(
        "FTF-0404",
        "negative_acceptance: the historical row's own output_schema_json is untouched",
        current.output_schema_json == original_output_schema,
    )
    check(
        "FTF-0404",
        "negative_acceptance: the historical row's own description is untouched",
        current.description == original_description,
    )
    check(
        "FTF-0404",
        "negative_acceptance: the historical row's own side_effect_class is untouched",
        current.side_effect_class == original_side_effect_class,
    )
    check(
        "FTF-0404",
        "the historical row's own name is untouched (never renamed by an edit)",
        current.name == original_name,
    )
    check(
        "FTF-0404",
        "the historical row IS marked superseded -- this is the only real change to it",
        current.superseded_at is not None,
    )


def test_apply_new_version_refuses_to_edit_a_superseded_row() -> None:
    now = datetime.now(UTC)
    already_superseded = _make_tool(version=1, superseded_at=now)
    check(
        "FTF-0404",
        "negative_acceptance: editing an already-superseded (historical) "
        "version is refused -- only the current version can be edited",
        _rejects(
            apply_new_version,
            already_superseded,
            description="should not be allowed",
            input_schema_json=None,
            output_schema_json=None,
            side_effect_class=None,
            requires_confirmation=None,
            is_critical=None,
            created_by="user-1",
        ),
    )


def test_apply_new_version_recomputes_schema_hash_on_real_change() -> None:
    current = _make_tool(version=1)
    original_hash = current.schema_hash
    new_version = apply_new_version(
        current,
        description=None,
        input_schema_json={"type": "object", "properties": {"new_field": {"type": "string"}}},
        output_schema_json=None,
        side_effect_class=None,
        requires_confirmation=None,
        is_critical=None,
        created_by="user-1",
    )
    check(
        "FTF-0404",
        "a real schema content change produces a genuinely different schema_hash",
        new_version.schema_hash != original_hash,
    )
    check(
        "FTF-0404",
        "the historical row's own schema_hash is untouched",
        current.schema_hash == original_hash,
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
        build_tool_definition_router(
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


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    principal: str | None = None,
    body: bytes | None = None,
) -> _HttpResult:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    if body is not None:
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        response.read()
        return _HttpResult(status=response.status)
    finally:
        conn.close()


def test_unauthenticated_request_never_opens_a_session() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "GET",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/tools",
        )
        check(
            "FTF-0404",
            "an unauthenticated request is denied via the real require_role "
            "dependency (401), never a hand-rolled check",
            result.status == 401,
        )
        check(
            "FTF-0404",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_evaluator_can_read_but_not_write() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "GET",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/tools",
            principal="evaluator",
        )
        check(
            "FTF-0404",
            "an evaluator (a role Section 45 names, with no dedicated restrictive "
            "sentence for tool schemas) can read (not 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-0404",
            "the evaluator's read genuinely reaches the session layer",
            session_factory.call_count >= 1,
        )

        session_factory.reset_mock()
        result = _request(
            base_url,
            "POST",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/tools",
            principal="evaluator",
        )
        check(
            "FTF-0404",
            "an evaluator is denied create (403) -- write is workspace_admin/"
            "organization_admin only",
            result.status == 403,
        )
        check(
            "FTF-0404",
            "the write role-boundary denial never opens a session",
            session_factory.call_count == 0,
        )


def test_workspace_admin_reaches_the_session_layer_for_writes() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "PATCH",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/tools/{uuid.uuid4()}",
            principal="workspace_admin",
            body=b'{"description": "x"}',
        )
        check(
            "FTF-0404",
            "a workspace_admin's edit request passes the role gate and reaches "
            "the session layer (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-0404",
            "the workspace_admin's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_role_split_matches_section_45() -> None:
    check(
        "FTF-0404",
        "Section 45: 'workspace_admin: project administration' is the only sentence "
        "naming a role for this class of work -- TOOL_WRITE_ROLES is exactly "
        "{workspace_admin, organization_admin}, the same precedent FTF-0401 applied",
        TOOL_WRITE_ROLES == frozenset({Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}),
    )
    check(
        "FTF-0404",
        "TOOL_READ_ROLES includes every role Section 45 names",
        TOOL_READ_ROLES == frozenset(Role),
    )


def test_require_role_is_the_real_imported_dependency_not_a_duplicate() -> None:
    import inspect

    from app.auth.rbac_middleware import require_role
    from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
    from app.tools import tool_definition_crud, tool_definition_routes

    routes_source = inspect.getsource(tool_definition_routes)
    check(
        "FTF-0404",
        "tool_definition_routes.py builds its dependencies from the real, "
        "imported require_role -- not a second, hand-rolled role check",
        "require_role(" in routes_source and require_role.__module__ in routes_source,
    )
    crud_source = inspect.getsource(tool_definition_crud)
    check(
        "FTF-0404",
        "tool_definition_crud.py authorizes every fetch through the real, "
        "imported authorize_resource_ownership (FTF-H003)",
        "authorize_resource_ownership(" in crud_source
        and "app.auth.resource_authorization" in crud_source,
    )
    check(
        "FTF-0404",
        "tool_definition_crud.py reads every row through the real, imported "
        "execute_tenant_scoped (FTF-0105) -- never a bare session.get(...) call",
        "execute_tenant_scoped(" in crud_source and execute_tenant_scoped.__module__ in crud_source,
    )


def test_serialize_never_exposes_a_wrong_shape() -> None:
    tool = _make_tool()
    body = _serialize(tool)
    check(
        "FTF-0404",
        "the serialized tool carries version as a real integer field",
        isinstance(body["version"], int) and body["version"] == 1,
    )
    check(
        "FTF-0404",
        "a fresh (never-edited) tool has no previous_version_id and is not superseded",
        body["previous_version_id"] is None and body["superseded_at"] is None,
    )
    check(
        "FTF-0404",
        "the serialized tool's input/output schemas are plain dicts, never the raw JSONB column",
        isinstance(body["input_schema"], dict) and isinstance(body["output_schema"], dict),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0404 Tool schema CRUD tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0404 Tool schema CRUD tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
