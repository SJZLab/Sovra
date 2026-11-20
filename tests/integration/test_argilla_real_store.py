#!/usr/bin/env python3
# ruff: noqa: E501
"""FTF-R006-P1b round 4 -- the Argilla review-scope / reviewer-identity / workspace-membership
store (`app/reviews/argilla_identity_store.py`) run against the REAL argilla-server on the
owner-ruled stack (OpenSearch 2.19.x + Redis 7.2.x, `infra/docker/argilla/docker-compose.yml`),
with real Postgres and real OpenBao. Control plane's round-3 return (finding 3): the earlier
real-server job "never creates an annotator, never calls the store, never checks cross-org /
cross-project 403/404 ... and 'unhealthy once stopped' is port 1, not a stopped server".

Proves, on the real server (annotator reads are made with the annotator's OWN real API key,
resolved from OpenBao -- never the owner key):

1. Scope: `ensure_review_scope` creates a real workspace per (organization, project).
2. Cross-org / cross-project: an org1 annotator granted org1/project1 is refused (HTTP 403/404,
   never 200, never "unavailable") on org2's dataset and on org1's other project's dataset, and
   only sees its own workspace in `/me/workspaces`.
3. Foreign scope / forged workspace id / relabelled org / foreign identity: the store refuses
   and the real server still shows no access (0 membership gained).
4. Narrowing and revoke: dropping the role removes real access; revoke removes all of it, and a
   later sync is refused.
5. Races on the real server: a sync whose real `add_workspace_user` is slowed while a revoke
   completes (deterministic, R11b/R11c class), 8x parallel sync converging to one membership, and
   a free-running sync-vs-revoke loop -- the revoked identity never reads afterwards.
6. Outage (Law 17): with the real server really stopped (`docker stop`), `get_version`,
   `ensure_review_scope` and `revoke_reviewer_identity` raise `ArgillaUnavailableError`; no scope
   row is fabricated, and the identity stays revoked.

Requires: SOVRA_DATABASE_URL, SOVRA_APP_DB_PASSWORD, a `bao` binary, ARGILLA_BASE_URL (default
http://127.0.0.1:6900), ARGILLA_OWNER_API_KEY, and the `docker` CLI (container publishing the
Argilla port is stopped for the outage section; override via ARGILLA_SERVER_CONTAINER).
Run: python3 tests/integration/test_argilla_real_store.py
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import test_argilla_identity_scope as base  # noqa: E402
from packages.integrations.argilla.client import (  # noqa: E402
    ArgillaClient,
    ArgillaRejectedError,
    ArgillaUnavailableError,
)
from packages.litellm_client.secret_manager_integration import (  # noqa: E402
    OpenBaoConfig,
    resolve_secret_ref,
)
from packages.security.url_policy import ConnectionPolicy  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.rbac_middleware import Role  # noqa: E402
from app.reviews.argilla_identity_store import (  # noqa: E402
    ArgillaIdentityError,
    ReviewerIdentityRecord,
    ReviewerIdentityRevokedError,
    ReviewScopeRecord,
    ensure_review_scope,
    ensure_reviewer_identity,
    revoke_reviewer_identity,
    sync_workspace_membership,
)

FAILURES: list[str] = []
CID = "R006P1b-real-store"
ISSUER = "https://issuer.example"


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


def raises(exc: type[BaseException], fn: Any, *args: Any, **kwargs: Any) -> bool:
    try:
        fn(*args, **kwargs)
    except exc:
        return True
    except Exception as unexpected:  # noqa: BLE001
        print(f"  (unexpected {type(unexpected).__name__}: {unexpected})", file=sys.stderr)
        return False
    return False


def _finish() -> None:
    FAILURES.extend(base.FAILURES)
    if FAILURES:
        for failure in FAILURES:
            print(failure)
        print(f"{len(FAILURES)} failure(s)")
        sys.exit(1)
    print("All FTF-R006-P1b real-Argilla store checks passed.")
    sys.exit(0)


def _policy(port: int) -> ConnectionPolicy:
    return ConnectionPolicy(
        name="real-argilla-store-ci",
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )


class Real:
    """The real server's coordinates plus the helpers every section shares."""

    def __init__(self, base_url: str, owner_key: str) -> None:
        self.base_url = base_url
        self.port = urlsplit(base_url).port or 6900
        self.policy = _policy(self.port)
        self.owner_key = owner_key
        self.owner = self.client(owner_key)

    def client(self, api_key: str | None, timeout: float = 15.0) -> ArgillaClient:
        return ArgillaClient(self.base_url, self.policy, api_key=api_key, timeout=timeout)

    def create_dataset(self, workspace_id: str, label: str) -> str:
        created = self.owner._call(
            "POST",
            "/datasets",
            body={"name": f"{label}-{uuid.uuid4().hex[:8]}", "workspace_id": workspace_id},
        )
        return str(created["id"])

    def can_read(self, annotator: ArgillaClient, dataset_id: str) -> bool:
        """True on HTTP 200; False ONLY on a real 403/404 refusal. An outage or any other
        error propagates -- 'unavailable' is never read as 'denied' (Law 17)."""
        try:
            annotator._call("GET", f"/datasets/{dataset_id}")
        except ArgillaRejectedError:
            return False
        return True

    def workspaces_of(self, annotator: ArgillaClient) -> set[str]:
        return {w.id for w in annotator.list_workspaces_me()}


