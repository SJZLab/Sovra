#!/usr/bin/env python3
"""FTF-R005 round 2 -- durable MLflow outbox, run-level drift, Law 17 registry
status and model-version paging against a REAL `mlflow server` (SQLite backend,
loopback) and a REAL Postgres (the certified FTF-R002 `component_sync_outbox`,
`sovra_app` role, tenant RLS). Never a canned response, never an in-memory
store.

R2-D1  a result is admitted to the real R002 outbox row, synced to MLflow and
       resolved `succeeded` with the MLflow run id as external id.
R2-D2  an outage keeps the row `pending` (attempt + error recorded, never
       SYNCED). After a "Factory process restart" (engine disposed, every
       Python object dropped, a fresh engine/outbox/client) the buffered
       result is still there; a retry before its time is not attempted; at
       its time it syncs with the exact lineage and no duplicate run.
R2-D3  bounded retries: at the attempt limit the row is resolved `failed`
       (`retry limit reached`), is counted by R002's component-status
       projection as a failed outbox entry, and is never retried or resurrected.
R2-D4  tenants: org B cannot queue org A's result, cannot use org A's binding,
       and never sees org A's pending rows.
R2-D5  a result that changed or vanished in Factory evidence after admission
       is failed visibly (`result_changed` / `result_missing`), never synced;
       a different result for an already-queued job is refused.
R2-D6  two MLflow runs claiming one logical key fail the row `ambiguous`.
R2-D7  run-level drift on the real server: an extra metric, a changed hash tag
       or an extra param on a completed run (payload tag untouched, so a
       tag-only check would say SYNCED) is FAILED with drift codes and the run
       is not overwritten; a drifted run found via the durable outbox fails
       the row with `run drift:`.
R2-D8  Law 17 on real model versions: READY reads READY; a version flipped to
       PENDING_REGISTRATION / FAILED_REGISTRATION in the server's own store is
       reported as such, never clean; forged approval tags change nothing.
R2-D9  205 versions of one registered model (more than one 200-row page) are
       all found by `search_model_versions_by_tag`.
R2-D10 `client_from_binding` builds the adapter from the tenant's real
       MLflow ComponentBinding row.

Requires `mlflow` (CI: pip install mlflow==3.1.1 'sqlalchemy>=2.0,<2.1'; locally
MLFLOW_BIN), SOVRA_DATABASE_URL (migration owner) and SOVRA_APP_DB_PASSWORD.
    python3 tests/integration/test_mlflow_durable_outbox.py
"""

from __future__ import annotations

import ipaddress
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import test_mlflow_projection as proj  # noqa: E402
from packages.integrations.mlflow.client import MlflowClient  # noqa: E402
from packages.integrations.mlflow.durable_outbox import (  # noqa: E402
    DurableOutbox,
    client_from_binding,
)
from packages.integrations.mlflow.reconcile import (  # noqa: E402
    TAG_KEY,
    TAG_ORG,
    TAG_PAYLOAD,
    RetryPolicy,
    RunConflictError,
    SyncState,
    TrackedRun,
    experiment_name,
    logical_run_key,
    sync_pending,
    sync_run,
)
from packages.integrations.mlflow.registry_binding import (  # noqa: E402
    TAG_MODEL_VERSION,
    bind_model_version,
    inspect_binding,
    project_registry_binding,
)
from packages.security.url_policy import ConnectionPolicy  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.organization_workspace_schema import APP_ROLE_NAME, Organization  # noqa: E402
from app.component_bindings.component_binding_crud import (  # noqa: E402
    ActorInfo,
    ComponentBindingNotFoundError,
    create_component_binding,
    get_component_binding,
    get_component_status_projection,
)
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402

FAILURES: list[str] = []
PREFIXES = ("s3://sovra-model-artifacts/",)
ACTOR = ActorInfo(actor_type="system", actor_id="mlflow-sync", request_id="r005-r2")


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"BLOCKED_EXTERNAL: {name} is not set (needs a live Postgres).", file=sys.stderr)
        sys.exit(1)
    return value


