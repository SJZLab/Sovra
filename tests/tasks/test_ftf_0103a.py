#!/usr/bin/env python3
"""FTF-0103A -- live session principal + Membership persistence (no-DB tests).

Runnable without Postgres or network egress. The real live-database /
real-OIDC-provider proof (login -> API 200; 401/403 matrix; RLS and grant
negative tests) is `tests/integration/test_ftf_0103a_live_auth_path.py`,
run in CI's dedicated `live-auth-path-integration` job. This suite proves,
with no database:

1. The forward migration `apps/api/alembic/versions/0021_memberships.py`
   chains onto the serialized head, creates `memberships` with the six
   Section 6.4 roles CHECK-constrained, the (organization_id, workspace_id)
   composite FK, NULL-safe uniqueness, ENABLE+FORCE row-level security, a
   non-allow-all policy, and grants the app role SELECT only.
2. `app.main.create_app()` passes the REAL principal resolver and the REAL
   DB-backed role lookup to EVERY /api/v1 route: no route keeps
   `default_principal_resolver`/`default_role_lookup`, and no /api/v1 route
   exists without a `require_role` guard at all. The enumeration helper is
   itself shown able to fail against a deliberately default-wired app.
3. `SessionPrincipalResolver` fails closed on every missing, garbage,
   foreign-key, expired or revoked session cookie, and ignores any
   body/header/query claim of identity or role.

Run directly: python3 tests/tasks/test_ftf_0103a.py
"""

from __future__ import annotations

import importlib
import inspect
import os
import re
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from starlette.requests import Request  # noqa: E402

FAILURES: list[str] = []

MIGRATION = API_ROOT / "alembic" / "versions" / "0021_memberships.py"
SIGNING_KEY = b"ftf-0103a-unit-test-signing-key"
SIX_ROLES = (
    "organization_admin",
    "workspace_admin",
    "ml_engineer",
    "evaluator",
    "approver",
    "viewer",
)


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- 1. migration shape ------------------------------------------------


def test_migration_shape() -> None:
    check("FTF-0103A", "migration 0021_memberships.py exists", MIGRATION.is_file())
    if not MIGRATION.is_file():
        return
    text = MIGRATION.read_text()
    check(
        "FTF-0103A",
        "chains onto the serialized head 0020_org_gateway_policies",
        'down_revision = "0020_org_gateway_policies"' in text,
    )
    check("FTF-0103A", "revision id fits alembic_version (<=32 chars)", _revision_ok(text))
    check("FTF-0103A", "creates the memberships table", '"memberships"' in text)
    for role in SIX_ROLES:
        check("FTF-0103A", f"role CHECK names Section 6.4 role {role}", f"'{role}'" in text)
    check(
        "FTF-0103A",
        "role CHECK allows exactly six roles",
        len(re.findall(rf"'(?:{'|'.join(SIX_ROLES)})'", text)) == 6,
    )
    check(
        "FTF-0103A",
        "status is CHECK-constrained to active/revoked",
        "'active'" in text and "'revoked'" in text,
    )
    check(
        "FTF-0103A",
        "workspace_id is nullable with a composite FK to a workspace of the same org",
        '"workspaces.organization_id"' in text and '"workspaces.id"' in text,
    )
    check(
        "FTF-0103A",
        "uniqueness covers the NULL-workspace case (NULLS NOT DISTINCT)",
        "NULLS NOT DISTINCT" in text,
    )
    check("FTF-0103A", "ENABLE ROW LEVEL SECURITY", "ENABLE ROW LEVEL SECURITY" in text)
    check("FTF-0103A", "FORCE ROW LEVEL SECURITY", "FORCE ROW LEVEL SECURITY" in text)
    check("FTF-0103A", "declares a tenant_isolation policy", "tenant_isolation" in text)
    check(
        "FTF-0103A",
        "no allow-all policy literal",
        not re.search(r"USING\s*\(\s*true\s*\)", text, re.IGNORECASE),
    )
    grants = re.findall(r"GRANT\s+([A-Z, ]+?)\s+ON\s+memberships", text)
    check("FTF-0103A", "app role is granted exactly SELECT", grants == ["SELECT"])
    check(
        "FTF-0103A",
        "app role is never granted INSERT/UPDATE/DELETE",
        not re.search(r"GRANT[^\n]*(INSERT|UPDATE|DELETE)[^\n]*memberships", text),
    )


def _revision_ok(text: str) -> bool:
    match = re.search(r'^revision = "([^"]+)"', text, re.MULTILINE)
    return match is not None and len(match.group(1)) <= 32


# -- 2. every /api/v1 route carries the real resolver and role lookup ---