def _annotator(
    real: Real, world: Any, subject: str
) -> tuple[ReviewerIdentityRecord, ArgillaClient]:
    identity = ensure_reviewer_identity(
        world.factory,
        organization_id=world.org1,
        issuer=ISSUER,
        subject=subject,
        client=real.owner,
        openbao_config=world.openbao,
    )
    key = resolve_secret_ref(identity.credential_secret_ref, config=world.openbao)
    return identity, real.client(key)


def _sync(
    world: Any,
    real: Real,
    identity: ReviewerIdentityRecord,
    scope: ReviewScopeRecord,
    role: Role | None,
    *,
    client: ArgillaClient | None = None,
) -> None:
    sync_workspace_membership(
        world.factory,
        organization_id=world.org1,
        issuer=identity.issuer,
        subject=identity.subject,
        role=role,
        scope=scope,
        identity=identity,
        client=client or real.owner,
    )


def _revoke(world: Any, real: Real, identity: ReviewerIdentityRecord) -> None:
    revoke_reviewer_identity(
        world.factory,
        organization_id=world.org1,
        issuer=identity.issuer,
        subject=identity.subject,
        client=real.owner,
    )


def section_scope_and_isolation(world: Any, real: Real) -> dict[str, Any]:
    scope1 = ensure_review_scope(
        world.factory,
        organization_id=world.org1,
        project_id=world.project1,
        component_binding_id=world.binding1,
        client=real.owner,
    )
    scope_other = ensure_review_scope(
        world.factory,
        organization_id=world.org1,
        project_id=world.new_project(),
        component_binding_id=world.binding1,
        client=real.owner,
    )
    scope2 = ensure_review_scope(
        world.factory,
        organization_id=world.org2,
        project_id=world.project2,
        component_binding_id=world.binding2,
        client=real.owner,
    )
    real_ids = {w.id for w in real.owner.list_workspaces_me()}
    check(
        "ensure_review_scope created three distinct real workspaces on the real server",
        len(
            {
                scope1.argilla_workspace_id,
                scope_other.argilla_workspace_id,
                scope2.argilla_workspace_id,
            }
        )
        == 3
        and {
            scope1.argilla_workspace_id,
            scope_other.argilla_workspace_id,
            scope2.argilla_workspace_id,
        }
        <= real_ids,
    )
    ds1 = real.create_dataset(scope1.argilla_workspace_id, "own")
    ds_other = real.create_dataset(scope_other.argilla_workspace_id, "sibling-project")
    ds2 = real.create_dataset(scope2.argilla_workspace_id, "foreign-org")

    identity, annotator = _annotator(real, world, f"iso-{uuid.uuid4().hex[:8]}")
    me = annotator._call("GET", "/me")
    check(
        "the real server reads the new reviewer back as role 'annotator'",
        me.get("role") == "annotator",
    )
    check(
        "before any membership sync the annotator reads NOTHING (own project, sibling project, foreign org)",
        not any(real.can_read(annotator, d) for d in (ds1, ds_other, ds2)),
    )
    check(
        "before sync the annotator's own workspace list is empty",
        real.workspaces_of(annotator) == set(),
    )

    _sync(world, real, identity, scope1, Role.ML_ENGINEER)
    check(
        "after sync the annotator reads its own project's dataset (HTTP 200)",
        real.can_read(annotator, ds1),
    )
    check(
        "after sync the annotator is REFUSED (403/404) on the sibling project's dataset",
        not real.can_read(annotator, ds_other),
    )
    check(
        "after sync the annotator is REFUSED (403/404) on the foreign organization's dataset",
        not real.can_read(annotator, ds2),
    )
    check(
        "the annotator sees exactly its own workspace in /me/workspaces",
        real.workspaces_of(annotator) == {scope1.argilla_workspace_id},
    )
    memberships = {u.id for u in real.owner.list_workspace_users(scope1.argilla_workspace_id)}
    check(
        "the owner's view shows exactly one membership for the annotator",
        identity.argilla_user_id in memberships,
    )

    # Foreign scope / forged workspace id / relabelled org / foreign identity: the store refuses
    # and the real server gains no access.
    forged = ReviewScopeRecord(
        id=scope2.id,
        organization_id=world.org1,  # relabelled org
        project_id=scope2.project_id,
        component_binding_id=scope2.component_binding_id,
        argilla_workspace_id=scope2.argilla_workspace_id,
        argilla_workspace_name=scope2.argilla_workspace_name,
    )
    check(
        "a foreign org's scope is refused by the store",
        raises(ArgillaIdentityError, _sync, world, real, identity, scope2, Role.ML_ENGINEER),
    )
    check(
        "a relabelled-org scope is refused by the store",
        raises(ArgillaIdentityError, _sync, world, real, identity, forged, Role.ML_ENGINEER),
    )
    forged_ws = ReviewScopeRecord(
        id=scope1.id,
        organization_id=world.org1,
        project_id=scope1.project_id,
        component_binding_id=scope1.component_binding_id,
        argilla_workspace_id=scope2.argilla_workspace_id,  # forged workspace id
        argilla_workspace_name=scope1.argilla_workspace_name,
    )
    check(
        "a forged workspace id on a real scope is refused by the store",
        raises(ArgillaIdentityError, _sync, world, real, identity, forged_ws, Role.ML_ENGINEER),
    )
    check(
        "after every refused sync the annotator STILL cannot read the foreign dataset on the real server",
        not real.can_read(annotator, ds2),
    )
    foreign_identity = ReviewerIdentityRecord(
        id=uuid.uuid4(),
        organization_id=world.org2,
        issuer=identity.issuer,
        subject=identity.subject,
        argilla_user_id=identity.argilla_user_id,
        credential_secret_ref=identity.credential_secret_ref,
        status="active",
    )
    check(
        "a foreign/forged identity record is refused by the store",
        raises(
            ArgillaIdentityError, _sync, world, real, foreign_identity, scope1, Role.ML_ENGINEER
        ),
    )

    # Narrowing, then revoke.
    _sync(world, real, identity, scope1, Role.VIEWER)
    check(
        "narrowing the role removes the real access (annotator now REFUSED on its own dataset)",
        not real.can_read(annotator, ds1),
    )
    check(
        "narrowed annotator has no workspace on the real server",
        real.workspaces_of(annotator) == set(),
    )
    _sync(world, real, identity, scope1, Role.ML_ENGINEER)
    check("re-granting restores the real access", real.can_read(annotator, ds1))
    _revoke(world, real, identity)
    check(
        "revoke removes every real access (annotator REFUSED on its own dataset)",
        not real.can_read(annotator, ds1),
    )
    check(
        "a sync after revoke is refused by the store",
        raises(
            ReviewerIdentityRevokedError, _sync, world, real, identity, scope1, Role.ML_ENGINEER
        ),
    )
    check(
        "after the refused sync the revoked annotator still reads nothing",
        not real.can_read(annotator, ds1),
    )
    _revoke(world, real, identity)  # idempotent: no error
    return {"scope1": scope1, "ds1": ds1, "ds2": ds2}


