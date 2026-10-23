#!/usr/bin/env python3
# ruff: noqa: E501
# mypy: disable-error-code="arg-type,attr-defined,assignment,call-arg,index,var-annotated,misc,union-attr,no-untyped-def,operator,return-value,call-overload"
"""FTF-R006-P2 -- real PostgreSQL 16 (+ real OpenBao in CI) proof for the Argilla review queue
(`apps/api/app/reviews/review_queue_store.py`, `packages/integrations/argilla/review_mapping.py`,
migration 0036).

What `tests/tasks/test_ftf_r006_p2.py` cannot show (SQLite has no row-level security, triggers or
row locks) is proved here, as the restricted `sovra_app` role:

  I1  migration 0036 upgrade / downgrade / upgrade, one Alembic head; ENABLE + FORCE RLS and a
      non-allow-all `tenant_isolation` policy on all three tables; SELECT / INSERT / UPDATE and no
      DELETE for `sovra_app`; the update-guard triggers and the sealed-purpose CHECK exist
  I2  a queue built from a real dataset version through the real FTF-0501 stores, the real FTF-0516
      quality report, the real FTF-0502-P1 source reader and the real importer; the composite
      foreign key pins the dataset lineage
  I3  every refusal writes nothing, to PostgreSQL or to Argilla: sealed purposes (with a canary
      that never reaches Argilla), a source of a locked FTF-0519 manifest, a revoked grant, an
      unclear quality report
  I4  replay and crash-resume are idempotent against the real unique constraint, including an
      8-thread create race (one queue, one set of records)
  I5  responses are written with the reviewer's own key (the real FTF-R006-P1b identity, key
      resolved from OpenBao in CI); `row_version` is enforced under real `SELECT ... FOR UPDATE`
      locks: an 8-thread first-response race has exactly one winner and one Argilla response; a
      stale version writes nothing to Argilla; Argilla down leaves the row unchanged
  I6  reconciliation marks Argilla-side edits, owner / unmapped responses and missing records as
      drift; drift is sticky in the database itself
  I7  the database refuses, for the app role: DELETE, a frozen-column UPDATE, a status move out of
      `ready` / `drift`, a `row_version` step other than +1, a sealed-purpose queue, and every
      cross-tenant read, write and foreign-key reference
  I8  no Argilla API key appears in any new table or audit row, and audit rows are content-free

Requires SOVRA_DATABASE_URL (migration owner), SOVRA_APP_DB_PASSWORD and a `bao` binary (CI builds
the pinned commit). Local development without OpenBao may set SOVRA_R006P2_LOCAL_NO_OPENBAO=1; the
OpenBao leg is then reported as NOT RUN and CI never sets it.
Run: python3 tests/integration/test_argilla_review_queue.py
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import test_ftf_0519_p1_split_manifests as T  # noqa: E402 -- the real-store seeding helpers
from argilla_test_support import FakeArgillaServer  # noqa: E402
from packages.integrations.argilla import review_mapping as rm  # noqa: E402
from packages.litellm_client.secret_manager_integration import (  # noqa: E402
    OpenBaoConfig,
    build_secret_ref,
)
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from workers.data import import_common as ic  # noqa: E402

import app.reviews.argilla_identity_store as identity_store  # noqa: E402
import app.reviews.review_queue_store as store  # noqa: E402
from app.auth.rbac_middleware import Role  # noqa: E402
from app.component_bindings.component_binding_schema import ComponentBinding  # noqa: E402
from app.datasets import dataset_upload_store as ustore  # noqa: E402
from app.datasets.dataset_object_source import DatasetObjectStorage  # noqa: E402
from app.db.tenant_context import TenantContext  # noqa: E402

CID = "FTF-R006-P2"
FAILURES: list[str] = []
ISSUER = "https://issuer.example"
BAO_ADDRESS = "http://127.0.0.1:18502"
BAO_ROOT_TOKEN = f"sovra-ci-test-{uuid.uuid4().hex}"
CANARY = "CANARY-FINAL-SUITE-0b1d9c"
MAPPING = ic.mapping_from_object(
    {"shape": "prompt_completion", "prompt": {"key": "prompt"}, "completion": {"key": "completion"}}
)


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


def _finish() -> None:
    if FAILURES:
        print("\n".join(FAILURES))
        print(f"{len(FAILURES)} failure(s)")
        sys.exit(1)
    print("All FTF-R006-P2 integration checks passed.")
    sys.exit(0)


def code_of(fn: Any) -> str | None:
    try:
        fn()
    except store.ReviewQueueError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001
        return f"unexpected:{type(exc).__name__}:{exc}"
    return None


# ------------------------------------------------------------------------ OpenBao (CI) / local


def _bao_binary() -> str | None:
    explicit = os.environ.get("SOVRA_BAO_BINARY")
    if explicit:
        return explicit
    return shutil.which("bao")


def _wait_for_bao() -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{BAO_ADDRESS}/v1/sys/health", timeout=1) as response:  # noqa: S310
                if response.status in (200, 429, 472, 473, 501, 503):
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    raise RuntimeError("OpenBao dev server never became healthy")


class LocalVault:
    """Local development only (SOVRA_R006P2_LOCAL_NO_OPENBAO=1): a dict in place of OpenBao."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def store(self, *, config: Any, mount: str, path: str, field: str, value: str) -> str:
        ref = build_secret_ref(mount=mount, path=path, field=field)
        self.values[ref] = value
        return ref

    def resolve(self, ref: str, *, config: Any) -> str:
        return self.values[ref]


# --------------------------------------------------------------------------------- the world


