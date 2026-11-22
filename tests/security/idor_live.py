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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(ROOT / "tests" / "security"))

import uvicorn  # noqa: E402
from idor_suite import (  # noqa: E402
    ROUTE_REGISTRY,
    CellVerdict,
    IdorSuiteError,
    Observed,
    StateSnapshot,
    assert_digest_covers,
    evaluate_cell,
    evaluate_positive_control,
)
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

ISSUER = "https://idor-suite.invalid/"
SIGNING_KEY = "idor-suite-only-signing-key-not-a-real-secret"
TENANTS = ("alpha", "bravo")
ROLES = ("organization_admin",)
#: Observed 2026-11-21 on this harness: a `workspace_admin` membership scoped to
#: the first workspace gets 403 on every project route, including its own project,
#: so no workspace-scoped role can serve as the `foreign_project` positive control
#: yet. `foreign_project` is therefore not driven for an organization admin (it
#: legitimately reaches every project of its organization).


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
            ws2 = Workspace(organization_id=org.id, name=f"{name} ws two", slug="second")
            session.add(ws2)
            session.flush()
            project2 = Project(
                organization_id=org.id,
                workspace_id=ws2.id,
                name=f"{name}-project-two",
                project_type="generic_model_tuning",
                status="active",
                security_profile="standard",
                row_version=1,
                created_by="seed",
            )
            session.add(project2)
            session.flush()
            subject = f"user-{name}"
            grant_membership(
                session,
                organization_id=org.id,
                issuer=ISSUER,
                subject=subject,
                role="organization_admin",
            )
            ws_subject = f"user-{name}-ws"
            grant_membership(
                session,
                organization_id=org.id,
                issuer=ISSUER,
                subject=ws_subject,
                role="workspace_admin",
                workspace_id=ws.id,
            )
            out[name] = {
                "subjects": {"organization_admin": subject, "workspace_admin": ws_subject},
                "organization_id": org.id,
                "workspace_id": ws.id,
                "project_id": project.id,
                "project2_id": project2.id,
                "subject": subject,
            }
        session.commit()
    return out


# -- Seeders ------------------------------------------------------------------
#
# One row per (tenant, kind), written as the migration owner with plain SQL
# (RLS cannot hide or refuse the seed). Every free-text field of a row seeded
# for tenant `bravo` carries `marker(...)`; a response to an `alpha` request
# containing it is a leak. Kinds without a seeder are reported by
# `unseeded_routes`, never skipped silently.

ORG_KINDS = ("trainable_artifact", "litellm_connection", "cost_profile", "component_binding")
PROJECT_KINDS = (
    "dataset_source_connection",
    "tool_definition",
    "model_version",
    "target_connection",
    "target_source_snapshot",
    "discovered_agent_candidate",
    "agent_manifest",
    "manifest_evidence",
    "evaluation_suite",
    "evaluation_case",
    "dataset_split_manifest",
)


def marker(tenant: str, what: str) -> str:
    return f"MARK-{tenant}-{what}-{uuid.uuid5(uuid.NAMESPACE_URL, tenant + what).hex[:10]}"


def marker_sha256(tenant: str, what: str) -> str:
    """A well-formed, deterministic sha256 hex digest per (tenant, what), for
    columns whose CHECK demands `^[0-9a-f]{64}$`; the digest itself is the
    leak marker (`marker_sha256` is scanned for in foreign responses)."""
    return hashlib.sha256(marker(tenant, what).encode()).hexdigest()


def _insert(conn: Any, table: str, values: dict[str, Any]) -> uuid.UUID:
    row_id = uuid.uuid4()
    cols = {"id": row_id, **values}
    names = ", ".join(f'"{c}"' for c in cols)
    params = ", ".join(f":{c}" for c in cols)
    conn.execute(text(f'INSERT INTO "{table}" ({names}) VALUES ({params})'), cols)  # noqa: S608
    return row_id