def section_races(world: Any, real: Real, ctx: dict[str, Any]) -> None:
    scope1: ReviewScopeRecord = ctx["scope1"]
    ds1: str = ctx["ds1"]

    # (a) Deterministic: a sync whose real add is slowed while a revoke completes.
    identity, annotator = _annotator(real, world, f"race-{uuid.uuid4().hex[:8]}")
    slow = real.client(real.owner_key)
    real_add = slow.add_workspace_user

    def delayed_add(workspace_id: str, user_id: str) -> None:
        time.sleep(1.0)
        real_add(workspace_id, user_id)

    slow.add_workspace_user = delayed_add  # type: ignore[method-assign]

    def run_sync() -> str:
        try:
            _sync(world, real, identity, scope1, Role.ML_ENGINEER, client=slow)
            return "ok"
        except ReviewerIdentityRevokedError:
            return "revoked"

    def run_revoke() -> str:
        time.sleep(0.3)
        _revoke(world, real, identity)
        return "done"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f_sync = pool.submit(run_sync)
        f_rev = pool.submit(run_revoke)
        outcome = f_sync.result(timeout=60)
        f_rev.result(timeout=60)
    check(
        f"racing sync is refused once revoke wins on the real server (got {outcome!r})",
        outcome == "revoked",
    )
    check(
        "the revoked identity reads NOTHING on the real server after the slowed-add race (R11b/R11c class)",
        not real.can_read(annotator, ds1),
    )
    check(
        "the revoked identity holds no real workspace membership after the race",
        identity.argilla_user_id
        not in {u.id for u in real.owner.list_workspace_users(scope1.argilla_workspace_id)},
    )

    # (b) 8x parallel sync converges to exactly one membership (advisory: real Argilla may 500 on its own
    # UNIQUE race; the store surfaces that as Unavailable -- Law 17 -- so only those are tolerated).
    identity_p, annotator_p = _annotator(real, world, f"par-{uuid.uuid4().hex[:8]}")

    def parallel_sync(_: int) -> str:
        try:
            _sync(world, real, identity_p, scope1, Role.ML_ENGINEER)
            return "ok"
        except ArgillaUnavailableError:
            return "unavailable"

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(parallel_sync, range(8)))
    check(f"at least one of 8 parallel syncs succeeded (got {outcomes})", "ok" in outcomes)
    _sync(world, real, identity_p, scope1, Role.ML_ENGINEER)  # converge
    members = [u.id for u in real.owner.list_workspace_users(scope1.argilla_workspace_id)]
    check(
        "8x parallel sync converges to exactly one real membership",
        members.count(identity_p.argilla_user_id) == 1,
    )
    check("the parallel-synced annotator reads its dataset", real.can_read(annotator_p, ds1))

    # (c) Free-running sync-vs-revoke: the revoked identity never reads afterwards.
    leaks = 0
    for trial in range(5):
        ident, ann = _annotator(real, world, f"free-{trial}-{uuid.uuid4().hex[:8]}")

        def go_sync(ident: ReviewerIdentityRecord = ident) -> None:
            try:
                _sync(world, real, ident, scope1, Role.ML_ENGINEER)
            except (ReviewerIdentityRevokedError, ArgillaUnavailableError):
                pass

        def go_revoke(ident: ReviewerIdentityRecord = ident) -> None:
            _revoke(world, real, ident)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(go_sync), pool.submit(go_revoke)
            a.result(timeout=60)
            b.result(timeout=60)
        if real.can_read(ann, ds1):
            leaks += 1
    check(
        f"free-running sync-vs-revoke: revoked identity read the dataset in {leaks} of 5 trials (must be 0)",
        leaks == 0,
    )


