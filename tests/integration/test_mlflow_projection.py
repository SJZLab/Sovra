#!/usr/bin/env python3
"""FTF-R005 -- real MLflow projection integration proof.

Boots a real `mlflow server` (pinned version, SQLite backend store, local
artifact root, loopback only) and drives `packages/integrations/mlflow/`
against it -- never a canned response. Proves:

R005-A1  a real MLflow run is reconciled from a durable job identity; the
         metrics, params and Factory hash tags read back from MLflow equal
         what was measured, and a replay produces no second run and no
         duplicated metric rows.
R005-A1b an ambiguous create (the run exists, the response was lost) is
         adopted, not duplicated.
R005-A2  a tracking outage keeps the result in the outbox as
         TRACKING_SYNC_PENDING (never SYNCED); after the server returns,
         the exact lineage is restored without duplicates.
R005-N1  a forged approval tag, a forged alias and a tampered artifact-hash
         tag cannot approve anything: the projection has a fixed field set
         with Factory approval authority, and the tampered hash is reported
         as drift.
R005-N2  tenants are isolated: the same job id in another organization maps
         to a different run in a different experiment; a run in an
         organization's experiment tagged for another tenant is refused;
         a binding presented under another organization is refused.
R005-N3  sealed final-qualification content and non-metric export classes
         are refused before any MLflow call.
R005-N4  two runs claiming one logical key are AMBIGUOUS, never guessed.
R005-N5  a model source outside the approved internal prefixes is refused
         before any byte is sent.

Requires `mlflow` on PATH (CI: `pip install mlflow==3.1.1`; locally set
MLFLOW_BIN to the binary). Run directly:
    python3 tests/integration/test_mlflow_projection.py
"""

from __future__ import annotations

import ipaddress
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.integrations.mlflow.client import (  # noqa: E402
    MlflowClient,
    TrackingRejectedError,
    TrackingUnavailableError,
)
from packages.integrations.mlflow.reconcile import (  # noqa: E402
    TAG_KEY,
    TAG_ORG,
    InMemoryOutbox,
    SealedExportRefusedError,
    SyncState,
    TrackedRun,
    experiment_name,
    logical_run_key,
    sync_pending,
    sync_run,
)
from packages.integrations.mlflow.registry_binding import (  # noqa: E402
    PROJECTION_FIELDS,
    RegistryDriftError,
    bind_model_version,
    project_registry_binding,
    registered_model_name,
    verify_binding,
)
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