def seed_org_kind(conn: Any, kind: str, tenant: dict[str, Any], tag: str) -> uuid.UUID:
    org = tenant["organization_id"]
    if kind == "trainable_artifact":
        return _insert(
            conn,
            "trainable_artifacts",
            {
                "organization_id": org,
                "display_name": marker(tag, "artifact"),
                "source_type": "huggingface",
                "source_uri": f"hf://{marker(tag, 'uri')}",
                "model_family": "llama",
                "base_model_revision": "r1",
                "license_id": "apache-2.0",
            },
        )
    if kind == "litellm_connection":
        return _insert(
            conn,
            "litellm_connections",
            {
                "organization_id": org,
                "name": marker(tag, "gateway"),
                "base_url": "https://litellm.invalid",
                "secret_ref": marker(tag, "secretref"),
            },
        )
    if kind == "cost_profile":
        return _insert(
            conn,
            "cost_profiles",
            {
                "organization_id": org,
                "profile_type": "provider",
                "label": marker(tag, "cost"),
                "rate_unit": "per_hour",
                "rate_amount": 1,
                "currency": "USD",
                "source": "seed",
                "rate_recorded_at": datetime.now(UTC),
                "created_by": "seed",
            },
        )
    if kind == "component_binding":
        return _insert(
            conn,
            "component_bindings",
            {
                "organization_id": org,
                "component_key": "mlflow",
                "security_zone_scope": "org",
                "deployment_mode": "internal_service",
                "approved_endpoint": "https://mlflow.invalid",
                "network_policy_identity": marker(tag, "netpol"),
                "compatibility_lock_reference": "lock",
            },
        )
    raise IdorSuiteError(f"no organization-level seeder for kind {kind!r}")


def seed_project_kind(
    conn: Any,
    kind: str,
    tenant: dict[str, Any],
    project_id: Any,
    tag: str,
    deps: dict[str, Any] | None = None,
) -> uuid.UUID:
    """`deps` holds the ids already seeded for THIS project (kinds are seeded
    in `PROJECT_KINDS` order), so a child row always hangs off its own
    project's parent, never the other project's."""
    org = tenant["organization_id"]
    deps = deps or {}
    if kind == "dataset_source_connection":
        return _insert(
            conn,
            "dataset_source_connections",
            {
                "organization_id": org,
                "project_id": project_id,
                "source_type": "upload",
                "display_name": marker(tag, "source"),
                "allowed_purposes_json": "[]",
                "security_classification": "internal",
                "created_by": "seed",
            },
        )
    if kind == "tool_definition":
        return _insert(
            conn,
            "tool_definitions",
            {
                "organization_id": org,
                "project_id": project_id,
                "name": marker(tag, "tool"),
                "input_schema_json": "{}",
                "output_schema_json": "{}",
                "side_effect_class": "read_only",
                "schema_hash": marker(tag, "hash"),
                "created_by": "seed",
            },
        )
    if kind == "model_version":
        return _insert(
            conn,
            "model_versions",
            {
                "organization_id": org,
                "project_id": project_id,
                "version_number": 1,
                "import_kind": "endpoint",
                "origin": "external_endpoint",
                "external_provider_model_ref": marker(tag, "modelref"),
                "endpoint_reference": f"https://{marker(tag, 'endpoint')}.invalid",
                "created_by": "seed",
            },
        )
    if kind == "target_connection":
        return _insert(
            conn,
            "target_application_connections",
            {
                "organization_id": org,
                "project_id": project_id,
                "connector_type": "github",
                "display_name": marker(tag, "target"),
                "repository_identity": marker(tag, "repo"),
                "created_by": "seed",
            },
        )
    if kind == "target_source_snapshot":
        return _insert(
            conn,
            "target_source_snapshots",
            {
                "organization_id": org,
                "target_application_connection_id": deps["target_connection"],
                "commit_sha": marker(tag, "commit"),
                "tree_hash": marker(tag, "tree"),
                "capture_method": "github_resolved_commit",
            },
        )
    if kind == "discovered_agent_candidate":
        return _insert(
            conn,
            "discovered_agent_candidates",
            {
                "organization_id": org,
                "target_source_snapshot_id": deps["target_source_snapshot"],
                "candidate_key": marker(tag, "candkey"),
                "display_name": marker(tag, "candidate"),
                "confidence_score": 0.5,
            },
        )
    if kind == "agent_manifest":
        return _insert(
            conn,
            "agent_manifests",
            {
                "organization_id": org,
                "project_id": project_id,
                "target_source_snapshot_id": deps["target_source_snapshot"],
                "version": 1,
                "created_by": "seed",
            },
        )
    if kind == "manifest_evidence":
        return _insert(
            conn,
            "manifest_evidence",
            {
                "organization_id": org,
                "agent_manifest_id": deps["agent_manifest"],
                "field_path": marker(tag, "fieldpath"),
                "evidence_type": "user_input",
                "evidence_json": "{}",
            },
        )
    if kind == "evaluation_suite":
        return _insert(
            conn,
            "evaluation_suites",
            {
                "organization_id": org,
                "project_id": project_id,
                "agent_manifest_id": deps["agent_manifest"],
                "version": 1,
                "purpose": "development",
                "name": marker(tag, "suite"),
                "grading_logic_version": 1,
                "created_by": "seed",
            },
        )
    if kind == "evaluation_case":
        return _insert(
            conn,
            "evaluation_cases",
            {
                "organization_id": org,
                "project_id": project_id,
                "suite_id": deps["evaluation_suite"],
                "case_key": marker(tag, "casekey"),
                "category": "general",
                "severity": "low",
                "language": "en",
                "group_key": marker(tag, "group"),
                "input_json": "{}",
                "expected_json": '{"answer": "ok"}',
                "grader_type": "exact_match",
                "created_by": "seed",
            },
        )
    if kind == "dataset_split_manifest":
        return _insert(
            conn,
            "dataset_split_manifests",
            {
                "organization_id": org,
                "project_id": project_id,
                "name": marker(tag, "split"),
                "version": 1,
                "strategy": "sha256_group_hash_v1",
                "seed": "1",
                "ratios": [70, 10, 10, 10],
                "normalization_version": "v1",
                "split_manifest_sha256": marker_sha256(tag, "splitsha"),
                "proposal_sha256": marker_sha256(tag, "proposal"),
                "leakage_report": "{}",
                "leakage_report_sha256": marker_sha256(tag, "leaksha"),
                "leakage_status": "clear",
                "created_by": "seed",
            },
        )
    raise IdorSuiteError(f"no project-level seeder for kind {kind!r}")