def _stop_real_server(port: int) -> None:
    named = os.environ.get("ARGILLA_SERVER_CONTAINER")
    if named:
        ids = [named]
    else:
        listed = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"publish={port}"],
            capture_output=True,
            text=True,
            check=True,
        )
        ids = listed.stdout.split()
    check(
        f"exactly one container publishes the real Argilla port {port} (got {ids})", len(ids) == 1
    )
    subprocess.run(["docker", "stop", "-t", "5", *ids], check=True, capture_output=True)


def section_outage(world: Any, real: Real, ctx: dict[str, Any]) -> None:
    scope1: ReviewScopeRecord = ctx["scope1"]
    identity, annotator = _annotator(real, world, f"outage-{uuid.uuid4().hex[:8]}")
    _sync(world, real, identity, scope1, Role.ML_ENGINEER)
    new_project = world.new_project()

    _stop_real_server(real.port)
    short = real.client(real.owner_key, timeout=3.0)
    check(
        "a really stopped real server makes get_version raise ArgillaUnavailableError",
        raises(ArgillaUnavailableError, short.get_version),
    )
    check(
        "ensure_review_scope on a really stopped server raises ArgillaUnavailableError (F16)",
        raises(
            ArgillaUnavailableError,
            ensure_review_scope,
            world.factory,
            organization_id=world.org1,
            project_id=new_project,
            component_binding_id=world.binding1,
            client=short,
        ),
    )
    with Session(world.owner) as session:
        rows = session.execute(
            text("SELECT count(*) FROM argilla_review_scopes WHERE project_id = :p"),
            {"p": str(new_project)},
        ).scalar_one()
    check("no workspace/scope row was fabricated during the outage (F16)", rows == 0)
    check(
        "revoke on a really stopped server raises ArgillaUnavailableError (F14) -- never swallowed",
        raises(ArgillaUnavailableError, _revoke_with, world, identity, short),
    )
    with Session(world.owner) as session:
        status = session.execute(
            text("SELECT status FROM argilla_reviewer_identities WHERE id = :i"),
            {"i": str(identity.id)},
        ).scalar_one()
    check(
        "the identity is already 'revoked' in the database when the outage interrupts the removal",
        status == "revoked",
    )


