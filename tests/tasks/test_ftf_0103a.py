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


# -- 4. store / CLI / migration logic against a scripted fake session ------
#    (the SQL itself is proven against real Postgres in the integration
#    test; these pin the Python decision logic around it, no database)


class _FakeResult:
    def __init__(self, rows: list[tuple] | None = None, scalar: object = None, rowcount: int = 0):
        self.rows = rows or []
        self._scalar = scalar
        self.rowcount = rowcount

    def first(self) -> tuple | None:
        return self.rows[0] if self.rows else None

    def one(self) -> tuple:
        if len(self.rows) != 1:
            raise RuntimeError("expected exactly one row")
        return self.rows[0]

    def __iter__(self):  # noqa: ANN204
        return iter(self.rows)

    def scalar_one_or_none(self) -> object:
        return self._scalar


class _FakeSession:
    """Records every statement; models only the tenant-context read-back that
    `execute_tenant_scoped` performs, everything else is scripted."""

    def __init__(self, handler=None) -> None:  # noqa: ANN001
        self.handler = handler or (lambda sql, params: _FakeResult())
        self.calls: list[tuple[str, dict]] = []
        self.tenant = ""
        self.committed = self.rolled_back = self.closed = False

    def execute(self, clause: object, params: dict | None = None) -> _FakeResult:
        sql, params = str(clause), dict(params or {})
        self.calls.append((sql, params))
        if "set_config('app.current_organization_id'" in sql:
            self.tenant = params["org_id"]
            return _FakeResult()
        if "current_setting('app.current_organization_id'" in sql:
            return _FakeResult(scalar=self.tenant or None)
        if "set_config('app.current_issuer'" in sql:
            return _FakeResult()
        return self.handler(sql, params)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def _data_calls(session: _FakeSession) -> list[tuple[str, dict]]:
    return [c for c in session.calls if "set_config" not in c[0] and "current_setting" not in c[0]]


def test_lookup_org_role_decision_logic() -> None:
    from app.auth.membership_store import lookup_org_role
    from app.auth.rbac_middleware import Role

    org = uuid.uuid4()
    session = _FakeSession(lambda sql, params: _FakeResult(rows=[("viewer",)]))
    role = lookup_org_role(lambda: session, "https://iss/", "sub-1", org)  # type: ignore[arg-type,return-value]
    check("FTF-0103A", "an active org-level row maps to its Role", role is Role.VIEWER)
    sql, params = _data_calls(session)[0]
    check(
        "FTF-0103A", "role query only counts ORGANIZATION-level rows", "workspace_id IS NULL" in sql
    )
    check("FTF-0103A", "role query only counts ACTIVE rows", "status = 'active'" in sql)
    check(
        "FTF-0103A",
        "role query is bound to the path organization and the validated identity",
        params == {"org": org, "issuer": "https://iss/", "subject": "sub-1"},
    )
    check(
        "FTF-0103A",
        "the tenant context was bound to the path organization",
        session.tenant == str(org),
    )
    check(
        "FTF-0103A",
        "the unit of work was committed and closed",
        session.committed and session.closed,
    )

    none_session = _FakeSession(lambda sql, params: _FakeResult(rows=[]))
    check(
        "FTF-0103A",
        "no matching row -> None (denied), never a default role",
        lookup_org_role(lambda: none_session, "i", "s", org) is None,  # type: ignore[arg-type,return-value]
    )
    odd = _FakeSession(lambda sql, params: _FakeResult(rows=[("superuser",)]))
    check(
        "FTF-0103A",
        "an unknown stored role fails closed (None), never guessed",
        lookup_org_role(lambda: odd, "i", "s", org) is None,  # type: ignore[arg-type,return-value]
    )

    def boom(sql: str, params: dict) -> _FakeResult:
        raise RuntimeError("database unavailable")

    broken = _FakeSession(boom)
    try:
        lookup_org_role(lambda: broken, "i", "s", org)  # type: ignore[arg-type,return-value]
        raised = False
    except RuntimeError:
        raised = True
    check(
        "FTF-0103A", "a database error propagates (never converted to a grant or a denial)", raised
    )
    check(
        "FTF-0103A",
        "the failed unit of work was rolled back and closed",
        broken.rolled_back and broken.closed,
    )


