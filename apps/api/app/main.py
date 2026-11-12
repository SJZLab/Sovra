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
`app.costs.cost_profile_routes.build_cost_profile_router` -- the eighth,
and (FTF-1001) the real
`/api/v1/organizations/{organization_id}/projects/{project_id}/model-versions`
router from `app.model_versions.model_version_routes.build_model_version_router`
-- the ninth, and (FTF-0500) the real
`/api/v1/organizations/{organization_id}/projects/{project_id}/dataset-sources`
router from
`app.datasets.dataset_source_connection_routes.build_dataset_source_connection_router`
-- the tenth, and (FTF-R002) the real
`/api/v1/organizations/{organization_id}/component-bindings` and
`/api/v1/organizations/{organization_id}/component-status` routes from
`app.component_bindings.component_binding_routes.build_component_binding_router`
-- the eleventh (FTF-04A01's own `target-connections`/`target-snapshots`
routers landed between the tenth and eleventh, see their own module
docstrings), and (FTF-04A17) the real, snapshot-scoped
`/api/v1/organizations/{organization_id}/projects/{project_id}/
target-connections/{connection_id}/snapshots/{snapshot_id}/candidates`
router from
`app.target_connectors.discovered_agent_candidate_routes.build_discovered_agent_candidate_router`
-- the twelfth, and (FTF-04A03) the real
`/api/v1/organizations/{organization_id}/projects/{project_id}/
target-connections/github-repositories` router from
`app.target_connectors.github_repository_listing_routes.build_github_repository_listing_router`
-- the thirteenth, and (FTF-04A04) the real
`/api/v1/organizations/{organization_id}/projects/{project_id}/
target-connections/github-branches` and `.../github-resolve-ref` routers
from
`app.target_connectors.github_branch_ref_routes.build_github_branch_ref_router`
-- the fourteenth, and (FTF-04A05) the real
`/api/v1/organizations/{organization_id}/projects/{project_id}/
target-connections/{connection_id}/github-source-snapshot` router from
`app.target_connectors.github_source_snapshot_routes.build_github_source_snapshot_router`
-- the fifteenth. Six further routers (FTF-R002's `component-bindings`,
FTF-04A19's `manifests`, FTF-04A20's `manifests/{manifest_id}/evidence`,
FTF-0601's `evaluation-suites`, and now (FTF-04A18) the real
`.../candidates/{candidate_id}/select` action route from
`app.target_connectors.discovered_agent_candidate_selection_routes.
build_discovered_agent_candidate_selection_router`) have landed since --
see each router's own module docstring rather than this enumeration,
which this task does not attempt to keep perfectly renumbered.

Secret resolution gap (disclosed, not hidden -- same class of gap FTF-0005
and FTF-R017 already carry): `OidcSettings` only carries *references*
(`vault://...`) for the OIDC client secret and the session-signing key.
This module still resolves them from two directly-injected environment
variables (`SOVRA_OIDC_CLIENT_SECRET_VALUE`, `SOVRA_SESSION_SIGNING_KEY_VALUE`)
rather than from the `*_secret_ref` values themselves -- FTF-0202's own
OpenBao integration (`packages/litellm_client/secret_manager_integration.py`,
now CERTIFIED) exists, but wiring the OIDC/session bootstrap secrets
through it is this module's own separate, not-yet-claimed follow-up, not
this task's. FTF-0500's own dataset-source-connection router below *is*
this repository's first real caller of that integration (a credential/
config value supplied at connection-creation time is written for real to
OpenBao; see that router's own module docstring) -- this is a real,
tracked gap for whichever task first wires the OIDC bootstrap path through
it too, not a production mock of the OIDC/session logic itself, which is
fully real from this point on.

FTF-0201's own CRUD routes never need that same secret-resolution step
(see `app/litellm/connection_schema.py`'s own module docstring): they
only store/validate a connection's `secret_ref` shape, never resolve it
to a value. This router's own `/validate` route (FTF-0203, added later
on the same router) is different -- it is this repository's first real
caller of `packages/litellm_client/secret_manager_integration.py`'s
`resolve_secret_ref` for the LiteLLM connection family, using the same
lazy `secret_manager_config_factory` convention FTF-0500's dataset-source
router below established (`OpenBaoConfig.from_env`, called fresh per
request, `None` when unset rather than a boot-time failure).
What this router does need is a real database engine: `SOVRA_DATABASE_URL` (FTF-0005's
own `Settings.from_env()`) is trusted here to already be a real connection
string for the restricted `sovra_app` role in a real deployment -- the
same migration-owner/app-role operational split every integration test in
this repository already assumes (see e.g.
`tests/integration/test_ftf_0106_rbac_idor_security.py`'s own
`_app_role_url` helper); migrations run as a separate, more-privileged
process/step, never through this running app. `install_tenant_pool_reset_guard`
(FTF-0105) is installed on that engine as the same connection-pool-reuse
defense-in-depth every tenant-scoped table in this repository already
relies on.

