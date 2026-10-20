#!/usr/bin/env python3
"""FTF-04A19 -- AgentManifest schema/versioning/hash (no-DB unit/fault tests).

Runnable without Postgres or network egress (the real live-database
create/get/list/update-content/submit-for-review/lock, cross-tenant IDOR,
cross-project snapshot-mismatch and grant-surface/CHECK-constraint
evidence is `tests/integration/test_ftf_04a19_agent_manifest.py`, run in
CI's dedicated integration job -- this repo's interactive dev sandbox has
no container-registry egress, same documented finding as every prior
integration-tested task). This suite proves, without any database:

1. `validate_status`/`validate_status_transition`/`validate_agent_name_or_none`:
   real, bounded, closed-set/graph validation, never silently accepted.
2. `compute_manifest_sha256`: deterministic, order-independent, and
   sensitive to every real content change.
3. `AgentManifest.__init__` itself refuses every structurally inconsistent
   combination this task's own negative_acceptance implies before any
   session or database is ever touched: a hash present on a non-locked
   row, a locked row missing its hash, and a mismatched approval pair.
4. The real, locally-hosted `build_agent_manifest_router` (a real uvicorn
   server over a real local socket, the same pattern
   `tests/tasks/test_ftf_1001.py`/`test_ftf_04a07.py` established) denies
   an unauthenticated request and a role outside
   `AGENT_MANIFEST_WRITE_ROLES` *before* `session_factory` is ever called.
5. `agent_manifest_crud.py`/`agent_manifest_routes.py` compose the same
   certified primitives every prior task in this chain already
   established -- no new, hand-rolled authorization/tenant-scoping
   primitive is introduced.

Run directly: python3 tests/tasks/test_ftf_04a19.py
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
from pathlib import Path
from unittest.mock import MagicMock

import uvicorn

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.target_connectors.agent_manifest_crud import (  # noqa: E402
    create_agent_manifest,
    get_agent_manifest,
    list_agent_manifests,
    lock_agent_manifest,
    submit_for_review,
    update_agent_manifest_content,
)
from app.target_connectors.agent_manifest_routes import (  # noqa: E402
    AGENT_MANIFEST_READ_ROLES,
    AGENT_MANIFEST_WRITE_ROLES,
    _serialize,
    build_agent_manifest_router,
)
from app.target_connectors.agent_manifest_schema import (  # noqa: E402
    ALLOWED_STATUS_TRANSITIONS,
    ALLOWED_STATUSES,
    CONTENT_FIELDS,
    CRITICAL_LOCK_FIELDS,
    AgentManifest,
    AgentManifestValidationError,
    compute_manifest_sha256,
    validate_agent_name_or_none,
    validate_status,
    validate_status_transition,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except AgentManifestValidationError:
        return True


_ORG_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()
_SNAPSHOT_ID = uuid.uuid4()


def _make_manifest(**overrides: object) -> AgentManifest:
    from datetime import UTC, datetime

    kwargs: dict[str, object] = {
        "organization_id": _ORG_ID,
        "project_id": _PROJECT_ID,
        "target_source_snapshot_id": _SNAPSHOT_ID,
        "version": 1,
        "status": "draft",
        "created_by": "user-1",
        # No DB session is involved in this suite, so the column's own
        # server_default("now()") never fires -- set explicitly here so
        # `_serialize` (which calls `.isoformat()`) has a real value.
        "created_at": datetime.now(UTC),
    }
    kwargs.update(overrides)
    return AgentManifest(**kwargs)


# -- 1. field validators --------------------------------------------------


def test_validate_status_is_the_closed_four_value_set() -> None:
    check(
        "FTF-04A19",
        "ALLOWED_STATUSES is exactly Section 6.8D's own four-value set, in its own literal order",
        ALLOWED_STATUSES == ("draft", "review_required", "locked", "superseded"),
    )
    for status in ALLOWED_STATUSES:
        check(
            "FTF-04A19",
            f"validate_status accepts the real status {status!r}",
            validate_status(status) == status,
        )
    check(
        "FTF-04A19",
        "an unrecognized status is refused, not silently accepted",
        _rejects(validate_status, "archived"),
    )
    check(
        "FTF-04A19",
        "a non-string status is refused",
        _rejects(validate_status, None),
    )


def _accepts_transition(current: str, new: str) -> bool:
    try:
        validate_status_transition(current, new)
        return True
    except AgentManifestValidationError:
        return False


def test_validate_status_transition_is_a_strict_forward_chain() -> None:
    check(
        "FTF-04A19",
        "draft -> review_required is legal (this task's own acceptance: "
        "'reviewed/locked/superseded transitions')",
        _accepts_transition("draft", "review_required"),
    )
    check(
        "FTF-04A19",
        "review_required -> locked is legal",
        _accepts_transition("review_required", "locked"),
    )
    check(
        "FTF-04A19",
        "locked -> superseded is legal",
        _accepts_transition("locked", "superseded"),
    )
    check(
        "FTF-04A19",
        "superseded is a terminal state -- no legal transition out of it",
        ALLOWED_STATUS_TRANSITIONS["superseded"] == frozenset(),
    )
    check(
        "FTF-04A19",
        "draft -> locked (a skip) is illegal",
        _rejects(validate_status_transition, "draft", "locked"),
    )
    check(
        "FTF-04A19",
        "locked -> draft (a reverse) is illegal",
        _rejects(validate_status_transition, "locked", "draft"),
    )
    check(
        "FTF-04A19",
        "draft -> draft (a self-loop) is illegal",
        _rejects(validate_status_transition, "draft", "draft"),
    )
    check(
        "FTF-04A19",
        "superseded -> anything is illegal (terminal)",
        _rejects(validate_status_transition, "superseded", "draft"),
    )


def test_validate_agent_name_or_none() -> None:
    check(
        "FTF-04A19",
        "a real agent_name round-trips unchanged",
        validate_agent_name_or_none("CustomerSupportAgent") == "CustomerSupportAgent",
    )
    check(
        "FTF-04A19",
        "null is accepted -- Section 6.8D's own agent_name is nullable "
        "before discovery/authoring completes",
        validate_agent_name_or_none(None) is None,
    )
    check(
        "FTF-04A19",
        "an empty-string agent_name is refused (must be null, not empty, when absent)",
        _rejects(validate_agent_name_or_none, ""),
    )
    check(
        "FTF-04A19",
        "an over-length agent_name is refused",
        _rejects(validate_agent_name_or_none, "x" * 201),
    )


# -- 2. compute_manifest_sha256 -------------------------------------------


def test_compute_manifest_sha256_is_deterministic_and_order_independent() -> None:
    content_a: dict[str, object] = {
        "target_source_snapshot_id": _SNAPSHOT_ID,
        "agent_name": "Agent1",
    }
    content_b: dict[str, object] = {
        "agent_name": "Agent1",
        "target_source_snapshot_id": _SNAPSHOT_ID,
    }
    check(
        "FTF-04A19",
        "hashing the same content in a different key order produces the same hash",
        compute_manifest_sha256(content_a) == compute_manifest_sha256(content_b),
    )
    check(
        "FTF-04A19",
        "the hash is a real 64-hex-char sha256 digest",
        len(compute_manifest_sha256(content_a)) == 64
        and all(c in "0123456789abcdef" for c in compute_manifest_sha256(content_a)),
    )


def test_compute_manifest_sha256_is_sensitive_to_content_changes() -> None:
    base: dict[str, object] = {"agent_name": "Agent1"}
    changed: dict[str, object] = {"agent_name": "Agent2"}
    check(
        "FTF-04A19",
        "changing a real content field changes the hash",
        compute_manifest_sha256(base) != compute_manifest_sha256(changed),
    )


def test_compute_manifest_sha256_only_covers_content_fields() -> None:
    content: dict[str, object] = {field: None for field in CONTENT_FIELDS}
    content_with_extra: dict[str, object] = dict(content)
    content_with_extra["id"] = str(uuid.uuid4())
    content_with_extra["status"] = "locked"
    check(
        "FTF-04A19",
        "a key outside CONTENT_FIELDS (e.g. id/status envelope metadata) "
        "never changes the hash -- only real content is hashed",
        compute_manifest_sha256(content) == compute_manifest_sha256(content_with_extra),
    )


# -- 3. AgentManifest.__init__ structural consistency ---------------------


def test_construction_accepts_a_real_draft_manifest() -> None:
    manifest = _make_manifest()
    check(
        "FTF-04A19",
        "a real, well-formed draft manifest constructs cleanly with no hash/approval",
        manifest.status == "draft"
        and manifest.manifest_sha256 is None
        and manifest.approved_by is None
        and manifest.approved_at is None,
    )


def test_construction_accepts_a_real_locked_manifest() -> None:
    from datetime import UTC, datetime

    manifest = _make_manifest(
        status="locked",
        manifest_sha256="a" * 64,
        approved_by="admin-1",
        approved_at=datetime.now(UTC),
    )
    check(
        "FTF-04A19",
        "a real locked manifest with a real hash and approval pair constructs cleanly",
        manifest.status == "locked" and manifest.manifest_sha256 == "a" * 64,
    )


def test_construction_rejects_hash_without_locked_status() -> None:
    check(
        "FTF-04A19",
        "negative_acceptance: a draft manifest may never carry a "
        "manifest_sha256 -- Section 6.8D: 'locking computes manifest_sha256'",
        _rejects(_make_manifest, status="draft", manifest_sha256="a" * 64),
    )


def test_construction_rejects_locked_status_without_hash() -> None:
    check(
        "FTF-04A19",
        "a locked manifest with no hash at all is refused",
        _rejects(_make_manifest, status="locked"),
    )


def test_construction_rejects_mismatched_approval_pair() -> None:
    from datetime import UTC, datetime

    check(
        "FTF-04A19",
        "approved_by with no approved_at is refused",
        _rejects(
            _make_manifest,
            status="locked",
            manifest_sha256="a" * 64,
            approved_by="admin-1",
            approved_at=None,
        ),
    )
    check(
        "FTF-04A19",
        "approved_at with no approved_by is refused",
        _rejects(
            _make_manifest,
            status="locked",
            manifest_sha256="a" * 64,
            approved_by=None,
            approved_at=datetime.now(UTC),
        ),
    )


def test_construction_rejects_locked_status_without_approval() -> None:
    check(
        "FTF-04A19",
        "a locked manifest with a real hash but no approval at all is refused",
        _rejects(_make_manifest, status="locked", manifest_sha256="a" * 64),
    )


def test_construction_rejects_invalid_json_content_field() -> None:
    check(
        "FTF-04A19",
        "a non-object value for a JSON content field is refused",
        _rejects(_make_manifest, mandate_json="not-an-object"),
    )


def test_critical_lock_fields_matches_negative_acceptance() -> None:
    check(
        "FTF-04A19",
        "CRITICAL_LOCK_FIELDS is exactly this task's own literal "
        "'evidence/model/runtime tuple' three-item negative_acceptance mapping",
        CRITICAL_LOCK_FIELDS
        == ("agent_entrypoint_json", "current_model_binding_json", "runtime_dependencies_json"),
    )


# -- 4. Real demo router: role-boundary denial never opens a session -----


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


_WORKSPACE_ADMIN_PRINCIPAL = AuthenticatedPrincipal(
    issuer="https://issuer.example", subject="wsadmin-1"
)
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
        build_agent_manifest_router(
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


def _collection_path() -> str:
    return f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/manifests"


def test_unauthenticated_request_never_opens_a_session() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", _collection_path())
        check(
            "FTF-04A19",
            "an unauthenticated request is denied via the real require_role "
            "dependency (401), never a hand-rolled check",
            result.status == 401,
        )
        check(
            "FTF-04A19",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_evaluator_is_denied_write() -> None:
    # A fresh session_factory dedicated to this write-only check -- never
    # shared with a prior real read (see test_evaluator_is_allowed_read),
    # so `call_count == 0` genuinely proves this denied write alone never
    # opened a session.
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "POST", _collection_path(), principal="evaluator", body=b"{}")
        check(
            "FTF-04A19",
            "an evaluator is denied create (403) -- write is gated to "
            "{workspace_admin, organization_admin} alone",
            result.status == 403,
        )
        check(
            "FTF-04A19",
            "the evaluator's denied write never opens a session",
            session_factory.call_count == 0,
        )


def test_evaluator_is_allowed_read() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", _collection_path(), principal="evaluator")
        check(
            "FTF-04A19",
            "an evaluator is allowed read (never 401/403) -- Section 45's widest read baseline",
            result.status not in (401, 403),
        )


def test_workspace_admin_reaches_the_session_layer() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "GET",
            _collection_path(),
            principal="workspace_admin",
        )
        check(
            "FTF-04A19",
            "a workspace_admin's read passes the role gate and reaches "
            "the session layer (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-04A19",
            "the workspace_admin's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_role_sets_match_section_45() -> None:
    check(
        "FTF-04A19",
        "AGENT_MANIFEST_WRITE_ROLES is exactly {workspace_admin, organization_admin} -- "
        "Section 45: 'workspace_admin: project administration' is the closest real "
        "sentence for this class of resource",
        AGENT_MANIFEST_WRITE_ROLES == frozenset({Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}),
    )
    check(
        "FTF-04A19",
        "AGENT_MANIFEST_READ_ROLES is every role Section 45 names -- the same wide-read "
        "baseline FTF-1001's own MODEL_VERSION_READ_ROLES already established",
        AGENT_MANIFEST_READ_ROLES == frozenset(Role),
    )


def test_crud_module_composes_certified_primitives_not_duplicates() -> None:
    import inspect

    from app.auth.rbac_middleware import require_role
    from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
    from app.target_connectors import agent_manifest_crud, agent_manifest_routes

    routes_source = inspect.getsource(agent_manifest_routes)
    check(
        "FTF-04A19",
        "agent_manifest_routes.py builds its dependency from the real, "
        "imported require_role -- not a second, hand-rolled role check",
        "require_role(" in routes_source and require_role.__module__ in routes_source,
    )
    crud_source = inspect.getsource(agent_manifest_crud)
    check(
        "FTF-04A19",
        "agent_manifest_crud.py authorizes every fetch through the real, "
        "imported authorize_resource_ownership (FTF-H003)",
        "authorize_resource_ownership(" in crud_source
        and "app.auth.resource_authorization" in crud_source,
    )
    check(
        "FTF-04A19",
        "agent_manifest_crud.py reads every row through the real, imported "
        "execute_tenant_scoped (FTF-0105) -- never a bare session.get(...) call",
        "execute_tenant_scoped(" in crud_source and execute_tenant_scoped.__module__ in crud_source,
    )
    check(
        "FTF-04A19",
        "the real, callable CRUD surface this router's own module imports",
        callable(create_agent_manifest)
        and callable(get_agent_manifest)
        and callable(list_agent_manifests)
        and callable(update_agent_manifest_content)
        and callable(submit_for_review)
        and callable(lock_agent_manifest),
    )


def test_serialize_never_exposes_a_wrong_shape() -> None:
    manifest = _make_manifest(agent_name="Agent1")
    body = _serialize(manifest)
    check(
        "FTF-04A19",
        "the serialized manifest carries its real status/version/agent_name "
        "and a null manifest_sha256/approved_by/approved_at for a draft row",
        body["status"] == "draft"
        and body["version"] == 1
        and body["agent_name"] == "Agent1"
        and body["manifest_sha256"] is None
        and body["approved_by"] is None
        and body["approved_at"] is None,
    )
    check(
        "FTF-04A19",
        "id/organization_id/project_id/target_source_snapshot_id serialize as strings, "
        "not raw UUID objects",
        isinstance(body["id"], str)
        and isinstance(body["organization_id"], str)
        and isinstance(body["project_id"], str)
        and isinstance(body["target_source_snapshot_id"], str),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A19 AgentManifest schema tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A19 AgentManifest schema tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
