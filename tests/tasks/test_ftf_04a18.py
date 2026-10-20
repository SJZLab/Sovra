#!/usr/bin/env python3
"""FTF-04A18 -- Candidate-selection route role-boundary + pure-module
tests (no real Postgres).

Same real-uvicorn-server/`MagicMock`-session-factory pattern
`tests/tasks/test_ftf_04a04_routes.py` already established: proves the
real `require_role` dependency denies an unauthenticated or
under-privileged caller *before* a database session is ever opened --
without needing a live Postgres for the role-boundary case itself (the
real selection/stale-snapshot/persisted-audit path is proven end to end
against a real Postgres by
`tests/integration/test_ftf_04a18_candidate_selection.py`).

Also covers this module's own pure, no-I/O pieces:
`StaleSnapshotCandidateError` being a real negative-test-bindable
`ValueError` subclass, `CandidateSelectionRecord`'s own frozen-dataclass
construction, and `CANDIDATE_SELECTED_ACTION` being the exact literal
string the integration test's own audit-row lookup depends on (a typo in
either place would make that lookup vacuously pass against zero rows --
this test is what would catch that).

Run directly: python3 tests/tasks/test_ftf_04a18.py
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
from dataclasses import dataclass, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import uvicorn

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.target_connectors.discovered_agent_candidate_selection import (  # noqa: E402
    CANDIDATE_SELECTED_ACTION,
    CandidateSelectionRecord,
    StaleSnapshotCandidateError,
)
from app.target_connectors.discovered_agent_candidate_selection_routes import (  # noqa: E402
    CANDIDATE_SELECTION_ROLES,
    build_discovered_agent_candidate_selection_router,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_PROJECT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
_CONNECTION_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
_SNAPSHOT_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
_CANDIDATE_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")

_ORG_ADMIN_PRINCIPAL = AuthenticatedPrincipal(issuer="https://issuer.example", subject="admin-1")
_EVALUATOR_PRINCIPAL = AuthenticatedPrincipal(
    issuer="https://issuer.example", subject="evaluator-1"
)

_SELECT_PATH = (
    f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}"
    f"/target-connections/{_CONNECTION_ID}/snapshots/{_SNAPSHOT_ID}"
    f"/candidates/{_CANDIDATE_ID}/select"
)


def _principal_resolver(request):
    header = request.headers.get("x-test-principal")
    if header == "organization_admin":
        return _ORG_ADMIN_PRINCIPAL
    if header == "evaluator":
        return _EVALUATOR_PRINCIPAL
    return None


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID):
    if organization_id != _ORG_ID:
        return None
    if subject == _ORG_ADMIN_PRINCIPAL.subject:
        return Role.ORGANIZATION_ADMIN
    if subject == _EVALUATOR_PRINCIPAL.subject:
        return Role.EVALUATOR
    return None


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def _running_router(session_factory) -> Iterator[str]:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        build_discovered_agent_candidate_selection_router(
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


def _post(base_url: str, path: str, *, principal: str | None = None) -> _HttpResult:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request("POST", path, headers=headers)
        response = conn.getresponse()
        response.read()
        return _HttpResult(status=response.status)
    finally:
        conn.close()


def test_unauthenticated_request_never_opens_a_session() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _post(base_url, _SELECT_PATH)
        check(
            "FTF-04A18",
            "an unauthenticated select request is denied via the real require_role "
            "dependency (401), never a hand-rolled check",
            result.status == 401,
        )
        check(
            "FTF-04A18",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_evaluator_is_denied() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _post(base_url, _SELECT_PATH, principal="evaluator")
        check(
            "FTF-04A18",
            "an evaluator (not in CANDIDATE_SELECTION_ROLES) is denied (403)",
            result.status == 403,
        )
        check(
            "FTF-04A18",
            "the role-boundary denial never opens a session",
            session_factory.call_count == 0,
        )


def test_organization_admin_reaches_the_session_layer() -> None:
    session_factory = MagicMock()
    session_factory.side_effect = RuntimeError("no real database in this no-DB test")
    with _running_router(session_factory) as base_url:
        result = _post(base_url, _SELECT_PATH, principal="organization_admin")
        check(
            "FTF-04A18",
            "organization_admin passes the role gate and reaches the session layer "
            "(never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-04A18",
            "organization_admin's request genuinely attempts to open a session",
            session_factory.call_count >= 1,
        )


def test_role_set_matches_precedent() -> None:
    check(
        "FTF-04A18",
        "CANDIDATE_SELECTION_ROLES is exactly {organization_admin} -- the same "
        "credential-bearing role boundary FTF-04A01/FTF-04A07/FTF-04A17 already established",
        CANDIDATE_SELECTION_ROLES == frozenset({Role.ORGANIZATION_ADMIN}),
    )


def test_stale_snapshot_candidate_error_is_a_real_bindable_value_error() -> None:
    try:
        raise StaleSnapshotCandidateError("snapshot superseded")
    except ValueError as exc:
        check(
            "FTF-04A18",
            "StaleSnapshotCandidateError is a real, catchable ValueError subclass -- "
            "the route layer's own except clause binds it to a real HTTP 409, not a 500",
            str(exc) == "snapshot superseded",
        )


def test_candidate_selection_record_is_an_immutable_dataclass() -> None:
    check(
        "FTF-04A18",
        "CandidateSelectionRecord is a real dataclass",
        is_dataclass(CandidateSelectionRecord),
    )
    record = CandidateSelectionRecord(
        candidate_id=_CANDIDATE_ID,
        target_source_snapshot_id=_SNAPSHOT_ID,
        actor_id="user-1",
        selected_at=datetime.now(UTC),
    )
    try:
        record.candidate_id = uuid.uuid4()  # type: ignore[misc]
        mutated = True
    except Exception:
        mutated = False
    check(
        "FTF-04A18",
        "CandidateSelectionRecord is frozen -- a caller cannot silently rewrite which "
        "candidate was actually selected after the fact",
        not mutated,
    )


def test_audit_action_literal_matches_what_the_integration_test_depends_on() -> None:
    check(
        "FTF-04A18",
        "CANDIDATE_SELECTED_ACTION is the exact, stable literal both this module's own "
        "write_audit_event call and the integration test's own audit-row lookup key on",
        CANDIDATE_SELECTED_ACTION == "discovered_agent_candidate.selected",
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A18 candidate selection (routes + pure module): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A18 candidate selection (routes + pure module): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
