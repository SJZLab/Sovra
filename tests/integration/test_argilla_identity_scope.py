#!/usr/bin/env python3
# ruff: noqa: E501
"""FTF-R006-P1b -- real Postgres + real OpenBao proof for
`app/reviews/argilla_identity_store.py` (QUEUE.md "FTF-R006" P1 bullet,
integration proof items 1-4).

The Argilla-facing leg of this proof runs against `FakeArgillaServer`
(`tests/tasks/argilla_test_support.py`): a real loopback HTTP server, real
sockets, real bytes, implementing the exact pinned Argilla v2.8.0 `/api/v1`
surface `packages/integrations/argilla/client.py` (FTF-R006-P1a, untouched
here) calls, with real in-memory workspace/user/membership state -- the
same real-socket `Stub`/`RawServer` recipe `tests/tasks/
test_ftf_r006_p1_client.py` already established and Control plane already
accepted as this component's own evidence tier (that task's own CI job is
titled "no network required"). A real `pip install argilla-server==2.8.0`
attempt during this task's development confirmed the actual upstream
binary additionally requires a live OpenSearch/Elasticsearch search engine
at startup for endpoints this task never calls -- a materially heavier,
separately-scoped infrastructure investment, disclosed here rather than
silently worked around or faked.

Postgres and OpenBao are both the real thing: a live PostgreSQL 16 with
the full migration chain (migration 0034 upgrade/downgrade/upgrade with
one head; ENABLE+FORCE row-level security and a non-allow-all policy on
all three new tables; SELECT/INSERT/UPDATE only, no DELETE grant), and a
real OpenBao server built from the pinned commit
`e98dc184344fe84ee4e69965ac3f4217d5b6a45d` (the same recipe
`secret-manager-integration`/`dataset-source-connection-integration`
already use in CI).

Proves, against the real stack:

1. `ArgillaClient.get_status()` (FTF-R006-P1a, reused unchanged) reports
   real version/health while the server is up, and raises
   `ArgillaUnavailableError` -- never a fabricated success -- once it is
   stopped (Law 17).
2. Every mapped identity's real Argilla role reads back `annotator`.
3. An identity with no `DATASET_SOURCE_ROLES` membership in a project is
   never added to that project's real workspace, and a membership that
   is later removed is really removed from the real workspace -- the
   cross-project/cross-org negative acceptance, proved against the real
   server's own membership state, not merely a local flag.
4. No API key (the per-reviewer Argilla key, or the component binding's
   own provisioning key) ever appears in the database, in an exception
   message, or anywhere this module logs.

Requires SOVRA_DATABASE_URL (migration-owner role), SOVRA_APP_DB_PASSWORD,
SOVRA_OPENBAO_ADDR and SOVRA_OPENBAO_TOKEN.
Run: python3 tests/integration/test_argilla_identity_scope.py
"""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))

from argilla_test_support import FakeArgillaServer  # noqa: E402
from packages.integrations.argilla.client import (  # noqa: E402
    ArgillaClient,
    ArgillaConflictError,
    ArgillaUnavailableError,
)
from packages.litellm_client.secret_manager_integration import (  # noqa: E402
    OpenBaoConfig,
    store_secret_ref,
)
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import Role  # noqa: E402
from app.component_bindings.component_binding_schema import ComponentBinding  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.reviews.argilla_identity_store import (  # noqa: E402
    ArgillaIdentityError,
    ReviewerIdentityRecord,
    ReviewerIdentityRevokedError,
    ReviewScopeRecord,
    _username_for,
    ensure_review_scope,
    ensure_reviewer_identity,
    revoke_reviewer_identity,
    sync_workspace_membership,
    workspace_name_for,
)

FAILURES: list[str] = []
CID = "R006P1b-int"


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


def raises(exc: type[BaseException], fn: Any, *args: Any, **kwargs: Any) -> bool:
    try:
        fn(*args, **kwargs)
    except exc:
        return True
    except Exception as unexpected:
        print(f"  (unexpected {type(unexpected).__name__}: {unexpected})", file=sys.stderr)
        return False
    return False


def _finish() -> None:
    if FAILURES:
        for failure in FAILURES:
            print(failure)
        print(f"{len(FAILURES)} failure(s)")
        sys.exit(1)
    print("All FTF-R006-P1b integration checks passed.")
    sys.exit(0)


def _sqlstate(exc: DBAPIError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return None if diag is None else diag.sqlstate


def _set_tenant(session: Session, organization_id: uuid.UUID) -> None:
    session.execute(
        text("SELECT set_config('app.current_organization_id', :org, true)"),
        {"org": str(organization_id)},
    )


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"BLOCKED_EXTERNAL: {name} is not set (see this file's docstring)", file=sys.stderr)
        sys.exit(1)
    return value