def test_lookup_membership_status_decision_logic() -> None:
    from app.auth.membership_store import lookup_membership_status
    from app.auth.oidc_login import MembershipStatus

    def status_for(rows: list[tuple]) -> tuple[MembershipStatus, _FakeSession]:
        session = _FakeSession(lambda sql, params: _FakeResult(rows=rows))
        return lookup_membership_status(lambda: session, "https://iss/", "sub-1"), session  # type: ignore[arg-type,return-value]

    active, session = status_for([("active",)])
    check("FTF-0103A", "an active membership -> ACTIVE", active is MembershipStatus.ACTIVE)
    check(
        "FTF-0103A",
        "revoked only -> REVOKED",
        status_for([("revoked",)])[0] is MembershipStatus.REVOKED,
    )
    check("FTF-0103A", "no row -> UNKNOWN", status_for([])[0] is MembershipStatus.UNKNOWN)
    check(
        "FTF-0103A",
        "any active membership wins over revoked ones",
        status_for([("revoked",), ("active",)])[0] is MembershipStatus.ACTIVE,
    )
    bind = [c for c in session.calls if "set_config('app.current_issuer'" in c[0]]
    check(
        "FTF-0103A",
        "the identity (not a tenant) is bound transaction-locally",
        len(bind) == 1
        and bind[0][1] == {"issuer": "https://iss/", "subject": "sub-1"}
        and ", true)" in bind[0][0],
    )
    check(
        "FTF-0103A",
        "the lookup committed and closed its unit of work",
        session.committed and session.closed,
    )

    def boom(sql: str, params: dict) -> _FakeResult:
        raise RuntimeError("database unavailable")

    broken = _FakeSession(boom)
    try:
        lookup_membership_status(lambda: broken, "i", "s")  # type: ignore[arg-type,return-value]
        raised = False
    except RuntimeError:
        raised = True
    check("FTF-0103A", "a database error propagates instead of reading as UNKNOWN/ACTIVE", raised)
    check(
        "FTF-0103A",
        "the failed unit of work was rolled back and closed",
        broken.rolled_back and broken.closed,
    )


def test_builders_delegate_to_the_real_lookups() -> None:
    from app.auth.membership_store import build_membership_status_lookup, build_role_lookup
    from app.auth.oidc_login import MembershipStatus
    from app.auth.rbac_middleware import Role

    org = uuid.uuid4()
    session = _FakeSession(lambda sql, params: _FakeResult(rows=[("approver",)]))
    role = build_role_lookup(lambda: session)("i", "s", org)  # type: ignore[arg-type,return-value]
    check("FTF-0103A", "build_role_lookup returns the stored role", role is Role.APPROVER)
    session2 = _FakeSession(lambda sql, params: _FakeResult(rows=[("active",)]))
    status = build_membership_status_lookup(lambda: session2)("i", "s")  # type: ignore[arg-type,return-value]
    check(
        "FTF-0103A",
        "build_membership_status_lookup returns the stored status",
        status is MembershipStatus.ACTIVE,
    )


