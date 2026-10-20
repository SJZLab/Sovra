#!/usr/bin/env python3
"""FTF-04A03 -- GitHub accessible-repository listing route (role-boundary
tests, no real Postgres).

Same real-uvicorn-server/`MagicMock`-session-factory pattern
`tests/tasks/test_ftf_04a01.py` already established: proves the real
`require_role` dependency denies an unauthenticated or under-privileged
caller *before* a database session is ever opened, and that an
`organization_admin` genuinely reaches the session layer -- without
needing a live Postgres for the role-boundary case itself (the real
project-authorization/audit-write/GitHub-listing path is proven end to end
against a real Postgres by
`tests/integration/test_ftf_04a03_github_repository_listing.py`).

Run directly: python3 tests/tasks/test_ftf_04a03_routes.py
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
from app.target_connectors.github_repository_listing_routes import (  # noqa: E402
    GITHUB_REPOSITORY_LISTING_ROLES,
    build_github_repository_listing_router,
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
        build_github_repository_listing_router(
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


_PATH = (
    f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/target-connections/github-repositories"
)


def test_unauthenticated_request_never_opens_a_session() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _post(base_url, _PATH, payload={"credential": "tok"})
        check(
            "FTF-04A03",
            "an unauthenticated request is denied via the real require_role dependency "
            "(401), never a hand-rolled check",
            result.status == 401,
        )
        check(
            "FTF-04A03",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_viewer_is_denied() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _post(base_url, _PATH, principal="viewer", payload={"credential": "tok"})
        check(
            "FTF-04A03",
            "a viewer (not in GITHUB_REPOSITORY_LISTING_ROLES) is denied (403) -- this "
            "credential-bearing resource does not open even a listing read wider than "
            "TargetApplicationConnection writes",
            result.status == 403,
        )
        check(
            "FTF-04A03",
            "the role-boundary denial never opens a session",
            session_factory.call_count == 0,
        )


def test_missing_credential_never_reaches_github() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _post(base_url, _PATH, principal="organization_admin", payload={})
        check(
            "FTF-04A03",
            "negative_acceptance: a request with no credential is refused (400) before any "
            "GitHub call or project authorization",
            result.status == 400,
        )


def test_organization_admin_reaches_the_session_layer() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _post(
            base_url, _PATH, principal="organization_admin", payload={"credential": "tok"}
        )
        check(
            "FTF-04A03",
            "organization_admin passes the role gate and reaches the session layer (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-04A03",
            "the organization_admin's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_role_set_matches_target_connection_roles() -> None:
    check(
        "FTF-04A03",
        "GITHUB_REPOSITORY_LISTING_ROLES is exactly {organization_admin} -- the same "
        "credential-bearing-'connection' role boundary FTF-04A01 already established, not "
        "opened wider for this browse-before-connecting step",
        GITHUB_REPOSITORY_LISTING_ROLES == frozenset({Role.ORGANIZATION_ADMIN}),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A03 GitHub accessible-repository listing (routes): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A03 GitHub accessible-repository listing (routes): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