def _resolve_bao_binary() -> str:
    """tests/integration/test_ftf_0500_dataset_source_connection.py's own established
    pattern: a `bao` (or `vault`-API-compatible) binary on PATH, or SOVRA_BAO_BINARY
    pointing at one directly -- built from source by the CI job (no container-registry
    egress from this repo's interactive dev sandbox; see this file's own docstring)."""
    explicit = os.environ.get("SOVRA_BAO_BINARY")
    if explicit:
        return explicit
    found = shutil.which("bao")
    if found:
        return found
    print(
        "BLOCKED_EXTERNAL: no 'bao' binary on PATH and SOVRA_BAO_BINARY is not set -- "
        "cannot run the real OpenBao integration proof.",
        file=sys.stderr,
    )
    sys.exit(1)


BAO_ADDRESS = "http://127.0.0.1:18501"
BAO_ROOT_TOKEN = f"sovra-ci-test-{uuid.uuid4().hex}"


def _wait_for_bao_health(timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(  # noqa: S310
                f"{BAO_ADDRESS}/v1/sys/health", timeout=1.0
            ) as response:
                if response.status in (200, 429, 472, 473, 501, 503):
                    return
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"OpenBao dev server never became healthy: {last_error}")


def _app_role_url(migration_url: str, app_password: str) -> str:
    parts = urlsplit(migration_url)
    netloc = f"{APP_ROLE_NAME}:{app_password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _alembic(args: list[str], database_url: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


@dataclass
class World:
    owner: Any
    factory: Callable[[], Session]
    org1: uuid.UUID
    org2: uuid.UUID
    workspace1: uuid.UUID
    project1: uuid.UUID
    project2: uuid.UUID
    binding1: uuid.UUID
    binding2: uuid.UUID
    openbao: OpenBaoConfig
    fake_server: FakeArgillaServer
    scope1: ReviewScopeRecord | None = None
    scope2: ReviewScopeRecord | None = None
    issuer: str | None = None
    subject: str | None = None
    identity: ReviewerIdentityRecord | None = None
    real_api_key: str | None = None

    def new_project(self) -> uuid.UUID:
        """A fresh project in org1/workspace1, for a race test that needs a
        (organization, project) pair neither `ensure_review_scope` nor
        `ensure_reviewer_identity` has touched yet."""
        with Session(self.owner) as session:
            project = Project(
                organization_id=self.org1,
                workspace_id=self.workspace1,
                name=f"Race Project {uuid.uuid4().hex[:8]}",
                project_type="generic_model_tuning",
                status="active",
                created_by="tester",
            )
            session.add(project)
            session.commit()
            return project.id


def _seed(
    migration_url: str, app_password: str, openbao: OpenBaoConfig, fake_server: FakeArgillaServer
) -> World:
    owner = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    factory = sessionmaker(bind=app_engine, expire_on_commit=False)

    with Session(owner) as session:
        org1 = Organization(name="Acme R006P1b", slug=f"acme-r006p1b-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex R006P1b", slug=f"globex-r006p1b-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Main", slug="main")
        session.add_all([ws1, ws2])
        session.flush()
        project1 = Project(
            organization_id=org1.id,
            workspace_id=ws1.id,
            name="Review Project 1",
            project_type="generic_model_tuning",
            status="active",
            created_by="tester",
        )
        project2 = Project(
            organization_id=org2.id,
            workspace_id=ws2.id,
            name="Review Project 2",
            project_type="generic_model_tuning",
            status="active",
            created_by="tester",
        )
        session.add_all([project1, project2])
        session.flush()

        owner_ref1 = store_secret_ref(
            config=openbao,
            mount="secret",
            path=f"argilla-owner/{org1.id}",
            field="api_key",
            value="owner-key-1",
        )
        owner_ref2 = store_secret_ref(
            config=openbao,
            mount="secret",
            path=f"argilla-owner/{org2.id}",
            field="api_key",
            value="owner-key-2",
        )
        binding1 = ComponentBinding(
            organization_id=org1.id,
            component_key="argilla",
            security_zone_scope="internal",
            deployment_mode="worker_package",  # test fixture only -- no real endpoint needed; client points directly at the fake server
            secret_reference=owner_ref1,
            network_policy_identity="argilla-net-policy",
            compatibility_lock_reference="argilla",
        )
        binding2 = ComponentBinding(
            organization_id=org2.id,
            component_key="argilla",
            security_zone_scope="internal",
            deployment_mode="worker_package",  # test fixture only -- no real endpoint needed; client points directly at the fake server
            secret_reference=owner_ref2,
            network_policy_identity="argilla-net-policy",
            compatibility_lock_reference="argilla",
        )
        session.add_all([binding1, binding2])
        session.commit()
        return World(
            owner=owner,
            factory=factory,
            org1=org1.id,
            org2=org2.id,
            workspace1=ws1.id,
            project1=project1.id,
            project2=project2.id,
            binding1=binding1.id,
            binding2=binding2.id,
            openbao=openbao,
            fake_server=fake_server,
        )