def app_url(migration_url: str, password: str) -> str:
    parts = urlsplit(migration_url)
    netloc = f"{APP_ROLE_NAME}:{password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class FileResultSource:
    """Durable stand-in for Factory evidence: one JSON file per result on disk
    (survives the simulated process restart; shares no Python state)."""

    def __init__(self, directory: pathlib.Path) -> None:
        self.directory = directory

    def _path(self, org: uuid.UUID, job: uuid.UUID) -> pathlib.Path:
        return self.directory / f"{org}-{job}.json"

    def save(self, run: TrackedRun) -> None:
        self._path(run.organization_id, run.job_id).write_text(
            json.dumps(
                {
                    "organization_id": str(run.organization_id),
                    "job_id": str(run.job_id),
                    "completed_at_ms": run.completed_at_ms,
                    "metrics": [list(m) for m in run.metrics],
                    "params": [list(p) for p in run.params],
                    "hashes": [list(h) for h in run.hashes],
                    "data_purpose": run.data_purpose,
                    "export_class": run.export_class,
                    "status": run.status,
                }
            )
        )

    def load(self, organization_id: uuid.UUID, job_id: uuid.UUID) -> TrackedRun | None:
        path = self._path(organization_id, job_id)
        if not path.exists():
            return None
        raw = json.loads(path.read_text())
        return TrackedRun(
            organization_id=uuid.UUID(raw["organization_id"]),
            job_id=uuid.UUID(raw["job_id"]),
            completed_at_ms=raw["completed_at_ms"],
            metrics=tuple((k, v) for k, v in raw["metrics"]),
            params=tuple((k, v) for k, v in raw["params"]),
            hashes=tuple((k, v) for k, v in raw["hashes"]),
            data_purpose=raw["data_purpose"],
            export_class=raw["export_class"],
            status=raw["status"],
        )

    def delete(self, org: uuid.UUID, job: uuid.UUID) -> None:
        self._path(org, job).unlink()


def run_migration(url: str) -> None:
    env = dict(os.environ, SOVRA_DATABASE_URL=url)
    done = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    check(
        "R2-D0",
        f"alembic upgrade head ({done.returncode}) {done.stderr[-300:]}",
        not done.returncode,
    )
    if done.returncode:
        raise SystemExit("\n".join(FAILURES))


def outbox_row(owner_url: str, org: uuid.UUID, job: uuid.UUID) -> dict[str, object]:
    engine = create_engine(owner_url)
    try:
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT status, attempt_count, last_error, external_id, resolved_at "
                        "FROM component_sync_outbox "
                        "WHERE organization_id = :o AND factory_job_id = :j"
                    ),
                    {"o": org, "j": job},
                )
                .mappings()
                .one()
            )
            return dict(row)
    finally:
        engine.dispose()


def now_ms() -> int:
    return int(time.time() * 1000)


def make_run(org: uuid.UUID, job: uuid.UUID, **over: object) -> TrackedRun:
    return proj.run_for(org, job, **over)


class Env:
    """One 'Factory process': its own engine, session factory, outbox, client."""

    def __init__(
        self,
        app_db_url: str,
        org: uuid.UUID,
        binding_id: uuid.UUID,
        results: FileResultSource,
        client: MlflowClient,
        policy: RetryPolicy,
    ) -> None:
        self.engine = create_engine(app_db_url)
        self.factory = sessionmaker(bind=self.engine)
        self.context = TenantContext(organization_id=org)
        self.client = client
        self.outbox = DurableOutbox(
            self.factory,
            self.context,
            binding_id=binding_id,
            results=results,
            actor=ACTOR,
            policy=policy,
            clock=lambda: datetime.now(UTC),
        )

    def close(self) -> None:
        self.engine.dispose()