def test_grant_and_revoke_membership_logic() -> None:
    from app.auth.membership_store import MembershipError, grant_membership, revoke_membership

    org, ws, new_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    def refused(**kwargs: object) -> bool:
        session = _FakeSession()
        try:
            grant_membership(session, **kwargs)  # type: ignore[arg-type]
        except MembershipError:
            return not _data_calls(session)
        return False

    base = {"organization_id": org, "issuer": "i", "subject": "s", "role": "viewer"}
    check(
        "FTF-0103A",
        "a role outside the six is refused before any SQL",
        refused(**{**base, "role": "superuser"}),
    )
    check(
        "FTF-0103A", "an empty issuer is refused before any SQL", refused(**{**base, "issuer": ""})
    )
    check(
        "FTF-0103A",
        "an empty subject is refused before any SQL",
        refused(**{**base, "subject": ""}),
    )
    check(
        "FTF-0103A",
        "a missing issuer AND subject are each refused independently",
        refused(**{**base, "issuer": "", "subject": ""}),
    )

    session = _FakeSession(lambda sql, params: _FakeResult(rows=[(new_id,)]))
    got = grant_membership(session, **base, workspace_id=ws)  # type: ignore[arg-type]
    sql, params = _data_calls(session)[0]
    check("FTF-0103A", "grant returns the membership id", got == new_id)
    check(
        "FTF-0103A",
        "grant binds the organization's tenant context first",
        session.tenant == str(org),
    )
    check(
        "FTF-0103A",
        "grant upserts and re-activates an existing membership",
        "ON CONFLICT" in sql and "status = 'active'" in sql and "role = EXCLUDED.role" in sql,
    )
    check(
        "FTF-0103A",
        "grant binds every identity field",
        params == {"org": org, "ws": ws, "issuer": "i", "subject": "s", "role": "viewer"},
    )

    for rows, expected in ((1, 1), (0, 0)):
        rsession = _FakeSession(lambda sql, params, n=rows: _FakeResult(rowcount=n))
        changed = revoke_membership(rsession, organization_id=org, issuer="i", subject="s")  # type: ignore[arg-type]
        rsql, rparams = _data_calls(rsession)[0]
        check(
            "FTF-0103A", f"revoke returns the changed-row count ({expected})", changed == expected
        )
        check(
            "FTF-0103A",
            "revoke binds the organization's tenant context",
            rsession.tenant == str(org),
        )
        check(
            "FTF-0103A",
            "revoke only touches an ACTIVE row of the exact identity/workspace",
            "status = 'revoked'" in rsql
            and "status = 'active'" in rsql
            and "workspace_id IS NOT DISTINCT FROM :ws" in rsql
            and rparams == {"org": org, "ws": None, "issuer": "i", "subject": "s"},
        )


def _run_cli(argv: list[str], session: _FakeSession | None) -> tuple[int, str, str]:
    import contextlib
    import io

    from app.auth import membership_admin_cli as cli

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv, session_factory=(lambda: session) if session else None)  # type: ignore[arg-type,return-value]
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def test_membership_admin_cli_behaviour() -> None:
    org = str(uuid.uuid4())
    common = ["--organization-id", org, "--issuer", "https://iss/", "--subject", "s"]
    new_id = uuid.uuid4()

    session = _FakeSession(lambda sql, params: _FakeResult(rows=[(new_id,)]))
    code, out, _err = _run_cli(["grant", *common, "--role", "viewer"], session)
    check(
        "FTF-0103A",
        "CLI grant succeeds (exit 0), commits, closes",
        code == 0 and session.committed and session.closed,
    )
    check("FTF-0103A", "CLI grant reports the membership id", str(new_id) in out)
    check("FTF-0103A", "CLI grant used the grant SQL", "ON CONFLICT" in _data_calls(session)[0][0])

    session = _FakeSession(lambda sql, params: _FakeResult(rowcount=1))
    code, out, _err = _run_cli(["revoke", *common], session)
    check("FTF-0103A", "CLI revoke succeeds (exit 0) and commits", code == 0 and session.committed)
    check(
        "FTF-0103A",
        "CLI revoke used the revoke SQL, not the grant SQL",
        "status = 'revoked'" in _data_calls(session)[0][0],
    )
    check("FTF-0103A", "CLI revoke reports the count", "revoked 1 membership" in out)

    session = _FakeSession(lambda sql, params: _FakeResult(rowcount=0))
    code, _out, err = _run_cli(["revoke", *common], session)
    check(
        "FTF-0103A",
        "CLI revoke with nothing to revoke exits 1",
        code == 1 and "nothing revoked" in err,
    )

    session = _FakeSession()
    code, _out, err = _run_cli(["grant", *common, "--role", "superuser"], session)
    check(
        "FTF-0103A",
        "CLI refuses an invalid role (exit 1), rolls back, never commits",
        code == 1 and "refused" in err and session.rolled_back and not session.committed,
    )

    for label, argv in (
        ("grant without --role", ["grant", *common]),
        ("revoke with a stray --role", ["revoke", *common, "--role", "viewer"]),
        (
            "a non-uuid organization",
            [
                "grant",
                "--organization-id",
                "nope",
                "--issuer",
                "i",
                "--subject",
                "s",
                "--role",
                "viewer",
            ],
        ),
        ("no sub-command", []),
    ):
        code, _o, _e = _run_cli(argv, _FakeSession())
        check("FTF-0103A", f"CLI usage error ({label}) exits 2", code == 2)

    import contextlib
    import io

    from app.auth import membership_admin_cli as cli

    saved = os.environ.pop("SOVRA_DATABASE_URL", None)
    err_buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(err_buf):
            code = cli.main(["revoke", *common])
    finally:
        if saved is not None:
            os.environ["SOVRA_DATABASE_URL"] = saved
    check(
        "FTF-0103A",
        "no SOVRA_DATABASE_URL and no injected factory exits 2",
        code == 2 and "SOVRA_DATABASE_URL" in err_buf.getvalue(),
    )

    class _Engine:
        disposed = False

        def dispose(self) -> None:
            _Engine.disposed = True

    fake_session = _FakeSession(lambda sql, params: _FakeResult(rowcount=1))
    saved_ce, saved_sm = cli.create_engine, cli.sessionmaker
    cli.__dict__["create_engine"] = lambda url: _Engine()
    cli.__dict__["sessionmaker"] = lambda bind: (lambda: fake_session)
    os.environ["SOVRA_DATABASE_URL"] = "postgresql+psycopg://migrate@localhost/none"
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["revoke", *common])
    finally:
        cli.__dict__["create_engine"], cli.__dict__["sessionmaker"] = saved_ce, saved_sm
        if saved is None:
            os.environ.pop("SOVRA_DATABASE_URL", None)
        else:
            os.environ["SOVRA_DATABASE_URL"] = saved
    check(
        "FTF-0103A",
        "the CLI builds its own engine from SOVRA_DATABASE_URL and disposes it",
        code == 0 and _Engine.disposed and fake_session.closed,
    )


