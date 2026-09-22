"""Factory API entrypoint (first live FastAPI app in this repository).

FTF-0101 (OIDC login) is the first task that needs an actual running HTTP
service -- every prior CERTIFIED task deliberately disclosed "no live
FastAPI app exists yet" as the reason its own module had no live caller
(FTF-0005, FTF-0006, FTF-0102, FTF-0104). This module is that caller: it
builds and serves the real `/auth/*` router from
`app.auth.oidc_login.build_oidc_login_router`, (FTF-0201) the real
`/api/v1/organizations/{organization_id}/connections/litellm` router from
`app.litellm.connection_routes.build_litellm_connection_router` -- the
second live-wired product-runtime path in this repository -- and
(FTF-0301) the real `/api/v1/organizations/{organization_id}/artifacts`
router from `app.artifacts.artifact_routes.build_trainable_artifact_router`
-- the third, and (FTF-0401) the real
`/api/v1/organizations/{organization_id}/projects` router from
`app.projects.project_routes.build_project_router` -- the fourth, and
(FTF-0404) the real
`/api/v1/organizations/{organization_id}/projects/{project_id}/tools`
router from `app.tools.tool_definition_routes.build_tool_definition_router`
-- the fifth, and (FTF-0407) the real, read-only
`/api/v1/organizations/{organization_id}/projects/{project_id}/requirements`
router from
`app.tools.critical_behavior_requirement_routes.build_critical_behavior_requirement_router`
-- the sixth, and (FTF-0403) the real
`/api/v1/organizations/{organization_id}/projects/{project_id}/budget-policy`
router from `app.budget.budget_policy_routes.build_budget_policy_router`
-- the seventh, and (FTF-1301) the real
`/api/v1/organizations/{organization_id}/cost-profiles` and
`/api/v1/organizations/{organization_id}/projects/{project_id}/costs/
cost-per-success/search` routes from
`app.costs.cost_profile_routes.build_cost_profile_router` -- the eighth.

Secret resolution gap (disclosed, not hidden -- same class of gap FTF-0005
and FTF-R017 already carry): `OidcSettings` only carries *references*
(`vault://...`) for the OIDC client secret and the session-signing key.
No secret-manager integration exists anywhere in this repository yet, so
this module resolves them from two directly-injected environment
variables (`SOVRA_OIDC_CLIENT_SECRET_VALUE`, `SOVRA_SESSION_SIGNING_KEY_VALUE`)
rather than from the `*_secret_ref` values themselves. This is a real,
tracked gap for whichever task first wires an actual secret-manager
resolver -- not a production mock of the OIDC/session logic itself, which
is fully real from this point on.

FTF-0201's own connection router never needs that same secret-resolution
step at all (see `app/litellm/connection_schema.py`'s own module
docstring): it only stores/validates a connection's `secret_ref` shape,
never resolves it to a value, since this task never calls the gateway.
What it does need is a real database engine: `SOVRA_DATABASE_URL` (FTF-0005's
own `Settings.from_env()`) is trusted here to already be a real connection
string for the restricted `sovra_app` role in a real deployment -- the
same migration-owner/app-role operational split every integration test in
this repository already assumes (see e.g.
`tests/integration/test_ftf_0106_rbac_idor_security.py`'s own
`_app_role_url` helper); migrations run as a separate, more-privileged
process/step, never through this running app. `install_tenant_pool_reset_guard`
(FTF-0105) is installed on that engine as the same connection-pool-reuse
defense-in-depth every tenant-scoped table in this repository already
relies on. `principal_resolver`/`role_lookup` stay at their fail-closed
defaults -- no live session-reader backed by `oidc_login.read_session_cookie`
or DB-backed Membership lookup is wired in yet, the same disclosed gap
FTF-0103/FTF-0106 already carry.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.artifacts.artifact_routes import build_trainable_artifact_router
from app.auth.oidc_login import build_oidc_login_router
from app.auth.oidc_settings import OidcSettings
from app.auth.tenant_isolation_query_guards import install_tenant_pool_reset_guard
from app.budget.budget_policy_routes import build_budget_policy_router
from app.config.configuration_system import Settings
from app.costs.cost_profile_routes import build_cost_profile_router
from app.litellm.connection_routes import build_litellm_connection_router
from app.projects.project_routes import build_project_router
from app.tools.critical_behavior_requirement_routes import (
    build_critical_behavior_requirement_router,
)
from app.tools.tool_definition_routes import build_tool_definition_router


def create_app() -> FastAPI:
    production = os.environ.get("SOVRA_ENVIRONMENT") == "production"
    settings = OidcSettings.from_env(production=production)
    app_settings = Settings.from_env()

    # See module docstring: resolving `*_secret_ref` to a real value is a
    # disclosed, tracked gap -- no secret-manager integration exists yet.
    client_secret = os.environ["SOVRA_OIDC_CLIENT_SECRET_VALUE"]
    signing_key = os.environ["SOVRA_SESSION_SIGNING_KEY_VALUE"].encode("utf-8")

    engine = create_engine(app_settings.database_url)
    install_tenant_pool_reset_guard(engine)
    session_factory = sessionmaker(bind=engine)

    app = FastAPI(title="Sovra Factory API")
    app.include_router(
        build_oidc_login_router(settings, client_secret=client_secret, signing_key=signing_key)
    )
    app.include_router(build_litellm_connection_router(session_factory))
    app.include_router(build_trainable_artifact_router(session_factory))
    app.include_router(build_project_router(session_factory))
    app.include_router(build_tool_definition_router(session_factory))
    app.include_router(build_critical_behavior_requirement_router(session_factory))
    app.include_router(build_budget_policy_router(session_factory))
    app.include_router(build_cost_profile_router(session_factory))
    return app


# Run with `uvicorn app.main:create_app --factory` so settings are only
# resolved (and can only fail fast) at actual process startup, never as a
# side effect of importing this module from a test or type-checker.