def _full_app_env() -> dict[str, str]:
    return {
        "SOVRA_ENVIRONMENT": "staging",
        "SOVRA_DATABASE_URL": "postgresql+psycopg://sovra_app:s3cr3t@db.internal:5432/sovra",
        "SOVRA_LITELLM_GATEWAY_URL": "https://litellm.internal.example",
        "SOVRA_PRIVACY_POLICY": "standard",
        "SOVRA_TLS_VERIFY": "true",
        "SOVRA_MAX_TRAINING_BUDGET_USD": "500.00",
        "SOVRA_LITELLM_API_KEY_SECRET_REF": "vault://sovra/litellm-api-key",
        "SOVRA_DATABASE_PASSWORD_SECRET_REF": "openbao://sovra/db-password",
        "SOVRA_OIDC_ISSUER": "https://idp.internal.example",
        "SOVRA_OIDC_CLIENT_ID": "sovra-factory",
        "SOVRA_OIDC_CLIENT_SECRET_REF": "vault://sovra/oidc-client-secret",
        "SOVRA_OIDC_REDIRECT_URI": "https://factory.internal.example/auth/callback",
        "SOVRA_OIDC_AUTHORIZATION_ENDPOINT": "https://idp.internal.example/authorize",
        "SOVRA_OIDC_TOKEN_ENDPOINT": "https://idp.internal.example/token",
        "SOVRA_OIDC_JWKS_URI": "https://idp.internal.example/jwks",
        "SOVRA_SESSION_SIGNING_KEY_SECRET_REF": "vault://sovra/session-signing-key",
        "SOVRA_OIDC_ALLOWED_ORIGIN": "https://factory.internal.example",
        "SOVRA_OIDC_CLIENT_SECRET_VALUE": "not-a-real-secret",
        "SOVRA_SESSION_SIGNING_KEY_VALUE": "not-a-real-signing-key",
    }


def _build_real_app() -> FastAPI:
    added = set(_full_app_env()) - set(os.environ)
    old = {k: os.environ[k] for k in _full_app_env() if k in os.environ}
    os.environ.update(_full_app_env())
    try:
        import app.main as main_module

        return main_module.create_app()
    finally:
        for key in added:
            os.environ.pop(key, None)
        os.environ.update(old)


def _walk_dependants(dependant: object) -> list[object]:
    found = [dependant]
    for sub in getattr(dependant, "dependencies", []):
        found.extend(_walk_dependants(sub))
    return found


def _flatten_routes(
    routes: list[object], context: object | None = None
) -> list[tuple[str, object]]:
    """(effective path, APIRoute) for every route, descending through
    FastAPI's lazily-included routers (`_IncludedRouter`) so a router mounted
    by `include_router` is enumerated under its real, prefixed path."""
    out: list[tuple[str, object]] = []
    for route in routes:
        if type(route).__name__ == "_IncludedRouter":
            child = route.include_context  # type: ignore[attr-defined]
            if context is not None:
                child = context.combine(child)  # type: ignore[attr-defined]
            out.extend(_flatten_routes(route.original_router.routes, child))  # type: ignore[attr-defined]
        elif isinstance(route, APIRoute):
            path = context.path_for(route) if context is not None else route.path  # type: ignore[attr-defined]
            out.append((path, route))
    return out


def audit_api_routes(app: FastAPI) -> tuple[int, list[str]]:
    """Returns (number of /api/v1 routes inspected, violations). A route is a
    violation when it has no `require_role` guard at all, or when its guard
    still closes over the default fail-closed resolver / role lookup."""
    from app.auth.rbac_middleware import default_principal_resolver, default_role_lookup

    inspected = 0
    violations: list[str] = []
    for path, route in _flatten_routes(list(app.routes)):
        if not path.startswith("/api/v1"):
            continue
        inspected += 1
        guards = 0
        for dependant in _walk_dependants(route.dependant):  # type: ignore[attr-defined]
            call = getattr(dependant, "call", None)
            if call is None or not inspect.isfunction(call):
                continue
            nonlocals = inspect.getclosurevars(call).nonlocals
            if "principal_resolver" not in nonlocals or "role_lookup" not in nonlocals:
                continue
            guards += 1
            if nonlocals["principal_resolver"] is default_principal_resolver:
                violations.append(f"{path}: default principal_resolver")
            if nonlocals["role_lookup"] is default_role_lookup:
                violations.append(f"{path}: default role_lookup")
        if guards == 0:
            violations.append(f"{path}: no require_role guard")
    return inspected, violations


def test_every_api_route_has_the_real_resolver_and_role_lookup() -> None:
    app = _build_real_app()
    inspected, violations = audit_api_routes(app)
    check("FTF-0103A", f"enumerated a real route set (got {inspected})", inspected >= 40)
    check(
        "FTF-0103A",
        f"no /api/v1 route keeps a default resolver or role lookup: {violations[:5]}",
        not violations,
    )


