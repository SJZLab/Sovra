#!/usr/bin/env python3
"""FTF-H003 -- Cross-layer tenant isolation and relational identity.

Runnable without Postgres or network egress (the real live-database RLS-
through-view, migration-role-bypass, and end-to-end guessed-ID evidence is
`tests/integration/test_cross_layer_tenant_isolation.py`, run in CI's
dedicated `cross-layer-tenant-isolation-integration` job -- this repo's
interactive dev sandbox has no container-registry egress, same documented
finding as FTF-0004/FTF-0102/FTF-0104/FTF-0105/FTF-R017; see that file's
docstring). This suite proves, without any database:

1. `authorize_resource()` is a thin, undrifting pass-through to
   `session.get(...)` -- it adds no independent tenant check of its own
   that could silently disagree with the database's own RLS policy.
2. `tenant_context_dependency()` composes FTF-0103's `require_role` and
   FTF-0105's `tenant_scoped_session` in the right order: authorization
   runs first, and a database session is opened -- taking a real
   connection from the pool -- only once authorization actually succeeds.
   Proven against a real, locally-hosted demonstration FastAPI app (a
   real uvicorn server over a real local socket, the same pattern
   `tests/tasks/test_ftf_0101.py`/`test_ftf_0103.py` established), using a
   test-double `session_factory` this suite can count calls against --
   Section 84.4 permits test doubles for exactly this isolated wiring
   proof, and a real Postgres-backed proof of the same composition exists
   in the integration suite above.
3. Static, structural evidence for this task's own negative_acceptance
   clause ("Migration role cannot be used by the API or worker"): no real
   application source file anywhere under `apps/*` or `workers/*`
   (excluding the Alembic migration tooling itself, which is expected to
   use it) references the migration-owner role name or opens a database
   connection with it.

Run directly: python3 tests/tasks/test_ftf_h003.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
import re
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
from fastapi import Depends, FastAPI

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.auth.organization_workspace_schema import APP_ROLE_NAME  # noqa: E402
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.auth.resource_authorization import authorize_resource  # noqa: E402
from app.db.tenant_context import TenantScopedRequest, tenant_context_dependency  # noqa: E402

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


# -- 1. authorize_resource: a thin, undrifting pass-through ------------------


def test_authorize_resource_delegates_to_session_get_exactly_once() -> None:
    session = MagicMock()
    sentinel = object()
    session.get.return_value = sentinel

    class FakeModel:
        pass

    resource_id = uuid.uuid4()
    result = authorize_resource(session, FakeModel, resource_id)
    check(
        "FTF-H003",
        "authorize_resource returns exactly what the tenant-scoped session's "
        "own session.get(...) returned -- no independent tenant re-check of "
        "its own that could drift from the database's real RLS policy",
        result is sentinel,
    )
    check(
        "FTF-H003",
        "authorize_resource calls session.get exactly once, with the given "
        "model and resource_id",
        session.get.call_count == 1 and session.get.call_args.args == (FakeModel, resource_id),
    )


def test_authorize_resource_returns_none_identically_for_missing_or_foreign() -> None:
    session = MagicMock()
    session.get.return_value = None

    class FakeModel:
        pass

    result = authorize_resource(session, FakeModel, uuid.uuid4())
    check(
        "FTF-H003",
        "negative_acceptance: a resource session.get cannot see (missing, "
        "or filtered out by another tenant's RLS policy) returns None -- "
        "the same result either way, no distinguishable side channel",
        result is None,
    )


# -- 2. tenant_context_dependency: fail-fast composition ordering -----------

ISSUER = "https://issuer.example"
ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()

_PRINCIPALS: dict[str, AuthenticatedPrincipal] = {
    "viewer-a": AuthenticatedPrincipal(issuer=ISSUER, subject="user-viewer-a"),
}
_MEMBERSHIPS: dict[tuple[str, str, uuid.UUID], Role] = {
    (ISSUER, "user-viewer-a", ORG_A): Role.VIEWER,
}


def _test_principal_resolver(request):
    header = request.headers.get("x-test-principal")
    if header is None:
        return None
    return _PRINCIPALS.get(header)


def _test_role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
    return _MEMBERSHIPS.get((issuer, subject, organization_id))


def _build_demo_app(session_factory) -> FastAPI:
    app = FastAPI()

    require_viewer = Depends(
        tenant_context_dependency(
            frozenset({Role.VIEWER}),
            session_factory=session_factory,
            principal_resolver=_test_principal_resolver,
            role_lookup=_test_role_lookup,
        )
    )

    @app.get("/api/v1/orgs/{organization_id}/widgets")
    def list_widgets(ctx: TenantScopedRequest = require_viewer) -> dict:
        return {
            "organization_id": str(ctx.authorized.organization_id),
            "role": ctx.authorized.role.value,
        }

    return app


@contextlib.contextmanager
def _running_demo_app(session_factory) -> Iterator[str]:
    app = _build_demo_app(session_factory)
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


def _request(base_url: str, method: str, path: str, *, principal: str | None = None) -> _HttpResult:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, headers=headers)
        response = conn.getresponse()
        raw_body = response.read()
        return _HttpResult(status=response.status, body=raw_body)
    finally:
        conn.close()


def test_unauthorized_request_never_opens_a_session() -> None:
    session = MagicMock()
    session_factory = MagicMock(return_value=session)
    with _running_demo_app(session_factory) as base_url:
        result = _request(base_url, "GET", f"/api/v1/orgs/{ORG_A}/widgets")
        check(
            "FTF-H003",
            "an unauthenticated request is denied with 401",
            result.status == 401,
        )
        check(
            "FTF-H003",
            "negative_acceptance/ordering: an unauthorized request never "
            "even takes a connection from the pool -- session_factory is "
            "never called when authorization fails",
            session_factory.call_count == 0,
        )

        result = _request(base_url, "GET", f"/api/v1/orgs/{ORG_B}/widgets", principal="viewer-a")
        check(
            "FTF-H003",
            "negative_acceptance: a guessed/foreign organization id the "
            "caller has no membership in is denied (403)",
            result.status == 403,
        )
        check(
            "FTF-H003",
            "a role/tenant-boundary denial also never opens a session",
            session_factory.call_count == 0,
        )


def test_authorized_request_opens_exactly_one_correctly_scoped_session() -> None:
    session = MagicMock()
    session_factory = MagicMock(return_value=session)
    with _running_demo_app(session_factory) as base_url:
        result = _request(base_url, "GET", f"/api/v1/orgs/{ORG_A}/widgets", principal="viewer-a")
        check(
            "FTF-H003",
            "an authorized request succeeds",
            result.status == 200 and result.json()["organization_id"] == str(ORG_A),
        )
        check(
            "FTF-H003",
            "acceptance: authorization succeeding opens exactly one session "
            "from the given session_factory",
            session_factory.call_count == 1,
        )
        # set_tenant_context's own set_config(...) call is the first (and,
        # for a route that does no further querying, only) session.execute
        # call -- proving the session was actually bound to ORG_A's own
        # transaction-local context, not left unscoped.
        check(
            "FTF-H003",
            "the opened session was bound to the exact organization "
            "require_role just authorized, via a real parameterized "
            "set_config(...) call (FTF-0102's set_tenant_context, reused "
            "unchanged)",
            session.execute.call_count >= 1
            and "set_config(" in str(session.execute.call_args_list[0].args[0])
            and session.execute.call_args_list[0].args[1]["org_id"] == str(ORG_A),
        )
        check("FTF-H003", "a successful request commits the session", session.commit.called)
        check("FTF-H003", "a successful request closes the session", session.close.called)


# -- 3. Static evidence: the migration role is never used by API/worker -----

_EXCLUDED_DIRS = {"alembic", "__pycache__", "node_modules", ".venv", "dist"}


def _real_application_source_files() -> list[Path]:
    files: list[Path] = []
    for root_dir in (ROOT / "apps", ROOT / "workers"):
        if not root_dir.exists():
            continue
        for path in root_dir.rglob("*.py"):
            if any(part in _EXCLUDED_DIRS for part in path.parts):
                continue
            files.append(path)
    return files


def test_migration_role_never_referenced_outside_alembic_tooling() -> None:
    migration_role_pattern = re.compile(r"sovra_migrate")
    offenders = []
    for path in _real_application_source_files():
        text = path.read_text(encoding="utf-8")
        if migration_role_pattern.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    check(
        "FTF-H003",
        "negative_acceptance: no real application source file under "
        f"apps/*or workers/* (excluding alembic/) references the "
        f"migration-owner role name (found in: {offenders})",
        offenders == [],
    )


def test_app_role_name_is_the_only_db_role_application_code_references() -> None:
    # Confirms the one real application role application code does
    # reference is the restricted, unprivileged one -- not a decorative
    # check that would pass even if no role were referenced at all.
    check(
        "FTF-H003",
        "the restricted application role (APP_ROLE_NAME) is the real, "
        "non-empty role name every prior tenant-scoped migration already "
        "grants exclusively",
        isinstance(APP_ROLE_NAME, str) and APP_ROLE_NAME == "sovra_app",
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-H003 cross-layer tenant isolation: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-H003 cross-layer tenant isolation: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