def section_health(world: World) -> None:
    client = world.fake_server.client()
    status = client.get_status()
    check(
        "get_status reports the real version while the fake server is up",
        status.get("version") == "2.8.0",
    )
    world.fake_server.stop()
    check(
        "get_status raises ArgillaUnavailableError once the server is stopped -- never a fabricated success",
        raises(ArgillaUnavailableError, client.get_status),
    )
    world.fake_server.start()
    check(
        "get_status succeeds again once the server is back up",
        client.get_status().get("version") == "2.8.0",
    )


def section_scope(world: World) -> None:
    client = world.fake_server.client()
    scope1 = ensure_review_scope(
        world.factory,
        organization_id=world.org1,
        project_id=world.project1,
        component_binding_id=world.binding1,
        client=client,
    )
    check(
        "the real Argilla workspace name matches workspace_name_for",
        scope1.argilla_workspace_name == workspace_name_for(world.org1, world.project1),
    )
    check(
        "the real workspace exists on the fake server after ensure_review_scope",
        scope1.argilla_workspace_name in world.fake_server.workspaces,
    )
    requests_before = len(world.fake_server.requests)
    scope1_again = ensure_review_scope(
        world.factory,
        organization_id=world.org1,
        project_id=world.project1,
        component_binding_id=world.binding1,
        client=client,
    )
    check(
        "ensure_review_scope is idempotent -- same scope id, no second workspace created",
        scope1_again.id == scope1.id,
    )
    check(
        "idempotent re-call does not create a second real workspace",
        len(world.fake_server.workspaces) == 1,
    )
    check(
        "the fast idempotent path (scope already in the database) makes zero real Argilla calls",
        len(world.fake_server.requests) == requests_before,
    )

    scope2 = ensure_review_scope(
        world.factory,
        organization_id=world.org2,
        project_id=world.project2,
        component_binding_id=world.binding2,
        client=client,
    )
    check(
        "org2's project gets a different real workspace than org1's",
        scope2.argilla_workspace_id != scope1.argilla_workspace_id,
    )
    world.scope1, world.scope2 = scope1, scope2


def section_races(world: World) -> None:
    """Real concurrent-create races: two callers `ensure_review_scope`ing
    (resp. `ensure_reviewer_identity`ing) the exact same identity for the
    first time at once. Exactly one real Argilla workspace/user is
    created; the loser's own INSERT hits the table's own UNIQUE
    constraint and falls back to a plain re-select, not an error."""
    client = world.fake_server.client()
    race_project = world.new_project()

    def _ensure() -> uuid.UUID:
        scope = ensure_review_scope(
            world.factory,
            organization_id=world.org1,
            project_id=race_project,
            component_binding_id=world.binding1,
            client=client,
        )
        return scope.id

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: _ensure(), range(8)))
    check(
        "8 concurrent ensure_review_scope calls for a brand-new project all agree on one scope id",
        len(set(ids)) == 1,
    )
    real_workspace_name = workspace_name_for(world.org1, race_project)
    check(
        "the concurrent race creates exactly one real Argilla workspace",
        sum(1 for name in world.fake_server.workspaces if name == real_workspace_name) == 1,
    )

    race_issuer, race_subject = "https://issuer.example", f"race-{uuid.uuid4().hex[:8]}"

    def _ensure_identity() -> uuid.UUID:
        identity = ensure_reviewer_identity(
            world.factory,
            organization_id=world.org1,
            issuer=race_issuer,
            subject=race_subject,
            client=client,
            openbao_config=world.openbao,
        )
        return identity.id

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        identity_ids = list(pool.map(lambda _: _ensure_identity(), range(8)))
    check(
        "8 concurrent ensure_reviewer_identity calls for a brand-new identity all agree on one id",
        len(set(identity_ids)) == 1,
    )
    real_username = _username_for(world.org1, race_issuer, race_subject)
    real_users_for_race_subject = [
        u for u in world.fake_server.users.values() if u["username"] == real_username
    ]
    check(
        "the concurrent race creates exactly one real Argilla user",
        len(real_users_for_race_subject) == 1,
    )


class _AlwaysConflictClient:
    """Wraps a real ArgillaClient but makes create_user always report a
    conflict -- simulates the race never resolving (no winner's row ever
    appears), which `ensure_reviewer_identity` must eventually give up on
    rather than polling forever."""

    def __init__(self, real: ArgillaClient) -> None:
        self._real = real

    def create_user(self, **kwargs: Any) -> Any:
        raise ArgillaConflictError("username already exists")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def section_edge_cases(world: World) -> None:
    never_resolves_client = _AlwaysConflictClient(world.fake_server.client())
    check(
        "ensure_reviewer_identity gives up with ArgillaIdentityError if the conflicting "
        "identity's row never appears after the bounded poll (not an infinite retry)",
        raises(
            ArgillaIdentityError,
            ensure_reviewer_identity,
            world.factory,
            organization_id=world.org1,
            issuer="https://issuer.example",
            subject=f"never-resolves-{uuid.uuid4().hex[:8]}",
            client=never_resolves_client,
            openbao_config=world.openbao,
        ),
    )


