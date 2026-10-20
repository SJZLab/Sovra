#!/usr/bin/env python3
"""FTF-04A04 -- GitHub branch/ref resolver routes (role-boundary tests,
no real Postgres).

Same real-uvicorn-server/`MagicMock`-session-factory pattern
`tests/tasks/test_ftf_04a03_routes.py` already established: proves the
real `require_role` dependency denies an unauthenticated or
under-privileged caller *before* a database session is ever opened, for
both routes this task adds, and that an `organization_admin` genuinely
reaches the session layer -- without needing a live Postgres for the
role-boundary case itself (the real project-authorization/audit-write/
GitHub-call path is proven end to end against a real Postgres by
`tests/integration/test_ftf_04a04_github_branch_ref.py`).

Run directly: python3 tests/tasks/test_ftf_04a04_routes.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
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
from app.target_connectors.github_branch_ref_routes import (  # noqa: E402
    GITHUB_BRANCH_REF_ROLES,
    build_github_branch_ref_router,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_PROJECT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

_ORG_ADMIN_PRINCIPAL = AuthenticatedPrincipal(issuer="https://issuer.example", subject="admin-1")
_VIEWER_PRINCIPAL = AuthenticatedPrincipal(issuer="https://issuer.example", subject="viewer-1")


def _principal_resolver(request):
    header = request.headers.get("x-test-principal")
    if header == "organization_admin":
        return _ORG_ADMIN_PRINCIPAL
    if header == "viewer":
        return _VIEWER_PRINCIPAL
    return None


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID):
    if organization_id != _ORG_ID:
        return None
    if subject == _ORG_ADMIN_PRINCIPAL.subject:
        return Role.ORGANIZATION_ADMIN
    if subject == _VIEWER_PRINCIPAL.subject:
        return Role.VIEWER
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
        build_github_branch_ref_router(
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
    body: bytes


def _post(
    base_url: str,
    path: str,
    *,
    principal: str | None = None,
    payload: dict[str, object] | None = None,
) -> _HttpResult:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    body = json.dumps(payload).encode() if payload is not None else b"{}"
    headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request("POST", path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        return _HttpResult(status=response.status, body=data)
    finally:
        conn.close()


_BRANCHES_PATH = (
    f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/target-connections/github-branches"
)
_RESOLVE_PATH = (
    f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}"
    "/target-connections/github-resolve-ref"
)


_CONNECTION_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")

_BOTH_ROUTES_PAYLOADS: tuple[tuple[str, dict[str, object]], ...] = (
    (_BRANCHES_PATH, {"credential": "tok", "repository_identity": "acme/repo"}),
    (
        _RESOLVE_PATH,
        {
            "credential": "tok",
            "repository_identity": "acme/repo",
            "ref": "main",
            "target_application_connection_id": str(_CONNECTION_ID),
        },
    ),
)


def test_unauthenticated_request_never_opens_a_session() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        for path, payload in _BOTH_ROUTES_PAYLOADS:
            result = _post(base_url, path, payload=payload)
            check(
                "FTF-04A04",
                f"an unauthenticated request to {path} is denied via the real require_role "
                "dependency (401), never a hand-rolled check",
                result.status == 401,
            )
        check(
            "FTF-04A04",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_viewer_is_denied_on_both_routes() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        for path, payload in _BOTH_ROUTES_PAYLOADS:
            result = _post(base_url, path, principal="viewer", payload=payload)
            check(
                "FTF-04A04",
                f"a viewer (not in GITHUB_BRANCH_REF_ROLES) is denied (403) on {path}",
                result.status == 403,
            )
        check(
            "FTF-04A04",
            "the role-boundary denial never opens a session",
            session_factory.call_count == 0,
        )


def test_missing_required_fields_never_reach_github() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _post(base_url, _BRANCHES_PATH, principal="organization_admin", payload={})
        check(
            "FTF-04A04",
            "negative_acceptance: a branches request with no credential is refused (400) before "
            "any GitHub call or project authorization",
            result.status == 400,
        )
        result = _post(
            base_url,
            _BRANCHES_PATH,
            principal="organization_admin",
            payload={"credential": "tok"},
        )
        check(
            "FTF-04A04",
            "negative_acceptance: a branches request with no repository_identity is refused (400)",
            result.status == 400,
        )
        result = _post(
            base_url,
            _RESOLVE_PATH,
            principal="organization_admin",
            payload={"credential": "tok", "repository_identity": "acme/repo"},
        )
        check(
            "FTF-04A04",
            "negative_acceptance: a resolve-ref request with no ref is refused (400)",
            result.status == 400,
        )
        result = _post(
            base_url,
            _RESOLVE_PATH,
            principal="organization_admin",
            payload={"credential": "tok", "repository_identity": "acme/repo", "ref": "main"},
        )
        check(
            "FTF-04A04",
            "negative_acceptance: a resolve-ref request with no "
            "target_application_connection_id is refused (400) -- the persist half of "
            "acceptance clause 1 has nothing to persist onto without it",
            result.status == 400,
        )
        result = _post(
            base_url,
            _RESOLVE_PATH,
            principal="organization_admin",
            payload={
                "credential": "tok",
                "repository_identity": "acme/repo",
                "ref": "main",
                "target_application_connection_id": "not-a-uuid",
            },
        )
        check(
            "FTF-04A04",
            "negative_acceptance: a non-UUID target_application_connection_id is refused (400)",
            result.status == 400,
        )


def test_organization_admin_reaches_the_session_layer_on_both_routes() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _post(
            base_url,
            _BRANCHES_PATH,
            principal="organization_admin",
            payload={"credential": "tok", "repository_identity": "acme/repo"},
        )
        check(
            "FTF-04A04",
            "organization_admin passes the role gate and reaches the session layer on "
            "github-branches (never 401/403)",
            result.status not in (401, 403),
        )
        result = _post(
            base_url,
            _RESOLVE_PATH,
            principal="organization_admin",
            payload={
                "credential": "tok",
                "repository_identity": "acme/repo",
                "ref": "main",
                "target_application_connection_id": str(_CONNECTION_ID),
            },
        )
        check(
            "FTF-04A04",
            "organization_admin passes the role gate and reaches the session layer on "
            "github-resolve-ref (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-04A04",
            "the organization_admin's requests genuinely open a session",
            session_factory.call_count >= 2,
        )


def test_role_set_matches_target_connection_roles() -> None:
    check(
        "FTF-04A04",
        "GITHUB_BRANCH_REF_ROLES is exactly {organization_admin} -- the same credential-bearing "
        "role boundary every prior target-connection route in this repository already "
        "established",
        GITHUB_BRANCH_REF_ROLES == frozenset({Role.ORGANIZATION_ADMIN}),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A04 GitHub branch/ref resolver (routes): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A04 GitHub branch/ref resolver (routes): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
