#!/usr/bin/env python3
"""FTF-0103A -- live auth path proof: real OIDC login -> real API call.

Three real components, no test doubles anywhere in the request path:

  1. a real `idpyoidc` OpenID Provider (reused from FTF-0101's integration
     test) that signs real ID tokens;
  2. the REAL `app.main.create_app()` served by uvicorn, reading the session
     cookie and the `memberships` table through the restricted `sovra_app`
     role (never the migration owner);
  3. a real Postgres 16 with the real migration chain applied.

Memberships are written only through the operator CLI
(`app.auth.membership_admin_cli`, migrate role) -- the app has no write path.

Requires `SOVRA_DATABASE_URL` (migration owner) and `SOVRA_APP_DB_PASSWORD`;
otherwise exits 1 with BLOCKED_EXTERNAL (never skipped).

Run directly:
    SOVRA_DATABASE_URL=postgresql+psycopg://<owner>@host/db \\
    SOVRA_APP_DB_PASSWORD=<pw> python3 tests/integration/test_ftf_0103a_live_auth_path.py
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import test_oidc_login_real_provider as oidc_it  # noqa: E402  (FTF-0101's real-OP harness)
import uvicorn  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.oidc_login import read_session_cookie  # noqa: E402
from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)

FAILURES: list[str] = []
# The real OP's fixed (test-only) authenticated user; its ID-token `sub` is the
# OP's own opaque identifier for that user, discovered at run time (below).
SUBJECT = ""


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- FTF-0103A's real live-auth-path "
            "evidence needs a live Postgres. Not silently skipped.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def _app_role_url(migration_url: str, app_password: str) -> str:
    parts = urlsplit(migration_url)
    netloc = f"{APP_ROLE_NAME}:{app_password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _alembic(migration_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = migration_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _cli(migration_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = migration_url
    env["PYTHONPATH"] = str(API_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "app.auth.membership_admin_cli", *args],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


@contextlib.contextmanager
def _serve_real_app(
    *, issuer: str, api_port: int, app_url: str, ttl_seconds: int = 900
) -> Iterator[str]:
    """The REAL `create_app()`, configured through the environment exactly as
    production configures it, served over a real socket."""
    env = oidc_it._base_env(issuer=issuer, api_port=api_port)
    env.update(
        {
            "SOVRA_ENVIRONMENT": "staging",
            "SOVRA_DATABASE_URL": app_url,
            "SOVRA_LITELLM_GATEWAY_URL": "https://litellm.internal.example",
            "SOVRA_PRIVACY_POLICY": "standard",
            "SOVRA_TLS_VERIFY": "true",
            "SOVRA_MAX_TRAINING_BUDGET_USD": "500.00",
            "SOVRA_LITELLM_API_KEY_SECRET_REF": "vault://sovra/litellm-api-key",
            "SOVRA_DATABASE_PASSWORD_SECRET_REF": "openbao://sovra/db-password",
            "SOVRA_OIDC_CLIENT_SECRET_VALUE": oidc_it.CLIENT_SECRET,
            "SOVRA_SESSION_SIGNING_KEY_VALUE": oidc_it.SIGNING_KEY.decode(),
            "SOVRA_SESSION_TTL_SECONDS": str(ttl_seconds),
        }
    )
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        import app.main as main_module

        app = main_module.create_app()
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
    config = uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{api_port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _login(api_base: str, issuer: str) -> tuple[int, str | None, dict]:
    """A real browser-shaped login against the real OP. Returns
    (callback status, session cookie `name=value` or None, callback json)."""
    login = oidc_it._request(api_base, "GET", "/auth/login")
    op_path = login.location()[len(issuer.rstrip("/")) :]
    op_resp = oidc_it._request(issuer.rstrip("/"), "GET", op_path)
    callback_query = urllib.parse.urlparse(op_resp.location()).query
    callback = oidc_it._request(
        api_base,
        "GET",
        f"/auth/callback?{callback_query}",
        headers={"Cookie": oidc_it._cookie_header(*login.set_cookies())},
    )
    cookie = next((c for c in callback.set_cookies() if c.startswith("sovra_session=")), None)
    body: dict = {}
    if callback.status != 302:
        with contextlib.suppress(ValueError):
            body = callback.json()
    return callback.status, (cookie.split(";", 1)[0] if cookie else None), body


def _discover_subject(issuer: str, api_port: int) -> str:
    """The real OP's own `sub` for its one test user, read from a real login
    against a throwaway lenient login router (membership never consulted)."""
    redirect = f"http://127.0.0.1:{api_port}/auth/callback"
    env = oidc_it._base_env(issuer=issuer, api_port=api_port)
    settings = oidc_it.OidcSettings.from_env(env, production=False)
    del redirect
    with oidc_it._run_api_app(settings) as lenient:
        _status, cookie, _body = _login(lenient, issuer)
        assert cookie is not None, "setup: a lenient login must succeed"
        _code, session = _api(lenient, "GET", "/auth/session", cookie=cookie)
    assert isinstance(session, dict) and session.get("sub")
    return str(session["sub"])


def _api(
    api_base: str,
    method: str,
    path: str,
    *,
    cookie: str | None = None,
    headers: dict[str, str] | None = None,
    body: dict | None = None,
) -> tuple[int, object]:
    hdrs = dict(headers or {})
    if cookie:
        hdrs["Cookie"] = cookie
    raw = None
    if body is not None:
        raw = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    resp = oidc_it._request(api_base, method, path, headers=hdrs, body=raw)
    try:
        return resp.status, json.loads(resp.body)
    except ValueError:
        return resp.status, resp.body


def _count(owner_engine, org_id: uuid.UUID, **where: str) -> int:
    clauses = "".join(f" AND {k} = :{k}" for k in where)
    with owner_engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_organization_id', :o, true)"),
            {"o": str(org_id)},
        )
        return conn.execute(
            text(f"SELECT count(*) FROM memberships WHERE organization_id = :o{clauses}"),
            {"o": org_id, **where},
        ).scalar_one()


def _raw_app_query(app_engine, sql: str, *, settings: dict[str, str] | None = None, params=None):
    """Run `sql` as the restricted sovra_app role with only the given
    transaction-local settings bound (no settings = no tenant context)."""
    with app_engine.connect() as conn:
        for name, value in (settings or {}).items():
            conn.execute(text("SELECT set_config(:n, :v, true)"), {"n": name, "v": value})
        try:
            return conn.execute(text(sql), params or {}).fetchall()
        finally:
            conn.rollback()


def _expect_db_error(engine_or_conn_fn, fragment: str) -> bool:
    try:
        engine_or_conn_fn()
    except DBAPIError as exc:
        return fragment.lower() in str(exc.orig).lower()
    return False


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")
    app_url = _app_role_url(migration_url, app_password)

    # -- migration: upgrade / downgrade / upgrade round trip ---------------
    up = _alembic(migration_url, "upgrade", "head")
    check(
        "FTF-0103A",
        f"alembic upgrade head ({up.returncode}): {up.stderr[-300:]}",
        up.returncode == 0,
    )
    owner = create_engine(migration_url)
    with owner.connect() as conn:
        has_table = conn.execute(text("SELECT to_regclass('public.memberships')")).scalar_one()
    check("FTF-0103A", "memberships exists after upgrade", has_table is not None)
    down = _alembic(migration_url, "downgrade", "0020_org_gateway_policies")
    with owner.connect() as conn:
        gone = conn.execute(text("SELECT to_regclass('public.memberships')")).scalar_one()
    check(
        "FTF-0103A",
        f"downgrade removes memberships ({down.returncode}): {down.stderr[-300:]}",
        down.returncode == 0 and gone is None,
    )
    up2 = _alembic(migration_url, "upgrade", "head")
    check(
        "FTF-0103A", f"re-upgrade head ({up2.returncode}): {up2.stderr[-300:]}", up2.returncode == 0
    )
    if FAILURES:
        _finish()

    # -- seed two real tenants (migration role, seeding only) --------------
    with Session(owner) as session:
        org_a = Organization(name="Acme 0103A", slug=f"acme-0103a-{uuid.uuid4().hex[:8]}")
        org_b = Organization(name="Globex 0103A", slug=f"globex-0103a-{uuid.uuid4().hex[:8]}")
        session.add_all([org_a, org_b])
        session.flush()
        ws_a = Workspace(organization_id=org_a.id, name="A Main", slug="main")
        ws_b = Workspace(organization_id=org_b.id, name="B Main", slug="main")
        session.add_all([ws_a, ws_b])
        session.commit()
        A, B, WS_A, WS_B = org_a.id, org_b.id, ws_a.id, ws_b.id

    api_port = oidc_it.free_port()
    redirect_uri = f"http://127.0.0.1:{api_port}/auth/callback"
    with oidc_it._run_real_op(redirect_uri=redirect_uri) as (issuer, _op):
        global SUBJECT
        SUBJECT = _discover_subject(issuer, api_port)
        with _serve_real_app(issuer=issuer, api_port=api_port, app_url=app_url) as api:
            grant = lambda org, role, ws=None: _cli(  # noqa: E731
                migration_url,
                "grant",
                "--organization-id",
                str(org),
                "--issuer",
                issuer,
                "--subject",
                SUBJECT,
                "--role",
                role,
                *(["--workspace-id", str(ws)] if ws else []),
            )
            revoke = lambda org, ws=None: _cli(  # noqa: E731
                migration_url,
                "revoke",
                "--organization-id",
                str(org),
                "--issuer",
                issuer,
                "--subject",
                SUBJECT,
                *(["--workspace-id", str(ws)] if ws else []),
            )
            projects_a = f"/api/v1/organizations/{A}/projects"
            projects_b = f"/api/v1/organizations/{B}/projects"

            # No membership at all: the real login is denied, never a session.
            status, cookie, body = _login(api, issuer)
            check(
                "FTF-0103A",
                f"login with no membership is denied (403 unknown): {status} {body}",
                status == 403 and cookie is None and body.get("status") == "unknown",
            )

            # Active viewer in org A -> real login -> real API calls.
            check("FTF-0103A", "CLI grant viewer in A", grant(A, "viewer").returncode == 0)
            status, cookie, body = _login(api, issuer)
            check(
                "FTF-0103A",
                f"login for an active member succeeds: {status} {body}",
                status == 302 and cookie is not None,
            )
            assert cookie is not None, (status, body, FAILURES)
            code, _ = _api(api, "GET", projects_a, cookie=cookie)
            check("FTF-0103A", f"active viewer GET own org -> 200 (got {code})", code == 200)

            # 401 matrix.
            code, _ = _api(api, "GET", projects_a)
            check("FTF-0103A", f"no cookie -> 401 (got {code})", code == 401)
            name, value = cookie.split("=", 1)
            tampered = f"{name}={value[:-4]}{'AAAA' if not value.endswith('AAAA') else 'BBBB'}"
            code, _ = _api(api, "GET", projects_a, cookie=tampered)
            check("FTF-0103A", f"tampered cookie -> 401 (got {code})", code == 401)
            code, _ = _api(api, "GET", projects_a, cookie=f"{name}=garbage")
            check("FTF-0103A", f"garbage cookie -> 401 (got {code})", code == 401)
            code, _ = _api(
                api,
                "GET",
                projects_a,
                headers={
                    "X-Sovra-Subject": SUBJECT,
                    "X-Forwarded-User": SUBJECT,
                    "Authorization": f"Bearer {SUBJECT}",
                },
            )
            check(
                "FTF-0103A", f"identity headers without a session -> 401 (got {code})", code == 401
            )

            # 403 matrix: foreign org behaves like a missing org.
            foreign_code, foreign_body = _api(api, "GET", projects_b, cookie=cookie)
            missing_code, missing_body = _api(
                api, "GET", f"/api/v1/organizations/{uuid.uuid4()}/projects", cookie=cookie
            )
            check(
                "FTF-0103A",
                f"member of A only, org B -> 403 (got {foreign_code})",
                foreign_code == 403,
            )
            check(
                "FTF-0103A",
                "a foreign organization is indistinguishable from a missing one",
                (foreign_code, foreign_body) == (missing_code, missing_body),
            )
            code, _ = _api(
                api,
                "POST",
                projects_a,
                cookie=cookie,
                body={
                    "workspace_id": str(WS_A),
                    "name": "p1",
                    "project_type": "generic_model_tuning",
                },
            )
            check("FTF-0103A", f"viewer (insufficient role) POST -> 403 (got {code})", code == 403)

            # negative_acceptance: nothing in the request can grant/widen a role.
            code, _ = _api(
                api,
                "POST",
                projects_b,
                cookie=cookie,
                headers={"X-Sovra-Role": "organization_admin", "X-Role": "organization_admin"},
                body={
                    "organization_id": str(B),
                    "role": "organization_admin",
                    "workspace_id": str(WS_B),
                    "name": "x",
                    "project_type": "generic_model_tuning",
                },
            )
            check(
                "FTF-0103A",
                f"body/header role claims cannot widen -> 403 (got {code})",
                code == 403,
            )
            code, _ = _api(api, "GET", f"{projects_b}?role=organization_admin", cookie=cookie)
            check("FTF-0103A", f"query role claim cannot widen -> 403 (got {code})", code == 403)
            check(
                "FTF-0103A",
                "CLI grants a WORKSPACE-level org_admin in B",
                grant(B, "organization_admin", WS_B).returncode == 0,
            )
            code, _ = _api(api, "GET", projects_b, cookie=cookie)
            check(
                "FTF-0103A",
                f"a workspace-level membership never grants an org-level role (got {code})",
                code == 403,
            )

            # Role change takes effect on the SAME session (role read per request).
            check(
                "FTF-0103A",
                "CLI upgrades A to workspace_admin",
                grant(A, "workspace_admin").returncode == 0,
            )
            code, resp = _api(
                api,
                "POST",
                projects_a,
                cookie=cookie,
                body={
                    "workspace_id": str(WS_A),
                    "name": "p1",
                    "project_type": "generic_model_tuning",
                },
            )
            check("FTF-0103A", f"workspace_admin POST -> 201 (got {code} {resp})", code == 201)

            # Revoked membership: existing session denied, new login denied.
            check("FTF-0103A", "CLI revokes A", revoke(A).returncode == 0)
            code, _ = _api(api, "GET", projects_a, cookie=cookie)
            check("FTF-0103A", f"revoked membership, live session -> 403 (got {code})", code == 403)
            check("FTF-0103A", "CLI revokes B workspace-level", revoke(B, WS_B).returncode == 0)
            status, cookie2, body = _login(api, issuer)
            check(
                "FTF-0103A",
                f"login with only revoked memberships -> 403 revoked: {status} {body}",
                status == 403 and cookie2 is None and body.get("status") == "revoked",
            )
            nothing = revoke(A)
            check(
                "FTF-0103A",
                "revoking an already-revoked membership exits 1",
                nothing.returncode == 1,
            )
            bad = grant(A, "superuser")
            check("FTF-0103A", "CLI refuses a role outside the six", bad.returncode == 1)

            # Logout revokes the session for the API too.
            check("FTF-0103A", "CLI re-grants viewer in A", grant(A, "viewer").returncode == 0)
            status, cookie3, _ = _login(api, issuer)
            assert cookie3 is not None
            code, _ = _api(api, "GET", projects_a, cookie=cookie3)
            check("FTF-0103A", f"fresh session -> 200 (got {code})", code == 200)
            data = read_session_cookie(oidc_it.SIGNING_KEY, cookie3.split("=", 1)[1], max_age=900)
            assert data is not None
            code, _ = _api(
                api,
                "POST",
                "/auth/logout",
                cookie=cookie3,
                headers={"Origin": "http://localhost:3000", "X-CSRF-Token": data["csrf"]},
            )
            check("FTF-0103A", f"logout -> 200 (got {code})", code == 200)
            code, _ = _api(api, "GET", projects_a, cookie=cookie3)
            check("FTF-0103A", f"logged-out (revoked) session -> 401 (got {code})", code == 401)

        # Expired session: a second real app with a 2-second session TTL.
        api_port2 = oidc_it.free_port()
        # (redirect URI is fixed in the OP's client registration; reuse the first port)
        del api_port2
        with _serve_real_app(
            issuer=issuer, api_port=api_port, app_url=app_url, ttl_seconds=2
        ) as api_short:
            status, cookie4, _ = _login(api_short, issuer)
            assert cookie4 is not None
            code, _ = _api(api_short, "GET", f"/api/v1/organizations/{A}/projects", cookie=cookie4)
            check("FTF-0103A", f"short-TTL fresh session -> 200 (got {code})", code == 200)
            time.sleep(3.2)
            code, _ = _api(api_short, "GET", f"/api/v1/organizations/{A}/projects", cookie=cookie4)
            check("FTF-0103A", f"expired session -> 401 (got {code})", code == 401)

    # -- negative_acceptance at the database (raw sovra_app, no app code) ---
    app_engine = create_engine(app_url)
    check("FTF-0103A", "raw engine is the sovra_app role", app_engine.url.username == APP_ROLE_NAME)

    def _try(sql: str) -> bool:
        def run() -> None:
            with app_engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_organization_id', :o, true)"),
                    {"o": str(A)},
                )
                conn.execute(text(sql))

        return _expect_db_error(run, "permission denied")

    before = _count(owner, A)
    check(
        "FTF-0103A",
        "sovra_app cannot INSERT memberships",
        _try(
            f"INSERT INTO memberships (organization_id, issuer, subject, role) "
            f"VALUES ('{A}', 'i', 's', 'viewer')"
        ),
    )
    check(
        "FTF-0103A",
        "sovra_app cannot UPDATE memberships",
        _try("UPDATE memberships SET role = 'organization_admin'"),
    )
    check("FTF-0103A", "sovra_app cannot DELETE memberships", _try("DELETE FROM memberships"))
    check(
        "FTF-0103A",
        "no membership row was changed by the refused writes",
        _count(owner, A) == before,
    )

    rows_no_ctx = _raw_app_query(app_engine, "SELECT id FROM memberships")
    rows_empty = _raw_app_query(
        app_engine, "SELECT id FROM memberships", settings={"app.current_organization_id": ""}
    )
    rows_foreign = _raw_app_query(
        app_engine,
        "SELECT id FROM memberships WHERE organization_id = :o",
        settings={"app.current_organization_id": str(B)},
        params={"o": A},
    )
    rows_own = _raw_app_query(
        app_engine,
        "SELECT id FROM memberships",
        settings={"app.current_organization_id": str(A)},
    )
    check(
        "FTF-0103A",
        f"absent tenant context reads 0 rows (got {len(rows_no_ctx)})",
        len(rows_no_ctx) == 0,
    )
    check(
        "FTF-0103A",
        f"empty tenant context reads 0 rows (got {len(rows_empty)})",
        len(rows_empty) == 0,
    )
    check(
        "FTF-0103A",
        f"foreign tenant context reads 0 rows (got {len(rows_foreign)})",
        len(rows_foreign) == 0,
    )
    check("FTF-0103A", "positive control: own tenant context reads A's rows", len(rows_own) >= 1)

    # identity_self_lookup exposes only the bound identity's own rows.
    for label, settings, expect in (
        ("issuer only", {"app.current_issuer": issuer}, 0),
        ("subject only", {"app.current_subject": SUBJECT}, 0),
        ("another identity", {"app.current_issuer": issuer, "app.current_subject": "nobody"}, 0),
        (
            "another issuer",
            {"app.current_issuer": "https://evil.example/", "app.current_subject": SUBJECT},
            0,
        ),
    ):
        got = _raw_app_query(app_engine, "SELECT id FROM memberships", settings=settings)
        check(
            "FTF-0103A",
            f"identity binding ({label}) reads {expect} rows (got {len(got)})",
            len(got) == expect,
        )
    own_identity = _raw_app_query(
        app_engine,
        "SELECT DISTINCT subject FROM memberships",
        settings={"app.current_issuer": issuer, "app.current_subject": SUBJECT},
    )
    check(
        "FTF-0103A",
        "positive control: own identity reads only its own rows",
        [r[0] for r in own_identity] == [SUBJECT],
    )

    # Policy shape: RLS on+forced, no allow-all policy.
    with owner.connect() as conn:
        flags = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'memberships'"
            )
        ).one()
        policies = conn.execute(
            text(
                "SELECT policyname, cmd, qual, with_check FROM pg_policies "
                "WHERE tablename = 'memberships'"
            )
        ).fetchall()
    check("FTF-0103A", "RLS enabled and forced on memberships", tuple(flags) == (True, True))
    check(
        "FTF-0103A",
        "no allow-all policy",
        all((p[2] or "").strip() not in ("true", "(true)") for p in policies)
        and len(policies) == 2,
    )

    # Constraints (migration role; the superuser bypasses RLS, constraints still hold).
    def _owner_insert(org, ws, role="viewer", status="active", subject="constraint-probe"):
        def run() -> None:
            with owner.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO memberships "
                        "(organization_id, workspace_id, issuer, subject, role, status) "
                        "VALUES (:o, :w, 'iss', :s, :r, :st)"
                    ),
                    {"o": org, "w": ws, "s": subject, "r": role, "st": status},
                )

        return run

    _owner_insert(A, None)()
    check(
        "FTF-0103A",
        "duplicate org-level (NULL workspace) membership is rejected",
        _expect_db_error(_owner_insert(A, None), "uq_memberships_org_workspace_identity"),
    )
    _owner_insert(A, WS_A)()  # same identity, workspace-level: a distinct row
    check(
        "FTF-0103A",
        "role outside the six is rejected",
        _expect_db_error(
            _owner_insert(A, None, role="superuser", subject="x1"), "ck_memberships_role"
        ),
    )
    check(
        "FTF-0103A",
        "status outside active/revoked is rejected",
        _expect_db_error(
            _owner_insert(A, None, status="pending", subject="x2"), "ck_memberships_status"
        ),
    )
    check(
        "FTF-0103A",
        "workspace of a different organization is rejected",
        _expect_db_error(
            _owner_insert(A, WS_B, subject="x3"), "fk_memberships_organization_id_workspace_id"
        ),
    )

    # WITH CHECK is not fail-open: a non-superuser holding INSERT is still bound by RLS.
    with owner.begin() as conn:
        conn.execute(text("DROP ROLE IF EXISTS ftf0103a_probe"))
        conn.execute(text("CREATE ROLE ftf0103a_probe NOLOGIN"))
        conn.execute(text("GRANT SELECT, INSERT ON memberships TO ftf0103a_probe"))

    def _probe_insert(ctx: str | None, org) -> Callable[[], None]:
        def run() -> None:
            with owner.begin() as conn:
                conn.execute(text("SET LOCAL ROLE ftf0103a_probe"))
                if ctx is not None:
                    conn.execute(
                        text("SELECT set_config('app.current_organization_id', :c, true)"),
                        {"c": ctx},
                    )
                conn.execute(
                    text(
                        "INSERT INTO memberships (organization_id, issuer, subject, role) "
                        "VALUES (:o, 'iss', 'with-check-probe', 'viewer')"
                    ),
                    {"o": org},
                )

        return run

    for label, ctx, org in (
        ("no tenant context", None, A),
        ("empty tenant context", "", A),
        ("foreign tenant context", str(B), A),
    ):
        check(
            "FTF-0103A",
            f"WITH CHECK refuses an INSERT under {label}",
            _expect_db_error(_probe_insert(ctx, org), "row-level security"),
        )
    check(
        "FTF-0103A",
        "no row leaked from the refused WITH CHECK inserts",
        _count(owner, A, subject="with-check-probe") == 0,
    )
    _probe_insert(str(A), A)()  # positive control: own context passes
    check(
        "FTF-0103A",
        "positive control: own-context INSERT succeeds",
        _count(owner, A, subject="with-check-probe") == 1,
    )
    with owner.begin() as conn:
        conn.execute(text("REVOKE ALL ON memberships FROM ftf0103a_probe"))
        conn.execute(text("DROP ROLE ftf0103a_probe"))

    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-0103A live auth path integration: {len(FAILURES)} check(s) failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0103A live auth path integration: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