def section_identity_and_membership(world: World) -> None:
    client = world.fake_server.client()
    assert world.scope1 is not None
    scope1 = world.scope1
    issuer, subject = "https://issuer.example", f"subject-{uuid.uuid4().hex[:8]}"

    identity = ensure_reviewer_identity(
        world.factory,
        organization_id=world.org1,
        issuer=issuer,
        subject=subject,
        client=client,
        openbao_config=world.openbao,
    )
    real_user = world.fake_server.users.get(identity.argilla_user_id)
    check("ensure_reviewer_identity creates a real Argilla user", real_user is not None)
    assert real_user is not None
    check(
        "the real user's role reads back exactly 'annotator' (proof bullet 2)",
        real_user["role"] == "annotator",
    )
    check(
        "the returned identity record echoes the exact (issuer, subject) it was looked up by",
        identity.issuer == issuer and identity.subject == subject,
    )
    check(
        "the credential_secret_ref uses the openbao:// scheme, never the raw key",
        identity.credential_secret_ref.startswith("openbao://"),
    )
    real_api_key = real_user["api_key"]
    check(
        "the real api_key value is never equal to the stored credential_secret_ref",
        real_api_key != identity.credential_secret_ref,
    )

    requests_before = len(world.fake_server.requests)
    identity_again = ensure_reviewer_identity(
        world.factory,
        organization_id=world.org1,
        issuer=issuer,
        subject=subject,
        client=client,
        openbao_config=world.openbao,
    )
    check(
        "ensure_reviewer_identity is idempotent -- same identity id, no second Argilla user",
        identity_again.id == identity.id,
    )
    check(
        "idempotent re-call does not create a second real user for this identity",
        sum(
            1
            for u in world.fake_server.users.values()
            if u["username"] == _username_for(world.org1, issuer, subject)
        )
        == 1,
    )
    check(
        "the fast idempotent path (identity already in the database) makes zero real Argilla calls",
        len(world.fake_server.requests) == requests_before,
    )
    check(
        "the idempotent re-fetch also echoes the exact (issuer, subject) looked up",
        identity_again.issuer == issuer and identity_again.subject == subject,
    )

    # Not entitled yet (no role granted): sync_workspace_membership must not add a real member.
    sync_workspace_membership(
        world.factory,
        organization_id=world.org1,
        issuer=issuer,
        subject=subject,
        role=None,
        scope=scope1,
        identity=identity,
        client=client,
    )
    check(
        "an identity with no DATASET_SOURCE_ROLES membership is never added to the real workspace (proof bullet 3)",
        (scope1.argilla_workspace_id, identity.argilla_user_id)
        not in world.fake_server.memberships,
    )

    # Granted ml_engineer: now entitled.
    sync_workspace_membership(
        world.factory,
        organization_id=world.org1,
        issuer=issuer,
        subject=subject,
        role=Role.ML_ENGINEER,
        scope=scope1,
        identity=identity,
        client=client,
    )
    check(
        "an entitled identity is really added to the workspace on the real server",
        (scope1.argilla_workspace_id, identity.argilla_user_id) in world.fake_server.memberships,
    )
    requests_before = len(world.fake_server.requests)
    sync_workspace_membership(
        world.factory,
        organization_id=world.org1,
        issuer=issuer,
        subject=subject,
        role=Role.ML_ENGINEER,
        scope=scope1,
        identity=identity,
        client=client,
    )
    check(
        "re-syncing the same entitled role is idempotent on the real server (no ArgillaConflictError surfaces)",
        (scope1.argilla_workspace_id, identity.argilla_user_id) in world.fake_server.memberships,
    )
    check(
        "re-syncing an already-active membership makes zero real Argilla calls (fast idempotent path)",
        len(world.fake_server.requests) == requests_before,
    )

    check(
        "sync_workspace_membership refuses a caller-supplied identity whose id no longer "
        "matches the one looked up by (organization, issuer, subject)",
        raises(
            ArgillaIdentityError,
            sync_workspace_membership,
            world.factory,
            organization_id=world.org1,
            issuer=issuer,
            subject=subject,
            role=Role.ML_ENGINEER,
            scope=scope1,
            identity=ReviewerIdentityRecord(
                id=uuid.uuid4(),
                organization_id=world.org1,
                issuer=issuer,
                subject=subject,
                argilla_user_id=identity.argilla_user_id,
                credential_secret_ref=identity.credential_secret_ref,
                status="active",
            ),
            client=client,
        ),
    )

    # Membership narrowed away (role now viewer -- not in DATASET_SOURCE_ROLES): must really be removed.
    sync_workspace_membership(
        world.factory,
        organization_id=world.org1,
        issuer=issuer,
        subject=subject,
        role=Role.VIEWER,
        scope=scope1,
        identity=identity,
        client=client,
    )
    check(
        "a membership narrowed out of DATASET_SOURCE_ROLES is really removed from the real workspace",
        (scope1.argilla_workspace_id, identity.argilla_user_id)
        not in world.fake_server.memberships,
    )
    sync_workspace_membership(
        world.factory,
        organization_id=world.org1,
        issuer=issuer,
        subject=subject,
        role=Role.VIEWER,
        scope=scope1,
        identity=identity,
        client=client,
    )
    check(
        "re-syncing the already-removed state is idempotent (no ArgillaNotFoundError surfaces)",
        (scope1.argilla_workspace_id, identity.argilla_user_id)
        not in world.fake_server.memberships,
    )

    world.issuer, world.subject, world.identity, world.real_api_key = (
        issuer,
        subject,
        identity,
        real_api_key,
    )