def seed_project_extras(
    conn: Any, tenant: dict[str, Any], project_id: Any, tag: str, ids: dict[str, Any]
) -> None:
    """Rows that make singleton/list project routes non-vacuous: without them
    an own project answers the same as a missing one (empty list / 404), so a
    positive control could not tell a working route from a missing project."""
    org = tenant["organization_id"]
    policy_id = _insert(
        conn,
        "budget_policies",
        {
            "organization_id": org,
            "project_id": project_id,
            "total_amount": 100,
            "currency": "USD",
            "scopes_json": "[]",
            "created_by": "seed",
        },
    )
    conn.execute(
        text("UPDATE projects SET budget_policy_id = :b WHERE id = :p AND organization_id = :o"),
        {"b": policy_id, "p": project_id, "o": org},
    )
    _insert(
        conn,
        "model_import_jobs",
        {
            "organization_id": org,
            "project_id": project_id,
            "requested_origin": "external_endpoint",
            "external_provider_model_ref": marker(tag, "jobref"),
            "litellm_connection_id": ids["litellm_connection"],
            "created_by": "seed",
        },
    )


def seed_kinds(owner: Engine, tenants: dict[str, dict[str, Any]]) -> None:
    """Add, per tenant, one row of every seeded kind: org-level rows, and
    project-level rows in the tenant's project and in its second project
    (`project2`, the `foreign_project` class)."""
    with owner.begin() as conn:
        for name, tenant in tenants.items():
            ids: dict[str, Any] = {}
            for kind in ORG_KINDS:
                ids[kind] = seed_org_kind(conn, kind, tenant, name)
            second: dict[str, Any] = {}
            for kind in PROJECT_KINDS:
                ids[kind] = seed_project_kind(conn, kind, tenant, tenant["project_id"], name, ids)
                second[kind] = seed_project_kind(
                    conn, kind, tenant, tenant["project2_id"], name + "-p2", second
                )
            tenant["ids"] = ids
            tenant["ids_project2"] = second
            seed_project_extras(conn, tenant, tenant["project_id"], name, ids)
            seed_project_extras(conn, tenant, tenant["project2_id"], name + "-p2", ids)


# -- Matrix -----------------------------------------------------------------------

SEEDED_KINDS = frozenset(("organization", "project", *ORG_KINDS, *PROJECT_KINDS))


def unseeded_routes() -> list[tuple[str, str]]:
    """Route-verbs the matrix cannot yet exercise: a path kind without a
    seeder, or a method the runner does not drive (writes need valid bodies;
    destructive verbs need a fresh row per cell)."""
    out = []
    for (method, template), spec in ROUTE_REGISTRY.items():
        kinds = {slot.kind for slot in spec.path_slots}
        if method != "GET" or spec.destructive or not kinds <= SEEDED_KINDS or spec.query_slots:
            out.append((method, template))
    return out


def _slot_value(
    slot_kind: str, id_class: str, alpha: dict[str, Any], bravo: dict[str, Any]
) -> str | None:
    """Value for one path slot under an id class; None = class not defined
    for this slot."""
    if id_class == "missing":
        return str(uuid.uuid4())
    if id_class == "own":
        own = {"organization": alpha["organization_id"], "project": alpha["project_id"]}
        return str(own.get(slot_kind) or alpha["ids"].get(slot_kind))
    if id_class == "foreign_org":
        foreign = {"organization": bravo["organization_id"], "project": bravo["project_id"]}
        return str(foreign.get(slot_kind) or bravo["ids"].get(slot_kind))
    if id_class == "foreign_project":
        if slot_kind == "project":
            return str(alpha["project2_id"])
        if slot_kind in PROJECT_KINDS:
            return str(alpha["ids_project2"][slot_kind])
        return None
    return None


