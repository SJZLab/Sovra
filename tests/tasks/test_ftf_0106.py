#!/usr/bin/env python3
"""FTF-0106 -- RBAC/IDOR security tests.

Runnable without Postgres or network egress (the real live-database,
full six-role, two-tenant read/write/approve-with-guessed-IDs evidence is
`tests/integration/test_ftf_0106_rbac_idor_security.py`, run in CI's
dedicated `rbac-idor-security-integration` job -- this repo's interactive
dev sandbox has no container-registry egress, same documented finding as
FTF-0004/FTF-0102/FTF-0104/FTF-0105/FTF-H003/FTF-R017; see that file's
docstring). This suite proves, without any database:

1. `TwoTenantRoleMatrixFixture.build()` genuinely assigns all six of
   Section 6.4's roles to a distinct real principal in each of two real
   organizations -- twelve distinct (issuer, subject) identities, never
   one principal reused across roles or organizations.
2. The fixture's `principal_resolver`/`role_lookup` are fail-closed on
   every malformed/unknown/cross-fixture input, never silently guessing.
3. `build_two_tenant_security_demo_app`'s role-boundary gate genuinely
   denies a disallowed role *before* ever touching a database session --
   proven against a real, locally-hosted demonstration FastAPI app (a
   real uvicorn server over a real local socket, the same pattern
   `tests/tasks/test_ftf_0101.py`/`test_ftf_0103.py` established) with a
   test-double `session_factory` this suite can count calls against.

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
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import uvicorn

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.auth.organization_workspace_schema import Workspace  # noqa: E402
from app.auth.rbac_idor_security_tests import (  # noqa: E402
    APPROVE_ROLES,
    READ_ROLES,
    WRITE_ROLES,
    TwoTenantRoleMatrixFixture,
    build_two_tenant_security_demo_app,
)
from app.auth.rbac_middleware import Role  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# -- 1. TwoTenantRoleMatrixFixture: a real full six-role, two-org matrix -----

ORG1 = uuid.uuid4()
ORG2 = uuid.uuid4()


def _build_fixture() -> TwoTenantRoleMatrixFixture:
    return TwoTenantRoleMatrixFixture.build({"org1": ORG1, "org2": ORG2})


def test_fixture_assigns_all_six_roles_in_both_organizations() -> None:
    fixture = _build_fixture()
    expected_keys = {(label, role) for label in ("org1", "org2") for role in Role}
    check(
        "FTF-0106",
        "the fixture assigns exactly the 12 (org_label, role) combinations "
        "-- all six roles, in both organizations",
        set(fixture.principals.keys()) == expected_keys,
    )
    subjects = {p.subject for p in fixture.principals.values()}
    check(
        "FTF-0106",
        "every one of the 12 principals has a distinct subject -- no role "
        "or organization shares an identity with another",
        len(subjects) == 12,
    )


def test_fixture_rejects_a_non_two_organization_map() -> None:
    for bad_orgs in ({"org1": ORG1}, {"org1": ORG1, "org2": ORG2, "org3": uuid.uuid4()}, {}):
        try:
            TwoTenantRoleMatrixFixture.build(bad_orgs)
            check(
                "FTF-0106",
                f"building the fixture with {len(bad_orgs)} organization(s) is refused",
                False,
            )
        except ValueError:
            check(
                "FTF-0106",
                f"building the fixture with {len(bad_orgs)} organization(s) is refused",
                True,
            )


def test_role_lookup_denies_a_real_subject_in_the_wrong_organization() -> None:
    fixture = _build_fixture()
    viewer_org1 = fixture.principals[("org1", Role.VIEWER)]
    check(
        "FTF-0106",
        "negative_acceptance-adjacent: a real principal's role lookup in "
        "their own organization succeeds",
        fixture.role_lookup(viewer_org1.issuer, viewer_org1.subject, ORG1) == Role.VIEWER,
    )
    check(
        "FTF-0106",
        "the exact same real principal has no role at all in the other "
        "real organization -- the fixture never grants a role by subject "
        "alone, only by (issuer, subject, organization_id) together",
        fixture.role_lookup(viewer_org1.issuer, viewer_org1.subject, ORG2) is None,
    )


def test_principal_resolver_is_fail_closed_on_malformed_input() -> None:
    fixture = _build_fixture()
    from fastapi import Request

    def _request_with_header(value: str | None) -> Request:
        headers = [] if value is None else [(b"x-test-principal", value.encode())]
        return Request({"type": "http", "path_params": {}, "method": "GET", "headers": headers})

    check(
        "FTF-0106",
        "no principal header at all resolves to no principal",
        fixture.principal_resolver(_request_with_header(None)) is None,
    )
    check(
        "FTF-0106",
        "an unknown role value in the header resolves to no principal",
        fixture.principal_resolver(_request_with_header("org1:not-a-real-role")) is None,
    )
    check(
        "FTF-0106",
        "a well-formed role for an organization label the fixture never "
        "defined resolves to no principal, not a guessed match",
        fixture.principal_resolver(_request_with_header("org9:viewer")) is None,
    )
    real_header = fixture.header_for("org1", Role.VIEWER)
    check(
        "FTF-0106",
        "a real, well-formed header resolves to the exact matching principal",
        fixture.principal_resolver(_request_with_header(real_header))
        == fixture.principals[("org1", Role.VIEWER)],
    )


def test_role_allow_lists_match_section_45() -> None:
    check("FTF-0106", "READ_ROLES is every one of the six roles", READ_ROLES == frozenset(Role))
    check(
        "FTF-0106",
        "WRITE_ROLES is exactly ml_engineer/workspace_admin/organization_admin",
        WRITE_ROLES == frozenset({Role.ML_ENGINEER, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}),
    )
    check(
        "FTF-0106",
        "APPROVE_ROLES is exactly approver/organization_admin",
        APPROVE_ROLES == frozenset({Role.APPROVER, Role.ORGANIZATION_ADMIN}),
    )
    check(
        "FTF-0106",
        "viewer/evaluator can read but cannot write or approve -- the "
        "role boundaries this task's own matrix exercises are real, not "
        "vacuous (every role allowed everywhere)",
        Role.VIEWER not in WRITE_ROLES
        and Role.VIEWER not in APPROVE_ROLES
        and Role.EVALUATOR not in WRITE_ROLES
        and Role.EVALUATOR not in APPROVE_ROLES,
    )


# -- 2. Real demonstration app: role-boundary denial never opens a session --


@contextlib.contextmanager
def _running_demo_app(session_factory) -> Iterator[str]:
    fixture = _build_fixture()
    app = build_two_tenant_security_demo_app(
        fixture,
        session_factory,
        Workspace.__table__,
        Workspace.__table__.c.id,
        Workspace.__table__.c.organization_id,
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
    with _running_demo_app(session_factory) as base_url:
        result = _request(base_url, "GET", f"/api/v1/orgs/{ORG1}/workspaces/{uuid.uuid4()}")
        check("FTF-0106", "an unauthenticated request is denied (401)", result.status == 401)
        check(
            "FTF-0106",
            "an unauthenticated request never even takes a connection from "
            "the pool -- session_factory is never called",
            session_factory.call_count == 0,
        )


def test_role_boundary_denial_never_opens_a_session() -> None:
    session_factory = MagicMock()
    with _running_demo_app(session_factory) as base_url:
        viewer_header = TwoTenantRoleMatrixFixture.build({"org1": ORG1, "org2": ORG2}).header_for(
            "org1", Role.VIEWER
        )
        result = _request(
            base_url,
            "PATCH",
            f"/api/v1/orgs/{ORG1}/workspaces/{uuid.uuid4()}",
            principal=viewer_header,
        )
        check(
            "FTF-0106",
            "a viewer (not in WRITE_ROLES) is denied a write (403), matching "
            "Section 45's own role description",
            result.status == 403,
        )
        check(
            "FTF-0106",
            "negative_acceptance/ordering: a role-boundary denial never "
            "opens a database session -- the role gate runs first",
            session_factory.call_count == 0,
        )

        result = _request(
            base_url,
            "POST",
            f"/api/v1/orgs/{ORG1}/workspaces/{uuid.uuid4()}/approve",
            principal=viewer_header,
        )
        check(
            "FTF-0106",
            "a viewer (not in APPROVE_ROLES) is denied an approval (403)",
            result.status == 403,
        )
        check(
            "FTF-0106",
            "an approval role-boundary denial also never opens a session",
            session_factory.call_count == 0,
        )


def test_guessed_foreign_organization_is_denied_before_any_resource_lookup() -> None:
    session_factory = MagicMock()
    with _running_demo_app(session_factory) as base_url:
        org2_admin_header = TwoTenantRoleMatrixFixture.build(
            {"org1": ORG1, "org2": ORG2}
        ).header_for("org2", Role.ORGANIZATION_ADMIN)
        # A real, validly authorized organization_admin of org2 has no
        # membership in org1 at all -- denied at the role/tenant-membership
        # layer, before authorize_resource_ownership (and therefore any
        # database session) is ever reached.
        result = _request(
            base_url,
            "GET",
            f"/api/v1/orgs/{ORG1}/workspaces/{uuid.uuid4()}",
            principal=org2_admin_header,
        )
        check(
            "FTF-0106",
            "negative_acceptance: a real, elevated role in the wrong "
            "organization is denied (403), never granted by role alone",
            result.status == 403,
        )
        check(
            "FTF-0106",
            "this no-membership-in-this-org denial never opens a session either",
            session_factory.call_count == 0,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0106 RBAC/IDOR security tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0106 RBAC/IDOR security tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
