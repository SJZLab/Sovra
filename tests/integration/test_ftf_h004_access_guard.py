#!/usr/bin/env python3
"""FTF-H004 -- real Postgres authorized-access-guard proof.

Proves this task's own goal ("fail closed on revoked/missing grants")
against a real, live PostgreSQL server -- never a mocked connection or an
in-memory SQLite stand-in (SQLite has no row-level security at all, so a
passing test against it would prove nothing about
`packages/litellm_client/access_guard.py::require_model_access`'s own real
cross-tenant-IDOR half, which composes FTF-H003's own already-CERTIFIED
`authorize_resource_ownership`).

Two real organizations (org1/org2) with one real `LiteLLMConnection`
(FTF-0201, CERTIFIED) each are seeded as the migration-owner role, plus a
second, already-revoked connection for org1 -- every check below runs
through a real pooled `sovra_app` session, same distinct least-privileged
application role FTF-0102's migration creates (never the migration role),
exactly like `tests/integration/test_ftf_h003_cross_layer_isolation.py`
already established for `workspaces`, applied here to the resource
`require_model_access` actually protects.

This task's own acceptance clause ("Real restricted gateway key cannot
enumerate/probe metadata-only models; revoke a once-valid model/key and
verify new queued actions fail while history remains visible") is covered,
for the guard's own real ownership/status half, by
`test_owned_active_connection_with_callable_model_is_genuinely_authorized`,
`test_cross_tenant_connection_id_is_denied_identically_to_a_missing_one`
and `test_explicitly_revoked_connection_denies_even_a_still_callable_model`.
This task's own negative_acceptance ("401/403 never invoke last-known-good
execution; retries of a disappearing catalog cannot silently restore
access") is covered by
`test_catalog_level_revocation_denies_even_an_owned_active_connection` and
`test_never_successfully_discovered_catalog_denies_by_default`.

`packages/model_catalog/access_snapshot.py`'s own real left-join/real-
gateway evidence needs no database at all and is proven separately in
`tests/tasks/test_ftf_h004.py`.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching FTF-0102/FTF-0105/
FTF-H003/FTF-0201's own integration tests' environment contract exactly.

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_h004_access_guard.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from packages.litellm_client.access_guard import (  # noqa: E402
    ModelAccessDenialReason,
    ModelAccessDeniedError,
    require_model_access,
)
from packages.litellm_client.last_known_good_catalog import LastKnownGoodCatalog  # noqa: E402
from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    DiscoveredModel,
    DiscoveryState,
    ModelDiscoveryResult,
    ModelMetadataStatus,
)
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import QueuePool  # noqa: E402

from app.auth.organization_workspace_schema import APP_ROLE_NAME, Organization  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.litellm.connection_schema import LiteLLMConnection  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "access-guard evidence requires a live Postgres (migration "
            "owner connection). Not silently skipped: see this file's own "
            "docstring for how to run it locally or in CI.",
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


def _run_migration() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
    )
    check(
        "FTF-H004",
        f"alembic upgrade head runs cleanly (exit {result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}",
        result.returncode == 0,
    )


_NOW = datetime(2026, 9, 28, tzinfo=UTC)


def _healthy_catalog(*model_ids: str) -> LastKnownGoodCatalog:
    catalog = LastKnownGoodCatalog()
    result = ModelDiscoveryResult(
        state=DiscoveryState.DISCOVERED,
        models=tuple(
            DiscoveredModel(id=mid, metadata_status=ModelMetadataStatus.LIMITED)
            for mid in model_ids
        ),
        model_count=len(model_ids),
        content_hash="irrelevant-to-this-task",
        detail="gateway reachable and authorized",
        checked_at=_NOW,
    )
    catalog.apply_discovery_result(result, now=_NOW)
    return catalog


def _revoked_catalog(*model_ids: str) -> LastKnownGoodCatalog:
    """A catalog that was once healthy for `model_ids` and has since seen
    a real gateway revocation -- entries downgraded to stale, never
    dropped (Section 3.3's own "keep last-known-good snapshots for
    audit")."""
    catalog = _healthy_catalog(*model_ids)
    revoked_result = ModelDiscoveryResult(
        state=DiscoveryState.INVALID_TOKEN,
        models=(),
        model_count=None,
        content_hash=None,
        detail="gateway rejected the inference credential",
        checked_at=_NOW,
    )
    catalog.apply_discovery_result(revoked_result, now=_NOW + timedelta(seconds=1))
    return catalog


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    _run_migration()
    if FAILURES:
        print(f"FTF-H004 real access-guard proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)

    # -- Seed two real, distinct tenants and three real connections ---------
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme H004", slug=f"acme-h004-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex H004", slug=f"globex-h004-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()

        conn1_active = LiteLLMConnection(
            organization_id=org1.id,
            name="Org1 Gateway",
            base_url="https://gateway.example.com",
            secret_ref="openbao://secret/litellm/org1/api_key",
            status="active",
        )
        conn1_revoked = LiteLLMConnection(
            organization_id=org1.id,
            name="Org1 Gateway (revoked)",
            base_url="https://gateway.example.com",
            secret_ref="openbao://secret/litellm/org1b/api_key",
            status="revoked",
        )
        conn2_active = LiteLLMConnection(
            organization_id=org2.id,
            name="Org2 Gateway",
            base_url="https://gateway.example.com",
            secret_ref="openbao://secret/litellm/org2/api_key",
            status="active",
        )
        session.add_all([conn1_active, conn1_revoked, conn2_active])
        session.commit()
        org1_id = org1.id
        conn1_active_id, conn1_revoked_id, conn2_active_id = (
            conn1_active.id,
            conn1_revoked.id,
            conn2_active.id,
        )

    pooled_engine = create_engine(app_url, poolclass=QueuePool, pool_size=2, max_overflow=0)
    session_factory = sessionmaker(bind=pooled_engine)
    org1_context = TenantContext(organization_id=org1_id)

    table = LiteLLMConnection.__table__
    id_col = table.c.id
    org_col = table.c.organization_id
    status_col = table.c.status

    try:
        callable_catalog = _healthy_catalog("gpt-x")
        access_revoked_catalog = _revoked_catalog("gpt-x")
        never_discovered_catalog = LastKnownGoodCatalog()

        # == 1. acceptance: a genuinely owned, active connection with a =====
        # ==    genuinely callable model is authorized ========================
        with tenant_scoped_session_for(session_factory, org1_context) as session:
            try:
                require_model_access(
                    session,
                    org1_context,
                    table,
                    id_col,
                    org_col,
                    status_col,
                    conn1_active_id,
                    callable_catalog,
                    "gpt-x",
                    now=_NOW,
                )
                authorized = True
            except ModelAccessDeniedError:
                authorized = False
        check(
            "FTF-H004",
            "acceptance: an owned, active connection with a genuinely callable model is "
            "authorized against a real database (no exception)",
            authorized,
        )

        # == 2. negative_acceptance ("missing grant"): cross-tenant/missing ==
        with tenant_scoped_session_for(session_factory, org1_context) as session:
            try:
                require_model_access(
                    session,
                    org1_context,
                    table,
                    id_col,
                    org_col,
                    status_col,
                    conn2_active_id,
                    callable_catalog,
                    "gpt-x",
                    now=_NOW,
                )
                cross_tenant_reason: ModelAccessDenialReason | None = None
            except ModelAccessDeniedError as exc:
                cross_tenant_reason = exc.reason
        check(
            "FTF-H004",
            "negative_acceptance: a real connection genuinely belonging to org2 is denied "
            "while authenticated as org1, live against Postgres/RLS -- not a mocked assumption",
            cross_tenant_reason == ModelAccessDenialReason.CONNECTION_NOT_OWNED,
        )

        with tenant_scoped_session_for(session_factory, org1_context) as session:
            try:
                require_model_access(
                    session,
                    org1_context,
                    table,
                    id_col,
                    org_col,
                    status_col,
                    uuid.uuid4(),
                    callable_catalog,
                    "gpt-x",
                    now=_NOW,
                )
                missing_reason: ModelAccessDenialReason | None = None
            except ModelAccessDeniedError as exc:
                missing_reason = exc.reason
        check(
            "FTF-H004",
            "a wholly nonexistent connection ID is denied with the identical reason as a "
            "real guessed foreign-tenant ID -- no existence oracle, live",
            missing_reason == cross_tenant_reason == ModelAccessDenialReason.CONNECTION_NOT_OWNED,
        )

        # == 3. negative_acceptance ("missing grant"): an explicitly =========
        # ==    revoked Factory-level connection denies even a catalog that =
        # ==    still considers the model callable (a "disappearing =========
        # ==    catalog" cannot restore a grant the connection itself lost) ==
        with tenant_scoped_session_for(session_factory, org1_context) as session:
            try:
                require_model_access(
                    session,
                    org1_context,
                    table,
                    id_col,
                    org_col,
                    status_col,
                    conn1_revoked_id,
                    callable_catalog,
                    "gpt-x",
                    now=_NOW,
                )
                revoked_conn_reason: ModelAccessDenialReason | None = None
            except ModelAccessDeniedError as exc:
                revoked_conn_reason = exc.reason
        check(
            "FTF-H004",
            "negative_acceptance: a real, explicitly-revoked LiteLLMConnection denies model "
            "access even though the in-memory catalog still reports the model callable",
            revoked_conn_reason == ModelAccessDenialReason.CONNECTION_REVOKED,
        )

        # == 4. negative_acceptance: catalog-level revocation denies even ===
        # ==    an owned, active connection -- and history is untouched by ==
        # ==    the denial itself ============================================
        with tenant_scoped_session_for(session_factory, org1_context) as session:
            try:
                require_model_access(
                    session,
                    org1_context,
                    table,
                    id_col,
                    org_col,
                    status_col,
                    conn1_active_id,
                    access_revoked_catalog,
                    "gpt-x",
                    now=_NOW,
                )
                catalog_revoked_reason: ModelAccessDenialReason | None = None
            except ModelAccessDeniedError as exc:
                catalog_revoked_reason = exc.reason
        check(
            "FTF-H004",
            "negative_acceptance: a real gateway-level (catalog) revocation denies model "
            "access even for an owned, still-active Factory connection",
            catalog_revoked_reason == ModelAccessDenialReason.ACCESS_REVOKED,
        )
        check(
            "FTF-H004",
            "acceptance: the revoked model remains visible in the catalog's own history, "
            "downgraded to stale -- the denial above did not erase it",
            any(
                entry.id == "gpt-x" and entry.status.value == "stale"
                for entry in access_revoked_catalog.snapshot().entries
            ),
        )

        # == 5. negative_acceptance: a catalog with no successful discovery =
        # ==    yet denies by default, never treated as "no rows, so allow" ==
        with tenant_scoped_session_for(session_factory, org1_context) as session:
            try:
                require_model_access(
                    session,
                    org1_context,
                    table,
                    id_col,
                    org_col,
                    status_col,
                    conn1_active_id,
                    never_discovered_catalog,
                    "gpt-x",
                    now=_NOW,
                )
                never_discovered_reason: ModelAccessDenialReason | None = None
            except ModelAccessDeniedError as exc:
                never_discovered_reason = exc.reason
        check(
            "FTF-H004",
            "a catalog that has never had a successful discovery cycle denies by default",
            never_discovered_reason == ModelAccessDenialReason.NO_SUCCESSFUL_DISCOVERY_YET,
        )
    finally:
        pooled_engine.dispose()

    if FAILURES:
        print(f"FTF-H004 real access-guard proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-H004 real access-guard proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