def main() -> int:
    owner_url = required_env("SOVRA_DATABASE_URL")
    password = required_env("SOVRA_APP_DB_PASSWORD")
    run_migration(owner_url)
    db_url = app_url(owner_url, password)

    owner_engine = create_engine(owner_url)
    with Session(owner_engine) as session:
        org_a = Organization(name="Acme R005", slug=f"acme-r005-{uuid.uuid4().hex[:8]}")
        org_b = Organization(name="Globex R005", slug=f"globex-r005-{uuid.uuid4().hex[:8]}")
        session.add_all([org_a, org_b])
        session.commit()
        a_id, b_id = org_a.id, org_b.id

    bootstrap = create_engine(db_url)
    boot_factory = sessionmaker(bind=bootstrap)
    binding_ids: dict[uuid.UUID, uuid.UUID] = {}
    for org in (a_id, b_id):
        ctx = TenantContext(organization_id=org)
        with tenant_scoped_session_for(boot_factory, ctx) as session:
            created = create_component_binding(
                session,
                ctx,
                component_key="mlflow",
                deployment_mode="internal_service",
                approved_endpoint="https://mlflow.sovra.internal:5000",
                actor=ACTOR,
            )
            binding_ids[org] = created.id
    bootstrap.dispose()

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="r005r2-"))
    evidence = pathlib.Path(tempfile.mkdtemp(prefix="r005r2-evidence-"))
    results = FileResultSource(evidence)
    port = proj.free_port()
    server = proj.Server(workdir, port)
    loop_policy = ConnectionPolicy(
        name="r005-loopback",
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )

    def new_client() -> MlflowClient:
        return MlflowClient(f"http://127.0.0.1:{port}", loop_policy, timeout=5)

    pol = RetryPolicy(max_attempts=3, base_delay_ms=1000, max_delay_ms=4000)
    envs: list[Env] = []

    def env_for(org: uuid.UUID, policy: RetryPolicy = pol) -> Env:
        made = Env(db_url, org, binding_ids[org], results, new_client(), policy)
        envs.append(made)
        return made

    try:
        server.start()
        env_a = env_for(a_id)
        client = env_a.client

        # R2-D1: admit -> sync -> resolve succeeded with the MLflow run id.
        job1 = uuid.uuid4()
        run1 = make_run(a_id, job1)
        results.save(run1)
        entry = env_a.outbox.add(run1)
        row = outbox_row(owner_url, a_id, job1)
        check("R2-D1", "admission writes a real pending outbox row", row["status"] == "pending")
        check(
            "R2-D1",
            "a replayed admission is the same pending entry",
            env_a.outbox.add(run1).entry_id == entry.entry_id,
        )
        done = sync_pending(client, env_a.outbox, now_ms=now_ms())
        row = outbox_row(owner_url, a_id, job1)
        check(
            "R2-D1", "sync resolves the entry SYNCED", [e.state for e in done] == [SyncState.SYNCED]
        )
        check(
            "R2-D1",
            "the real row is succeeded with the MLflow run id and a resolution time",
            row["status"] == "succeeded"
            and row["external_id"] == done[0].mlflow_run_id
            and row["resolved_at"] is not None
            and row["attempt_count"] == 1,
        )
        check("R2-D1", "a resolved row is no longer pending", env_a.outbox.pending() == [])
        replay = env_a.outbox.add(run1)
        check(
            "R2-D1",
            "a replayed admission of a resolved job carries the stored MLflow run id",
            replay.mlflow_run_id == done[0].mlflow_run_id and replay.next_retry_at_ms is None,
        )
        exp_a = client.get_or_create_experiment(experiment_name(a_id))
        record = client.get_run(done[0].mlflow_run_id or "")
        check(
            "R2-D1",
            "MLflow holds the measured metrics",
            {m["key"]: m["value"] for m in record["data"]["metrics"]}
            == {"eval/accuracy": 0.875, "train/loss": 0.125},
        )
        synced_entry = done[0]

        # R2-D2: outage keeps the row pending; restart loses nothing.
        job2 = uuid.uuid4()
        run2 = make_run(a_id, job2, metrics=(("eval/accuracy", 0.5),))
        results.save(run2)
        env_a.outbox.add(run2)
        runs_before = len(proj.runs_in(port, exp_a))
        server.stop()
        t0 = now_ms()
        down = sync_pending(client, env_a.outbox, now_ms=t0)
        row = outbox_row(owner_url, a_id, job2)
        check(
            "R2-D2",
            "an outage is TRACKING_SYNC_PENDING",
            [e.state for e in down] == [SyncState.PENDING],
        )
        check(
            "R2-D2",
            "the real row stays pending with the attempt and the error recorded",
            row["status"] == "pending"
            and row["attempt_count"] == 1
            and "unreachable" in str(row["last_error"])
            and row["resolved_at"] is None
            and row["external_id"] is None,
        )
        # "restart the Factory process": drop everything, start from nothing
        env_a.close()
        envs.remove(env_a)
        del env_a, client, down, entry, synced_entry
        server.start()
        env_a = env_for(a_id)
        client = env_a.client
        buffered = env_a.outbox.pending()
        check(
            "R2-D2",
            "after the restart the buffered result is still pending, exact",
            [e.run.job_id for e in buffered] == [job2]
            and buffered[0].run.metrics == (("eval/accuracy", 0.5),)
            and buffered[0].attempts == 1,
        )
        check(
            "R2-D2",
            "the retry time survives the restart",
            (buffered[0].next_retry_at_ms or 0) >= t0 + pol.delay_ms(1) - 5000,
        )
        early = sync_pending(client, env_a.outbox, now_ms=t0)
        row = outbox_row(owner_url, a_id, job2)
        check(
            "R2-D2",
            "before its retry time nothing is attempted",
            early == []
            and row["attempt_count"] == 1
            and row["status"] == "pending"
            and len(proj.runs_in(port, exp_a)) == runs_before,
        )
        later = sync_pending(client, env_a.outbox, now_ms=t0 + 60_000)
        row = outbox_row(owner_url, a_id, job2)
        check(
            "R2-D2",
            "when due, the buffered result syncs",
            [e.state for e in later] == [SyncState.SYNCED],
        )
        check(
            "R2-D2",
            "the row is succeeded after two attempts",
            row["status"] == "succeeded"
            and row["attempt_count"] == 2
            and row["external_id"] == later[0].mlflow_run_id,
        )
        rec2 = client.get_run(later[0].mlflow_run_id or "")
        check(
            "R2-D2",
            "exact lineage restored, one new run, no duplicate",
            {m["key"]: m["value"] for m in rec2["data"]["metrics"]} == {"eval/accuracy": 0.5}
            and len(proj.runs_in(port, exp_a)) == runs_before + 1,
        )

        # R2-D3: bounded retries -> failed, visible, never resurrected.
        job3 = uuid.uuid4()
        run3 = make_run(a_id, job3)
        results.save(run3)
        env_a.outbox.add(run3)
        server.stop()
        base = now_ms()
        for sweep in range(3):
            sync_pending(client, env_a.outbox, now_ms=base + sweep * 60_000)
        row = outbox_row(owner_url, a_id, job3)
        check(
            "R2-D3",
            "at the limit the row is failed with a retry-limit error",
            row["status"] == "failed"
            and row["attempt_count"] == 3
            and "retry limit reached after 3 attempts" in str(row["last_error"])
            and row["resolved_at"] is not None,
        )
        check("R2-D3", "a failed row is not pending", env_a.outbox.pending() == [])
        ctx_a = TenantContext(organization_id=a_id)
        with tenant_scoped_session_for(env_a.factory, ctx_a) as session:
            status = {e.component_key: e for e in get_component_status_projection(session, ctx_a)}
        check(
            "R2-D3",
            "R002's status projection counts it as a failed outbox entry",
            status["mlflow"].failed_outbox_count >= 1,
        )
        server.start()
        again = sync_pending(client, env_a.outbox, now_ms=base + 10**7)
        row = outbox_row(owner_url, a_id, job3)
        check(
            "R2-D3",
            "the server returning does not resurrect a failed row",
            again == [] and row["status"] == "failed" and row["attempt_count"] == 3,
        )

        # R2-D4: tenant isolation.
        env_b = env_for(b_id)
        job4 = uuid.uuid4()
        run4 = make_run(a_id, job4)
        results.save(run4)
        try:
            env_b.outbox.add(run4)
            check("R2-D4", "org B cannot queue org A's result", False)
        except RunConflictError:
            pass
        env_a.outbox.add(run4)
        check("R2-D4", "org B never sees org A's pending rows", env_b.outbox.pending() == [])
        foreign = DurableOutbox(
            env_b.factory,
            env_b.context,
            binding_id=binding_ids[a_id],
            results=results,
            actor=ACTOR,
        )
        run_b = make_run(b_id, uuid.uuid4())
        results.save(run_b)
        try:
            foreign.add(run_b)
            check("R2-D4", "org B cannot use org A's binding", False)
        except ComponentBindingNotFoundError:
            pass
        sync_pending(client, env_a.outbox, now_ms=now_ms())  # settle job4

        # R2-D5: evidence changed / missing; conflicting re-admission.
        job5 = uuid.uuid4()
        run5 = make_run(a_id, job5)
        results.save(run5)
        env_a.outbox.add(run5)
        results.save(make_run(a_id, job5, metrics=(("eval/accuracy", 0.01),)))
        check("R2-D5", "a changed result is not offered for sync", env_a.outbox.pending() == [])
        row = outbox_row(owner_url, a_id, job5)
        check(
            "R2-D5",
            "a changed result fails the row visibly, never synced",
            row["status"] == "failed" and "result_changed" in str(row["last_error"]),
        )
        job6 = uuid.uuid4()
        run6 = make_run(a_id, job6)
        results.save(run6)
        env_a.outbox.add(run6)
        results.delete(a_id, job6)
        check("R2-D5", "a vanished result is not offered for sync", env_a.outbox.pending() == [])
        row = outbox_row(owner_url, a_id, job6)
        check(
            "R2-D5",
            "a vanished result fails the row visibly",
            row["status"] == "failed" and "result_missing" in str(row["last_error"]),
        )
        job7 = uuid.uuid4()
        run7 = make_run(a_id, job7)
        results.save(run7)
        env_a.outbox.add(run7)
        try:
            env_a.outbox.add(make_run(a_id, job7, metrics=(("eval/accuracy", 0.2),)))
            check("R2-D5", "a different result for a queued job is refused", False)
        except RunConflictError:
            pass
        check(
            "R2-D5",
            "the first result stays queued untouched",
            [e.run.metrics for e in env_a.outbox.pending()] == [run7.metrics],
        )
        sync_pending(client, env_a.outbox, now_ms=now_ms())  # settle job7

        # R2-D5b: the evidence source must hand back the result for exactly the
        # (organization, job) asked for; either identity differing fails the row.
        for label, make_wrong in (
            ("another job", lambda j: make_run(a_id, uuid.uuid4())),
            ("another organization", lambda j: make_run(b_id, j)),
        ):
            job_x = uuid.uuid4()
            run_x = make_run(a_id, job_x)
            results.save(run_x)
            env_a.outbox.add(run_x)
            wrong = make_wrong(job_x)
            results.save(wrong)
            results._path(a_id, job_x).write_text(
                results._path(wrong.organization_id, wrong.job_id).read_text()
            )
            check(
                "R2-D5",
                f"a result for {label} is not offered for sync",
                env_a.outbox.pending() == [],
            )
            row = outbox_row(owner_url, a_id, job_x)
            check(
                "R2-D5",
                f"a result for {label} fails the row as result_mismatch",
                row["status"] == "failed" and "result_mismatch" in str(row["last_error"]),
            )

        # R2-D6: ambiguous logical run -> failed row.
        job8 = uuid.uuid4()
        run8 = make_run(a_id, job8)
        results.save(run8)
        key8 = logical_run_key(a_id, job8)
        for _ in range(2):
            client.create_run(exp_a, run_name=key8, tags={TAG_KEY: key8, TAG_ORG: str(a_id)})
        env_a.outbox.add(run8)
        amb = sync_pending(client, env_a.outbox, now_ms=now_ms())
        row = outbox_row(owner_url, a_id, job8)
        check(
            "R2-D6",
            "two claimants fail the row as ambiguous",
            [e.state for e in amb] == [SyncState.AMBIGUOUS]
            and row["status"] == "failed"
            and str(row["last_error"]).startswith("ambiguous: "),
        )

        # R2-D7: run-level drift on the real server.
        job9 = uuid.uuid4()
        run9 = make_run(a_id, job9)
        results.save(run9)
        e9 = env_a.outbox.add(run9)
        sync_pending(client, env_a.outbox, now_ms=now_ms())
        run_id9 = outbox_row(owner_url, a_id, job9)["external_id"]
        assert isinstance(run_id9, str)
        proj.raw(
            port,
            "POST",
            "/runs/log-metric",
            {"run_id": run_id9, "key": "sneaky/extra", "value": 1.0, "timestamp": 1, "step": 0},
        )
        proj.raw(
            port,
            "POST",
            "/runs/set-tag",
            {"run_id": run_id9, "key": "factory.hash.artifact_sha256", "value": "f" * 64},
        )
        proj.raw(
            port,
            "POST",
            "/runs/log-parameter",
            {"run_id": run_id9, "key": "smuggled", "value": "1"},
        )
        rec9 = client.get_run(run_id9)
        tags9 = {t["key"]: t["value"] for t in rec9["data"]["tags"]}
        check(
            "R2-D7",
            "the payload tag is untouched (a tag-only check would say SYNCED)",
            tags9[TAG_PAYLOAD] == run9.payload_sha256(),
        )
        e9.attempts = 0
        drifted = sync_run(client, e9)
        check(
            "R2-D7",
            "a drifted completed run is FAILED, never SYNCED",
            drifted.state == SyncState.FAILED,
        )
        check(
            "R2-D7",
            "drift codes name the extra metric, changed hash and extra param",
            {
                "metric_extra:sneaky/extra",
                "hash_changed:factory.hash.artifact_sha256",
                "param_extra:smuggled",
            }
            <= set(drifted.drift),
        )
        after = client.get_run(run_id9)
        check(
            "R2-D7",
            "the run was not overwritten (tampered hash still tampered, no extra writes)",
            {t["key"]: t["value"] for t in after["data"]["tags"]}["factory.hash.artifact_sha256"]
            == "f" * 64
            and {m["key"] for m in after["data"]["metrics"]}
            == {"eval/accuracy", "train/loss", "sneaky/extra"},
        )
        # the same drift found through the durable outbox fails the row
        job10 = uuid.uuid4()
        run10 = make_run(a_id, job10)
        results.save(run10)
        pre = sync_run(client, env_a.outbox.add(run10))  # writes the run, row still pending
        proj.raw(
            port,
            "POST",
            "/runs/set-tag",
            {"run_id": pre.mlflow_run_id, "key": "factory.hash.manifest_sha256", "value": "9" * 64},
        )
        drift_sweep = sync_pending(client, env_a.outbox, now_ms=now_ms())
        row = outbox_row(owner_url, a_id, job10)
        check(
            "R2-D7",
            "drift fails the real row with run drift",
            [e.state for e in drift_sweep] == [SyncState.FAILED]
            and row["status"] == "failed"
            and str(row["last_error"]).startswith("run drift:"),
        )

        # R2-D8: Law 17 on real model versions.
        mv = uuid.uuid4()
        source = f"s3://sovra-model-artifacts/{a_id}/{mv}/"
        binding = bind_model_version(
            client,
            organization_id=a_id,
            model_version_id=mv,
            run_id=str(run_id9),
            artifact_sha256="c" * 64,
            source_uri=source,
            approved_source_prefixes=PREFIXES,
        )
        check(
            "R2-D8",
            "a real READY version reads READY and clean",
            inspect_binding(client, binding, approved_source_prefixes=PREFIXES) == ("READY", None),
        )
        db_file = workdir / "mlflow.db"

        def set_status(value: str) -> None:
            conn = sqlite3.connect(db_file)
            try:
                conn.execute(
                    "UPDATE model_versions SET status = ? WHERE name = ?",
                    (value, binding.registered_model_name),
                )
                conn.commit()
            finally:
                conn.close()

        proj.raw(
            port,
            "POST",
            "/model-versions/set-tag",
            {
                "name": binding.registered_model_name,
                "version": binding.registry_version,
                "key": "factory.approved",
                "value": "true",
            },
        )
        for status_value in ("PENDING_REGISTRATION", "FAILED_REGISTRATION"):
            set_status(status_value)
            state, reason = inspect_binding(client, binding, approved_source_prefixes=PREFIXES)
            check(
                "R2-D8", f"real {status_value} is reported as {status_value}", state == status_value
            )
            check("R2-D8", f"real {status_value} carries a reason, never drift None", bool(reason))
            body = project_registry_binding(binding, drift=reason, registration_state=state)
            check(
                "R2-D8",
                f"the projection for a real {status_value} is not clean",
                body["registration_state"] == status_value
                and body["drift"] is not None
                and body["approval_authority"] == "factory",
            )
        set_status("READY")
        check(
            "R2-D8",
            "back to READY, the binding is clean again (a forged approval tag changed nothing)",
            inspect_binding(client, binding, approved_source_prefixes=PREFIXES) == ("READY", None),
        )

        # R2-D9: more versions than one search page.
        many = uuid.uuid4()
        many_name = f"sovra-{a_id}-{many}"
        client.ensure_registered_model(many_name, {TAG_MODEL_VERSION: str(many)})
        for index in range(205):
            client.create_model_version(
                many_name,
                source=f"s3://sovra-model-artifacts/{a_id}/{many}/{index}/",
                run_id=str(run_id9),
                tags={TAG_MODEL_VERSION: str(many)},
                approved_source_prefixes=PREFIXES,
            )
        found = client.search_model_versions_by_tag(many_name, TAG_MODEL_VERSION, str(many))
        check(
            "R2-D9",
            f"all 205 versions are found across pages (got {len(found)})",
            len(found) == 205 and len({v["version"] for v in found}) == 205,
        )

        # R2-D10: client from the tenant's real binding row.
        ctx = TenantContext(organization_id=a_id)
        with tenant_scoped_session_for(env_a.factory, ctx) as session:
            row_binding = get_component_binding(session, ctx, binding_ids[a_id])
            built = client_from_binding(row_binding, policy=loop_policy)
        check(
            "R2-D10",
            "the client is built from the binding row's own endpoint",
            built._base == "https://mlflow.sovra.internal:5000",
        )
    finally:
        for made in envs:
            made.close()
        server.stop()
        owner_engine.dispose()
        shutil.rmtree(workdir, ignore_errors=True)
        shutil.rmtree(evidence, ignore_errors=True)

    if FAILURES:
        print(f"FTF-R005 round-2 durable outbox integration: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        return 1
    print("FTF-R005 round-2 durable outbox integration: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
