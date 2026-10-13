#!/usr/bin/env python3
"""FTF-0502-P1 -- the dataset source reader over PostgreSQL 16 as the restricted
`sovra_app` role and the live SeaweedFS S3 API.

`tests/tasks/test_ftf_0502_p1.py` proves the module over SQLite with a fake object
store. This suite proves what only the real services can:

I1  Datasets made by the real FTF-0501-P1 store functions: the bytes streamed back
    from the real bucket hash to `Dataset.verified_sha256` and a ChunkReader over them
    completes with the declared size
I2  a foreign organization's dataset, a foreign project's dataset, a missing id and a
    hidden held-out dataset all raise the same `DatasetNotFoundError`; raw `sovra_app`
    SQL under another tenant sees no rows and cannot insert one for this tenant
I3  a held-out dataset is refused with include_held_out=False and readable with True
I4  a request counter on the S3 client shows exactly one GET of the session's own key
    and no listing or any other call
I5  an object the verified size does not match (truncated in storage) and a mid-stream
    stop of the S3 service both surface as `UploadStorageUnavailableError`, and a
    ChunkReader over them is `blocked` / `source_unavailable`, never completed or failed

Requires SOVRA_DATABASE_URL (migration owner) and SOVRA_APP_DB_PASSWORD, and an S3
endpoint as in test_ftf_0501_direct_upload_s3.py (this file boots the compose `s3`
service when nothing answers).
Run: python3 tests/integration/test_ftf_0502_p1_dataset_source_postgres_s3.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from workers.data import import_common as ic  # noqa: E402

from app.auth.organization_workspace_schema import Organization, Workspace  # noqa: E402
from app.datasets import dataset_object_source as source_module  # noqa: E402
from app.datasets import dataset_upload_store as store  # noqa: E402
from app.datasets import direct_object_storage_upload as dos  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.services.storage_semantics_client import S3Client  # noqa: E402

CID = "FTF-0502-P1"
FAILURES: list[str] = []
HERE = Path(__file__).resolve().parent
MIB = 1024**2


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name[:-3], HERE / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


p1 = _load("test_ftf_0501_p1_dataset_uploads.py")
s3lab = _load("test_ftf_0501_direct_upload_s3.py")


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CountingClient:
    """Delegates to a real S3Client and records every call by name."""

    def __init__(self, inner: S3Client) -> None:
        self._inner = inner
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def stream_object(self, bucket: str, key: str, *, chunk_size: int = MIB) -> Any:
        self.calls.append(("stream_object", (bucket, key)))
        return self._inner.stream_object(bucket, key, chunk_size=chunk_size)

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._inner, name)

        def recorded(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args))
            return target(*args, **kwargs)

        return recorded


def main() -> None:
    migration_url = p1._required_env("SOVRA_DATABASE_URL")
    app_password = p1._required_env("SOVRA_APP_DB_PASSWORD")
    result = p1._alembic(["upgrade", "head"], migration_url)
    check(f"alembic upgrade head ({result.stdout}{result.stderr})", result.returncode == 0)
    if result.returncode != 0:
        return _finish()

    endpoint, credentials = s3lab._load_endpoint_and_credentials()
    booted_here = False
    try:
        s3lab._wait_for_tcp(endpoint.host, endpoint.port, timeout_seconds=3.0)
    except TimeoutError:
        s3lab.compose("up", "-d", "s3")
        booted_here = True
        s3lab._wait_for_tcp(endpoint.host, endpoint.port, timeout_seconds=90.0)
    s3lab._wait_for_s3_http_ready(endpoint, timeout_seconds=90.0)
    client = S3Client(endpoint, credentials, timeout_seconds=60.0)
    bucket = f"sovra-ftf0502p1-{uuid.uuid4().hex[:12]}"
    client.create_bucket(bucket)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(p1._app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=app_engine, expire_on_commit=False)

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0502", slug=f"acme-0502-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0502", slug=f"globex-0502-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Main", slug="main")
        session.add_all([ws1, ws2])
        session.flush()

        def project(org: Organization, ws: Workspace, name: str) -> Project:
            return Project(
                organization_id=org.id,
                workspace_id=ws.id,
                name=name,
                project_type="generic_model_tuning",
                status="active",
                security_profile="standard",
                row_version=1,
                created_by="seed",
            )

        proj1, proj1b, proj2 = (
            project(org1, ws1, "p1"),
            project(org1, ws1, "p1b"),
            project(org2, ws2, "p2"),
        )
        session.add_all([proj1, proj1b, proj2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        p1_id, p1b_id, p2_id = proj1.id, proj1b.id, proj2.id

    def seed_connection(org: uuid.UUID, project_id: uuid.UUID) -> uuid.UUID:
        connection_id = uuid.uuid4()
        with owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dataset_source_connections (id, organization_id, project_id, "
                    "source_type, display_name, allowed_purposes_json, security_classification, "
                    "connection_status, created_by) VALUES (:id, :org, :project, 'upload', "
                    ":name, CAST(:purposes AS jsonb), 'internal', 'active', 'seed')"
                ),
                {
                    "id": connection_id,
                    "org": org,
                    "project": project_id,
                    "name": f"upload-{connection_id.hex[:6]}",
                    "purposes": json.dumps(["training", "held_out_evaluation"]),
                },
            )
        return connection_id

    conn1, conn2 = seed_connection(org1_id, p1_id), seed_connection(org2_id, p2_id)
    ctx1, ctx2 = TenantContext(organization_id=org1_id), TenantContext(organization_id=org2_id)
    actor = store.UploadActor(actor_type="user", actor_id="user-0502", request_id="req-0502")

    def make_dataset(
        ctx: TenantContext,
        project_id: uuid.UUID,
        connection: uuid.UUID,
        data: bytes,
        name: str,
        purpose: str = "training",
        store_bytes: bytes | None = None,
    ) -> tuple[store.Dataset, str]:
        with tenant_scoped_session_for(factory, ctx) as session:
            record = store.create_upload_session(
                session,
                ctx,
                project_id=project_id,
                connection_id=connection,
                purpose=purpose,
                declared_size_bytes=len(data),
                declared_sha256=sha(data),
                declared_content_type="application/jsonl",
                max_bytes=len(data) * 2,
                ttl=timedelta(hours=1),
                now=datetime.now(UTC),
                actor=actor,
            )
            session_id, key = record.id, record.object_key
        client.put_object(bucket, key, data if store_bytes is None else store_bytes)
        with tenant_scoped_session_for(factory, ctx) as session:
            dataset = store.record_upload_verified_and_create_dataset(
                session,
                ctx,
                project_id=project_id,
                upload_session_id=session_id,
                dataset_name=name,
                verified_size_bytes=len(data),
                verified_sha256=sha(data),
                verified_content_type="application/jsonl",
                now=datetime.now(UTC),
                actor=actor,
            )
        return dataset, key

    def line(i: int) -> bytes:
        return json.dumps({"prompt": f"question {i}", "completion": f"answer {i}"}).encode() + b"\n"

    small = b"".join(line(i) for i in range(500))
    train, train_key = make_dataset(ctx1, p1_id, conn1, small, "train")
    held, held_key = make_dataset(
        ctx1, p1_id, conn1, small + line(999), "held", purpose="held_out_evaluation"
    )
    foreign, _ = make_dataset(ctx2, p2_id, conn2, small + line(7), "foreign")

    def open_source(
        ctx: TenantContext,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        *,
        include_held_out: bool = False,
        storage_client: Any = None,
    ) -> source_module.DatasetObjectSource:
        with tenant_scoped_session_for(factory, ctx) as session:
            return source_module.open_dataset_source(
                session,
                ctx,
                project_id=project_id,
                dataset_id=dataset_id,
                include_held_out=include_held_out,
                storage=source_module.DatasetObjectStorage(storage_client or client, bucket),
            )

    # ---- I1 + I4: real bytes verify; one GET, no LIST --------------------------------
    counting = CountingClient(client)
    src = open_source(ctx1, p1_id, train.id, storage_client=counting)
    check("I4 opening the source makes no storage call yet", counting.calls == [])
    reader = ic.ChunkReader(
        src.chunks, expected_size=src.expected_size, expected_sha256=src.expected_sha256
    )
    streamed = b"".join(reader)
    check("I1 the streamed bytes are the stored bytes", streamed == small)
    check(
        "I1 the streamed bytes hash to Dataset.verified_sha256",
        sha(streamed) == train.verified_sha256 == src.expected_sha256,
    )
    check(
        "I1 the declared size is the verified size",
        src.expected_size == train.verified_size_bytes == len(small),
    )
    check(
        "I1 purpose, source type and content type come from the rows",
        (src.purpose, src.source_type, src.verified_content_type)
        == ("training", "upload", "application/jsonl"),
    )
    check(
        f"I4 exactly one GET of the session's own key and nothing else: {counting.calls}",
        counting.calls == [("stream_object", (bucket, train_key))],
    )
    check("I4 the object key is not in the returned source", train_key not in repr(src))

    mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "prompt": {"key": "prompt"},
            "completion": {"key": "completion"},
        }
    )
    accepted: list[dict[str, object]] = []
    reader2 = ic.ChunkReader(
        open_source(ctx1, p1_id, train.id).chunks,
        expected_size=len(small),
        expected_sha256=train.verified_sha256,
    )
    buffer = b"".join(reader2)
    for index, raw in enumerate(buffer.splitlines()):
        record = ic.normalize_mapped_record(
            mapping,
            ic.apply_mapping(mapping, ic.strict_loads(raw)),
            source_record_index=index,
            dataset_sha256=train.verified_sha256,
            source_type=src.source_type,
        )  # type: ignore[arg-type]
        accepted.append(
            ic.to_dataset_record_candidate(
                record, record_index=index, tool_schema_status="not_applicable"
            )
        )
    from workers.data.record_schema_validator import validate_record_batch

    check(
        "I1 every real record becomes a candidate FTF-0508 accepts",
        len(accepted) == 500 and validate_record_batch(accepted).issues == (),
    )

    # ---- I2: scoping ------------------------------------------------------------------
    def failure(
        ctx: TenantContext, project_id: uuid.UUID, dataset_id: uuid.UUID, **kw: Any
    ) -> str | None:
        try:
            open_source(ctx, project_id, dataset_id, **kw)
        except store.DatasetNotFoundError as exc:
            return str(exc).replace(str(dataset_id), "<id>")
        except BaseException as exc:  # noqa: BLE001 - the test inspects the type
            return f"WRONG {type(exc).__name__}"
        return None

    missing_id = uuid.uuid4()
    failures = {
        "missing": failure(ctx1, p1_id, missing_id),
        "foreign org": failure(ctx1, p1_id, foreign.id),
        "foreign org (their project id)": failure(ctx1, p2_id, foreign.id),
        "foreign project": failure(ctx1, p1b_id, train.id),
        "wrong tenant": failure(ctx2, p1_id, train.id),
        "hidden held-out": failure(ctx1, p1_id, held.id),
    }
    check(
        f"I2 every unauthorized read is DatasetNotFoundError: {failures}",
        all(v is not None and not v.startswith("WRONG") for v in failures.values()),
    )
    check(
        f"I2 all of them read exactly alike: {set(failures.values())}",
        len(set(failures.values())) == 1,
    )

    with tenant_scoped_session_for(factory, ctx2) as session:
        seen = session.execute(
            text("SELECT count(*) FROM datasets WHERE organization_id = :o"), {"o": org1_id}
        ).scalar()
        sessions_seen = session.execute(
            text("SELECT count(*) FROM dataset_upload_sessions WHERE organization_id = :o"),
            {"o": org1_id},
        ).scalar()
        own = session.execute(text("SELECT count(*) FROM datasets")).scalar()
    check(
        "I2 raw sovra_app SQL under another tenant sees none of this tenant's datasets or sessions",
        (seen, sessions_seen) == (0, 0) and own == 1,
    )
    refused = False
    try:
        with tenant_scoped_session_for(factory, ctx2) as session:
            session.execute(
                text(
                    "INSERT INTO datasets (id, organization_id, project_id, name, version, "
                    "purpose, upload_session_id, upload_status, verified_sha256, "
                    "verified_size_bytes, created_by, created_at) "
                    "SELECT gen_random_uuid(), :o, project_id, 'planted', 99, purpose, "
                    "upload_session_id, upload_status, verified_sha256, "
                    "verified_size_bytes, 'x', now() FROM datasets WHERE id = :d"
                ),
                {"o": org1_id, "d": foreign.id},
            )
    except Exception:  # noqa: BLE001 - any refusal by the database is the point
        refused = True
    check("I2 sovra_app under another tenant cannot insert a dataset for this tenant", refused)
    with owner_engine.connect() as conn:
        planted = conn.execute(
            text("SELECT count(*) FROM datasets WHERE name = 'planted'")
        ).scalar()
    check("I2 nothing was planted", planted == 0)

    # ---- I3: held-out -----------------------------------------------------------------
    got = open_source(ctx1, p1_id, held.id, include_held_out=True)
    check(
        "I3 a held-out dataset is readable with include_held_out=True",
        got.purpose == "held_out_evaluation",
    )
    held_bytes = b"".join(
        ic.ChunkReader(
            got.chunks, expected_size=got.expected_size, expected_sha256=got.expected_sha256
        )
    )
    check("I3 ...and its bytes verify", sha(held_bytes) == held.verified_sha256)
    check("I3 it is refused with include_held_out=False", failures["hidden held-out"] is not None)

    # ---- I5: truncated in storage, then S3 stopped mid-stream ---------------------------
    truncated, _ = make_dataset(ctx1, p1_id, conn1, small, "truncated", store_bytes=small[:-1000])
    src = open_source(ctx1, p1_id, truncated.id)
    outcome = None
    try:
        for _ in ic.ChunkReader(
            src.chunks, expected_size=src.expected_size, expected_sha256=src.expected_sha256
        ):
            pass
    except ic.ImportAborted as exc:
        outcome = exc
    check(
        f"I5 an object shorter than its verified size is blocked/source_unavailable ({outcome})",
        outcome is not None
        and (outcome.state, outcome.failure_code) == ("blocked", "source_unavailable"),
    )

    big = os.urandom(MIB) * 96
    large_a, _ = make_dataset(ctx1, p1_id, conn1, big, "large-a")
    large_b, _ = make_dataset(ctx1, p1_id, conn1, big + b"x", "large-b")
    raw = open_source(ctx1, p1_id, large_a.id)
    via_reader_src = open_source(ctx1, p1_id, large_b.id)
    via_reader = ic.ChunkReader(
        via_reader_src.chunks,
        expected_size=via_reader_src.expected_size,
        expected_sha256=via_reader_src.expected_sha256,
    )
    check(
        "I5 the first megabyte of each large object streams",
        len(next(raw.chunks)) == MIB and len(next(via_reader)) == MIB,
    )
    s3lab.compose("stop", "s3", check_exit=False)
    caught: BaseException | None = None
    try:
        for _ in raw.chunks:
            pass
    except BaseException as exc:  # noqa: BLE001 - the test inspects the type
        caught = exc
    check(
        f"I5 stopping S3 mid-stream is UploadStorageUnavailableError ({type(caught).__name__})",
        isinstance(caught, dos.UploadStorageUnavailableError),
    )
    check(
        "I5 the error carries no key, bucket or session id",
        caught is not None and bucket not in str(caught) and "tenants/" not in str(caught),
    )
    check(
        "I5 the error is raised from None",
        caught is not None and caught.__cause__ is None and caught.__suppress_context__,
    )
    verdict = None
    try:
        for _ in via_reader:
            pass
    except ic.ImportAborted as exc:
        verdict = exc
    check(
        f"I5 a mid-stream stop of S3 makes the ChunkReader blocked/source_unavailable ({verdict})",
        verdict is not None
        and (verdict.state, verdict.failure_code) == ("blocked", "source_unavailable"),
    )

    app_engine.dispose()
    owner_engine.dispose()
    if booted_here:
        s3lab.compose("stop", "s3", check_exit=False)
    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-0502-P1 dataset source (PG16 + SeaweedFS): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0502-P1 dataset source (PG16 + SeaweedFS): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