def _all_ids(tenant: dict[str, Any]) -> list[str]:
    ids = [str(v) for v in tenant["ids"].values()] + [
        str(v) for v in tenant["ids_project2"].values()
    ]
    return ids + [str(tenant["organization_id"]), str(tenant["project_id"])]


def run_live_matrix(
    base_url: str, owner: Engine, tenants: dict[str, dict[str, Any]]
) -> tuple[list[CellVerdict], list[tuple[str, str]]]:
    """Every seedable GET route x path slot x foreign id class, against the
    `missing` baseline, plus the positive control per slot. Returns the
    verdicts and the route-verbs the matrix could not drive."""
    alpha, bravo = tenants["alpha"], tenants["bravo"]
    tables = enumerate_tables(owner)
    assert_digest_covers(tables, ["audit_events"])
    skipped = set(unseeded_routes())
    markers = [marker("bravo", w) for w in ("artifact", "uri", "gateway", "secretref", "cost")]
    markers += [marker("bravo", w) for w in ("netpol", "source", "tool", "hash")]
    markers += [marker("bravo-p2", w) for w in ("source", "tool", "hash")]
    new_words = (
        *("commit", "tree", "candkey", "candidate", "fieldpath"),
        *("suite", "casekey", "group", "split"),
    )
    markers += [marker(t, w) for t in ("bravo", "bravo-p2") for w in new_words]
    sha_words = ("splitsha", "proposal", "leaksha")
    markers += [marker_sha256(t, w) for t in ("bravo", "bravo-p2") for w in sha_words]
    forbidden = _all_ids(bravo)
    verdicts: list[CellVerdict] = []
    for (method, template), spec in ROUTE_REGISTRY.items():
        if (method, template) in skipped:
            continue
        for index, slot in enumerate(spec.path_slots):
            if not slot.resolves_row:
                continue

            def url_for(
                id_class: str, index: int = index, template: str = template, spec: Any = spec
            ) -> str:
                path = template
                for i, s in enumerate(spec.path_slots):
                    cls = id_class if i == index else "own"
                    value = _slot_value(s.kind, cls, alpha, bravo)
                    if value is None:
                        raise IdorSuiteError(f"class {id_class} undefined for kind {s.kind}")
                    path = path.replace("{" + s.name + "}", value, 1)
                return path

            for role in ROLES:
                subject = alpha["subjects"][role]

                def sender(
                    id_class: str,
                    method: str = method,
                    url_for: Any = url_for,
                    subject: str = subject,
                ) -> Observed:
                    return send(base_url, method, url_for(id_class), subject=subject)

                verdicts.append(
                    evaluate_positive_control(
                        method=method,
                        template=template,
                        slot=slot.name,
                        role=role,
                        send=sender,
                    )
                )
                for id_class in ("foreign_org", "foreign_project"):
                    if _slot_value(slot.kind, id_class, alpha, bravo) is None:
                        continue
                    if id_class == "foreign_project":
                        continue  # see ROLES above; not driven, listed in the report
                    verdicts.append(
                        evaluate_cell(
                            method=method,
                            template=template,
                            slot=slot.name,
                            id_class=id_class,
                            role=role,
                            send=sender,
                            snapshot=lambda: snapshot(owner, tables),
                            markers=markers,
                            forbidden=forbidden,
                        )
                    )
    return verdicts, sorted(skipped)


@contextlib.contextmanager
def live_environment() -> Iterator[tuple[str, Engine, dict[str, dict[str, Any]]]]:
    migration_url = required_env("SOVRA_DATABASE_URL")
    app_password = required_env("SOVRA_APP_DB_PASSWORD")
    migrate(migration_url)
    owner = create_engine(migration_url)
    try:
        tenants = seed_tenants(owner)
        seed_kinds(owner, tenants)
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
        verdicts, skipped = run_live_matrix(base_url, owner, tenants)
        failed = [v for v in verdicts if not v.passed]
        print(f"cells: {len(verdicts)} failed: {len(failed)} unseeded route-verbs: {len(skipped)}")
        for v in failed:
            print("FAIL", v.method, v.template, v.slot, v.id_class, v.status, v.problems)
        if failed or skipped:
            sys.exit(1)


if __name__ == "__main__":
    main()