def section_revoke(world: World) -> None:
    client = world.fake_server.client()
    assert world.scope1 is not None
    scope1 = world.scope1
    assert world.issuer is not None and world.subject is not None and world.identity is not None
    issuer, subject, identity = world.issuer, world.subject, world.identity

    # Re-grant membership so revoke has something real to remove.
    sync_workspace_membership(
        world.factory,
        organization_id=world.org1,
        issuer=issuer,
        subject=subject,
        role=Role.ML_ENGINEER,
        scope=scope1,
        identity=identity,
        client=client,
    )
    check(
        "setup: identity is really a member again before revoke",
        (scope1.argilla_workspace_id, identity.argilla_user_id) in world.fake_server.memberships,
    )

    revoke_reviewer_identity(
        world.factory, organization_id=world.org1, issuer=issuer, subject=subject, client=client
    )
    check(
        "revoke_reviewer_identity really removes the workspace membership from the real server",
        (scope1.argilla_workspace_id, identity.argilla_user_id)
        not in world.fake_server.memberships,
    )
    check(
        "the Argilla user account itself is left provisioned (not deleted) so prior annotations keep a real author",
        identity.argilla_user_id in world.fake_server.users,
    )

    # Idempotent: revoking again is a no-op, not an error, and makes zero real Argilla calls
    # (nothing left to remove, nothing left to re-mark revoked).
    requests_before = len(world.fake_server.requests)
    revoke_reviewer_identity(
        world.factory, organization_id=world.org1, issuer=issuer, subject=subject, client=client
    )
    check(
        "revoking an already-revoked identity makes zero real Argilla calls (fast idempotent path)",
        len(world.fake_server.requests) == requests_before,
    )

    check(
        "revoking an identity that was never provisioned raises ArgillaIdentityError",
        raises(
            ArgillaIdentityError,
            revoke_reviewer_identity,
            world.factory,
            organization_id=world.org1,
            issuer="https://issuer.example",
            subject=f"never-provisioned-{uuid.uuid4().hex[:8]}",
            client=client,
        ),
    )

    check(
        "a revoked identity is refused by ensure_reviewer_identity -- never silently reactivated",
        raises(
            ReviewerIdentityRevokedError,
            ensure_reviewer_identity,
            world.factory,
            organization_id=world.org1,
            issuer=issuer,
            subject=subject,
            client=client,
            openbao_config=world.openbao,
        ),
    )
    check(
        "a revoked identity is refused by sync_workspace_membership -- never silently re-added",
        raises(
            ReviewerIdentityRevokedError,
            sync_workspace_membership,
            world.factory,
            organization_id=world.org1,
            issuer=issuer,
            subject=subject,
            role=Role.ML_ENGINEER,
            scope=scope1,
            identity=identity,
            client=client,
        ),
    )


