#!/usr/bin/env python3
"""FTF-0103 -- RBAC middleware.

Proves `apps/api/app/auth/rbac_middleware.py`'s real, server-side
role/tenant enforcement (Section 45/65.1) for `/api/v1` mutations.

No live `/api/v1` product route exists anywhere in this repository yet
(`apps/api/app/main.py` only wires FTF-0101's `/auth/*` router) -- the
same disclosed "no live caller yet" class already accepted for
FTF-0005/FTF-0006/FTF-0102/FTF-0104 before each one's own first live
caller existed. This suite proves `require_role` end to end against a
real, locally-hosted demonstration FastAPI app -- a real uvicorn server
over a real local socket, the same pattern tests/tasks/test_ftf_0101.py
established -- exercising real FastAPI routing, dependency injection,
path-parameter parsing and JSON-body parsing, not an in-process function
call or a mocked request object.

The demonstration app's principal resolution (`X-Test-Principal` header)
and role lookup (a plain dict) are test doubles standing in for a real
session reader and a real DB-backed Membership lookup -- Section 84.4
permits test doubles for exactly this isolated proof. Neither exists yet
in this repository (see the module's own docstring): `default_role_lookup`
is fail-closed because no Membership table exists (this task's own
`database_changes` contract forbids an independent schema change), and
`default_principal_resolver` is fail-closed because no live session
reader is wired in yet. Both are proven fail-closed directly below, not
hidden.

This task's own negative_acceptance clause -- "Guessed UUID or
body-selected tenant cannot bypass viewer/evaluator/approver boundaries"
-- is proven against all three of those named roles: a viewer cannot
create an evaluation; an evaluator cannot approve a promotion; a guessed
organization UUID the caller has no membership in is denied regardless of
the real role they hold elsewhere; and a request body that names a
different organization than the URL path is denied outright, never
trusted to select or override the enforced tenant.

Run directly: python3 tests/tasks/test_ftf_0103.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
import pathlib
import socket
import sys
import threading
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import uvicorn
from fastapi import Depends, FastAPI, Request

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.auth.rbac_middleware import (  # noqa: E402
    ALL_ROLES,
    AuthenticatedPrincipal,
    AuthorizedContext,
    Role,
    TenantResolutionError,
    default_principal_resolver,
    default_role_lookup,
    organization_id_from_path,
    require_role,
)

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


# --- Module-level unit/fail-closed evidence (no server needed) --------------


def test_role_enum_matches_section_6_4_exactly() -> None:
    expected = {
        "organization_admin",
        "workspace_admin",
        "ml_engineer",
        "evaluator",
        "approver",
        "viewer",
    }
    actual = {role.value for role in Role}
    check("FTF-0103", "Role enum is exactly Section 6.4's allowed role set", actual == expected)
    check("FTF-0103", "ALL_ROLES matches the Role enum", ALL_ROLES == frozenset(Role))


def test_default_principal_resolver_is_fail_closed() -> None:
    scope = {"type": "http", "path_params": {}, "method": "GET", "headers": []}
    request = Request(scope)
    check(
        "FTF-0103",
        "default_principal_resolver denies every request (no session reader wired yet)",
        default_principal_resolver(request) is None,
    )


def test_default_role_lookup_is_fail_closed() -> None:
    check(
        "FTF-0103",
        "default_role_lookup grants no role (no Membership table exists yet)",
        default_role_lookup("https://issuer.example", "user-1", uuid.uuid4()) is None,
    )


def test_organization_id_from_path_parses_a_real_uuid() -> None:
    org_id = uuid.uuid4()
    scope = {
        "type": "http",
        "path_params": {"organization_id": str(org_id)},
        "method": "GET",
        "headers": [],
    }
    request = Request(scope)
    resolved = organization_id_from_path()(request)
    check(
        "FTF-0103",
        "organization_id_from_path parses a well-formed path UUID",
        resolved == org_id,
    )


def test_organization_id_from_path_rejects_missing_param() -> None:
    scope = {"type": "http", "path_params": {}, "method": "GET", "headers": []}
    request = Request(scope)
    try:
        organization_id_from_path()(request)
        raised = False
    except TenantResolutionError:
        raised = True
    check(
        "FTF-0103",
        "organization_id_from_path raises when the path has no organization_id",
        raised,
    )


def test_organization_id_from_path_rejects_malformed_uuid() -> None:
    scope = {
        "type": "http",
        "path_params": {"organization_id": "not-a-uuid"},
        "method": "GET",
        "headers": [],
    }
    request = Request(scope)
    try:
        organization_id_from_path()(request)
        raised = False
    except TenantResolutionError:
        raised = True
    check(
        "FTF-0103",
        "organization_id_from_path raises on a malformed UUID rather than passing it through",
        raised,
    )


def test_require_role_rejects_empty_allowed_roles() -> None:
    try:
        require_role(frozenset())
        raised = False
    except ValueError:
        raised = True
    check(
        "FTF-0103",
        "require_role refuses to build a dependency with no allowed role at all",
        raised,
    )


# --- Real demonstration app: real uvicorn server, real local socket ---------

ISSUER = "https://issuer.example"

ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()

# (issuer, subject) -> AuthenticatedPrincipal, keyed by the test-only
# `X-Test-Principal` header value the demo app's own principal_resolver reads.
_PRINCIPALS: dict[str, AuthenticatedPrincipal] = {
    "viewer": AuthenticatedPrincipal(issuer=ISSUER, subject="user-viewer"),
    "evaluator": AuthenticatedPrincipal(issuer=ISSUER, subject="user-evaluator"),
    "approver": AuthenticatedPrincipal(issuer=ISSUER, subject="user-approver"),
    "no-membership": AuthenticatedPrincipal(issuer=ISSUER, subject="user-ml"),
}

# Real Membership fixture: exactly one role, in exactly one organization,
# per test principal -- "user-ml" is intentionally absent (no row anywhere).
_MEMBERSHIPS: dict[tuple[str, str, uuid.UUID], Role] = {
    (ISSUER, "user-viewer", ORG_A): Role.VIEWER,
    (ISSUER, "user-evaluator", ORG_A): Role.EVALUATOR,
    (ISSUER, "user-approver", ORG_B): Role.APPROVER,
}


def _test_principal_resolver(request: Request) -> AuthenticatedPrincipal | None:
    header = request.headers.get("x-test-principal")
    if header is None:
        return None
    return _PRINCIPALS.get(header)


def _test_role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
    return _MEMBERSHIPS.get((issuer, subject, organization_id))


def _build_demo_app() -> FastAPI:
    app = FastAPI()

    read_roles = frozenset(Role)  # any real member may read
    create_evaluation_roles = frozenset(
        {Role.EVALUATOR, Role.ML_ENGINEER, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}
    )
    approve_promotion_roles = frozenset({Role.APPROVER, Role.ORGANIZATION_ADMIN})

    # Built once at module-build time, referenced (not called) in each
    # route's own default -- ruff B008 flags a literal Depends(...) call
    # sitting in a parameter default; this is the same fix FastAPI's own
    # docs recommend, with no behavioral difference.
    require_read = Depends(
        require_role(
            read_roles, principal_resolver=_test_principal_resolver, role_lookup=_test_role_lookup
        )
    )
    require_create_evaluation = Depends(
        require_role(
            create_evaluation_roles,
            principal_resolver=_test_principal_resolver,
            role_lookup=_test_role_lookup,
        )
    )
    require_approve_promotion = Depends(
        require_role(
            approve_promotion_roles,
            principal_resolver=_test_principal_resolver,
            role_lookup=_test_role_lookup,
        )
    )

    @app.get("/api/v1/orgs/{organization_id}/evaluations")
    def list_evaluations(ctx: AuthorizedContext = require_read) -> dict:
        return {"organization_id": str(ctx.organization_id), "role": ctx.role.value, "items": []}

    @app.post("/api/v1/orgs/{organization_id}/evaluations")
    def create_evaluation(ctx: AuthorizedContext = require_create_evaluation) -> dict:
        return {"organization_id": str(ctx.organization_id), "created": True}

    @app.post("/api/v1/orgs/{organization_id}/promotions/{model_id}/approve")
    def approve_promotion(ctx: AuthorizedContext = require_approve_promotion) -> dict:
        return {"organization_id": str(ctx.organization_id), "approved": True}

    return app


@contextlib.contextmanager
def _running_demo_app() -> Iterator[str]:
    app = _build_demo_app()
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

    def json(self) -> dict:
        return json.loads(self.body)


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    principal: str | None = None,
    json_body: dict | None = None,
) -> _HttpResult:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    body: bytes | None = None
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        raw_body = response.read()
        return _HttpResult(status=response.status, body=raw_body)
    finally:
        conn.close()


def test_real_demo_app_enforces_roles_and_tenants() -> None:
    with _running_demo_app() as base_url:
        # Unauthenticated -> 401.
        result = _request(base_url, "GET", f"/api/v1/orgs/{ORG_A}/evaluations")
        check(
            "FTF-0103",
            "an unauthenticated request is denied with 401, not silently allowed",
            result.status == 401,
        )

        # A real member with a real role in their own org can read.
        result = _request(base_url, "GET", f"/api/v1/orgs/{ORG_A}/evaluations", principal="viewer")
        check(
            "FTF-0103",
            "a viewer can read their own organization's resources",
            result.status == 200,
        )

        # Section 45: "viewer: read only" -- create is a mutation.
        result = _request(
            base_url,
            "POST",
            f"/api/v1/orgs/{ORG_A}/evaluations",
            principal="viewer",
            json_body={},
        )
        check(
            "FTF-0103",
            "negative_acceptance: a viewer cannot create an evaluation (their own role boundary)",
            result.status == 403,
        )

        # Section 45: "evaluator: create/run evaluation, no deployment."
        result = _request(
            base_url,
            "POST",
            f"/api/v1/orgs/{ORG_A}/evaluations",
            principal="evaluator",
            json_body={},
        )
        check(
            "FTF-0103",
            "an evaluator can create an evaluation in their own organization",
            result.status == 200,
        )

        result = _request(
            base_url,
            "POST",
            f"/api/v1/orgs/{ORG_A}/promotions/model-1/approve",
            principal="evaluator",
            json_body={},
        )
        check(
            "FTF-0103",
            "negative_acceptance: an evaluator cannot approve a promotion "
            "(their own role boundary)",
            result.status == 403,
        )

        # A real approver, in their own real organization, can approve.
        result = _request(
            base_url,
            "POST",
            f"/api/v1/orgs/{ORG_B}/promotions/model-1/approve",
            principal="approver",
            json_body={},
        )
        check(
            "FTF-0103",
            "an approver can approve a promotion in their own organization",
            result.status == 200,
        )

        # Guessed UUID: the approver has a real, elevated role -- just not
        # in ORG_A, which they have no membership in at all.
        result = _request(
            base_url,
            "POST",
            f"/api/v1/orgs/{ORG_A}/promotions/model-1/approve",
            principal="approver",
            json_body={},
        )
        check(
            "FTF-0103",
            "negative_acceptance: a guessed org UUID the caller has no "
            "membership in is denied even though they hold that exact "
            "role elsewhere",
            result.status == 403,
        )

        result = _request(base_url, "GET", f"/api/v1/orgs/{ORG_B}/evaluations", principal="viewer")
        check(
            "FTF-0103",
            "negative_acceptance: a guessed org UUID is denied for a read too, "
            "not only for mutations",
            result.status == 403,
        )

        # No membership anywhere -> denied regardless of organization.
        result = _request(
            base_url, "GET", f"/api/v1/orgs/{ORG_A}/evaluations", principal="no-membership"
        )
        check(
            "FTF-0103",
            "a principal with no Membership row anywhere is denied",
            result.status == 403,
        )

        # Body-selected tenant: a real evaluator, hitting their own real
        # org's path (which alone would be permitted), names a *different*
        # organization inside the JSON body.
        result = _request(
            base_url,
            "POST",
            f"/api/v1/orgs/{ORG_A}/evaluations",
            principal="evaluator",
            json_body={"organization_id": str(ORG_B)},
        )
        check(
            "FTF-0103",
            "negative_acceptance: a body-selected tenant that disagrees with "
            "the URL path is denied outright, never trusted to override it",
            result.status == 403,
        )

        # The same body field, but agreeing with the path, is unaffected.
        result = _request(
            base_url,
            "POST",
            f"/api/v1/orgs/{ORG_A}/evaluations",
            principal="evaluator",
            json_body={"organization_id": str(ORG_A)},
        )
        check(
            "FTF-0103",
            "a body tenant field that agrees with the URL path does not " "itself cause a denial",
            result.status == 200,
        )

        # Malformed path organization id -> 400, not a silent pass-through.
        result = _request(
            base_url, "GET", "/api/v1/orgs/not-a-uuid/evaluations", principal="viewer"
        )
        check(
            "FTF-0103",
            "a malformed organization id in the path is rejected, not passed through",
            result.status == 400,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0103 RBAC middleware: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0103 RBAC middleware: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