FTF-0103A wires the live auth path: ONE real `SessionPrincipalResolver`
(reads and verifies FTF-0101's session cookie, including revocation) and
ONE real DB-backed `role_lookup` (the `memberships` table for the
path-resolved organization) are passed to EVERY router below, and the same
store backs FTF-0101's login-time `membership_lookup` so a revoked membership
is denied at login. The login router and the resolver share one
`SessionRevocationStore`, so `/auth/logout` revokes the session for the API
too. A test (`tests/tasks/test_ftf_0103a.py`) fails if any /api/v1 route
keeps a default resolver or role lookup.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.artifacts.artifact_routes import build_trainable_artifact_router
from app.auth.membership_store import build_membership_status_lookup, build_role_lookup
from app.auth.oidc_login import SessionRevocationStore, build_oidc_login_router
from app.auth.oidc_settings import OidcSettings
from app.auth.session_principal import SessionPrincipalResolver
from app.auth.tenant_isolation_query_guards import install_tenant_pool_reset_guard
from app.budget.budget_policy_routes import build_budget_policy_router
from app.component_bindings.component_binding_routes import build_component_binding_router
from app.config.configuration_system import Settings
from app.costs.cost_profile_routes import build_cost_profile_router
from app.datasets.dataset_quality_routes import build_dataset_quality_router
from app.datasets.dataset_routes import build_dataset_router
from app.datasets.dataset_source_connection_routes import (
    build_dataset_source_connection_router,
)
from app.datasets.dataset_split_manifest_routes import build_dataset_split_manifest_router
from app.datasets.direct_object_storage_upload_routes import build_direct_upload_router
from app.evaluation.evaluation_case_routes import build_evaluation_case_router
from app.evaluation.evaluation_suite_routes import build_evaluation_suite_router
from app.litellm.connection_routes import build_litellm_connection_router
from app.litellm.model_capability_routes import build_model_capability_router
from app.litellm.model_catalog_mapping_routes import build_model_catalog_mapping_router
from app.litellm.model_catalog_routes import build_model_catalog_router
from app.log_redaction_setup import install_api_log_redaction
from app.model_versions.model_import_routes import build_model_import_router
from app.model_versions.model_role_routes import build_model_role_router
from app.model_versions.model_version_routes import build_model_version_router
from app.projects.project_routes import build_project_router
from app.target_connectors.agent_manifest_routes import build_agent_manifest_router
from app.target_connectors.discovered_agent_candidate_routes import (
    build_discovered_agent_candidate_router,
)
from app.target_connectors.discovered_agent_candidate_selection_routes import (
    build_discovered_agent_candidate_selection_router,
)
from app.target_connectors.github_branch_ref_routes import build_github_branch_ref_router
from app.target_connectors.github_repository_listing_routes import (
    build_github_repository_listing_router,
)
from app.target_connectors.github_source_snapshot_routes import (
    build_github_source_snapshot_router,
)
from app.target_connectors.manifest_evidence_routes import build_manifest_evidence_router
from app.target_connectors.target_application_connection_routes import (
    build_target_application_connection_router,
)
from app.target_connectors.target_source_snapshot_routes import (
    build_target_source_snapshot_router,
)
from app.tools.critical_behavior_requirement_routes import (
    build_critical_behavior_requirement_router,
)
from app.tools.tool_definition_routes import build_tool_definition_router


def create_app() -> FastAPI:
    # FTF-0904-W1: redact every log record (incl. uvicorn's access log) before
    # anything else can log a request line, a secret setting or a traceback.
    install_api_log_redaction()
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
    revocations = SessionRevocationStore()
    # FTF-0103A: the one real resolver + role lookup every router receives.
    principal_resolver = SessionPrincipalResolver(
        signing_key=signing_key,
        max_age_seconds=settings.session_ttl_seconds,
        revocations=revocations,
    )
    role_lookup = build_role_lookup(session_factory)
    app.include_router(
        build_oidc_login_router(
            settings,
            client_secret=client_secret,
            signing_key=signing_key,
            membership_lookup=build_membership_status_lookup(session_factory),
            revocations=revocations,
        )
    )
    app.include_router(
        build_litellm_connection_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_model_catalog_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_model_catalog_mapping_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_model_capability_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_trainable_artifact_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_project_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_tool_definition_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_critical_behavior_requirement_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_budget_policy_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_cost_profile_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_model_version_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_model_import_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_model_role_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_dataset_source_connection_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_dataset_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_dataset_quality_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_dataset_split_manifest_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_direct_upload_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_target_application_connection_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_github_repository_listing_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_github_branch_ref_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_target_source_snapshot_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_github_source_snapshot_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_discovered_agent_candidate_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_component_binding_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_agent_manifest_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_manifest_evidence_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_evaluation_suite_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_evaluation_case_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_discovered_agent_candidate_selection_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    return app


# Run with `uvicorn app.main:create_app --factory` so settings are only
# resolved (and can only fail fast) at actual process startup, never as a
# side effect of importing this module from a test or type-checker.
