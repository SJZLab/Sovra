#!/usr/bin/env python3
"""FTF-1503 -- CSRF suite (Section 45-47, 52-53, 65-66, 77, 81).

Proves `apps/api/app/auth/csrf_protection.py`'s real CSRF-protection
dependency end to end against a real, locally-hosted demonstration
FastAPI app -- a real uvicorn server over a real local socket, the same
pattern `tests/tasks/test_ftf_0103.py` established for
`app.auth.rbac_middleware.require_role`. This suite's own principal
resolver, role lookup and CSRF-token resolver are test doubles standing
in for a real session reader and a real DB-backed Membership lookup --
Section 84.4 permits test doubles for exactly this isolated proof.
Neither a live session reader nor a live DB-backed Membership lookup is
wired into `apps/api/app/main.py` yet (see that module's own docstring):
`require_role` itself is already proven the same way, against the same
kind of demonstration app, in `tests/tasks/test_ftf_0103.py` -- this is
the same disclosed "designed and proven, not yet wired into the real
product route" class of gap, not a new one this task introduces.

This suite composes the real `require_csrf_protection` with the real
`app.auth.rbac_middleware.require_role` on the demonstration app's own
mutating route, so both defenses are proven together the way a real
`/api/v1` mutation would actually need them: an authenticated caller
holding the right role is still denied a mutation unless their request
also carries a correct `Origin` and a correct `X-CSRF-Token`.

Scenarios (this task's own negative_acceptance: "Cross-site forged
form/fetch, missing token and unsafe CORS cannot mutate state"):
  1. Correct session cookie + matching `X-CSRF-Token` + same-origin +
     the right role -> the mutation succeeds (201).
  2. The identical session cookie replayed with no `X-CSRF-Token` header
     at all -- a plain cross-site `<form>` POST attaches a same-site
     cookie automatically but cannot set a custom header -> denied (403),
     no mutation.
  3. The identical session cookie replayed with a foreign `Origin`
     (`https://attacker.example`, simulating a cross-site `fetch()`),
     even carrying a stale/guessed token -> denied (403), no mutation.
  4. Correct `Origin`, a wrong/garbage `X-CSRF-Token` value -> denied
     (403), no mutation.
  5. No session cookie at all (a pure cross-site actor with no session of
     their own) -> denied (401) at the role/authentication layer, before
     CSRF is even reached.
  6. Correct `Origin` and correct token, but the caller's real role does
     not permit this mutation -> denied (403) at the role layer -- CSRF
     protection is not the only guard, and does not weaken it.
  7. Correct `Origin`, correct token, correct role, but a foreign
     Origin's own token used against the wrong session -> denied (403).

Run directly: python3 tests/security/csrf_suite.py
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

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "tests" / "security"))

import uvicorn  # noqa: E402
from fastapi import Depends, FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from tenant_isolation_suite import (  # noqa: E402
    ScenarioResult,
    TenantIsolationEvidence,
    build_evidence,
)

from app.auth.csrf_protection import (  # noqa: E402
    InMemoryCsrfSessionStore,
    require_csrf_protection,
)
from app.auth.rbac_middleware import (  # noqa: E402
    AuthenticatedPrincipal,
    AuthorizedContext,
    Role,
    require_role,
)

__all__ = ["run_csrf_scenarios", "ScenarioResult", "TenantIsolationEvidence"]

ISSUER = "https://issuer.example"
ALLOWED_ORIGIN = "https://app.sovra.example"
FOREIGN_ORIGIN = "https://attacker.example"
ORG = uuid.uuid4()

_ADMIN_SUBJECT = "user-admin"
_VIEWER_SUBJECT = "user-viewer"

# Real Membership fixture: one workspace_admin (Section 45's own
# "workspace_admin: project administration"), one viewer, matching
# `apps/api/app/projects/project_routes.py`'s own PROJECT_WRITE_ROLES.
_MEMBERSHIPS: dict[tuple[str, str, uuid.UUID], Role] = {
    (ISSUER, _ADMIN_SUBJECT, ORG): Role.WORKSPACE_ADMIN,
    (ISSUER, _VIEWER_SUBJECT, ORG): Role.VIEWER,
}

_COOKIE_NAME = "demo_session"


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _build_demo_app(store: InMemoryCsrfSessionStore) -> FastAPI:
    app = FastAPI()

    def _principal_resolver(request: Request) -> AuthenticatedPrincipal | None:
        session_id = request.cookies.get(_COOKIE_NAME)
        if session_id is None:
            return None
        subject = store.subject_for(session_id)
        if subject is None:
            return None
        return AuthenticatedPrincipal(issuer=ISSUER, subject=subject)

    def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        return _MEMBERSHIPS.get((issuer, subject, organization_id))

    def _csrf_token_resolver(request: Request) -> str | None:
        session_id = request.cookies.get(_COOKIE_NAME)
        if session_id is None:
            return None
        return store.csrf_token_for(session_id)

    require_write = Depends(
        require_role(
            frozenset({Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}),
            principal_resolver=_principal_resolver,
            role_lookup=_role_lookup,
        )
    )
    require_csrf = Depends(
        require_csrf_protection(
            allowed_origin=ALLOWED_ORIGIN, csrf_token_resolver=_csrf_token_resolver
        )
    )

    @app.post("/demo/login")
    def login(subject: str) -> JSONResponse:
        session_id, csrf_token = store.create(subject)
        response = JSONResponse({"csrf_token": csrf_token})
        response.set_cookie(_COOKIE_NAME, session_id, httponly=True, secure=True, samesite="lax")
        return response

    @app.post("/api/v1/organizations/{organization_id}/projects", status_code=201)
    def create_project(
        organization_id: uuid.UUID,
        ctx: AuthorizedContext = require_write,
        _csrf: None = require_csrf,
    ) -> dict[str, object]:
        return {"organization_id": str(ctx.organization_id), "created": True}

    return app


@contextlib.contextmanager
def _running_demo_app(store: InMemoryCsrfSessionStore) -> Iterator[str]:
    app = _build_demo_app(store)
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
    cookie: str | None

    def json(self) -> dict[str, object]:
        return json.loads(self.body)  # type: ignore[no-any-return]


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    cookie: str | None = None,
    origin: str | None = None,
    csrf_token: str | None = None,
) -> _HttpResult:
    headers: dict[str, str] = {}
    if cookie is not None:
        headers["Cookie"] = f"{_COOKIE_NAME}={cookie}"
    if origin is not None:
        headers["Origin"] = origin
    if csrf_token is not None:
        headers["X-CSRF-Token"] = csrf_token

    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, headers=headers)
        response = conn.getresponse()
        raw_body = response.read()
        set_cookie = response.getheader("Set-Cookie")
        cookie_value = None
        if set_cookie and set_cookie.startswith(f"{_COOKIE_NAME}="):
            cookie_value = set_cookie.split(";", 1)[0].split("=", 1)[1]
        return _HttpResult(status=response.status, body=raw_body, cookie=cookie_value)
    finally:
        conn.close()


def run_csrf_scenarios() -> list[ScenarioResult]:
    """Runs every scenario against a real, freshly started demonstration
    app and returns the real per-scenario results. Never mutates module
    state across calls (a fresh `InMemoryCsrfSessionStore` and a fresh
    server each time), so this is safe to call more than once in the same
    process (`tests/tasks/test_ftf_1503.py` does exactly that)."""
    results: list[ScenarioResult] = []
    store = InMemoryCsrfSessionStore()

    def record(
        check_name: str, method: str, path: str, *, status: int, expected_status: int
    ) -> None:
        results.append(
            ScenarioResult(
                resource_kind="csrf",
                check=check_name,
                method=method,
                path=path,
                status=status,
                expected_denied=expected_status in (401, 403),
                passed=status == expected_status,
            )
        )

    with _running_demo_app(store) as base_url:
        path = f"/api/v1/organizations/{ORG}/projects"

        login = _request(base_url, "POST", f"/demo/login?subject={_ADMIN_SUBJECT}")
        admin_cookie = login.cookie
        admin_csrf = login.json()["csrf_token"]
        assert admin_cookie is not None

        viewer_login = _request(base_url, "POST", f"/demo/login?subject={_VIEWER_SUBJECT}")
        viewer_cookie = viewer_login.cookie
        viewer_csrf = viewer_login.json()["csrf_token"]

        # 1. Everything correct -> mutation succeeds.
        result = _request(
            base_url,
            "POST",
            path,
            cookie=admin_cookie,
            origin=ALLOWED_ORIGIN,
            csrf_token=admin_csrf,
        )
        record(
            "same_origin_correct_token_mutates",
            "POST",
            path,
            status=result.status,
            expected_status=201,
        )

        # 2. Same cookie, no X-CSRF-Token at all (plain cross-site <form>
        # POST: the cookie is attached automatically, a custom header is
        # not).
        result = _request(base_url, "POST", path, cookie=admin_cookie, origin=ALLOWED_ORIGIN)
        record(
            "missing_token_denied",
            "POST",
            path,
            status=result.status,
            expected_status=403,
        )

        # 3. Same cookie, foreign Origin (cross-site fetch()), even with
        # the real token attached -- Origin alone must deny it.
        result = _request(
            base_url,
            "POST",
            path,
            cookie=admin_cookie,
            origin=FOREIGN_ORIGIN,
            csrf_token=admin_csrf,
        )
        record(
            "foreign_origin_denied_even_with_real_token",
            "POST",
            path,
            status=result.status,
            expected_status=403,
        )

        # 4. Correct Origin, wrong/garbage token.
        result = _request(
            base_url,
            "POST",
            path,
            cookie=admin_cookie,
            origin=ALLOWED_ORIGIN,
            csrf_token="not-the-real-token",
        )
        record(
            "wrong_token_denied",
            "POST",
            path,
            status=result.status,
            expected_status=403,
        )

        # 5. No session cookie at all -- denied at the auth/role layer
        # (401), before CSRF is even reached.
        result = _request(base_url, "POST", path, origin=ALLOWED_ORIGIN, csrf_token="anything")
        record(
            "no_session_denied_unauthenticated",
            "POST",
            path,
            status=result.status,
            expected_status=401,
        )

        # 6. Correct Origin and correct token, but the caller's real role
        # (viewer) does not permit this mutation -- CSRF protection is not
        # the only guard, and does not weaken the role check.
        result = _request(
            base_url,
            "POST",
            path,
            cookie=viewer_cookie,
            origin=ALLOWED_ORIGIN,
            csrf_token=viewer_csrf,
        )
        record(
            "correct_csrf_wrong_role_denied",
            "POST",
            path,
            status=result.status,
            expected_status=403,
        )

        # 7. Correct Origin, but the admin's cookie paired with the
        # viewer's own (real, currently valid) token -- one session's
        # token can never authorize a different session's mutation.
        result = _request(
            base_url,
            "POST",
            path,
            cookie=admin_cookie,
            origin=ALLOWED_ORIGIN,
            csrf_token=viewer_csrf,
        )
        record(
            "cross_session_token_denied",
            "POST",
            path,
            status=result.status,
            expected_status=403,
        )

    return results


def main() -> None:
    results = run_csrf_scenarios()
    evidence = build_evidence(results)
    print(json.dumps(evidence.to_dict(), indent=2))

    failures = [r for r in results if not r.passed]
    if failures:
        print(f"FTF-1503 CSRF suite: {len(failures)} failed")
        for r in failures:
            print(
                f"  FAIL [FTF-1503] {r.resource_kind}/{r.check}: "
                f"{r.method} {r.path} -> {r.status}"
            )
        sys.exit(1)

    print(
        f"FTF-1503 CSRF suite: all checks passed "
        f"({evidence.scenarios_passed}/{evidence.scenarios_total})"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