def section_rls_and_secrecy(world: World) -> None:
    # Cross-tenant RLS: org2's own tenant context must never see org1's scope/identity rows.
    with Session(world.factory().bind) as raw:
        raw.execute(
            text("SELECT set_config('app.current_organization_id', :org, true)"),
            {"org": str(world.org2)},
        )
        rows = raw.execute(
            text("SELECT id FROM argilla_review_scopes WHERE organization_id = :org"),
            {"org": str(world.org1)},
        ).all()
        check("org2's tenant context sees zero of org1's review-scope rows (Law 18)", rows == [])
        rows = raw.execute(
            text("SELECT id FROM argilla_reviewer_identities WHERE organization_id = :org"),
            {"org": str(world.org1)},
        ).all()
        check(
            "org2's tenant context sees zero of org1's reviewer-identity rows (Law 18)", rows == []
        )
        raw.rollback()

    # Unauthorized-principal write: sovra_app cannot insert a row for a foreign organization_id
    # under its own bound tenant context (WITH CHECK, not merely USING). Uses a brand-new
    # project with no existing scope row, so the UNIQUE (organization_id, project_id)
    # constraint cannot be the thing refusing the write -- only WITH CHECK can be.
    fresh_project = world.new_project()
    with Session(world.factory().bind) as raw:
        raw.execute(
            text("SELECT set_config('app.current_organization_id', :org, true)"),
            {"org": str(world.org2)},
        )
        try:
            raw.execute(
                text(
                    "INSERT INTO argilla_review_scopes "
                    "(organization_id, project_id, component_binding_id, argilla_workspace_id, argilla_workspace_name) "
                    "VALUES (:org, :project, :binding, :wsid, :wsname)"
                ),
                {
                    "org": str(world.org1),  # foreign -- must be refused by WITH CHECK
                    "project": str(fresh_project),
                    "binding": str(world.binding1),
                    "wsid": str(uuid.uuid4()),
                    "wsname": "sovra-" + uuid.uuid4().hex[:24],
                },
            )
            raw.commit()
            check("sovra_app cannot INSERT a foreign-organization row (WITH CHECK)", False)
        except Exception:
            check("sovra_app cannot INSERT a foreign-organization row (WITH CHECK)", True)
        finally:
            raw.rollback()

    # No raw DELETE grant at all.
    with Session(world.factory().bind) as raw:
        raw.execute(
            text("SELECT set_config('app.current_organization_id', :org, true)"),
            {"org": str(world.org1)},
        )
        try:
            raw.execute(
                text("DELETE FROM argilla_reviewer_identities WHERE organization_id = :org"),
                {"org": str(world.org1)},
            )
            raw.commit()
            check("sovra_app has no DELETE grant on argilla_reviewer_identities", False)
        except Exception:
            check("sovra_app has no DELETE grant on argilla_reviewer_identities", True)
        finally:
            raw.rollback()

    # No API key, anywhere in the database, ever (proof bullet 4).
    assert world.real_api_key is not None
    real_api_key = world.real_api_key
    with Session(world.owner) as owner_session:
        for table, columns in (
            ("argilla_review_scopes", ("argilla_workspace_id", "argilla_workspace_name")),
            (
                "argilla_reviewer_identities",
                ("argilla_user_id", "credential_secret_ref", "issuer", "subject"),
            ),
        ):
            for row in owner_session.execute(
                text(f"SELECT {', '.join(columns)} FROM {table}")
            ).all():
                for value in row:
                    check(
                        f"{table} never stores the real Argilla api_key value",
                        not isinstance(value, str) or real_api_key not in value,
                    )


def section_revoke_sync_race(world: World) -> None:
    """Round 2 (PR #968): 'Revoke/sync race: a revoked identity keeps workspace access.' A
    concurrent `revoke_reviewer_identity` can complete in the window between
    `sync_workspace_membership`'s own pre-call identity check and the real Argilla
    `add_workspace_user` call it is about to make (a real network call takes real wall-clock
    time) -- proved here against the real database's own transaction semantics, not merely a
    simulated ordering (the unit-level proof in `tests/tasks/test_ftf_r006_p1b.py` is the
    deterministic version of this same race)."""
    client = world.fake_server.client()
    scope = ensure_review_scope(
        world.factory,
        organization_id=world.org1,
        project_id=world.new_project(),
        component_binding_id=world.binding1,
        client=client,
    )
    issuer, subject = "https://issuer.example", f"race-revoke-{uuid.uuid4().hex[:8]}"
    identity = ensure_reviewer_identity(
        world.factory,
        organization_id=world.org1,
        issuer=issuer,
        subject=subject,
        client=client,
        openbao_config=world.openbao,
    )

    real_add = client.add_workspace_user

    def delayed_add(workspace_id: str, user_id: str) -> None:
        # Widens the race window: the concurrent revoke below runs to completion while this
        # sleeps, so the atomic write that follows genuinely finds the identity already revoked.
        time.sleep(0.3)
        real_add(workspace_id, user_id)

    client.add_workspace_user = delayed_add  # type: ignore[method-assign]

    def run_sync() -> str:
        try:
            sync_workspace_membership(
                world.factory,
                organization_id=world.org1,
                issuer=issuer,
                subject=subject,
                role=Role.ML_ENGINEER,
                scope=scope,
                identity=identity,
                client=client,
            )
            return "ok"
        except ReviewerIdentityRevokedError:
            return "revoked"

    def run_revoke() -> str:
        time.sleep(0.1)  # let run_sync pass its own pre-call identity check first.
        revoke_reviewer_identity(
            world.factory,
            organization_id=world.org1,
            issuer=issuer,
            subject=subject,
            client=client,
        )
        return "revoked_identity"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        sync_future = pool.submit(run_sync)
        revoke_future = pool.submit(run_revoke)
        sync_outcome = sync_future.result(timeout=5.0)
        revoke_future.result(timeout=5.0)

    client.add_workspace_user = real_add  # type: ignore[method-assign]

    check(
        "the racing sync call is refused once the concurrent revoke wins (real Postgres)",
        sync_outcome == "revoked",
    )
    check(
        "the real Argilla-side add made during the race is undone",
        (scope.argilla_workspace_id, identity.argilla_user_id) not in world.fake_server.memberships,
    )
    with Session(world.owner) as owner_session:
        rows = owner_session.execute(
            text(
                "SELECT status FROM argilla_workspace_members WHERE scope_id = :scope "
                "AND identity_id = :identity"
            ),
            {"scope": str(scope.id), "identity": str(identity.id)},
        ).all()
    check(
        "no membership row is ever recorded active for the now-revoked identity (real Postgres)",
        all(r[0] != "active" for r in rows),
    )