FAILURES: list[str] = []
PREFIXES = ("s3://sovra-model-artifacts/",)


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Server:
    def __init__(self, workdir: pathlib.Path, port: int) -> None:
        self.workdir, self.port = workdir, port
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        binary = os.environ.get("MLFLOW_BIN") or shutil.which("mlflow") or "mlflow"
        self.proc = subprocess.Popen(
            [
                binary,
                "server",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--backend-store-uri",
                f"sqlite:///{self.workdir}/mlflow.db",
                "--default-artifact-root",
                str(self.workdir / "artifacts"),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2):
                    return
            except OSError:
                time.sleep(0.5)
        raise RuntimeError("real mlflow server did not become healthy")

    def stop(self) -> None:
        if self.proc is not None:
            # The server forks workers: signal the whole process group so
            # the port is genuinely closed (a real outage), not half-alive.
            os.killpg(self.proc.pid, signal.SIGTERM)
            self.proc.wait(timeout=30)
            deadline = time.time() + 30
            while time.time() < deadline:
                try:
                    socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
                    time.sleep(0.3)
                except OSError:
                    break
            self.proc = None


def raw(port: int, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/2.0/mlflow{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def run_for(org: uuid.UUID, job: uuid.UUID, **overrides: object) -> TrackedRun:
    fields: dict = {
        "organization_id": org,
        "job_id": job,
        "completed_at_ms": 1_760_000_000_000,
        "metrics": (("eval/accuracy", 0.875), ("train/loss", 0.125)),
        "params": (("base_model", "qwen2.5-7b"), ("seed", "42")),
        "hashes": (("manifest_sha256", "a" * 64), ("artifact_sha256", "b" * 64)),
        "data_purpose": "development_eval",
    }
    fields.update(overrides)
    return TrackedRun(**fields)


def runs_in(port: int, experiment_id: str) -> list[dict]:
    return raw(port, "POST", "/runs/search", {"experiment_ids": [experiment_id]}).get("runs", [])


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="r005-"))
    port = free_port()
    server = Server(workdir, port)
    policy = ConnectionPolicy(
        name="r005-loopback",
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )
    client = MlflowClient(f"http://127.0.0.1:{port}", policy, timeout=10)
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    try:
        server.start()

        # R005-A1: real run reconciled from a durable job identity.
        job = uuid.uuid4()
        store = InMemoryOutbox()
        entry = sync_run(client, store.add(run_for(org_a, job)))
        check("R005-A1", "entry reaches SYNCED", entry.state == SyncState.SYNCED)
        exp_a = client.get_or_create_experiment(experiment_name(org_a))
        runs = runs_in(port, exp_a)
        check("R005-A1", "exactly one real run exists", len(runs) == 1)
        record = client.get_run(entry.mlflow_run_id or "")
        metrics = {m["key"]: m["value"] for m in record["data"]["metrics"]}
        params = {p["key"]: p["value"] for p in record["data"]["params"]}
        tags = {t["key"]: t["value"] for t in record["data"]["tags"]}
        check(
            "R005-A1",
            "metrics read back equal",
            metrics == {"eval/accuracy": 0.875, "train/loss": 0.125},
        )
        check(
            "R005-A1",
            "params read back equal",
            params == {"base_model": "qwen2.5-7b", "seed": "42"},
        )
        check(
            "R005-A1", "hash tags read back", tags.get("factory.hash.artifact_sha256") == "b" * 64
        )
        check("R005-A1", "run is FINISHED", record["info"]["status"] == "FINISHED")
        again = sync_run(client, entry)
        history = raw(
            port,
            "GET",
            f"/metrics/get-history?run_id={entry.mlflow_run_id}&metric_key=train%2Floss&max_results=100",
        )
        check("R005-A1", "replay stays SYNCED", again.state == SyncState.SYNCED)
        check("R005-A1", "replay creates no second run", len(runs_in(port, exp_a)) == 1)
        check("R005-A1", "replay duplicates no metric row", len(history.get("metrics", [])) == 1)

        # R005-A1b: ambiguous create adopted, not duplicated.
        job_b = uuid.uuid4()
        key_b = logical_run_key(org_a, job_b)
        pre = client.create_run(exp_a, run_name=key_b, tags={TAG_KEY: key_b, TAG_ORG: str(org_a)})
        adopted = sync_run(client, store.add(run_for(org_a, job_b)))
        check("R005-A1b", "adopts the pre-existing run", adopted.mlflow_run_id == pre)
        check("R005-A1b", "no duplicate after adoption", len(runs_in(port, exp_a)) == 2)

        # R005-A2: outage keeps the result pending, restores exact lineage.
        job_c = uuid.uuid4()
        outbox = InMemoryOutbox()
        pending = outbox.add(run_for(org_a, job_c, metrics=(("eval/accuracy", 0.5),)))
        server.stop()
        down = sync_run(client, pending)
        check("R005-A2", "outage state is TRACKING_SYNC_PENDING", down.state == SyncState.PENDING)
        check("R005-A2", "outage never reports SYNCED", down.mlflow_run_id is None)
        check(
            "R005-A2", "measured result preserved", pending.run.metrics == (("eval/accuracy", 0.5),)
        )
        try:
            client.get_or_create_experiment(experiment_name(org_a))
            check("R005-A2", "client raises on outage", False)
        except TrackingUnavailableError:
            pass
        server.start()
        restored = sync_pending(client, outbox)
        check(
            "R005-A2",
            "restored entry is SYNCED",
            bool(restored) and restored[0].state == SyncState.SYNCED,
        )
        rec = client.get_run(pending.mlflow_run_id or "")
        got = {m["key"]: m["value"] for m in rec["data"]["metrics"]}
        check("R005-A2", "exact lineage restored", got == {"eval/accuracy": 0.5})
        check("R005-A2", "restore creates no duplicate", len(runs_in(port, exp_a)) == 3)

        # R005-N1: forged tags/aliases cannot approve; tampered digest is drift.
        mv_id, run_id = uuid.uuid4(), entry.mlflow_run_id or ""
        source = f"s3://sovra-model-artifacts/{org_a}/{mv_id}/"
        binding = bind_model_version(
            client,
            organization_id=org_a,
            model_version_id=mv_id,
            run_id=run_id,
            artifact_sha256="c" * 64,
            source_uri=source,
            approved_source_prefixes=PREFIXES,
        )
        name = binding.registered_model_name
        replay = bind_model_version(
            client,
            organization_id=org_a,
            model_version_id=mv_id,
            run_id=run_id,
            artifact_sha256="c" * 64,
            source_uri=source,
            approved_source_prefixes=PREFIXES,
        )
        check("R005-N1", "bind replay finds the same version", replay == binding)
        versions = raw(port, "GET", f"/model-versions/search?filter=name%3D%27{name}%27")
        check(
            "R005-N1", "exactly one registry version", len(versions.get("model_versions", [])) == 1
        )
        raw(
            port,
            "POST",
            "/model-versions/set-tag",
            {
                "name": name,
                "version": binding.registry_version,
                "key": "factory.approved",
                "value": "true",
            },
        )
        raw(
            port,
            "POST",
            "/registered-models/alias",
            {"name": name, "alias": "champion", "version": binding.registry_version},
        )
        verify_binding(client, binding, approved_source_prefixes=PREFIXES)
        body = project_registry_binding(binding, drift=None)
        text = json.dumps(body)
        check("R005-N1", "projection has the fixed field set", tuple(body) == PROJECTION_FIELDS)
        check(
            "R005-N1",
            "forged tag/alias absent from projection",
            "champion" not in text and "approved" not in text.replace("approval_authority", ""),
        )
        check(
            "R005-N1", "approval authority is the Factory", body["approval_authority"] == "factory"
        )
        raw(
            port,
            "POST",
            "/model-versions/set-tag",
            {
                "name": name,
                "version": binding.registry_version,
                "key": "factory.artifact_sha256",
                "value": "d" * 64,
            },
        )
        try:
            verify_binding(client, binding, approved_source_prefixes=PREFIXES)
            check("R005-N1", "tampered artifact digest is drift", False)
        except RegistryDriftError:
            pass

        # R005-N2: tenant isolation.
        iso = sync_run(client, store.add(run_for(org_b, job)))
        exp_b = client.get_or_create_experiment(experiment_name(org_b))
        check(
            "R005-N2",
            "same job id in another org is a different run",
            iso.mlflow_run_id != entry.mlflow_run_id,
        )
        check(
            "R005-N2",
            "other org's run lives in its own experiment",
            exp_b != exp_a and len(runs_in(port, exp_b)) == 1,
        )
        job_x = uuid.uuid4()
        key_x = logical_run_key(org_a, job_x)
        client.create_run(exp_a, run_name=key_x, tags={TAG_KEY: key_x, TAG_ORG: str(org_b)})
        foreign = sync_run(client, store.add(run_for(org_a, job_x)))
        check("R005-N2", "foreign-tenant tagged run is refused", foreign.state == SyncState.FAILED)
        stolen = type(binding)(**{**binding.__dict__, "organization_id": org_b})
        try:
            verify_binding(client, stolen, approved_source_prefixes=PREFIXES)
            check("R005-N2", "binding under another org is refused", False)
        except RegistryDriftError:
            pass
        check("R005-N2", "model names embed the org", registered_model_name(org_b, mv_id) != name)

        # R005-N3: sealed / non-metric export refused before any MLflow call.
        before = len(runs_in(port, exp_a))
        for kwargs in ({"data_purpose": "final_qualification"}, {"export_class": "raw_trace"}):
            try:
                run_for(org_a, uuid.uuid4(), **kwargs)
                check("R005-N3", f"{kwargs} refused", False)
            except SealedExportRefusedError:
                pass
        check("R005-N3", "refusal creates no run", len(runs_in(port, exp_a)) == before)

        # R005-N4: two runs claiming one key are AMBIGUOUS.
        job_d = uuid.uuid4()
        key_d = logical_run_key(org_a, job_d)
        for _ in range(2):
            client.create_run(exp_a, run_name=key_d, tags={TAG_KEY: key_d, TAG_ORG: str(org_a)})
        amb = sync_run(client, store.add(run_for(org_a, job_d)))
        check("R005-N4", "ambiguous key is AMBIGUOUS", amb.state == SyncState.AMBIGUOUS)
        check("R005-N4", "ambiguity never picks a run", amb.mlflow_run_id is None)

        # R005-N5: unapproved model source refused before any byte is sent.
        mv2 = uuid.uuid4()
        try:
            bind_model_version(
                client,
                organization_id=org_a,
                model_version_id=mv2,
                run_id=run_id,
                artifact_sha256="e" * 64,
                source_uri="https://evil.example/model/",
                approved_source_prefixes=PREFIXES,
            )
            check("R005-N5", "unapproved source refused", False)
        except TrackingRejectedError:
            pass
        found = raw(port, "GET", f"/registered-models/search?filter=name%20LIKE%20%27%25{mv2}%27")
        check("R005-N5", "refusal registered nothing", not found.get("registered_models"))
    finally:
        server.stop()
        shutil.rmtree(workdir, ignore_errors=True)

    if FAILURES:
        print("\n".join(FAILURES))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
