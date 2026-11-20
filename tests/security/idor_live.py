#!/usr/bin/env python3
"""FTF-1504 live harness (layer 3, part 1): the real app over a real socket,
two real tenants, real session cookies, and an observable-state digest.

This module is the *bootstrap* of the live matrix. It deliberately contains
no matrix cells and claims no coverage: `idor_suite.run_live_matrix` (not yet
written) is what turns it into evidence. Nothing here is a test double:

* the app is `app.main.create_app()` served by uvicorn;
* memberships are written with the production `grant_membership` (migrate
  role), and the session cookie is minted by the production
  `_issue_session_cookie` signer with the same signing key the app reads;
* the app reads Postgres through the restricted `sovra_app` role.

Requires `SOVRA_DATABASE_URL` (migration owner) and `SOVRA_APP_DB_PASSWORD`;
otherwise exits 1 with BLOCKED_EXTERNAL (never skipped).
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(ROOT / "tests" / "security"))

import uvicorn  # noqa: E402
from idor_suite import IdorSuiteError, Observed, StateSnapshot  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

ISSUER = "https://idor-suite.invalid/"
SIGNING_KEY = "idor-suite-only-signing-key-not-a-real-secret"
TENANTS = ("alpha", "bravo")
ROLES = ("organization_admin",)


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- the FTF-1504 live matrix needs a real "
            "Postgres (migration-owner connection). Not silently skipped.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def app_role_url(migration_url: str, app_password: str) -> str:
    from app.auth.organization_workspace_schema import APP_ROLE_NAME

    parts = urlsplit(migration_url)
    netloc = f"{APP_ROLE_NAME}:{app_password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def migrate(migration_url: str) -> None:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = migration_url
    done = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    if done.returncode != 0:
        raise IdorSuiteError(f"alembic upgrade head failed: {done.stderr[-800:]}")


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


@contextlib.contextmanager
def serve_real_app(app_url: str) -> Iterator[str]:
    """The REAL `create_app()`, configured through the environment the way
    production configures it, on a real socket."""
    port = free_port()
    env = {
        "SOVRA_ENVIRONMENT": "staging",
        "SOVRA_DATABASE_URL": app_url,
        "SOVRA_OIDC_ISSUER": ISSUER,
        "SOVRA_OIDC_CLIENT_ID": "idor-suite",
        "SOVRA_OIDC_CLIENT_SECRET_REF": "secret://oidc/client-secret",
        "SOVRA_OIDC_REDIRECT_URI": f"http://127.0.0.1:{port}/auth/callback",
        "SOVRA_OIDC_AUTHORIZATION_ENDPOINT": f"{ISSUER}authorization",
        "SOVRA_OIDC_TOKEN_ENDPOINT": f"{ISSUER}token",
        "SOVRA_OIDC_JWKS_URI": f"{ISSUER}jwks",
        "SOVRA_SESSION_SIGNING_KEY_SECRET_REF": "secret://session/signing-key",
        "SOVRA_OIDC_ALLOWED_ORIGIN": "http://localhost:3000",
        "SOVRA_LITELLM_GATEWAY_URL": "https://litellm.internal.example",
        "SOVRA_PRIVACY_POLICY": "standard",
        "SOVRA_TLS_VERIFY": "true",
        "SOVRA_MAX_TRAINING_BUDGET_USD": "500.00",
        "SOVRA_LITELLM_API_KEY_SECRET_REF": "vault://sovra/litellm-api-key",
        "SOVRA_DATABASE_PASSWORD_SECRET_REF": "openbao://sovra/db-password",
        "SOVRA_OIDC_CLIENT_SECRET_VALUE": "idor-suite-client-secret",
        "SOVRA_SESSION_SIGNING_KEY_VALUE": SIGNING_KEY,
        "SOVRA_SESSION_TTL_SECONDS": "3600",
    }
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        import app.main as main_module

        server = uvicorn.Server(
            uvicorn.Config(
                main_module.create_app(), host="127.0.0.1", port=port, log_level="warning"
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(500):
            if server.started:
                break
            time.sleep(0.02)
        else:
            raise IdorSuiteError("the real app did not start")
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def session_cookie(subject: str) -> tuple[str, str]:
    """(cookie value, csrf token) from the production signer."""
    from app.auth.oidc_login import _issue_session_cookie

    token, csrf, _jti = _issue_session_cookie(
        SIGNING_KEY.encode(), subject=subject, issuer=ISSUER, email=None
    )
    return token, csrf


def send(
    base_url: str,
    method: str,
    path: str,
    *,
    subject: str | None = None,
    body: bytes | None = None,
    content_type: str = "application/json",
) -> Observed:
    """One raw request; status, every header and the exact body bytes."""
    headers: dict[str, str] = {}
    if subject is not None:
        token, csrf = session_cookie(subject)
        headers["Cookie"] = f"sovra_session={token}"
        if method not in ("GET", "HEAD", "OPTIONS"):
            headers["X-CSRF-Token"] = csrf
            headers["Origin"] = "http://localhost:3000"
    if body is not None:
        headers["Content-Type"] = content_type
    parsed = urlsplit(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=15)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        return Observed(response.status, tuple(response.getheaders()), raw)
    finally:
        conn.close()


# -- Observable state -------------------------------------------------------


def enumerate_tables(owner: Engine) -> list[str]:
    with owner.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            )
        ).all()
    return [r[0] for r in rows]


def db_digest(owner: Engine, tables: list[str]) -> dict[str, str]:
    """Per-table digest of every row (migration-owner connection, so row
    level security cannot hide a write from the observer)."""
    digest: dict[str, str] = {}
    with owner.connect() as conn:
        for table in tables:
            rows = conn.execute(text(f'SELECT t::text FROM "{table}" t ORDER BY 1')).all()  # noqa: S608
            digest[table] = hashlib.sha256("\n".join(r[0] for r in rows).encode()).hexdigest()
    return digest


def snapshot(owner: Engine, tables: list[str]) -> StateSnapshot:
    return StateSnapshot(db=db_digest(owner, tables), storage="", outbound={})


# -- Tenants ----------------------------------------------------------------


def seed_tenants(owner: Engine) -> dict[str, dict[str, Any]]:
    """Two organisations, each with a workspace, a project and one
    organization_admin member, created through the production
    `grant_membership` write path."""
    from app.auth.membership_store import grant_membership
    from app.auth.organization_workspace_schema import Organization, Workspace
    from app.projects.project_schema import Project  # noqa: PLC0415

    out: dict[str, dict[str, Any]] = {}
    with Session(owner) as session:
        for name in TENANTS:
            org = Organization(name=f"{name} org", slug=f"{name}-{uuid.uuid4().hex[:8]}")
            session.add(org)
            session.flush()
            ws = Workspace(organization_id=org.id, name=f"{name} ws", slug="main")
            session.add(ws)
            session.flush()
            project = Project(
                organization_id=org.id,
                workspace_id=ws.id,
                name=f"{name}-project",
                project_type="generic_model_tuning",
                status="active",
                security_profile="standard",
                row_version=1,
                created_by="seed",
            )
            session.add(project)
            session.flush()
            subject = f"user-{name}"
            grant_membership(
                session,
                organization_id=org.id,
                issuer=ISSUER,
                subject=subject,
                role="organization_admin",
            )
            out[name] = {
                "organization_id": org.id,
                "workspace_id": ws.id,
                "project_id": project.id,
                "subject": subject,
            }
        session.commit()
    return out


@contextlib.contextmanager
def live_environment() -> Iterator[tuple[str, Engine, dict[str, dict[str, Any]]]]:
    migration_url = required_env("SOVRA_DATABASE_URL")
    app_password = required_env("SOVRA_APP_DB_PASSWORD")
    migrate(migration_url)
    owner = create_engine(migration_url)
    try:
        tenants = seed_tenants(owner)
        with serve_real_app(app_role_url(migration_url, app_password)) as base_url:
            yield base_url, owner, tenants
    finally:
        owner.dispose()


def main() -> None:
    with live_environment() as (base_url, owner, tenants):
        alpha, bravo = tenants["alpha"], tenants["bravo"]
        own = send(
            base_url,
            "GET",
            f"/api/v1/organizations/{alpha['organization_id']}/projects/{alpha['project_id']}",
            subject=alpha["subject"],
        )
        print("own project:", own.status)
        foreign = send(
            base_url,
            "GET",
            f"/api/v1/organizations/{alpha['organization_id']}/projects/{bravo['project_id']}",
            subject=alpha["subject"],
        )
        missing = send(
            base_url,
            "GET",
            f"/api/v1/organizations/{alpha['organization_id']}/projects/{uuid.uuid4()}",
            subject=alpha["subject"],
        )
        print("foreign:", foreign.status, "missing:", missing.status)
        tables = enumerate_tables(owner)
        print("digested tables:", len(tables))


if __name__ == "__main__":
    main()