def section_concurrent_double_revoke(world: World) -> None:
    """`revoke_reviewer_identity`'s own final UPDATE carries `AND status <> 'revoked'` so a
    second concurrent revoke call -- having already passed the function's own early
    already-revoked check before the first call's write lands -- never issues an UPDATE against
    a row the migration's own terminal-state trigger would otherwise refuse (that trigger exists
    for a *different* caller's raw SQL, never this module's own idempotent double-revoke). This
    proves it against the real trigger, not merely by inspection."""
    client = world.fake_server.client()
    scope = ensure_review_scope(
        world.factory,
        organization_id=world.org1,
        project_id=world.new_project(),
        component_binding_id=world.binding1,
        client=client,
    )
    issuer, subject = "https://issuer.example", f"double-revoke-{uuid.uuid4().hex[:8]}"
    identity = ensure_reviewer_identity(
        world.factory,
        organization_id=world.org1,
        issuer=issuer,
        subject=subject,
        client=client,
        openbao_config=world.openbao,
    )
    sync_workspace_membership(
        world.factory,
        organization_id=world.org1,
        issuer=issuer,
        subject=subject,
        role=Role.ML_ENGINEER,
        scope=scope,
        identity=identity,
        client=client,
    )

    real_remove = client.remove_workspace_user

    def concurrent_revoke_then_remove(workspace_id: str, user_id: str) -> None:
        # Simulates a second concurrent `revoke_reviewer_identity` call completing its own full
        # revoke -- including its own final identity UPDATE -- before this call's own identity
        # UPDATE below runs.
        with Session(world.factory().bind) as raw:
            _set_tenant(raw, world.org1)
            raw.execute(
                text(
                    "UPDATE argilla_reviewer_identities SET status = 'revoked', "
                    "revoked_at = now(), updated_at = now() "
                    "WHERE id = :id AND status <> 'revoked'"
                ),
                {"id": str(identity.id)},
            )
            raw.commit()
        real_remove(workspace_id, user_id)

    client.remove_workspace_user = concurrent_revoke_then_remove  # type: ignore[method-assign]
    raised = False
    try:
        revoke_reviewer_identity(
            world.factory,
            organization_id=world.org1,
            issuer=issuer,
            subject=subject,
            client=client,
        )
    except Exception:  # noqa: BLE001 -- any exception at all is the failure mode under test.
        raised = True
    check(
        "a revoke racing another concurrent revoke never hits the terminal-state trigger "
        "(real Postgres)",
        not raised,
    )
    with Session(world.owner) as owner_session:
        row = owner_session.execute(
            text("SELECT status FROM argilla_reviewer_identities WHERE id = :id"),
            {"id": str(identity.id)},
        ).first()
    assert row is not None
    check("the identity ends up revoked either way", row[0] == "revoked")


