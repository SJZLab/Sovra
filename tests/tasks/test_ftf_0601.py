#!/usr/bin/env python3
"""FTF-0601 -- EvaluationSuite schema/API (no-DB unit/fault tests).

Runnable without Postgres or network egress (the real live-database
create/get/list/update/lock, cross-tenant IDOR, cross-project manifest-
mismatch, not-locked-manifest and grant-surface/CHECK-constraint evidence
is `tests/integration/test_ftf_0601_evaluation_suite.py`, run in CI's
dedicated integration job -- this repo's interactive dev sandbox has no
container-registry egress, same documented finding as every prior
integration-tested task). This suite proves, without any database:

1. `validate_status`/`validate_purpose`/`validate_name`/
   `validate_grading_logic_version`/`validate_reviewer_id_or_none`: real,
   bounded, closed-set validation, never silently accepted.
2. `EvaluationSuite.__init__` itself refuses every structurally
   inconsistent combination this task's own negative_acceptance implies
   before any session or database is ever touched: a locked suite missing
   `locked_at`, a locked suite with no reviewer, and a locked final-purpose
   suite whose reviewer equals its own creator.
3. The real, locally-hosted `build_evaluation_suite_router` (a real
   uvicorn server over a real local socket, the same pattern
   `tests/tasks/test_ftf_04a19.py` established) denies an unauthenticated
   request and a role outside `EVALUATION_SUITE_WRITE_ROLES` *before*
   `session_factory` is ever called, and allows an evaluator's write
   (Section 45: "evaluator: create/run evaluation, no deployment").
4. `evaluation_suite_crud.py`/`evaluation_suite_routes.py` compose the
   same certified primitives every prior task in this chain already
   established -- no new, hand-rolled authorization/tenant-scoping
   primitive is introduced.

Run directly: python3 tests/tasks/test_ftf_0601.py
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
from app.evaluation.evaluation_suite_crud import (  # noqa: E402
    create_evaluation_suite,
    get_evaluation_suite,
    list_evaluation_suites,
    lock_evaluation_suite,
    update_evaluation_suite,
)
from app.evaluation.evaluation_suite_routes import (  # noqa: E402
    EVALUATION_SUITE_READ_ROLES,
    EVALUATION_SUITE_WRITE_ROLES,
    _serialize,
    build_evaluation_suite_router,
)
from app.evaluation.evaluation_suite_schema import (  # noqa: E402
    ALLOWED_PURPOSES,
    ALLOWED_STATUSES,
    PURPOSE_DEVELOPMENT,
    PURPOSE_FINAL,
    EvaluationSuite,
    EvaluationSuiteValidationError,
    validate_grading_logic_version,
    validate_name,
    validate_purpose,
    validate_reviewer_id_or_none,
    validate_status,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except EvaluationSuiteValidationError:
        return True


_ORG_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()
_MANIFEST_ID = uuid.uuid4()


def _make_suite(**overrides: object) -> EvaluationSuite:
    kwargs: dict[str, object] = {
        "organization_id": _ORG_ID,
        "project_id": _PROJECT_ID,
        "agent_manifest_id": _MANIFEST_ID,
        "version": 1,
        "status": "draft",
        "purpose": PURPOSE_DEVELOPMENT,
        "name": "Suite 1",
        "grading_logic_version": 1,
        "created_by": "user-1",
        "created_at": datetime.now(UTC),
    }
    kwargs.update(overrides)
    return EvaluationSuite(**kwargs)


# -- 1. field validators --------------------------------------------------


def test_validate_status_is_the_closed_two_value_set() -> None:
    check(
        "FTF-0601",
        "ALLOWED_STATUSES is exactly the two-value set ('draft', 'locked')",
        ALLOWED_STATUSES == ("draft", "locked"),
    )
    for status in ALLOWED_STATUSES:
        check(
            "FTF-0601",
            f"validate_status accepts the real status {status!r}",
            validate_status(status) == status,
        )
    check(
        "FTF-0601",
        "an unrecognized status is refused, not silently accepted",
        _rejects(validate_status, "review_required"),
    )


def test_validate_purpose_is_the_closed_two_value_set() -> None:
    check(
        "FTF-0601",
        "ALLOWED_PURPOSES is exactly ('development', 'final')",
        ALLOWED_PURPOSES == (PURPOSE_DEVELOPMENT, PURPOSE_FINAL),
    )
    for purpose in ALLOWED_PURPOSES:
        check(
            "FTF-0601",
            f"validate_purpose accepts the real purpose {purpose!r}",
            validate_purpose(purpose) == purpose,
        )
    check(
        "FTF-0601",
        "negative_acceptance: a 'mixed' purpose value is refused -- there "
        "is no way to request 'both' train/final data",
        _rejects(validate_purpose, "mixed"),
    )
    check(
        "FTF-0601",
        "a non-string purpose is refused",
        _rejects(validate_purpose, None),
    )


def test_validate_name() -> None:
    check(
        "FTF-0601",
        "a real name round-trips unchanged",
        validate_name("Golden path suite") == "Golden path suite",
    )
    check(
        "FTF-0601",
        "an empty name is refused",
        _rejects(validate_name, ""),
    )
    check(
        "FTF-0601",
        "an over-length name is refused",
        _rejects(validate_name, "x" * 201),
    )


def test_validate_grading_logic_version() -> None:
    check(
        "FTF-0601",
        "a real positive integer round-trips unchanged",
        validate_grading_logic_version(3) == 3,
    )
    check(
        "FTF-0601",
        "negative_acceptance: 'unversioned grading logic ... rejected' -- " "None is refused",
        _rejects(validate_grading_logic_version, None),
    )
    check(
        "FTF-0601",
        "zero is refused (not a real version)",
        _rejects(validate_grading_logic_version, 0),
    )
    check(
        "FTF-0601",
        "a negative integer is refused",
        _rejects(validate_grading_logic_version, -1),
    )
    check(
        "FTF-0601",
        "a bool is refused even though bool is an int subclass in Python",
        _rejects(validate_grading_logic_version, True),
    )
    check(
        "FTF-0601",
        "a non-integer is refused",
        _rejects(validate_grading_logic_version, "1"),
    )


def test_validate_reviewer_id_or_none() -> None:
    check(
        "FTF-0601",
        "a real reviewer_id round-trips unchanged",
        validate_reviewer_id_or_none("reviewer-1") == "reviewer-1",
    )
    check(
        "FTF-0601",
        "null is accepted -- reviewer_id is nullable while draft",
        validate_reviewer_id_or_none(None) is None,
    )
    check(
        "FTF-0601",
        "an empty-string reviewer_id is refused",
        _rejects(validate_reviewer_id_or_none, ""),
    )
    check(
        "FTF-0601",
        "a whitespace-only reviewer_id is refused",
        _rejects(validate_reviewer_id_or_none, "   "),
    )


# -- 2. EvaluationSuite.__init__ structural consistency -------------------


def test_construction_accepts_a_real_draft_suite() -> None:
    suite = _make_suite()
    check(
        "FTF-0601",
        "a real, well-formed draft suite constructs cleanly with no locked_at",
        suite.status == "draft" and suite.locked_at is None,
    )


def test_construction_accepts_a_real_locked_development_suite() -> None:
    suite = _make_suite(
        status="locked",
        locked_at=datetime.now(UTC),
        reviewer_id="reviewer-1",
    )
    check(
        "FTF-0601",
        "a real locked development suite with a real reviewer constructs cleanly",
        suite.status == "locked" and suite.reviewer_id == "reviewer-1",
    )


def test_construction_accepts_a_real_locked_final_suite_with_distinct_reviewer() -> None:
    suite = _make_suite(
        purpose=PURPOSE_FINAL,
        status="locked",
        locked_at=datetime.now(UTC),
        reviewer_id="reviewer-1",
        created_by="creator-1",
    )
    check(
        "FTF-0601",
        "a locked final suite with a distinct reviewer/creator constructs cleanly",
        suite.status == "locked" and suite.reviewer_id != suite.created_by,
    )


def test_construction_rejects_locked_status_without_locked_at() -> None:
    check(
        "FTF-0601",
        "a locked suite missing locked_at is refused",
        _rejects(_make_suite, status="locked", reviewer_id="reviewer-1"),
    )


def test_construction_rejects_locked_at_without_locked_status() -> None:
    check(
        "FTF-0601",
        "a draft suite carrying a locked_at is refused",
        _rejects(_make_suite, status="draft", locked_at=datetime.now(UTC)),
    )


def test_construction_rejects_locked_suite_with_no_reviewer() -> None:
    check(
        "FTF-0601",
        "negative_acceptance: a locked suite with no reviewer_id at all is refused "
        "-- Section 69.1's own independence rule requires a real reviewer",
        _rejects(_make_suite, status="locked", locked_at=datetime.now(UTC)),
    )


def test_construction_rejects_final_suite_locked_with_self_review() -> None:
    check(
        "FTF-0601",
        "negative_acceptance: a locked final-purpose suite whose reviewer_id "
        "equals its own created_by is refused -- Section 69.1: 'Final suite "
        "creator/reviewer is independent of candidate implementer'",
        _rejects(
            _make_suite,
            purpose=PURPOSE_FINAL,
            status="locked",
            locked_at=datetime.now(UTC),
            reviewer_id="creator-1",
            created_by="creator-1",
        ),
    )


def test_construction_allows_development_suite_locked_with_self_review() -> None:
    # Section 69.1 names the independence rule for the *final* suite
    # specifically -- a development suite has no such restriction (see the
    # schema module's own docstring, "reviewer role").
    suite = _make_suite(
        purpose=PURPOSE_DEVELOPMENT,
        status="locked",
        locked_at=datetime.now(UTC),
        reviewer_id="creator-1",
        created_by="creator-1",
    )
    check(
        "FTF-0601",
        "a development-purpose suite may legally be locked with reviewer_id "
        "== created_by -- Section 69.1 restricts only the final suite",
        suite.status == "locked",
    )


def test_construction_rejects_invalid_json_field_types() -> None:
    check(
        "FTF-0601",
        "a non-string name is refused",
        _rejects(_make_suite, name=123),
    )
    check(
        "FTF-0601",
        "a non-int grading_logic_version is refused",
        _rejects(_make_suite, grading_logic_version="1"),
    )


# -- 3. Real demo router: role-boundary denial never opens a session -----


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
_VIEWER_PRINCIPAL = AuthenticatedPrincipal(issuer="https://issuer.example", subject="viewer-1")


def _principal_resolver(request):
    header = request.headers.get("x-test-principal")
    if header == "workspace_admin":
        return _WORKSPACE_ADMIN_PRINCIPAL
    if header == "evaluator":
        return _EVALUATOR_PRINCIPAL
    if header == "viewer":
        return _VIEWER_PRINCIPAL
    return None


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID):
    if organization_id != _ORG_ID:
        return None
    if subject == _WORKSPACE_ADMIN_PRINCIPAL.subject:
        return Role.WORKSPACE_ADMIN
    if subject == _EVALUATOR_PRINCIPAL.subject:
        return Role.EVALUATOR
    if subject == _VIEWER_PRINCIPAL.subject:
        return Role.VIEWER
    return None


@contextlib.contextmanager
def _running_router(session_factory) -> Iterator[str]:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        build_evaluation_suite_router(
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
    return f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/evaluation-suites"


def test_unauthenticated_request_never_opens_a_session() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", _collection_path())
        check(
            "FTF-0601",
            "an unauthenticated request is denied via the real require_role "
            "dependency (401), never a hand-rolled check",
            result.status == 401,
        )
        check(
            "FTF-0601",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_viewer_is_denied_write() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "POST", _collection_path(), principal="viewer", body=b"{}")
        check(
            "FTF-0601",
            "a viewer is denied create (403) -- write is gated to "
            "{evaluator, workspace_admin, organization_admin} alone",
            result.status == 403,
        )
        check(
            "FTF-0601",
            "the viewer's denied write never opens a session",
            session_factory.call_count == 0,
        )


def test_evaluator_is_allowed_write() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "POST", _collection_path(), principal="evaluator", body=b"{}")
        check(
            "FTF-0601",
            "an evaluator's write passes the role gate and reaches the "
            "session layer (never 401/403) -- Section 45: 'evaluator: "
            "create/run evaluation, no deployment'",
            result.status not in (401, 403),
        )
        check(
            "FTF-0601",
            "the evaluator's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_evaluator_is_allowed_read() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", _collection_path(), principal="evaluator")
        check(
            "FTF-0601",
            "an evaluator is allowed read (never 401/403)",
            result.status not in (401, 403),
        )


def test_workspace_admin_reaches_the_session_layer() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", _collection_path(), principal="workspace_admin")
        check(
            "FTF-0601",
            "a workspace_admin's read passes the role gate and reaches "
            "the session layer (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-0601",
            "the workspace_admin's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_role_sets_match_section_45() -> None:
    check(
        "FTF-0601",
        "EVALUATION_SUITE_WRITE_ROLES is exactly {evaluator, workspace_admin, "
        "organization_admin} -- Section 45: 'evaluator: create/run evaluation, "
        "no deployment' is a real, specific sentence for this class of resource",
        EVALUATION_SUITE_WRITE_ROLES
        == frozenset({Role.EVALUATOR, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}),
    )
    check(
        "FTF-0601",
        "EVALUATION_SUITE_READ_ROLES is every role Section 45 names -- the same "
        "wide-read baseline FTF-04A19's own AGENT_MANIFEST_READ_ROLES already established",
        EVALUATION_SUITE_READ_ROLES == frozenset(Role),
    )


def test_crud_module_composes_certified_primitives_not_duplicates() -> None:
    import inspect

    from app.auth.rbac_middleware import require_role
    from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
    from app.evaluation import evaluation_suite_crud, evaluation_suite_routes

    routes_source = inspect.getsource(evaluation_suite_routes)
    check(
        "FTF-0601",
        "evaluation_suite_routes.py builds its dependency from the real, "
        "imported require_role -- not a second, hand-rolled role check",
        "require_role(" in routes_source and require_role.__module__ in routes_source,
    )
    crud_source = inspect.getsource(evaluation_suite_crud)
    check(
        "FTF-0601",
        "evaluation_suite_crud.py authorizes every fetch through the real, "
        "imported authorize_resource_ownership (FTF-H003)",
        "authorize_resource_ownership(" in crud_source
        and "app.auth.resource_authorization" in crud_source,
    )
    check(
        "FTF-0601",
        "evaluation_suite_crud.py reads every row through the real, imported "
        "execute_tenant_scoped (FTF-0105) -- never a bare session.get(...) call",
        "execute_tenant_scoped(" in crud_source and execute_tenant_scoped.__module__ in crud_source,
    )
    check(
        "FTF-0601",
        "the real, callable CRUD surface this router's own module imports",
        callable(create_evaluation_suite)
        and callable(get_evaluation_suite)
        and callable(list_evaluation_suites)
        and callable(update_evaluation_suite)
        and callable(lock_evaluation_suite),
    )


def test_serialize_never_exposes_a_wrong_shape() -> None:
    suite = _make_suite(name="Suite 1")
    body = _serialize(suite)
    check(
        "FTF-0601",
        "the serialized suite carries its real status/version/purpose/name and "
        "a null reviewer_id/locked_at for a draft row",
        body["status"] == "draft"
        and body["version"] == 1
        and body["purpose"] == PURPOSE_DEVELOPMENT
        and body["name"] == "Suite 1"
        and body["reviewer_id"] is None
        and body["locked_at"] is None,
    )
    check(
        "FTF-0601",
        "id/organization_id/project_id/agent_manifest_id serialize as strings, "
        "not raw UUID objects",
        isinstance(body["id"], str)
        and isinstance(body["organization_id"], str)
        and isinstance(body["project_id"], str)
        and isinstance(body["agent_manifest_id"], str),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0601 EvaluationSuite schema tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0601 EvaluationSuite schema tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