class MemoryStorage:
    """The `stream_object_windows` surface `open_dataset_source` reads, over the real bytes of
    each seeded dataset (keyed by the real upload session's object key)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def stream_object_windows(
        self, bucket: str, key: str, *, window_bytes: int = 0, chunk_size: int = 1 << 20
    ):
        data = self.objects[key]
        for start in range(0, len(data), chunk_size):
            yield data[start : start + chunk_size]


def jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(json.dumps(r).encode() + b"\n" for r in rows)


def rows_n(n: int, tag: str = "r") -> list[dict[str, Any]]:
    return [{"prompt": f"{tag} question {i}", "completion": f"{tag} answer {i}"} for i in range(n)]


class World:
    def __init__(self, env: Any, openbao: OpenBaoConfig | None, server: FakeArgillaServer) -> None:
        self.env = env
        self.openbao = openbao
        self.server = server
        self.storage = MemoryStorage()
        self.client = server.client()
        self.api = rm.ArgillaReviewApi(self.client.transport)
        self.w = env.world("review")
        self.org = env.org1
        self.ctx = env.ctx1
        self.project = self.w.project_id
        self._binding = self._make_binding()
        self.scope = identity_store.ensure_review_scope(
            env.factory,
            organization_id=self.org,
            project_id=self.project,
            component_binding_id=self._binding,
            client=self.client,
        )
        self.reviewers: dict[str, Any] = {}

    def _make_binding(self) -> uuid.UUID:
        with Session(self.env.owner) as session:
            binding = ComponentBinding(
                organization_id=self.org,
                component_key="argilla",
                security_zone_scope="internal",
                deployment_mode="worker_package",
                secret_reference=build_secret_ref(
                    mount="secret", path=f"argilla-owner/{self.org}", field="api_key"
                ),
                network_policy_identity="argilla-net-policy",
                compatibility_lock_reference="argilla",
            )
            session.add(binding)
            session.commit()
            return binding.id

    def dataset(
        self,
        name: str,
        purpose: str,
        rows: list[dict[str, Any]],
        *,
        report: bool = True,
        split_records: int = 40,
    ) -> Any:
        data = jsonl(rows)
        digest = hashlib.sha256(data).hexdigest()
        env, w = self.env, self.w

        def build(session: Session) -> Any:
            opened = ustore.create_upload_session(
                session,
                w.ctx,
                project_id=w.project_id,
                connection_id=w.conns["baseline" if purpose == "baseline_only" else "all"],
                purpose=purpose,
                declared_size_bytes=len(data),
                declared_sha256=digest,
                declared_content_type="application/jsonl",
                max_bytes=len(data),
                actor=env.upload_actor,
            )
            return ustore.record_upload_verified_and_create_dataset(
                session,
                w.ctx,
                project_id=w.project_id,
                upload_session_id=opened.id,
                dataset_name=name,
                verified_size_bytes=len(data),
                verified_sha256=digest,
                verified_content_type="application/jsonl",
                actor=env.upload_actor,
            )

        dataset, error = env.run(w.ctx, build)
        if error is not None:
            raise error
        key = env.scalar(
            "SELECT object_key FROM dataset_upload_sessions WHERE id=:i",
            i=dataset.upload_session_id,
        )
        self.storage.objects[key] = data
        env.records[dataset.id] = T._records(str(dataset.id), split_records)
        if report:
            env.quality(w, dataset)
        return dataset

    def create(self, dataset: Any, **overrides: Any) -> store.ReviewQueueRecord:
        kwargs: dict[str, Any] = dict(
            context=self.ctx,
            project_id=self.project,
            dataset_id=dataset.id,
            mapping=MAPPING,
            storage=DatasetObjectStorage(self.storage, "bucket"),
            owner_api=self.api,
            actor_type="user",
            actor_id="creator-1",
            request_id=f"req-{uuid.uuid4().hex[:8]}",
        )
        kwargs.update(overrides)
        return store.create_review_queue(self.env.factory, **kwargs)

    def reviewer(self, subject: str) -> Any:
        if subject not in self.reviewers:
            identity = identity_store.ensure_reviewer_identity(
                self.env.factory,
                organization_id=self.org,
                issuer=ISSUER,
                subject=subject,
                client=self.client,
                openbao_config=self.openbao,
            )
            identity_store.sync_workspace_membership(
                self.env.factory,
                organization_id=self.org,
                issuer=ISSUER,
                subject=subject,
                role=Role.ML_ENGINEER,
                scope=self.scope,
                identity=identity,
                client=self.client,
            )
            self.reviewers[subject] = SimpleNamespace(subject=subject, identity=identity)
        return self.reviewers[subject]

    def respond(
        self,
        queue: Any,
        position: int,
        dataset: Any,
        who: Any,
        decision: str = "accept",
        rationale: str = "",
        expected_version: int = 0,
        reviewer_api_factory: Any = None,
    ) -> Any:
        return store.submit_review_response(
            self.env.factory,
            context=self.ctx,
            queue_id=queue.id,
            record_id=f"{dataset.id}:{position}",
            issuer=ISSUER,
            subject=who.subject,
            decision=decision,
            rationale=rationale,
            expected_version=expected_version,
            owner_api=self.api,
            reviewer_api_factory=reviewer_api_factory
            or (lambda key: rm.ArgillaReviewApi(self.server.client(api_key=key).transport)),
            openbao_config=self.openbao,
            request_id=f"req-{uuid.uuid4().hex[:8]}",
        )

    def reconcile(self, queue: Any) -> Any:
        return store.reconcile_review_queue(
            self.env.factory,
            context=self.ctx,
            queue_id=queue.id,
            owner_api=self.api,
            actor_type="user",
            actor_id="reconciler-1",
            request_id=f"req-{uuid.uuid4().hex[:8]}",
        )

    def state(self) -> tuple[int, ...]:
        e = self.env
        return (
            int(e.scalar("SELECT count(*) FROM review_queues")),
            int(e.scalar("SELECT count(*) FROM review_items")),
            int(e.scalar("SELECT count(*) FROM review_responses")),
            int(e.scalar("SELECT count(*) FROM audit_events WHERE resource_type LIKE 'review_%'")),
            len(self.server.records),
            len(self.server.datasets),
            len([c for c in self.server.review_calls if c[0] in ("POST", "PUT", "DELETE")]),
        )


# ---------------------------------------------------------------------------------- sections


def section_migration(env: Any, migration_url: str) -> None:
    for args, label in (
        (["upgrade", "head"], "alembic upgrade head"),
        (["downgrade", "-1"], "alembic downgrade -1"),
        (["upgrade", "head"], "alembic upgrade head after downgrade -1"),
    ):
        migrated = T._alembic(args, migration_url)
        check(
            f"{label} runs cleanly (exit {migrated.returncode})\n{migrated.stdout}\n{migrated.stderr}",
            migrated.returncode == 0,
        )
        if migrated.returncode != 0:
            _finish()
    heads = T._alembic(["heads"], migration_url)
    check(f"exactly one Alembic head: {heads.stdout!r}", heads.stdout.count("(head)") == 1)
    down = T._alembic(["downgrade", "-1"], migration_url)
    gone = int(
        env.scalar(
            "SELECT count(*) FROM information_schema.tables WHERE table_name IN ('review_queues','review_items','review_responses')"
        )
    )
    check(
        f"downgrade removes the three tables (exit {down.returncode}, {gone} left)",
        down.returncode == 0 and gone == 0,
    )
    up = T._alembic(["upgrade", "head"], migration_url)
    check(f"upgrade restores them (exit {up.returncode})", up.returncode == 0)
    for table in ("review_queues", "review_items", "review_responses"):
        rls = env.rows(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t", t=table
        )
        check(f"{table}: ENABLE and FORCE row-level security", rls == [(True, True)])
        policies = env.rows(
            "SELECT policyname, qual, with_check, cmd FROM pg_policies WHERE tablename = :t",
            t=table,
        )
        check(
            f"{table}: exactly one tenant_isolation policy for ALL",
            len(policies) == 1 and policies[0][0] == "tenant_isolation" and policies[0][3] == "ALL",
        )
        check(
            f"{table}: the policy is keyed on app.current_organization_id, not allow-all",
            all(
                "app.current_organization_id" in (p[1] or "")
                and "app.current_organization_id" in (p[2] or "")
                for p in policies
            ),
        )
        privileges = {
            p: bool(env.scalar("SELECT has_table_privilege('sovra_app', :t, :p)", t=table, p=p))
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
        }
        check(
            f"{table}: sovra_app may SELECT, INSERT, UPDATE and nothing else: {privileges}",
            privileges
            == {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": False, "TRUNCATE": False},
        )
        triggers = env.rows(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = CAST(:t AS regclass) AND NOT tgisinternal",
            t=table,
        )
        check(
            f"{table}: its update-guard trigger exists", triggers == [(f"tr_{table}_update_guard",)]
        )
    checks = {
        r[0]
        for r in env.rows(
            "SELECT conname FROM pg_constraint WHERE conrelid = CAST('review_queues' AS regclass) AND contype = 'c'"
        )
    }
    check(
        "review_queues: the sealed-purpose CHECK exists",
        "ck_review_queues_purpose_not_sealed" in checks,
    )
    fks = {
        r[0]
        for r in env.rows(
            "SELECT conname FROM pg_constraint WHERE conrelid = CAST('review_queues' AS regclass) AND contype = 'f'"
        )
    }
    check(
        "review_queues: composite foreign keys to the dataset lineage and the scope",
        {"fk_review_queues_dataset_lineage", "fk_review_queues_org_scope"} <= fks,
    )


def section_create_and_refusals(world: World) -> dict[str, Any]:
    env = world.env
    train = world.dataset("train-a", "training", rows_n(5, "train"))
    before = world.state()
    queue = world.create(train)
    after = world.state()
    check(
        "I2: the queue is ready with its Argilla dataset",
        queue.status == "ready"
        and queue.argilla_dataset_id is not None
        and queue.record_count == 5,
    )
    check(
        "I2: one queue, five items, one creation audit, five records, one dataset",
        after[0] == before[0] + 1
        and after[1] == before[1] + 5
        and after[3] == before[3] + 1
        and after[4] == before[4] + 5
        and after[5] == before[5] + 1,
    )
    row = env.rows(
        "SELECT dataset_id, dataset_name, dataset_purpose, dataset_version, dataset_sha256, scope_id FROM review_queues WHERE id = :i",
        i=queue.id,
    )[0]
    check(
        "I2: the queue pins the dataset lineage",
        row[0] == train.id
        and row[1] == train.name
        and row[2] == "training"
        and row[3] == train.version
        and row[4] == train.verified_sha256
        and row[5] == world.scope.id,
    )
    items = env.rows(
        "SELECT record_id, batch_position, argilla_record_id, status FROM review_items WHERE queue_id = :q ORDER BY batch_position",
        q=queue.id,
    )
    check(
        "I2: every item is linked and pending, in position order",
        [i[1] for i in items] == [0, 1, 2, 3, 4]
        and all(i[2] and i[3] == "pending" for i in items)
        and [i[0] for i in items] == [f"{train.id}:{p}" for p in range(5)],
    )
    check(
        "I2: the records are really on the Argilla server with the Factory ids",
        sorted(r["external_id"] for r in world.server.records.values())
        == sorted(i[0] for i in items),
    )
    audit = env.rows(
        "SELECT action, actor_id, details_json::text FROM audit_events WHERE resource_id = :r",
        r=str(queue.id),
    )
    check(
        "I8: one content-free creation audit by the Factory principal",
        len(audit) == 1
        and audit[0][0] == "review_queue.created"
        and audit[0][1] == "creator-1"
        and "train question" not in audit[0][2],
    )

    # replay: same queue, nothing duplicated
    replay_before = world.state()
    again = world.create(train)
    check(
        "I4: a replay returns the same queue and writes nothing",
        again.id == queue.id and world.state() == replay_before,
    )

    # excluded positions via a real resolved quality report
    excl = world.dataset("excluded-a", "validation", rows_n(5, "excl"), report=False)
    env.exclude(world.w, excl, (1, 3))
    q_excl = world.create(excl)
    check(
        "I2: positions excluded by the real quality resolution are never queued",
        q_excl.record_count == 3
        and [
            r[0]
            for r in env.rows(
                "SELECT batch_position FROM review_items WHERE queue_id = :q ORDER BY 1",
                q=q_excl.id,
            )
        ]
        == [0, 2, 4],
    )

    # I3 refusals: each writes nothing, to PostgreSQL or Argilla
    held = world.dataset(
        "held-out",
        "held_out_evaluation",
        [{"prompt": CANARY, "completion": "x"}] + rows_n(3, "held"),
    )
    baseline = world.dataset(
        "baseline", "baseline_only", [{"prompt": CANARY, "completion": "y"}] + rows_n(3, "base")
    )
    for label, dataset in (("held_out_evaluation", held), ("baseline_only", baseline)):
        state = world.state()
        check(
            f"I3: {label} is sealed_purpose_refused",
            code_of(lambda dataset=dataset: world.create(dataset)) == "sealed_purpose_refused",
        )
        check(f"I3: {label} refusal wrote nothing", world.state() == state)
    check(
        "I3: the sealed-suite canary is nowhere on the Argilla server",
        not world.server.holds_bytes(CANARY.encode()),
    )
    unclear = world.dataset("unclear", "training", rows_n(3, "unclear"), report=False)
    env.quality(world.w, unclear, clean=False)
    state = world.state()
    check(
        "I3: an unresolved blocking quality issue is quality_not_clear",
        code_of(lambda: world.create(unclear)) == "quality_not_clear",
    )
    check("I3: quality_not_clear wrote nothing", world.state() == state)
    noreport = world.dataset("no-report", "training", rows_n(3, "noreport"), report=False)
    check(
        "I3: a dataset with no quality report is quality_not_clear",
        code_of(lambda: world.create(noreport)) == "quality_not_clear",
    )
    revoked = world.dataset("revoked-grant", "training", rows_n(3, "revoked"))
    env.execute(
        "UPDATE dataset_source_connections SET connection_status = 'revoked' WHERE id = :c",
        c=world.w.conns["all"],
    )
    state = world.state()
    check(
        "I3: a revoked grant is grant_changed (live check, not creation-time)",
        code_of(lambda: world.create(revoked)) == "grant_changed",
    )
    check("I3: grant_changed wrote nothing", world.state() == state)
    env.execute(
        "UPDATE dataset_source_connections SET connection_status = 'active' WHERE id = :c",
        c=world.w.conns["all"],
    )
    # the already-built queue's dataset is also refused on replay once the grant is gone
    env.execute(
        "UPDATE dataset_source_connections SET connection_status = 'revoked' WHERE id = :c",
        c=world.w.conns["all"],
    )
    check(
        "I3: replaying an existing queue also needs the live grant",
        code_of(lambda: world.create(train)) == "grant_changed",
    )
    env.execute(
        "UPDATE dataset_source_connections SET connection_status = 'active' WHERE id = :c",
        c=world.w.conns["all"],
    )

    # a source of a locked FTF-0519 manifest
    d_train = world.dataset("split-train", "training", rows_n(40, "st"), split_records=200)
    d_val = world.dataset("split-val", "validation", rows_n(40, "sv"))
    d_dev = world.dataset("split-dev", "candidate_development", rows_n(40, "sd"), split_records=8)
    d_held = world.dataset("split-held", "held_out_evaluation", rows_n(40, "sh"), split_records=8)
    manifest, error = env.propose(world.w, "campaign", [d_train, d_val, d_dev, d_held])
    check(f"I3: a split manifest proposes ({error!r})", error is None)
    if error is None:
        approved, error = env.approve(world.w, manifest)
        check(f"I3: and locks ({error!r})", error is None and approved.status == "locked")
        state = world.state()
        check(
            "I3: a source of a locked manifest is dataset_already_split",
            code_of(lambda: world.create(d_train)) == "dataset_already_split",
        )
        check("I3: dataset_already_split wrote nothing", world.state() == state)
    return {"train": train, "queue": queue, "excl": excl}


def section_resume_and_race(world: World) -> None:
    env = world.env
    resume = world.dataset("resume", "training", rows_n(6, "resume"))
    world.server.stop()
    state = world.state()
    check(
        "I4: Argilla down is argilla_unavailable",
        code_of(lambda: world.create(resume)) == "argilla_unavailable",
    )
    building = env.rows(
        "SELECT status, argilla_dataset_id FROM review_queues WHERE dataset_id = :d", d=resume.id
    )
    check(
        "I4: after the outage the queue is 'building' with no Argilla dataset (never 'ready')",
        building == [("building", None)],
    )
    check("I4: the outage created no Argilla record", world.state()[4] == state[4])
    world.server.start()
    done = world.create(resume)
    check(
        "I4: the replay completes the same queue",
        done.status == "ready"
        and int(env.scalar("SELECT count(*) FROM review_queues WHERE dataset_id = :d", d=resume.id))
        == 1,
    )
    check(
        "I4: every item is linked after the resume",
        int(
            env.scalar(
                "SELECT count(*) FROM review_items WHERE queue_id = :q AND argilla_record_id IS NULL",
                q=done.id,
            )
        )
        == 0,
    )

    racing = world.dataset("race", "training", rows_n(12, "race"))
    before_records = len(world.server.records)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: _try(lambda: world.create(racing)), range(8)))
    queues = env.rows("SELECT id, status FROM review_queues WHERE dataset_id = :d", d=racing.id)
    check(f"I4: an 8-thread create race leaves exactly one queue: {queues}", len(queues) == 1)
    final = world.create(racing)
    check("I4: the queue is ready after the race settles", final.status == "ready")
    check(
        "I4: exactly the queued records exist on Argilla, none duplicated",
        len(world.server.records) - before_records == 12,
    )
    check(
        "I4: exactly one set of items",
        int(env.scalar("SELECT count(*) FROM review_items WHERE queue_id = :q", q=final.id)) == 12,
    )
    check(
        "I4: exactly one creation audit",
        int(
            env.scalar(
                "SELECT count(*) FROM audit_events WHERE resource_id = :r AND action = 'review_queue.created'",
                r=str(final.id),
            )
        )
        == 1,
    )
    check(
        "I4: every racer got a queue or a retryable Argilla/conflict code, never an unexpected error",
        all(
            o is None
            or (isinstance(o, str) and not o.startswith("unexpected"))
            or isinstance(o, store.ReviewQueueRecord)
            for o in outcomes
        ),
    )


def _try(fn: Any) -> Any:
    try:
        return fn()
    except store.ReviewQueueError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001
        return f"unexpected:{type(exc).__name__}:{exc}"


def section_responses(world: World, built: dict[str, Any]) -> None:
    env = world.env
    train, queue = built["train"], built["queue"]
    alice, bob = world.reviewer("alice"), world.reviewer("bob")
    first = world.respond(queue, 0, train, alice, "accept", "good")
    check("I5: a first response has row_version 1", first.row_version == 1)
    stored = next(r for r in world.server.responses.values())
    check(
        "I5: the Argilla response author is the mapped annotator",
        stored["user_id"] == alice.identity.argilla_user_id,
    )
    writes = [
        c for c in world.server.review_calls if c[1].endswith("/responses") and c[0] == "POST"
    ]
    api_key = world.server.users[alice.identity.argilla_user_id]["api_key"]
    check(
        "I5: it was written with the reviewer's own key (resolved from the secret manager)",
        writes[-1][2] == api_key and writes[-1][2] != world.server.owner_key,
    )
    check(
        "I5: the stored hash is the independent recompute",
        first.response_sha256
        == rm.response_sha256(
            record_id=f"{train.id}:0",
            user_id=alice.identity.argilla_user_id,
            decision="accept",
            rationale="good",
        ),
    )
    state = world.state()
    check(
        "I5: a stale expected_version is review_version_conflict",
        code_of(lambda: world.respond(queue, 0, train, alice, "reject", "x", expected_version=0))
        == "review_version_conflict",
    )
    check("I5: a conflict sends nothing to Argilla and writes nothing", world.state() == state)
    second = world.respond(queue, 0, train, alice, "reject", "", expected_version=1)
    check(
        "I5: a change bumps row_version to 2 on the same Argilla response",
        second.row_version == 2 and second.argilla_response_id == first.argilla_response_id,
    )

    # 8-thread first-response race by the same reviewer on another item
    calls_before = len(
        [c for c in world.server.review_calls if c[0] == "POST" and c[1].endswith("/responses")]
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(
            pool.map(
                lambda _: _try(lambda: world.respond(queue, 1, train, bob, "accept", "race")),
                range(8),
            )
        )
    winners = [o for o in outcomes if isinstance(o, store.ReviewResponseRecord)]
    check(
        f"I5: an 8-thread first-response race has exactly one winner: {[type(o).__name__ if not isinstance(o, str) else o for o in outcomes]}",
        len(winners) == 1,
    )
    check(
        "I5: every loser is review_version_conflict",
        sorted(o for o in outcomes if isinstance(o, str)) == ["review_version_conflict"] * 7,
    )
    check(
        "I5: exactly one response row",
        int(
            env.scalar(
                "SELECT count(*) FROM review_responses WHERE identity_id = :i", i=bob.identity.id
            )
        )
        == 1,
    )
    calls_after = len(
        [c for c in world.server.review_calls if c[0] == "POST" and c[1].endswith("/responses")]
    )
    check(
        "I5: exactly one Argilla response write for the race (the locks held the losers off)",
        calls_after - calls_before == 1,
    )

    # Argilla outage leaves the row unchanged
    row_before = env.rows(
        "SELECT decision, rationale, response_sha256, row_version FROM review_responses WHERE identity_id = :i",
        i=alice.identity.id,
    )
    world.server.stop()
    check(
        "I5: Argilla down is argilla_unavailable",
        code_of(lambda: world.respond(queue, 0, train, alice, "accept", "x", expected_version=2))
        == "argilla_unavailable",
    )
    world.server.start()
    check(
        "I5: the Factory row is unchanged after the outage",
        env.rows(
            "SELECT decision, rationale, response_sha256, row_version FROM review_responses WHERE identity_id = :i",
            i=alice.identity.id,
        )
        == row_before,
    )

    # an identity without membership is refused; a revoked one too
    carol = world.reviewer("carol")
    identity_store.sync_workspace_membership(
        env.factory,
        organization_id=world.org,
        issuer=ISSUER,
        subject="carol",
        role=None,
        scope=world.scope,
        identity=carol.identity,
        client=world.client,
    )
    check(
        "I5: a reviewer whose DATASET_SOURCE_ROLES membership was removed is reviewer_not_authorized",
        code_of(lambda: world.respond(queue, 2, train, carol)) == "reviewer_not_authorized",
    )
    identity_store.revoke_reviewer_identity(
        env.factory, organization_id=world.org, issuer=ISSUER, subject="alice", client=world.client
    )
    check(
        "I5: a revoked reviewer identity is reviewer_not_authorized",
        code_of(lambda: world.respond(queue, 0, train, alice, "accept", "", expected_version=2))
        == "reviewer_not_authorized",
    )


def section_drift(world: World, built: dict[str, Any]) -> None:
    env = world.env
    excl = built["excl"]
    queue = world.create(excl)
    clean = world.reconcile(queue)
    check("I6: a clean queue reconciles with no findings", clean == ())
    bob = world.reviewer("bob")
    world.respond(queue, 0, excl, bob, "accept", "ok")
    check("I6: a recorded response is not drift", world.reconcile(queue) == ())
    records = {
        r["external_id"]: r
        for r in world.server.records.values()
        if r["dataset_id"] == queue.argilla_dataset_id
    }
    target = records[f"{excl.id}:2"]
    world.server.edit_record_fields(
        target["id"], "conversation", "edited behind the Factory's back"
    )
    other = records[f"{excl.id}:4"]
    world.server.inject_response(other["id"], world.server.OWNER_USER_ID, "accept")
    findings = world.reconcile(queue)
    marked = {
        r[0]: r[1]
        for r in env.rows(
            "SELECT record_id, drift_reason FROM review_items WHERE queue_id = :q AND status = 'drift'",
            q=queue.id,
        )
    }
    check(
        f"I6: an edited record and an owner response are drift: {marked}",
        marked
        == {f"{excl.id}:2": "record_fields_changed", f"{excl.id}:4": "response_by_unmapped_user"},
    )
    check(
        "I6: the untouched items stay pending",
        int(
            env.scalar(
                "SELECT count(*) FROM review_items WHERE queue_id = :q AND status = 'pending'",
                q=queue.id,
            )
        )
        == 1
        and len(findings) == 2,
    )
    check(
        "I6: a drifted item takes no response",
        code_of(lambda: world.respond(queue, 2, excl, bob)) == "review_item_drift",
    )
    world.server.stop()
    state = world.state()
    check(
        "I6: an outage during reconcile is argilla_unavailable",
        code_of(lambda: world.reconcile(queue)) == "argilla_unavailable",
    )
    world.server.start()
    check("I6: and changes nothing", world.state() == state)


def section_reconcile_race(world: World) -> None:
    env = world.env
    race = world.dataset("reconcile-race", "training", rows_n(12, "rr"))
    queue = world.create(race)
    reviewer = world.reviewer("racer")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        submits = [
            pool.submit(
                _try, lambda p=p: world.respond(queue, p, race, reviewer, "accept", f"r{p}")
            )
            for p in range(8)
        ]
        polls = [pool.submit(_try, lambda: world.reconcile(queue)) for _ in range(8)]
        outcomes = [f.result() for f in submits + polls]
    check(
        f"I6: submits racing reconciles all succeed: {[o for o in outcomes if isinstance(o, str)]}",
        not any(isinstance(o, str) for o in outcomes),
    )
    marked = env.rows(
        "SELECT record_id, drift_reason FROM review_items WHERE queue_id = :q AND status = 'drift'",
        q=queue.id,
    )
    check(
        f"I6: no legitimate response was ever marked drift by an overlapping poll: {marked}",
        marked == [],
    )
    check(
        "I6: every raced response is recorded",
        int(env.scalar("SELECT count(*) FROM review_responses WHERE queue_id = :q", q=queue.id))
        == 8,
    )
    check("I6: a final reconcile is clean", world.reconcile(queue) == ())


class BlockingApi(rm.ArgillaReviewApi):
    """Writes to Argilla, then holds the surrounding Factory transaction open until released."""

    def __init__(self, transport: Any, entered: threading.Event, release: threading.Event) -> None:
        super().__init__(transport)
        self._entered, self._release = entered, release

    def submit_response(self, record_id: str, decision: str, rationale: str) -> Any:
        result = super().submit_response(record_id, decision, rationale)
        self._entered.set()
        self._release.wait(20)
        return result


def section_reconcile_waits_for_submit(world: World) -> None:
    """The deterministic interleaving: Argilla already holds the response, the Factory row is
    not committed yet. A poll must wait for the item lock and then see a consistent state."""
    env = world.env
    dataset = world.dataset("reconcile-wait", "training", rows_n(3, "rw"))
    queue = world.create(dataset)
    reviewer = world.reviewer("waiter")
    entered, release = threading.Event(), threading.Event()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        submit = pool.submit(
            _try,
            lambda: world.respond(
                queue,
                0,
                dataset,
                reviewer,
                "accept",
                "held",
                reviewer_api_factory=lambda key: BlockingApi(
                    world.server.client(api_key=key).transport, entered, release
                ),
            ),
        )
        check("I6: the submit reached Argilla and is holding its transaction", entered.wait(15))
        poll = pool.submit(_try, lambda: world.reconcile(queue))
        time.sleep(1.0)
        check("I6: the poll is waiting on the item lock while the submit is open", not poll.done())
        release.set()
        submitted, polled = submit.result(30), poll.result(30)
    check(
        f"I6: the held submit commits: {submitted!r}",
        isinstance(submitted, store.ReviewResponseRecord),
    )
    check(f"I6: the waiting poll finds no drift: {polled!r}", polled == ())
    check(
        "I6: nothing was marked drift",
        int(
            env.scalar(
                "SELECT count(*) FROM review_items WHERE queue_id = :q AND status = 'drift'",
                q=queue.id,
            )
        )
        == 0,
    )


def section_database_protections(world: World, built: dict[str, Any]) -> None:
    env = world.env
    queue, train = built["queue"], built["train"]
    ctx1, ctx2 = env.ctx1, env.ctx2

    def refused(label: str, ctx: TenantContext, sql: str, *needles: str, **params: Any) -> None:
        _, error = env.run(ctx, lambda s: s.execute(text(sql), params))
        message = str(error)
        check(
            f"I7: {label} is refused ({type(error).__name__ if error else 'ACCEPTED'})",
            error is not None and all(n in message for n in needles),
        )

    item = env.rows(
        "SELECT id FROM review_items WHERE queue_id = :q ORDER BY batch_position LIMIT 1",
        q=queue.id,
    )[0][0]
    refused(
        "DELETE on review_queues",
        ctx1,
        "DELETE FROM review_queues WHERE id = :i",
        "permission denied",
        i=queue.id,
    )
    refused(
        "DELETE on review_items",
        ctx1,
        "DELETE FROM review_items WHERE id = :i",
        "permission denied",
        i=item,
    )
    refused("DELETE on review_responses", ctx1, "DELETE FROM review_responses", "permission denied")
    refused(
        "changing a frozen queue column",
        ctx1,
        "UPDATE review_queues SET record_count = 99 WHERE id = :i",
        "a review queue is immutable except status and argilla_dataset_id",
        i=queue.id,
    )
    refused(
        "rewriting the queue snapshot hash",
        ctx1,
        "UPDATE review_queues SET queue_snapshot_sha256 = :h WHERE id = :i",
        "a review queue is immutable except status and argilla_dataset_id",
        h="0" * 64,
        i=queue.id,
    )
    refused(
        "moving a ready queue back to building",
        ctx1,
        "UPDATE review_queues SET status = 'building' WHERE id = :i",
        "a review queue may only move building -> ready",
        i=queue.id,
    )
    refused(
        "re-pointing the Argilla dataset id",
        ctx1,
        "UPDATE review_queues SET argilla_dataset_id = :a WHERE id = :i",
        "argilla_dataset_id is set once",
        a=str(uuid.uuid4()),
        i=queue.id,
    )
    refused(
        "changing a frozen item column",
        ctx1,
        "UPDATE review_items SET record_sha256 = :h WHERE id = :i",
        "a review item is immutable except",
        h="1" * 64,
        i=item,
    )
    refused(
        "re-pointing an item's Argilla record",
        ctx1,
        "UPDATE review_items SET argilla_record_id = :a WHERE id = :i",
        "argilla_record_id is set once",
        a=str(uuid.uuid4()),
        i=item,
    )
    refused(
        "a drift status without a reason",
        ctx1,
        "UPDATE review_items SET status = 'drift' WHERE id = :i",
        "ck_review_items_drift_reason_consistent",
        i=item,
    )
    marked = env.rows("SELECT id FROM review_items WHERE status = 'drift' LIMIT 1")
    if marked:
        refused(
            "clearing drift (drift is sticky)",
            ctx1,
            "UPDATE review_items SET status = 'pending', drift_reason = NULL WHERE id = :i",
            "drift is sticky",
            i=marked[0][0],
        )
        refused(
            "rewriting a drift reason",
            ctx1,
            "UPDATE review_items SET drift_reason = 'record_missing' WHERE id = :i",
            "drift is sticky",
            i=marked[0][0],
        )
    response = env.rows("SELECT id, row_version FROM review_responses LIMIT 1")
    if response:
        refused(
            "a row_version step of +2",
            ctx1,
            "UPDATE review_responses SET row_version = row_version + 2 WHERE id = :i",
            "row_version must rise by exactly 1",
            i=response[0][0],
        )
        refused(
            "no row_version step",
            ctx1,
            "UPDATE review_responses SET decision = 'reject' WHERE id = :i",
            "row_version must rise by exactly 1",
            i=response[0][0],
        )
        refused(
            "changing the response's reviewer",
            ctx1,
            "UPDATE review_responses SET identity_id = :x, row_version = row_version + 1 WHERE id = :i",
            "a review response is immutable except",
            x=uuid.uuid4(),
            i=response[0][0],
        )
        refused(
            "an invalid decision value",
            ctx1,
            "UPDATE review_responses SET decision = 'approve', row_version = row_version + 1 WHERE id = :i",
            "ck_review_responses_decision",
            i=response[0][0],
        )
    # uniqueness at the database
    refused(
        "a second item for one (queue, record)",
        ctx1,
        "INSERT INTO review_items (organization_id, queue_id, record_id, batch_position, group_id, record_sha256, fields_sha256) "
        "SELECT organization_id, queue_id, record_id, 9999, group_id, record_sha256, fields_sha256 FROM review_items WHERE id = :i",
        "uq_review_items_queue_record",
        i=item,
    )
    refused(
        "a second item at one (queue, batch position)",
        ctx1,
        "INSERT INTO review_items (organization_id, queue_id, record_id, batch_position, group_id, record_sha256, fields_sha256) "
        "SELECT organization_id, queue_id, 'dup:record', batch_position, group_id, record_sha256, fields_sha256 FROM review_items WHERE id = :i",
        "uq_review_items_queue_position",
        i=item,
    )
    if response:
        refused(
            "a second response row for one (item, reviewer)",
            ctx1,
            "INSERT INTO review_responses (organization_id, queue_id, item_id, identity_id, decision, rationale, response_sha256, argilla_response_id) "
            "SELECT organization_id, queue_id, item_id, identity_id, decision, rationale, response_sha256, :a FROM review_responses WHERE id = :i",
            "uq_review_responses_item_identity",
            a=str(uuid.uuid4()),
            i=response[0][0],
        )
    # sealed purpose at the database
    held = env.rows(
        "SELECT id, name, version, verified_sha256 FROM datasets WHERE purpose = 'held_out_evaluation' LIMIT 1"
    )[0]
    refused(
        "queueing a held-out dataset even by raw INSERT",
        ctx1,
        "INSERT INTO review_queues (organization_id, project_id, dataset_id, dataset_name, dataset_purpose, dataset_version, dataset_sha256, scope_id, argilla_dataset_name, quality_report_id, quality_report_sha256, excluded_positions_sha256, queue_snapshot_sha256, record_count, schema_version, created_by) "
        "VALUES (:o, :p, :d, :n, 'held_out_evaluation', :v, :s, :sc, 'sovra-rq-' || repeat('a', 24), :q, :h, :h, :h, 1, 'x', 'raw')",
        "ck_review_queues_purpose_not_sealed",
        o=world.org,
        p=world.project,
        d=held[0],
        n=held[1],
        v=held[2],
        s=held[3],
        sc=world.scope.id,
        q=uuid.uuid4(),
        h="a" * 64,
    )
    # cross-tenant
    for table in ("review_queues", "review_items", "review_responses"):
        seen, error = env.run(
            ctx2, lambda s, table=table: s.execute(text(f"SELECT count(*) FROM {table}")).scalar()
        )
        check(f"I7: another organization sees no {table} row", error is None and seen == 0)
        seen, error = env.run(
            ctx1, lambda s, table=table: s.execute(text(f"SELECT count(*) FROM {table}")).scalar()
        )
        check(f"I7: the owning organization sees its {table} rows", error is None and seen > 0)
    updated, error = env.run(
        ctx2, lambda s: s.execute(text("UPDATE review_queues SET updated_at = now()")).rowcount
    )
    check("I7: another organization's UPDATE touches no queue", error is None and updated == 0)
    updated, error = env.run(
        ctx2, lambda s: s.execute(text("UPDATE review_items SET updated_at = now()")).rowcount
    )
    check("I7: another organization's UPDATE touches no row", error is None and updated == 0)
    refused(
        "another organization inserting an item under this organization's queue",
        ctx2,
        "INSERT INTO review_items (organization_id, queue_id, record_id, batch_position, group_id, record_sha256, fields_sha256) VALUES (:o, :q, 'x:0', 99, :h, :h, :h)",
        o=env.org2,
        q=queue.id,
        h="b" * 64,
    )
    refused(
        "this organization writing a row stamped with another organization",
        ctx1,
        "INSERT INTO review_items (organization_id, queue_id, record_id, batch_position, group_id, record_sha256, fields_sha256) VALUES (:o, :q, 'x:0', 99, :h, :h, :h)",
        o=env.org2,
        q=queue.id,
        h="b" * 64,
    )
    _, error = env.run(
        TenantContext(organization_id=uuid.uuid4()),
        lambda s: s.execute(text("SELECT count(*) FROM review_queues")).scalar(),
    )
    check("I7: an unknown tenant sees nothing", error is None)
    with env.factory() as session:
        count = session.execute(text("SELECT count(*) FROM review_queues")).scalar()
        check(f"I7: with no tenant context the app role sees no row ({count})", count == 0)
    _ = train


def section_secrecy(world: World) -> None:
    env = world.env
    keys = [u["api_key"] for u in world.server.users.values()] + [world.server.owner_key]
    for table in (
        "review_queues",
        "review_items",
        "review_responses",
        "argilla_reviewer_identities",
    ):
        dump = "\n".join(str(r) for r in env.rows(f"SELECT {table}::text FROM {table}"))
        check(f"I8: no Argilla API key value in {table}", not any(k in dump for k in keys))
    audits = "\n".join(
        str(r)
        for r in env.rows(
            "SELECT details_json::text FROM audit_events WHERE resource_type LIKE 'review_%'"
        )
    )
    check("I8: no API key in any review audit row", not any(k in audits for k in keys))
    check(
        "I8: no conversation or rationale text in any review audit row",
        all(t not in audits for t in ("train question", "excl question", "good", "race")),
    )
    check(
        "I8: review audit rows exist for creation, response and reconcile",
        {
            "review_queue.created",
            "review_response.submitted",
            "review_response.changed",
            "review_queue.reconciled",
        }
        <= {
            r[0]
            for r in env.rows("SELECT action FROM audit_events WHERE resource_type LIKE 'review_%'")
        },
    )


def main() -> None:
    migration_url = T._required_env("SOVRA_DATABASE_URL")
    app_password = T._required_env("SOVRA_APP_DB_PASSWORD")
    binary = _bao_binary()
    local_only = os.environ.get("SOVRA_R006P2_LOCAL_NO_OPENBAO") == "1"
    if binary is None and not local_only:
        print(
            "BLOCKED_EXTERNAL: no 'bao' binary on PATH and SOVRA_BAO_BINARY is not set",
            file=sys.stderr,
        )
        sys.exit(1)

    env = T.Env()
    env.owner = create_engine(migration_url)
    section_migration(env, migration_url)
    app_engine = create_engine(T._app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    env.factory = sessionmaker(bind=app_engine, expire_on_commit=False)
    T.seed(env)

    bao_process = None
    openbao: OpenBaoConfig | None = None
    vault = LocalVault()
    saved = (identity_store.store_secret_ref, store.resolve_secret_ref)
    server = FakeArgillaServer()
    try:
        if binary is not None:
            bao_process = subprocess.Popen(
                [
                    binary,
                    "server",
                    "-dev",
                    f"-dev-listen-address={BAO_ADDRESS.removeprefix('http://')}",
                    f"-dev-root-token-id={BAO_ROOT_TOKEN}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            _wait_for_bao()
            openbao = OpenBaoConfig(address=BAO_ADDRESS, token=BAO_ROOT_TOKEN)
        else:
            print("NOT RUN: the OpenBao leg (local development only; CI always runs it)")
            identity_store.store_secret_ref = vault.store
            store.resolve_secret_ref = vault.resolve
        world = World(env, openbao, server)
        built = section_create_and_refusals(world)
        section_resume_and_race(world)
        section_responses(world, built)
        section_drift(world, built)
        section_reconcile_race(world)
        section_reconcile_waits_for_submit(world)
        section_database_protections(world, built)
        section_secrecy(world)
    finally:
        identity_store.store_secret_ref, store.resolve_secret_ref = saved
        server.shutdown()
        if bao_process is not None:
            bao_process.terminate()
            try:
                bao_process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                bao_process.kill()
                bao_process.wait(timeout=10)
    _finish()


if __name__ == "__main__":
    main()