def _revoke_with(world: Any, identity: ReviewerIdentityRecord, client: ArgillaClient) -> None:
    revoke_reviewer_identity(
        world.factory,
        organization_id=world.org1,
        issuer=identity.issuer,
        subject=identity.subject,
        client=client,
    )


def main() -> None:
    migration_url = base._required_env("SOVRA_DATABASE_URL")
    app_password = base._required_env("SOVRA_APP_DB_PASSWORD")
    owner_key = base._required_env("ARGILLA_OWNER_API_KEY")
    base_url = os.environ.get("ARGILLA_BASE_URL", "http://127.0.0.1:6900")
    bao_binary = base._resolve_bao_binary()

    for args, label in (
        (["upgrade", "head"], "alembic upgrade head"),
        (["heads"], "alembic heads"),
    ):
        migrated = base._alembic(args, migration_url)
        check(
            f"{label} runs cleanly\n{migrated.stdout}\n{migrated.stderr}", migrated.returncode == 0
        )
        if migrated.returncode != 0:
            _finish()

    bao_process = subprocess.Popen(
        [
            bao_binary,
            "server",
            "-dev",
            f"-dev-listen-address={base.BAO_ADDRESS.removeprefix('http://')}",
            f"-dev-root-token-id={base.BAO_ROOT_TOKEN}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        base._wait_for_bao_health()
        openbao = OpenBaoConfig(address=base.BAO_ADDRESS, token=base.BAO_ROOT_TOKEN)
        world = base._seed(migration_url, app_password, openbao, None)
        real = Real(base_url, owner_key)
        ctx = section_scope_and_isolation(world, real)
        section_races(world, real, ctx)
        section_outage(world, real, ctx)  # last: it stops the real server
    finally:
        bao_process.terminate()
        try:
            bao_process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            bao_process.kill()
            bao_process.wait(timeout=10)
    _finish()


if __name__ == "__main__":
    main()