def test_migration_functions_emit_the_real_schema() -> None:
    import importlib.util

    class _FakeOp:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple, dict]] = []

        def __getattr__(self, name: str):  # noqa: ANN204
            def record(*args: object, **kwargs: object) -> None:
                self.calls.append((name, args, kwargs))

            return record

        def statements(self) -> list[str]:
            return [str(a[0]) for n, a, _k in self.calls if n == "execute"]

    spec = importlib.util.spec_from_file_location("migration_0021_under_test", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    up = _FakeOp()
    module.__dict__["op"] = up
    module.upgrade()
    tables = [a for n, a, _k in up.calls if n == "create_table"]
    check(
        "FTF-0103A",
        "upgrade creates exactly the memberships table",
        len(tables) == 1 and tables[0][0] == "memberships",
    )
    columns = {
        c.name for c in tables[0][1:] if hasattr(c, "name") and c.__class__.__name__ == "Column"
    }
    check(
        "FTF-0103A",
        "memberships has the Section 6.4 columns",
        {"organization_id", "workspace_id", "issuer", "subject", "role", "status", "created_at"}
        <= columns,
    )
    constraints = {
        c.name: str(c.sqltext) for c in tables[0][1:] if c.__class__.__name__ == "CheckConstraint"
    }
    role_check = constraints.get("ck_memberships_role", "")
    check(
        "FTF-0103A",
        "the role CHECK allows exactly the six Section 6.4 roles",
        sorted(re.findall(r"'([a-z_]+)'", role_check)) == sorted(SIX_ROLES),
    )
    check(
        "FTF-0103A",
        "the status CHECK allows exactly active/revoked",
        sorted(re.findall(r"'([a-z_]+)'", constraints.get("ck_memberships_status", "")))
        == ["active", "revoked"],
    )
    statements = " | ".join(up.statements())
    for needle in (
        'GRANT SELECT ON memberships TO "sovra_app"',
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation ON memberships FOR ALL",
        "WITH CHECK (organization_id = NULLIF(current_setting("
        "'app.current_organization_id', true), '')::uuid)",
        "CREATE POLICY identity_self_lookup ON memberships FOR SELECT",
        "issuer = NULLIF(current_setting('app.current_issuer', true), '') AND subject = NULLIF(",
        "NULLS NOT DISTINCT",
    ):
        check("FTF-0103A", f"upgrade executes: {needle[:60]}", needle in statements)
    check(
        "FTF-0103A",
        "upgrade grants the app role nothing beyond SELECT",
        statements.count("GRANT") == 1
        and "INSERT" not in statements
        and "UPDATE" not in statements,
    )
    check(
        "FTF-0103A",
        "upgrade creates the issuer/subject lookup index",
        any(
            n == "create_index" and a[0] == "ix_memberships_issuer_subject" for n, a, _k in up.calls
        ),
    )

    down = _FakeOp()
    module.__dict__["op"] = down
    module.downgrade()
    dstatements = " | ".join(down.statements())
    check(
        "FTF-0103A",
        "downgrade drops both policies, revokes the grant and the unique index",
        "DROP POLICY IF EXISTS identity_self_lookup" in dstatements
        and "DROP POLICY IF EXISTS tenant_isolation" in dstatements
        and 'REVOKE ALL ON memberships FROM "sovra_app"' in dstatements
        and "DROP INDEX IF EXISTS uq_memberships_org_workspace_identity" in dstatements,
    )
    check(
        "FTF-0103A",
        "downgrade drops the table",
        [a for n, a, _k in down.calls if n == "drop_table"] == [("memberships",)],
    )


# -- 5. session resolver: malformed claims + content-free decision events ---


def _mint(payload: dict) -> str:
    from app.auth import oidc_login

    return oidc_login._serializer(SIGNING_KEY, oidc_login._SESSION_SALT).dumps(payload)


def test_session_resolver_malformed_claims_and_decision_events() -> None:
    import logging

    from app.auth import oidc_login
    from app.auth.session_principal import SessionPrincipalResolver

    resolver = SessionPrincipalResolver(
        signing_key=SIGNING_KEY,
        max_age_seconds=900,
        revocations=oidc_login.SessionRevocationStore(),
    )
    good = {"jti": "j1", "iss": "https://iss/", "sub": "s1"}
    check(
        "FTF-0103A",
        "control: a fully-formed signed payload resolves",
        resolver(_request(_mint(good))) is not None,
    )
    for label, payload in (
        ("missing jti", {k: v for k, v in good.items() if k != "jti"}),
        ("missing iss", {k: v for k, v in good.items() if k != "iss"}),
        ("missing sub", {k: v for k, v in good.items() if k != "sub"}),
        ("empty iss", {**good, "iss": ""}),
        ("empty sub", {**good, "sub": ""}),
        ("non-string jti", {**good, "jti": 7}),
        ("non-string iss", {**good, "iss": 7}),
        ("non-string sub", {**good, "sub": 7}),
    ):
        check(
            "FTF-0103A",
            f"a validly-signed but malformed session ({label}) -> None",
            resolver(_request(_mint(payload))) is None,
        )

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("sovra.auth.decision")
    handler, old_level = _Capture(), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        resolver(_request(None, {"x-request-id": "req-123"}))
        resolver(_request("garbage"))
        resolver(_request(_mint({"jti": "j"})))
        revoked_store = oidc_login.SessionRevocationStore()
        revoked_store.revoke("j1")
        SessionPrincipalResolver(
            signing_key=SIGNING_KEY, max_age_seconds=900, revocations=revoked_store
        )(_request(_mint(good)))
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    reasons = [getattr(r, "reason", None) for r in records]
    check(
        "FTF-0103A",
        f"each denial emits a decision event with its reason code (got {reasons})",
        reasons
        == ["no_session", "invalid_or_expired_session", "malformed_session", "revoked_session"],
    )
    check(
        "FTF-0103A",
        "decision events carry outcome=denied and the request id",
        all(getattr(r, "outcome", None) == "denied" for r in records)
        and getattr(records[0], "request_id", None) == "req-123",
    )
    dump = " ".join(str(r.__dict__) for r in records)
    check(
        "FTF-0103A",
        "decision events are content-free (no subject, issuer or cookie)",
        "s1" not in dump and "https://iss/" not in dump and "garbage" not in dump,
    )


# -- 6. main.create_app: production flag + shared logout revocation ----------


def test_create_app_honours_the_production_flag() -> None:
    from app.auth.oidc_settings import OidcConfigurationError

    http_env = {
        **_full_app_env(),
        "SOVRA_OIDC_ISSUER": "http://idp.internal.example",
        "SOVRA_OIDC_REDIRECT_URI": "http://factory.internal.example/auth/callback",
        "SOVRA_OIDC_AUTHORIZATION_ENDPOINT": "http://idp.internal.example/authorize",
        "SOVRA_OIDC_TOKEN_ENDPOINT": "http://idp.internal.example/token",
        "SOVRA_OIDC_JWKS_URI": "http://idp.internal.example/jwks",
        "SOVRA_OIDC_ALLOWED_ORIGIN": "http://factory.internal.example",
    }

    def build(environment: str) -> BaseException | None:
        env = {**http_env, "SOVRA_ENVIRONMENT": environment}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            import app.main as main_module

            main_module.create_app()
            return None
        except BaseException as exc:  # noqa: BLE001
            return exc
        finally:
            for key, old in saved.items():
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old

    check("FTF-0103A", "staging accepts plain-http OIDC endpoints", build("staging") is None)
    check(
        "FTF-0103A",
        "production rejects plain-http OIDC endpoints",
        isinstance(build("production"), OidcConfigurationError),
    )


def test_logout_revokes_the_session_for_the_api_resolver() -> None:
    """FTF-0101's login router and FTF-0103A's resolver share ONE revocation
    store: login -> API 200 -> logout -> the same cookie is 401 on the API."""
    import contextlib
    import threading

    import uvicorn
    from fastapi import Depends

    sys.path.insert(0, str(ROOT / "tests" / "tasks"))
    import test_ftf_0101 as t101  # noqa: E402  (FTF-0101's fake-OP harness)

    from app.auth.oidc_login import (
        MembershipStatus,
        SessionRevocationStore,
        build_oidc_login_router,
        read_session_cookie,
    )
    from app.auth.oidc_settings import OidcSettings
    from app.auth.rbac_middleware import AuthorizedContext, Role, require_role
    from app.auth.session_principal import SessionPrincipalResolver

    with t101._run_fake_provider() as (provider_base, state):
        provider_port = int(provider_base.rsplit(":", 1)[1])
        api_port = t101.free_port()
        settings = OidcSettings.from_env(
            t101._base_settings_env(provider_port, api_port), production=False
        )
        revocations = SessionRevocationStore()
        app = FastAPI()
        app.include_router(
            build_oidc_login_router(
                settings,
                client_secret=t101.CLIENT_SECRET,
                signing_key=t101.SIGNING_KEY,
                membership_lookup=lambda iss, sub: MembershipStatus.ACTIVE,
                revocations=revocations,
            )
        )
        guard = require_role(
            frozenset(Role),
            principal_resolver=SessionPrincipalResolver(
                signing_key=t101.SIGNING_KEY,
                max_age_seconds=settings.session_ttl_seconds,
                revocations=revocations,
            ),
            role_lookup=lambda issuer, subject, org: Role.VIEWER,
        )

        guard_dependency = Depends(guard)

        @app.get("/api/v1/organizations/{organization_id}/ping")
        def ping(organization_id: str, ctx: AuthorizedContext = guard_dependency) -> dict[str, str]:
            return {"role": ctx.role.value}

        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.02)
        api = f"http://127.0.0.1:{api_port}"
        try:
            cookie = t101._establish_session(api, settings, state)
            path = f"/api/v1/organizations/{uuid.uuid4()}/ping"
            before = t101._request(api, "GET", path, headers={"Cookie": cookie})
            check(
                "FTF-0103A", f"live session -> API 200 (got {before.status})", before.status == 200
            )
            none = t101._request(api, "GET", path)
            check("FTF-0103A", f"no session -> API 401 (got {none.status})", none.status == 401)
            data = read_session_cookie(
                t101.SIGNING_KEY, cookie.split("=", 1)[1], max_age=settings.session_ttl_seconds
            )
            assert data is not None
            logout = t101._request(
                api,
                "POST",
                "/auth/logout",
                headers={
                    "Cookie": cookie,
                    "Origin": "http://localhost:3000",
                    "X-CSRF-Token": data["csrf"],
                },
                body=b"",
            )
            check("FTF-0103A", f"logout -> 200 (got {logout.status})", logout.status == 200)
            after = t101._request(api, "GET", path, headers={"Cookie": cookie})
            check(
                "FTF-0103A",
                f"logged-out session -> API 401 (got {after.status})",
                after.status == 401,
            )
            check(
                "FTF-0103A",
                "the shared store records the revoked jti",
                revocations.is_revoked(data["jti"]),
            )
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            with contextlib.suppress(Exception):
                pass