def test_route_audit_is_able_to_fail() -> None:
    """Law 1: the audit above must redden against a default-wired router."""
    from app.projects.project_routes import build_project_router

    bad = FastAPI()
    bad.include_router(build_project_router(lambda: None))  # type: ignore[arg-type,return-value]
    inspected, violations = audit_api_routes(bad)
    check("FTF-0103A", "default-wired router is enumerated", inspected > 0)
    check(
        "FTF-0103A",
        "audit reports BOTH default resolver and default role lookup",
        any("default principal_resolver" in v for v in violations)
        and any("default role_lookup" in v for v in violations),
    )
    unguarded = FastAPI()

    @unguarded.get("/api/v1/open")
    def open_route() -> dict[str, str]:
        return {}

    _, violations = audit_api_routes(unguarded)
    check(
        "FTF-0103A",
        "audit reports a /api/v1 route with no require_role guard",
        violations == ["/api/v1/open: no require_role guard"],
    )


def test_login_membership_lookup_is_the_real_store() -> None:
    """The wired /auth router's membership_lookup must not be the default
    fail-closed UNKNOWN lookup (a revoked membership is denied at login only
    when the real store backs it)."""
    from app.auth.oidc_login import default_membership_lookup

    app = _build_real_app()
    lookups = []
    for path, route in _flatten_routes(list(app.routes)):
        if path == "/auth/callback":
            lookups.append(
                inspect.getclosurevars(route.endpoint).nonlocals.get("membership_lookup")  # type: ignore[attr-defined]
            )
    check("FTF-0103A", "/auth/callback is mounted", len(lookups) == 1)
    check(
        "FTF-0103A",
        "/auth/callback's membership_lookup is not the default",
        len(lookups) == 1
        and lookups[0] is not None
        and lookups[0] is not default_membership_lookup,
    )


# -- 3. session principal resolver ------------------------------------------


def _request(cookie: str | None = None, headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if cookie is not None:
        raw.append((b"cookie", f"sovra_session={cookie}".encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


def test_session_principal_resolver_fails_closed() -> None:
    try:
        session_principal = importlib.import_module("app.auth.session_principal")
        oidc_login = importlib.import_module("app.auth.oidc_login")
    except ImportError as exc:
        check("FTF-0103A", f"app.auth.session_principal is importable ({exc})", False)
        return

    revocations = oidc_login.SessionRevocationStore()
    resolver = session_principal.SessionPrincipalResolver(
        signing_key=SIGNING_KEY, max_age_seconds=900, revocations=revocations
    )
    token, _csrf, jti = oidc_login._issue_session_cookie(
        SIGNING_KEY, subject="diana", issuer="https://idp.example/", email="d@example.com"
    )
    principal = resolver(_request(token))
    check(
        "FTF-0103A",
        "a valid session resolves to the cookie's issuer+subject",
        principal is not None
        and principal.issuer == "https://idp.example/"
        and principal.subject == "diana",
    )
    check("FTF-0103A", "no cookie -> None", resolver(_request(None)) is None)
    check("FTF-0103A", "empty cookie -> None", resolver(_request("")) is None)
    check("FTF-0103A", "garbage cookie -> None", resolver(_request("not-a-session")) is None)
    check(
        "FTF-0103A",
        "a cookie with a tampered payload -> None",
        resolver(_request(token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB"))) is None,
    )
    foreign, _c, _j = oidc_login._issue_session_cookie(
        b"some-other-signing-key", subject="diana", issuer="https://idp.example/", email=None
    )
    check(
        "FTF-0103A",
        "a cookie signed with a foreign key -> None",
        resolver(_request(foreign)) is None,
    )

    short = session_principal.SessionPrincipalResolver(
        signing_key=SIGNING_KEY, max_age_seconds=1, revocations=revocations
    )
    time.sleep(2.1)
    check("FTF-0103A", "an expired cookie -> None", short(_request(token)) is None)

    spoof = resolver(
        _request(
            None,
            {
                "x-forwarded-user": "diana",
                "x-sovra-subject": "diana",
                "x-sovra-role": "organization_admin",
                "authorization": "Bearer diana",
            },
        )
    )
    check("FTF-0103A", "identity/role headers without a session never authenticate", spoof is None)

    revocations.revoke(jti)
    check("FTF-0103A", "a revoked (logged-out) session -> None", resolver(_request(token)) is None)

    other, _c2, _j2 = oidc_login._issue_session_cookie(
        SIGNING_KEY, subject="eve", issuer="https://idp.example/", email=None
    )
    check(
        "FTF-0103A",
        "revoking one session does not revoke another",
        resolver(_request(other)) is not None,
    )
    check(
        "FTF-0103A",
        "a random uuid is never a principal",
        resolver(_request(str(uuid.uuid4()))) is None,
    )


def test_store_and_cli_modules_exist() -> None:
    for module in ("app.auth.membership_store", "app.auth.membership_admin_cli"):
        try:
            importlib.import_module(module)
            ok = True
        except ImportError:
            ok = False
        check("FTF-0103A", f"{module} is importable", ok)


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0103A live session principal tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0103A live session principal tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