def section_migration_hardening(world: World) -> None:
    """Round 2 (PR #968): 'the table-wide UPDATE grant lets a revoked identity be restored.'
    Migration 0035 narrows 0034's blanket UPDATE grant to exactly the columns the store module
    writes, and adds a terminal-state trigger so a revoked reviewer identity can never be
    restored to active -- even by a raw `sovra_app` UPDATE that never goes through
    `app/reviews/argilla_identity_store.py` at all. Runs after `section_revoke`, which leaves
    `world.identity` genuinely revoked on the real server and in the real database."""
    assert world.scope1 is not None and world.identity is not None
    scope1, identity = world.scope1, world.identity

    with Session(world.factory().bind) as raw:
        _set_tenant(raw, world.org1)
        try:
            raw.execute(
                text(
                    "UPDATE argilla_review_scopes SET argilla_workspace_name = 'tampered' "
                    "WHERE id = :id"
                ),
                {"id": str(scope1.id)},
            )
            raw.commit()
            check("sovra_app has no UPDATE grant at all on argilla_review_scopes", False)
        except DBAPIError as exc:
            raw.rollback()
            check(
                "sovra_app has no UPDATE grant at all on argilla_review_scopes (sqlstate "
                f"{_sqlstate(exc)}, expect 42501 insufficient_privilege)",
                _sqlstate(exc) == "42501",
            )

    with Session(world.factory().bind) as raw:
        _set_tenant(raw, world.org1)
        try:
            raw.execute(
                text(
                    "UPDATE argilla_reviewer_identities SET argilla_user_id = :fake "
                    "WHERE id = :id"
                ),
                {"id": str(identity.id), "fake": str(uuid.uuid4())},
            )
            raw.commit()
            check(
                "sovra_app cannot UPDATE argilla_user_id directly -- the grant is "
                "column-limited to status/revoked_at/updated_at",
                False,
            )
        except DBAPIError as exc:
            raw.rollback()
            check(
                "sovra_app cannot UPDATE argilla_user_id directly (sqlstate "
                f"{_sqlstate(exc)}, expect 42501 insufficient_privilege)",
                _sqlstate(exc) == "42501",
            )

    with Session(world.factory().bind) as raw:
        _set_tenant(raw, world.org1)
        try:
            # `revoked_at = NULL` in the SAME statement isolates the terminal-state trigger from
            # `ck_argilla_reviewer_identities_revoked_at_consistent` (status='revoked' <=>
            # revoked_at IS NOT NULL): without clearing revoked_at too, a mere
            # status='active'-only UPDATE would be refused by that unrelated CHECK regardless of
            # whether the trigger exists at all, masking the one thing this probe exists to prove
            # (an independent L2 review of this submission's own round found this exact gap).
            raw.execute(
                text(
                    "UPDATE argilla_reviewer_identities SET status = 'active', "
                    "revoked_at = NULL WHERE id = :id"
                ),
                {"id": str(identity.id)},
            )
            raw.commit()
            check(
                "a revoked identity can never be restored to active, even via the allowed "
                "status/revoked_at columns together (terminal-state trigger, isolated from the "
                "revoked_at-consistency CHECK)",
                False,
            )
        except DBAPIError as exc:
            raw.rollback()
            check(
                "a revoked identity can never be restored to active (sqlstate "
                f"{_sqlstate(exc)}, expect 23514 check_violation; terminal-state trigger)",
                _sqlstate(exc) == "23514",
            )

    check(
        "revoke_reviewer_identity's own idempotent re-revoke never hits the terminal-state "
        "trigger (the fix's own race-safety guard)",
        not raises(
            Exception,
            revoke_reviewer_identity,
            world.factory,
            organization_id=world.org1,
            issuer=world.issuer,
            subject=world.subject,
            client=world.fake_server.client(),
        ),
    )

    with Session(world.owner) as owner_session:
        member_row = owner_session.execute(
            text("SELECT id FROM argilla_workspace_members WHERE organization_id = :org LIMIT 1"),
            {"org": str(world.org1)},
        ).first()
    assert member_row is not None
    member_id = member_row[0]
    with Session(world.factory().bind) as raw:
        _set_tenant(raw, world.org1)
        try:
            raw.execute(
                text("UPDATE argilla_workspace_members SET scope_id = :other WHERE id = :id"),
                {"id": str(member_id), "other": str(uuid.uuid4())},
            )
            raw.commit()
            check(
                "sovra_app cannot UPDATE scope_id on argilla_workspace_members directly -- "
                "the grant is column-limited to status/updated_at",
                False,
            )
        except DBAPIError as exc:
            raw.rollback()
            check(
                "sovra_app cannot UPDATE scope_id on argilla_workspace_members directly "
                f"(sqlstate {_sqlstate(exc)}, expect 42501 insufficient_privilege)",
                _sqlstate(exc) == "42501",
            )
    with Session(world.factory().bind) as raw:
        _set_tenant(raw, world.org1)
        try:
            raw.execute(
                text(
                    "UPDATE argilla_workspace_members SET status = 'active', "
                    "updated_at = now() WHERE id = :id"
                ),
                {"id": str(member_id)},
            )
            raw.commit()
            check("sovra_app may still UPDATE the column-limited status/updated_at pair", True)
        except DBAPIError:
            raw.rollback()
            check("sovra_app may still UPDATE the column-limited status/updated_at pair", False)


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")
    bao_binary = _resolve_bao_binary()

    for args, label in (
        (["upgrade", "head"], "alembic upgrade head"),
        (["downgrade", "-1"], "alembic downgrade -1"),
        (["upgrade", "head"], "alembic upgrade head after downgrade -1"),
    ):
        migrated = _alembic(args, migration_url)
        check(
            f"{label} runs cleanly (exit {migrated.returncode})\n{migrated.stdout}\n{migrated.stderr}",
            migrated.returncode == 0,
        )
        if migrated.returncode != 0:
            _finish()
    heads = _alembic(["heads"], migration_url)
    check(f"exactly one Alembic head: {heads.stdout!r}", heads.stdout.count("(head)") == 1)
    if FAILURES:
        _finish()

    bao_process = subprocess.Popen(
        [
            bao_binary,
            "server",
            "-dev",
            f"-dev-listen-address={BAO_ADDRESS.removeprefix('http://')}",
            f"-dev-root-token-id={BAO_ROOT_TOKEN}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    fake_server = FakeArgillaServer()
    try:
        _wait_for_bao_health()
        openbao = OpenBaoConfig(address=BAO_ADDRESS, token=BAO_ROOT_TOKEN)
        world = _seed(migration_url, app_password, openbao, fake_server)
        section_health(world)
        section_scope(world)
        section_races(world)
        section_edge_cases(world)
        section_identity_and_membership(world)
        section_revoke_sync_race(world)
        section_concurrent_double_revoke(world)
        section_revoke(world)
        section_migration_hardening(world)
        section_rls_and_secrecy(world)
    finally:
        fake_server.shutdown()
        bao_process.terminate()
        try:
            bao_process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover -- defensive cleanup only
            bao_process.kill()
            bao_process.wait(timeout=10)
    _finish()


if __name__ == "__main__":
    main()
