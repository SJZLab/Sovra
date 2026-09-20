#!/usr/bin/env python3
"""FTF-0106 -- RBAC/IDOR security-test harness.

Runnable without Postgres or network egress (the real live-database
two-tenant, six-role, guessed-ID matrix evidence is
`tests/integration/test_ftf_0106_rbac_idor_security_matrix.py`, run in
CI's dedicated `rbac-idor-security-matrix-integration` job -- this repo's
interactive dev sandbox has no container-registry egress, same documented
finding as FTF-0004/FTF-0102/FTF-0104/FTF-0105/FTF-H003). This suite
proves, without any database, that the harness's own pure-Python
structure is correct -- the role matrix genuinely covers all six real
roles, the read/write policy is grounded in Section 45's own worked
examples, and the demonstration app genuinely denies before it ever
reaches the database. It never asserts that a *mocked* role lookup
returning a canned value proves the negative_acceptance clause -- that
clause is proven only by the real-Postgres suite, where the lookup is a
genuine SQL `SELECT` against real seeded rows.

Run directly: python3 tests/tasks/test_ftf_0106.py
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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.auth.rbac_idor_security_tests import (  # noqa: E402
    READ_ALLOWED_ROLES,
    ROLE_MATRIX,
    WORKSPACE_WRITE_ALLOWED_ROLES,
    all_roles_except,
    build_rbac_idor_demo_app,
    security_test_memberships,
)
from app.auth.rbac_middleware import ALL_ROLES, AuthenticatedPrincipal, Role  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- 1. ROLE_MATRIX genuinely covers every real Section 6.4 role ------------


def test_role_matrix_covers_every_real_role() -> None:
    check(
        "FTF-0106",
        "ROLE_MATRIX is exactly Section 6.4's six-role set, no subset chosen for convenience",
        frozenset(ROLE_MATRIX) == ALL_ROLES and len(ROLE_MATRIX) == 6,
    )


def test_read_allowed_roles_includes_every_role() -> None:
    check(
        "FTF-0106",
        "every real role can read (Section 45: 'viewer: read only' is the "
        "floor, not an exclusion)",
        READ_ALLOWED_ROLES == frozenset(ROLE_MATRIX),
    )


def test_workspace_write_allowed_roles_is_a_strict_subset() -> None:
    check(
        "FTF-0106",
        "workspace-write is restricted to workspace_admin/organization_admin "
        "only (Section 45's own worked examples), a strict, non-empty subset "
        "of the full role matrix",
        WORKSPACE_WRITE_ALLOWED_ROLES == {Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}
        and WORKSPACE_WRITE_ALLOWED_ROLES < frozenset(ROLE_MATRIX),
    )


def test_all_roles_except_helper() -> None:
    remaining = all_roles_except({Role.VIEWER})
    check(
        "FTF-0106",
        "all_roles_except excludes exactly the named role(s) and nothing else",
        Role.VIEWER not in remaining and len(remaining) == 5,
    )


# -- 2. security_test_memberships is never the frozen production schema ----


def test_security_test_membership_table_is_not_a_production_table() -> None:
    check(
        "FTF-0106",
        "the harness table name is underscore-prefixed and distinct from "
        "any real Section 6 entity table name (never masquerades as the "
        "frozen 'Membership' contract)",
        security_test_memberships.name == "_security_test_memberships"
        and security_test_memberships.name not in {"organizations", "workspaces", "memberships"},
    )


def test_security_test_membership_columns_are_the_minimum_needed() -> None:
    columns = {c.name for c in security_test_memberships.columns}
    check(
        "FTF-0106",
        "the harness table carries exactly the columns a role lookup needs "
        "(issuer, subject, organization_id, role) plus its own id -- no "
        "invented extra Membership-shaped columns (workspace_id, etc.)",
        columns == {"id", "issuer", "subject", "organization_id", "role"},
    )


# -- 3. the demo app denies before ever reaching the database ---------------
#
# Real uvicorn server over a real local socket -- the same pattern
# `tests/tasks/test_ftf_0103.py` and
# `tests/integration/test_ftf_h003_cross_layer_isolation.py` already
# established -- never FastAPI's `TestClient` (which needs `httpx`, not an
# owner-approved dependency of this repository).

_ISSUER = "https://issuer.example"
_ORG_ID = uuid.uuid4()
_WORKSPACE_ID = uuid.uuid4()


def _no_op_session_factory():  # pragma: no cover - must never be called
    raise AssertionError(
        "the session factory must never be invoked once require_role has "
        "already denied the request -- authorization happens before any "
        "database session is opened"
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


def _request(base_url: str, method: str, path: str) -> int:
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path)
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


def test_demo_app_denies_unauthenticated_reads_without_touching_the_database() -> None:
    def _resolver(request: object) -> AuthenticatedPrincipal | None:
        return None

    def _lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        raise AssertionError("role_lookup must never run for an unauthenticated request")

    app = build_rbac_idor_demo_app(
        _no_op_session_factory, principal_resolver=_resolver, role_lookup=_lookup
    )
    with _running_app(app) as base_url:
        status = _request(base_url, "GET", f"/api/v1/orgs/{_ORG_ID}/workspaces/{_WORKSPACE_ID}")
    check(
        "FTF-0106",
        "an unauthenticated read is denied (401) before any role lookup or "
        "database session is ever touched",
        status == 401,
    )


def test_demo_app_denies_a_role_with_no_membership_row_without_touching_the_database() -> None:
    principal = AuthenticatedPrincipal(issuer=_ISSUER, subject="user-nobody")

    def _resolver(request: object) -> AuthenticatedPrincipal | None:
        return principal

    def _lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        return None  # genuinely fail-closed: no real row would exist for this principal

    app = build_rbac_idor_demo_app(
        _no_op_session_factory, principal_resolver=_resolver, role_lookup=_lookup
    )
    with _running_app(app) as base_url:
        status = _request(base_url, "GET", f"/api/v1/orgs/{_ORG_ID}/workspaces/{_WORKSPACE_ID}")
    check(
        "FTF-0106",
        "a principal with no real membership row anywhere is denied (403) "
        "before any database session is opened for the resource itself",
        status == 403,
    )


def test_demo_app_denies_a_read_only_role_from_writing() -> None:
    principal = AuthenticatedPrincipal(issuer=_ISSUER, subject="user-viewer")

    def _resolver(request: object) -> AuthenticatedPrincipal | None:
        return principal

    def _lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        return Role.VIEWER if organization_id == _ORG_ID else None

    app = build_rbac_idor_demo_app(
        _no_op_session_factory, principal_resolver=_resolver, role_lookup=_lookup
    )
    with _running_app(app) as base_url:
        status = _request(base_url, "PATCH", f"/api/v1/orgs/{_ORG_ID}/workspaces/{_WORKSPACE_ID}")
    check(
        "FTF-0106",
        "negative_acceptance: a real, validly authorized viewer role -- "
        "genuinely resolved, not merely absent -- is still denied (403) "
        "write access, never reaching the resource-ownership check",
        status == 403,
    )


def test_demo_app_denies_every_non_write_role_from_writing() -> None:
    excluded = list(all_roles_except(WORKSPACE_WRITE_ALLOWED_ROLES))
    check(
        "FTF-0106",
        "exactly 4 roles are exercised here, not a fault-collapsed empty "
        "set (Section 84.4: zero collected checks is failure, not a "
        "vacuous pass)",
        len(excluded) == 4,
    )
    for role in excluded:
        principal = AuthenticatedPrincipal(issuer=_ISSUER, subject=f"user-{role.value}")

        def _resolver(request: object, _principal: AuthenticatedPrincipal = principal):
            return _principal

        def _lookup(
            issuer: str, subject: str, organization_id: uuid.UUID, _role: Role = role
        ) -> Role | None:
            return _role if organization_id == _ORG_ID else None

        app = build_rbac_idor_demo_app(
            _no_op_session_factory, principal_resolver=_resolver, role_lookup=_lookup
        )
        with _running_app(app) as base_url:
            status = _request(
                base_url, "PATCH", f"/api/v1/orgs/{_ORG_ID}/workspaces/{_WORKSPACE_ID}"
            )
        check(
            "FTF-0106",
            f"role {role.value!r}, outside WORKSPACE_WRITE_ALLOWED_ROLES, is "
            "denied (403) write access",
            status == 403,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0106 RBAC/IDOR security-test harness: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0106 RBAC/IDOR security-test harness: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