# -- 7. require_role: the organization comes from the path only -----------
def test_require_role_ignores_header_and_query_org_overrides() -> None:
    import asyncio
    from urllib.parse import urlencode

    from fastapi import HTTPException

    from app.auth.rbac_middleware import (
        AuthenticatedPrincipal,
        Role,
        require_role,
    )

    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    lookups: list[uuid.UUID] = []

    def viewer_of_a_only(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        lookups.append(organization_id)
        return Role.VIEWER if organization_id == org_a else None

    def permissive(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        lookups.append(organization_id)
        return Role.VIEWER

    def call(
        lookup,
        path_org: uuid.UUID,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, object]:
        guard = require_role(
            {Role.VIEWER},
            principal_resolver=lambda request: AuthenticatedPrincipal("iss", "sub"),
            role_lookup=lookup,
        )
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": f"/orgs/{path_org}/projects",
                "path_params": {"organization_id": str(path_org)},
                "query_string": urlencode(params or {}).encode(),
                "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            }
        )
        try:
            ctx = asyncio.run(guard(request))
        except HTTPException as exc:
            return exc.status_code, exc.detail
        return 200, ctx

    code, ctx = call(viewer_of_a_only, org_a)
    check("FTF-0103A", f"unit control: viewer of A reads A -> 200 (got {code})", code == 200)
    missing = call(viewer_of_a_only, uuid.uuid4())
    check("FTF-0103A", f"unit control: an unknown org -> 403 (got {missing[0]})", missing[0] == 403)

    attempts: list[tuple[str, dict[str, str], dict[str, str]]] = [
        (f"header {name}", {name: str(org_a)}, {})
        for name in (
            "X-Organization-Id",
            "X-Sovra-Organization-Id",
            "X-Tenant-Id",
            "X-Organization",
            "Organization-Id",
        )
    ]
    attempts.append(("query organization_id", {}, {"organization_id": str(org_a)}))
    attempts.append(("query tenant_id", {}, {"tenant_id": str(org_a)}))
    for label, headers, params in attempts:
        lookups.clear()
        code, detail = call(viewer_of_a_only, org_b, headers, params)
        check(
            "FTF-0103A",
            f"unit: {label} naming A cannot override path org B -> 403 (got {code})",
            code == 403,
        )
        check(
            "FTF-0103A",
            f"unit: {label} override gets the same detail as the missing-org case",
            detail == missing[1],
        )
        check(
            "FTF-0103A",
            f"unit: {label} -- the role lookup is only ever asked about the path org B",
            bool(lookups) and all(entry == org_b for entry in lookups),
        )

    # Negative test on the real default code with the attack input present: with every
    # override naming A, authorization is still made against the path org B only.
    lookups.clear()
    code, ctx = call(
        permissive,
        org_b,
        {"X-Organization-Id": str(org_a), "X-Tenant-Id": str(org_a)},
        {"organization_id": str(org_a)},
    )
    check(
        "FTF-0103A",
        "unit: with every override naming A, the context is still the path org B",
        lookups == [org_b] and getattr(ctx, "organization_id", None) == org_b,
    )


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
