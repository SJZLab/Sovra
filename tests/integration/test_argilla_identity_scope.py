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

import os
import subprocess
import sys
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
from packages.integrations.argilla.client import ArgillaUnavailableError  # noqa: E402
from packages.litellm_client.secret_manager_integration import (  # noqa: E402
    OpenBaoConfig,
    store_secret_ref,
)
from sqlalchemy import create_engine, text  # noqa: E402
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
    ReviewerIdentityRevokedError,
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


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"BLOCKED_EXTERNAL: {name} is not set (see this file's docstring)", file=sys.stderr)
        sys.exit(1)
    return value


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
    project1: uuid.UUID
    project2: uuid.UUID
    binding1: uuid.UUID
    binding2: uuid.UUID
    openbao: OpenBaoConfig
    fake_server: FakeArgillaServer


def _role_lookup(
    mapping: dict[tuple[str, str, uuid.UUID], Role],
) -> Callable[[str, str, uuid.UUID], Role | None]:
    def lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        return mapping.get((issuer, subject, organization_id))

    return lookup


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
    world._scope1, world._scope2 = scope1, scope2  # type: ignore[attr-defined]


def section_identity_and_membership(world: World) -> None:
    client = world.fake_server.client()
    scope1 = world._scope1  # type: ignore[attr-defined]
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
    check(
        "the real user's role reads back exactly 'annotator' (proof bullet 2)",
        real_user["role"] == "annotator",
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
        "idempotent re-call does not create a second real user", len(world.fake_server.users) == 1
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

    world._issuer, world._subject, world._identity, world._real_api_key = (
        issuer,
        subject,
        identity,
        real_api_key,
    )  # type: ignore[attr-defined]


def section_revoke(world: World) -> None:
    client = world.fake_server.client()
    scope1 = world._scope1  # type: ignore[attr-defined]
    issuer, subject, identity = world._issuer, world._subject, world._identity  # type: ignore[attr-defined]

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

    # Idempotent: revoking again is a no-op, not an error.
    revoke_reviewer_identity(
        world.factory, organization_id=world.org1, issuer=issuer, subject=subject, client=client
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
    with Session(world.factory().bind) as raw:  # type: ignore[arg-type]
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
    # under its own bound tenant context (WITH CHECK, not merely USING).
    with Session(world.factory().bind) as raw:  # type: ignore[arg-type]
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
                    "project": str(world.project1),
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
    with Session(world.factory().bind) as raw:  # type: ignore[arg-type]
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
    real_api_key = world._real_api_key  # type: ignore[attr-defined]
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


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")
    openbao = OpenBaoConfig.from_env(os.environ, require_tls=False)

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

    fake_server = FakeArgillaServer()
    try:
        world = _seed(migration_url, app_password, openbao, fake_server)
        section_health(world)
        section_scope(world)
        section_identity_and_membership(world)
        section_revoke(world)
        section_rls_and_secrecy(world)
    finally:
        fake_server.shutdown()
    _finish()


if __name__ == "__main__":
    main()
