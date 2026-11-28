#!/usr/bin/env python3
# ruff: noqa: E501
# mypy: disable-error-code="arg-type,attr-defined,assignment,call-arg,index,var-annotated,misc,union-attr,no-untyped-def,operator,return-value,call-overload"
"""FTF-R006-P2 -- the Argilla review queue (`apps/api/app/reviews/review_queue_store.py`,
`packages/integrations/argilla/review_mapping.py`) run against the REAL argilla-server on the
owner-ruled stack (OpenSearch 2.19.x + Redis 7.2.x, `infra/docker/argilla/docker-compose.yml`),
with real Postgres and real OpenBao (the P1b ruling: a fake Argilla alone is not evidence for the
adapter's wire contract).

`tests/integration/test_argilla_review_queue.py` keeps proving the database side against the
real-socket fake. This file proves the adapter against the real server, using the queue store's
own code paths and the P1b identity store:

  RS1  a queue built from a real dataset version creates the real Argilla dataset in the project's
       real workspace with exactly one real record per item (read back with the owner key, by
       external id), and the Factory's stored record ids are the real server's ids
  RS2  replay and an 8-thread create race leave exactly one dataset and one record per item
  RS3  a reviewer's response is written with the reviewer's OWN key (resolved from OpenBao): the
       real server shows the response under that annotator's user id, never the owner's
  RS4  a response by an annotator without membership in the project's workspace is refused by the
       real server (HTTP 403/404, never success) and the Factory records nothing
  RS5  reconciliation sees real drift: an owner-written response and a deleted record
  RS6  outage (Law 17): with the real server really stopped (`docker stop`) create and reconcile
       fail as `argilla_unavailable` and write nothing to the database (runs last)

Requires SOVRA_DATABASE_URL, SOVRA_APP_DB_PASSWORD, a `bao` binary, ARGILLA_BASE_URL (default
http://127.0.0.1:6900), ARGILLA_OWNER_API_KEY and the `docker` CLI (the container publishing the
Argilla port is stopped for RS6; override via ARGILLA_SERVER_CONTAINER).
Run: python3 tests/integration/test_argilla_real_review_queue.py
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import test_argilla_review_queue as Q  # noqa: E402 -- the fake-leg World, MAPPING and helpers
import test_ftf_0519_p1_split_manifests as T  # noqa: E402
from packages.integrations.argilla import review_mapping as rm  # noqa: E402
from packages.integrations.argilla.client import ArgillaClient  # noqa: E402
from packages.litellm_client.secret_manager_integration import OpenBaoConfig  # noqa: E402
from packages.security.url_policy import ConnectionPolicy  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.reviews.argilla_identity_store as identity_store  # noqa: E402

CID = "FTF-R006-P2-real"
FAILURES: list[str] = []
ISSUER = Q.ISSUER


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


def _finish() -> None:
    FAILURES.extend(Q.FAILURES)
    if FAILURES:
        for failure in FAILURES:
            print(failure)
        print(f"{len(FAILURES)} failure(s)")
        sys.exit(1)
    print("All FTF-R006-P2 real-Argilla review-queue checks passed.")
    sys.exit(0)


class Real:
    def __init__(self, base_url: str, owner_key: str) -> None:
        self.base_url = base_url
        port = urlsplit(base_url).port or 6900
        self.policy = ConnectionPolicy(
            name="real-argilla-review-queue-ci",
            allowed_schemes=frozenset({"http"}),
            allowed_ports=frozenset({port}),
            explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        )
        self.owner_key = owner_key
        self.owner = self.client(owner_key)

    def client(self, api_key: str | None, timeout: float = 20.0) -> ArgillaClient:
        return ArgillaClient(self.base_url, self.policy, api_key=api_key, timeout=timeout)


class RealWorld(Q.World):
    """The fake-leg World, pointed at the real server. Every Argilla call goes through the real
    owner client; reviewers write through their own real client."""

    def __init__(self, env: Any, openbao: OpenBaoConfig | None, real: Real) -> None:
        self.env = env
        self.openbao = openbao
        self.real = real
        self.storage = Q.MemoryStorage()
        self.client = real.owner
        self.api = rm.ArgillaReviewApi(real.owner.transport)
        self.w = env.world("review-real")
        self.org = env.org1
        self.ctx = env.ctx1
        self.project = self.w.project_id
        self._binding = self._make_binding()
        self.scope = identity_store.ensure_review_scope(
            env.factory,
            organization_id=self.org,
            project_id=self.project,
            component_binding_id=self._binding,
            client=real.owner,
        )
        self.reviewers: dict[str, Any] = {}

    def factory_for(self, key: str) -> rm.ArgillaReviewApi:
        return rm.ArgillaReviewApi(self.real.client(key).transport)

    def respond(
        self,
        queue,
        position,
        dataset,
        who,
        decision="accept",
        rationale="",
        expected_version=0,
        reviewer_api_factory=None,
    ):  # type: ignore[override]
        return super().respond(
            queue,
            position,
            dataset,
            who,
            decision,
            rationale,
            expected_version,
            reviewer_api_factory or self.factory_for,
        )

    def db_counts(self) -> tuple[int, ...]:
        e = self.env
        return (
            int(e.scalar("SELECT count(*) FROM review_queues")),
            int(e.scalar("SELECT count(*) FROM review_items")),
            int(e.scalar("SELECT count(*) FROM review_responses")),
            int(e.scalar("SELECT count(*) FROM audit_events WHERE resource_type LIKE 'review_%'")),
        )

    def real_records(self, argilla_dataset_id: str) -> dict[str, str]:
        """external_id -> real record id, read straight from the real server with the owner key."""
        out: dict[str, str] = {}
        for record in self.api.list_records(argilla_dataset_id):
            out[record.external_id] = record.id
        return out

    def real_datasets_in_workspace(self) -> list[str]:
        listed = self.real.owner._call(
            "GET", f"/me/datasets?workspace_id={self.scope.argilla_workspace_id}"
        )
        return [str(d["id"]) for d in (listed.get("items") if isinstance(listed, dict) else listed)]


def section_create_and_replay(world: RealWorld) -> dict[str, Any]:
    env = world.env
    train = world.dataset("real-train", "training", Q.rows_n(5, "real"))
    before = world.db_counts()
    queue = world.create(train)
    after = world.db_counts()
    check(
        "RS1: the queue is ready with a real Argilla dataset and five items",
        queue.status == "ready"
        and queue.argilla_dataset_id is not None
        and queue.record_count == 5,
    )
    check(
        "RS1: one queue, five items, one creation audit in the database",
        after[0] == before[0] + 1 and after[1] == before[1] + 5 and after[3] == before[3] + 1,
    )
    records = world.real_records(queue.argilla_dataset_id)
    check(
        f"RS1: the real server holds exactly one record per item, by external id: {sorted(records)}",
        sorted(records) == sorted(f"{train.id}:{i}" for i in range(5)),
    )
    stored = {
        r[0]: r[1]
        for r in env.rows(
            "SELECT record_id, argilla_record_id FROM review_items WHERE queue_id = :q", q=queue.id
        )
    }
    check(
        "RS1: the Factory's stored record ids are the real server's ids",
        stored == records,
    )
    check(
        "RS1: the real dataset lives in the project's real workspace",
        queue.argilla_dataset_id in world.real_datasets_in_workspace(),
    )

    replayed = world.create(train)
    check(
        "RS2: a replay returns the same queue and creates nothing new on the real server",
        replayed.id == queue.id
        and len(world.real_records(queue.argilla_dataset_id)) == 5
        and world.db_counts()[:2] == after[:2],
    )

    race = world.dataset("real-race", "training", Q.rows_n(4, "race"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(lambda: world.create(race)) for _ in range(8)]
        results = [f.exception() or f.result() for f in futures]
    winners = [r for r in results if not isinstance(r, BaseException)]
    ids = {w.id for w in winners}
    check(
        f"RS2: an 8-thread create race yields one queue ({len(winners)} succeeded)",
        len(ids) == 1 and len(winners) >= 1,
    )
    if ids:
        dataset_id = winners[0].argilla_dataset_id
        check(
            "RS2: and exactly one real record per item (no duplicates on the real server)",
            len(world.real_records(dataset_id)) == 4
            and int(
                env.scalar("SELECT count(*) FROM review_queues WHERE dataset_id = :d", d=race.id)
            )
            == 1,
        )
    return {"train": train, "queue": queue}


def section_responses(world: RealWorld, built: dict[str, Any]) -> dict[str, Any]:
    env = world.env
    train, queue = built["train"], built["queue"]
    alice = world.reviewer("alice-real")
    first = world.respond(queue, 0, train, alice, "accept", "good")
    check("RS3: a first response has row_version 1", first.row_version == 1)
    records = world.real_records(queue.argilla_dataset_id)
    record = world.api.get_record(records[f"{train.id}:0"])
    users = {r.user_id for r in record.responses}
    check(
        f"RS3: the real server shows the response under the annotator's own user id: {users}",
        users == {alice.identity.argilla_user_id}
        and world.real.owner_key != ""
        and first.argilla_response_id in {r.id for r in record.responses},
    )
    check(
        "RS3: the stored hash is the independent recompute",
        first.response_sha256
        == rm.response_sha256(
            record_id=f"{train.id}:0",
            user_id=alice.identity.argilla_user_id,
            decision="accept",
            rationale="good",
        ),
    )
    second = world.respond(queue, 0, train, alice, "reject", "", expected_version=1)
    check(
        "RS3: a change updates the same real response and bumps row_version to 2",
        second.row_version == 2 and second.argilla_response_id == first.argilla_response_id,
    )

    # RS4: an annotator that was never synced into this project's real workspace.
    outsider_identity = identity_store.ensure_reviewer_identity(
        env.factory,
        organization_id=world.org,
        issuer=ISSUER,
        subject=f"outsider-{uuid.uuid4().hex[:8]}",
        client=world.real.owner,
        openbao_config=world.openbao,
    )
    outsider = SimpleNamespace(subject=outsider_identity.subject, identity=outsider_identity)
    state = world.db_counts()
    failed = Q.code_of(lambda: world.respond(queue, 1, train, outsider, "accept", "x"))
    check(
        f"RS4: a response by an annotator outside the workspace is refused (got {failed})",
        failed is not None and world.db_counts() == state,
    )
    after = world.api.get_record(records[f"{train.id}:1"])
    check("RS4: and the real server holds no response on that record", len(after.responses) == 0)
    return {"alice": alice}


def section_drift(world: RealWorld) -> None:
    env = world.env
    excl = world.dataset("real-drift", "training", Q.rows_n(4, "drift"))
    queue = world.create(excl)
    clean = world.reconcile(queue)
    check("RS5: a clean queue reconciles with no findings on the real server", clean == ())
    bob = world.reviewer("bob-real")
    world.respond(queue, 0, excl, bob, "accept", "ok")
    check("RS5: a recorded response is not drift", world.reconcile(queue) == ())
    records = world.real_records(queue.argilla_dataset_id)
    # An owner-written response on a record (the owner is not a mapped reviewer).
    world.api.submit_response(records[f"{excl.id}:2"], "accept", "")
    # A record deleted behind the Factory's back.
    world.real.owner._call("DELETE", f"/records/{records[f'{excl.id}:3']}")
    findings = world.reconcile(queue)
    marked = {
        r[0]: r[1]
        for r in env.rows(
            "SELECT record_id, drift_reason FROM review_items WHERE queue_id = :q AND status = 'drift'",
            q=queue.id,
        )
    }
    check(
        f"RS5: an owner response and a deleted record are drift on the real server: {marked}",
        marked
        == {
            f"{excl.id}:2": "response_by_unmapped_user",
            f"{excl.id}:3": "record_missing",
        }
        and len(findings) == 2,
    )
    check(
        "RS5: a drifted item takes no response",
        Q.code_of(lambda: world.respond(queue, 2, excl, bob)) == "review_item_drift",
    )


def section_outage(world: RealWorld, container: str) -> None:
    pending = world.dataset("real-outage", "training", Q.rows_n(3, "outage"))
    reconcile_queue = None
    ready = world.dataset("real-outage-ready", "training", Q.rows_n(3, "outage-r"))
    reconcile_queue = world.create(ready)
    stopped = subprocess.run(
        ["docker", "stop", container], capture_output=True, text=True, timeout=120
    )
    check(
        f"RS6: the real server container stopped ({stopped.stderr.strip()})",
        stopped.returncode == 0,
    )
    if stopped.returncode != 0:
        return
    state = world.db_counts()
    check(
        "RS6: create against a really stopped server is argilla_unavailable",
        Q.code_of(lambda: world.create(pending)) == "argilla_unavailable",
    )
    check(
        "RS6: reconcile against a really stopped server is argilla_unavailable",
        Q.code_of(lambda: world.reconcile(reconcile_queue)) == "argilla_unavailable",
    )
    check(
        "RS6: an outage never writes a queue, item, response or audit row",
        world.db_counts() == state,
    )


def main() -> None:
    migration_url = T._required_env("SOVRA_DATABASE_URL")
    app_password = T._required_env("SOVRA_APP_DB_PASSWORD")
    owner_key = os.environ.get("ARGILLA_OWNER_API_KEY")
    if not owner_key:
        print("BLOCKED_EXTERNAL: ARGILLA_OWNER_API_KEY is not set", file=sys.stderr)
        sys.exit(1)
    base_url = os.environ.get("ARGILLA_BASE_URL", "http://127.0.0.1:6900")
    container = os.environ.get("ARGILLA_SERVER_CONTAINER", "argilla-server")
    binary = Q._bao_binary()
    if binary is None:
        print(
            "BLOCKED_EXTERNAL: no 'bao' binary on PATH and SOVRA_BAO_BINARY is not set",
            file=sys.stderr,
        )
        sys.exit(1)

    env = T.Env()
    env.owner = create_engine(migration_url)
    Q.section_migration(env, migration_url)
    app_engine = create_engine(T._app_role_url(migration_url, app_password))
    env.factory = sessionmaker(bind=app_engine, expire_on_commit=False)
    T.seed(env)

    bao_process = subprocess.Popen(
        [
            binary,
            "server",
            "-dev",
            f"-dev-listen-address={Q.BAO_ADDRESS.removeprefix('http://')}",
            f"-dev-root-token-id={Q.BAO_ROOT_TOKEN}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        Q._wait_for_bao()
        openbao = OpenBaoConfig(address=Q.BAO_ADDRESS, token=Q.BAO_ROOT_TOKEN)
        real = Real(base_url, owner_key)
        world = RealWorld(env, openbao, real)
        built = section_create_and_replay(world)
        section_responses(world, built)
        section_drift(world)
        section_outage(world, container)  # last: it stops the real server
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
